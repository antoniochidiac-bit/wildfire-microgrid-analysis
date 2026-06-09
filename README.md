Wildfire risk financial & economic analysis - methodology walkthrough

Step 1 – Establish the Affected Population
Starting point: The 1,710 transmission line segments classified as Critical or High tier from Script 02.
We applied five topology proxies (Script 04b) to get a realistic affected population:
First, we drew voltage-scaled buffers around each segment: 500kV lines got a 20km buffer, 230-345kV got 10km, 115-229kV got 5km, under 115kV got 2km. This replaced the original flat 10km buffer for all lines.
Second, we clipped each buffer to the utility service territory of the line – so a PG&E line failure only counts PG&E customers, not SCE customers who happen to live nearby.
Third, we only counted census block groups that contained or were adjacent to an affected substation – removing rural areas with transmission lines passing overhead but no actual connection to them.
Fourth, we removed census block groups with fewer than 10 people per km² – eliminating vast unpopulated wildland areas.
Fifth, we applied a redundancy discount – lines with a parallel route within 8km had their affected population multiplied by 0.35 (reflecting the high probability that a backup pathway exists), and lines without a parallel route were multiplied by 0.90.
Result after all five proxies: 9.32 million people affected under Scenario B.

Step 2 – Calculate Outage Duration
Data source: CPUC PSPS historical event reports (2013-2025), 7,243 rows across 23 CSV files.
Method: We grouped historical PSPS events by the voltage class of the line that triggered the shutoff and calculated the median outage duration for each voltage class. Higher voltage lines tend to have longer restoration times because they require more complex repair and safety verification.
Result: The median outage duration for Scenario B came out at 36.7 hours. This is the blended median across all voltage classes present in the 1,710 affected segments.

Step 3 – Calculate Person-Hours of Outage
This is the core impact metric before any dollars are applied.
Person-hours = affected population × median outage hours
Person-hours = 9,320,000 × 36.7
Person-hours = 342,044,000 ≈ 342 million person-hours

Step 4 – Convert to Economic Cost (Value of Lost Load)
Key assumption: CPUC residential Value of Lost Load (VOLL) = $9 per kWh.
Key assumption: Average California household consumes 1.5 kWh per hour during an outage.
Key assumption: Average California household size = 2.5 persons (Census average).
Households affected = 9,320,000 ÷ 2.5 = 3,728,000 households
Energy not served = 3,728,000 × 1.5 kWh/hr × 36.7 hrs
                 = 205,280,400 kWh
Economic cost = 205,280,400 × $9
              = $1,847,523,600 ≈ $1.85 billion
This is the refined economic cost of the outage before any microgrid intervention.

Step 5 – Apply the Islanding Fraction
This is where the microgrid counterfactual begins. For Scenario B (Islands of Power) we assumed that proactive deployment of microgrids across all Critical and High tier corridors would allow 45% of affected load to be served locally during an outage.
The 45% is an assumption. It reflects the logic that full deployment across all high-risk corridors with solar and battery storage at adequate capacity could realistically serve close to half the affected load, but not all of it due to geographic constraints, load variability, and the time needed to island.
Population served by microgrids = 9,320,000 × 45% = 4,194,000 people
Revised person-hours of outage = 342,044,000 × (1 - 0.45)
                               = 342,044,000 × 0.55
                               = 188,124,200 person-hours
Avoided person-hours = 342,044,000 - 188,124,200 = 153,919,800
Avoided economic cost = 153,919,800 ÷ 2.5 persons × 1.5 kWh × $9
                      = $830,366,520 ≈ $830 million per event
This $830M is the value of outage costs avoided per major event under Scenario B.

Step 6 – Calculate Required Microgrid Capacity
We needed to know how much solar and storage capacity to build to achieve 45% islanding.
Households served = 4,194,000 ÷ 2.5 = 1,677,600 households
Capacity required = 1,677,600 × 1.5 kW per household
                  = 2,516,400 kW ≈ 2,515 MW
The 1.5 kW per household figure represents the average power draw during an outage – lower than normal consumption because during an outage households typically run only essential loads.

Step 7 – Calculate Gross Capital Cost
Key assumption: NREL 2023 Annual Technology Baseline benchmark for solar + storage = $1,800 per kW installed.
Gross capital cost = 2,515 MW × 1,000 kW/MW × $1,800/kW
                   = $4,527,000,000 ≈ $4.53 billion

Step 8 – Apply Incentive Stacking
This is where the net cost drops dramatically. Two incentives stack on top of each other:
Incentive 1 – SGIP Equity Program (California): In High Fire Threat Districts, the Self-Generation Incentive Program covers 100% of battery storage costs. Battery storage is roughly 40% of the total solar + storage capital cost at NREL benchmarks.
SGIP Equity offset = $4,530M × 40% storage share × 100% coverage = $1,812M offset
Incentive 2 – IRA Investment Tax Credit (Federal): The Inflation Reduction Act Section 48 provides a 30% investment tax credit on the full solar + storage capital cost.
IRA ITC offset = $4,530M × 30% = $1,359M offset
Net cost after stacking both incentives:
Net capital cost = $4,530M - $1,812M - $1,359M
                 = $1,359M ≈ $1,358 million
Combined offset = ($1,812M + $1,359M) ÷ $4,530M = 70.0%
So 70% of the gross capital cost is covered by existing incentive programs, leaving a net cost of $1.358 billion.

Step 9 – Calculate Simple Payback Period
Key assumption: One major outage event per 5 years. This is the single most influential assumption in the entire financial model – see the sensitivity table in the document for how payback changes under different frequencies.
Annual avoided cost = $830M per event ÷ 5 years per event
                    = $166M per year
Simple payback = Net capital cost ÷ Annual avoided cost
               = $1,358M ÷ $166M
               = 8.2 years

Step 10 – Calculate 20-Year NPV
Parameters: 5% discount rate, 20-year asset life, $166M avoided cost per year (assumed constant). The NPV calculation discounts each year's avoided cost back to present value and subtracts the upfront net capital cost:
NPV = Σ (Annual avoided cost ÷ (1 + 0.05)^year) - Net capital cost
    = $166M × [annuity factor for 5%, 20 years] - $1,358M
    = $166M × 12.46 - $1,358M
    = $2,068M - $1,358M
    = $710M ≈ $711M
The annuity factor of 12.46 is the standard present value of an annuity formula: (1 - (1+r)^-n) / r = (1 - 1.05^-20) / 0.05.

Step 11 – Calculate Cost Per Person Protected
Cost per person = Net capital cost ÷ Population served by microgrids
               = $1,358M ÷ 4,194,000 people
               = $324 per person
