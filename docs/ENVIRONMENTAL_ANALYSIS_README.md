# Environmental Analysis — CHCCS Elementary Schools

**Analysis Date:** March 2026
**Source Scripts:** `src/road_pollution.py`, `src/flood_map.py`, `src/environmental_map.py`
**Primary Output:** `assets/maps/chccs_environmental_analysis.html`

---

## High-Level Summary

The consolidated environmental analysis map is an interactive HTML file that layers four environmental factors onto a single map of all 11 CHCCS elementary schools. Users can toggle each layer on and off to explore how traffic pollution, flood risk, tree cover, and heat vulnerability vary across the district.

**What each layer shows:**

- **Air pollution from traffic (TRAP Exposure Index).** A heatmap showing where pollution from vehicle exhaust is highest, based on the size and proximity of nearby roads. Bigger roads and closer roads produce higher scores. The map offers both a "raw" version and a "net" version that accounts for tree canopy filtering some pollution.
- **Flood risk (FEMA Flood Plains).** Shaded zones from FEMA's National Flood Hazard Layer showing areas with a 1% annual chance of flooding (100-year) and a 0.2% annual chance (500-year). Where these zones overlap school property parcels, the overlap is highlighted in red.
- **Tree cover (ESA WorldCover).** A green overlay showing where satellite imagery classifies the ground as tree-covered at 10-meter resolution. Areas without trees appear transparent.
- **Heat vulnerability (UHI Proxy).** A blue-to-red heatmap estimating which areas are more likely to retain heat based on what covers the ground — pavement and buildings absorb heat, while trees and water cool the surrounding area. This is a proxy based on land cover classification, not actual temperature measurements.

**What the scores mean.** All indices are comparative: they rank schools against each other within the district. A higher TRAP score means more traffic pollution pressure relative to other schools, not that the air is unsafe. A higher UHI score means more built-up surroundings relative to other schools, not a measured temperature. These are screening tools for identifying relative differences, not absolute risk assessments.

**Key caveats in plain language.** The pollution model uses road size as a stand-in for actual traffic counts (only about 1% of road segments have measured traffic data). Tree cover comes from 2021 satellite imagery that can miss scattered suburban trees. The heat vulnerability layer is based entirely on what the ground looks like from space — it does not use thermometers or thermal satellite sensors. None of these indices account for wind, terrain, buildings, or time of day.

---

## Data Sources

| Data | Source | Resolution | Date | API/URL | Cache Path |
|------|--------|-----------|------|---------|------------|
| School locations | NCES EDGE 2023-24 | Point | 2023-24 | NCES REST API (LEAID 3700720) | `data/cache/nces_school_locations.csv` |
| School properties | Orange County parcel data | Polygon | current | Local `.gpkg` | `data/raw/properties/combined_data_polys.gpkg` |
| Road network | OpenStreetMap via osmnx | Line | current | `network_type="drive_service"` | `data/cache/osm_roads_orange_county_buffered.gpkg` |
| AADT traffic counts | NCDOT ArcGIS | Point | 2002-2022 | NCDOT Feature Server | `data/cache/ncdot_aadt_orange_county.gpkg` |
| Land cover | ESA WorldCover V2 2021 | 10m raster | 2021 | Planetary Computer STAC | `data/cache/esa_worldcover_orange_county.tif` |
| Flood zones | FEMA NFHL | Polygon | current | FEMA ArcGIS REST (layer 28) | `data/cache/fema_flood_zones.gpkg` |
| District boundary | Census TIGER/Line | Polygon | current | Census API | `data/cache/chccs_district_boundary.gpkg` |

---

## Technical Methodology — TRAP Exposure Index

Source: `src/road_pollution.py`

### Road Network Acquisition

Roads are downloaded from OpenStreetMap via osmnx using `network_type="drive_service"`, which includes all drivable through-roads plus service roads (parking-lot access, alleys) but excludes parking aisles and private roads. The query polygon is the Orange County, NC boundary buffered by 1000 m (the maximum analysis radius) in UTM 17N, ensuring that schools near the county edge still capture all road segments within the full analysis radius.

