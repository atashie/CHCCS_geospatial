# CHCCS Geospatial Analysis — Implementation Notes

Technical implementation details for the geospatial analysis modules.

---

## Flood Plain Analysis

### Overview

FEMA flood plain overlay on school property parcels showing flood exposure across all CHCCS elementary schools.

### Key Findings

| School | 100-yr Overlap | % of Property |
|--------|---------------|---------------|
| FPG Bilingue | 2.59 acres | 26% of 9.8 ac |
| Rashkis | 1.22 acres | 7% of 17.14 ac |
| All others | 0 | 0% |

### Data Sources

- **Flood zones:** FEMA National Flood Hazard Layer (NFHL), layer 28 (S_FLD_HAZ_AR), queried via ArcGIS REST API with 3x3 tiled bbox requests
- **School properties:** Orange County parcel data (`combined_data_polys.gpkg`), matched by which parcel contains each school's NCES coordinate point
- **School locations:** `data/cache/nces_school_locations.csv` (NCES EDGE 2023-24)

### Key Outputs

| File | Purpose |
|------|---------|
| `src/flood_map.py` | Standalone script: downloads FEMA data, identifies school parcels, computes overlaps, renders single-panel PNG |
| `assets/maps/flood_school_properties.png` | Map of school properties with flood zone overlays |
| `data/cache/fema_flood_zones.gpkg` | Cached FEMA flood zone polygons |

### Technical Notes

- FEMA API (`hazards.fema.gov/arcgis/...`) errors on large bounding boxes; solved by tiling into 3x3 sub-bboxes
- Some FEMA polygons have invalid geometry; fixed with `make_valid()` before `unary_union`
- School parcels are owned by various entities (school board, Orange County, Town of Chapel Hill); identified by spatial containment of NCES point rather than owner name filtering
- Overlap areas are computed in UTM (EPSG:32617): both school properties and flood unions are reprojected to UTM, intersection geometry area is computed in square meters, then converted to acres (`m² / 4046.86`). This replaces the earlier approximate latitude-factor approach.

---

## School Desert Analysis

### Overview

Interactive map (`school_community_map.html`) showing travel-time impacts of closing each elementary school, with affected-household histograms that update per scenario and travel mode.

### Workflow

1. **Load schools** — NCES EDGE 2023-24 locations (11 schools, cached at `data/cache/nces_school_locations.csv`)
2. **Load district boundary** — Census TIGER/Line GEOID 3700720 (cached as GeoPackage)
3. **Download road networks** — OSMnx drive/bike/walk graphs, cached as GraphML; reverse edges added for bidirectional traversal
4. **Dijkstra from each school** — 33 runs (11 schools x 3 modes), each exploring the full graph (no cutoff)
5. **Create grid** — 100 m resolution point grid inside district polygon (~16K points); school locations injected as zero-time anchor points
6. **Edge-snap grid points** — Each grid point snaps to the nearest road edge via Shapely STRtree (not just nearest node); travel time is interpolated along the matched edge using fractional position; off-network access leg adds time at a mode-specific fraction of modal speed (walk 90%, bike 80%, drive 20%)
7. **Compute desert scores** — For each grid point x scenario x mode, take min travel time across open schools; delta = scenario time - baseline time
8. **Rasterize** — Grid values projected onto a shared pixel grid (WGS84), gap-filled for UTM->WGS84 rotation artifacts, masked to district polygon; colorized with RdYlGn_r (absolute) or Oranges (delta); saved as GeoTIFF + base64 PNG for Leaflet image overlays
9. **Load property centroids** — ~21K residential parcels from Orange County GIS (`combined_data_centroids.gpkg`), clipped to district boundary
10. **Snap centroids to grid** — cKDTree with cos(lat) longitude scaling; each parcel assigned its nearest `grid_id`
11. **Compute affected parcels** — For each non-baseline scenario x mode, parcels whose grid point has `delta_minutes > 0` are "affected"
12. **Render histograms** — Two matplotlib charts per scenario|mode: assessed value (blue, 25 bins) and years since sale (green, 25 bins), each with a red dashed median line; encoded as base64 PNGs
13. **Build map** — Folium map with all overlays + JS switching; chart panel occupies bottom 35vh of viewport and updates on scenario/mode change

### Definition of "Affected"

A residential parcel is **affected** if its nearest grid point has `delta_minutes > 0` for the selected closure scenario + travel mode. This means closing that school increased travel time to the nearest remaining school at that location.

### Assumptions

