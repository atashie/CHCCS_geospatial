"""
School Closure Impact Analysis for CHCCS Elementary Schools

Comprehensive analysis answering two questions:
1. Access impact: How much farther must families travel if a school closes?
2. Traffic impact: How does school closure redistribute vehicle traffic?

Part 1 — School Desert Impacts (re-implementation):
  Re-implements the travel-time infrastructure from school_desert.py using
  dijkstra_predecessor_and_distance() instead of single_source_dijkstra_path_length(),
  enabling full route extraction while maintaining identical travel-time results.
  Vectorizes pixel assignment with NumPy for ~10x speedup over the Python-loop approach.

Part 2 — Traffic Network Impacts (new):
  Distributes ACS children counts (ages 0-4 and 5-9) to grid pixels via dasymetric
  downscaling, then reconstructs driving routes from predecessor maps to aggregate
  children-weighted traffic on each road segment. Supports:
  - Walk zone masking (exclude pixels inside walk zones from traffic)
  - Zone-restricted routing (route to assigned attendance zone school, not nearest)
  - Difference views (closure traffic minus baseline traffic)

Speed model sources:
- Walk: MUTCD Section 4E.06 / Fitzpatrick et al. (2006, FHWA-HRT-06-042).
  2.5 mph (3.67 ft/s) — mid-range for K-5 children.
- Drive: HCM6 Ch.16 Urban Street Facilities, FHWA Urban Arterial Speed Studies.
  Effective/posted ratios: ~65% residential, ~71% secondary, ~73% primary/trunk.
- Edge snapping: Shapely STRtree nearest-edge with fractional interpolation
  (identical to school_desert.py methodology).

Data sources:
- Road networks: OpenStreetMap via OSMnx
- School locations: NCES EDGE Public School Locations 2023-24
- District boundary: Census TIGER/Line Unified School Districts 2023
- Children counts: ACS 5-Year B01001 (block group level)
- Block geometries: TIGER/Line 2020 Census blocks
- Residential parcels: Orange County GIS (combined_data_polys.gpkg)
- Walk/attendance zones: CHCCS.shp (ESWALK + ENAME dissolve)

Outputs:
- assets/maps/school_closure_analysis.html — Interactive map with all layers
- data/processed/school_closure_assignments.csv — Per-pixel travel assignments
- data/processed/school_closure_traffic.csv — Per-edge traffic aggregation

Assumptions & limitations:
- Static speeds; no real-time traffic or turn penalties.
- All remaining schools absorb displaced students (no capacity constraints).
- Children distribution uses dasymetric area weighting (residential parcels).
- Traffic analysis is drive-mode only (bike/walk traffic is negligible).
- Predecessor maps consume ~4 MB total (O(V) per run, 33 runs).
- "Zone-restricted" mode uses CHCCS attendance zone assignments; edge effects
  at zone boundaries may cause some pixels to fall outside all zones.
"""

import argparse
import base64
import io
import json
import math
import os
import pickle
import sys
import tempfile
import warnings
import zipfile
from dataclasses import dataclass, field
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
import requests
import shapely
from scipy.ndimage import uniform_filter
from scipy.spatial import cKDTree
from shapely.geometry import LineString, Point, box
from shapely.prepared import prep

warnings.filterwarnings("ignore", category=FutureWarning)
matplotlib.use("Agg")

# ---------------------------------------------------------------------------
# Paths
# ---------------------------------------------------------------------------
PROJECT_ROOT = Path(__file__).parent.parent
DATA_RAW = PROJECT_ROOT / "data" / "raw"
DATA_PROCESSED = PROJECT_ROOT / "data" / "processed"
DATA_CACHE = PROJECT_ROOT / "data" / "cache"
CLOSURE_CACHE = DATA_CACHE / "closure_analysis"
ASSETS_MAPS = PROJECT_ROOT / "assets" / "maps"

SCHOOL_CSV = DATA_CACHE / "nces_school_locations.csv"
DISTRICT_CACHE = DATA_CACHE / "chccs_district_boundary.gpkg"
CHCCS_SHP = DATA_RAW / "properties" / "CHCCS" / "CHCCS.shp"
PARCEL_POLYS = DATA_RAW / "properties" / "combined_data_polys.gpkg"

# ---------------------------------------------------------------------------
# Constants — identical to school_desert.py for consistency
# ---------------------------------------------------------------------------
# Travel speeds
WALK_SPEED_MPS = 1.12   # 2.5 mph — K-5 children
BIKE_SPEED_MPS = 5.36   # 12 mph
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
ACCESS_SPEED_FACTORS = {"walk": 0.9, "bike": 0.8, "drive": 0.2}

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

# Closure scenarios
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

# ENAME → standard school name mapping (from school_socioeconomic_analysis.py)
_ENAME_TO_SCHOOL = {
    "Carrboro Elementary": "Carrboro Elementary",
    "Ephesus Elementary": "Ephesus Elementary",
    "Estes Hills Elementary": "Estes Hills Elementary",
    "Frank Porter Graham Bilingue": "Frank Porter Graham Bilingue",
    "Frank Porter Graham Elementary": "Frank Porter Graham Bilingue",
    "FPG Bilingue": "Frank Porter Graham Bilingue",
    "Glenwood Elementary": "Glenwood Elementary",
    "McDougle Elementary": "McDougle Elementary",
    "Morris Grove Elementary": "Morris Grove Elementary",
    "Northside Elementary": "Northside Elementary",
    "Rashkis Elementary": "Rashkis Elementary",
    "Scroggs Elementary": "Scroggs Elementary",
    "Seawell Elementary": "Seawell Elementary",
}

# Census — Orange County, NC (37 = NC, 135 = Orange County)
# Note: 063 is Durham County — a common mistake
STATE_FIPS = "37"
COUNTY_FIPS = "135"
ACS_BASE_URL = "https://api.census.gov/data/2022/acs/acs5"
TIGER_BG_URL = "https://www2.census.gov/geo/tiger/TIGER2023/BG/tl_2023_37_bg.zip"
TIGER_BLOCK_URL = (
    "https://www2.census.gov/geo/tiger/TIGER2020PL/STATE/"
    "37_NORTH_CAROLINA/37135/tl_2020_37135_tabblock20.zip"
)


# ---------------------------------------------------------------------------
# Data classes
# ---------------------------------------------------------------------------
@dataclass
class SnapResult:
    """Edge-snapping results for a set of grid points."""
    start_nodes: np.ndarray    # node ID at fraction=0 of matched edge
    end_nodes: np.ndarray      # node ID at fraction=1 of matched edge
    fractions: np.ndarray      # fractional position along edge (0..1)
    edge_times: np.ndarray     # travel_time of the matched edge (seconds)
    access_times: np.ndarray   # off-network access time (seconds)
    reachable: np.ndarray      # bool mask — True if within max_access_m


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------
def _progress(msg: str):
    print(f"  ... {msg}")


def ensure_directories():
    for d in [DATA_PROCESSED, DATA_CACHE, CLOSURE_CACHE, ASSETS_MAPS]:
        d.mkdir(parents=True, exist_ok=True)


# Grid algorithm version — bump when create_grid() changes to auto-invalidate
# grid-dependent caches. Dijkstra caches (per-node distances) and
# children_blockgroups.csv (Census data) are grid-independent and kept.
_GRID_VERSION = "2"  # v2 = WGS84-native grid (was v1 = UTM grid)

_GRID_DEPENDENT_CACHES = [
    "pixel_grid.csv",
    "snap_drive.npz", "snap_bike.npz", "snap_walk.npz",
    "pixel_children.csv",
    "pixel_walk_zone_assignments.csv",
    "pixel_zone_assignments.csv",
]


def _check_grid_version():
    """Auto-invalidate grid-dependent caches when the grid algorithm changes."""
    version_file = CLOSURE_CACHE / "grid_version.txt"
    if version_file.exists():
        existing = version_file.read_text().strip()
        if existing == _GRID_VERSION:
            return  # up to date
        _progress(f"Grid version changed ({existing} → {_GRID_VERSION}), clearing grid-dependent caches")
    else:
        # First run with versioning — check if old caches exist
        if (CLOSURE_CACHE / "pixel_grid.csv").exists():
            _progress(f"Adding grid version sentinel, clearing old grid-dependent caches")

    for name in _GRID_DEPENDENT_CACHES:
        path = CLOSURE_CACHE / name
        if path.exists():
            path.unlink()
            _progress(f"  Deleted: {name}")

    version_file.write_text(_GRID_VERSION)


def _get_census_api_key():
    """Get Census API key from environment or .env file."""
    key = os.environ.get("CENSUS_API_KEY")
    if key:
        return key
    env_file = PROJECT_ROOT / ".env"
    if env_file.exists():
        for line in env_file.read_text().splitlines():
            line = line.strip()
            if line.startswith("CENSUS_API_KEY="):
                return line.split("=", 1)[1].strip().strip('"').strip("'")
    return None


# ═══════════════════════════════════════════════════════════════════════════
# Section 1: Data Loading
# ═══════════════════════════════════════════════════════════════════════════

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


def load_district_boundary(schools: gpd.GeoDataFrame) -> gpd.GeoDataFrame:
    """Load district boundary (must be pre-cached by school_desert.py)."""
    if DISTRICT_CACHE.exists():
        _progress(f"Loading cached district boundary from {DISTRICT_CACHE}")
        return gpd.read_file(DISTRICT_CACHE)

    # Fallback: convex hull with 3km buffer
    _progress("District boundary not cached — creating convex hull fallback")
    schools_utm = schools.to_crs(CRS_UTM17N)
    hull = schools_utm.union_all().convex_hull
    buffered = hull.buffer(3000)
    gdf = gpd.GeoDataFrame(geometry=[buffered], crs=CRS_UTM17N).to_crs(CRS_WGS84)
    return gdf


def load_walk_zones() -> gpd.GeoDataFrame | None:
    """Load walk zone polygons from CHCCS shapefile (ESWALK=='Y')."""
    if not CHCCS_SHP.exists():
        _progress("Walk zone shapefile not found")
        return None

    raw = gpd.read_file(CHCCS_SHP).to_crs(CRS_WGS84)
    walk = raw[raw["ESWALK"] == "Y"].copy()
    if walk.empty:
        _progress("No walk-eligible features found (ESWALK=='Y')")
        return None

    walk = walk.dissolve(by="ENAME").reset_index()
    walk["school"] = walk["ENAME"].map(_ENAME_TO_SCHOOL)
    walk = walk[walk["school"].notna()][["school", "geometry"]].copy()
    _progress(f"Loaded {len(walk)} walk zones")
    return walk


def load_attendance_zones() -> gpd.GeoDataFrame | None:
    """Load attendance zones from CHCCS shapefile, dissolve by ENAME."""
    if not CHCCS_SHP.exists():
        _progress(f"Attendance zone shapefile not found at {CHCCS_SHP}")
        return None

    _progress("Loading attendance zones from CHCCS shapefile ...")
    raw = gpd.read_file(CHCCS_SHP).to_crs(CRS_WGS84)
    zones = raw.dissolve(by="ENAME").reset_index()
    zones["school"] = zones["ENAME"].map(_ENAME_TO_SCHOOL)

    unmapped = zones[zones["school"].isna()]
    if len(unmapped) > 0:
        for _, row in unmapped.iterrows():
            _progress(f"  WARNING: Unmapped ENAME '{row['ENAME']}' — skipping")
        zones = zones[zones["school"].notna()].copy()

    zones = zones[["school", "ENAME", "geometry"]].copy()
    _progress(f"Loaded {len(zones)} attendance zones")
    return zones


# ═══════════════════════════════════════════════════════════════════════════
# Section 2: Network Loading and Graph Utilities
# ═══════════════════════════════════════════════════════════════════════════

def _add_travel_time_weights(G: nx.MultiDiGraph, mode: str):
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
            speed_mps = speed_mph * 0.44704
            data["travel_time"] = length_m / speed_mps if speed_mps > 0 else 9999


def _ensure_bidirectional(G: nx.MultiDiGraph):
    """Add reverse edges where missing."""
    edges_to_add = []
    for u, v, key, data in G.edges(keys=True, data=True):
        if not G.has_edge(v, u):
            edges_to_add.append((v, u, data.copy()))
    for v, u, data in edges_to_add:
        G.add_edge(v, u, **data)


def load_network(mode: str) -> nx.MultiDiGraph:
    """Load cached road network graph, add travel_time weights."""
    cache_path = DATA_CACHE / f"network_{mode}.graphml"
    if not cache_path.exists():
        raise FileNotFoundError(
            f"Network graph not found at {cache_path}. "
            "Run school_desert.py first to download networks."
        )

    _progress(f"Loading cached {mode} network from {cache_path}")
    G = ox.load_graphml(cache_path)
    _add_travel_time_weights(G, mode)
    n_before = G.number_of_edges()
    _ensure_bidirectional(G)
    n_added = G.number_of_edges() - n_before
    if n_added:
        _progress(f"  Added {n_added} reverse edges for bidirectional {mode} network")
    _progress(f"  {mode}: {G.number_of_nodes()} nodes, {G.number_of_edges()} edges")
    return G


def _build_node_index(G: nx.MultiDiGraph):
    """Build cKDTree spatial index for graph nodes."""
    node_ids = list(G.nodes())
    raw_coords = np.array([(G.nodes[n]["x"], G.nodes[n]["y"]) for n in node_ids])
    mean_lat = raw_coords[:, 1].mean()
    cos_lat = np.cos(np.radians(mean_lat))
    scaled_coords = np.column_stack([raw_coords[:, 0] * cos_lat, raw_coords[:, 1]])
    tree = cKDTree(scaled_coords)
    return node_ids, tree, cos_lat