### Road Classification Weights

Each OSM road segment is assigned a weight as a proxy for its traffic volume. The `highway` tag determines the base weight:

| Road Class | AADT Proxy | Weight |
|------------|-----------|--------|
| motorway | ~50,000 | 1.000 |
| motorway_link | ~40,000 | 0.800 |
| trunk | ~30,000 | 0.600 |
| trunk_link | ~24,000 | 0.480 |
| primary | ~15,000 | 0.300 |
| primary_link | ~12,000 | 0.240 |
| secondary | ~7,500 | 0.150 |
| secondary_link | ~6,000 | 0.120 |
| tertiary | ~3,000 | 0.060 |
| tertiary_link | ~2,400 | 0.048 |
| unclassified | ~1,000 | 0.020 |
| residential | ~500 | 0.010 |
| service | ~250 | 0.005 |
| living_street | ~250 | 0.005 |

### Service Road Subtype Overrides

The base `service` weight (0.005) is overridden for tagged subtypes:

| Service Subtype | Weight |
|----------------|--------|
| driveway | 0.002 |
| alley | 0.003 |
| drive-through | 0.001 |

### NCDOT AADT Integration

Where measured traffic counts are available, they replace the proxy weights:

1. NCDOT AADT stations for Orange County are downloaded from the NCDOT ArcGIS Feature Server, paginated at 1000 records per request.
2. For each station, the most recent non-blank AADT value is extracted (checking fields `AADT_2022` down to `AADT_2002`).
3. Each station is snapped to the nearest OSM road segment within 50 m (`AADT_SNAP_DISTANCE_M`) using `gpd.sjoin_nearest` in UTM projection.
4. Weight is derived as: `weight = AADT / 50,000`, clipped to [0.001, 2.0], where 50,000 is the reference AADT for a motorway (weight = 1.0).
5. A `weight_source` column tracks whether each segment uses `"aadt"` or `"proxy"`.

Coverage: approximately 1.2% of road segments receive measured AADT overrides; the remaining 98.8% use proxy weights. AADT stations are concentrated on major and secondary roads.

### Road Discretization

Each road linestring is converted to ~50 m sub-segment centroids in UTM:

1. The road geometry is projected to EPSG:32617 (UTM 17N).
2. `n_segments = max(1, int(length / 50))` segments per road.
3. Each sub-segment centroid is computed via `geom.interpolate((i + 0.5) / n_segments, normalized=True)`.

This captures both distance and road length: a 500 m road produces ~10 sub-segments, each contributing independently to the pollution sum.

### Exponential Decay Formula

For each road sub-segment *i* within the analysis radius of a point:

```
P_i = W_i * exp(-λ * d_i)
```

Where:
- `W_i` = weight of sub-segment *i* (from road classification or AADT)
- `d_i` = Euclidean distance from sub-segment centroid to the query point (meters, in UTM)
- `λ` = 0.003 m⁻¹ (composite decay rate for NOx, black carbon, and ultrafine particles)

The KD-tree (`scipy.spatial.cKDTree`) built from sub-segment UTM coordinates provides efficient spatial queries. `query_ball_point` retrieves all sub-segments within the search radius, and distances are computed directly from the coordinate arrays.

**Total raw index:**

```
P_raw = Σ P_i   (for all sub-segments within radius)
```

**Literature validation:** The composite decay rate λ = 0.003 m⁻¹ is validated by Boogaard et al. (2019), a meta-analysis that found λ = 0.0026 for black carbon and λ = 0.0027 for NOx. The use of road classification as an AADT proxy is standard practice in Land-Use Regression (LUR) epidemiological models (Hoek et al., 2008).

### Tree Canopy Mitigation

Tree canopy fraction is measured from ESA WorldCover (class 10 = tree cover):

```
f = min(α × CC, 0.80)
P_net = P_raw × (1 - f)
```

