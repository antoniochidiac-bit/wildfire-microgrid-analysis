"""
07_visualization.py — Revised presentation outputs for wildfire-microgrid analysis
Produces:
  1. outputs/maps/wildfire_microgrid_analysis_v2.html  — interactive folium map (v2)
  2. outputs/tables/prioritization_matrix_v2.png       — redesigned scatter (300 DPI)

Retains existing outputs unchanged (scenario_comparison, financial_case,
prioritization_scatter, killer_slide) by keeping their generation blocks below.
"""

import os, warnings, base64
import numpy as np
import pandas as pd
import geopandas as gpd
import folium
from folium.plugins import MeasureControl
from shapely.ops import unary_union
import matplotlib
matplotlib.use("Agg")
import matplotlib.pyplot as plt
import matplotlib.patches as mpatches
import matplotlib.colors as mcolors
import matplotlib.patheffects as pe
from matplotlib.lines import Line2D
from matplotlib.table import Table

warnings.filterwarnings("ignore")

# ── Paths ────────────────────────────────────────────────────────────────────
DATA_PROC = "data/processed"
DATA_RAW  = "data/raw"
OUT_MAPS  = "outputs/maps"
OUT_TABS  = "outputs/tables"

os.makedirs(OUT_MAPS, exist_ok=True)
os.makedirs(OUT_TABS, exist_ok=True)

# ── Voltage → buffer distance (metres) ───────────────────────────────────────
def kv_to_buffer_m(kv_num):
    """Voltage-scaled outage buffer radius."""
    kv = float(kv_num) if kv_num else 0
    if kv >= 500: return 20_000
    if kv >= 230: return 15_000
    if kv >= 115: return 10_000
    if kv >= 69:  return  7_000
    return                 5_000


def build_scenario_zone(segments_gdf, reduction_factor=1.0, crs_proj=3310):
    """
    Buffer each segment by voltage-scaled distance × reduction_factor.
    Returns a single dissolved + simplified MultiPolygon in EPSG:4326.
    """
    proj = segments_gdf.to_crs(crs_proj).copy()
    buffers = []
    for _, row in proj.iterrows():
        if row.geometry is None:
            continue
        dist = kv_to_buffer_m(row["kv_num"]) * reduction_factor
        buffers.append(row.geometry.buffer(dist))
    if not buffers:
        return None
    union = unary_union(buffers)
    # Simplify to ~500 m tolerance so the HTML stays lightweight
    simplified = union.simplify(500, preserve_topology=True)
    import shapely.wkt
    from shapely.geometry import shape, mapping
    gdf_out = gpd.GeoDataFrame(geometry=[simplified], crs=crs_proj)
    return gdf_out.to_crs(4326)


def geojson_layer(gdf, name, fill_color, fill_opacity, line_color="none",
                  line_weight=0.5, show=False, tooltip_text=None):
    """Wrap a single-polygon GDF into a folium FeatureGroup."""
    fg = folium.FeatureGroup(name=name, show=show)
    tip = tooltip_text or name
    geojson_str = gdf.to_json()
    folium.GeoJson(
        geojson_str,
        style_function=lambda x, fc=fill_color, fo=fill_opacity,
                               lc=line_color, lw=line_weight: {
            "fillColor": fc,
            "fillOpacity": fo,
            "color": lc,
            "weight": lw,
        },
        tooltip=folium.Tooltip(tip),
    ).add_to(fg)
    return fg


# ─────────────────────────────────────────────────────────────────────────────
# OUTPUT 1 — Interactive folium map v2
# ─────────────────────────────────────────────────────────────────────────────
print("=" * 60)
print("OUTPUT 1: Interactive map v2")
print("=" * 60)

# ── Load base datasets ───────────────────────────────────────────────────────
print("  Loading transmission risk scores…")
risk = gpd.read_file(f"{DATA_PROC}/transmission_risk_scores.gpkg").to_crs(4326)

print("  Loading FHSZ (decimated)…")
fhsz_full = gpd.read_file(f"{DATA_RAW}/wildfire_risk/fhsz.gpkg")
fhsz = fhsz_full.iloc[::5].copy().to_crs(4326)
del fhsz_full

print("  Loading critical facilities…")
cf = gpd.read_file(f"{DATA_RAW}/microgrids/critical_facilities.gpkg").to_crs(4326)

print("  Loading prioritization matrix…")
pm = pd.read_csv(f"{DATA_PROC}/prioritization_matrix.csv")
top4_plan = pm[pm["action_tier"] == "Plan Now"].nlargest(4, "priority_score")

print("  Loading census block groups…")
cbg = gpd.read_file(f"{DATA_RAW}/census/census_block_groups.gpkg").to_crs(4326)

print("  Loading SGIP data…")
sgip = pd.read_csv(f"{DATA_RAW}/microgrids/sgip_data.csv", low_memory=False)

# ── Identify SGIP-covered corridors ─────────────────────────────────────────
# Counties with active SGIP installations
sgip_counties = set(
    sgip[sgip["County"].notna()]["County"]
    .str.strip().str.lower().unique()
)
risk_low = risk.copy()
risk_low["county_lower"] = risk_low["county"].str.strip().str.lower()
sgip_segments = risk_low[risk_low["county_lower"].isin(sgip_counties)].copy()

# Top 25% risk-scored corridors → pre-positioned microgrid deployment areas
q75 = risk["composite_score"].quantile(0.75)
top25pct = risk[risk["composite_score"] >= q75].copy()

# ── Build scenario exposure zones ────────────────────────────────────────────
print("  Building Scenario A buffer zone (Critical×0.50, High×0.75)…")
crit_a = risk[risk["risk_tier"] == "Critical"].copy()
high_a = risk[risk["risk_tier"] == "High"].copy()
zone_a_crit = build_scenario_zone(crit_a, reduction_factor=0.50)
zone_a_high = build_scenario_zone(high_a, reduction_factor=0.75)
if zone_a_crit is not None and zone_a_high is not None:
    zone_a_union = unary_union(
        [zone_a_crit.geometry.iloc[0], zone_a_high.geometry.iloc[0]]
    ).simplify(500, preserve_topology=True)
elif zone_a_crit is not None:
    zone_a_union = zone_a_crit.geometry.iloc[0]
else:
    zone_a_union = zone_a_high.geometry.iloc[0]
zone_a_gdf = gpd.GeoDataFrame(geometry=[zone_a_union], crs=4326)

print("  Building Scenario B buffer zone (full scale, subtract covered)…")
crit_high = risk[risk["risk_tier"].isin(["Critical", "High"])].copy()
zone_b_full = build_scenario_zone(crit_high, reduction_factor=1.0)
zone_b_full_geom = zone_b_full.geometry.iloc[0]

# Covered area: SGIP-county segments (buffered) + top-25% corridors (buffered)
zone_sgip = build_scenario_zone(sgip_segments, reduction_factor=1.0)
zone_top25 = build_scenario_zone(top25pct, reduction_factor=1.0)
covered_parts = []
if zone_sgip is not None:
    covered_parts.append(zone_sgip.geometry.iloc[0])
if zone_top25 is not None:
    covered_parts.append(zone_top25.geometry.iloc[0])

if covered_parts:
    covered_geom = unary_union(covered_parts).simplify(500, preserve_topology=True)
    covered_clipped = covered_geom.intersection(zone_b_full_geom)
    residual_b = zone_b_full_geom.difference(covered_clipped).simplify(500)
else:
    covered_clipped = None
    residual_b = zone_b_full_geom

zone_b_residual_gdf = gpd.GeoDataFrame(geometry=[residual_b], crs=4326)
if covered_clipped is not None and not covered_clipped.is_empty:
    zone_b_coverage_gdf = gpd.GeoDataFrame(
        geometry=[covered_clipped.simplify(500, preserve_topology=True)], crs=4326
    )
else:
    zone_b_coverage_gdf = None

print("  Building Scenario C buffer zone (Critical only, full scale)…")
crit_c = risk[risk["risk_tier"] == "Critical"].copy()
zone_c = build_scenario_zone(crit_c, reduction_factor=1.0)
zone_c_geom = zone_c.geometry.iloc[0]
zone_c_gdf = gpd.GeoDataFrame(geometry=[zone_c_geom], crs=4326)

# Critical facilities within Scenario C outage zone (for emergency microgrid dots)
cf_proj = cf.to_crs(3310)
zone_c_proj = zone_c.to_crs(3310)
cf_in_c = cf_proj[cf_proj.within(zone_c_proj.geometry.iloc[0])].to_crs(4326)

# ── Build map ────────────────────────────────────────────────────────────────
ca_center = [37.3, -119.5]
m = folium.Map(
    location=ca_center,
    zoom_start=6,
    tiles="CartoDB dark_matter",
    control_scale=True,
)

# ── Layer 1 (ON): Transmission lines coloured by risk tier ───────────────────
RISK_COLORS = {
    "Critical": "#E74C3C",
    "High":     "#E67E22",
    "Moderate": "#F1C40F",
    "Low":      "#95A5A6",
}

# Identify which risk rows correspond to top-4 Plan Now corridors
# Match by county + Owner + kv_class bucket
def kv_to_class(kv_num):
    kv = float(kv_num) if kv_num else 0
    if kv >= 500: return "500kV+"
    if kv >= 230: return "230-499kV"
    if kv >= 115: return "115-229kV"
    return "<115kV"

risk["kv_class_calc"] = risk["kv_num"].apply(kv_to_class)

plan_now_keys = set(
    zip(
        top4_plan["county"].str.strip().str.lower(),
        top4_plan["Owner"].str.strip().str.lower(),
        top4_plan["kv_class"].str.strip().str.lower(),
    )
)
risk["county_lower"] = risk["county"].str.strip().str.lower()
risk["owner_lower"]  = risk["Owner"].str.strip().str.lower()
risk["kv_cls_lower"] = risk["kv_class_calc"].str.strip().str.lower()
risk["is_plan_now"]  = risk.apply(
    lambda r: (r["county_lower"], r["owner_lower"], r["kv_cls_lower"]) in plan_now_keys,
    axis=1,
)

risk_layer = folium.FeatureGroup(name="Transmission Lines (risk tier)", show=True)

