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
5. **Create grid** — 100 m WGS84-native point grid inside district polygon (~16K points) using latitude-corrected degree spacing; school locations injected as zero-time anchor points
6. **Edge-snap grid points** — Each grid point snaps to the nearest road edge via Shapely STRtree (not just nearest node); travel time is interpolated along the matched edge using fractional position; off-network access leg adds time at a mode-specific fraction of modal speed (walk 90%, bike 80%, drive 20%)
7. **Compute desert scores** — For each grid point x scenario x mode, take min travel time across open schools; delta = scenario time - baseline time
8. **Rasterize** — Grid values projected onto a shared pixel grid (WGS84), masked to district polygon; colorized with RdYlGn_r (absolute) or Oranges (delta); saved as GeoTIFF + base64 PNG for Leaflet image overlays
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
| Static travel speeds with explicit intersection penalties (no real-time traffic) | Consistent, reproducible model; friction speeds capture mid-block delay, intersection penalties (signals 22 s, stops 11 s) model control delays explicitly |
| Walk speed 2.5 mph for all K-5 | Mid-range of MUTCD/FHWA measurements for school-age children |
| Drive friction speeds 76-91% of posted + node penalties | HCM6 Ch.16 (friction), Ch.19/20 (intersection penalties, LOS D school-hour peak); tags from Overpass API |
| Off-network access leg at reduced speed (walk 90%, bike 80%, drive 20%) | Walking/biking to the road is nearly full-speed; driving off-network (driveways, lots) is much slower |
| Grid points >200 m from any road are unreachable | 2x grid resolution; filters lakes, large parks, undeveloped land |
| All remaining schools absorb displaced students | No capacity constraints modeled |
| Binary affected definition (delta > 0) | Simple, transparent; does not weight by magnitude of increase |
| Parcel-to-grid snapping uses Euclidean nearest point | Not network distance; acceptable at 100 m grid resolution |

### Limitations

- **No capacity constraints:** The model assumes every remaining school can absorb displaced students. In practice, some schools may be full.
- **No turn penalties:** Dijkstra models intersection control delays (traffic signals, stop signs, yield signs) as explicit per-node penalties added to edge weights, but does not differentiate left-turn vs. right-turn costs. Stop sign coverage in OSM is incomplete — only a fraction of actual stop signs are mapped; untagged intersections receive no penalty.
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

- **6 choropleth layers** (block level): median income, % below 185% poverty, % minority, % zero-vehicle, % elementary age 5-9, % young children 0-4
- **1:1 dot-density race layer** (~95,764 dots) with dasymetric placement constrained to residential parcels
- **4 marker layers** under HOUSING category: affordable housing (AMI-colored), MLS home sales (price-colored), planned developments — CH Active Dev (blue-yellow-red by unit count, 2 bar charts) and SAPFOTAC (same color scheme, 3 bar charts including projected elementary students)
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
| `data/processed/census_dot_zone_demographics.csv` | Per-zone-type demographics aggregated from dot placement. Population-denominated percentages (`pct_minority`, `pct_elementary_age`, `pct_young_children`) use a dot-weighted mean of block values; extensive-denominator percentages (`pct_below_185_poverty`, `pct_zero_vehicle`, `pct_renter`) use per-dot attribution of block raw counts (Σ num / Σ den). Consumed by scrollytelling scripts and static drive-zone bar charts. |
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
- Below the map, a 2×2 grid of horizontal bar charts (Chart.js 4.x via CDN) compares all 11 schools on Raw Air Pollution, Net Air Pollution, Urban Heat Island (Index), and Flood Zone %; data is serialized from `all_metrics` and injected via post-save HTML replacement

---

## School Closure Impact Analysis

### Overview

Comprehensive analysis combining travel-time impacts with children-weighted traffic network analysis for arbitrary multi-school closure scenarios across all 11 CHCCS elementary schools. Extends the school desert methodology with predecessor maps for client-side route reconstruction and dasymetric children distribution.

### Workflow

1. **Load data** — NCES schools, district boundary, walk zones, attendance zones
2. **Load networks** — Cached OSMnx drive/bike/walk graphs
3. **Create grid** — 100 m WGS84-native grid (~16K points) using latitude-corrected degree spacing + school anchor points
4. **Edge-snap** — Shapely STRtree batch nearest-edge with fractional interpolation
5. **Dijkstra with predecessors** — `dijkstra_predecessor_and_distance()` returns both distances AND predecessor maps; ~4 MB total memory for 33 runs
6. **Per-school travel grids** — For each school × mode, rasterize travel time to float32 2D array (~2.8 MB total for 33 grids). These replace pre-rendered PNG overlays.
7. **Zone polygon generation** — Baseline pixel assignments → 55 m squares → dissolve → clip → GeoJSON (3 sets: baseline × 3 modes)
8. **Children distribution** — ACS B01001 → blocks (dasymetric) → pixels (area intersection)
9. **Client-side traffic data** — Predecessor maps (int16, ~200 KB) + edge lookup (~66 KB) + per-pixel metadata (children, walk zone, zone school, entry nodes) embedded for JS route reconstruction
10. **Build map** — Folium + client-side canvas rendering with embedded per-school float32 grids, colormap LUTs, road GeoJSON, predecessor maps, edge lookup

### Key Design Decisions

| Decision | Rationale |
|----------|-----------|
| `dijkstra_predecessor_and_distance()` instead of `single_source_dijkstra_path_length()` | Enables route reconstruction at O(V) memory cost instead of O(V×path_length) |
| Vectorized pixel assignment with NumPy | ~10x speedup over per-pixel Python loop |
| Multi-select school checkboxes instead of single-scenario radio buttons | Enables any combination of closures (2^11 = 2,048 scenarios) without pre-computation |
| Client-side canvas rendering from per-school grids + colormap LUTs | JS computes `min(open_schools)` in ~5 ms for any closure combination; 256-entry RGBA LUTs from matplotlib for faithful coloring |
| Predecessor maps + edge lookup for client-side traffic (~0.5 MB) | JS walks predecessor chains to reconstruct routes for any closure set; replaces 4.8 MB of pre-computed per-scenario traffic arrays |
| Tabbed sidebar UI (Part 1 / Part 2) | Separates travel time and traffic controls into focused views instead of 17+ mixed controls |
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

## School Closure Scrollytelling

### Overview

Interactive scrollytelling page (`closure_methodology.html`) that walks non-technical readers through the school closure analysis methodology, mirroring the environmental methodology page pattern.

**Script:** `src/closure_story.py`
**Output:** `assets/maps/closure_methodology.html`

### Architecture

Mirrors `environmental_story.py` exactly: loads all data from existing caches (requires `school_closure_analysis.py` to have been run first), computes lightweight visualizations, and embeds everything into a single self-contained HTML file with Leaflet + Scrollama.

### Key Implementation Details