Where:
- `CC` = tree canopy fraction (tree pixels / valid pixels within the buffer)
- `α` = 0.56 (derived from 2.8% PM2.5 reduction per 5% canopy increase)
- Maximum mitigation capped at 80%
- Source: Nowak et al. (2014) meta-analysis of urban vegetation air quality effects

### Grid Computation

The county-wide pollution grid is built in WGS84 coordinates:

1. Grid extent is derived from the road data bounds plus 0.01° (~1 km) padding.
2. Grid dimensions are computed by measuring the bounding box extent in UTM: `nx = round(width_m / resolution)`, `ny = round(height_m / resolution)`.
3. Cell centers are evenly spaced in WGS84. Default resolution is 100 m.
4. For each cell, the WGS84 center is transformed to UTM on-the-fly for KD-tree distance queries.
5. Raw pollution: `Σ W_i × exp(-λ × d_i)` for all sub-segments within 1000 m.
6. Net pollution: raw value multiplied by `(1 - mitigation)`, where mitigation is computed from the tree canopy fraction in a square window of `resolution` meters around each cell center.

### Normalization and Ranking

Per-school scores are normalized to a 0-100 scale by dividing by the maximum value and multiplying by 100. Ranks are assigned with 1 = highest pollution (descending order, `method="min"`).

---

## Technical Methodology — Flood Plain Analysis

Source: `src/flood_map.py`

### FEMA NFHL Query

Flood hazard polygons are downloaded from the FEMA National Flood Hazard Layer REST API, layer 28 (`S_FLD_HAZ_AR`):

- **Endpoint:** `https://hazards.fema.gov/arcgis/rest/services/public/NFHL/MapServer/28/query`
- **Spatial filter:** `esriSpatialRelIntersects` with `esriGeometryEnvelope`
- **Output fields:** `FLD_ZONE`, `ZONE_SUBTY`, `SFHA_TF`
- **Pagination:** 1000 features per request (`resultRecordCount` / `resultOffset`)

### Tiling Strategy

The FEMA server errors on large bounding boxes. The query area (school properties bounds + 0.01° buffer) is subdivided into a 3x3 grid of sub-bounding boxes. Each tile is queried independently and results are merged, with duplicate features removed via `drop_duplicates(subset=["geometry"])`.

### Geometry Conversion

Esri JSON ring arrays are converted to Shapely Polygon objects:
- Single-ring features become `Polygon(rings[0])`
- Multi-ring features become `Polygon(rings[0], rings[1:])` (first ring = exterior, subsequent = holes)

### Flood Zone Classification

- **100-year (1% annual chance):** Features where `FLD_ZONE` is in `["A", "AE", "AO", "AH"]`
- **500-year (0.2% annual chance):** Features where `ZONE_SUBTY` contains `"0.2 PCT"` (case-insensitive)

### School Property Matching

Each school's parcel is identified by spatial containment: the NCES coordinate point is tested against all Orange County parcels (`combined_data_polys.gpkg`). If no parcel contains the point, the nearest parcel by centroid distance is used as a fallback. This yields one parcel per school.

### Overlap Computation

1. Invalid flood geometries are repaired with `make_valid()`.
2. School properties and flood unions are reprojected to UTM 17N (EPSG:32617) for accurate area computation.
3. All 100-year features are merged into a single geometry via `unary_union()`, and likewise for 500-year.
4. For each school property, `school_geom.intersection(flood_union)` produces the overlap polygon in UTM.
5. Overlap area is computed directly in square meters from the UTM geometry:

```
acres = intersection_area_m² / 4046.86
overlap_pct = overlap_acres / CALC_ACRES × 100
```

6. Intersection geometries are reprojected back to WGS84 for storage and display.

---

## Technical Methodology — Tree Canopy

Source: `src/road_pollution.py` (download and per-school analysis), `src/environmental_map.py` (map layer)

### Data Acquisition

