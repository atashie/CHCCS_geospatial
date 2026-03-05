"""closure_story.py — Scrollytelling methodology page for school closure impact analysis.

Generates a self-contained HTML page (closure_methodology.html) that walks
non-technical readers through the school closure analysis methodology:
  - Travel-time computation (Dijkstra, edge snapping, 100 m grid)
  - Dasymetric child distribution (ACS → block group → block → pixel)
  - Traffic aggregation and walk-zone masking
  - Limitations and citations

Uses Northside Elementary as the illustrative example (centrally located,
clear redistribution impacts). Identical methodology applies to all 11 schools.

Requires school_closure_analysis.py to have been run first (reads from its caches).

Usage:
    python src/closure_story.py
    python src/closure_story.py --cache-only   # same behavior (always cache-only)
"""

import argparse
import base64
import io
import json
import pickle
import warnings
from math import cos, radians
from pathlib import Path

import geopandas as gpd
import matplotlib
import matplotlib.colors as mcolors
import matplotlib.pyplot as plt
import networkx as nx
import numpy as np
import osmnx as ox
import pandas as pd
from PIL import Image
from shapely.geometry import LineString, Point, mapping

warnings.filterwarnings("ignore", category=FutureWarning)
matplotlib.use("Agg")

# ═══════════════════════════════════════════════════════════════════════════
# Paths
# ═══════════════════════════════════════════════════════════════════════════
PROJECT_ROOT = Path(__file__).parent.parent
DATA_RAW = PROJECT_ROOT / "data" / "raw"
DATA_PROCESSED = PROJECT_ROOT / "data" / "processed"
DATA_CACHE = PROJECT_ROOT / "data" / "cache"
CLOSURE_CACHE = DATA_CACHE / "closure_analysis"
ASSETS_MAPS = PROJECT_ROOT / "assets" / "maps"
OUTPUT_HTML = ASSETS_MAPS / "closure_methodology.html"

SCHOOL_CSV = DATA_CACHE / "nces_school_locations.csv"
DISTRICT_CACHE = DATA_CACHE / "chccs_district_boundary.gpkg"
CHCCS_SHP = DATA_RAW / "properties" / "CHCCS" / "CHCCS.shp"
PARCEL_POLYS = DATA_RAW / "properties" / "combined_data_polys.gpkg"

# ═══════════════════════════════════════════════════════════════════════════
# Constants
# ═══════════════════════════════════════════════════════════════════════════
CRS_WGS84 = "EPSG:4326"
CRS_UTM17N = "EPSG:32617"
CHAPEL_HILL_CENTER = [35.9132, -79.0558]

NORTHSIDE_NAME = "Northside Elementary"
NORTHSIDE_BBOX_PAD = 0.020  # ±0.02° around Northside for local views

# Speed model (mirrors school_closure_analysis.py)
WALK_SPEED_MPS = 1.12   # 2.5 mph — MUTCD 4E.06
BIKE_SPEED_MPS = 5.36   # 12 mph
DEFAULT_DRIVE_EFFECTIVE_MPH = 18
DRIVE_EFFECTIVE_SPEEDS_MPH = {
    "motorway": 60, "motorway_link": 50,
    "trunk": 40, "trunk_link": 35,
    "primary": 30, "primary_link": 25,
    "secondary": 25, "secondary_link": 22,
    "tertiary": 22, "tertiary_link": 18,
    "residential": 18, "living_street": 10,
    "service": 10, "unclassified": 18,
}
ACCESS_SPEED_FACTORS = {"walk": 0.9, "bike": 0.8, "drive": 0.2}
GRID_RESOLUTION_M = 100

MODE_RANGES = {
    "drive": {"abs": (0, 15), "delta": (0, 10)},
    "walk":  {"abs": (0, 60), "delta": (0, 30)},
}

# Road display colors by highway type
ROAD_COLORS = {
    "motorway": "#e41a1c", "motorway_link": "#e41a1c",
    "trunk": "#ff7f00", "trunk_link": "#ff7f00",
    "primary": "#984ea3", "primary_link": "#984ea3",
    "secondary": "#377eb8", "secondary_link": "#377eb8",
    "tertiary": "#4daf4a", "tertiary_link": "#4daf4a",
    "residential": "#999999", "living_street": "#999999",
    "service": "#cccccc", "unclassified": "#999999",
}

# Posted vs effective speeds for narrative table
ROAD_SPEED_TABLE = [
    ("Motorway / Freeway", 65, 60),
    ("Trunk", 55, 40),
    ("Primary", 45, 30),
    ("Secondary", 35, 25),
    ("Tertiary", 25, 22),
    ("Residential", 25, 18),
    ("Service / Living street", 15, 10),
]


# ═══════════════════════════════════════════════════════════════════════════
# Utility functions
# ═══════════════════════════════════════════════════════════════════════════

def _progress(msg: str):
    print(f"  ... {msg}")


def grid_to_base64_png(grid: np.ndarray, colormap: str = "YlOrRd",
                       vmin: float = None, vmax: float = None) -> str:
    """Convert 2D numpy grid to base64 PNG data URL."""
    nonzero = grid[grid > 0]
    if vmin is None:
        vmin = np.percentile(nonzero, 5) if len(nonzero) > 0 else 0
    if vmax is None:
        vmax = np.percentile(nonzero, 95) if len(nonzero) > 0 else 1

    norm = mcolors.Normalize(vmin=vmin, vmax=vmax, clip=True)
    cmap = plt.get_cmap(colormap)
    normalized = norm(grid)
    mapped = cmap(normalized)  # (ny, nx, 4) float

    ny, nx_val = grid.shape
    rgba = np.zeros((ny, nx_val, 4), dtype=np.uint8)
    rgba[..., :3] = (mapped[..., :3] * 255).astype(np.uint8)
    active = grid > 0.001
    alpha_vals = np.where(
        active, np.clip(120 + 80 * normalized, 0, 255).astype(np.uint8), 0
    )
    rgba[..., 3] = alpha_vals

    img = Image.fromarray(rgba, "RGBA")
    buf = io.BytesIO()
    img.save(buf, format="PNG")
    buf.seek(0)
    b64 = base64.b64encode(buf.read()).decode()
    return f"data:image/png;base64,{b64}"


def _round_coords(geom_dict: dict, precision: int = 4) -> dict:
    """Round coordinates in a GeoJSON geometry dict to reduce size."""
    def _round(coords):
        if isinstance(coords[0], (list, tuple)):
            return [_round(c) for c in coords]
        return [round(c, precision) for c in coords]

    result = dict(geom_dict)
    if "coordinates" in result:
        result["coordinates"] = _round(result["coordinates"])
    return result


def gdf_to_geojson_str(gdf: gpd.GeoDataFrame, properties: list = None,
                       simplify_m: float = None) -> str:
    """Convert GeoDataFrame to compact GeoJSON string."""
    if len(gdf) == 0:
        return '{"type":"FeatureCollection","features":[]}'
    gdf = gdf.to_crs(CRS_WGS84)
    if simplify_m:
        gdf = gdf.copy()
        gdf_utm = gdf.to_crs(CRS_UTM17N)
        gdf_utm["geometry"] = gdf_utm.geometry.simplify(
            simplify_m, preserve_topology=True
        )
        gdf = gdf_utm.to_crs(CRS_WGS84)
    features = []
    for _, row in gdf.iterrows():
        if row.geometry is None or row.geometry.is_empty:
            continue
        props = {}
        if properties:
            for p in properties:
                val = row.get(p)
                if pd.notna(val):
                    props[p] = (
                        float(val) if isinstance(val, (np.integer, np.floating))
                        else val
                    )
        features.append({
            "type": "Feature",
            "geometry": _round_coords(mapping(row.geometry)),
            "properties": props,
        })
    fc = {"type": "FeatureCollection", "features": features}
    return json.dumps(fc, separators=(",", ":"))


# ═══════════════════════════════════════════════════════════════════════════
# Data loading functions
# ═══════════════════════════════════════════════════════════════════════════

def load_schools() -> pd.DataFrame:
    """Load NCES school locations."""
    if not SCHOOL_CSV.exists():
        raise FileNotFoundError(
            f"School CSV not found at {SCHOOL_CSV}. "
            "Run road_pollution.py first to download school data."
        )
    return pd.read_csv(SCHOOL_CSV)


def get_northside(schools: pd.DataFrame) -> dict:
    """Extract Northside Elementary location."""
    row = schools[schools["school"].str.contains("Northside", case=False)]
    if row.empty:
        raise ValueError("Northside Elementary not found in school data")
    r = row.iloc[0]
    return {"lat": r["lat"], "lon": r["lon"], "school": r["school"]}


def get_bbox(center: dict, pad: float = NORTHSIDE_BBOX_PAD) -> tuple:
    """Get bounding box around a school location."""
    return (
        center["lon"] - pad, center["lat"] - pad,
        center["lon"] + pad, center["lat"] + pad,
    )


def load_district_boundary() -> gpd.GeoDataFrame:
    """Load CHCCS district boundary polygon."""
    if not DISTRICT_CACHE.exists():
        raise FileNotFoundError(f"District boundary not found at {DISTRICT_CACHE}")
    return gpd.read_file(DISTRICT_CACHE)


def load_network_edges(mode: str, bbox: tuple) -> gpd.GeoDataFrame:
    """Load road network edges as GeoDataFrame, clipped to bbox."""
    cache_path = DATA_CACHE / f"network_{mode}.graphml"
    if not cache_path.exists():
        raise FileNotFoundError(
            f"Network graph not found at {cache_path}. "
            "Run school_desert.py first."
        )
    _progress(f"Loading {mode} network for display ...")
    G = ox.load_graphml(cache_path)

    # Extract edges with attributes
    edges = []
    seen = set()
    for u, v, key, data in G.edges(keys=True, data=True):
        canon = (min(u, v), max(u, v), key)
        if canon in seen:
            continue
        seen.add(canon)

        if "geometry" in data:
            geom = data["geometry"]
        else:
            geom = LineString([
                (G.nodes[u]["x"], G.nodes[u]["y"]),
                (G.nodes[v]["x"], G.nodes[v]["y"]),
            ])

        highway = data.get("highway", "residential")
        if isinstance(highway, list):
            highway = highway[0]

        speed_mph = DRIVE_EFFECTIVE_SPEEDS_MPH.get(highway, DEFAULT_DRIVE_EFFECTIVE_MPH)

        edges.append({
            "geometry": geom,
            "highway": highway,
            "speed_mph": speed_mph,
            "length_m": data.get("length", 0),
        })

    gdf = gpd.GeoDataFrame(edges, crs=CRS_WGS84)

    # Clip to bbox
    from shapely.geometry import box as shapely_box
    clip_poly = shapely_box(bbox[0], bbox[1], bbox[2], bbox[3])
    gdf = gdf[gdf.intersects(clip_poly)].copy()

    # Drop very short edges and service roads to reduce size
    gdf = gdf[gdf["length_m"] > 15].copy()
    if mode == "drive":
        # For display, drop service roads (too many, small)
        gdf = gdf[~gdf["highway"].isin(["service"])].copy()
    elif mode == "walk":
        # For walk, only keep non-drive paths (the interesting ones)
        gdf = gdf[~gdf["highway"].isin(["service", "living_street"])].copy()

    _progress(f"  {mode}: {len(gdf)} edges in view")
    return gdf


