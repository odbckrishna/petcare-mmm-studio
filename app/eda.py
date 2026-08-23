"""Pre-modeling exploratory data analysis, following Google Meridian's EDA guide
(https://developers.google.com/meridian/docs/pre-modeling/perform-eda).

Five report sections, each mirroring the docs' checks with the same severity
scale — ERROR (blocks modeling), ATTENTION (model with caution), INFO — plus a
locally-generated "AI summary" narrative per section and a complete analysis.

All parameters exposed to the UI have the documented defaults (IQR k = 1.5,
pairwise-correlation threshold 0.999, VIF threshold 1000, KPI std threshold
1e-4, default ROI prior LogNormal(0.2, 0.9)).
"""
from __future__ import annotations

import math
from typing import Dict, List, Optional

import numpy as np
import pandas as pd
from scipy import stats as sps

ERROR, ATTENTION, INFO, OK = "ERROR", "ATTENTION", "INFO", "OK"


# ---------------------------------------------------------------------------
# shared helpers
# ---------------------------------------------------------------------------

def _national(df: pd.DataFrame, cfg: dict, cols: List[str], how: str = "sum") -> pd.DataFrame:
    """Aggregate a geo panel to national level per time period."""
    t = cfg["time_col"]
    agg = {c: how for c in cols}
    return df.groupby(t, as_index=False).agg(agg).sort_values(t)


def _scale_by_population(df: pd.DataFrame, cfg: dict, col: str) -> pd.Series:
    pop = cfg.get("population_col")
    if cfg.get("geo_col") and pop and pop in df.columns:
        return df[col] / df[pop].replace(0, np.nan) * 1000.0  # per 1,000 population
    return df[col].astype(float)


def _standardize(v: pd.Series) -> pd.Series:
    sd = v.std(ddof=0)
    return (v - v.mean()) / (sd if sd > 1e-12 else 1.0)


def _iqr_bounds(v: np.ndarray, k: float):
    q1, q3 = np.nanpercentile(v, [25, 75])
    iqr = q3 - q1
    return q1 - k * iqr, q3 + k * iqr, q1, q3


def _box_stats(v: pd.Series, k: float) -> dict:
    x = v.dropna().to_numpy(dtype=float)
    if len(x) == 0:
        return {}
    lo, hi, q1, q3 = _iqr_bounds(x, k)
    inside = x[(x >= lo) & (x <= hi)]
    return {
        "min": float(np.min(x)), "whisker_lo": float(np.min(inside)) if len(inside) else float(np.min(x)),
        "q1": float(q1), "median": float(np.median(x)), "q3": float(q3),
        "whisker_hi": float(np.max(inside)) if len(inside) else float(np.max(x)),
        "max": float(np.max(x)), "n_outliers": int(((x < lo) | (x > hi)).sum()),
        "std": float(np.std(x)),
    }


def _r2_categorical(y: pd.Series, groups: pd.Series) -> float:
    """R^2 of regressing y on a categorical factor = 1 - SSwithin/SStotal."""
    y = y.astype(float)
    tot = ((y - y.mean()) ** 2).sum()
    if tot <= 1e-12:
        return 1.0
    within = y.groupby(groups.values).apply(lambda s: ((s - s.mean()) ** 2).sum()).sum()
    return float(max(0.0, 1.0 - within / tot))


def _fmt(n, d=1):
    if n is None or (isinstance(n, float) and not math.isfinite(n)):
        return "—"
    a = abs(n)
    if a >= 1e9: return f"{n/1e9:.{d}f}bn"
    if a >= 1e6: return f"{n/1e6:.{d}f}m"
    if a >= 1e3: return f"{n/1e3:.{d}f}k"
    return f"{n:.{d}f}"


def worst(statuses: List[str]) -> str:
    for s in (ERROR, ATTENTION, INFO):
        if s in statuses:
            return s
    return OK


# ---------------------------------------------------------------------------
# 1 · Spend and media units
# ---------------------------------------------------------------------------