ESA WorldCover V2 2021 is downloaded via the Planetary Computer STAC API:

1. The `esa-worldcover` collection is searched for 2021 tiles intersecting the Orange County bounding box.
2. Each tile is read with a window clipped to the county extent.
3. Tiles are merged via `rasterio.merge.merge()`.
4. The merged raster is reprojected from its source CRS to EPSG:32617 (UTM 17N) using nearest-neighbor resampling (appropriate for categorical data).
5. The result is saved as a compressed GeoTIFF at `data/cache/esa_worldcover_orange_county.tif`.

### Per-School Canopy Fraction

For each school at each analysis radius (500 m and 1000 m):

1. The school's WGS84 coordinate is transformed to the raster's native CRS.
2. A square window of `±radius` meters is read from the raster.
3. `canopy_fraction = tree_pixels / valid_pixels`, where tree pixels are those with class code 10, and valid pixels are those with any class code > 0.

### Map Layer

The tree canopy map overlay reads the full raster, downsampled by `step = max(1, max(height, width) // 2000)` for performance. Pixels with class 10 are rendered as forest green (`#228b22`, RGBA [34, 139, 34, 160]); all other pixels are transparent. The overlay is clipped to the district boundary (passed as `district_gdf` parameter) using the same boolean mask as the TRAP and UHI raster layers, preventing tree canopy data from extending beyond the district boundary.

---

## Technical Methodology — UHI Proxy

Source: `src/environmental_map.py`

### What It Is

The UHI (Urban Heat Island) proxy is a land-cover-based thermal composition index. It estimates relative heat vulnerability by classifying each pixel's land cover type and assigning a thermal weight. **It is NOT measured surface temperature** — no Landsat or MODIS thermal data is used.

### ESA WorldCover Class Weights

Each ESA WorldCover land cover class is assigned a thermal contribution weight. Positive values indicate heat contributors; negative values indicate cooling effects.

| Class Code | Land Cover | Weight | Rationale |
|-----------|-----------|--------|-----------|
| 10 | Tree cover | -0.60 | Cooling via evapotranspiration + shading |
| 20 | Shrubland | -0.30 | Partial cooling |
| 30 | Herbaceous vegetation | -0.10 | Minimal cooling |
| 40 | Cropland | -0.05 | Minimal cooling |
| 50 | Built-up | +1.00 | Reference heating class (impervious surfaces) |
| 60 | Bare/sparse vegetation | +0.40 | Heat absorption |
| 80 | Permanent water bodies | -0.50 | Thermal buffering |
| 90 | Herbaceous wetland | -0.40 | Cooling |
| 95 | Mangroves / woody wetland | -0.40 | Cooling |

**These weights are author-assigned estimates informed by the general principles described in Oke (1982) and the Local Climate Zone framework of Stewart & Oke (2012). They are NOT directly measured thermal coefficients from those papers.** The literature provides the energetic basis (impervious surfaces store and re-emit heat; vegetation cools via evapotranspiration; water bodies buffer temperatures) but does not prescribe these specific numeric weights.

### Grid Computation

The UHI grid uses the same spatial extent and resolution as the TRAP grid:

1. Grid extent is derived from the TRAP grid bounding box (road data bounds + 0.01° padding).
2. Grid dimensions: `nx = round(width_m / resolution)`, `ny = round(height_m / resolution)`, default 100 m.
3. Cell centers are evenly spaced in WGS84.
4. For each cell center:
   - Transform from WGS84 to the raster's native CRS.
   - Read a square window of `±resolution` meters in each direction (i.e., a 2×resolution wide square — 200 m × 200 m at default 100 m resolution, covering ~20×20 = 400 underlying 10 m pixels).
   - Compute the area-weighted UHI index:
     ```
     raw_UHI = Σ(weight_class × count_class) / valid_count
     ```
   - Normalize to 0-100:
     ```
     UHI_norm = (raw_UHI - (-0.60)) / (1.00 - (-0.60)) × 100
     ```
     Where -0.60 is the minimum possible (100% tree cover) and +1.00 is the maximum possible (100% built-up).
   - Clamp to [0, 100].
