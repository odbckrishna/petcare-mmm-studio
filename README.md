# Petcare MMM Studio

Measure the real sales driven by marketing — a **Marketing Mix Modeling** workbench
built on **Google Meridian** that runs entirely on your computer. Excel in →
guided pre-modeling EDA → Bayesian MMM → ROI with credible intervals, budget
optimization and Meridian's own HTML reports. A fast classic engine is included
for instant previews, and the model is also exposed as an **MCP server** so
Claude Desktop can drive it conversationally.

Your data never leaves your machine.

---

## 1 · Quick start

Requires **Python 3.10 – 3.12** ([python.org/downloads](https://www.python.org/downloads/)).

**Windows — scripted (recommended):**

```cmd
util\install.bat        :: one-time setup: deps + sample data
util\redeploy.bat       :: stop any old instance and start the app
```

Re-run `util\redeploy.bat` after any code change. See [util/README.md](util/README.md)
for all options (`--port`, `--stop`, `--console`, `--with-meridian`).

**Any platform — manual:**

```bash
cd petcare-mmm-studio
pip install -r requirements.txt      # includes google-meridian (~2 GB with TensorFlow)
python run.py
```

Open **http://127.0.0.1:8050** and load `petcare_campaign_long.xlsx` on the Data
tab — a synthetic campaign-grain Petcare dataset (73,865 METRIC/VALUE rows ≈ 9 MB:
6 brands × 14 sub-brands × 7 channels × 5 retailers × 157 Saturday weeks ×
5 metrics, H2 2023 → H1 2026) so every screen works before you bring your own data.

The first load parses the workbook (~40 s) and caches it under `data/.cache/`;
every load after that takes about a second. The cache is keyed on the file's
timestamp, so replacing the workbook refreshes it automatically.

Windows: use `py -m pip install -r requirements.txt` and `py run.py` if `pip`/`python` aren't found.
Apple Silicon: TensorFlow installs as `tensorflow-macos` automatically via Meridian's dependencies.
If the Meridian install is a problem, the app still runs — the classic engine is used and
the UI tells you Meridian is unavailable.

**Memory:** full Bayesian MCMC is RAM-hungry — plan for **8–16 GB free** for a
multi-brand weekly panel. Runs execute in an isolated worker process, so even an
out-of-memory kill never crashes the app: you get a clear message with remedies
(fewer chains/draws, lower max lag, fewer geos). Start small (2 chains × 200+200+500),
confirm R-hat, then scale up.

## 2 · Data format — campaign-grain (long)

The primary format is **long / campaign-grain**: one row per
`DATE × RETAILER × SUB_BRAND × MARKETING_CHANNEL × CAMPAIGN × CREATIVE × AUDIENCE`,
carrying the three media metrics plus the KPI. Download the blank template with a
column-by-column guide from the Data tab (`/api/template`), or load
`petcare_campaign_long.xlsx`.

**28 mandatory dimension columns**, in order:

```
DATE  ZONE  REGION  COUNTRY  COUNTRY_REGION  PROVINCE  LOCATION  RETAIL_CHANNEL
CHANNEL_NAME  RETAILER_NAME  MANUFACTURER  PRODUCT_FAMILY  SPECIES  BRAND
SUB_BRAND  BRAND_TECH  SKU_NAME  MARKETING_TYPE  MARKETING_CHANNEL  CAMPAIGN_NAME
CAMPAIGN_OBJECTIVE  MEDIA_OBJECTIVE  PLATFORM_PLACEMENT  FORMAT_CAMPAIGN_TYPE
DURATION_LENGTH_SIZE  AUDIENCE_NAME_AUDIENCE_TYPE  CREATIVE_NAME_DEVICE_NAME
BUY_TYPE_LANGUAGE
```

**Plus a `METRIC` / `VALUE` pair** — every measure is one row:

| METRIC | meaning |
|---|---|
| `Clicks` · `Impressions` · `Spend` | media inputs |
| `Units_Sold` · `Revenue` | the KPI (either is selectable; `Clicks` also works as an engagement KPI) |

`VALUE` carries the number. So one campaign slice becomes 5 rows — one per
metric. The app un-pivots `METRIC`/`VALUE` on load, so the rest of the pipeline
sees ordinary measure columns.

`DATE` is **weekly, Saturdays only**. The sample spans **H2 2023 → H1 2026**
(2023-07-01 → 2026-06-27, 157 weeks) and carries **1,440 named campaigns** of the
form `<Brand> <Theme> <Season> <Year>` — e.g. *Pedigree Tails of Joy Spring 2024*,
*Whiskas Bowl Envy Holiday 2025*.

- `BRAND` is the **model geo** — the panel unit Meridian is fit across.
- `SUB_BRAND` and `MARKETING_CHANNEL` are the **report filters** (see below).
- `LOCATION` defaults to `US`; the geographic hierarchy is constant for a single-market file.
- Retailers in the sample: Walmart, Chewy, Kroger, Target, Amazon.
- **No missing cells** — media gaps = 0. Every brand needs every week.

### How the panel is built

The app keeps the raw long rows and **aggregates them up** to a `BRAND × week`
panel for modeling: `MARKETING_CHANNEL` is pivoted into Meridian's per-channel
`<Channel>_impression` / `<Channel>_spend` / `<Channel>_click` columns, organic
channels become `Organic_*` (impressions, no spend), and the KPI is summed per
brand-week. Filtering happens **before** the pivot, so a filtered report is a
genuine re-aggregation of the campaign rows, not a re-slice of an existing panel.

### Filters — on every report

Each EDA report carries a **Sub-brand** and **Marketing channel** filter next to
its parameter knobs; the Data tab also has dataset-wide versions that set the
baseline every report starts from. A report-level filter narrows further without
disturbing the global selection.

### Choosing the KPI

`REVENUE`, `UNITS_SOLD` and `CLICKS` are all selectable in the UI. Picking
**CLICKS** models engagement — the click columns are dropped from the media side
so the model never predicts an input from itself; ROI is then read as
cost-per-click efficiency rather than revenue return.

The wide Meridian panel (`meridian_sample_petcare.xlsx`, one row per brand × week)
and simple national files still load — the format is auto-detected, and everything
is overridable in the UI.

## 3 · Pre-modeling EDA (tab 2)

Implements the checks from Meridian's
[Perform an exploratory data analysis](https://developers.google.com/meridian/docs/pre-modeling/perform-eda)
guide, with the same ERROR / ATTENTION / INFO severities. Reports generate
automatically from the loaded data; every threshold is a control in the UI:

1. **Spend & media units** — spend share (small-channel warning), spend vs delivered
   units time series, cost-per-1k outliers (IQR), spend↔units mismatch weeks,
   data-to-parameter ratio.
2. **Variables (box plots)** — Meridian-style population-scaled + standardized box
   plots for media / organic / controls / KPI, variation checks (constant variables,
   no-geo-variation controls, KPI std threshold), top outliers, sparsity check.
3. **Population scaling** — Spearman ρ of population vs raw media and vs controls,
   per-capita brand footprint, `control_population_scaling_id` suggestions.
4. **Relationships** — pairwise correlation heatmap, VIF, redundancy errors
   (|r| ≥ 0.999 default), geo/time collinearity R² rankings.
5. **Prior specifications** — per-channel LogNormal ROI prior editor (μ, σ) with live
   density curves, prior mean contributions, and the prior probability of a
   negative baseline (Monte-Carlo), with Meridian's remediation guidance.

Each tab ends with an **AI summary** written from your numbers (generated locally —
no API calls), and **Complete analysis** rolls everything up into a verdict with
every finding ranked worst-first.

## 4 · Modeling (tab 3)

- **Google Meridian** (default): full Bayesian MCMC. Controls for chains, adapt,
  burn-in, keep, seed, max adstock lag and knots; kpi_type and revenue-per-KPI;
  channel/organic/treatment/control selection. Your saved ROI priors feed
  `PriorDistribution(roi_m=LogNormal(μ, σ))` per channel. Runs in a background
  thread with live progress. If a control has no geo variation, knots are
  auto-reduced (logged) so the model stays identifiable.
- **Classic ridge** (instant): adstock + Hill saturation + bounded ridge with
  time-series holdout — a seconds-fast approximation for iteration.

Results tab: fit (actual vs posterior expected), baseline vs media-driven,
**ROI with 90% credible intervals**, contribution shares, response curves,
max R-hat convergence badge, and a link to **Meridian's own HTML model report**
(saved under `reports/`).

Budget optimizer tab: Meridian's `BudgetOptimizer` (fixed budget = weekly × 52)
with its HTML optimization report, or the classic response-curve optimizer.

## 5 · REST API

The UI is a thin client over a local API — automate everything. Interactive docs
at **/docs** while running.

| Method & path | Purpose |
|---|---|
| `GET  /api/files` · `POST /api/upload` · `POST /api/load` | manage & load Excel |
| `GET  /api/template` | blank campaign-grain template |
| `POST /api/mapping` | override detected columns/channels |
| `GET  /api/filters` · `POST /api/filters` | facet values; set the global KPI + filters and rebuild the panel |
| `POST /api/eda` | `{"section": "spend_media" ... "complete", "params": {..., "filters": {"SUB_BRAND": "…"}}}` |
| `POST /api/priors` | save per-channel ROI priors |
| `POST /api/meridian/run` → `GET /api/meridian/status` → `GET /api/meridian/results` | Bayesian run |
| `POST /api/meridian/optimize` | Meridian budget optimizer + HTML report |
| `POST /api/model/run` · `POST /api/optimize` | classic engine |

## 6 · MCP — talk to the model from Claude

`mcp_server/server.py` exposes `list_data_files`, `load_excel`, `data_summary`,
`run_model` (classic engine — instant), `channel_roi`, `get_response_curve`,
`optimize_weekly_budget`. Claude Desktop → Settings → Developer → Edit Config:

```json
{
  "mcpServers": {
    "petcare-mmm": {
      "command": "python",
      "args": ["C:/path/to/petcare-mmm-studio/mcp_server/server.py"]
    }
  }
}
```

For Meridian runs from Claude, have it call the REST API above while `python run.py`
is up (full MCMC is too slow for a synchronous MCP tool call).

## 7 · Project layout

```
petcare-mmm-studio/
├── run.py                     # start here → http://127.0.0.1:8050
├── util/                      # install.bat + redeploy.bat (Windows) + their guide
├── requirements.txt
├── app/
│   ├── main.py                # FastAPI backend (REST API + UI + reports)
│   ├── eda.py                 # the five EDA report sections + AI summaries
│   ├── meridian_adapter.py    # Meridian InputData builder, MCMC job, results, optimizer
│   ├── mmm.py                 # classic engine (adstock, saturation, ridge, optimizer)
│   ├── data_loader.py         # Excel ingestion + Meridian mapping auto-detection
│   └── schemas.py
├── mcp_server/server.py       # MCP server for Claude Desktop
├── ui/index.html + app.js     # Mars Petcare–branded web UI (Chart.js + fonts vendored)
├── data/petcare_campaign_long.xlsx     # campaign-grain sample (73,865 rows, 28 dims + METRIC/VALUE)
├── data/.cache/                        # parsed-workbook cache (auto, gitignored)
├── data/meridian_sample_petcare.xlsx   # wide panel: 6 brands × 157 weeks
├── data/sample_marketing_data.xlsx     # simple national sample (classic engine)
├── templates/meridian_template.xlsx    # blank long-format template + column guide
├── reports/                   # Meridian HTML reports land here
└── scripts/generate_sample_data.py
```

## 8 · Branding note

The UI uses the Mars brand palette (Mars Blue `#0000A0`, teal `#00D7B9`, yellow
`#FFDC00`) and references the proprietary **Mars Centra** typeface first in its
font stack — it renders wherever that font is installed and falls back to the
bundled open-licensed **Montserrat**. Chart colors are a colorblind-validated
palette derived from the brand hues.
