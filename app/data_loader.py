"""Excel ingestion, validation and column auto-detection for the MMM."""
from __future__ import annotations

import os
import re
from typing import Dict, List, Optional

import pandas as pd

DATA_DIR = os.path.join(os.path.dirname(__file__), "..", "data")

MEDIA_HINTS = re.compile(
    r"spend|cost|invest|media|budget|tv|radio|print|ooh|search|social|digital|display|"
    r"video|youtube|meta|facebook|instagram|tiktok|email|retail|influencer|affiliate|cinema",
    re.I,
)
KPI_HINTS = re.compile(r"revenue|sales|kpi|units|volume|conversions|orders|signups|transactions", re.I)
FLAG_HINTS = re.compile(r"flag|dummy|holiday|promo|event|covid|lockdown", re.I)


def list_excel_files() -> List[str]:
    if not os.path.isdir(DATA_DIR):
        return []
    return sorted(
        f for f in os.listdir(DATA_DIR)
        if f.lower().endswith((".xlsx", ".xlsm", ".xls")) and not f.startswith("~$")
    )


CACHE_DIR = os.path.join(DATA_DIR, ".cache")


def _cache_path(path: str, sheet: Optional[str]) -> str:
    """Cache file for a workbook+sheet, keyed by mtime so it self-invalidates."""
    st = os.stat(path)
    stamp = f"{int(st.st_mtime)}_{st.st_size}_{sheet or 0}"
    name = re.sub(r"[^A-Za-z0-9_.-]", "_", os.path.basename(path))
    return os.path.join(CACHE_DIR, f"{name}.{stamp}.pkl")


def load_excel(path: str, sheet: Optional[str] = None) -> pd.DataFrame:
    """Load an Excel file. `path` may be absolute or a name inside data/.

    Parsing a large .xlsx costs tens of seconds in openpyxl, so the parsed
    frame is cached next to the data. The cache key includes the file's mtime
    and size, so editing the workbook invalidates it automatically.
    """
    if not os.path.isabs(path):
        candidate = os.path.join(DATA_DIR, path)
        path = candidate if os.path.exists(candidate) else path
    if not os.path.exists(path):
        raise FileNotFoundError(f"Excel file not found: {path}")

    cache = _cache_path(path, sheet)
    if os.path.exists(cache):
        try:
            return pd.read_pickle(cache)
        except Exception:
            pass  # unreadable cache: fall through and re-parse

    df = pd.read_excel(path, sheet_name=sheet or 0)
    df.columns = [str(c).strip() for c in df.columns]
    df = df.dropna(how="all").reset_index(drop=True)

    try:
        os.makedirs(CACHE_DIR, exist_ok=True)
        # Drop stale caches for this workbook before writing the new one.
        base = re.sub(r"[^A-Za-z0-9_.-]", "_", os.path.basename(path))
        for f in os.listdir(CACHE_DIR):
            if f.startswith(base + ".") and f.endswith(".pkl"):
                try:
                    os.remove(os.path.join(CACHE_DIR, f))
                except OSError:
                    pass
        df.to_pickle(cache)
    except Exception:
        pass  # a read-only data dir must not break loading
    return df


def detect_columns(df: pd.DataFrame) -> Dict[str, object]:
    """Best-effort mapping: date, kpi, media channels, controls."""
    date_col = None
    for c in df.columns:
        if pd.api.types.is_datetime64_any_dtype(df[c]):
            date_col = c
            break
    if date_col is None:
        for c in df.columns:
            if df[c].dtype == object or "date" in c.lower() or "week" in c.lower() or "month" in c.lower():
                parsed = pd.to_datetime(df[c], errors="coerce", format="mixed")
                if parsed.notna().mean() > 0.9:
                    date_col = c
                    break

    numeric = [c for c in df.columns if c != date_col and pd.api.types.is_numeric_dtype(df[c])]

    kpi = next((c for c in numeric if KPI_HINTS.search(c)), None)
    if kpi is None and numeric:
        kpi = max(numeric, key=lambda c: float(pd.to_numeric(df[c], errors="coerce").fillna(0).sum()))

    media = [c for c in numeric if c != kpi and MEDIA_HINTS.search(c) and not FLAG_HINTS.search(c)]
    controls = [c for c in numeric if c not in media and c != kpi]

    return {"date_col": date_col, "kpi": kpi, "channels": media, "controls": controls}