def spend_and_media(df: pd.DataFrame, cfg: dict, iqr_k: float = 1.5,
                    small_share_pct: float = 2.0, n_knots: Optional[int] = None,
                    ratio_warn: float = 15.0) -> dict:
    t = cfg["time_col"]
    channels = cfg["channels"]  # [{name, spend_col, units_col|None}]
    findings, rows, series = [], [], {}

    spend_cols = [c["spend_col"] for c in channels if c.get("spend_col")]
    total_spend = float(df[spend_cols].sum().sum()) if spend_cols else 0.0

    for ch in channels:
        name, sc, uc = ch["name"], ch.get("spend_col"), ch.get("units_col")
        nat = _national(df, cfg, [c for c in (sc, uc) if c])
        sp = nat[sc].to_numpy(float) if sc else np.zeros(len(nat))
        un = nat[uc].to_numpy(float) if uc else None

        share = float(df[sc].sum()) / total_spend * 100 if (sc and total_spend > 0) else 0.0
        row = {"channel": name, "total_spend": float(df[sc].sum()) if sc else 0.0,
               "spend_share_pct": share,
               "total_units": float(df[uc].sum()) if uc else None,
               "avg_cost_per_1k_units": None, "cpu_outlier_weeks": 0,
               "mismatch_weeks": 0, "status": OK, "notes": []}

        if share < small_share_pct and sc:
            row["status"] = ATTENTION
            row["notes"].append(f"only {share:.1f}% of total spend — small channels are hard to estimate; consider combining")
            findings.append((ATTENTION, f"{name}: spend share {share:.1f}% < {small_share_pct}% — consider combining with a similar channel."))

        cpu = None
        if un is not None and sc:
            mism = int(((sp > 0) & (un <= 0)).sum() + ((un > 0) & (sp <= 0)).sum())
            row["mismatch_weeks"] = mism
            if mism:
                row["status"] = worst([row["status"], ATTENTION])
                row["notes"].append(f"{mism} weeks where spend and media units disagree (one is zero)")
                findings.append((ATTENTION, f"{name}: {mism} national weeks with zero spend but positive units, or vice-versa — check the data pipeline."))
            with np.errstate(divide="ignore", invalid="ignore"):
                cpu = np.where(un > 0, sp / un * 1000.0, np.nan)
            valid = cpu[np.isfinite(cpu)]
            if len(valid) > 8:
                lo, hi, *_ = _iqr_bounds(valid, iqr_k)
                out = int(((valid < lo) | (valid > hi)).sum())
                row["cpu_outlier_weeks"] = out
                row["avg_cost_per_1k_units"] = float(np.nanmean(valid))
                if out:
                    row["status"] = worst([row["status"], ATTENTION])
                    row["notes"].append(f"{out} cost-per-unit outlier weeks (IQR k={iqr_k})")
                    findings.append((ATTENTION, f"{name}: {out} weeks with outlying cost per 1k units — verify rate-card changes or data errors."))

        series[name] = {
            "dates": nat[t].astype(str).tolist(),
            "spend": [float(x) for x in sp],
            "units": [float(x) for x in un] if un is not None else None,
            "cost_per_1k": [None if (x is None or not np.isfinite(x)) else float(x) for x in (cpu if cpu is not None else [])],
        }
        rows.append(row)

    # data-to-parameter ratio (docs formula)
    n_geos = df[cfg["geo_col"]].nunique() if cfg.get("geo_col") else 1
    n_times = df[t].nunique()
    knots = int(n_knots) if n_knots else n_times
    n_controls = len(cfg.get("controls", []))
    n_treatments = len(channels) + len(cfg.get("organic", [])) + len(cfg.get("non_media", []))
    n_params = (n_geos - 1) + knots + n_controls + n_treatments
    ratio = (n_geos * n_times) / max(n_params, 1)
    ratio_status = OK if ratio >= ratio_warn else ATTENTION
    if ratio_status == ATTENTION:
        findings.append((ATTENTION, f"Data-to-parameter ratio {ratio:.1f} < {ratio_warn}: consider fewer knots, or dropping/combining channels."))

    rows.sort(key=lambda r: r["spend_share_pct"])
    small = [r for r in rows if r["spend_share_pct"] < small_share_pct]
    summary = (
        f"Total recorded media spend is {_fmt(total_spend)} across {len(channels)} paid channels over "
        f"{n_times} periods and {n_geos} {'brands' if n_geos>1 else 'market'}. "
        f"The biggest channel is {max(rows, key=lambda r: r['spend_share_pct'])['channel']} "
        f"({max(r['spend_share_pct'] for r in rows):.1f}% of spend); the smallest is "
        f"{rows[0]['channel']} ({rows[0]['spend_share_pct']:.1f}%). "
        + (f"{len(small)} channel(s) sit under the {small_share_pct}% share watch-line — their effects will be hard for any MMM "
           f"to separate from noise, so consider merging them into a sister channel. " if small else
           "No channel is small enough to threaten estimability. ")
        + (f"Cost-per-unit checks flagged {sum(r['cpu_outlier_weeks'] for r in rows)} outlier week(s) and "
           f"{sum(r['mismatch_weeks'] for r in rows)} spend/units mismatch week(s) — worth a pipeline check before modeling. "
           if any(r["cpu_outlier_weeks"] or r["mismatch_weeks"] for r in rows) else
           "Spend and delivered media units move together cleanly, with no cost-per-unit anomalies flagged. ")
        + f"With ~{knots} time knots the design implies {n_params} parameters for {n_geos*n_times} data points "
          f"(ratio {ratio:.1f}) — {'comfortable' if ratio >= ratio_warn else 'tight: simplify the design or add history'}."
    )
    return {"table": rows, "series": series, "spend_share": sorted(
                [{"channel": r["channel"], "share_pct": r["spend_share_pct"]} for r in rows],
                key=lambda x: -x["share_pct"]),
            "data_to_param": {"n_geos": n_geos, "n_times": n_times, "knots": knots,
                              "n_controls": n_controls, "n_treatments": n_treatments,
                              "n_params": n_params, "ratio": round(ratio, 2), "status": ratio_status},
            "findings": [{"severity": s, "text": txt} for s, txt in findings],
            "status": worst([f[0] for f in findings] or [OK]),
            "ai_summary": summary}


