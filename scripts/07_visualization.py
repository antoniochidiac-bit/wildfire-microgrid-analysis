"""
07_visualization.py — Final presentation outputs for wildfire-microgrid analysis
Produces:
  1. outputs/maps/wildfire_microgrid_analysis.html  — interactive folium map
  2. outputs/tables/scenario_comparison.png          — scenario bar chart (300 DPI)
  3. outputs/tables/financial_case.png               — 2-panel financial chart (300 DPI)
  4. outputs/tables/prioritization_scatter.png       — D1 vs D2 scatter with tiers (300 DPI)
  5. outputs/tables/killer_slide.png                 — formatted summary table (300 DPI)
"""

import os, warnings
import numpy as np
import pandas as pd
import geopandas as gpd
import folium
from folium.plugins import MeasureControl
import matplotlib
matplotlib.use("Agg")
import matplotlib.pyplot as plt
import matplotlib.patches as mpatches
import matplotlib.colors as mcolors
from matplotlib.table import Table

warnings.filterwarnings("ignore")

# ── Paths ────────────────────────────────────────────────────────────────────
DATA_PROC = "data/processed"
DATA_RAW  = "data/raw"
OUT_MAPS  = "outputs/maps"
OUT_TABS  = "outputs/tables"

os.makedirs(OUT_MAPS, exist_ok=True)
os.makedirs(OUT_TABS, exist_ok=True)

# ── Color scheme ─────────────────────────────────────────────────────────────
COL_A   = "#2196F3"   # Scenario A — blue
COL_B   = "#4CAF50"   # Scenario B — green
COL_C   = "#F44336"   # Scenario C — red
RISK_COLORS = {
    "Critical": "#B71C1C",
    "High":     "#E64A19",
    "Moderate": "#FFA000",
    "Low":      "#388E3C",
}
TIER_COLORS = {
    "Act Now":   "#B71C1C",
    "Plan Now":  "#E64A19",
    "Monitor":   "#FFA000",
    "Defer":     "#388E3C",
}

# ─────────────────────────────────────────────────────────────────────────────
# OUTPUT 1 — Interactive folium map
# ─────────────────────────────────────────────────────────────────────────────
print("=" * 60)
print("OUTPUT 1: Interactive map")
print("=" * 60)

# --- Load layers ------------------------------------------------------------
print("  Loading transmission risk scores…")
risk = gpd.read_file(f"{DATA_PROC}/transmission_risk_scores.gpkg").to_crs(4326)

print("  Loading FHSZ (decimated)…")
# Sample every 5th row to keep folium snappy (~20MB instead of 102MB)
fhsz_full = gpd.read_file(f"{DATA_RAW}/wildfire_risk/fhsz.gpkg")
fhsz = fhsz_full.iloc[::5].copy().to_crs(4326)
del fhsz_full

print("  Loading critical facilities…")
cf = gpd.read_file(f"{DATA_RAW}/microgrids/critical_facilities.gpkg").to_crs(4326)

print("  Loading scenario C substations…")
c_subs = gpd.read_file(f"{DATA_PROC}/scenario_c_crisis.gpkg",
                        layer="substations").to_crs(4326)
c_segs = gpd.read_file(f"{DATA_PROC}/scenario_c_crisis.gpkg",
                        layer="critical_segments").to_crs(4326)

# Scenario A / B use the same 1,710 high+critical line segments
scn_a = gpd.read_file(f"{DATA_PROC}/scenario_a_fortress.gpkg").to_crs(4326)
scn_b = gpd.read_file(f"{DATA_PROC}/scenario_b_islands.gpkg").to_crs(4326)

# --- Build map --------------------------------------------------------------
ca_center = [37.3, -119.5]
m = folium.Map(location=ca_center, zoom_start=6,
               tiles="CartoDB positron",
               control_scale=True)

# Layer 1 — FHSZ severity zones
FHSZ_PAL = {1: "#FFF9C4", 2: "#FBC02D", 3: "#B71C1C"}  # Moderate/High/VeryHigh
fhsz_layer = folium.FeatureGroup(name="Fire Hazard Severity Zones", show=True)
for _, row in fhsz.iterrows():
    sev = int(row.get("FHSZ", 1))
    color = FHSZ_PAL.get(sev, "#EEEEEE")
    if row.geometry is None:
        continue
    folium.GeoJson(
        row.geometry.__geo_interface__,
        style_function=lambda x, c=color: {
            "fillColor": c, "color": "none", "fillOpacity": 0.35,
        },
    ).add_to(fhsz_layer)
