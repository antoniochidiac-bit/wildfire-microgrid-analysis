"""
06_prioritization_matrix.py
Four-dimension corridor-level prioritization matrix.

Corridors are defined as county × utility owner × voltage class (4 bins):
  <115kV | 115-229kV | 230-499kV | 500kV+

Four dimensions, each scored 0–10:
  D1 Life Safety     (weight 0.35): CF density, SVI, elderly %
  D2 Resilience Gap  (weight 0.25): SGIP gap, redundancy, CF isolation, sub distance
  D3 Financial       (weight 0.25): avoided cost proxy, net cost/MW, GDP, payback
  D4 Ecological Risk (weight 0.15): critical habitat, watershed, fire perimeters

Combined priority = 0.35·D1 + 0.25·D2 + 0.25·D3 + 0.15·D4

Action tiers: ≥7 Act Now | 5–7 Plan Now | 3–5 Monitor | <3 Defer

Outputs:
  data/processed/prioritization_matrix.csv
"""

import warnings
warnings.filterwarnings("ignore")

import re, time
import numpy as np
import pandas as pd
import geopandas as gpd
from pathlib import Path

# ── Paths ──────────────────────────────────────────────────────────────────────
ROOT      = Path(__file__).resolve().parent.parent
RISK_F    = ROOT / "data/processed/transmission_risk_scores.gpkg"
SGIP_F    = ROOT / "data/raw/microgrids/sgip_data.csv"
SVI_F     = ROOT / "data/raw/census/svi_california_2022.csv"
GDP_F     = ROOT / "data/raw/financial/county_gdp.csv"
CF_F      = ROOT / "data/raw/microgrids/critical_facilities.gpkg"
SUBS_F    = ROOT / "data/raw/substations/substations.gpkg"
HAB_F     = ROOT / "data/raw/ecology/critical_habitat.gpkg"
WSHED_F   = ROOT / "data/raw/ecology/watersheds.gpkg"
OUT_F     = ROOT / "data/processed/prioritization_matrix.csv"

PROJ_EPSG      = 3310     # CA Albers (metres)
CORRIDOR_BUF_M = 15_000   # 15 km representative buffer for spatial overlaps
PERSONS_PER_HH = 2.5
KWH_PER_HH     = 1.5
KWH_VOLL       = 9.0
SGIP_ISLANDING = 0.30     # baseline islanding fraction for capacity sizing

CA_COUNTY_FIPS = {
    "001":"Alameda","003":"Alpine","005":"Amador","007":"Butte","009":"Calaveras",
    "011":"Colusa","013":"Contra Costa","015":"Del Norte","017":"El Dorado","019":"Fresno",
    "021":"Glenn","023":"Humboldt","025":"Imperial","027":"Inyo","029":"Kern",
    "031":"Kings","033":"Lake","035":"Lassen","037":"Los Angeles","039":"Madera",
    "041":"Marin","043":"Mariposa","045":"Mendocino","047":"Merced","049":"Modoc",
    "051":"Mono","053":"Monterey","055":"Napa","057":"Nevada","059":"Orange",
    "061":"Placer","063":"Plumas","065":"Riverside","067":"Sacramento","069":"San Benito",
    "071":"San Bernardino","073":"San Diego","075":"San Francisco","077":"San Joaquin",
    "079":"San Luis Obispo","081":"San Mateo","083":"Santa Barbara","085":"Santa Clara",
    "087":"Santa Cruz","089":"Shasta","091":"Sierra","093":"Siskiyou","095":"Solano",
    "097":"Sonoma","099":"Stanislaus","101":"Sutter","103":"Tehama","105":"Trinity",
    "107":"Tulare","109":"Tuolumne","111":"Ventura","113":"Yolo","115":"Yuba",
}
CA_NAME_TO_FIPS = {v: k for k, v in CA_COUNTY_FIPS.items()}
CA_FIPS_TO_NAME = CA_COUNTY_FIPS

# ── Helpers ────────────────────────────────────────────────────────────────────
def norm01(s: pd.Series) -> pd.Series:
    """Min-max normalize to [0, 1]. Returns 0.5 if range == 0."""
    mn, mx = s.min(), s.max()
    if mx == mn:
        return pd.Series(0.5, index=s.index)
    return (s - mn) / (mx - mn)

