"""Generate sample datasets for the Petcare MMM Studio.

The primary dataset is LONG / campaign-grain in METRIC/VALUE (EAV) form: one row
per date x retailer x sub-brand x marketing channel x campaign x creative x
audience x METRIC, where METRIC is one of Clicks / Impressions / Spend (media
inputs) or Units_Sold / Revenue (the KPI) and VALUE carries the number.

Dates are weekly, Saturdays only, spanning H2 2023 through H1 2026. The app
pivots METRIC/VALUE and aggregates up to a BRAND x week panel for Meridian; the
fine grain stays available for filtering and EDA.

Produces:
  data/petcare_campaign_long.xlsx     — long campaign-grain dataset (28 mandatory
        dimension columns + CLICKS/IMPRESSIONS/SPEND + UNITS_SOLD/REVENUE)
  data/meridian_sample_petcare.xlsx   — the same truth pivoted to the wide
        BRAND x week Meridian panel (kept so the classic/Meridian path works
        without a pivot step)
  data/sample_marketing_data.xlsx     — simple national dataset (classic engine demo)
  templates/meridian_template.xlsx    — blank long-format template + column guide

Run:  python scripts/generate_sample_data.py
"""
from __future__ import annotations

import os

import numpy as np
import pandas as pd

RNG = np.random.default_rng(7)
ROOT = os.path.join(os.path.dirname(os.path.abspath(__file__)), "..")
DATA = os.path.join(ROOT, "data")
TPL = os.path.join(ROOT, "templates")

# Weekly, Saturdays only. H2 2023 → H1 2026.
WEEKS = pd.date_range("2023-07-01", "2026-06-27", freq="W-SAT")
N = len(WEEKS)
T = np.arange(N)

# Measures carried in the METRIC column, one row per metric per campaign slice.
MEDIA_METRICS = ["Clicks", "Impressions", "Spend"]
KPI_METRICS = ["Units_Sold", "Revenue"]
METRICS = MEDIA_METRICS + KPI_METRICS

# Retailers the media is bought against.
RETAILERS = ["Walmart", "Chewy", "Kroger", "Target", "Amazon"]

RETAILER_CHANNEL = {
    "Walmart": "Mass",
    "Chewy": "Pet Specialty",
    "Kroger": "Grocery",
    "Target": "Mass",
    "Amazon": "eCommerce",
}

# Geo constants — the sample is a single US market, per spec.
LOCATION_DEFAULT = "US"
GEO_CONSTANTS = {
    "ZONE": "North America",
    "REGION": "NA",
    "COUNTRY": "USA",
    "COUNTRY_REGION": "US",
    "PROVINCE": "National",
    "LOCATION": LOCATION_DEFAULT,
}

MANUFACTURER = "Mars Petcare"

# BRAND is the Meridian geo (the panel unit the model is fit across).
# Each brand carries its sub-brands, which are the filter dimension.
BRANDS = {
    "Pedigree": {
        "population": 5_200_000, "scale": 1.00, "price": 22.0,
        "species": "Dog", "family": "Dry Food",
        "sub_brands": ["Pedigree Adult", "Pedigree Puppy", "Pedigree DentaStix"],
    },
    "Whiskas": {
        "population": 4_800_000, "scale": 0.92, "price": 19.5,
        "species": "Cat", "family": "Wet Food",
        "sub_brands": ["Whiskas Adult", "Whiskas Kitten", "Whiskas Temptations"],
    },
    "Royal Canin": {
        "population": 2_100_000, "scale": 0.55, "price": 41.0,
        "species": "Dog", "family": "Prescription",
        "sub_brands": ["Royal Canin Breed Health", "Royal Canin Veterinary Diet"],
    },
    "Sheba": {
        "population": 2_600_000, "scale": 0.60, "price": 24.5,
        "species": "Cat", "family": "Wet Food",
        "sub_brands": ["Sheba Perfect Portions", "Sheba Craft Collection"],
    },
    "Cesar": {
        "population": 1_700_000, "scale": 0.42, "price": 27.0,
        "species": "Dog", "family": "Wet Food",
        "sub_brands": ["Cesar Classic Loaf", "Cesar Home Delights"],
    },
    "Dreamies": {
        "population": 3_400_000, "scale": 0.70, "price": 12.5,
        "species": "Cat", "family": "Treats",
        "sub_brands": ["Dreamies Mix", "Dreamies Cheese"],
    },
}