def load_graph(mode: str) -> nx.MultiDiGraph:
    """Load full network graph with travel_time weights (for route computation)."""
    cache_path = DATA_CACHE / f"network_{mode}.graphml"
    if not cache_path.exists():
        raise FileNotFoundError(f"Network graph not found at {cache_path}")
    G = ox.load_graphml(cache_path)

    # Add travel_time weights
    for u, v, key, data in G.edges(keys=True, data=True):
        length_m = data.get("length", 0)
        if mode == "walk":
            data["travel_time"] = length_m / WALK_SPEED_MPS
        elif mode == "bike":
            data["travel_time"] = length_m / BIKE_SPEED_MPS
        else:
            highway = data.get("highway", "residential")
            if isinstance(highway, list):
                highway = highway[0]
            speed_mph = DRIVE_EFFECTIVE_SPEEDS_MPH.get(
                highway, DEFAULT_DRIVE_EFFECTIVE_MPH
            )
            speed_mps = speed_mph * 0.44704
            data["travel_time"] = length_m / speed_mps if speed_mps > 0 else 9999

    # Ensure bidirectional
    to_add = []
    for u, v, key, data in G.edges(keys=True, data=True):
        if not G.has_edge(v, u):
            to_add.append((v, u, data.copy()))
    for v, u, data in to_add:
        G.add_edge(v, u, **data)

    return G


def load_dijkstra(mode: str) -> dict:
    """Load cached Dijkstra results for all schools."""
    cache_path = CLOSURE_CACHE / f"dijkstra_{mode}.pkl"
    if not cache_path.exists():
        raise FileNotFoundError(
            f"Dijkstra cache not found at {cache_path}. "
            "Run school_closure_analysis.py first."
        )
    _progress(f"Loading Dijkstra cache: {cache_path.name}")
    with open(cache_path, "rb") as f:
        return pickle.load(f)


def load_snap_data(mode: str):
    """Load cached edge-snap data."""
    cache_path = CLOSURE_CACHE / f"snap_{mode}.npz"
    if not cache_path.exists():
        raise FileNotFoundError(
            f"Snap cache not found at {cache_path}. "
            "Run school_closure_analysis.py first."
        )
    _progress(f"Loading snap data: {cache_path.name}")
    data = np.load(cache_path, allow_pickle=True)
    return {
        "start_nodes": data["start_nodes"],
        "end_nodes": data["end_nodes"],
        "fractions": data["fractions"],
        "edge_times": data["edge_times"],
        "access_times": data["access_times"],
        "reachable": data["reachable"],
    }


def load_pixel_grid() -> pd.DataFrame:
    """Load cached pixel grid."""
    cache_path = CLOSURE_CACHE / "pixel_grid.csv"
    if not cache_path.exists():
        raise FileNotFoundError(
            f"Pixel grid not found at {cache_path}. "
            "Run school_closure_analysis.py first."
        )
    grid = pd.read_csv(cache_path)
    return grid


def _align_snap_to_grid(snap: dict, grid: pd.DataFrame) -> dict:
    """Truncate snap arrays to match pixel grid length.

    The main analysis appends 11 school anchor points to the grid after
    saving pixel_grid.csv but before computing snap arrays. We trim those
    extra entries to keep arrays aligned with the CSV.
    """
    n = len(grid)
    n_snap = len(snap["start_nodes"])
    if n_snap == n:
        return snap
    _progress(f"  Aligning snap ({n_snap}) to grid ({n}) — trimming {n_snap - n} school anchors")
    return {k: v[:n] for k, v in snap.items()}


def load_block_groups_with_children(bbox: tuple) -> gpd.GeoDataFrame:
    """Load block groups with child population, clipped to bbox."""
    bg_path = DATA_CACHE / "tiger_blockgroups_orange.gpkg"
    children_path = CLOSURE_CACHE / "children_blockgroups.csv"
    if not bg_path.exists() or not children_path.exists():
        _progress("Block group data not found, skipping")
        return gpd.GeoDataFrame()

    bg = gpd.read_file(bg_path)
    children = pd.read_csv(children_path, dtype={"GEOID": str})
    bg = bg.merge(children, on="GEOID", how="left").fillna(0)
    bg["total_children"] = bg["children_0_4"] + bg["children_5_9"]

    # Clip to bbox
    from shapely.geometry import box as shapely_box
    clip_poly = shapely_box(bbox[0], bbox[1], bbox[2], bbox[3])
    bg = bg.to_crs(CRS_WGS84)
    bg = bg[bg.intersects(clip_poly)].copy()
    _progress(f"  {len(bg)} block groups with children data")
    return bg


def load_blocks(bbox: tuple) -> gpd.GeoDataFrame:
    """Load census blocks clipped to bbox."""
    path = DATA_CACHE / "tiger_blocks_orange.gpkg"
    if not path.exists():
        _progress("Census blocks not found, skipping")
        return gpd.GeoDataFrame()

    blocks = gpd.read_file(path).to_crs(CRS_WGS84)
    from shapely.geometry import box as shapely_box
    clip_poly = shapely_box(bbox[0], bbox[1], bbox[2], bbox[3])
    blocks = blocks[blocks.intersects(clip_poly)].copy()
    _progress(f"  {len(blocks)} census blocks in view")
    return blocks


def load_residential_parcels(bbox: tuple) -> gpd.GeoDataFrame:
    """Load residential parcels clipped to bbox (for dasymetric illustration)."""
    if not PARCEL_POLYS.exists():
        _progress("Parcel data not found, skipping")
        return gpd.GeoDataFrame()

    parcels = gpd.read_file(PARCEL_POLYS).to_crs(CRS_WGS84)
    # Filter to improved residential
    if "is_residential" in parcels.columns:
        parcels = parcels[parcels["is_residential"] == True].copy()
    if "imp_vac" in parcels.columns:
        parcels = parcels[parcels["imp_vac"].str.contains("Improved", na=False)].copy()

    from shapely.geometry import box as shapely_box
    # Use tight bbox for parcels (heavy data)
    pad = 0.005
    center_lon = (bbox[0] + bbox[2]) / 2
    center_lat = (bbox[1] + bbox[3]) / 2
    tight_bbox = (center_lon - pad, center_lat - pad,
                  center_lon + pad, center_lat + pad)
    clip_poly = shapely_box(*tight_bbox)
    parcels = parcels[parcels.intersects(clip_poly)].copy()
    _progress(f"  {len(parcels)} residential parcels in view")
    return parcels


def load_walk_zones() -> gpd.GeoDataFrame:
    """Load CHCCS walk zone polygons (ESWALK=='Y')."""
    if not CHCCS_SHP.exists():
        _progress("Walk zones shapefile not found, skipping")
        return gpd.GeoDataFrame()

    raw = gpd.read_file(CHCCS_SHP)
    walk = raw[raw["ESWALK"] == "Y"].copy()
    if walk.empty:
        return gpd.GeoDataFrame()
    walk = walk.dissolve(by="ENAME").reset_index()
    walk = walk.rename(columns={"ENAME": "school"})
    walk = walk[["school", "geometry"]].to_crs(CRS_WGS84)
    _progress(f"  {len(walk)} walk zone polygons")
    return walk


def load_pixel_children() -> pd.DataFrame:
    """Load dasymetric child distribution per pixel."""
    path = CLOSURE_CACHE / "pixel_children.csv"
    if not path.exists():
        _progress("Pixel children data not found, skipping")
        return pd.DataFrame()
    return pd.read_csv(path)


# ═══════════════════════════════════════════════════════════════════════════
# Computation functions
# ═══════════════════════════════════════════════════════════════════════════

def compute_pixel_times(snap: dict, dijkstra_results: dict,
                        open_schools: list) -> np.ndarray:
    """Compute min travel time (seconds) across open schools for each pixel."""
    n = len(snap["start_nodes"])
    best_times = np.full(n, np.inf)

    for school_name in open_schools:
        if school_name not in dijkstra_results:
            continue
        dist = dijkstra_results[school_name]["dist"]

        t_u = np.array([dist.get(int(u), np.inf) for u in snap["start_nodes"]])
        t_v = np.array([dist.get(int(v), np.inf) for v in snap["end_nodes"]])

        via_u = t_u + snap["fractions"] * snap["edge_times"]
        via_v = t_v + (1.0 - snap["fractions"]) * snap["edge_times"]

        best_via = np.minimum(via_u, via_v)
        total = best_via + snap["access_times"]

        mask = snap["reachable"] & (total < best_times)
        best_times[mask] = total[mask]

    return best_times


def rasterize_times(pixel_grid: pd.DataFrame, times_sec: np.ndarray,
                    reachable: np.ndarray) -> tuple:
    """Convert pixel-level travel times to a 2D grid + bounds.

    Returns (grid_2d, bounds) where bounds = (west, south, east, north).
    """
    lats = pixel_grid["lat"].values
    lons = pixel_grid["lon"].values

    # Grid spacing
    center_lat = np.mean(lats)
    dlat = GRID_RESOLUTION_M / 111_320.0
    dlon = GRID_RESOLUTION_M / (111_320.0 * cos(radians(center_lat)))

    min_lat, max_lat = lats.min(), lats.max()
    min_lon, max_lon = lons.min(), lons.max()

    # Grid dimensions
    n_cols = max(1, int(round((max_lon - min_lon) / dlon)) + 1)
    n_rows = max(1, int(round((max_lat - min_lat) / dlat)) + 1)

    # Map pixels to grid positions
    cols = np.clip(np.round((lons - min_lon) / dlon).astype(int), 0, n_cols - 1)
    rows = np.clip(np.round((max_lat - lats) / dlat).astype(int), 0, n_rows - 1)

    grid = np.zeros((n_rows, n_cols), dtype=np.float32)
    times_min = times_sec / 60.0
    valid = reachable & np.isfinite(times_sec) & (times_sec < 1e6)
    grid[rows[valid], cols[valid]] = times_min[valid]

    bounds = (min_lon - dlon / 2, min_lat - dlat / 2,
              max_lon + dlon / 2, max_lat + dlat / 2)
    return grid, bounds


def crop_grid(grid: np.ndarray, grid_bounds: tuple, crop_bbox: tuple):
    """Crop a 2D numpy grid to a bbox. Returns (cropped_grid, cropped_bounds)."""
    west, south, east, north = grid_bounds
    cw, cs, ce, cn = crop_bbox
    ny, nx_val = grid.shape

    dx = (east - west) / nx_val
    dy = (north - south) / ny

    i0 = max(0, int((cw - west) / dx))
    i1 = min(nx_val, int((ce - west) / dx))
    j0 = max(0, int((north - cn) / dy))
    j1 = min(ny, int((north - cs) / dy))

    cropped = grid[j0:j1, i0:i1]
    cropped_bounds = (
        west + i0 * dx, north - j1 * dy,
        west + i1 * dx, north - j0 * dy,
    )
    return cropped, cropped_bounds