def score_dim(components: dict) -> pd.Series:
    """
    Weighted sum of normalized components → series of [0, 1] scores.
    components = {col_name: (series, weight, higher_is_better_bool)}
    """
    total_w = sum(w for _, w, _ in components.values())
    result  = pd.Series(0.0, index=next(iter(components.values()))[0].index)
    for _, (series, weight, higher_is_good) in components.items():
        n = norm01(series)
        if not higher_is_good:
            n = 1 - n
        result += n * (weight / total_w)
    return result

t0 = time.time()

# ══════════════════════════════════════════════════════════════════════════════
# LOAD DATA
# ══════════════════════════════════════════════════════════════════════════════
print("Loading data...", flush=True)

risk  = gpd.read_file(RISK_F)
cf    = gpd.read_file(CF_F)
subs  = gpd.read_file(SUBS_F)
hab   = gpd.read_file(HAB_F)
wshed = gpd.read_file(WSHED_F)

sgip_raw = pd.read_csv(SGIP_F, low_memory=False)
svi_raw  = pd.read_csv(SVI_F, dtype=str)
gdp      = pd.read_csv(GDP_F)

print(f"  Risk segments: {len(risk):,}  |  CF: {len(cf):,}  "
      f"|  Subs: {len(subs):,}  |  Habitat: {len(hab):,}  "
      f"|  Watersheds: {len(wshed):,}", flush=True)

# ══════════════════════════════════════════════════════════════════════════════
# DEFINE CORRIDORS
# ══════════════════════════════════════════════════════════════════════════════
print("\nDefining corridors...", flush=True)

KV_BINS   = [0, 114.9, 229.9, 499.9, 9999]
KV_LABELS = ["<115kV", "115-229kV", "230-499kV", "500kV+"]

risk["kv_class"] = pd.cut(
    risk["kv_num"], bins=KV_BINS, labels=KV_LABELS, right=True
).astype(str).replace("nan", "<115kV")
risk["kv_class"] = risk["kv_class"].fillna("<115kV")

# Compute line length in km from geometry (not present in risk scores file)
risk_for_len = risk.to_crs(epsg=PROJ_EPSG)
risk["line_length_km"] = (risk_for_len.geometry.length / 1000).round(3)

GRP_KEYS = ["county", "Owner", "kv_class"]

# ── Tabular corridor aggregation ──────────────────────────────────────────────
corr = (
    risk.groupby(GRP_KEYS, observed=True)
    .agg(
        n_segs            = ("composite_score",   "count"),
        total_km          = ("line_length_km",     "sum"),
        mean_composite    = ("composite_score",    "mean"),
        max_composite     = ("composite_score",    "max"),
        mean_hazard       = ("hazard_score",       "mean"),
        mean_exposure     = ("exposure_score",     "mean"),
        mean_vuln         = ("vulnerability_score","mean"),
        mean_cbg_10km     = ("cbg_count_10km",     "mean"),
        mean_fire_perims  = ("fire_perims_5km",    "mean"),
        mean_bp           = ("bp_mean",            "mean"),
    )
    .reset_index()
)
N = len(corr)
print(f"  {N} corridors (county × owner × kv_class)", flush=True)

# Estimated corridor population proxy:
#   mean_cbg_10km × mean_CBG_population (CA average ≈ 1543/CBG)
MEAN_POP_PER_CBG = 1543
corr["pop_est"] = (corr["mean_cbg_10km"] * MEAN_POP_PER_CBG).round(0)

# ── Project all segment layers once ──────────────────────────────────────────
print("  Projecting layers...", flush=True)
risk_proj  = risk.to_crs(epsg=PROJ_EPSG).copy()
subs_proj  = subs.to_crs(epsg=PROJ_EPSG)
hab_proj   = hab.to_crs(epsg=PROJ_EPSG)
wshed_proj = wshed.to_crs(epsg=PROJ_EPSG)

# Assign corr_idx to each segment (integer position matching corr row)
corr_key_to_idx = {
    (row.county, row.Owner, row.kv_class): i
    for i, row in corr.iterrows()
}
risk_proj["corr_idx"] = [
    corr_key_to_idx.get((r.county, r.Owner, r.kv_class), -1)
    for r in risk_proj.itertuples()
]
risk_proj = risk_proj[risk_proj["corr_idx"] >= 0].copy()