def _nearest_node(node_ids, tree, lon, lat, cos_lat):
    """Find nearest graph node to (lon, lat)."""
    _, idx = tree.query([lon * cos_lat, lat])
    return node_ids[idx]


def _build_edge_index(G: nx.MultiDiGraph) -> dict:
    """Build Shapely STRtree spatial index over deduplicated edge geometries."""
    lats = [G.nodes[n]["y"] for n in G.nodes()]
    mean_lat = np.mean(lats)
    cos_lat = np.cos(np.radians(mean_lat))

    seen = set()
    scaled_geoms = []
    start_nodes = []
    end_nodes = []
    edge_times = []
    orig_u_list = []
    orig_v_list = []

    for u, v, key, data in G.edges(keys=True, data=True):
        canon = (min(u, v), max(u, v), key)
        if canon in seen:
            continue
        seen.add(canon)

        if "geometry" in data:
            geom = data["geometry"]
        else:
            u_x, u_y = G.nodes[u]["x"], G.nodes[u]["y"]
            v_x, v_y = G.nodes[v]["x"], G.nodes[v]["y"]
            geom = LineString([(u_x, u_y), (v_x, v_y)])

        g0 = geom.coords[0]
        u_x, u_y = G.nodes[u]["x"], G.nodes[u]["y"]
        if abs(g0[0] - u_x) + abs(g0[1] - u_y) < 1e-8:
            s_node, e_node = u, v
        else:
            s_node, e_node = v, u

        scaled = shapely.transform(geom, lambda c: c * [[cos_lat, 1]])
        scaled_geoms.append(scaled)
        start_nodes.append(s_node)
        end_nodes.append(e_node)
        edge_times.append(data.get("travel_time", 0.0))
        orig_u_list.append(u)
        orig_v_list.append(v)

    tree = shapely.STRtree(scaled_geoms)
    return {
        "tree": tree,
        "scaled_geoms": scaled_geoms,
        "start_nodes": np.array(start_nodes),
        "end_nodes": np.array(end_nodes),
        "edge_times": np.array(edge_times, dtype=np.float64),
        "cos_lat": cos_lat,
        "orig_u": np.array(orig_u_list),
        "orig_v": np.array(orig_v_list),
    }


def _graph_to_geojson_with_ids(G: nx.MultiDiGraph) -> tuple[dict, dict]:
    """Convert graph edges to GeoJSON with edge IDs for traffic overlay.

    Returns:
        (geojson_dict, edge_id_map)
        edge_id_map: {canonical_edge_tuple: feature_index}
    """
    seen = set()
    features = []
    edge_id_map = {}
    idx = 0

    for u, v, data in G.edges(data=True):
        edge_key = (min(u, v), max(u, v))
        if edge_key in seen:
            continue
        seen.add(edge_key)

        if "geometry" in data:
            coords = [[round(c[0], 5), round(c[1], 5)]
                      for c in data["geometry"].coords]
        else:
            u_x = round(G.nodes[u]["x"], 5)
            u_y = round(G.nodes[u]["y"], 5)
            v_x = round(G.nodes[v]["x"], 5)
            v_y = round(G.nodes[v]["y"], 5)
            coords = [[u_x, u_y], [v_x, v_y]]

        highway = data.get("highway", "")
        if isinstance(highway, list):
            highway = highway[0]
        name = data.get("name", "")
        if isinstance(name, list):
            name = name[0]

        features.append({
            "type": "Feature",
            "geometry": {"type": "LineString", "coordinates": coords},
            "properties": {
                "idx": idx,
                "highway": highway,
                "name": str(name) if name else "",
            },
        })
        edge_id_map[edge_key] = idx
        idx += 1

    geojson = {"type": "FeatureCollection", "features": features}
    return geojson, edge_id_map


def _graph_to_display_geojson(G: nx.MultiDiGraph) -> dict:
    """Convert graph edges to lightweight GeoJSON for display only (no properties).

    Used for bike/walk network overlays in Part 1.  Strips all feature
    properties to minimise serialised size (~30-40 % smaller than the
    full-property variant).
    """
    seen: set[tuple[int, int]] = set()
    features: list[dict] = []

    for u, v, data in G.edges(data=True):
        edge_key = (min(u, v), max(u, v))
        if edge_key in seen:
            continue
        seen.add(edge_key)

        if "geometry" in data:
            coords = [[round(c[0], 5), round(c[1], 5)]
                      for c in data["geometry"].coords]
        else:
            coords = [
                [round(G.nodes[u]["x"], 5), round(G.nodes[u]["y"], 5)],
                [round(G.nodes[v]["x"], 5), round(G.nodes[v]["y"], 5)],
            ]

        features.append({
            "type": "Feature",
            "geometry": {"type": "LineString", "coordinates": coords},
            "properties": {},
        })

    return {"type": "FeatureCollection", "features": features}


# ═══════════════════════════════════════════════════════════════════════════
# Section 3: Grid Creation and Edge Snapping
# ═══════════════════════════════════════════════════════════════════════════

def create_grid(district_polygon, resolution_m: int = GRID_RESOLUTION_M) -> gpd.GeoDataFrame:
    """Create a regular WGS84 point grid over the district at given resolution.

    Grid is built directly in WGS84 using latitude-corrected degree spacing,
    matching the proven approach in road_pollution.py and environmental_map.py.
    This eliminates the ~1° convergence-angle rotation that occurs when creating
    a grid in UTM and reprojecting to WGS84.
    """
    center_lat = (district_polygon.bounds[1] + district_polygon.bounds[3]) / 2
    dlat = resolution_m / 111_320.0
    dlon = resolution_m / (111_320.0 * np.cos(np.radians(center_lat)))

    minlon, minlat, maxlon, maxlat = district_polygon.bounds
    lons = np.arange(minlon, maxlon, dlon)
    lats = np.arange(minlat, maxlat, dlat)

    prepared = prep(district_polygon)
    points = []
    grid_ids = []
    idx = 0
    for lon in lons:
        for lat in lats:
            pt = Point(lon, lat)
            if prepared.contains(pt):
                points.append(pt)
                grid_ids.append(idx)
                idx += 1

    _progress(f"Created grid with {len(points)} points at {resolution_m}m resolution")

    gdf = gpd.GeoDataFrame(
        {"grid_id": grid_ids}, geometry=points, crs=CRS_WGS84,
    )
    gdf["lat"] = gdf.geometry.y
    gdf["lon"] = gdf.geometry.x
    return gdf


def snap_grid_to_edges(
    grid: gpd.GeoDataFrame, G: nx.MultiDiGraph, mode: str,
) -> SnapResult:
    """Batch-snap all grid points to nearest edges via STRtree."""
    cache_path = CLOSURE_CACHE / f"snap_{mode}.npz"
    if cache_path.exists():
        _progress(f"Loading cached edge-snapping: {cache_path.name}")
        data = np.load(cache_path, allow_pickle=True)
        return SnapResult(
            start_nodes=data["start_nodes"],
            end_nodes=data["end_nodes"],
            fractions=data["fractions"],
            edge_times=data["edge_times"],
            access_times=data["access_times"],
            reachable=data["reachable"],
        )

    _progress(f"Snapping {len(grid)} grid points to {mode} network edges ...")
    eidx = _build_edge_index(G)
    cos_lat = eidx["cos_lat"]
    grid_lons = grid["lon"].values
    grid_lats = grid["lat"].values

    # Batch query: nearest edge for every grid point
    query_pts = shapely.points(grid_lons * cos_lat, grid_lats)
    nearest_ei = eidx["tree"].nearest(query_pts)

    # Vectorized perpendicular distance
    matched_geoms = np.array(eidx["scaled_geoms"], dtype=object)[nearest_ei]
    access_dist_m = shapely.distance(query_pts, matched_geoms) * 111_320.0

    # Fractional position along matched edge
    snap_fracs = shapely.line_locate_point(matched_geoms, query_pts, normalized=True)

    # Endpoint IDs and edge travel times
    snap_start = eidx["start_nodes"][nearest_ei]
    snap_end = eidx["end_nodes"][nearest_ei]
    snap_etime = eidx["edge_times"][nearest_ei]

    # Access-leg time
    modal_speed = {
        "walk": WALK_SPEED_MPS,
        "bike": BIKE_SPEED_MPS,
        "drive": DEFAULT_DRIVE_EFFECTIVE_MPH * 0.44704,
    }[mode]
    access_speed = ACCESS_SPEED_FACTORS[mode] * modal_speed
    max_access_m = 2 * GRID_RESOLUTION_M

    access_times = access_dist_m / access_speed
    reachable = access_dist_m <= max_access_m

    result = SnapResult(
        start_nodes=snap_start,
        end_nodes=snap_end,
        fractions=snap_fracs,
        edge_times=snap_etime,
        access_times=access_times,
        reachable=reachable,
    )

    # Cache
    np.savez_compressed(
        cache_path,
        start_nodes=snap_start, end_nodes=snap_end,
        fractions=snap_fracs, edge_times=snap_etime,
        access_times=access_times, reachable=reachable,
    )
    _progress(f"  Snapped and cached to {cache_path.name}")
    return result


# ═══════════════════════════════════════════════════════════════════════════
# Section 4: Dijkstra with Predecessors
# ═══════════════════════════════════════════════════════════════════════════

def compute_dijkstra_with_predecessors(
    G: nx.MultiDiGraph,
    schools: gpd.GeoDataFrame,
    mode: str,
) -> dict:
    """Run Dijkstra from each school, returning both distances AND predecessors.

    Returns:
        {school_name: {"pred": pred_dict, "dist": dist_dict, "source_node": node_id}}
    """
    cache_path = CLOSURE_CACHE / f"dijkstra_{mode}.pkl"
    if cache_path.exists():
        _progress(f"Loading cached Dijkstra results: {cache_path.name}")
        with open(cache_path, "rb") as f:
            return pickle.load(f)

    _progress(f"Running Dijkstra with predecessors for {mode} ...")
    node_ids, tree, cos_lat = _build_node_index(G)
    results = {}

    for _, row in schools.iterrows():
        name = row["school"]
        source_node = _nearest_node(node_ids, tree, row.geometry.x, row.geometry.y, cos_lat)
        pred, dist = nx.dijkstra_predecessor_and_distance(
            G, source_node, weight="travel_time"
        )
        results[name] = {
            "pred": dict(pred),
            "dist": dict(dist),
            "source_node": source_node,
        }
        _progress(f"  {name}: reached {len(dist)} nodes")

    # Cache
    with open(cache_path, "wb") as f:
        pickle.dump(results, f, protocol=pickle.HIGHEST_PROTOCOL)
    _progress(f"  Cached Dijkstra results to {cache_path.name}")
    return results


# ═══════════════════════════════════════════════════════════════════════════
# Section 5: Vectorized Pixel Assignment
# ═══════════════════════════════════════════════════════════════════════════

def assign_pixels_to_schools(
    snap: SnapResult,
    dijkstra_results: dict,
    open_schools: list[str],
) -> tuple[np.ndarray, np.ndarray, np.ndarray]:
    """Vectorized nearest-school assignment for all pixels.

    Returns:
        (min_times, nearest_schools, entry_nodes)
        min_times: float array of travel times in seconds (inf if unreachable)
        nearest_schools: array of school name strings
        entry_nodes: array of graph node IDs used to enter network (for route reconstruction)
    """
    n_points = len(snap.start_nodes)
    best_times = np.full(n_points, np.inf)
    best_schools = np.empty(n_points, dtype=object)
    best_entry = np.zeros(n_points, dtype=np.int64)

    for school_name in open_schools:
        dij = dijkstra_results[school_name]
        dist = dij["dist"]

        # Vectorized distance lookup
        t_u = np.array([dist.get(u, np.inf) for u in snap.start_nodes])
        t_v = np.array([dist.get(v, np.inf) for v in snap.end_nodes])

        # Interpolate via each endpoint
        via_u = t_u + snap.fractions * snap.edge_times
        via_v = t_v + (1.0 - snap.fractions) * snap.edge_times

        # Choose better endpoint
        use_u = via_u <= via_v
        best_via = np.where(use_u, via_u, via_v)
        entry = np.where(use_u, snap.start_nodes, snap.end_nodes)

        total = best_via + snap.access_times

        # Update best
        improved = total < best_times
        improved &= snap.reachable
        best_times[improved] = total[improved]
        best_schools[improved] = school_name
        best_entry[improved] = entry[improved]

    return best_times, best_schools, best_entry


# ═══════════════════════════════════════════════════════════════════════════
# Section 6: School Zone Polygon Generation
# ═══════════════════════════════════════════════════════════════════════════

def build_zone_polygons(
    grid: gpd.GeoDataFrame,
    nearest_schools: np.ndarray,
    reachable: np.ndarray,
    district_gdf: gpd.GeoDataFrame,
) -> gpd.GeoDataFrame | None:
    """Convert pixel nearest-school assignments to dissolved zone polygons."""
    df = grid[["grid_id", "lon", "lat"]].copy()
    df["nearest_school"] = nearest_schools
    df = df[reachable & (df["nearest_school"] != None)].copy()
    if df.empty:
        return None

    pts = gpd.GeoDataFrame(
        df, geometry=gpd.points_from_xy(df["lon"], df["lat"]), crs=CRS_WGS84,
    ).to_crs(CRS_UTM17N)

    half = 55  # slightly over half of 100m grid cell
    pts["geometry"] = [box(g.x - half, g.y - half, g.x + half, g.y + half)
                       for g in pts.geometry]
    dissolved = pts.dissolve(by="nearest_school").reset_index()
    dissolved = dissolved.rename(columns={"nearest_school": "school"})

    dist_utm = district_gdf.to_crs(CRS_UTM17N)
    dissolved = gpd.clip(dissolved, dist_utm)
    mask = dissolved.geometry.geom_type.isin(["Polygon", "MultiPolygon"])
    dissolved = dissolved[mask].copy()
    dissolved = dissolved[["school", "geometry"]].to_crs(CRS_WGS84)
    return dissolved