# ---------------------------------------------------------------------------
# 2 · Individual explanatory / response variables (box plots)
# ---------------------------------------------------------------------------

def variables(df: pd.DataFrame, cfg: dict, iqr_k: float = 1.5,
              std_threshold: float = 1e-4, scale_population: bool = True) -> dict:
    t, geo = cfg["time_col"], cfg.get("geo_col")
    findings = []

    def prep(col):
        v = _scale_by_population(df, cfg, col) if scale_population else df[col].astype(float)
        return _standardize(v)

    groups = {
        "media": [(c["name"], c.get("units_col") or c.get("spend_col")) for c in cfg["channels"]],
        "organic": [(c, c) for c in cfg.get("organic", [])],
        "controls": [(c, c) for c in cfg.get("controls", []) + cfg.get("non_media", [])],
        "kpi": [(cfg["kpi"], cfg["kpi"])],
    }
    boxes, details = {g: [] for g in groups}, []
    for g, items in groups.items():
        for name, col in items:
            if not col or col not in df.columns:
                continue
            scaled = prep(col)
            bs = _box_stats(scaled, iqr_k)
            bs["variable"] = name
            boxes[g].append(bs)

            raw = df[col].astype(float)
            lo, hi, *_ = _iqr_bounds(raw.to_numpy(), iqr_k)
            mask = (raw < lo) | (raw > hi)
            top = df.loc[mask, [t] + ([geo] if geo else []) + [col]].copy()
            top["dev"] = (raw[mask] - raw.median()).abs()
            top = top.sort_values("dev", ascending=False).head(5)
            top5 = [{"time": str(r[t]), "geo": str(r[geo]) if geo else "national",
                     "value": float(r[col])} for _, r in top.iterrows()]

            std_all = float(_standardize(raw).std(ddof=0))
            inside = raw[(raw >= lo) & (raw <= hi)]
            std_wo = float(inside.std(ddof=0)) if len(inside) else 0.0
            time_var = float(raw.groupby(df[t].values).mean().std(ddof=0)) if True else 0.0
            geo_var = float(raw.groupby(df[geo].values).mean().std(ddof=0)) if geo else None

            no_geo_variation = bool(geo) and bool(
                (df.groupby(df[t])[col].std(ddof=0).fillna(0) < 1e-12).all())
            status = OK
            if g == "kpi" and float(scaled.std(ddof=0)) < std_threshold:
                status = ERROR
                findings.append((ERROR, f"KPI '{name}' has essentially no variation (std < {std_threshold:g}) — no signal to model."))
            elif g != "kpi":
                if time_var < 1e-9:
                    status = ERROR
                    findings.append((ERROR, f"'{name}' is constant over time — it is collinear with the baseline and must be dropped (or use fewer knots)."))
                elif no_geo_variation and g == "controls":
                    status = ATTENTION
                    findings.append((ATTENTION, f"'{name}' varies over time but is identical across brands — with full time knots "
                                                f"(Meridian's geo default) it is unidentifiable. Set knots < number of periods, or drop it. "
                                                f"The Meridian engine here auto-reduces knots when it detects this."))
                elif std_wo < 1e-9 and bs.get("n_outliers", 0) > 0:
                    status = ATTENTION
                    findings.append((ATTENTION, f"'{name}' collapses to a constant once outliers are removed — a data-sparsity signature; verify or aggregate."))
                elif bs.get("n_outliers", 0) > 0:
                    status = INFO
            details.append({"variable": name, "group": g, "n_outliers": bs.get("n_outliers", 0),
                            "std_scaled": round(float(scaled.std(ddof=0)), 6),
                            "std_wo_outliers": round(std_wo, 6),
                            "time_variation": round(time_var, 4),
                            "geo_variation": round(geo_var, 4) if geo_var is not None else None,
                            "top_outliers": top5, "status": status})

    n_out = sum(d["n_outliers"] for d in details)
    worst_var = max(details, key=lambda d: d["n_outliers"]) if details else None
    summary = (
        f"Box-plot screening covered {len(details)} variables ({len(boxes['media'])} paid media, "
        f"{len(boxes['organic'])} organic, {len(boxes['controls'])} controls/treatments, and the KPI), "
        f"{'population-scaled then ' if scale_population and geo else ''}standardized as Meridian does before fitting. "
        + (f"No variable fails the variation checks — every series carries usable signal. " if not any(f[0] == ERROR for f in findings)
           else "At least one variable fails the variation check and would break or bias the model — fix before fitting. ")
        + f"The IQR rule (k={iqr_k}) marks {n_out} observation(s) as outliers"
        + (f", concentrated in '{worst_var['variable']}' ({worst_var['n_outliers']} points) — eyeball its top outliers "
           f"in the table; genuine spikes (Black Friday, TV bursts) can stay, data errors should be corrected. "
           if worst_var and worst_var["n_outliers"] else ". ")
        + "Meridian tolerates outliers better than hard zeros in the KPI, so prefer correcting obvious logging errors over trimming real demand spikes."
    )
    return {"boxes": boxes, "details": details,
            "findings": [{"severity": s, "text": txt} for s, txt in findings],
            "status": worst([f[0] for f in findings] or [OK]),
            "ai_summary": summary}