# MARKETING_CHANNEL is the media channel dimension (the second filter).
# base = avg weekly national spend, cpm = cost per 1000 impressions,
# decay = adstock, hs_q = half-saturation quantile, max = max weekly effect,
# ctr = click-through rate, type = MARKETING_TYPE bucket.
CHANNELS = {
    "TV":          {"base": 38000, "cpm": 8.0,  "decay": 0.55, "hs_q": 1.1, "max": 5200, "ctr": 0.0000, "type": "Offline"},
    "YouTube":     {"base": 12000, "cpm": 5.5,  "decay": 0.35, "hs_q": 1.0, "max": 1900, "ctr": 0.0090, "type": "Online Video"},
    "Meta":        {"base": 15000, "cpm": 4.5,  "decay": 0.25, "hs_q": 0.9, "max": 2400, "ctr": 0.0120, "type": "Social"},
    "Search":      {"base": 14000, "cpm": 14.0, "decay": 0.05, "hs_q": 0.8, "max": 2800, "ctr": 0.0400, "type": "Search"},
    "RetailMedia": {"base": 9000,  "cpm": 7.0,  "decay": 0.20, "hs_q": 0.9, "max": 1600, "ctr": 0.0055, "type": "Retail Media"},
    "Influencer":  {"base": 5000,  "cpm": 11.0, "decay": 0.45, "hs_q": 1.0, "max": 900,  "ctr": 0.0075, "type": "Social"},
}

ORGANIC_CHANNEL = "Organic Social"

# Per-channel campaign metadata pools, drawn per row to populate the
# campaign-grain dimension columns.
CHANNEL_META = {
    "TV": {
        "objective": ["Awareness"], "media_objective": ["Reach"],
        "placement": ["Linear TV / National", "Linear TV / Spot"],
        "format": ["TV Spot"], "duration": ["30s", "15s"],
        "audience": ["Pet Owners 25-54 / Demo", "Households w Pets / Demo"],
        "creative": ["Hero Film / TV", "Brand Story / TV"],
        "buy": ["Upfront / EN", "Scatter / EN"],
    },
    "YouTube": {
        "objective": ["Awareness", "Consideration"], "media_objective": ["Views", "Reach"],
        "placement": ["YouTube / In-Stream", "YouTube / Shorts", "YouTube / Bumper"],
        "format": ["Skippable In-Stream", "Bumper Ad"], "duration": ["6s", "15s", "30s"],
        "audience": ["Pet Enthusiasts / Affinity", "New Pet Owners / In-Market"],
        "creative": ["Puppy Moments / CTV", "Feeding Ritual / Mobile"],
        "buy": ["Auction / EN", "Reserved / ES"],
    },
    "Meta": {
        "objective": ["Consideration", "Conversion"], "media_objective": ["Engagement", "Traffic"],
        "placement": ["Facebook / Feed", "Instagram / Reels", "Instagram / Stories"],
        "format": ["Single Image", "Carousel", "Short Video"], "duration": ["Static", "15s"],
        "audience": ["Pet Parents / Lookalike", "Cart Abandoners / Retargeting"],
        "creative": ["Bowl Closeup / Mobile", "UGC Testimonial / Mobile"],
        "buy": ["Auction / EN", "Auction / ES"],
    },
    "Search": {
        "objective": ["Conversion"], "media_objective": ["Traffic"],
        "placement": ["Google / Search", "Bing / Search", "Google / Shopping"],
        "format": ["Responsive Search Ad", "Shopping Ad"], "duration": ["Text", "Static"],
        "audience": ["Brand Terms / Intent", "Category Terms / Intent"],
        "creative": ["Brand Copy / Desktop", "Promo Copy / Mobile"],
        "buy": ["Auction / EN", "Auction / ES"],
    },
    "RetailMedia": {
        "objective": ["Conversion"], "media_objective": ["Sales"],
        "placement": ["Onsite / Search", "Onsite / Display", "Offsite / Display"],
        "format": ["Sponsored Product", "Sponsored Brand", "Display Banner"],
        "duration": ["Static", "300x250"],
        "audience": ["Category Shoppers / Behavioral", "Repeat Buyers / CRM"],
        "creative": ["Pack Shot / Desktop", "Value Msg / Mobile"],
        "buy": ["Auction / EN", "Managed Service / EN"],
    },
    "Influencer": {
        "objective": ["Awareness", "Consideration"], "media_objective": ["Engagement"],
        "placement": ["Instagram / Creator Post", "TikTok / Creator Video"],
        "format": ["Creator Video", "Creator Photo"], "duration": ["30s", "Static"],
        "audience": ["Creator Followers / Organic", "Pet Community / Affinity"],
        "creative": ["Creator Unboxing / Mobile", "Day In The Life / Mobile"],
        "buy": ["Fixed Fee / EN", "Fixed Fee / ES"],
    },
    ORGANIC_CHANNEL: {
        "objective": ["Awareness"], "media_objective": ["Engagement"],
        "placement": ["Instagram / Organic", "Facebook / Organic"],
        "format": ["Organic Post"], "duration": ["Static"],
        "audience": ["Brand Followers / Organic"],
        "creative": ["Community Post / Mobile"],
        "buy": ["Owned / EN"],
    },
}

