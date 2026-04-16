"""
05_microgrid_resilience.py
Microgrid resilience counterfactual analysis.

Uses data/processed/impact_summary_refined.csv as the base impact input.

Steps:
  1  Existing DER coverage (SGIP data mapped to counties/CBGs)
  2  Microgrid deployment scenarios (islanding fractions per scenario)
  3  Load islanding — revised person-hours and economic cost
  4  Microgrid capital cost with incentive stacking (SGIP Equity + IRA ITC)
  5  Net benefit, payback period, 20-year NPV, cost per person protected
  6  SVI equity overlay (% of served population in highest SVI quartile)
  7  Final table + save data/processed/resilience_financial_summary.csv

Islanding fractions by scenario:
  A — Fortress Grid      15%  (top 10% risk corridors only)
  B — Islands of Power   45%  (all Critical + High corridors)
  C — Reactive Crisis    20%  (critical facilities + highest SVI areas)
"""

import warnings
warnings.filterwarnings("ignore")

import re, time
import numpy as np
import pandas as pd
import geopandas as gpd
from pathlib import Path

ROOT      = Path(__file__).resolve().parent.parent
REFINED_F = ROOT / "data/processed/impact_summary_refined.csv"
SCN_A_F   = ROOT / "data/processed/scenario_a_fortress.gpkg"
SCN_B_F   = ROOT / "data/processed/scenario_b_islands.gpkg"
SCN_C_F   = ROOT / "data/processed/scenario_c_crisis.gpkg"
SGIP_F    = ROOT / "data/raw/microgrids/sgip_data.csv"
SVI_F     = ROOT / "data/raw/census/svi_california_2022.csv"
NREL_F    = ROOT / "data/raw/financial/nrel_microgrid_costs.csv"
INC_F     = ROOT / "data/raw/financial/incentive_programs.csv"
CBG_F     = ROOT / "data/raw/census/census_block_groups.gpkg"
OUT_F     = ROOT / "data/processed/resilience_financial_summary.csv"

# Financial constants
KWH_VOLL       = 9.0
KWH_PER_HH     = 1.5
PERSONS_PER_HH = 2.5
OUTAGE_YRS     = 5          # 1 major outage event per N years
DISCOUNT_RATE  = 0.05
NPV_YEARS      = 20

# Scenario-level parameters
SCENARIOS = {
    "A — Fortress Grid": {
        "islanding_frac"  : 0.15,
        "hftd_frac"       : 0.70,  # fraction of deployment in HFTD zones
        "deployment_note" : "Top 10% risk corridors; targeted hardening",
    },
    "B — Islands of Power": {
        "islanding_frac"  : 0.45,
        "hftd_frac"       : 0.60,  # broader coverage, slightly lower HFTD share
        "deployment_note" : "All Critical+High corridors; broad DER deployment",
    },
    "C — Reactive Crisis": {
        "islanding_frac"  : 0.20,
        "hftd_frac"       : 0.80,  # explicitly targets HFTD/high-SVI areas
        "deployment_note" : "Emergency deployment at critical facilities + high-SVI",
    },
}

# CA county FIPS ↔ name (from 02_risk_scoring.py)
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

t0 = time.time()

# ══════════════════════════════════════════════════════════════════════════════
# LOAD BASE DATA
# ══════════════════════════════════════════════════════════════════════════════
print("Loading base data...", flush=True)

refined = pd.read_csv(REFINED_F)
refined = refined.set_index("Scenario")
print(f"  Refined impact rows: {len(refined)}")
print(f"  Scenarios: {list(refined.index)}")

nrel = pd.read_csv(NREL_F)
inc  = pd.read_csv(INC_F)

# Base technology: Solar+Storage
tech = nrel[nrel["Technology"] == "Solar_plus_Storage"].iloc[0]
COST_PER_KW  = float(tech["Cost_per_kW"])
LIFETIME_YRS = float(tech["Lifetime_years"])
# Storage fraction of the bundle (Li-Ion cost / Solar+Storage cost)
li_ion_cost    = float(nrel[nrel["Technology"] == "Li_Ion_Battery_4hr"]["Cost_per_kW"].iloc[0])
STORAGE_FRAC   = li_ion_cost / COST_PER_KW          # 1200/1800 = 0.667
print(f"  Base tech: {tech['Technology']}  ${COST_PER_KW:,.0f}/kW  "
      f"  Storage fraction: {STORAGE_FRAC:.1%}  Lifetime: {LIFETIME_YRS:.0f}yr")

