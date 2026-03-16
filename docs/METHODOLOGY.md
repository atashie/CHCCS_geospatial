# How These Maps Were Built: Data Sources, Methods, and Limitations

This document explains how the three interactive maps in this project were created — what data goes into them, how that data is processed, and what the results can and cannot tell you. It is written for community members (parents, teachers, board members) who want to understand the analysis without needing GIS or programming expertise.

Each map section follows the same pattern: what you see on the map, where the data comes from, how it was processed, and what the key limitations are.

**Jump to a map:**
- [Map 1: School Socioeconomic Map](#map-1-school-socioeconomic-map)
- [Map 2: School Closure Impact Map](#map-2-school-closure-impact-map)
- [Map 3: Environmental Analysis Map](#map-3-environmental-analysis-map)

---

## Quick Glossary

| Term | Plain-English Definition |
|------|--------------------------|
| **AADT** | Annual Average Daily Traffic — the estimated number of vehicles passing a point on a road in an average day, measured by NCDOT counting stations. |
| **ACS (American Community Survey)** | An ongoing U.S. Census Bureau survey that collects detailed demographic, economic, and housing data every year. The "5-Year" version pools five years of responses for more reliable estimates at small geographies. |
| **Attendance zone** | The geographic boundary that determines which school a student is assigned to by default, based on home address. Set by the school district. |
| **Census block** | The smallest geographic unit used by the Census — typically a city block or equivalent (~500 people on average). |
| **Census block group** | A cluster of Census blocks (~1,500 people on average). The ACS publishes most detailed data at this level, not individual blocks. |
| **Choropleth map** | A map where geographic areas are shaded by color to represent the value of a variable (e.g., darker green = higher income). |
| **Dasymetric mapping** | A technique for redistributing data from larger areas (like block groups) to smaller areas (like blocks) using additional information — in our case, the locations of residential parcels. |
| **Dijkstra's algorithm** | A well-known algorithm for finding the shortest (or fastest) path through a network of roads, accounting for speed and distance. |
| **Dot-density map** | A map where each dot represents a fixed number of people, placed randomly within the area where those people live. Shows spatial patterns of population groups. |
| **FEMA flood zone** | A geographic area identified by the Federal Emergency Management Agency as having a defined level of flood risk (e.g., 1% annual chance). |
| **FRL (Free and Reduced-Price Lunch)** | A federal program for students from lower-income families. The income threshold is 185% of the federal poverty level. |
| **TRAP (Traffic-Related Air Pollution)** | Pollutants (nitrogen oxides, fine particulates, ultrafine particles) concentrated near high-traffic roads that decay with distance. |
| **UHI (Urban Heat Island)** | The phenomenon where built-up areas are warmer than surrounding vegetated or rural areas, due to impervious surfaces absorbing and re-radiating heat. |

---

## Map 1: School Socioeconomic Map

**File:** `school_socioeconomic_map.html`

### 1.1 What This Map Shows

This map displays demographic characteristics of the neighborhoods around each of the 11 CHCCS elementary schools. It includes:

- **Six choropleth layers** showing different demographic measures (income, poverty, minority percentage, zero-vehicle households, elementary-age children, and young children) at both block group and estimated block level
- **A dot-density layer** showing the racial/ethnic composition of each area, with each dot representing a group of residents
- **Attendance zone boundaries** showing which neighborhoods are assigned to which school
- **School markers** for all 11 elementary schools

You can toggle layers on and off using the layer control to explore different aspects of each attendance zone.

### 1.2 Data Sources

| Data | Source & Link | What It Tells Us | Freshness | Key Limitations |
|------|---------------|------------------|-----------|-----------------|
| Demographics (income, poverty, age, vehicles, tenure, family structure) | [ACS 5-Year 2020–2024](https://www.census.gov/programs-surveys/acs) — 42 variables from 8 Census tables | Detailed socioeconomic characteristics at the block group level | 5-year average ending 2024 (released Dec 2025) | Survey-based estimates with margins of error; not a full count |
| Race/ethnicity | [2020 Decennial Census P.L. 94-171](https://www.census.gov/programs-surveys/decennial-census/about/rdo.html) | Block-level racial/ethnic breakdown | April 2020 snapshot | Subject to differential privacy noise injection; population may have shifted since 2020 |
| Geographic boundaries (block groups, blocks) | [Census TIGER/Line](https://www.census.gov/geographies/mapping-files/time-series/geo/tiger-line-file.html) | Shapes for mapping Census data | 2024 block groups, 2020 blocks | — |
| School locations | [NCES EDGE 2023–24](https://nces.ed.gov/programs/edge/geographic/schoollocations) | Verified coordinates for all 11 schools | 2023–24 school year | — |
| Attendance zones | CHCCS district shapefile | Which neighborhoods feed into which school | Current as of download date | May become outdated if district rezones |
| Residential parcels | Orange County GIS | Where homes are located (used for dasymetric weighting) | Current as of data extract | Includes only residential-classified parcels |

### 1.3 How Demographics Are Estimated

**The problem:** The Census publishes detailed income, poverty, and housing data only at the block group level — areas of roughly 1,500 people. But CHCCS attendance zones don't follow block group boundaries. A single block group might be split across two or three school zones. We need to figure out how much of each block group's data belongs to each zone.

**The solution:** Dasymetric areal interpolation — a method that uses the locations of residential parcels to distribute Census data more accurately than simple area-based splitting.

```
                    ┌──────────────┐     ┌──────────────────┐
                    │ Census Data  │     │ Residential      │
                    │ (block group │     │ Parcel Locations  │
                    │  totals)     │     │ (Orange County)   │
                    └──────┬───────┘     └────────┬─────────┘
                           │                      │
                           ▼                      ▼
                    ┌──────────────────────────────────────┐
                    │  Overlay: Split block groups by      │
                    │  attendance zone boundaries          │
                    └──────────────────┬───────────────────┘
                                       │
                                       ▼
                    ┌──────────────────────────────────────┐
                    │  Weight each fragment by its share   │
                    │  of the block group's residential    │
                    │  parcel area                         │
                    └──────────────────┬───────────────────┘
                                       │
                                       ▼
                    ┌──────────────────────────────────────┐
                    │  Sum weighted values to get          │
                    │  attendance zone totals              │
                    └──────────────────────────────────────┘
```

**Why not just use area?** If a block group is 60% inside Zone A and 40% inside Zone B by area, you might assume 60% of its population lives in Zone A. But what if Zone A's portion is mostly a park or commercial area? The residential parcels tell us where people actually live, giving a much better estimate.

<details>
<summary>Technical detail: Dasymetric formula</summary>

For each Census variable *Y* measured at block group *s*, the estimated value for attendance zone *t* is:

```
Y_t = Σ_s (R_st / R_s) × Y_s
```

Where:
- *R_st* = total residential parcel area in the fragment where block group *s* overlaps zone *t*
- *R_s* = total residential parcel area in the entire block group *s*
- *Y_s* = the Census value for block group *s*

If a block group has no residential parcels (e.g., entirely commercial), the formula falls back to simple area weighting: *A_st / A_s*.

All weights are clipped to [0.0, 1.0] to prevent over-allocation.

**References:**
- Mennis, J. (2003). Generating surface models of population using dasymetric mapping. *The Professional Geographer*, 55(1).
- Eicher, C. L., & Brewer, C. A. (2001). Dasymetric mapping and areal interpolation: Implementation and evaluation. *Cartography and Geographic Information Science*, 28(2).

</details>

### 1.4 Map Layers

**Choropleth layers** (each available at block group and estimated block level):

| Layer | What it shows | Color scale |
|-------|--------------|-------------|
| Median Income | Estimated median household income | Yellow → Green (higher = greener) |
| % Below 185% Poverty | Share of population below 185% of the federal poverty level (the FRL eligibility threshold) | Yellow → Red (higher = redder) |
| % Minority | Share of population that is not White Non-Hispanic | Purple → Blue → Green |
| % Zero-Vehicle HH | Share of households with no vehicle available | Light Red → Dark Red |
| % Elementary Age (5–9) | Share of total population aged 5–9 | Blue → Purple |
| % Young Children (0–4) | Share of total population aged 0–4 | Purple → Red |

**Dot-density layer:** Each dot represents a group of residents, color-coded by race/ethnicity (White, Black, Hispanic/Latino, Asian, Multiracial, Native American/Other). Dots are placed randomly within the Census block where people live — the exact dot positions do not represent actual home addresses.

**Other layers:** School markers, attendance zone boundaries, affordable housing locations, planned development markers.

### 1.5 Key Limitations

1. **ACS data is a 5-year average, not a snapshot.** The income, poverty, and housing data reflects conditions averaged across 2020–2024, not a single point in time.

2. **Zone demographics ≠ school enrollment.** The map shows who *lives* in each attendance zone, not who *attends* each school. School choice, magnet programs, private schools, and transfers mean actual enrollment demographics can differ significantly.

3. **Margins of error are not displayed.** All ACS estimates have sampling uncertainty. Small-area estimates (especially for zero-vehicle households or narrow poverty bands) can have wide margins of error.

4. **Median income is approximated.** When a block group spans multiple zones, the median income for each zone fragment is estimated using the income distribution brackets — it is not a directly measured median for that zone.

5. **2020 race/ethnicity data includes differential privacy noise.** The Census Bureau added random noise to the 2020 Decennial data to protect individual privacy. At the block level, this can distort counts, especially for small populations.

6. **Dot placement is approximate.** Dots in the dot-density map are placed randomly within each Census block. They show general patterns, not exact locations of individual residents.

7. **185% poverty is a proxy for FRL eligibility, not actual FRL enrollment.** The Census poverty ratio (185% of the federal poverty level) approximates the income threshold for Free and Reduced-Price Lunch, but actual FRL eligibility also considers participation in SNAP, TANF, and other programs.

### 1.7 Step-by-Step Walkthrough

For a detailed, visual explanation of every step — from Census data loading through dasymetric areal interpolation, dot-density generation, and zone aggregation — see the **[Socioeconomic Methodology Walkthrough](../assets/maps/socioeconomic_methodology.html)**. This interactive scrollytelling page walks through the full pipeline using Northside Elementary as an illustrative example.

---

### 1.8 MLS Home Sales Data

The socioeconomic map also incorporates MLS (Multiple Listing Service) home sales data to show recent real estate market conditions across attendance zones.

**Data:** 2,193 closed residential sales from the Triangle MLS, covering 2023-2025. Each record includes the sale address, close price, and bedroom count (from the "Bedrooms Total" field in the raw CSV).

**Geocoding:** Addresses are geocoded using the U.S. Census Bureau batch geocoding API (primary) with OpenStreetMap Nominatim as a fallback for unmatched records. Successfully geocoded sales are spatially joined to attendance zones and Census blocks.

**What you see:** MLS sales are spatially joined to all zone types (attendance zones, walk zones, nearest walk/bike/drive). The interactive map provides a consolidated **"Housing Market (2023-2025)"** toggle that displays a 2x2 chart grid per zone: Homes Sold, Median Price, Median Price/SqFt, and Bedroom Distribution histogram. Charts update dynamically when switching zone types. Individual MLS markers show sale price, close date, and bedroom count on hover.

**Privacy:** Map markers show sale price, close date, and bedroom count on hover — addresses are not displayed. Point locations are approximate (interpolated along road centerlines by the geocoder, not exact rooftops or parcel centroids), which provides an additional degree of location privacy.

**Key limitations:**
- **MLS-only:** Does not include for-sale-by-owner (FSBO) or off-market transactions
- **Not point-in-time:** Sales span three years (2023-2025), mixing different market conditions
- **Small samples:** Some Census blocks have very few sales, making block-level medians unreliable
- **Geocoding is approximate:** Points represent interpolated road-segment positions, not exact property locations. This is intentional — it avoids pinpointing individual homes while remaining accurate enough for zone/block aggregation
- **No property controls:** Price differences between areas may reflect housing stock (size, age, condition) rather than location alone

---

### 1.9 Planned Developments Data

The socioeconomic map includes two planned development layers, each from a different source, displayed as separate metrics under the HOUSING radio-button group.

**1. Planned Developments (CH Active Dev):** 36 projects hand-transcribed from the Town of Chapel Hill [Active Development](https://www.chapelhillnc.gov/Business-and-Development/Active-Development) page on March 12, 2026 (`data/raw/properties/planned/CH_Development-3_26.csv`). Each record includes the project name, address, and estimated unit count. Covers Chapel Hill only; no student yield estimates. Bar charts show total expected units and number of developments per zone.

**2. Planned Developments (SAPFOTAC):** 21 future residential projects from the CHCCS Student Attendance Projections and Facility Optimization Technical Advisory Committee (SAPFOTAC) 2025 Annual Report (certified June 3, 2025; `data/raw/properties/planned/SAPFOTAC_2025_future_residential.csv`). Covers both Chapel Hill and Carrboro. Includes projected student yields (elementary, middle, high) based on district student generation rates. Bar charts show total projects, total units, and projected elementary students per zone.

**Geocoding:** Both datasets are geocoded using the same two-stage pipeline as MLS data: U.S. Census Bureau batch geocoding API (primary) with OpenStreetMap Nominatim fallback. CH Active Dev projects are stored as a GeoPackage (`data/cache/planned_developments.gpkg`); SAPFOTAC coordinates are stored directly in the CSV.

**What you see:** Each dataset appears as its own metric with color-coded circle markers (blue → yellow → orange → red by unit count). Click a marker for project details; SAPFOTAC popups include student yield breakdowns. Projects are spatially joined to the selected zone type for bar-chart aggregation.

**Key limitations:**
- **Projects may not all proceed:** Planned and approved developments are not guaranteed to be built. Market conditions, financing, or regulatory changes may alter or cancel projects.
- **Unit counts are estimates:** The number of housing units comes from planning documents and may change during construction or final approval.
- **Geocoding is approximate:** Points represent interpolated road-segment positions from the Census geocoder, not exact project boundaries or parcel centroids.
- **Single data snapshots:** Each dataset reflects a point-in-time extract — CH Active Dev from March 2026, SAPFOTAC from June 2025 — and does not automatically update.
- **Datasets overlap but are not deduplicated:** Some projects appear in both sources. The two layers are independent; selecting one does not affect the other.
- **SAPFOTAC student yields are model estimates:** Projected student counts are based on student generation rates applied to planned unit counts, not actual enrollment. Actual yields will vary with household composition and market absorption.

---

## Map 2: School Closure Impact Map

**File:** `school_closure_analysis.html`

### 2.1 What This Map Shows

This map simulates what would happen to travel times and traffic patterns if individual CHCCS elementary schools were closed. It has two tabs:

- **Tab 1 — Travel Time Heatmap:** Shows how long it takes to reach the nearest open school from every point in the district, for each closure scenario and travel mode (walk, bike, drive)
- **Tab 2 — Traffic Burden:** Shows how many children would travel along each road segment, comparing the baseline (all schools open) to each closure scenario

### 2.2 Data Sources

| Data | Source & Link | What It Tells Us | Freshness | Key Limitations |
|------|---------------|------------------|-----------|-----------------|
| Road network | [OpenStreetMap via OSMnx](https://www.openstreetmap.org/) | All roads with type classifications (residential, primary, etc.) | Current as of download date | Community-maintained; may have minor gaps or misclassifications |
| Children counts (ages 0–4, 5–9) | [ACS 5-Year 2020–2024](https://www.census.gov/programs-surveys/acs) | How many young children live in each block group | 5-year average ending 2024 | Survey estimates; actual numbers may differ |
| Census blocks & block groups | [Census TIGER/Line](https://www.census.gov/geographies/mapping-files/time-series/geo/tiger-line-file.html) | Geographic boundaries for distributing population | 2020/2024 vintages | — |
| Residential parcels | Orange County GIS | Where homes are located (for distributing children to specific locations) | Current as of data extract | — |
| Attendance zones | CHCCS district shapefile | Which school each area is assigned to | Current as of download date | May become outdated if district rezones |
| Walk zones | CHCCS district shapefile | Areas designated as walkable to each school | Current as of download date | Definitions may not reflect actual walkability |
| School locations | [NCES EDGE 2023–24](https://nces.ed.gov/programs/edge/geographic/schoollocations) | Verified coordinates for all 11 schools | 2023–24 school year | — |
| District boundary | [Census TIGER/Line](https://www.census.gov/geographies/mapping-files/time-series/geo/tiger-line-file.html) | Outer boundary of the CHCCS district | Current | — |

### 2.3 How Travel Times Are Computed

The analysis lays a grid of approximately 16,000 points across the district at 100-meter spacing. For each point, it computes the fastest travel time to every school using the actual road network.

```
     ┌──────────────────┐     ┌──────────────────┐
     │ OpenStreetMap     │     │ Speed Assignment  │
     │ Road Network      │────▶│ (by road type     │
     │                   │     │  and travel mode)  │
     └──────────────────┘     └────────┬───────────┘
                                       │
                                       ▼
                              ┌──────────────────┐
                              │ Dijkstra's        │
                              │ Algorithm:        │
                              │ Find fastest route│
                              │ from each school  │
                              │ to every road node│
                              └────────┬──────────┘
                                       │
                                       ▼
                              ┌──────────────────┐
                              │ Grid Snapping:    │
                              │ Connect each      │
                              │ 100m grid point   │
                              │ to nearest road   │
                              └────────┬──────────┘
                                       │
                                       ▼
                              ┌────────────────────┐
                              │ For each scenario:  │
                              │ Find nearest OPEN   │
                              │ school's travel time│
                              └─────────────────────┘
```

**Speed model:**

| Travel Mode | Speed | Basis |
|-------------|-------|-------|
| Walk | 2.5 mph (1.12 m/s) | Between the MUTCD 4E.06 design minimum (3.5 ft/s = 2.4 mph) and Fitzpatrick et al. (2006) measured range (3.7–4.2 ft/s = 2.5–2.9 mph). Conservative for K–5 children. |
| Bike | 12 mph (5.36 m/s) | Standard planning assumption for mixed-age cyclists |
| Drive | 10–60 mph | Varies by road type (see details below) |

<details>
<summary>Technical detail: Drive speed table and Dijkstra implementation</summary>

**Effective driving speeds by road type:**

| Road Type | Speed (mph) |
|-----------|-------------|
| Motorway | 60 |
| Motorway link | 50 |
| Trunk | 40 |
| Trunk link | 35 |
| Primary | 30 |
| Primary link | 25 |
| Secondary | 25 |
| Secondary link | 22 |
| Tertiary | 22 |
| Tertiary link | 18 |
| Residential | 18 |
| Living street | 10 |
| Service | 10 |
| Unclassified | 18 |

These are *effective* speeds — lower than posted speed limits to account for stops, turns, congestion, and school-zone slowdowns.

**Access leg:** Each grid point must connect to the nearest road. This "access leg" uses a reduced speed (90% for walk, 80% for bike, 20% for drive) to account for parking, crossing streets, or navigating to the road network. Maximum access distance is 200m (twice the grid resolution).

**Dijkstra's algorithm** runs once from each school node, computing the fastest travel time to every node in the road network. Results are cached to avoid recomputation.

**References:**
- MUTCD Section 4E.06 — pedestrian walking speed for signal timing
- Fitzpatrick, K., et al. (2006). *Improving Pedestrian Safety at Unsignalized Crossings.* FHWA-HRT-06-042.
- Highway Capacity Manual, 6th Ed., Ch. 16

</details>

### 2.4 How Traffic Impact Is Estimated

The traffic tab estimates how many children would travel along each road segment under different scenarios. It uses drive-mode routing only.

```
  ┌───────────────────┐     ┌───────────────────┐
  │ ACS Children      │     │ Residential        │
  │ (ages 0-4, 5-9)   │     │ Parcels            │
  │ by block group     │     │ (Orange County)    │
  └────────┬──────────┘     └─────────┬──────────┘
           │                          │
           ▼                          ▼
  ┌──────────────────────────────────────────────┐
  │ Dasymetric distribution:                      │
  │ Block group → Block → 100m grid pixel         │
  │ (weighted by residential parcel area)         │
  └───────────────────────┬──────────────────────┘
                          │
                          ▼
  ┌──────────────────────────────────────────────┐
  │ For each pixel with children:                 │
  │ Trace shortest drive route to assigned school │
  │ (or nearest open school if assigned is closed)│
  └───────────────────────┬──────────────────────┘
                          │
                          ▼
  ┌──────────────────────────────────────────────┐
  │ Count children on each road segment          │
  │ Compare baseline vs. closure scenario        │
  └──────────────────────────────────────────────┘
```

**How children are distributed:** ACS data tells us how many children aged 0–4 and 5–9 live in each block group. Since block groups are too large, we distribute those children in two steps:

1. **Block group → block:** Allocate children proportionally based on how much residential parcel area each block contains relative to its parent block group
2. **Block → 100m pixel:** Further distribute each block's children to the grid pixels that fall within it, proportionally by area overlap

<details>
<summary>Technical detail: Dasymetric child distribution formula</summary>

Stage 1 — Block group to block:

```
block_children = bg_children × (block_residential_area / bg_residential_area)
```

Falls back to simple area ratio when no residential parcels are available:

```
block_children = bg_children × (block_area / bg_area)
```

Stage 2 — Block to pixel:

Each block's children are distributed to the 100m grid pixels based on the fractional area of the block that each pixel covers.

</details>

### 2.5 Map Layers and Controls

**Tab 1 — Travel Time:**
- **Scenario selector:** Choose which school to close (or "All Open" baseline)
- **Mode selector:** Walk, Bike, or Drive
- **Heatmap overlay:** Color-coded grid showing travel time to nearest open school (blue = short, red = long)
- **Walk zone overlays:** CHCCS-designated walkable areas (turn red when their school is closed)
- **School markers:** Open schools shown normally; closed school shown with X

**Tab 2 — Traffic:**
- **Scenario selector:** Choose which school to close
- **Routing mode:** "Current school zone" (routes to assigned school) or "Closest school by driving" (routes to nearest school by road)
- **Road segments:** Color-coded by number of children routed along them (thicker/redder = more children)
- **Walk zone masking:** Children inside walk zones are tracked separately

### 2.6 Key Limitations

1. **No capacity constraints.** The model assumes every school can absorb unlimited additional students. In reality, receiving schools may not have enough classrooms or staff.

2. **Static road network.** Travel times reflect current road conditions. They don't account for construction, seasonal changes, or new roads being built.

3. **Children counts are estimates.** The ACS-based child distribution is a statistical estimate, not an enrollment roster. Actual numbers of school-age children may differ.

4. **No school choice modeled.** All children are routed to either their assigned zone school or the nearest open school. In practice, families may choose magnet programs, private schools, or other options.

5. **Theoretical shortest paths.** Routes assume drivers take the fastest path. Real-world route choices vary based on familiarity, traffic signals, personal preference, and congestion.

6. **No elevation or hills.** Walk and bike speeds are constant — the model doesn't slow down for uphill segments, which matters in Chapel Hill's hilly terrain.

7. **Walk zone definitions may be outdated.** The walk zone boundaries used in the analysis may not reflect the most current CHCCS designations.

### 2.7 Step-by-Step Walkthrough

For a detailed, visual explanation of every step — from road network loading through Dijkstra, edge snapping, dasymetric child distribution, traffic aggregation, and limitations — see the **[School Closure Methodology Walkthrough](../assets/maps/closure_methodology.html)**. This interactive scrollytelling page walks through the full pipeline using Northside Elementary as an illustrative example.

---

## Map 3: Environmental Analysis Map

**File:** `chccs_environmental_analysis.html`

### 3.1 What This Map Shows

This map overlays four environmental layers across the CHCCS district to compare conditions near each school:

- **Air pollution (TRAP):** Relative exposure to traffic-related air pollution
- **Flood risk:** FEMA-designated flood zones overlapping school properties
- **Tree canopy:** Areas covered by trees (from satellite imagery)
- **Heat vulnerability (UHI proxy):** Relative heat exposure based on land cover type

**All scores are comparative rankings, not absolute measurements.** A school with a higher TRAP score is more exposed to road pollution *relative to other CHCCS schools*, not necessarily above any health standard.

### 3.2 Data Sources

| Data | Source & Link | What It Tells Us | Freshness | Key Limitations |
|------|---------------|------------------|-----------|-----------------|
| Road network | [OpenStreetMap](https://www.openstreetmap.org/) | Road locations and types (for pollution modeling) | Current as of download | Community-maintained |
| Traffic counts | [NCDOT AADT stations](https://ncdot.maps.arcgis.com/) | Measured vehicle counts for calibrating road weights | Various years | Only ~1.2% of road segments have measured counts; rest use road-type defaults |
| Land cover (tree canopy, built-up, water, etc.) | [ESA WorldCover V2 2021](https://esa-worldcover.org/) (10m resolution) | What covers the ground: trees, buildings, grass, water | 2021 satellite imagery | 10m pixels may miss individual yard trees in suburban areas |
| Flood zones | [FEMA National Flood Hazard Layer](https://www.fema.gov/flood-maps/national-flood-hazard-layer) (Layer 28) | Official flood risk designations | As mapped by FEMA | Maps may not reflect recent development or climate changes |
| School properties | Orange County GIS parcels | School property boundaries (for flood overlap calculation) | Current as of data extract | — |
| School locations | [NCES EDGE 2023–24](https://nces.ed.gov/programs/edge/geographic/schoollocations) | Verified coordinates for all 11 schools | 2023–24 school year | — |
| District boundary | [Census TIGER/Line](https://www.census.gov/geographies/mapping-files/time-series/geo/tiger-line-file.html) | Outer boundary of the CHCCS district | Current | — |

### 3.3 Air Pollution (TRAP Exposure Index)

The TRAP layer estimates *relative* exposure to traffic-related air pollution at each point in the district. It is a screening index, not an air quality measurement.

```
  ┌──────────────────┐     ┌──────────────────┐
  │ Road Network     │     │ NCDOT AADT       │
  │ (types: primary, │     │ (measured traffic │
  │  secondary, etc.)│     │  counts, where    │
  │                  │     │  available)       │
  └────────┬─────────┘     └────────┬──────────┘
           │                        │
           ▼                        ▼
  ┌──────────────────────────────────────────┐
  │ Assign weight to each road segment:      │
  │ • AADT-based weight where counts exist   │
  │ • Road-type default weight otherwise     │
  └───────────────────┬──────────────────────┘
                      │
                      ▼
  ┌──────────────────────────────────────────┐
  │ For each map point:                      │
  │ Sum pollution from all nearby roads      │
  │ (exponential decay with distance)        │
  │                                          │
  │ P_raw = Σ W_i × e^(-λ × d_i)            │
  └───────────────────┬──────────────────────┘
                      │
                      ▼
  ┌──────────────────────────────────────────┐
  │ Apply tree canopy mitigation:            │
  │ More tree cover nearby → lower net score │
  │                                          │
  │ P_net = P_raw × (1 - mitigation)         │
  └──────────────────────────────────────────┘
```

**In plain English:** Bigger roads and closer roads contribute more pollution. The contribution of each road drops off exponentially with distance — a road 500 meters away has much less impact than the same road 50 meters away. Tree canopy near the measurement point partially filters pollution, reducing the net score.

The analysis calculates scores at two radii: 500m and 1,000m from each school.

<details>
<summary>Technical detail: TRAP formula and parameters</summary>

**Exponential decay model:**

```
P_raw = Σ W_i × exp(-λ × d_i)
```

Where:
- *W_i* = weight of road segment *i* (based on road type or measured AADT)
- *d_i* = Euclidean distance (meters) from the measurement point to road segment *i*
- *λ* = 0.003 m⁻¹ — composite decay rate for NOx, black carbon, and ultrafine particles

**Road type weights** (used when AADT counts are not available):

| Road Type | Weight | Road Type | Weight |
|-----------|--------|-----------|--------|
| Motorway | 1.000 | Tertiary | 0.060 |
| Motorway link | 0.800 | Tertiary link | 0.048 |
| Trunk | 0.600 | Unclassified | 0.020 |
| Trunk link | 0.480 | Residential | 0.010 |
| Primary | 0.300 | Service | 0.005 |
| Primary link | 0.240 | Living street | 0.005 |
| Secondary | 0.150 | Driveway | 0.002 |
| Secondary link | 0.120 | Alley | 0.003 |

**AADT override:** Where NCDOT has a traffic counting station within 50m of a road segment, the weight is replaced with: `AADT / 50,000` (clipped to range 0.001–2.0).

**Tree canopy mitigation:**

```
mitigation = min(0.56 × canopy_cover, 0.80)
P_net = P_raw × (1 - mitigation)
```

Where *canopy_cover* is the fraction of tree cover within the analysis radius (from ESA WorldCover class 10). The coefficient 0.56 is derived from Nowak et al. (2014): approximately 2.8% PM2.5 reduction per 5% canopy cover increase. Mitigation is capped at 80%.

Road segments are discretized into 50m sub-segments for more accurate distance calculations.

**References:**
- Karner, A. A., Eisinger, D. S., & Niemeier, D. A. (2010). Near-roadway air quality: Synthesizing the findings from real-world data. *Environmental Science & Technology*, 44(14).
- Boogaard, H., et al. (2019). Concentration decay rates for near-road air pollutants. *International Journal of Hygiene and Environmental Health*, 222(7). [Validated λ ≈ 0.0026 for BC, 0.0027 for NOx]
- Nowak, D. J., et al. (2014). Tree and forest effects on air quality and human health in the United States. *Environmental Pollution*, 193.
- Hoek, G., et al. (2008). Land-use regression models for intraurban air pollution. *Atmospheric Environment*, 42(33). [Road-class-as-AADT-proxy precedent]

</details>

### 3.4 Flood Risk Layer

The flood layer shows FEMA-designated flood zones and calculates how much of each school's property falls within them.

```
  ┌──────────────────┐     ┌──────────────────┐
  │ FEMA Flood Zones │     │ School Property   │
  │ (National Flood  │     │ Parcels (Orange   │
  │  Hazard Layer)   │     │  County GIS)      │
  └────────┬─────────┘     └────────┬──────────┘
           │                        │
           ▼                        ▼
  ┌──────────────────────────────────────────┐
  │ Compute geometric intersection:          │
  │ How many acres of each school property   │
  │ overlap with each flood zone type?       │
  └──────────────────────────────────────────┘
```

**Flood zone types shown:**

| Zone | Meaning | Annual Chance |
|------|---------|---------------|
| A, AE, AO, AH | Special Flood Hazard Area (100-year flood zone) | 1% or higher |
| 0.2% Annual Chance | Moderate flood hazard (500-year flood zone) | 0.2% |

<details>
<summary>Technical detail: Flood zone definitions</summary>

- **Zone A:** 1% annual chance flood area where no base flood elevation has been determined
- **Zone AE:** 1% annual chance flood area with base flood elevation determined
- **Zone AO:** 1% annual chance shallow flooding (sheet flow, 1–3 feet) with average depth determined
- **Zone AH:** 1% annual chance shallow flooding (ponding) with base flood elevation determined
- **0.2% Annual Chance (500-year):** Area with 0.2% annual chance of flooding — identified by the `ZONE_SUBTY` field containing "0.2 PCT"

Areas are calculated in UTM 17N (EPSG:32617) for accurate acreage, then converted back to WGS84 for display.

</details>

### 3.5 Tree Canopy Layer

The tree canopy layer shows where trees are located across the district, based on the ESA WorldCover V2 2021 satellite dataset at 10-meter resolution.

Tree cover is identified as ESA WorldCover class 10 ("Tree cover"). Each 10m × 10m pixel is classified as tree or non-tree, and the canopy percentage around each school is calculated as the fraction of tree-classified pixels within the analysis radius.

**Suburban limitation:** In suburban neighborhoods, individual yard trees or small clusters may be too small or scattered to be classified as "Tree cover" at 10m resolution. They may instead be classified as "Built-up" (class 50) if the surrounding pixel is dominated by impervious surface. This means the tree canopy data likely underestimates true canopy cover in suburban residential areas.

### 3.6 Heat Vulnerability (UHI Proxy)

The UHI proxy layer estimates *relative* heat vulnerability based on what covers the ground. It is **not a temperature measurement** — it uses land cover types as a proxy for how much each area is likely to absorb and re-radiate heat.

**How it works:** Each 10m land cover pixel from the ESA WorldCover dataset is assigned a heating or cooling weight based on its type. Built-up areas (pavement, rooftops) get positive weights (more heat). Vegetated and water areas get negative weights (cooling). The weights are averaged across each analysis area and normalized to a 0–100 scale.

<details>
<summary>Technical detail: UHI weight table and normalization</summary>

**Land cover class weights:**

| ESA Class | Land Cover Type | Weight | Effect |
|-----------|----------------|--------|--------|
| 10 | Tree cover | −0.60 | Strong cooling (evapotranspiration + shading) |
| 20 | Shrubland | −0.30 | Partial cooling |
| 30 | Herbaceous vegetation | −0.10 | Minimal cooling |
| 40 | Cropland | −0.05 | Minimal cooling |
| 50 | Built-up | +1.00 | Reference heating class (impervious surfaces) |
| 60 | Bare/sparse vegetation | +0.40 | Heat absorption |
| 80 | Permanent water bodies | −0.50 | Thermal buffering |
| 90 | Herbaceous wetland | −0.40 | Cooling |
| 95 | Mangroves / woody wetland | −0.40 | Cooling |

**Normalization:**

```
raw_uhi   = (sum of pixel weights) / (number of valid pixels)
uhi_score = (raw_uhi − (−0.60)) / (1.00 − (−0.60)) × 100
```

Resulting score is clipped to [0, 100]. A score of 0 means 100% tree cover; a score of 100 means 100% built-up.

**Important disclaimer:** These weights are author-assigned based on urban climatology literature (Oke, 1982; Stewart & Oke, 2012), not calibrated to local temperature measurements. The proxy indicates *relative* heat vulnerability for comparing locations, not actual temperatures.

**References:**
- Oke, T. R. (1982). The energetic basis of the urban heat island. *Quarterly Journal of the Royal Meteorological Society*, 108(455).
- Stewart, I. D., & Oke, T. R. (2012). Local climate zones for urban temperature studies. *Bulletin of the American Meteorological Society*, 93(12).

</details>

### 3.7 Map Controls

- **Layer selector:** Radio buttons to switch between Raw Air Pollution, Net Air Pollution (with tree mitigation), and UHI Proxy views
- **FEMA Flood Plains:** Toggle on/off using layer control
- **Tree Canopy:** Toggle on/off using a checkbox control
- **Dynamic legends:** Each raster layer has its own color scale legend that appears when that layer is active
- **School markers:** Show TRAP, UHI, and canopy scores when clicked
- **School comparison charts:** A 2×2 grid of horizontal bar charts below the map (built with Chart.js) compares all 11 schools on four metrics: Raw Air Pollution, Net Air Pollution, Urban Heat Island (Index), and Flood Zone %. All values are at the 500 m radius.

### 3.8 Key Limitations

1. **Only ~1.2% of road segments have actual traffic counts.** The vast majority of roads use default weights based on road type (e.g., "primary road" → 0.300). Real traffic volumes on individual roads may differ significantly from these defaults.

2. **Tree canopy data is from 2021 and may miss suburban trees.** The ESA WorldCover satellite data was captured in 2021. Tree planting, removal, and growth since then are not reflected. Additionally, scattered yard trees in suburban areas may be classified as "Built-up" at 10m resolution.

3. **UHI is a proxy, not temperature.** The heat vulnerability scores are based on land cover weights, not measured temperatures. A score of 75 does not mean 75°F or any specific temperature — it means that location has more heat-absorbing land cover than a location scoring 25.

4. **No wind, terrain, or buildings modeled.** The TRAP model uses distance-based decay only. It does not account for wind direction, topography, building barriers, or atmospheric conditions that affect how pollution actually disperses.

5. **FEMA flood maps may be outdated.** Flood zone designations reflect conditions when FEMA last studied the area. New development, upstream changes, and climate trends may alter actual flood risk.

6. **All indices are relative screening tools.** None of the environmental scores represent regulatory measurements, health risk assessments, or standards compliance. They are designed for comparing schools to each other, not for making absolute safety judgments.

7. **UHI weights are author-assigned.** The specific numerical weights given to each land cover class (e.g., built-up = +1.00, tree cover = −0.60) are based on published urban climatology research but are not calibrated to local conditions in Chapel Hill. Different weight choices would produce different rankings.

---

## Full Reference List

Boogaard, H., et al. (2019). Concentration decay rates for near-road air pollutants. *International Journal of Hygiene and Environmental Health*, 222(7). DOI: 10.1016/j.ijheh.2019.07.005

Eicher, C. L., & Brewer, C. A. (2001). Dasymetric mapping and areal interpolation: Implementation and evaluation. *Cartography and Geographic Information Science*, 28(2), 125–138.

ESA / Copernicus. (2021). ESA WorldCover V2 2021 (10m). Accessed via Microsoft Planetary Computer STAC API.

FEMA. National Flood Hazard Layer (NFHL). ArcGIS REST MapServer, Layer 28 (S_FLD_HAZ_AR). https://www.fema.gov/flood-maps/national-flood-hazard-layer

Fitzpatrick, K., et al. (2006). *Improving Pedestrian Safety at Unsignalized Crossings.* FHWA-HRT-06-042. Federal Highway Administration.

Highway Capacity Manual, 6th Edition (HCM6), Chapter 16: Urban Street Segments.

Hoek, G., et al. (2008). Land-use regression models for intraurban air pollution. *Atmospheric Environment*, 42(33).

Karner, A. A., Eisinger, D. S., & Niemeier, D. A. (2010). Near-roadway air quality: Synthesizing the findings from real-world data. *Environmental Science & Technology*, 44(14). DOI: 10.1021/es100008x

Mennis, J. (2003). Generating surface models of population using dasymetric mapping. *The Professional Geographer*, 55(1), 31–42.

MUTCD Section 4E.06. Pedestrian intervals — walking speed for signal timing. *Manual on Uniform Traffic Control Devices.*

NCES EDGE. (2024). Public School Locations 2023–24. National Center for Education Statistics. https://nces.ed.gov/programs/edge/geographic/schoollocations

Nowak, D. J., et al. (2014). Tree and forest effects on air quality and human health in the United States. *Environmental Pollution*, 193, 119–129.

Oke, T. R. (1982). The energetic basis of the urban heat island. *Quarterly Journal of the Royal Meteorological Society*, 108(455), 1–24.

Stewart, I. D., & Oke, T. R. (2012). Local climate zones for urban temperature studies. *Bulletin of the American Meteorological Society*, 93(12), 1879–1900.

U.S. Census Bureau. American Community Survey 5-Year Estimates (2020–2024). Tables: B01001, B03002, B11003, B19001, B19013, B25003, B25044, C17002. https://www.census.gov/programs-surveys/acs

U.S. Census Bureau. 2020 Decennial Census, P.L. 94-171 Redistricting Data. Tables: P1, P2. https://www.census.gov/programs-surveys/decennial-census/about/rdo.html

U.S. Census Bureau. TIGER/Line Shapefiles. https://www.census.gov/geographies/mapping-files/time-series/geo/tiger-line-file.html

---

## Links to Technical Documentation

For deeper implementation details, code-level documentation, and complete lists of known limitations:

- **[School Closure Impact Analysis](SCHOOL_CLOSURE_ANALYSIS.md)** — Full methodology for travel-time computation and traffic redistribution (12 documented limitations)
- **[School Closure Methodology Walkthrough](../assets/maps/closure_methodology.html)** — Interactive scrollytelling page explaining the closure analysis step by step
- **[Environmental Analysis](ENVIRONMENTAL_ANALYSIS_README.md)** — TRAP, flood, UHI proxy, and tree canopy technical details (23 documented limitations)
- **[Socioeconomic Analysis & Limitations](SOCIOECONOMIC_ANALYSIS_AND_LIMITATIONS.md)** — Census variable selection, dasymetric method details (26 documented limitations)
- **[Socioeconomic Methodology Walkthrough](../assets/maps/socioeconomic_methodology.html)** — Interactive scrollytelling page explaining Census data, dasymetric interpolation, and dot-density mapping
- **[School Desert Analysis & Limitations](SCHOOL_DESERT_ANALYSIS_AND_LIMITATIONS.md)** — Travel-time heatmap methodology (13 documented limitations)
- **[Geospatial Analysis Guidelines](GEOSPATIAL_ANALYSIS_GUIDELINES.md)** — Coordinate reference system standards and spatial operation conventions
- **[Implementation Notes](IMPLEMENTATION_NOTES.md)** — Technical implementation details for all modules