# ── Per-segment 15 km buffer GDF (vectorized) ─────────────────────────────
print("  Building per-segment 15 km buffers (vectorized)...", flush=True)
seg_buf = risk_proj[["corr_idx", "geometry"]].copy()
seg_buf["geometry"] = seg_buf.geometry.buffer(CORRIDOR_BUF_M)
seg_buf_gdf = gpd.GeoDataFrame(seg_buf, crs=PROJ_EPSG)

# ── Per-segment centroid GDF (vectorized) ─────────────────────────────────
seg_cent = risk_proj[["corr_idx", "geometry"]].copy()
seg_cent["geometry"] = seg_cent.geometry.interpolate(0.5, normalized=True)
seg_cent_gdf = gpd.GeoDataFrame(seg_cent, crs=PROJ_EPSG)

# ══════════════════════════════════════════════════════════════════════════════
# COUNTY-LEVEL LOOKUPS
# ══════════════════════════════════════════════════════════════════════════════
print("Building county-level lookups...", flush=True)

# ── SVI by county ──────────────────────────────────────────────────────────
svi_raw["county_fips"]   = svi_raw["FIPS"].str[2:5]
svi_raw["RPL_f"]         = pd.to_numeric(svi_raw["RPL_THEMES"], errors="coerce")
svi_raw["EP_AGE65_f"]    = pd.to_numeric(svi_raw["EP_AGE65"],   errors="coerce")
svi_raw["E_TOTPOP_f"]    = pd.to_numeric(svi_raw["E_TOTPOP"],   errors="coerce")

svi_c = svi_raw[(svi_raw["RPL_f"] >= 0) & (svi_raw["E_TOTPOP_f"] >= 0)].copy()
county_svi = (
    svi_c.groupby("county_fips")
    .agg(
        mean_svi     = ("RPL_f",      "mean"),
        mean_age65   = ("EP_AGE65_f", "mean"),
        total_pop    = ("E_TOTPOP_f", "sum"),
    )
    .reset_index()
)
county_svi["county"] = county_svi["county_fips"].map(CA_FIPS_TO_NAME)
print(f"  SVI county aggregations: {len(county_svi)}")

# ── Critical facilities by county ─────────────────────────────────────────
cf["county_std"] = cf["COUNTY"].str.title()
county_cf = (
    cf.groupby("county_std")
    .agg(
        n_hospitals     = ("facility_type", lambda x: (x == "Hospital").sum()),
        n_fire_stations = ("facility_type", lambda x: (x == "Fire Station").sum()),
        n_cf_total      = ("facility_type", "count"),
    )
    .reset_index()
    .rename(columns={"county_std": "county"})
)
# CF density per 100k population
county_cf = county_cf.merge(
    county_svi[["county", "total_pop"]], on="county", how="left"
)
county_cf["total_pop"] = county_cf["total_pop"].fillna(MEAN_POP_PER_CBG * 10)
county_cf["cf_per_100k"] = (
    county_cf["n_cf_total"] / county_cf["total_pop"] * 100_000
).round(2)
print(f"  CF county aggregations: {len(county_cf)}")

# ── SGIP capacity by county ────────────────────────────────────────────────
COUNTY_FIXES = {
    "Los Angeled": "Los Angeles", "Los Angeles County": "Los Angeles",
    "San Diego County": "San Diego", "Orange County": "Orange",
    "Ca": None, "Usa": None,
}
def clean_county(s):
    if pd.isna(s): return None
    s = re.sub(r"\s+[Cc]ounty$", "", str(s).strip()).title().strip()
    return COUNTY_FIXES.get(s, s)