# Incentive rates (from incentive_programs.csv)
sgip_equity_cov = float(inc.loc[inc["Program"] == "SGIP_Equity",    "Max_Coverage_pct"].iloc[0]) / 100
ira_itc_cov     = float(inc.loc[inc["Program"] == "IRA_ITC_Base",   "Max_Coverage_pct"].iloc[0]) / 100
print(f"  SGIP Equity: {sgip_equity_cov:.0%} on storage  |  IRA ITC: {ira_itc_cov:.0%} on solar+storage")

# Scenario segment files → county lists
scn_a_segs = gpd.read_file(SCN_A_F)[["county","kv_num","line_length_km"]]
scn_b_segs = scn_a_segs.copy()   # same segments
scn_c_segs = gpd.read_file(SCN_C_F, layer="critical_segments")[["county","kv_num","line_length_km"]]
segment_map = {
    "A — Fortress Grid"  : scn_a_segs,
    "B — Islands of Power": scn_b_segs,
    "C — Reactive Crisis" : scn_c_segs,
}
print(f"  Scenario A/B segments: {len(scn_a_segs):,}  |  Scenario C: {len(scn_c_segs):,}")


# ══════════════════════════════════════════════════════════════════════════════
# STEP 1 — EXISTING DER COVERAGE
# ══════════════════════════════════════════════════════════════════════════════
print("\n" + "="*70)
print("Step 1 — Existing DER Coverage (SGIP)")
print("="*70)

sgip_raw = pd.read_csv(SGIP_F, low_memory=False)

# Active/completed storage projects only
sgip = sgip_raw[
    sgip_raw["Fully Qualified State"].isin(["Payment Completed", "RRF Confirmed"]) &
    sgip_raw["Equipment Type"].str.contains("Storage", na=False)
].copy()
print(f"  Active storage projects: {len(sgip):,}  "
      f"Total capacity: {sgip['Rated Capacity [kW]'].sum():,.0f} kW  "
      f"/ {sgip['Energy Storage Capacity (kWh)'].sum():,.0f} kWh", flush=True)

# ── Normalise county names ─────────────────────────────────────────────────
COUNTY_FIXES = {
    "Los Angeled": "Los Angeles",
    "Los Angeles County": "Los Angeles",
    "San Diego County": "San Diego",
    "Orange County": "Orange",
    "Ca": None, "Usa": None,
}

def clean_county(s):
    if pd.isna(s): return None
    s = str(s).strip()
    s = re.sub(r"\s+[Cc]ounty$", "", s)
    s = s.title().strip()
    return COUNTY_FIXES.get(s, s)

sgip["county_std"] = sgip["County"].apply(clean_county)
sgip["county_fips"] = sgip["county_std"].map(CA_NAME_TO_FIPS)
sgip_ca = sgip[sgip["county_fips"].notna()].copy()

# Aggregate by county
sgip_by_county = sgip_ca.groupby("county_fips").agg(
    sgip_projects        = ("Rated Capacity [kW]", "count"),
    sgip_capacity_kw     = ("Rated Capacity [kW]", "sum"),
    sgip_capacity_kwh    = ("Energy Storage Capacity (kWh)", "sum"),
    hftd_projects        = ("Located in HFTD (Tier 2, Tier 3)",
                             lambda x: (x.isin(["Tier 2", "Tier 3"])).sum()),
).reset_index()

n_counties_with_sgip = sgip_by_county["county_fips"].nunique()
print(f"  CA counties with SGIP storage: {n_counties_with_sgip}/58  "
      f"(total {sgip_ca['Rated Capacity [kW]'].sum():,.0f} kW matched to county)")

# ── Compute DER coverage per scenario ─────────────────────────────────────
print("\n  DER coverage by scenario:")