# Regular lines first
for _, row in risk[~risk["is_plan_now"]].iterrows():
    if row.geometry is None:
        continue
    color = RISK_COLORS.get(row.get("risk_tier", "Low"), "#95A5A6")
    folium.GeoJson(
        row.geometry.__geo_interface__,
        style_function=lambda x, c=color: {
            "color": c, "weight": 2.5, "opacity": 0.8,
        },
        tooltip=folium.Tooltip(
            f"<b>{row.get('Name','')}</b><br>"
            f"Owner: {row.get('Owner','')}<br>"
            f"kV: {row.get('kV','')}<br>"
            f"Risk: {row.get('risk_tier','')}<br>"
            f"Score: {row.get('composite_score',0):.3f}"
        ),
    ).add_to(risk_layer)

# Plan Now corridors — thick white outline + popup
plan_now_meta = {
    (r["county"].strip().lower(), r["Owner"].strip().lower(), r["kv_class"].strip().lower()): r
    for _, r in top4_plan.iterrows()
}
for _, row in risk[risk["is_plan_now"]].iterrows():
    if row.geometry is None:
        continue
    key = (row["county_lower"], row["owner_lower"], row["kv_cls_lower"])
    pm_row = plan_now_meta.get(key, None)
    if pm_row is not None:
        popup_html = (
            f"<div style='font-family:Arial;font-size:13px;min-width:200px'>"
            f"<b style='color:#E74C3C'>PLAN NOW CORRIDOR</b><br>"
            f"<b>{pm_row['county']} — {pm_row['Owner']} {pm_row['kv_class']}</b><br>"
            f"Priority score: <b>{pm_row['priority_score']:.3f}</b><br>"
            f"D1 Life Safety: <b>{pm_row['d1_life_safety']:.1f}/10</b><br>"
            f"D2 Resilience Gap: <b>{pm_row['d2_resilience_gap']:.1f}/10</b><br>"
            f"Action: <b>{pm_row['action_tier']}</b>"
            f"</div>"
        )
        popup = folium.Popup(popup_html, max_width=260)
    else:
        popup = None
    # White outer glow line
    folium.GeoJson(
        row.geometry.__geo_interface__,
        style_function=lambda x: {
            "color": "white", "weight": 6, "opacity": 0.6,
        },
    ).add_to(risk_layer)
    # Coloured line on top
    color = RISK_COLORS.get(row.get("risk_tier", "Low"), "#95A5A6")
    geo = folium.GeoJson(
        row.geometry.__geo_interface__,
        style_function=lambda x, c=color: {
            "color": c, "weight": 3.5, "opacity": 1.0,
        },
        tooltip=folium.Tooltip(
            f"<b>★ PLAN NOW</b> | {row.get('Name','')} | "
            f"{row.get('risk_tier','')} | Score: {row.get('composite_score',0):.3f}"
        ),
    )
    if popup:
        geo.add_child(popup)
    geo.add_to(risk_layer)

risk_layer.add_to(m)

# ── Layer 2 (OFF): FHSZ severity zones ───────────────────────────────────────
FHSZ_PAL = {1: "#8B3A3A", 2: "#CC6600", 3: "#FF4500"}   # Moderate/High/VeryHigh
FHSZ_LABELS = {1: "Moderate", 2: "High", 3: "Very High"}
fhsz_layer = folium.FeatureGroup(name="Fire Hazard Severity Zones", show=False)
for _, row in fhsz.iterrows():
    if row.geometry is None:
        continue
    sev = int(row.get("FHSZ", 1))
    color = FHSZ_PAL.get(sev, "#555555")
    label = FHSZ_LABELS.get(sev, "Unknown")
    folium.GeoJson(
        row.geometry.__geo_interface__,
        style_function=lambda x, c=color: {
            "fillColor": c, "color": "none", "fillOpacity": 0.20,
        },
        tooltip=folium.Tooltip(f"FHSZ: {label}"),
    ).add_to(fhsz_layer)
fhsz_layer.add_to(m)

# ── Layer 3 (OFF): Scenario A outage zone ────────────────────────────────────
scn_a_layer = geojson_layer(
    zone_a_gdf,
    name="Scenario A — Fortress Grid (residual exposure)",
    fill_color="#FF8C00",
    fill_opacity=0.25,
    line_color="#FF8C00",
    line_weight=1.0,
    show=False,
    tooltip_text="Fortress Grid — Residual exposure after hardening",
)
scn_a_layer.add_to(m)

# ── Layer 4 (OFF): Scenario B net exposure zone ──────────────────────────────
scn_b_layer = geojson_layer(
    zone_b_residual_gdf,
    name="Scenario B — Islands of Power (net exposure)",
    fill_color="#3498DB",
    fill_opacity=0.25,
    line_color="#3498DB",
    line_weight=0.8,
    show=False,
    tooltip_text="Islands of Power — Net exposure after microgrid coverage",
)
scn_b_layer.add_to(m)

# ── Layer 5 (OFF): Scenario B microgrid coverage areas ───────────────────────
if zone_b_coverage_gdf is not None:
    scn_b_cov_layer = geojson_layer(
        zone_b_coverage_gdf,
        name="Scenario B — Microgrid coverage areas",
        fill_color="none",
        fill_opacity=0.0,
        line_color="#27AE60",
        line_weight=1.5,
        show=False,
        tooltip_text="Microgrid coverage area — SGIP + pre-positioned deployments",
    )
    scn_b_cov_layer.add_to(m)

# ── Layer 6 (OFF): Scenario C full exposure zone ─────────────────────────────
scn_c_layer = geojson_layer(
    zone_c_gdf,
    name="Scenario C — Reactive Crisis (full exposure)",
    fill_color="#E74C3C",
    fill_opacity=0.30,
    line_color="#C0392B",
    line_weight=1.0,
    show=False,
    tooltip_text="Reactive Crisis — Full exposure, late-stage remedy only",
)
scn_c_layer.add_to(m)

# ── Layer 7 (OFF): Scenario C emergency microgrids ───────────────────────────
scn_c_em_layer = folium.FeatureGroup(
    name="Scenario C — Emergency microgrids (critical facilities)", show=False
)
popup_em = "Emergency microgrid deployment — reactive, post-crisis"
for _, row in cf_in_c.iterrows():
    if row.geometry is None:
        continue
    folium.CircleMarker(
        location=[row.geometry.y, row.geometry.x],
        radius=4,
        color="#8B0000",
        fill=True,
        fill_color="#8B0000",
        fill_opacity=0.85,
        weight=1,
        tooltip=folium.Tooltip(popup_em),
        popup=folium.Popup(
            f"<b>{row.get('NAME','')}</b><br>{row.get('facility_type','')}<br>"
            f"<i>{popup_em}</i>",
            max_width=220,
        ),
    ).add_to(scn_c_em_layer)
scn_c_em_layer.add_to(m)

# ── Layer 8 (OFF): Critical facilities ───────────────────────────────────────
cf_layer = folium.FeatureGroup(name="Critical Facilities (hospitals + fire stations)", show=False)
for _, row in cf.iterrows():
    if row.geometry is None:
        continue
    ftype = row.get("facility_type", "Hospital")
    tip = (f"<b>{row.get('NAME','')}</b><br>{ftype}<br>"
           f"{row.get('COUNTY','')} County")
    if ftype == "Hospital":
        folium.Marker(
            location=[row.geometry.y, row.geometry.x],
            icon=folium.DivIcon(
                html=(
                    '<div style="font-size:14px;font-weight:900;color:white;'
                    'line-height:1;margin-top:-8px;margin-left:-4px">+</div>'
                ),
                icon_size=(14, 14),
                icon_anchor=(7, 8),
            ),
            tooltip=folium.Tooltip(tip),
        ).add_to(cf_layer)
    else:
        folium.CircleMarker(
            location=[row.geometry.y, row.geometry.x],
            radius=2,
            color="white",
            fill=True,
            fill_color="white",
            fill_opacity=0.6,
            weight=1,
            tooltip=folium.Tooltip(tip),
        ).add_to(cf_layer)
cf_layer.add_to(m)

# ── Layer control ─────────────────────────────────────────────────────────────
folium.LayerControl(collapsed=False, position="topright").add_to(m)
MeasureControl().add_to(m)

# ── Title bar ─────────────────────────────────────────────────────────────────
title_html = """
<div style="position:fixed;top:10px;left:50%;transform:translateX(-50%);
            z-index:9999;background:rgba(10,10,30,0.88);
            padding:9px 22px;border-radius:6px;border:1px solid #555;
            font-size:15px;font-weight:bold;font-family:Arial,sans-serif;
            color:white;letter-spacing:0.3px">
  California Wildfire-Microgrid Resilience Analysis
</div>
"""
m.get_root().html.add_child(folium.Element(title_html))

# ── Info / explanation panel (bottom-left) ───────────────────────────────────
info_html = """
<div style="position:fixed;bottom:30px;left:30px;z-index:1000;
            background:rgba(10,10,30,0.88);padding:14px 18px;border-radius:8px;
            border:1px solid #555;font-size:12px;line-height:1.85;
            color:#e0e0e0;max-width:320px;font-family:Arial,sans-serif">
  <b style="color:white;font-size:13px">How to read this map</b><br>
  <hr style="margin:5px 0;border-color:#555">
  <b style="color:#E74C3C">1.</b> Start with <b>transmission lines</b>
  (on by default) —
  <span style="color:#E74C3C">red</span> = highest wildfire risk<br>
  <b style="color:#aaa">2.</b> Toggle scenarios to compare outage exposure:<br>
  &nbsp;&nbsp;<span style="color:#E74C3C">■</span>
  <b>Scenario C (red)</b> = largest exposure — no pre-built remedy<br>
  &nbsp;&nbsp;<span style="color:#FF8C00">■</span>
  <b>Scenario A (orange)</b> = medium — hardening reduces but
  doesn't eliminate risk<br>
  &nbsp;&nbsp;<span style="color:#3498DB">■</span>
  <b>Scenario B (blue+green)</b> = smallest net exposure — microgrids
  pre-positioned before failure occurs<br>
  <b style="color:#aaa">3.</b> Toggle <b>critical facilities</b> when
  zoomed in to see hospitals and fire stations within outage zones<br>
  <hr style="margin:5px 0;border-color:#555">
  <span style="color:#fff;font-size:10px">★ White-outlined lines = Plan Now corridors
  (click for detail)</span>
</div>
"""
m.get_root().html.add_child(folium.Element(info_html))