# ---------------------------------------------------------------------------
# 3 · Population scaling of explanatory variables
# ---------------------------------------------------------------------------

def population_scaling(df: pd.DataFrame, cfg: dict, high_corr: float = 0.6) -> dict:
    geo, pop = cfg.get("geo_col"), cfg.get("population_col")
    if not geo or not pop or pop not in df.columns:
        return {"applicable": False, "status": INFO, "findings": [],
                "ai_summary": "Population scaling applies to geo-level datasets only. This file is national "
                              "(single market, population treated as 1.0), so Meridian will skip population scaling — nothing to review here."}

    per_geo = df.groupby(geo).agg(population=(pop, "first"))
    raw_rows, scaled_rows = [], []
    findings = []

    def spearman(a, b):
        if len(a) < 3 or np.std(a) < 1e-12 or np.std(b) < 1e-12:
            return None
        r, _ = sps.spearmanr(a, b)
        return None if not np.isfinite(r) else float(r)

    media_items = [(c["name"], c.get("units_col") or c.get("spend_col")) for c in cfg["channels"]] + \
                  [(c, c) for c in cfg.get("organic", [])]
    for name, col in media_items:
        m = df.groupby(geo)[col].mean()
        r = spearman(per_geo["population"].values, m.reindex(per_geo.index).values)
        status = OK if (r is not None and r > 0) else INFO
        if r is not None and r <= 0:
            findings.append((INFO, f"Raw media '{name}' does not rise with population (ρ={r:.2f}) — unusual; check whether the data is already per-capita or mis-joined."))
        raw_rows.append({"variable": name, "spearman_rho": None if r is None else round(r, 3), "status": status})

    for col in cfg.get("controls", []) + cfg.get("non_media", []):
        m = df.groupby(geo)[col].mean()
        r = spearman(per_geo["population"].values, m.reindex(per_geo.index).values)
        flag = r is not None and abs(r) >= high_corr
        if flag:
            findings.append((INFO, f"Control '{col}' scales with population (ρ={r:.2f}) — consider population scaling for it "
                                    f"(Meridian: control_population_scaling_id / non_media_population_scaling_id)."))
        scaled_rows.append({"variable": col, "spearman_rho": None if r is None else round(r, 3),
                            "suggest_population_scaling": bool(flag)})

    percap = []
    kpi = cfg["kpi"]
    for g, sub in df.groupby(geo):
        p = float(sub[pop].iloc[0])
        percap.append({"geo": str(g), "population": p,
                       "kpi_per_1k": float(sub[kpi].sum() / p * 1000.0),
                       "media_units_per_1k": float(sum(sub[c.get("units_col") or c["spend_col"]].sum() for c in cfg["channels"]) / p * 1000.0),
                       "spend_per_1k": float(sum(sub[c["spend_col"]].sum() for c in cfg["channels"] if c.get("spend_col")) / p * 1000.0)})

    pos = sum(1 for r in raw_rows if (r["spearman_rho"] or 0) > 0)
    sugg = [r["variable"] for r in scaled_rows if r["suggest_population_scaling"]]
    spread = max(p["kpi_per_1k"] for p in percap) / max(min(p["kpi_per_1k"] for p in percap), 1e-9)
    summary = (
        f"{pos} of {len(raw_rows)} media variables correlate positively with brand population, as expected — "
        f"bigger addressable bases receive more media. "
        + (f"Controls {', '.join(sugg)} track population strongly; telling Meridian to population-scale them "
           f"will stop size differences masquerading as effects. " if sugg else
           "No control variable tracks population strongly, so default scaling settings are fine. ")
        + f"Per-capita intensity varies {spread:.1f}× between the strongest and weakest brand — Meridian's population "
          f"scaling puts brands on a comparable footing, which is exactly why the population column matters. "
          "Verify the population figures are current; stale numbers bias every per-capita comparison."
    )
    return {"applicable": True, "raw_media_vs_population": raw_rows,
            "controls_vs_population": scaled_rows, "per_capita": percap,
            "findings": [{"severity": s, "text": txt} for s, txt in findings],
            "status": worst([f[0] for f in findings] or [OK]),
            "ai_summary": summary}


