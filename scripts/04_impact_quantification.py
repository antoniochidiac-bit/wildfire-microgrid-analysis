"""
04_impact_quantification.py
Quantify human and economic impact for three wildfire resilience scenarios.

Steps:
  1  Population affected (Census ACS block-group population + spatial join)
  2  Structures affected (tile-based building count + critical facilities)
  3  Outage duration (PSPS historical median by voltage class)
  4  Person-hours of outage + economic cost ($9/kWh VOLL)
  5  Print and save impact_summary.csv
"""

import warnings
warnings.filterwarnings("ignore")

import gzip, json, math, time, urllib.request
from pathlib import Path
import numpy as np
import pandas as pd
import geopandas as gpd
from shapely.geometry import Point, box
from shapely.ops import unary_union
from shapely.prepared import prep

# ── Paths ──────────────────────────────────────────────────────────────────────
ROOT       = Path(__file__).resolve().parent.parent
SCN_A_F    = ROOT / "data/processed/scenario_a_fortress.gpkg"
SCN_B_F    = ROOT / "data/processed/scenario_b_islands.gpkg"
SCN_C_F    = ROOT / "data/processed/scenario_c_crisis.gpkg"
CBG_F      = ROOT / "data/raw/census/census_block_groups.gpkg"
TILES_DIR  = ROOT / "data/raw/buildings/tiles"
CF_F       = ROOT / "data/raw/microgrids/critical_facilities.gpkg"
PSPS_F     = ROOT / "data/raw/psps_history/cpuc_psps_event_rollup.xlsx"
OUT_CSV    = ROOT / "data/processed/impact_summary.csv"

PROJ_EPSG  = 3310          # CA Albers (metres)
KWH_VOLL   = 9.0           # CPUC residential value of lost load $/kWh
KWH_PER_HH = 1.5           # average kWh/hour per household
PERSONS_PER_HH = 2.5

# Voltage class definitions for outage multipliers
# Base: PSPS historical median; higher kV = more complex restoration
KV_MULTIPLIERS = {
    "500kV"   : 1.40,
    "230-345kV": 1.15,
    "115-230kV": 1.00,
    "<115kV"  : 0.80,
}


# ══════════════════════════════════════════════════════════════════════════════
# HELPERS
# ══════════════════════════════════════════════════════════════════════════════

def qk_to_bbox(qk: str):
    """Return (minx, miny, maxx, maxy) WGS84 for a Bing Maps quadkey."""
    zoom = len(qk)
    tx, ty = 0, 0
    for ch in qk:
        tx <<= 1; ty <<= 1
        if ch == "1":   tx |= 1
        elif ch == "2": ty |= 1
        elif ch == "3": tx |= 1; ty |= 1
    n = 2 ** zoom
    w = tx / n * 360 - 180
    e = (tx + 1) / n * 360 - 180
    north = math.degrees(math.atan(math.sinh(math.pi * (1 - 2 * ty / n))))
    south = math.degrees(math.atan(math.sinh(math.pi * (1 - 2 * (ty+1) / n))))
    return (w, south, e, north)


def count_buildings_in_zone(zone_geom_wgs84, tiles_dir: Path):
    """
    Count buildings whose polygon centroid lies within zone_geom_wgs84.
    Uses tile bbox pre-filter then shapely prepared-geometry containment.
    Returns (count, n_tiles_processed).
    """
    prepared = prep(zone_geom_wgs84)
    zb = zone_geom_wgs84.bounds        # minx, miny, maxx, maxy

    count, n_tiles = 0, 0
    for tile_path in sorted(tiles_dir.glob("*.csv.gz")):
        tb = qk_to_bbox(tile_path.stem)
        # Fast bbox rejection
        if tb[2] < zb[0] or tb[0] > zb[2] or tb[3] < zb[1] or tb[1] > zb[3]:
            continue
        # Precise tile–zone overlap
        if not box(tb[0], tb[1], tb[2], tb[3]).intersects(zone_geom_wgs84):
            continue
        n_tiles += 1
        with gzip.open(tile_path, "rt") as f:
            for line in f:
                line = line.strip()
                if not line:
                    continue
                try:
                    feat   = json.loads(line)
                    coords = feat["geometry"]["coordinates"][0]
                    cx     = sum(p[0] for p in coords) / len(coords)
                    cy     = sum(p[1] for p in coords) / len(coords)
                    if prepared.contains(Point(cx, cy)):
                        count += 1
                except Exception:
                    pass
    return count, n_tiles


