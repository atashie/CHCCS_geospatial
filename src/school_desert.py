"""
School Community Analysis for CHCCS Elementary Schools

Quantifies the geographic impact of school closures by computing travel times
(drive, bike, walk) from every point in the school district to the nearest
elementary school under multiple closure scenarios.

Methodology:
- Inverts the problem: runs Dijkstra OUTWARD from each school (33 total runs)
  rather than from every grid point (thousands of runs)
- For closure scenarios, simply excludes removed school(s) and re-takes the
  minimum travel time — no recomputation needed
- Travel speeds: walk 2.5 mph (K-5 children), bike 12 mph,
  drive by road type using effective speeds (10-60 mph)
- Grid resolution: 100m over the entire CHCCS district

Speed model sources:
- Walk: MUTCD Section 4E.06 design walk speed (3.5 ft/s = 2.4 mph).
  Fitzpatrick et al. (2006, FHWA-HRT-06-042) measured 3.7-4.2 ft/s for
  school-age children. We use 2.5 mph (3.67 ft/s) — mid-range for K-5.
- Drive: Posted limits reduced to effective speeds per HCM6 Ch.16 (Urban
  Street Facilities) and FHWA Urban Arterial Speed Studies. Effective/posted
  ratios: ~65% residential, ~71% secondary, ~73% primary/trunk, ~92% motorway.
  Accounts for intersection delays, stop signs, signals, school-hour traffic.
- Edge snapping: grid points snap to nearest edge LineString (not just nodes)
  via Shapely STRtree, with travel time interpolated along the matched edge.
  Longitudes scaled by cos(latitude) for metric-approximate queries in WGS84.

Affected-household analysis:
- Residential parcel centroids (~21K from Orange County tax records) are
  snapped to the nearest grid point via cKDTree with cos(lat) scaling.
- A parcel is "affected" by a closure scenario if its nearest grid point has
  delta_minutes > 0 for that scenario+mode (travel time increased).
- For each scenario × mode, histograms of assessed value and years since last
  sale are pre-rendered as base64 PNGs and embedded in the HTML map.
- The chart panel below the map updates when the user changes scenario or mode.

Data sources:
- Road networks: OpenStreetMap via OSMnx
- School locations: NCES EDGE Public School Locations 2023-24
- District boundary: Census TIGER/Line Unified School Districts 2023
- Residential parcels: Orange County GIS (combined_data_centroids.gpkg)

Outputs:
- assets/maps/school_community_map.html (interactive map with scenario switching
  and affected-household histograms)
- data/processed/school_desert_grid.csv (raw grid travel time data)

Assumptions & limitations:
- Travel time model uses static speeds; no real-time traffic or turn penalties.
- "Affected" is binary (delta > 0); does not weight by magnitude of increase.
- Parcel-to-grid snapping uses straight-line nearest-point, not network distance.
- Assessed values are from the latest Orange County tax records and may lag
  current market values.
- years_since_sale reflects the most recent recorded deed transfer; properties
  with no recorded sale show NaN and are excluded from that histogram.
- Grid points > 200 m from any road edge are marked unreachable (NaN).
- The analysis assumes all remaining schools absorb displaced students with
  no capacity constraints.
"""

import base64
import io
import json
import math
import sys
import warnings
from pathlib import Path

import folium
import geopandas as gpd
import matplotlib
import matplotlib.cm as cm
import matplotlib.colors as mcolors
import matplotlib.pyplot as plt
import networkx as nx
import numpy as np
import osmnx as ox
import pandas as pd
from scipy.spatial import cKDTree
import shapely                      # for points(), STRtree, distance, line_locate_point
from shapely.geometry import LineString, Point

warnings.filterwarnings("ignore", category=FutureWarning)
matplotlib.use("Agg")

# ---------------------------------------------------------------------------
# Paths
# ---------------------------------------------------------------------------
PROJECT_ROOT = Path(__file__).parent.parent
DATA_PROCESSED = PROJECT_ROOT / "data" / "processed"
DATA_CACHE = PROJECT_ROOT / "data" / "cache"
ASSETS_MAPS = PROJECT_ROOT / "assets" / "maps"

SCHOOL_CSV = DATA_CACHE / "nces_school_locations.csv"
DISTRICT_CACHE = DATA_CACHE / "chccs_district_boundary.gpkg"

# ---------------------------------------------------------------------------
# Constants
# ---------------------------------------------------------------------------
# Travel speeds
WALK_SPEED_MPS = 1.12   # 2.5 mph — K-5 children (MUTCD 4E.06 / FHWA-HRT-06-042)
BIKE_SPEED_MPS = 5.36   # 12 mph

# Posted speed limits (kept for documentation only)
DRIVE_POSTED_SPEEDS_MPH = {
    "motorway": 65, "motorway_link": 55,
    "trunk": 55, "trunk_link": 45,
    "primary": 45, "primary_link": 35,
    "secondary": 35, "secondary_link": 30,
    "tertiary": 30, "tertiary_link": 25,
    "residential": 25, "living_street": 15,
    "service": 15, "unclassified": 25,
}

# Effective speeds accounting for signals, stops, and school-hour traffic
# Sources: HCM6 Ch.16 Urban Street Facilities, FHWA Urban Arterial Speed Studies
# Ratios vs posted: ~92% motorway, ~73% trunk/primary, ~71% secondary, ~65% residential
DRIVE_EFFECTIVE_SPEEDS_MPH = {
    "motorway": 60, "motorway_link": 50,
    "trunk": 40, "trunk_link": 35,
    "primary": 30, "primary_link": 25,
    "secondary": 25, "secondary_link": 22,
    "tertiary": 22, "tertiary_link": 18,
    "residential": 18, "living_street": 10,
    "service": 10, "unclassified": 18,
}
DEFAULT_DRIVE_EFFECTIVE_MPH = 18

# Grid
GRID_RESOLUTION_M = 100

# CRS
CRS_WGS84 = "EPSG:4326"
CRS_UTM17N = "EPSG:32617"

# Map center
CHAPEL_HILL_CENTER = [35.9132, -79.0558]

# Color scale ranges (minutes)
MODE_RANGES = {
    "drive": {"abs": (0, 15), "delta": (0, 10)},
    "bike":  {"abs": (0, 30), "delta": (0, 15)},
    "walk":  {"abs": (0, 60), "delta": (0, 30)},
}

# Closure scenarios — one per elementary school for equal treatment
SCENARIOS = {
    "baseline": [],
    "no_carrboro": ["Carrboro Elementary"],
    "no_ephesus": ["Ephesus Elementary"],
    "no_estes": ["Estes Hills Elementary"],
    "no_fpg": ["Frank Porter Graham Bilingue"],
    "no_glenwood": ["Glenwood Elementary"],
    "no_mcdougle": ["McDougle Elementary"],
    "no_morris_grove": ["Morris Grove Elementary"],
    "no_northside": ["Northside Elementary"],
    "no_rashkis": ["Rashkis Elementary"],
    "no_scroggs": ["Scroggs Elementary"],
    "no_seawell": ["Seawell Elementary"],
}

SCENARIO_LABELS = {
    "baseline": "Baseline (All 11 Schools)",
    "no_carrboro": "Close Carrboro",
    "no_ephesus": "Close Ephesus",
    "no_estes": "Close Estes Hills",
    "no_fpg": "Close FPG Bilingue",
    "no_glenwood": "Close Glenwood",
    "no_mcdougle": "Close McDougle",
    "no_morris_grove": "Close Morris Grove",
    "no_northside": "Close Northside",
    "no_rashkis": "Close Rashkis",
    "no_scroggs": "Close Scroggs",
    "no_seawell": "Close Seawell",
}

MODE_LABELS = {"drive": "Drive", "bike": "Bike", "walk": "Walk"}

# Styling
ACCENT_COLOR = "#2c3e50"


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------
def _progress(msg: str):
    print(f"  ... {msg}")


def ensure_directories():
    for d in [DATA_PROCESSED, DATA_CACHE, ASSETS_MAPS]:
        d.mkdir(parents=True, exist_ok=True)


def _build_node_index(G: nx.MultiDiGraph):
    """Build a cKDTree spatial index for graph nodes.

    Scales longitudes by cos(mean_latitude) so Euclidean distance in the
    tree approximates true metric distance. At 35.9°N, cos(lat) ≈ 0.81.

    Returns (node_ids, tree, cos_lat).
    """
    node_ids = list(G.nodes())
    raw_coords = np.array([(G.nodes[n]["x"], G.nodes[n]["y"]) for n in node_ids])
    mean_lat = raw_coords[:, 1].mean()
    cos_lat = np.cos(np.radians(mean_lat))
    scaled_coords = np.column_stack([raw_coords[:, 0] * cos_lat, raw_coords[:, 1]])
    tree = cKDTree(scaled_coords)
    return node_ids, tree, cos_lat


def _nearest_node(node_ids, tree, lon, lat, cos_lat):
    """Find the nearest graph node to a (lon, lat) point."""
    _, idx = tree.query([lon * cos_lat, lat])
    return node_ids[idx]


def _build_edge_index(G: nx.MultiDiGraph) -> dict:
    """Build a Shapely STRtree spatial index over deduplicated edge geometries.

    Returns dict with keys: tree, scaled_geoms, start_nodes, end_nodes,
    edge_times, cos_lat.  ``start_node`` is at fraction 0 of the geometry,
    ``end_node`` at fraction 1.
    """
    # Compute cos(mean_lat) for metric-approximate scaling
    lats = [G.nodes[n]["y"] for n in G.nodes()]
    mean_lat = np.mean(lats)
    cos_lat = np.cos(np.radians(mean_lat))

    seen = set()
    scaled_geoms = []
    start_nodes = []
    end_nodes = []
    edge_times = []

    for u, v, key, data in G.edges(keys=True, data=True):
        canon = (min(u, v), max(u, v), key)
        if canon in seen:
            continue
        seen.add(canon)

        # Get or construct geometry
        if "geometry" in data:
            geom = data["geometry"]
        else:
            u_x, u_y = G.nodes[u]["x"], G.nodes[u]["y"]
            v_x, v_y = G.nodes[v]["x"], G.nodes[v]["y"]
            geom = LineString([(u_x, u_y), (v_x, v_y)])

        # Detect which node is at the start of the geometry
        g0 = geom.coords[0]
        u_x, u_y = G.nodes[u]["x"], G.nodes[u]["y"]
        if abs(g0[0] - u_x) + abs(g0[1] - u_y) < 1e-8:
            s_node, e_node = u, v          # geom start ≈ node u
        else:
            s_node, e_node = v, u          # geom start ≈ node v

        # Scale geometry for metric-approximate nearest-neighbor
        scaled = shapely.transform(geom, lambda c: c * [[cos_lat, 1]])

        scaled_geoms.append(scaled)
        start_nodes.append(s_node)
        end_nodes.append(e_node)
        edge_times.append(data.get("travel_time", 0.0))

    tree = shapely.STRtree(scaled_geoms)
    return {
        "tree": tree,
        "scaled_geoms": scaled_geoms,
        "start_nodes": np.array(start_nodes),
        "end_nodes": np.array(end_nodes),
        "edge_times": np.array(edge_times, dtype=np.float64),
        "cos_lat": cos_lat,
    }