# ---------------------------------------------------------------------------
# 4 · Relationships among variables
# ---------------------------------------------------------------------------

def relationships(df: pd.DataFrame, cfg: dict, corr_error: float = 0.999,
                  corr_attention: float = 0.90, vif_threshold: float = 1000.0,
                  vif_std_threshold: float = 1e-4) -> dict:
    t, geo = cfg["time_col"], cfg.get("geo_col")
    findings = []

    treat = [(c["name"], c.get("units_col") or c.get("spend_col")) for c in cfg["channels"]]
    treat += [(c, c) for c in cfg.get("organic", [])] + [(c, c) for c in cfg.get("non_media", [])]
    ctrl = [(c, c) for c in cfg.get("controls", [])]
    items = treat + ctrl

    nat = _national(df, cfg, [c for _, c in items])
    X = pd.DataFrame({name: _standardize(nat[col].astype(float)) for name, col in items})
    X = X.loc[:, X.std(ddof=0) > 1e-12]
    names = list(X.columns)

    corr = X.corr().round(3)
    pairs = []
    for i in range(len(names)):
        for j in range(i + 1, len(names)):
            r = float(corr.iloc[i, j])
            if abs(r) >= corr_attention:
                sev = ERROR if abs(r) >= corr_error else ATTENTION
                pairs.append({"a": names[i], "b": names[j], "corr": r, "severity": sev})
                findings.append((sev, f"|corr({names[i]}, {names[j]})| = {abs(r):.3f}"
                                      f"{' ≥ ' + str(corr_error) + ' — effectively duplicates; remove one.' if sev == ERROR else ' — high; consider combining or dropping one.'}"))

    # VIF via inverse correlation matrix (variables with usable std only)
    vif_rows = []
    usable = [n for n in names if X[n].std(ddof=0) >= vif_std_threshold]
    if len(usable) >= 2:
        C = X[usable].corr().to_numpy()
        try:
            inv = np.linalg.pinv(C)
            for k, n in enumerate(usable):
                v = float(inv[k, k])
                sev = ERROR if v >= vif_threshold else (ATTENTION if v >= 10 else OK)
                if sev == ERROR:
                    findings.append((ERROR, f"VIF({n}) = {v:,.0f} ≥ {vif_threshold:,.0f} — near-perfect linear combination; drop or combine."))
                elif sev == ATTENTION:
                    findings.append((ATTENTION, f"VIF({n}) = {v:.1f} — notable multicollinearity; expect wide credible intervals."))
                vif_rows.append({"variable": n, "vif": round(v, 2), "status": sev})
        except Exception:
            pass

    # collinearity with geo / time main effects
    geo_r2, time_r2 = [], []
    for name, col in items:
        v = df[col].astype(float)
        if geo:
            geo_r2.append({"variable": name, "r2_vs_geo": round(_r2_categorical(v, df[geo]), 3)})
        time_r2.append({"variable": name, "r2_vs_time": round(_r2_categorical(v, df[t]), 3)})
    geo_r2.sort(key=lambda r: -r["r2_vs_geo"]) if geo_r2 else None
    time_r2.sort(key=lambda r: -r["r2_vs_time"])

    hi_geo = [r for r in (geo_r2 or []) if r["r2_vs_geo"] > 0.95]
    for r in hi_geo[:3]:
        findings.append((INFO, f"'{r['variable']}' is mostly explained by brand identity (R²={r['r2_vs_geo']}) — low time variation weakens identifiability."))

    n_bad = len([p for p in pairs if p["severity"] == ERROR])
    max_vif = max((r["vif"] for r in vif_rows), default=1.0)
    summary = (
        f"The correlation screen compared {len(names)} scaled variables at national level. "
        + (f"{len(pairs)} pair(s) exceed |r| ≥ {corr_attention}"
           + (f", of which {n_bad} breach the {corr_error} redundancy line and must be resolved before fitting. " if n_bad
              else " — none at redundancy level, but watch the flagged pairs: correlated inputs share credit and widen intervals. ")
           if pairs else f"No variable pair exceeds |r| ≥ {corr_attention} — an unusually clean design. ")
        + f"The worst variance-inflation factor is {max_vif:,.1f} "
        + ("(fine — VIF near 1 means effects are separable). " if max_vif < 10 else
           f"(threshold {vif_threshold:,.0f}; values this high mean the model cannot tell these variables apart). ")
        + ("Several variables are near-constant within brands over time — Meridian's geo effects will absorb them; "
           "make sure the ones you care about vary over time. " if hi_geo else "")
        + "Rule of thumb: fix ERRORs by dropping/combining, accept ATTENTIONs consciously, and lean on informative priors where media channels overlap."
    )
    return {"variables": names, "corr_matrix": corr.values.tolist(),
            "flagged_pairs": pairs, "vif": vif_rows,
            "geo_r2": (geo_r2 or [])[:5], "time_r2": time_r2[:5],
            "findings": [{"severity": s, "text": txt} for s, txt in findings],
            "status": worst([f[0] for f in findings] or [OK]),
            "ai_summary": summary}


