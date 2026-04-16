"""
02_risk_scoring.py
Composite wildfire risk scoring for CA transmission line segments.

Scores:
  Hazard (40%)      — burn probability (raster sample) + FHSZ severity class
  Exposure (30%)    — voltage (kV) + census block group count within 10 km
  Vulnerability (30%) — historical fire perimeter count within 5 km
                       + no prescribed-burn coverage flag within 5 km

Output: data/processed/transmission_risk_scores.gpkg
"""

import warnings
warnings.filterwarnings("ignore")

import re, time
import numpy as np
import pandas as pd
import geopandas as gpd
import rasterio
from pathlib import Path

# ── Paths ──────────────────────────────────────────────────────────────────────
ROOT    = Path(__file__).resolve().parent.parent
LINES_F = ROOT / "data/raw/transmission_lines/transmission_lines.gpkg"
BP_TIF  = ROOT / "data/raw/wildfire_risk/carbonplan_burn_probability.tif"
FHSZ_F  = ROOT / "data/raw/wildfire_risk/fhsz.gpkg"
PERIMS_F= ROOT / "data/raw/wildfire_risk/fire_perimeters.gpkg"
BURNS_F = ROOT / "data/raw/wildfire_risk/prescribed_burns.gpkg"
CBG_F   = ROOT / "data/raw/census/census_block_groups.gpkg"
OUT_F   = ROOT / "data/processed/transmission_risk_scores.gpkg"
OUT_F.parent.mkdir(parents=True, exist_ok=True)

PROJ_EPSG  = 3310   # CA Albers — metres
INTERVAL_M = 100    # raster sample spacing along each line