# Named campaigns per channel — realistic, seasonal, brand-agnostic themes.
# The final CAMPAIGN_NAME is "<Brand> <Theme> <Season> <Year>".
CAMPAIGN_THEMES = {
    "TV": ["Tails of Joy", "Feed The Bond", "Every Bowl Counts"],
    "YouTube": ["Puppy Diaries", "Unboxing Happy", "Real Pet Stories"],
    "Meta": ["Bowl Envy", "Paws & Share", "Tag Your Pet"],
    "Search": ["Always On Brand", "Category Capture", "Compare & Convert"],
    "RetailMedia": ["Shelf Wins", "Basket Builder", "Cart Closer"],
    "Influencer": ["Creator Kitchen", "Pet Parent Picks", "Day With My Pet"],
    ORGANIC_CHANNEL: ["Community Corner", "Pet Tips Weekly"],
}

# Season is derived from the week so campaign names read naturally.
SEASONS = {1: "Spring", 2: "Summer", 3: "Autumn", 4: "Holiday"}


def campaign_name(brand: str, channel: str, ts: pd.Timestamp, idx: int) -> str:
    theme = CAMPAIGN_THEMES[channel][idx % len(CAMPAIGN_THEMES[channel])]
    return f"{brand} {theme} {SEASONS[ts.quarter]} {ts.year}"


# The 28 mandatory dimension columns, in the required order.
DIMENSION_COLUMNS = [
    "DATE", "ZONE", "REGION", "COUNTRY", "COUNTRY_REGION", "PROVINCE", "LOCATION",
    "RETAIL_CHANNEL", "CHANNEL_NAME", "RETAILER_NAME", "MANUFACTURER",
    "PRODUCT_FAMILY", "SPECIES", "BRAND", "SUB_BRAND", "BRAND_TECH", "SKU_NAME",
    "MARKETING_TYPE", "MARKETING_CHANNEL", "CAMPAIGN_NAME", "CAMPAIGN_OBJECTIVE",
    "MEDIA_OBJECTIVE", "PLATFORM_PLACEMENT", "FORMAT_CAMPAIGN_TYPE",
    "DURATION_LENGTH_SIZE", "AUDIENCE_NAME_AUDIENCE_TYPE",
    "CREATIVE_NAME_DEVICE_NAME", "BUY_TYPE_LANGUAGE",
]

# Every measure lives in the METRIC / VALUE pair — one row per metric.
LONG_COLUMNS = DIMENSION_COLUMNS + ["METRIC", "VALUE"]


def adstock(x, decay):
    out, carry = np.zeros(len(x)), 0.0
    for i, v in enumerate(x):
        carry = v + decay * carry
        out[i] = carry
    return out


def hill(x, hs, shape=1.2):
    x = np.clip(x, 0, None)
    return x**shape / (x**shape + hs**shape)


def bursty(base, every, ln, mult, noise=0.25, dark=0.05):
    s = np.full(N, float(base))
    for start in range(0, N, every):
        s[start:start + ln] *= mult
    s *= 1 + noise * RNG.standard_normal(N)
    s[RNG.random(N) < dark] = 0.0
    return np.clip(s, 0, None)


def _pick(pool, i):
    """Deterministic-ish spread across a metadata pool."""
    return pool[i % len(pool)]