der_results = {}
for lbl, segs in segment_map.items():
    # Affected counties in this scenario
    aff_counties_names = segs["county"].dropna().unique()
    aff_fips = {CA_NAME_TO_FIPS[c] for c in aff_counties_names if c in CA_NAME_TO_FIPS}

    # Which affected counties have SGIP storage?
    counties_w_der = set(sgip_by_county["county_fips"]).intersection(aff_fips)

    # Population denominator: use refined affected population
    ref_pop = int(refined.loc[lbl, "Population affected (refined)"])

    # CBG population breakdown by county — use county-level split of refined population
    # Approximate: since population is distributed uniformly across counties in zone
    # use segment count per county as proxy weight
    seg_by_county = segs[segs["county"].map(CA_NAME_TO_FIPS).isin(aff_fips)].copy()
    seg_by_county["county_fips"] = seg_by_county["county"].map(CA_NAME_TO_FIPS)
    cnt_per_county = seg_by_county.groupby("county_fips").size()
    total_segs = cnt_per_county.sum()

    # Fraction of segments in counties with DER
    segs_in_der_counties = cnt_per_county[cnt_per_county.index.isin(counties_w_der)].sum()
    pct_pop_with_der = segs_in_der_counties / total_segs if total_segs > 0 else 0

    # SGIP capacity in affected counties
    sgip_in_zone = sgip_by_county[sgip_by_county["county_fips"].isin(aff_fips)]
    total_kw  = sgip_in_zone["sgip_capacity_kw"].sum()
    total_kwh = sgip_in_zone["sgip_capacity_kwh"].sum()

    der_results[lbl] = {
        "affected_counties"    : len(aff_fips),
        "counties_with_der"    : len(counties_w_der),
        "pct_counties_with_der": len(counties_w_der) / len(aff_fips) if aff_fips else 0,
        "pct_pop_with_der"     : pct_pop_with_der,
        "existing_sgip_kw"     : round(total_kw, 0),
        "existing_sgip_kwh"    : round(total_kwh, 0),
    }
    print(f"  {lbl[:22]:<22}  "
          f"Counties: {len(aff_fips)} ({len(counties_w_der)} w/DER = "
          f"{len(counties_w_der)/len(aff_fips):.0%})  "
          f"Existing: {total_kw/1e3:,.0f} MW  "
          f"Pop w/DER: {pct_pop_with_der:.0%}")


# ══════════════════════════════════════════════════════════════════════════════
# STEPS 2–5 — MICROGRID SIZING, COSTS, BENEFITS
# ══════════════════════════════════════════════════════════════════════════════
print("\n" + "="*70)
print("Steps 2–5 — Deployment, Costs, Benefits, NPV")
print("="*70)

# Annuity factor: PV of $1/year for NPV_YEARS at DISCOUNT_RATE
annuity_factor = (1 - (1 + DISCOUNT_RATE) ** -NPV_YEARS) / DISCOUNT_RATE

financial = {}