def prepare(df: pd.DataFrame, date_col: str) -> pd.DataFrame:
    out = df.copy()
    out[date_col] = pd.to_datetime(out[date_col], errors="coerce", format="mixed")
    out = out.dropna(subset=[date_col]).sort_values(date_col).reset_index(drop=True)
    return out


# ---------------------------------------------------------------------------
# Long / campaign-grain format
#
# One row per DATE x retailer x sub-brand x marketing channel x campaign x
# creative x audience, carrying CLICKS / IMPRESSIONS / SPEND plus the sales
# KPIs. BRAND is the model geo; SUB_BRAND and MARKETING_CHANNEL are the two
# report filters. The panel the model sees is built by `pivot_long_to_panel`,
# so every downstream consumer (EDA, classic engine, Meridian) keeps working
# against the familiar wide layout.
# ---------------------------------------------------------------------------

# The 28 mandatory dimension columns.
DIMENSION_COLUMNS = [
    "DATE", "ZONE", "REGION", "COUNTRY", "COUNTRY_REGION", "PROVINCE", "LOCATION",
    "RETAIL_CHANNEL", "CHANNEL_NAME", "RETAILER_NAME", "MANUFACTURER",
    "PRODUCT_FAMILY", "SPECIES", "BRAND", "SUB_BRAND", "BRAND_TECH", "SKU_NAME",
    "MARKETING_TYPE", "MARKETING_CHANNEL", "CAMPAIGN_NAME", "CAMPAIGN_OBJECTIVE",
    "MEDIA_OBJECTIVE", "PLATFORM_PLACEMENT", "FORMAT_CAMPAIGN_TYPE",
    "DURATION_LENGTH_SIZE", "AUDIENCE_NAME_AUDIENCE_TYPE",
    "CREATIVE_NAME_DEVICE_NAME", "BUY_TYPE_LANGUAGE",
]

# Measures live in a METRIC / VALUE pair (EAV). They are un-pivoted into these
# canonical measure columns on load.
METRIC_COL, VALUE_COL = "METRIC", "VALUE"
METRIC_COLUMNS = ["CLICKS", "IMPRESSIONS", "SPEND"]

# Candidate KPI columns, in preference order. CLICKS is offered too: it can be
# modeled as an engagement KPI, in which case it is dropped from the media side.
KPI_CANDIDATES = ["REVENUE", "UNITS_SOLD", "CLICKS"]

# All recognized measures, normalized to upper-case for matching.
MEASURE_NAMES = ["CLICKS", "IMPRESSIONS", "SPEND", "UNITS_SOLD", "REVENUE"]

# The dimension acting as Meridian's geo (the panel unit the model is fit across).
GEO_DIMENSION = "BRAND"

# Dimensions exposed as per-report filters. Every dimension is filterable and
# multi-select; these lead the list because they drive the model panel.
PRIMARY_FILTER_DIMENSIONS = ["BRAND", "SUB_BRAND", "MARKETING_CHANNEL"]
FILTER_DIMENSIONS = PRIMARY_FILTER_DIMENSIONS + [
    d for d in DIMENSION_COLUMNS
    if d not in PRIMARY_FILTER_DIMENSIONS and d != "DATE"
]

# Organic channels carry impressions but no spend.
ORGANIC_MARKER = re.compile(r"organic", re.I)

# Minimum columns that identify the long format. Either the EAV pair
# (METRIC + VALUE) or explicit measure columns satisfy the measure requirement.
_LONG_DIMS_REQUIRED = {"DATE", "BRAND", "SUB_BRAND", "MARKETING_CHANNEL"}