def build_panel() -> pd.DataFrame:
    """Build the wide BRAND x week panel — the ground truth the long file expands.

    Returns one row per brand x week with per-channel impressions/spend/clicks,
    organic media, controls, and both KPIs.
    """
    rows = []
    week_of_year = WEEKS.isocalendar().week.to_numpy(dtype=float)
    season = np.sin(2 * np.pi * (week_of_year - 6) / 52.0)
    holiday = np.isin(WEEKS.month, [11, 12]).astype(int)

    for brand, b in BRANDS.items():
        sc = b["scale"]
        promo = (RNG.random(N) < 0.16).astype(int)
        gqv = np.clip(100 * (1 + 0.25 * season + 0.10 * holiday + 0.004 * T)
                      * (1 + 0.08 * RNG.standard_normal(N)), 20, None)
        price = b["price"] * (1 + 0.002 * T / 52) * (1 - 0.06 * promo) \
                * (1 + 0.01 * RNG.standard_normal(N))
        dist = np.clip(74 + 6 * sc + 0.04 * T + RNG.standard_normal(N), 60, 99)
        organic_imp = np.clip(sc * 900_000 * (1 + 0.3 * season + 0.2 * holiday)
                              * (1 + 0.35 * RNG.standard_normal(N)), 0, None)

        spends, imps, clicks, effect = {}, {}, {}, np.zeros(N)
        for ch, c in CHANNELS.items():
            mix = 1 + 0.35 * RNG.standard_normal()  # brand-specific channel tilt
            sp = bursty(c["base"] * sc * max(mix, 0.25), RNG.integers(6, 14),
                        RNG.integers(2, 5), RNG.uniform(1.5, 2.4))
            cpm = c["cpm"] * (1 + 0.10 * RNG.standard_normal(N)) * (1 + 0.002 * T / 52)
            im = np.where(sp > 0, sp / np.clip(cpm, .5, None) * 1000, 0.0)
            ctr = c["ctr"] * (1 + 0.15 * RNG.standard_normal(N))
            cl = np.clip(im * np.clip(ctr, 0, None), 0, None)
            spends[ch], imps[ch], clicks[ch] = sp.round(0), im.round(0), cl.round(0)
            ad = adstock(im, c["decay"])
            hs = np.quantile(ad[ad > 0], 0.6) * c["hs_q"] if (ad > 0).any() else 1.0
            effect += c["max"] * sc * hill(ad, hs)

        organic_effect = 600 * sc * hill(adstock(organic_imp, 0.3),
                                         np.quantile(organic_imp, 0.6) + 1e-9)
        organic_clicks = np.clip(organic_imp * 0.004
                                 * (1 + 0.2 * RNG.standard_normal(N)), 0, None)
        base_units = (b["population"] / 52.0) * 0.011
        units = np.clip(
            base_units * (1 + 0.16 * season + 0.10 * holiday + 0.0012 * T)
            + effect + organic_effect
            + 320 * sc * promo
            - 95 * sc * (price - b["price"])
            + 26 * sc * (dist - dist.mean())
            + 90 * sc * RNG.standard_normal(N),
            base_units * 0.25, None)
        rpu = price * RNG.uniform(0.97, 1.03)

        for i in range(N):
            row = {
                "time": WEEKS[i].date().isoformat(),
                "geo": brand,
                "population": b["population"],
                "units_sold": round(float(units[i]), 1),
                "revenue_per_unit": round(float(rpu[i]), 2),
                "revenue": round(float(units[i] * rpu[i]), 0),
                "GQV": round(float(gqv[i]), 1),
                "avg_price": round(float(price[i]), 2),
                "distribution_pct": round(float(dist[i]), 1),
                "holiday_flag": int(holiday[i]),
                "Promo": int(promo[i]),
                "Organic_social_impression": float(organic_imp[i].round(0)),
                "Organic_social_click": float(organic_clicks[i].round(0)),
            }
            for ch in CHANNELS:
                row[f"{ch}_impression"] = float(imps[ch][i])
                row[f"{ch}_spend"] = float(spends[ch][i])
                row[f"{ch}_click"] = float(clicks[ch][i])
            rows.append(row)
    return pd.DataFrame(rows)