def kv_class(kv_num):
    if kv_num >= 500:   return "500kV"
    if kv_num >= 230:   return "230-345kV"
    if kv_num >= 115:   return "115-230kV"
    return "<115kV"


def weighted_median_outage(gdf, base_median_hrs):
    """
    Weighted mean outage hours for a set of segments, weighting each segment
    by its line_length_km and kV-class multiplier.
    """
    gdf = gdf.copy()
    gdf["kv_class"]    = gdf["kv_num"].apply(kv_class)
    gdf["multiplier"]  = gdf["kv_class"].map(KV_MULTIPLIERS)
    gdf["outage_hrs"]  = base_median_hrs * gdf["multiplier"]
    # Length-weighted mean
    total_len = gdf["line_length_km"].sum()
    if total_len == 0:
        return base_median_hrs
    w_mean = (gdf["outage_hrs"] * gdf["line_length_km"]).sum() / total_len
    return w_mean


# ══════════════════════════════════════════════════════════════════════════════
# STEP 0 — Load shared reference layers
# ══════════════════════════════════════════════════════════════════════════════
t0 = time.time()
print("Loading reference layers...", flush=True)

# Census block groups (no population yet — fetched below)
cbg = gpd.read_file(CBG_F)
cbg_proj = cbg.to_crs(epsg=PROJ_EPSG)
print(f"  CBG: {len(cbg):,} block groups")

# Critical facilities
cf = gpd.read_file(CF_F)
cf_proj = cf.to_crs(epsg=PROJ_EPSG)
print(f"  Critical facilities: {len(cf):,}")

# ── Fetch population from Census ACS 2022 5-year estimates ──────────────────
print("\nFetching Census ACS population for CA block groups...", flush=True)

CA_FIPS = [f"{i:03d}" for i in range(1, 116, 2)]  # 001, 003, ..., 115

pop_rows = []
api_ok = True
try:
    for county_fips in CA_FIPS:
        url = (
            "https://api.census.gov/data/2022/acs/acs5"
            "?get=B01003_001E"
            f"&for=block%20group:*"
            f"&in=state:06%20county:{county_fips}"
        )
        req = urllib.request.Request(
            url, headers={"User-Agent": "wildfire-microgrid-analysis/1.0"}
        )
        with urllib.request.urlopen(req, timeout=20) as r:
            rows = json.loads(r.read())
        # rows[0] = header, rows[1:] = data
        for row in rows[1:]:
            pop, state, county, tract, bg = row
            geoid = f"{state}{county}{tract}{bg}"   # 12-char GEOID
            pop_rows.append({"GEOID": geoid, "population": int(pop) if pop else 0})

    pop_df = pd.DataFrame(pop_rows)
    cbg = cbg.merge(pop_df, on="GEOID", how="left")
    cbg["population"] = cbg["population"].fillna(0).astype(int)
    total_pop = cbg["population"].sum()
    print(f"  Fetched population for {len(pop_df):,} block groups  "
          f"(CA total: {total_pop/1e6:.2f}M people)")

except Exception as e:
    print(f"  Census API failed ({e}) — using area-based estimate", flush=True)
    api_ok = False
    # Fallback: CA ~39.5M / 25607 CBG = ~1,543 per CBG (very rough)
    MEAN_POP_PER_CBG = 1543
    cbg["population"] = MEAN_POP_PER_CBG
    print(f"  Fallback: {MEAN_POP_PER_CBG} people per block group")

cbg_proj = cbg.to_crs(epsg=PROJ_EPSG)

# ══════════════════════════════════════════════════════════════════════════════
# STEP 1 — PSPS historical outage hours by voltage class
# ══════════════════════════════════════════════════════════════════════════════
print("\nParsing PSPS historical outage durations...", flush=True)

psps = pd.read_excel(PSPS_F, header=2)
hours = pd.to_numeric(psps["Outage Hours"], errors="coerce")
hours = hours[(hours > 0) & (hours < 500)]   # drop bad/erroneous values
base_median_hrs = hours.median()

print(f"  Valid PSPS events: {len(hours):,}  "
      f"Median: {base_median_hrs:.1f}h  "
      f"P25: {hours.quantile(0.25):.1f}h  "
      f"P75: {hours.quantile(0.75):.1f}h")

