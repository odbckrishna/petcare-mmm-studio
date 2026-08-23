"""Petcare MMM Studio — FastAPI backend.

Serves the branded web UI at /  and a JSON API under /api/*.
Everything runs locally; data never leaves the machine.
"""
from __future__ import annotations

import math
import os
import re
import shutil
from typing import Any, Dict, Optional

from fastapi import Body, FastAPI, File, HTTPException, UploadFile
from fastapi.responses import FileResponse, JSONResponse
from fastapi.staticfiles import StaticFiles

from . import data_loader as dl
from . import eda as eda_mod
from . import meridian_adapter as ma
from .mmm import ModelResult, fit_mmm, optimize_budget, response_curve
from .schemas import LoadRequest, OptimizeRequest, RunRequest

BASE_DIR = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
UI_DIR = os.path.join(BASE_DIR, "ui")
TPL_DIR = os.path.join(BASE_DIR, "templates")
REPORTS_DIR = os.path.join(BASE_DIR, "reports")
os.makedirs(REPORTS_DIR, exist_ok=True)

app = FastAPI(title="Petcare MMM Studio", version="2.0.0")
app.mount("/vendor", StaticFiles(directory=os.path.join(UI_DIR, "vendor")), name="vendor")
app.mount("/reports", StaticFiles(directory=REPORTS_DIR), name="reports")

# ----------------------------------------------------------------------------
# in-memory session state
# ----------------------------------------------------------------------------
STATE: Dict[str, Any] = {"df": None, "file": None, "mapping": None, "result": None,
                         "meridian_mapping": None, "prior_spec": {},
                         # long / campaign-grain mode
                         "raw_long": None, "is_long": False, "kpi_choice": "REVENUE",
                         "filters": {}}


def _san(obj: Any) -> Any:
    """Make payloads strictly JSON-safe (no NaN/inf)."""
    if isinstance(obj, dict):
        return {k: _san(v) for k, v in obj.items()}
    if isinstance(obj, (list, tuple)):
        return [_san(v) for v in obj]
    if isinstance(obj, float):
        return obj if math.isfinite(obj) else None
    return obj


def _require_df():
    if STATE["df"] is None:
        raise HTTPException(400, "No dataset loaded. Load an Excel file first.")
    return STATE["df"]


def _require_result() -> ModelResult:
    if STATE["result"] is None:
        raise HTTPException(400, "No model has been run yet.")
    return STATE["result"]


# ----------------------------------------------------------------------------
# endpoints
# ----------------------------------------------------------------------------
@app.get("/api/health")
def health():
    return {"status": "ok", "app": "Petcare MMM Studio"}


@app.get("/api/files")
def files():
    return {"files": dl.list_excel_files(), "data_dir": os.path.abspath(dl.DATA_DIR)}


@app.post("/api/upload")
async def upload(file: UploadFile = File(...)):
    if not file.filename or not file.filename.lower().endswith((".xlsx", ".xlsm", ".xls")):
        raise HTTPException(400, "Please upload an Excel file (.xlsx / .xlsm / .xls)")
    safe = re.sub(r"[^A-Za-z0-9._ -]", "_", os.path.basename(file.filename))
    os.makedirs(dl.DATA_DIR, exist_ok=True)
    dest = os.path.join(dl.DATA_DIR, safe)
    with open(dest, "wb") as fh:
        shutil.copyfileobj(file.file, fh)
    return _load(dest)


def _rebuild_panel(kpi: Optional[str] = None,
                   filters: Optional[Dict[str, Any]] = None) -> Dict[str, Any]:
    """Re-materialize the modeling panel from the raw long frame.

    Called on load and whenever the KPI or a report filter changes: filtering
    happens before the pivot, so each report is a true re-aggregation.
    """
    raw = STATE["raw_long"]
    if raw is None:
        raise HTTPException(400, "No long-format dataset loaded.")
    if kpi is not None:
        STATE["kpi_choice"] = kpi
    if filters is not None:
        STATE["filters"] = filters
    try:
        mm = dl.detect_long_mapping(raw, kpi=STATE["kpi_choice"],
                                    filters=STATE["filters"])
        panel = dl.pivot_long_to_panel(raw, kpi=STATE["kpi_choice"],
                                       filters=STATE["filters"])
    except ValueError as e:
        raise HTTPException(400, str(e))
    mapping = {"date_col": mm["time_col"], "kpi": mm["kpi"],
               "channels": [c["spend_col"] or c["units_col"] for c in mm["channels"]],
               "controls": mm["controls"]}
    STATE.update(df=panel, mapping=mapping, meridian_mapping=mm, result=None)
    return mm


