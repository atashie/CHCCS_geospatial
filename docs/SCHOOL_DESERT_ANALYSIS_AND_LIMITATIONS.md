# School Desert Analysis: Methodology and Limitations

## Overview

The school desert analysis (`src/school_desert.py`) quantifies how school closures affect travel times across the Chapel Hill-Carrboro City Schools (CHCCS) district. For every 100-meter grid cell in the district, it computes the minimum travel time to the nearest open elementary school under seven scenarios: baseline (all 11 schools open) and six closure scenarios. The output is an interactive heatmap (`assets/maps/school_community_map.html`) with scenario/mode switching and hover tooltips.

---

## Pipeline Steps

### Step 1: Load School Locations

**Source:** NCES EDGE Public School Locations 2023-24 (LEAID 3700720), cached at `data/cache/nces_school_locations.csv`.

Loads the 11 CHCCS elementary schools with their official NCES coordinates (lat/lon in WGS84). These coordinates represent the school building locations as reported by the National Center for Education Statistics.

### Step 2: Load District Boundary

**Source:** U.S. Census Bureau TIGER/Line Unified School District boundaries (2023), GEOID `3700720`.

Downloads the official CHCCS district polygon from Census TIGER/Line shapefiles and caches it as a GeoPackage (`data/cache/chccs_district_boundary.gpkg`). If the download fails, a fallback boundary is created from the convex hull of all 11 school locations with a 3 km buffer — this is less accurate but ensures the analysis can still run.

### Step 3: Download Road Networks

**Source:** OpenStreetMap via the OSMnx library.

Three separate road network graphs are downloaded, one per travel mode:

| Mode | OSMnx network_type | Description |
|------|-------------------|-------------|
| Drive | `drive_service` | Roads accessible to motor vehicles, including service roads. All edges are made bidirectional (reverse edges added where missing). |
| Bike | `bike` | Roads and paths accessible to cyclists (falls back to `all` if the bike-specific network fails) |
| Walk | `walk` | All pedestrian-accessible paths including sidewalks, trails, footways |

Each network is downloaded for the district polygon plus a 500-meter buffer (in UTM) to capture roads that cross the district boundary. Networks are cached as GraphML files in `data/cache/`.

**Bidirectional edges:** After loading or downloading each network, `_ensure_bidirectional()` adds a reverse edge for every edge that lacks one. This makes all three networks (drive, bike, walk) fully traversable in both directions. Because the graph is symmetric, Dijkstra run outward from a school gives the same travel time as a resident traveling inward toward the school — no graph reversal is needed.

**Edge weights (travel_time in seconds)** are computed per edge from `edge length / speed`:

- **Walk:** 2.5 mph (1.12 m/s) — mid-range for K-5 children. Based on MUTCD Section 4E.06 design speed of 3.5 ft/s and Fitzpatrick et al. (2006, FHWA-HRT-06-042) measurements of 3.7–4.2 ft/s for school-age children.
- **Bike:** 12 mph (5.36 m/s) — flat constant.
- **Drive:** Decomposed into two components:
  1. **Free-flow friction speed** — mid-block travel speed by OSM `highway` tag, accounting for acceleration/deceleration cycles and roadway friction but excluding intersection control delays.
  2. **Intersection penalties** — explicit per-node delays at traffic signals, stop signs, yield signs, and pedestrian crossings, based on the destination node's OSM `highway` tag.

  Intersection control tags are supplemented from the Overpass API to fill gaps left by OSMnx graph simplification (which drops intermediate nodes that may carry stop/signal tags).

  **Free-flow friction speeds:**

| Road type | Posted (mph) | Friction (mph) | Ratio |
|-----------|-------------|----------------|-------|
| Motorway | 65 | 62 | 95% |
| Trunk | 55 | 45 | 82% |
| Primary | 45 | 36 | 80% |
| Secondary | 35 | 29 | 83% |
| Tertiary | 30 | 25 | 83% |
| Residential | 25 | 21 | 84% |
| Living street | 15 | 12 | 80% |
| Service | 15 | 12 | 80% |

  Edges with unrecognized highway types default to 21 mph.

  **Intersection penalties (seconds):**

