"""
03_scenario_builder.py
Build three wildfire resilience scenarios from transmission risk scores.

Scenario A — Fortress Grid:    Critical + High, hardened with risk reductions
Scenario B — Islands of Power: Critical + High, microgrid service-area buffers
Scenario C — Reactive Crisis:  Critical only, with cascade substation risk

Outputs (data/processed/):
  scenario_a_fortress.gpkg
  scenario_b_islands.gpkg
  scenario_c_crisis.gpkg  (two layers: critical_segments + substations)
"""

import warnings
warnings.filterwarnings("ignore")

import geopandas as gpd
import pandas as pd
from pathlib import Path
from shapely.ops import unary_union

ROOT    = Path(__file__).resolve().parent.parent
RISK_F  = ROOT / "data/processed/transmission_risk_scores.gpkg"
SUBS_F  = ROOT / "data/raw/substations/substations.gpkg"
OUT_DIR = ROOT / "data/processed"
OUT_DIR.mkdir(parents=True, exist_ok=True)

PROJ_EPSG = 3310  # CA Albers — metres

# ── Load scored transmission lines ─────────────────────────────────────────
print("Loading scored transmission lines...", flush=True)
risk = gpd.read_file(RISK_F)
print(f"  {len(risk):,} segments loaded")

# Line length in km (projected CRS)
risk_proj = risk.to_crs(epsg=PROJ_EPSG)
risk["line_length_km"] = (risk_proj.geometry.length / 1000).round(3)

# Tier subsets
critical = risk[risk["risk_tier"] == "Critical"].copy()
high     = risk[risk["risk_tier"] == "High"].copy()
top25    = risk[risk["risk_tier"].isin(["Critical", "High"])].copy()

print(f"  Critical: {len(critical):,}  "
      f"High: {len(high):,}  "
      f"Critical+High: {len(top25):,}")

# ══════════════════════════════════════════════════════════════════════════════
# SCENARIO A — FORTRESS GRID
# ══════════════════════════════════════════════════════════════════════════════
print("\nScenario A: Fortress Grid...", flush=True)

a_crit = critical.copy()
a_high = high.copy()

# Critical: 50% composite reduction, then × 0.3 outage factor
a_crit["risk_reduction_pct"] = 50
a_crit["reduced_composite"]  = (a_crit["composite_score"] * 0.50).round(4)
a_crit["outage_probability"] = (a_crit["reduced_composite"] * 0.30).round(4)

# High: 25% composite reduction, then × 0.3 outage factor
a_high["risk_reduction_pct"] = 25
a_high["reduced_composite"]  = (a_high["composite_score"] * 0.75).round(4)
a_high["outage_probability"] = (a_high["reduced_composite"] * 0.30).round(4)

scenario_a = gpd.GeoDataFrame(
    pd.concat([a_crit, a_high], ignore_index=True), crs="EPSG:4326"
)

out_a = OUT_DIR / "scenario_a_fortress.gpkg"
scenario_a.to_file(str(out_a), driver="GPKG")
print(f"  Saved {out_a.name}  "
      f"({out_a.stat().st_size/1e6:.1f} MB, {len(scenario_a):,} segments)")

# ══════════════════════════════════════════════════════════════════════════════
# SCENARIO B — ISLANDS OF POWER
# ══════════════════════════════════════════════════════════════════════════════
print("\nScenario B: Islands of Power...", flush=True)

scenario_b = top25.copy()
scenario_b["microgrid_opportunity"] = True
scenario_b["outage_probability"]    = (scenario_b["composite_score"] * 0.60).round(4)

# Preserve original line geometry as WKT before replacing with 10 km buffer
scenario_b["line_geometry_wkt"] = scenario_b.geometry.to_wkt()

# Build 10 km service-area buffers in projected CRS, return to WGS84
b_proj = scenario_b.to_crs(epsg=PROJ_EPSG).copy()
b_proj["geometry"] = b_proj.geometry.buffer(10_000)
scenario_b = b_proj.to_crs(epsg=4326)

out_b = OUT_DIR / "scenario_b_islands.gpkg"
scenario_b.to_file(str(out_b), driver="GPKG")
print(f"  Saved {out_b.name}  "
      f"({out_b.stat().st_size/1e6:.1f} MB, {len(scenario_b):,} service-area polygons)")

# Estimate total unique service area in km²
service_union = unary_union(b_proj.geometry)  # in metres (PROJ_EPSG)
total_service_km2 = service_union.area / 1e6
print(f"  Combined service area (deduplicated): {total_service_km2:,.0f} km²")

# ══════════════════════════════════════════════════════════════════════════════
# SCENARIO C — REACTIVE CRISIS
# ══════════════════════════════════════════════════════════════════════════════
print("\nScenario C: Reactive Crisis...", flush=True)

scenario_c_lines = critical.copy()
scenario_c_lines["outage_probability"] = (
    scenario_c_lines["composite_score"] * 0.90
).round(4)

# Load substations
subs = gpd.read_file(SUBS_F)
print(f"  Substations loaded: {len(subs):,}")

# Buffer Critical segments by 50 km in projected CRS
c_proj   = scenario_c_lines.to_crs(epsg=PROJ_EPSG)
subs_proj = subs.to_crs(epsg=PROJ_EPSG)

buf50 = c_proj[["geometry"]].copy()
buf50["geometry"] = c_proj.geometry.buffer(50_000)