def _load(path: str, sheet: Optional[str] = None):
    try:
        df = dl.load_excel(path, sheet)
    except FileNotFoundError as e:
        raise HTTPException(404, str(e))
    except Exception as e:  # unreadable / corrupt workbook
        raise HTTPException(400, f"Could not read Excel file: {e}")

    # Long / campaign-grain files are pivoted into a BRAND x week panel; the raw
    # frame is kept so filters re-aggregate from the true grain.
    if dl.is_long_format(df):
        raw = dl.normalize_long(df)
        kpi = next((k for k in dl.KPI_CANDIDATES if k in raw.columns), None)
        if kpi is None:
            raise HTTPException(400, "Long-format file has no KPI column "
                                     f"({', '.join(dl.KPI_CANDIDATES)}).")
        STATE.update(raw_long=raw, is_long=True, kpi_choice=kpi, filters={},
                     file=os.path.basename(path))
        mm = _rebuild_panel()
        panel = STATE["df"]
        if len(panel) < 30:
            raise HTTPException(400, f"Only {len(panel)} usable periods after "
                                     f"aggregation — MMM needs at least ~30.")
        return _san({"file": STATE["file"], **dl.summarize(panel, STATE["mapping"]),
                     "meridian_mapping": mm, "meridian": ma.available(),
                     "is_long": True, "long_rows": int(len(raw)),
                     "kpi_options": mm["kpi_options"], "kpi_choice": STATE["kpi_choice"],
                     "facets": mm["facets"], "filters": STATE["filters"]})

    STATE.update(raw_long=None, is_long=False, filters={})
    mapping = dl.detect_columns(df)
    mm = dl.detect_meridian_mapping(df)
    date_col = mm["time_col"] or mapping["date_col"]
    if date_col is None:
        raise HTTPException(400, "No date column detected — the file needs a date/week/month column.")
    mapping["date_col"] = date_col
    df = dl.prepare(df, date_col)
    if len(df) < 30:
        raise HTTPException(400, f"Only {len(df)} usable rows — MMM needs at least ~30 periods.")
    STATE.update(df=df, file=os.path.basename(path), mapping=mapping,
                 meridian_mapping=mm, result=None)
    return _san({"file": STATE["file"], **dl.summarize(df, mapping),
                 "meridian_mapping": mm, "meridian": ma.available()})


@app.post("/api/load")
def load(req: LoadRequest):
    return _load(req.filename, req.sheet)


@app.get("/api/dataset/summary")
def dataset_summary():
    df = _require_df()
    return _san({"file": STATE["file"], **dl.summarize(df, STATE["mapping"])})


@app.post("/api/model/run")
def run_model(req: RunRequest):
    df = _require_df()
    missing = [c for c in [req.date_col, req.kpi, *req.channels, *req.controls] if c not in df.columns]
    if missing:
        raise HTTPException(400, f"Columns not in dataset: {missing}")
    if not req.channels:
        raise HTTPException(400, "Pick at least one media channel.")
    result = fit_mmm(df, req.date_col, req.kpi, req.channels, req.controls, passes=req.passes)
    STATE["result"] = result
    return _san(_result_payload(result))


@app.get("/api/model/results")
def results():
    return _san(_result_payload(_require_result()))


@app.post("/api/optimize")
def optimize(req: OptimizeRequest):
    result = _require_result()
    table = optimize_budget(result, req.total_weekly_budget, req.min_share, req.max_share)
    if table.empty:
        raise HTTPException(400, "Optimizer returned no allocation.")
    return _san({
        "total_weekly_budget": req.total_weekly_budget,
        "allocation": table.round(2).to_dict(orient="records"),
        "expected_total_weekly_contribution": table.attrs["expected_total"],
        "current_total_weekly_contribution": table.attrs["current_total"],
    })


def _result_payload(result: ModelResult) -> Dict[str, Any]:
    curves = {}
    for ch in result.channels:
        row = result.channel_summary.loc[result.channel_summary.channel == ch].iloc[0]
        max_spend = max(row["avg_weekly_spend"] * 2.5, 1000.0)
        xs, ys = response_curve(result, ch, float(max_spend))
        curves[ch] = {"x": xs, "y": ys, "current_weekly": float(row["avg_weekly_spend"])}
    return {
        "kpi": result.kpi,
        "r2": result.r2,
        "holdout_mape": result.holdout_mape,
        "alpha": result.alpha,
        "channels": result.channels,
        "controls": result.controls,
        "dates": result.dates,
        "actual": result.actual,
        "predicted": result.predicted,
        "baseline": result.baseline,
        "contributions": result.contributions,
        "summary": result.summary_records(),
        "curves": curves,
        "params": {ch: {"decay": p.decay, "half_sat": p.half_sat, "shape": p.shape}
                   for ch, p in result.params.items()},
    }