| Assumption | Rationale |
|-----------|-----------|
| Static travel speeds (no real-time traffic, no turn penalties) | Consistent, reproducible model; effective speeds already discount for signals/stops via HCM6 ratios |
| Walk speed 2.5 mph for all K-5 | Mid-range of MUTCD/FHWA measurements for school-age children |
| Effective drive speeds 65-92% of posted | HCM6 Ch.16 and FHWA Urban Arterial Speed Studies |
| Off-network access leg at reduced speed (walk 90%, bike 80%, drive 20%) | Walking/biking to the road is nearly full-speed; driving off-network (driveways, lots) is much slower |
| Grid points >200 m from any road are unreachable | 2x grid resolution; filters lakes, large parks, undeveloped land |
| All remaining schools absorb displaced students | No capacity constraints modeled |
| Binary affected definition (delta > 0) | Simple, transparent; does not weight by magnitude of increase |
| Parcel-to-grid snapping uses Euclidean nearest point | Not network distance; acceptable at 100 m grid resolution |

### Limitations

- **No capacity constraints:** The model assumes every remaining school can absorb displaced students. In practice, some schools may be full.
- **No turn penalties or intersection delays:** Dijkstra uses edge-level travel times only; left-turn delays, traffic signals, and stop signs are approximated by the effective speed reduction but not modeled explicitly.
- **Tax-record lag:** Assessed values are from the latest Orange County tax records and may not reflect current market values.
- **Sale date coverage:** `years_since_sale` reflects the most recent recorded deed transfer. Properties with no recorded sale are excluded from that histogram (shown as NaN).
- **Static road network:** The OSM snapshot is fixed at download time. Road construction or closures after download are not reflected.
- **No school-choice or magnet effects:** The model assumes families attend their geographically nearest school. Magnet, charter, and school-choice assignments are not modeled.

### Key Outputs

| Output | Description |
|--------|-------------|
| `assets/maps/school_community_map.html` | Interactive map: heatmap + road network + property parcels + affected-household histograms |
| `data/processed/school_desert_grid.csv` | ~340K rows: grid_id x scenario x mode with travel times and deltas |
| `data/cache/school_desert_tiffs/` | GeoTIFF rasters for each scenario/mode/layer |

### Affected Household Counts

Affected household counts are computed per scenario when `school_desert.py` runs. The analysis now covers all 11 elementary schools with one closure scenario each. Run `python src/school_desert.py` to generate current counts.

---

## Socioeconomic Analysis

### Overview

Census-based demographic analysis of CHCCS elementary school attendance zones using ACS 5-Year block group estimates and 2020 Decennial block-level race data, with dasymetric areal interpolation weighted by residential parcel area. Produces per-zone demographic summaries, an interactive Folium map, static comparison charts, and auto-generated methodology documentation.

### Key Features

- **7 choropleth layers** (block level): median income, % below 185% poverty, % minority, % renter, % zero-vehicle, % elementary age 5-9, % young children 0-4
- **1:1 dot-density race layer** (~95,764 dots) with dasymetric placement constrained to residential parcels
- **5 zone types** with radio-button switching: School Zones (10 attendance zones), Walk Zones (7 CHCCS walk zones), Nearest Walk (11 Voronoi-like zones), Nearest Bike (11), Nearest Drive (11)
- **Per-zone barplots and histograms** rendered in a sidebar panel, updating on zone type and school selection
- **Batch JS rendering** for dot-density (compact array + for-loop, Canvas renderer)

### Key Outputs

| File | Purpose |
|------|---------|
| `src/school_socioeconomic_analysis.py` | Main module: Census API download, spatial analysis, dasymetric interpolation, dot-density generation, Folium map, charts, auto-docs |
| `assets/maps/school_socioeconomic_map.html` | Interactive Folium map with choropleth, dot-density, and 5 zone type overlays |
| `assets/charts/socioeconomic_*.png` | Horizontal bar charts + income distribution chart |
| `data/processed/census_school_demographics.csv` | Per-school-zone demographic summaries (~20 metrics) |
| `data/processed/census_blockgroup_profiles.csv` | Block-group-level derived metrics within district |
| `docs/socioeconomic/SOCIOECONOMIC_ANALYSIS.md` | Auto-generated methodology and results documentation |

---

## Environmental Analysis (Consolidated Map)

### Overview

Consolidated interactive map combining TRAP pollution, flood risk, tree canopy, and UHI proxy layers for all 11 CHCCS elementary schools. See [`docs/ENVIRONMENTAL_ANALYSIS_README.md`](ENVIRONMENTAL_ANALYSIS_README.md) for full methodology.

