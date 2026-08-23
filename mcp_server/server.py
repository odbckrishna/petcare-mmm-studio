"""Petcare MMM — MCP server.

Exposes the Marketing Mix Model as tools any MCP client (e.g. Claude Desktop)
can call, so you can ask questions like:

  "Load data/sample_marketing_data.xlsx, run the model and tell me which
   channel has the best ROI — then how should I split £120k a week?"

Run (stdio):  python -m mcp_server.server
Claude Desktop config: see README.md.
"""
from __future__ import annotations

import json
import os
import sys
from typing import List, Optional

# allow running as `python mcp_server/server.py` too
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from mcp.server.fastmcp import FastMCP  # noqa: E402

from app import data_loader as dl  # noqa: E402
from app.mmm import fit_mmm, optimize_budget, response_curve  # noqa: E402

mcp = FastMCP("petcare-mmm")

STATE = {"df": None, "file": None, "mapping": None, "result": None}


def _fmt(obj) -> str:
    return json.dumps(obj, indent=2, default=str)


@mcp.tool()
def list_data_files() -> str:
    """List the Excel files available in the app's data/ folder."""
    return _fmt({"data_dir": os.path.abspath(dl.DATA_DIR), "files": dl.list_excel_files()})


@mcp.tool()
def load_excel(path: str, sheet: Optional[str] = None) -> str:
    """Load a marketing dataset from an Excel file (absolute path, or a file name
    inside the app's data/ folder). Returns the detected column mapping and a summary."""
    df = dl.load_excel(path, sheet)
    mapping = dl.detect_columns(df)
    if mapping["date_col"] is None:
        return "ERROR: no date column detected — the file needs a date/week/month column."
    df = dl.prepare(df, mapping["date_col"])
    STATE.update(df=df, file=os.path.basename(path), mapping=mapping, result=None)
    return _fmt(dl.summarize(df, mapping))


@mcp.tool()
def data_summary() -> str:
    """Summarize the currently loaded dataset (rows, dates, detected KPI/channels/controls)."""
    if STATE["df"] is None:
        return "No dataset loaded — call load_excel first."
    return _fmt({"file": STATE["file"], **dl.summarize(STATE["df"], STATE["mapping"])})


@mcp.tool()
def run_model(kpi: Optional[str] = None, channels: Optional[List[str]] = None,
              controls: Optional[List[str]] = None, passes: int = 2) -> str:
    """Fit the Marketing Mix Model. Arguments default to the auto-detected mapping.
    Returns fit quality and the per-channel summary (spend, contribution, ROI,
    adstock decay, half-saturation)."""
    if STATE["df"] is None:
        return "No dataset loaded — call load_excel first."
    m = STATE["mapping"]
    kpi = kpi or m["kpi"]
    channels = channels or m["channels"]
    controls = controls if controls is not None else m["controls"]
    result = fit_mmm(STATE["df"], m["date_col"], kpi, channels, controls, passes=passes)
    STATE["result"] = result
    return _fmt({
        "kpi": kpi, "r2": round(result.r2, 3), "holdout_mape": round(result.holdout_mape, 4),
        "alpha": result.alpha,
        "channel_summary": result.summary_records(),
        "note": "ROI = modeled contribution per unit spend over the full period.",
    })


@mcp.tool()
def channel_roi() -> str:
    """Return the per-channel ROI / contribution table from the last model run."""
    if STATE["result"] is None:
        return "No model run yet — call run_model first."
    return _fmt(STATE["result"].summary_records())


@mcp.tool()
def get_response_curve(channel: str, max_weekly_spend: Optional[float] = None) -> str:
    """Response curve for one channel: expected weekly KPI contribution at steady
    weekly spend levels (shows where returns diminish)."""
    res = STATE["result"]
    if res is None:
        return "No model run yet — call run_model first."
    if channel not in res.channels:
        return f"Unknown channel {channel!r}. Modeled channels: {res.channels}"
    row = res.channel_summary.loc[res.channel_summary.channel == channel].iloc[0]
    mx = max_weekly_spend or float(row["avg_weekly_spend"] * 2.5)
    xs, ys = response_curve(res, channel, mx, points=15)
    return _fmt({"channel": channel, "current_avg_weekly_spend": float(row["avg_weekly_spend"]),
                 "weekly_spend": [round(x) for x in xs], "expected_weekly_contribution": [round(y) for y in ys]})


@mcp.tool()
def optimize_weekly_budget(total_weekly_budget: float, min_share: float = 0.0,
                           max_share: float = 1.0) -> str:
    """Split a total weekly budget across the modeled channels to maximize expected
    KPI, using the fitted response curves. Shares are fractions (0.6 = 60%)."""
    res = STATE["result"]
    if res is None:
        return "No model run yet — call run_model first."
    table = optimize_budget(res, total_weekly_budget, min_share, max_share)
    return _fmt({
        "total_weekly_budget": total_weekly_budget,
        "allocation": table.round(2).to_dict(orient="records"),
        "expected_total_weekly_contribution": round(table.attrs["expected_total"]),
        "at_current_mix": round(table.attrs["current_total"]),
    })


if __name__ == "__main__":
    mcp.run()