fhsz_layer.add_to(m)

# Layer 2 — Transmission lines coloured by risk tier
risk_layer = folium.FeatureGroup(name="Transmission Lines (risk tier)", show=True)
for _, row in risk.iterrows():
    if row.geometry is None:
        continue
    color = RISK_COLORS.get(row.get("risk_tier", "Low"), "#999999")
    tip = (f"<b>{row.get('Name','')}</b><br>"
           f"Owner: {row.get('Owner','')}<br>"
           f"kV: {row.get('kV','')}<br>"
           f"Risk: {row.get('risk_tier','')}<br>"
           f"Composite: {row.get('composite_score',0):.3f}")
    folium.GeoJson(
        row.geometry.__geo_interface__,
        style_function=lambda x, c=color: {
            "color": c, "weight": 1.5, "opacity": 0.8,
        },
        tooltip=folium.Tooltip(tip),
    ).add_to(risk_layer)
risk_layer.add_to(m)

# Layer 3 — Scenario A: Fortress Grid (top 10% risk, high+critical segments)
def _line_style(color, weight=2.5, dash=""):
    return lambda x: {"color": color, "weight": weight,
                       "opacity": 0.9, "dashArray": dash}

scn_a_layer = folium.FeatureGroup(name="Scenario A — Fortress Grid", show=False)
for _, row in scn_a.iterrows():
    if row.geometry is None: continue
    folium.GeoJson(
        row.geometry.__geo_interface__,
        style_function=_line_style(COL_A, weight=2.5),
        tooltip="Scenario A: targeted hardening",
    ).add_to(scn_a_layer)
scn_a_layer.add_to(m)

# Layer 4 — Scenario B: Islands of Power (all critical+high segments)
scn_b_layer = folium.FeatureGroup(name="Scenario B — Islands of Power", show=False)
for _, row in scn_b.iterrows():
    if row.geometry is None: continue
    folium.GeoJson(
        row.geometry.__geo_interface__,
        style_function=_line_style(COL_B, weight=2.5, dash="6 3"),
        tooltip="Scenario B: broad DER deployment",
    ).add_to(scn_b_layer)
scn_b_layer.add_to(m)

# Layer 5 — Scenario C: Reactive Crisis (684 critical segments + substation risk)
scn_c_layer = folium.FeatureGroup(name="Scenario C — Reactive Crisis", show=False)
for _, row in c_segs.iterrows():
    if row.geometry is None: continue
    folium.GeoJson(
        row.geometry.__geo_interface__,
        style_function=_line_style(COL_C, weight=3.0, dash="2 4"),
        tooltip="Scenario C: critical segment",
    ).add_to(scn_c_layer)
# Mark substations with cascade risk
cascade_subs = c_subs[c_subs.get("cascade_risk", False) == True] if "cascade_risk" in c_subs.columns else c_subs.sample(min(200, len(c_subs)))
for _, row in cascade_subs.iterrows():
    if row.geometry is None: continue
    folium.CircleMarker(
        location=[row.geometry.y, row.geometry.x],
        radius=4, color=COL_C, fill=True, fill_opacity=0.7,
        tooltip=f"Substation: {row.get('NAME', '')}<br>County: {row.get('COUNTY', '')}",
    ).add_to(scn_c_layer)
scn_c_layer.add_to(m)

# Layer 6 — Critical Facilities (Hospitals + Fire Stations) — off by default
cf_layer = folium.FeatureGroup(name="Critical Facilities", show=False)
for _, row in cf.iterrows():
    if row.geometry is None: continue
    ftype = row.get("facility_type", "Hospital")
    tip = (f"<b>{row.get('NAME','')}</b><br>{ftype}<br>"
           f"{row.get('COUNTY','')} County")
    if ftype == "Hospital":
        # Blue + cross marker via DivIcon, radius=5 equivalent
        folium.Marker(
            location=[row.geometry.y, row.geometry.x],
            icon=folium.DivIcon(
                html=(
                    '<div style="font-size:15px;font-weight:900;color:#1565C0;'
                    'text-shadow:-1px 0 white,0 1px white,1px 0 white,0 -1px white;'
                    'line-height:1;margin-top:-8px;margin-left:-4px">+</div>'
                ),
                icon_size=(15, 15),
                icon_anchor=(7, 8),
            ),
            tooltip=folium.Tooltip(tip),
        ).add_to(cf_layer)
    else:
        # Fire station — small semi-transparent orange dot
        folium.CircleMarker(
            location=[row.geometry.y, row.geometry.x],
            radius=3,
            color="orange",
            fill=True,
            fill_opacity=0.4,
            weight=1,
            tooltip=folium.Tooltip(tip),
        ).add_to(cf_layer)