for lbl, params in SCENARIOS.items():
    if lbl not in refined.index:
        print(f"  WARNING: {lbl!r} not in refined impact CSV — skipping")
        continue

    row = refined.loc[lbl]
    iso   = params["islanding_frac"]
    hftd  = params["hftd_frac"]

    orig_pop       = int(row["Population affected (refined)"])
    orig_phrs      = int(row["Person-hours"])
    orig_econ_cost = float(row["Economic cost ($)"])

    # ── Step 3: Post-islanding impact ────────────────────────────────────
    post_phrs      = int(orig_phrs      * (1 - iso))
    post_econ_cost = orig_econ_cost     * (1 - iso)
    avoided_cost   = orig_econ_cost     * iso

    # ── Step 4: Capital cost ──────────────────────────────────────────────
    # Capacity sized to cover islanding_frac of affected households
    capacity_kw    = (orig_pop / PERSONS_PER_HH) * KWH_PER_HH * iso
    gross_cost     = capacity_kw * COST_PER_KW

    # Incentive stacking:
    #  SGIP Equity: 100% of storage component × HFTD fraction
    #  IRA ITC:     30% of solar+storage (applied to post-SGIP remainder)
    sgip_offset_rate = hftd * STORAGE_FRAC * sgip_equity_cov    # ×HFTD ×storage% ×100%
    ira_offset_rate  = ira_itc_cov                               # 30% of total
    # ITC applies to cost after SGIP (SGIP is a direct incentive, ITC is a tax credit
    # applied to net eligible cost after grants; conservative: apply both to gross)
    combined_offset  = min(sgip_offset_rate + ira_offset_rate, 0.90)  # cap at 90%
    sgip_value       = gross_cost * sgip_offset_rate
    ira_value        = gross_cost * ira_offset_rate
    net_cost         = gross_cost * (1 - combined_offset)

    # ── Step 5: Returns ───────────────────────────────────────────────────
    annual_avoided = avoided_cost / OUTAGE_YRS
    payback_yrs    = net_cost / annual_avoided if annual_avoided > 0 else float("inf")
    npv_20yr       = (annual_avoided * annuity_factor) - net_cost
    pop_served     = int(orig_pop * iso)
    cost_per_person = net_cost / pop_served if pop_served > 0 else float("nan")

    financial[lbl] = {
        "islanding_frac"          : iso,
        "orig_pop"                : orig_pop,
        "orig_person_hrs"         : orig_phrs,
        "orig_econ_cost"          : orig_econ_cost,
        "post_islanding_phrs"     : post_phrs,
        "post_islanding_cost"     : round(post_econ_cost, 0),
        "avoided_cost"            : round(avoided_cost, 0),
        "capacity_kw"             : round(capacity_kw, 0),
        "gross_capital_cost"      : round(gross_cost, 0),
        "sgip_equity_offset"      : round(sgip_value, 0),
        "ira_itc_offset"          : round(ira_value, 0),
        "combined_offset_pct"     : round(combined_offset * 100, 1),
        "net_capital_cost"        : round(net_cost, 0),
        "annual_avoided_cost"     : round(annual_avoided, 0),
        "payback_years"           : round(payback_yrs, 1),
        "npv_20yr"                : round(npv_20yr, 0),
        "net_benefit"             : round(avoided_cost - net_cost, 0),
        "pop_served_by_microgrid" : pop_served,
        "cost_per_person"         : round(cost_per_person, 2),
    }

    print(f"\n  {lbl}")
    print(f"    Islanding: {iso:.0%}  |  Capacity: {capacity_kw/1e3:,.0f} MW")
    print(f"    Gross cost: ${gross_cost/1e9:.2f}B  |  SGIP: ${sgip_value/1e9:.2f}B  "
          f"IRA: ${ira_value/1e9:.2f}B  |  Net: ${net_cost/1e9:.2f}B  "
          f"(offset: {combined_offset:.1%})")
    print(f"    Avoided cost: ${avoided_cost/1e9:.2f}B  |  Annual avoided: ${annual_avoided/1e6:.1f}M")
    print(f"    Payback: {payback_yrs:.1f}yr  |  20-yr NPV: ${npv_20yr/1e9:.2f}B  "
          f"|  Cost/person: ${cost_per_person:,.0f}")


# ══════════════════════════════════════════════════════════════════════════════
# STEP 6 — SVI EQUITY OVERLAY
# ══════════════════════════════════════════════════════════════════════════════
print("\n" + "="*70)
print("Step 6 — SVI Equity Overlay")
print("="*70)

svi = pd.read_csv(SVI_F, dtype=str)

# Extract 3-digit county FIPS from 11-char tract FIPS
# FIPS format: SS_CCC_TTTTTT (2-state + 3-county + 6-tract)
svi["county_fips"] = svi["FIPS"].str[2:5]
svi["RPL_THEMES_f"] = pd.to_numeric(svi["RPL_THEMES"], errors="coerce")
svi["E_TOTPOP_f"]   = pd.to_numeric(svi["E_TOTPOP"],   errors="coerce")

# Drop missing values and sentinel -999
svi_clean = svi[
    (svi["RPL_THEMES_f"] >= 0) &
    (svi["E_TOTPOP_f"]   >= 0)
].copy()
print(f"  SVI tracts loaded: {len(svi_clean):,} (of {len(svi):,})")

# SVI quartile threshold (top quartile = most vulnerable)
q75 = svi_clean["RPL_THEMES_f"].quantile(0.75)
print(f"  RPL_THEMES top-quartile threshold: {q75:.3f}")

svi_equity = {}