def _ensure_bidirectional(G: nx.MultiDiGraph) -> nx.MultiDiGraph:
    """Add reverse edges where missing, so all roads are traversable both ways."""
    edges_to_add = []
    for u, v, key, data in G.edges(keys=True, data=True):
        if not G.has_edge(v, u):
            edges_to_add.append((v, u, data.copy()))
    for v, u, data in edges_to_add:
        G.add_edge(v, u, **data)
    return G


def _graph_to_geojson(G: nx.MultiDiGraph) -> dict:
    """Convert graph edges to a GeoJSON FeatureCollection of LineStrings.

    Uses actual edge ``geometry`` attribute (curved road shapes from OSMnx
    simplification) when available; falls back to straight line between
    endpoint nodes.  Deduplicates bidirectional edges (keeps one direction
    per node pair).  Rounds coordinates to 4 decimal places (~11 m precision)
    to reduce file size.
    """
    seen = set()
    features = []
    for u, v, data in G.edges(data=True):
        edge_key = (min(u, v), max(u, v))
        if edge_key in seen:
            continue
        seen.add(edge_key)

        if "geometry" in data:
            coords = [[round(c[0], 4), round(c[1], 4)]
                      for c in data["geometry"].coords]
        else:
            u_x, u_y = round(G.nodes[u]["x"], 4), round(G.nodes[u]["y"], 4)
            v_x, v_y = round(G.nodes[v]["x"], 4), round(G.nodes[v]["y"], 4)
            coords = [[u_x, u_y], [v_x, v_y]]

        features.append({
            "type": "Feature",
            "geometry": {"type": "LineString", "coordinates": coords},
            "properties": {},
        })

    return {"type": "FeatureCollection", "features": features}


# ---------------------------------------------------------------------------
# 1. Load school locations
# ---------------------------------------------------------------------------
def load_schools() -> gpd.GeoDataFrame:
    """Load NCES school locations from cache."""
    if not SCHOOL_CSV.exists():
        raise FileNotFoundError(
            f"School locations not found at {SCHOOL_CSV}. "
            "Run road_pollution.py first to download them."
        )
    df = pd.read_csv(SCHOOL_CSV)
    gdf = gpd.GeoDataFrame(
        df, geometry=gpd.points_from_xy(df.lon, df.lat), crs=CRS_WGS84
    )
    _progress(f"Loaded {len(gdf)} schools from {SCHOOL_CSV}")
    return gdf


# ---------------------------------------------------------------------------
# 2. Download district boundary
# ---------------------------------------------------------------------------
def download_district_boundary(schools: gpd.GeoDataFrame) -> gpd.GeoDataFrame:
    """Download CHCCS unified school district polygon from Census TIGER/Line.

    Uses GEOID 3700720 (Chapel Hill-Carrboro City Schools).
    Falls back to convex hull around schools with buffer if download fails.
    """
    if DISTRICT_CACHE.exists():
        _progress(f"Loading cached district boundary from {DISTRICT_CACHE}")
        return gpd.read_file(DISTRICT_CACHE)

    _progress("Downloading CHCCS district boundary from Census TIGER/Line ...")
    import requests
    import zipfile
    import tempfile

    url = "https://www2.census.gov/geo/tiger/TIGER2023/UNSD/tl_2023_37_unsd.zip"
    try:
        resp = requests.get(url, timeout=120)
        resp.raise_for_status()

        with tempfile.TemporaryDirectory() as tmpdir:
            zip_path = Path(tmpdir) / "unsd.zip"
            zip_path.write_bytes(resp.content)

            with zipfile.ZipFile(zip_path, "r") as zf:
                zf.extractall(tmpdir)

            # Find the shapefile
            shp_files = list(Path(tmpdir).glob("*.shp"))
            if not shp_files:
                raise FileNotFoundError("No .shp found in TIGER/Line zip")

            gdf = gpd.read_file(shp_files[0])
            district = gdf[gdf["GEOID"] == "3700720"].copy()

            if len(district) == 0:
                raise ValueError("GEOID 3700720 not found in TIGER/Line data")

            district = district.to_crs(CRS_WGS84)
            district.to_file(DISTRICT_CACHE, driver="GPKG")
            _progress(f"Cached district boundary to {DISTRICT_CACHE}")
            return district

    except Exception as e:
        _progress(f"WARNING: District download failed ({e}), using convex hull fallback")
        return _fallback_district_boundary(schools)


def _fallback_district_boundary(schools: gpd.GeoDataFrame) -> gpd.GeoDataFrame:
    """Create fallback district boundary from convex hull of schools + 3km buffer."""
    schools_utm = schools.to_crs(CRS_UTM17N)
    hull = schools_utm.union_all().convex_hull
    buffered = hull.buffer(3000)  # 3km buffer
    gdf = gpd.GeoDataFrame(geometry=[buffered], crs=CRS_UTM17N).to_crs(CRS_WGS84)
    gdf.to_file(DISTRICT_CACHE, driver="GPKG")
    _progress("Created fallback district boundary (convex hull + 3km buffer)")
    return gdf


# ---------------------------------------------------------------------------
# 3. Download and prepare road networks
# ---------------------------------------------------------------------------
def _get_network_cache_path(mode: str) -> Path:
    return DATA_CACHE / f"network_{mode}.graphml"


def _add_travel_time_weights(G: nx.MultiDiGraph, mode: str) -> nx.MultiDiGraph:
    """Add travel_time (seconds) edge weights based on mode."""
    for u, v, key, data in G.edges(keys=True, data=True):
        length_m = data.get("length", 0)

        if mode == "walk":
            data["travel_time"] = length_m / WALK_SPEED_MPS
        elif mode == "bike":
            data["travel_time"] = length_m / BIKE_SPEED_MPS
        elif mode == "drive":
            highway = data.get("highway", "residential")
            if isinstance(highway, list):
                highway = highway[0]
            speed_mph = DRIVE_EFFECTIVE_SPEEDS_MPH.get(highway, DEFAULT_DRIVE_EFFECTIVE_MPH)
            speed_mps = speed_mph * 0.44704  # mph to m/s
            data["travel_time"] = length_m / speed_mps if speed_mps > 0 else 9999
    return G


def download_network(district_polygon, mode: str) -> nx.MultiDiGraph:
    """Download OSM road network for the district area.

    Args:
        district_polygon: Shapely polygon of the district (WGS84)
        mode: 'walk', 'bike', or 'drive'
    Returns:
        NetworkX graph with travel_time edge weights (seconds)
    """
    cache_path = _get_network_cache_path(mode)

    if cache_path.exists():
        _progress(f"Loading cached {mode} network from {cache_path}")
        G = ox.load_graphml(cache_path)
        # Re-add travel_time weights (graphml stores as strings)
        _add_travel_time_weights(G, mode)
        n_before = G.number_of_edges()
        _ensure_bidirectional(G)
        n_added = G.number_of_edges() - n_before
        if n_added:
            _progress(f"  Added {n_added} reverse edges for bidirectional {mode} network")
        return G

    _progress(f"Downloading {mode} road network from OSM (this may take a few minutes) ...")

    ox.settings.use_cache = True
    ox.settings.requests_timeout = 300

    # Buffer the polygon slightly for edge accuracy
    district_gdf = gpd.GeoDataFrame(geometry=[district_polygon], crs=CRS_WGS84)
    district_utm = district_gdf.to_crs(CRS_UTM17N)
    buffered_utm = district_utm.geometry.iloc[0].buffer(500)
    buffered_wgs = gpd.GeoDataFrame(
        geometry=[buffered_utm], crs=CRS_UTM17N
    ).to_crs(CRS_WGS84).geometry.iloc[0]

    # Map mode to OSMnx network_type
    osmnx_type = {"walk": "walk", "bike": "bike", "drive": "drive_service"}[mode]

    try:
        G = ox.graph_from_polygon(buffered_wgs, network_type=osmnx_type, simplify=True)
    except Exception as e:
        if mode == "bike":
            _progress(f"Bike network failed ({e}), falling back to 'all' network")
            G = ox.graph_from_polygon(buffered_wgs, network_type="all", simplify=True)
        else:
            raise

    G = _add_travel_time_weights(G, mode)

    ox.save_graphml(G, cache_path)
    _progress(f"Cached {mode} network ({G.number_of_nodes()} nodes, {G.number_of_edges()} edges) to {cache_path}")
    n_before = G.number_of_edges()
    _ensure_bidirectional(G)
    n_added = G.number_of_edges() - n_before
    if n_added:
        _progress(f"  Added {n_added} reverse edges for bidirectional {mode} network")
    return G


# ---------------------------------------------------------------------------
# 4. Compute school-outward travel times
# ---------------------------------------------------------------------------
def compute_school_travel_times(
    G: nx.MultiDiGraph,
    schools: gpd.GeoDataFrame,
) -> dict:
    """Run Dijkstra from each school outward (no cutoff — explores entire graph).

    Returns:
        {school_name: {node_id: travel_time_seconds, ...}, ...}
    """
    node_ids, tree, cos_lat = _build_node_index(G)
    travel_times = {}
    for _, row in schools.iterrows():
        name = row["school"]
        nearest_node = _nearest_node(node_ids, tree, row.geometry.x, row.geometry.y, cos_lat)
        times = nx.single_source_dijkstra_path_length(
            G, nearest_node, weight="travel_time"
        )
        travel_times[name] = dict(times)
        _progress(f"  {name}: reached {len(times)} nodes (full graph)")

    return travel_times