cf_layer.add_to(m)

# Layer control
folium.LayerControl(collapsed=False).add_to(m)
MeasureControl().add_to(m)

# Legend (HTML)
legend_html = """
<div style="position:fixed;bottom:30px;left:30px;z-index:1000;
            background:white;padding:14px 18px;border-radius:8px;
            border:1px solid #ccc;font-size:12px;line-height:1.8;
            box-shadow:2px 2px 6px rgba(0,0,0,0.2)">
<b>Wildfire-Microgrid Analysis</b><br>
<hr style="margin:4px 0">
<b>Risk Tier</b><br>
<span style="color:#B71C1C">&#9644;</span> Critical &nbsp;
<span style="color:#E64A19">&#9644;</span> High<br>
<span style="color:#FFA000">&#9644;</span> Moderate &nbsp;
<span style="color:#388E3C">&#9644;</span> Low<br>
<hr style="margin:4px 0">
<b>Scenarios</b><br>
<span style="color:#2196F3">&#9644;</span> A — Fortress Grid<br>
<span style="color:#4CAF50">&#9644;</span> B — Islands of Power<br>
<span style="color:#F44336">&#9644;</span> C — Reactive Crisis<br>
<hr style="margin:4px 0">
<b>FHSZ</b><br>
<span style="color:#FBC02D">&#9632;</span> High &nbsp;
<span style="color:#B71C1C">&#9632;</span> Very High<br>
<hr style="margin:4px 0">
<span style="color:red">+</span> Hospital &nbsp;
<span style="color:orange">&#9679;</span> Fire Station
</div>
"""
m.get_root().html.add_child(folium.Element(legend_html))

# Title bar
title_html = """
<div style="position:fixed;top:10px;left:50%;transform:translateX(-50%);
            z-index:9999;background:rgba(255,255,255,0.9);
            padding:8px 20px;border-radius:6px;border:1px solid #aaa;
            font-size:15px;font-weight:bold;font-family:Arial,sans-serif">
California Wildfire-Microgrid Resilience Analysis
</div>
"""
m.get_root().html.add_child(folium.Element(title_html))

out_map = f"{OUT_MAPS}/wildfire_microgrid_analysis.html"
m.save(out_map)
print(f"  Saved: {out_map}")

# ─────────────────────────────────────────────────────────────────────────────
# OUTPUT 2 — Scenario comparison bar chart
# ─────────────────────────────────────────────────────────────────────────────
print("\nOUTPUT 2: Scenario comparison chart")

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

# Panel 1: Population affected vs served
ax = axes[0, 0]
bars1 = ax.bar(x, pop_aff, bar_w, color=scn_colors, alpha=0.4, label="Affected")
bars2 = ax.bar(x, pop_srv, bar_w, color=scn_colors, alpha=0.9, label="Served by microgrids")
ax.set_title("Population (millions)", fontweight="bold")
ax.set_xticks(x); ax.set_xticklabels(["A", "B", "C"])
ax.set_ylabel("People (M)")
ax.legend(fontsize=8)
for i, (a, s) in enumerate(zip(pop_aff, pop_srv)):
    ax.text(i, s + 0.05, f"{s:.2f}M", ha="center", fontsize=8, fontweight="bold")

# Panel 2: Avoided cost
ax = axes[0, 1]
ax.bar(x, avoid_M, bar_w, color=scn_colors, alpha=0.85)
ax.set_title("Avoided Economic Cost ($M)", fontweight="bold")
ax.set_xticks(x); ax.set_xticklabels(["A", "B", "C"])
ax.set_ylabel("$M")
for i, v in enumerate(avoid_M):
    ax.text(i, v + 5, f"${v:,.0f}M", ha="center", fontsize=9, fontweight="bold")