# ---------------------------------------------------------------------------
# 5 · Prior specifications
# ---------------------------------------------------------------------------

def priors(df: pd.DataFrame, cfg: dict, prior_spec: Optional[Dict[str, dict]] = None,
           mc_draws: int = 4000, neg_attention: float = 0.2, neg_error: float = 0.5) -> dict:
    """ROI priors per channel (LogNormal), prior contribution means, and the prior
    probability of a negative baseline — Meridian's Category-5 checks."""
    rng = np.random.default_rng(11)
    channels = cfg["channels"]
    kpi_total = float(df[cfg["kpi"]].sum())
    rpk = cfg.get("revenue_per_kpi")
    if cfg.get("kpi_type") == "non_revenue" and rpk and rpk in df.columns:
        outcome_total = float((df[cfg["kpi"]] * df[rpk]).sum())
        outcome_label = "revenue (KPI × revenue-per-KPI)"
    else:
        outcome_total = kpi_total
        outcome_label = cfg["kpi"]

    prior_spec = prior_spec or {}
    rows, draws_sum = [], np.zeros(mc_draws)
    grid = np.linspace(0.01, 12, 160)
    densities = {}
    for ch in channels:
        name = ch["name"]
        p = prior_spec.get(name, {})
        mu, sigma = float(p.get("mu", 0.2)), float(p.get("sigma", 0.9))
        spend = float(df[ch["spend_col"]].sum()) if ch.get("spend_col") else 0.0
        mean_roi = math.exp(mu + sigma**2 / 2)
        med_roi = math.exp(mu)
        lo, hi = math.exp(mu - 1.645 * sigma), math.exp(mu + 1.645 * sigma)
        contrib_share = mean_roi * spend / outcome_total if outcome_total > 0 else 0.0
        rows.append({"channel": name, "mu": mu, "sigma": sigma,
                     "prior_roi_median": round(med_roi, 2), "prior_roi_mean": round(mean_roi, 2),
                     "prior_roi_90ci": [round(lo, 2), round(hi, 2)],
                     "spend": spend, "prior_mean_contribution_pct": round(contrib_share * 100, 1)})
        draws = rng.lognormal(mu, sigma, mc_draws) * spend
        draws_sum += draws
        pdf = sps.lognorm.pdf(grid, s=sigma, scale=math.exp(mu))
        densities[name] = {"x": grid.round(3).tolist(), "y": [float(v) for v in pdf]}

    neg_baseline_prob = float((draws_sum > outcome_total).mean()) if outcome_total > 0 else None
    total_prior_share = sum(r["prior_mean_contribution_pct"] for r in rows)
    status = OK
    findings = []
    if neg_baseline_prob is not None:
        if neg_baseline_prob >= neg_error:
            status = ERROR
            findings.append((ERROR, f"Prior probability of a negative baseline is {neg_baseline_prob:.0%} — the ROI priors "
                                    f"claim more outcome than exists. Lower mu/sigma or switch to contribution priors."))
        elif neg_baseline_prob >= neg_attention:
            status = ATTENTION
            findings.append((ATTENTION, f"Prior probability of a negative baseline is {neg_baseline_prob:.0%} — consider tighter "
                                        f"custom priors (Meridian docs suggest a custom 'contribution' prior type)."))

    summary = (
        f"With the current priors every channel's ROI is centred near {rows[0]['prior_roi_median'] if rows else '—'} "
        f"(LogNormal defaults μ=0.2, σ=0.9 give median ≈1.2, mean ≈1.8, 90% CI ≈0.3–5.4) unless you edited it. "
        f"Taken together the priors expect media to explain about {total_prior_share:.0f}% of total {outcome_label}, "
        f"which leaves a prior probability of {neg_baseline_prob:.0%} that the implied baseline goes negative. "
        + ("That is comfortably low — the priors are letting the data speak without contradicting it. "
           if (neg_baseline_prob or 0) < neg_attention else
           "That is high: your priors and your data disagree about how much of the business media can explain — tighten σ, lower μ, or use contribution priors. ")
        + "Where you have lift tests or past MMM results for a channel, encode them: set μ = ln(expected ROI) and shrink σ "
          "(0.3–0.5 ≈ strong evidence, 0.7 ≈ directional, 0.9 = default vague)."
    )
    return {"channels": rows, "densities": densities,
            "prior_negative_baseline_prob": neg_baseline_prob,
            "outcome_total": outcome_total, "outcome_label": outcome_label,
            "findings": [{"severity": s, "text": txt} for s, txt in findings],
            "status": status, "ai_summary": summary}