- **19 narrative steps** covering road networks, speed model, Dijkstra, edge snapping, grid, heatmaps (drive + walk), dasymetric child distribution, route reconstruction, traffic aggregation, walk zone masking, traffic redistribution, and limitations
- **Northside Elementary** used as illustrative example (centrally located, clear redistribution)
- **Heatmaps computed from cached Dijkstra** — loads `dijkstra_drive.pkl` and `snap_drive.npz`, computes pixel-level travel times, rasterizes to 2D grid, converts to base64 PNG overlays
- **Traffic computed from scratch** — reconstructs routes from predecessor maps rather than relying on `school_closure_traffic.csv` edge indices, ensuring robustness to edge ordering changes
- **Traffic delta** computed as closure traffic minus baseline traffic per edge, displayed as diverging blue-red coloring
- **Bbox clipping** — all heavy data (roads, blocks, parcels) clipped to ±0.02° around Northside for local views

### Key Outputs

| File | Purpose |
|------|---------|
| `src/closure_story.py` | Generator script |
| `assets/maps/closure_methodology.html` | Self-contained scrollytelling page (~1.2 MB) |

---

## Socioeconomic Methodology Scrollytelling

### Overview

Interactive scrollytelling page (`socioeconomic_methodology.html`) that walks non-technical readers through the Census-based socioeconomic analysis methodology — explaining areal interpolation, dasymetric weighting, dot-density mapping, and zone-level aggregation.

**Script:** `src/socioeconomic_story.py`
**Output:** `assets/maps/socioeconomic_methodology.html`

### Architecture

Mirrors `closure_story.py` exactly: loads all data from existing caches (requires `school_socioeconomic_analysis.py` to have been run first), computes lightweight visualizations, and embeds everything into a single self-contained HTML file with Leaflet + Scrollama.

### Key Implementation Details

- **21-step story arc** covering zones, block groups, the mismatch problem, area vs. dasymetric weighting, derived metrics, block-level data, dot-density generation, zone aggregation, walk zones, affordable housing, MLS home sales, planned developments (CH Active Dev + SAPFOTAC), and limitations
- **Focus area:** Northside Elementary bbox (~0.02° padding) — generates ~5-10K dots instead of 95K for the full district, keeping file size < 5 MB
- **Fragment visualization:** Computes zone-BG intersection fragments for Northside with both area and dasymetric weights, showing the contrast visually
- **Dot-density generation:** Same algorithm as `generate_racial_dots()` in `school_socioeconomic_analysis.py`, spatially filtered to focus area, 1:1 dot-to-person ratio
- **Nearest-drive zone demographics** are read from `data/processed/census_dot_zone_demographics.csv`, a per-zone aggregation exported by `school_socioeconomic_analysis.py` that mirrors the interactive map's JS `updateHistograms()` routing: population-denominated percentages use a dot-weighted mean of block values; extensive-denominator percentages (poverty, zero-vehicle, renter) use per-dot attribution of block raw counts (universe-correct as of April 2026)
- **All data is cache-only** — no network requests, no Census API calls

### Key Outputs

| File | Purpose |
|------|---------|
| `src/socioeconomic_story.py` | Generator script |
| `assets/maps/socioeconomic_methodology.html` | Self-contained scrollytelling page (~3-5 MB) |

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

### Zone-Aggregated Unit Counts — Historical Bug Pair (both FIXED 2026-04-10)

Two independent code paths aggregate affordable-housing unit counts per school zone and at one point disagreed by up to ~3× per school. Both bugs were fixed on 2026-04-10 and the two paths now share a single canonical zone builder.

**Affected paths:**

1. **Static PNG** — `src/bar_charts_drive_zones.py` → `assets/charts/bars_affordable_housing_drive.png`
2. **Interactive map bar-ah canvas** — `src/school_socioeconomic_analysis.py` → `assets/maps/school_socioeconomic_map.html` (shown when metric = "Affordable Housing Units")

#### Bug A: Interactive map block-based aggregation

**Symptom:** The in-browser bar chart for Affordable Housing inflated totals by ~34% district-wide, with distortions concentrated at drive zones whose boundaries cut through densely-AH blocks (Glenwood +76, Northside +154, Seawell +57).

**Root cause:** The JS function `updateHistograms()` computed zone totals *dynamically from Census blocks* instead of using a precomputed point-in-polygon count. The algorithm iterated over every population dot, looked up the dot's zone and its block, and if that `(zone, block)` pair had not been seen yet, added **the entire block's `ah_units`** to the zone. Any block whose dots fell in multiple drive zones credited its full AH count to every such zone, so blocks straddling boundaries were double-counted. Blocks with no population dots were silently dropped entirely, so the total was simultaneously over- and under-counted. A flat `ahByZone` array (attendance-zone totals from a correct direct sjoin) was already serialized into the HTML but was declared-and-forgotten — the JS never read it.

**Fix:**

- In `school_socioeconomic_analysis.py`, replaced the flat `ah_by_zone_list` with a **nested per-zone-type list** built by a new `_ah_by_zone_type()` helper that mirrors `_mls_by_zone_type()` / `_dev_by_zone_type()`. The helper runs a direct `sjoin(ah_points, zone_gdf, predicate="within")`, dedupes AH points that fall in multiple zones (defensive `keep="first"` — unnecessary after Bug B fix but kept as a safeguard), groups by school, and returns a list aligned to `master_school_names`. The outer loop runs it once per entry in `active_zone_gdfs` so every active zone type (School Zones, Walk Zones, Nearest Walk/Bike/Drive) gets its own correct counts.
- In the embedded JS, the ~25-line dot/block loop in `updateHistograms()`'s `isHousing` branch was replaced with a single lookup: `drawBarplot('bar-ah', masterSchools, ahByZone[currentZoneType], 'count')`.

#### Bug B: Overlapping slivers in the square-buffer zone construction

**Symptom:** Before the fix, `bars_affordable_housing_drive.png` reported 1,179 total AH units across drive zones when the source gpkg contained only 1,168. 12 points were double-counted, 1 was dropped (outside the clipped district).

**Root cause:** Both `_build_drive_zones()` in `src/bar_charts_drive_zones.py` and `_build_nearest_zones()` in `src/school_socioeconomic_analysis.py` constructed nearest-school zones by reading the `school_desert_grid.csv` point grid, projecting to UTM17N, **buffering each point to a 110 m square** (`box(x-55, y-55, x+55, y+55)`), and dissolving by `nearest_school`. Because the 110 m squares exceed the ~100 m grid spacing, squares belonging to different schools overlap by ~10 m near each zone boundary. The dissolve does not resolve those cross-school overlaps, so the per-school polygons end up with thin overlap slivers along every shared edge. Twelve AH points happened to fall inside Ephesus↔neighbour slivers, so `sjoin(predicate="within")` returned them in **two** zones.

The bar chart's direct sjoin was internally consistent — the bug was upstream, in the zone construction, and was silently present in the map's `_build_nearest_zones()` as well (though masked for most metrics because they were aggregated from dot-level data that only uses "nearest zone" labels, not the zone polygons).

