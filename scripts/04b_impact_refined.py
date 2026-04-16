"""
04b_impact_refined.py
Refined wildfire impact quantification with 5 topology proxies applied
in sequence to all three scenarios.

Proxy 1 — Voltage-scaled buffers:
    500kV → 20 km  |  230-345kV → 10 km  |  115-229kV → 5 km  |  <115kV → 2 km

Proxy 2 — Service territory clipping:
    Each segment's buffer is clipped to the territory polygon of its operating utility.
    Unmatched owners (out-of-state, no HIFLD territory) keep their full buffer.

Proxy 3 — Substation containment:
    Find substations within the zone; 2 km buffer each.
    Only retain CBGs that intersect a substation buffer.

Proxy 4 — Population density threshold:
    Drop CBGs with density ≤ 10 people/km².

Proxy 5 — Redundancy discount:
    Per segment: check if any other CA transmission line exists within 8 km.
    Redundant segments → ×0.35 population weight.
    Non-redundant segments → ×0.90 population weight.
    Blended factor = length-weighted mean across all scenario segments.

Output: data/processed/impact_summary_refined.csv
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
from shapely import wkt as shapely_wkt

# ── Paths ──────────────────────────────────────────────────────────────────────
ROOT      = Path(__file__).resolve().parent.parent
SCN_A_F   = ROOT / "data/processed/scenario_a_fortress.gpkg"
SCN_B_F   = ROOT / "data/processed/scenario_b_islands.gpkg"
SCN_C_F   = ROOT / "data/processed/scenario_c_crisis.gpkg"
LINES_F   = ROOT / "data/raw/transmission_lines/transmission_lines.gpkg"
CBG_F     = ROOT / "data/raw/census/census_block_groups.gpkg"
SVCTERR_F = ROOT / "data/raw/census/service_territories.gpkg"
SUBS_F    = ROOT / "data/raw/substations/substations.gpkg"
TILES_DIR = ROOT / "data/raw/buildings/tiles"
CF_F      = ROOT / "data/raw/microgrids/critical_facilities.gpkg"
PSPS_F    = ROOT / "data/raw/psps_history/cpuc_psps_event_rollup.xlsx"
ORIG_CSV  = ROOT / "data/processed/impact_summary.csv"
OUT_CSV   = ROOT / "data/processed/impact_summary_refined.csv"

PROJ_EPSG      = 3310    # CA Albers (metres)
KWH_VOLL       = 9.0     # CPUC residential $/kWh
KWH_PER_HH     = 1.5     # kWh/hour per household
PERSONS_PER_HH = 2.5
DENSITY_MIN    = 10.0    # people/km² threshold (Proxy 4)
PARALLEL_M     = 8_000   # redundancy search radius (Proxy 5)
SUB_ADJACENT_M = 2_000   # substation "adjacent" buffer (Proxy 3)

# Voltage-class → buffer metres (Proxy 1)
def kv_buffer_m(kv_num):
    if kv_num >= 500: return 20_000
    if kv_num >= 230: return 10_000
    if kv_num >= 115: return  5_000
    return 2_000

# Voltage-class → outage multiplier
KV_MULTIPLIERS = {"500kV": 1.40, "230-345kV": 1.15, "115-230kV": 1.00, "<115kV": 0.80}

def kv_class(kv_num):
    if kv_num >= 500: return "500kV"
    if kv_num >= 230: return "230-345kV"
    if kv_num >= 115: return "115-230kV"
    return "<115kV"

# ── Utility abbreviation → HIFLD service territory NAME ───────────────────────
# Covers the 17 owners (of 36 unique) that have a matching CA territory polygon.
# Big-3 IOU + large munis account for ~90% of CA transmission line length.
OWNER_TERRITORY = {
    "ANZA":        "ANZA ELECTRIC COOP INC",
    "BVES":        "BEAR VALLEY ELECTRIC SERVICE",
    "CALPECO":     "LIBERTY UTILITIES",
    "IID":         "IMPERIAL IRRIGATION DISTRICT",
    "LADWP":       "LOS ANGELES DEPARTMENT OF WATER & POWER",
    "LMUD":        "LASSEN MUNICIPAL UTILITY DISTRICT",
    "MID":         "MODESTO IRRIGATION DISTRICT",
    "PG&E":        "PACIFIC GAS & ELECTRIC CO.",
    "PLSR":        "PLUMAS-SIERRA RURAL ELEC COOP",
    "REU":         "CITY OF REDDING - (CA)",
    "RPU":         "CITY OF RIVERSIDE - (CA)",
    "SCE":         "SOUTHERN CALIFORNIA EDISON CO",
    "SDG&E":       "SAN DIEGO GAS & ELECTRIC CO",
    "SHASTA_LAKE": "CITY OF SHASTA LAKE - (CA)",
    "SMUD":        "SACRAMENTO MUNICIPAL UTIL DIST",
    "SVP":         "CITY OF SANTA CLARA - (CA)",
    "SVEC":        "SURPRISE VALLEY ELECTRIFICATION",
}

# ── Quadkey helpers (unchanged from 04_impact_quantification.py) ──────────────
def qk_to_bbox(qk: str):
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
    prepared = prep(zone_geom_wgs84)
    zb = zone_geom_wgs84.bounds
    count, n_tiles = 0, 0
    for tile_path in sorted(tiles_dir.glob("*.csv.gz")):
        tb = qk_to_bbox(tile_path.stem)
        if tb[2] < zb[0] or tb[0] > zb[2] or tb[3] < zb[1] or tb[1] > zb[3]:
            continue
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


def weighted_outage_hrs(gdf, base_median):
    gdf = gdf.copy()
    gdf["mult"] = gdf["kv_num"].apply(kv_class).map(KV_MULTIPLIERS)
    total_len = gdf["line_length_km"].sum()
    if total_len == 0:
        return base_median
    return (gdf["mult"] * gdf["line_length_km"]).sum() / total_len * base_median


# ══════════════════════════════════════════════════════════════════════════════
# LOAD REFERENCE DATA
# ══════════════════════════════════════════════════════════════════════════════
t0 = time.time()
print("Loading reference layers...", flush=True)

cbg      = gpd.read_file(CBG_F)
cbg_proj = cbg.to_crs(epsg=PROJ_EPSG)

svcterr      = gpd.read_file(SVCTERR_F)
svcterr_proj = svcterr.to_crs(epsg=PROJ_EPSG)

subs      = gpd.read_file(SUBS_F)
subs_proj = subs.to_crs(epsg=PROJ_EPSG)

cf      = gpd.read_file(CF_F)
cf_proj = cf.to_crs(epsg=PROJ_EPSG)

all_lines      = gpd.read_file(LINES_F)
all_lines_proj = all_lines.to_crs(epsg=PROJ_EPSG)

print(f"  CBG: {len(cbg):,}  "
      f"| Territories: {len(svcterr):,}  "
      f"| Substations: {len(subs):,}  "
      f"| All lines: {len(all_lines_proj):,}", flush=True)

# ── Fetch Census ACS population ───────────────────────────────────────────────
print("\nFetching Census ACS population...", flush=True)
CA_FIPS  = [f"{i:03d}" for i in range(1, 116, 2)]
pop_rows = []
try:
    for fips in CA_FIPS:
        url = (
            "https://api.census.gov/data/2022/acs/acs5"
            "?get=B01003_001E"
            f"&for=block%20group:*&in=state:06%20county:{fips}"
        )
        req = urllib.request.Request(
            url, headers={"User-Agent": "wildfire-microgrid-analysis/1.0"}
        )
        with urllib.request.urlopen(req, timeout=20) as r:
            rows = json.loads(r.read())
        for row in rows[1:]:
            pop, state, county, tract, bg = row
            pop_rows.append({
                "GEOID":      f"{state}{county}{tract}{bg}",
                "population": int(pop) if pop else 0,
            })
    pop_df = pd.DataFrame(pop_rows)
    cbg    = cbg.merge(pop_df, on="GEOID", how="left")
    cbg["population"] = cbg["population"].fillna(0).astype(int)
    print(f"  {len(pop_df):,} block groups  "
          f"(CA total: {cbg['population'].sum()/1e6:.2f}M)", flush=True)
except Exception as e:
    print(f"  API failed ({e}) — fallback 1 543/CBG", flush=True)
    cbg["population"] = 1543

cbg_proj = cbg.to_crs(epsg=PROJ_EPSG)
cbg_proj["area_km2"]    = cbg_proj.geometry.area / 1e6
cbg_proj["pop_density"] = (
    cbg_proj["population"] / cbg_proj["area_km2"].clip(lower=0.01)
)

# ── PSPS outage hours ─────────────────────────────────────────────────────────
print("\nParsing PSPS outage durations...", flush=True)
psps = pd.read_excel(PSPS_F, header=2)
hrs  = pd.to_numeric(psps["Outage Hours"], errors="coerce")
hrs  = hrs[(hrs > 0) & (hrs < 500)]
base_median = hrs.median()
print(f"  Median: {base_median:.1f}h  (n={len(hrs):,})", flush=True)

# ── Build territory lookup: owner abbrev → projected geometry ─────────────────
print("\nBuilding service-territory lookup...", flush=True)
territory_geom = {}
for owner_abbrev, terr_name in OWNER_TERRITORY.items():
    match = svcterr_proj[svcterr_proj["NAME"] == terr_name]
    if len(match) == 0:
        # fuzzy fallback: substring match
        match = svcterr_proj[
            svcterr_proj["NAME"].str.contains(terr_name.split()[0], case=False, na=False)
        ]
    if len(match) > 0:
        territory_geom[owner_abbrev] = unary_union(match.geometry.values)
    else:
        print(f"  WARNING: No territory polygon found for {owner_abbrev!r} "
              f"(looking for '{terr_name}')")

n_matched = len(territory_geom)
print(f"  Matched {n_matched}/{len(OWNER_TERRITORY)} owners to territory polygons",
      flush=True)

# ── Summarise which owners are covered ───────────────────────────────────────
matched_owners = set(territory_geom.keys())
unmatched_msg  = [o for o in OWNER_TERRITORY if o not in matched_owners]
if unmatched_msg:
    print(f"  Unmatched (keep full buffer): {unmatched_msg}")


# ══════════════════════════════════════════════════════════════════════════════
# CORE PROCESSING FUNCTION
# ══════════════════════════════════════════════════════════════════════════════

def process_scenario_refined(label, segments_gdf, description):
    """
    Apply all 5 proxies and return an impact dict.
    segments_gdf: LineString geometry, EPSG:4326, must have kv_num, Owner,
                  line_length_km columns.
    """
    print(f"\n{'='*70}", flush=True)
    print(f"Scenario {label}: {description}", flush=True)
    print(f"  {len(segments_gdf):,} segments", flush=True)
    t = time.time()

    seg_proj = segments_gdf.to_crs(epsg=PROJ_EPSG).reset_index(drop=True)
    n_segs   = len(seg_proj)

    # ─────────────────────────────────────────────────────────────────────────
    # PROXY 1 — Per-segment voltage-scaled buffers
    # ─────────────────────────────────────────────────────────────────────────
    print("  [P1] Voltage-scaled buffers...", flush=True)

    buf_radii = seg_proj["kv_num"].apply(kv_buffer_m)
    buf_geoms = [row.geometry.buffer(r)
                 for row, r in zip(seg_proj.itertuples(), buf_radii)]

    kv_dist = buf_radii.value_counts().sort_index()
    for r, cnt in kv_dist.items():
        print(f"       {r/1000:.0f} km → {cnt:,} segments")

    # ─────────────────────────────────────────────────────────────────────────
    # PROXY 5 — Redundancy discount (computed now, applied after population)
    # ─────────────────────────────────────────────────────────────────────────
    print("  [P5] Redundancy check (8 km radius)...", flush=True)

    buf8_gdf = gpd.GeoDataFrame(
        {"seg_local_idx": range(n_segs)},
        geometry=[g.buffer(PARALLEL_M) for g in seg_proj.geometry],
        crs=PROJ_EPSG,
    )
    par_join = gpd.sjoin(
        buf8_gdf,
        all_lines_proj[["geometry"]].reset_index(names="line_global_idx"),
        how="left",
        predicate="intersects",
    )
    # A segment is redundant if ≥2 matches from all_lines (the segment itself + ≥1 other)
    match_counts = par_join.groupby("seg_local_idx").size()
    is_redundant = (match_counts >= 2).reindex(range(n_segs), fill_value=False)

    n_redundant  = is_redundant.sum()
    len_redundant    = seg_proj.loc[is_redundant,    "line_length_km"].sum()
    len_nonredundant = seg_proj.loc[~is_redundant,   "line_length_km"].sum()
    total_len        = seg_proj["line_length_km"].sum()

    # Length-weighted blended redundancy factor
    redundancy_factor = (
        len_redundant    * 0.35 +
        len_nonredundant * 0.90
    ) / total_len if total_len > 0 else 0.90

    print(f"       Redundant: {n_redundant:,}/{n_segs:,} segments "
          f"({100*n_redundant/n_segs:.0f}%)  "
          f"Length-weighted factor: {redundancy_factor:.3f}", flush=True)

    # ─────────────────────────────────────────────────────────────────────────
    # PROXY 2 — Service territory clipping
    # ─────────────────────────────────────────────────────────────────────────
    print("  [P2] Clipping buffers to service territories...", flush=True)

    clipped_bufs = []
    n_clipped, n_unclipped = 0, 0
    for i, row in enumerate(seg_proj.itertuples()):
        owner = getattr(row, "Owner", None)
        terr  = territory_geom.get(owner) if owner else None
        buf   = buf_geoms[i]
        if terr is not None:
            try:
                clipped = buf.intersection(terr)
                clipped_bufs.append(clipped if not clipped.is_empty else buf)
                n_clipped += 1
            except Exception:
                clipped_bufs.append(buf)
                n_unclipped += 1
        else:
            clipped_bufs.append(buf)
            n_unclipped += 1

    print(f"       Territory-clipped: {n_clipped:,}  "
          f"Unclipped (no match): {n_unclipped:,}", flush=True)

    # Combined outage zone (union of all per-segment clipped buffers)
    print("  Building combined outage zone...", flush=True)
    zone_proj  = unary_union(clipped_bufs)
    zone_area  = zone_proj.area / 1e6
    zone_wgs84 = gpd.GeoSeries([zone_proj], crs=PROJ_EPSG).to_crs(4326).iloc[0]
    print(f"       Zone area (P1+P2): {zone_area:,.0f} km²", flush=True)

    # ─────────────────────────────────────────────────────────────────────────
    # CBG base set — all block groups intersecting the zone
    # ─────────────────────────────────────────────────────────────────────────
    zone_gdf = gpd.GeoDataFrame(geometry=[zone_proj], crs=PROJ_EPSG)
    cbg_raw  = gpd.sjoin(
        cbg_proj[["geometry", "GEOID", "population", "area_km2", "pop_density"]],
        zone_gdf,
        how="inner",
        predicate="intersects",
    )
    cbg_raw = cbg_raw[~cbg_raw.index.duplicated()]
    print(f"       CBGs in zone (raw): {len(cbg_raw):,}  "
          f"Pop: {cbg_raw['population'].sum():,.0f}", flush=True)

    # ─────────────────────────────────────────────────────────────────────────
    # PROXY 3 — Substation containment
    # ─────────────────────────────────────────────────────────────────────────
    print("  [P3] Substation containment...", flush=True)

    # Substations within zone
    subs_in_zone = gpd.sjoin(
        subs_proj[["geometry"]],
        zone_gdf,
        how="inner",
        predicate="within",
    )
    subs_in_zone = subs_in_zone[~subs_in_zone.index.duplicated()]

    if len(subs_in_zone) == 0:
        print("       WARNING: No substations in zone — skipping P3", flush=True)
        cbg_p3 = cbg_raw.copy()
    else:
        # Buffer substations
        sub_buf_union = unary_union(
            subs_proj.loc[subs_in_zone.index, "geometry"].buffer(SUB_ADJACENT_M)
        )
        sub_buf_gdf = gpd.GeoDataFrame(geometry=[sub_buf_union], crs=PROJ_EPSG)

        # Keep CBGs that intersect a substation buffer
        cbg_p3 = gpd.sjoin(
            cbg_raw[["geometry", "GEOID", "population", "area_km2", "pop_density"]],
            sub_buf_gdf,
            how="inner",
            predicate="intersects",
        )
        cbg_p3 = cbg_p3[~cbg_p3.index.duplicated()]

    n_dropped_p3 = len(cbg_raw) - len(cbg_p3)
    print(f"       Substations in zone: {len(subs_in_zone):,}  "
          f"CBGs kept: {len(cbg_p3):,}  "
          f"Dropped (no substation): {n_dropped_p3:,}", flush=True)

    # ─────────────────────────────────────────────────────────────────────────
    # PROXY 4 — Population density threshold
    # ─────────────────────────────────────────────────────────────────────────
    print(f"  [P4] Density filter (> {DENSITY_MIN} people/km²)...", flush=True)

    cbg_p4 = cbg_p3[cbg_p3["pop_density"] > DENSITY_MIN].copy()
    n_dropped_p4 = len(cbg_p3) - len(cbg_p4)
    print(f"       CBGs dropped (low density): {n_dropped_p4:,}  "
          f"Remaining: {len(cbg_p4):,}", flush=True)

    # ─────────────────────────────────────────────────────────────────────────
    # Population after P3+P4, then P5 redundancy discount
    # ─────────────────────────────────────────────────────────────────────────
    pop_before_discount = int(cbg_p4["population"].sum())
    pop_refined         = int(pop_before_discount * redundancy_factor)
    n_cbg_final         = len(cbg_p4)

    print(f"  Population before P5: {pop_before_discount:,.0f}  "
          f"× {redundancy_factor:.3f} = {pop_refined:,.0f}", flush=True)

    # ─────────────────────────────────────────────────────────────────────────
    # Building count — scan within union of qualifying CBG polygons
    # ─────────────────────────────────────────────────────────────────────────
    print("  Counting buildings (tile scan on qualified CBG zone)...", flush=True)
    if len(cbg_p4) > 0:
        cbg_p4_wgs84  = cbg_p4.to_crs(4326)
        cbg_zone_geom = unary_union(cbg_p4_wgs84.geometry.values)
        n_buildings, n_tiles = count_buildings_in_zone(cbg_zone_geom, TILES_DIR)
    else:
        n_buildings, n_tiles = 0, 0
    # Apply redundancy discount to building count as well
    n_buildings_refined = int(n_buildings * redundancy_factor)
    print(f"  Buildings (raw): {n_buildings:,}  "
          f"× {redundancy_factor:.3f} = {n_buildings_refined:,}  "
          f"({n_tiles} tiles)", flush=True)

    # ─────────────────────────────────────────────────────────────────────────
    # Critical facilities
    # ─────────────────────────────────────────────────────────────────────────
    cf_in = gpd.sjoin(
        cf_proj[["geometry", "facility_type"]],
        zone_gdf,
        how="inner",
        predicate="within",
    )
    cf_in = cf_in[~cf_in.index.duplicated()]
    n_cf  = len(cf_in)
    n_hos = int((cf_in["facility_type"] == "Hospital").sum())
    n_fst = int((cf_in["facility_type"] == "Fire Station").sum())

    # ─────────────────────────────────────────────────────────────────────────
    # Outage hours and economic cost
    # ─────────────────────────────────────────────────────────────────────────
    outage_hrs  = weighted_outage_hrs(segments_gdf, base_median)
    person_hrs  = int(pop_refined * outage_hrs)
    econ_cost   = (pop_refined / PERSONS_PER_HH) * KWH_PER_HH * outage_hrs * KWH_VOLL

    print(f"  Outage hrs: {outage_hrs:.1f}h  "
          f"Person-hours: {person_hrs:,.0f}  "
          f"Economic cost: ${econ_cost:,.0f}", flush=True)
    print(f"  Elapsed: {time.time()-t:.0f}s", flush=True)

    return {
        "Scenario"                       : label,
        "Description"                    : description,
        "Segments"                        : len(segments_gdf),
        "Total line length (km)"          : round(segments_gdf["line_length_km"].sum(), 1),
        "Zone area P1+P2 (km²)"           : round(zone_area, 0),
        "CBGs raw (P1+P2)"                : len(cbg_raw),
        "Pop raw (P1+P2)"                 : int(cbg_raw["population"].sum()),
        "CBGs dropped P3 (no substation)" : n_dropped_p3,
        "CBGs dropped P4 (low density)"   : n_dropped_p4,
        "CBGs final"                      : n_cbg_final,
        "Pop before discount"             : pop_before_discount,
        "Redundant segments (%)"          : round(100 * n_redundant / n_segs, 1),
        "Redundancy factor"               : round(redundancy_factor, 3),
        "Population affected (refined)"   : pop_refined,
        "Buildings affected (refined)"    : n_buildings_refined,
        "Critical facilities"             : n_cf,
        "  Hospitals"                     : n_hos,
        "  Fire stations"                 : n_fst,
        "Median outage (hours)"           : round(outage_hrs, 1),
        "Person-hours"                    : person_hrs,
        "Economic cost ($)"               : round(econ_cost, 0),
    }


# ══════════════════════════════════════════════════════════════════════════════
# RUN ALL THREE SCENARIOS
# ══════════════════════════════════════════════════════════════════════════════

# Scenario A — Fortress Grid
scn_a = gpd.read_file(SCN_A_F)
res_a = process_scenario_refined(
    "A — Fortress Grid", scn_a,
    "Critical+High, hardened (50%/25% reduction)"
)

# Scenario B — Islands of Power (reconstruct line geometry from WKT)
scn_b_raw = gpd.read_file(SCN_B_F)
scn_b = scn_b_raw.copy()
scn_b["geometry"] = scn_b["line_geometry_wkt"].apply(shapely_wkt.loads)
scn_b = gpd.GeoDataFrame(scn_b, geometry="geometry", crs="EPSG:4326")
res_b = process_scenario_refined(
    "B — Islands of Power", scn_b,
    "Critical+High, microgrid service areas"
)

# Scenario C — Reactive Crisis
scn_c = gpd.read_file(SCN_C_F, layer="critical_segments")
res_c = process_scenario_refined(
    "C — Reactive Crisis", scn_c,
    "Critical only, no hardening (crisis conditions)"
)


# ══════════════════════════════════════════════════════════════════════════════
# SAVE REFINED RESULTS
# ══════════════════════════════════════════════════════════════════════════════
refined_df = pd.DataFrame([res_a, res_b, res_c])
refined_df.to_csv(str(OUT_CSV), index=False)
print(f"\nSaved: {OUT_CSV}  ({OUT_CSV.stat().st_size/1e3:.1f} KB)")


# ══════════════════════════════════════════════════════════════════════════════
# COMPARISON TABLE — ORIGINAL vs REFINED
# ══════════════════════════════════════════════════════════════════════════════
W = 100
print(f"\n{'='*W}")
print("COMPARISON: ORIGINAL vs REFINED")
print(f"{'='*W}")

orig = pd.read_csv(ORIG_CSV)
orig_map = {row["Scenario"]: row for _, row in orig.iterrows()}

# Align original keys to our scenario labels
orig_labels = {
    "A — Fortress Grid"  : "A — Fortress Grid",
    "B — Islands of Power": "B — Islands of Power",
    "C — Reactive Crisis" : "C — Reactive Crisis",
}

comparison_rows = []
for res in [res_a, res_b, res_c]:
    lbl = res["Scenario"]
    o   = orig_map.get(lbl, {})
    orig_pop  = int(o.get("Population affected", 1))
    orig_bldg = int(o.get("Buildings affected",  1))
    orig_cost = float(o.get("Economic cost ($)",  1))
    comparison_rows.append({
        "Scenario"         : lbl,
        "Orig pop"         : orig_pop,
        "Refined pop"      : res["Population affected (refined)"],
        "Pop delta"        : f"{(res['Population affected (refined)'] / orig_pop - 1)*100:+.0f}%",
        "Orig buildings"   : orig_bldg,
        "Refined buildings": res["Buildings affected (refined)"],
        "Bldg delta"       : f"{(res['Buildings affected (refined)'] / orig_bldg - 1)*100:+.0f}%",
        "Orig cost $M"     : round(orig_cost / 1e6, 1),
        "Refined cost $M"  : round(res["Economic cost ($)"] / 1e6, 1),
        "Cost delta"       : f"{(res['Economic cost ($)'] / orig_cost - 1)*100:+.0f}%",
    })

comp = pd.DataFrame(comparison_rows).set_index("Scenario")
pd.set_option("display.width", W)
pd.set_option("display.max_colwidth", 30)
print(comp.to_string())

# ── Proxy contribution analysis ───────────────────────────────────────────────
print(f"\n{'='*W}")
print("PROXY CONTRIBUTION ANALYSIS  (Scenario A — Fortress Grid)")
print(f"{'='*W}")

orig_pop_a = int(orig_map.get("A — Fortress Grid", {}).get("Population affected", 0))
raw_p1p2_a = res_a["Pop raw (P1+P2)"]
after_p3_a = res_a["Pop before discount"] + \
             sum(cbg_p3_pop := [0])  # placeholder — see below

# Reconstruct the proxy cascade from saved fields
steps = [
    ("Original (flat 5 km buffer)",    orig_pop_a),
    ("After P1+P2 (vol-scaled + clip)", res_a["Pop raw (P1+P2)"]),
    ("After P3 (substation contain.)",  res_a["Pop before discount"] +
                                        res_a["CBGs dropped P3 (no substation)"] * 0),
    # P3 and P4 combined take pop from raw P1P2 → pop_before_discount
    ("After P3+P4 (substation+density)",res_a["Pop before discount"]),
    ("After P5 (redundancy discount)",  res_a["Population affected (refined)"]),
]
# We don't have P3-only intermediate, compute P3 contribution as share of total drop
pop_drop_total = orig_pop_a - res_a["Population affected (refined)"]

proxy_effects = {
    "P1 Voltage-scaled buffers" : orig_pop_a - res_a["Pop raw (P1+P2)"],
    "P2 Territory clipping"     : 0,   # bundled into P1+P2 zone
    "P3 Substation containment" : res_a["CBGs dropped P3 (no substation)"],  # in CBG units
    "P4 Density threshold"      : res_a["CBGs dropped P4 (low density)"],
    "P5 Redundancy discount"    : res_a["Pop before discount"] - res_a["Population affected (refined)"],
}

print(f"\n  {'Step':<42} {'Population':>14}  {'Change':>10}")
print(f"  {'-'*70}")
prev = orig_pop_a
for step_name, step_pop in steps:
    delta = step_pop - prev if step_name != steps[0][0] else 0
    delta_str = f"{delta:+,.0f}" if delta != 0 else "  baseline"
    print(f"  {step_name:<42} {step_pop:>14,.0f}  {delta_str:>10}")
    prev = step_pop

print(f"\n  Population reduction breakdown (Scenario A):")
p1_drop  = orig_pop_a     - res_a["Pop raw (P1+P2)"]
p34_drop = res_a["Pop raw (P1+P2)"] - res_a["Pop before discount"]
p5_drop  = res_a["Pop before discount"] - res_a["Population affected (refined)"]
tot_drop = orig_pop_a - res_a["Population affected (refined)"]

for name, drop in [
    ("P1+P2  Voltage-scaled buffers + territory clip",  p1_drop),
    ("P3+P4  Substation contain. + density filter",    p34_drop),
    ("P5     Redundancy discount",                      p5_drop),
]:
    pct = 100 * drop / tot_drop if tot_drop > 0 else 0
    print(f"    {name:<48} {drop:>10,.0f}  ({pct:.0f}% of total reduction)")

print(f"\n  Largest corrective proxy: ", end="")
effects = [("P1+P2", p1_drop), ("P3+P4", p34_drop), ("P5", p5_drop)]
biggest = max(effects, key=lambda x: x[1])
print(f"{biggest[0]} ({biggest[1]:,.0f} people, "
      f"{100*biggest[1]/tot_drop:.0f}% of total reduction)")

print(f"\nTotal run time: {time.time()-t0:.0f}s")