# ---------------------------------------------------------------------------
# 5. Create analysis grid
# ---------------------------------------------------------------------------
def create_grid(district_polygon, resolution_m: int = GRID_RESOLUTION_M) -> gpd.GeoDataFrame:
    """Create a regular point grid over the district at given resolution."""
    # Project to UTM for meter-based grid
    district_gdf = gpd.GeoDataFrame(geometry=[district_polygon], crs=CRS_WGS84)
    district_utm = district_gdf.to_crs(CRS_UTM17N).geometry.iloc[0]

    minx, miny, maxx, maxy = district_utm.bounds
    xs = np.arange(minx, maxx, resolution_m)
    ys = np.arange(miny, maxy, resolution_m)

    points = []
    grid_ids = []
    idx = 0
    for x in xs:
        for y in ys:
            pt = Point(x, y)
            if district_utm.contains(pt):
                points.append(pt)
                grid_ids.append(idx)
                idx += 1

    _progress(f"Created grid with {len(points)} points at {resolution_m}m resolution")

    gdf = gpd.GeoDataFrame(
        {"grid_id": grid_ids},
        geometry=points,
        crs=CRS_UTM17N,
    ).to_crs(CRS_WGS84)

    gdf["lat"] = gdf.geometry.y
    gdf["lon"] = gdf.geometry.x

    return gdf


# ---------------------------------------------------------------------------
# 6. Compute travel scores
# ---------------------------------------------------------------------------
def compute_travel_scores(
    grid: gpd.GeoDataFrame,
    travel_times_by_mode: dict,
    graphs: dict,
    scenarios: dict,
) -> pd.DataFrame:
    """For each grid point, scenario, and mode, compute min travel time.

    Args:
        grid: GeoDataFrame of grid points
        travel_times_by_mode: {mode: {school_name: {node_id: seconds}}}
        graphs: {mode: nx.MultiDiGraph}
        scenarios: {scenario_name: [closed_schools]}

    Returns:
        DataFrame with columns:
            grid_id, lat, lon, mode, scenario, min_time_seconds, nearest_school
    """
    all_schools = list(next(iter(travel_times_by_mode.values())).keys())

    results = []
    grid_ids = grid["grid_id"].values
    grid_lats = grid["lat"].values
    grid_lons = grid["lon"].values
    n_points = len(grid)

    for mode, travel_times in travel_times_by_mode.items():
        G = graphs[mode]

        _progress(f"Snapping grid points to {mode} network (nearest-edge) ...")
        eidx = _build_edge_index(G)
        cos_lat = eidx["cos_lat"]

        # Batch query: nearest edge for every grid point
        query_pts = shapely.points(grid_lons * cos_lat, grid_lats)
        nearest_ei = eidx["tree"].nearest(query_pts)

        # Vectorized perpendicular distance (scaled degrees → meters)
        matched_geoms = np.array(eidx["scaled_geoms"], dtype=object)[nearest_ei]
        access_dist_m = shapely.distance(query_pts, matched_geoms) * 111_320.0

        # Vectorized fraction along matched edge (0 = start_node, 1 = end_node)
        snap_fracs = shapely.line_locate_point(matched_geoms, query_pts, normalized=True)

        # Per-point endpoint IDs and edge travel times
        snap_start = eidx["start_nodes"][nearest_ei]
        snap_end   = eidx["end_nodes"][nearest_ei]
        snap_etime = eidx["edge_times"][nearest_ei]

        # Off-network access-leg speed as a fraction of modal speed.
        # Walk/bike access legs (sidewalks, lawns, parking lots) are close
        # to full speed; drive access legs (driveways, parking lots) are
        # much slower than road speed.
        ACCESS_SPEED_FACTOR = {"walk": 0.9, "bike": 0.8, "drive": 0.2}[mode]
        access_speed = ACCESS_SPEED_FACTOR * {
            "walk": WALK_SPEED_MPS, "bike": BIKE_SPEED_MPS,
            "drive": DEFAULT_DRIVE_EFFECTIVE_MPH * 0.44704,
        }[mode]

        # Max access-leg distance: pixels more than 2 grid cells from any
        # network edge are unreachable (e.g. lakes, large parks).
        max_access_m = 2 * GRID_RESOLUTION_M

        for scenario_name, closed_schools in scenarios.items():
            open_schools = [s for s in all_schools if s not in closed_schools]

            _progress(f"  Computing {mode} / {scenario_name} ({len(open_schools)} open schools) ...")

            for i in range(n_points):
                # Too far from any road → unreachable
                if access_dist_m[i] > max_access_m:
                    results.append({
                        "grid_id": grid_ids[i],
                        "lat": grid_lats[i],
                        "lon": grid_lons[i],
                        "mode": mode,
                        "scenario": scenario_name,
                        "min_time_seconds": np.nan,
                        "nearest_school": None,
                    })
                    continue

                u_node = snap_start[i]
                v_node = snap_end[i]
                f = snap_fracs[i]
                e_time = snap_etime[i]
                access_time_s = access_dist_m[i] / access_speed

                best_time = np.inf
                best_school = None

                for school_name in open_schools:
                    t_u = travel_times[school_name].get(u_node, np.inf)
                    t_v = travel_times[school_name].get(v_node, np.inf)
                    # Interpolate: travel from snap point to whichever endpoint is faster
                    via_u = t_u + f * e_time           # school→u, then f of edge back to snap pt
                    via_v = t_v + (1.0 - f) * e_time   # school→v, then (1-f) of edge back
                    total = min(via_u, via_v) + access_time_s
                    if total < best_time:
                        best_time = total
                        best_school = school_name

                results.append({
                    "grid_id": grid_ids[i],
                    "lat": grid_lats[i],
                    "lon": grid_lons[i],
                    "mode": mode,
                    "scenario": scenario_name,
                    "min_time_seconds": best_time if best_time < np.inf else np.nan,
                    "nearest_school": best_school,
                })

    df = pd.DataFrame(results)
    _progress(f"Computed {len(df)} travel scores")
    return df


# ---------------------------------------------------------------------------
# 7. Rasterize grid → GeoTIFF → colorized PNG overlay
# ---------------------------------------------------------------------------
TIFF_DIR = DATA_CACHE / "school_desert_tiffs"


def rasterize_grid(
    grid_df: pd.DataFrame,
    value_column: str,
    resolution_m: int = GRID_RESOLUTION_M,
    district_polygon=None,
    grid_params: dict = None,
) -> tuple:
    """Convert grid points to a 2D value raster in WGS84 space.

    Builds the pixel grid directly in lat/lon so rows align with latitude
    lines, matching Leaflet's axis-aligned image overlay display.

    Args:
        district_polygon: Optional Shapely polygon (WGS84) used to mask
            pixels outside the district after gap filling.
        grid_params: Pre-computed grid parameters (minlon, maxlon, minlat,
            maxlat, ncols, nrows, dlat, dlon).  When provided, all calls
            share the same pixel grid, ensuring consistent alignment across
            scenarios and modes.

    Returns:
        (values_2d, grid_meta, bounds) or (None, None, None) if no valid data.
        values_2d: np.float32 array (nRows×nCols), NaN for empty cells
        grid_meta: dict with WGS84 bounds, cell size, dimensions
        bounds: [[south, west], [north, east]] for Leaflet
    """
    valid = grid_df.dropna(subset=[value_column])
    if len(valid) == 0:
        return None, None, None

    lats = valid["lat"].values
    lons = valid["lon"].values
    vals = valid[value_column].values

    if grid_params is not None:
        minlon = grid_params["minlon"]
        maxlon = grid_params["maxlon"]
        minlat = grid_params["minlat"]
        maxlat = grid_params["maxlat"]
        ncols = grid_params["ncols"]
        nrows = grid_params["nrows"]
        dlat = grid_params["dlat"]
        dlon = grid_params["dlon"]
    else:
        # Cell size in degrees, approximating resolution_m at center latitude
        center_lat = lats.mean()
        dlat = resolution_m / 111_320.0
        dlon = resolution_m / (111_320.0 * np.cos(np.radians(center_lat)))

        # Raster bounds with half-cell padding
        minlon = lons.min() - dlon / 2
        maxlon = lons.max() + dlon / 2
        minlat = lats.min() - dlat / 2
        maxlat = lats.max() + dlat / 2

        ncols = int(np.ceil((maxlon - minlon) / dlon))
        nrows = int(np.ceil((maxlat - minlat) / dlat))

        # Snap bounds to exact pixel multiples so Leaflet's pixel stride
        # matches the rasterizer's dlat/dlon (fixes sub-pixel visual shift).
        maxlon = minlon + ncols * dlon
        minlat = maxlat - nrows * dlat

    values_2d = np.full((nrows, ncols), np.inf, dtype=np.float32)
    col_indices = np.clip(((lons - minlon) / dlon).astype(int), 0, ncols - 1)
    row_indices = np.clip(((maxlat - lats) / dlat).astype(int), 0, nrows - 1)
    # Use minimum-wins so the lowest travel time wins when multiple grid
    # points (including injected school anchor points) map to the same pixel.
    np.minimum.at(values_2d, (row_indices, col_indices), vals)
    values_2d[np.isinf(values_2d)] = np.nan

    # Track which pixels have ANY grid point (including routing NaNs).
    # This lets us distinguish rotation gaps (no grid point mapped here)
    # from routing gaps (grid point exists but Dijkstra found no path).
    all_lats = grid_df["lat"].values
    all_lons = grid_df["lon"].values
    has_point = np.zeros((nrows, ncols), dtype=bool)
    all_col_idx = np.clip(((all_lons - minlon) / dlon).astype(int), 0, ncols - 1)
    all_row_idx = np.clip(((maxlat - all_lats) / dlat).astype(int), 0, nrows - 1)
    has_point[all_row_idx, all_col_idx] = True

    # Fill ONLY rotation gaps: NaN pixels with no grid point assigned.
    # These are ~1 pixel wide from UTM→WGS84 coordinate misalignment.
    # Routing NaN gaps (where Dijkstra found no path) are preserved.
    from scipy.ndimage import uniform_filter
    for _ in range(2):
        rotation_gap = np.isnan(values_2d) & ~has_point
        if not rotation_gap.any():
            break
        filled = np.where(np.isnan(values_2d), 0.0, values_2d)
        valid_mask = ~np.isnan(values_2d)
        counts = uniform_filter(valid_mask.astype(np.float64), size=3, mode='constant', cval=0.0)
        smoothed = uniform_filter(filled.astype(np.float64), size=3, mode='constant', cval=0.0)
        has_neighbor = counts > 0
        fillable = rotation_gap & has_neighbor
        values_2d[fillable] = (smoothed[fillable] / counts[fillable]).astype(np.float32)

    # Mask out pixels whose centers fall outside the district polygon
    # so the gap fill doesn't bleed past the boundary.
    if district_polygon is not None:
        from shapely.prepared import prep
        prepared = prep(district_polygon)
        col_centers = minlon + (np.arange(ncols) + 0.5) * dlon
        row_centers = maxlat - (np.arange(nrows) + 0.5) * dlat
        cc, rr = np.meshgrid(col_centers, row_centers)
        pixel_points = [Point(lon, lat) for lon, lat in zip(cc.ravel(), rr.ravel())]
        inside = np.array([prepared.contains(p) for p in pixel_points]).reshape(nrows, ncols)
        values_2d[~inside] = np.nan

    bounds = [[minlat, minlon], [maxlat, maxlon]]

    grid_meta = {
        "lonMin": float(minlon), "latMin": float(minlat),
        "lonMax": float(maxlon), "latMax": float(maxlat),
        "cellSize": resolution_m, "nRows": nrows, "nCols": ncols,
    }
    return values_2d, grid_meta, bounds