# ----------------------------------------------------------------------------
# templates, mapping, EDA, Meridian
# ----------------------------------------------------------------------------
@app.get("/api/template")
def template():
    path = os.path.join(TPL_DIR, "meridian_template.xlsx")
    if not os.path.exists(path):
        raise HTTPException(404, "Template not found — run scripts/generate_sample_data.py")
    return FileResponse(path, filename="meridian_template.xlsx",
                        media_type="application/vnd.openxmlformats-officedocument.spreadsheetml.sheet")


@app.get("/api/meridian/availability")
def meridian_availability():
    return ma.available()


def _mm() -> Dict[str, Any]:
    _require_df()
    if not STATE.get("meridian_mapping"):
        raise HTTPException(400, "No mapping — load a dataset first.")
    return STATE["meridian_mapping"]


@app.post("/api/mapping")
def update_mapping(body: Dict[str, Any] = Body(...)):
    """Apply user overrides to the detected Meridian mapping (UI controls)."""
    mm = dict(_mm())
    allowed = {"time_col", "geo_col", "population_col", "kpi", "kpi_type",
               "revenue_per_kpi", "channels", "organic", "non_media", "controls"}
    df = STATE["df"]
    for k, v in body.items():
        if k not in allowed:
            continue
        mm[k] = v
    # basic validation
    for key in ("kpi", "time_col"):
        if mm.get(key) and mm[key] not in df.columns:
            raise HTTPException(400, f"Column {mm[key]!r} not in dataset.")
    for ch in mm.get("channels", []):
        for c in (ch.get("spend_col"), ch.get("units_col")):
            if c and c not in df.columns:
                raise HTTPException(400, f"Column {c!r} not in dataset.")
    STATE["meridian_mapping"] = mm
    return _san({"meridian_mapping": mm})


@app.post("/api/priors")
def set_priors(body: Dict[str, Any] = Body(...)):
    spec_in = body.get("priors", {})
    clean = {}
    for ch, p in spec_in.items():
        try:
            mu, sigma = float(p.get("mu", 0.2)), float(p.get("sigma", 0.9))
        except (TypeError, ValueError):
            raise HTTPException(400, f"Bad prior for {ch}: {p}")
        if not (0 < sigma <= 5) or not (-5 <= mu <= 5):
            raise HTTPException(400, f"Prior out of range for {ch} (need -5<=mu<=5, 0<sigma<=5).")
        clean[ch] = {"mu": mu, "sigma": sigma}
    STATE["prior_spec"] = clean
    return {"saved": True, "priors": clean}


@app.get("/api/filters")
def get_filters():
    """Facet values for the report filters, plus the current selection."""
    _require_df()
    if not STATE.get("is_long"):
        return {"is_long": False, "facets": {}, "filters": {},
                "kpi_options": [], "kpi_choice": None}
    mm = _mm()
    return _san({"is_long": True, "facets": mm.get("facets", {}),
                 "filters": STATE.get("filters", {}),
                 "filter_dimensions": mm.get("filter_dimensions", []),
                 "kpi_options": mm.get("kpi_options", []),
                 "kpi_choice": STATE.get("kpi_choice")})


@app.post("/api/filters")
def set_filters(body: Dict[str, Any] = Body(default={})):
    """Apply global filters / KPI choice and rebuild the modeling panel."""
    if not STATE.get("is_long"):
        raise HTTPException(400, "Filters apply to long-format datasets only.")
    kpi = body.get("kpi")
    if kpi and kpi not in dl.KPI_CANDIDATES:
        raise HTTPException(400, f"Unknown KPI {kpi!r}.")
    filters = body.get("filters")
    if filters is not None and not isinstance(filters, dict):
        raise HTTPException(400, "filters must be an object.")
    mm = _rebuild_panel(kpi=kpi, filters=filters)
    panel = STATE["df"]
    return _san({"meridian_mapping": mm, "kpi_choice": STATE["kpi_choice"],
                 "filters": STATE["filters"], "rows": int(len(panel)),
                 **dl.summarize(panel, STATE["mapping"])})


def _eda_scope(p: Dict[str, Any]):
    """Resolve the (df, cfg) an EDA section runs against.

    A section may carry its own `filters` — a per-report slice that leaves the
    global selection untouched. Filtering precedes the pivot, so the report is
    a real re-aggregation of the campaign-grain rows.
    """
    df, cfg = _require_df(), _mm()
    local = p.get("filters")
    if not (STATE.get("is_long") and local):
        return df, cfg
    merged = {**STATE.get("filters", {}), **local}
    try:
        cfg = dl.detect_long_mapping(STATE["raw_long"], kpi=STATE["kpi_choice"],
                                     filters=merged)
        df = dl.pivot_long_to_panel(STATE["raw_long"], kpi=STATE["kpi_choice"],
                                     filters=merged)
    except ValueError as e:
        raise HTTPException(400, str(e))
    return df, cfg