def is_long_format(df: pd.DataFrame) -> bool:
    """True when the frame is campaign-grain long rather than a wide panel."""
    cols = {str(c).strip().upper() for c in df.columns}
    if not _LONG_DIMS_REQUIRED.issubset(cols):
        return False
    return {METRIC_COL, VALUE_COL}.issubset(cols) or {"SPEND", "IMPRESSIONS"} <= cols


def _unpivot_metrics(df: pd.DataFrame) -> pd.DataFrame:
    """Turn METRIC / VALUE rows into one column per measure.

    Rows are grouped by every dimension present, so the campaign grain is kept
    and each measure lands in its own column (CLICKS, IMPRESSIONS, SPEND,
    UNITS_SOLD, REVENUE) exactly as the rest of the pipeline expects.
    """
    if METRIC_COL not in df.columns or VALUE_COL not in df.columns:
        return df
    out = df.copy()
    out[METRIC_COL] = (out[METRIC_COL].astype(str).str.strip()
                       .str.upper().str.replace(r"[\s-]+", "_", regex=True))
    out[VALUE_COL] = pd.to_numeric(out[VALUE_COL], errors="coerce").fillna(0.0)

    dims = [c for c in out.columns if c not in (METRIC_COL, VALUE_COL)]

    # Spread each metric into its own column against a single row key. A
    # pivot_table over ~28 dimension columns would build a cartesian index far
    # too large to materialize, so key the rows by their group code instead.
    # Group by a single hashed row key rather than the dimension columns
    # themselves: a groupby/pivot over ~28 columns builds a cartesian index
    # large enough to overflow pandas' internal group codes.
    key = pd.util.hash_pandas_object(out[dims].astype(str), index=False)
    tagged = out.assign(_k=key.values)
    spread = tagged.pivot_table(index="_k", columns=METRIC_COL, values=VALUE_COL,
                                aggfunc="sum", observed=True)
    spread.columns.name = None
    base = tagged.drop_duplicates("_k").set_index("_k")[dims]
    wide = base.join(spread, how="left").reset_index(drop=True)

    for m in MEASURE_NAMES:
        if m not in wide.columns:
            wide[m] = 0.0
    return wide


def normalize_long(df: pd.DataFrame) -> pd.DataFrame:
    """Canonicalize headers, un-pivot METRIC/VALUE, and coerce measures.

    Accepts either the EAV shape (a METRIC column naming the measure and a
    VALUE column holding the number) or explicit measure columns.
    """
    # Fast path: already normalized (measures are columns, no METRIC/VALUE
    # pair left). Re-running the full pass on every filter change is wasted
    # work on a large frame.
    if METRIC_COL not in df.columns and "DATE" in df.columns             and any(m in df.columns for m in MEASURE_NAMES)             and pd.api.types.is_datetime64_any_dtype(df["DATE"]):
        return df

    known = {c.upper(): c for c in
             DIMENSION_COLUMNS + MEASURE_NAMES + [METRIC_COL, VALUE_COL]}
    out = df.rename(columns={c: known.get(str(c).strip().upper(), str(c).strip())
                             for c in df.columns})
    if "DATE" in out.columns:
        out["DATE"] = pd.to_datetime(out["DATE"], errors="coerce", format="mixed")
        out = out.dropna(subset=["DATE"])
    out = _unpivot_metrics(out)
    for c in MEASURE_NAMES:
        if c in out.columns:
            out[c] = pd.to_numeric(out[c], errors="coerce").fillna(0.0)
    return out.reset_index(drop=True)


# A dimension with more distinct values than this is not offered as a filter —
# the dropdown would be unusable and the payload large. Campaign lists run to a
# few thousand, and the dropdown has a search box, so the ceiling is generous.
MAX_FACET_VALUES = 5000