def save_geotiff(values_2d: np.ndarray, grid_meta: dict, path: Path):
    """Save a value raster as a GeoTIFF with WGS84 CRS."""
    import rasterio
    from rasterio.transform import from_bounds

    nrows, ncols = values_2d.shape
    transform = from_bounds(
        grid_meta["lonMin"], grid_meta["latMin"],
        grid_meta["lonMax"], grid_meta["latMax"],
        ncols, nrows,
    )
    path.parent.mkdir(parents=True, exist_ok=True)
    with rasterio.open(
        str(path), "w", driver="GTiff",
        height=nrows, width=ncols, count=1,
        dtype="float32", crs=CRS_WGS84,
        transform=transform, nodata=float("nan"),
        compress="deflate",
    ) as dst:
        dst.write(values_2d, 1)


def colorize_raster(
    values_2d: np.ndarray,
    vmin: float, vmax: float,
    cmap_name: str,
) -> str:
    """Apply a colormap to a 2D value raster and return a base64 PNG.

    Cells with NaN are fully transparent; data cells are opaque (alpha=210).
    """
    if values_2d is None:
        return None

    cmap = plt.get_cmap(cmap_name)
    norm = mcolors.Normalize(vmin=vmin, vmax=vmax)

    has_data = ~np.isnan(values_2d)
    normed = norm(np.where(has_data, values_2d, 0))
    rgba = (cmap(normed) * 255).astype(np.uint8)
    rgba[..., 3] = np.where(has_data, 210, 0)

    buf = io.BytesIO()
    plt.imsave(buf, rgba, format="png")
    buf.seek(0)
    return base64.b64encode(buf.read()).decode("utf-8")


def encode_value_grid(values_2d: np.ndarray) -> str:
    """Encode a 2D float32 array as base64 for JS hover lookup."""
    return base64.b64encode(values_2d.astype(np.float32).tobytes()).decode("utf-8")


# ---------------------------------------------------------------------------
# 8a. Render affected-household histograms
# ---------------------------------------------------------------------------
def _render_affected_charts(affected_data: dict, style: str = "affected") -> dict:
    """Render histogram PNGs for affected parcels per scenario|mode.

    Args:
        affected_data: {"scenario|mode": {"count": N, "values": ndarray, "years": ndarray}}
        style: "affected" (blue/green) or "walkzone" (orange/purple)

    Returns:
        {"scenario|mode": {"count": N, "chart_value": b64, "chart_years": b64}}
    """
    if style == "walkzone":
        color_value, color_years = "#d35400", "#8e44ad"
        label_suffix = "Walk-Zone Parcels"
    else:
        color_value, color_years = "#4a90d9", "#6ab04c"
        label_suffix = "Affected Parcels"

    result = {}
    for key, info in affected_data.items():
        count = info["count"]
        if count == 0:
            result[key] = {"count": 0}
            continue

        entry = {"count": count}

        # Chart 1 — Assessed Value
        values = info["values"]
        if len(values) > 0:
            fig, ax = plt.subplots(figsize=(4.5, 2.5), dpi=100)
            ax.hist(values, bins=25, color=color_value, edgecolor="white", linewidth=0.5)
            median_val = np.median(values)
            ax.axvline(median_val, color="#dc3545", linestyle="--", linewidth=1.5)
            ax.annotate(
                f"Median: ${median_val:,.0f}",
                xy=(median_val, ax.get_ylim()[1] * 0.85),
                xytext=(10, 0), textcoords="offset points",
                fontsize=9, color="#dc3545", fontweight="bold",
            )
            ax.set_xlabel("Assessed Value ($)", fontsize=10)
            ax.set_ylabel("Parcels", fontsize=10)
            ax.set_title(f"Assessed Value — {count:,} {label_suffix}", fontsize=11, fontweight="bold")
            ax.xaxis.set_major_formatter(plt.FuncFormatter(lambda x, p: f"${x:,.0f}"))
            ax.tick_params(labelsize=8)
            plt.xticks(rotation=30)
            fig.tight_layout()
            buf = io.BytesIO()
            fig.savefig(buf, format="png", bbox_inches="tight")
            plt.close(fig)
            buf.seek(0)
            entry["chart_value"] = base64.b64encode(buf.read()).decode("utf-8")

        # Chart 2 — Years Since Last Sale
        years = info["years"]
        if len(years) > 0:
            fig, ax = plt.subplots(figsize=(4.5, 2.5), dpi=100)
            ax.hist(years, bins=25, color=color_years, edgecolor="white", linewidth=0.5)
            median_yr = np.median(years)
            ax.axvline(median_yr, color="#dc3545", linestyle="--", linewidth=1.5)
            ax.annotate(
                f"Median: {median_yr:.0f} yr",
                xy=(median_yr, ax.get_ylim()[1] * 0.85),
                xytext=(10, 0), textcoords="offset points",
                fontsize=9, color="#dc3545", fontweight="bold",
            )
            ax.set_xlabel("Years Since Last Sale", fontsize=10)
            ax.set_ylabel("Parcels", fontsize=10)
            ax.set_title(f"Years Since Sale — {count:,} {label_suffix}", fontsize=11, fontweight="bold")
            ax.tick_params(labelsize=8)
            fig.tight_layout()
            buf = io.BytesIO()
            fig.savefig(buf, format="png", bbox_inches="tight")
            plt.close(fig)
            buf.seek(0)
            entry["chart_years"] = base64.b64encode(buf.read()).decode("utf-8")

        result[key] = entry

    return result


# ---------------------------------------------------------------------------
# 8b. Create interactive map
# ---------------------------------------------------------------------------
def create_map(
    heatmap_data: dict,
    schools: gpd.GeoDataFrame,
    district_gdf: gpd.GeoDataFrame,
    bounds: list,
    hover_grids: dict = None,
    grid_meta: dict = None,
    network_geojsons: dict = None,
    property_points: list = None,
    affected_charts: dict = None,
    walk_zones_geojson: dict = None,
    walk_zone_charts: dict = None,
) -> folium.Map:
    """Create interactive Folium map with scenario/mode switching.

    Args:
        heatmap_data: {(scenario, mode, layer_type): (base64_png, bounds)}
        schools: GeoDataFrame of school locations
        district_gdf: District boundary GeoDataFrame
        bounds: [[south, west], [north, east]] for overlay positioning
        hover_grids: {"scenario|mode|type": base64_float32_grid}
        grid_meta: dict with UTM/WGS84 bounds and grid dimensions
        network_geojsons: {mode: geojson_dict} for road network overlay
        property_points: list of dicts with residential parcel centroids
        affected_charts: {"scenario|mode": {"count": N, "chart_value": b64, "chart_years": b64}}
        walk_zones_geojson: GeoJSON FeatureCollection of dissolved walk zone polygons
        walk_zone_charts: {"scenario": {"count": N, "chart_value": b64, "chart_years": b64}}
    """
    m = folium.Map(
        location=CHAPEL_HILL_CENTER,
        zoom_start=12,
        tiles="cartodbpositron",
        control_scale=True,
        prefer_canvas=True,
    )

    # Add district boundary
    folium.GeoJson(
        district_gdf.__geo_interface__,
        name="District Boundary",
        style_function=lambda x: {
            "fillColor": "transparent",
            "color": "#333333",
            "weight": 2,
            "dashArray": "5,5",
        },
    ).add_to(m)

    # School data for JS
    school_data = []
    for _, row in schools.iterrows():
        school_data.append({
            "name": row["school"],
            "lat": row["lat"],
            "lon": row["lon"],
            "address": row.get("address", ""),
        })

    # Build the custom HTML/JS control with overlay data embedded
    control_html = _build_control_html(
        heatmap_data, school_data, hover_grids or {}, grid_meta,
        network_geojsons=network_geojsons,
        property_points=property_points or [],
        affected_charts=affected_charts,
        walk_zones_geojson=walk_zones_geojson,
        walk_zone_charts=walk_zone_charts,
    )
    m.get_root().html.add_child(folium.Element(control_html))

    return m