| Node tag | Penalty | Source |
|----------|---------|--------|
| traffic_signals | 15 s | HCM6 Ch.19, LOS C average cycle delay |
| stop | 7 s | HCM6 Ch.20, decelerate + stop + gap acceptance + accelerate |
| give_way | 4 s | Yield — slow but not full stop |
| crossing | 2 s | Pedestrian crossing — minor yield/awareness for drivers |
| turning_circle | 3 s | Cul-de-sac turnaround |

  For each edge (u, v): `travel_time = length / friction_speed + penalty(v)`.

### Step 4: Compute School-Outward Travel Times (Dijkstra)

For each school, Dijkstra's single-source shortest-path algorithm is run outward across the entire network graph with no distance cutoff. This produces a lookup table of `{node_id: travel_time_seconds}` for every reachable node.

Because all three networks are made bidirectional (reverse edges added where missing), Dijkstra from a school outward gives the same travel times as a resident traveling inward toward the school. No graph reversal is needed for any mode.

This yields 33 Dijkstra runs total (11 schools × 3 modes). Travel times are cached in memory — they do not change across scenarios.

### Step 5: Create Analysis Grid

A regular point grid is generated directly in WGS84 (EPSG:4326) at 100-meter spacing, using latitude-corrected degree intervals (`dlat = 100m / 111320`, `dlon = 100m / (111320 × cos(center_lat))`). Only points inside the district polygon (via `shapely.prepared.prep().contains()`) are retained. This WGS84-native approach matches `road_pollution.py` and `environmental_map.py`, eliminating the convergence-angle rotation that occurs when creating a UTM grid and reprojecting to WGS84.

After the grid is created, the 11 school locations are injected as extra grid points (anchor points). This ensures each school's pixel receives a near-zero travel time in the baseline scenario, preventing schools from appearing as high-travel-time artifacts due to grid misalignment.

This produces approximately 16,175 grid points (16,164 regular + 11 school anchors) covering the district interior.

### Step 6: Compute Desert Scores

For each travel mode, grid points are snapped to the road network using **edge-snapping** (not node-snapping), then travel times are computed for each scenario:

1. **Edge index construction:** A Shapely `STRtree` spatial index is built over deduplicated edge geometries (LineStrings). Longitudes are scaled by `cos(latitude)` so Euclidean distances approximate true metric distances in WGS84.
2. **Nearest-edge query:** Each grid point is matched to the nearest road edge via batch `STRtree.nearest()`.
3. **Access distance:** The perpendicular distance from grid point to matched edge is computed (vectorized via `shapely.distance`). Points more than 200 m (2 grid cells) from any edge are marked unreachable (NaN) — these represent lakes, large parks, and other off-network areas.
4. **Fractional position:** The position along the matched edge is computed via `shapely.line_locate_point(normalized=True)`, yielding `f ∈ [0, 1]` where `f = 0` is the edge's start node and `f = 1` is the end node.
5. **Travel time interpolation:** For each open school in the scenario, travel time is interpolated via both edge endpoints: `via_u = t_u + f × edge_time` and `via_v = t_v + (1−f) × edge_time`, where `t_u` and `t_v` are Dijkstra times from the school to the edge's start and end nodes respectively. The minimum of the two routes is selected.
6. **Access-leg penalty:** An off-network penalty is added: `access_time = perpendicular_distance / (factor × modal_speed)`, where the speed factor is mode-specific. Walk and bike access legs (crossing lawns, parking lots, sidewalks) happen at close to full modal speed; drive access legs (navigating driveways and parking lots) are much slower than road speed:

   | Mode | Factor | Modal speed | Access speed | 50 m penalty |
   |------|--------|-------------|--------------|--------------|
   | Walk | 90% | 1.12 m/s (2.5 mph) | 1.01 m/s (2.3 mph) | 50 s |
   | Bike | 80% | 5.36 m/s (12 mph) | 4.29 m/s (9.6 mph) | 12 s |
   | Drive | 20% | 8.05 m/s (18 mph) | 1.61 m/s (3.6 mph) | 31 s |
7. **Minimum across schools:** The minimum total time (network travel + access penalty) across all open schools is recorded, along with the identity of the nearest school. If no school reaches either endpoint of the matched edge, the travel time is NaN.

**Scenarios evaluated:**

