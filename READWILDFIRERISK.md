# California Wildfire-Microgrid Resilience Analysis

**Goal:** Quantify the human and economic impact of wildfire-driven transmission outages across all 58 CA counties, evaluate distributed energy resource (DER) deployment as a resilience counterfactual, and produce a corridor-level investment prioritization matrix for utility and policy decision-makers.

**Status: COMPLETE** — all 12 scripts run, all 8 processed datasets generated, all 5 final outputs produced.

**Last updated:** April 16, 2026

---

## Table of Contents

1. [Pipeline Overview](#pipeline-overview)
2. [Scripts](#scripts)
3. [Data Sources](#data-sources)
4. [Key Results](#key-results)
5. [Final Outputs](#final-outputs)
6. [Next Steps](#next-steps)
7. [Methodology Notes and Limitations](#methodology-notes-and-limitations)

---

## Pipeline Overview

```
00_download_carbonplan.py       ─┐
00b_download_supplementary.py    │  Raw data acquisition          [COMPLETE]
01_download_ms_buildings.py      │
fetch_critical_facilities.py     │
merge_buildings.py              ─┘
        │
        ▼
02_risk_scoring.py              ──  Composite wildfire risk score  [COMPLETE]
        │
        ▼
03_scenario_builder.py          ──  Three outage scenarios (A/B/C) [COMPLETE]
        │
        ▼
04_impact_quantification.py     ──  Flat-buffer impact (v1)        [COMPLETE]
04b_impact_refined.py           ──  Topology-proxy impact (v2, preferred) [COMPLETE]
        │
        ▼
05_microgrid_resilience.py      ──  DER counterfactual + financials [COMPLETE]
        │
        ▼
06_prioritization_matrix.py     ──  4-dimension corridor matrix     [COMPLETE]
        │
        ▼
07_visualization.py             ──  5 final presentation outputs    [COMPLETE]
```

---

## Scripts

### `scripts/00_download_carbonplan.py` (198 lines) — COMPLETE
Downloads the CarbonPlan v3 wildfire burn probability raster for California. Saves a GeoTIFF at `data/raw/wildfire_risk/carbonplan_burn_probability.tif`. Used as a continuous burn-probability surface in risk scoring.

### `scripts/00b_download_supplementary.py` (554 lines) — COMPLETE
Downloads six supplementary datasets not available through the primary pipeline:

| Dataset | Source | Notes |
|---------|--------|-------|
| CDC SVI 2022 | CDC GRASP portal | Tract-level social vulnerability for CA |
| USFWS Critical Habitat | ArcGIS FeatureServer | 139 Final-status polygons |
| USGS WBD HUC8 Watersheds | USGS Hydro REST | 140 CA subbasins |
| BEA County GDP | Manual fallback | API was inactive; estimates from published 2022 data |
| NREL ATB 2023 Microgrid Costs | Structured CSV | 7 technology types with $/kW benchmarks |
| Incentive Programs | Constructed CSV | SGIP, IRA ITC, FEMA BRIC, CEC EPIC, USDA REAP |

### `scripts/01_download_ms_buildings.py` (176 lines) — COMPLETE
Downloads Microsoft Building Footprints for California using quadkey-based tile enumeration. Fetches tiles at zoom level 9, saves gzip-compressed CSVs under `data/raw/buildings/tiles/`, assembles `data/raw/buildings/ca_buildings.parquet`.

### `scripts/02_risk_scoring.py` (292 lines) — COMPLETE
Scores all 6,839 CA transmission line segments on three dimensions:

- **Hazard:** CarbonPlan burn probability (mean, max, 90th-pct) intersected with segment
- **Exposure:** FHSZ severity (max within 1km), fire perimeter count (5km buffer), prescribed burn proximity
- **Vulnerability:** Census block group count within 10km, PSPS event history

Combines into a composite score (0–1). Risk tiers: Critical (top 10%), High (10–25%), Moderate (25–50%), Low (bottom 50%). Saves `data/processed/transmission_risk_scores.gpkg`.

### `scripts/03_scenario_builder.py` (245 lines) — COMPLETE
Constructs three planning scenarios from the risk-scored network:

| Scenario | Segments | Description |
|----------|----------|-------------|
| A — Fortress Grid | 1,710 | Critical + High; 50%/25% outage probability reduction from hardening |
| B — Islands of Power | 1,710 | Same corridors; broad DER deployment enabling islanded microgrids |
| C — Reactive Crisis | 684 | Critical only; no hardening, emergency response posture |

### `scripts/04_impact_quantification.py` (393 lines) — COMPLETE
First-pass impact using flat circular buffers (A: 5km, B: 10km, C: 25km). Census ACS 2021 population, Microsoft building count, $9/kWh VOLL, PSPS median 33.7 hours. Saves `data/processed/impact_summary.csv`.

**Results (flat buffer):** A = $6.05B, B = $7.34B, C = $8.03B

### `scripts/04b_impact_refined.py` (634 lines) — COMPLETE
Preferred impact estimate. Five topology proxies applied in sequence:

1. **Voltage-scaled buffers** — 500kV: 20km / 230–499kV: 10km / 115–229kV: 5km / <115kV: 2km
2. **Service territory clipping** — 17 CA utility owners mapped to HIFLD territory polygons
3. **Substation containment** — only CBGs with a substation within 2km retained
4. **Population density filter** — drops CBGs with <10 people/km²
5. **Redundancy discount** — 99.9% of segments have a parallel line within 8km → ×0.35 factor

Saves `data/processed/impact_summary_refined.csv`.

**Results (refined):** A/B = 9.3M people, $1.85B; C = 8.0M people, $1.69B

### `scripts/05_microgrid_resilience.py` (504 lines) — COMPLETE
Microgrid resilience counterfactual. Islanding fractions: A=15%, B=45%, C=20%. SGIP Equity + IRA ITC incentive stacking (capped at 90%). 20-year NPV at 5% discount rate. SVI Q4 equity overlay. Saves `data/processed/resilience_financial_summary.csv`.

### `scripts/06_prioritization_matrix.py` (613 lines) — COMPLETE
Four-dimension corridor prioritization over 291 corridors (county × Owner × voltage class):

| Dimension | Weight | Key Sub-components |
|-----------|--------|--------------------|
| D1 — Life Safety | 0.35 | Population density, SVI Q4 share, age 65+, critical facility rate |
| D2 — Resilience Gap | 0.25 | SGIP ratio (inverse), n_alternatives (inverse), substation distance |
| D3 — Financial Viability | 0.25 | GDP per corridor, payback proxy (inverse), avoided cost proxy |
| D4 — Ecological Risk | 0.15 | Critical habitat species count, watershed count, fire perimeter density |

Combined priority = 0.35×D1 + 0.25×D2 + 0.25×D3 + 0.15×D4. Runtime ~57 seconds using vectorized spatial joins. Saves `data/processed/prioritization_matrix.csv`.

### `scripts/07_visualization.py` (623 lines) — COMPLETE
Produces all five final presentation outputs. Includes three targeted post-review fixes:
- Interactive map: critical facilities layer defaults off; hospitals as blue `+` DivIcon markers; fire stations semi-transparent (opacity=0.4)
- Financial case: break-even line prominent and labelled; net cost labels moved above bars; scenario-coloured bar borders
- Killer slide: reduced inter-table whitespace; per-scenario action labels; Key Takeaways section with 3 bold bullets

### `scripts/fetch_critical_facilities.py` (75 lines) — COMPLETE
Fetches hospital and fire station locations from HIFLD ArcGIS services. Saves `data/raw/microgrids/critical_facilities.gpkg` (3,778 points).

### `scripts/merge_buildings.py` (88 lines) — COMPLETE
Merges per-tile Microsoft Building Footprint CSVs into `data/raw/buildings/ca_buildings.parquet`.

---

## Data Sources

### Raw Data (`data/raw/`)

#### Transmission & Grid Infrastructure
| File | Source | Records |
|------|--------|---------|
| `transmission_lines/transmission_lines.gpkg` | HIFLD | 6,839 segments, 57,660 km |
| `substations/substations.gpkg` | HIFLD | 4,265 CA substations |
| `census/service_territories.gpkg` | HIFLD | 17 mapped CA utilities |

#### Wildfire Risk
| File | Source | Notes |
|------|--------|-------|
| `wildfire_risk/carbonplan_burn_probability.tif` | CarbonPlan v3 | 270m resolution |
| `wildfire_risk/fhsz.gpkg` | CAL FIRE | 102 MB; 3 severity classes |
| `wildfire_risk/fire_perimeters.gpkg` | CAL FIRE FRAP | Historical perimeters |
| `wildfire_risk/prescribed_burns.gpkg` | CAL FIRE | Treatment polygons |

#### PSPS Outage History
26 XLSX files under `data/raw/psps_history/` covering PG&E, SCE, SDG&E, PacifiCorp, Bear Valley, and Liberty Utilities for 2021–2024 (PSDR + Post-SR2B reports). Derived statistic: **median outage duration = 33.7 hours** (Scenarios A/B), 39.0 hours (Scenario C).

#### Population & Socioeconomic
| File | Source | Notes |
|------|--------|-------|
| `census/census_block_groups.gpkg` | Census ACS 2021 | All CA CBGs |
| `census/svi_california_2022.csv` | CDC SVI 2022 | Tract-level RPL_THEMES |
| `financial/county_gdp.csv` | BEA 2022 (manual) | 58 CA counties |

#### Buildings
| File | Source | Notes |
|------|--------|-------|
| `buildings/ca_buildings.parquet` | Microsoft Building Footprints | ~12M footprints |
| `buildings/tiles/*.csv.gz` | Microsoft / quadkey z9 | ~200 tile files |

#### Ecology
| File | Source | Records |
|------|--------|---------|
| `ecology/critical_habitat.gpkg` | USFWS FeatureServer | 139 Final-status polygons |
| `ecology/watersheds.gpkg` | USGS WBD HUC8 | 140 CA subbasins |

#### DER / Microgrids
| File | Source | Notes |
|------|--------|-------|
| `microgrids/sgip_data.csv` | CPUC SGIP | 62,352 active projects |
| `microgrids/critical_facilities.gpkg` | HIFLD | 3,778 hospitals + fire stations |
| `microgrids/nrel_solar_potential.csv` | NREL | County-level solar capacity factors |

#### Financial
| File | Source | Notes |
|------|--------|-------|
| `financial/nrel_microgrid_costs.csv` | NREL ATB 2023 | Solar PV $1,100/kW, Li-Ion 4hr $1,200/kW |
| `financial/incentive_programs.csv` | Constructed | SGIP Equity (100%), IRA ITC (30%), FEMA BRIC (75%) |

---

### Processed Data (`data/processed/`) — ALL COMPLETE

| File | Size | Rows | Description |
|------|------|------|-------------|
| `transmission_risk_scores.gpkg` | 8.3 MB | 6,839 | Segments with composite score and risk tier |
| `scenario_a_fortress.gpkg` | 3.3 MB | 1,710 | Scenario A line segments (Critical + High) |
| `scenario_b_islands.gpkg` | 9.4 MB | 1,710 | Scenario B; original line WKT in `line_geometry_wkt` column |
| `scenario_c_crisis.gpkg` | 3.2 MB | 684 + 4,265 | Two layers: `critical_segments` + `substations` |
| `impact_summary.csv` | 749 B | 3 | Flat-buffer impact by scenario |
| `impact_summary_refined.csv` | 989 B | 3 | Topology-proxy refined impact (preferred) |
| `resilience_financial_summary.csv` | 1.4 KB | 3 | Full financial model per scenario |
| `prioritization_matrix.csv` | 47 KB | 291 | Corridor D1–D4 scores and action tiers |

---

## Key Results

### Transmission Network
- 6,839 segments · 58 counties · 36 owners · 57,660 km total
- Risk tiers: Critical 684 (10%) · High 1,026 (15%) · Moderate 1,710 (25%) · Low 3,419 (50%)

### Impact — Refined Estimates

| Scenario | Population at Risk | Economic Cost | Person-Hours Lost |
|----------|--------------------|---------------|-------------------|
| A — Fortress Grid | 9.3M | $1.85B | 342M |
| B — Islands of Power | 9.3M | $1.85B | 342M |
| C — Reactive Crisis | 8.0M | $1.69B | 313M |

Redundancy proxy (×0.35 factor) accounts for ~81% of reduction from flat-buffer to refined estimates.

### Microgrid Resilience — Financial Summary

| Scenario | Net Capital Cost | 20-yr NPV | Payback | Cost/Person | High-SVI Served |
|----------|-----------------|-----------|---------|-------------|-----------------|
| A — Fortress Grid | $352M | $338M | 6.4 yr | $252 | 31% |
| B — Islands of Power | $1,358M | $711M | 8.2 yr | $324 | 26% |
| C — Reactive Crisis | $289M | $553M | **4.3 yr** | **$180** | **65%** |

Scenario C: only scenario with positive net benefit (+$49M); best payback; highest SVI equity coverage.

### Corridor Prioritization — 291 Corridors

| Tier | Count | Score Range |
|------|-------|-------------|
| Act Now | 0 | ≥ 7.0 |
| Plan Now | 4 | 5.0–6.9 |
| Monitor | 222 | 3.0–4.9 |
| Defer | 65 | < 3.0 |

Top 5 corridors:

| Rank | County | Owner | kV Class | Score | D1 | D2 |
|------|--------|-------|----------|-------|----|----|
| 1 | Sierra | PLSR | <115kV | 5.58 | 7.76 | 7.86 |
| 2 | Tulare | LADWP | 500kV+ | 5.47 | 3.16 | 7.00 |
| 3 | Sierra | NVENERGY | <115kV | 5.34 | 7.76 | 7.74 |
| 4 | Sierra | PG&E | <115kV | 5.29 | 7.76 | 7.74 |
| 5 | Los Angeles | SCE | 500kV+ | 4.96 | 2.63 | 6.03 |

Sierra County dominates: highest D1 (7.76) driven by elevated 65+ population, high SVI, low SGIP penetration, minimal redundancy.

---

## Final Outputs — ALL COMPLETE

```
outputs/
├── maps/
│   └── wildfire_microgrid_analysis.html    (100 MB — interactive folium map)
└── tables/
    ├── scenario_comparison.png             (384 KB — 4770×2687, 300 DPI)
    ├── financial_case.png                  (294 KB — 4170×1850, 300 DPI)
    ├── prioritization_scatter.png          (350 KB — 3270×2366, 300 DPI)
    └── killer_slide.png                    (480 KB — 4668×3176, 300 DPI)
```

### Output Descriptions

**1. `wildfire_microgrid_analysis.html`** — Interactive folium map with 6 toggle layers:
- Fire Hazard Severity Zones (FHSZ, decimated for performance)
- Transmission lines coloured by risk tier (Critical/High/Moderate/Low)
- Scenario A, B, C line overlays (off by default)
- Critical Facilities: hospitals as blue `+` markers, fire stations as semi-transparent orange dots (off by default)
- Layer control, scale bar, measure tool, legend, title bar

**2. `scenario_comparison.png`** — 2×3 panel bar chart:
Population protected, avoided cost, net capital cost, 20-yr NPV, payback period, cost per person. Bottom legend explains Scenario A/B/C with full names and descriptions.

**3. `financial_case.png`** — 2-panel chart:
Left: cumulative 20-year cash flow curves (A=blue, B=green, C=red) with prominent Break-even line. Right: stacked capital cost breakdown (gross → SGIP Equity offset → IRA ITC offset → net) with scenario-coloured bar borders; net cost labels above bars.

**4. `prioritization_scatter.png`** — D1 Life Safety vs D2 Resilience Gap scatter plot. Bubble size ∝ population at risk. Coloured by action tier. Top 8 corridors labelled. Quadrant annotations including HIGH PRIORITY ZONE.

**5. `killer_slide.png`** — Executive summary slide:
- Scenario summary table (3 rows): population, avoided cost, net cost, NPV, payback, cost/person, SVI %, action label
- Top 5 Priority Corridors table (5 rows): county, owner, kV class, score, tier, D1, D2
- Key Takeaways box (indigo background, 3 bold bullets)

---

## Next Steps

### Validation
- [ ] Ground-truth the 4 Plan Now corridors against CPUC transmission planning records and PG&E/SCE grid hardening filings
- [ ] Cross-check Sierra County D1 score (elderly/SVI flags) with county health department data
- [ ] Verify SGIP ratio for PLSR and NVENERGY — both at 0.067, may reflect incomplete matching for smaller utilities

### Sensitivity Analysis
- [ ] Vary VOLL ($7–$12/kWh range) — effect on NPV and payback rankings
- [ ] Vary discount rate (3%, 5%, 7%) for 20-year NPV
- [ ] Vary redundancy multiplier (0.25–0.50) — highest-leverage assumption, drives 81% of refined impact reduction
- [ ] Test islanding fractions ±10% per scenario

### Methodology Improvements
- [ ] Replace 8km parallel-line proxy with N-1 contingency analysis (CAISO or WECC grid model)
- [ ] Integrate hourly CAISO load profiles to replace flat 33.7hr PSPS median
- [ ] Time-of-use VOLL (higher at evening peak) rather than flat $9/kWh
- [ ] Building occupancy classification (residential/commercial/industrial) for weighted economic impact
- [ ] CPUC HFTD Tier 2/3 boundaries to sharpen SGIP Equity eligibility

### Presentation / Delivery
- [ ] Present 5 output files to stakeholders
- [ ] Draft county-level policy briefs for the 4 Plan Now corridors (Sierra, Tulare)
- [ ] Explore FEMA BRIC and CEC EPIC grant eligibility for top-tier corridors

---

## Methodology Notes and Limitations

### Redundancy Proxy
The most significant assumption. A per-segment 8km buffer detects parallel lines as a proxy for grid redundancy. 99.9% of CA segments have a parallel line within 8km → ×0.35 discount applies to virtually the entire network, driving ~81% of the flat-buffer-to-refined reduction. True N-1 resilience is determined by switching topology, not geographic proximity — this proxy may substantially understate impact for co-located but electrically isolated corridors.

### VOLL ($9/kWh)
Flat rate from LBNL estimates. Does not vary by customer class, time of day, season, or duration. For outages >8 hours, VOLL typically escalates non-linearly; this analysis uses a linear model.

### BEA County GDP
BEA API (demo key) was inactive during data collection. County GDP figures are a manual fallback from published 2022 estimates. D3 Financial Viability scores in low-GDP rural counties should be treated as approximate.

### FHSZ Map Decimation
CAL FIRE FHSZ GeoPackage is 102 MB. Decimated to every 5th row for the interactive HTML map. Full dataset used in risk scoring (script 02).

### Scenario B Geometry
`scenario_b_islands.gpkg` stores polygon buffer geometries as the primary geometry column (10km buffers). Original line geometry is preserved as WKT in `line_geometry_wkt` and must be reconstructed with `shapely.wkt.loads()` for line operations.

### SGIP County Matching
SGIP data has ~740 county string variants for 58 CA counties. Normalization strips " County" suffixes, applies `.title()`, and uses a corrections dictionary for known typos. Smaller utilities (PLSR, NVENERGY, PCORP, BPA) returned near-zero matched capacity; their elevated D2 scores may reflect the matching limitation rather than true DER absence.

### SVI Resolution
CDC SVI is tract-level. Aggregated to county by averaging RPL_THEMES, then joined to corridors by county. Tract-level variation within a county is lost.

### Critical Tier Ceiling
0 "Act Now" corridors (score ≥ 7.0); only 4 "Plan Now". D3 Financial Viability constrains rural corridors with low GDP and low avoided-cost proxies. The tier thresholds were set before running the model; a data-driven threshold (top 5th percentile) would promote more corridors into actionable tiers.

### Projection
All distance-dependent operations in CA Albers EPSG:3310 (metres). All spatial joins and file outputs use EPSG:4326 for folium compatibility.