out_map_v2 = f"{OUT_MAPS}/wildfire_microgrid_analysis_v2.html"
m.save(out_map_v2)
print(f"  Saved: {out_map_v2}")
fsize_map = os.path.getsize(out_map_v2) / 1e6
print(f"  File size: {fsize_map:.1f} MB")

# ─────────────────────────────────────────────────────────────────────────────
# OUTPUT 2 — Redesigned Prioritisation Scatter
# ─────────────────────────────────────────────────────────────────────────────
print("\nOUTPUT 2: Prioritisation scatter v2")

pm = pd.read_csv(f"{DATA_PROC}/prioritization_matrix.csv")

BG_COLOR = "#1a1a2e"
TIER_FILL = {
    "Plan Now": ("#E74C3C", 1.0),
    "Monitor":  ("#D4A017", 0.60),
    "Defer":    ("#444444", 0.40),
}

fig, ax = plt.subplots(figsize=(14, 10), facecolor=BG_COLOR)
ax.set_facecolor(BG_COLOR)

# ── Quadrant background fills ────────────────────────────────────────────────
ax.fill_between([5, 10], [5, 5], [10, 10],
                color="#E74C3C", alpha=0.05, zorder=0)   # top-right: red
ax.fill_between([0, 5],  [5, 5], [10, 10],
                color="#F39C12", alpha=0.05, zorder=0)   # top-left: yellow
ax.fill_between([5, 10], [0, 0], [5, 5],
                color="#2980B9", alpha=0.05, zorder=0)   # bottom-right: blue
ax.fill_between([0, 5],  [0, 0], [5, 5],
                color="#555555", alpha=0.05, zorder=0)   # bottom-left: grey

# ── Quadrant lines ────────────────────────────────────────────────────────────
ax.axvline(5.0, color="#666", linewidth=1.0, linestyle="--", alpha=0.7, zorder=1)
ax.axhline(5.0, color="#666", linewidth=1.0, linestyle="--", alpha=0.7, zorder=1)

# ── Bubble size scaling ───────────────────────────────────────────────────────
POP_SCALE = 12_000   # divisor for scatter size parameter

def pop_to_size(pop):
    return np.clip(pop, 0, 5e6) / POP_SCALE + 12

# ── Plot tiers in reverse priority so Plan Now is on top ─────────────────────
for tier in ["Defer", "Monitor", "Plan Now"]:
    sub = pm[pm["action_tier"] == tier].copy()
    if len(sub) == 0:
        continue
    fill, alpha = TIER_FILL[tier]
    sizes = pop_to_size(sub["pop_est"].values)

    # Separate by d3_financial for outline styling
    strong  = sub[sub["d3_financial"] >= 6]
    moderate = sub[(sub["d3_financial"] >= 4) & (sub["d3_financial"] < 6)]
    weak    = sub[sub["d3_financial"] < 4]

    for subset, ec, lw in [
        (weak,     fill,      0.0),
        (moderate, "#F1C40F", 1.5),
        (strong,   "#2ECC71", 2.5),
    ]:
        if len(subset) == 0:
            continue
        s_sizes = pop_to_size(subset["pop_est"].values)
        ax.scatter(
            subset["d2_resilience_gap"],
            subset["d1_life_safety"],
            s=s_sizes,
            c=fill,
            alpha=alpha,
            edgecolors=ec,
            linewidths=lw,
            zorder=4 if tier == "Plan Now" else 3,
        )

# ── Quadrant labels (centred in each quadrant) ───────────────────────────────
quad_label_kw = dict(fontsize=13, fontweight="bold", ha="center", va="center",
                     zorder=2)
ax.text(7.5, 7.5, "ACT / PLAN NOW\nHigh need + High gap",
        color="#E74C3C", **quad_label_kw)
ax.text(2.5, 7.5, "PUBLIC FUNDING\nHigh need, existing DER",
        color="#F39C12", **quad_label_kw)
ax.text(7.5, 2.5, "COMMERCIAL\nOPPORTUNITY\nStrong financial case",
        color="#2980B9", **quad_label_kw)
ax.text(2.5, 2.5, "MONITOR\nLower priority",
        color="#888888", **quad_label_kw)

# ── Label top 8 corridors ─────────────────────────────────────────────────────
top8 = pm.nlargest(8, "priority_score")
for _, row in top8.iterrows():
    label = f"{row['county']}\n{row['Owner'][:9]}\n{row['kv_class']}"
    ax.annotate(
        label,
        xy=(row["d2_resilience_gap"], row["d1_life_safety"]),
        xytext=(22, 12),
        textcoords="offset points",
        fontsize=7.5,
        color="white",
        fontweight="bold",
        bbox=dict(boxstyle="round,pad=0.25", fc="#1a1a2e", ec="#666",
                  alpha=0.85, lw=0.8),
        arrowprops=dict(arrowstyle="-", color="white", lw=0.7, alpha=0.7),
        zorder=6,
    )

# ── Annotation insight boxes ──────────────────────────────────────────────────
box_style = dict(boxstyle="round,pad=0.45", fc="#0d0d1f", ec="#888",
                 alpha=0.92, lw=1.0)
ann_kw = dict(fontsize=9, color="white", va="top", bbox=box_style, zorder=7)

# Box 1 — Sierra County (top-right cluster): place annotation in mid-left space
ax.text(0.3, 9.7,
        "Sierra County: highest life\nsafety risk in the state.\nPublic funding priority.",
        ha="left", **ann_kw)

# Box 2 — Tulare/LADWP (bottom-right zone)
ax.text(5.2, 4.7,
        "Tulare/LADWP: strong financial\ncase + high ecological risk.\nD4 score = 7.4",
        ha="left", **ann_kw)

# Box 3 — Most CA corridors (bottom-right cluster label)
ax.text(6.0, 1.4,
        "Most CA corridors: large\nresilience gap, moderate\nlife safety consequence.",
        ha="left", **ann_kw)

# ── Axes styling ──────────────────────────────────────────────────────────────
ax.set_xlim(0, 10)
ax.set_ylim(0, 10)
ax.set_xlabel("Resilience Gap — how much new microgrid capacity is needed",
              fontsize=12, color="white", labelpad=10)
ax.set_ylabel("Life Safety Risk — vulnerability of affected population",
              fontsize=12, color="white", labelpad=10)

ax.tick_params(colors="white", labelsize=10)
for spine in ax.spines.values():
    spine.set_edgecolor("#555")

ax.set_title(
    "California Wildfire-Microgrid Corridor Prioritisation",
    fontsize=16, fontweight="bold", color="white", pad=14,
)
ax.text(
    0.5, 1.025,
    "Each bubble = one transmission corridor (county × utility × voltage class).  "
    "Size = population at risk.  Colour = priority tier.  Green outline = strong financial case.",
    transform=ax.transAxes, ha="center", fontsize=9.5, color="#aaaaaa",
    style="italic",
)

# ── Legend 1: Bubble size ─────────────────────────────────────────────────────
size_handles = [
    ax.scatter([], [], s=pop_to_size(v), c="#888", alpha=0.7, label=lbl)
    for v, lbl in [(1e5, "100K"), (1e6, "1M"), (5e6, "5M+")]
]
leg1 = ax.legend(
    handles=size_handles,
    title="Population at risk",
    title_fontsize=9,
    fontsize=8.5,
    loc="upper left",
    frameon=True,
    framealpha=0.85,
    facecolor="#0d0d1f",
    edgecolor="#666",
    labelcolor="white",
)
leg1.get_title().set_color("white")
ax.add_artist(leg1)

# ── Legend 2: Tier fill + financial outline ───────────────────────────────────
tier_patches = [
    mpatches.Patch(facecolor=TIER_FILL[t][0],
                   alpha=TIER_FILL[t][1],
                   label=t)
    for t in ["Plan Now", "Monitor", "Defer"]
]
outline_handles = [
    Line2D([0], [0], marker="o", color="none",
           markerfacecolor="#888", markeredgecolor="#2ECC71",
           markeredgewidth=2.5, markersize=9,
           label="Strong financial case (D3 ≥ 6)"),
    Line2D([0], [0], marker="o", color="none",
           markerfacecolor="#888", markeredgecolor="#F1C40F",
           markeredgewidth=1.5, markersize=9,
           label="Moderate financial case (D3 4–6)"),
    Line2D([0], [0], marker="o", color="none",
           markerfacecolor="#888", markeredgecolor="#888",
           markeredgewidth=0.5, markersize=9,
           label="Weak financial case (D3 < 4)"),
]
leg2 = ax.legend(
    handles=tier_patches + outline_handles,
    title="Action Tier & Financial Viability",
    title_fontsize=9,
    fontsize=8.5,
    loc="lower left",
    frameon=True,
    framealpha=0.85,
    facecolor="#0d0d1f",
    edgecolor="#666",
    labelcolor="white",
)
leg2.get_title().set_color("white")

plt.tight_layout(rect=[0, 0, 1, 0.97])
out_scatter_v2 = f"{OUT_TABS}/prioritization_matrix_v2.png"
fig.savefig(out_scatter_v2, dpi=300, bbox_inches="tight", facecolor=BG_COLOR)
plt.close(fig)
print(f"  Saved: {out_scatter_v2}")

# ── Report file sizes and dimensions ─────────────────────────────────────────
from PIL import Image
img = Image.open(out_scatter_v2)
w, h = img.size
fsize_scatter = os.path.getsize(out_scatter_v2) / 1e6
print()
print("=" * 60)
print("NEW OUTPUTS COMPLETE")
print("=" * 60)
print(f"  {out_map_v2}")
print(f"    Size: {fsize_map:.1f} MB")
print(f"  {out_scatter_v2}")
print(f"    Dimensions: {w} × {h} px  |  Size: {fsize_scatter:.2f} MB")

# ─────────────────────────────────────────────────────────────────────────────
# RETAINED OUTPUTS — unchanged from original script
# ─────────────────────────────────────────────────────────────────────────────
print("\nRetained outputs (unchanged):")

COL_A = "#2196F3"
COL_B = "#4CAF50"
COL_C = "#F44336"
TIER_COLORS_OLD = {
    "Act Now":  "#B71C1C",
    "Plan Now": "#E64A19",
    "Monitor":  "#FFA000",
    "Defer":    "#388E3C",
}

