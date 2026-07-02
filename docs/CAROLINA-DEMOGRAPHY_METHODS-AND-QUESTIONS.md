# Carolina Demography Enrollment Forecast: Methods and Open Questions

> **Source:** Carolina Demography at UNC-Chapel Hill's Carolina Population Center + Demographic Analytics Advisors, LLC.
> Published in the Orange County BOCC agenda packet for April 7, 2026, Item 7-a.
> PDF archived at `data/raw/agenda_07APR2026.pdf`, pages 95-162 (72-page report).
> This analysis reviewed 2026-05-04.

This document catalogs what the Carolina Demography enrollment forecast report explicitly states, what it implies, and what it is silent or opaque on — organized by methodological component.

---

## Table of Contents

1. [Report Structure and Forecast Layers](#1-report-structure-and-forecast-layers)
2. [Birth-to-Kindergarten Ratio](#2-birth-to-kindergarten-ratio)
3. [Per-Grade Per-School GPR Model](#3-per-grade-per-school-gpr-model)
4. [Development Yield Pipeline](#4-development-yield-pipeline)
   - [Step 1: Spatial Allocation](#step-1-spatial-allocation--developments-to-schools)
   - [Step 2: Completion Probability](#step-2-completion-probability-by-status)
   - [Step 3: Occupancy Ramp](#step-3-delivery-timing-and-occupancy-ramp)
   - [Step 4: Census Occupancy Rate](#step-4-census-tract-level-occupancy-rate)
   - [Step 5: Age-Group Household Rates](#step-5-age-group-household-rates-student-yield-per-occupied-unit)
   - [Step 6: Market Share Adjustment](#step-6-market-share-adjustment)
   - [Step 7: GPR Baseline Deduction](#step-7-gpr-baseline-deduction-double-count-removal)
5. [Program Schools vs. Traditional Schools](#5-program-schools-vs-traditional-schools)
6. [Data Sources](#6-data-sources)
7. [Summary of Opacity](#7-summary-of-opacity)

---

## 1. Report Structure and Forecast Layers

The report builds forecasts incrementally in two computational layers, plus a third interpretive overlay:

1. **Baseline (GPR only)** — Grade Progression Ratio model using 10 years of historical ADM by grade by school. Kindergarten forecasted via birth-to-kindergarten ratios. Time-series methods used where GPRs show trends. This produces a per-school, per-year ADM forecast.

2. **Baseline + Development Yield** — Adds net new students from 42 residential/mixed-use developments (6,825 total units, 6,653 non-age-restricted) for CHCCS. This is the **primary forecast** reported per school. Key: they estimate *net new* students above the GPR baseline to avoid double-counting.

3. **Facilities Utilization Overlay** — Maps the development-adjusted forecast against each school's reported capacity. Not a separate ADM projection — same numbers viewed through a capacity lens.

### CHCCS District-Wide Summary

| Metric | Current (2025-26) | 5-Year (2030-31) | 10-Year (2035-36) | Change |
|--------|-------------------|-------------------|--------------------|--------|
| **Total ADM (dev-adjusted)** | 10,758 | ~10,161 | 9,688 | **-1,070 (-10%)** |
| Elementary ADM | 4,294 | 4,191 | 4,183 | -111 (-2.6%) |
| Middle ADM | 2,579 | 2,371 | 2,299 | -280 (-10.9%) |
| High ADM | 3,885 | 3,536 | 3,206 | -679 (-17.5%) |
| **Baseline (no dev)** | 10,758 | 9,759 | 9,122 | -1,637 (-15.2%) |

Development yield recovers 566 students vs. baseline over 10 years.

---

## 2. Birth-to-Kindergarten Ratio

### What the Report States

**District-level ratio** (p. 116):

> "The birth-to-kindergarten ratio measures the number of students entering CHCCS kindergarten divided by the number of births in Chapel Hill and Carrboro five years earlier."

Formula at the district level:

```
BirthToK_district(t) = Total_K_ADM_CHCCS(t) / Births_ChapelHill+Carrboro(t-5)
```

The report presents this as a single district-wide chart: 1.18 in 2015-16, declining over time. A ratio above 1.0 means more kindergarteners enroll than were born locally five years earlier (net in-migration of families); below 1.0 means net attrition before school entry.

**GPR model is per-school per-grade** (p. 112):

> "In our baseline model, we do this for each grade and for each school in a district."

**Kindergarten is a special case** (p. 112-113):

> "There is one special case...Kindergarten is forecast using a ratio of births 5 years prior to Kindergarteners in the current year."

**Development step references zone-level birth-to-K** (p. 113):

> "1. We begin by looking at GRPs and birth-to-kindergarten ratios for the attendance zone."

### What's Opaque

**The denominator problem — what "births" are used per school?**

The birth data source (Appendix A, p. 150) is "Births to resident mothers in Orange County" from NC DPH, 2011-2022. NC DPH birth data is released at the **county level**. The report also references births "in Chapel Hill and Carrboro" (municipal level). Neither can be directly assigned to school attendance zones without geocoding individual birth records.

If the GPR model operates "by school" including kindergarten, the per-school birth-to-K ratio must take one of these forms:

**Option A — Single district-wide ratio applied uniformly:**
```
K_forecast(school s, t+1) = Births_district(t-4) * (K_ADM(s,t) / Total_K_ADM(t))
```
Each school keeps its current *share* of kindergarteners, scaled by the district birth-to-K trend. All schools' K cohorts would rise and fall proportionally.

**Option B — Per-school ratio using district births as denominator:**
```
BirthToK(s, t) = K_ADM(s, t) / Births_district(t-5)
```
Each school gets its own ratio history, but the denominator is the same for all. The "ratio" becomes each school's K enrollment normalized by total district births — conflating zone demographics with program popularity, school reputation, and transfer effects. It would not have the demographic interpretation (in-migration vs. out-migration) that the report ascribes to it.

**Option C — True zone-level births from geocoded vital records:**
```
BirthToK(s, t) = K_ADM(s, t) / Births_zone(s, t-5)
```
This is the only formulation matching the report's interpretive language. But the report never claims to have geocoded birth data, and the data sources table lists only county-level births from NC DPH.

The report doesn't say which option is used. The development methodology (p. 113) references "birth-to-kindergarten ratios for the attendance zone," which sounds like Option C — but this appears in the context of qualitatively assessing whether a zone already shows growth, not necessarily as a computational step.

**Consequences for program schools:**
- For FPG (full program school, district-wide lottery draw): if births are zone-level (Option C), the tiny nominal FPG zone would produce a nearly meaningless ratio. If district-level (Options A or B), the ratio captures program demand rather than zone demographics.
- For partial program schools: any per-school ratio blends zone-resident kindergarteners with program-strand kindergarteners from outside the zone, inflating the ratio relative to zone-only births.

---

## 3. Per-Grade Per-School GPR Model

### What the Report States

The entire GPR methodology is described in two paragraphs (pp. 112-113).

**Historical GPR calculation** — For grades 1-12, at each school:

```
GPR(grade g, school s, year t) = ADM(g, s, t) / ADM(g-1, s, t-1)
```

**Input data:** Month 2 ADM "by grade and by school for the past 10 school years" (2015-16 through 2025-26), sourced from CHCCS and NCDPI. This produces **9 historical GPR values** per grade-school combination.

**School-level transitions:** "Ensuring to build correct progressions between elementary, middle, and high schools."

**Forecasting GPRs forward** (p. 113, quoted in full):

> "We begin by creating a forecast of GPRs. Many simply either use the most recent year's GPR, or some form of weighted average of the last several years, and then hold each of these GPRs (by school and by grade) constant for the remainder of the forecast period. This works well when there isn't a trend to the GPRs -- that is, when they are not increasing or decreasing. When they are, we are much more likely to use time series forecasting methods to allow these values to vary throughout the forecast period. What we choose is entirely dependent on which model best fits the local context."

**Applying forecast GPRs:**

> "Once we have final GPRs we are able to apply these data to current births and enrollment creating a baseline enrollment forecast."

### The Decision Tree (Reconstructed)

For each of the ~240 school-grade combinations in CHCCS (roughly 20 schools x applicable grades):

```
For each (school, grade) pair:
  1. Compute 9 historical GPR values (2016-17 through 2025-26)
  2. Assess: Do these GPRs show a trend?
     -> If NO trend: Use "most recent year's GPR" or "weighted average
        of the last several years," held CONSTANT for all forecast years
     -> If YES trend: Use "time series forecasting methods" to let the
        GPR VARY across forecast years
  3. This choice is "entirely dependent on which model best fits
     the local context"
```

### What's Opaque

#### 3a. The trend/no-trend decision criterion

- What statistical test or criterion determines "trend"? (Visual inspection? Linear regression significance? Autocorrelation?)
- What threshold separates "trending" from "stable"?
- Was this decision made independently for each of ~240 school-grade cells, or applied in broader groups (all grades at a school, all schools at a grade level)?

#### 3b. The "weighted average" parameters (stable GPRs)

- How many years? ("Several" could be 3, 5, 7, or all 9)
- What weights? (Equal? Exponentially declining? Recency-biased?)
- Same averaging window and weights for all stable school-grade pairs, or calibrated per cell?
- When do they use "most recent year's GPR" vs. "weighted average"? What determines this sub-choice?

#### 3c. The "time series forecasting methods" (trending GPRs)

- Which model? (ARIMA? Exponential smoothing / Holt-Winters? Linear trend extrapolation?)
- Model order/parameters? (e.g., ARIMA(1,1,0) vs. ARIMA(0,1,1))
- Selected per school-grade cell (auto-ARIMA?) or imposed uniformly?
- Are GPR forecasts bounded? (Can a GPR exceed 1.3? Fall below 0.7? Compound indefinitely?)
- Does the time-series forecast eventually level off (mean-reverting) or extrapolate indefinitely?

This matters enormously for schools with recent sharp trends. For example, Rashkis showed ~15% year-over-year ADM decline in the current year. If its GPRs are trending down and extrapolated, the decline compounds over 10 years into -30% cumulative. If dampened toward a long-run mean, the result is more moderate.

#### 3d. School-level transitions (5th->6th, 8th->9th)

CHCCS has feeder patterns: multiple elementary schools feed each middle school, multiple middle schools feed each high school. The report says only that they ensure "correct progressions." It never states:

- Whether the transition GPR is computed at the receiving school level:
  ```
  GPR(6th, McDougle_Middle, t) = ADM(6th, McDougle_Middle, t) / SUM(ADM(5th, feeder_elementaries, t-1))
  ```
  ...or as per-feeder-pair ratios
- How feeder weights are determined (current zone boundaries? historical flow data? the student-address data for the current year?)
- Whether feeder patterns are held constant for the forecast period
- How program school students are handled at transitions (e.g., do FPG 5th graders feed disproportionately into a specific middle school?)

#### 3e. COVID-era handling

The 10-year historical window (2015-16 through 2025-26) includes the 2020-21 pandemic year, which produced extreme enrollment disruptions:

- Were 2020-21 or 2021-22 GPRs excluded, down-weighted, or smoothed?
- Were post-COVID rebound years (2022-24) treated differently?
- Does the "weighted average" formulation systematically down-weight pandemic years?

A single anomalous year in a 9-data-point series can heavily influence both trend assessment and weighted averages.

#### 3f. No intermediate values published

The report never publishes:
- A table of GPR values for any school-grade combination
- Which cells were classified as "trending" vs. "stable"
- Which cells use "most recent year" vs. "weighted average" vs. "time-series"
- The actual forecast GPR values for any cell
- Any goodness-of-fit metrics for the chosen models
- Baseline-only (pre-development) per-school forecasts

#### 3g. Constraints and boundary conditions

- Are forecast GPRs bounded?
- Is forecast ADM floored at zero or some minimum viable school size?
- Is the sum of per-school forecasts reconciled against any district-wide total (top-down consistency check)?
- Are per-grade forecasts within a school reconciled against capacity or any school-level constraint?

Phoenix Academy's flat 25 ADM for all 11 years suggests some kind of floor or manual override exists, but it's never described as a general mechanism.

#### 3h. Manual adjustments / expert judgment

The report repeatedly emphasizes judgment ("each of these steps requires both data and judgement," "entirely dependent on which model best fits the local context," "based on local knowledge and our experience") but never discloses:
- Whether any GPR values were manually overridden
- Whether any school-grade forecasts were adjusted post-model
- What "local context" information led to specific modeling choices

### Trajectory Patterns Observed in the Output

The published ADM heatmap (p. 130) shows distinctly different trajectory shapes, implying different GPR treatments:

| Pattern | Schools | Implied GPR behavior |
|---|---|---|
| Steady decline, constant rate | Carrboro Elem (462->388), Rashkis (367->256) | Stable GPRs < 1.0 held constant, or trending GPRs extrapolated downward |
| Decline then flattening | Scroggs (366->307->314), Northside (335->289->289) | Trending GPRs that the time-series model dampens toward 1.0 in later years |
| Steady growth | Ephesus (343->423), Estes Hills (324->403) | Stable GPRs > 1.0 held constant (partially development-yield-driven) |
| Near flat | FPG (499->487), Morris Grove (371->371) | GPRs very close to 1.0, held constant |
| Growth then plateau | Glenwood (394->421->418), McDougle Elem (469->511->495) | GPRs > 1.0 early, declining toward 1.0 -- possibly time-series treatment |
| Absolute constant | Phoenix Academy (25->25) | Manual hold, not GPR-modeled |

These different shapes confirm the modelers made different decisions across schools. But without per-school baseline-only forecasts (which the report publishes only at the district level), we cannot separate GPR-driven trajectories from development-yield-driven trajectories at the school level.

---

## 4. Development Yield Pipeline

The report describes a multi-step multiplicative chain (pp. 113-114, 124-125). For each development project, for each forecast year:

```
NetYield(school, grade_band, year) =
    Units(d)
    x P_completion(status_d)                       [Step 2]
    x OccupancyRamp(status_d, year)                [Step 3]
    x TractOccupancyRate(tract_d)                  [Step 4]
    x AgeGroupHouseholdRate(tract_d, grade_band)   [Step 5]
    x MarketShare(?)                               [Step 6]
    - BaselineAlreadyCaptured(?)                   [Step 7]
```

### Step 1: Spatial Allocation -- Developments to Schools

**What the report states** (p. 124):

> "We first mapped them with Orange County parcel data. In case a development was spread across multiple parcels, the units from the development were allocated to the parcels based on the area. Those parcels were then joined to school attendance zones to determine which elementary, middle, and high schools would receive the new students."

**What's published:**
- The list of 42 developments with total units and project status (p. 122)
- The mapping of developments to schools in per-school detail tables (Appendix B)
- Some developments appear under multiple schools (e.g., "860 Weaver Dairy Road" contributes to both Estes Hills and Morris Grove), confirming multi-parcel/multi-zone allocation

**What's opaque:**
- The actual parcel-level split ratios for multi-zone projects
- Whether the parcel data and zone boundaries are the same vintage
- How "units" are defined for mixed-use developments (the 172 age-restricted units are excluded district-wide, but per-project exclusions are not shown)

**Consequence for program schools:** Development yield is assigned to schools **solely by attendance zone geography**. A development in Rashkis's zone contributes zero students to FPG, Glenwood's immersion strand, or any other program school -- even if families in that development would choose a program school via lottery.

### Step 2: Completion Probability by Status

**What the report states** (p. 124):

> "We assign a probability that units will be built and occupied during the forecast period based on project status and field verification."

**Published probability table:**

| Status | Probability |
|---|---|
| Under construction, field-verified near complete | 95-100% |
| Final Plans Review | 85% |
| Entitled | 75% |
| Formal Application Review | 60% |
| Concept Plan Complete | 40% |
| Waiting for Formal Dev Application Submittal | 30% |

Already-occupied units are removed entirely to avoid double-counting with baseline ADM.

**What's opaque:**
- **The 95-100% range:** Which projects got 95% vs. 100%? Per-project values not published
- **Empirical basis:** Are these derived from historical completion rates in Orange County? National data? Professional judgment? The report says "based on local knowledge and our experience throughout the country" but provides no empirical backing
- **Per-project deviations:** The text implies field-visit-based adjustment to individual projects. Were any assigned probabilities different from their status-category default?
- **Already-built unit counts:** The "Units Complete (Est.)" column reflects field observation but no systematic methodology is described

### Step 3: Delivery Timing and Occupancy Ramp

**What the report states** (p. 124):

> "Since new developments do not reach full occupancy in a single year, we apply a project-specific occupancy ramp schedule to spread occupied units across forecast years."

**Published occupancy ramp table (CHCCS, p. 124):**

| Year | Complete/Near | Under Constr. | Final Plans | Entitled | Formal App | Concept Plan | Waiting |
|---|---|---|---|---|---|---|---|
| 2026 | 80% | 0% | 0% | 0% | 0% | 0% | 0% |
| 2027 | 100% | 30% | 0% | 0% | 0% | 0% | 0% |
| 2028 | 100% | 50% | 30% | 20% | 0% | 0% | 0% |
| 2029 | 100% | 80% | 50% | 50% | 30% | 0% | 0% |
| 2030 | 100% | 100% | 80% | 80% | 50% | 20% | 10% |
| 2031 | 100% | 100% | 100% | 100% | 80% | 50% | 30% |
| 2032 | 100% | 100% | 100% | 100% | 100% | 80% | 90% |
| 2033+ | 100% | 100% | 100% | 100% | 100% | 100% | 100% |

**What's opaque:**
- **"Project-specific" vs. status-category:** The text says "project-specific occupancy ramp schedule" but the published table is by status category, not by project. Were any projects given a custom ramp different from their category?
- **Empirical basis for the ramp percentages:** Based on historical absorption rates? Local data? Professional assumption? No source or justification given
- **Reclassified projects:** Projects whose field-verified status differed from municipal records -- were these moved to a different column in the ramp table, or given custom schedules?
- **Cumulative vs. incremental:** The values increase monotonically, suggesting cumulative occupancy (e.g., "50% of eventual units occupied by this year"), but this is not stated
- **Interaction with per-project "First Occupancy" year:** Each development has an estimated first occupancy year. The ramp table uses absolute calendar years. How are these reconciled for projects with different start dates? Does the ramp shift to align with first occupancy, or are all projects keyed to the same calendar-year schedule?

### Step 4: Census Tract-Level Occupancy Rate

**What the report states** (p. 124):

> "We estimate occupied units using tract-level occupancy rates from the 2020 Census. These occupancy rates account for the share of housing units that are typically occupied versus vacant or used for short-term rental in each tract."

**What's published:** Nothing. No tract-level occupancy rates are given for any tract.

**What's opaque:**
- **Which Census variable:** "Occupancy rate" could mean total housing unit occupancy (H1 table), homeowner vacancy rate, renter vacancy rate, or a composite. Not specified
- **Actual values:** For Chapel Hill tracts, 2020 Census occupancy rates typically range from ~88% to ~97%, with lower rates near campus due to seasonal student housing. A 10-percentage-point difference translates directly to a ~10% difference in student yield
- **2020 Census vintage:** The 2020 Census was conducted during COVID, when occupancy patterns were abnormal in a college town. Were adjustments made?
- **Interaction with the ramp schedule:** The ramp represents "percent occupied by year." Is the Census occupancy rate applied multiplicatively on top of the ramp (ramp x occupancy rate), or does the ramp schedule already incorporate expected vacancy (with 100% in the ramp meaning the tract occupancy rate, not literal full occupancy)? If multiplicative, a project at 100% ramp x 93% Census occupancy = 93% occupied. If the ramp ceiling IS the occupancy rate, 100% already accounts for vacancy. The report describes these as separate steps, implying multiplicative, but this is never made explicit

### Step 5: Age-Group Household Rates (Student Yield per Occupied Unit)

**What the report states** (p. 125):

> "The percentage of households with children ages 5-10 years is applied to estimate elementary students from the new development units, ages 11-13 years for middle school, and ages 14-17 years for high school."

And from the general methodology (p. 114):

> "We apply yield by age groups to each of the units to match elementary, middle, and high school ages. These yields are based on our understanding of the type of units being built and historical patterns of yield from decennial census and local development data."

**What's published:** Nothing. No age-group household rates are given for any tract, and no per-unit student yield factors are published.

**What's opaque:**

This is the **most opaque step in the entire pipeline.**

- **Which Census variable?** "Percentage of households with children ages 5-10" is not a standard Census table. It could be derived from P14 (sex by age in households), ACS B09001 (population under 18 by age), or some combination. Not specified
- **Per-household or per-unit?** The text alternates between "households" and "units." If 100 units are occupied and 30% of households in the tract have children ages 5-10, does that mean 30 elementary students? Or 30 households with at least one child (potentially yielding more than 30 students)? The distinction between "% of households with children in age band" and "average number of children per household in age band" is substantial
- **Tract-level variation:** Different Chapel Hill tracts have very different household compositions. Near-campus tracts might have 5% households with school-age children; suburban tracts might have 35%. Development location dramatically affects yield, but values are not published
- **"Type of units" adjustment:** The methodology (p. 114) says yields are "based on our understanding of the type of units being built." This implies differentiation between single-family, townhouse, and apartment developments (single-family typically yields more students per unit). But no unit-type adjustment factors are published or described
- **"Local development data":** The methodology mentions "historical patterns of yield from decennial census and local development data." If actual student counts from past developments were used for calibration, this would be valuable -- but neither the data nor how it informed the rates is identified

### Step 6: Market Share Adjustment

**What the report states** (p. 114):

> "Finally, we apply market share adjustments to these net new students, as we know not all of them will end up in public schools."

**What's published:** Nothing. No market share adjustment factor is given.

**What's opaque:**
- **What value is used?** The report discusses CHCCS capturing ~67% of the K-12 market in Orange County (all school types), or ~91% of resident 5-17 year olds. These are different numbers measuring different things. Which is applied?
- **Geographic granularity:** Single district-wide factor, or per-zone?
- **Development-type variation:** Higher-income developments might see higher private school choice rates
- **Temporal variation:** The report warns that the Opportunity Scholarship Program expansion could shift market share. Is this factored into future-year yields, or held constant?
- **Sequencing:** Applied before or after the GPR baseline deduction?

### Step 7: GPR Baseline Deduction (Double-Count Removal)

**What the report states** (p. 125):

> "Since the GPR-based baseline model already incorporates some development-related enrollment increases, we adjust the student yields to ensure we are not double-counting student yields from new developments."

And from the development methodology (p. 113):

> "1. We begin by looking at GRPs and birth-to-kindergarten ratios for the attendance zone. Are we already seeing ratios over 1? If so, some in-migration is already occurring, so we need to adjust to make sure we don't double-count students."

**What's published:** Nothing. No deduction amounts, no methodology for determining the baseline-captured portion.

**What's opaque:**

This is the **second-most opaque step** and arguably the most consequential.

- **Attribution method:** How do they determine what portion of development yield is "already captured" in the GPR baseline? If a zone's GPRs have been above 1.0 due to ongoing development, the baseline already projects continued growth. Determining how much is "already in" requires decomposing the historical GPR into development-driven and non-development components -- a non-trivial attribution problem
- **Additive or multiplicative?**
  ```
  NetYield = GrossYield - BaselineAlreadyCaptured     (additive)
  NetYield = GrossYield x (1 - BaselineCaptureFraction)  (multiplicative)
  ```
- **Granularity:** Per-school, per-zone, or per-development?
- **Temporal variation:** If the GPR baseline captures development from earlier years (reflected in historical data) but not later years (new projects), the deduction should decline over the forecast period. Is this modeled?
- **Zones with declining GPRs:** If a zone's GPRs show decline, presumably the full development yield is additive with no deduction. The report doesn't describe this case
- **Magnitude:** The 10-year difference between baseline (-1,637) and development-adjusted (-1,070) forecasts is 566 students from 6,653 non-age-restricted units -- approximately **0.085 students per unit**. This extremely low ratio implies either very low gross yields, a very large baseline deduction, or both. The breakdown is never provided

### Reverse-Engineering the Combined Pipeline

The report publishes enough to estimate the combined effect of Steps 2-7 per school, but not to isolate any individual step:

| School | Dev Units Mapped | 10yr Net Yield | Net Students/Unit |
|---|---|---|---|
| Ephesus Elem | 1,931 | 49 | 0.025 |
| Estes Hills Elem | 1,506 | 58 | 0.039 |
| Morris Grove Elem | 1,360 | 42 | 0.031 |
| Scroggs Elem | 977 | 33 | 0.034 |
| Seawell Elem | 251 | 18 | 0.072 |
| Rashkis Elem | 1,197 | 16 | 0.013 |
| Glenwood Elem | 580 | 12 | 0.021 |
| Northside Elem | 596 | 11 | 0.018 |
| FPG Elem | -- | 0 | -- |
| McDougle Elem | -- | 0 | -- |
| Carrboro Elem | 59 (all built) | 0 | -- |

The wide variation (0.013 for Rashkis vs. 0.072 for Seawell) shows the pipeline produces very different results per zone. Possible explanations include different tract-level rates (Steps 4-5), different unit-type mixes, different baseline deductions (Step 7), or different completion probabilities (Step 2) -- but the report does not provide the information needed to distinguish between these.

Seawell's high per-unit yield (0.072) may reflect smaller, single-family-oriented projects (Homestead Gardens, Homestead Road Tri-Point, Newbury) in suburban tracts with high household-with-children rates. Rashkis's low yield (0.013) may reflect large mixed-use/apartment projects (Aura South Elliott, University Place) in near-campus tracts with low household-with-children rates. But this is inference from context; the report never discusses unit-type differentiation or publishes tract-level rates.

### Development Yield Pipeline: Opacity Summary

| Step | What's Published | What's Opaque |
|---|---|---|
| 1. Spatial allocation | Development list, zone assignments | Parcel-level split ratios for multi-zone projects |
| 2. Completion probability | Category-level table (30%-100%) | Per-project values, empirical basis |
| 3. Occupancy ramp | Full year-by-category table | Empirical basis, interaction with per-project timing |
| 4. Census occupancy rate | **Nothing** | Specific Census variable, per-tract values, COVID adjustment, interaction with ramp |
| 5. Age-group household rate | **Nothing** | Census variable, per-tract values, per-HH vs per-unit, unit-type adjustments |
| 6. Market share | **Nothing** | Value, geographic granularity, temporal variation |
| 7. GPR baseline deduction | **Nothing** | Method, magnitude, granularity, temporal variation |

Steps 4-7 are entirely opaque -- no parameter values, no intermediate results, no methodology detail. The final per-school net yields are published, but the pipeline between "occupied units" and "net new students" is a black box with at least four unpublished multipliers applied in sequence.

---

## 5. Program Schools vs. Traditional Schools

### The Report's Silence

The report applies a geographically uniform methodology -- GPR baseline + zone-based development yield -- to all schools. It **never acknowledges that some CHCCS schools are program schools or have program strands.** Specifically:

The words "program school," "dual language," "immersion," "lottery," "Mandarin," "Chinese," "Spanish," "magnet," "application-based," and "open enrollment" appear **zero times** in the 72-page report.

### CHCCS School Types (Not Discussed in Report)

| School | Type | Zone Status | Enrollment Mechanism |
|---|---|---|---|
| **FPG Elementary** | Full program (dual-language immersion) | Has nominal zone; most/all students enter via district-wide lottery | Lottery-based |
| **Phoenix Academy High** | Full program (alternative HS) | No attendance zone | Application-based, district-wide draw |
| **Glenwood Elementary** | Partial program | Has attendance zone + immersion strand drawing from outside zone | Zone + lottery |
| **Carrboro Elementary** | Partial program | Has attendance zone + Spanish dual-language strand | Zone + lottery |
| **Seawell Elementary** | Partial program | Has attendance zone + program strand | Zone + lottery |
| All other schools | Traditional | Full attendance zone | Zone-based |

### How the Data Shows They Were Treated

**Phoenix Academy High** (full program, alternative HS):
- ADM held **flat at 25 for all 11 years** (2025-26 through 2035-36)
- Development yield: 0
- Capacity utilization: 62% throughout, flat
- **Implied treatment:** Manual constant hold. The GPR and birth-to-K models were not applied. Never explained

**FPG Elementary** (full program, dual-language immersion):
- ADM varies year-by-year: 499 -> 491 -> 505 -> 503 -> 499 -> 498 -> 496 -> 495 -> 492 -> 489 -> 487
- Development yield: 0 (no developments mapped to its zone)
- **Implied treatment:** Full GPR model applied. The GPRs capture historical program-demand patterns rather than zone-demographic patterns, but the report treats these as interchangeable without discussion. The slight decline (-12 over 10 years, -2%) may reflect district-wide enrollment shrinkage filtering into lottery applicant pools

**Partial program schools** (Glenwood, Carrboro, Seawell):
- Full GPR model applied (year-by-year variation visible)
- Development yield assigned by zone geography only
- **Implied treatment:** The GPR blends zone students and program-strand students without disaggregation. Development yield captures only the zone-based component. The program-strand component -- affected by district-wide demographics, not zone-local demographics -- receives no separate treatment

### Methodological Gaps for Program Schools

1. **GPR conflation:** For partial program schools, the historical GPR blends two fundamentally different populations:
   - Zone students whose enrollment is driven by residential demographics, migration, and births in the zone
   - Program students whose enrollment is driven by district-wide demand for the program strand

   These may trend differently (program demand could hold steady while zone enrollment declines, or vice versa). The report never discusses whether this blending introduces forecast bias.

2. **Development yield misattribution:** Developments in other zones that might feed program-strand enrollment at Glenwood, FPG, etc. contribute zero yield to those schools. Conversely, the full zone-based yield for Glenwood/Carrboro/Seawell is attributed to the school, even though some zone-resident families might choose the program strand at a different school.

3. **Birth-to-K ratio interpretation:** For FPG, the birth-to-K ratio (however calculated -- see Section 2) reflects program demand rather than in-migration. Forecasting this ratio forward implicitly assumes program demand trends continue, which may or may not be valid.

4. **No explanation of the FPG vs. Phoenix Academy difference:** Both are full program schools with no meaningful geographic attendance zone. One gets full GPR modeling with year-by-year variation; the other gets a flat constant. The difference likely reflects school size (499 vs. 25 making GPR statistically meaningful for FPG but noisy for Phoenix) and school type (dual-language immersion vs. alternative high school). But the report never discusses this decision.

5. **Transition effects:** When FPG 5th graders move to middle school, they scatter across multiple middle schools rather than feeding the zone-expected middle school. The report says "correct progressions" are built but doesn't explain how program-school feeder patterns are handled at the elementary-to-middle transition.

---

## 6. Data Sources

From Appendix A (p. 150), the data inputs for CHCCS:

| Dataset | Source | Years | Granularity |
|---|---|---|---|
| Average Daily Membership Month 2 | CHCCS + NCDPI | 2015-16 through 2025-26 | By grade, by school. Student/address level for current year only |
| Attendance Zone Boundaries | CHCCS | 2025-26 | Geographic boundaries per school |
| Decennial Census | US Census Bureau | 2000, 2010, 2020 | Population by age, housing occupancy, persons per household |
| Population Estimates and Forecasts | NC OSBM | 2010 through 2060 | Orange County, by age |
| Births to resident mothers | NC DPH | 2011 through 2022 | Orange County (municipal-level for Chapel Hill/Carrboro referenced in text) |
| Approved Developments | Chapel Hill & Carrboro municipalities | Current (March 2026) | Per-project |
| Proposed Developments | Chapel Hill & Carrboro municipalities | Current (March 2026) | Per-project |

### Data Source Questions

- **Student-address data (current year only):** The report notes that student-level address data was provided for 2025-26 only (prior years are aggregate by grade by school). This data could reveal the actual zone vs. program split for each school, feeder patterns, and residence-to-school distances. It is unclear whether this data was used to calibrate any model parameter, or only as a quality check
- **Births geographic granularity:** NC DPH publishes at the county level. The text references "births in Chapel Hill and Carrboro" without explaining how municipal-level births are derived from county data (presumably by geocoding birth certificates or using municipality of residence on vital records)
- **2020 Census vintage:** Used for tract-level occupancy and household-with-children rates. The 2020 Census was conducted during COVID -- were any adjustments made for abnormal patterns in a college town?
- **No ACS data listed:** The American Community Survey (5-year estimates) provides more current demographic data at the tract level than the 2020 Decennial Census. The data sources table does not list ACS, suggesting the development yield pipeline relies on 2020 Census snapshots rather than rolling ACS estimates. (SAIPE data is referenced for district-level school-age population estimates, but not for the development yield pipeline)

---

## 7. Summary of Opacity

### Fully Published Parameters and Data

- List of 42 developments with units and status
- Per-school development-to-school mapping (Appendix B)
- Completion probability by status category
- Occupancy ramp schedule by status category and year
- Final per-school ADM forecast (development-adjusted) for all 11 years
- Per-school 5-year and 10-year net development yield
- Capacity utilization per school per year
- District-level birth-to-K ratio (single chart)
- District-level ADM trends (historical and forecast)

### Described Qualitatively but Not Quantified

- GPR model decision criterion (trending vs. stable)
- "Weighted average" parameters
- "Time series forecasting methods" specification
- School-level transition / feeder pattern handling
- COVID-era data treatment
- Manual adjustments and expert judgment
- Unit-type differentiation in yield calculation

### Entirely Opaque (No Values, No Methodology Detail)

- Per-school birth-to-K ratio derivation and denominator
- Per-school-grade GPR values (historical and forecast)
- Which school-grade cells received which GPR method
- Tract-level Census occupancy rates used
- Tract-level age-group household rates used
- Market share adjustment factor(s)
- GPR baseline deduction method and magnitude
- Baseline-only (pre-development) per-school forecasts
- Goodness-of-fit metrics for any model component
- Program school treatment rationale

### What Would Be Needed for Full Reproducibility

To reproduce the per-school forecasts, one would need at minimum:
1. The 10-year Month 2 ADM table by grade by school (available from NCDPI)
2. The per-school-grade GPR method choice (constant vs. time-series) and parameters
3. The birth data at whatever geographic level was used, and the per-school birth-to-K calculation method
4. The feeder-pattern weights for school transitions
5. The per-development parcel-to-zone allocation weights
6. The per-tract Census occupancy rate and age-group household rate values
7. The market share adjustment factor(s)
8. The GPR baseline deduction method and per-zone/per-project amounts

Items 2-8 are not published. Without them, the forecast is a black box: inputs (historical ADM, birth data, development list) and outputs (per-school ADM forecast) are visible, but the ~240+ modeling decisions and unpublished parameters connecting them are not.