5. NaN values (cells outside LULC coverage) are replaced with 0.

### Per-School Scores

School UHI scores follow the same windowed-raster pattern at 500 m and 1000 m radii:

1. Transform the school's WGS84 coordinate to the raster CRS.
2. Read a square window of `±radius` meters.
3. Compute `raw_UHI` and normalize identically to the grid.
4. Rankings: 1 = hottest (most built-up surroundings), ascending=False with `method="min"`.

---

## Technical Methodology — Consolidated Map

Source: `src/environmental_map.py`

### Map Assembly

The map is built with Folium using a CartoDB Positron basemap, centered at [35.9132, -79.0558]. The following layers are added (bottom to top):

0. **District Boundary** (always on) — dashed outline (`#333333`, weight 2, dashArray "5,5") matching the socioeconomic map style
1. **School Properties** (always on) — parcel polygons with rich popups aggregating metrics from all analyses
2. **Road Network (tertiary+)** (always on) — vector PolyLines for motorway through tertiary classes only, clipped to district boundary with 2 km buffer via `gpd.clip()`
3. **FEMA Flood Plains** (off by default) — 100-year and 500-year zones as individual GeoJSON features, plus red overlap polygons
4. **Raw Air Pollution** (on by default) — raster overlay from TRAP grid, clipped to district boundary
5. **Tree Canopy** (off by default) — raster overlay from ESA WorldCover, clipped to district boundary
6. **Net Air Pollution** (off by default) — raster overlay from mitigated TRAP grid, clipped to district boundary
7. **UHI Proxy** (off by default) — raster overlay from UHI grid, clipped to district boundary; labeled "Urban Heat Island (Index)" in the bar charts section
8. **Schools** (always on) — fixed-blue CircleMarkers (radius 6, `#2196F3`) matching the socioeconomic map style

Each raster layer also has an associated set of metric-colored school CircleMarkers (radius 6) as a separate toggleable overlay.

### Raster Overlay Pipeline

1. A district boundary mask is precomputed: grid cells inside the district polygon (buffered by 200 m in UTM for edge smoothing) are marked True. Cells outside the mask are zeroed out before rendering, preventing data from extending beyond the district boundary.
2. The numpy grid is converted to an RGBA image array using matplotlib perceptually-uniform colormaps (vectorized, no per-pixel loops):
   - TRAP grids use `YlOrRd` (yellow-orange-red), normalized by the 5th–95th percentile of nonzero values
   - UHI grid uses `RdYlBu_r` (blue-yellow-red diverging), normalized to the fixed 0–100 scale
   - Tree canopy uses forest green with transparency proportional to coverage, also clipped to district boundary
3. The RGBA array is converted to a PNG via PIL, then base64-encoded.
4. A Folium `ImageOverlay` is created with bounds matching the grid's WGS84 extent.

### Road Network Rendering

Only tertiary-class and above roads are rendered as vector PolyLines to keep file size manageable. Roads are clipped to the district boundary polygon buffered by 2 km in UTM, using `gpd.clip()` with proper CRS handling. Each road class has a distinct color and line width:

| Class | Color | Width |
|-------|-------|-------|
| motorway | `#e41a1c` | 4 |
| motorway_link | `#e41a1c` | 3 |
| trunk | `#ff7f00` | 3.5 |
| trunk_link | `#ff7f00` | 2.5 |
| primary | `#377eb8` | 3 |
| primary_link | `#377eb8` | 2 |
| secondary | `#4daf4a` | 2.5 |
| secondary_link | `#4daf4a` | 1.5 |
| tertiary | `#984ea3` | 2 |
| tertiary_link | `#984ea3` | 1.5 |

### Flood Zone Rendering

Flood geometries are simplified with a tolerance of 0.0001° before rendering as individual GeoJSON features (not a single merged GeoJSON, to support per-feature tooltips). Colors:

| Zone | Fill | Edge | Opacity |
|------|------|------|---------|
| 500-year | `#bdd7e7` | `#6baed6` | 0.25 |
| 100-year | `#6baed6` | `#2171b5` | 0.40 |
| School overlap | `#e6031b` | `#e6031b` | 0.60 |

### School Property Popups

Each school property polygon has a popup that aggregates all available metrics:
- Property acreage (from `CALC_ACRES`)
- Flood overlap (type, acres, percentage) or "None"
- TRAP raw index at 500 m with rank
- TRAP net index at 500 m
- Tree canopy fraction at 500 m
- UHI proxy score at 500 m with rank

### School Comparison Bar Charts

Below the map, a 2×2 grid of horizontal bar charts (rendered with Chart.js) compares all 11 schools side-by-side across four metrics at the 500 m radius:

| Chart | Metric | Source column |
|-------|--------|---------------|
| Raw Air Pollution | TRAP raw index (0–100 normalized) | `road_pollution_scores.csv` |
| Net Air Pollution | TRAP net index after tree mitigation (0–100 normalized) | `road_pollution_scores.csv` |
| Urban Heat Island (Index) | UHI proxy score (0–100 normalized) | `uhi_proxy_scores.csv` |
| Flood Zone % | Percentage of school parcel in a FEMA flood zone | school property popup data |

Schools are sorted by value (highest first) within each chart. Bar colors use the same perceptually-uniform color scales as the map raster layers (YlOrRd for TRAP, RdYlBu_r for UHI, blue for flood). Charts are embedded as static HTML/JS in the output file and require no server — Chart.js is loaded from a CDN.

### Dynamic Legends

A JavaScript block listens for Leaflet `overlayadd` and `overlayremove` events and shows/hides the appropriate legend panel. Each legend is a fixed-position HTML div in the bottom-left corner. The TRAP and UHI legends include gradient bars; the flood legend uses color swatches.

### Grid Caching

Both the TRAP grids and UHI grid are cached as compressed numpy archives (`.npz` format):

| Cache File | Contents |
|-----------|----------|
| `data/cache/trap_grids.npz` | `raw_grid`, `net_grid`, `bounds` arrays |
| `data/cache/uhi_grid.npz` | `uhi_grid`, `bounds` arrays |

Cached grids are loaded on subsequent runs to skip the computationally expensive grid generation. **Changes to input data (road network, AADT stations, ESA WorldCover) require manual deletion of cache files to regenerate grids.**

---

## Coordinate Reference Systems

| CRS | EPSG | Usage |
|-----|------|-------|
| WGS84 | 4326 | Storage, map display, grid cell centers, Folium overlays |
| UTM 17N | 32617 | Area computations, distance calculations, KD-tree queries, buffering, ESA WorldCover native CRS |

On-the-fly reprojection is performed via `pyproj.Transformer`. The grid lives in WGS84; each cell center is transformed to UTM for distance computation against the road sub-segment KD-tree. The ESA WorldCover raster is stored in UTM 17N after reprojection from its source CRS; windowed reads operate in raster-native coordinates.

Flood overlap areas are computed entirely in UTM (EPSG:32617), eliminating the previous approximate latitude-factor conversion. District boundary masking for raster layers is performed by buffering the district polygon in UTM, then reprojecting back to WGS84 for grid cell containment tests.

See [`docs/GEOSPATIAL_ANALYSIS_GUIDELINES.md`](GEOSPATIAL_ANALYSIS_GUIDELINES.md) for the full CRS discipline rules.

---

## Limitations

### TRAP Exposure Index

1. **Partial AADT coverage.** Only ~1.2% of road segments use measured NCDOT traffic counts. The remaining 98.8% use road-class proxy weights. AADT stations are concentrated on major and secondary roads; residential and service roads rely entirely on proxy estimates.

