# CHCCS District Geospatial Analysis

Objective spatial analysis of all 11 Chapel Hill-Carrboro City Schools (CHCCS) elementary schools, covering demographics, accessibility, environmental exposure, flood risk, and childcare proximity.

## Modules

| Module | Description | Output |
|--------|-------------|--------|
| `school_desert.py` | Travel-time impacts of school closure scenarios (walk/bike/drive) using Dijkstra shortest paths on OSM road networks | `school_community_map.html` |
| `school_closure_analysis.py` | School closure impact: travel-time + children-weighted traffic redistribution analysis | `school_closure_analysis.html` |
| `school_socioeconomic_analysis.py` | Census demographics by attendance zone — ACS 5-Year + 2020 Decennial with dasymetric interpolation | `school_socioeconomic_map.html` |
| `road_pollution.py` | Traffic-related air pollution (TRAP) exposure with ESA WorldCover tree canopy mitigation scoring | `road_pollution_scores.csv` |
| `flood_map.py` | FEMA NFHL flood zone overlay on school property parcels | `flood_school_properties.png` |
| `environmental_map.py` | Consolidated environmental map (TRAP + flood + UHI proxy + tree canopy) | `chccs_environmental_analysis.html` |
| `environmental_story.py` | Interactive scrollytelling walkthrough of how the environmental map is built | `environmental_methodology.html` |
| `closure_story.py` | Interactive scrollytelling walkthrough of how the school closure impact map is built | `closure_methodology.html` |
| `socioeconomic_story.py` | Interactive scrollytelling walkthrough of how the socioeconomic map is built | `socioeconomic_methodology.html` |
| `affordable_housing.py` | Affordable housing data download and quality assessment | `affordable_housing.gpkg` |
| `mls_geocode.py` | MLS home sales geocoding (Census batch + Nominatim fallback) | `mls_home_sales.gpkg` |
| `planned_dev_geocode.py` | Planned development geocoding (Census batch + Nominatim fallback) | `planned_developments.gpkg` |
| `childcare_geocode.py` | Childcare facility geocoding and proximity analysis by distance bands | CSVs in `data/processed/` |
| `property_data.py` | Orange County residential parcel classification and centroid extraction | `combined_data_centroids.gpkg` |

## Quick Start

```bash
# Install dependencies
pip install -r requirements.txt

# Run individual analysis modules
python src/road_pollution.py              # TRAP + tree canopy analysis
python src/school_desert.py               # Travel-time heatmaps + affected households
python src/flood_map.py                   # FEMA flood plain × school properties
python src/school_socioeconomic_analysis.py  # Census demographics by zone
python src/childcare_geocode.py           # Childcare proximity analysis
python src/property_data.py              # Process Orange County parcel data
python src/environmental_map.py          # Consolidated environmental analysis map
python src/environmental_story.py        # Environmental methodology scrollytelling page
python src/affordable_housing.py         # Affordable housing data download
python src/mls_geocode.py               # Geocode MLS home sales data
python src/planned_dev_geocode.py      # Geocode planned development data
python src/school_closure_analysis.py   # School closure impact (travel + traffic)
python src/closure_story.py            # School closure methodology scrollytelling page
python src/socioeconomic_story.py     # Socioeconomic methodology scrollytelling page
```

## Execution Order

Most modules are independent, but some depend on cached data:

1. **`property_data.py`** — Run first if parcel centroids are not yet generated (required by `school_desert.py`)
2. **`road_pollution.py`** — Downloads NCES school locations (used by all other modules)
3. All other modules can run independently once school locations are cached

## Data Sources

| Source | Provider | Notes |
|--------|----------|-------|
| School locations | NCES EDGE 2023-24 | Auto-downloaded, cached at `data/cache/nces_school_locations.csv` |
| District boundary | Census TIGER/Line | Auto-downloaded by modules |
| Road networks | OpenStreetMap via OSMnx | Auto-downloaded, cached as GraphML |
| Census demographics | ACS 5-Year + 2020 Decennial | Auto-downloaded via Census API |
| Flood zones | FEMA NFHL (ArcGIS REST) | Auto-downloaded, cached as GeoPackage |
| Tree canopy | ESA WorldCover 2021 via Planetary Computer | Auto-downloaded per tile |
| Parcel data | Orange County GIS | Committed in `data/raw/properties/` |
| Childcare facilities | NC DCDEE | Committed in `data/raw/childcare/` |
| MLS home sales | Triangle MLS (2023-2025) | Committed in `data/raw/MLS/`, geocoded to `data/cache/mls_home_sales.gpkg` |
| Planned developments | Town of Chapel Hill (2025) | Committed in `data/raw/properties/planned/`, geocoded to `data/cache/planned_developments.gpkg` |
| Attendance zones | CHCCS GIS | Auto-downloaded, cached as GeoPackage |

## Prerequisites

- Python 3.10+
- ~2 GB disk space for cached data (downloaded on first run)
- Internet connection for initial data downloads

## Directory Structure

```
CHCCS_geospatial/
├── src/                    # Analysis modules
├── data/
│   ├── raw/               # Committed source data (parcels, childcare)
│   ├── cache/             # Auto-downloaded data (.gitignored)
│   └── processed/         # Analysis outputs (CSVs, markdown reports)
├── assets/
│   ├── maps/              # Interactive HTML maps + static images
│   └── charts/            # Generated comparison charts
├── docs/                  # Methodology documentation + limitations
└── reference/             # Reference materials (flood PDFs)
```

## Documentation

