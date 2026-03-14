# CHCCS District Geospatial Analysis — Project Guide

## Project Overview

Objective geospatial analysis of all 11 CHCCS elementary schools. This repository provides spatial analysis tools for demographics, accessibility, environmental exposure, flood risk, and childcare proximity — treating all schools equally with no advocacy for any particular school.

## CRITICAL: Intellectual Honesty Requirements

1. **NEVER fabricate data** — No source = no claim
2. **NEVER overstate claims** — Be precise about what data shows
3. **Treat all schools equally** — No school should receive special highlighting, coloring, or emphasis
4. **Be transparent about limitations** — Say when data is incomplete
5. **Acknowledge uncertainty** — Mark estimates and approximations clearly

---

## School Location Data

**Authoritative source:** NCES EDGE Public School Locations 2023-24 (LEAID 3700720)
- Downloaded by `src/road_pollution.py:download_school_locations()`
- Cached at `data/cache/nces_school_locations.csv`
- Columns: `nces_id, school, lat, lon, address, city`

**NEVER** generate school coordinates manually. Always use the NCES data.

---

## File Structure

```
CHCCS_geospatial/
├── CLAUDE.md                    # This file
├── README.md                    # Project overview
├── requirements.txt             # Python dependencies
├── .gitignore
├── src/
│   ├── school_socioeconomic_analysis.py  # Census demographics by attendance zone
│   ├── school_desert.py                  # Travel-time school closure analysis
│   ├── road_pollution.py                 # TRAP + tree canopy spatial analysis
│   ├── flood_map.py                      # FEMA flood plain × school properties
│   ├── childcare_geocode.py              # Childcare proximity analysis
│   ├── property_data.py                  # Orange County parcel processing
│   ├── affordable_housing.py             # Affordable housing data download & assessment
│   ├── mls_geocode.py                   # MLS home sales geocoding (Census + Nominatim)
│   ├── planned_dev_geocode.py            # Planned development geocoding (Census + Nominatim)
│   ├── environmental_map.py              # Consolidated environmental map (TRAP + flood + UHI)
│   ├── environmental_story.py            # Scrollytelling methodology walkthrough generator
│   ├── school_closure_analysis.py        # School closure impact (travel + traffic)
│   ├── closure_story.py                  # Scrollytelling closure methodology walkthrough
│   ├── socioeconomic_story.py            # Scrollytelling socioeconomic methodology walkthrough
│   ├── data_processing.py                # Shared data loading utilities
│   └── maps.py                           # Map visualizations (TODO: needs restructuring)
├── data/
│   ├── raw/                    # Committed source data
│   │   ├── properties/         # Orange County parcels (~7 MB)
│   │   ├── childcare/          # NC DCDEE facility data
│   │   └── MLS/               # Triangle MLS home sales (2023-2025)
│   ├── cache/                  # Auto-downloaded (.gitignored)
│   └── processed/              # Analysis outputs
├── assets/
│   ├── maps/                   # Interactive HTML maps + static images
│   └── charts/                 # Comparison charts
├── docs/                       # Methodology and limitations
│   ├── ENVIRONMENTAL_ANALYSIS_README.md  # Consolidated env analysis (TRAP + flood + UHI + canopy)
│   ├── GEOSPATIAL_ANALYSIS_GUIDELINES.md # CRS, spatial ops, and map visualization standards
│   ├── SCHOOL_CLOSURE_ANALYSIS.md         # School closure impact methodology & limitations
│   ├── SCHOOL_DESERT_ANALYSIS_AND_LIMITATIONS.md
│   ├── SOCIOECONOMIC_ANALYSIS_AND_LIMITATIONS.md
│   ├── IMPLEMENTATION_NOTES.md
│   └── socioeconomic/
│       └── SOCIOECONOMIC_ANALYSIS.md
└── reference/
    └── flood_plains/           # FEMA flood reference PDFs
```

---

## Commands

```bash
# Run TRAP / tree canopy analysis (also downloads school locations + AADT)
python src/road_pollution.py

# Run TRAP analysis with road network diagnostic report
python src/road_pollution.py --diagnose-roads --skip-grid

# Run school desert analysis (travel-time heatmaps + affected households)
python src/school_desert.py

# Generate FEMA flood plain × school property map
python src/flood_map.py

# Run socioeconomic analysis (Census demographics by attendance zone)
python src/school_socioeconomic_analysis.py

# Run childcare proximity analysis
python src/childcare_geocode.py

# Process Orange County parcel data
python src/property_data.py

# Download & assess affordable housing data
python src/affordable_housing.py
python src/affordable_housing.py --cache-only  # cached data only

# Geocode MLS home sales data
python src/mls_geocode.py

# Geocode planned development data
python src/planned_dev_geocode.py
python src/planned_dev_geocode.py --cache-only  # cached data only

# Generate consolidated environmental analysis map (TRAP + flood + UHI)
python src/environmental_map.py
python src/environmental_map.py --cache-only   # cached data only

# Generate environmental methodology scrollytelling page
python src/environmental_story.py
python src/environmental_story.py --cache-only  # cached data only

# School closure impact analysis (travel time + traffic redistribution)
python src/school_closure_analysis.py
python src/school_closure_analysis.py --cache-only    # cached data only
python src/school_closure_analysis.py --skip-traffic  # Part 1 only
python src/school_closure_analysis.py --mode drive    # single mode

# Generate school closure methodology scrollytelling page
python src/closure_story.py
python src/closure_story.py --cache-only  # cached data only

# Generate socioeconomic methodology scrollytelling page
python src/socioeconomic_story.py
python src/socioeconomic_story.py --cache-only  # cached data only
```