**Fix — Voronoi partition via a shared primitive:** A new helper
`voronoi_zones_from_labelled_points(labelled_pts, label_col, district)` in
`school_socioeconomic_analysis.py` encapsulates the canonical partition logic:

1. Reproject labelled points to UTM17N.
2. Build an envelope 1 km larger than the district bounding box.
3. Run `shapely.ops.voronoi_diagram(MultiPoint, envelope=...)` to generate
   one Voronoi cell per input point.
4. Transfer labels to cells via `sjoin(predicate="contains")` (each cell
   contains exactly one generator).
5. Dissolve by label, clip to the district polygon, return `[school, geometry]`.

`_build_nearest_zones()` in `school_socioeconomic_analysis.py` now delegates
to this primitive. **All other modules that previously duplicated the
square-buffer-and-dissolve logic were refactored to share it**:

- `src/bar_charts_drive_zones.py` — imports `_build_nearest_zones`.
- `src/district_schools_map.py` — imports `_build_nearest_zones`, reprojects to WebMercator.
- `src/alternative_schools_map.py` — imports `_build_nearest_zones`.
- `src/school_closure_analysis.py` — `build_zone_polygons()` imports `voronoi_zones_from_labelled_points` directly (its inputs come from numpy arrays rather than the CSV, so it skips the `_build_nearest_zones` wrapper).
- `example_stories/closure_scenarios_story.py` — editorial scrollytelling page; `build_nearest_walk_zones()` now delegates to `_build_nearest_zones(GRID_CSV, "walk", district)` and overlays the `ZONE_COLORS` mapping for Leaflet styling. Closed the last remaining `half = 55` instance in the repository (2026-04-10).
- `example_stories/chccs_demographics_story.py` — editorial scrollytelling page; already imported `_build_nearest_zones` before this work, but the four in-script sjoin calls that join MLS / AH / planned dev / SAPFOTAC points to drive zones now carry the same defensive `joined[~joined.index.duplicated(keep="first")]` guard as the helpers in `src/school_socioeconomic_analysis.py`. No-op under the Voronoi partition; insurance against future zone sources with genuine overlap.

All five scripts previously contained an identical `half = 55` square-buffer
block. That dead code has been deleted; a single source of truth now governs
every nearest-school partition in the project.