- **[School Closure Impact Analysis](docs/SCHOOL_CLOSURE_ANALYSIS.md)** — Travel-time + traffic redistribution methodology and 12 known limitations
- **[School Desert Analysis & Limitations](docs/SCHOOL_DESERT_ANALYSIS_AND_LIMITATIONS.md)** — Travel-time methodology, assumptions, and 13 known limitations
- **[Socioeconomic Analysis & Limitations](docs/SOCIOECONOMIC_ANALYSIS_AND_LIMITATIONS.md)** — Census demographics methodology and 26 known limitations
- **[Environmental Analysis](docs/ENVIRONMENTAL_ANALYSIS_README.md)** — TRAP, flood, UHI proxy, tree canopy methodology and 23 known limitations
- **[How the Environmental Map Works](assets/maps/environmental_methodology.html)** — Interactive scrollytelling walkthrough of data sources, processing, and limitations
- **[How the School Closure Impact Map Works](assets/maps/closure_methodology.html)** — Interactive scrollytelling walkthrough of travel-time and traffic methodology
- **[How the Socioeconomic Map Works](assets/maps/socioeconomic_methodology.html)** — Interactive scrollytelling walkthrough of Census data, dasymetric interpolation, and dot-density mapping
- **[How These Maps Were Built](docs/METHODOLOGY.md)** — Public-facing guide to data sources, methods, and limitations for all three maps
- **[Geospatial Analysis Guidelines](docs/GEOSPATIAL_ANALYSIS_GUIDELINES.md)** — CRS discipline, spatial operations, and map visualization standards
- **[Implementation Notes](docs/IMPLEMENTATION_NOTES.md)** — Technical details and data pipeline notes

## Frequently Asked Questions

### What does "Census ACS 2022 5-Year" mean?

**ACS** stands for **American Community Survey**, an ongoing survey conducted by the U.S. Census Bureau that collects detailed demographic, economic, and housing data annually.

**5-Year** means the data is a rolling average of survey responses collected over 5 calendar years (2018–2022). This pooling increases sample size and statistical reliability, especially for small geographic areas like Census block groups.

**2022** refers to the release year — the ACS 2022 5-Year estimates were published in December 2023.

### What is an Attendance Zone?

An **Attendance Zone** is the geographic boundary that determines which school students are assigned to by default based on their home address. These boundaries are set by CHCCS and may change over time.

**Important:** Attendance zone demographics ≠ actual school enrollment. Students can attend schools outside their assigned zone through school choice programs, magnet programs, or transfers. The maps show *who lives in each zone*, not *who attends each school*.

### What are "Blocks" vs "Blocks (est.)"?

**Block** = Census block, the smallest Census geography (~500 people on average). Block-level race data comes directly from the 2020 Decennial Census.

**Block (est.)** = "Estimated" block-level data. The ACS only publishes data at the block group level (~1,500 people), not individual blocks. We use **dasymetric interpolation** to estimate block-level values by distributing block group totals proportionally based on residential parcel area within each block.

The **(est.)** suffix indicates derived estimates, not raw Census data.

### Who is considered "Minority" in % Minority?

**% Minority** is calculated as `100% − % White Non-Hispanic`. This includes all residents who identify as:
- Black or African American
- Hispanic or Latino (of any race)
- Asian
- Two or more races (Multiracial)
- Native American, Pacific Islander, or other races

**Why is this different from the Race/Ethnicity dots?** The dot-density map shows each racial/ethnic group as separate colored dots. % Minority aggregates all non-White-NH groups into a single metric for comparing zones at a glance.

### What does "% Elementary Age (5-9)" measure?

- **Numerator:** Population aged 5–9 years (elementary school age)
- **Denominator:** Total population of all ages in the area

This is *not* the percentage of children who are 5–9, but rather the percentage of *all residents* in that area who fall into this age range. A higher % means a greater concentration of elementary-age children relative to the total population.

### What does "Travel Mode" (Walk/Bike/Drive) show in the School Closure Map?

The Travel Mode selector shows **travel time to the geographically nearest open school** — not the child's assigned attendance zone school.

- **Walk:** Time at 2.5 mph child walking speed
- **Bike:** Time at 12 mph cycling speed
- **Drive:** Time at effective driving speeds (18–60 mph depending on road type)

Travel times are computed using Dijkstra shortest-path algorithm on actual road networks from OpenStreetMap, not straight-line distance.

### What are "Walk Zones" in the School Closure Map?

**Walk Zones** are CHCCS-designated geographic boundaries where students are considered close enough to walk to school (typically within ~1 mile). These are shown as semi-transparent blue overlays when toggled on.

Walk zones turn red when a school is closed in a simulation to highlight affected walkable areas.

### What is "Current school zone" vs "Closest school by driving" routing?

In the Traffic tab of the School Closure Map:

- **Current school zone:** Routes each child to their CHCCS-assigned attendance zone school, regardless of distance. This reflects how traffic would flow if all families drove to their assigned school.
- **Closest school by driving:** Routes each child to the geographically nearest school by road network. This shows a "nearest-school" scenario that ignores zone boundaries.

Comparing these modes reveals how attendance zone assignments affect traffic patterns.

---

## Known TODOs

- **`maps.py`**: Generates walkability, comparison, and childcare maps for all schools using NCES location data.
- **`data_processing.py`**: Contains non-geospatial functions (enrollment, academic, costs, housing loaders). Should be slimmed to geospatial utilities only.
- **Asset regeneration**: Maps and charts should be regenerated after code updates to reflect neutral styling.
- **Cross-module constants**: `CHAPEL_HILL_CENTER`, `CRS_WGS84`, `CRS_UTM17N`, `SCHOOL_CSV` are duplicated across modules. Consider a shared `config.py`.

## License

This analysis uses publicly available data from federal, state, and local government sources.