# ── OUTPUT 3 — Scenario comparison bar chart ──────────────────────────────────
print("  scenario_comparison.png…")
res = pd.read_csv(f"{DATA_PROC}/resilience_financial_summary.csv")
scn_labels = ["A — Fortress Grid", "B — Islands of Power", "C — Reactive Crisis"]
scn_colors = [COL_A, COL_B, COL_C]

pop_aff  = res["Population affected (refined)"].values / 1e6
pop_srv  = res["Population served by microgrids"].values / 1e6
avoid_M  = res["Avoided cost ($M)"].values
net_cost = res["Net capital cost ($M)"].values
npv      = res["20-year NPV ($M)"].values
payback  = res["Payback period (years)"].values

fig, axes = plt.subplots(2, 3, figsize=(16, 9))
fig.suptitle("California Wildfire-Microgrid Analysis — Scenario Comparison",
             fontsize=14, fontweight="bold", y=0.98)

x = np.arange(3)
bar_w = 0.55

ax = axes[0, 0]
ax.bar(x, pop_aff, bar_w, color=scn_colors, alpha=0.4, label="Affected")
ax.bar(x, pop_srv, bar_w, color=scn_colors, alpha=0.9, label="Served by microgrids")
ax.set_title("Population (millions)", fontweight="bold")
ax.set_xticks(x); ax.set_xticklabels(["A", "B", "C"])
ax.set_ylabel("People (M)")
ax.legend(fontsize=8)
for i, (a, s) in enumerate(zip(pop_aff, pop_srv)):
    ax.text(i, s + 0.05, f"{s:.2f}M", ha="center", fontsize=8, fontweight="bold")

ax = axes[0, 1]
ax.bar(x, avoid_M, bar_w, color=scn_colors, alpha=0.85)
ax.set_title("Avoided Economic Cost ($M)", fontweight="bold")
ax.set_xticks(x); ax.set_xticklabels(["A", "B", "C"])
ax.set_ylabel("$M")
for i, v in enumerate(avoid_M):
    ax.text(i, v + 5, f"${v:,.0f}M", ha="center", fontsize=9, fontweight="bold")

ax = axes[0, 2]
ax.bar(x, net_cost, bar_w, color=scn_colors, alpha=0.85)
ax.set_title("Net Capital Cost ($M, after incentives)", fontweight="bold")
ax.set_xticks(x); ax.set_xticklabels(["A", "B", "C"])
ax.set_ylabel("$M")
for i, v in enumerate(net_cost):
    ax.text(i, v + 10, f"${v:,.0f}M", ha="center", fontsize=9, fontweight="bold")

ax = axes[1, 0]
bar_colors_npv = [COL_A if v >= 0 else "#999" for v in npv]
ax.bar(x, npv, bar_w, color=bar_colors_npv, alpha=0.85)
ax.axhline(0, color="black", linewidth=0.8, linestyle="--")
ax.set_title("20-Year NPV ($M)", fontweight="bold")
ax.set_xticks(x); ax.set_xticklabels(["A", "B", "C"])
ax.set_ylabel("$M")
for i, v in enumerate(npv):
    offset = 5 if v >= 0 else -25
    ax.text(i, v + offset, f"${v:,.0f}M", ha="center", fontsize=9, fontweight="bold")

ax = axes[1, 1]
ax.bar(x, payback, bar_w, color=scn_colors, alpha=0.85)
ax.axhline(10, color="red", linewidth=1, linestyle="--", label="10-yr threshold")
ax.set_title("Simple Payback Period (years)", fontweight="bold")
ax.set_xticks(x); ax.set_xticklabels(["A", "B", "C"])
ax.set_ylabel("Years")
ax.legend(fontsize=8)
for i, v in enumerate(payback):
    ax.text(i, v + 0.15, f"{v:.1f}yr", ha="center", fontsize=9, fontweight="bold")

cpp = res["Cost per person protected ($)"].values
ax = axes[1, 2]
ax.bar(x, cpp, bar_w, color=scn_colors, alpha=0.85)
ax.set_title("Net Cost per Person Protected ($)", fontweight="bold")
ax.set_xticks(x); ax.set_xticklabels(["A", "B", "C"])
ax.set_ylabel("$ / person")
for i, v in enumerate(cpp):
    ax.text(i, v + 3, f"${v:,.0f}", ha="center", fontsize=9, fontweight="bold")

for ax in axes.flat:
    ax.tick_params(axis="x", labelsize=10)
    ax.grid(axis="y", alpha=0.3, linestyle="--")
    ax.spines["top"].set_visible(False)
    ax.spines["right"].set_visible(False)

SCN_DESCS = [
    ("A — Fortress Grid",      "Top 10% risk corridors; targeted hardening"),
    ("B — Islands of Power",   "All Critical+High corridors; broad DER deployment"),
    ("C — Reactive Crisis",    "Critical tier only; emergency response posture"),
]
legend_handles = [
    mpatches.Patch(facecolor=c, edgecolor="white", linewidth=0.5,
                   label=f"{name}  —  {desc}")
    for (name, desc), c in zip(SCN_DESCS, scn_colors)
]
fig.legend(handles=legend_handles, loc="lower center", ncol=3, fontsize=9,
           frameon=True, framealpha=0.9, edgecolor="#ccc",
           bbox_to_anchor=(0.5, 0.0))
plt.tight_layout(rect=[0, 0.055, 1, 0.97])
out_scn = f"{OUT_TABS}/scenario_comparison.png"
fig.savefig(out_scn, dpi=300, bbox_inches="tight")
plt.close(fig)
print(f"    Saved: {out_scn}")

# ── OUTPUT 4 — Financial case (2-panel) ───────────────────────────────────────
print("  financial_case.png…")
years = np.arange(0, 21)
fig, (ax_left, ax_right) = plt.subplots(1, 2, figsize=(14, 6))
fig.suptitle("Microgrid Deployment — Financial Case (20-Year Horizon)",
             fontsize=13, fontweight="bold", y=1.02)

for i, (label, color, nc, ann) in enumerate(
    zip(scn_labels, scn_colors, net_cost, res["Annual avoided cost ($M)"].values)):
    cum = np.zeros(21)
    cum[0] = -nc
    for y in range(1, 21):
        cum[y] = cum[y-1] + ann
    ax_left.plot(years, cum, color=color, linewidth=2.5, label=label)

ax_left.axhline(0, color="#333333", linewidth=1.8, linestyle="--", zorder=2)
ax_left.text(1, 30, "Break-even", fontsize=9, color="#333333",
             fontweight="bold", va="bottom")
ax_left.set_title("Cumulative Cash Flow ($M)", fontweight="bold")
ax_left.set_xlabel("Year"); ax_left.set_ylabel("Cumulative $M")
ax_left.legend(fontsize=8, loc="lower right")
ax_left.grid(alpha=0.3, linestyle="--")
ax_left.spines["top"].set_visible(False); ax_left.spines["right"].set_visible(False)

gross = res["Gross capital cost ($M)"].values
sgip_off = res["SGIP Equity offset ($M)"].values
ira   = res["IRA ITC offset ($M)"].values
x = np.arange(3); w = 0.45
ax_right.bar(x, gross, w, color="#BDBDBD", alpha=0.9, label="Gross cost",
             edgecolor=scn_colors, linewidth=2.5)
ax_right.bar(x, -sgip_off, w, bottom=gross, color="#66BB6A", alpha=0.9, label="SGIP Equity offset")
ax_right.bar(x, -ira,  w, bottom=gross - sgip_off, color="#42A5F5", alpha=0.9, label="IRA ITC offset")
y_label_gross = max(gross) * 1.02; y_label_net = max(gross) * 1.12
ax_right.set_ylim(0, max(gross) * 1.28)
for i, (g, nc_val) in enumerate(zip(gross, net_cost)):
    ax_right.text(i, y_label_gross, f"Gross: ${g:,.0f}M", ha="center", fontsize=8, color="#555")
    ax_right.text(i, y_label_net, f"Net: ${nc_val:,.0f}M", ha="center", fontsize=9,
                  fontweight="bold", color=scn_colors[i])
ax_right.set_title("Capital Cost Breakdown ($M)", fontweight="bold")
ax_right.set_xticks(x); ax_right.set_xticklabels(["A", "B", "C"])
ax_right.set_ylabel("$M"); ax_right.legend(fontsize=8)
ax_right.grid(axis="y", alpha=0.3, linestyle="--")
ax_right.spines["top"].set_visible(False); ax_right.spines["right"].set_visible(False)
plt.tight_layout()
out_fin = f"{OUT_TABS}/financial_case.png"
fig.savefig(out_fin, dpi=300, bbox_inches="tight")
plt.close(fig)
print(f"    Saved: {out_fin}")

# ── OUTPUT 5 — Original prioritization scatter (retained) ─────────────────────
print("  prioritization_scatter.png…")
pm2 = pd.read_csv(f"{DATA_PROC}/prioritization_matrix.csv")
fig, ax = plt.subplots(figsize=(11, 8))
for tier, color in TIER_COLORS_OLD.items():
    sub = pm2[pm2["action_tier"] == tier]
    if len(sub) == 0: continue
    ax.scatter(sub["d2_resilience_gap"], sub["d1_life_safety"],
               c=color, s=sub["pop_est"].clip(upper=5e6) / 8000 + 10,
               alpha=0.75, edgecolors="white", linewidths=0.5,
               label=f"{tier} (n={len(sub)})",
               zorder=5 if tier in ("Act Now", "Plan Now") else 3)
top8b = pm2.nlargest(8, "priority_score")
for _, row in top8b.iterrows():
    ax.annotate(
        f"{row['county']}\n{row['Owner'][:8]}\n{row['kv_class']}",
        xy=(row["d2_resilience_gap"], row["d1_life_safety"]),
        xytext=(5, 5), textcoords="offset points",
        fontsize=6.5, color="#333",
        arrowprops=dict(arrowstyle="-", color="#aaa", lw=0.8))
ax.axvline(5.0, color="#999", linewidth=1.0, linestyle="--", alpha=0.6)
ax.axhline(5.0, color="#999", linewidth=1.0, linestyle="--", alpha=0.6)
ax.text(8.5, 9.3, "HIGH PRIORITY ZONE\n(Act/Plan Now)", ha="center",
        fontsize=8.5, color="#B71C1C", fontweight="bold", style="italic")