# ═══════════════════════════════════════════════════════════════════════════
# Section 7: Rasterization
# ═══════════════════════════════════════════════════════════════════════════

def rasterize_grid(
    grid_df: pd.DataFrame,
    value_column: str,
    resolution_m: int = GRID_RESOLUTION_M,
    district_polygon=None,
    grid_params: dict = None,
) -> tuple:
    """Convert grid points to a 2D value raster in WGS84 space.

    Returns:
        (values_2d, grid_meta, bounds) or (None, None, None) if no valid data.
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
        center_lat = lats.mean()
        dlat = resolution_m / 111_320.0
        dlon = resolution_m / (111_320.0 * np.cos(np.radians(center_lat)))
        minlon = lons.min() - dlon / 2
        maxlon = lons.max() + dlon / 2
        minlat = lats.min() - dlat / 2
        maxlat = lats.max() + dlat / 2
        ncols = int(np.ceil((maxlon - minlon) / dlon))
        nrows = int(np.ceil((maxlat - minlat) / dlat))
        maxlon = minlon + ncols * dlon
        minlat = maxlat - nrows * dlat

    values_2d = np.full((nrows, ncols), np.inf, dtype=np.float32)
    col_indices = np.clip(((lons - minlon) / dlon).astype(int), 0, ncols - 1)
    row_indices = np.clip(((maxlat - lats) / dlat).astype(int), 0, nrows - 1)
    np.minimum.at(values_2d, (row_indices, col_indices), vals)
    values_2d[np.isinf(values_2d)] = np.nan

    # Track which pixels have any grid point
    all_lats = grid_df["lat"].values
    all_lons = grid_df["lon"].values
    has_point = np.zeros((nrows, ncols), dtype=bool)
    all_col_idx = np.clip(((all_lons - minlon) / dlon).astype(int), 0, ncols - 1)
    all_row_idx = np.clip(((maxlat - all_lats) / dlat).astype(int), 0, nrows - 1)
    has_point[all_row_idx, all_col_idx] = True

    # Safety-net gap fill (no-op with WGS84-native grid; kept for robustness)
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

    # Mask outside district
    if district_polygon is not None:
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


def colorize_raster(values_2d, vmin, vmax, cmap_name) -> str | None:
    """Apply colormap to 2D raster, return base64 PNG."""
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


def _generate_cmap_lut(cmap_name: str, n: int = 256) -> str:
    """Generate a colormap lookup table as base64 RGBA bytes for client-side rendering.

    Returns base64-encoded array of n*4 bytes (RGBA for each of 256 levels).
    """
    cmap = plt.get_cmap(cmap_name)
    indices = np.linspace(0, 1, n)
    rgba = (cmap(indices) * 255).astype(np.uint8)  # shape (n, 4)
    return base64.b64encode(rgba.tobytes()).decode("utf-8")


def compute_per_school_grids(
    snap: SnapResult,
    dijkstra_results: dict,
    grid: gpd.GeoDataFrame,
    school_names: list[str],
    school_anchor_ids: dict,
    district_polygon,
    shared_grid_params: dict,
) -> tuple[dict[str, str], dict]:
    """Compute per-school travel time grids for client-side nearest-school computation.

    For each school, computes a rasterized float32 grid of travel times (minutes)
    from every pixel to that school. Client-side JS takes min() across open schools
    to produce the nearest-school heatmap.

    Returns:
        (grids_b64, grid_meta)
        grids_b64: {school_name: base64-encoded float32 2D array}
        grid_meta: dict with lonMin, latMin, lonMax, latMax, nRows, nCols
    """
    n_points = len(snap.start_nodes)
    grid_ids = grid["grid_id"].values
    grid_meta = None

    grids_b64 = {}
    for school_name in school_names:
        dij = dijkstra_results[school_name]
        dist = dij["dist"]

        # Vectorized distance lookup (same logic as assign_pixels_to_schools)
        t_u = np.array([dist.get(u, np.inf) for u in snap.start_nodes])
        t_v = np.array([dist.get(v, np.inf) for v in snap.end_nodes])

        via_u = t_u + snap.fractions * snap.edge_times
        via_v = t_v + (1.0 - snap.fractions) * snap.edge_times

        best_via = np.minimum(via_u, via_v)
        total_seconds = best_via + snap.access_times
        total_seconds[~snap.reachable] = np.inf

        # Zero out the school's own anchor point
        for gid, sname in school_anchor_ids.items():
            if sname == school_name:
                idx = np.where(grid_ids == gid)[0]
                if len(idx) > 0:
                    total_seconds[idx[0]] = 0.0

        total_minutes = total_seconds / 60.0
        total_minutes[np.isinf(total_minutes)] = np.nan

        # Rasterize
        result_df = grid[["grid_id", "lat", "lon"]].copy()
        result_df["value"] = total_minutes
        vals_2d, meta, _bounds = rasterize_grid(
            result_df, "value",
            district_polygon=district_polygon,
            grid_params=shared_grid_params,
        )
        if meta is not None and grid_meta is None:
            grid_meta = meta

        if vals_2d is not None:
            grids_b64[school_name] = base64.b64encode(
                vals_2d.astype(np.float32).tobytes()
            ).decode("utf-8")

    _progress(f"Computed {len(grids_b64)} per-school travel grids")
    return grids_b64, grid_meta


# ═══════════════════════════════════════════════════════════════════════════
# Section 8: Children Distribution (Part 2a)
# ═══════════════════════════════════════════════════════════════════════════

def _census_get(base_url: str, get_vars: list, for_geo: str,
                in_geo: str | None = None) -> pd.DataFrame:
    """Make a Census API request and return a DataFrame."""
    chunk_size = 48
    all_chunks = []
    key = _get_census_api_key()
    if not key:
        _progress("NOTE: No CENSUS_API_KEY. Using unauthenticated access (500 req/day).")

    for i in range(0, len(get_vars), chunk_size):
        chunk = get_vars[i:i + chunk_size]
        params = {
            "get": ",".join(["NAME"] + chunk),
            "for": for_geo,
        }
        if in_geo:
            params["in"] = in_geo
        if key:
            params["key"] = key
        resp = requests.get(base_url, params=params, timeout=60)
        resp.raise_for_status()
        data = resp.json()
        if len(data) < 2:
            raise RuntimeError(f"Census API returned no data for {for_geo}")
        header = data[0]
        rows = data[1:]
        df = pd.DataFrame(rows, columns=header)
        for col in chunk:
            if col in df.columns:
                df[col] = pd.to_numeric(df[col], errors="coerce")
        all_chunks.append(df)

    if len(all_chunks) == 1:
        return all_chunks[0]

    result = all_chunks[0]
    geo_cols = [c for c in result.columns
                if c in ("state", "county", "tract", "block group", "block", "NAME")]
    for chunk_df in all_chunks[1:]:
        new_cols = [c for c in chunk_df.columns if c not in result.columns]
        result = result.merge(chunk_df[geo_cols + new_cols], on=geo_cols, how="left")
    return result


def fetch_children_by_blockgroup(cache_only: bool = False) -> pd.DataFrame:
    """Fetch ACS children counts (0-4, 5-9) by block group for Orange County.

    Returns DataFrame with columns: GEOID, children_0_4, children_5_9
    """
    cache_path = CLOSURE_CACHE / "children_blockgroups.csv"
    if cache_path.exists():
        _progress(f"Loading cached children data: {cache_path.name}")
        return pd.read_csv(cache_path)

    if cache_only:
        raise FileNotFoundError(f"Children cache not found. Run without --cache-only.")

    _progress("Fetching ACS children counts from Census API ...")
    acs_vars = [
        "B01001_003E",  # male under 5
        "B01001_027E",  # female under 5
        "B01001_004E",  # male 5-9
        "B01001_028E",  # female 5-9
    ]
    df = _census_get(
        ACS_BASE_URL, acs_vars,
        for_geo="block group:*",
        in_geo=f"state:{STATE_FIPS}+county:{COUNTY_FIPS}",
    )

    df["GEOID"] = df["state"] + df["county"] + df["tract"] + df["block group"]
    df["children_0_4"] = df["B01001_003E"].fillna(0) + df["B01001_027E"].fillna(0)
    df["children_5_9"] = df["B01001_004E"].fillna(0) + df["B01001_028E"].fillna(0)

    result = df[["GEOID", "children_0_4", "children_5_9"]].copy()
    result.to_csv(cache_path, index=False)
    _progress(f"  Fetched children for {len(result)} block groups")
    return result


def download_tiger_blocks(cache_only: bool = False) -> gpd.GeoDataFrame:
    """Download Census block geometries for Orange County."""
    block_gpkg = DATA_CACHE / "tiger_blocks_orange.gpkg"
    if block_gpkg.exists():
        _progress(f"Loading cached block geometries: {block_gpkg.name}")
        return gpd.read_file(block_gpkg)

    if cache_only:
        raise FileNotFoundError(f"Block cache not found. Run without --cache-only.")

    _progress("Downloading TIGER/Line block shapefile for Orange County ...")
    resp = requests.get(TIGER_BLOCK_URL, timeout=180)
    resp.raise_for_status()

    with tempfile.TemporaryDirectory() as tmpdir:
        zip_path = Path(tmpdir) / "blocks.zip"
        zip_path.write_bytes(resp.content)
        _progress(f"  Downloaded {len(resp.content) / 1e6:.1f} MB")
        with zipfile.ZipFile(zip_path, "r") as zf:
            zf.extractall(tmpdir)
        shp_files = list(Path(tmpdir).glob("*.shp"))
        if not shp_files:
            raise FileNotFoundError("No .shp in TIGER block zip")
        gdf = gpd.read_file(shp_files[0])

    gdf = gdf.to_crs(CRS_WGS84)
    keep_cols = ["GEOID20", "TRACTCE20", "BLOCKCE20", "ALAND20", "geometry"]
    gdf = gdf[[c for c in keep_cols if c in gdf.columns]].copy()
    gdf.to_file(block_gpkg, driver="GPKG")
    _progress(f"  Cached {len(gdf)} blocks")
    return gdf


def download_tiger_blockgroups(cache_only: bool = False) -> gpd.GeoDataFrame:
    """Download Census block group geometries for Orange County."""
    bg_gpkg = DATA_CACHE / "tiger_blockgroups_orange.gpkg"
    if bg_gpkg.exists():
        _progress(f"Loading cached block group geometries: {bg_gpkg.name}")
        return gpd.read_file(bg_gpkg)

    if cache_only:
        raise FileNotFoundError(f"BG cache not found. Run without --cache-only.")

    _progress("Downloading TIGER/Line block group shapefile for NC ...")
    resp = requests.get(TIGER_BG_URL, timeout=180)
    resp.raise_for_status()

    with tempfile.TemporaryDirectory() as tmpdir:
        zip_path = Path(tmpdir) / "bg.zip"
        zip_path.write_bytes(resp.content)
        with zipfile.ZipFile(zip_path, "r") as zf:
            zf.extractall(tmpdir)
        shp_files = list(Path(tmpdir).glob("*.shp"))
        if not shp_files:
            raise FileNotFoundError("No .shp in TIGER BG zip")
        gdf = gpd.read_file(shp_files[0])

    gdf = gdf[gdf["COUNTYFP"] == COUNTY_FIPS].copy()
    gdf = gdf.to_crs(CRS_WGS84)
    keep_cols = ["GEOID", "TRACTCE", "BLKGRPCE", "ALAND", "geometry"]
    gdf = gdf[[c for c in keep_cols if c in gdf.columns]].copy()
    gdf.to_file(bg_gpkg, driver="GPKG")
    _progress(f"  Cached {len(gdf)} block groups")
    return gdf


def distribute_children_to_pixels(
    grid: gpd.GeoDataFrame,
    district_gdf: gpd.GeoDataFrame,
    cache_only: bool = False,
) -> pd.DataFrame:
    """Distribute ACS block-group children counts to 100m grid pixels.

    Uses dasymetric downscaling: BG → blocks (weighted by residential area)
    → pixels (area intersection).

    Returns DataFrame with columns: grid_id, children_0_4, children_5_9
    """
    cache_path = CLOSURE_CACHE / "pixel_children.csv"
    if cache_path.exists():
        _progress(f"Loading cached pixel children: {cache_path.name}")
        return pd.read_csv(cache_path)

    if cache_only:
        raise FileNotFoundError(f"Pixel children cache not found. Run without --cache-only.")

    # 1. Fetch children counts by block group
    bg_children = fetch_children_by_blockgroup(cache_only=cache_only)

    # 2. Download geometries
    blocks_gdf = download_tiger_blocks(cache_only=cache_only)
    bg_gdf = download_tiger_blockgroups(cache_only=cache_only)

    # 3. Clip to district + buffer
    district_utm = district_gdf.to_crs(CRS_UTM17N)
    district_buff = district_utm.geometry.iloc[0].buffer(1000)
    district_buff_wgs = gpd.GeoDataFrame(
        geometry=[district_buff], crs=CRS_UTM17N
    ).to_crs(CRS_WGS84).geometry.iloc[0]

    blocks_gdf = blocks_gdf[blocks_gdf.intersects(district_buff_wgs)].copy()
    bg_gdf = bg_gdf[bg_gdf.intersects(district_buff_wgs)].copy()
    _progress(f"  {len(blocks_gdf)} blocks, {len(bg_gdf)} block groups in district area")

    # 4. Derive parent BG GEOID from block GEOID (first 12 chars)
    blocks_gdf["parent_bg"] = blocks_gdf["GEOID20"].str[:12]

    # 5. Load residential parcels for dasymetric weights
    parcels_utm = None
    if PARCEL_POLYS.exists():
        _progress("  Loading residential parcels for dasymetric weighting ...")
        parcels = gpd.read_file(PARCEL_POLYS)
        if "is_residential" in parcels.columns:
            res_mask = parcels["is_residential"] == True
            if "imp_vac" in parcels.columns:
                res_mask = res_mask & parcels["imp_vac"].str.contains("Improved", case=False, na=False)
            parcels = parcels[res_mask].copy()
        parcels_utm = parcels.to_crs(CRS_UTM17N)
        _progress(f"  {len(parcels_utm)} residential parcels")

    # 6. Dasymetric: BG children → blocks
    blocks_utm = blocks_gdf.to_crs(CRS_UTM17N)
    blocks_utm["block_area"] = blocks_utm.geometry.area

    if parcels_utm is not None and len(parcels_utm) > 0:
        parcel_sindex = parcels_utm.sindex
        _progress("  Computing residential area per block ...")
        res_areas = np.zeros(len(blocks_utm))
        for i, geom in enumerate(blocks_utm.geometry):
            if geom is None or geom.is_empty:
                continue
            candidates = list(parcel_sindex.intersection(geom.bounds))
            if not candidates:
                continue
            clipped = parcels_utm.iloc[candidates].intersection(geom)
            res_areas[i] = clipped.area.sum()
        blocks_utm["block_res_area"] = res_areas

        bg_res_totals = blocks_utm.groupby("parent_bg")["block_res_area"].sum()
        bg_area_totals = blocks_utm.groupby("parent_bg")["block_area"].sum()

        weights = []
        for _, row in blocks_utm.iterrows():
            bg_id = row["parent_bg"]
            bg_res = bg_res_totals.get(bg_id, 0)
            if bg_res > 0:
                weights.append(row["block_res_area"] / bg_res)
            else:
                bg_a = bg_area_totals.get(bg_id, 1)
                weights.append(row["block_area"] / bg_a)
        blocks_gdf["weight"] = np.clip(weights, 0, 1)
    else:
        bg_area_totals = blocks_utm.groupby("parent_bg")["block_area"].sum()
        blocks_gdf["weight"] = [
            row["block_area"] / bg_area_totals.get(row["parent_bg"], 1)
            for _, row in blocks_utm.iterrows()
        ]
        blocks_gdf["weight"] = blocks_gdf["weight"].clip(upper=1.0)

    # Map BG children to blocks (ensure GEOID types match — CSV may load as int)
    bg_children["GEOID"] = bg_children["GEOID"].astype(str)
    bg_lookup = bg_children.set_index("GEOID")
    blocks_gdf["children_0_4"] = (
        blocks_gdf["parent_bg"].map(bg_lookup["children_0_4"]).fillna(0) *
        blocks_gdf["weight"]
    )
    blocks_gdf["children_5_9"] = (
        blocks_gdf["parent_bg"].map(bg_lookup["children_5_9"]).fillna(0) *
        blocks_gdf["weight"]
    )

    _progress(f"  Block-level totals: 0-4={blocks_gdf['children_0_4'].sum():.0f}, "
              f"5-9={blocks_gdf['children_5_9'].sum():.0f}")

    # 7. Distribute blocks → pixels via overlay intersection
    _progress("  Distributing block children to pixels ...")
    grid_utm = grid.to_crs(CRS_UTM17N)
    half = GRID_RESOLUTION_M / 2
    pixel_squares = gpd.GeoDataFrame(
        {"grid_id": grid["grid_id"].values},
        geometry=[box(g.x - half, g.y - half, g.x + half, g.y + half)
                  for g in grid_utm.geometry],
        crs=CRS_UTM17N,
    )

    blocks_for_join = blocks_gdf[["GEOID20", "children_0_4", "children_5_9", "geometry"]].copy()
    blocks_for_join = blocks_for_join.to_crs(CRS_UTM17N)
    blocks_for_join["block_area_total"] = blocks_for_join.geometry.area

    # Filter to blocks with children (saves overlay time)
    blocks_with_kids = blocks_for_join[
        (blocks_for_join["children_0_4"] > 0) | (blocks_for_join["children_5_9"] > 0)
    ].copy()
    _progress(f"  {len(blocks_with_kids)} blocks with children for overlay")

    pixel_children = {gid: [0.0, 0.0] for gid in grid["grid_id"].values}

    if len(blocks_with_kids) > 0:
        # Use overlay intersection to get pixel×block fragments
        fragments = gpd.overlay(pixel_squares, blocks_with_kids, how="intersection")
        fragments["frag_area"] = fragments.geometry.area

        for _, row in fragments.iterrows():
            gid = row["grid_id"]
            block_area = row["block_area_total"]
            if block_area <= 0:
                continue
            frac = row["frag_area"] / block_area
            pixel_children[gid][0] += row["children_0_4"] * frac
            pixel_children[gid][1] += row["children_5_9"] * frac

        _progress(f"  Overlay produced {len(fragments)} pixel×block fragments")

    result = pd.DataFrame([
        {"grid_id": gid, "children_0_4": vals[0], "children_5_9": vals[1]}
        for gid, vals in pixel_children.items()
    ])

    _progress(f"  Pixel-level totals: 0-4={result['children_0_4'].sum():.0f}, "
              f"5-9={result['children_5_9'].sum():.0f}")

    result.to_csv(cache_path, index=False)
    _progress(f"  Cached pixel children to {cache_path.name}")
    return result


# ═══════════════════════════════════════════════════════════════════════════
# Section 9: Route Extraction and Traffic Aggregation (Part 2b)
# ═══════════════════════════════════════════════════════════════════════════

def reconstruct_path(pred: dict, source_node, target_node) -> list | None:
    """Reconstruct shortest path from predecessor map. O(path_length)."""
    if target_node not in pred:
        return None
    path = [target_node]
    current = target_node
    visited = set()
    while current != source_node:
        visited.add(current)
        predecessors = pred.get(current)
        if not predecessors:
            return None
        current = predecessors[0]
        if current in visited:
            return None  # cycle guard
        path.append(current)
    path.reverse()
    return path


def compute_traffic(
    grid: gpd.GeoDataFrame,
    snap: SnapResult,
    dijkstra_results: dict,
    pixel_children: pd.DataFrame,
    edge_id_map: dict,
    open_schools: list[str],
    entry_nodes: np.ndarray,
    nearest_schools: np.ndarray,
    walk_zones_gdf: gpd.GeoDataFrame | None = None,
    zone_schools: np.ndarray | None = None,
    closed_schools: list[str] | None = None,
    pixel_walk_zone: np.ndarray | None = None,
) -> tuple[dict, dict]:
    """Aggregate children-weighted traffic on each road edge.

    Also tracks per-walk-zone contributions for client-side masking:
    for each walk zone school, records how many children from pixels inside
    that walk zone traverse each edge.

    Args:
        zone_schools: If provided, pixel_zone_school[i] for zone-restricted routing.
        closed_schools: Schools closed in this scenario (for zone-restricted routing).
        pixel_walk_zone: Pre-computed array mapping pixel index → walk zone school name
            (or None if pixel is not in any walk zone). Used for contribution tracking.

    Returns:
        (traffic, walk_zone_contributions)
        traffic: {edge_feature_idx: {"children_0_4": float, "children_5_9": float}}
        walk_zone_contributions: {walk_zone_school: {edge_idx: {"children_0_4": float, "children_5_9": float}}}
    """
    n_pixels = len(grid)
    grid_ids = grid["grid_id"].values

    # Build pixel children lookup
    children_lookup = pixel_children.set_index("grid_id")

    # Traffic accumulator
    traffic = {}
    # Per-walk-zone contribution accumulators
    wz_contributions = {}

    # Determine actual school for each pixel
    actual_schools = nearest_schools.copy()
    actual_entries = entry_nodes.copy()
    if zone_schools is not None:
        # Zone-restricted: use zone school unless it's closed
        for i in range(n_pixels):
            zs = zone_schools[i]
            if zs is not None and zs in open_schools:
                # Re-compute entry for zone school
                dij = dijkstra_results.get(zs)
                if dij is None:
                    continue
                dist = dij["dist"]
                t_u = dist.get(snap.start_nodes[i], np.inf)
                t_v = dist.get(snap.end_nodes[i], np.inf)
                via_u = t_u + snap.fractions[i] * snap.edge_times[i]
                via_v = t_v + (1.0 - snap.fractions[i]) * snap.edge_times[i]
                if via_u <= via_v:
                    actual_entries[i] = snap.start_nodes[i]
                else:
                    actual_entries[i] = snap.end_nodes[i]
                actual_schools[i] = zs
            elif zs is not None and closed_schools and zs in closed_schools:
                # Displaced: routes to nearest open school (already in nearest_schools)
                pass
            # else: no zone assignment, use nearest_schools default

    processed = 0

    for i in range(n_pixels):
        if not snap.reachable[i]:
            continue

        school = actual_schools[i]
        if school is None or school not in dijkstra_results:
            continue

        # Get children count
        gid = grid_ids[i]
        if gid in children_lookup.index:
            c04 = children_lookup.at[gid, "children_0_4"]
            c59 = children_lookup.at[gid, "children_5_9"]
        else:
            continue

        if c04 + c59 < 0.001:
            continue

        # Determine walk zone membership for this pixel
        wz_school = pixel_walk_zone[i] if pixel_walk_zone is not None else None

        # Reconstruct path from entry_node to school source_node
        dij = dijkstra_results[school]
        entry = actual_entries[i]
        source = dij["source_node"]

        path = reconstruct_path(dij["pred"], source, entry)
        if path is None:
            continue

        # Accumulate traffic on each edge in path
        for j in range(len(path) - 1):
            u, v = path[j], path[j + 1]
            edge_key = (min(u, v), max(u, v))
            feat_idx = edge_id_map.get(edge_key)
            if feat_idx is None:
                continue

            if feat_idx not in traffic:
                traffic[feat_idx] = {"children_0_4": 0.0, "children_5_9": 0.0}
            traffic[feat_idx]["children_0_4"] += c04
            traffic[feat_idx]["children_5_9"] += c59

            # Track walk zone contribution
            if wz_school is not None:
                if wz_school not in wz_contributions:
                    wz_contributions[wz_school] = {}
                wz_edges = wz_contributions[wz_school]
                if feat_idx not in wz_edges:
                    wz_edges[feat_idx] = {"children_0_4": 0.0, "children_5_9": 0.0}
                wz_edges[feat_idx]["children_0_4"] += c04
                wz_edges[feat_idx]["children_5_9"] += c59

        processed += 1

    _progress(f"  Traffic: {processed} pixels routed, "
              f"{len(traffic)} edges with traffic, "
              f"{len(wz_contributions)} walk zones tracked")
    return traffic, wz_contributions


def precompute_pixel_walk_zones(
    grid: gpd.GeoDataFrame,
    walk_zones_gdf: gpd.GeoDataFrame | None,
) -> np.ndarray | None:
    """Assign each pixel to its walk zone school (if any).

    Returns array of school names (None for pixels outside all walk zones).
    Cached to closure_analysis directory.
    """
    if walk_zones_gdf is None:
        return None

    cache_path = CLOSURE_CACHE / "pixel_walk_zone_assignments.csv"
    if cache_path.exists():
        _progress(f"Loading cached walk zone assignments: {cache_path.name}")
        df = pd.read_csv(cache_path)
        result = np.empty(len(grid), dtype=object)
        lookup = dict(zip(df["grid_id"], df["walk_zone_school"]))
        for i, gid in enumerate(grid["grid_id"].values):
            val = lookup.get(gid)
            result[i] = val if pd.notna(val) else None
        return result

    _progress("Assigning pixels to walk zones ...")
    joined = gpd.sjoin(
        grid[["grid_id", "geometry"]], walk_zones_gdf[["school", "geometry"]],
        how="left", predicate="within"
    )
    deduped = joined.drop_duplicates(subset=["grid_id"], keep="first")
    zone_map = dict(zip(deduped["grid_id"], deduped["school"]))

    result = np.empty(len(grid), dtype=object)
    for i, gid in enumerate(grid["grid_id"].values):
        val = zone_map.get(gid)
        result[i] = val if pd.notna(val) else None

    n_assigned = sum(1 for v in result if v is not None)
    _progress(f"  {n_assigned}/{len(grid)} pixels in walk zones")

    cache_df = pd.DataFrame({
        "grid_id": grid["grid_id"].values,
        "walk_zone_school": result,
    })
    cache_df.to_csv(cache_path, index=False)
    return result


# ═══════════════════════════════════════════════════════════════════════════
# Section 10: Zone-Restricted Pixel Assignment
# ═══════════════════════════════════════════════════════════════════════════

def assign_pixels_to_zones(
    grid: gpd.GeoDataFrame,
    attendance_zones: gpd.GeoDataFrame | None,
) -> np.ndarray | None:
    """Assign each pixel to its attendance zone school.

    Returns array of school names (None for pixels outside all zones).
    """
    if attendance_zones is None:
        return None

    cache_path = CLOSURE_CACHE / "pixel_zone_assignments.csv"
    if cache_path.exists():
        _progress(f"Loading cached zone assignments: {cache_path.name}")
        df = pd.read_csv(cache_path)
        result = np.empty(len(grid), dtype=object)
        lookup = dict(zip(df["grid_id"], df["zone_school"]))
        for i, gid in enumerate(grid["grid_id"].values):
            val = lookup.get(gid)
            result[i] = val if pd.notna(val) else None
        return result

    _progress("Assigning pixels to attendance zones ...")
    joined = gpd.sjoin(
        grid[["grid_id", "geometry"]], attendance_zones[["school", "geometry"]],
        how="left", predicate="within"
    )

    # Handle duplicates (pixel in multiple zones): take first
    deduped = joined.drop_duplicates(subset=["grid_id"], keep="first")
    zone_map = dict(zip(deduped["grid_id"], deduped["school"]))

    result = np.empty(len(grid), dtype=object)
    for i, gid in enumerate(grid["grid_id"].values):
        val = zone_map.get(gid)
        result[i] = val if pd.notna(val) else None

    n_assigned = sum(1 for v in result if v is not None)
    _progress(f"  {n_assigned}/{len(grid)} pixels assigned to attendance zones")

    # Cache
    cache_df = pd.DataFrame({
        "grid_id": grid["grid_id"].values,
        "zone_school": result,
    })
    cache_df.to_csv(cache_path, index=False)
    return result


# ═══════════════════════════════════════════════════════════════════════════
# Section 11: Interactive Map
# ═══════════════════════════════════════════════════════════════════════════

def create_map(
    heatmap_overlays: dict,
    per_school_grids: dict[str, dict[str, str]],
    grid_meta: dict,
    schools: gpd.GeoDataFrame,
    district_gdf: gpd.GeoDataFrame,
    zone_polygons: dict,
    road_geojson: dict,
    traffic_arrays: dict,
    walk_zone_contributions: dict,
    n_edges: int,
    walk_zones_geojson: dict | None,
    network_geojson: dict | None = None,
) -> folium.Map:
    """Create interactive Folium map with tabbed sidebar.

    Args:
        heatmap_overlays: {scenario|mode|view: (base64_png, bounds)} — pre-rendered PNGs
        per_school_grids: {mode: {school_name: base64 float32 grid}} — for hover tooltips
        grid_meta: dict with lonMin, latMin, lonMax, latMax, nRows, nCols
        schools: GeoDataFrame of school locations
        district_gdf: GeoDataFrame of district boundary
        zone_polygons: {scenario|mode: GeoJSON dict}
        road_geojson: GeoJSON of road network edges
        traffic_arrays: {scenario|zone|age: base64 float32 array} — unmasked only
        walk_zone_contributions: {scenario|zone|age|walk_school: sparse JSON}
        n_edges: number of road edges
        walk_zones_geojson: GeoJSON of walk zone polygons
        network_geojson: {mode: GeoJSON} — per-mode network for Part 1 overlay
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
        district_gdf.to_crs(CRS_WGS84).__geo_interface__,
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

    control_html = _build_control_html(
        heatmap_overlays, per_school_grids, grid_meta, school_data,
        road_geojson, traffic_arrays, walk_zone_contributions,
        n_edges, zone_polygons, walk_zones_geojson,
        network_geojson=network_geojson,
    )
    m.get_root().html.add_child(folium.Element(control_html))
    return m


