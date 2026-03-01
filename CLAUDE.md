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
│   ├── data_processing.py                # Shared data loading utilities
│   └── maps.py                           # Map visualizations (TODO: needs restructuring)
├── data/
│   ├── raw/                    # Committed source data
│   │   ├── properties/         # Orange County parcels (~7 MB)
│   │   └── childcare/          # NC DCDEE facility data
│   ├── cache/                  # Auto-downloaded (.gitignored)
│   └── processed/              # Analysis outputs
├── assets/
│   ├── maps/                   # Interactive HTML maps + static images
│   └── charts/                 # Comparison charts
├── docs/                       # Methodology and limitations
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
# Run TRAP / tree canopy analysis (also downloads school locations)
python src/road_pollution.py

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

---

## Known TODOs

- **`maps.py`**: Needs complete restructuring — currently school-specific. Must be generalized to support any school or all schools equally.
- **`data_processing.py`**: Contains non-geospatial functions. Should be slimmed to geospatial utilities only.
- **Cross-module constants**: Consider a shared `config.py` for `CHAPEL_HILL_CENTER`, `CRS_WGS84`, `CRS_UTM17N`, `SCHOOL_CSV`.
- **`data/processed/ROAD_POLLUTION.md`**: Auto-generated — will be regenerated with neutral framing when `road_pollution.py` is next run.