def build_long(panel: pd.DataFrame) -> pd.DataFrame:
    """Expand the brand x week panel into campaign-grain long rows.

    Each brand-week's channel totals are split across retailers, sub-brands and
    campaign metadata so that summing CLICKS/IMPRESSIONS/SPEND back up over
    DATE x BRAND x MARKETING_CHANNEL exactly reproduces the panel. The KPI is
    carried on the row-share too, so the KPI also re-aggregates exactly.
    """
    rows = []
    channels = list(CHANNELS) + [ORGANIC_CHANNEL]

    for brand, b in BRANDS.items():
        sub_brands = b["sub_brands"]
        pan = panel[panel.geo == brand].reset_index(drop=True)

        # A stable split of each brand across its sub-brands.
        sb_w = RNG.dirichlet(np.full(len(sub_brands), 4.0))

        for week_idx, (_, prow) in enumerate(pan.iterrows()):
            date = prow["time"]
            for ch_idx, ch in enumerate(channels):
                if ch == ORGANIC_CHANNEL:
                    imp = float(prow["Organic_social_impression"])
                    clk = float(prow["Organic_social_click"])
                    spend = 0.0
                else:
                    imp = float(prow[f"{ch}_impression"])
                    clk = float(prow[f"{ch}_click"])
                    spend = float(prow[f"{ch}_spend"])
                if imp <= 0 and spend <= 0:
                    continue

                meta = CHANNEL_META[ch]
                mtype = ("Organic" if ch == ORGANIC_CHANNEL
                         else CHANNELS[ch]["type"])

                for si, sb in enumerate(sub_brands):
                        # One retailer per slice rather than a fan-out across
                        # all five: keeps the file ~5x smaller while every
                        # retailer still appears often enough to filter on. The
                        # rotation is deterministic, so totals reproduce.
                        ri = (si + week_idx + ch_idx) % len(RETAILERS)
                        retailer = RETAILERS[ri]
                        w = sb_w[si]
                        r_imp, r_clk, r_spend = imp * w, clk * w, spend * w
                        if r_imp < 1 and r_spend < 1:
                            continue

                        idx = si + ri
                        ts = pd.Timestamp(date)
                        # KPI is attributed on the same share so it re-aggregates.
                        measures = {
                            "Clicks": round(r_clk, 0),
                            "Impressions": round(r_imp, 0),
                            "Spend": round(r_spend, 2),
                            "Units_Sold": round(float(prow["units_sold"]) * w / len(channels), 3),
                            "Revenue": round(float(prow["revenue"]) * w / len(channels), 2),
                        }
                        dims = {
                            "DATE": date,
                            **GEO_CONSTANTS,
                            "RETAIL_CHANNEL": RETAILER_CHANNEL[retailer],
                            "CHANNEL_NAME": f"{retailer} {RETAILER_CHANNEL[retailer]}",
                            "RETAILER_NAME": retailer,
                            "MANUFACTURER": MANUFACTURER,
                            "PRODUCT_FAMILY": b["family"],
                            "SPECIES": b["species"],
                            "BRAND": brand,
                            "SUB_BRAND": sb,
                            "BRAND_TECH": f"{sb} Core",
                            "SKU_NAME": f"{sb} {(idx % 3) * 400 + 400}g",
                            "MARKETING_TYPE": mtype,
                            "MARKETING_CHANNEL": ch,
                            "CAMPAIGN_NAME": campaign_name(brand, ch, ts, idx),
                            "CAMPAIGN_OBJECTIVE": _pick(meta["objective"], idx),
                            "MEDIA_OBJECTIVE": _pick(meta["media_objective"], idx),
                            "PLATFORM_PLACEMENT": _pick(meta["placement"], idx),
                            "FORMAT_CAMPAIGN_TYPE": _pick(meta["format"], idx),
                            "DURATION_LENGTH_SIZE": _pick(meta["duration"], idx),
                            "AUDIENCE_NAME_AUDIENCE_TYPE": _pick(meta["audience"], idx),
                            "CREATIVE_NAME_DEVICE_NAME": _pick(meta["creative"], idx),
                            "BUY_TYPE_LANGUAGE": _pick(meta["buy"], idx),
                        }
                        # One row per metric — the METRIC / VALUE pair.
                        for metric, value in measures.items():
                            rows.append({**dims, "METRIC": metric, "VALUE": value})
    return pd.DataFrame(rows, columns=LONG_COLUMNS)


