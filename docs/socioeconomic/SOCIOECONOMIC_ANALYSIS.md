# Socioeconomic Analysis: CHCCS Attendance Zones

## Purpose

This analysis provides neighborhood-level socioeconomic data for each CHCCS
elementary school attendance zone. It uses US Census Bureau data to characterize
the populations served by each school, enabling informed discussion about the
equity implications of school closure decisions.

## Data Sources

### ACS 5-Year Estimates (2022, Block Group Level)

**API Endpoint:** `https://api.census.gov/data/2022/acs/acs5`
**Geography:** Block groups in Orange County, NC (FIPS 37135)

| Census Table | Description | Key Metric |
|---|---|---|
| B01001 | Population by age and sex | % elementary-age children (5-9) |
| B03002 | Hispanic origin by race | Racial/ethnic composition |
| B19013 | Median household income | Income levels by block group |
| B19001 | Household income brackets | Income distribution (16 bins) |
| C17002 | Ratio of income to poverty level | % below 185% poverty (FRL proxy) |
| B25003 | Tenure (owner vs. renter) | % renter-occupied |
| B25044 | Tenure by vehicles available | % zero-vehicle households |
| B11003 | Family type by presence of children | % single-parent families |

### 2020 Decennial Census P.L. 94-171 (Block Level)

**API Endpoint:** `https://api.census.gov/data/2020/dec/pl`
**Geography:** Census blocks in Orange County, NC

| Census Table | Description |
|---|---|
| P1 | Total population by race (7 categories) |
| P2 | Hispanic/Latino origin by race |

Used exclusively for dot-density visualization (highest spatial resolution).

### TIGER/Line Geometries

- **Block groups:** `https://www2.census.gov/geo/tiger/TIGER2023/BG/tl_2023_37_bg.zip`
- **Blocks:** `https://www2.census.gov/geo/tiger/TIGER2020PL/STATE/37_NORTH_CAROLINA/37135/tl_2020_37135_tabblock20.zip`

### Local Data

- **Attendance zone boundaries:** `data/raw/properties/CHCCS/CHCCS.shp` (dissolved by ENAME field)
- **District boundary:** `data/cache/chccs_district_boundary.gpkg`
- **School locations:** `data/cache/nces_school_locations.csv` (NCES EDGE 2023-24)
- **Residential parcels:** `data/raw/properties/combined_data_polys.gpkg` (for dasymetric dot placement)

## Variable Definitions

| Variable | Census Source | Definition |
|---|---|---|
| `total_pop` | B01001_001E | Total population |
| `pct_elementary_age` | B01001_004E + B01001_028E | % of population aged 5-9 |
| `pct_minority` | 1 - (B03002_003E / B03002_001E) | % non-white non-Hispanic |
| `pct_black` | B03002_004E / B03002_001E | % Black non-Hispanic |
| `pct_hispanic` | B03002_012E / B03002_001E | % Hispanic/Latino |
| `median_hh_income` | B19013_001E | Median household income (dollars) |
| `pct_below_185_poverty` | Sum(C17002_002-007) / C17002_001E | % below 185% FPL (FRL eligibility proxy) |
| `pct_renter` | B25003_003E / B25003_001E | % renter-occupied housing units |
| `pct_zero_vehicle` | (B25044_003E + B25044_010E) / B25044_001E | % households with zero vehicles |
| `pct_single_parent` | (B11003_010E + B11003_016E) / families-with-kids | % single-parent among families with children |
| `pct_low_income` | Sum(B19001_002-010) / B19001_001E | % households with income < $50,000 |

## Methodology

### Area-Weighted Interpolation

Census block groups do not align with CHCCS attendance zone boundaries. To estimate
demographics for each school zone, we use **area-weighted interpolation**:

1. Compute the geometric intersection of each block group with each attendance zone
2. Calculate the proportion of each block group's area that falls within each zone
3. Allocate block group population proportionally:
   `zone_pop = Sum(bg_pop x overlap_area / bg_area)`

**Assumption:** Population is uniformly distributed within each block group. This is
a standard approach but introduces error where population density varies significantly
within a block group (e.g., if one half is residential and the other is commercial).

### Median Income Estimation

Median household income for each zone is approximated as the population-weighted
average of block group medians, which is less precise than true median calculation
but provides a reasonable estimate given the available data.

### Dot-Density Map

The racial dot-density map uses 2020 Decennial Census block-level data (the highest
available spatial resolution). Each dot represents approximately 5 people of a given
racial/ethnic group.

**Dasymetric refinement:** When residential parcel polygon data is available, dots are
constrained to the intersection of Census blocks with residential parcels. This prevents
dots from being placed in parks, roads, commercial areas, or other non-residential land.
When parcels are unavailable, dots are placed randomly within Census block boundaries.