kv_hours = {kc: base_median_hrs * mult for kc, mult in KV_MULTIPLIERS.items()}
print("  Voltage-class outage hours:")
for kc, h in kv_hours.items():
    print(f"    {kc:<12}: {h:.1f}h  ({KV_MULTIPLIERS[kc]:.2f}× base)")


# ══════════════════════════════════════════════════════════════════════════════
# SCENARIO PROCESSING FUNCTION
# ══════════════════════════════════════════════════════════════════════════════

def process_scenario(label, segments_gdf, buffer_m, description):
    """
    Run all impact steps for one scenario.
    segments_gdf: GeoDataFrame of affected line segments (EPSG:4326, LineString geometry)
    buffer_m:     buffer radius in metres for outage zone
    """
    print(f"\n{'='*70}", flush=True)
    print(f"Processing Scenario {label}: {description}", flush=True)
    print(f"  {len(segments_gdf):,} segments, {buffer_m/1000:.0f} km buffer", flush=True)
    t = time.time()

    # ── Build outage zone ──────────────────────────────────────────────────
    print("  Building outage zone...", flush=True)
    seg_proj  = segments_gdf.to_crs(epsg=PROJ_EPSG)
    buf_geoms = seg_proj.geometry.buffer(buffer_m)
    zone_proj = unary_union(buf_geoms)
    zone_wgs84 = gpd.GeoSeries([zone_proj], crs=PROJ_EPSG).to_crs(4326).iloc[0]
    zone_area_km2 = zone_proj.area / 1e6
    print(f"  Zone area: {zone_area_km2:,.0f} km²", flush=True)

    # ── Step 1: Population ─────────────────────────────────────────────────
    print("  Counting affected population...", flush=True)
    zone_gdf = gpd.GeoDataFrame(geometry=[zone_proj], crs=PROJ_EPSG)

    cbg_in_zone = gpd.sjoin(
        cbg_proj[["geometry", "GEOID", "population"]],
        zone_gdf,
        how="inner",
        predicate="intersects"
    )
    cbg_in_zone = cbg_in_zone[~cbg_in_zone.index.duplicated()]
    n_cbg   = len(cbg_in_zone)
    pop_tot = int(cbg_in_zone["population"].sum())
    print(f"  CBGs in zone: {n_cbg:,}   Population: {pop_tot:,.0f}", flush=True)

    # ── Step 2: Buildings ─────────────────────────────────────────────────
    print("  Counting buildings (tile scan)...", flush=True)
    n_buildings, n_tiles = count_buildings_in_zone(zone_wgs84, TILES_DIR)
    print(f"  Buildings: {n_buildings:,}  (from {n_tiles} tiles)", flush=True)

    # Critical facilities in zone
    cf_in_zone = gpd.sjoin(
        cf_proj[["geometry", "NAME", "facility_type"]],
        zone_gdf,
        how="inner",
        predicate="within"
    )
    cf_in_zone = cf_in_zone[~cf_in_zone.index.duplicated()]
    n_cf        = len(cf_in_zone)
    n_hospitals = (cf_in_zone["facility_type"] == "Hospital").sum()
    n_fire      = (cf_in_zone["facility_type"] == "Fire Station").sum()
    print(f"  Critical facilities: {n_cf:,}  "
          f"(hospitals: {n_hospitals}, fire stations: {n_fire})", flush=True)

    # ── Step 3: Weighted outage duration ──────────────────────────────────
    median_outage_hrs = weighted_median_outage(segments_gdf, base_median_hrs)
    print(f"  Weighted outage duration: {median_outage_hrs:.1f}h", flush=True)

    # ── Step 4: Person-hours and cost ─────────────────────────────────────
    person_hours = int(pop_tot * median_outage_hrs)
    households   = pop_tot / PERSONS_PER_HH
    econ_cost    = households * KWH_PER_HH * median_outage_hrs * KWH_VOLL
    total_km     = segments_gdf["line_length_km"].sum()

    print(f"  Person-hours of outage: {person_hours:,.0f}", flush=True)
    print(f"  Economic cost: ${econ_cost:,.0f}", flush=True)
    print(f"  Elapsed: {time.time()-t:.0f}s", flush=True)

    return {
        "Scenario"                   : label,
        "Description"                : description,
        "Segments affected"          : len(segments_gdf),
        "Total line length (km)"     : round(total_km, 1),
        "Outage buffer (km)"         : buffer_m / 1000,
        "Zone area (km²)"            : round(zone_area_km2, 0),
        "Census block groups"        : n_cbg,
        "Population affected"        : pop_tot,
        "Buildings affected"         : n_buildings,
        "Critical facilities"        : n_cf,
        "  Hospitals"                : int(n_hospitals),
        "  Fire stations"            : int(n_fire),
        "Median outage (hours)"      : round(median_outage_hrs, 1),
        "Person-hours of outage"     : person_hours,
        "Economic cost ($)"          : round(econ_cost, 0),
    }


