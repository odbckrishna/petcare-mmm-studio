"""Marketing Mix Modeling engine.

Pipeline
--------
1. Geometric adstock per media channel (carry-over of advertising effect).
2. Hill saturation per media channel (diminishing returns).
3. Bounded ridge regression: media coefficients constrained >= 0, controls /
   trend / seasonality unconstrained. Ridge implemented as data augmentation
   so scipy.optimize.lsq_linear can enforce the bounds.
4. Hyperparameters (decay, half-saturation, ridge alpha) picked by coordinate
   grid search against a time-ordered holdout (last 20% of weeks).
5. Outputs: fit quality, weekly decomposition, channel contribution & ROI,
   response curves, and a budget optimizer over the fitted curves.

Everything is pure numpy/pandas/scipy — no heavyweight MMM frameworks — so it
runs fast on a laptop and is easy to audit.
"""
from __future__ import annotations

from dataclasses import dataclass, field
from typing import Dict, List, Optional

import numpy as np
import pandas as pd
from scipy.optimize import lsq_linear, minimize

# ----------------------------------------------------------------------------
# transforms
# ----------------------------------------------------------------------------

def geometric_adstock(x: np.ndarray, decay: float) -> np.ndarray:
    """Carry-over: a[t] = x[t] + decay * a[t-1]."""
    out = np.zeros(len(x), dtype=float)
    carry = 0.0
    for i, v in enumerate(np.asarray(x, dtype=float)):
        carry = v + decay * carry
        out[i] = carry
    return out


def hill(x: np.ndarray, half_sat: float, shape: float = 1.2) -> np.ndarray:
    """Hill saturation mapped to [0, 1). half_sat = spend level giving 50% of max effect."""
    x = np.clip(np.asarray(x, dtype=float), 0.0, None)
    hs = max(float(half_sat), 1e-9)
    return x**shape / (x**shape + hs**shape)


def media_transform(spend: np.ndarray, decay: float, half_sat: float, shape: float = 1.2) -> np.ndarray:
    return hill(geometric_adstock(spend, decay), half_sat, shape)


# ----------------------------------------------------------------------------
# bounded ridge
# ----------------------------------------------------------------------------

def _ridge_bounded(X: np.ndarray, y: np.ndarray, alpha: float, lower: np.ndarray, upper: np.ndarray):
    """Ridge via augmentation: minimise ||Xb - y||^2 + alpha*||b||^2 subject to bounds.

    The intercept column (assumed first) is not penalised.
    """
    n, p = X.shape
    pen = np.sqrt(alpha) * np.eye(p)
    pen[0, 0] = 0.0  # do not shrink the intercept
    X_aug = np.vstack([X, pen])
    y_aug = np.concatenate([y, np.zeros(p)])
    res = lsq_linear(X_aug, y_aug, bounds=(lower, upper), lsmr_tol="auto", max_iter=200)
    return res.x


@dataclass
class ChannelParams:
    decay: float = 0.4
    half_sat_q: float = 0.5   # quantile of positive adstocked spend
    shape: float = 1.2
    half_sat: float = 0.0      # resolved absolute value (set during fit)


@dataclass
class ModelResult:
    kpi: str
    date_col: str
    channels: List[str]
    controls: List[str]
    params: Dict[str, ChannelParams]
    coefs: Dict[str, float]
    alpha: float
    r2: float
    holdout_mape: float
    dates: List[str]
    actual: List[float]
    predicted: List[float]
    baseline: List[float]                      # intercept + trend + season + controls
    contributions: Dict[str, List[float]]      # weekly media contributions
    channel_summary: pd.DataFrame = field(repr=False, default=None)

    def summary_records(self) -> List[dict]:
        return self.channel_summary.round(4).to_dict(orient="records")


# ----------------------------------------------------------------------------
# design matrix
# ----------------------------------------------------------------------------

def _seasonal_matrix(dates: pd.Series) -> np.ndarray:
    """Trend + annual Fourier terms (2 harmonics)."""
    n = len(dates)
    t = np.arange(n, dtype=float)
    woy = pd.to_datetime(dates).dt.isocalendar().week.to_numpy(dtype=float)
    cols = [t / max(n - 1, 1)]
    for k in (1, 2):
        cols.append(np.sin(2 * np.pi * k * woy / 52.0))
        cols.append(np.cos(2 * np.pi * k * woy / 52.0))
    return np.column_stack(cols)


SEASON_NAMES = ["trend", "sin1", "cos1", "sin2", "cos2"]