def long_facets(df: pd.DataFrame, dims: Optional[List[str]] = None,
                max_values: int = MAX_FACET_VALUES) -> Dict[str, List[str]]:
    """Distinct values per dimension — powers the multi-select filter dropdowns.

    Dimensions with only one value, or with more than `max_values` distinct
    values, are omitted: neither makes a useful filter.
    """
    out: Dict[str, List[str]] = {}
    for c in (dims if dims is not None else DIMENSION_COLUMNS):
        if c not in df.columns:
            continue
        vals = sorted(str(v) for v in df[c].dropna().unique())
        if 1 <= len(vals) <= max_values:
            out[c] = vals
    return out


def apply_filters(df: pd.DataFrame, filters: Optional[Dict[str, object]]) -> pd.DataFrame:
    """Subset long rows by dimension values.

    `filters` maps a dimension to a value or list of values; empty / missing /
    'All' means no constraint on that dimension. Filtering happens BEFORE the
    pivot, so a filtered report is a genuine re-aggregation, not a re-slice of
    an already-summed panel.
    """
    if not filters:
        return df
    out = df
    for dim, val in filters.items():
        if dim not in out.columns or val in (None, "", "All", "all"):
            continue
        vals = val if isinstance(val, (list, tuple, set)) else [val]
        vals = [str(v) for v in vals if v not in (None, "", "All", "all")]
        if not vals:
            continue
        out = out[out[dim].astype(str).isin(vals)]
    return out.reset_index(drop=True)


def pivot_long_to_panel(df: pd.DataFrame, kpi: str = "REVENUE",
                        filters: Optional[Dict[str, object]] = None) -> pd.DataFrame:
    """Aggregate campaign-grain long rows into a BRAND x DATE wide panel.

    Media metrics are summed per MARKETING_CHANNEL into `<Channel>_impression`,
    `<Channel>_spend` and `<Channel>_click`; organic channels get an
    `Organic_<name>_impression` column and no spend. The KPI is summed per
    brand-week from the de-duplicated row shares.
    """
    df = apply_filters(normalize_long(df), filters)
    if df.empty:
        raise ValueError("No rows match the selected filters.")
    if kpi not in df.columns:
        raise ValueError(f"KPI column {kpi!r} is not in the dataset.")

    geo, time = GEO_DIMENSION, "DATE"
    kpi_is_click = kpi.upper() == "CLICKS"

    # KPI per brand-week: sum across every long row in the slice.
    panel = (df.groupby([time, geo], as_index=False)[kpi].sum()
               .rename(columns={kpi: kpi.lower()}))

    # Media per channel.
    grouped = df.groupby([time, geo, "MARKETING_CHANNEL"], as_index=False)[
        [c for c in METRIC_COLUMNS if c in df.columns]].sum()

    for ch in sorted(grouped["MARKETING_CHANNEL"].dropna().unique()):
        sub = grouped[grouped["MARKETING_CHANNEL"] == ch]
        organic = bool(ORGANIC_MARKER.search(str(ch)))
        safe = re.sub(r"[^A-Za-z0-9]+", "_", str(ch)).strip("_")
        # "Organic Social" already carries the marker — don't double the prefix.
        name = safe if not organic or safe.lower().startswith("organic") \
            else f"Organic_{safe}"
        cols = {}
        if "IMPRESSIONS" in sub.columns:
            cols[f"{name}_impression"] = "IMPRESSIONS"
        if "CLICKS" in sub.columns and not kpi_is_click:
            cols[f"{name}_click"] = "CLICKS"
        if "SPEND" in sub.columns and not organic:
            cols[f"{name}_spend"] = "SPEND"
        take = sub[[time, geo] + list(cols.values())].rename(
            columns={v: k for k, v in cols.items()})
        panel = panel.merge(take, on=[time, geo], how="left")

    media_cols = [c for c in panel.columns if c not in (time, geo, kpi.lower())]
    panel[media_cols] = panel[media_cols].fillna(0.0)

    # A population column keeps Meridian's population scaling meaningful; with
    # no demographic input available, every brand carries equal weight.
    panel["population"] = 1.0
    return panel.sort_values([time, geo]).reset_index(drop=True)