# Panel 3: Net capital cost
ax = axes[0, 2]
ax.bar(x, net_cost, bar_w, color=scn_colors, alpha=0.85)
ax.set_title("Net Capital Cost ($M, after incentives)", fontweight="bold")
ax.set_xticks(x); ax.set_xticklabels(["A", "B", "C"])
ax.set_ylabel("$M")
for i, v in enumerate(net_cost):
    ax.text(i, v + 10, f"${v:,.0f}M", ha="center", fontsize=9, fontweight="bold")

# Panel 4: 20-yr NPV
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

# Panel 5: Payback period
ax = axes[1, 1]
ax.bar(x, payback, bar_w, color=scn_colors, alpha=0.85)
ax.axhline(10, color="red", linewidth=1, linestyle="--", label="10-yr threshold")
ax.set_title("Simple Payback Period (years)", fontweight="bold")
ax.set_xticks(x); ax.set_xticklabels(["A", "B", "C"])
ax.set_ylabel("Years")
ax.legend(fontsize=8)
for i, v in enumerate(payback):
    ax.text(i, v + 0.15, f"{v:.1f}yr", ha="center", fontsize=9, fontweight="bold")

# Panel 6: Cost per person
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

# Scenario legend — coloured patches with full names and one-line descriptions
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
fig.legend(
    handles=legend_handles,
    loc="lower center",
    ncol=3,
    fontsize=9,
    frameon=True,
    framealpha=0.9,
    edgecolor="#ccc",
    bbox_to_anchor=(0.5, 0.0),
)

plt.tight_layout(rect=[0, 0.055, 1, 0.97])
out_scn = f"{OUT_TABS}/scenario_comparison.png"
fig.savefig(out_scn, dpi=300, bbox_inches="tight")
plt.close(fig)
print(f"  Saved: {out_scn}")

# ─────────────────────────────────────────────────────────────────────────────
# OUTPUT 3 — Financial case (2-panel)
# ─────────────────────────────────────────────────────────────────────────────
print("\nOUTPUT 3: Financial case chart")

# Build cumulative cash flow over 20 years for each scenario
years = np.arange(0, 21)
fig, (ax_left, ax_right) = plt.subplots(1, 2, figsize=(14, 6))
fig.suptitle("Microgrid Deployment — Financial Case (20-Year Horizon)",
             fontsize=13, fontweight="bold", y=1.02)

for i, (label, color, nc, ann) in enumerate(
    zip(scn_labels, scn_colors,
        net_cost,
        res["Annual avoided cost ($M)"].values)):

    # Year 0: outlay = -net_cost; Year 1+: +annual avoided
    cum = np.zeros(21)
    cum[0] = -nc
    for y in range(1, 21):
        cum[y] = cum[y-1] + ann
    ax_left.plot(years, cum, color=color, linewidth=2.5, label=label)

# Break-even line — drawn once outside the loop, prominently labelled
ax_left.axhline(0, color="#333333", linewidth=1.8, linestyle="--", zorder=2)
ax_left.text(1, 30, "Break-even", fontsize=9, color="#333333",
             fontweight="bold", va="bottom")

ax_left.set_title("Cumulative Cash Flow ($M)", fontweight="bold")
ax_left.set_xlabel("Year")
ax_left.set_ylabel("Cumulative $M")
ax_left.legend(fontsize=8, loc="lower right")
ax_left.grid(alpha=0.3, linestyle="--")
ax_left.spines["top"].set_visible(False)
ax_left.spines["right"].set_visible(False)

# Right panel: cost breakdown stacked bar
gross = res["Gross capital cost ($M)"].values
sgip  = res["SGIP Equity offset ($M)"].values
ira   = res["IRA ITC offset ($M)"].values
net   = res["Net capital cost ($M)"].values

x = np.arange(3)
w = 0.45
# Scenario-coloured edges on gross bars for A/B/C consistency
ax_right.bar(x, gross, w, color="#BDBDBD", alpha=0.9, label="Gross cost",
             edgecolor=scn_colors, linewidth=2.5)
ax_right.bar(x, -sgip, w, bottom=gross, color="#66BB6A", alpha=0.9, label="SGIP Equity offset")
ax_right.bar(x, -ira,  w, bottom=gross - sgip, color="#42A5F5", alpha=0.9, label="IRA ITC offset")