def _build_X(df: pd.DataFrame, date_col: str, channels: List[str], controls: List[str],
             params: Dict[str, ChannelParams]):
    n = len(df)
    media_cols = []
    for ch in channels:
        p = params[ch]
        ad = geometric_adstock(df[ch].to_numpy(), p.decay)
        pos = ad[ad > 0]
        p.half_sat = float(np.quantile(pos, p.half_sat_q)) if len(pos) else 1.0
        media_cols.append(hill(ad, p.half_sat, p.shape))
    media = np.column_stack(media_cols) if media_cols else np.zeros((n, 0))

    ctrl_arrays, ctrl_scale = [], {}
    for c in controls:
        v = df[c].to_numpy(dtype=float)
        mu, sd = float(np.mean(v)), float(np.std(v))
        sd = sd if sd > 1e-12 else 1.0
        ctrl_scale[c] = (mu, sd)
        ctrl_arrays.append((v - mu) / sd)
    ctrl = np.column_stack(ctrl_arrays) if ctrl_arrays else np.zeros((n, 0))

    season = _seasonal_matrix(df[date_col])
    intercept = np.ones((n, 1))
    X = np.hstack([intercept, media, ctrl, season])
    names = ["intercept"] + channels + controls + SEASON_NAMES
    return X, names, ctrl_scale


def _fit_once(df, date_col, kpi, channels, controls, params, alpha, holdout_frac=0.2):
    X, names, _ = _build_X(df, date_col, channels, controls, params)
    y = df[kpi].to_numpy(dtype=float)
    p = X.shape[1]
    lower = np.full(p, -np.inf)
    upper = np.full(p, np.inf)
    for i, nm in enumerate(names):
        if nm in channels:
            lower[i] = 0.0  # media effects cannot be negative

    cut = max(int(len(y) * (1 - holdout_frac)), 10)
    beta = _ridge_bounded(X[:cut], y[:cut], alpha, lower, upper)
    pred_hold = X[cut:] @ beta
    denom = np.clip(np.abs(y[cut:]), 1e-9, None)
    mape = float(np.mean(np.abs((y[cut:] - pred_hold) / denom)))
    return mape


DECAY_GRID = [0.0, 0.15, 0.3, 0.45, 0.6, 0.75]
HALFSAT_Q_GRID = [0.3, 0.5, 0.75]
ALPHA_GRID = [0.1, 1.0, 10.0, 100.0]


def fit_mmm(df: pd.DataFrame, date_col: str, kpi: str, channels: List[str],
            controls: Optional[List[str]] = None, passes: int = 2,
            progress=None) -> ModelResult:
    """Fit the MMM with coordinate grid search over per-channel hyperparameters."""
    controls = controls or []
    df = df.sort_values(date_col).reset_index(drop=True)
    params = {ch: ChannelParams() for ch in channels}
    alpha = 1.0

    def report(msg):
        if progress:
            progress(msg)

    # coordinate search: per channel, try decay x half_sat grid keeping others fixed
    for sweep in range(passes):
        for ch in channels:
            best = (None, np.inf)
            for d in DECAY_GRID:
                for q in HALFSAT_Q_GRID:
                    trial = {k: ChannelParams(v.decay, v.half_sat_q, v.shape) for k, v in params.items()}
                    trial[ch] = ChannelParams(d, q)
                    mape = _fit_once(df, date_col, kpi, channels, controls, trial, alpha)
                    if mape < best[1]:
                        best = ((d, q), mape)
            params[ch] = ChannelParams(*best[0])
            report(f"pass {sweep+1}: {ch} -> decay={best[0][0]}, half_sat_q={best[0][1]} (holdout MAPE {best[1]:.3f})")

    best_alpha = (alpha, np.inf)
    for a in ALPHA_GRID:
        mape = _fit_once(df, date_col, kpi, channels, controls, params, a)
        if mape < best_alpha[1]:
            best_alpha = (a, mape)
    alpha, holdout_mape = best_alpha
    report(f"alpha={alpha} (holdout MAPE {holdout_mape:.3f})")

    # final fit on all data
    X, names, _ = _build_X(df, date_col, channels, controls, params)
    y = df[kpi].to_numpy(dtype=float)
    p = X.shape[1]
    lower = np.full(p, -np.inf)
    upper = np.full(p, np.inf)
    for i, nm in enumerate(names):
        if nm in channels:
            lower[i] = 0.0
    beta = _ridge_bounded(X, y, alpha, lower, upper)
    coefs = dict(zip(names, beta))
    predicted = X @ beta

    ss_res = float(np.sum((y - predicted) ** 2))
    ss_tot = float(np.sum((y - np.mean(y)) ** 2))
    r2 = 1.0 - ss_res / max(ss_tot, 1e-9)

    # decomposition
    contributions: Dict[str, List[float]] = {}
    media_total = np.zeros(len(y))
    for ch in channels:
        j = names.index(ch)
        contrib = X[:, j] * beta[j]
        contributions[ch] = contrib.tolist()
        media_total += contrib
    baseline = predicted - media_total

    rows = []
    for ch in channels:
        spend = float(df[ch].sum())
        contrib = float(np.sum(contributions[ch]))
        rows.append({
            "channel": ch,
            "total_spend": spend,
            "total_contribution": contrib,
            "contribution_share_of_media": 0.0,
            "roi": contrib / spend if spend > 0 else 0.0,
            "decay": params[ch].decay,
            "half_sat_spend": params[ch].half_sat,
            "coef_max_effect": float(coefs[ch]),
            "avg_weekly_spend": float(df[ch].mean()),
        })
    summary = pd.DataFrame(rows)
    tot = summary["total_contribution"].sum()
    if tot > 0:
        summary["contribution_share_of_media"] = summary["total_contribution"] / tot

    dates = pd.to_datetime(df[date_col]).dt.strftime("%Y-%m-%d").tolist()
    return ModelResult(
        kpi=kpi, date_col=date_col, channels=channels, controls=controls,
        params=params, coefs={k: float(v) for k, v in coefs.items()}, alpha=float(alpha),
        r2=float(r2), holdout_mape=float(holdout_mape), dates=dates,
        actual=y.tolist(), predicted=predicted.tolist(), baseline=baseline.tolist(),
        contributions=contributions, channel_summary=summary,
    )


