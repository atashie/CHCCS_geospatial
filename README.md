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
| `affordable_housing.py` | Affordable housing data download and quality assessment | `affordable_housing.gpkg` |
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
python src/affordable_housing.py         # Affordable housing data download
python src/school_closure_analysis.py   # School closure impact (travel + traffic)
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
- **[Geospatial Analysis Guidelines](docs/GEOSPATIAL_ANALYSIS_GUIDELINES.md)** — CRS discipline, spatial operations, and map visualization standards
- **[Implementation Notes](docs/IMPLEMENTATION_NOTES.md)** — Technical details and data pipeline notes

## Known TODOs

- **`maps.py`**: Generates walkability, comparison, and childcare maps for all schools using NCES location data.
- **`data_processing.py`**: Contains non-geospatial functions (enrollment, academic, costs, housing loaders). Should be slimmed to geospatial utilities only.
- **Asset regeneration**: Maps and charts should be regenerated after code updates to reflect neutral styling.
- **Cross-module constants**: `CHAPEL_HILL_CENTER`, `CRS_WGS84`, `CRS_UTM17N`, `SCHOOL_CSV` are duplicated across modules. Consider a shared `config.py`.

## License

This analysis uses publicly available data from federal, state, and local government sources.