# Labels above all bars — no overlap with colour blocks
y_label_gross = max(gross) * 1.02
y_label_net   = max(gross) * 1.12
ax_right.set_ylim(0, max(gross) * 1.28)
for i, (g, nc_val) in enumerate(zip(gross, net)):
    ax_right.text(i, y_label_gross, f"Gross: ${g:,.0f}M",
                  ha="center", fontsize=8, color="#555")
    ax_right.text(i, y_label_net, f"Net: ${nc_val:,.0f}M",
                  ha="center", fontsize=9, fontweight="bold", color=scn_colors[i])

ax_right.set_title("Capital Cost Breakdown ($M)", fontweight="bold")
ax_right.set_xticks(x)
ax_right.set_xticklabels(["A", "B", "C"])
ax_right.set_ylabel("$M")
ax_right.legend(fontsize=8)
ax_right.grid(axis="y", alpha=0.3, linestyle="--")
ax_right.spines["top"].set_visible(False)
ax_right.spines["right"].set_visible(False)

plt.tight_layout()
out_fin = f"{OUT_TABS}/financial_case.png"
fig.savefig(out_fin, dpi=300, bbox_inches="tight")
plt.close(fig)
print(f"  Saved: {out_fin}")

# ─────────────────────────────────────────────────────────────────────────────
# OUTPUT 4 — Prioritization scatter plot
# ─────────────────────────────────────────────────────────────────────────────
print("\nOUTPUT 4: Prioritization scatter")

pm = pd.read_csv(f"{DATA_PROC}/prioritization_matrix.csv")

fig, ax = plt.subplots(figsize=(11, 8))

# Plot by tier
for tier, color in TIER_COLORS.items():
    sub = pm[pm["action_tier"] == tier]
    if len(sub) == 0:
        continue
    ax.scatter(
        sub["d2_resilience_gap"], sub["d1_life_safety"],
        c=color, s=sub["pop_est"].clip(upper=5e6) / 8000 + 10,
        alpha=0.75, edgecolors="white", linewidths=0.5,
        label=f"{tier} (n={len(sub)})",
        zorder=5 if tier in ("Act Now", "Plan Now") else 3,
    )

# Label top 8 corridors
top8 = pm.nlargest(8, "priority_score")
for _, row in top8.iterrows():
    ax.annotate(
        f"{row['county']}\n{row['Owner'][:8]}\n{row['kv_class']}",
        xy=(row["d2_resilience_gap"], row["d1_life_safety"]),
        xytext=(5, 5), textcoords="offset points",
        fontsize=6.5, color="#333",
        arrowprops=dict(arrowstyle="-", color="#aaa", lw=0.8),
    )

# Quadrant lines at midpoint (5.0)
ax.axvline(5.0, color="#999", linewidth=1.0, linestyle="--", alpha=0.6)
ax.axhline(5.0, color="#999", linewidth=1.0, linestyle="--", alpha=0.6)

# Quadrant labels
ax.text(1.5, 9.3, "High Life Safety\nLow Resilience Gap", ha="center",
        fontsize=8, color="#555", style="italic")
ax.text(8.5, 9.3, "HIGH PRIORITY ZONE\n(Act/Plan Now)", ha="center",
        fontsize=8.5, color="#B71C1C", fontweight="bold", style="italic")
ax.text(1.5, 0.5, "Monitor / Defer", ha="center",
        fontsize=8, color="#555", style="italic")
ax.text(8.5, 0.5, "High Resilience Gap\nLow Life Safety", ha="center",
        fontsize=8, color="#555", style="italic")

ax.set_xlabel("D2 — Resilience Gap Score (0–10)", fontsize=11)
ax.set_ylabel("D1 — Life Safety Score (0–10)", fontsize=11)
ax.set_title("Corridor Prioritization Matrix\n"
             "Bubble size ∝ population at risk",
             fontsize=13, fontweight="bold")
ax.set_xlim(0, 10); ax.set_ylim(0, 10)
ax.legend(title="Action Tier", fontsize=9, title_fontsize=10,
          loc="lower right")
ax.grid(alpha=0.2, linestyle="--")
ax.spines["top"].set_visible(False)
ax.spines["right"].set_visible(False)

# Bubble size legend
for pop_m, label in [(1e5, "100K"), (1e6, "1M"), (5e6, "5M+")]:
    ax.scatter([], [], s=min(pop_m, 5e6) / 8000 + 10,
               c="#999", alpha=0.6, label=label)
bubble_leg = ax.legend(title="Population at risk", fontsize=8,
                       title_fontsize=9, loc="upper left",
                       handles=ax.get_legend_handles_labels()[0][-3:],
                       labels=["100K", "1M", "5M+"])