for lbl, segs in segment_map.items():
    # Counties with affected transmission segments
    aff_names = segs["county"].dropna().unique()
    aff_fips  = {CA_NAME_TO_FIPS[c] for c in aff_names if c in CA_NAME_TO_FIPS}

    svi_aff = svi_clean[svi_clean["county_fips"].isin(aff_fips)].copy()

    total_pop   = svi_aff["E_TOTPOP_f"].sum()
    high_svi    = svi_aff[svi_aff["RPL_THEMES_f"] > q75]
    hi_svi_pop  = high_svi["E_TOTPOP_f"].sum()
    pct_hi_svi  = hi_svi_pop / total_pop if total_pop > 0 else 0.0

    n_tracts    = len(svi_aff)
    n_hi_tracts = len(high_svi)
    mean_svi    = svi_aff["RPL_THEMES_f"].mean()

    # For Scenario C (targeting highest SVI): estimate served pop in high-SVI areas
    # Scenario C explicitly deploys at highest-SVI communities, so served pop
    # is concentrated in the top quartile
    iso = SCENARIOS[lbl]["islanding_frac"]
    pop_served = financial[lbl]["pop_served_by_microgrid"]
    if lbl == "C — Reactive Crisis":
        # Emergency deployment targets high-SVI + critical facilities
        # Assume 80% of served population is in high-SVI areas
        pct_served_high_svi = min(0.80, pct_hi_svi * 2.5)
    elif lbl == "A — Fortress Grid":
        # Fortress Grid targets highest-risk corridors (wildland-urban interface)
        # Often overlaps with rural/low-income areas — moderate SVI overlap
        pct_served_high_svi = pct_hi_svi * 1.2
    else:  # B — Islands of Power
        # Broad deployment proportional to zone distribution
        pct_served_high_svi = pct_hi_svi

    pct_served_high_svi = min(pct_served_high_svi, 1.0)
    n_high_svi_served   = int(pop_served * pct_served_high_svi)

    svi_equity[lbl] = {
        "affected_counties"       : len(aff_fips),
        "affected_tracts"         : n_tracts,
        "high_svi_tracts"         : n_hi_tracts,
        "pct_tracts_high_svi"     : round(100 * n_hi_tracts / n_tracts, 1),
        "zone_pop_in_high_svi_pct": round(100 * pct_hi_svi, 1),
        "mean_svi_score"          : round(mean_svi, 3),
        "pop_served"              : pop_served,
        "pct_served_in_high_svi"  : round(100 * pct_served_high_svi, 1),
        "n_high_svi_served"       : n_high_svi_served,
    }

    print(f"\n  {lbl}")
    print(f"    Affected counties: {len(aff_fips)}  |  "
          f"Tracts in zone: {n_tracts:,}  |  Mean SVI: {mean_svi:.3f}")
    print(f"    High-SVI tracts (RPL>{q75:.2f}): {n_hi_tracts:,} "
          f"({pct_hi_svi:.1%} of zone pop)")
    print(f"    Microgrid-served pop: {pop_served:,}  →  "
          f"{pct_served_high_svi:.1%} in high-SVI areas "
          f"({n_high_svi_served:,} people)")


# ══════════════════════════════════════════════════════════════════════════════
# STEP 7 — ASSEMBLE FINAL TABLE
# ══════════════════════════════════════════════════════════════════════════════
W = 106
print(f"\n{'='*W}")
print("RESILIENCE FINANCIAL SUMMARY — ALL SCENARIOS")
print(f"{'='*W}")