sgip_active = sgip_raw[
    sgip_raw["Fully Qualified State"].isin(["Payment Completed", "RRF Confirmed"]) &
    sgip_raw["Equipment Type"].str.contains("Storage", na=False)
].copy()
sgip_active["county_std"] = sgip_active["County"].apply(clean_county)
county_sgip = (
    sgip_active.dropna(subset=["county_std"])
    .groupby("county_std")
    .agg(
        sgip_kw  = ("Rated Capacity [kW]", "sum"),
        sgip_kwh = ("Energy Storage Capacity (kWh)", "sum"),
        n_sgip   = ("Rated Capacity [kW]", "count"),
    )
    .reset_index()
    .rename(columns={"county_std": "county"})
)
# Required capacity (county-level, 30% islanding baseline)
county_sgip = county_sgip.merge(county_svi[["county","total_pop"]], on="county", how="left")
county_sgip["required_kw"] = (
    county_sgip["total_pop"].fillna(0) / PERSONS_PER_HH * KWH_PER_HH * SGIP_ISLANDING
)
county_sgip["sgip_ratio"] = (
    county_sgip["sgip_kw"] / county_sgip["required_kw"].clip(lower=1)
).clip(upper=1.0)   # cap at 1.0 (fully covered)
print(f"  SGIP county aggregations: {len(county_sgip)}")

# ── GDP by county ──────────────────────────────────────────────────────────
gdp["county"] = gdp["GeoName_clean"].str.replace(r",\s*CA$", "", regex=True).str.strip()
county_gdp = gdp[["county", "GDP_millions"]].copy()
print(f"  GDP county records: {len(county_gdp)}")

# ══════════════════════════════════════════════════════════════════════════════
# SPATIAL FEATURES
# ══════════════════════════════════════════════════════════════════════════════
print("\nComputing spatial features...", flush=True)

# ── Critical habitat overlap — segment buffers vs habitat polygons ────────
print("  Critical habitat overlap...", flush=True)
hab_join = gpd.sjoin(
    seg_buf_gdf[["corr_idx","geometry"]],
    hab_proj[["geometry","comname"]],
    how="left", predicate="intersects"
)
hab_species = (
    hab_join.dropna(subset=["comname"])
    .groupby("corr_idx")["comname"]
    .nunique()
    .reindex(range(N), fill_value=0)
)
corr["habitat_species"] = hab_species.values
print(f"    Corridors with habitat overlap: {(corr['habitat_species']>0).sum()}/{N}")

# ── Watershed overlap — segment buffers vs HUC8 polygons ─────────────────
print("  Watershed overlap...", flush=True)
wshed_join = gpd.sjoin(
    seg_buf_gdf[["corr_idx","geometry"]],
    wshed_proj[["geometry","huc8"]],
    how="left", predicate="intersects"
)
wshed_count = (
    wshed_join.dropna(subset=["huc8"])
    .groupby("corr_idx")["huc8"]
    .nunique()
    .reindex(range(N), fill_value=0)
)
corr["watershed_count"] = wshed_count.values
print(f"    Corridors with watershed overlap: {(corr['watershed_count']>0).sum()}/{N}")

# ── Distance to nearest substation — from segment midpoints ──────────────
print("  Substation proximity...", flush=True)
near_sub = gpd.sjoin_nearest(
    seg_cent_gdf[["corr_idx","geometry"]],
    subs_proj[["geometry"]],
    how="left",
    distance_col="sub_dist_m",
)
# Per-corridor: minimum distance across all its segments
sub_min = (
    near_sub.groupby("corr_idx")["sub_dist_m"]
    .min()
    .reindex(range(N), fill_value=50_000)
)
corr["sub_dist_km"] = (sub_min.values / 1000).round(2)
print(f"    Substation dist: min={corr['sub_dist_km'].min():.1f} km  "
      f"max={corr['sub_dist_km'].max():.1f} km  "
      f"median={corr['sub_dist_km'].median():.1f} km")

# ── Redundancy proxy: other corridors in same county + kv_class ──────────
# More alternatives at same voltage = more meshed = less isolated
kv_county_count = (
    corr.groupby(["county", "kv_class"], observed=True)
    .size()
    .reset_index(name="n_same_kv")
)
corr = corr.merge(kv_county_count, on=["county","kv_class"], how="left")
# n_alternatives: corridors with same county+kv_class minus self
corr["n_alternatives"] = (corr["n_same_kv"] - 1).clip(lower=0)

# ══════════════════════════════════════════════════════════════════════════════
# JOIN COUNTY LOOKUPS TO CORRIDORS
# ══════════════════════════════════════════════════════════════════════════════
print("\nJoining county lookups...", flush=True)

corr = (
    corr
    .merge(county_svi[["county","mean_svi","mean_age65","total_pop"]], on="county", how="left")
    .merge(county_cf[["county","cf_per_100k","n_hospitals","n_cf_total"]],  on="county", how="left")
    .merge(county_sgip[["county","sgip_kw","sgip_ratio"]],  on="county", how="left")
    .merge(county_gdp[["county","GDP_millions"]], on="county", how="left")
)