def _build_control_html(
    heatmap_data: dict, schools: list,
    hover_grids: dict, grid_meta: dict,
    network_geojsons: dict = None,
    property_points: list = None,
    affected_charts: dict = None,
    walk_zones_geojson: dict = None,
    walk_zone_charts: dict = None,
) -> str:
    """Build HTML/CSS/JS for scenario/mode/layer switching controls.

    Creates L.imageOverlay instances directly in JS (bypassing Folium)
    and stores them in a dictionary for direct access by updateCommunityMap().
    Includes hover tooltip that reads values from embedded Float32 grids.
    """

    # Build overlay data for JS — keyed by "scenario|mode|type"
    overlays_data = {}
    for (scenario, mode, layer_type), (b64, img_bounds) in heatmap_data.items():
        if b64 is None:
            continue
        key = f"{scenario}|{mode}|{layer_type}"
        overlays_data[key] = {
            "url": f"data:image/png;base64,{b64}",
            "bounds": img_bounds,
        }

    overlays_data_json = json.dumps(overlays_data)
    scenarios_json = json.dumps(SCENARIOS)
    scenario_labels_json = json.dumps(SCENARIO_LABELS)
    mode_labels_json = json.dumps(MODE_LABELS)
    mode_ranges_json = json.dumps(MODE_RANGES)
    schools_json = json.dumps(schools)
    hover_grids_json = json.dumps(hover_grids)
    grid_meta_json = json.dumps(grid_meta) if grid_meta else "null"
    network_edges_json = json.dumps(network_geojsons or {})
    property_points_json = json.dumps(property_points or [])
    affected_charts_json = json.dumps(affected_charts or {})
    walk_zones_geojson_json = json.dumps(walk_zones_geojson or {})
    walk_zone_charts_json = json.dumps(walk_zone_charts or {})

    return f"""
<style>
/* Force crisp pixel rendering on all Leaflet image overlays */
.leaflet-image-layer {{
    image-rendering: pixelated;
    image-rendering: -moz-crisp-edges;
    image-rendering: crisp-edges;
}}
#community-controls {{
    flex: 0 0 280px;
    width: 280px;
    height: 100vh;
    overflow-y: auto;
    background: white;
    padding: 15px;
    border-left: 1px solid #dee2e6;
    box-shadow: -2px 0 8px rgba(0,0,0,0.1);
    font-family: 'Segoe UI', Tahoma, sans-serif;
    font-size: 13px;
    box-sizing: border-box;
}}
#main-column {{
    flex: 1;
    display: flex;
    flex-direction: column;
    min-width: 0;
    height: 100vh;
    overflow: hidden;
}}
#community-controls h3 {{
    margin: 0 0 10px 0;
    font-size: 15px;
    color: #333;
    border-bottom: 2px solid {ACCENT_COLOR};
    padding-bottom: 5px;
}}
#community-controls label {{
    display: block;
    margin: 3px 0;
    cursor: pointer;
    padding: 2px 4px;
    border-radius: 3px;
}}
#community-controls label:hover {{
    background: #f0f0f0;
}}
#community-controls .section-title {{
    font-weight: bold;
    margin: 10px 0 5px 0;
    color: #555;
    font-size: 12px;
    text-transform: uppercase;
}}
#community-legend {{
    margin-top: 10px;
    padding-top: 8px;
    border-top: 1px solid #ddd;
}}
#community-legend .gradient-bar {{
    height: 12px;
    border-radius: 3px;
    margin: 4px 0;
}}
#community-legend .range-labels {{
    display: flex;
    justify-content: space-between;
    font-size: 11px;
    color: #666;
}}
.school-marker-info {{
    font-size: 12px;
    margin-top: 8px;
    padding-top: 8px;
    border-top: 1px solid #ddd;
    color: #666;
}}
.school-marker-info .closed {{
    color: {ACCENT_COLOR};
    font-weight: bold;
}}
#community-tooltip {{
    position: fixed;
    z-index: 2000;
    background: rgba(0,0,0,0.8);
    color: #fff;
    padding: 4px 8px;
    border-radius: 4px;
    font-size: 12px;
    font-family: 'Segoe UI', Tahoma, sans-serif;
    pointer-events: none;
    display: none;
    white-space: nowrap;
}}
#property-legend {{
    margin-top: 6px;
    padding-top: 6px;
    border-top: 1px solid #eee;
    display: none;
}}
#property-legend .gradient-bar {{
    height: 10px;
    border-radius: 3px;
    margin: 3px 0;
    background: linear-gradient(to right, #ffffcc, #a1dab4, #41b6c4, #225ea8);
}}
#property-legend .range-labels {{
    display: flex;
    justify-content: space-between;
    font-size: 10px;
    color: #666;
}}
#affected-panel {{
    background: #f8f9fa;
    border-top: 2px solid #dee2e6;
    display: flex;
    align-items: center;
    justify-content: center;
    gap: 20px;
    padding: 10px 24px;
    overflow-y: auto;
    box-sizing: border-box;
    flex-wrap: wrap;
}}
#affected-panel .chart-container {{
    display: flex;
    align-items: center;
    gap: 16px;
    flex-wrap: wrap;
    justify-content: center;
}}
#affected-panel img {{
    max-height: calc(35vh - 40px);
    width: auto;
    border-radius: 4px;
    box-shadow: 0 1px 4px rgba(0,0,0,0.12);
}}
#affected-count-label {{
    font-family: 'Segoe UI', Tahoma, sans-serif;
    font-size: 14px;
    color: #555;
    text-align: center;
    min-width: 120px;
    white-space: nowrap;
}}
#affected-count-label .count {{
    font-size: 28px;
    font-weight: bold;
    color: #333;
    display: block;
    line-height: 1.2;
}}
#walkzone-count-label {{
    font-family: 'Segoe UI', Tahoma, sans-serif;
    font-size: 14px;
    color: #555;
    text-align: center;
    min-width: 120px;
    white-space: nowrap;
}}
#walkzone-count-label .count {{
    font-size: 28px;
    font-weight: bold;
    display: block;
    line-height: 1.2;
}}
</style>

<div id="community-controls">
    <h3>School Community Analysis</h3>

    <div class="section-title">Scenario</div>
    <div id="scenario-options"></div>

    <div class="section-title">Travel Mode</div>
    <div id="mode-options"></div>

    <div class="section-title">Layer</div>
    <label><input type="radio" name="layer_type" value="abs" checked onchange="window.updateCommunityMap()"> Travel Time</label>
    <label><input type="radio" name="layer_type" value="delta" onchange="window.updateCommunityMap()"> Change from Baseline</label>

    <label style="display:block;margin-top:8px">
      <input type="checkbox" id="show-network" checked onchange="window.updateCommunityMap()"> Show road network
    </label>
    <label style="display:block;margin-top:4px">
      <input type="checkbox" id="show-properties" onchange="window.togglePropertyLayer()"> Show residential parcels
    </label>
    <label style="display:block;margin-top:4px">
      <input type="checkbox" id="show-walk-zones" onchange="window.toggleWalkZones()"> Show walk zones
    </label>

    <div id="community-legend">
        <div class="section-title">Legend</div>
        <div id="legend-label"></div>
        <div class="gradient-bar" id="legend-bar"></div>
        <div class="range-labels">
            <span id="legend-min"></span>
            <span id="legend-max"></span>
        </div>
    </div>

    <div class="school-marker-info" id="school-info"></div>

    <div id="property-legend">
        <div style="font-weight:bold;font-size:11px;color:#555;margin-bottom:2px">Years since last sale</div>
        <div class="gradient-bar"></div>
        <div class="range-labels">
            <span>0 yr</span>
            <span style="color:#999">gray = no sale</span>
            <span>50+ yr</span>
        </div>
    </div>

    <div id="walkzone-legend" style="display:none;margin-top:6px;padding-top:6px;border-top:1px solid #eee">
        <div style="font-weight:bold;font-size:11px;color:#555;margin-bottom:2px">Elementary walk zones</div>
        <div style="display:flex;align-items:center;gap:6px;font-size:10px">
            <span style="display:inline-block;width:14px;height:14px;background:rgba(52,152,219,0.25);border:2px solid #3498db;border-radius:2px"></span> Open school
            <span style="display:inline-block;width:14px;height:14px;background:rgba(231,76,60,0.25);border:2px solid #e74c3c;border-radius:2px"></span> Closed school
        </div>
    </div>
</div>
<div id="community-tooltip"></div>
<div id="affected-panel">
    <div id="affected-count-label">Select a closure scenario to see affected households.</div>
    <div class="chart-container">
        <img id="chart-value" style="display:none" alt="Assessed value histogram" />
        <img id="chart-years" style="display:none" alt="Years since sale histogram" />
    </div>
    <div id="walkzone-section" style="display:none">
        <div style="border-top:1px solid #ccc;margin:6px 0"></div>
        <div id="walkzone-count-label"></div>
        <div class="chart-container">
            <img id="chart-wz-value" style="display:none" alt="Walk-zone assessed value histogram" />
            <img id="chart-wz-years" style="display:none" alt="Walk-zone years since sale histogram" />
        </div>
    </div>
</div>

<script>
(function() {{
    var SCENARIOS = {scenarios_json};
    var SCENARIO_LABELS = {scenario_labels_json};
    var MODE_LABELS = {mode_labels_json};
    var MODE_RANGES = {mode_ranges_json};
    var OVERLAYS_DATA = {overlays_data_json};
    var SCHOOLS = {schools_json};
    var HOVER_GRIDS_B64 = {hover_grids_json};
    var GRID_META = {grid_meta_json};
    var NETWORK_EDGES = {network_edges_json};
    var PROPERTY_POINTS = {property_points_json};
    var AFFECTED_CHARTS = {affected_charts_json};
    var WALK_ZONES_GEO = {walk_zones_geojson_json};
    var WALK_ZONE_CHARTS = {walk_zone_charts_json};

    var overlayLayers = {{}};
    var networkLayers = {{}};
    var schoolMarkers = [];
    var currentOverlayKey = null;
    var tooltip = document.getElementById('community-tooltip');
    var propertyLayer = null;
    var walkZoneLayer = null;

    // --- Hover grid decoding ---
    var decodedGrids = {{}};
    function decodeGrid(key) {{
        if (decodedGrids[key]) return decodedGrids[key];
        var b64 = HOVER_GRIDS_B64[key];
        if (!b64) return null;
        var raw = atob(b64);
        var buf = new ArrayBuffer(raw.length);
        var u8 = new Uint8Array(buf);
        for (var i = 0; i < raw.length; i++) u8[i] = raw.charCodeAt(i);
        decodedGrids[key] = new Float32Array(buf);
        return decodedGrids[key];
    }}

    function getGridValue(lat, lon) {{
        if (!currentOverlayKey || !GRID_META) return null;
        var grid = decodeGrid(currentOverlayKey);
        if (!grid) return null;
        // Linear WGS84 → fractional grid coords (accurate for small areas)
        var fracX = (lon - GRID_META.lonMin) / (GRID_META.lonMax - GRID_META.lonMin);
        var fracY = (lat - GRID_META.latMin) / (GRID_META.latMax - GRID_META.latMin);
        var col = Math.floor(fracX * GRID_META.nCols);
        var row = Math.floor((1 - fracY) * GRID_META.nRows);
        if (row < 0 || row >= GRID_META.nRows || col < 0 || col >= GRID_META.nCols) return null;
        var val = grid[row * GRID_META.nCols + col];
        return isNaN(val) ? null : val;
    }}

    function getSelectedValue(name) {{
        var radios = document.querySelectorAll('input[name="' + name + '"]');
        for (var i = 0; i < radios.length; i++) {{
            if (radios[i].checked) return radios[i].value;
        }}
        return null;
    }}

    function getMap() {{
        for (var key in window) {{
            try {{
                if (window[key] && window[key]._leaflet_id && window[key].getZoom) {{
                    return window[key];
                }}
            }} catch(e) {{}}
        }}
        return null;
    }}

    function initNetworkLayers(map) {{
        for (var mode in NETWORK_EDGES) {{
            networkLayers[mode] = L.geoJSON(NETWORK_EDGES[mode], {{
                style: {{ color: '#000', weight: 1, opacity: 0 }}
            }}).addTo(map);
        }}
    }}

    function initOverlays(map) {{
        for (var key in OVERLAYS_DATA) {{
            var d = OVERLAYS_DATA[key];
            overlayLayers[key] = L.imageOverlay(d.url, d.bounds, {{opacity: 0}}).addTo(map);
        }}
        initNetworkLayers(map);
        // Attach hover handler once
        map.on('mousemove', function(e) {{
            var val = getGridValue(e.latlng.lat, e.latlng.lng);
            if (val !== null) {{
                var layerType = getSelectedValue('layer_type');
                var label = (layerType === 'delta') ? '+' + val.toFixed(1) + ' min' : val.toFixed(1) + ' min';
                tooltip.textContent = label;
                tooltip.style.left = (e.originalEvent.pageX + 15) + 'px';
                tooltip.style.top = (e.originalEvent.pageY - 10) + 'px';
                tooltip.style.display = 'block';
            }} else {{
                tooltip.style.display = 'none';
            }}
        }});
        map.on('mouseout', function() {{ tooltip.style.display = 'none'; }});
    }}

    function updateSchoolMarkers(map, scenario) {{
        schoolMarkers.forEach(function(m) {{ map.removeLayer(m); }});
        schoolMarkers = [];

        var closedSchools = SCENARIOS[scenario] || [];

        SCHOOLS.forEach(function(school) {{
            var isClosed = closedSchools.indexOf(school.name) !== -1;
            var marker = L.circleMarker([school.lat, school.lon], {{
                radius: isClosed ? 8 : 7,
                fillColor: isClosed ? '#dc3545' : '#0d6efd',
                color: isClosed ? '#dc3545' : '#0a58ca',
                weight: 2,
                opacity: 1,
                fillOpacity: isClosed ? 0.3 : 0.8,
                dashArray: isClosed ? '4,4' : null,
            }});

            var status = isClosed ? '<span style="color:#dc3545;font-weight:bold">CLOSED</span>' : '<span style="color:#198754">Open</span>';
            marker.bindPopup(
                '<b>' + school.name + '</b><br>' +
                school.address + '<br>' +
                status
            );

            if (isClosed) {{
                var xIcon = L.divIcon({{
                    html: '<span style="color:#dc3545;font-size:18px;font-weight:bold;">&times;</span>',
                    className: 'closed-school-x',
                    iconSize: [20, 20],
                    iconAnchor: [10, 10],
                }});
                var xMarker = L.marker([school.lat, school.lon], {{icon: xIcon}});
                xMarker.addTo(map);
                schoolMarkers.push(xMarker);
            }}

            marker.addTo(map);
            schoolMarkers.push(marker);
        }});
    }}

    // --- Property parcel layer ---
    function getPropertyColor(yrs) {{
        if (yrs === null || yrs === undefined || isNaN(yrs)) return '#999';
        var t = Math.min(yrs, 50) / 50;  // clamp to 0-50
        // Interpolate: yellow-green (#ffffcc) → teal (#41b6c4) → dark blue (#225ea8)
        var r, g, b;
        if (t < 0.5) {{
            var s = t * 2;
            r = Math.round(255 * (1 - s) + 65 * s);
            g = Math.round(255 * (1 - s) + 182 * s);
            b = Math.round(204 * (1 - s) + 196 * s);
        }} else {{
            var s = (t - 0.5) * 2;
            r = Math.round(65 * (1 - s) + 34 * s);
            g = Math.round(182 * (1 - s) + 94 * s);
            b = Math.round(196 * (1 - s) + 168 * s);
        }}
        return 'rgb(' + r + ',' + g + ',' + b + ')';
    }}

    function initPropertyLayer(map) {{
        if (propertyLayer) return;
        propertyLayer = L.layerGroup();
        PROPERTY_POINTS.forEach(function(pt) {{
            var color = getPropertyColor(pt.years_since_sale);
            var m = L.circleMarker([pt.lat, pt.lon], {{
                radius: 3,
                fillColor: color,
                color: color,
                weight: 0.5,
                fillOpacity: 0.7,
                opacity: 0.8,
            }});
            var popupLines = ['<b>PIN:</b> ' + (pt.pin || '—')];
            if (pt.luc) popupLines.push('<b>Land use:</b> ' + pt.luc);
            if (pt.imp_vac) popupLines.push('<b>Status:</b> ' + pt.imp_vac);
            if (pt.valuation) popupLines.push('<b>Valuation:</b> $' + Number(pt.valuation).toLocaleString());
            if (pt.sqft) popupLines.push('<b>Sq ft:</b> ' + Number(pt.sqft).toLocaleString());
            if (pt.year_built) popupLines.push('<b>Year built:</b> ' + pt.year_built);
            if (pt.acres) popupLines.push('<b>Acres:</b> ' + Number(pt.acres).toFixed(2));
            if (pt.sale_date) popupLines.push('<b>Last sale:</b> ' + pt.sale_date);
            if (pt.sale_price) popupLines.push('<b>Sale price:</b> $' + Number(pt.sale_price).toLocaleString());
            if (pt.years_since_sale !== null && pt.years_since_sale !== undefined)
                popupLines.push('<b>Years since sale:</b> ' + pt.years_since_sale);
            if (pt.subdivision) popupLines.push('<b>Subdivision:</b> ' + pt.subdivision);
            if (pt.condo_name) popupLines.push('<b>Condo:</b> ' + pt.condo_name);
            if (pt.appraised_value) popupLines.push('<b>Appraised:</b> $' + Number(pt.appraised_value).toLocaleString());
            m.bindPopup(popupLines.join('<br>'), {{maxWidth: 280}});
            propertyLayer.addLayer(m);
        }});
    }}

    window.togglePropertyLayer = function() {{
        var map = getMap();
        if (!map) return;
        var cb = document.getElementById('show-properties');
        var legend = document.getElementById('property-legend');
        if (cb && cb.checked) {{
            initPropertyLayer(map);
            propertyLayer.addTo(map);
            if (legend) legend.style.display = 'block';
        }} else {{
            if (propertyLayer) map.removeLayer(propertyLayer);
            if (legend) legend.style.display = 'none';
        }}
    }};

    // --- Walk zone layer ---
    function initWalkZoneLayer(map) {{
        if (!WALK_ZONES_GEO || !WALK_ZONES_GEO.features) return;
        walkZoneLayer = L.geoJSON(WALK_ZONES_GEO, {{
            style: function(feature) {{
                return {{
                    fillColor: 'rgba(52,152,219,0.25)',
                    color: '#3498db',
                    weight: 2,
                    fillOpacity: 0.25,
                    opacity: 0.8
                }};
            }},
            onEachFeature: function(feature, layer) {{
                if (feature.properties && feature.properties.school_name) {{
                    layer.bindPopup('<b>Walk Zone:</b> ' + feature.properties.school_name);
                }}
            }}
        }});
    }}

    function styleWalkZones() {{
        if (!walkZoneLayer) return;
        var scenario = getSelectedValue('scenario');
        var closedSchools = SCENARIOS[scenario] || [];
        walkZoneLayer.eachLayer(function(layer) {{
            var name = layer.feature.properties.school_name;
            var isClosed = closedSchools.indexOf(name) !== -1;
            layer.setStyle({{
                fillColor: isClosed ? 'rgba(231,76,60,0.25)' : 'rgba(52,152,219,0.25)',
                color: isClosed ? '#e74c3c' : '#3498db',
                weight: 2,
                fillOpacity: 0.25,
                opacity: 0.8
            }});
        }});
    }}

    window.toggleWalkZones = function() {{
        var map = getMap();
        if (!map) return;
        var cb = document.getElementById('show-walk-zones');
        var legend = document.getElementById('walkzone-legend');
        if (cb && cb.checked) {{
            if (!walkZoneLayer) initWalkZoneLayer(map);
            if (walkZoneLayer) walkZoneLayer.addTo(map);
            if (legend) legend.style.display = 'block';
            styleWalkZones();
        }} else {{
            if (walkZoneLayer) map.removeLayer(walkZoneLayer);
            if (legend) legend.style.display = 'none';
        }}
    }};

    // Define updateCommunityMap BEFORE radio buttons are created
    window.updateCommunityMap = function() {{
        var scenario = getSelectedValue('scenario');
        var mode = getSelectedValue('mode');
        var layerType = getSelectedValue('layer_type');

        if (!scenario || !mode || !layerType) return;

        var map = getMap();
        if (!map) return;

        if (Object.keys(overlayLayers).length === 0) {{
            initOverlays(map);
        }}

        // Disable "Change from Baseline" for baseline scenario
        var deltaRadio = document.querySelector('input[name="layer_type"][value="delta"]');
        if (scenario === 'baseline') {{
            if (layerType === 'delta') {{
                document.querySelector('input[name="layer_type"][value="abs"]').checked = true;
                layerType = 'abs';
            }}
            deltaRadio.disabled = true;
            deltaRadio.parentElement.style.opacity = '0.4';
        }} else {{
            deltaRadio.disabled = false;
            deltaRadio.parentElement.style.opacity = '1';
        }}

        // Hide all overlays, show selected
        for (var key in overlayLayers) {{
            overlayLayers[key].setOpacity(0);
        }}
        var overlayKey = scenario + '|' + mode + '|' + layerType;
        if (overlayLayers[overlayKey]) {{
            overlayLayers[overlayKey].setOpacity(0.7);
        }}
        currentOverlayKey = overlayKey;

        // Toggle network layers: show selected mode, hide others
        var showNetworkEl = document.getElementById('show-network');
        var showNetwork = showNetworkEl ? showNetworkEl.checked : true;
        for (var m in networkLayers) {{
            networkLayers[m].setStyle({{opacity: (showNetwork && m === mode) ? 0.4 : 0}});
        }}

        updateSchoolMarkers(map, scenario);

        // Update legend
        var ranges = MODE_RANGES[mode];
        var range = layerType === 'abs' ? ranges['abs'] : ranges['delta'];
        var legendLabel = document.getElementById('legend-label');
        var legendBar = document.getElementById('legend-bar');
        var legendMin = document.getElementById('legend-min');
        var legendMax = document.getElementById('legend-max');

        if (layerType === 'abs') {{
            legendLabel.textContent = 'Minutes to nearest school';
            legendBar.style.background = 'linear-gradient(to right, #1a9850, #a6d96a, #ffffbf, #fdae61, #d73027)';
        }} else {{
            legendLabel.textContent = 'Added minutes (vs baseline)';
            legendBar.style.background = 'linear-gradient(to right, #fff5eb, #fdbe85, #fd8d3c, #e6550d, #a63603)';
        }}
        legendMin.textContent = range[0] + ' min';
        legendMax.textContent = range[1] + ' min';

        // Update school info
        var closedSchools = SCENARIOS[scenario] || [];
        var infoDiv = document.getElementById('school-info');
        if (closedSchools.length > 0) {{
            infoDiv.innerHTML = '<span class="closed">Closed:</span> ' + closedSchools.join(', ');
        }} else {{
            infoDiv.innerHTML = 'All 11 schools open';
        }}

        // Update affected-household charts
        var chartKey = scenario + '|' + mode;
        var panel = document.getElementById('affected-panel');
        var countLabel = document.getElementById('affected-count-label');
        var chartValue = document.getElementById('chart-value');
        var chartYears = document.getElementById('chart-years');

        if (scenario === 'baseline') {{
            countLabel.innerHTML = 'Baseline &mdash; no school closures';
            chartValue.style.display = 'none';
            chartYears.style.display = 'none';
        }} else if (AFFECTED_CHARTS[chartKey]) {{
            var ac = AFFECTED_CHARTS[chartKey];
            countLabel.innerHTML = '<span class="count">' + ac.count.toLocaleString() + '</span>affected households';
            if (ac.chart_value) {{
                chartValue.src = 'data:image/png;base64,' + ac.chart_value;
                chartValue.style.display = 'block';
            }} else {{
                chartValue.style.display = 'none';
            }}
            if (ac.chart_years) {{
                chartYears.src = 'data:image/png;base64,' + ac.chart_years;
                chartYears.style.display = 'block';
            }} else {{
                chartYears.style.display = 'none';
            }}
        }} else {{
            countLabel.textContent = 'No affected-household data for this scenario.';
            chartValue.style.display = 'none';
            chartYears.style.display = 'none';
        }}

        // Update walk-zone charts
        var wzSection = document.getElementById('walkzone-section');
        var wzLabel = document.getElementById('walkzone-count-label');
        var wzChartVal = document.getElementById('chart-wz-value');
        var wzChartYrs = document.getElementById('chart-wz-years');

        if (scenario === 'baseline') {{
            wzSection.style.display = 'none';
        }} else if (WALK_ZONE_CHARTS[scenario]) {{
            var wz = WALK_ZONE_CHARTS[scenario];
            wzSection.style.display = 'block';
            wzLabel.innerHTML = '<span class="count" style="color:#d35400">' + wz.count.toLocaleString() + '</span>walk-zone households';
            if (wz.chart_value) {{
                wzChartVal.src = 'data:image/png;base64,' + wz.chart_value;
                wzChartVal.style.display = 'block';
            }} else {{
                wzChartVal.style.display = 'none';
            }}
            if (wz.chart_years) {{
                wzChartYrs.src = 'data:image/png;base64,' + wz.chart_years;
                wzChartYrs.style.display = 'block';
            }} else {{
                wzChartYrs.style.display = 'none';
            }}
        }} else {{
            wzSection.style.display = 'block';
            wzLabel.textContent = 'No walk zone defined for this school.';
            wzChartVal.style.display = 'none';
            wzChartYrs.style.display = 'none';
        }}

        // Update walk-zone polygon styling if visible
        styleWalkZones();
    }};

    // Populate scenario radio buttons
    var scenarioDiv = document.getElementById('scenario-options');
    var first = true;
    for (var key in SCENARIO_LABELS) {{
        var label = document.createElement('label');
        var radio = document.createElement('input');
        radio.type = 'radio';
        radio.name = 'scenario';
        radio.value = key;
        radio.onchange = function() {{ window.updateCommunityMap(); }};
        if (first) {{ radio.checked = true; first = false; }}
        label.appendChild(radio);
        label.appendChild(document.createTextNode(' ' + SCENARIO_LABELS[key]));
        scenarioDiv.appendChild(label);
    }}

    // Populate mode radio buttons
    var modeDiv = document.getElementById('mode-options');
    first = true;
    for (var key in MODE_LABELS) {{
        var label = document.createElement('label');
        var radio = document.createElement('input');
        radio.type = 'radio';
        radio.name = 'mode';
        radio.value = key;
        radio.onchange = function() {{ window.updateCommunityMap(); }};
        if (first) {{ radio.checked = true; first = false; }}
        label.appendChild(radio);
        label.appendChild(document.createTextNode(' ' + MODE_LABELS[key]));
        modeDiv.appendChild(label);
    }}

    // Initialize on page load: move affected-panel below map div and resize
    setTimeout(function() {{
        var mapDiv = document.querySelector('.folium-map');
        var panel = document.getElementById('affected-panel');
        var controls = document.getElementById('community-controls');
        if (mapDiv && panel) {{
            document.documentElement.style.cssText = 'height:100vh;margin:0;overflow:hidden';
            document.body.style.cssText = 'display:flex;flex-direction:row;height:100vh;margin:0;overflow:hidden';
            // Create left-column wrapper for map + affected-panel
            var wrapper = document.createElement('div');
            wrapper.id = 'main-column';
            mapDiv.parentNode.insertBefore(wrapper, mapDiv);
            wrapper.appendChild(mapDiv);
            wrapper.appendChild(panel);
            mapDiv.style.cssText += ';flex:0 0 65vh;height:65vh;position:relative;';
            panel.style.cssText += ';flex:0 0 35vh;height:35vh;';
            // Move controls to be direct body child (sidebar on right)
            if (controls) document.body.appendChild(controls);
            var map = getMap();
            if (map) setTimeout(function() {{ map.invalidateSize(); }}, 100);
        }}
        window.updateCommunityMap();
    }}, 500);
}})();
</script>
"""