# ---------------------------------------------------------------------------
# complete analysis
# ---------------------------------------------------------------------------

def complete_analysis(sections: Dict[str, dict], cfg: dict, df: pd.DataFrame) -> dict:
    sev_rank = {ERROR: 0, ATTENTION: 1, INFO: 2, OK: 3}
    all_findings = []
    for sec, out in sections.items():
        for f in out.get("findings", []):
            all_findings.append({"section": sec, **f})
    all_findings.sort(key=lambda f: sev_rank.get(f["severity"], 9))
    counts = {s: sum(1 for f in all_findings if f["severity"] == s) for s in (ERROR, ATTENTION, INFO)}
    overall = ERROR if counts[ERROR] else (ATTENTION if counts[ATTENTION] else (INFO if counts[INFO] else OK))

    n_geos = df[cfg["geo_col"]].nunique() if cfg.get("geo_col") else 1
    n_times = df[cfg["time_col"]].nunique()
    verdict = {
        ERROR: "NOT ready to model — resolve the ERROR items first; Meridian would either fail to converge or silently mis-attribute.",
        ATTENTION: "Ready to model with caution — the ATTENTION items won't block a run, but read them before trusting channel-level results.",
        INFO: "Ready to model — only informational notes remain.",
        OK: "Ready to model — the dataset passes every check cleanly.",
    }[overall]
    summary = (
        f"Verdict: {verdict} The panel holds {n_geos} brand(s) × {n_times} weekly periods "
        f"({n_geos * n_times} observations) against {len(cfg['channels'])} paid channels, "
        f"{len(cfg.get('organic', []))} organic signal(s), {len(cfg.get('non_media', []))} non-media treatment(s) and "
        f"{len(cfg.get('controls', []))} controls. Across the five EDA categories the checks raised "
        f"{counts[ERROR]} error(s), {counts[ATTENTION]} attention item(s) and {counts[INFO]} informational note(s). "
        "Suggested order of work: fix pipeline mismatches and redundant variables first, then revisit small-spend channels "
        "(combine or accept wide intervals), then encode any experiment-based knowledge into the ROI priors — priors are "
        "the cheapest accuracy you can buy in MMM. When the ERROR count is zero, proceed to the Model tab; start with "
        "default sampling, check R-hat < 1.1, then iterate."
    )
    return {"overall_status": overall, "counts": counts,
            "section_status": {k: v.get("status", OK) for k, v in sections.items()},
            "findings": all_findings, "verdict": verdict, "ai_summary": summary}