def detect_long_mapping(df: pd.DataFrame, kpi: str = "REVENUE",
                        filters: Optional[Dict[str, object]] = None) -> Dict[str, object]:
    """Build the Meridian mapping for a long-format file, plus the facets and
    the materialized panel the model and every EDA report run against."""
    norm = normalize_long(df)
    facets = long_facets(norm, dims=FILTER_DIMENSIONS)
    panel = pivot_long_to_panel(norm, kpi=kpi, filters=filters)

    channels: List[Dict[str, object]] = []
    organic: List[str] = []
    for c in panel.columns:
        if c.endswith("_impression"):
            base = c[: -len("_impression")]
            if base.startswith("Organic_"):
                organic.append(c)
            else:
                channels.append({
                    "name": base,
                    "spend_col": f"{base}_spend" if f"{base}_spend" in panel.columns else None,
                    "units_col": c,
                })
    channels = [c for c in channels if c["spend_col"] or c["units_col"]]

    kpi_col = kpi.lower()
    kpi_type = "revenue" if kpi.upper() == "REVENUE" else "non_revenue"
    return {
        "format": "long",
        "time_col": "DATE",
        "geo_col": GEO_DIMENSION,
        "population_col": "population",
        "kpi": kpi_col,
        "kpi_type": kpi_type,
        "revenue_per_kpi": None,
        "alt_kpis": [k.lower() for k in KPI_CANDIDATES
                     if k != kpi and k.lower() in panel.columns],
        "channels": channels,
        "organic": organic,
        "non_media": [],
        "controls": [],
        "kpi_options": [k for k in KPI_CANDIDATES if k in norm.columns],
        # Facets come from the UNFILTERED frame so the dropdowns keep offering
        # every value — narrowing one dimension must not empty the others.
        "facets": facets,
        "filter_dimensions": [d for d in FILTER_DIMENSIONS if d in facets],
        "primary_filters": [d for d in PRIMARY_FILTER_DIMENSIONS if d in facets],
        "filters": filters or {},
    }


UNITS_SUFFIX = re.compile(r"_(impressions?|clicks?|grps?|views?|units)$", re.I)
SPEND_SUFFIX = re.compile(r"_(spend|cost)$", re.I)
ORGANIC_PREFIX = re.compile(r"^organic[_ ]", re.I)
NON_MEDIA_HINTS = re.compile(r"^(promo\w*|price_event|pr_event|event\w*)$", re.I)
RPK_HINTS = re.compile(r"revenue_per|rev_per|price_per", re.I)
POP_HINTS = re.compile(r"population|pop$|buyers|households", re.I)
GEO_HINTS = re.compile(r"^(geo|brand|market|region|dma|country)$", re.I)