@app.post("/api/eda")
def run_eda(body: Dict[str, Any] = Body(default={})):
    """body: {section: spend_media|variables|population|relationships|priors|complete,
              params: {...}} — params are the user-adjustable knobs per section.
    params.filters optionally scopes just this report (e.g. one SUB_BRAND)."""
    section = body.get("section", "complete")
    p = body.get("params", {}) or {}
    df, cfg = _eda_scope(p)
    try:
        if section == "spend_media":
            out = eda_mod.spend_and_media(df, cfg, iqr_k=float(p.get("iqr_k", 1.5)),
                                          small_share_pct=float(p.get("small_share_pct", 2.0)),
                                          n_knots=p.get("n_knots"),
                                          ratio_warn=float(p.get("ratio_warn", 15)))
        elif section == "variables":
            out = eda_mod.variables(df, cfg, iqr_k=float(p.get("iqr_k", 1.5)),
                                    std_threshold=float(p.get("std_threshold", 1e-4)),
                                    scale_population=bool(p.get("scale_population", True)))
        elif section == "population":
            out = eda_mod.population_scaling(df, cfg, high_corr=float(p.get("high_corr", 0.6)))
        elif section == "relationships":
            out = eda_mod.relationships(df, cfg,
                                        corr_error=float(p.get("corr_error", 0.999)),
                                        corr_attention=float(p.get("corr_attention", 0.90)),
                                        vif_threshold=float(p.get("vif_threshold", 1000)))
        elif section == "priors":
            out = eda_mod.priors(df, cfg, prior_spec=STATE.get("prior_spec") or p.get("priors"),
                                 neg_attention=float(p.get("neg_attention", 0.2)),
                                 neg_error=float(p.get("neg_error", 0.5)))
        elif section == "complete":
            sections = {
                "Spend & media units": eda_mod.spend_and_media(df, cfg),
                "Variables": eda_mod.variables(df, cfg),
                "Population scaling": eda_mod.population_scaling(df, cfg),
                "Relationships": eda_mod.relationships(df, cfg),
                "Priors": eda_mod.priors(df, cfg, prior_spec=STATE.get("prior_spec")),
            }
            out = eda_mod.complete_analysis(sections, cfg, df)
        else:
            raise HTTPException(400, f"Unknown EDA section {section!r}")
    except HTTPException:
        raise
    except Exception as e:
        raise HTTPException(500, f"EDA failed: {type(e).__name__}: {e}")
    return _san(out)


@app.post("/api/meridian/run")
def meridian_run(body: Dict[str, Any] = Body(default={})):
    if not ma.available()["available"]:
        raise HTTPException(400, f"Meridian is not installed: {ma.available()['error']} — "
                                 f"pip install google-meridian, or use the classic engine.")
    df = _require_df()
    cfg = dict(_mm())
    for k in ("kpi", "kpi_type", "revenue_per_kpi"):
        if body.get(k):
            cfg[k] = body[k]
    sampling = body.get("sampling", {})
    res = ma.start_run(df, cfg, sampling, prior_spec=STATE.get("prior_spec") or None)
    if not res.get("started"):
        raise HTTPException(409, res.get("reason", "Could not start run"))
    return {"started": True, "poll": "/api/meridian/status"}


@app.get("/api/meridian/status")
def meridian_status():
    return _san(ma.job_status())


@app.get("/api/meridian/results")
def meridian_results():
    r = ma.get_result()
    if r is None:
        raise HTTPException(400, "No Meridian results yet.")
    return _san(r)


@app.post("/api/meridian/optimize")
def meridian_optimize(body: Dict[str, Any] = Body(default={})):
    try:
        return _san(ma.optimize(body.get("total_weekly_budget"), int(body.get("n_weeks", 52))))
    except RuntimeError as e:
        raise HTTPException(400, str(e))
    except Exception as e:
        raise HTTPException(500, f"Meridian optimizer failed: {type(e).__name__}: {e}")


# ----------------------------------------------------------------------------
# UI
# ----------------------------------------------------------------------------
@app.get("/", include_in_schema=False)
def index():
    return FileResponse(os.path.join(UI_DIR, "index.html"))


@app.get("/app.js", include_in_schema=False)
def app_js():
    return FileResponse(os.path.join(UI_DIR, "app.js"), media_type="application/javascript")


@app.exception_handler(Exception)
async def unhandled(request, exc):  # keep the UI's error toasts informative
    return JSONResponse(status_code=500, content={"detail": f"{type(exc).__name__}: {exc}"})