| Scenario | Schools closed | Schools open |
|----------|---------------|-------------|
| Baseline | None | All 11 |
| Close Carrboro | Carrboro Elementary | 10 |
| Close Ephesus | Ephesus Elementary | 10 |
| Close Estes Hills | Estes Hills Elementary | 10 |
| Close FPG | Frank Porter Graham Bilingue | 10 |
| Close Glenwood | Glenwood Elementary | 10 |
| Close McDougle | McDougle Elementary | 10 |
| Close Morris Grove | Morris Grove Elementary | 10 |
| Close Northside | Northside Elementary | 10 |
| Close Rashkis | Rashkis Elementary | 10 |
| Close Scroggs | Scroggs Elementary | 10 |
| Close Seawell | Seawell Elementary | 10 |

### Step 7: Compute Deltas

For each non-baseline scenario, the delta (change from baseline) is computed:

```
delta = closure_scenario_time - baseline_time
```

A positive delta means the closure increased travel time at that grid point. Zero delta means the grid point's nearest school was unaffected by the closure.

### Step 8: Rasterize to Heatmap Images

Grid points (already on a regular WGS84 lat/lon grid) are binned into a pixel grid for image rendering:

1. **Shared grid parameters** are pre-computed once from the full set of unique grid point coordinates and reused across all scenario/mode combinations. This ensures pixel-perfect alignment — every heatmap layer maps to the same pixel grid.
2. **Cell size** is computed from the 100m resolution: `dlat = 100 / 111320` degrees, `dlon = 100 / (111320 × cos(center_lat))` degrees. This matches the grid creation spacing, so points map 1:1 to pixels.
3. Each grid point is assigned to its containing pixel via integer index arithmetic. **Minimum-wins** assignment (`np.minimum.at`) is used so that when multiple grid points (including injected school anchor points) map to the same pixel, the lowest travel time wins.
4. **Safety-net gap fill:** A gap-fill pass is retained as a robustness measure, but with WGS84-native grid points it is a no-op (no rotation gaps exist). NaN pixels from Dijkstra routing failures (where a grid point exists but no school was reachable) are intentionally preserved as transparent.
5. **District boundary masking:** Every pixel center is tested for containment within the CHCCS district polygon. Pixels outside the polygon are set to NaN.
7. **Colorization:** The value raster is mapped to RGBA using matplotlib colormaps. NaN cells become fully transparent (alpha=0); data cells get alpha=210/255. Absolute time layers use `RdYlGn_r` (green=close, red=far). Delta layers use `Oranges` (white=no change, dark orange=large increase).
8. **Encoding:** The RGBA image is saved as a base64 PNG for embedding in the HTML map. The raw float32 values are also base64-encoded for the hover tooltip lookup.

GeoTIFFs are saved to `data/cache/school_desert_tiffs/` for archival.

### Step 9: Interactive Map

A Folium map is generated with:
- CartoDB Positron base tiles
- The district boundary as a dashed polygon overlay
- School locations as circle markers (blue=open, red with ×=closed, per scenario)
- All heatmap layers pre-loaded as Leaflet `L.imageOverlay` objects (toggled via opacity)
- Road network edges rendered as GeoJSON LineStrings per mode (drive/bike/walk), toggled with a "Show road network" checkbox. Only the active mode's network is displayed.
- Radio button controls for scenario, travel mode, and layer type (absolute time vs. delta)
- A gradient color legend showing the active color scale per mode/layer (RdYlGn_r for absolute time, Oranges for delta), with min/max labels that update when switching modes
- A hover tooltip that decodes the base64 float32 grid in JavaScript and reports the value at the cursor position using linear WGS84 coordinate interpolation

---

## Data Sources

| Data | Source | Vintage | Access |
|------|--------|---------|--------|
| School locations | NCES EDGE Public School Locations | 2023-24 | Public download, LEAID 3700720 |
| District boundary | U.S. Census TIGER/Line Unified School Districts | 2023 | Public download, GEOID 3700720 |
| Road networks | OpenStreetMap via OSMnx | Fetched at analysis time | Overpass API |
| Walk speed | MUTCD 4E.06, Fitzpatrick et al. 2006 (FHWA-HRT-06-042) | 2006 | Published research |
| Drive friction speeds | HCM6 Ch.16, FHWA Urban Arterial Speed Studies | 2016 | Published standards |
| Intersection penalties | HCM6 Ch.19 (signalized), Ch.20 (stop-controlled) | 2016 | Published standards |
| Intersection control tags | OpenStreetMap via Overpass API | Fetched at analysis time | Overpass API |