def _build_control_html(
    heatmap_overlays: dict, per_school_grids: dict, grid_meta: dict,
    schools: list,
    road_geojson: dict, traffic_arrays: dict,
    walk_zone_contributions: dict,
    n_edges: int,
    zone_polygons: dict,
    walk_zones_geojson: dict | None,
    network_geojson: dict | None = None,
) -> str:
    """Build HTML/CSS/JS for the tabbed control panel with pre-rendered image overlays."""

    # Build overlay data for JS — keyed by "scenario|mode|type"
    overlays_data = {}
    for key, (b64, img_bounds) in heatmap_overlays.items():
        if b64 is None:
            continue
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
    grid_meta_json = json.dumps(grid_meta) if grid_meta else "null"
    per_school_grids_json = json.dumps(per_school_grids)
    road_geojson_json = json.dumps(road_geojson)
    traffic_arrays_json = json.dumps(traffic_arrays)
    n_edges_json = json.dumps(n_edges)
    zone_polygons_json = json.dumps(zone_polygons)
    walk_zones_json = json.dumps(walk_zones_geojson or {})
    walk_zone_contributions_json = json.dumps(walk_zone_contributions)
    network_geojson_json = json.dumps(network_geojson or {})

    # Build list of all school names for walk zone checkboxes
    school_names = [s["name"] for s in schools]
    school_names_json = json.dumps(school_names)

    return f"""
<style>
/* Force crisp pixel rendering on all Leaflet image overlays */
.leaflet-image-layer {{
    image-rendering: pixelated;
    image-rendering: -moz-crisp-edges;
    image-rendering: crisp-edges;
}}
#closure-controls {{
    flex: 0 0 320px;
    width: 320px;
    height: 100vh;
    overflow-y: auto;
    background: white;
    border-left: 1px solid #dee2e6;
    box-shadow: -2px 0 8px rgba(0,0,0,0.1);
    font-family: 'Segoe UI', Tahoma, sans-serif;
    font-size: 13px;
    box-sizing: border-box;
    display: flex;
    flex-direction: column;
}}
#main-column {{
    flex: 1;
    display: flex;
    flex-direction: column;
    min-width: 0;
    height: 100vh;
    overflow: hidden;
}}
.tab-buttons {{
    display: flex;
    border-bottom: 2px solid #dee2e6;
    flex-shrink: 0;
}}
.tab-btn {{
    flex: 1;
    padding: 10px 8px;
    border: none;
    background: #f8f9fa;
    cursor: pointer;
    font-weight: 600;
    font-size: 13px;
    color: #666;
    transition: all 0.2s;
}}
.tab-btn.active {{
    background: white;
    color: {ACCENT_COLOR};
    border-bottom: 2px solid {ACCENT_COLOR};
    margin-bottom: -2px;
}}
.tab-btn:hover:not(.active) {{
    background: #e9ecef;
}}
.tab-content {{
    display: none;
    padding: 12px 15px;
    flex: 1;
    overflow-y: auto;
}}
.tab-content.active {{
    display: block;
}}
#closure-controls label {{
    display: block;
    margin: 2px 0;
    cursor: pointer;
    padding: 2px 4px;
    border-radius: 3px;
    font-size: 12px;
}}
#closure-controls label:hover {{
    background: #f0f0f0;
}}
#closure-controls .section-title {{
    font-weight: bold;
    margin: 10px 0 5px 0;
    color: #555;
    font-size: 11px;
    text-transform: uppercase;
}}
.legend-box {{
    margin-top: 10px;
    padding-top: 8px;
    border-top: 1px solid #ddd;
}}
.legend-box .gradient-bar {{
    height: 12px;
    border-radius: 3px;
    margin: 4px 0;
}}
.legend-box .range-labels {{
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
    color: #dc3545;
    font-weight: bold;
}}
#closure-tooltip {{
    position: fixed;
    z-index: 2000;
    background: rgba(0,0,0,0.85);
    color: #fff;
    padding: 5px 10px;
    border-radius: 4px;
    font-size: 12px;
    font-family: 'Segoe UI', Tahoma, sans-serif;
    pointer-events: none;
    display: none;
    white-space: nowrap;
    max-width: 350px;
}}
.subsection {{
    margin-left: 8px;
    padding-left: 8px;
    border-left: 2px solid #eee;
}}
.scenario-list {{
    max-height: 220px;
    overflow-y: auto;
    border: 1px solid #eee;
    border-radius: 4px;
    padding: 2px;
    margin-top: 4px;
}}
.scenario-list label {{
    font-size: 11px !important;
    padding: 3px 6px !important;
}}
.scenario-list label.selected {{
    background: #e8f0fe;
    border-radius: 3px;
}}
</style>

<div id="closure-controls">
    <div class="tab-buttons">
        <button class="tab-btn" onclick="window.switchTab('part1')">Travel Time</button>
        <button class="tab-btn active" onclick="window.switchTab('part2')">Traffic</button>
    </div>

    <!-- Part 1: Travel Time -->
    <div class="tab-content" id="tab-part1">
        <div class="section-title">Closure Scenario</div>
        <div class="scenario-list" id="p1-scenario-list"></div>

        <div class="section-title">Travel Mode</div>
        <div id="p1-mode-options"></div>

        <div class="section-title">View</div>
        <div class="subsection">
            <label><input type="radio" name="p1-view" value="abs" checked onchange="window.updatePart1()"> Absolute travel time</label>
            <label><input type="radio" name="p1-view" value="delta" onchange="window.updatePart1()"> Increase vs. baseline</label>
        </div>

        <div class="section-title">Layers</div>
        <div class="subsection">
            <label><input type="checkbox" id="p1-show-zones" checked onchange="window.updatePart1()"> Zone boundaries</label>
            <label><input type="checkbox" id="p1-show-network" onchange="window.updatePart1()"> Road network</label>
        </div>

        <div class="legend-box" id="p1-legend">
            <div class="section-title">Legend</div>
            <div id="p1-legend-label"></div>
            <div class="gradient-bar" id="p1-legend-bar"></div>
            <div class="range-labels">
                <span id="p1-legend-min"></span>
                <span id="p1-legend-max"></span>
            </div>
        </div>

        <div class="school-marker-info" id="p1-school-info"></div>
    </div>

    <!-- Part 2: Traffic -->
    <div class="tab-content active" id="tab-part2">
        <div class="section-title">Closure Scenario</div>
        <div class="scenario-list" id="p2-scenario-list"></div>

        <div class="section-title">Age Group</div>
        <div class="subsection">
            <label><input type="radio" name="p2-age" value="5_9" checked onchange="window.updatePart2()"> Children 5-9</label>
            <label><input type="radio" name="p2-age" value="0_4" onchange="window.updatePart2()"> Children 0-4</label>
        </div>

        <div class="section-title">School Community Definition</div>
        <div class="subsection">
            <label><input type="radio" name="p2-routing" value="zone" checked onchange="window.updatePart2()"> Current school zone</label>
            <label><input type="radio" name="p2-routing" value="nearest" onchange="window.updatePart2()"> Closest school by driving</label>
        </div>

        <div class="section-title">Choose Map Type</div>
        <div class="subsection">
            <label><input type="radio" name="p2-view" value="abs" checked onchange="window.updatePart2()"> Estimated traffic</label>
            <label><input type="radio" name="p2-view" value="diff" onchange="window.updatePart2()"> Estimated change in traffic</label>
        </div>

        <div class="section-title">Mask Walk Zones</div>
        <div class="subsection">
            <label><input type="radio" name="p2-wzmask" value="no" checked onchange="window.updatePart2()"> No</label>
            <label><input type="radio" name="p2-wzmask" value="yes" onchange="window.updatePart2()"> Yes</label>
        </div>

        <div class="section-title">Show Walk Zone Polygons</div>
        <div class="subsection">
            <label><input type="radio" name="p2-showwz" value="no" checked onchange="window.updatePart2()"> No</label>
            <label><input type="radio" name="p2-showwz" value="yes" onchange="window.updatePart2()"> Yes</label>
        </div>

        <div class="legend-box" id="p2-legend">
            <div class="section-title" id="p2-legend-title">Traffic</div>
            <div class="gradient-bar" id="p2-legend-bar"></div>
            <div class="range-labels">
                <span id="p2-legend-min"></span>
                <span id="p2-legend-max"></span>
            </div>
        </div>

        <div class="school-marker-info" id="p2-school-info"></div>
    </div>
</div>
<div id="closure-tooltip"></div>

<script>
(function() {{
    var SCENARIOS = {scenarios_json};
    var SCENARIO_LABELS = {scenario_labels_json};
    var MODE_LABELS = {mode_labels_json};
    var MODE_RANGES = {mode_ranges_json};
    var SCHOOLS = {schools_json};
    var SCHOOL_NAMES = {school_names_json};
    var GRID_META = {grid_meta_json};
    var PER_SCHOOL_GRIDS_B64 = {per_school_grids_json};
    var OVERLAYS_DATA = {overlays_data_json};
    var ROAD_GEOJSON = {road_geojson_json};
    var TRAFFIC_ARRAYS_B64 = {traffic_arrays_json};
    var WZ_CONTRIBUTIONS = {walk_zone_contributions_json};
    var N_EDGES = {n_edges_json};
    var ZONE_POLYGONS = {zone_polygons_json};
    var WALK_ZONES_GEO = {walk_zones_json};
    var NETWORK_GEOJSON = {network_geojson_json};

    var schoolMarkers = [];
    var tooltip = document.getElementById('closure-tooltip');
    var roadLayer = null;
    var networkLayer = null;  // Part 1 display-only network overlay
    var walkZoneLayer = null;
    var zoneLayer = null;
    var overlayLayers = {{}};  // key -> L.imageOverlay
    var activeOverlayKey = null;
    var activeTab = 'part2';
    var mapRef = null;
    var initialized = false;
    var currentTrafficArr = null;  // Float32Array displayed on Part 2 (for hover)

    // --- Decode caches ---
    var decodedSchoolGrids = {{}};  // mode|school -> Float32Array
    var decodedTraffic = {{}};

    function b64ToFloat32(b64) {{
        var raw = atob(b64);
        var buf = new ArrayBuffer(raw.length);
        var u8 = new Uint8Array(buf);
        for (var i = 0; i < raw.length; i++) u8[i] = raw.charCodeAt(i);
        return new Float32Array(buf);
    }}

    function getSchoolGrid(mode, school) {{
        var key = mode + '|' + school;
        if (!decodedSchoolGrids[key]) {{
            var grids = PER_SCHOOL_GRIDS_B64[mode];
            if (!grids || !grids[school]) return null;
            decodedSchoolGrids[key] = b64ToFloat32(grids[school]);
        }}
        return decodedSchoolGrids[key];
    }}

    function getTrafficArray(key) {{
        if (!decodedTraffic[key]) {{
            if (!TRAFFIC_ARRAYS_B64[key]) return null;
            decodedTraffic[key] = b64ToFloat32(TRAFFIC_ARRAYS_B64[key]);
        }}
        return decodedTraffic[key];
    }}

    // --- Client-side grid computations (hover tooltips only) ---
    function computeNearestSchoolGrid(mode, closedSchools) {{
        if (!GRID_META) return null;
        var nPx = GRID_META.nRows * GRID_META.nCols;
        var result = new Float32Array(nPx);
        result.fill(Infinity);
        var nearestNames = new Array(nPx);

        var schoolList = SCHOOL_NAMES.filter(function(s) {{
            return closedSchools.indexOf(s) === -1;
        }});

        for (var si = 0; si < schoolList.length; si++) {{
            var grid = getSchoolGrid(mode, schoolList[si]);
            if (!grid) continue;
            for (var j = 0; j < nPx; j++) {{
                if (!isNaN(grid[j]) && grid[j] < result[j]) {{
                    result[j] = grid[j];
                    nearestNames[j] = schoolList[si];
                }}
            }}
        }}

        // Replace Infinity with NaN
        for (var j = 0; j < nPx; j++) {{
            if (result[j] === Infinity) {{ result[j] = NaN; nearestNames[j] = null; }}
        }}
        return {{ values: result, names: nearestNames }};
    }}

    // --- Pre-rendered heatmap overlays (Python-side plt.imsave) ---
    function initOverlays(map) {{
        for (var key in OVERLAYS_DATA) {{
            var d = OVERLAYS_DATA[key];
            overlayLayers[key] = L.imageOverlay(d.url, d.bounds, {{
                opacity: 0,
                interactive: false,
                pane: 'heatmapPane',
            }}).addTo(map);
        }}
    }}

    function showOverlay(key) {{
        if (activeOverlayKey && overlayLayers[activeOverlayKey]) {{
            overlayLayers[activeOverlayKey].setOpacity(0);
        }}
        activeOverlayKey = key;
        if (key && overlayLayers[key]) {{
            overlayLayers[key].setOpacity(1);
        }}
    }}

    function clearHeatmapOverlay() {{
        if (activeOverlayKey && overlayLayers[activeOverlayKey]) {{
            overlayLayers[activeOverlayKey].setOpacity(0);
        }}
        activeOverlayKey = null;
    }}

    // --- Helpers ---
    function getSelectedValue(name) {{
        var radios = document.querySelectorAll('input[name="' + name + '"]');
        for (var i = 0; i < radios.length; i++) {{
            if (radios[i].checked) return radios[i].value;
        }}
        return null;
    }}

    function getMap() {{
        if (mapRef) return mapRef;
        for (var key in window) {{
            try {{
                if (window[key] && window[key]._leaflet_id && window[key].getZoom) {{
                    mapRef = window[key];
                    return mapRef;
                }}
            }} catch(e) {{}}
        }}
        return null;
    }}

    // --- Traffic color scale ---
    function trafficColor(val, maxVal, isDiff) {{
        if (val === 0 || isNaN(val)) return {{ color: 'transparent', weight: 0, opacity: 0 }};
        if (isDiff) {{
            var t = Math.max(-1, Math.min(1, val / maxVal));
            var r, g, b;
            if (t < 0) {{
                var s = -t;
                r = Math.round(255*(1-s) + 49*s);
                g = Math.round(255*(1-s) + 130*s);
                b = Math.round(255*(1-s) + 189*s);
            }} else {{
                var s = t;
                r = Math.round(255*(1-s) + 215*s);
                g = Math.round(255*(1-s) + 48*s);
                b = Math.round(255*(1-s) + 39*s);
            }}
            var w = 1 + Math.abs(t) * 5;
            return {{ color: 'rgb('+r+','+g+','+b+')', weight: w, opacity: 0.8 }};
        }} else {{
            var t = Math.min(1, val / maxVal);
            var r, g, b;
            if (t < 0.33) {{
                var s = t / 0.33;
                r = Math.round(255*(1-s) + 254*s);
                g = Math.round(255*(1-s) + 178*s);
                b = Math.round(204*(1-s) + 76*s);
            }} else if (t < 0.66) {{
                var s = (t - 0.33) / 0.33;
                r = Math.round(254*(1-s) + 240*s);
                g = Math.round(178*(1-s) + 59*s);
                b = Math.round(76*(1-s) + 32*s);
            }} else {{
                var s = (t - 0.66) / 0.34;
                r = Math.round(240*(1-s) + 189*s);
                g = Math.round(59*(1-s) + 0*s);
                b = Math.round(32*(1-s) + 38*s);
            }}
            var w = 1 + t * 5;
            return {{ color: 'rgb('+r+','+g+','+b+')', weight: w, opacity: 0.8 }};
        }}
    }}

    // --- School markers ---
    function updateSchoolMarkers(map, closedSchools) {{
        schoolMarkers.forEach(function(m) {{ map.removeLayer(m); }});
        schoolMarkers = [];
        SCHOOLS.forEach(function(school) {{
            var isClosed = closedSchools.indexOf(school.name) !== -1;
            var marker = L.circleMarker([school.lat, school.lon], {{
                radius: isClosed ? 8 : 7,
                fillColor: isClosed ? '#dc3545' : '#0d6efd',
                color: isClosed ? '#dc3545' : '#0a58ca',
                weight: 2, opacity: 1,
                fillOpacity: isClosed ? 0.3 : 0.8,
                dashArray: isClosed ? '4,4' : null,
            }});
            var status = isClosed ? '<span style="color:#dc3545;font-weight:bold">CLOSED</span>' : '<span style="color:#198754">Open</span>';
            marker.bindPopup('<b>' + school.name + '</b><br>' + school.address + '<br>' + status);
            if (isClosed) {{
                var xIcon = L.divIcon({{
                    html: '<span style="color:#dc3545;font-size:18px;font-weight:bold;">&times;</span>',
                    className: 'closed-school-x',
                    iconSize: [20, 20], iconAnchor: [10, 10],
                }});
                var xm = L.marker([school.lat, school.lon], {{icon: xIcon}}).addTo(map);
                schoolMarkers.push(xm);
            }}
            marker.addTo(map);
            schoolMarkers.push(marker);
        }});
    }}

    // --- Walk zone layer ---
    function updateWalkZones(map, closedSchools, show) {{
        if (walkZoneLayer) {{ map.removeLayer(walkZoneLayer); walkZoneLayer = null; }}
        if (!show || !WALK_ZONES_GEO || !WALK_ZONES_GEO.features) return;
        walkZoneLayer = L.geoJSON(WALK_ZONES_GEO, {{
            style: function(feature) {{
                var isClosed = closedSchools.indexOf(feature.properties.school) !== -1;
                return {{
                    fillColor: isClosed ? 'rgba(231,76,60,0.25)' : 'rgba(52,152,219,0.25)',
                    color: isClosed ? '#e74c3c' : '#3498db',
                    weight: 2, fillOpacity: 0.25, opacity: 0.8
                }};
            }},
            onEachFeature: function(feature, layer) {{
                if (feature.properties && feature.properties.school) {{
                    layer.bindPopup('<b>Walk Zone:</b> ' + feature.properties.school);
                }}
            }}
        }}).addTo(map);
    }}

    // --- Zone polygons layer ---
    function updateZonePolygons(map, scenario, mode, show) {{
        if (zoneLayer) {{ map.removeLayer(zoneLayer); zoneLayer = null; }}
        if (!show) return;
        var key = scenario + '|' + mode;
        var geo = ZONE_POLYGONS[key];
        if (!geo || !geo.features) return;
        var colors = [
            '#1f77b4','#ff7f0e','#2ca02c','#d62728','#9467bd',
            '#8c564b','#e377c2','#7f7f7f','#bcbd22','#17becf','#aec7e8'
        ];
        var schoolColors = {{}};
        var ci = 0;
        geo.features.forEach(function(f) {{
            var s = f.properties.school;
            if (!schoolColors[s]) {{ schoolColors[s] = colors[ci % colors.length]; ci++; }}
        }});
        zoneLayer = L.geoJSON(geo, {{
            style: function(feature) {{
                return {{
                    fillColor: 'transparent',
                    color: schoolColors[feature.properties.school] || '#ccc',
                    weight: 2.5, fillOpacity: 0, opacity: 0.8
                }};
            }},
            onEachFeature: function(feature, layer) {{
                layer.bindPopup('<b>Zone:</b> ' + feature.properties.school);
            }}
        }}).addTo(map);
    }}

    // --- Initialize ---
    function initMap(map) {{
        if (initialized) return;
        initialized = true;

        // Create custom pane so heatmap renders below polygons and markers
        map.createPane('heatmapPane');
        map.getPane('heatmapPane').style.zIndex = 250;
        map.getPane('heatmapPane').style.pointerEvents = 'none';

        // Initialize pre-rendered heatmap overlays (all start hidden)
        initOverlays(map);

        // Road layer for Part 2
        roadLayer = L.geoJSON(ROAD_GEOJSON, {{
            style: {{ color: 'transparent', weight: 0, opacity: 0 }},
            onEachFeature: function(feature, layer) {{
                layer.on('mouseover', function(e) {{
                    var idx = feature.properties.idx;
                    var name = feature.properties.name || 'Unnamed road';
                    var hw = feature.properties.highway || '';
                    var lines = '<b>' + name + '</b> (' + hw + ')';
                    if (currentTrafficArr && idx < currentTrafficArr.length) {{
                        var val = currentTrafficArr[idx];
                        if (val !== 0) {{
                            lines += '<br>Students: ' + val.toFixed(1);
                        }}
                    }}
                    tooltip.innerHTML = lines;
                    tooltip.style.left = (e.originalEvent.pageX + 15) + 'px';
                    tooltip.style.top = (e.originalEvent.pageY - 10) + 'px';
                    tooltip.style.display = 'block';
                }});
                layer.on('mouseout', function() {{ tooltip.style.display = 'none'; }});
            }}
        }}).addTo(map);

        // Grid hover for Part 1
        var currentGridResult = null;
        window._setGridResult = function(r) {{ currentGridResult = r; }};

        map.on('mousemove', function(e) {{
            if (activeTab !== 'part1' || !currentGridResult || !GRID_META) return;
            var lat = e.latlng.lat, lon = e.latlng.lng;
            var fracX = (lon - GRID_META.lonMin) / (GRID_META.lonMax - GRID_META.lonMin);
            var fracY = (lat - GRID_META.latMin) / (GRID_META.latMax - GRID_META.latMin);
            var col = Math.floor(fracX * GRID_META.nCols);
            var row = Math.floor((1 - fracY) * GRID_META.nRows);
            if (row < 0 || row >= GRID_META.nRows || col < 0 || col >= GRID_META.nCols) {{
                tooltip.style.display = 'none'; return;
            }}
            var idx = row * GRID_META.nCols + col;
            var val = currentGridResult.values[idx];
            var name = currentGridResult.names ? currentGridResult.names[idx] : null;
            if (isNaN(val) || val === null) {{ tooltip.style.display = 'none'; return; }}

            var view = getSelectedValue('p1-view');
            var lines = [];
            if (name) lines.push('Nearest: ' + name);
            if (view === 'delta') {{
                lines.push('+' + val.toFixed(1) + ' min increase');
            }} else {{
                lines.push(val.toFixed(1) + ' min');
            }}
            tooltip.innerHTML = lines.join('<br>');
            tooltip.style.left = (e.originalEvent.pageX + 15) + 'px';
            tooltip.style.top = (e.originalEvent.pageY - 10) + 'px';
            tooltip.style.display = 'block';
        }});
        map.on('mouseout', function() {{ tooltip.style.display = 'none'; }});
    }}

    // --- Tab switching ---
    window.switchTab = function(tab) {{
        activeTab = tab;
        document.querySelectorAll('.tab-btn').forEach(function(b) {{ b.classList.remove('active'); }});
        document.querySelectorAll('.tab-content').forEach(function(c) {{ c.classList.remove('active'); }});
        if (tab === 'part1') {{
            document.querySelectorAll('.tab-btn')[0].classList.add('active');
            document.getElementById('tab-part1').classList.add('active');
            // Hide road layer
            if (roadLayer) roadLayer.eachLayer(function(l) {{
                l.setStyle({{ color: 'transparent', weight: 0, opacity: 0 }});
            }});
            updateWalkZones(getMap(), [], false);
            window.updatePart1();
        }} else {{
            document.querySelectorAll('.tab-btn')[1].classList.add('active');
            document.getElementById('tab-part2').classList.add('active');
            // Hide canvas overlay and Part 1 network layer
            clearHeatmapOverlay();
            if (networkLayer) {{ getMap().removeLayer(networkLayer); networkLayer = null; }}
            if (zoneLayer) {{ getMap().removeLayer(zoneLayer); zoneLayer = null; }}
            window.updatePart2();
        }}
    }};

    // --- Part 1 update ---
    window.updatePart1 = function() {{
        var map = getMap();
        if (!map) return;
        initMap(map);

        var scenario = getSelectedValue('p1-scenario');
        var mode = getSelectedValue('p1-mode');
        var view = getSelectedValue('p1-view');
        var showZones = document.getElementById('p1-show-zones').checked;
        if (!scenario || !mode) return;

        var closedSchools = SCENARIOS[scenario] || [];

        // Disable delta for baseline
        var deltaRadio = document.querySelector('input[name="p1-view"][value="delta"]');
        if (scenario === 'baseline') {{
            if (view === 'delta') {{
                document.querySelector('input[name="p1-view"][value="abs"]').checked = true;
                view = 'abs';
            }}
            deltaRadio.disabled = true;
            deltaRadio.parentElement.style.opacity = '0.4';
        }} else {{
            deltaRadio.disabled = false;
            deltaRadio.parentElement.style.opacity = '1';
        }}

        // Show pre-rendered overlay
        var overlayKey = scenario + '|' + mode + '|' + view;
        showOverlay(overlayKey);

        // Compute hover grid (client-side from per-school grids)
        if (view === 'abs') {{
            var result = computeNearestSchoolGrid(mode, closedSchools);
            if (result) window._setGridResult(result);
        }} else {{
            var baseResult = computeNearestSchoolGrid(mode, []);
            var closureResult = computeNearestSchoolGrid(mode, closedSchools);
            if (closureResult && baseResult) {{
                var nPx = closureResult.values.length;
                var delta = new Float32Array(nPx);
                for (var i = 0; i < nPx; i++) {{
                    var cv = closureResult.values[i];
                    var bv = baseResult.values[i];
                    if (isNaN(cv) || isNaN(bv)) {{ delta[i] = NaN; }}
                    else {{
                        var d = cv - bv;
                        delta[i] = d > 0.01 ? d : NaN;
                    }}
                }}
                var hoverResult = {{ values: delta, names: closureResult.names }};
                window._setGridResult(hoverResult);
            }}
        }}

        // Zone polygons
        updateZonePolygons(map, scenario, mode, showZones);

        // Network overlay
        var showNetwork = document.getElementById('p1-show-network').checked;
        if (networkLayer) {{ map.removeLayer(networkLayer); networkLayer = null; }}
        if (showNetwork && NETWORK_GEOJSON[mode]) {{
            networkLayer = L.geoJSON(NETWORK_GEOJSON[mode], {{
                style: {{ color: '#333', weight: 1, opacity: 0.4 }},
                interactive: false
            }}).addTo(map);
        }}

        // School markers
        updateSchoolMarkers(map, closedSchools);

        // Legend
        var ranges = MODE_RANGES[mode];
        if (view === 'delta') {{
            document.getElementById('p1-legend-label').textContent = 'Added minutes (vs baseline)';
            document.getElementById('p1-legend-bar').style.background = 'linear-gradient(to right, #fff5eb, #fdbe85, #fd8d3c, #e6550d, #a63603)';
            document.getElementById('p1-legend-min').textContent = ranges.delta[0] + ' min';
            document.getElementById('p1-legend-max').textContent = ranges.delta[1] + ' min';
        }} else {{
            document.getElementById('p1-legend-label').textContent = 'Minutes to nearest school';
            document.getElementById('p1-legend-bar').style.background = 'linear-gradient(to right, #ffffcc, #feb24c, #fd8d3c, #fc4e2a, #bd0026)';
            document.getElementById('p1-legend-min').textContent = ranges.abs[0] + ' min';
            document.getElementById('p1-legend-max').textContent = ranges.abs[1] + ' min';
        }}

        // School info
        var infoDiv = document.getElementById('p1-school-info');
        if (closedSchools.length > 0) {{
            infoDiv.innerHTML = '<span class="closed">Closed:</span> ' + closedSchools.join(', ');
        }} else {{
            infoDiv.innerHTML = 'All 11 schools open';
        }}
    }};

    // --- Part 2 update ---
    window.updatePart2 = function() {{
        var map = getMap();
        if (!map) return;
        initMap(map);

        var scenario = getSelectedValue('p2-scenario');
        var ageGroup = getSelectedValue('p2-age');
        var routing = getSelectedValue('p2-routing');
        var view = getSelectedValue('p2-view');
        var wzMask = getSelectedValue('p2-wzmask') === 'yes';
        if (!scenario || !ageGroup || !routing) return;

        var closedSchools = SCENARIOS[scenario] || [];

        // Disable diff for baseline
        var diffRadio = document.querySelector('input[name="p2-view"][value="diff"]');
        if (scenario === 'baseline') {{
            if (view === 'diff') {{
                document.querySelector('input[name="p2-view"][value="abs"]').checked = true;
                view = 'abs';
            }}
            diffRadio.disabled = true;
            diffRadio.parentElement.style.opacity = '0.4';
        }} else {{
            diffRadio.disabled = false;
            diffRadio.parentElement.style.opacity = '1';
        }}

        // Walk zone masking: all or nothing
        var maskedSchools = wzMask ? SCHOOL_NAMES.slice() : [];

        // Get unmasked traffic array
        var tKey = scenario + '|' + routing + '|' + ageGroup;
        var arr = getTrafficArray(tKey);
        if (!arr || !roadLayer) return;

        // Apply walk zone masking client-side
        var displayed = new Float32Array(arr);
        for (var mi = 0; mi < maskedSchools.length; mi++) {{
            var wzKey = tKey + '|' + maskedSchools[mi];
            var contrib = WZ_CONTRIBUTIONS[wzKey];
            if (contrib) {{
                for (var edgeIdx in contrib) {{
                    var ei = parseInt(edgeIdx);
                    var cv = contrib[edgeIdx];
                    var ageKey = 'children_' + ageGroup;
                    if (cv[ageKey] && ei < displayed.length) {{
                        displayed[ei] = Math.max(0, displayed[ei] - cv[ageKey]);
                    }}
                }}
            }}
        }}

        if (view === 'diff' && scenario !== 'baseline') {{
            // Difference: closure - baseline
            var baseKey = 'baseline|' + routing + '|' + ageGroup;
            var baseArr = getTrafficArray(baseKey);
            if (!baseArr) return;

            // Apply same masking to baseline
            var baseDisplayed = new Float32Array(baseArr);
            for (var mi = 0; mi < maskedSchools.length; mi++) {{
                var wzKeyB = 'baseline|' + routing + '|' + ageGroup + '|' + maskedSchools[mi];
                var contribB = WZ_CONTRIBUTIONS[wzKeyB];
                if (contribB) {{
                    for (var edgeIdx in contribB) {{
                        var ei = parseInt(edgeIdx);
                        var cv = contribB[edgeIdx];
                        var ageKey = 'children_' + ageGroup;
                        if (cv[ageKey] && ei < baseDisplayed.length) {{
                            baseDisplayed[ei] = Math.max(0, baseDisplayed[ei] - cv[ageKey]);
                        }}
                    }}
                }}
            }}

            var diffArr = new Float32Array(N_EDGES);
            var maxDiff = 0;
            for (var i = 0; i < N_EDGES; i++) {{
                diffArr[i] = displayed[i] - baseDisplayed[i];
                if (Math.abs(diffArr[i]) > maxDiff) maxDiff = Math.abs(diffArr[i]);
            }}
            var sorted = Array.from(diffArr).map(Math.abs).filter(function(v){{return v>0}}).sort(function(a,b){{return a-b}});
            var p95 = sorted.length > 0 ? sorted[Math.floor(sorted.length * 0.95)] : 1;
            if (p95 < 0.1) p95 = maxDiff || 1;

            roadLayer.eachLayer(function(layer) {{
                var idx = layer.feature.properties.idx;
                var val = idx < diffArr.length ? diffArr[idx] : 0;
                layer.setStyle(trafficColor(val, p95, true));
            }});

            document.getElementById('p2-legend-title').textContent = 'Traffic Difference';
            document.getElementById('p2-legend-bar').style.background = 'linear-gradient(to right, #3182bd, #fff, #d73027)';
            document.getElementById('p2-legend-min').textContent = '-' + p95.toFixed(1);
            document.getElementById('p2-legend-max').textContent = '+' + p95.toFixed(1);
            currentTrafficArr = diffArr;
        }} else {{
            // Absolute
            var nonzero = [];
            for (var i = 0; i < displayed.length; i++) {{
                if (displayed[i] > 0) nonzero.push(displayed[i]);
            }}
            nonzero.sort(function(a,b){{return a-b}});
            var p95 = nonzero.length > 0 ? nonzero[Math.floor(nonzero.length * 0.95)] : 1;
            if (p95 < 0.1) p95 = 1;

            roadLayer.eachLayer(function(layer) {{
                var idx = layer.feature.properties.idx;
                var val = idx < displayed.length ? displayed[idx] : 0;
                layer.setStyle(trafficColor(val, p95, false));
            }});

            var ageLabel = ageGroup === '5_9' ? '5-9' : '0-4';
            document.getElementById('p2-legend-title').textContent = 'Children ' + ageLabel + ' traffic';
            document.getElementById('p2-legend-bar').style.background = 'linear-gradient(to right, #ffffcc, #fd8d3c, #bd0026)';
            document.getElementById('p2-legend-min').textContent = '0';
            document.getElementById('p2-legend-max').textContent = p95.toFixed(1);
            currentTrafficArr = displayed;
        }}

        // Walk zone polygons — separate toggle from masking
        var showWZ = getSelectedValue('p2-showwz') === 'yes';
        updateWalkZones(map, closedSchools, showWZ);

        // School markers
        updateSchoolMarkers(map, closedSchools);

        // School info
        var infoDiv = document.getElementById('p2-school-info');
        if (closedSchools.length > 0) {{
            infoDiv.innerHTML = '<span class="closed">Closed:</span> ' + closedSchools.join(', ');
        }} else {{
            infoDiv.innerHTML = 'All 11 schools open';
        }}
    }};

    // --- Populate controls ---
    function populateScenarioList(containerId, radioName, onchangeFn) {{
        var container = document.getElementById(containerId);
        var first = true;
        for (var key in SCENARIO_LABELS) {{
            var label = document.createElement('label');
            var radio = document.createElement('input');
            radio.type = 'radio';
            radio.name = radioName;
            radio.value = key;
            radio.onchange = function() {{
                // Highlight selected label
                container.querySelectorAll('label').forEach(function(l) {{ l.classList.remove('selected'); }});
                this.parentElement.classList.add('selected');
                onchangeFn();
            }};
            if (first) {{ radio.checked = true; label.classList.add('selected'); first = false; }}
            label.appendChild(radio);
            label.appendChild(document.createTextNode(' ' + SCENARIO_LABELS[key]));
            container.appendChild(label);
        }}
    }}
    populateScenarioList('p1-scenario-list', 'p1-scenario', function() {{ window.updatePart1(); }});
    populateScenarioList('p2-scenario-list', 'p2-scenario', function() {{ window.updatePart2(); }});

    // Part 1 mode radios
    var modeDiv = document.getElementById('p1-mode-options');
    var first = true;
    for (var key in MODE_LABELS) {{
        var label = document.createElement('label');
        var radio = document.createElement('input');
        radio.type = 'radio';
        radio.name = 'p1-mode';
        radio.value = key;
        radio.onchange = function() {{ window.updatePart1(); }};
        if (first) {{ radio.checked = true; first = false; }}
        label.appendChild(radio);
        label.appendChild(document.createTextNode(' ' + MODE_LABELS[key]));
        modeDiv.appendChild(label);
    }}

    // Layout initialization
    setTimeout(function() {{
        var mapDiv = document.querySelector('.folium-map');
        var controls = document.getElementById('closure-controls');
        if (mapDiv) {{
            document.documentElement.style.cssText = 'height:100vh;margin:0;overflow:hidden';
            document.body.style.cssText = 'display:flex;flex-direction:row;height:100vh;margin:0;overflow:hidden';
            var wrapper = document.createElement('div');
            wrapper.id = 'main-column';
            mapDiv.parentNode.insertBefore(wrapper, mapDiv);
            wrapper.appendChild(mapDiv);
            mapDiv.style.cssText += ';flex:1;height:100vh;position:relative;';
            if (controls) document.body.appendChild(controls);
            var map = getMap();
            if (map) setTimeout(function() {{ map.invalidateSize(); }}, 100);
        }}
        window.updatePart2();
    }}, 500);
}})();
</script>
"""