def compute_isochrones(pixel_grid: pd.DataFrame, dijkstra_results: dict,
                       snap: dict, school_name: str,
                       thresholds_min: list = None) -> str:
    """Compute drive-time isochrone rings from a school. Returns GeoJSON string."""
    if thresholds_min is None:
        thresholds_min = [5, 10, 15, 20]

    if school_name not in dijkstra_results:
        return '{"type":"FeatureCollection","features":[]}'

    dist = dijkstra_results[school_name]["dist"]

    t_u = np.array([dist.get(int(u), np.inf) for u in snap["start_nodes"]])
    t_v = np.array([dist.get(int(v), np.inf) for v in snap["end_nodes"]])
    via_u = t_u + snap["fractions"] * snap["edge_times"]
    via_v = t_v + (1.0 - snap["fractions"]) * snap["edge_times"]
    total = np.minimum(via_u, via_v) + snap["access_times"]
    total_min = total / 60.0

    lats = pixel_grid["lat"].values
    lons = pixel_grid["lon"].values
    reachable = snap["reachable"]

    features = []
    colors = ["#fee5d9", "#fcae91", "#fb6a4a", "#cb181d"]

    for i, thresh in enumerate(thresholds_min):
        prev = thresholds_min[i - 1] if i > 0 else 0
        mask = reachable & (total_min >= prev) & (total_min < thresh)
        if not mask.any():
            continue

        mask_pts = lats[mask]
        # Subsample if too many points (for manageable polygon size)
        max_pts = 400
        mask_idx = np.where(mask)[0]
        if len(mask_idx) > max_pts:
            mask_idx = mask_idx[np.linspace(0, len(mask_idx) - 1, max_pts, dtype=int)]
        pts = gpd.GeoDataFrame(
            {"lat": lats[mask_idx], "lon": lons[mask_idx]},
            geometry=gpd.points_from_xy(lons[mask_idx], lats[mask_idx]),
            crs=CRS_WGS84,
        ).to_crs(CRS_UTM17N)

        # Buffer each point and dissolve
        pts["geometry"] = pts.geometry.buffer(80)
        dissolved = pts.dissolve()
        dissolved = dissolved.to_crs(CRS_WGS84)
        geom = dissolved.geometry.iloc[0]
        geom = geom.simplify(0.002, preserve_topology=True)

        features.append({
            "type": "Feature",
            "geometry": mapping(geom),
            "properties": {
                "min": prev, "max": thresh,
                "label": f"{prev}-{thresh} min",
                "color": colors[i] if i < len(colors) else "#67000d",
            },
        })

    fc = {"type": "FeatureCollection", "features": features}
    return json.dumps(fc, separators=(",", ":"))