---

## Known Limitations

### 1. Speed Model Simplifications

**Walk speed is a single constant (2.5 mph).** In reality, walking speed varies with:
- Age (kindergartners walk slower than 5th graders)
- Terrain and elevation (Chapel Hill has hills; the model treats all paths as flat)
- Sidewalk availability (the OSM walk network includes paths that exist in the data, but not all streets have sidewalks — a path's presence in OSM does not mean a safe, paved sidewalk exists)
- Weather and season
- Whether the child is accompanied by an adult

**Bike speed is a single constant (12 mph).** No adjustment for hills, road surface, or rider age.

**Drive speeds use a decomposed model (friction speed + intersection penalties) that is still static.** Remaining limitations:
- Time-of-day variation (school drop-off congestion vs. midday)
- Seasonal variation or construction
- Left-turn vs. right-turn penalties (no turn-angle modeling)
- School zone speed reductions (20 mph zones active during arrival/dismissal)
- Stop sign coverage in OSM is incomplete — only a fraction of actual stop signs are tagged; untagged intersections receive no penalty

### 2. Network Completeness and Accuracy

**OpenStreetMap data is community-maintained and may be incomplete.** Specific risks:
- Missing sidewalks, cut-throughs, or pedestrian paths
- Incorrect or outdated one-way street designations
- Missing or incorrect `highway` tags (affecting drive speed assignment)
- New developments or road changes not yet mapped

**The bike network may use a fallback.** If OSMnx fails to download a bike-specific network, the code falls back to `network_type="all"`, which includes roads not suitable for cycling.

**Network edges are simplified by OSMnx.** Intermediate nodes on straight road segments are removed. This reduces computational cost but means the network cannot represent mid-block access points.

### 3. Edge Snapping and Access-Leg Approximation

Grid points are snapped to the **nearest road edge** (not just the nearest node), which dramatically reduces access distance for points along long road segments. Travel time is interpolated along the matched edge using the fractional position. Remaining limitations:
- The **200 m cutoff** still applies — grid points farther than 200 m (2 grid cells) from any road edge are marked as unreachable (NaN). This affects areas like lakes, large parks, and undeveloped land.
- The **access-leg penalty** uses straight-line (perpendicular) distance at a mode-specific fraction of modal speed (walk 90%, bike 80%, drive 20%). The mode-specific factors reflect that pedestrians and cyclists traverse off-road access legs (lawns, parking lots) at near full speed, while drivers must navigate driveways and parking lots at much lower speed. This is still an approximation — real off-road paths may be longer or shorter than the perpendicular distance.
- Two grid points near the same edge segment may receive similar travel times, since they share the same edge's interpolated time. This is less of an issue than node-snapping (where both would get identical times) but still masks some local variation.
- **School self-times are not zero.** Even the injected school anchor points receive non-trivial travel times because (a) the school location may snap to an edge whose endpoints are distant from the school's Dijkstra source node in network terms, and (b) the perpendicular distance to the nearest edge adds an access penalty. Schools set back from the road network (accessed via driveways or paths not in OSM) are most affected. In the walk baseline, school self-times range from ~0.4 min (McDougle) to ~7 min (Ephesus), with most under 1.5 min. This primarily reflects missing pedestrian paths in OpenStreetMap rather than a modeling error.

### 4. Dijkstra Routing Gaps

Some grid points receive NaN travel times because neither endpoint of the matched edge is reachable from any school. This can happen due to disconnected components in the bidirectional graph — rare, but possible for isolated road segments that share no connected nodes with the main network component. Additionally, grid points beyond the 200 m access-distance cutoff are marked as NaN. These NaN gaps are **intentionally preserved** in the heatmap as transparent pixels — they represent real routing failures or unreachable areas, not missing data. On the map, they appear as small holes or transparent patches in the heatmap interior.

### 5. District Boundary Precision

The Census TIGER/Line district boundary is a cartographic generalization. It may not match the actual school attendance boundary to the parcel level. If the TIGER download fails, the fallback boundary (convex hull of school locations + 3 km buffer) is significantly less accurate and will include areas outside the actual district.

### 6. "Nearest School" Is Not "Assigned School"

The analysis computes travel time to the **geographically nearest** school. CHCCS assigns students to schools based on attendance zones, not pure proximity. A student's assigned school may not be the closest one. This analysis shows geographic access patterns, not actual assignment impacts.

### 7. No Capacity Constraints

The model treats all open schools as equally available regardless of capacity. In a real closure scenario, students would be redistributed according to capacity and policy, and some schools might become overcrowded. This analysis does not model redistribution — it only measures raw geographic access.

### 8. Mode-Specific Limitations

**Drive mode** uses a bidirectional network (reverse edges added where missing) so Dijkstra from school outward gives symmetric grid→school times. It assumes the driver follows the shortest-time route. It does not model:
- Parking and walking from a parking lot to the school entrance
- Drop-off line queuing time
- Route choice preferences (parents may avoid certain roads)

**Walk mode** does not account for:
- Road crossing safety (no intersection danger weighting)
- Sidewalk connectivity gaps
- Safe Routes to School infrastructure (or lack thereof)
- Whether a route is suitable for an unaccompanied child
- Schools set back from OSM walk edges (e.g., Ephesus, Rashkis) show inflated self-times because the school-to-road access path is not in the network

**Bike mode** does not account for:
- Whether bike infrastructure exists on the route
- Road safety for child cyclists
- Bike parking availability at schools

### 9. Raster Resolution vs. Display Resolution

The 100-meter grid resolution means each pixel represents a 100m × 100m area. Travel time is assigned to the entire pixel based on a single point at (or near) its center. Sub-pixel variation is lost. The `image-rendering: pixelated` CSS directive preserves crisp pixel boundaries when the map is zoomed in, but this makes the blocky resolution visually obvious.

### 10. Color Scale Clamping

Travel times are mapped to fixed color ranges per mode:
- Drive: 0–15 min (absolute), 0–10 min (delta)
- Bike: 0–30 min (absolute), 0–15 min (delta)
- Walk: 0–60 min (absolute), 0–30 min (delta)

Values exceeding the maximum are clamped to the darkest color. This means a 20-minute drive and a 40-minute drive both appear as the same dark red. The hover tooltip shows the actual value, but the visual impression can be misleading at extremes.

### 11. Static Network Snapshot

The OSM road network is downloaded once and cached. It represents road infrastructure at the time of download. New roads, closed roads, or construction are not reflected unless the cache is cleared and the network re-downloaded.

### 12. No Elevation or Terrain Model

All travel time calculations are based on 2D network distance. Hill gradients, which meaningfully affect walk and bike speeds in Chapel Hill's terrain, are not modeled. Routes through hilly areas will underestimate actual travel times for walk and bike modes.

---

## Output Files

| File | Description |
|------|-------------|
| `assets/maps/school_community_map.html` | Interactive map with all scenarios, modes, and layers |
| `data/processed/school_desert_grid.csv` | Raw data: 339,675 rows (16,175 grid points × 7 scenarios × 3 modes) |
| `data/cache/school_desert_tiffs/*.tif` | GeoTIFF rasters (WGS84) for each scenario/mode/layer combination |
| `data/cache/nces_school_locations.csv` | School coordinates (input) |
| `data/cache/chccs_district_boundary.gpkg` | District polygon (input) |
| `data/cache/network_{drive,bike,walk}.graphml` | Cached road network graphs (input) |

---

## Reproducibility

To regenerate the analysis from scratch:

```bash
# Delete cached networks to force fresh download from OSM
rm data/cache/network_*.graphml

# Run the analysis
python src/school_desert.py
```

School locations and the district boundary are stable (NCES 2023-24 and Census 2023). Road networks may change between OSM downloads. All random seeds are deterministic (Dijkstra is deterministic given the same graph).

---

## See Also

- **[School Closure Impact Analysis](SCHOOL_CLOSURE_ANALYSIS.md)** — Extends this travel-time methodology with traffic redistribution modeling, dasymetric children distribution, and route extraction using `dijkstra_predecessor_and_distance()`.