ax.text(1.5, 0.5, "Monitor / Defer", ha="center", fontsize=8, color="#555", style="italic")
ax.set_xlabel("D2 — Resilience Gap Score (0–10)", fontsize=11)
ax.set_ylabel("D1 — Life Safety Score (0–10)", fontsize=11)
ax.set_title("Corridor Prioritization Matrix\nBubble size ∝ population at risk",
             fontsize=13, fontweight="bold")
ax.set_xlim(0, 10); ax.set_ylim(0, 10)
ax.grid(alpha=0.2, linestyle="--")
ax.spines["top"].set_visible(False); ax.spines["right"].set_visible(False)
for pop_m, lbl in [(1e5, "100K"), (1e6, "1M"), (5e6, "5M+")]:
    ax.scatter([], [], s=min(pop_m, 5e6) / 8000 + 10, c="#999", alpha=0.6, label=lbl)
bubble_leg = ax.legend(title="Population at risk", fontsize=8, title_fontsize=9,
                       loc="upper left",
                       handles=ax.get_legend_handles_labels()[0][-3:],
                       labels=["100K", "1M", "5M+"])
ax.add_artist(bubble_leg)
tier_handles_b = [mpatches.Patch(color=c, label=t)
                  for t, c in TIER_COLORS_OLD.items() if t in pm2["action_tier"].values]
ax.legend(handles=tier_handles_b, title="Action Tier", fontsize=9, title_fontsize=10,
          loc="lower right")
plt.tight_layout()
fig.savefig(f"{OUT_TABS}/prioritization_scatter.png", dpi=300, bbox_inches="tight")
plt.close(fig)
print(f"    Saved: {OUT_TABS}/prioritization_scatter.png")

# ── OUTPUT 6 — Killer slide ───────────────────────────────────────────────────
print("  killer_slide.png…")
ACTION_LABELS = {"A": "Plan — include in capital cycle",
                 "B": "Prioritise — strongest long-run case",
                 "C": "Caution — reactive deployment risk"}
ACTION_CELL   = {"A": ("#FFF8E1","#E65100"),
                 "B": ("#E8F5E9","#1B5E20"),
                 "C": ("#FFF3E0","#BF360C")}
summary_rows = []
for _, row in res.iterrows():
    scn = row["Scenario"].split("—")[0].strip()
    summary_rows.append({
        "Scenario":             row["Scenario"],
        "Population Protected": f"{row['Population served by microgrids']/1e6:.2f}M",
        "Avoided Cost":         f"${row['Avoided cost ($M)']:,.0f}M",
        "Net Capital Cost":     f"${row['Net capital cost ($M)']:,.0f}M",
        "20-yr NPV":            f"${row['20-year NPV ($M)']:,.0f}M",
        "Payback":              f"{row['Payback period (years)']:.1f} yr",
        "Cost / Person":        f"${row['Cost per person protected ($)']:,.0f}",
        "High-SVI Served":      f"{row['% served pop in high-SVI']:.1f}%",
        "Action":               ACTION_LABELS.get(scn, "Review"),
    })
df_sum = pd.DataFrame(summary_rows)
top5 = pm2.nlargest(5, "priority_score")[
    ["county","Owner","kv_class","priority_score","action_tier","d1_life_safety","d2_resilience_gap"]
].copy()
top5.columns = ["County","Owner","kV Class","Priority Score","Tier","D1","D2"]
top5["Priority Score"] = top5["Priority Score"].round(2)
top5["D1"] = top5["D1"].round(1); top5["D2"] = top5["D2"].round(1)

fig = plt.figure(figsize=(16, 11)); fig.patch.set_facecolor("#F5F5F5")
fig.text(0.5, 0.97, "California Wildfire-Microgrid Resilience — Executive Summary",
         ha="center", va="top", fontsize=16, fontweight="bold", color="#1A237E")
fig.text(0.5, 0.938,
         "Three investment scenarios for grid hardening and distributed energy deployment across 58 CA counties",
         ha="center", va="top", fontsize=10, color="#555")
ax_top = fig.add_axes([0.02, 0.63, 0.96, 0.26]); ax_top.axis("off")
col_labels = list(df_sum.columns); cell_data = [list(r) for r in df_sum.itertuples(index=False)]
tbl = ax_top.table(cellText=cell_data, colLabels=col_labels, cellLoc="center", loc="center")
tbl.auto_set_font_size(False); tbl.set_fontsize(9); tbl.scale(1, 2.0)
for j in range(len(col_labels)):
    tbl[0,j].set_facecolor("#1A237E"); tbl[0,j].set_text_props(color="white", fontweight="bold")
for i in range(3):
    for j in range(len(col_labels)):
        tbl[i+1,j].set_facecolor(mcolors.to_rgba([COL_A,COL_B,COL_C][i], alpha=0.15))
    npv_col = col_labels.index("20-yr NPV")
    npv_val = res.iloc[i]["20-year NPV ($M)"]
    tbl[i+1,npv_col].set_facecolor(mcolors.to_rgba("#4CAF50" if npv_val>0 else "#F44336", alpha=0.25))
    act_col = col_labels.index("Action")
    act_bg, act_fg = ACTION_CELL[["A","B","C"][i]]
    tbl[i+1,act_col].set_facecolor(act_bg); tbl[i+1,act_col].set_text_props(fontweight="bold",color=act_fg)
ax_bot = fig.add_axes([0.02, 0.34, 0.96, 0.26]); ax_bot.axis("off")
ax_bot.text(0.0, 1.03, "Top 5 Priority Corridors", transform=ax_bot.transAxes,
            fontsize=11, fontweight="bold", color="#1A237E")
col_labels2 = list(top5.columns); cell_data2 = [list(r) for r in top5.itertuples(index=False)]
tbl2 = ax_bot.table(cellText=cell_data2, colLabels=col_labels2, cellLoc="center", loc="center")
tbl2.auto_set_font_size(False); tbl2.set_fontsize(9); tbl2.scale(1, 2.1)
for j in range(len(col_labels2)):
    tbl2[0,j].set_facecolor("#37474F"); tbl2[0,j].set_text_props(color="white",fontweight="bold")
TIER_BG = {"Act Now":"#FFCDD2","Plan Now":"#FFE0B2","Monitor":"#F9FBE7","Defer":"#E8F5E9"}
tier_col_idx = col_labels2.index("Tier")
for i, (_, r) in enumerate(top5.iterrows()):
    bg = TIER_BG.get(r["Tier"],"#FAFAFA")
    for j in range(len(col_labels2)): tbl2[i+1,j].set_facecolor(bg)
    tbl2[i+1,tier_col_idx].set_text_props(fontweight="bold")
ax_take = fig.add_axes([0.02, 0.09, 0.96, 0.21])
ax_take.set_xlim(0,1); ax_take.set_ylim(0,1); ax_take.set_facecolor("#E8EAF6")
ax_take.tick_params(left=False,bottom=False,labelleft=False,labelbottom=False)
for sp in ax_take.spines.values(): sp.set_edgecolor("#9FA8DA"); sp.set_linewidth(1.2)
ax_take.text(0.01,0.90,"Key Takeaways",transform=ax_take.transAxes,
             fontsize=11,fontweight="bold",color="#1A237E",va="top")
for k, btext in enumerate([
    "Risk is systemic: 98% of CA substations exposed to cascade risk",
    "The financial case is strong: 70\u201383% of gross costs offset by existing incentives (SGIP + IRA ITC)",
    "Sierra County and Tulare/LADWP are the immediate priority corridors for pilot deployment",
]):
    ax_take.text(0.01,0.63-k*0.27,f"\u2022  {btext}",transform=ax_take.transAxes,
                 fontsize=10,fontweight="bold",color="#1A237E",va="center")
fig.text(0.5,0.028,
         "Source: HIFLD Transmission, Census ACS 2021, SGIP, NREL ATB 2023, BEA 2022 GDP | Analysis: 2026",
         ha="center",fontsize=7.5,color="#777")
fig.savefig(f"{OUT_TABS}/killer_slide.png",dpi=300,bbox_inches="tight",facecolor="#F5F5F5")
plt.close(fig)
print(f"    Saved: {OUT_TABS}/killer_slide.png")

# ─────────────────────────────────────────────────────────────────────────────
# OUTPUT V3 A — Revised scatter plot v3 (all fixes applied)
# ─────────────────────────────────────────────────────────────────────────────
print("\nOUTPUT V3 A: Prioritisation scatter v3")

pm_v3 = pd.read_csv(f"{DATA_PROC}/prioritization_matrix.csv")
_BG   = "#1a1a2e"
_TIER = {
    "Plan Now": ("#E74C3C", 1.0),
    "Monitor":  ("#D4A017", 0.60),
    "Defer":    ("#444444", 0.40),
}

def _psz(pop):
    return np.clip(pop, 0, 5e6) / 12_000 + 12

fig3, ax3 = plt.subplots(figsize=(14, 10), facecolor=_BG)
ax3.set_facecolor(_BG)

# Layout: leave headroom for title+subtitle above axes and legend to the right
fig3.subplots_adjust(left=0.09, right=0.82, top=0.89, bottom=0.14)

# Title and subtitle as separate fig.text calls — no ax.set_title → no overlap
fig3.text(0.5, 0.96,
          "California Wildfire-Microgrid Corridor Prioritisation",
          ha="center", va="top", fontsize=16, fontweight="bold", color="white")
fig3.text(0.5, 0.924,
          "Each bubble = one transmission corridor (county × utility × voltage class).  "
          "Size = population at risk.  Colour = action tier.  "
          "Green outline = strong financial case (D3 ≥ 6).",
          ha="center", va="top", fontsize=10, color="#aaaaaa", style="italic")

# Quadrant fills
ax3.fill_between([5, 10], [5, 5], [10, 10], color="#E74C3C", alpha=0.05, zorder=0)
ax3.fill_between([0,  5], [5, 5], [10, 10], color="#F39C12", alpha=0.05, zorder=0)
ax3.fill_between([5, 10], [0, 0], [ 5,  5], color="#2980B9", alpha=0.05, zorder=0)
ax3.fill_between([0,  5], [0, 0], [ 5,  5], color="#555555", alpha=0.05, zorder=0)