---

## Key Data References

| Data | Location | Source |
|------|----------|--------|
| School locations | `data/cache/nces_school_locations.csv` | NCES EDGE 2023-24 |
| District boundary | `data/cache/chccs_district_boundary.gpkg` | Census TIGER/Line |
| Road networks | `data/cache/network_*.graphml` | OSMnx (OpenStreetMap) |
| Census demographics | `data/cache/census_*.gpkg` | ACS 5-Year + Decennial |
| Flood zones | `data/cache/fema_flood_zones.gpkg` | FEMA NFHL |
| School desert grid | `data/processed/school_desert_grid.csv` | Computed (Dijkstra) |
| Pollution scores | `data/processed/road_pollution_scores.csv` | Computed (TRAP model) |
| Zone demographics | `data/processed/census_school_demographics.csv` | Computed (dasymetric) |
| NCDOT AADT stations | `data/cache/ncdot_aadt_orange_county.gpkg` | NCDOT ArcGIS (Orange County) |
| Affordable housing | `data/cache/affordable_housing.gpkg` | Town of Chapel Hill ArcGIS (2025) |
| MLS home sales | `data/cache/mls_home_sales.gpkg` | Triangle MLS (2023-2025), geocoded via Census + Nominatim |
| Planned developments | `data/cache/planned_developments.gpkg` | Town of Chapel Hill Active Development page (hand-transcribed 2026-03-12), geocoded via Census + Nominatim |
| UHI proxy scores | `data/processed/uhi_proxy_scores.csv` | Computed (ESA WorldCover proxy) |
| TRAP grid cache | `data/cache/trap_grids.npz` | Computed (road_pollution grid) |
| UHI grid cache | `data/cache/uhi_grid.npz` | Computed (ESA WorldCover proxy) |
| Closure Dijkstra cache | `data/cache/closure_analysis/dijkstra_{mode}.pkl` | Computed (predecessors + distances) |
| Pixel children | `data/cache/closure_analysis/pixel_children.csv` | Computed (dasymetric ACS → pixels) |
| Closure assignments | `data/processed/school_closure_assignments.csv` | Computed (travel time per pixel) |
| Closure traffic | `data/processed/school_closure_traffic.csv` | Computed (children per edge) |

---

## CRITICAL: Documentation Maintenance

**All documentation must be fully updated whenever a substantial change is made to a workflow, dataset, or asset (map or chart).** This includes but is not limited to:

- [`docs/SCHOOL_CLOSURE_ANALYSIS.md`](docs/SCHOOL_CLOSURE_ANALYSIS.md) — School closure impact (travel + traffic methodology, limitations)
- [`docs/ENVIRONMENTAL_ANALYSIS_README.md`](docs/ENVIRONMENTAL_ANALYSIS_README.md) — TRAP, flood, UHI proxy, tree canopy, consolidated map
- [`docs/GEOSPATIAL_ANALYSIS_GUIDELINES.md`](docs/GEOSPATIAL_ANALYSIS_GUIDELINES.md) — CRS discipline, spatial operations, map visualization standards
- [`docs/IMPLEMENTATION_NOTES.md`](docs/IMPLEMENTATION_NOTES.md) — Technical implementation details for all modules
- [`docs/SCHOOL_DESERT_ANALYSIS_AND_LIMITATIONS.md`](docs/SCHOOL_DESERT_ANALYSIS_AND_LIMITATIONS.md) — School desert analysis
- [`docs/SOCIOECONOMIC_ANALYSIS_AND_LIMITATIONS.md`](docs/SOCIOECONOMIC_ANALYSIS_AND_LIMITATIONS.md) — Socioeconomic analysis
- [`docs/socioeconomic/SOCIOECONOMIC_ANALYSIS.md`](docs/socioeconomic/SOCIOECONOMIC_ANALYSIS.md) — Auto-generated socioeconomic methodology
- [`data/processed/ROAD_POLLUTION.md`](data/processed/ROAD_POLLUTION.md) — TRAP analysis results and methodology
- [`docs/METHODOLOGY.md`](docs/METHODOLOGY.md) — Public-facing methodology guide (data sources, methods, limitations for all three maps)

If you change a formula, constant, data source, output file, or analysis pipeline, update every document that references the changed item. Stale documentation is worse than no documentation.

**Mandatory cross-check:** Whenever any workflow touching the files listed above is edited, systematically assess both [`README.md`](README.md) and [`docs/METHODOLOGY.md`](docs/METHODOLOGY.md) to ensure all descriptions, constants, data sources, and limitations remain accurate and up to date. These two documents are the primary public-facing entry points and must never fall out of sync with the technical docs or source code.

---

## Known TODOs

- **`data_processing.py`**: Contains non-geospatial functions. Should be slimmed to geospatial utilities only.
- **Cross-module constants**: Consider a shared `config.py` for `CHAPEL_HILL_CENTER`, `CRS_WGS84`, `CRS_UTM17N`, `SCHOOL_CSV`.
