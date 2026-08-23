"""Meridian worker — runs as a SEPARATE PROCESS so a heavy MCMC (or an
out-of-memory kill) can never take the app down.

Modes:
  python -m app.meridian_worker fit <jobdir>       — build, sample, extract, save
  python -m app.meridian_worker optimize <jobdir>  — load saved model, run BudgetOptimizer

The job directory contract (all JSON unless noted):
  input.pkl        dataframe            (fit)
  cfg.json         meridian mapping     (fit)
  sampling.json    sampling params      (fit)
  priors.json      {channel:{mu,sigma}} (fit, optional)
  opt.json         optimizer request    (optimize)
  progress.log     appended progress lines (worker → app)
  result.json      extracted results payload
  optimize.json    optimizer output
  model.pkl        fitted Meridian model (save_mmm)
  reports/ under the app's reports dir (HTML)
"""
from __future__ import annotations

import json
import os
import sys
import traceback

import numpy as np
import pandas as pd

BASE_DIR = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
REPORTS_DIR = os.path.join(BASE_DIR, "reports")


def log(jobdir: str, msg: str) -> None:
    with open(os.path.join(jobdir, "progress.log"), "a", encoding="utf-8") as fh:
        fh.write(msg.rstrip() + "\n")


def _ci(draws: np.ndarray, axis=None):
    return (np.mean(draws, axis=axis), np.percentile(draws, 5, axis=axis),
            np.percentile(draws, 95, axis=axis))