# Quadrant dividers
ax3.axvline(5.0, color="#666", linewidth=1.0, linestyle="--", alpha=0.7, zorder=1)
ax3.axhline(5.0, color="#666", linewidth=1.0, linestyle="--", alpha=0.7, zorder=1)

# Bubbles — Defer first (z-order), Plan Now last (on top)
for tier in ["Defer", "Monitor", "Plan Now"]:
    sub = pm_v3[pm_v3["action_tier"] == tier].copy()
    if len(sub) == 0:
        continue
    fc, alpha = _TIER[tier]
    for subset, ec, lw in [
        (sub[sub["d3_financial"] < 4],                          fc,         0.0),
        (sub[(sub["d3_financial"] >= 4) & (sub["d3_financial"] < 6)], "#F1C40F", 1.5),
        (sub[sub["d3_financial"] >= 6],                         "#2ECC71",  2.5),
    ]:
        if len(subset) == 0:
            continue
        ax3.scatter(
            subset["d2_resilience_gap"], subset["d1_life_safety"],
            s=_psz(subset["pop_est"].values),
            c=fc, alpha=alpha, edgecolors=ec, linewidths=lw,
            zorder=4 if tier == "Plan Now" else 3,
        )

# Quadrant labels — TRUE CENTER of each quadrant
_qlbl = dict(fontsize=13, fontweight="bold", ha="center", va="center", zorder=2)
ax3.text(7.5, 7.5, "ACT / PLAN NOW\nHigh need + High gap",        color="#E74C3C", **_qlbl)
ax3.text(2.5, 7.5, "PUBLIC FUNDING\nHigh need, existing DER",     color="#F39C12", **_qlbl)
ax3.text(7.5, 2.5, "COMMERCIAL\nOPPORTUNITY\nStrong financial case", color="#2980B9", **_qlbl)
ax3.text(2.5, 2.5, "MONITOR\nLower priority",                     color="#888888", **_qlbl)

# Top-8 labels — hardcoded overlap-free text positions (data coords)
# Verified against: annotation boxes at (6.5,9.2), (5.2,4.7), (6.0,1.4)
# and inter-label separation ≥ 0.8 units in at least one axis.
top8_v3 = pm_v3.nlargest(8, "priority_score").reset_index(drop=True)
_lxy = [
    (8.1, 9.5),   # Sierra / PLSR   / <115kV  — d2=7.86, d1=7.76
    (7.8, 2.2),   # Tulare / LADWP  / 500kV+  — d2=7.00, d1=3.16
    (5.4, 8.5),   # Sierra / NVENERGY/<115kV  — d2=7.74, d1=7.76
    (8.6, 8.0),   # Sierra / PG&E   / <115kV  — d2=7.74, d1=7.76
    (4.4, 1.8),   # LA    / SCE     / 500kV+  — d2=6.03, d1=2.63
    (8.1, 7.1),   # Modoc / PCORP   /115-229  — d2=8.56, d1=6.34
    (8.2, 4.0),   # Siskiyou/PCORP  /<115kV  — d2=7.71, d1=4.93
    (4.8, 7.4),   # Modoc / BPA     /230-499  — d2=7.13, d1=6.34
]
for idx, (_, row) in enumerate(top8_v3.iterrows()):
    if idx >= len(_lxy):
        break
    xt, yt = _lxy[idx]
    ax3.annotate(
        f"{row['county']}\n{row['Owner'][:8]}\n{row['kv_class']}",
        xy=(row["d2_resilience_gap"], row["d1_life_safety"]),
        xytext=(xt, yt),
        textcoords="data",
        fontsize=7.5, color="white", fontweight="bold",
        bbox=dict(boxstyle="round,pad=0.25", fc=_BG, ec="#666", alpha=0.85, lw=0.8),
        arrowprops=dict(arrowstyle="-", color="white", lw=0.7, alpha=0.7),
        zorder=6,
    )

# Annotation insight boxes
_abox = dict(boxstyle="round,pad=0.45", fc="#0d0d1f", ec="#888", alpha=0.92, lw=1.0)
_akw  = dict(fontsize=9, color="white", va="top", bbox=_abox, zorder=7)

# Box 1 — Sierra County INSIDE top-right quadrant (x=6.5, y=9.2)
ax3.text(6.5, 9.2,
         "Sierra County: highest life\nsafety risk in the state.\nPublic funding priority.",
         ha="left", **_akw)
# Box 2 — Tulare/LADWP
ax3.text(5.2, 4.7,
         "Tulare/LADWP: strong financial\ncase + high ecological risk.\nD4 score = 7.4",
         ha="left", **_akw)
# Box 3 — Most CA corridors
ax3.text(6.0, 1.4,
         "Most CA corridors: large\nresilience gap, moderate\nlife safety consequence.",
         ha="left", **_akw)

# Axis labels — 2 lines each, with directional arrows on second line
ax3.set_xlabel(
    "Resilience Gap Score (D2) — 0 to 10\n"
    "← Low gap (existing DER adequate)          "
    "High gap (new microgrids urgently needed) →",
    fontsize=11, color="white", labelpad=12,
)
ax3.set_ylabel(
    "Life Safety Risk Score (D1) — 0 to 10\n"
    "← Lower consequence          "
    "Higher consequence (vulnerable population) →",
    fontsize=11, color="white", labelpad=12,
)
ax3.set_xlim(0, 10); ax3.set_ylim(0, 10)
ax3.tick_params(colors="white", labelsize=10)
for sp in ax3.spines.values():
    sp.set_edgecolor("#555")

# Legend 1: bubble size — TOP RIGHT, OUTSIDE the axes area
_szh = [ax3.scatter([], [], s=_psz(v), c="#888", alpha=0.7, label=lb)
        for v, lb in [(1e5, "100K"), (1e6, "1M"), (5e6, "5M+")]]
leg3a = ax3.legend(
    handles=_szh,
    title="Population at risk",
    title_fontsize=9, fontsize=8.5,
    loc="upper left",
    bbox_to_anchor=(1.01, 1.0),
    bbox_transform=ax3.transAxes,
    frameon=True, framealpha=0.88,
    facecolor="#0d0d1f", edgecolor="#666",
    labelcolor="white",
)
leg3a.get_title().set_color("white")
ax3.add_artist(leg3a)

# Legend 2: tier + financial viability — bottom right, inside axes
_tp = [mpatches.Patch(facecolor=_TIER[t][0], alpha=_TIER[t][1], label=t)
       for t in ["Plan Now", "Monitor", "Defer"]]
_oh = [
    Line2D([0],[0], marker="o", color="none",
           markerfacecolor="#888", markeredgecolor="#2ECC71",
           markeredgewidth=2.5, markersize=9,
           label="Strong commercial case (D3 ≥ 6)"),
    Line2D([0],[0], marker="o", color="none",
           markerfacecolor="#888", markeredgecolor="#F1C40F",
           markeredgewidth=1.5, markersize=9,
           label="Moderate commercial case (D3 4–6)"),
    Line2D([0],[0], marker="o", color="none",
           markerfacecolor="#888", markeredgecolor="#888",
           markeredgewidth=0.5, markersize=9,
           label="Weak commercial case (D3 < 4)"),
]
leg3b = ax3.legend(
    handles=_tp + _oh,
    title="Action Tier & Financial Viability",
    title_fontsize=9, fontsize=8.5,
    loc="lower right",
    frameon=True, framealpha=0.88,
    facecolor="#0d0d1f", edgecolor="#666",
    labelcolor="white",
)
leg3b.get_title().set_color("white")

out_v3_scatter = f"{OUT_TABS}/prioritization_matrix_v3.png"
fig3.savefig(out_v3_scatter, dpi=300, bbox_inches="tight", facecolor=_BG)
plt.close(fig3)
print(f"  Saved: {out_v3_scatter}")

# ─────────────────────────────────────────────────────────────────────────────
# OUTPUT V3 B — Three scenario maps side-by-side HTML (all fixes applied)
# ─────────────────────────────────────────────────────────────────────────────
print("\nOUTPUT V3 B: Three-map HTML (wildfire_scenarios_v3.html)")

_RCOLS = {"Critical": "#E74C3C", "High": "#E67E22",
           "Moderate": "#F1C40F", "Low": "#95A5A6"}
_RWGTS = {"Critical": 2.0, "High": 1.5, "Moderate": 1.0, "Low": 0.8}

# FIX 2: print exact column names to verify tooltip fields
print("  Risk GDF columns:", [c for c in risk.columns if c != "geometry"])

# Simplified + subsampled transmission lines — keep tooltip properties
print("  Simplifying transmission lines…")
_rsimp = risk.copy()
_rsimp["geometry"]        = _rsimp["geometry"].simplify(0.004, preserve_topology=False)
_rsimp["composite_score"] = _rsimp["composite_score"].round(3)
_rsimp = gpd.GeoDataFrame(
    pd.concat([
        _rsimp[_rsimp["risk_tier"] == "Critical"],
        _rsimp[_rsimp["risk_tier"] == "High"],
        _rsimp[_rsimp["risk_tier"] == "Moderate"].iloc[::2],
        _rsimp[_rsimp["risk_tier"] == "Low"].iloc[::3],
    ]).reset_index(drop=True),
    crs=4326,
)
print(f"  Lines for maps: {len(_rsimp)}")

# FIX 3: validate Scenario C zone — rebuild with debug output
print("\n  FIX 3 — Validating Scenario C zone:")
_crit_v3 = risk[risk["risk_tier"] == "Critical"].copy()
print(f"  Critical segments found: {len(_crit_v3)}")
print(f"  CRS before buffering: {_crit_v3.crs}")
_crit_proj3 = _crit_v3.to_crs(3310)
_c3_bufs = []
for _, _r3 in _crit_proj3.iterrows():
    if _r3.geometry is None or _r3.geometry.is_empty:
        continue
    _buf = _r3.geometry.buffer(kv_to_buffer_m(_r3["kv_num"]))
    _c3_bufs.append(_buf if _buf.is_valid else _buf.buffer(0))
print(f"  Valid individual buffers: {len(_c3_bufs)}")
_c3_union = unary_union(_c3_bufs)
print(f"  Combined buffer area: {_c3_union.area/1e6:,.0f} km²  |  is_valid: {_c3_union.is_valid}")
_c3_simplified = _c3_union.simplify(500, preserve_topology=True)
_zone_c3 = gpd.GeoDataFrame(geometry=[_c3_simplified], crs=3310).to_crs(4326)
print(f"  Zone C CRS after reproject: {_zone_c3.crs}  |  empty: {_zone_c3.geometry.iloc[0].is_empty}")