2. **ESA WorldCover urban canopy limitation.** The ESA WorldCover 10 m land cover classifies each pixel into a single dominant class. In suburban areas like Chapel Hill, neighborhoods with scattered trees along streets and in yards are classified as "Built-up" (class 50) rather than "Tree cover" (class 10). This means tree canopy mitigation is significantly underestimated for urban and suburban schools. **The raw pollution index (without mitigation) is the more reliable metric for comparing schools.**

3. **Linear summation assumption.** The model sums pollution contributions from all road segments (`P = Σ P_i`), treating pollution as perfectly additive. Real atmospheric chemistry is more complex (e.g., ozone titration by NO near sources), but for a comparative index this is a reasonable first-order approximation.

4. **No meteorological or terrain effects.** Wind patterns, valley channeling, building canyons, and atmospheric stability are not modeled. These factors significantly influence actual pollutant dispersion.

5. **No temporal variation.** The index does not capture rush-hour peaks, seasonal variation, or weekend vs. weekday differences.

6. **Composite decay rate simplification.** The exponential decay rate λ = 0.003 m⁻¹ is a composite for multiple pollutants (NOx, black carbon, ultrafine particles), each of which decays at a different rate. Boogaard et al. (2019) found λ = 0.0026 for BC and λ = 0.0027 for NOx.

7. **Service road weight uncertainty.** OSM `highway=service` covers a broad category (parking-lot access, alleys, driveways). The weights assigned (0.002-0.005) are low-confidence estimates. These roads individually contribute little, but schools near commercial areas have many of them, producing a non-trivial cumulative effect.

### Flood Plain Analysis

8. **Single parcel per school.** Each school is matched to a single containing parcel. Multi-parcel campuses (e.g., schools with separate athletic fields or annexes) may be incompletely represented.

9. **No validation of CALC_ACRES.** The overlap percentage uses the parcel's `CALC_ACRES` field from Orange County tax records. This value is not validated against the computed geometry area.

10. **Static FEMA data.** FEMA flood maps may not reflect recent development, stormwater infrastructure changes, or climate-change-driven shifts in flood frequency.

### Tree Canopy

11. **Single-class pixel limitation.** ESA WorldCover classifies each 10 m pixel as a single dominant land cover class. Mixed pixels (e.g., a suburban lot with one large tree and a house) default to the dominant class, typically "Built-up," understating tree presence.

12. **2021 vintage.** The land cover data is from 2021. Tree canopy may have changed due to development, storms, disease, or planting.

13. **No species, height, or health data.** The analysis does not distinguish tree species, canopy height, density, or health — all of which affect air quality mitigation and thermal cooling effectiveness.

### UHI Proxy

14. **Land-cover proxy, NOT measured surface temperature.** No Landsat thermal band, MODIS land surface temperature, or ground-station temperature data is used. The index reflects land cover composition, not thermal measurements.

15. **Same 10 m pixel limitation as tree canopy.** Suburban scattered trees are classified as "Built-up" at 10 m resolution, biasing the UHI proxy toward higher (hotter) values in suburban areas.

16. **Thermal weights are author-assigned estimates.** The numeric weights (e.g., tree cover = -0.60, built-up = +1.00) are informed by the general principles in Oke (1982) and Stewart & Oke (2012), but they are not directly measured thermal coefficients from those papers. Different weight assignments would produce different rankings.

17. **No building height, albedo, anthropogenic heat, or wind data.** The UHI proxy considers only horizontal land cover, not vertical structure, surface reflectivity, waste heat from HVAC systems, or ventilation corridors.

18. **Static snapshot.** The proxy uses 2021 land cover and does not capture seasonal UHI variation (stronger in summer), diurnal cycles (nighttime UHI often differs from daytime), or year-to-year land cover changes.

19. **Water body cooling overestimation.** Small water features below 10 m resolution (ponds, streams) may not appear in the raster, while larger features may have their cooling effect overrepresented at the grid cell scale.

### Cross-Cutting