# ══════════════════════════════════════════════════════════════════════════════
# RUN SCENARIOS
# ══════════════════════════════════════════════════════════════════════════════
results = []

# Scenario A: load line segments, 5 km buffer
scn_a = gpd.read_file(SCN_A_F)
res_a = process_scenario(
    "A — Fortress Grid", scn_a, buffer_m=5_000,
    description="Critical+High, hardened (50%/25% reduction)"
)
results.append(res_a)

# Scenario B: load line segments (geometry is already 10km buffer polygons)
# Re-read the original line geometry for consistent buffering
scn_b = gpd.read_file(SCN_B_F)
# Reconstruct line geometries from stored WKT
from shapely import wkt as shapely_wkt
scn_b_lines = scn_b.copy()
scn_b_lines["geometry"] = scn_b_lines["line_geometry_wkt"].apply(shapely_wkt.loads)
scn_b_lines = gpd.GeoDataFrame(scn_b_lines, geometry="geometry", crs="EPSG:4326")

res_b = process_scenario(
    "B — Islands of Power", scn_b_lines, buffer_m=10_000,
    description="Critical+High, microgrid service areas"
)
results.append(res_b)

# Scenario C: load critical_segments layer, 25 km buffer
scn_c = gpd.read_file(SCN_C_F, layer="critical_segments")
res_c = process_scenario(
    "C — Reactive Crisis", scn_c, buffer_m=25_000,
    description="Critical only, no hardening (crisis conditions)"
)
results.append(res_c)


# ══════════════════════════════════════════════════════════════════════════════
# STEP 5 — FINAL TABLE
# ══════════════════════════════════════════════════════════════════════════════
W = 100
print(f"\n{'='*W}")
print("IMPACT SUMMARY — ALL SCENARIOS")
print(f"{'='*W}")

summary = pd.DataFrame(results).set_index("Scenario")

# Display formatting
def fmt(val):
    if isinstance(val, float) and abs(val) >= 1000:
        return f"{val:,.0f}"
    if isinstance(val, (int, float)):
        return f"{val:,}" if isinstance(val, int) else f"{val:,.1f}"
    return str(val)

for col in summary.columns:
    print(f"\n  {col}")
    for scenario, val in summary[col].items():
        print(f"    {scenario:<28} {fmt(val)}")

# Print in table form
print(f"\n{'='*W}")
print("COMPACT TABLE")
print(f"{'='*W}")
display_cols = [
    "Segments affected", "Total line length (km)", "Population affected",
    "Buildings affected", "Critical facilities", "Median outage (hours)",
    "Person-hours of outage", "Economic cost ($)"
]
compact = summary[display_cols].copy()
compact["Economic cost ($)"] = compact["Economic cost ($)"].apply(
    lambda x: f"${x/1e6:.1f}M"
)
compact["Person-hours of outage"] = compact["Person-hours of outage"].apply(
    lambda x: f"{x/1e6:.2f}M"
)
compact["Population affected"] = compact["Population affected"].apply(
    lambda x: f"{x/1e6:.2f}M"
)
compact["Buildings affected"] = compact["Buildings affected"].apply(
    lambda x: f"{x/1e3:.0f}K"
)
pd.set_option("display.max_colwidth", 20)
pd.set_option("display.width", W)
print(compact.T.to_string())

# Save raw numbers
summary_save = pd.DataFrame(results)
summary_save.to_csv(str(OUT_CSV), index=False)
print(f"\nSaved: {OUT_CSV}  ({OUT_CSV.stat().st_size/1e3:.1f} KB)")
print(f"\nTotal run time: {time.time()-t0:.0f}s")