# Critical facilities inside Zone C
_cf_proj3 = cf.to_crs(3310)
_zc3_proj = _zone_c3.to_crs(3310)
_cf_in_c3 = _cf_proj3[_cf_proj3.within(_zc3_proj.geometry.iloc[0])].to_crs(4326)
print(f"  Critical facilities in C zone: {len(_cf_in_c3)}")

# FIX 4: Census-block-group split (Phase 1 = near Critical, Phase 2 = High-only)
print("\n  FIX 4 — Building CBG Phase split for Scenario B:")
# Full outage zone: Critical + High segments
_ch_proj4 = risk[risk["risk_tier"].isin(["Critical","High"])].to_crs(3310)
_ch4_bufs = []
for _, _r4 in _ch_proj4.iterrows():
    if _r4.geometry is None or _r4.geometry.is_empty: continue
    _b4 = _r4.geometry.buffer(kv_to_buffer_m(_r4["kv_num"]))
    _ch4_bufs.append(_b4 if _b4.is_valid else _b4.buffer(0))
_ch4_union = unary_union(_ch4_bufs).simplify(500, preserve_topology=True)
_zone_b4_full = gpd.GeoDataFrame(geometry=[_ch4_union], crs=3310).to_crs(4326)
_b4_geom = _zone_b4_full.geometry.iloc[0]
print(f"  Full outage zone area: {_ch4_union.area/1e6:,.0f} km²")

# CBG spatial filter (sindex for speed)
print("  Filtering CBGs against outage zone…")
_cbg4 = cbg.copy()
_cbg4["geometry"] = _cbg4["geometry"].simplify(0.005, preserve_topology=False)
_cands_idx = list(_cbg4.sindex.intersection(_b4_geom.bounds))
_cbg4_in = _cbg4.iloc[_cands_idx][_cbg4.iloc[_cands_idx].intersects(_b4_geom)].copy()
print(f"  CBGs in outage zone: {len(_cbg4_in)}")

# Split: Phase-1 covered = near Critical-tier buffers; Phase-2 = High-only
_crit_geom4 = _zone_c3.geometry.iloc[0]          # reuse validated Zone C (Critical buffers)
_cbg4_covered   = _cbg4_in[_cbg4_in.intersects(_crit_geom4)]
_cbg4_uncovered = _cbg4_in[~_cbg4_in.intersects(_crit_geom4)]
print(f"  Phase-1 (near critical, pre-deployed): {len(_cbg4_covered)} CBGs "
      f"({100*len(_cbg4_covered)/max(len(_cbg4_in),1):.0f}%)")
print(f"  Phase-2 (high-tier only, residual):     {len(_cbg4_uncovered)} CBGs "
      f"({100*len(_cbg4_uncovered)/max(len(_cbg4_in),1):.0f}%)")


# ── Shared helpers ────────────────────────────────────────────────────────────
_CA_BOUNDS = [[32.5, -124.5], [42.0, -114.0]]

def _base_map_v3():
    """FIX 1: locked to same CA bounding box for all three maps."""
    _m = folium.Map(
        location=[37.5, -119.5], zoom_start=6,
        tiles="CartoDB dark_matter",
        zoom_control=True, scrollWheelZoom=True,
    )
    _m.fit_bounds(_CA_BOUNDS)
    return _m


def _add_lines_v3(m):
    """FIX 2: GeoJsonTooltip with verified field names (sticky=True, localize=True)."""
    for tier in ["Low", "Moderate", "High", "Critical"]:
        grp = _rsimp[_rsimp["risk_tier"] == tier].copy()
        if len(grp) == 0:
            continue
        c, w = _RCOLS[tier], _RWGTS[tier]
        # Only keep tooltip columns + geometry to keep GeoJSON payload lean
        _tip_cols = [col for col in ["risk_tier", "kV", "Owner", "composite_score"]
                     if col in grp.columns] + ["geometry"]
        folium.GeoJson(
            grp[_tip_cols].to_json(),
            name=f"Lines — {tier}",
            style_function=lambda x, col=c, wgt=w: {
                "color": col, "weight": wgt, "opacity": 0.9,
            },
            tooltip=folium.GeoJsonTooltip(
                fields=["risk_tier", "kV", "Owner", "composite_score"],
                aliases=["Risk Tier:", "Voltage (kV):", "Utility:", "Risk Score:"],
                localize=True,
                sticky=True,
                labels=True,
                style="""
                    background-color: #1a1a2e;
                    color: white;
                    font-family: Arial;
                    font-size: 12px;
                    border: 1px solid #444;
                    border-radius: 4px;
                    padding: 8px;
                """,
            ),
        ).add_to(m)


def _add_textbox_v3(m, html_text):
    m.get_root().html.add_child(folium.Element(
        '<div style="position:fixed;bottom:20px;left:20px;z-index:1000;'
        'background:rgba(10,10,30,0.90);padding:10px 14px;border-radius:6px;'
        'border:1px solid #555;color:#e0e0e0;font-size:11px;max-width:250px;'
        f'font-family:Arial,sans-serif;line-height:1.65">{html_text}</div>'
    ))


def _save_and_iframe_v3(m, fname, height=600):
    """Save map to its own HTML file and return an iframe src= tag.
    Using src= (not srcdoc= or base64 data URIs) preserves JavaScript execution,
    which is required for fit_bounds and GeoJsonTooltip to work correctly."""
    m.fit_bounds(_CA_BOUNDS)
    m.save(fname)
    basename = os.path.basename(fname)
    return (f'<iframe src="{basename}" '
            f'style="width:100%;height:{height}px;border:none;display:block;"></iframe>')


# ── Map A: Fortress Grid ──────────────────────────────────────────────────────
print("\n  Building Map A (Fortress Grid)…")
_ma = _base_map_v3()
_add_lines_v3(_ma)
# FIX 5: #FF8C00 at opacity 0.20  +  TOOLTIP 2
folium.GeoJson(
    zone_a_gdf.to_json(),
    style_function=lambda x: {
        "fillColor": "#FF8C00", "fillOpacity": 0.20,
        "color": "#FF8C00", "weight": 0.8,
    },
    tooltip=folium.Tooltip(
        "Fortress Grid \u2014 Residual outage zone after hardening.<br>"
        "Buffer reduced 25\u201350% from original.<br>"
        "Hardening investment reduces but does not eliminate risk.",
        sticky=False,
    ),
).add_to(_ma)
_add_textbox_v3(
    _ma,
    "Orange zone = outage area <b>after hardening</b>.<br>"
    "Buffer distances shrunk 25–50% from line hardening.<br>"
    "Residual risk persists on highest-voltage corridors.",
)
_ifa = _save_and_iframe_v3(_ma, f"{OUT_MAPS}/scenario_a_temp.html")
print(f"  Map A: {os.path.getsize(f'{OUT_MAPS}/scenario_a_temp.html')//1024} KB")

# ── Map B: Islands of Power ───────────────────────────────────────────────────
print("  Building Map B (Islands of Power)…")
_mb = _base_map_v3()
_add_lines_v3(_mb)

# Full outage footprint — dashed blue outline (FIX 4)
folium.GeoJson(
    _zone_b4_full.to_json(),
    style_function=lambda x: {
        "fillOpacity": 0.0, "color": "#3498DB",
        "weight": 1.5, "dashArray": "6,4", "opacity": 0.5,
    },
).add_to(_mb)
# Phase-2 residual CBGs (High-tier only) — solid blue fill  +  TOOLTIP 4
if len(_cbg4_uncovered) > 0:
    folium.GeoJson(
        _cbg4_uncovered[["geometry"]].to_json(),
        style_function=lambda x: {
            "fillColor": "#3498DB", "fillOpacity": 0.20,
            "color": "#3498DB", "weight": 0.3, "opacity": 0.4,
        },
        tooltip=folium.Tooltip(
            "Residual exposed area \u2014 not yet covered by pre-deployed microgrids.<br>"
            "Represents ~55% of affected load still at risk<br>"
            "under Islands of Power scenario.",
            sticky=False,
        ),
    ).add_to(_mb)
# Phase-1 covered CBGs (near Critical) — green outline  +  TOOLTIP 3
if len(_cbg4_covered) > 0:
    folium.GeoJson(
        _cbg4_covered[["geometry"]].to_json(),
        style_function=lambda x: {
            "fillOpacity": 0.0, "color": "#2ECC71",
            "weight": 1.0, "opacity": 0.4,
        },
        tooltip=folium.Tooltip(
            "Pre-deployed microgrid coverage area.<br>"
            "This block group is covered by existing SGIP storage<br>"
            "or planned deployment on Critical corridors.<br>"
            "Estimated 45% of affected load served locally.",
            sticky=False,
        ),
    ).add_to(_mb)

_add_textbox_v3(
    _mb,
    "Dashed blue = full outage zone (Critical + High corridors).<br>"
    "<span style='color:#2ECC71'>&#9632;</span> Green outline = Phase-1 block groups "
    f"({len(_cbg4_covered):,} CBGs near Critical lines — pre-deployed).<br>"
    "<span style='color:#3498DB'>&#9632;</span> Blue fill = Phase-2 residual "
    f"({len(_cbg4_uncovered):,} CBGs near High-tier lines — still exposed).",
)
_ifb = _save_and_iframe_v3(_mb, f"{OUT_MAPS}/scenario_b_temp.html")
print(f"  Map B: {os.path.getsize(f'{OUT_MAPS}/scenario_b_temp.html')//1024} KB")

# ── Map C: Reactive Crisis ────────────────────────────────────────────────────
print("  Building Map C (Reactive Crisis)…")
_mc = _base_map_v3()
_add_lines_v3(_mc)