# Fill missing values with neutral defaults
corr["mean_svi"]     = corr["mean_svi"].fillna(corr["mean_svi"].median())
corr["mean_age65"]   = corr["mean_age65"].fillna(corr["mean_age65"].median())
corr["cf_per_100k"]  = corr["cf_per_100k"].fillna(0)
corr["sgip_ratio"]   = corr["sgip_ratio"].fillna(0)
corr["GDP_millions"] = corr["GDP_millions"].fillna(corr["GDP_millions"].median())
corr["total_pop"]    = corr["total_pop"].fillna(MEAN_POP_PER_CBG * 100)

# ══════════════════════════════════════════════════════════════════════════════
# COMPUTE AVOIDED COST PROXY (for financial dimension)
# ══════════════════════════════════════════════════════════════════════════════
# Proxy: risk × exposure × population × VOLL × outage hours
# Scaled so that corridor-level values are comparable
KV_OUTAGE_HRS = {
    "<115kV":    27.0,
    "115-229kV": 33.7,
    "230-499kV": 38.8,
    "500kV+":    47.2,
}
corr["outage_hrs"]   = corr["kv_class"].map(KV_OUTAGE_HRS).fillna(33.7)
corr["avoided_cost_proxy"] = (
    corr["mean_composite"]
    * corr["total_km"]
    * corr["pop_est"]
    * corr["outage_hrs"]
    * (KWH_PER_HH / PERSONS_PER_HH)
    * KWH_VOLL
    / 1e6   # scale to $M
)

# Net cost per MW proxy (using 75% average incentive offset)
corr["capacity_kw_proxy"] = (
    corr["pop_est"] / PERSONS_PER_HH * KWH_PER_HH * SGIP_ISLANDING
).clip(lower=1)
corr["gross_cost_proxy"] = corr["capacity_kw_proxy"] * 1800 / 1e6   # $M
corr["net_cost_proxy"]   = corr["gross_cost_proxy"] * 0.25           # 75% offset
corr["net_cost_per_mw"]  = (
    corr["net_cost_proxy"] / (corr["capacity_kw_proxy"] / 1000).clip(lower=0.001)
)  # $M per MW

# Payback proxy
corr["annual_avoided_proxy"] = corr["avoided_cost_proxy"] / 5        # 1 event per 5yr
corr["payback_proxy"] = (
    corr["net_cost_proxy"] / corr["annual_avoided_proxy"].clip(lower=0.001)
).clip(upper=100)

# ══════════════════════════════════════════════════════════════════════════════
# DIMENSION SCORES
# ══════════════════════════════════════════════════════════════════════════════
print("Computing dimension scores...", flush=True)

# ── Dimension 1: Life Safety (0-10, higher = more population at risk) ─────
d1_components = {
    "cf_density": (corr["cf_per_100k"],  0.40, True),   # more CF at risk = higher priority
    "svi_score":  (corr["mean_svi"],     0.35, True),   # higher vulnerability = higher priority
    "elderly_pct":(corr["mean_age65"],   0.25, True),   # more elderly = higher priority
}
corr["d1_life_safety"] = score_dim(d1_components) * 10

# ── Dimension 2: Resilience Gap (0-10, higher = more gaps) ───────────────
d2_components = {
    "sgip_gap":    (corr["sgip_ratio"],       0.40, False),  # lower ratio = bigger gap
    "redundancy":  (corr["n_alternatives"],   0.30, False),  # fewer alternatives = more isolated
    "cf_no_backup":(corr["cf_per_100k"],      0.25, True),   # more CF without backup power
    "sub_dist":    (corr["sub_dist_km"],      0.05, True),   # farther from substation = more isolated
}
corr["d2_resilience_gap"] = score_dim(d2_components) * 10

# ── Dimension 3: Financial Viability (0-10, higher = stronger financial case) ─
d3_components = {
    "avoided_cost": (corr["avoided_cost_proxy"], 0.40, True),   # more value = better case
    "net_cost_mw":  (corr["net_cost_per_mw"],    0.30, False),  # lower cost per MW = better
    "gdp_exposure": (corr["GDP_millions"],        0.20, True),   # higher GDP = bigger stake
    "payback":      (corr["payback_proxy"],       0.10, False),  # shorter payback = better
}
corr["d3_financial"] = score_dim(d3_components) * 10

