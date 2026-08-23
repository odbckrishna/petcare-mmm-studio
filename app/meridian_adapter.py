"""Google Meridian integration — subprocess orchestration.

The heavy Bayesian run executes in a SEPARATE PROCESS (app/meridian_worker.py):
a crash or an out-of-memory kill can never take the app down, and the fitted
model is persisted to disk so the budget optimizer can run later in its own
short-lived process.

Meridian is optional at runtime: if the import fails, `available()` says so and
the app falls back to the classic engine.
"""
from __future__ import annotations

import json
import os
import shutil
import subprocess
import sys
from typing import Any, Dict, Optional

import pandas as pd

BASE_DIR = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
REPORTS_DIR = os.path.join(BASE_DIR, "reports")
JOB_DIR = os.path.join(REPORTS_DIR, "_job")

# Availability probe WITHOUT importing meridian/TensorFlow into the app process
# (TF alone costs ~2 GB of RAM — that memory belongs to the worker, not the app).
try:
    import importlib.util
    from importlib import metadata as _md

    if importlib.util.find_spec("meridian") is None:
        raise ModuleNotFoundError("No module named 'meridian'")
    try:
        MERIDIAN_VERSION = _md.version("google-meridian")
    except Exception:
        MERIDIAN_VERSION = "unknown"
    MERIDIAN_OK, MERIDIAN_ERR = True, None
except Exception as e:  # pragma: no cover
    MERIDIAN_OK, MERIDIAN_ERR, MERIDIAN_VERSION = False, str(e), None

_PROC: Dict[str, Any] = {"fit": None}


def available() -> Dict[str, Any]:
    return {"available": MERIDIAN_OK, "version": MERIDIAN_VERSION, "error": MERIDIAN_ERR}


def _read_log() -> list:
    path = os.path.join(JOB_DIR, "progress.log")
    if not os.path.exists(path):
        return []
    with open(path, encoding="utf-8", errors="replace") as fh:
        return [ln.rstrip() for ln in fh if ln.strip()]


def start_run(df: pd.DataFrame, cfg: Dict[str, Any], sampling: Dict[str, Any],
              prior_spec: Optional[Dict[str, dict]] = None) -> Dict[str, Any]:
    proc = _PROC.get("fit")
    if proc is not None and proc.poll() is None:
        return {"started": False, "reason": "A Meridian run is already in progress."}

    shutil.rmtree(JOB_DIR, ignore_errors=True)
    os.makedirs(JOB_DIR, exist_ok=True)
    df.to_pickle(os.path.join(JOB_DIR, "input.pkl"))
    json.dump(cfg, open(os.path.join(JOB_DIR, "cfg.json"), "w"), default=str)
    json.dump(sampling or {}, open(os.path.join(JOB_DIR, "sampling.json"), "w"))
    if prior_spec:
        json.dump(prior_spec, open(os.path.join(JOB_DIR, "priors.json"), "w"))

    _PROC["fit"] = subprocess.Popen(
        [sys.executable, "-m", "app.meridian_worker", "fit", JOB_DIR],
        cwd=BASE_DIR, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL,
    )
    return {"started": True}


def job_status() -> Dict[str, Any]:
    proc = _PROC.get("fit")
    lines = _read_log()
    reports = {}
    rp = os.path.join(JOB_DIR, "reports.json")
    if os.path.exists(rp):
        try:
            reports = json.load(open(rp))
        except Exception:
            pass
    has_result = os.path.exists(os.path.join(JOB_DIR, "result.json"))

    if proc is None:
        state = "done" if has_result else "idle"
        error = None
    elif proc.poll() is None:
        state, error = "running", None
    elif proc.returncode == 0 and has_result:
        state, error = "done", None
    else:
        state = "error"
        tail = " · ".join(lines[-4:]) if lines else ""
        if proc.returncode and proc.returncode < 0:
            error = (f"The Meridian worker was killed (signal {-proc.returncode}) — almost always "
                     f"OUT OF MEMORY. Lower chains/draws (e.g. 2 × 200+200+300), reduce max lag, "
                     f"or model fewer geos. {tail}")
        else:
            error = tail or f"Worker exited with code {proc.returncode}."
    return {"state": state, "progress": lines[-14:], "error": error,
            "has_result": has_result, "reports": reports}


def get_result() -> Optional[Dict[str, Any]]:
    path = os.path.join(JOB_DIR, "result.json")
    if not os.path.exists(path):
        return None
    r = json.load(open(path))
    rp = os.path.join(JOB_DIR, "reports.json")
    if os.path.exists(rp):
        try:
            r["reports"] = json.load(open(rp))
        except Exception:
            pass
    return r


def optimize(total_weekly_budget: Optional[float] = None, n_weeks: int = 52,
             timeout: int = 900) -> Dict[str, Any]:
    if not os.path.exists(os.path.join(JOB_DIR, "model.pkl")):
        raise RuntimeError("No fitted Meridian model on disk — run the model first.")
    json.dump({"total_weekly_budget": total_weekly_budget, "n_weeks": n_weeks},
              open(os.path.join(JOB_DIR, "opt.json"), "w"))
    for f in ("optimize.json",):
        try:
            os.remove(os.path.join(JOB_DIR, f))
        except FileNotFoundError:
            pass
    proc = subprocess.run(
        [sys.executable, "-m", "app.meridian_worker", "optimize", JOB_DIR],
        cwd=BASE_DIR, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL, timeout=timeout,
    )
    out_path = os.path.join(JOB_DIR, "optimize.json")
    if proc.returncode != 0 or not os.path.exists(out_path):
        tail = " · ".join(_read_log()[-3:])
        raise RuntimeError(f"Meridian optimizer failed ({'killed — likely out of memory' if proc.returncode < 0 else 'exit ' + str(proc.returncode)}). {tail}")
    return json.load(open(out_path))