# FIX 3: validated Zone C with corrected radius=4  +  TOOLTIP 5
folium.GeoJson(
    _zone_c3.to_json(),
    style_function=lambda x: {
        "fillColor": "#E74C3C", "fillOpacity": 0.30,
        "color": "#C0392B", "weight": 0.8,
    },
    tooltip=folium.Tooltip(
        "Reactive Crisis \u2014 Full exposure zone.<br>"
        "No pre-positioned microgrids at point of failure.<br>"
        "Entire population within this zone loses power<br>"
        "until emergency response arrives.",
        sticky=False,
    ),
).add_to(_mc)
for _, _rcf3 in _cf_in_c3.iterrows():
    if _rcf3.geometry is None:
        continue
    folium.CircleMarker(
        location=[_rcf3.geometry.y, _rcf3.geometry.x],
        radius=4,
        color="#8B0000",
        fill=True, fill_color="#8B0000", fill_opacity=0.85, weight=0.5,
        tooltip="Emergency microgrid deployment site. Reactive \u2014 arrives after crisis onset. Covers critical facility essential load only.",
    ).add_to(_mc)
_add_textbox_v3(
    _mc,
    "Red zone = full population exposure at point of failure.<br>"
    "No pre-positioned microgrids.<br>"
    "Dark circles = emergency deployment at critical facilities "
    "arriving after crisis onset.",
)
_ifc = _save_and_iframe_v3(_mc, f"{OUT_MAPS}/scenario_c_temp.html")
print(f"  Map C: {os.path.getsize(f'{OUT_MAPS}/scenario_c_temp.html')//1024} KB")

# ── Compose final HTML ────────────────────────────────────────────────────────
print("  Composing layout HTML…")
_html_v3 = f"""<!DOCTYPE html>
<html lang="en">
<head>
  <meta charset="utf-8">
  <meta name="viewport" content="width=device-width,initial-scale=1">
  <title>California Wildfire-Microgrid Resilience &#8212; Scenario Comparison</title>
  <style>
    *,*::before,*::after{{box-sizing:border-box}}
    body{{margin:0;padding:20px 24px 28px;background:#1a1a2e;
         font-family:Arial,Helvetica,sans-serif;color:#e0e0e0}}
    .hdr{{text-align:center;margin-bottom:18px}}
    .hdr h1{{font-size:22px;font-weight:bold;margin:0 0 8px;color:#fff}}
    .hdr p{{font-size:13px;color:#aaa;max-width:840px;margin:0 auto;line-height:1.55}}
    .row{{display:flex;gap:10px;margin-bottom:14px}}
    .col{{flex:1;min-width:0}}
    .mtitle{{font-size:13px;font-weight:bold;text-align:center;margin-bottom:4px;
             padding:7px 10px;background:rgba(255,255,255,.06);
             border-radius:5px 5px 0 0;border-bottom:1px solid #333;color:#fff}}
    .msub{{font-size:10.5px;color:#aaa;text-align:center;margin:0 0 6px;
           font-style:italic;line-height:1.4}}
    iframe{{width:100%;height:600px;border:1px solid #333;
            border-radius:0 0 4px 4px;display:block}}
    .leg{{background:rgba(255,255,255,.05);border-radius:6px;
          border:1px solid #333;padding:14px 20px;margin-top:6px}}
    .leg-hdr{{font-size:14px;font-weight:bold;margin:0 0 12px;color:#fff}}
    .leg-grid{{display:grid;grid-template-columns:1fr 1fr;gap:22px}}
    .ls h4{{margin:0 0 8px;font-size:11px;text-transform:uppercase;
            letter-spacing:.05em;color:#888}}
    .li{{display:flex;align-items:flex-start;gap:9px;margin-bottom:6px;font-size:12px}}
    .sl{{display:inline-block;width:30px;border-radius:2px;flex-shrink:0;margin-top:5px}}
    .sf{{display:inline-block;width:14px;height:14px;border-radius:3px;
         flex-shrink:0;margin-top:2px}}
    .sd{{display:inline-block;width:10px;height:10px;border-radius:50%;
         flex-shrink:0;margin-top:3px}}
    .ki{{grid-column:1/-1;background:rgba(231,76,60,.08);
         border-left:3px solid #E74C3C;padding:10px 14px;
         border-radius:0 5px 5px 0;font-size:12.5px;color:#ccc;
         line-height:1.6;margin-top:6px}}
    .ki b{{color:#fff}}
  </style>
</head>
<body>

<div class="hdr">
  <h1>California Wildfire-Microgrid Resilience &#8212; Scenario Comparison</h1>
  <p>All three maps show the same California extent. Hover over any transmission line
  for risk tier, voltage, utility, and risk score.
  <b style="color:#fff">Scenario B (Islands of Power)</b> shows the smallest net
  exposure because microgrids are pre-positioned on critical corridors before failures occur.</p>
</div>

<div class="row">
  <div class="col">
    <div class="mtitle">Scenario A &#8212; Fortress Grid</div>
    <div class="msub">Hardening investment reduces but does not eliminate exposure</div>
    {_ifa}
  </div>
  <div class="col">
    <div class="mtitle">Scenario B &#8212; Islands of Power</div>
    <div class="msub">Pre-positioned microgrids reduce net community exposure</div>
    {_ifb}
  </div>
  <div class="col">
    <div class="mtitle">Scenario C &#8212; Reactive Crisis</div>
    <div class="msub">No pre-built remedy &#8212; full population exposed at point of failure</div>
    {_ifc}
  </div>
</div>

<div class="leg">
  <div class="leg-hdr">Shared Legend</div>
  <div class="leg-grid">

    <div class="ls">
      <h4>Transmission Line Risk Tier (hover for details)</h4>
      <div class="li">
        <span class="sl" style="height:3px;background:#E74C3C"></span>
        <span>Critical &#8212; highest wildfire-driven failure risk</span>
      </div>
      <div class="li">
        <span class="sl" style="height:2px;background:#E67E22"></span>
        <span>High &#8212; elevated risk</span>
      </div>
      <div class="li">
        <span class="sl" style="height:1.5px;background:#F1C40F"></span>
        <span>Moderate</span>
      </div>
      <div class="li">
        <span class="sl" style="height:1px;background:#95A5A6"></span>
        <span>Low</span>
      </div>
    </div>

    <div class="ls">
      <h4>Scenario Exposure Zones</h4>
      <div class="li">
        <span class="sf" style="background:#FF8C00;opacity:.6"></span>
        <span><b>Scenario A</b> &#8212; orange fill = residual outage zone after hardening
        (buffer distances shrunk 25&#8211;50%)</span>
      </div>
      <div class="li">
        <span class="sf" style="background:none;border:1.5px dashed #3498DB"></span>
        <span><b>Scenario B</b> &#8212; dashed blue outline = full outage footprint
        (Critical + High corridors)</span>
      </div>
      <div class="li">
        <span class="sf" style="background:#3498DB;opacity:.5"></span>
        <span><b>Scenario B</b> &#8212; blue fill = Phase-2 census block groups
        (near High-tier lines only, still exposed — {len(_cbg4_uncovered):,} CBGs)</span>
      </div>
      <div class="li">
        <span class="sf" style="background:none;border:1.5px solid #2ECC71"></span>
        <span><b>Scenario B</b> &#8212; green outline = Phase-1 census block groups
        (near Critical lines, microgrids pre-deployed — {len(_cbg4_covered):,} CBGs)</span>
      </div>
      <div class="li">
        <span class="sf" style="background:#E74C3C;opacity:.6"></span>
        <span><b>Scenario C</b> &#8212; red fill = full exposure, no pre-positioned remedy
        ({_c3_union.area/1e6:,.0f} km&#178; outage zone)</span>
      </div>
      <div class="li">
        <span class="sd" style="background:#8B0000"></span>
        <span><b>Scenario C</b> &#8212; dark circles = emergency microgrid at critical
        facilities (reactive &#8212; arrives after crisis onset,
        {len(_cf_in_c3):,} facilities)</span>
      </div>
    </div>

    <div class="ki">
      <b>Reading left to right: exposure shrinks from Scenario C &#8594; A &#8594; B</b>
      as investment becomes more proactive and pre-positioned.
      Scenario B converts {len(_cbg4_covered):,} of {len(_cbg4_in):,} block groups
      ({100*len(_cbg4_covered)/max(len(_cbg4_in),1):.0f}%) from &#8220;full exposure&#8221;
      to &#8220;pre-deployed coverage&#8221; by positioning microgrids on the most
      critical corridors before failures occur.
    </div>

  </div>
</div>

</body>
</html>"""

out_v3_html = f"{OUT_MAPS}/wildfire_scenarios_v3.html"
with open(out_v3_html, "w", encoding="utf-8") as _fh:
    _fh.write(_html_v3)

_fsize_v3h = os.path.getsize(out_v3_html) / 1e6
print(f"  Saved: {out_v3_html}")
print(f"  HTML file size: {_fsize_v3h:.1f} MB")

# ── Step 4: Verification ──────────────────────────────────────────────────────
print("\n  VERIFICATION:")
print(f"    wildfire_scenarios_v3.html : {os.path.getsize(out_v3_html)/1e6:.2f} MB")
print(f"    scenario_a_temp.html       : {os.path.getsize(f'{OUT_MAPS}/scenario_a_temp.html')/1e6:.2f} MB")
print(f"    scenario_b_temp.html       : {os.path.getsize(f'{OUT_MAPS}/scenario_b_temp.html')/1e6:.2f} MB")
print(f"    scenario_c_temp.html       : {os.path.getsize(f'{OUT_MAPS}/scenario_c_temp.html')/1e6:.2f} MB")
print(f"    Bounds locked to: {_CA_BOUNDS}")
print(f"    Map A location  : {_ma.location}")
print(f"    Map B location  : {_mb.location}")
print(f"    Map C location  : {_mc.location}")
print(f"    Transmission GDF columns: {[c for c in risk.columns if c != 'geometry']}")

# ── V3 output summary ─────────────────────────────────────────────────────────
from PIL import Image as _PILv3
_iv3 = _PILv3.open(out_v3_scatter)
_wv3, _hv3 = _iv3.size
_sv3 = os.path.getsize(out_v3_scatter) / 1e6
print()
print("=" * 60)
print("V3 OUTPUTS")
print("=" * 60)
print(f"  {out_v3_scatter}")
print(f"    Dimensions: {_wv3} × {_hv3} px  |  {_sv3:.2f} MB")
print(f"  {out_v3_html}")
print(f"    Size: {_fsize_v3h:.1f} MB")

print("\n" + "=" * 60)
print("ALL OUTPUTS COMPLETE")
print("=" * 60)