# ── Dimension 4: Ecological Risk (0-10, higher = more sensitive) ─────────
d4_components = {
    "habitat":   (corr["habitat_species"], 0.40, True),  # more species = higher eco risk
    "watershed": (corr["watershed_count"], 0.35, True),  # more watersheds = higher risk
    "fire_hist": (corr["mean_fire_perims"],0.25, True),  # more historical fires = higher risk
}
corr["d4_ecological"] = score_dim(d4_components) * 10

# ── Combined priority score ────────────────────────────────────────────────
corr["priority_score"] = (
    0.35 * corr["d1_life_safety"]    +
    0.25 * corr["d2_resilience_gap"] +
    0.25 * corr["d3_financial"]      +
    0.15 * corr["d4_ecological"]
)

# ── Action tiers ───────────────────────────────────────────────────────────
def assign_tier(score):
    if score >= 7: return "Act Now"
    if score >= 5: return "Plan Now"
    if score >= 3: return "Monitor"
    return "Defer"

corr["action_tier"] = corr["priority_score"].apply(assign_tier)

TIER_ORDER = ["Act Now", "Plan Now", "Monitor", "Defer"]
corr["tier_rank"] = corr["action_tier"].map({t: i for i, t in enumerate(TIER_ORDER)})

corr_sorted = corr.sort_values(["tier_rank", "priority_score"], ascending=[True, False]).reset_index(drop=True)

# ══════════════════════════════════════════════════════════════════════════════
# OUTPUT
# ══════════════════════════════════════════════════════════════════════════════
W = 110

# ── TOP 20 CORRIDORS ──────────────────────────────────────────────────────────
print(f"\n{'='*W}")
print("TOP 20 PRIORITY CORRIDORS")
print(f"{'='*W}")

display_cols = [
    "county", "Owner", "kv_class",
    "n_segs", "total_km",
    "d1_life_safety", "d2_resilience_gap", "d3_financial", "d4_ecological",
    "priority_score", "action_tier",
]

top20 = corr_sorted.head(20)[display_cols].copy()
for col in ["d1_life_safety","d2_resilience_gap","d3_financial","d4_ecological","priority_score"]:
    top20[col] = top20[col].round(2)
top20["total_km"] = top20["total_km"].round(0).astype(int)

pd.set_option("display.max_colwidth", 18)
pd.set_option("display.width", W)
pd.set_option("display.max_rows", 25)
print(top20.rename(columns={
    "d1_life_safety":"D1-LS","d2_resilience_gap":"D2-RG",
    "d3_financial":"D3-FV","d4_ecological":"D4-ER","priority_score":"Priority",
}).to_string(index=False))

# ── TIER DISTRIBUTION ─────────────────────────────────────────────────────────
print(f"\n{'='*W}")
print("ACTION TIER DISTRIBUTION")
print(f"{'='*W}")

tier_stats = (
    corr_sorted.groupby("action_tier", observed=False)
    .agg(
        corridors    = ("priority_score","count"),
        mean_score   = ("priority_score","mean"),
        total_km     = ("total_km","sum"),
        mean_d1      = ("d1_life_safety","mean"),
        mean_d2      = ("d2_resilience_gap","mean"),
        mean_d3      = ("d3_financial","mean"),
        mean_d4      = ("d4_ecological","mean"),
    )
    .reindex([t for t in TIER_ORDER if t in corr_sorted["action_tier"].unique()])
)
tier_stats["total_km"] = tier_stats["total_km"].round(0).astype(int)
tier_stats["mean_score"] = tier_stats["mean_score"].round(2)
for c in ["mean_d1","mean_d2","mean_d3","mean_d4"]:
    tier_stats[c] = tier_stats[c].round(2)
print(tier_stats.to_string())

# ── COUNTIES WITH MOST "ACT NOW" CORRIDORS ────────────────────────────────────
print(f"\n{'='*W}")
print("COUNTIES WITH MOST 'ACT NOW' CORRIDORS")
print(f"{'='*W}")