# ---------------------------------------------------------------------------
# 9. Main
# ---------------------------------------------------------------------------
def main():
    """Run full school community analysis."""
    print("=" * 60)
    print("School Community Analysis")
    print("=" * 60)

    ensure_directories()

    # 1. Load schools
    print("\n[1/9] Loading school locations ...")
    schools = load_schools()

    # 2. Download district boundary
    print("\n[2/9] Loading district boundary ...")
    district_gdf = download_district_boundary(schools)
    district_polygon = district_gdf.union_all()

    # 2b. Load and dissolve elementary walk zones
    walk_zones_geojson = None
    walk_zones_gdf = None
    walk_zone_shp = PROJECT_ROOT / "data" / "raw" / "properties" / "CHCCS" / "CHCCS.shp"
    if walk_zone_shp.exists():
        _progress("Loading elementary walk zones from CHCCS shapefile ...")
        wz_raw = gpd.read_file(walk_zone_shp)
        wz_walk = wz_raw[wz_raw["ESWALK"] == "Y"].copy()
        if len(wz_walk) > 0:
            wz_walk = wz_walk.to_crs(CRS_WGS84)
            walk_zones_gdf = wz_walk.dissolve(by="ENAME").reset_index()
            walk_zones_gdf = walk_zones_gdf.rename(columns={"ENAME": "school_name"})
            # Build GeoJSON for JS
            features = []
            for _, row in walk_zones_gdf.iterrows():
                features.append({
                    "type": "Feature",
                    "geometry": json.loads(gpd.GeoSeries([row.geometry]).to_json())["features"][0]["geometry"],
                    "properties": {"school_name": row["school_name"]},
                })
            walk_zones_geojson = {"type": "FeatureCollection", "features": features}
            _progress(f"  Dissolved {len(wz_walk)} features -> {len(walk_zones_gdf)} walk zone polygons")
            for _, row in walk_zones_gdf.iterrows():
                _progress(f"    {row['school_name']}")
        else:
            _progress("  No ESWALK='Y' features found")
    else:
        print("  (Walk zone shapefile not found — skipping walk zone overlay)")

    # 3. Download/load road networks
    print("\n[3/9] Loading road networks ...")
    graphs = {}
    modes = ["drive", "bike", "walk"]
    for mode in modes:
        graphs[mode] = download_network(district_polygon, mode)

    # Build network GeoJSONs for map overlay
    network_geojsons = {}
    for mode in modes:
        network_geojsons[mode] = _graph_to_geojson(graphs[mode])
        _progress(f"  {mode}: {len(network_geojsons[mode]['features'])} edges for overlay")

    # 4. Compute travel times from each school
    print("\n[4/9] Computing school-outward travel times ...")
    travel_times_by_mode = {}
    for mode in modes:
        print(f"\n  --- {mode.upper()} (no cutoff) ---")
        travel_times_by_mode[mode] = compute_school_travel_times(
            graphs[mode], schools,
        )

    # 5. Create grid
    print("\n[5/9] Creating analysis grid ...")
    grid = create_grid(district_polygon)

    # Inject school locations as extra grid points so each school's
    # pixel gets the correct (near-zero) travel time.
    max_grid_id = grid["grid_id"].max()
    school_pts = gpd.GeoDataFrame(
        {
            "grid_id": range(max_grid_id + 1, max_grid_id + 1 + len(schools)),
            "lat": schools["lat"].values,
            "lon": schools["lon"].values,
        },
        geometry=gpd.points_from_xy(schools["lon"], schools["lat"]),
        crs=CRS_WGS84,
    )
    grid = pd.concat([grid, school_pts], ignore_index=True)
    school_anchor_ids = dict(zip(
        range(max_grid_id + 1, max_grid_id + 1 + len(schools)),
        schools["school"].values,
    ))
    _progress(f"Added {len(schools)} school locations as grid anchor points")

    # 6. Compute travel scores
    print("\n[6/9] Computing travel scores for all scenarios ...")
    scores_df = compute_travel_scores(grid, travel_times_by_mode, graphs, SCENARIOS)

    # School locations get zero travel time when open in the scenario
    for scenario_name, closed_schools in SCENARIOS.items():
        open_anchors = {gid: name for gid, name in school_anchor_ids.items()
                        if name not in closed_schools}
        mask = (scores_df["scenario"] == scenario_name) & scores_df["grid_id"].isin(open_anchors)
        scores_df.loc[mask, "min_time_seconds"] = 0.0
        scores_df.loc[mask, "nearest_school"] = (
            scores_df.loc[mask, "grid_id"].map(open_anchors)
        )
    n_zeroed = (scores_df["min_time_seconds"] == 0.0).sum()
    _progress(f"Zeroed travel time for {n_zeroed} school-anchor cells across all scenarios/modes")

    # 7. Compute delta (change from baseline)
    print("\n[7/9] Computing deltas ...")
    baseline = scores_df[scores_df["scenario"] == "baseline"][
        ["grid_id", "mode", "min_time_seconds"]
    ].rename(columns={"min_time_seconds": "baseline_time"})

    scores_df = scores_df.merge(baseline, on=["grid_id", "mode"], how="left")
    scores_df["delta_seconds"] = scores_df["min_time_seconds"] - scores_df["baseline_time"]
    scores_df["min_time_minutes"] = scores_df["min_time_seconds"] / 60.0
    scores_df["delta_minutes"] = scores_df["delta_seconds"] / 60.0

    # Save CSV
    csv_path = DATA_PROCESSED / "school_desert_grid.csv"
    scores_df.to_csv(csv_path, index=False)
    _progress(f"Saved grid data to {csv_path} ({len(scores_df)} rows)")

    # 8. Rasterize → GeoTIFF → PNG overlays
    print("\n[8/9] Rendering heatmaps (GeoTIFF -> PNG) ...")

    # Pre-compute a single pixel grid shared by all scenarios/modes.
    # Shift minlon so school markers sit closer to pixel centers on average,
    # reducing the perceived east-west displacement of the heatmap.
    unique_pts = scores_df[["lat", "lon"]].drop_duplicates()
    _all_lats = unique_pts["lat"].values
    _all_lons = unique_pts["lon"].values
    _center_lat = _all_lats.mean()
    _dlat = GRID_RESOLUTION_M / 111_320.0
    _dlon = GRID_RESOLUTION_M / (111_320.0 * np.cos(np.radians(_center_lat)))
    _minlon = _all_lons.min() - _dlon / 2
    _maxlat = _all_lats.max() + _dlat / 2
    _maxlon = _all_lons.max() + _dlon / 2
    _minlat = _all_lats.min() - _dlat / 2
    # Nudge minlon so the mean fractional x-offset of schools ≈ 0.5
    _school_fracs = ((schools["lon"].values - _minlon) / _dlon) % 1
    _minlon -= (0.5 - _school_fracs.mean()) * _dlon
    _ncols = int(np.ceil((_maxlon - _minlon) / _dlon))
    _nrows = int(np.ceil((_maxlat - _minlat) / _dlat))
    _maxlon = _minlon + _ncols * _dlon
    _minlat = _maxlat - _nrows * _dlat
    shared_grid_params = {
        "minlon": _minlon, "maxlon": _maxlon,
        "minlat": _minlat, "maxlat": _maxlat,
        "ncols": _ncols, "nrows": _nrows,
        "dlat": _dlat, "dlon": _dlon,
    }

    heatmap_data = {}    # (scenario, mode, layer_type) → (b64_png, bounds)
    hover_grids = {}     # "scenario|mode|type" → base64 Float32Array
    grid_meta = None
    common_bounds = None
    TIFF_DIR.mkdir(parents=True, exist_ok=True)

    for scenario in SCENARIOS:
        for mode in modes:
            subset = scores_df[
                (scores_df["scenario"] == scenario) & (scores_df["mode"] == mode)
            ].copy()

            # --- Absolute time layer ---
            vmin, vmax = MODE_RANGES[mode]["abs"]
            vals_2d, meta, bounds = rasterize_grid(subset, "min_time_minutes",
                                                      district_polygon=district_polygon,
                                                      grid_params=shared_grid_params)
            if meta is not None and grid_meta is None:
                grid_meta = meta
            if bounds is not None and common_bounds is None:
                common_bounds = bounds

            # Save GeoTIFF
            if vals_2d is not None:
                tiff_path = TIFF_DIR / f"{scenario}_{mode}_abs.tif"
                save_geotiff(vals_2d, meta, tiff_path)
                hover_grids[f"{scenario}|{mode}|abs"] = encode_value_grid(vals_2d)

            b64 = colorize_raster(vals_2d, vmin, vmax, "RdYlGn_r")
            heatmap_data[(scenario, mode, "abs")] = (b64, bounds)

            # --- Delta layer (skip baseline) ---
            if scenario != "baseline":
                vmin_d, vmax_d = MODE_RANGES[mode]["delta"]
                vals_d, meta_d, bounds_d = rasterize_grid(subset, "delta_minutes",
                                                            district_polygon=district_polygon,
                                                            grid_params=shared_grid_params)
                if vals_d is not None:
                    tiff_path_d = TIFF_DIR / f"{scenario}_{mode}_delta.tif"
                    save_geotiff(vals_d, meta_d, tiff_path_d)
                    hover_grids[f"{scenario}|{mode}|delta"] = encode_value_grid(vals_d)
                b64_d = colorize_raster(vals_d, vmin_d, vmax_d, "Oranges")
                heatmap_data[(scenario, mode, "delta")] = (b64_d, bounds_d)
            else:
                heatmap_data[(scenario, mode, "delta")] = (None, None)

            _progress(f"  Rendered {scenario} / {mode}")

    _progress(f"GeoTIFFs saved to {TIFF_DIR}")

    # 9. Load residential property centroids (optional layer)
    print("\n[9/10] Loading residential property centroids ...")
    property_points = []
    centroids_path = PROJECT_ROOT / "data" / "raw" / "properties" / "combined_data_centroids.gpkg"
    if centroids_path.exists():
        centroids_gdf = gpd.read_file(centroids_path)
        # Clip to district boundary
        centroids_gdf = gpd.clip(centroids_gdf, district_gdf.to_crs(centroids_gdf.crs))
        for _, row in centroids_gdf.iterrows():
            pt = row.geometry
            sale_dt = row.get("sale_date")
            sale_str = str(sale_dt.date()) if pd.notna(sale_dt) else None
            property_points.append({
                "lat": round(pt.y, 6),
                "lon": round(pt.x, 6),
                "pin": row.get("PIN"),
                "luc": row.get("primary_luc"),
                "imp_vac": row.get("imp_vac"),
                "valuation": int(row["VALUATION"]) if pd.notna(row.get("VALUATION")) else None,
                "sqft": int(row["SQFT"]) if pd.notna(row.get("SQFT")) and row.get("SQFT") else None,
                "year_built": int(row["YEARBUILT"]) if pd.notna(row.get("YEARBUILT")) and row.get("YEARBUILT") else None,
                "acres": round(float(row["CALC_ACRES"]), 2) if pd.notna(row.get("CALC_ACRES")) else None,
                "sale_date": sale_str,
                "sale_price": int(row["sale_price"]) if pd.notna(row.get("sale_price")) else None,
                "years_since_sale": int(row["years_since_sale"]) if pd.notna(row.get("years_since_sale")) else None,
                "subdivision": row.get("SUBDIVISIO") if pd.notna(row.get("SUBDIVISIO")) else None,
                "condo_name": row.get("CONDONAME") if pd.notna(row.get("CONDONAME")) else None,
                "appraised_value": int(row["appraised_value"]) if pd.notna(row.get("appraised_value")) else None,
            })
        _progress(f"  Loaded {len(property_points):,} residential centroids")
    else:
        centroids_gdf = None
        print("  (No centroids file found — run property_data.py first)")

    # 10. Snap property centroids to grid → compute affected-household data
    affected_charts = None
    if centroids_gdf is not None and len(centroids_gdf) > 0:
        print("\n[10/11] Computing affected-household histograms ...")
        # Build cKDTree from grid lat/lon (with cosine-latitude scaling)
        grid_lats_arr = grid["lat"].values
        grid_lons_arr = grid["lon"].values
        grid_ids_arr = grid["grid_id"].values
        _mean_lat = grid_lats_arr.mean()
        _cos_lat = np.cos(np.radians(_mean_lat))
        grid_coords_scaled = np.column_stack([grid_lons_arr * _cos_lat, grid_lats_arr])
        grid_tree = cKDTree(grid_coords_scaled)

        # Query each property centroid
        prop_lats = np.array([g.y for g in centroids_gdf.geometry])
        prop_lons = np.array([g.x for g in centroids_gdf.geometry])
        prop_scaled = np.column_stack([prop_lons * _cos_lat, prop_lats])
        _, nearest_idx = grid_tree.query(prop_scaled)
        centroids_gdf = centroids_gdf.copy()
        centroids_gdf["grid_id"] = grid_ids_arr[nearest_idx]
        _progress(f"  Snapped {len(centroids_gdf):,} centroids to nearest grid points")

        # Compute affected parcels per (scenario, mode)
        affected_data = {}
        for scenario_name, closed_schools in SCENARIOS.items():
            if scenario_name == "baseline":
                continue
            for mode in modes:
                mask = (
                    (scores_df["scenario"] == scenario_name)
                    & (scores_df["mode"] == mode)
                    & (scores_df["delta_minutes"] > 0)
                )
                affected_grid_ids = set(scores_df.loc[mask, "grid_id"].values)
                affected_parcels = centroids_gdf[centroids_gdf["grid_id"].isin(affected_grid_ids)]
                count = len(affected_parcels)
                values = affected_parcels["assessed_value"].dropna().values
                years = affected_parcels["years_since_sale"].dropna().values
                key = f"{scenario_name}|{mode}"
                affected_data[key] = {"count": count, "values": values, "years": years}
                _progress(f"  {key}: {count:,} affected parcels")

        affected_charts = _render_affected_charts(affected_data)
        _progress(f"  Rendered {len(affected_charts)} chart sets")

    # 10b. Walk-zone household histograms
    walk_zone_charts = None
    if centroids_gdf is not None and len(centroids_gdf) > 0 and walk_zones_gdf is not None:
        print("\n[10b/11] Computing walk-zone household histograms ...")
        # Spatial join centroids against walk-zone polygons
        centroids_wgs = centroids_gdf.to_crs(CRS_WGS84) if centroids_gdf.crs != CRS_WGS84 else centroids_gdf
        wz_joined = gpd.sjoin(centroids_wgs, walk_zones_gdf[["school_name", "geometry"]], how="left", predicate="within")
        _progress(f"  {wz_joined['school_name'].notna().sum():,} parcels fall within a walk zone")

        walk_zone_data = {}
        for scenario_name, closed_schools in SCENARIOS.items():
            if scenario_name == "baseline":
                continue
            # Parcels in the walk zone of any closed school
            wz_parcels = wz_joined[wz_joined["school_name"].isin(closed_schools)]
            count = len(wz_parcels)
            if count > 0:
                values = wz_parcels["assessed_value"].dropna().values
                years = wz_parcels["years_since_sale"].dropna().values
            else:
                values = np.array([])
                years = np.array([])
            walk_zone_data[scenario_name] = {"count": count, "values": values, "years": years}
            _progress(f"  {scenario_name}: {count:,} walk-zone parcels")

        walk_zone_charts = _render_affected_charts(walk_zone_data, style="walkzone")
        _progress(f"  Rendered {len(walk_zone_charts)} walk-zone chart sets")

    # 11. Create interactive map
    print("\n[11/11] Creating interactive map ...")
    m = create_map(heatmap_data, schools, district_gdf, common_bounds,
                   hover_grids, grid_meta, network_geojsons,
                   property_points=property_points,
                   affected_charts=affected_charts,
                   walk_zones_geojson=walk_zones_geojson,
                   walk_zone_charts=walk_zone_charts)

    map_path = ASSETS_MAPS / "school_community_map.html"
    m.save(str(map_path))
    _progress(f"Saved map to {map_path}")

    # Summary stats
    print("\n" + "=" * 60)
    print("Analysis complete!")
    print(f"  Map: {map_path}")
    print(f"  Data: {csv_path}")
    print(f"  Grid points: {len(grid)}")
    print(f"  Scenarios: {len(SCENARIOS)}")
    print(f"  Heatmap images: {sum(1 for v in heatmap_data.values() if v[0] is not None)}")

    print("=" * 60)


if __name__ == "__main__":
    main()