def compute_example_routes(G: nx.MultiDiGraph, dijkstra_results: dict,
                           pixel_grid: pd.DataFrame, snap: dict,
                           school_name: str, n_routes: int = 6) -> str:
    """Reconstruct a few example driving routes to a school. Returns GeoJSON."""
    if school_name not in dijkstra_results:
        return '{"type":"FeatureCollection","features":[]}'

    dij = dijkstra_results[school_name]
    source = dij["source_node"]
    dist = dij["dist"]
    pred = dij["pred"]

    # Find pixels with moderate travel times (3-12 min range) for clear routes
    t_u = np.array([dist.get(int(u), np.inf) for u in snap["start_nodes"]])
    t_v = np.array([dist.get(int(v), np.inf) for v in snap["end_nodes"]])
    via_u = t_u + snap["fractions"] * snap["edge_times"]
    via_v = t_v + (1.0 - snap["fractions"]) * snap["edge_times"]
    total = np.minimum(via_u, via_v) + snap["access_times"]
    total_min = total / 60.0

    # Select pixels in 3-12 min range, spread across directions
    candidates = np.where(
        snap["reachable"] & (total_min >= 3) & (total_min <= 12)
    )[0]
    if len(candidates) == 0:
        return '{"type":"FeatureCollection","features":[]}'

    # Sample evenly by angle from school
    school_lat = G.nodes[source].get("y", 0)
    school_lon = G.nodes[source].get("x", 0)
    lats = pixel_grid["lat"].values
    lons = pixel_grid["lon"].values

    angles = np.arctan2(
        lats[candidates] - school_lat,
        lons[candidates] - school_lon,
    )
    # Sort by angle, pick evenly spaced
    sorted_idx = np.argsort(angles)
    step = max(1, len(sorted_idx) // n_routes)
    selected = candidates[sorted_idx[::step][:n_routes]]

    features = []
    colors = ["#e41a1c", "#377eb8", "#4daf4a", "#984ea3", "#ff7f00", "#a65628",
              "#f781bf", "#999999"]

    for ci, pixel_idx in enumerate(selected):
        # Determine entry node
        use_u = via_u[pixel_idx] <= via_v[pixel_idx]
        entry = int(snap["start_nodes"][pixel_idx] if use_u
                     else snap["end_nodes"][pixel_idx])

        # Reconstruct path
        path = _reconstruct_path(pred, source, entry)
        if path is None or len(path) < 2:
            continue

        coords = []
        for node in path:
            x = G.nodes[node].get("x", 0)
            y = G.nodes[node].get("y", 0)
            coords.append((x, y))

        features.append({
            "type": "Feature",
            "geometry": {"type": "LineString", "coordinates": coords},
            "properties": {
                "time_min": round(total_min[pixel_idx], 1),
                "color": colors[ci % len(colors)],
            },
        })

    fc = {"type": "FeatureCollection", "features": features}
    return json.dumps(fc, separators=(",", ":"))


def _reconstruct_path(pred: dict, source_node, target_node) -> list:
    """Reconstruct shortest path from predecessor map."""
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
            return None
        path.append(current)
    path.reverse()
    return path


def compute_snap_diagram(pixel_grid: pd.DataFrame, snap: dict,
                         G: nx.MultiDiGraph, center: dict,
                         n_points: int = 8) -> str:
    """Generate snap diagram: grid points → nearest edges. Returns GeoJSON."""
    lats = pixel_grid["lat"].values
    lons = pixel_grid["lon"].values

    # Find points near center
    dists = np.sqrt(
        ((lats - center["lat"]) * 111320) ** 2 +
        ((lons - center["lon"]) * 111320 * cos(radians(center["lat"]))) ** 2
    )
    near = np.argsort(dists)[:n_points * 3]
    # Filter to reachable
    near = near[snap["reachable"][near]][:n_points]

    features = []

    for idx in near:
        pt_lon, pt_lat = lons[idx], lats[idx]

        # Get the snapped edge endpoints
        s_node = int(snap["start_nodes"][idx])
        e_node = int(snap["end_nodes"][idx])
        frac = snap["fractions"][idx]

        s_x = G.nodes[s_node].get("x", 0)
        s_y = G.nodes[s_node].get("y", 0)
        e_x = G.nodes[e_node].get("x", 0)
        e_y = G.nodes[e_node].get("y", 0)

        # Snap point on edge
        snap_lon = s_x + frac * (e_x - s_x)
        snap_lat = s_y + frac * (e_y - s_y)

        # Grid point marker
        features.append({
            "type": "Feature",
            "geometry": {"type": "Point", "coordinates": [pt_lon, pt_lat]},
            "properties": {"type": "grid_point"},
        })

        # Snap line
        features.append({
            "type": "Feature",
            "geometry": {
                "type": "LineString",
                "coordinates": [[pt_lon, pt_lat], [snap_lon, snap_lat]],
            },
            "properties": {"type": "snap_line"},
        })

        # Snap point on edge
        features.append({
            "type": "Feature",
            "geometry": {"type": "Point", "coordinates": [snap_lon, snap_lat]},
            "properties": {"type": "snap_point"},
        })

    fc = {"type": "FeatureCollection", "features": features}
    return json.dumps(fc, separators=(",", ":"))


def compute_grid_points_sample(pixel_grid: pd.DataFrame, bbox: tuple,
                               max_points: int = 300) -> str:
    """Sample grid points within bbox for visualization. Returns GeoJSON."""
    lats = pixel_grid["lat"].values
    lons = pixel_grid["lon"].values

    mask = (
        (lons >= bbox[0]) & (lons <= bbox[2]) &
        (lats >= bbox[1]) & (lats <= bbox[3])
    )
    indices = np.where(mask)[0]

    if len(indices) > max_points:
        indices = indices[np.linspace(0, len(indices) - 1, max_points, dtype=int)]

    features = []
    for idx in indices:
        features.append({
            "type": "Feature",
            "geometry": {"type": "Point", "coordinates": [float(lons[idx]), float(lats[idx])]},
            "properties": {},
        })

    fc = {"type": "FeatureCollection", "features": features}
    return json.dumps(fc, separators=(",", ":"))


def compute_traffic_edges(G: nx.MultiDiGraph, dijkstra_results: dict,
                          snap: dict, pixel_grid: pd.DataFrame,
                          pixel_children: pd.DataFrame,
                          open_schools: list, bbox: tuple) -> str:
    """Compute children-weighted traffic per edge for a scenario. Returns GeoJSON."""
    n = len(snap["start_nodes"])

    # Build children lookup
    if pixel_children.empty:
        return '{"type":"FeatureCollection","features":[]}'
    children_by_id = {}
    for _, row in pixel_children.iterrows():
        children_by_id[int(row["grid_id"])] = (
            row.get("children_0_4", 0) + row.get("children_5_9", 0)
        )

    # Compute nearest school per pixel
    best_times = np.full(n, np.inf)
    best_entry = np.zeros(n, dtype=np.int64)
    best_school = np.empty(n, dtype=object)

    for school_name in open_schools:
        if school_name not in dijkstra_results:
            continue
        dij = dijkstra_results[school_name]
        dist = dij["dist"]

        t_u = np.array([dist.get(int(u), np.inf) for u in snap["start_nodes"]])
        t_v = np.array([dist.get(int(v), np.inf) for v in snap["end_nodes"]])
        via_u = t_u + snap["fractions"] * snap["edge_times"]
        via_v = t_v + (1.0 - snap["fractions"]) * snap["edge_times"]

        use_u = via_u <= via_v
        best_via = np.where(use_u, via_u, via_v)
        entry = np.where(use_u, snap["start_nodes"], snap["end_nodes"])
        total = best_via + snap["access_times"]

        improved = snap["reachable"] & (total < best_times)
        best_times[improved] = total[improved]
        best_entry[improved] = entry[improved]
        best_school[improved] = school_name

    # Accumulate traffic on edges
    grid_ids = pixel_grid["grid_id"].values
    edge_traffic = {}  # (min(u,v), max(u,v)) → children count

    for i in range(n):
        if not snap["reachable"][i] or best_times[i] >= 1e6:
            continue
        gid = int(grid_ids[i])
        kids = children_by_id.get(gid, 0)
        if kids < 0.001:
            continue

        school_name = best_school[i]
        if school_name not in dijkstra_results:
            continue
        dij = dijkstra_results[school_name]
        path = _reconstruct_path(dij["pred"], dij["source_node"], int(best_entry[i]))
        if path is None:
            continue

        for j in range(len(path) - 1):
            u_node, v_node = path[j], path[j + 1]
            edge_key = (min(u_node, v_node), max(u_node, v_node))
            edge_traffic[edge_key] = edge_traffic.get(edge_key, 0) + kids

    # Convert to GeoJSON (clipped to bbox)
    from shapely.geometry import box as shapely_box
    clip_poly = shapely_box(bbox[0], bbox[1], bbox[2], bbox[3])

    features = []
    for (u, v), children in edge_traffic.items():
        if children < 3.0:
            continue
        try:
            ux, uy = G.nodes[u]["x"], G.nodes[u]["y"]
            vx, vy = G.nodes[v]["x"], G.nodes[v]["y"]
        except KeyError:
            continue
        line = LineString([(ux, uy), (vx, vy)])
        if not line.intersects(clip_poly):
            continue
        features.append({
            "type": "Feature",
            "geometry": {"type": "LineString", "coordinates": [
                [round(ux, 4), round(uy, 4)], [round(vx, 4), round(vy, 4)]
            ]},
            "properties": {"children": round(children, 1)},
        })

    fc = {"type": "FeatureCollection", "features": features}
    return json.dumps(fc, separators=(",", ":"))


# ═══════════════════════════════════════════════════════════════════════════
# HTML Builder
# ═══════════════════════════════════════════════════════════════════════════

def build_html(data: dict) -> str:
    """Build the complete scrollytelling HTML page."""

    # Speed table rows
    speed_rows = ""
    for road_type, posted, effective in ROAD_SPEED_TABLE:
        ratio = round(effective / posted * 100) if posted > 0 else 0
        speed_rows += f"<tr><td>{road_type}</td><td>{posted}</td><td>{effective}</td><td>{ratio}%</td></tr>\n"

    return f"""<!DOCTYPE html>
<html lang="en">
<head>
<meta charset="UTF-8">
<meta name="viewport" content="width=device-width, initial-scale=1.0">
<title>How the School Closure Impact Map Works</title>
<link rel="stylesheet" href="https://unpkg.com/leaflet@1.9.3/dist/leaflet.css" />
<style>
* {{ margin: 0; padding: 0; box-sizing: border-box; }}
body {{ font-family: -apple-system, BlinkMacSystemFont, "Segoe UI", Roboto, sans-serif;
       background: #f5f5f5; overflow-x: hidden; }}

/* ── Two-column layout ── */
.scroll-container {{
  position: relative; width: 45%; padding: 0 30px; z-index: 10;
}}
#map-container {{
  position: fixed; right: 0; top: 0; width: 55%; height: 100vh; z-index: 5;
}}
#map {{ width: 100%; height: 100%; }}
#map-dim {{
  position: absolute; top: 0; left: 0; width: 100%; height: 100%;
  background: rgba(255,255,255,0.4); pointer-events: none;
  z-index: 1000; display: none;
}}

/* ── Step cards ── */
.step {{
  min-height: 80vh; padding: 30px 25px; margin: 20px 0;
  background: #fff; border-radius: 8px; border-left: 4px solid #2196F3;
  box-shadow: 0 2px 8px rgba(0,0,0,0.08); opacity: 0.3;
  transition: opacity 0.4s ease, border-color 0.3s ease;
}}
.step:first-child {{ margin-top: 40vh; }}
.step:last-child {{ margin-bottom: 60vh; }}
.step.is-active {{ opacity: 1; border-color: #1565C0; }}

.step-number {{
  display: inline-block; width: 28px; height: 28px; border-radius: 50%;
  background: #2196F3; color: white; text-align: center; line-height: 28px;
  font-size: 13px; font-weight: 600; margin-bottom: 10px;
}}
.step h2 {{ font-size: 1.25rem; margin-bottom: 12px; color: #1a1a1a; }}
.step p, .step li {{ font-size: 0.93rem; line-height: 1.65; color: #333; margin-bottom: 8px; }}

/* ── Info boxes ── */
.source {{
  background: #e3f2fd; border-radius: 6px; padding: 10px 14px; margin: 12px 0;
  font-size: 0.85rem; color: #1565C0; line-height: 1.5;
}}
.source strong {{ color: #0d47a1; }}
.limitation {{
  background: #fff8e1; border-left: 3px solid #f9a825; border-radius: 0 6px 6px 0;
  padding: 10px 14px; margin: 12px 0; font-size: 0.85rem; color: #5d4037; line-height: 1.5;
}}
.limitation strong {{ color: #e65100; }}

/* ── Tables ── */
.data-table {{ width: 100%; border-collapse: collapse; margin: 12px 0; font-size: 0.85rem; }}
.data-table th {{ background: #e3f2fd; padding: 8px 10px; text-align: left; font-weight: 600; }}
.data-table td {{ padding: 6px 10px; border-bottom: 1px solid #eee; }}

/* ── Collapsible ── */
details {{ margin: 10px 0; }}
summary {{
  cursor: pointer; font-weight: 600; font-size: 0.9rem; color: #1565C0;
  padding: 6px 0;
}}
details[open] summary {{ margin-bottom: 8px; }}

/* ── Legend ── */
.legend-bar {{
  height: 14px; border-radius: 3px; margin: 6px 0;
  background: linear-gradient(to right, #ffffb2, #fd8d3c, #bd0026);
}}
.legend-labels {{ display: flex; justify-content: space-between; font-size: 0.78rem; color: #666; }}

/* ── Responsive ── */
@media (max-width: 900px) {{
  .scroll-container {{ width: 100%; padding: 0 15px; }}
  #map-container {{ position: fixed; top: 0; left: 0; width: 100%; height: 40vh; }}
  .step:first-child {{ margin-top: 42vh; }}
  .step {{ min-height: auto; padding: 20px 15px; }}
}}

a {{ color: #1565C0; }}
.school-x {{
  display: inline-block; width: 20px; height: 20px; line-height: 20px;
  text-align: center; background: #d32f2f; color: white; border-radius: 50%;
  font-weight: bold; font-size: 14px;
}}
</style>
</head>
<body>

<div class="scroll-container">

<!-- ═══ Step 0: Introduction ═══ -->
<div class="step" data-step="0">
  <div class="step-number">1</div>
  <h2>How the School Closure Impact Map Works</h2>
  <p>When a school closes, two things change for every family in the district:</p>
  <ul>
    <li><strong>Access:</strong> How much longer is the drive, bike ride, or walk to the nearest remaining school?</li>
    <li><strong>Traffic:</strong> Which roads see more (or fewer) school-bound vehicles?</li>
  </ul>
  <p>This walkthrough explains exactly how we answer both questions, step by step.
     We use <strong>Northside Elementary</strong> as an illustrative example &mdash;
     but the methodology is identical for all 11 schools.</p>
  <p><a href="school_closure_analysis.html">View the interactive closure map &rarr;</a></p>
  <div class="source">
    <strong>Note:</strong> This is a purely illustrative walkthrough.
    The same analysis runs for every school. No school is singled out.
  </div>
</div>

<!-- ═══ Step 1: School Locations & Road Network ═══ -->
<div class="step" data-step="1">
  <div class="step-number">2</div>
  <h2>School Locations &amp; Road Network</h2>
  <p>We start with two public datasets:</p>
  <ul>
    <li><strong>School locations</strong> from the National Center for Education Statistics (NCES EDGE, 2023-24).
        These are official geocoded coordinates for all 11 CHCCS elementary schools.</li>
    <li><strong>Road network</strong> from OpenStreetMap, downloaded via the OSMnx library.
        Every road segment carries a type (motorway, residential, etc.) and length.</li>
  </ul>
  <p>The map shows the drive network colored by road type. Major roads (trunks, primaries)
     carry faster traffic; residential streets are slower but more numerous.</p>
  <div class="source">
    <strong>Data:</strong> NCES EDGE Public School Locations 2023-24
    (LEAID 3700720) &bull; OpenStreetMap via OSMnx &bull;
    Boeing, G. (2017). OSMnx: New Methods for Acquiring, Constructing,
    Analyzing, and Visualizing Complex Street Networks. <em>Computers,
    Environment and Urban Systems</em>, 65, 126&ndash;139.
  </div>
  <div class="limitation">
    <strong>Limitation:</strong> OSM road data is community-maintained.
    Some new developments or private roads may be missing.
    Network downloaded as a snapshot; no real-time updates.
  </div>
</div>

<!-- ═══ Step 2: Travel Speed Model ═══ -->
<div class="step" data-step="2">
  <div class="step-number">3</div>
  <h2>Travel Speed Model</h2>
  <p>Raw road type alone doesn&rsquo;t tell us how fast people actually drive.
     We assign <strong>effective speeds</strong> &mdash; typical urban travel speeds
     that account for stop signs, traffic lights, congestion, and turning movements.</p>
  <table class="data-table">
    <thead><tr><th>Road Type</th><th>Posted (mph)</th><th>Effective (mph)</th><th>Ratio</th></tr></thead>
    <tbody>{speed_rows}</tbody>
  </table>
  <p>Roads on the map are now colored by effective speed: darker = faster.</p>
  <div class="source">
    <strong>Source:</strong> Effective-to-posted ratios derived from
    HCM 6th Edition, Chapter 16 (Urban Street Facilities).
    Residential effective speeds are conservative, reflecting school-zone conditions.
  </div>
  <details>
    <summary>Why not posted speed limits?</summary>
    <p>Posted limits rarely match actual travel speeds during school hours.
       Urban arterials average 65&ndash;75% of posted speed; residential streets
       even less during morning/afternoon peaks. The HCM-based factors
       provide a more realistic estimate.</p>
  </details>
</div>

<!-- ═══ Step 3: Walk & Bike Networks ═══ -->
<div class="step" data-step="3">
  <div class="step-number">4</div>
  <h2>Walk &amp; Bike Networks</h2>
  <p>The analysis covers three travel modes. Walk and bike networks include
     paths that cars cannot use &mdash; sidewalks, greenways, cut-throughs &mdash;
     which makes them larger than the drive network in some areas.</p>
  <ul>
    <li><strong>Walk:</strong> 2.5 mph (MUTCD 4E.06 / Fitzpatrick et al. 2006)</li>
    <li><strong>Bike:</strong> 12 mph (typical urban cycling speed)</li>
    <li><strong>Drive:</strong> Variable by road type (previous step)</li>
  </ul>
  <p>The remaining steps of this walkthrough focus on <strong>drive mode</strong>
     for clarity, but the same Dijkstra algorithm runs for all three.</p>
  <div class="source">
    <strong>Speed sources:</strong> Walk: Manual on Uniform Traffic Control Devices
    (MUTCD) Section 4E.06 &bull; Bike: AASHTO Guide for the Development of
    Bicycle Facilities, 4th ed. &bull; Drive: HCM6 Ch.16.
  </div>
</div>

<!-- ═══ Step 4: Inverted Dijkstra ═══ -->
<div class="step" data-step="4">
  <div class="step-number">5</div>
  <h2>Inverted Dijkstra: From School to Everywhere</h2>
  <p>To find travel time from every location to a school, a na&iuml;ve approach
     would run one shortest-path search per grid point &mdash; over 16,000 runs.
     Instead, we use the <strong>inverted Dijkstra</strong> trick:</p>
  <p>Run Dijkstra <em>from</em> each school <em>outward</em> to all reachable nodes.
     Since the road network is bidirectional, driving <em>to</em> a school takes
     the same time as driving <em>from</em> it. This gives us distances from
     all ~16,000 grid points using just <strong>11 runs per mode</strong>
     (33 total across drive/walk/bike).</p>
  <p>The map shows drive-time rings radiating outward from Northside.
     Notice how they follow the road network rather than forming perfect circles.</p>
  <div class="source">
    <strong>Algorithm:</strong> Single-source Dijkstra via NetworkX
    (<code>dijkstra_predecessor_and_distance</code>). Produces both
    distance and predecessor maps for route reconstruction.
  </div>
  <details>
    <summary>Technical: Why bidirectional?</summary>
    <p>OSMnx downloads a directed graph, but residential streets are overwhelmingly
       two-way. We add reverse edges for any missing direction, making the graph
       effectively undirected. This is necessary for the inverted Dijkstra trick
       to produce correct results and matches how families actually drive.</p>
  </details>
</div>

<!-- ═══ Step 5: Edge Snapping ═══ -->
<div class="step" data-step="5">
  <div class="step-number">6</div>
  <h2>Edge Snapping: Grid Points to Roads</h2>
  <p>Grid points don&rsquo;t sit exactly on road nodes. We snap each point to
     the <strong>nearest road edge</strong> (not nearest node), which is more
     accurate:</p>
  <ul>
    <li>Find the closest road segment using a spatial index (STRtree)</li>
    <li>Compute the <strong>fractional position</strong> along the edge
        (0 = start node, 1 = end node)</li>
    <li>Interpolate travel time: if Dijkstra says the start node is 5 min
        from school and the end node is 7 min, a point at fraction 0.3
        is 5 + 0.3&times;(edge time) min away</li>
    <li>Add an <strong>access-leg penalty</strong> for the perpendicular
        distance from the grid point to the road
        (walking to the car at ~1.6 m/s for drive mode)</li>
  </ul>
  <p>The map shows snap lines connecting grid points to their nearest road edges.
     Yellow dots are grid points; blue dots are the snap positions on the road.</p>
  <div class="limitation">
    <strong>Limitation:</strong> Points more than 200 m from any road edge
    are marked unreachable. This affects very few pixels within the district.
  </div>
</div>

<!-- ═══ Step 6: 100 m Grid ═══ -->
<div class="step" data-step="6">
  <div class="step-number">7</div>
  <h2>The 100-Meter Grid</h2>
  <p>We tile the entire CHCCS district with a <strong>100 m &times; 100 m grid</strong>
     &mdash; roughly 16,000 points. Each point represents a potential household location.</p>
  <p>The grid uses WGS84-native coordinates with a cosine-latitude correction
     to keep cells approximately square despite the curvature of longitude lines.
     At Chapel Hill&rsquo;s latitude (35.9&deg;N), 100 m &asymp; 0.000898&deg; lat
     and &asymp; 0.001109&deg; lon.</p>
  <p>Only points inside the district boundary are kept.</p>
  <details>
    <summary>Why not a projected (UTM) grid?</summary>
    <p>Earlier versions used UTM 17N, but the convergence angle introduced
       slight grid rotation relative to the lat/lon-aligned Leaflet tiles.
       A WGS84-native grid with cos(lat) correction produces cells that are
       equally square (to within 0.01%) and align perfectly with the web map.</p>
  </details>
</div>

<!-- ═══ Step 7: Baseline Accessibility ═══ -->
<div class="step" data-step="7">
  <div class="step-number">8</div>
  <h2>Baseline: Nearest School Drive Time</h2>
  <p>For each grid point, we take the <strong>minimum travel time across all
     11 schools</strong>. This is the baseline: how long the drive is today,
     with every school open.</p>
  <p>Yellow areas are close to a school (&lt;5 min); orange and red areas are farther
     (&gt;10 min). Most of the district is within a 10-minute drive.</p>
  <div class="legend-bar"></div>
  <div class="legend-labels"><span>0 min</span><span>7.5</span><span>15 min</span></div>
</div>

<!-- ═══ Step 8: Closure Scenario ═══ -->
<div class="step" data-step="8">
  <div class="step-number">9</div>
  <h2>Closure Scenario: Remove Northside</h2>
  <p>Now we remove Northside from the calculation and take the minimum across
     the <strong>remaining 10 schools</strong>. No recomputation is needed &mdash;
     we simply exclude Northside&rsquo;s Dijkstra results from the minimum.</p>
  <p>The <span class="school-x">&times;</span> marks the closed school.
     Areas near Northside now show longer travel times because families must
     drive to the next-closest school.</p>
  <div class="limitation">
    <strong>Key assumption:</strong> We do not model capacity constraints.
    If the nearest remaining school is &ldquo;full,&rdquo; families might be
    assigned even farther. This analysis shows the <em>minimum possible</em>
    travel time increase.
  </div>
</div>

<!-- ═══ Step 9: Travel Time Increase ═══ -->
<div class="step" data-step="9">
  <div class="step-number">10</div>
  <h2>Travel Time Increase (Delta)</h2>
  <p>Subtracting baseline from closure gives the <strong>delta</strong> &mdash;
     how many additional minutes each location would need to drive.</p>
  <p>Only the former Northside zone is affected (everywhere else, the
     nearest school hasn&rsquo;t changed). The delta ranges from 0 (already
     near another school) to several minutes for the most isolated points.</p>
  <div class="legend-bar" style="background: linear-gradient(to right, #fff5eb, #fd8d3c, #d94701);"></div>
  <div class="legend-labels"><span>0 min</span><span>5</span><span>10+ min</span></div>
  <div class="source">
    <strong>Interpretation:</strong> Orange shading shows increased drive time.
    Deeper color = larger impact. White/unshaded areas see no change.
  </div>
</div>

<!-- ═══ Step 10: Walk & Bike Impact ═══ -->
<div class="step" data-step="10">
  <div class="step-number">11</div>
  <h2>Walk &amp; Bike: Who Is Hit Hardest?</h2>
  <p>The same analysis runs for walk and bike modes. Walking families see the
     <strong>largest absolute increase</strong> because at 2.5 mph, every
     additional mile adds 24 minutes.</p>
  <p>The walk-mode delta map often shows a wider and deeper impact zone
     than drive mode &mdash; highlighting that school closures
     disproportionately affect families without cars.</p>
  <p>The remaining steps continue with drive mode for the traffic analysis.</p>
  <div class="limitation">
    <strong>Limitation:</strong> Walk and bike networks assume all paths are
    usable year-round. Seasonal factors (ice, flooding) are not modeled.
  </div>
</div>

<!-- ═══ Step 11: Where Do the Children Live? ═══ -->
<div class="step" data-step="11">
  <div class="step-number">12</div>
  <h2>Where Do the Children Live?</h2>
  <p>To estimate traffic, we need to know where school-age children live.
     The U.S. Census Bureau&rsquo;s American Community Survey (ACS) 5-Year
     provides population by age at the <strong>block group</strong> level:</p>
  <ul>
    <li><strong>Ages 0&ndash;4</strong> (B01001_003E + B01001_027E)</li>
    <li><strong>Ages 5&ndash;9</strong> (B01001_004E + B01001_028E)</li>
  </ul>
  <p>The map shows block groups shaded by total children (ages 0&ndash;9).
     Block groups are the finest geography with reliable age data.</p>
  <div class="source">
    <strong>Data:</strong> ACS 5-Year Estimates, Table B01001 (Sex by Age),
    Orange County NC (FIPS 37135). Census TIGER/Line block group boundaries.
  </div>
  <div class="limitation">
    <strong>Limitation:</strong> ACS 5-Year estimates have margins of error,
    especially for small block groups. We use the estimates as-is without
    modeling uncertainty.
  </div>
</div>

<!-- ═══ Step 12: Dasymetric Downscaling ═══ -->
<div class="step" data-step="12">
  <div class="step-number">13</div>
  <h2>Dasymetric Downscaling</h2>
  <p>Block groups are too coarse for pixel-level traffic estimation.
     We downscale children counts in two stages:</p>
  <ol>
    <li><strong>Block group &rarr; block:</strong> Distribute children proportionally
        by <em>residential land area</em> within each block. We use Orange County
        parcel data to identify improved residential parcels, so children are
        allocated where houses actually exist &mdash; not in parks or commercial zones.</li>
    <li><strong>Block &rarr; pixel:</strong> Intersect the 100 m grid cells with
        blocks, allocating children by the area of overlap.</li>
  </ol>
  <p>The map shows census blocks (outlines) overlaid with residential parcels
     (shaded). Children are spread across the blue parcels, not the empty areas.</p>
  <div class="source">
    <strong>Data:</strong> Census TIGER/Line blocks &bull;
    Orange County parcel boundaries (combined_data_polys.gpkg,
    filtered to is_residential=True, Improved).
  </div>
  <details>
    <summary>What is dasymetric mapping?</summary>
    <p>A technique that uses ancillary data (here, parcel boundaries) to distribute
       population more accurately than simple area-weighted interpolation.
       First described by Wright (1936) and widely used in environmental justice
       and transportation planning.</p>
  </details>
</div>

<!-- ═══ Step 13: Route Reconstruction ═══ -->
<div class="step" data-step="13">
  <div class="step-number">14</div>
  <h2>Route Reconstruction</h2>
  <p>The Dijkstra algorithm produces a <strong>predecessor map</strong> for each
     school: for every network node, it records which node comes before it on the
     shortest path. This lets us reconstruct the exact route from any grid point
     to any school by walking the predecessor chain backward.</p>
  <p>The map shows several example driving routes to Northside, reconstructed
     from the predecessor map. Each colored line is the shortest-time path
     from a different part of the district.</p>
  <div class="source">
    <strong>Algorithm:</strong> Path reconstruction is O(path length) per route.
    NetworkX stores predecessor lists; we always take the first predecessor
    for deterministic results.
  </div>
</div>

<!-- ═══ Step 14: Baseline Traffic ═══ -->
<div class="step" data-step="14">
  <div class="step-number">15</div>
  <h2>Baseline Traffic Volume</h2>
  <p>For every pixel, we know:</p>
  <ol>
    <li>How many children live there (from dasymetric downscaling)</li>
    <li>Which school is nearest (from Dijkstra)</li>
    <li>The exact route to that school (from predecessor map)</li>
  </ol>
  <p>We trace each pixel&rsquo;s children along their route and
     <strong>accumulate the count on every road segment traversed</strong>.
     The result is an estimate of school-bound traffic volume on each road.</p>
  <p>Thicker, redder lines carry more children. Major collectors and arterials
     near schools naturally accumulate the most traffic.</p>
  <div class="limitation">
    <strong>Limitations:</strong> (1) Assumes every child takes the shortest-time
    route. Real families may prefer different routes. (2) Does not model
    carpooling or bus routes. (3) One vehicle per child &mdash; no sibling
    consolidation.
  </div>
</div>

<!-- ═══ Step 15: Walk Zone Masking ═══ -->
<div class="step" data-step="15">
  <div class="step-number">16</div>
  <h2>Walk Zone Masking</h2>
  <p>CHCCS designates <strong>walk zones</strong> around each school.
     Children inside a walk zone typically walk rather than drive.
     We subtract their traffic contribution from the driving estimate.</p>
  <p>The map shows walk zone polygons. When a school is open, children inside
     its walk zone are removed from driving traffic. When a school closes,
     its walk zone is <strong>not</strong> masked &mdash; those children must
     now drive to a different school.</p>
  <div class="source">
    <strong>Data:</strong> CHCCS attendance zone shapefile
    (ESWALK=&rsquo;Y&rsquo; features dissolved by ENAME).
  </div>
  <div class="limitation">
    <strong>Limitation:</strong> Walk zone boundaries are policy-defined,
    not route-based. Some families inside the walk zone may still drive;
    some outside may walk. We use the official boundaries as-is.
  </div>
</div>

<!-- ═══ Step 16: Traffic Redistribution ═══ -->
<div class="step" data-step="16">
  <div class="step-number">17</div>
  <h2>Traffic Redistribution After Closure</h2>
  <p>We repeat the traffic computation with Northside closed. Children formerly
     routed to Northside are now sent to their next-nearest school, flowing
     along new routes and adding traffic to different roads.</p>
  <p>The <strong>difference</strong> between closure and baseline traffic reveals
     which roads gain traffic (red) and which lose it (blue). Roads near the
     closed school lose traffic; roads leading to neighboring schools gain it.</p>
  <div class="source">
    <strong>Method:</strong> Closure traffic &minus; baseline traffic, per edge.
    Positive = more children driving on that road. Negative = fewer.
  </div>
</div>

<!-- ═══ Step 17: Combined View ═══ -->
<div class="step" data-step="17">
  <div class="step-number">18</div>
  <h2>Putting It Together</h2>
  <p>The full analysis combines both lenses:</p>
  <ul>
    <li><strong>Heatmap overlay:</strong> Travel time increase for every location</li>
    <li><strong>Traffic overlay:</strong> Which roads absorb redistributed traffic</li>
  </ul>
  <p>This dual view lets planners see both the household-level access impact
     and the road-network-level traffic consequences of any hypothetical closure.</p>
  <p><a href="school_closure_analysis.html">Explore all 11 closure scenarios
     in the interactive map &rarr;</a></p>
</div>

<!-- ═══ Step 18: Limitations ═══ -->
<div class="step" data-step="18">
  <div class="step-number">19</div>
  <h2>Limitations &amp; Caveats</h2>
  <p>Every model makes simplifying assumptions. Here are the most important ones:</p>
  <ol>
    <li><strong>No capacity constraints.</strong> We assume every school can absorb
        reassigned students. In practice, receiving schools may lack space.</li>
    <li><strong>Shortest path only.</strong> Real families may prefer familiar routes,
        avoid left turns, or chain trips (daycare &rarr; school &rarr; work).</li>
    <li><strong>Static network.</strong> Road speeds don&rsquo;t change with added
        congestion from redistributed traffic.</li>
    <li><strong>No turn penalties.</strong> Left turns across traffic and U-turns
        are treated identically to right turns.</li>
    <li><strong>One vehicle per child.</strong> Carpooling, siblings, and school
        buses are not modeled.</li>
    <li><strong>ACS margin of error.</strong> Small-area child counts are estimates
        with confidence intervals we do not propagate.</li>
    <li><strong>Parcel-based dasymetric.</strong> Assumes uniform child density
        within residential parcels. Apartment complexes and single-family homes
        are weighted equally by area.</li>
    <li><strong>Walk zone boundaries.</strong> Based on policy, not actual behavior.
        Some driving families inside walk zones; some walking families outside.</li>
    <li><strong>No temporal variation.</strong> Morning vs. afternoon traffic
        patterns are not differentiated.</li>
    <li><strong>OSM completeness.</strong> Community-maintained road data may miss
        new roads or private roads.</li>
    <li><strong>Flat terrain.</strong> Elevation and hills are not factored into
        walk/bike speeds.</li>
    <li><strong>Age range proxy.</strong> ACS ages 0&ndash;9 is a proxy for
        K&ndash;5 elementary enrollment (ages 5&ndash;11). Some children attend
        private schools or are homeschooled.</li>
    <li><strong>No behavioral response.</strong> Families may move, switch to
        private school, or change work schedules in response to a closure.</li>
  </ol>
  <div class="source">
    <strong>Full technical documentation:</strong>
    See the <a href="https://github.com/sashalikesplanes/CHCCS_geospatial/blob/main/docs/SCHOOL_CLOSURE_ANALYSIS.md">
    School Closure Analysis methodology document</a> for complete details.
  </div>
</div>

</div><!-- end scroll-container -->

<div id="map-container">
  <div id="map"></div>
  <div id="map-dim"></div>
</div>

<script src="https://unpkg.com/leaflet@1.9.3/dist/leaflet.js"></script>
<script src="https://unpkg.com/scrollama@3.2.0/build/scrollama.min.js"></script>
<script>
// ── Data ──
var SCHOOLS = {data["schools_json"]};
var DISTRICT = {data["district_json"]};
var DRIVE_ROADS = {data["drive_roads_json"]};
var WALK_ROADS = {data["walk_roads_json"]};
var ISOCHRONES = {data["isochrones_json"]};
var SNAP_DIAGRAM = {data["snap_diagram_json"]};
var GRID_POINTS = {data["grid_points_json"]};
var BLOCK_GROUPS = {data["block_groups_json"]};
var BLOCKS = {data["blocks_json"]};
var PARCELS = {data["parcels_json"]};
var ROUTES = {data["routes_json"]};
var WALK_ZONES = {data["walk_zones_json"]};
var TRAFFIC_BASE = {data["traffic_base_json"]};
var TRAFFIC_CLOSURE = {data["traffic_closure_json"]};

var NORTHSIDE = {json.dumps(data["northside"])};

var BASELINE_DRIVE_B64 = "{data["baseline_drive_b64"]}";
var BASELINE_DRIVE_BOUNDS = {json.dumps(data["baseline_drive_bounds"])};
var CLOSURE_DRIVE_B64 = "{data["closure_drive_b64"]}";
var CLOSURE_DRIVE_BOUNDS = {json.dumps(data["closure_drive_bounds"])};
var DELTA_DRIVE_B64 = "{data["delta_drive_b64"]}";
var DELTA_DRIVE_BOUNDS = {json.dumps(data["delta_drive_bounds"])};
var WALK_BASELINE_B64 = "{data["walk_baseline_b64"]}";
var WALK_BASELINE_BOUNDS = {json.dumps(data["walk_baseline_bounds"])};
var WALK_DELTA_B64 = "{data["walk_delta_b64"]}";
var WALK_DELTA_BOUNDS = {json.dumps(data["walk_delta_bounds"])};

// ── Map setup ──
var map = L.map("map", {{
  center: [{data["northside"]["lat"]}, {data["northside"]["lon"]}],
  zoom: 13,
  scrollWheelZoom: false,
  zoomControl: true,
}});
L.tileLayer("https://{{s}}.basemaps.cartocdn.com/light_all/{{z}}/{{x}}/{{y}}@2x.png", {{
  attribution: '&copy; <a href="https://carto.com/">CARTO</a> &copy; <a href="https://www.openstreetmap.org/copyright">OSM</a>',
  maxZoom: 18,
}}).addTo(map);

// Road type colors
var ROAD_COLORS = {json.dumps(ROAD_COLORS)};

// ── Layer factories ──
var layers = {{}};

// District boundary
layers.district = L.geoJSON(DISTRICT, {{
  style: {{ color: "#333", weight: 2, fill: false, dashArray: "6,4" }}
}});

// School markers
function schoolIcon(name) {{
  var isNorthside = name.toLowerCase().indexOf("northside") >= 0;
  return L.divIcon({{
    html: '<div style="width:10px;height:10px;border-radius:50%;background:' +
          (isNorthside ? '#d32f2f' : '#1565C0') +
          ';border:2px solid white;box-shadow:0 1px 3px rgba(0,0,0,0.4)"></div>',
    iconSize: [14, 14], iconAnchor: [7, 7], className: '',
  }});
}}
layers.schools = L.geoJSON(SCHOOLS, {{
  pointToLayer: function(f, ll) {{
    return L.marker(ll, {{ icon: schoolIcon(f.properties.school) }})
      .bindTooltip(f.properties.school, {{ permanent: false, direction: "top" }});
  }}
}});

// Closed school X marker
layers.closedX = L.marker([NORTHSIDE.lat, NORTHSIDE.lon], {{
  icon: L.divIcon({{
    html: '<div style="width:24px;height:24px;line-height:24px;text-align:center;background:#d32f2f;color:white;border-radius:50%;font-weight:bold;font-size:16px;border:2px solid white;box-shadow:0 2px 6px rgba(0,0,0,0.5)">&times;</div>',
    iconSize: [28, 28], iconAnchor: [14, 14], className: '',
  }})
}});

// Drive roads by type
layers.driveRoadsByType = L.geoJSON(DRIVE_ROADS, {{
  style: function(f) {{
    var hw = f.properties.highway || "residential";
    return {{ color: ROAD_COLORS[hw] || "#999", weight: 1.5, opacity: 0.7 }};
  }}
}});

// Drive roads by speed
layers.driveRoadsBySpeed = L.geoJSON(DRIVE_ROADS, {{
  style: function(f) {{
    var spd = f.properties.speed_mph || 18;
    var t = Math.min(1, Math.max(0, (spd - 10) / 50));
    var r = Math.round(255 * (1 - t));
    var g = Math.round(100 * (1 - t));
    var b = Math.round(50 + 200 * t);
    return {{ color: "rgb(" + r + "," + g + "," + b + ")", weight: 2, opacity: 0.8 }};
  }}
}});

// Walk roads
layers.walkRoads = L.geoJSON(WALK_ROADS, {{
  style: {{ color: "#4daf4a", weight: 1, opacity: 0.5 }}
}});

// Isochrones
layers.isochrones = L.geoJSON(ISOCHRONES, {{
  style: function(f) {{
    return {{ color: f.properties.color, weight: 1, fillColor: f.properties.color,
             fillOpacity: 0.3 }};
  }},
  onEachFeature: function(f, layer) {{
    layer.bindTooltip(f.properties.label);
  }}
}});

// Snap diagram
layers.snapDiagram = L.geoJSON(SNAP_DIAGRAM, {{
  style: function(f) {{
    if (f.properties.type === "snap_line")
      return {{ color: "#ff7f00", weight: 2, dashArray: "4,4" }};
    return {{}};
  }},
  pointToLayer: function(f, ll) {{
    if (f.properties.type === "grid_point")
      return L.circleMarker(ll, {{ radius: 5, color: "#ff7f00", fillColor: "#ffff00",
                                   fillOpacity: 1, weight: 2 }});
    return L.circleMarker(ll, {{ radius: 4, color: "#1565C0", fillColor: "#42a5f5",
                                 fillOpacity: 1, weight: 2 }});
  }}
}});

// Grid points
layers.gridPoints = L.geoJSON(GRID_POINTS, {{
  pointToLayer: function(f, ll) {{
    return L.circleMarker(ll, {{ radius: 2, color: "#666", fillColor: "#999",
                                 fillOpacity: 0.6, weight: 1 }});
  }}
}});

// Heatmap overlays
function imgOverlay(b64, bounds) {{
  if (!b64 || b64 === "None") return L.layerGroup();
  return L.imageOverlay(b64, [[bounds[1], bounds[0]], [bounds[3], bounds[2]]], {{
    opacity: 0.85, interactive: false
  }});
}}
layers.baselineDrive = imgOverlay(BASELINE_DRIVE_B64, BASELINE_DRIVE_BOUNDS);
layers.closureDrive = imgOverlay(CLOSURE_DRIVE_B64, CLOSURE_DRIVE_BOUNDS);
layers.deltaDrive = imgOverlay(DELTA_DRIVE_B64, DELTA_DRIVE_BOUNDS);
layers.walkBaseline = imgOverlay(WALK_BASELINE_B64, WALK_BASELINE_BOUNDS);
layers.walkDelta = imgOverlay(WALK_DELTA_B64, WALK_DELTA_BOUNDS);

// Block groups choropleth
layers.blockGroups = L.geoJSON(BLOCK_GROUPS, {{
  style: function(f) {{
    var c = f.properties.total_children || 0;
    var t = Math.min(1, c / 200);
    var r = Math.round(255 * t);
    var g = Math.round(255 * (1 - t * 0.7));
    var b = Math.round(100 * (1 - t));
    return {{ color: "#666", weight: 1, fillColor: "rgb(" + r + "," + g + "," + b + ")",
             fillOpacity: 0.5 }};
  }},
  onEachFeature: function(f, layer) {{
    var c = f.properties.total_children || 0;
    layer.bindTooltip("Children: " + Math.round(c));
  }}
}});

// Blocks outlines
layers.blocks = L.geoJSON(BLOCKS, {{
  style: {{ color: "#888", weight: 1, fill: false, dashArray: "3,3" }}
}});

// Residential parcels
layers.parcels = L.geoJSON(PARCELS, {{
  style: {{ color: "#1565C0", weight: 0.5, fillColor: "#42a5f5", fillOpacity: 0.4 }}
}});

// Example routes
layers.routes = L.geoJSON(ROUTES, {{
  style: function(f) {{
    return {{ color: f.properties.color || "#e41a1c", weight: 3, opacity: 0.8 }};
  }},
  onEachFeature: function(f, layer) {{
    layer.bindTooltip(f.properties.time_min + " min");
  }}
}});

// Walk zones
layers.walkZones = L.geoJSON(WALK_ZONES, {{
  style: {{ color: "#4caf50", weight: 2, fillColor: "#a5d6a7", fillOpacity: 0.25,
           dashArray: "5,5" }},
  onEachFeature: function(f, layer) {{
    layer.bindTooltip(f.properties.school + " walk zone");
  }}
}});

// Traffic (baseline)
function trafficStyle(f, maxChildren) {{
  var c = f.properties.children || 0;
  var t = Math.min(1, c / maxChildren);
  var w = 1 + 5 * t;
  var r = Math.round(255 * Math.min(1, t * 2));
  var g = Math.round(255 * Math.max(0, 1 - t * 1.5));
  return {{ color: "rgb(" + r + "," + g + ",0)", weight: w, opacity: 0.7 }};
}}

var maxTrafficBase = 1;
TRAFFIC_BASE.features.forEach(function(f) {{
  maxTrafficBase = Math.max(maxTrafficBase, f.properties.children || 0);
}});
layers.trafficBase = L.geoJSON(TRAFFIC_BASE, {{
  style: function(f) {{ return trafficStyle(f, maxTrafficBase); }},
  onEachFeature: function(f, layer) {{
    layer.bindTooltip("Children: " + (f.properties.children || 0).toFixed(1));
  }}
}});

// Traffic (closure) — show as delta (closure - baseline computed in Python)
layers.trafficClosure = L.geoJSON(TRAFFIC_CLOSURE, {{
  style: function(f) {{
    var c = f.properties.children || 0;
    if (Math.abs(c) < 0.5) return {{ weight: 0, opacity: 0 }};
    var t = Math.min(1, Math.abs(c) / Math.max(1, maxTrafficBase * 0.5));
    var w = 1 + 4 * t;
    if (c > 0) return {{ color: "rgb(255," + Math.round(100*(1-t)) + ",0)", weight: w, opacity: 0.7 }};
    return {{ color: "rgb(0," + Math.round(100*(1-t)) + ",255)", weight: w, opacity: 0.7 }};
  }},
  onEachFeature: function(f, layer) {{
    var c = f.properties.children || 0;
    layer.bindTooltip((c > 0 ? "+" : "") + c.toFixed(1) + " children");
  }}
}});

// ── Step handler ──
var currentStep = -1;
var dimOverlay = document.getElementById("map-dim");
var districtBounds = layers.district.getBounds();

function clearAllLayers() {{
  Object.keys(layers).forEach(function(k) {{
    if (map.hasLayer(layers[k])) map.removeLayer(layers[k]);
  }});
  dimOverlay.style.display = "none";
}}

function handleStep(idx) {{
  if (idx === currentStep) return;
  currentStep = idx;
  clearAllLayers();

  var ns = [NORTHSIDE.lat, NORTHSIDE.lon];
  var localView = [ns, 14];
  var districtView = function() {{ map.fitBounds(districtBounds.pad(0.05)); }};

  switch(idx) {{
    case 0: // Intro
      layers.district.addTo(map);
      layers.schools.addTo(map);
      districtView();
      break;

    case 1: // Roads by type
      layers.district.addTo(map);
      layers.schools.addTo(map);
      layers.driveRoadsByType.addTo(map);
      map.setView(ns, 13);
      break;

    case 2: // Roads by speed
      layers.district.addTo(map);
      layers.schools.addTo(map);
      layers.driveRoadsBySpeed.addTo(map);
      map.setView(ns, 13);
      break;

    case 3: // Walk/bike
      layers.district.addTo(map);
      layers.schools.addTo(map);
      layers.walkRoads.addTo(map);
      map.setView(ns, 14);
      break;

    case 4: // Isochrones
      layers.district.addTo(map);
      layers.schools.addTo(map);
      layers.isochrones.addTo(map);
      map.setView(ns, 13);
      break;

    case 5: // Snap diagram
      layers.driveRoadsByType.addTo(map);
      layers.snapDiagram.addTo(map);
      map.setView(ns, 16);
      break;

    case 6: // Grid points
      layers.district.addTo(map);
      layers.gridPoints.addTo(map);
      layers.schools.addTo(map);
      map.setView(ns, 14);
      break;

    case 7: // Baseline heatmap
      layers.district.addTo(map);
      layers.baselineDrive.addTo(map);
      layers.schools.addTo(map);
      districtView();
      break;

    case 8: // Closure heatmap
      layers.district.addTo(map);
      layers.closureDrive.addTo(map);
      layers.schools.addTo(map);
      layers.closedX.addTo(map);
      districtView();
      break;

    case 9: // Delta heatmap
      layers.district.addTo(map);
      layers.deltaDrive.addTo(map);
      layers.schools.addTo(map);
      layers.closedX.addTo(map);
      map.setView(ns, 13);
      break;

    case 10: // Walk impact
      layers.district.addTo(map);
      layers.walkDelta.addTo(map);
      layers.schools.addTo(map);
      layers.closedX.addTo(map);
      map.setView(ns, 13);
      break;

    case 11: // Block groups
      layers.district.addTo(map);
      layers.blockGroups.addTo(map);
      layers.schools.addTo(map);
      districtView();
      break;

    case 12: // Dasymetric
      layers.blocks.addTo(map);
      layers.parcels.addTo(map);
      layers.schools.addTo(map);
      map.setView(ns, 15);
      break;

    case 13: // Routes
      layers.district.addTo(map);
      layers.routes.addTo(map);
      layers.schools.addTo(map);
      map.setView(ns, 13);
      break;

    case 14: // Baseline traffic
      layers.district.addTo(map);
      layers.trafficBase.addTo(map);
      layers.schools.addTo(map);
      map.setView(ns, 13);
      break;

    case 15: // Walk zones
      layers.district.addTo(map);
      layers.walkZones.addTo(map);
      layers.trafficBase.addTo(map);
      layers.schools.addTo(map);
      map.setView(ns, 13);
      break;

    case 16: // Traffic delta
      layers.district.addTo(map);
      layers.trafficClosure.addTo(map);
      layers.schools.addTo(map);
      layers.closedX.addTo(map);
      map.setView(ns, 13);
      break;

    case 17: // Combined
      layers.district.addTo(map);
      layers.deltaDrive.addTo(map);
      layers.trafficClosure.addTo(map);
      layers.schools.addTo(map);
      layers.closedX.addTo(map);
      map.setView(ns, 13);
      break;

    case 18: // Limitations
      layers.district.addTo(map);
      layers.deltaDrive.addTo(map);
      layers.trafficClosure.addTo(map);
      layers.schools.addTo(map);
      dimOverlay.style.display = "block";
      map.setView(ns, 13);
      break;
  }}
}}

// ── Scrollama ──
var scroller = scrollama();
scroller.setup({{
  step: ".step",
  offset: 0.5,
  progress: false,
}}).onStepEnter(function(response) {{
  document.querySelectorAll(".step").forEach(function(el) {{
    el.classList.remove("is-active");
  }});
  response.element.classList.add("is-active");
  handleStep(parseInt(response.element.dataset.step));
}});
window.addEventListener("resize", scroller.resize);

// Initial state
setTimeout(function() {{ handleStep(0); }}, 100);
</script>

</body>
</html>"""


# ═══════════════════════════════════════════════════════════════════════════
# Main
# ═══════════════════════════════════════════════════════════════════════════

def main():
    parser = argparse.ArgumentParser(
        description="Generate school closure methodology scrollytelling page"
    )
    parser.add_argument("--cache-only", action="store_true",
                        help="Only use cached data (default behavior)")
    parser.parse_args()

    print("Generating school closure methodology page ...")

    # ── 1. Schools ──
    _progress("Loading schools")
    schools = load_schools()
    northside = get_northside(schools)
    bbox = get_bbox(northside)
    wide_bbox = get_bbox(northside, pad=0.04)  # wider for district-level views

    schools_gdf = gpd.GeoDataFrame(
        schools,
        geometry=gpd.points_from_xy(schools["lon"], schools["lat"]),
        crs=CRS_WGS84,
    )
    schools_json = gdf_to_geojson_str(schools_gdf, properties=["school"])

    # ── 2. District boundary ──
    _progress("Loading district boundary")
    district = load_district_boundary()
    district_json = gdf_to_geojson_str(district, simplify_m=50)

    # ── 3. Drive roads ──
    _progress("Loading drive network")
    road_bbox = get_bbox(northside, pad=0.020)  # tight bbox for roads
    drive_edges = load_network_edges("drive", road_bbox)
    drive_roads_json = gdf_to_geojson_str(
        drive_edges, properties=["highway", "speed_mph"], simplify_m=10
    )

    # ── 4. Walk roads ──
    _progress("Loading walk network")
    try:
        walk_bbox = get_bbox(northside, pad=0.008)  # tight bbox for walk
        walk_edges = load_network_edges("walk", walk_bbox)
        walk_roads_json = gdf_to_geojson_str(
            walk_edges, properties=["highway"], simplify_m=10
        )
    except FileNotFoundError:
        _progress("Walk network not found, using empty")
        walk_roads_json = '{"type":"FeatureCollection","features":[]}'

    # ── 5. Pixel grid ──
    _progress("Loading pixel grid")
    pixel_grid = load_pixel_grid()

    # ── 6. Dijkstra + snap (drive) ──
    _progress("Loading Dijkstra and snap data (drive)")
    dijkstra_drive = load_dijkstra("drive")
    snap_drive = load_snap_data("drive")
    snap_drive = _align_snap_to_grid(snap_drive, pixel_grid)

    # ── 7. Dijkstra + snap (walk) ──
    try:
        dijkstra_walk = load_dijkstra("walk")
        snap_walk = load_snap_data("walk")
        snap_walk = _align_snap_to_grid(snap_walk, pixel_grid)
        has_walk = True
    except FileNotFoundError:
        _progress("Walk Dijkstra not found, skipping walk heatmaps")
        has_walk = False

    # ── 8. Compute heatmaps (drive) ──
    _progress("Computing drive heatmaps")
    all_schools = list(dijkstra_drive.keys())
    open_schools_no_northside = [s for s in all_schools if s != NORTHSIDE_NAME]

    baseline_times = compute_pixel_times(snap_drive, dijkstra_drive, all_schools)
    closure_times = compute_pixel_times(snap_drive, dijkstra_drive,
                                        open_schools_no_northside)
    with np.errstate(invalid="ignore"):
        delta_times = closure_times - baseline_times
    delta_times[~np.isfinite(delta_times) | (delta_times < 0.1)] = 0

    baseline_grid, baseline_bounds = rasterize_times(
        pixel_grid, baseline_times, snap_drive["reachable"]
    )
    closure_grid, closure_bounds = rasterize_times(
        pixel_grid, closure_times, snap_drive["reachable"]
    )
    delta_grid, delta_bounds = rasterize_times(
        pixel_grid, delta_times,
        snap_drive["reachable"] & (delta_times > 0.1)
    )

    abs_range = MODE_RANGES["drive"]["abs"]
    delta_range = MODE_RANGES["drive"]["delta"]
    baseline_drive_b64 = grid_to_base64_png(
        baseline_grid, "YlOrRd", vmin=abs_range[0], vmax=abs_range[1]
    )
    closure_drive_b64 = grid_to_base64_png(
        closure_grid, "YlOrRd", vmin=abs_range[0], vmax=abs_range[1]
    )
    delta_drive_b64 = grid_to_base64_png(
        delta_grid, "Oranges", vmin=delta_range[0], vmax=delta_range[1]
    )

    # ── 9. Walk heatmaps ──
    if has_walk:
        _progress("Computing walk heatmaps")
        walk_baseline = compute_pixel_times(snap_walk, dijkstra_walk, all_schools)
        walk_closure = compute_pixel_times(snap_walk, dijkstra_walk,
                                           open_schools_no_northside)
        with np.errstate(invalid="ignore"):
            walk_delta = walk_closure - walk_baseline
        walk_delta[~np.isfinite(walk_delta) | (walk_delta < 0.1)] = 0

        walk_bl_grid, walk_bl_bounds = rasterize_times(
            pixel_grid, walk_baseline, snap_walk["reachable"]
        )
        walk_dl_grid, walk_dl_bounds = rasterize_times(
            pixel_grid, walk_delta,
            snap_walk["reachable"] & (walk_delta > 0.1)
        )
        walk_abs_range = MODE_RANGES["walk"]["abs"]
        walk_delta_range = MODE_RANGES["walk"]["delta"]
        walk_baseline_b64 = grid_to_base64_png(
            walk_bl_grid, "YlOrRd", vmin=walk_abs_range[0], vmax=walk_abs_range[1]
        )
        walk_delta_b64 = grid_to_base64_png(
            walk_dl_grid, "Oranges", vmin=walk_delta_range[0], vmax=walk_delta_range[1]
        )
    else:
        walk_baseline_b64 = ""
        walk_delta_b64 = ""
        walk_bl_bounds = (0, 0, 0, 0)
        walk_dl_bounds = (0, 0, 0, 0)

    # ── 10. Isochrones ──
    _progress("Computing isochrones")
    isochrones_json = compute_isochrones(
        pixel_grid, dijkstra_drive, snap_drive, NORTHSIDE_NAME
    )

    # ── 11. Snap diagram ──
    _progress("Loading graph for snap diagram and routes")
    G_drive = load_graph("drive")
    snap_diagram_json = compute_snap_diagram(pixel_grid, snap_drive, G_drive, northside)

    # ── 12. Grid points sample ──
    grid_points_json = compute_grid_points_sample(pixel_grid, bbox, max_points=150)

    # ── 13. Block groups ──
    _progress("Loading block groups")
    block_groups = load_block_groups_with_children(wide_bbox)
    block_groups_json = gdf_to_geojson_str(
        block_groups, properties=["total_children", "GEOID"], simplify_m=20
    )

    # ── 14. Blocks + parcels ──
    _progress("Loading blocks and parcels")
    blocks = load_blocks(bbox)
    blocks_json = gdf_to_geojson_str(blocks, simplify_m=20)
    parcels = load_residential_parcels(bbox)
    parcels_json = gdf_to_geojson_str(parcels, simplify_m=15)

    # ── 15. Example routes ──
    _progress("Computing example routes")
    routes_json = compute_example_routes(
        G_drive, dijkstra_drive, pixel_grid, snap_drive, NORTHSIDE_NAME
    )

    # ── 16. Walk zones ──
    _progress("Loading walk zones")
    walk_zones = load_walk_zones()
    walk_zones_json = gdf_to_geojson_str(
        walk_zones, properties=["school"], simplify_m=20
    )

    # ── 17. Traffic ──
    _progress("Computing baseline traffic (this may take a moment)")
    pixel_children = load_pixel_children()
    traffic_base_json = compute_traffic_edges(
        G_drive, dijkstra_drive, snap_drive, pixel_grid,
        pixel_children, all_schools, wide_bbox
    )

    _progress("Computing closure traffic")
    traffic_closure_raw = compute_traffic_edges(
        G_drive, dijkstra_drive, snap_drive, pixel_grid,
        pixel_children, open_schools_no_northside, wide_bbox
    )

    # Compute delta for traffic (closure - baseline)
    base_traffic = json.loads(traffic_base_json)
    closure_traffic = json.loads(traffic_closure_raw)

    # Build lookup: edge coords → baseline children
    base_lookup = {}
    for f in base_traffic["features"]:
        key = json.dumps(f["geometry"]["coordinates"])
        base_lookup[key] = f["properties"]["children"]

    # Compute delta features
    delta_features = []
    seen_keys = set()
    for f in closure_traffic["features"]:
        key = json.dumps(f["geometry"]["coordinates"])
        seen_keys.add(key)
        base_val = base_lookup.get(key, 0)
        delta = f["properties"]["children"] - base_val
        if abs(delta) >= 0.5:
            delta_features.append({
                "type": "Feature",
                "geometry": f["geometry"],
                "properties": {"children": round(delta, 1)},
            })
    # Edges that lost all traffic
    for f in base_traffic["features"]:
        key = json.dumps(f["geometry"]["coordinates"])
        if key not in seen_keys:
            delta_features.append({
                "type": "Feature",
                "geometry": f["geometry"],
                "properties": {"children": round(-f["properties"]["children"], 1)},
            })

    traffic_closure_json = json.dumps(
        {"type": "FeatureCollection", "features": delta_features},
        separators=(",", ":"),
    )

    # ── 18. Build HTML ──
    _progress("Building HTML")
    data = {
        "schools_json": schools_json,
        "district_json": district_json,
        "drive_roads_json": drive_roads_json,
        "walk_roads_json": walk_roads_json,
        "isochrones_json": isochrones_json,
        "snap_diagram_json": snap_diagram_json,
        "grid_points_json": grid_points_json,
        "block_groups_json": block_groups_json,
        "blocks_json": blocks_json,
        "parcels_json": parcels_json,
        "routes_json": routes_json,
        "walk_zones_json": walk_zones_json,
        "traffic_base_json": traffic_base_json,
        "traffic_closure_json": traffic_closure_json,
        "northside": northside,
        "baseline_drive_b64": baseline_drive_b64,
        "baseline_drive_bounds": list(baseline_bounds),
        "closure_drive_b64": closure_drive_b64,
        "closure_drive_bounds": list(closure_bounds),
        "delta_drive_b64": delta_drive_b64,
        "delta_drive_bounds": list(delta_bounds),
        "walk_baseline_b64": walk_baseline_b64,
        "walk_baseline_bounds": list(walk_bl_bounds),
        "walk_delta_b64": walk_delta_b64,
        "walk_delta_bounds": list(walk_dl_bounds),
    }

    html = build_html(data)

    ASSETS_MAPS.mkdir(parents=True, exist_ok=True)
    OUTPUT_HTML.write_text(html, encoding="utf-8")

    size_kb = OUTPUT_HTML.stat().st_size / 1024
    _progress(f"Written {OUTPUT_HTML} ({size_kb:.0f} KB)")
    print("Done!")


if __name__ == "__main__":
    main()