act_now = corr_sorted[corr_sorted["action_tier"] == "Act Now"]
if len(act_now) > 0:
    top_counties = (
        act_now.groupby("county")
        .agg(
            act_now_corridors = ("priority_score","count"),
            mean_priority     = ("priority_score","mean"),
            total_km          = ("total_km","sum"),
            owners            = ("Owner", lambda x: "/".join(sorted(x.unique()))),
            kv_classes        = ("kv_class", lambda x: "/".join(sorted(x.unique()))),
        )
        .sort_values("act_now_corridors", ascending=False)
        .head(15)
    )
    top_counties["mean_priority"] = top_counties["mean_priority"].round(2)
    top_counties["total_km"] = top_counties["total_km"].round(0).astype(int)
    print(top_counties.to_string())
else:
    print("  No 'Act Now' corridors — showing top 15 'Plan Now' corridors by county")
    plan_now = corr_sorted[corr_sorted["action_tier"] == "Plan Now"]
    top_counties = (
        plan_now.groupby("county")
        .agg(
            corridors     = ("priority_score","count"),
            mean_priority = ("priority_score","mean"),
            total_km      = ("total_km","sum"),
        )
        .sort_values("corridors", ascending=False)
        .head(15)
    )
    print(top_counties.to_string())

# ── UTILITY CROSS-TABULATION ──────────────────────────────────────────────────
print(f"\n{'='*W}")
print("ACTION TIER CROSS-TAB BY UTILITY (top utilities by transmission share)")
print(f"{'='*W}")

# Focus on top utilities by total corridor km
top_owners = (
    corr_sorted.groupby("Owner")["total_km"]
    .sum()
    .sort_values(ascending=False)
    .head(10)
    .index
)

xtab = (
    corr_sorted[corr_sorted["Owner"].isin(top_owners)]
    .groupby(["Owner","action_tier"], observed=True)
    .size()
    .unstack(fill_value=0)
    .reindex(columns=TIER_ORDER, fill_value=0)
)
xtab["Total"] = xtab.sum(axis=1)
xtab["Act Now %"] = (xtab["Act Now"] / xtab["Total"] * 100).round(0).astype(int).astype(str) + "%"

# Add total km and mean priority score
owner_km = corr_sorted.groupby("Owner")["total_km"].sum().round(0).astype(int)
owner_ps = corr_sorted.groupby("Owner")["priority_score"].mean().round(2)
xtab["Total km"] = owner_km
xtab["Mean priority"] = owner_ps
xtab = xtab.sort_values("Total", ascending=False)
print(xtab.to_string())

# ── SCORE DISTRIBUTION SUMMARY ────────────────────────────────────────────────
print(f"\n{'='*W}")
print("SCORE DISTRIBUTION BY DIMENSION")
print(f"{'='*W}")

dist = corr_sorted[["d1_life_safety","d2_resilience_gap","d3_financial","d4_ecological","priority_score"]].describe().round(2)
dist.index = ["count","mean","std","min","p25","p50","p75","max"]
print(dist.to_string())

# ── SAVE ──────────────────────────────────────────────────────────────────────
save_cols = [
    "county","Owner","kv_class",
    "n_segs","total_km",
    "mean_composite","max_composite",
    "pop_est",
    # dimension inputs
    "cf_per_100k","mean_svi","mean_age65",
    "sgip_ratio","n_alternatives","sub_dist_km",
    "avoided_cost_proxy","net_cost_per_mw","GDP_millions","payback_proxy",
    "habitat_species","watershed_count","mean_fire_perims",
    # dimension scores
    "d1_life_safety","d2_resilience_gap","d3_financial","d4_ecological",
    "priority_score","action_tier",
]
out_df = corr_sorted[save_cols].copy()
for c in ["d1_life_safety","d2_resilience_gap","d3_financial","d4_ecological","priority_score",
          "mean_composite","max_composite","cf_per_100k","mean_svi","mean_age65",
          "sgip_ratio","sub_dist_km","avoided_cost_proxy","net_cost_per_mw","payback_proxy"]:
    out_df[c] = out_df[c].round(3)

out_df.to_csv(str(OUT_F), index=False)
print(f"\nSaved: {OUT_F}  ({OUT_F.stat().st_size/1e3:.1f} KB, {len(out_df):,} corridors)")
print(f"Total run time: {time.time()-t0:.0f}s")