## Limitations

1. **ACS Margins of Error:** ACS 5-Year estimates have sampling error, particularly
   for small block groups. Margins of error are not displayed but should be considered
   when interpreting small differences between zones.

2. **Disclosure Avoidance:** 2020 Decennial block data includes differential privacy
   noise injected by the Census Bureau. This can cause small counts to be inaccurate
   at the block level. Block data is used only for dot-density visualization, not
   statistical reporting.

3. **5-Year Rolling Average:** ACS 2022 5-Year estimates represent data collected
   2018-2022, not a single point in time.

4. **Attendance Zone vs. Actual Enrollment:** Demographics of an attendance zone
   describe the resident population, not actual school enrollment. Families may
   choose charter, private, or magnet schools, and transfer policies allow enrollment
   outside the home zone.

5. **Area-Weighting Assumptions:** Uniform population distribution within block groups
   is assumed. Dasymetric refinement at the block level (for dots) partially addresses
   this but is not applied to block group statistics.

6. **Temporal Mismatch:** ACS data (2018-2022), Decennial data (2020), and attendance
   zone boundaries (current) may not perfectly align temporally.

## Results: Per-School-Zone Demographics

| school | total_pop | median_hh_income | pct_below_185_poverty | pct_minority | pct_black | pct_hispanic | pct_renter | pct_zero_vehicle | pct_single_parent | pct_elementary_age |
| --- | --- | --- | --- | --- | --- | --- | --- | --- | --- | --- |
| Carrboro Elementary | 12016 | 67071 | 35.6 | 25.6 | 10.3 | 4.6 | 65.1 | 6.5 | 27.1 | 2.9 |
| Ephesus Elementary | 7617 | 115973 | 17.8 | 49.0 | 9.7 | 17.8 | 38.6 | 3.9 | 28.7 | 4.0 |
| Estes Hills Elementary | 8248 | 138600 | 18.5 | 31.8 | 4.1 | 6.6 | 42.5 | 6.1 | 20.1 | 4.2 |
| Glenwood Elementary | 2175 | 72162 | 12.7 | 26.5 | 9.6 | 4.8 | 34.8 | 1.9 | 6.8 | 1.9 |
| McDougle Elementary | 9648 | 101125 | 19.9 | 26.7 | 6.3 | 10.9 | 32.4 | 3.9 | 14.5 | 7.4 |
| Morris Grove Elementary | 9666 | 139112 | 12.5 | 41.5 | 14.0 | 8.5 | 18.0 | 2.1 | 17.4 | 6.0 |
| Northside Elementary | 16794 | 95503 | 34.6 | 32.0 | 14.3 | 4.6 | 54.1 | 8.1 | 18.2 | 4.3 |
| Rashkis Elementary | 8481 | 102369 | 23.6 | 41.0 | 16.2 | 5.4 | 58.4 | 4.6 | 20.2 | 4.4 |
| Scroggs Elementary | 12219 | 71994 | 26.0 | 36.3 | 11.8 | 6.4 | 51.3 | 9.4 | 16.1 | 3.4 |
| Seawell Elementary | 4379 | 97365 | 28.9 | 40.4 | 11.5 | 7.9 | 50.4 | 7.4 | 39.2 | 5.8 |

*All percentages rounded to 1 decimal place. Population counts are area-weighted estimates.*

## Intellectual Honesty Notes

- This analysis uses the best available public data but is subject to the limitations
  described above. Small differences between zones (< 5 percentage points) may not be
  statistically significant given ACS margins of error.
- Median household income is approximated, not computed from microdata.
- The 185% poverty threshold is a proxy for Free/Reduced Lunch eligibility. Actual FRL
  enrollment may differ due to application rates, direct certification, and CEP status.
- Zone boundaries represent geographic districts; actual school populations differ due
  to school choice, transfers, and magnet/charter enrollment.

## Stage 2: Planned Analysis (Future Work)

**Socioeconomic x School Desert / Walk Zone Overlay**

Stage 2 will cross-reference the Census demographic data from this analysis with:
- School desert masks (travel-time increase areas from `school_desert_grid.csv`) per closure scenario
- Walk zone masks (from CHCCS shapefile `ESWALK=="Y"` features)

This will answer:
- "What are the income, racial, vehicle-access, and poverty profiles of households
  whose travel time increases under each school closure scenario?"
- "What are the demographics of families within walk zones of schools proposed for closure?"

Stage 2 plans will be developed separately after Stage 1 is validated.

---

*Generated by `src/school_socioeconomic_analysis.py`*
*Census data accessed via api.census.gov*