def detect_meridian_mapping(df: pd.DataFrame) -> Dict[str, object]:
    """Detect a Meridian-style geo panel: time, geo, population, KPI (+type),
    revenue_per_kpi, paired media units/spend per channel, organic media,
    non-media treatments, controls."""
    cols = list(df.columns)

    # time
    time_col = None
    for c in cols:
        if pd.api.types.is_datetime64_any_dtype(df[c]) or re.search(r"^(time|date|week|month)$", c, re.I):
            parsed = pd.to_datetime(df[c], errors="coerce", format="mixed")
            if parsed.notna().mean() > 0.9:
                time_col = c
                break
    if time_col is None:
        base = detect_columns(df)
        time_col = base["date_col"]

    # geo: named hint, else a low-cardinality non-numeric column with repeated times
    geo_col = next((c for c in cols if GEO_HINTS.match(c)), None)
    if geo_col is None:
        for c in cols:
            if c != time_col and df[c].dtype == object and 1 < df[c].nunique() <= max(2, len(df) // 20):
                geo_col = c
                break

    population_col = next((c for c in cols if POP_HINTS.search(c)
                           and pd.api.types.is_numeric_dtype(df[c])), None)

    numeric = [c for c in cols if c not in (time_col, geo_col)
               and pd.api.types.is_numeric_dtype(df[c])]

    organic = [c for c in numeric if ORGANIC_PREFIX.search(c)]
    non_media = [c for c in numeric if NON_MEDIA_HINTS.match(c)]
    rpk = next((c for c in numeric if RPK_HINTS.search(c)), None)

    # paired channels via suffixes
    channels: List[Dict[str, object]] = []
    used = set(organic + non_media + ([population_col] if population_col else []) + ([rpk] if rpk else []))
    spends = {SPEND_SUFFIX.sub("", c): c for c in numeric if SPEND_SUFFIX.search(c) and c not in used}
    units = {UNITS_SUFFIX.sub("", c): c for c in numeric if UNITS_SUFFIX.search(c) and c not in used}
    for name in sorted(set(spends) | set(units)):
        if name.lower() in {"total", "media"}:
            continue
        channels.append({"name": name, "spend_col": spends.get(name), "units_col": units.get(name)})
    channels = [c for c in channels if c["spend_col"] or c["units_col"]]
    used |= {c["spend_col"] for c in channels if c["spend_col"]}
    used |= {c["units_col"] for c in channels if c["units_col"]}

    # KPI: prefer revenue-like, else unit-like among leftovers
    leftovers = [c for c in numeric if c not in used]
    kpi = next((c for c in leftovers if re.search(r"revenue|sales_value|turnover", c, re.I)), None)
    kpi_type = "revenue"
    if kpi is None:
        kpi = next((c for c in leftovers if KPI_HINTS.search(c)), None)
        kpi_type = "non_revenue" if kpi is not None else "revenue"
    if kpi is None and leftovers:
        kpi = max(leftovers, key=lambda c: float(pd.to_numeric(df[c], errors="coerce").fillna(0).sum()))
    if kpi:
        used.add(kpi)
    # a units KPI next to a revenue KPI stays out of controls
    alt_kpi = [c for c in leftovers if c != kpi and KPI_HINTS.search(c)]
    used |= set(alt_kpi)

    controls = [c for c in numeric if c not in used]
    is_meridian = bool(channels) and (geo_col is not None or bool(units))
    return {
        "format": "meridian" if is_meridian else "simple",
        "time_col": time_col, "geo_col": geo_col, "population_col": population_col,
        "kpi": kpi, "kpi_type": kpi_type, "revenue_per_kpi": rpk,
        "alt_kpis": alt_kpi, "channels": channels, "organic": organic,
        "non_media": non_media, "controls": controls,
    }


def summarize(df: pd.DataFrame, mapping: Dict[str, object]) -> Dict[str, object]:
    date_col = mapping.get("date_col")
    dates = pd.to_datetime(df[date_col]) if date_col else None
    freq = None
    if dates is not None and len(dates) > 2:
        # Use distinct periods: a geo panel repeats each date once per geo, so
        # the raw diff would be 0 days.
        delta = pd.Series(sorted(dates.unique())).diff().dropna().mode()
        if len(delta):
            days = delta.iloc[0].days
            freq = {1: "daily", 7: "weekly", 28: "monthly", 30: "monthly", 31: "monthly"}.get(days, f"{days}-day")
    return {
        "rows": int(len(df)),
        "columns": list(df.columns),
        "date_col": date_col,
        "date_min": str(dates.min().date()) if dates is not None else None,
        "date_max": str(dates.max().date()) if dates is not None else None,
        "frequency": freq,
        "kpi": mapping.get("kpi"),
        "channels": mapping.get("channels"),
        "controls": mapping.get("controls"),
        "preview": df.head(8).astype(str).to_dict(orient="records"),
        "spend_totals": {c: float(df[c].sum()) for c in mapping.get("channels", []) if c in df},
    }