# California county FIPS → name
COUNTY_FIPS = {
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

# ── Helpers ────────────────────────────────────────────────────────────────────
def normalize(s: pd.Series) -> pd.Series:
    mn, mx = s.min(), s.max()
    if mx == mn:
        return pd.Series(0.0, index=s.index)
    return (s - mn) / (mx - mn)

def sample_points(geom, interval_m: float):
    """Return list of (x, y) in the geometry's CRS at interval_m spacing."""
    length = geom.length
    if length == 0:
        c = geom.centroid
        return [(c.x, c.y)]
    n = max(1, int(length / interval_m))
    pts = []
    for i in range(n + 1):
        p = geom.interpolate(i / n, normalized=True)
        pts.append((p.x, p.y))
    return pts

# ── Load ───────────────────────────────────────────────────────────────────────
t0 = time.time()
print("Loading datasets...", flush=True)

lines      = gpd.read_file(LINES_F)
lines_proj = lines.to_crs(epsg=PROJ_EPSG).reset_index(drop=True)
n          = len(lines_proj)
print(f"  Transmission lines : {n:,} segments")

# ══════════════════════════════════════════════════════════════════════════════
# STEP 1 — HAZARD SCORE (40%)
# ══════════════════════════════════════════════════════════════════════════════
print(f"\nStep 1: Hazard score...", flush=True)
t1 = time.time()

# 1a — Sample burn probability at 100-m intervals
print("  Generating sample points along lines...", flush=True)
# Work in projected CRS, then convert coords to WGS84 for raster lookup
from shapely.geometry import Point

idxs, xs_proj, ys_proj = [], [], []
for i, row in lines_proj.iterrows():
    for x, y in sample_points(row.geometry, INTERVAL_M):
        idxs.append(i)
        xs_proj.append(x)
        ys_proj.append(y)

# Batch-convert projected → WGS84
pts_series = gpd.GeoSeries(
    [Point(x, y) for x, y in zip(xs_proj, ys_proj)], crs=PROJ_EPSG
).to_crs(epsg=4326)
coords_wgs84 = [(p.x, p.y) for p in pts_series]
print(f"  Sample points: {len(coords_wgs84):,}", flush=True)

print("  Sampling raster...", flush=True)
with rasterio.open(BP_TIF) as src:
    nodata = src.nodata
    raw = list(src.sample(coords_wgs84, masked=False))

bp_vals = np.array([r[0] for r in raw], dtype=np.float32)
if nodata is not None:
    bp_vals[bp_vals == nodata] = np.nan
bp_vals[bp_vals < 0] = np.nan

df_pts = pd.DataFrame({"i": idxs, "bp": bp_vals})
bp_agg = (df_pts.groupby("i")["bp"]
          .agg(bp_mean  = lambda x: np.nanmean(x)  if x.notna().any() else 0.0,
               bp_max   = lambda x: np.nanmax(x)   if x.notna().any() else 0.0,
               bp_p90   = lambda x: np.nanpercentile(x.dropna(), 90)
                          if x.notna().sum() >= 5 else np.nanmax(x) if x.notna().any() else 0.0)
          .reindex(range(n), fill_value=0.0))
print(f"  BP mean range: [{bp_agg.bp_mean.min():.4f}, {bp_agg.bp_mean.max():.4f}]", flush=True)

# 1b — FHSZ: highest severity class intersecting each line
print("  Joining FHSZ...", flush=True)
fhsz = gpd.read_file(FHSZ_F).to_crs(epsg=PROJ_EPSG)[["geometry", "FHSZ"]]
fhsz["FHSZ"] = pd.to_numeric(fhsz["FHSZ"], errors="coerce").fillna(0)

fhsz_join = gpd.sjoin(
    lines_proj[["geometry"]].assign(line_i=range(n)),
    fhsz, how="left", predicate="intersects"
)
fhsz_max = fhsz_join.groupby("line_i")["FHSZ"].max().fillna(0).reindex(range(n), fill_value=0)

# Combine: 50% BP-mean, 25% BP-p90, 25% FHSZ
hazard = (
    0.50 * normalize(bp_agg.bp_mean) +
    0.25 * normalize(bp_agg.bp_p90) +
    0.25 * normalize(fhsz_max)
).clip(0, 1)
print(f"  Hazard done  [{hazard.min():.3f}, {hazard.max():.3f}]  "
      f"({time.time()-t1:.0f}s)", flush=True)

# ══════════════════════════════════════════════════════════════════════════════
# STEP 2 — EXPOSURE SCORE (30%)
# ══════════════════════════════════════════════════════════════════════════════
print("\nStep 2: Exposure score...", flush=True)
t2 = time.time()

# 2a — Voltage: kV_Sort is already numeric
kv = pd.to_numeric(lines_proj["kV_Sort"], errors="coerce").fillna(0)
print(f"  kV range: {kv.min():.0f} – {kv.max():.0f}", flush=True)

# 2b — Population proxy: count census block groups within 10 km
print("  Counting census block groups in 10 km buffer...", flush=True)
cbg = gpd.read_file(CBG_F).to_crs(epsg=PROJ_EPSG)[["geometry", "COUNTYFP"]]

buf10 = lines_proj[["geometry"]].assign(line_i=range(n)).copy()
buf10["geometry"] = lines_proj.geometry.buffer(10_000)

cbg_join = gpd.sjoin(buf10, cbg[["geometry"]], how="left", predicate="intersects")
cbg_cnt  = cbg_join.groupby("line_i").size().reindex(range(n), fill_value=0)

exposure = (
    0.60 * normalize(kv) +
    0.40 * normalize(cbg_cnt)
).clip(0, 1)
print(f"  Exposure done [{exposure.min():.3f}, {exposure.max():.3f}]  "
      f"({time.time()-t2:.0f}s)", flush=True)

# ══════════════════════════════════════════════════════════════════════════════
# STEP 3 — VULNERABILITY SCORE (30%)
# ══════════════════════════════════════════════════════════════════════════════
print("\nStep 3: Vulnerability score...", flush=True)
t3 = time.time()

buf5 = lines_proj[["geometry"]].assign(line_i=range(n)).copy()
buf5["geometry"] = lines_proj.geometry.buffer(5_000)

# 3a — Historical fire perimeters within 5 km
print("  Counting fire perimeters in 5 km buffer...", flush=True)
perims = gpd.read_file(PERIMS_F).to_crs(epsg=PROJ_EPSG)[["geometry"]]
perim_join = gpd.sjoin(buf5, perims, how="left", predicate="intersects")
perim_cnt  = perim_join.groupby("line_i").size().reindex(range(n), fill_value=0)

# 3b — Prescribed burn coverage (1 = no burns nearby = more vulnerable)
print("  Checking prescribed burn coverage in 5 km buffer...", flush=True)
burns = gpd.read_file(BURNS_F).to_crs(epsg=PROJ_EPSG)[["geometry"]]
burns_join = gpd.sjoin(buf5, burns, how="left", predicate="intersects")
has_burns  = burns_join.groupby("line_i").size() > 0
has_burns  = has_burns.reindex(range(n), fill_value=False)
no_burn    = (~has_burns).astype(float)   # 1 = unprotected

vulnerability = (
    0.60 * normalize(perim_cnt) +
    0.40 * no_burn
).clip(0, 1)
print(f"  Vulnerability done [{vulnerability.min():.3f}, {vulnerability.max():.3f}]  "
      f"({time.time()-t3:.0f}s)", flush=True)

# ══════════════════════════════════════════════════════════════════════════════
# STEP 4 — COMPOSITE + TIERS
# ══════════════════════════════════════════════════════════════════════════════
print("\nStep 4: Composite scores and risk tiers...", flush=True)

composite = (0.4 * hazard + 0.3 * exposure + 0.3 * vulnerability).clip(0, 1)

q90 = composite.quantile(0.90)
q75 = composite.quantile(0.75)
q50 = composite.quantile(0.50)

def tier(score):
    if score >= q90: return "Critical"
    if score >= q75: return "High"
    if score >= q50: return "Moderate"
    return "Low"

risk_tier = composite.apply(tier)

# ── County assignment ──────────────────────────────────────────────────────────
centroids = gpd.GeoDataFrame(
    {"line_i": range(n),
     "geometry": lines_proj.geometry.centroid},
    crs=PROJ_EPSG
)
county_join = gpd.sjoin(centroids, cbg[["geometry","COUNTYFP"]], how="left", predicate="within")
county_fips = county_join.groupby("line_i")["COUNTYFP"].first().reindex(range(n))
county_name = county_fips.map(COUNTY_FIPS).fillna("Unknown")

# ── Assemble output ────────────────────────────────────────────────────────────
out = lines.copy()
out["county"]              = county_name.values
out["kv_num"]              = kv.values
out["bp_mean"]             = bp_agg.bp_mean.values.round(6)
out["bp_max"]              = bp_agg.bp_max.values.round(6)
out["bp_p90"]              = bp_agg.bp_p90.values.round(6)
out["fhsz_max"]            = fhsz_max.values.astype(int)
out["cbg_count_10km"]      = cbg_cnt.values.astype(int)
out["fire_perims_5km"]     = perim_cnt.values.astype(int)
out["has_prescribed_burn"] = has_burns.values
out["hazard_score"]        = hazard.values.round(4)
out["exposure_score"]      = exposure.values.round(4)
out["vulnerability_score"] = vulnerability.values.round(4)
out["composite_score"]     = composite.values.round(4)
out["risk_tier"]           = risk_tier.values

out.to_file(str(OUT_F), driver="GPKG")
print(f"\nSaved: {OUT_F}  ({OUT_F.stat().st_size/1e6:.1f} MB, {len(out):,} rows)  "
      f"[total: {time.time()-t0:.0f}s]", flush=True)

# ══════════════════════════════════════════════════════════════════════════════
# REPORTING
# ══════════════════════════════════════════════════════════════════════════════
W = 100
print("\n" + "="*W)
print("TOP 20 HIGHEST-SCORING TRANSMISSION LINE SEGMENTS")
print("="*W)
top20 = out.sort_values("composite_score", ascending=False).head(20)
cols  = ["Name","county","kv_num","bp_mean","fhsz_max",
         "hazard_score","exposure_score","vulnerability_score",
         "composite_score","risk_tier"]
print(top20[cols].to_string(index=False))

print("\n" + "="*W)
print("SUMMARY BY RISK TIER")
print("="*W)
tiers = ["Critical","High","Moderate","Low"]
summary = (out.groupby("risk_tier", observed=True)
             .agg(segments      = ("composite_score","count"),
                  mean_composite = ("composite_score","mean"),
                  mean_hazard   = ("hazard_score","mean"),
                  mean_exposure = ("exposure_score","mean"),
                  mean_vuln     = ("vulnerability_score","mean"),
                  mean_kv       = ("kv_num","mean"),
                  mean_bp_mean  = ("bp_mean","mean"))
             .reindex(tiers))
summary.insert(1, "pct_of_total", (summary.segments / len(out) * 100).round(1))
pd.set_option("display.float_format", "{:.3f}".format)
pd.set_option("display.max_columns", 20)
pd.set_option("display.width", W)
print(summary.to_string())

print("\n" + "="*W)
print("SCORE THRESHOLDS")
print("="*W)
print(f"  Critical  ≥ {q90:.4f}  (top 10%)")
print(f"  High      ≥ {q75:.4f}  (top 25%)")
print(f"  Moderate  ≥ {q50:.4f}  (top 50%)")
print(f"  Low        < {q50:.4f}  (bottom 50%)")