**Editorial story / generator drift hazard (2026-04-10):** While regenerating
`example_stories/chccs_demographics.html` from the post-R1 CSV, Codex's review
surfaced a silent regression: the age-choropleth colour scale reverted from a
data-driven `makeYlOrRd(bgMax("pct_young_children"))` factory to a hardcoded
0–10 % cap. Root cause was commit `fb804e8` (2026-03-31, "Demographics story:
use data-driven color scale for age choropleths"), which **hand-edited the
rendered HTML file without updating the generator script**. Any subsequent
regeneration would silently revert that change. Fixed by porting the
`makeYlOrRd(maxVal)` factory and `bgMax(metric)` helper into
`example_stories/chccs_demographics_story.py` at ~line 1916 so the generator
reproduces the committed HTML. **General hazard for `example_stories/`:** the
pattern of hand-editing `.html` outputs without back-propagating to the
`.py` generator leaves silent regressions waiting to be triggered. When
reviewing editorial stories, prefer `git log --format="%h %s" --follow
example_stories/<file>.html` and look for commits that touch only the `.html`
without the matching `.py` sibling.

**Why Voronoi is correct:** The grid points themselves carry a ground-truth `nearest_school` label (computed upstream by Dijkstra in `school_desert.py`). A Voronoi diagram over those points partitions space into "closest grid point" cells, so when dissolved by label the boundary between schools exactly traces the locus where the nearest labelled grid point switches from one school to another. This is what "nearest-school zone" was always supposed to mean; the square-buffer approach was an approximation that happened to leak overlap slivers.

**Verification (2026-04-10):**

| School | Old chart | Old map (after Bug A fix) | Voronoi (new, both paths) |
|---|---:|---:|---:|
| Ephesus | 381 | 369 | **376** |
| Northside | 252 | 252 | 252 |
| Frank Porter Graham Bilingue | 124 | 124 | 124 |
| Estes Hills | 121 | 121 | **115** |
| Seawell | 82 | 82 | 82 |
| Morris Grove | 70 | 70 | 70 |
| Rashkis | 48 | 48 | 48 |
| Glenwood | 43 | 43 | **42** |
| Carrboro | 30 | 30 | 30 |
| Scroggs | 28 | 28 | 28 |
| McDougle | 0 | 0 | 0 |
| **Total** | 1,179 | 1,167 | **1,167** |

The map's `ahByZone[4]` (Nearest Drive) and the static PNG now match unit-for-unit. All five zone types in the map (School Zones, Walk Zones, Nearest Walk/Bike/Drive) total 1,167 AH units within the district (1 point sits outside the clipped CHCCS boundary). The three shifts from the pre-Voronoi values (Ephesus −5, Estes −6, Glenwood −1; total 12) are AH units that previously landed in overlap slivers and are now correctly attributed to their actual nearest-school cell.

**Downstream implications:** Every consumer of `_build_nearest_zones()` — including MLS, planned developments, SAPFOTAC, and any future point-aggregation layer — now receives a clean partition. Totals for those metrics also shift slightly (by the same ~1% order of magnitude as AH) but are now correct. This also means the shared helper should be used wherever nearest-school aggregations are needed, rather than rebuilding zones locally.

### Zone-Level Percentage Metrics — Universe-Mismatch Bug (FIXED 2026-04-10)

After the Bug A/B fixes above, an audit of every metric in the interactive map exposed a second bug class in the JS `updateHistograms()` fallthrough branch: **universe mismatch** for extensive percentage metrics whose denominator is not total population.

**Symptom:** The map's Zero-Vehicle and Below-185%-Poverty barplots (per drive zone, walk zone, etc.) disagreed with the dasymetric reference in `census_school_demographics.csv` by up to 25 percentage points at magnet schools. Examples (School Zones, `pct_below_185_poverty`):

| School | Dasymetric (`census_school_demographics.csv`) | Dot-weighted (map, pre-fix) | Δ |
|---|---:|---:|---:|
| Glenwood | 14.3% | 39.7% | **+25.4 pp** |
| Seawell | 25.1% | 21.2% | −3.9 pp |
| Northside | 38.7% | 34.8% | −3.9 pp |
| Ephesus | 18.8% | 19.2% | +0.4 pp |

**Root cause:** The JS `updateHistograms()` fallthrough computed, for every percentage metric:

```js
mean = sum(schoolVals[si]) / len(schoolVals[si])    // dot-weighted mean of block %
estCount = round(schoolDotCounts[si] * mean / 100)  // labelled "Estimated Population"
```

`schoolVals[si]` was populated by pushing `block_pct` once for every dot landing in the zone. Since each block's `pct_below_185_poverty` equals its parent block group's percentage (weight cancels in the `downscale_bg_to_blocks` arithmetic), this was effectively a population-weighted mean of BG percentages. **This is correct only when the percentage's denominator is total population.** For `pct_zero_vehicle` (denominator: households) and `pct_renter` (denominator: households), population weighting overweights blocks with large households; for `pct_below_185_poverty` (denominator: `poverty_universe`, which excludes institutional group quarters like UNC dormitories), it overweights blocks with large group-quarters populations. Glenwood's +25 pp swing came from a small magnet zone picking up heavy dot placement from nearby densely-UNC blocks whose population was mostly in excluded group quarters.

The "Estimated Population" right-hand barplot compounded the error: for `pct_zero_vehicle` specifically, `population_dots × household_%` is dimensionally meaningless — neither "people in zero-vehicle households" nor "zero-vehicle household count."

The same bug propagated to `export_dot_zone_demographics()` in `src/school_socioeconomic_analysis.py`. Its derived count columns were all `pop × pct / 100` with `pop = n_dots`:

```python
rec["vehicles_zero"] = round(pop * _safe_pct("pct_zero_vehicle") / 100)
rec["vehicles_total_hh"] = pop          # WRONG — this is pop, not households
rec["below_185_pov"] = round(pop * _safe_pct("pct_below_185_poverty") / 100)
rec["poverty_universe"] = pop           # WRONG — this is pop, not the ACS poverty universe
```

The static chart `bars_no_vehicle_drive.png` read `vehicles_zero` directly from this CSV, so its bars were `population × household_%`. Its title was also wrong — it said "Households" but the column was actually a dimensional mismatch. Same for the "Residents Below 185% Poverty" chart (formerly mis-titled "Households below 185% Poverty"; the ACS C17002 universe is people, not households, so the new title is correct and matches the new correct counts).

**Fix:** `export_dot_zone_demographics()` was rewritten to split metrics into two classes:

1. **Population-denominated percentages** (`pct_minority`, `pct_elementary_age`, `pct_young_children`, `median_hh_income`): dot-weighted mean is provably correct when dots are 1-per-person and the percentage's denominator is total population. Algebraically, `mean(block_pct over dots) = Σ(block_num × dots_in_zone / total_pop) / n_dots_in_zone × 100` = `Σ(block_num × fraction_of_block_in_zone) / Σ(total_pop × fraction_of_block_in_zone) × 100` — the correct weighted aggregation. Left on the current path.

2. **Extensive metrics with non-population denominators** (`pct_below_185_poverty`, `pct_zero_vehicle`, `pct_renter`): each dot contributes a fraction `block_num / n_dots_in_block` to its zone's numerator total and `block_den / n_dots_in_block` to the denominator total. Zone percentage is `Σ num / Σ den × 100`, which is the universe-correct formula regardless of whether the underlying denominator is total population, households, or the ACS poverty universe. The per-dot weighting keeps the block→zone attribution consistent with how populations are attributed elsewhere in the map.

The function now returns `(df, extensive_by_zone)` where `extensive_by_zone` is a nested dict keyed by pct column name, each entry `{"pct": [zone_type_idx][school_idx], "count": [...]}`. The main pipeline serializes this as `extensiveByZone` in the embedded JS alongside a parallel `metricColumns` list mapping `currentMetric` index to the source column name.

In `updateHistograms()`, a new branch sits just before the dot-weighted fallthrough:

```js
var metricCol = metricColumns[currentMetric];
if (metricCol && extensiveByZone && extensiveByZone[metricCol]) {
    var extData = extensiveByZone[metricCol];
    drawBarplot('bar-left', masterSchools, extData.pct[currentZoneType], 'pct');
    drawBarplot('bar-right', masterSchools, extData.count[currentZoneType], 'count');
    return;
}
```

For `pct_below_185_poverty` and `pct_zero_vehicle`, this short-circuits the fallthrough with the precomputed correct values. For `pct_renter` the branch also fires (so the CSV and any future barplot agree). Population-denominated metrics (`pct_minority`, `pct_elementary_age`, `pct_young_children`) are not in `extensiveByZone`, so they continue to use the fallthrough — which is correct for them.

**Verification (2026-04-10):** Comparing the new dot-zone CSV to the dasymetric reference at School Zones:

| School | pct_pov (das / new / Δ) | pct_veh (das / new / Δ) |
|---|---|---|
| Glenwood | 14.3 / 15.3 / +1.0 | 2.0 / 2.5 / +0.5 |
| Seawell | 25.1 / 23.8 / −1.3 | 5.8 / 5.6 / −0.2 |
| Northside | 38.7 / 37.2 / −1.5 | 6.8 / 7.3 / +0.5 |
| Ephesus | 18.8 / 19.1 / +0.3 | 1.7 / 2.0 / +0.3 |
| Carrboro | 41.0 / 40.8 / −0.2 | 11.0 / 10.9 / −0.1 |

Residual shifts (≤ ~1.5 pp) come from the different boundary discretization between dasymetric area overlay (fragment weights based on parcel residential area) and dot-level attribution (weights based on the fraction of a block's random-parcel dots falling in a given zone). Both paths are now correct — just computed at different granularities.

**Static chart corrections:** Nearest-drive zero-vehicle counts shifted dramatically because the previous numbers were dimensionally nonsense. New totals: Carrboro 578, Estes 474, Northside 354, FPG 226, Ephesus 215, McDougle 104, Glenwood 74, Seawell 52, Scroggs 51, Morris Grove 20, Rashkis 1. Residents-below-185%-poverty: Northside 6,163, FPG 3,067, Carrboro 2,672, Estes 2,444, Ephesus 2,264, McDougle 1,142, Seawell 924, Glenwood 766, Morris Grove 641, Scroggs 360, Rashkis 29. The "Households below 185% Poverty" chart title was corrected to "Residents Below 185% Poverty" to match the ACS C17002 per-person universe.

**Defensive dedupe:** `_mls_by_zone_type`, `_dev_by_zone_type`, and `_sapfotac_by_zone_type` now also apply `joined = joined[~joined.index.duplicated(keep="first")]` after their sjoin calls, matching the pattern already in `_ah_by_zone_type`. The Voronoi nearest-zone partition currently has zero overlap so this is a no-op, but School Zones and Walk Zones come from separate shapefiles whose overlap hasn't been verified — the guard is cheap insurance. The `keep="first"` tiebreaker is arbitrary; if a future dataset genuinely has meaningfully overlapping zone polygons (rather than slivers), that assumption should be revisited.

**Defensive JS allow-list:** The `updateHistograms()` short-circuit branch also checks an explicit `extensiveAllowed` object before looking up `extensiveByZone[metricCol]`. This prevents accidental reuse if a future metric gets added to `extensive_specs` with a population denominator: the Python-side classification is the source of truth, but the JS defends itself independently.

**Dot-conservation sanity check (2026-04-10):** Summing the corrected raw numerator/denominator columns across all schools in each whole-district zone type yields identical district totals (Nearest Walk / Nearest Bike / Nearest Drive all produce 20,472 people below 185% poverty, 82,466 in the poverty universe, 2,149 zero-vehicle households, 34,766 total households, ±1 from integer rounding). Walk Zones sum to a lower total (22,300 dots) because they cover only ~1-mile buffers, not the whole district. The consistency across three partition schemes confirms the per-dot attribution conserves counts: no data is silently lost at zone boundaries, and dotless blocks (which would drop their numerator since `inv_dots_per_block = 0`) are not occurring in practice for the CHCCS district's 1:1 dot-per-person placement.

---

## MLS Home Sales Geocoding

### Overview

Geocodes Triangle MLS closed residential sales (2023-2025) and produces a point GeoPackage for spatial analysis with attendance zones and Census blocks.

### Workflow

1. **Load raw MLS data** — CSV from `data/raw/MLS/` containing address, close price, price per sqft, and other fields
2. **Census batch geocoding** — Addresses formatted and submitted to the U.S. Census Bureau batch geocoding API (`geocoding.geo.census.gov/geocoder/geographies/addressbatch`). Returns coordinates, match quality, and Census FIPS codes (state, county, tract, block). Submitted in batches (API limit: 10,000 per batch).
3. **Nominatim fallback** — Records that fail Census geocoding are retried against OpenStreetMap Nominatim with 1-second rate limiting per request to comply with usage policy.
4. **Merge and deduplicate** — Census and Nominatim results are merged; Census results take priority when both succeed.
5. **Output** — GeoPackage with point geometry (WGS84) saved to `data/cache/mls_home_sales.gpkg`.

### Key Outputs

| File | Purpose |
|------|---------|
| `src/mls_geocode.py` | Geocoding script: Census batch + Nominatim fallback |
| `data/cache/mls_home_sales.gpkg` | Geocoded MLS sales as point GeoPackage |

### Technical Notes

- Census batch API returns match type (`Exact`, `Non_Exact`, `Tie`, `No_Match`). Only `Exact` and `Non_Exact` matches are accepted.
- Nominatim geocoding uses structured queries with city/state constraints to reduce false matches.
- The script reports geocoding success rates (Census hit rate, Nominatim recovery rate, overall coverage) at runtime.
- Census geocoding returns Census geography FIPS codes, enabling direct block-level joins without a separate spatial join step for Census-matched records.
- The pipeline extracts a `bedrooms` column from the "Bedrooms Total" field in the raw MLS CSV. This is carried through geocoding into the GeoPackage output and displayed in map marker tooltips.
- The socioeconomic map presents MLS data under a consolidated **"Housing Market (2023-2025)"** toggle. The chart panel renders a 2x2 grid: Homes Sold (bar), Median Price (bar), Median Price/SqFt (bar), and Bedroom Distribution (histogram). All four charts update per zone when the zone type or school selection changes.
- **Bar chart labeling:** All HOUSING metric bar charts (AH, MLS, CH Active Dev, SAPFOTAC) use the master school list (all 11 schools) as labels, matching the master-indexed data arrays produced by spatial joins. This ensures correct label-value alignment across all zone types, including those with fewer than 11 zones (e.g., School Zones has 10, Walk Zones has 7). Schools absent from a zone type show zero values.

---

## Planned Developments Geocoding (CH Active Dev)

### Overview

Geocodes planned development addresses hand-transcribed from the Town of Chapel Hill [Active Development](https://www.chapelhillnc.gov/Business-and-Development/Active-Development) page (March 12, 2026) and produces a point GeoPackage for spatial analysis with attendance zones. Displayed on the socioeconomic map as the "Planned Developments (CH Active Dev)" metric.

### Workflow

1. **Load raw CSV** — `data/raw/properties/planned/CH_Development-3_26.csv` containing project name, address, and expected unit count
2. **Address cleaning** — Fix known typos (`_ADDRESS_FIXES` dict: "Erwin Toad" → "Erwin Road", "Weaver Diary" → "Weaver Dairy", "Martin Luther Kind" → "Martin Luther King") and simplify range/multi addresses via regex (e.g., "207 and 209 Meadowmont Lane" → "207 Meadowmont Lane", "1708 - 1712 Legion Road" → "1708 Legion Road")
3. **Census batch geocoding** — Addresses submitted to Census Bureau batch API. Chapel Hill first, then Carrboro retry for unmatched.
4. **Nominatim fallback** — Remaining unmatched addresses retried via Nominatim with 1-second rate limiting.
5. **District clipping** — Results clipped to CHCCS district boundary via `gpd.clip()`.
6. **Output** — GeoPackage with point geometry (WGS84) saved to `data/cache/planned_developments.gpkg`.

### Key Outputs

| File | Purpose |
|------|---------|
| `src/planned_dev_geocode.py` | Geocoding script: address cleaning + Census batch + Nominatim fallback |
| `data/cache/planned_developments.gpkg` | Geocoded planned developments as point GeoPackage |

### Technical Notes

- The socioeconomic map presents CH Active Dev planned developments as CircleMarkers under the HOUSING radio group ("Planned Developments (CH Active Dev)"), colored by unit count using the same blue→yellow→red palette as affordable housing (`#91bfdb` → `#fee090` → `#fc8d59` → `#d73027`). A categorical legend shows four unit-count bands (<50, 50–150, 150–400, 400+).
- Markers use a fixed radius of 10 px with a dark grey (`#555`) border (weight 1.5) for visual prominence, matching the Affordable Housing marker style.
- The chart panel renders a 1×2 grid: Total Expected Units (bar) and Number of Developments (bar), both updating per zone type.
- The demographics editorial story (`example_stories/chccs_demographics_story.py`) embeds both planned development datasets as `var DEV` (CH Active Dev) and `var SAPFOTAC` GeoJSON variables, rendering CircleMarkers with the same blue→yellow→red color scheme and fixed radius/border styling as the methodology map. Slide 16a shows CH Active Dev markers with a dual-panel bar chart of expected units. Slide 16b shows SAPFOTAC markers with a dual-panel bar chart of projected elementary students, plus an explanation of why the two datasets differ.
- Zone-level aggregation uses spatial join (`gpd.sjoin`) for each zone type, summing expected units and counting projects per zone.
- Loaded by `school_socioeconomic_analysis.py` as optional data — graceful degradation if cache is missing.

---

## SAPFOTAC 2025 Planned Developments

### Overview

Supplementary planned development data from the CHCCS SAPFOTAC (Student Attendance Projections and Facility Optimization Technical Advisory Committee) 2025 Annual Report, certified June 3, 2025. Provides projected student yields (elementary, middle, high) for 27 residential developments — data not available in the primary CH_Development source. The 21 future residential projects are displayed on the socioeconomic map as the "Planned Developments (SAPFOTAC)" metric with a 2×2 bar-chart grid (total projects, total units, elementary students per zone).

### Data Files

| File | Contents |
|------|----------|
| `data/raw/properties/planned/SAPFOTAC_2025_future_residential.csv` | 21 future residential projects with unit counts, projected student yields, and geocoded lat/lon |
| `data/raw/properties/planned/SAPFOTAC_2025_rezoning_approved.csv` | 6 rezoning-approved projects with expected unit ranges and geocoded lat/lon |
| `data/raw/properties/planned/2025_SAPFOTAC_Annual_Report-Certified_060325_202506061244509038.pdf` | Source PDF |
| `src/sapfotac_geocode.py` | Geocoding script (Census batch + Nominatim fallback) |

### Relationship to CH_Development Data

The SAPFOTAC CSVs are a **supplementary** source to the primary `CH_Development-3_26.csv`. Many projects appear in both datasets (e.g., Gateway, South Creek, Hillmont, Aura Chapel Hill). Key differences:

- **SAPFOTAC adds student yield projections** (elementary/middle/high counts per project) — not available in CH_Development.
- **SAPFOTAC includes Carrboro projects** (Jade Creek, Newbury) not in the Chapel Hill Active Development page.
- **CH_Development has more projects** (36 vs 27) and includes project status fields.
- **Both displayed as separate metrics** on the socioeconomic map under the HOUSING radio group. The two datasets are not deduplicated — some projects appear in both layers.
- **Carolina Demography supersedes for enrollment forecasting.** The April 2026 Carolina Demography enrollment forecast tracks 42 developments with field-verified statuses, probability-weighted completion estimates, and net-new student yield methodology (see [`CAROLINA_DEMOGRAPHY_ENROLLMENT_FORECAST_2026.md`](CAROLINA_DEMOGRAPHY_ENROLLMENT_FORECAST_2026.md)). SAPFOTAC and CH_Development remain the display datasets on the socioeconomic map.

### Address Research

Addresses were not included in the SAPFOTAC report and were researched from:
- `CH_Development-3_26.csv` (majority of Chapel Hill projects)
- Town of Chapel Hill official website (Gateway, Homestead Gardens, Tarheel Lodging, Weavers Grove)
- Town of Carrboro official website (Jade Creek, Newbury)
- Commercial listing sites (Millennium Chapel Hill — former University Inn site)

### Geocoding Notes

- Census Bureau batch API matched 18/27 addresses; Nominatim matched the remaining 9.
- `119 Bennett Way` (Park Apartments) is in `_CENSUS_SKIP` — the Census geocoder incorrectly matches it to "Bennett Woods," a different street. Nominatim correctly places it in the Blue Hill District near Fordham Blvd.
- All coordinates verified within 6.5 km of Chapel Hill center; no outliers.

---

## School Closure Scenarios (Editorial Story)

### Overview

Editorial scrollytelling page (`example_stories/closure_scenarios.html`) examining student movement under closure scenarios — traffic redistribution, capacity constraints, and school desert risk. Third story in the Ephesus-focused editorial series.

**Script:** `example_stories/closure_scenarios_story.py`
**Output:** `example_stories/closure_scenarios.html`

### Architecture

Same two-column layout as other editorial stories (45% narrative / 55% Leaflet map) with Scrollama-driven step transitions. 11 steps (data-step 0–10), numbered 1, 2, 3, 3b, 4, 4b, 5, 5b, 6, 7, 8 — covering introduction (transportation costs framing), capacity overview (with 2030 enrollment rationale), Seawell/Ephesus closure traffic (5-9 and 0-4 age groups), children bar chart, enrollment projections, choropleth, school desert scenario, and summary.

### Key Implementation Details

- **Traffic data extracted from working map** — Reads `school_closure_analysis.html` to extract road GeoJSON and base64-encoded Float32Array traffic arrays, avoiding recomputation
- **`find_road_deltas()` with `max_roads` parameter** — Reports most-positive delta (traffic increase) for specified roads instead of max-absolute; used for North Fordham Boulevard where the largest absolute delta is a large negative (traffic reduction near closed school) but the meaningful value is the max increase on receiving roads
- **Capacity overview slide (step 1, slide 2)** — DivIcon markers with per-school directional offsets (`capLabelOffsets` JS object) showing projected enrollment, capacity, and % occupied as center-aligned bold text with white text-shadow halo. Highlighted schools use `SCHOOL_COLORS` for the utilization percentage color; non-highlighted schools use utilization-based coloring (red >100%, amber ≥90%, gray otherwise). Narrative explains 2030 enrollment rationale, Seawell as fewest displaced students, and LEAP controlled-enrollment context. Offsets tuned to prevent label overlap at district zoom level.
- **Enrollment projections from PMR2 Forecast** — `ENROLLMENT_PROJECTIONS` constant with pre-Woolpert capacity figures; total spare capacity and below-capacity school count computed at generation time and embedded in narrative. Note: the April 2026 Carolina Demography enrollment forecast provides updated school-by-school ADM projections and capacity utilization through 2035-36 (see [`CAROLINA_DEMOGRAPHY_ENROLLMENT_FORECAST_2026.md`](CAROLINA_DEMOGRAPHY_ENROLLMENT_FORECAST_2026.md)); the story currently uses the older PMR2 figures.
- **11 handleStep cases** — Each Scrollama step triggers layer add/remove, traffic diff rendering, chart display, or choropleth display

### Key Outputs

| File | Purpose |
|------|---------|
| `example_stories/closure_scenarios_story.py` | Generator script |
| `example_stories/closure_scenarios.html` | Self-contained scrollytelling page (~3.4 MB) |

---

## Calibrated Enrollment Allocation Model

### Overview

**What this model is.** `src/naive_enrollment_allocation.py` is a **constrained fragment-level reallocation scenario generator** that can reproduce observed 2025-26 per-school ADM reasonably well. It fits a softmax (logit) choice model against the 11 per-school ADM totals using 14 free parameters. The only hard facts are (1) actual per-school ADM totaling 4,294, and (2) exactly 10% of district elementary-age residents opting out. The 14 parameters — intercepts, race bonuses, opt-out ratio, 5-9/0-4 mix, and income effects — are calibrated via multi-seed differential evolution + L-BFGS-B refinement, independently for MAE and RMSE.

**What this model is NOT.** It is **not** a credible estimator of the true magnitudes of racial preferences, income effects, or opt-out bias at CHCCS magnets. With 14 parameters against only 11 data points, and with max pairwise L2 ≈ 0.94 across DE seeds, the coefficient space is weakly identified and sometimes corner-driven: different seeds find quite different parameter vectors that reach similar school-total error. A casual reader should not interpret fitted coefficient values as "how strong the FPG Hispanic effect really is" — they are one configuration that reproduces the totals, not a point estimate of a real effect. Treat the model as a **scenario generator**, not a magnitude estimator.

**Granularity: block-group fragments.** The model operates on 143 (block-group × attendance-zone) fragments produced by dasymetric intersection of 121 ACS block groups (`data/cache/census_acs_blockgroups.gpkg`) with the CHCCS attendance zones, weighted by residential parcel area and filtered to drop fragments with `weight ≤ 1e-6`. Each fragment inherits its parent block group's race counts, age-bucket counts, and median household income, and is assigned to the attendance zone that contains it as its home school. A single block group that spans multiple zones becomes multiple fragments — each with its own home school but the same demographics. This preserves within-zone heterogeneity (wealthy block groups inside Northside can behave differently from poor ones) which is invisible in a zone-aggregate model. The loader calls `load_attendance_zones()` and `intersect_zones_with_blockgroups()` from `src/school_socioeconomic_analysis.py` via lazy import.

### Why FPG has no residency row

The CHCCS attendance-zone shapefile (`data/raw/properties/CHCCS/CHCCS.shp`) does not contain an `ENAME` entry for Frank Porter Graham Bilingue — it is a district-wide dual-language magnet with no traditional catchment. After the dasymetric intersection of ACS block groups with the 10 residential attendance zones, every fragment has a home school that is one of the 10 non-FPG zones. FPG has no home-zone fragments and its entire enrollment comes from the magnet-choice probabilities of kids in those fragments.

### Parameters (14 total)

| Parameter | Description | Bounds |
|---|---|---|
| `intercept_{FPG,Carrboro,Glenwood,Seawell}` | Log-odds intercept for each magnet relative to "stay at home-zone school" (4 params) | (-5, +5) |
| `bonus_FPG_hispanic` | Extra utility FPG gets for Hispanic kids | (0, 5) |
| `bonus_Glenwood_asian` | Extra utility Glenwood gets for Asian kids | (0, 5) |
| `bonus_Carrboro_{white,asian}` | Extra utility Carrboro gets for White/Asian kids (2 params) | (0, 5) |
| `bonus_Seawell_{white,asian}` | Extra utility Seawell gets for White/Asian kids (2 params) | (0, 5) |
| `w_white_optout` | Ratio of white vs non-white opt-out rate | (1, 5) |
| `mu_0_4` | Fraction of the effective elementary pool drawn from the ACS 0-4 bucket vs. the 5-9 bucket | (0, 1) |
| `w_income_magnet` | Per-(z_income) softmax utility bonus for Carrboro AND Seawell only | (0, 5) |
| `w_income_optout` | Log-linear per-(z_income) multiplier on the opt-out rate. Sign is **not** direction-constrained — negative values mean wealthier fragments opt out less | (-0.5, +0.5) |

The `(0, 5)` bound on bonus terms enforces the direction constraints the user confirmed (magnets' preferred races are at least as likely as the baseline; whites opt out at least as often as non-whites). Intercepts are allowed to be negative. `mu_0_4` exists because ACS 5-year estimates are 1-5 years old, so the 5-9 cohort has partially aged out of elementary and the 0-4 cohort has partially aged in; the optimizer picks the mix that best reproduces per-school ADM. The `w_income_optout` lower bound is negative by design — the data for this district showed that wealthier fragments retain more of their kids, which was the opposite of the original hypothesis but is empirically what the fit wants.

### Effective-pool construction

Both ACS buckets share each parent block group's total-population racial share as the age-race proxy. For a given `mu_0_4`, the forward model computes:

```
alpha = (1 - mu_0_4) * (DISTRICT_ADM / 0.9) / sum(kids_5_9)
beta  =       mu_0_4  * (DISTRICT_ADM / 0.9) / sum(kids_0_4)
combined[f, r] = alpha * raw_5_9[f, r] + beta * raw_0_4[f, r]
```

By construction, `sum(combined) == DISTRICT_ADM / 0.9` for every `mu_0_4 ∈ [0, 1]`. The per-race retention step (parameterized by `w_white_optout` + the kids-weighted district white share of the combined pool) then removes exactly 10% of the combined pool, leaving exactly `DISTRICT_ADM` students. No explicit rescaling step is needed.

### Key implementation details

- **Softmax rows are feasible by construction.** Every (fragment, race) destination distribution sums to 1 exactly with all entries in [0, 1]. The prior attempts at multiplicative `pull_rate × race_mult` formulations required soft penalties that broke L-BFGS-B refinement near the kinked boundary; the softmax eliminates that problem for the destination-choice step.
- **Opt-out rates can still go infeasible** at extreme parameter combinations — specifically, if `w_white_optout` is large and `w_income_optout` is strongly positive, the product `base * race_mult * income_mult` for a white kid in a wealthy fragment can exceed 1. `_retention_with_income()` returns a `feasible=False` flag in that case, and `compute_objective()` adds `INFEASIBLE_PENALTY = 1e5` to the loss so the optimizer avoids those corners. In practice this only triggers at the edges of the search space and does not affect the fitted optima.
- **Combined-pool total is pinned by construction.** On every forward-model call, the 5-9 and 0-4 buckets are mixed via `alpha = (1-mu_0_4) * TARGET/K_5_9` and `beta = mu_0_4 * TARGET/K_0_4`, where `TARGET = 4294/0.9 = 4771.11`. This guarantees `sum(combined) == TARGET` for any `mu_0_4 ∈ [0, 1]`. After the per-race opt-out removes exactly 10%, the retained pool is exactly 4,294. No explicit rescaling step is needed.
- **Per-(zone, race) retention** with income effect. The opt-out rate is `base * race_mult[r] * income_mult[f]`, where `race_mult[white_nh] = w_white_optout`, `income_mult[f] = exp(w_income_optout * z_income[f])`, and `base` is solved on every forward call so the weighted district opt-out equals exactly 10% regardless of the parameter vector. If any individual `opt_out_rate > 1` the objective returns an infeasibility penalty. The 10% constraint is verified at startup across a 3-D grid of `(w_white_optout, mu_0_4, w_income_optout)` values.
- **Calibration** uses `scipy.optimize.differential_evolution` from 3 seeds (42, 1337, 2024) followed by `scipy.optimize.minimize(method="L-BFGS-B")` on the best DE result. DE settings: `tol=1e-9, maxiter=500, popsize=25, init="sobol"`. Each calibration run takes ~30-60 seconds.
- **Stability diagnostic** records the L2 distance between DE seed parameter vectors (normalized to [0, 1] per dimension) and the objective spread. If either exceeds a loose tolerance, the fit is flagged as weakly identified.
- **Hessian diagnostic** computes a finite-difference Hessian at the optimum, reports its condition number, smallest absolute eigenvalue, and a covariance-derived correlation matrix for pairwise parameter trade-offs.
- **"Flows" in output are expected values.** The forward model is an assignment-probability model, not a solved integer transfer. Flow counts in `naive_enrollment_flows.csv` are `rescaled[f, r] * prob[f, m, r]` (where `f` is a block-group fragment) — real-valued expected-count contributions aggregated back to source zones for readability, not discrete student movements.

### Development history (the incremental path that got us here)

The module was built up through several iterations. Each step produced a meaningful finding that informed the next:

1. **11-parameter zone-level model** (no 5-9/0-4 mix, no income, direction-constrained opt-out). MAE ≈ 61, RMSE ≈ 109. FPG's Hispanic bonus fit to ≈ 0 because the loss function had no signal for racial composition. The Northside residual was ~300 students and structurally uncompressible.
2. **12-parameter zone-level model** adding `mu_0_4` (the mix weight between ACS 5-9 and 0-4 buckets). **MAE dropped to ~40, RMSE to ~57.** The optimizer pushed `mu_0_4` toward 1 — strong evidence the ACS 5-9 data is too stale for this district, and that reweighting toward the 0-4 cohort (which has since aged into elementary) matches the observed patterns better. The Northside residual compressed from ~300 to ~140.
3. **14-parameter zone-level model** adding `w_income_magnet` and `w_income_optout`. With the original direction constraint `w_income_optout ≥ 0`, both income parameters fit to zero — the hypothesis "wealthy zones opt out more" ran the wrong way against the observed residual pattern. Loosening to `w_income_optout ≥ -0.5` unlocked a meaningful negative fit (≈ -0.47), meaning **wealthy zones retain their kids more than poor ones**. The 10% district opt-out is still satisfied exactly; the skew is redistribution across zones, not a total change.
4. **14-parameter fragment-level model** (current). Replaced the zone-level `census_school_demographics.csv` with block-group-fragment granularity via dasymetric intersection. MAE ≈ 38, RMSE ≈ 52. Most importantly, **`w_income_magnet` became non-zero (≈ 0.7)** — the within-zone heterogeneity between wealthy and poor block groups, invisible to the zone-level model, actually carries the hypothesized "wealthy → more magnet" signal. This was the single clearest confirmation that the within-zone aggregation was hiding real variation.

### Observed findings from the current (14-param, fragment-level) calibration

- **All four magnets reach their ADM effectively exactly** under the MAE-fit (residuals ≤ 1 student).
- **FPG Hispanic bonus is strongly identified** (~+4.7). FPG's fitted racial composition is ~83% Hispanic vs. ~12% district average, matching the dual-language program's known skew.
- **Carrboro and Seawell show moderate White bonuses** (~+2.2 and +2.5) — consistent with the known wealthy/white skew at those partial magnets.
- **Glenwood's Asian bonus is effectively zero in the MAE-fit** — a softmax-ridge artifact. Glenwood's intercept plus the 0-4 bucket re-weighting fills the school without needing an Asian preference. The identifiability ridge means different DE seeds land at different parameter configurations with the same school-total error.
- **`w_income_magnet ≈ +0.7`.** Block groups with +1 standard deviation above average income get exp(0.7) ≈ 2× the probability of attending Carrboro/Seawell compared to the baseline.
- **`w_income_optout ≈ -0.48`.** Wealthy block groups retain kids more; poor block groups opt out at ~2-4× the rate of wealthy ones. This is the opposite of the user's original hypothesis but is what the data show.
- **`mu_0_4 ≈ 0.80`.** The model still leans heavily on the 0-4 bucket, though less extreme than at the zone level (where it was 0.97). Block-group granularity slightly reduces the need to compensate for stale 5-9 counts.
- **Northside residual persists at ~+165.** The covariates we have (race, age, block-group income) cannot explain Northside's anomaly. Candidate omitted factors: cross-district transfers, UNC-adjacent population turnover, private-school enrollment, lingering 2020-vintage ACS staleness. A Northside-specific dummy or dedicated transfer data would be needed.
- **Weak identification persists.** The MAE-fit's max pairwise L2 across DE seeds is ~0.94 — different seeds still land in quite different parameter regions. The RMSE-fit hits multiple rails and should be treated as a degenerate corner, not an answer. **MAE-fit is the more honest result.**

### Key outputs

| File | Purpose |
|------|---------|
| `data/processed/naive_enrollment_allocation.csv` | Per-school summary: actual ADM, raw kids 5-9 + 0-4 per school, naive enrollment, MAE-fit, RMSE-fit, residuals |
| `data/processed/naive_enrollment_by_race.csv` | Long format: school × race × scheme (naive, MAE-fit, RMSE-fit) expected counts |
| `data/processed/naive_enrollment_flows.csv` | Long format: scheme × source zone × destination magnet × race × expected-value count (aggregated from fragment-level flows) |
| `data/processed/NAIVE_ENROLLMENT_ALLOCATION.md` | Auto-generated methodology doc with fitted parameters, stability diagnostics, Hessian condition, per-school residuals |
| `assets/charts/naive_vs_actual_enrollment.png` | Grouped bar chart: actual ADM vs naive, with magnet labels |
| `assets/charts/naive_calibrated_vs_actual.png` | Four-bar comparison per school (actual, naive, MAE-fit, RMSE-fit) with both objectives in the title |
| `assets/charts/redistribution_flows_mae_fit.png` | MAE-fit magnet inflows stacked by race (expected values) |
| `assets/charts/redistribution_flows_rmse_fit.png` | RMSE-fit magnet inflows stacked by race |
| `assets/charts/magnet_racial_composition_comparison.png` | Three-panel per magnet: naive vs MAE-fit vs RMSE-fit racial composition |

### Known limitations

1. **14 params vs 11 per-school observations — under-determined at the loss level.** The fragment-level input gives the optimizer ~1,100+ (fragment × race) internal rows, but the loss aggregates back to the 11 school totals, so identification of the individual parameters is still weak. A near-perfect per-school fit is possible but parameter magnitudes are not interpretable as "true" district demographics.
2. **Weak identification is expected and observed.** Intercepts, bonuses, income coefficients, and the 5-9/0-4 mix all partially trade off inside the softmax. The MAE-fit's weak-identification flag is currently True; the RMSE-fit hits multiple bounds. Treat fitted parameters as "a" solution, not "the" solution.
3. **Race × age approximation.** Kids 5-9 and 0-4 racial composition is proxied by each fragment's parent block group's total-population racial shares. A proper per-age-bucket race breakdown would require ACS tables B01001A-I.
4. **Income sentinel imputation.** ~10 block groups carry the Census `-666,666,666` sentinel for `median_hh_income`. These are imputed with the district mean (z_income = 0) and flagged in the static model. Fragments inside these BGs contribute zero to the income signal.
5. **No out-of-sample validation.** Single data point = 2025-26 per-school ADM vector.
6. **Model structural limits.** The softmax cannot represent cross-district transfers, private-school enrollment (not included in the 10% opt-out), charter-specific draws, capacity constraints, or school-specific idiosyncrasies. The Northside residual is direct evidence of this.
7. **FPG Hispanic bonus has become load-bearing but is still partially a loss-function artifact.** FPG's total is dominated by the intercept since it has no home zone; the Hispanic bonus affects school totals only indirectly (by moving Hispanic kids out of their home zones and into FPG, which changes the home zones' residuals). A per-school enrollment-by-race loss term would identify this parameter better.
8. **Expected-value flows.** "Flows" in the output CSVs are continuous expected counts, not integer student movements.

---

## Attribution

This analysis was developed with assistance from Claude (Anthropic) for code generation, spatial analysis implementation, and documentation. All data comes from official public sources.

---

*Last updated: March 2026*