COLUMN_DOC = [
    ("DATE", "REQUIRED", "Period start date (YYYY-MM-DD). Weekly recommended; 104+ weeks per brand."),
    ("ZONE / REGION / COUNTRY / COUNTRY_REGION / PROVINCE", "REQUIRED", "Geographic hierarchy. Constant for a single-market file."),
    ("LOCATION", "REQUIRED", "Market the row belongs to. Defaults to 'US'."),
    ("RETAIL_CHANNEL", "REQUIRED", "Retail environment: Mass, Grocery, Pet Specialty, eCommerce…"),
    ("CHANNEL_NAME", "REQUIRED", "Retailer + retail channel label."),
    ("RETAILER_NAME", "REQUIRED", "Retailer the media is bought against: Walmart, Chewy, Kroger, Target, Amazon."),
    ("MANUFACTURER", "REQUIRED", "Manufacturer / vendor name."),
    ("PRODUCT_FAMILY", "REQUIRED", "Product family: Dry Food, Wet Food, Treats, Prescription…"),
    ("SPECIES", "REQUIRED", "Dog / Cat / Other."),
    ("BRAND", "REQUIRED — the model geo", "Brand. This is the panel unit Meridian is fit across (one model row per BRAND x DATE)."),
    ("SUB_BRAND", "REQUIRED — filter", "Sub-brand. Exposed as a per-report filter; rows are aggregated up to BRAND for modeling."),
    ("BRAND_TECH", "REQUIRED", "Brand technology / platform descriptor."),
    ("SKU_NAME", "REQUIRED", "SKU the activity ran behind."),
    ("MARKETING_TYPE", "REQUIRED", "Media type bucket: Offline, Online Video, Social, Search, Retail Media, Organic."),
    ("MARKETING_CHANNEL", "REQUIRED — filter", "Media channel: TV, YouTube, Meta, Search, RetailMedia, Influencer, Organic Social. Exposed as a per-report filter and pivoted into Meridian's per-channel media columns."),
    ("CAMPAIGN_NAME", "REQUIRED", "Campaign identifier."),
    ("CAMPAIGN_OBJECTIVE", "REQUIRED", "Awareness / Consideration / Conversion."),
    ("MEDIA_OBJECTIVE", "REQUIRED", "Reach / Views / Engagement / Traffic / Sales."),
    ("PLATFORM_PLACEMENT", "REQUIRED", "Platform and placement, e.g. 'Instagram / Reels'."),
    ("FORMAT_CAMPAIGN_TYPE", "REQUIRED", "Ad format / campaign type."),
    ("DURATION_LENGTH_SIZE", "REQUIRED", "Creative duration, length or size (6s, 30s, 300x250, Static…)."),
    ("AUDIENCE_NAME_AUDIENCE_TYPE", "REQUIRED", "Audience name and targeting type."),
    ("CREATIVE_NAME_DEVICE_NAME", "REQUIRED", "Creative name and device."),
    ("BUY_TYPE_LANGUAGE", "REQUIRED", "Buy type and creative language."),
    ("METRIC", "REQUIRED", "Which measure this row carries: Clicks, Impressions, Spend, Units_Sold or Revenue. One row per metric per campaign slice."),
    ("VALUE", "REQUIRED", "The numeric value of METRIC for this row. Non-negative and summable. Gaps = 0; organic Spend rows = 0."),
    ("", "", "Clicks / Impressions / Spend are the media inputs. Units_Sold and Revenue are the KPI — either is selectable in the UI, and Clicks may also be modeled as an engagement KPI."),
    ("", "", "DATE is weekly, Saturdays only. No missing cells. The app pivots METRIC/VALUE and aggregates DATE x BRAND x MARKETING_CHANNEL into the Meridian panel, so every brand needs every week."),
]