output_rows = []
for lbl in SCENARIOS:
    if lbl not in financial:
        continue
    f  = financial[lbl]
    sv = svi_equity[lbl]
    dr = der_results[lbl]

    output_rows.append({
        "Scenario"                              : lbl,
        "Deployment note"                       : SCENARIOS[lbl]["deployment_note"],

        # DER coverage
        "Affected counties"                     : dr["affected_counties"],
        "Counties with existing SGIP (DER)"     : dr["counties_with_der"],
        "% affected pop in DER counties"         : f"{dr['pct_pop_with_der']:.1%}",
        "Existing SGIP capacity (MW)"            : round(dr["existing_sgip_kw"] / 1e3, 1),

        # Impact before/after islanding
        "Population affected (refined)"          : f["orig_pop"],
        "Post-microgrid person-hours"            : f["post_islanding_phrs"],
        "Orig person-hours"                      : f["orig_person_hrs"],
        "Person-hours avoided"                   : f["orig_person_hrs"] - f["post_islanding_phrs"],
        "Orig economic cost ($M)"                : round(f["orig_econ_cost"] / 1e6, 1),
        "Post-microgrid cost ($M)"               : round(f["post_islanding_cost"] / 1e6, 1),
        "Avoided cost ($M)"                      : round(f["avoided_cost"] / 1e6, 1),

        # Capital cost
        "Islanding fraction"                     : f"{f['islanding_frac']:.0%}",
        "Microgrid capacity (MW)"                : round(f["capacity_kw"] / 1e3, 1),
        "Gross capital cost ($M)"                : round(f["gross_capital_cost"] / 1e6, 1),
        "SGIP Equity offset ($M)"                : round(f["sgip_equity_offset"] / 1e6, 1),
        "IRA ITC offset ($M)"                    : round(f["ira_itc_offset"] / 1e6, 1),
        "Combined incentive offset (%)"          : f["combined_offset_pct"],
        "Net capital cost ($M)"                  : round(f["net_capital_cost"] / 1e6, 1),

        # Returns
        "Annual avoided cost ($M)"               : round(f["annual_avoided_cost"] / 1e6, 1),
        "Payback period (years)"                 : f["payback_years"],
        "20-year NPV ($M)"                       : round(f["npv_20yr"] / 1e6, 1),
        "Net benefit ($M)"                       : round(f["net_benefit"] / 1e6, 1),
        "Population served by microgrids"        : f["pop_served_by_microgrid"],
        "Cost per person protected ($)"          : round(f["cost_per_person"], 0),

        # Equity
        "Affected census tracts (SVI)"           : sv["affected_tracts"],
        "% zone pop in high-SVI (Q4)"            : sv["zone_pop_in_high_svi_pct"],
        "% served pop in high-SVI"               : sv["pct_served_in_high_svi"],
        "Approx high-SVI pop served"             : sv["n_high_svi_served"],
    })

results_df = pd.DataFrame(output_rows).set_index("Scenario")

# ── Pretty print key metrics ──────────────────────────────────────────────────
display_groups = [
    ("EXISTING DER",
     ["Existing SGIP capacity (MW)", "% affected pop in DER counties"]),

    ("IMPACT (REFINED BASE vs POST-MICROGRID)",
     ["Population affected (refined)",
      "Orig economic cost ($M)", "Post-microgrid cost ($M)", "Avoided cost ($M)"]),

    ("MICROGRID CAPITAL COST",
     ["Islanding fraction", "Microgrid capacity (MW)",
      "Gross capital cost ($M)", "SGIP Equity offset ($M)",
      "IRA ITC offset ($M)", "Combined incentive offset (%)",
      "Net capital cost ($M)"]),

    ("FINANCIAL RETURNS",
     ["Annual avoided cost ($M)", "Payback period (years)",
      "20-year NPV ($M)", "Net benefit ($M)",
      "Population served by microgrids", "Cost per person protected ($)"]),

    ("SVI EQUITY",
     ["% zone pop in high-SVI (Q4)",
      "% served pop in high-SVI",
      "Approx high-SVI pop served"]),
]

for group_name, cols in display_groups:
    print(f"\n  ── {group_name} {'─'*(W-6-len(group_name))}")
    for col in cols:
        print(f"  {col:<42}", end="")
        for lbl in SCENARIOS:
            if lbl in results_df.index:
                val = results_df.loc[lbl, col]
                if isinstance(val, float):
                    print(f"  {val:>14,.1f}", end="")
                elif isinstance(val, int):
                    print(f"  {val:>14,}", end="")
                else:
                    print(f"  {str(val):>14}", end="")
        print()

# ── Save ──────────────────────────────────────────────────────────────────────
results_df.to_csv(str(OUT_F))
print(f"\nSaved: {OUT_F}  ({OUT_F.stat().st_size/1e3:.1f} KB)")
print(f"Total run time: {time.time()-t0:.0f}s")