# ----------------------------------------------------------------------------
# response curves & budget optimisation
# ----------------------------------------------------------------------------

def steady_state_response(result: ModelResult, channel: str, weekly_spend: float) -> float:
    """Expected weekly KPI contribution at a constant weekly spend level."""
    p = result.params[channel]
    equilibrium_adstock = weekly_spend / max(1.0 - p.decay, 1e-9)
    return result.coefs[channel] * float(hill(np.array([equilibrium_adstock]), p.half_sat, p.shape)[0])


def response_curve(result: ModelResult, channel: str, max_weekly_spend: float, points: int = 40):
    xs = np.linspace(0, max_weekly_spend, points)
    ys = [steady_state_response(result, channel, float(s)) for s in xs]
    return xs.tolist(), ys


def optimize_budget(result: ModelResult, total_weekly_budget: float,
                    min_share: float = 0.0, max_share: float = 1.0) -> pd.DataFrame:
    """Allocate a weekly budget across channels to maximise steady-state KPI."""
    chs = result.channels
    k = len(chs)
    if k == 0 or total_weekly_budget <= 0:
        return pd.DataFrame()

    def neg_total(b):
        return -sum(steady_state_response(result, ch, max(bi, 0.0)) for ch, bi in zip(chs, b))

    current = result.channel_summary.set_index("channel")["avg_weekly_spend"].reindex(chs).to_numpy()
    x0 = (current / current.sum() * total_weekly_budget) if current.sum() > 0 else np.full(k, total_weekly_budget / k)
    bounds = [(min_share * total_weekly_budget, max_share * total_weekly_budget)] * k
    cons = [{"type": "eq", "fun": lambda b: np.sum(b) - total_weekly_budget}]
    res = minimize(neg_total, x0, bounds=bounds, constraints=cons, method="SLSQP",
                   options={"maxiter": 500, "ftol": 1e-9})
    alloc = np.clip(res.x, 0, None)
    if alloc.sum() > 0:
        alloc = alloc * (total_weekly_budget / alloc.sum())

    rows = []
    for ch, b in zip(chs, alloc):
        current = result.channel_summary.loc[result.channel_summary.channel == ch, "avg_weekly_spend"].iloc[0]
        rows.append({
            "channel": ch,
            "current_avg_weekly_spend": float(current),
            "recommended_weekly_spend": float(b),
            "change_pct": float((b - current) / current * 100) if current > 0 else np.inf,
            "expected_weekly_contribution": steady_state_response(result, ch, float(b)),
            "current_weekly_contribution": steady_state_response(result, ch, float(current)),
        })
    out = pd.DataFrame(rows)
    out.attrs["expected_total"] = float(out["expected_weekly_contribution"].sum())
    out.attrs["current_total"] = float(out["current_weekly_contribution"].sum())
    return out