def do_fit(jobdir: str) -> None:
    import tensorflow_probability as tfp
    from meridian.data import data_frame_input_data_builder as dfb
    from meridian.model import model as mmodel
    from meridian.model import prior_distribution, spec
    from meridian.analysis import analyzer, summarizer

    df = pd.read_pickle(os.path.join(jobdir, "input.pkl"))
    cfg = json.load(open(os.path.join(jobdir, "cfg.json")))
    sampling = json.load(open(os.path.join(jobdir, "sampling.json")))
    priors_path = os.path.join(jobdir, "priors.json")
    prior_spec = json.load(open(priors_path)) if os.path.exists(priors_path) else None

    # ---------------- input data ----------------
    log(jobdir, "Building Meridian InputData…")
    d = df.copy()
    time_col, geo_col = cfg["time_col"], cfg.get("geo_col")
    d[time_col] = pd.to_datetime(d[time_col]).dt.strftime("%Y-%m-%d")
    rename = {time_col: "time"}
    if geo_col:
        rename[geo_col] = "geo"
    d = d.rename(columns=rename)
    if not geo_col:
        d["geo"] = "national"
    pop_col = cfg.get("population_col")
    if not pop_col or pop_col not in d.columns:
        d["population"] = 1.0
    elif pop_col != "population":
        d = d.rename(columns={pop_col: "population"})

    kpi_type = cfg.get("kpi_type", "revenue")
    rpk = cfg.get("revenue_per_kpi")
    builder = dfb.DataFrameInputDataBuilder(
        kpi_type=kpi_type,
        default_kpi_column=cfg["kpi"],
        default_revenue_per_kpi_column=rpk if (kpi_type == "non_revenue" and rpk) else None,
    )
    builder = builder.with_kpi(d)
    if kpi_type == "non_revenue" and rpk and rpk in d.columns:
        builder = builder.with_revenue_per_kpi(d)
    builder = builder.with_population(d)
    if cfg.get("controls"):
        builder = builder.with_controls(d, control_cols=cfg["controls"])
    channels = [c["name"] for c in cfg["channels"]]
    builder = builder.with_media(
        d, media_cols=[c.get("units_col") or c["spend_col"] for c in cfg["channels"]],
        media_spend_cols=[c["spend_col"] for c in cfg["channels"]],
        media_channels=channels)
    if cfg.get("organic"):
        builder = builder.with_organic_media(
            d, organic_media_cols=cfg["organic"],
            organic_media_channels=[c.replace("_impressions", "").replace("_impression", "")
                                    for c in cfg["organic"]])
    if cfg.get("non_media"):
        builder = builder.with_non_media_treatments(d, non_media_treatment_cols=cfg["non_media"])
    data = builder.build()

    # ---------------- spec ----------------
    max_lag = int(sampling.get("max_lag", 8))
    knots = sampling.get("knots")
    if not knots and geo_col:
        n_times = df[time_col].nunique()
        flat = [c for c in (cfg.get("controls", []) + cfg.get("non_media", []))
                if c in df.columns and
                bool((df.groupby(df[time_col])[c].std(ddof=0).fillna(0) < 1e-12).all())]
        if flat:
            knots = max(4, n_times // 4)
            log(jobdir, f"{', '.join(flat)} vary only over time (identical across geos) — "
                        f"setting knots={knots} (< {n_times}) to keep the model identifiable.")
    spec_kwargs = {"max_lag": max_lag}
    if knots:
        spec_kwargs["knots"] = int(knots)
    if prior_spec:
        mus = [float(prior_spec.get(ch, {}).get("mu", 0.2)) for ch in channels]
        sigmas = [float(prior_spec.get(ch, {}).get("sigma", 0.9)) for ch in channels]
        spec_kwargs["prior"] = prior_distribution.PriorDistribution(
            roi_m=tfp.distributions.LogNormal(np.array(mus, np.float32),
                                              np.array(sigmas, np.float32), name="roi_m"))
    model_spec = spec.ModelSpec(**spec_kwargs)
    log(jobdir, f"ModelSpec ready (max_lag={max_lag}, knots={knots or 'default'}, "
                f"priors={'custom ROI' if prior_spec else 'Meridian defaults'}).")

    mmm = mmodel.Meridian(input_data=data, model_spec=model_spec)
    n_prior = int(sampling.get("n_prior", 500))
    log(jobdir, f"Sampling prior ({n_prior} draws)…")
    mmm.sample_prior(n_prior)

    n_chains = int(sampling.get("n_chains", 4))
    n_adapt = int(sampling.get("n_adapt", 500))
    n_burnin = int(sampling.get("n_burnin", 500))
    n_keep = int(sampling.get("n_keep", 1000))
    seed = int(sampling.get("seed", 1))
    log(jobdir, f"Sampling posterior — {n_chains} chains × ({n_adapt} adapt + {n_burnin} burn-in "
                f"+ {n_keep} keep). Full MCMC: minutes, not seconds…")
    mmm.sample_posterior(n_chains=n_chains, n_adapt=n_adapt, n_burnin=n_burnin,
                         n_keep=n_keep, seed=seed)
    log(jobdir, "Posterior sampling done. Extracting results…")

    # ---------------- extract ----------------
    an = analyzer.Analyzer(mmm)
    use_kpi = kpi_type == "non_revenue" and not rpk

    roi = np.asarray(an.roi(use_kpi=use_kpi)); roi = roi.reshape(-1, roi.shape[-1])
    roi_mean, roi_lo, roi_hi = _ci(roi, axis=0)
    inc = np.asarray(an.incremental_outcome(use_kpi=use_kpi)); inc = inc.reshape(-1, inc.shape[-1])
    inc_mean, inc_lo, inc_hi = _ci(inc, axis=0)
    spend = np.asarray(an.get_historical_spend()).reshape(-1)[: len(channels)]

    fit = an.expected_vs_actual_data(aggregate_geos=True)
    dates = [str(x)[:10] for x in fit.coords["time"].values]

    def flat_series(da):
        v = da
        if "metric" in v.dims:
            v = v.sel(metric="mean")
        arr = np.asarray(v.values).reshape(len(dates), -1)
        return arr.sum(axis=1)

    actual = flat_series(fit["actual"])
    exp_np = flat_series(fit["expected"])
    base_np = flat_series(fit["baseline"])

    rhat_max = None
    try:
        rs = an.rhat_summary()
        vals = []
        for item in (rs if isinstance(rs, (list, tuple)) else [rs]):
            try:
                vals.append(float(pd.DataFrame(item).select_dtypes("number").max().max()))
            except Exception:
                pass
        rhat_max = max(vals) if vals else None
    except Exception:
        pass

    try:
        acc = an.predictive_accuracy().to_dataframe().reset_index()
        r2 = float(acc.loc[acc["metric"] == "R_Squared", "value"].iloc[0])
        mape = float(acc.loc[acc["metric"] == "MAPE", "value"].iloc[0])
    except Exception:
        ss_res = float(np.sum((actual - exp_np) ** 2))
        ss_tot = float(np.sum((actual - actual.mean()) ** 2))
        r2 = 1 - ss_res / max(ss_tot, 1e-9)
        mape = float(np.mean(np.abs((actual - exp_np) / np.clip(np.abs(actual), 1e-9, None))))

    curves = {}
    try:
        rc = an.response_curves(spend_multipliers=[round(x, 2) for x in np.linspace(0, 2.5, 26)])
        rcdf = rc.to_dataframe().reset_index()
        if "metric" in rcdf.columns:
            rcdf = rcdf[rcdf["metric"] == "mean"]
        weeks = df[time_col].nunique()
        for ch in channels:
            sub = rcdf[rcdf["channel"] == ch].sort_values("spend")
            if len(sub) and "incremental_outcome" in sub.columns:
                curves[ch] = {"x": (sub["spend"] / weeks).tolist(),
                              "y": (sub["incremental_outcome"] / weeks).tolist(),
                              "current_weekly": float(spend[channels.index(ch)]) / weeks}
    except Exception as e:
        log(jobdir, f"response curves skipped: {e}")

    media_total = float(np.sum(inc_mean))
    summary = [{
        "channel": ch, "total_spend": float(spend[i]),
        "total_contribution": float(inc_mean[i]),
        "contribution_lo": float(inc_lo[i]), "contribution_hi": float(inc_hi[i]),
        "contribution_share_of_media": float(inc_mean[i] / media_total) if media_total > 0 else 0.0,
        "roi": float(roi_mean[i]), "roi_lo": float(roi_lo[i]), "roi_hi": float(roi_hi[i]),
        "avg_weekly_spend": float(spend[i]) / max(df[time_col].nunique(), 1),
        "decay": None, "half_sat_spend": None, "coef_max_effect": None,
    } for i, ch in enumerate(channels)]

    result = {
        "engine": "meridian", "kpi": cfg["kpi"], "kpi_type": kpi_type,
        "r2": r2, "holdout_mape": mape, "rhat_max": rhat_max, "alpha": None,
        "channels": channels, "controls": cfg.get("controls", []),
        "dates": dates, "actual": actual.tolist(), "predicted": exp_np.tolist(),
        "baseline": base_np.tolist(), "contributions": {},
        "summary": summary, "curves": curves, "params": {},
        "media_driven_total": media_total, "total_outcome": float(np.sum(actual)),
    }
    json.dump(result, open(os.path.join(jobdir, "result.json"), "w"))

    # persist the fitted model for the optimizer
    try:
        mmodel.save_mmm(mmm, os.path.join(jobdir, "model.pkl"))
        log(jobdir, "Fitted model saved for budget optimization.")
    except Exception as e:
        log(jobdir, f"model save skipped: {e}")

    # Meridian's own HTML summary
    try:
        os.makedirs(REPORTS_DIR, exist_ok=True)
        times = pd.to_datetime(df[time_col])
        summarizer.Summarizer(mmm).output_model_results_summary(
            "meridian_model_summary.html", REPORTS_DIR,
            times.min().strftime("%Y-%m-%d"), times.max().strftime("%Y-%m-%d"))
        json.dump({"model_summary": "/reports/meridian_model_summary.html"},
                  open(os.path.join(jobdir, "reports.json"), "w"))
        log(jobdir, "Saved Meridian model summary report.")
    except Exception as e:
        log(jobdir, f"Model summary report skipped: {e}")
    log(jobdir, "DONE")


def do_optimize(jobdir: str) -> None:
    from meridian.model import model as mmodel
    from meridian.analysis import optimizer as mopt

    req = json.load(open(os.path.join(jobdir, "opt.json")))
    log(jobdir, "Loading fitted model…")
    mmm = mmodel.load_mmm(os.path.join(jobdir, "model.pkl"))
    opt = mopt.BudgetOptimizer(mmm)
    budget = req.get("total_weekly_budget")
    n_weeks = int(req.get("n_weeks", 52))
    log(jobdir, "Running BudgetOptimizer…")
    if budget:
        try:
            res = opt.optimize(fixed_budget=True, budget=float(budget) * n_weeks)
        except TypeError:
            res = opt.optimize()
    else:
        res = opt.optimize()

    out = {"engine": "meridian"}
    try:
        od = res.optimized_data.to_dataframe().reset_index()
        out["optimized"] = json.loads(od.to_json(orient="records"))
        nd = res.nonoptimized_data.to_dataframe().reset_index()
        out["nonoptimized"] = json.loads(nd.to_json(orient="records"))
    except Exception as e:
        out["note"] = f"allocation table unavailable ({e})"
    try:
        os.makedirs(REPORTS_DIR, exist_ok=True)
        res.output_optimization_summary("meridian_budget_optimization.html", REPORTS_DIR)
        out["report"] = "/reports/meridian_budget_optimization.html"
    except Exception as e:
        out.setdefault("note", f"optimization report unavailable ({e})")
    json.dump(out, open(os.path.join(jobdir, "optimize.json"), "w"))
    log(jobdir, "DONE")


if __name__ == "__main__":
    mode, jobdir = sys.argv[1], sys.argv[2]
    try:
        (do_fit if mode == "fit" else do_optimize)(jobdir)
    except Exception:
        log(jobdir, "FAILED\n" + traceback.format_exc()[-2000:])
        sys.exit(1)