# Spatial join: substations within 50 km of any Critical segment
cascade_join = gpd.sjoin(
    subs_proj[["geometry"]],
    buf50.reset_index(names="seg_idx"),
    how="inner",
    predicate="within"
)
cascade_subs_idx = set(cascade_join.index.unique())

subs_out = subs.copy()
subs_out["cascade_risk"] = subs_out.index.isin(cascade_subs_idx)

n_cascade = subs_out["cascade_risk"].sum()
print(f"  Substations in 50 km cascade zone: "
      f"{n_cascade:,} / {len(subs_out):,} ({100*n_cascade/len(subs_out):.1f}%)")

# Save two layers in one GeoPackage
out_c = OUT_DIR / "scenario_c_crisis.gpkg"
scenario_c_lines.to_file(str(out_c), driver="GPKG", layer="critical_segments")
subs_out.to_file(str(out_c), driver="GPKG", layer="substations")
print(f"  Saved {out_c.name}  ({out_c.stat().st_size/1e6:.1f} MB)")
print(f"    Layer 'critical_segments': {len(scenario_c_lines):,} rows")
print(f"    Layer 'substations':       {len(subs_out):,} rows "
      f"({n_cascade:,} cascade_risk=True)")

# ══════════════════════════════════════════════════════════════════════════════
# COMPARISON TABLE
# ══════════════════════════════════════════════════════════════════════════════
W = 96

def fmt_counties(gdf, top_n=5):
    col = "county" if "county" in gdf.columns else "COUNTY"
    return ", ".join(
        f"{c}({n})"
        for c, n in gdf[col].value_counts().head(top_n).items()
        if c and c not in ("Unknown", "nan")
    )

print("\n" + "="*W)
print("SCENARIO COMPARISON")
print("="*W)

rows = [
    {
        "Scenario"             : "A — Fortress Grid",
        "Tier scope"           : "Critical + High",
        "Segments"             : f"{len(scenario_a):,}",
        "Total length (km)"    : f"{scenario_a['line_length_km'].sum():,.0f}",
        "Investment"           : "50% crit / 25% high",
        "Mean outage prob"     : f"{scenario_a['outage_probability'].mean():.4f}",
        "Max outage prob"      : f"{scenario_a['outage_probability'].max():.4f}",
        "Substations @ risk"   : "—",
        "Top 5 counties"       : fmt_counties(scenario_a),
    },
    {
        "Scenario"             : "B — Islands of Power",
        "Tier scope"           : "Critical + High",
        "Segments"             : f"{len(scenario_b):,}",
        "Total length (km)"    : f"{scenario_b['line_length_km'].sum():,.0f}",
        "Investment"           : f"Microgrid buffers ({total_service_km2:,.0f} km²)",
        "Mean outage prob"     : f"{scenario_b['outage_probability'].mean():.4f}",
        "Max outage prob"      : f"{scenario_b['outage_probability'].max():.4f}",
        "Substations @ risk"   : "—",
        "Top 5 counties"       : fmt_counties(scenario_b),
    },
    {
        "Scenario"             : "C — Reactive Crisis",
        "Tier scope"           : "Critical only",
        "Segments"             : f"{len(scenario_c_lines):,}",
        "Total length (km)"    : f"{scenario_c_lines['line_length_km'].sum():,.0f}",
        "Investment"           : "None (crisis response)",
        "Mean outage prob"     : f"{scenario_c_lines['outage_probability'].mean():.4f}",
        "Max outage prob"      : f"{scenario_c_lines['outage_probability'].max():.4f}",
        "Substations @ risk"   : f"{n_cascade:,} / {len(subs_out):,}",
        "Top 5 counties"       : fmt_counties(scenario_c_lines),
    },
]

comp = pd.DataFrame(rows).set_index("Scenario")
pd.set_option("display.max_colwidth", 55)
pd.set_option("display.width", W)
print(comp.T.to_string())

# Per-scenario county breakdowns
print("\n" + "="*W)
print("SEGMENTS BY COUNTY")
print("="*W)
county_comp = pd.DataFrame({
    "Scenario A": scenario_a["county"].value_counts().head(10),
    "Scenario B": scenario_b["county"].value_counts().head(10),
    "Scenario C": scenario_c_lines["county"].value_counts().head(10),
}).fillna(0).astype(int)
print(county_comp.to_string())

print("\n" + "="*W)
print("SCENARIO C — CASCADE SUBSTATIONS BY COUNTY (top 10)")
print("="*W)
cascade_county = (
    subs_out[subs_out["cascade_risk"]]
    .groupby("COUNTY").size()
    .sort_values(ascending=False)
    .head(10)
    .rename("substations_at_risk")
)
print(cascade_county.to_string())

print("\n" + "="*W)
print("VOLTAGE BREAKDOWN BY SCENARIO (mean composite per kV class)")
print("="*W)
for label, gdf in [("A", scenario_a), ("B", scenario_b), ("C", scenario_c_lines)]:
    bins   = [0, 69, 115, 230, 345, 500, 9999]
    labels = ["<69kV","69kV","115kV","230kV","345kV","500kV"]
    gdf2   = gdf.copy()
    gdf2["kv_class"] = pd.cut(gdf2["kv_num"], bins=bins, labels=labels, right=True)
    tbl = (gdf2.groupby("kv_class", observed=True)
               .agg(segs=("composite_score","count"),
                    mean_score=("composite_score","mean"),
                    mean_outage=("outage_probability","mean"))
               .dropna())
    tbl.columns = [f"Sc{label}_{c}" for c in tbl.columns]
    print(f"\nScenario {label}:")
    print(tbl.to_string())