def write_template():
    os.makedirs(TPL, exist_ok=True)
    path = os.path.join(TPL, "meridian_template.xlsx")
    base = {
        "DATE": "2024-01-06",
        "ZONE": "North America", "REGION": "NA", "COUNTRY": "USA",
        "COUNTRY_REGION": "US", "PROVINCE": "National", "LOCATION": "US",
        "RETAIL_CHANNEL": "Mass", "CHANNEL_NAME": "Walmart Mass",
        "RETAILER_NAME": "Walmart", "MANUFACTURER": "Mars Petcare",
        "PRODUCT_FAMILY": "Dry Food", "SPECIES": "Dog", "BRAND": "Pedigree",
        "SUB_BRAND": "Pedigree Adult", "BRAND_TECH": "Pedigree Adult Core",
        "SKU_NAME": "Pedigree Adult 800g", "MARKETING_TYPE": "Social",
        "MARKETING_CHANNEL": "Meta", "CAMPAIGN_NAME": "Pedigree Bowl Envy Spring 2024",
        "CAMPAIGN_OBJECTIVE": "Consideration", "MEDIA_OBJECTIVE": "Engagement",
        "PLATFORM_PLACEMENT": "Instagram / Reels",
        "FORMAT_CAMPAIGN_TYPE": "Short Video", "DURATION_LENGTH_SIZE": "15s",
        "AUDIENCE_NAME_AUDIENCE_TYPE": "Pet Parents / Lookalike",
        "CREATIVE_NAME_DEVICE_NAME": "Bowl Closeup / Mobile",
        "BUY_TYPE_LANGUAGE": "Auction / EN",
    }
    # One example row per metric, so the shape of METRIC/VALUE is obvious.
    example = pd.DataFrame(
        [{**base, "METRIC": m, "VALUE": v} for m, v in
         [("Clicks", 3120.0), ("Impressions", 260000.0), ("Spend", 1170.0),
          ("Units_Sold", 91.4), ("Revenue", 1966.0)]],
        columns=LONG_COLUMNS)
    doc = pd.DataFrame(COLUMN_DOC, columns=["column", "requirement", "notes"])
    with pd.ExcelWriter(path, engine="openpyxl") as xw:
        example.head(0).to_excel(xw, index=False, sheet_name="data")       # headers only
        example.to_excel(xw, index=False, sheet_name="example_row")
        doc.to_excel(xw, index=False, sheet_name="column_guide")
    print("template ->", os.path.abspath(path))


def build_wide_from_panel(panel: pd.DataFrame) -> pd.DataFrame:
    """The Meridian-format wide panel (brand x week), unchanged in shape from v2."""
    keep = ["time", "geo", "population", "units_sold", "revenue_per_unit", "revenue",
            "GQV", "avg_price", "distribution_pct", "holiday_flag", "Promo",
            "Organic_social_impression"] + \
           [f"{ch}_{kind}" for ch in CHANNELS for kind in ("impression", "spend")]
    return panel[keep].copy()


def build_classic_sample(panel: pd.DataFrame) -> pd.DataFrame:
    """Small national file for the instant classic engine (kept from v1)."""
    g = panel.groupby("time", as_index=False).agg(
        revenue=("revenue", "sum"),
        **{f"{ch.lower()}_spend": (f"{ch}_spend", "sum") for ch in CHANNELS},
        avg_price=("avg_price", "mean"), distribution_pct=("distribution_pct", "mean"),
        promo_flag=("Promo", "max"), holiday_flag=("holiday_flag", "max"),
    )
    return g.rename(columns={"time": "week"})


if __name__ == "__main__":
    os.makedirs(DATA, exist_ok=True)
    panel = build_panel()

    long_df = build_long(panel)
    lpath = os.path.join(DATA, "petcare_campaign_long.xlsx")
    long_df.to_excel(lpath, index=False, sheet_name="campaign_data")
    print(f"long sample     -> {os.path.abspath(lpath)}  ({len(long_df):,} rows = "
          f"{long_df.BRAND.nunique()} brands x {long_df.SUB_BRAND.nunique()} sub-brands x "
          f"{long_df.MARKETING_CHANNEL.nunique()} channels x {long_df.DATE.nunique()} weeks x "
          f"{long_df.METRIC.nunique()} metrics)")
    print(f"                   metrics: {sorted(long_df.METRIC.unique())}")
    print(f"                   dates:   {long_df.DATE.min()} -> {long_df.DATE.max()} "
          f"({pd.Timestamp(long_df.DATE.min()).day_name()}s)")
    print(f"                   campaigns: {long_df.CAMPAIGN_NAME.nunique()}, e.g. "
          f"{sorted(long_df.CAMPAIGN_NAME.unique())[0]!r}")

    wide = build_wide_from_panel(panel)
    mpath = os.path.join(DATA, "meridian_sample_petcare.xlsx")
    wide.to_excel(mpath, index=False, sheet_name="weekly_data")
    print(f"meridian sample -> {os.path.abspath(mpath)}  ({len(wide)} rows = "
          f"{wide.geo.nunique()} brands x {wide.time.nunique()} weeks)")

    cdf = build_classic_sample(panel)
    cpath = os.path.join(DATA, "sample_marketing_data.xlsx")
    cdf.to_excel(cpath, index=False, sheet_name="weekly_data")
    print(f"classic sample  -> {os.path.abspath(cpath)}  ({len(cdf)} rows)")

    write_template()