ax.add_artist(bubble_leg)
# Restore tier legend
tier_handles = [mpatches.Patch(color=c, label=t)
                for t, c in TIER_COLORS.items()
                if t in pm["action_tier"].values]
ax.legend(handles=tier_handles, title="Action Tier", fontsize=9,
          title_fontsize=10, loc="lower right")

plt.tight_layout()
out_scatter = f"{OUT_TABS}/prioritization_scatter.png"
fig.savefig(out_scatter, dpi=300, bbox_inches="tight")
plt.close(fig)
print(f"  Saved: {out_scatter}")

# ─────────────────────────────────────────────────────────────────────────────
# OUTPUT 5 — Killer summary slide (formatted table)
# ─────────────────────────────────────────────────────────────────────────────
print("\nOUTPUT 5: Killer summary table")

# Per-scenario action labels and cell colours
ACTION_LABELS = {
    "A": "Plan — include in capital cycle",
    "B": "Prioritise — strongest long-run case",
    "C": "Caution — reactive deployment risk",
}
ACTION_CELL = {
    "A": ("#FFF8E1", "#E65100"),   # amber bg, dark-orange text
    "B": ("#E8F5E9", "#1B5E20"),   # green bg, dark-green text
    "C": ("#FFF3E0", "#BF360C"),   # orange bg, dark-red text
}

# Build a compact summary DataFrame
summary_rows = []
for _, row in res.iterrows():
    scn = row["Scenario"].split("—")[0].strip()   # "A", "B", "C"
    summary_rows.append({
        "Scenario":            row["Scenario"],
        "Population Protected": f"{row['Population served by microgrids'] / 1e6:.2f}M",
        "Avoided Cost":        f"${row['Avoided cost ($M)']:,.0f}M",
        "Net Capital Cost":    f"${row['Net capital cost ($M)']:,.0f}M",
        "20-yr NPV":           f"${row['20-year NPV ($M)']:,.0f}M",
        "Payback":             f"{row['Payback period (years)']:.1f} yr",
        "Cost / Person":       f"${row['Cost per person protected ($)']:,.0f}",
        "High-SVI Served":     f"{row['% served pop in high-SVI']:.1f}%",
        "Action":              ACTION_LABELS.get(scn, "Review"),
    })

df_sum = pd.DataFrame(summary_rows)

# Add top 5 priority corridors block
top5 = pm.nlargest(5, "priority_score")[
    ["county", "Owner", "kv_class", "priority_score", "action_tier",
     "d1_life_safety", "d2_resilience_gap"]
].copy()
top5.columns = ["County", "Owner", "kV Class", "Priority Score", "Tier", "D1", "D2"]
top5["Priority Score"] = top5["Priority Score"].round(2)
top5["D1"] = top5["D1"].round(1)
top5["D2"] = top5["D2"].round(1)

# Plot
fig = plt.figure(figsize=(16, 11))
fig.patch.set_facecolor("#F5F5F5")

# Title
fig.text(0.5, 0.97, "California Wildfire-Microgrid Resilience — Executive Summary",
         ha="center", va="top", fontsize=16, fontweight="bold", color="#1A237E")
fig.text(0.5, 0.938,
         "Three investment scenarios for grid hardening and distributed energy deployment across 58 CA counties",
         ha="center", va="top", fontsize=10, color="#555")

# ---- Scenario summary table (top) ----
ax_top = fig.add_axes([0.02, 0.63, 0.96, 0.26])
ax_top.axis("off")

col_labels = list(df_sum.columns)
cell_data  = [list(r) for r in df_sum.itertuples(index=False)]

tbl = ax_top.table(
    cellText=cell_data,
    colLabels=col_labels,
    cellLoc="center",
    loc="center",
)
tbl.auto_set_font_size(False)
tbl.set_fontsize(9)
tbl.scale(1, 2.0)

# Header styling
for j in range(len(col_labels)):
    tbl[0, j].set_facecolor("#1A237E")
    tbl[0, j].set_text_props(color="white", fontweight="bold")