# ═══════════════════════════════════════════════════════════════════════════
# Section 12: Main Pipeline
# ═══════════════════════════════════════════════════════════════════════════

def main():
    parser = argparse.ArgumentParser(description="School Closure Impact Analysis")
    parser.add_argument("--cache-only", action="store_true",
                        help="Use cached data only; skip downloads")
    parser.add_argument("--skip-traffic", action="store_true",
                        help="Skip Part 2 traffic analysis")
    parser.add_argument("--mode", choices=["drive", "bike", "walk"],
                        help="Single mode (default: all three)")
    args = parser.parse_args()

    print("=" * 60)
    print("School Closure Impact Analysis")
    print("=" * 60)

    ensure_directories()
    _check_grid_version()
    cache_only = args.cache_only
    modes = [args.mode] if args.mode else ["drive", "bike", "walk"]

    # ── Step 1: Load data ────────────────────────────────────────────
    print("\n[1/10] Loading school locations ...")
    schools = load_schools()

    print("\n[2/10] Loading district boundary ...")
    district_gdf = load_district_boundary(schools)
    district_polygon = district_gdf.union_all()

    print("\n[3/10] Loading walk zones and attendance zones ...")
    walk_zones_gdf = load_walk_zones()
    attendance_zones = load_attendance_zones()

    # ── Step 2: Load networks ────────────────────────────────────────
    print("\n[4/10] Loading road networks ...")
    graphs = {}
    for mode in modes:
        graphs[mode] = load_network(mode)

    # Build road GeoJSON with IDs (drive network for traffic)
    road_geojson, edge_id_map = _graph_to_geojson_with_ids(
        graphs.get("drive", graphs[modes[0]])
    )
    n_edges = len(road_geojson["features"])
    _progress(f"Road GeoJSON: {n_edges} edges")

    # Build lightweight display GeoJSON per mode (for Part 1 network overlay)
    network_geojson: dict[str, dict] = {"drive": road_geojson}
    for mode in modes:
        if mode != "drive":
            network_geojson[mode] = _graph_to_display_geojson(graphs[mode])
            _progress(
                f"  {mode} network: "
                f"{len(network_geojson[mode]['features'])} edges (display)"
            )

    # ── Step 3: Create grid ──────────────────────────────────────────
    print("\n[5/10] Creating analysis grid ...")
    grid_cache = CLOSURE_CACHE / "pixel_grid.csv"
    if grid_cache.exists():
        _progress(f"Loading cached grid: {grid_cache.name}")
        grid_df = pd.read_csv(grid_cache)
        grid = gpd.GeoDataFrame(
            grid_df,
            geometry=gpd.points_from_xy(grid_df.lon, grid_df.lat),
            crs=CRS_WGS84,
        )
    else:
        grid = create_grid(district_polygon)
        grid[["grid_id", "lat", "lon"]].to_csv(grid_cache, index=False)

    # Inject school anchor points
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
    grid = gpd.GeoDataFrame(
        pd.concat([grid, school_pts], ignore_index=True),
        crs=CRS_WGS84,
    )
    school_anchor_ids = dict(zip(
        range(max_grid_id + 1, max_grid_id + 1 + len(schools)),
        schools["school"].values,
    ))
    _progress(f"Grid: {len(grid)} points (incl. {len(schools)} school anchors)")

    # ── Step 4: Edge snapping ────────────────────────────────────────
    print("\n[6/10] Edge-snapping grid points ...")
    snaps = {}
    for mode in modes:
        snaps[mode] = snap_grid_to_edges(grid, graphs[mode], mode)

    # ── Step 5: Dijkstra with predecessors ───────────────────────────
    print("\n[7/10] Computing Dijkstra (predecessors + distances) ...")
    dijkstra_by_mode = {}
    for mode in modes:
        dijkstra_by_mode[mode] = compute_dijkstra_with_predecessors(
            graphs[mode], schools, mode
        )

    # ── Step 6: Per-school grids, pixel assignments & zone polygons ──
    print("\n[8/10] Computing per-school grids and zone polygons ...")
    all_schools = list(dijkstra_by_mode[modes[0]].keys())
    all_results = []
    zone_polygons = {}
    grid_meta = None

    # Pre-compute shared grid params for rasterization
    unique_pts = grid[["lat", "lon"]].drop_duplicates()
    _all_lats = unique_pts["lat"].values
    _all_lons = unique_pts["lon"].values
    _center_lat = _all_lats.mean()
    _dlat = GRID_RESOLUTION_M / 111_320.0
    _dlon = GRID_RESOLUTION_M / (111_320.0 * np.cos(np.radians(_center_lat)))
    _minlon = _all_lons.min() - _dlon / 2
    _maxlat = _all_lats.max() + _dlat / 2
    _maxlon = _all_lons.max() + _dlon / 2
    _minlat = _all_lats.min() - _dlat / 2
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

    # Compute per-school travel time grids (for client-side rendering)
    per_school_grids = {}  # {mode: {school: base64}}
    for mode in modes:
        grids_b64, meta = compute_per_school_grids(
            snaps[mode], dijkstra_by_mode[mode], grid,
            all_schools, school_anchor_ids,
            district_polygon, shared_grid_params,
        )
        per_school_grids[mode] = grids_b64
        if meta is not None and grid_meta is None:
            grid_meta = meta
        _progress(f"  {mode}: {len(grids_b64)} per-school grids computed")

    # Store pixel assignment results for traffic analysis + zone polygons
    pixel_assignments = {}

    for scenario_name, closed_schools in SCENARIOS.items():
        open_schools = [s for s in all_schools if s not in closed_schools]

        for mode in modes:
            _progress(f"  {scenario_name} / {mode} ({len(open_schools)} open schools) ...")

            min_times, nearest_schools, entry_nodes = assign_pixels_to_schools(
                snaps[mode], dijkstra_by_mode[mode], open_schools,
            )

            # Zero out school anchor points when school is open
            grid_ids = grid["grid_id"].values
            for gid, sname in school_anchor_ids.items():
                if sname in open_schools:
                    idx = np.where(grid_ids == gid)[0]
                    if len(idx) > 0:
                        min_times[idx[0]] = 0.0
                        nearest_schools[idx[0]] = sname

            pixel_assignments[(scenario_name, mode)] = (min_times, nearest_schools, entry_nodes)

            # Build result rows for CSV output
            min_minutes = min_times / 60.0
            min_minutes[np.isinf(min_minutes)] = np.nan

            result_df = grid[["grid_id", "lat", "lon"]].copy()
            result_df["scenario"] = scenario_name
            result_df["mode"] = mode
            result_df["min_time_seconds"] = min_times
            result_df["nearest_school"] = nearest_schools
            result_df["min_time_minutes"] = min_minutes
            all_results.append(result_df)

            # Build zone polygons
            zone_gdf = build_zone_polygons(
                grid, nearest_schools, snaps[mode].reachable, district_gdf,
            )
            if zone_gdf is not None:
                zone_polygons[f"{scenario_name}|{mode}"] = json.loads(
                    zone_gdf.to_json()
                )

    # Save assignments CSV
    scores_df = pd.concat(all_results, ignore_index=True)
    baseline = scores_df[scores_df["scenario"] == "baseline"][
        ["grid_id", "mode", "min_time_seconds"]
    ].rename(columns={"min_time_seconds": "baseline_time"})
    scores_df = scores_df.merge(baseline, on=["grid_id", "mode"], how="left")
    scores_df["delta_seconds"] = scores_df["min_time_seconds"] - scores_df["baseline_time"]
    scores_df["delta_minutes"] = scores_df["delta_seconds"] / 60.0

    csv_path = DATA_PROCESSED / "school_closure_assignments.csv"
    scores_df.to_csv(csv_path, index=False)
    _progress(f"Saved {len(scores_df)} assignment rows to {csv_path.name}")

    # Pre-render heatmap overlays in Python (proven alignment via plt.imsave)
    _progress("Pre-rendering heatmap overlays ...")
    heatmap_overlays = {}  # {scenario|mode|view: (base64_png, bounds)}
    common_bounds = None
    for scenario_name in SCENARIOS:
        for mode in modes:
            subset = scores_df[
                (scores_df["scenario"] == scenario_name) & (scores_df["mode"] == mode)
            ].copy()

            # Absolute travel time layer
            vmin, vmax = MODE_RANGES[mode]["abs"]
            vals_2d, meta, bounds = rasterize_grid(
                subset, "min_time_minutes",
                district_polygon=district_polygon,
                grid_params=shared_grid_params,
            )
            if bounds is not None and common_bounds is None:
                common_bounds = bounds

            b64 = colorize_raster(vals_2d, vmin, vmax, "YlOrRd")
            heatmap_overlays[f"{scenario_name}|{mode}|abs"] = (b64, bounds)

            # Delta layer (skip baseline — delta is zero)
            if scenario_name != "baseline":
                vmin_d, vmax_d = MODE_RANGES[mode]["delta"]
                # Filter out non-positive deltas for cleaner visualization
                delta_col = subset["delta_minutes"].copy()
                delta_col[delta_col <= 0.01] = np.nan
                subset_d = subset.copy()
                subset_d["delta_pos"] = delta_col
                vals_d, meta_d, bounds_d = rasterize_grid(
                    subset_d, "delta_pos",
                    district_polygon=district_polygon,
                    grid_params=shared_grid_params,
                )
                b64_d = colorize_raster(vals_d, vmin_d, vmax_d, "Oranges")
                heatmap_overlays[f"{scenario_name}|{mode}|delta"] = (b64_d, bounds_d)

    n_overlays = sum(1 for v in heatmap_overlays.values() if v[0] is not None)
    _progress(f"Pre-rendered {n_overlays} heatmap overlays")

    # ── Step 7: Traffic analysis (Part 2) ────────────────────────────
    traffic_arrays = {}
    walk_zone_contributions = {}

    if not args.skip_traffic and "drive" in modes:
        print("\n[9/10] Computing traffic analysis ...")

        # Distribute children to pixels
        pixel_children = distribute_children_to_pixels(
            grid, district_gdf, cache_only=cache_only,
        )

        # Assign pixels to attendance zones and walk zones
        zone_schools = assign_pixels_to_zones(grid, attendance_zones)
        pixel_walk_zone = precompute_pixel_walk_zones(grid, walk_zones_gdf)

        # Compute UNMASKED traffic for all scenarios × zone combinations
        # Walk zone masking is now handled client-side via sparse contributions
        for scenario_name, closed_schools in SCENARIOS.items():
            open_schools = [s for s in all_schools if s not in closed_schools]
            min_times, nearest_schools, entry_nodes = pixel_assignments[(scenario_name, "drive")]

            for zone_enabled in [False, True]:
                zone_str = "zone" if zone_enabled else "nearest"

                _progress(f"  Traffic: {scenario_name} / {zone_str} ...")

                zs = zone_schools if zone_enabled else None

                traffic, wz_contribs = compute_traffic(
                    grid, snaps["drive"], dijkstra_by_mode["drive"],
                    pixel_children, edge_id_map,
                    open_schools, entry_nodes, nearest_schools,
                    walk_zones_gdf=walk_zones_gdf,
                    zone_schools=zs if zone_enabled else None,
                    closed_schools=closed_schools if zone_enabled else None,
                    pixel_walk_zone=pixel_walk_zone,
                )

                # Encode unmasked traffic as Float32Array for each age group
                for age_group in ["0_4", "5_9"]:
                    arr = np.zeros(n_edges, dtype=np.float32)
                    for feat_idx, counts in traffic.items():
                        child_key = f"children_{age_group}"
                        arr[feat_idx] = counts.get(child_key, 0)

                    key = f"{scenario_name}|{zone_str}|{age_group}"
                    traffic_arrays[key] = base64.b64encode(
                        arr.tobytes()
                    ).decode("utf-8")

                    # Encode per-walk-zone contributions as sparse JSON
                    for wz_school, wz_edges in wz_contribs.items():
                        sparse = {}
                        for feat_idx, edge_counts in wz_edges.items():
                            c = edge_counts.get(f"children_{age_group}", 0)
                            if c > 0.001:
                                sparse[str(feat_idx)] = {
                                    f"children_{age_group}": round(c, 4)
                                }
                        if sparse:
                            wz_key = f"{key}|{wz_school}"
                            walk_zone_contributions[wz_key] = sparse

        _progress(f"  Traffic arrays: {len(traffic_arrays)}, "
                  f"walk zone contribution sets: {len(walk_zone_contributions)}")

        # Save traffic CSV (unmasked only)
        traffic_rows = []
        for key, b64 in traffic_arrays.items():
            parts = key.split("|")
            scenario, zone, age = parts
            arr = np.frombuffer(base64.b64decode(b64), dtype=np.float32)
            for idx in range(len(arr)):
                if arr[idx] > 0:
                    traffic_rows.append({
                        "edge_idx": idx,
                        "scenario": scenario,
                        "zone": zone,
                        "age_group": age,
                        "children": float(arr[idx]),
                    })
        if traffic_rows:
            traffic_csv = DATA_PROCESSED / "school_closure_traffic.csv"
            pd.DataFrame(traffic_rows).to_csv(traffic_csv, index=False)
            _progress(f"Saved {len(traffic_rows)} traffic rows to {traffic_csv.name}")
    else:
        print("\n[9/10] Skipping traffic analysis")

    # ── Step 8: Build walk zones GeoJSON ─────────────────────────────
    walk_zones_geojson = None
    if walk_zones_gdf is not None:
        features = []
        for _, row in walk_zones_gdf.iterrows():
            features.append({
                "type": "Feature",
                "geometry": json.loads(
                    gpd.GeoSeries([row.geometry]).to_json()
                )["features"][0]["geometry"],
                "properties": {"school": row["school"]},
            })
        walk_zones_geojson = {"type": "FeatureCollection", "features": features}

    # ── Step 9: Build map ────────────────────────────────────────────
    print("\n[10/10] Building interactive map ...")
    m = create_map(
        heatmap_overlays=heatmap_overlays,
        per_school_grids=per_school_grids,
        grid_meta=grid_meta,
        schools=schools,
        district_gdf=district_gdf,
        zone_polygons=zone_polygons,
        road_geojson=road_geojson,
        traffic_arrays=traffic_arrays,
        walk_zone_contributions=walk_zone_contributions,
        n_edges=n_edges,
        walk_zones_geojson=walk_zones_geojson,
        network_geojson=network_geojson,
    )

    map_path = ASSETS_MAPS / "school_closure_analysis.html"
    m.save(str(map_path))
    size_mb = map_path.stat().st_size / 1e6
    _progress(f"Saved map to {map_path} ({size_mb:.1f} MB)")

    print("\n" + "=" * 60)
    print("School Closure Impact Analysis complete!")
    print(f"  Map: {map_path}")
    print(f"  Assignments: {DATA_PROCESSED / 'school_closure_assignments.csv'}")
    if not args.skip_traffic:
        print(f"  Traffic: {DATA_PROCESSED / 'school_closure_traffic.csv'}")
    print("=" * 60)


if __name__ == "__main__":
    main()