20. **All indices are comparative/relative screening tools**, not absolute risk assessments. They should not be interpreted as pollutant concentrations, flood probabilities for specific structures, or temperature predictions.

21. **Square buffer windows for raster queries.** School buffer analyses use square windows (`±radius` meters) rather than circular buffers. This simplifies raster I/O but includes corner areas outside the true circular radius.

22. **Grid caching requires manual invalidation.** Cached `.npz` grids are loaded without checking whether input data has changed. Updates to road networks, AADT stations, or land cover require manual deletion of `data/cache/trap_grids.npz` and `data/cache/uhi_grid.npz` before regeneration.

23. **District boundary raster clipping uses a 200 m buffer.** Raster layers are masked to the district polygon buffered by 200 m in UTM. This prevents hard edges at the district boundary but means a thin band of data outside the official boundary is still visible.

---

## Output Files

| File | Description |
|------|-------------|
| `assets/maps/chccs_environmental_analysis.html` | Interactive consolidated map with all 7 toggleable layers |
| `data/processed/uhi_proxy_scores.csv` | Per-school UHI proxy scores at 500 m and 1000 m with ranks |
| `data/processed/road_pollution_scores.csv` | Per-school TRAP scores (raw, net, canopy) at 500 m and 1000 m |
| `data/cache/trap_grids.npz` | Cached TRAP raw and net grid arrays with bounds |
| `data/cache/uhi_grid.npz` | Cached UHI proxy grid array with bounds |
| `data/cache/fema_flood_zones.gpkg` | Cached FEMA flood zone polygons |
| `data/cache/esa_worldcover_orange_county.tif` | Cached ESA WorldCover raster (UTM 17N) |
| `assets/maps/flood_school_properties.png` | Standalone flood map (from `flood_map.py`) |

---

## Sources

### TRAP Methodology & Data
- Karner, A. A., Eisinger, D. S., & Niemeier, D. A. (2010). Near-roadway air quality: Synthesizing the findings from real-world data. *Environ Sci Technol*, 44(14). DOI: 10.1021/es100008x
- Health Effects Institute. (2010). Traffic-Related Air Pollution: A Critical Review of the Literature on Emissions, Exposure, and Health Effects. HEI Special Report 17.
- Boogaard, H., et al. (2019). Concentration decay rates for near-road air pollutants. *Int J Hyg Environ Health*, 222(7). [λ = 0.0026 BC, 0.0027 NOx]
- Hoek, G., et al. (2008). Land-use regression models for intraurban air pollution. *Atmos Environ*, 42(33). [Road-class-as-AADT-proxy precedent]
- OpenStreetMap contributors. Road network data.
- NCDOT. Annual Average Daily Traffic Stations — Orange County, NC. ArcGIS Feature Service.

### Tree Canopy
- Nowak, D. J., et al. (2014). Tree and forest effects on air quality and human health in the United States. *Environ Pollution*, 193. [α = 0.56 derivation]
- ESA WorldCover V2 2021. 10 m global land cover. Accessed via Microsoft Planetary Computer STAC API.

### Flood Plain Analysis
- FEMA National Flood Hazard Layer (NFHL). Layer 28 (S_FLD_HAZ_AR). ArcGIS REST API.
- Orange County, NC. Parcel boundary data (`combined_data_polys.gpkg`).

### UHI Proxy
- Oke, T. R. (1982). The energetic basis of the urban heat island. *Quarterly Journal of the Royal Meteorological Society*, 108(455), 1-24. [Energetic framework for urban heating]
- Stewart, I. D. & Oke, T. R. (2012). Local Climate Zones for urban temperature studies. *Bulletin of the American Meteorological Society*, 93(12), 1879-1900. [Land-cover-based thermal classification framework]
- ESA WorldCover V2 2021 (as above).

### School Locations
- National Center for Education Statistics (NCES). EDGE Public School Locations 2023-24. LEAID 3700720.

---

*Analysis generated by `src/environmental_map.py`, `src/road_pollution.py`, and `src/flood_map.py`*