# Row styling by scenario
row_colors = [COL_A, COL_B, COL_C]
alphas     = [0.12, 0.12, 0.12]
for i in range(3):
    for j in range(len(col_labels)):
        tbl[i+1, j].set_facecolor(
            mcolors.to_rgba(row_colors[i], alpha=0.15))
    # Highlight NPV cell green/red
    npv_col = col_labels.index("20-yr NPV")
    npv_val = res.iloc[i]["20-year NPV ($M)"]
    tbl[i+1, npv_col].set_facecolor(
        mcolors.to_rgba("#4CAF50" if npv_val > 0 else "#F44336", alpha=0.25))
    # Highlight Action cell with per-scenario colour
    act_col = col_labels.index("Action")
    act_bg, act_fg = ACTION_CELL[["A", "B", "C"][i]]
    tbl[i+1, act_col].set_facecolor(act_bg)
    tbl[i+1, act_col].set_text_props(fontweight="bold", color=act_fg)

# ---- Top-5 priority corridors table (middle) ----
ax_bot = fig.add_axes([0.02, 0.34, 0.96, 0.26])
ax_bot.axis("off")

ax_bot.text(0.0, 1.03, "Top 5 Priority Corridors",
            transform=ax_bot.transAxes,
            fontsize=11, fontweight="bold", color="#1A237E")

col_labels2 = list(top5.columns)
cell_data2  = [list(r) for r in top5.itertuples(index=False)]

tbl2 = ax_bot.table(
    cellText=cell_data2,
    colLabels=col_labels2,
    cellLoc="center",
    loc="center",
)
tbl2.auto_set_font_size(False)
tbl2.set_fontsize(9)
tbl2.scale(1, 2.1)

for j in range(len(col_labels2)):
    tbl2[0, j].set_facecolor("#37474F")
    tbl2[0, j].set_text_props(color="white", fontweight="bold")

TIER_BG = {
    "Act Now":  "#FFCDD2",
    "Plan Now": "#FFE0B2",
    "Monitor":  "#F9FBE7",
    "Defer":    "#E8F5E9",
}
tier_col_idx = col_labels2.index("Tier")
for i, (_, r) in enumerate(top5.iterrows()):
    bg = TIER_BG.get(r["Tier"], "#FAFAFA")
    for j in range(len(col_labels2)):
        tbl2[i+1, j].set_facecolor(bg)
    # Bold the tier cell
    tbl2[i+1, tier_col_idx].set_text_props(fontweight="bold")

# ---- Key Takeaways section ----
ax_take = fig.add_axes([0.02, 0.09, 0.96, 0.21])
ax_take.set_xlim(0, 1)
ax_take.set_ylim(0, 1)
ax_take.set_facecolor("#E8EAF6")
ax_take.tick_params(left=False, bottom=False, labelleft=False, labelbottom=False)
for sp in ax_take.spines.values():
    sp.set_edgecolor("#9FA8DA")
    sp.set_linewidth(1.2)

ax_take.text(0.01, 0.90, "Key Takeaways",
             transform=ax_take.transAxes, fontsize=11, fontweight="bold",
             color="#1A237E", va="top")

_BULLETS = [
    "Risk is systemic: 98% of CA substations exposed to cascade risk",
    "The financial case is strong: 70\u201383% of gross costs offset by existing incentives (SGIP + IRA ITC)",
    "Sierra County and Tulare/LADWP are the immediate priority corridors for pilot deployment",
]
for k, btext in enumerate(_BULLETS):
    ax_take.text(0.01, 0.63 - k * 0.27, f"\u2022  {btext}",
                 transform=ax_take.transAxes, fontsize=10, fontweight="bold",
                 color="#1A237E", va="center")

# Footer
fig.text(0.5, 0.028,
         "Source: HIFLD Transmission, Census ACS 2021, SGIP, NREL ATB 2023, BEA 2022 GDP | Analysis: 2026",
         ha="center", fontsize=7.5, color="#777")

out_killer = f"{OUT_TABS}/killer_slide.png"
fig.savefig(out_killer, dpi=300, bbox_inches="tight", facecolor="#F5F5F5")
plt.close(fig)
print(f"  Saved: {out_killer}")

# ─────────────────────────────────────────────────────────────────────────────
print("\n" + "=" * 60)
print("ALL OUTPUTS COMPLETE")
print("=" * 60)
print(f"  {OUT_MAPS}/wildfire_microgrid_analysis.html")
print(f"  {OUT_TABS}/scenario_comparison.png")
print(f"  {OUT_TABS}/financial_case.png")
print(f"  {OUT_TABS}/prioritization_scatter.png")
print(f"  {OUT_TABS}/killer_slide.png")