### Key Outputs

| File | Purpose |
|------|---------|
| `src/environmental_map.py` | Builds consolidated Folium map from cached grids and downloaded data |
| `assets/maps/chccs_environmental_analysis.html` | Interactive map with 7 toggleable layers, dynamic legends, and aggregated school popups |
| `data/processed/uhi_proxy_scores.csv` | Per-school UHI proxy scores at 500 m and 1000 m |
| `data/cache/trap_grids.npz` | Cached TRAP raw and net grid arrays |
| `data/cache/uhi_grid.npz` | Cached UHI proxy grid array |

### Technical Notes

- Raster layers (TRAP, UHI, tree canopy) are clipped to the district boundary polygon (with 200 m UTM buffer for edge smoothing) before rendering
- Uses matplotlib perceptually-uniform colormaps: `YlOrRd` for TRAP, `RdYlBu_r` for UHI
- School markers use fixed blue CircleMarkers (`#2196F3`, radius 6) matching the socioeconomic map style
- Grid caching (`.npz` files) requires manual deletion to regenerate after input data changes

---

## School Closure Impact Analysis

### Overview

Comprehensive analysis combining travel-time impacts with children-weighted traffic network analysis for all 11 CHCCS elementary school closure scenarios. Extends the school desert methodology with route extraction (via predecessor maps) and dasymetric children distribution.

### Workflow

1. **Load data** — NCES schools, district boundary, walk zones, attendance zones
2. **Load networks** — Cached OSMnx drive/bike/walk graphs
3. **Create grid** — 100 m UTM grid (~16K points) + school anchor points
4. **Edge-snap** — Shapely STRtree batch nearest-edge with fractional interpolation
5. **Dijkstra with predecessors** — `dijkstra_predecessor_and_distance()` returns both distances AND predecessor maps; ~4 MB total memory for 33 runs
6. **Vectorized pixel assignment** — NumPy-based nearest-school computation (~10x faster than Python loop in school_desert.py)
7. **Zone polygon generation** — Pixel assignments → 55 m squares → dissolve → clip
8. **Rasterize + delta** — Shared WGS84 pixel grid, rotation gap-fill, boundary mask
9. **Children distribution** — ACS B01001 → blocks (dasymetric) → pixels (area intersection)
10. **Route extraction + traffic** — Reconstruct paths from predecessors, accumulate children on edges
11. **Traffic variants** — 4 combinations: ±walk_mask × ±zone_routing, each for 12 scenarios × 2 age groups
12. **Build map** — Folium + embedded JS with road GeoJSON, traffic Float32Arrays, heatmap ImageOverlays

### Key Design Decisions

| Decision | Rationale |
|----------|-----------|
| `dijkstra_predecessor_and_distance()` instead of `single_source_dijkstra_path_length()` | Enables route reconstruction at O(V) memory cost instead of O(V×path_length) |
| Vectorized pixel assignment with NumPy | ~10x speedup over per-pixel Python loop |
| Pre-computed traffic for all mask/zone combinations | Avoids complex client-side recomputation; 4 combinations × 12 scenarios × 2 ages = 96 arrays |
| Drive-only traffic analysis | Bike/walk traffic is negligible for road network impact |
| Pickle for Dijkstra cache | Complex nested dict structure not suited for NPZ/CSV |

### Key Outputs

| File | Purpose |
|------|---------|
| `src/school_closure_analysis.py` | Self-contained standalone script |
| `assets/maps/school_closure_analysis.html` | Interactive map with Part 1 + Part 2 layers |
| `data/processed/school_closure_assignments.csv` | Per-pixel travel time assignments |
| `data/processed/school_closure_traffic.csv` | Per-edge traffic aggregation |
| `data/cache/closure_analysis/` | Cached Dijkstra results, grid, snap arrays, children data |

---

## Affordable Housing Data

### Overview

Downloads and assesses affordable housing locations from the Town of Chapel Hill ArcGIS REST API.

### Key Outputs

| File | Purpose |
|------|---------|
| `src/affordable_housing.py` | Downloads affordable housing data, assesses quality |
| `data/cache/affordable_housing.gpkg` | Cached affordable housing locations |
| `data/processed/AFFORDABLE_HOUSING_DATA.md` | Data quality assessment and summary |

---

## Attribution

This analysis was developed with assistance from Claude (Anthropic) for code generation, spatial analysis implementation, and documentation. All data comes from official public sources.

---

*Last updated: March 2026*
