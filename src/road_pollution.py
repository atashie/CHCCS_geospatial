"""
Road Pollution Exposure Analysis for CHCCS Elementary Schools

Compares traffic-related air pollution (TRAP) exposure across all 11 elementary
schools in the Chapel Hill-Carrboro City Schools district, with tree canopy
mitigation analysis.

Methodology:
- Road Pollution Index uses exponential decay from road segments, weighted by
  road classification as a proxy for traffic volume (NOT actual AADT counts)
- Road network: OSM via osmnx, network_type="drive_service" (includes service
  roads), county boundary buffered by max analysis radius for edge coverage
- Tree canopy mitigation using ESA WorldCover V2 2021 (10m), based on literature
  meta-analyses of urban vegetation air quality effects (NOT Chapel Hill-specific)
- Index is RELATIVE/COMPARATIVE, not an absolute health risk assessment

Literature basis:
- Karner et al. (2010): Near-road air quality monitoring review
- Health Effects Institute: 300-500m primary impact zone consensus
- Nowak et al. (2014): Urban tree air quality effects meta-analysis

Outputs:
- data/processed/road_pollution_scores.csv
- data/processed/ROAD_POLLUTION.md
- assets/charts/road_pollution_comparison.png
- assets/maps/road_pollution_raw_map.html
- assets/maps/tree_canopy_map.html
- assets/maps/road_pollution_net_map.html
- assets/maps/road_pollution_combined_map.html
"""

import argparse
import sys
import warnings
from pathlib import Path

import folium
import geopandas as gpd
import matplotlib.patches as mpatches
import matplotlib.pyplot as plt
import numpy as np
import pandas as pd
import rasterio
from pyproj import Transformer
from scipy.spatial import cKDTree
from shapely.geometry import Point, box

warnings.filterwarnings("ignore", category=FutureWarning)

# ---------------------------------------------------------------------------
# Paths
# ---------------------------------------------------------------------------
PROJECT_ROOT = Path(__file__).parent.parent
DATA_PROCESSED = PROJECT_ROOT / "data" / "processed"
DATA_CACHE = PROJECT_ROOT / "data" / "cache"
ASSETS_CHARTS = PROJECT_ROOT / "assets" / "charts"
ASSETS_MAPS = PROJECT_ROOT / "assets" / "maps"

SCHOOL_CSV = DATA_CACHE / "nces_school_locations.csv"
ROAD_CACHE = DATA_CACHE / "osm_roads_orange_county_buffered.gpkg"
AADT_CACHE = DATA_CACHE / "ncdot_aadt_orange_county.gpkg"
LULC_CACHE = DATA_CACHE / "esa_worldcover_orange_county.tif"
ASSETS_MAPS_DEBUG = ASSETS_MAPS / "debug"

# ---------------------------------------------------------------------------
# Constants
# ---------------------------------------------------------------------------
# Exponential decay rate (m^-1) — composite for NOx/BC/UFP
# Literature: Karner et al. (2010), HEI consensus
LAMBDA = 0.003

# Road-class AADT proxy weights (NOT actual traffic counts)
# "unclassified" in OSM = minor public through-roads (below tertiary, above residential)
# "service" = parking-lot access roads, alleys, etc. (low-speed, low-volume)
ROAD_WEIGHTS = {
    "motorway": 1.000,
    "motorway_link": 0.800,
    "trunk": 0.600,
    "trunk_link": 0.480,
    "primary": 0.300,
    "primary_link": 0.240,
    "secondary": 0.150,
    "secondary_link": 0.120,
    "tertiary": 0.060,
    "tertiary_link": 0.048,
    "unclassified": 0.020,
    "residential": 0.010,
    "service": 0.005,
    "living_street": 0.005,
}

# Service road subtype weight overrides (applied on top of base "service" weight)
SERVICE_SUBTYPE_WEIGHTS = {
    "driveway": 0.002,
    "alley": 0.003,
    "drive-through": 0.001,
}

# Tree canopy mitigation: alpha * canopy_cover
# alpha = 0.56 (2.8% PM2.5 reduction per 5% canopy increase)
# Literature: Nowak et al. (2014) meta-analysis
ALPHA = 0.56
MAX_MITIGATION = 0.80  # cap at 80% maximum reduction

# ESA WorldCover V2 2021: Tree cover = class 10
TREE_CLASS = 10

# Analysis radii (meters)
RADII = [500, 1000]

# Road sub-segment length for discretization (~50 m)
SEGMENT_LENGTH = 50.0

# County-wide grid resolution (meters)
DEFAULT_GRID_RESOLUTION = 100

# CRS
CRS_WGS84 = "EPSG:4326"
CRS_UTM17N = "EPSG:32617"

# Orange County, NC bounding box (WGS84)
ORANGE_COUNTY_BBOX = (-79.55, 35.73, -78.90, 36.25)

# NCDOT AADT (Annual Average Daily Traffic) integration
AADT_URL = (
    "https://services.arcgis.com/NuWFvHYDMVmmxMeM/ArcGIS/rest/services/"
    "NCDOT_AADT_Stations/FeatureServer/0/query"
)
AADT_SNAP_DISTANCE_M = 50     # max meters to snap AADT station to OSM road
AADT_MOTORWAY_REFERENCE = 50000  # AADT for weight=1.0 baseline
AADT_PAGE_SIZE = 1000

# Chart styling
NEUTRAL_COLOR = "#C0C0C0"
BAR_COLOR = "#3388ff"
EXCEEDED_COLOR = "#28A745"

plt.rcParams["font.family"] = "sans-serif"
plt.rcParams["font.sans-serif"] = ["Segoe UI", "Tahoma", "DejaVu Sans"]
plt.style.use("seaborn-v0_8-whitegrid")

# Chapel Hill center for maps
CHAPEL_HILL_CENTER = [35.9132, -79.0558]

# Road class colors for debug maps
ROAD_COLORS = {
    "motorway": "#e41a1c", "motorway_link": "#e41a1c",
    "trunk": "#ff7f00", "trunk_link": "#ff7f00",
    "primary": "#377eb8", "primary_link": "#377eb8",
    "secondary": "#4daf4a", "secondary_link": "#4daf4a",
    "tertiary": "#984ea3", "tertiary_link": "#984ea3",
    "unclassified": "#b07d2b",
    "residential": "#999999", "living_street": "#cccccc",
    "service": "#dddddd",
}
ROAD_LINE_WIDTHS = {
    "motorway": 4, "motorway_link": 3,
    "trunk": 3.5, "trunk_link": 2.5,
    "primary": 3, "primary_link": 2,
    "secondary": 2.5, "secondary_link": 1.5,
    "tertiary": 2, "tertiary_link": 1.5,
    "unclassified": 1.5,
    "residential": 1.5, "living_street": 1,
    "service": 0.75,
}


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------
def ensure_directories():
    """Create output directories if they don't exist."""
    for d in [DATA_PROCESSED, DATA_CACHE, ASSETS_CHARTS, ASSETS_MAPS, ASSETS_MAPS_DEBUG]:
        d.mkdir(parents=True, exist_ok=True)


def _progress(msg: str):
    """Print a progress message."""
    print(f"  ... {msg}")


def _score_to_color(normalized_score: float) -> str:
    """Convert a normalized TRAP score (0-100) to a hex color on green→yellow→red gradient."""
    val = max(0.0, min(1.0, normalized_score / 100.0))
    if val < 0.5:
        r = int(255 * (val / 0.5))
        g = 200
    else:
        r = 255
        g = int(200 * (1 - (val - 0.5) / 0.5))
    return f"#{r:02x}{g:02x}00"


# ---------------------------------------------------------------------------
# 1. Download / load school locations (NCES)
# ---------------------------------------------------------------------------
# NCES name -> project name mapping (NCES uses official names that may differ)
_NCES_NAME_MAP = {
    "CARRBORO ELEMENTARY": "Carrboro Elementary",
    "EPHESUS ELEMENTARY": "Ephesus Elementary",
    "EPHESUS ROAD ELEMENTARY": "Ephesus Elementary",
    "ESTES HILLS ELEMENTARY": "Estes Hills Elementary",
    "FPG ELEMENTARY": "Frank Porter Graham Bilingue",
    "FRANK PORTER GRAHAM ELEMENTARY": "Frank Porter Graham Bilingue",
    "FRANK PORTER GRAHAM BILINGUE": "Frank Porter Graham Bilingue",
    "FPG BILINGUE": "Frank Porter Graham Bilingue",
    "GLENWOOD ELEMENTARY": "Glenwood Elementary",
    "MCDOUGLE ELEMENTARY": "McDougle Elementary",
    "MORRIS GROVE ELEMENTARY": "Morris Grove Elementary",
    "NORTHSIDE ELEMENTARY": "Northside Elementary",
    "RASHKIS ELEMENTARY": "Rashkis Elementary",
    "SCROGGS ELEMENTARY": "Scroggs Elementary",
    "SEAWELL ELEMENTARY": "Seawell Elementary",
}

# Known CHCCS elementary school names (project-standard)
_CHCCS_ELEMENTARY = {
    "Carrboro Elementary",
    "Ephesus Elementary",
    "Estes Hills Elementary",
    "Frank Porter Graham Bilingue",
    "Glenwood Elementary",
    "McDougle Elementary",
    "Morris Grove Elementary",
    "Northside Elementary",
    "Rashkis Elementary",
    "Scroggs Elementary",
    "Seawell Elementary",
}


def download_school_locations(cache_only: bool = False) -> Path:
    """
    Download CHCCS elementary school locations from NCES EDGE 2023-24 API.
    Caches to data/cache/nces_school_locations.csv.

    NCES EDGE is the authoritative federal dataset for US school coordinates.
    LEAID 3700720 = Chapel Hill-Carrboro City Schools.
    """
    if SCHOOL_CSV.exists():
        _progress(f"Loading cached school locations from {SCHOOL_CSV}")
        return SCHOOL_CSV

    if cache_only:
        raise FileNotFoundError(
            f"School locations cache not found at {SCHOOL_CSV}. "
            "Run without --cache-only to download."
        )

    _progress("Downloading school locations from NCES EDGE API ...")
    import requests

    url = (
        "https://nces.ed.gov/opengis/rest/services/"
        "K12_School_Locations/EDGE_GEOCODE_PUBLICSCH_2324/"
        "MapServer/0/query"
    )
    params = {
        "where": "LEAID = '3700720'",
        "outFields": "NCESSCH,NAME,STREET,CITY,LAT,LON",
        "returnGeometry": "false",
        "f": "json",
    }

    resp = requests.get(url, params=params, timeout=30)
    resp.raise_for_status()
    data = resp.json()

    if "features" not in data or len(data["features"]) == 0:
        raise RuntimeError(
            "NCES API returned no features for LEAID 3700720. "
            "Check API availability."
        )

    rows = []
    for feat in data["features"]:
        attrs = feat["attributes"]
        nces_name = attrs["NAME"].strip().upper()
        project_name = _NCES_NAME_MAP.get(nces_name)
        if project_name is None:
            continue  # skip non-elementary or unrecognized schools
        rows.append({
            "nces_id": attrs["NCESSCH"],
            "school": project_name,
            "lat": attrs["LAT"],
            "lon": attrs["LON"],
            "address": attrs.get("STREET", ""),
            "city": attrs.get("CITY", ""),
        })

    df = pd.DataFrame(rows)

    # Verify we got all 11 schools
    found = set(df["school"].tolist())
    missing = _CHCCS_ELEMENTARY - found
    if missing:
        _progress(f"WARNING: Missing schools from NCES: {missing}")

    # Deduplicate (keep first if NCES returns multiple matches)
    df = df.drop_duplicates(subset="school", keep="first")

    df.to_csv(SCHOOL_CSV, index=False)
    _progress(f"Cached {len(df)} school locations to {SCHOOL_CSV}")
    return SCHOOL_CSV


def load_schools() -> gpd.GeoDataFrame:
    """Load CHCCS elementary schools from CSV + hypothetical locations."""
    df = pd.read_csv(SCHOOL_CSV)

    # Append hypothetical "New FPG Location" at Culbreth Middle School site
    # (NCES ID 370072000301, 225 Culbreth Rd, Chapel Hill)
    new_fpg = pd.DataFrame([{
        "nces_id": "hypothetical_new_fpg",
        "school": "New FPG Location",
        "lat": 35.8898,
        "lon": -79.0675,
        "address": "225 Culbreth Rd",
        "city": "Chapel Hill",
    }])
    df = pd.concat([df, new_fpg], ignore_index=True)

    gdf = gpd.GeoDataFrame(
        df,
        geometry=gpd.points_from_xy(df["lon"], df["lat"]),
        crs=CRS_WGS84,
    )
    return gdf


# ---------------------------------------------------------------------------
# 2. Download / load road network
# ---------------------------------------------------------------------------
def download_road_network(cache_only: bool = False) -> gpd.GeoDataFrame:
    """
    Download road network for Orange County (+ buffer) via osmnx.

    Uses network_type="drive_service" to include service roads (parking lot
    access, alleys, etc.) in addition to all drivable through-roads.

    The county boundary is buffered by max(RADII) so that schools near the
    county edge (e.g., Rashkis Elementary, ~60 m from border) still capture
    all road segments within the full analysis radius.

    Returns a GeoDataFrame of road edges cached as GeoPackage.
    """
    if ROAD_CACHE.exists():
        _progress(f"Loading cached roads from {ROAD_CACHE}")
        return gpd.read_file(ROAD_CACHE)

    if cache_only:
        raise FileNotFoundError(
            f"Road cache not found at {ROAD_CACHE}. "
            "Run without --cache-only to download."
        )

    _progress("Downloading Orange County road network from OpenStreetMap ...")
    import osmnx as ox

    # Get county boundary and buffer by max analysis radius so that schools
    # near the county edge still capture all roads within the analysis radius.
    buffer_m = max(RADII)
    _progress(f"Buffering county boundary by {buffer_m} m for edge coverage ...")
    county = ox.geocode_to_gdf("Orange County, North Carolina, USA")
    county_utm = county.to_crs(CRS_UTM17N)
    buffered_utm = county_utm.geometry.iloc[0].buffer(buffer_m)
    buffered_wgs = gpd.GeoDataFrame(
        geometry=[buffered_utm], crs=CRS_UTM17N,
    ).to_crs(CRS_WGS84).geometry.iloc[0]

    # Download with drive_service to include service roads (parking lot
    # access, alleys) but still exclude parking aisles and private roads.
    G = ox.graph_from_polygon(
        buffered_wgs,
        network_type="drive_service",
        simplify=True,
    )
    edges = ox.graph_to_gdfs(G, nodes=False, edges=True)

    # Keep relevant columns — include "service" for subtype weights
    keep_cols = ["geometry", "highway", "name", "length", "service"]
    available = [c for c in keep_cols if c in edges.columns]
    edges = edges[available].copy()

    # highway can be a list — flatten to first value
    def _first(val):
        if isinstance(val, list):
            return val[0]
        return val

    edges["highway"] = edges["highway"].apply(_first)
    if "service" in edges.columns:
        edges["service"] = edges["service"].apply(_first)

    edges = edges.to_crs(CRS_WGS84)
    edges.to_file(ROAD_CACHE, driver="GPKG")
    _progress(f"Cached {len(edges)} road segments to {ROAD_CACHE}")
    return edges


# ---------------------------------------------------------------------------
# 2b. Download NCDOT AADT stations (Orange County)
# ---------------------------------------------------------------------------
def download_ncdot_aadt(cache_only: bool = False) -> gpd.GeoDataFrame:
    """
    Download NCDOT AADT station data for Orange County via ArcGIS REST API.

    Paginates through the feature service, extracts the most recent non-blank
    AADT year value (2022 back to 2002), and caches as GeoPackage.

    Returns a GeoDataFrame with columns:
        location_id, route, route_class, location_desc, aadt, aadt_year, geometry
    """
    if AADT_CACHE.exists():
        _progress(f"Loading cached AADT data from {AADT_CACHE}")
        return gpd.read_file(AADT_CACHE)

    if cache_only:
        raise FileNotFoundError(
            f"AADT cache not found at {AADT_CACHE}. "
            "Run without --cache-only to download."
        )

    import requests

    _progress("Downloading NCDOT AADT stations for Orange County ...")

    all_features: list[dict] = []
    offset = 0

    while True:
        params = {
            "where": "COUNTY='ORANGE'",
            "outFields": "*",
            "outSR": "4326",
            "returnGeometry": "true",
            "f": "json",
            "resultRecordCount": AADT_PAGE_SIZE,
            "resultOffset": offset,
        }
        r = requests.get(AADT_URL, params=params, timeout=60)
        r.raise_for_status()
        data = r.json()

        if "error" in data:
            raise RuntimeError(f"NCDOT ArcGIS API error: {data['error']}")

        features = data.get("features", [])
        if not features:
            break

        all_features.extend(features)
        _progress(f"  Fetched {len(all_features)} AADT stations so far ...")

        if len(features) < AADT_PAGE_SIZE:
            break
        offset += AADT_PAGE_SIZE

    _progress(f"Downloaded {len(all_features)} total AADT stations")

    # Extract most recent AADT value for each station
    aadt_years = [f"AADT_{y}" for y in range(2022, 2001, -1)]

    rows = []
    skipped_no_geom = 0
    skipped_no_aadt = 0

    for feat in all_features:
        attrs = feat.get("attributes", {})
        geom = feat.get("geometry")

        if not geom or geom.get("x") is None or geom.get("y") is None:
            skipped_no_geom += 1
            continue

        # Find most recent non-blank AADT value
        aadt_val = None
        aadt_year = None
        for year_col in aadt_years:
            raw_val = attrs.get(year_col, "")
            if raw_val is None:
                continue
            raw_str = str(raw_val).strip().replace(",", "")
            if raw_str and raw_str != "0":
                try:
                    aadt_val = int(float(raw_str))
                    aadt_year = int(year_col.split("_")[1])
                    break
                except (ValueError, TypeError):
                    continue

        if aadt_val is None or aadt_val <= 0:
            skipped_no_aadt += 1
            continue

        rows.append({
            "location_id": str(attrs.get("LocationID", "")),
            "route": str(attrs.get("ROUTE", "")),
            "route_class": attrs.get("RTE_CLS"),
            "location_desc": str(attrs.get("LOCATION", "")),
            "aadt": aadt_val,
            "aadt_year": aadt_year,
            "geometry": Point(geom["x"], geom["y"]),
        })

    if skipped_no_geom:
        _progress(f"Skipped {skipped_no_geom} stations with no geometry")
    if skipped_no_aadt:
        _progress(f"Skipped {skipped_no_aadt} stations with no valid AADT value")

    gdf = gpd.GeoDataFrame(rows, crs=CRS_WGS84)
    _progress(f"Built GeoDataFrame with {len(gdf)} AADT stations")

    # Summary statistics
    _progress(f"  AADT range: {gdf['aadt'].min():,} - {gdf['aadt'].max():,}")
    _progress(f"  AADT years: {gdf['aadt_year'].min()} - {gdf['aadt_year'].max()}")

    DATA_CACHE.mkdir(parents=True, exist_ok=True)
    gdf.to_file(AADT_CACHE, driver="GPKG")
    _progress(f"Cached {len(gdf)} AADT stations to {AADT_CACHE}")

    return gdf


# ---------------------------------------------------------------------------
# 3. Filter & prepare roads
# ---------------------------------------------------------------------------
def filter_and_prepare_roads(roads: gpd.GeoDataFrame) -> gpd.GeoDataFrame:
    """Filter to relevant road types and add pollution weight column.

    Service roads receive subtype-specific weights (driveways, alleys get
    lower weights than untagged service roads like parking-lot access roads).
    Road types not in ROAD_WEIGHTS are logged before being dropped.
    """
    relevant = set(ROAD_WEIGHTS.keys())
    mask = roads["highway"].isin(relevant)

    # Log any road types being dropped so silent data loss is visible
    dropped = roads.loc[~mask]
    if len(dropped) > 0:
        dropped_types = dropped["highway"].value_counts().to_dict()
        _progress(
            f"Dropping {len(dropped)} road segments with unweighted types: "
            f"{dropped_types}"
        )

    filtered = roads.loc[mask].copy()

    # Assign base weight from road class
    filtered["weight"] = filtered["highway"].map(ROAD_WEIGHTS)

    # Override service road weights with subtype-specific values
    if "service" in filtered.columns:
        for subtype, weight in SERVICE_SUBTYPE_WEIGHTS.items():
            subtype_mask = (filtered["highway"] == "service") & (
                filtered["service"] == subtype
            )
            filtered.loc[subtype_mask, "weight"] = weight

    type_summary = filtered.groupby("highway")["weight"].agg(["count", "mean"])
    _progress(f"Prepared {len(filtered)} road segments:")
    for hw, row in type_summary.sort_values("mean", ascending=False).iterrows():
        _progress(f"  {hw}: {int(row['count'])} segments (avg weight={row['mean']:.3f})")

    return filtered


# ---------------------------------------------------------------------------
# 3b. Apply NCDOT AADT weight overrides
# ---------------------------------------------------------------------------
def apply_aadt_overrides(
    roads: gpd.GeoDataFrame,
    aadt_stations: gpd.GeoDataFrame,
) -> gpd.GeoDataFrame:
    """Override proxy road weights with NCDOT AADT-derived weights where available.

    For each AADT station, finds the nearest OSM road segment within
    AADT_SNAP_DISTANCE_M and replaces its proxy weight with
    aadt / AADT_MOTORWAY_REFERENCE, clipped to [0.001, 2.0].

    Adds a 'weight_source' column: "aadt" or "proxy".

    Logs summary statistics and large discrepancies for diagnostics.
    """
    if len(aadt_stations) == 0:
        _progress("No AADT stations available -- keeping all proxy weights")
        roads["weight_source"] = "proxy"
        return roads

    # Project both to UTM for distance computation
    roads_utm = roads.to_crs(CRS_UTM17N)
    aadt_utm = aadt_stations.to_crs(CRS_UTM17N)

    # Find nearest road for each AADT station
    joined = gpd.sjoin_nearest(
        aadt_utm,
        roads_utm,
        how="inner",
        max_distance=AADT_SNAP_DISTANCE_M,
        distance_col="snap_distance",
    )

    _progress(
        f"AADT snap: {len(joined)} of {len(aadt_stations)} stations matched "
        f"to OSM roads within {AADT_SNAP_DISTANCE_M}m"
    )

    if len(joined) == 0:
        _progress("No AADT stations matched -- keeping all proxy weights")
        roads["weight_source"] = "proxy"
        return roads

    # Initialize weight_source column
    roads["weight_source"] = "proxy"

    # Compute AADT-derived weights and apply overrides
    overridden = 0
    discrepancies = []

    for _, match in joined.iterrows():
        road_idx = match["index_right"]
        aadt_val = match["aadt"]
        aadt_weight = np.clip(aadt_val / AADT_MOTORWAY_REFERENCE, 0.001, 2.0)
        proxy_weight = roads.loc[road_idx, "weight"]

        # Log large discrepancies (AADT weight differs from proxy by >2x)
        if proxy_weight > 0:
            ratio = aadt_weight / proxy_weight
            if ratio > 2.0 or ratio < 0.5:
                discrepancies.append({
                    "road_idx": road_idx,
                    "highway": roads.loc[road_idx, "highway"],
                    "name": roads.loc[road_idx].get("name", ""),
                    "route": match.get("route", ""),
                    "aadt": aadt_val,
                    "aadt_weight": round(aadt_weight, 4),
                    "proxy_weight": round(proxy_weight, 4),
                    "ratio": round(ratio, 2),
                })

        roads.loc[road_idx, "weight"] = aadt_weight
        roads.loc[road_idx, "weight_source"] = "aadt"
        overridden += 1

    # Deduplicate: count unique road indices overridden
    unique_overridden = roads[roads["weight_source"] == "aadt"].shape[0]
    total_roads = len(roads)

    _progress(
        f"AADT overrides applied: {unique_overridden} road segments "
        f"({unique_overridden / total_roads * 100:.1f}%) use measured AADT, "
        f"{total_roads - unique_overridden} ({(total_roads - unique_overridden) / total_roads * 100:.1f}%) "
        f"use proxy weights"
    )

    if discrepancies:
        _progress(f"Large discrepancies (AADT vs proxy >2x): {len(discrepancies)}")
        for d in discrepancies[:10]:  # Show first 10
            name = d["name"] if d["name"] and not pd.isna(d["name"]) else d["route"]
            _progress(
                f"  {d['highway']} '{name}': "
                f"AADT={d['aadt']:,} -> weight={d['aadt_weight']:.4f} "
                f"(proxy was {d['proxy_weight']:.4f}, {d['ratio']:.1f}x diff)"
            )
        if len(discrepancies) > 10:
            _progress(f"  ... and {len(discrepancies) - 10} more")

    return roads


# ---------------------------------------------------------------------------
# 4. Discretize roads into ~50 m sub-segments (point representation)
# ---------------------------------------------------------------------------
def discretize_roads(roads: gpd.GeoDataFrame) -> gpd.GeoDataFrame:
    """
    Convert road linestrings into ~50 m sub-segment centroids in UTM.
    Returns GeoDataFrame with columns: geometry (Point, UTM), weight.
    """
    roads_utm = roads.to_crs(CRS_UTM17N)
    points = []
    weights = []

    for _, row in roads_utm.iterrows():
        geom = row.geometry
        w = row["weight"]
        length = geom.length
        if length == 0:
            continue
        n_segments = max(1, int(length / SEGMENT_LENGTH))
        for i in range(n_segments):
            frac = (i + 0.5) / n_segments
            pt = geom.interpolate(frac, normalized=True)
            points.append(pt)
            weights.append(w)

    gdf = gpd.GeoDataFrame(
        {"weight": weights},
        geometry=points,
        crs=CRS_UTM17N,
    )
    _progress(f"Discretized into {len(gdf)} sub-segment points")
    return gdf


# ---------------------------------------------------------------------------
# 5. Calculate raw pollution index for a single school
# ---------------------------------------------------------------------------
def calculate_raw_pollution(
    school_point_utm: Point,
    coords: np.ndarray,
    seg_weights: np.ndarray,
    tree: cKDTree,
    radius: float,
) -> float:
    """
    Sum of W_i * exp(-lambda * d_i) for all road sub-segments within radius.

    Uses direct distance computation from coords array to avoid index-order
    mismatch between query_ball_point and tree.query (Bug A fix).
    """
    idx_list = tree.query_ball_point(
        [school_point_utm.x, school_point_utm.y], r=radius
    )
    if len(idx_list) == 0:
        return 0.0
    pts = coords[idx_list]
    dists = np.sqrt(
        (pts[:, 0] - school_point_utm.x) ** 2
        + (pts[:, 1] - school_point_utm.y) ** 2
    )
    ws = seg_weights[idx_list]
    return float(np.sum(ws * np.exp(-LAMBDA * dists)))


# ---------------------------------------------------------------------------
# 6. Download / load ESA WorldCover tree cover
# ---------------------------------------------------------------------------
def download_esa_worldcover(cache_only: bool = False) -> Path:
    """
    Download ESA WorldCover V2 2021 (10 m) from Planetary Computer STAC API
    for Orange County extent. Reproject from EPSG:4326 to EPSG:32617 so
    downstream buffer calculations work in meters. Cache as GeoTIFF.

    Tree cover class = 10 in ESA WorldCover.
    """
    if LULC_CACHE.exists():
        _progress(f"Loading cached LULC from {LULC_CACHE}")
        return LULC_CACHE

    if cache_only:
        raise FileNotFoundError(
            f"LULC cache not found at {LULC_CACHE}. "
            "Run without --cache-only to download."
        )

    _progress("Downloading ESA WorldCover from Planetary Computer STAC API ...")
    import planetary_computer
    import pystac_client
    from rasterio.merge import merge
    from rasterio.warp import Resampling, calculate_default_transform, reproject

    catalog = pystac_client.Client.open(
        "https://planetarycomputer.microsoft.com/api/stac/v1",
        modifier=planetary_computer.sign_inplace,
    )

    bbox = ORANGE_COUNTY_BBOX
    search = catalog.search(
        collections=["esa-worldcover"],
        bbox=bbox,
        datetime="2021",
    )
    items = list(search.items())
    if not items:
        raise RuntimeError(
            "No ESA WorldCover items found for Orange County. "
            "Check Planetary Computer availability."
        )

    _progress(f"Found {len(items)} WorldCover tile(s). Reading and cropping ...")

    # Read each tile, windowed to bbox
    tile_datasets = []
    memfiles = []  # Keep MemoryFile objects alive for merge
    for item in items:
        href = item.assets["map"].href
        _progress(f"  Tile: {href[-30:]}")

        with rasterio.open(href) as src:
            window = src.window(*bbox)
            data = src.read(1, window=window, boundless=True, fill_value=0)
            transform = src.window_transform(window)

            from rasterio.io import MemoryFile

            memfile = MemoryFile()
            memfiles.append(memfile)  # prevent GC
            with memfile.open(
                driver="GTiff",
                height=data.shape[0],
                width=data.shape[1],
                count=1,
                dtype=data.dtype,
                crs=src.crs,
                transform=transform,
            ) as mem_ds:
                mem_ds.write(data, 1)
            # Reopen in read mode for merge
            tile_datasets.append(memfile.open())

    # Merge tiles
    _progress("Merging tiles ...")
    merged_data, merged_transform = merge(tile_datasets)
    merged_data = merged_data[0]  # single band
    src_crs = tile_datasets[0].crs

    # Clean up
    for ds in tile_datasets:
        ds.close()
    for mf in memfiles:
        mf.close()

    # Reproject EPSG:4326 -> EPSG:32617
    _progress("Reprojecting to EPSG:32617 ...")
    dst_crs = CRS_UTM17N
    dst_transform, dst_width, dst_height = calculate_default_transform(
        src_crs, dst_crs,
        merged_data.shape[1], merged_data.shape[0],
        left=merged_transform.c,
        bottom=merged_transform.f + merged_transform.e * merged_data.shape[0],
        right=merged_transform.c + merged_transform.a * merged_data.shape[1],
        top=merged_transform.f,
    )

    dst_data = np.zeros((dst_height, dst_width), dtype=merged_data.dtype)
    reproject(
        source=merged_data,
        destination=dst_data,
        src_transform=merged_transform,
        src_crs=src_crs,
        dst_transform=dst_transform,
        dst_crs=dst_crs,
        resampling=Resampling.nearest,  # categorical data
    )

    # Write to cache
    profile = {
        "driver": "GTiff",
        "height": dst_height,
        "width": dst_width,
        "count": 1,
        "dtype": dst_data.dtype,
        "crs": dst_crs,
        "transform": dst_transform,
        "compress": "lzw",
    }
    with rasterio.open(LULC_CACHE, "w", **profile) as dst:
        dst.write(dst_data, 1)

    _progress(f"Cached LULC raster ({dst_data.shape}) to {LULC_CACHE}")
    return LULC_CACHE


# ---------------------------------------------------------------------------
# 7. Calculate tree canopy fraction
# ---------------------------------------------------------------------------
def calculate_tree_canopy(
    school_point_wgs: Point,
    lulc_path: Path,
    radius: float,
) -> float:
    """
    Calculate tree canopy fraction within `radius` meters of a school.
    ESA WorldCover: Trees = class 10.
    """
    with rasterio.open(lulc_path) as src:
        # Transform school point to raster's native CRS (read dynamically, Bug D fix)
        to_raster = Transformer.from_crs(CRS_WGS84, src.crs, always_xy=True)
        rx, ry = to_raster.transform(school_point_wgs.x, school_point_wgs.y)

        # Buffer in raster CRS units (meters for projected CRS)
        bminx = rx - radius
        bminy = ry - radius
        bmaxx = rx + radius
        bmaxy = ry + radius

        try:
            window = src.window(bminx, bminy, bmaxx, bmaxy)
            data = src.read(1, window=window)
        except Exception:
            return 0.0

        if data.size == 0:
            return 0.0

        tree_pixels = np.sum(data == TREE_CLASS)
        valid_pixels = np.sum(data > 0)
        if valid_pixels == 0:
            return 0.0
        return float(tree_pixels / valid_pixels)


# ---------------------------------------------------------------------------
# 8. Calculate net pollution
# ---------------------------------------------------------------------------
def calculate_net_pollution(raw: float, canopy: float) -> float:
    """Apply tree canopy mitigation: P_net = P_raw * (1 - min(alpha*CC, max_mit))."""
    mitigation = min(ALPHA * canopy, MAX_MITIGATION)
    return raw * (1.0 - mitigation)


# ---------------------------------------------------------------------------
# 9. Run full school analysis
# ---------------------------------------------------------------------------
def run_school_analysis(
    schools: gpd.GeoDataFrame,
    road_points: gpd.GeoDataFrame,
    lulc_path: Path,
) -> pd.DataFrame:
    """
    Compute raw and mitigated pollution indices for all schools at both radii.
    """
    # Build KD-tree from road sub-segment points (UTM)
    coords = np.array([(p.x, p.y) for p in road_points.geometry])
    tree = cKDTree(coords)
    seg_weights = road_points["weight"].values

    # Project schools to UTM
    schools_utm = schools.to_crs(CRS_UTM17N)

    results = []
    for idx, (_, school) in enumerate(schools.iterrows()):
        name = school["school"]
        _progress(f"[{idx+1}/{len(schools)}] Analyzing {name}")

        school_utm = schools_utm.iloc[idx].geometry
        school_wgs = school.geometry

        row = {"school": name, "lat": school["lat"], "lon": school["lon"]}

        for radius in RADII:
            raw = calculate_raw_pollution(school_utm, coords, seg_weights, tree, radius)
            canopy = calculate_tree_canopy(school_wgs, lulc_path, radius)
            net = calculate_net_pollution(raw, canopy)

            row[f"raw_{radius}m"] = round(raw, 4)
            row[f"canopy_{radius}m"] = round(canopy, 4)
            row[f"net_{radius}m"] = round(net, 4)

        results.append(row)

    return pd.DataFrame(results)


# ---------------------------------------------------------------------------
# 10. Normalize and rank
# ---------------------------------------------------------------------------
def normalize_and_rank(df: pd.DataFrame) -> pd.DataFrame:
    """Normalize pollution scores to 0-100 and add rank columns."""
    for radius in RADII:
        raw_col = f"raw_{radius}m"
        net_col = f"net_{radius}m"
        raw_norm = f"raw_norm_{radius}m"
        net_norm = f"net_norm_{radius}m"

        max_raw = df[raw_col].max()
        max_net = df[net_col].max()

        df[raw_norm] = round(df[raw_col] / max_raw * 100, 1) if max_raw > 0 else 0
        df[net_norm] = round(df[net_col] / max_net * 100, 1) if max_net > 0 else 0

        # Rank: 1 = highest pollution
        df[f"rank_raw_{radius}m"] = df[raw_col].rank(ascending=False, method="min").astype(int)
        df[f"rank_net_{radius}m"] = df[net_col].rank(ascending=False, method="min").astype(int)

    return df


# ---------------------------------------------------------------------------
# 11. Generate county-wide pollution grid
# ---------------------------------------------------------------------------
def generate_county_grid(
    road_points: gpd.GeoDataFrame,
    roads_wgs84: gpd.GeoDataFrame,
    lulc_path: Path,
    resolution: int = DEFAULT_GRID_RESOLUTION,
) -> tuple:
    """
    Generate raster grids of raw and net pollution across the road network extent.

    Grid is built directly in WGS84 coordinates (Bug B fix). Each cell is
    transformed to UTM on-the-fly for KD-tree distance queries, avoiding
    reprojection distortion from mapping a UTM rectangle onto WGS84.

    Grid extent is derived from actual road data bounds (Bug C fix) instead
    of hardcoded ORANGE_COUNTY_BBOX.

    Returns: (raw_grid, net_grid, bounds_wgs84)
        bounds_wgs84 = (west, south, east, north) in WGS84
    """
    _progress(f"Generating pollution grid at {resolution}m resolution ...")

    # Derive grid extent from actual road data bounds + padding (Bug C fix)
    road_bounds = roads_wgs84.total_bounds  # (minx, miny, maxx, maxy) in WGS84
    padding = 0.01  # ~1 km in degrees
    grid_bbox_wgs = (
        road_bounds[0] - padding,
        road_bounds[1] - padding,
        road_bounds[2] + padding,
        road_bounds[3] + padding,
    )

    # Compute approximate grid dimensions by measuring bbox extent in UTM
    to_utm = Transformer.from_crs(CRS_WGS84, CRS_UTM17N, always_xy=True)
    sw_u = to_utm.transform(grid_bbox_wgs[0], grid_bbox_wgs[1])
    ne_u = to_utm.transform(grid_bbox_wgs[2], grid_bbox_wgs[3])
    width_m = ne_u[0] - sw_u[0]
    height_m = ne_u[1] - sw_u[1]

    nx = int(round(width_m / resolution))  # Bug E fix: round instead of int()
    ny = int(round(height_m / resolution))
    _progress(f"Grid size: {nx} x {ny} = {nx * ny:,} cells")

    # Build WGS84 grid cell centers (Bug B fix — grid lives in WGS84)
    dx = (grid_bbox_wgs[2] - grid_bbox_wgs[0]) / nx
    dy = (grid_bbox_wgs[3] - grid_bbox_wgs[1]) / ny
    xs_wgs = np.linspace(grid_bbox_wgs[0] + dx / 2, grid_bbox_wgs[2] - dx / 2, nx)
    ys_wgs = np.linspace(grid_bbox_wgs[3] - dy / 2, grid_bbox_wgs[1] + dy / 2, ny)  # N→S

    # Image overlay bounds = outer edges of grid (Bug E fix — exact match)
    bounds_wgs84 = (grid_bbox_wgs[0], grid_bbox_wgs[1], grid_bbox_wgs[2], grid_bbox_wgs[3])

    # Build KD-tree from road sub-segments (UTM)
    coords = np.array([(p.x, p.y) for p in road_points.geometry])
    tree = cKDTree(coords)
    seg_weights = road_points["weight"].values

    raw_grid = np.zeros((ny, nx), dtype=np.float32)
    search_radius = 1000.0  # only road sub-segments within 1000m contribute

    _progress("Computing raw pollution for each grid cell ...")
    report_interval = max(1, ny // 10)

    for j in range(ny):
        if j % report_interval == 0:
            _progress(f"  Grid row {j}/{ny} ({j / ny * 100:.0f}%)")
        # Transform entire row of WGS84 lons to UTM at this latitude
        row_lat = ys_wgs[j]
        ux_arr, uy_arr = to_utm.transform(xs_wgs, np.full(nx, row_lat))

        for i in range(nx):
            cx, cy = ux_arr[i], uy_arr[i]
            idx_list = tree.query_ball_point([cx, cy], r=search_radius)
            if len(idx_list) == 0:
                continue
            pts = coords[idx_list]
            dists = np.sqrt((pts[:, 0] - cx) ** 2 + (pts[:, 1] - cy) ** 2)
            ws = seg_weights[idx_list]
            raw_grid[j, i] = np.sum(ws * np.exp(-LAMBDA * dists))

    _progress("Computing tree canopy mitigation for grid ...")

    # Read LULC and compute tree fraction at grid resolution
    net_grid = raw_grid.copy()
    try:
        with rasterio.open(lulc_path) as src:
            # Read raster CRS dynamically (Bug D fix)
            to_raster = Transformer.from_crs(CRS_WGS84, src.crs, always_xy=True)
            half = resolution  # buffer around cell center in raster CRS units

            for j in range(ny):
                if j % report_interval == 0:
                    _progress(f"  Canopy row {j}/{ny} ({j / ny * 100:.0f}%)")
                row_lat = ys_wgs[j]
                # Batch-transform entire row to raster CRS
                rx_arr, ry_arr = to_raster.transform(xs_wgs, np.full(nx, row_lat))

                for i in range(nx):
                    if raw_grid[j, i] == 0:
                        continue
                    rx, ry = rx_arr[i], ry_arr[i]
                    try:
                        window = src.window(
                            rx - half, ry - half,
                            rx + half, ry + half,
                        )
                        data = src.read(1, window=window)
                        if data.size > 0:
                            valid = np.sum(data > 0)
                            if valid > 0:
                                cc = float(np.sum(data == TREE_CLASS)) / valid
                                mitigation = min(ALPHA * cc, MAX_MITIGATION)
                                net_grid[j, i] = raw_grid[j, i] * (1 - mitigation)
                    except Exception:
                        pass
    except Exception as e:
        _progress(f"Warning: Could not apply canopy mitigation to grid: {e}")
        _progress("Net grid will equal raw grid.")

    _progress("Grid complete.")
    return raw_grid, net_grid, bounds_wgs84


# ---------------------------------------------------------------------------
# 12. Save results CSV
# ---------------------------------------------------------------------------
def save_results_csv(df: pd.DataFrame):
    """Save per-school results to CSV."""
    cols = ["school", "lat", "lon"]
    for r in RADII:
        cols.extend([
            f"raw_{r}m", f"raw_norm_{r}m", f"rank_raw_{r}m",
            f"canopy_{r}m",
            f"net_{r}m", f"net_norm_{r}m", f"rank_net_{r}m",
        ])
    out = DATA_PROCESSED / "road_pollution_scores.csv"
    df[cols].to_csv(out, index=False)
    _progress(f"Saved {out}")


# ---------------------------------------------------------------------------
# 13. Generate analysis markdown
# ---------------------------------------------------------------------------
def generate_analysis_markdown(df: pd.DataFrame, roads: gpd.GeoDataFrame = None):
    """Write comprehensive analysis writeup to markdown file."""
    out = DATA_PROCESSED / "ROAD_POLLUTION.md"

    # Sort by net_500m rank for primary table
    df_sorted = df.sort_values("rank_net_500m")

    lines = []
    lines.append("# Road Pollution Exposure Analysis — CHCCS Elementary Schools")
    lines.append("")
    lines.append("**Analysis Date:** February 2026")
    lines.append("**Method:** Road-classification-weighted exponential decay model with tree canopy mitigation")
    lines.append("")
    lines.append("---")
    lines.append("")
    lines.append("## What This Index Measures (Plain Language)")
    lines.append("")
    lines.append("The TRAP Exposure Index answers a simple question: **how much traffic pollution")
    lines.append("is a school exposed to based on nearby roads?**")
    lines.append("")
    lines.append("1. **Every road segment within 500m of a school contributes pollution.** Bigger")
    lines.append("   roads contribute more — an interstate (weight 1.0) contributes 100x more than")
    lines.append("   a residential street (weight 0.01), because it carries roughly 100x more traffic.")
    lines.append("2. **Closer roads matter more than distant ones.** Pollution drops off exponentially")
    lines.append("   with distance. A road 100m away contributes about 3.5x more than the same road")
    lines.append("   at 500m. By ~500m, most of the pollution has dispersed to near-background levels.")
    lines.append("3. **The index sums up all contributions.** Every 50-meter chunk of every road within")
    lines.append("   the radius gets a score based on its size and distance, and they all get added")
    lines.append("   together. More big roads nearby = higher index.")
    lines.append("")
    lines.append("**What the numbers mean in practice:**")
    lines.append("")
    sorted_df = df.sort_values("raw_500m", ascending=False)
    highest = sorted_df.iloc[0]
    mid_high = sorted_df.iloc[1]
    mid_low = sorted_df.iloc[-2]
    lowest = sorted_df.iloc[-1]
    lines.append(f"- Schools near multiple major roads (e.g., {highest['school']} at {highest['raw_500m']:.1f}) score highest")
    lines.append(f"- Schools on busy corridors (e.g., {mid_high['school']} at {mid_high['raw_500m']:.1f}) also score high")
    lines.append(f"- Schools set back from major roads score much lower (e.g., {mid_low['school']} at {mid_low['raw_500m']:.1f})")
    lines.append(f"- Schools surrounded by neighborhood streets score lowest (e.g., {lowest['school']} at {lowest['raw_500m']:.1f})")
    lines.append("")
    ratio = highest['raw_500m'] / lowest['raw_500m'] if lowest['raw_500m'] > 0 else 0
    lines.append(f"The index is **comparative, not a health measurement**. A score of {highest['raw_500m']:.0f} doesn't mean")
    lines.append(f'"unhealthy air" — it means that school has roughly {ratio:.0f}x more traffic-generated pollution')
    lines.append("pressure than the lowest-scoring school. The actual air quality at any")
    lines.append("school depends on wind, terrain, buildings, and other factors this model doesn't capture.")
    lines.append("")
    lines.append("---")
    lines.append("")
    lines.append("## Methodology")
    lines.append("")
    lines.append("### Road Pollution Index")
    lines.append("")
    lines.append("For each road sub-segment *i* within the analysis radius of a school:")
    lines.append("")
    lines.append("```")
    lines.append("P_i = W(road_class_i) * exp(-0.003 * d_i)")
    lines.append("```")
    lines.append("")
    lines.append("Where:")
    lines.append("- `W` = weight based on road classification (proxy for traffic volume)")
    lines.append("- `d_i` = distance from sub-segment centroid to school (meters)")
    lines.append("- `0.003 m^-1` = composite decay rate for NOx/BC/UFP")
    lines.append("- Roads discretized into ~50m sub-segments to capture both distance AND length effects")
    lines.append("")
    lines.append("**Total raw index:** `P_raw = SUM(P_i)` for all sub-segments within radius")
    lines.append("")
    lines.append("**Methodological validation:** The composite decay rate λ = 0.003 m⁻¹ is validated by")
    lines.append("Boogaard et al. (2019), a meta-analysis of near-road pollutant concentration decay")
    lines.append("rates that found λ = 0.0026 for black carbon and λ = 0.0027 for NOx. Our composite")
    lines.append("value sits within the observed range for major TRAP pollutants. The use of road")
    lines.append("classification as an AADT proxy is standard practice in Land-Use Regression (LUR)")
    lines.append("epidemiological models when actual traffic count data is unavailable (Hoek et al., 2008).")
    lines.append("")
    lines.append("### Road Classification Weights (AADT Proxy)")
    lines.append("")
    lines.append("| Road Class | AADT Proxy | Weight |")
    lines.append("|------------|-----------|--------|")
    lines.append("| motorway | ~50,000 | 1.000 |")
    lines.append("| trunk | ~30,000 | 0.600 |")
    lines.append("| primary | ~15,000 | 0.300 |")
    lines.append("| secondary | ~7,500 | 0.150 |")
    lines.append("| tertiary | ~3,000 | 0.060 |")
    lines.append("| unclassified | ~1,000 | 0.020 |")
    lines.append("| residential | ~500 | 0.010 |")
    lines.append("| service (untagged) | ~250 | 0.005 |")
    lines.append("| service (alley) | ~150 | 0.003 |")
    lines.append("| service (driveway) | ~100 | 0.002 |")
    lines.append("| living_street | ~250 | 0.005 |")
    lines.append("")
    lines.append("**IMPORTANT:** These proxy weights are used as defaults. Where available,")
    lines.append("actual NCDOT AADT counts override these proxies (see AADT Data Integration below).")
    lines.append("")

    # --- AADT Data Integration section ---
    has_aadt = roads is not None and "weight_source" in roads.columns
    aadt_count = 0
    proxy_count = 0
    if has_aadt:
        aadt_count = int((roads["weight_source"] == "aadt").sum())
        proxy_count = int((roads["weight_source"] == "proxy").sum())

    lines.append("### AADT Data Integration")
    lines.append("")
    if has_aadt and aadt_count > 0:
        total = aadt_count + proxy_count
        lines.append(f"This analysis integrates **NCDOT Annual Average Daily Traffic (AADT)** data")
        lines.append(f"from {aadt_count + proxy_count:,} road segments:")
        lines.append("")
        lines.append(f"- **{aadt_count:,} segments ({aadt_count / total * 100:.1f}%)** use measured AADT")
        lines.append(f"  counts from NCDOT monitoring stations (snapped within {AADT_SNAP_DISTANCE_M}m)")
        lines.append(f"- **{proxy_count:,} segments ({proxy_count / total * 100:.1f}%)** use road-class")
        lines.append(f"  proxy weights (no nearby AADT station)")
        lines.append("")
        lines.append("**Data source:** NCDOT AADT Stations — Orange County, NC")
        lines.append("([ArcGIS Feature Service](https://services.arcgis.com/NuWFvHYDMVmmxMeM/ArcGIS/rest/services/NCDOT_AADT_Stations/FeatureServer/0))")
        lines.append("")
        lines.append("**Weight derivation:** `weight = AADT / 50,000` (clipped to [0.001, 2.0]),")
        lines.append("where 50,000 is the reference AADT for a motorway (weight = 1.0).")
        lines.append("")

        # Load AADT cache for per-school table if available
        try:
            aadt_gdf = gpd.read_file(AADT_CACHE)
            lines.append("**AADT values for major roads (top stations by traffic volume):**")
            lines.append("")
            lines.append("| Route | Location | AADT | Year | Weight |")
            lines.append("|-------|----------|------|------|--------|")
            top_aadt = aadt_gdf.nlargest(15, "aadt")
            for _, station in top_aadt.iterrows():
                w = np.clip(station["aadt"] / AADT_MOTORWAY_REFERENCE, 0.001, 2.0)
                lines.append(
                    f"| {station['route']} | {station['location_desc']} | "
                    f"{station['aadt']:,} | {station['aadt_year']} | {w:.4f} |"
                )
            lines.append("")
        except Exception:
            pass
    else:
        lines.append("NCDOT AADT data was not available for this analysis run.")
        lines.append("All road weights use road-class proxy values (see table above).")
        lines.append("")
        lines.append("To enable AADT integration, run without `--cache-only` to download")
        lines.append("NCDOT station data from the ArcGIS Feature Service.")
        lines.append("")

    lines.append("### Road Network Coverage")
    lines.append("")
    lines.append("Road data is downloaded from OpenStreetMap using `network_type='drive_service'`,")
    lines.append("which includes all drivable through-roads plus service roads (parking-lot access,")
    lines.append("alleys) but excludes parking aisles and private roads. The Orange County boundary")
    lines.append(f"is buffered by {max(RADII)} m (the maximum analysis radius) to ensure complete")
    lines.append("coverage for schools near the county border (e.g., Rashkis Elementary, ~60 m from")
    lines.append("the Orange–Durham county line).")
    lines.append("")
    lines.append("### Tree Canopy Mitigation")
    lines.append("")
    lines.append("```")
    lines.append("f_mitigation = 0.56 * canopy_cover  (capped at 80%)")
    lines.append("P_net = P_raw * (1 - f_mitigation)")
    lines.append("```")
    lines.append("")
    lines.append("- Canopy cover fraction from Impact Observatory 10m Land Use/Land Cover")
    lines.append("- Alpha = 0.56 derived from: 2.8% PM2.5 reduction per 5% canopy cover increase")
    lines.append("- Based on meta-analyses of urban vegetation air quality effects (Nowak et al., 2014)")
    lines.append("")
    lines.append("---")
    lines.append("")
    lines.append("## Results: 500m Radius")
    lines.append("")
    lines.append("| Rank | School | Raw Index | Canopy % | Mitigation % | Net Index | Net (Normalized) |")
    lines.append("|------|--------|-----------|----------|-------------|-----------|-----------------|")
    for _, row in df.sort_values("rank_net_500m").iterrows():
        mit = min(ALPHA * row["canopy_500m"], MAX_MITIGATION) * 100
        lines.append(
            f"| {row['rank_net_500m']} | {row['school']} | "
            f"{row['raw_500m']:.2f} | {row['canopy_500m']*100:.1f}% | "
            f"{mit:.1f}% | {row['net_500m']:.2f} | "
            f"{row['net_norm_500m']:.1f} |"
        )
    lines.append("")

    lines.append("## Results: 1000m Radius")
    lines.append("")
    lines.append("| Rank | School | Raw Index | Canopy % | Mitigation % | Net Index | Net (Normalized) |")
    lines.append("|------|--------|-----------|----------|-------------|-----------|-----------------|")
    for _, row in df.sort_values("rank_net_1000m").iterrows():
        mit = min(ALPHA * row["canopy_1000m"], MAX_MITIGATION) * 100
        lines.append(
            f"| {row['rank_net_1000m']} | {row['school']} | "
            f"{row['raw_1000m']:.2f} | {row['canopy_1000m']*100:.1f}% | "
            f"{mit:.1f}% | {row['net_1000m']:.2f} | "
            f"{row['net_norm_1000m']:.1f} |"
        )
    lines.append("")

    lines.append("---")
    lines.append("")
    lines.append("---")
    lines.append("")
    lines.append("## Health & Educational Implications")
    lines.append("")
    lines.append("A large and growing body of peer-reviewed research links traffic-related air pollution")
    lines.append("(TRAP) exposure near schools to adverse health, cognitive, and academic outcomes in")
    lines.append("children. The Health Effects Institute's comprehensive review of 353 reports (HEI SR23,")
    lines.append("2022) confirmed causal or likely-causal associations between TRAP and respiratory")
    lines.append("disease, cardiovascular effects, lung cancer, cognitive impairment, and preterm birth.")
    lines.append("Health Canada's 2025 systematic review meta-analyzed 64 studies and established a")
    lines.append("causal relationship between TRAP and all-cause mortality. The evidence below is")
    lines.append("organized by how closely each study's exposure metric matches our index (distance from")
    lines.append("roads) versus measured pollutant concentrations (which our index does not estimate).")
    lines.append("")
    lines.append("### Proximity-Based Health Evidence")
    lines.append("")
    lines.append("The following studies use distance from roads as their exposure metric — the same")
    lines.append("quantity our index captures. Yu et al. (2025), the most comprehensive proximity-based")
    lines.append("meta-analysis (55 studies, 373,320 participants), found that living within 200m of a")
    lines.append("major road is associated with asthma (OR 1.23, 95% CI: 1.15–1.31), wheezing (OR 1.21,")
    lines.append("1.12–1.30), and rhinitis (OR 1.22, 1.13–1.32). McConnell et al. (2006) found asthma")
    lines.append("OR 1.50 for children living <75m from a major road, with effects returning to")
    lines.append("background at 150–200m. Gauderman et al. (2007) showed significant lung function")
    lines.append("deficits (FEV₁ and MMEF) in children living <500m from a freeway — the same radius as")
    lines.append("our primary analysis. Freid et al. (2021) found that infants living <100m from a major")
    lines.append("road had a wheeze hazard ratio of 1.59 (1.08–2.33) and asthma OR of 1.51 (1.00–2.28).")
    lines.append("Nishimura et al. (2020) documented a dose-response gradient: each 100m increase in")
    lines.append("distance from a major road was associated with 29% fewer symptom days (OR 0.71,")
    lines.append("0.58–0.87), directly paralleling our index's continuous distance-decay function.")
    lines.append("")
    lines.append("### Dispersion Model Evidence")
    lines.append("")
    lines.append("CALINE4 dispersion models share mathematical structure with our formula — both weight")
    lines.append("exposure by traffic volume and distance from road. McConnell et al. (2010), using")
    lines.append("CALINE4 in the Children's Health Study (2,497 children), found that non-freeway local")
    lines.append("road pollution at home carried an asthma hazard ratio of 1.51 (1.25–1.81), and at")
    lines.append("school HR 1.45 (1.06–1.98). Combined home and school exposure yielded HR 1.61")
    lines.append("(1.29–2.00). Islam et al. (2019) confirmed elevated bronchitic symptom risk from")
    lines.append("non-freeway near-road air pollution: OR 1.18 (1.04–1.33) for all children, rising to")
    lines.append("OR 1.44 (1.17–1.78) for asthmatic children. These findings validate our inclusion of")
    lines.append("all OSM road classes (not just freeways) with traffic-volume-based weights.")
    lines.append("")
    lines.append("### Cognitive and Academic Effects")
    lines.append("")
    lines.append("Sunyer et al. (2015, BREATHE study) found that children at high-TRAP schools showed")
    lines.append("7.4% working memory growth over 12 months versus 11.5% at low-TRAP schools — a")
    lines.append("substantial gap measured by elemental carbon and NO₂ concentrations at school.")
    lines.append("Heissel et al. (2022) used a natural experiment (wind direction variation while holding")
    lines.append("distance constant) to establish a causal effect: schools downwind of highways >60% of")
    lines.append("the year showed −0.040 SD in test scores and increased behavioral incidents. Kweon et")
    lines.append("al. (2018) found that Michigan schools closer to highways had higher test failure rates")
    lines.append("and lower attendance after controlling for SES — using continuous distance in meters,")
    lines.append("the same metric as our index. Requia et al. (2021, 256 Brazilian schools) demonstrated")
    lines.append("distance decay in academic effects: the strongest impact (−0.011 pts per km of road)")
    lines.append("occurred within 250m, weakening to −0.002 at 1km, mirroring the exponential decay in")
    lines.append("our formula. Stenson et al. (2021) systematically reviewed 10 studies on TRAP and")
    lines.append("academic performance; 9 of 10 found a negative association.")
    lines.append("")
    lines.append("*For complete citations, evidence tier classification, and methodological notes, see*")
    lines.append("*`data/processed/TRAP_FULL_LITERATURE_REVIEW.md`.*")
    lines.append("")
    lines.append("---")
    lines.append("")
    lines.append("## Limitations")
    lines.append("")
    if has_aadt and aadt_count > 0:
        lines.append(f"1. **Partial AADT coverage.** Only {aadt_count:,} of {aadt_count + proxy_count:,}")
        lines.append(f"   road segments ({aadt_count / (aadt_count + proxy_count) * 100:.0f}%) use measured")
        lines.append("   NCDOT traffic counts. The remaining segments use road-class proxy weights.")
        lines.append("   AADT stations are concentrated on major/secondary roads; residential and service")
        lines.append("   roads still rely on proxy estimates.")
    else:
        lines.append("1. **Road weights are proxies**, not actual AADT traffic counts.")
        lines.append("   Real traffic volumes may differ significantly from class-based estimates.")
    lines.append("2. **The pollution index is relative/comparative**, not an absolute health risk")
    lines.append("   assessment. It should not be interpreted as pollutant concentrations.")
    lines.append("3. **Service road weights are approximate.** OSM `highway=service` covers a")
    lines.append("   broad category (parking-lot access, alleys, driveways). The weights assigned")
    lines.append("   (0.002–0.005) are low-confidence estimates. These roads individually contribute")
    lines.append("   little, but schools near commercial areas (e.g., Scroggs near Southern Village)")
    lines.append("   have many of them, producing a non-trivial cumulative effect.")
    lines.append("4. **Tree canopy mitigation factors** are from literature meta-analyses, not")
    lines.append("   Chapel Hill-specific measurements. Local conditions (species, density,")
    lines.append("   seasonality) may differ.")
    lines.append("5. **Wind patterns, terrain, and building effects** are not modeled. These")
    lines.append("   factors significantly influence actual pollutant dispersion.")
    lines.append("6. **Temporal variation** (rush hour, seasonal) is not captured.")
    lines.append("7. **Linear summation assumption.** The model sums pollution contributions from")
    lines.append("   all road segments (P = ΣP_i). This treats pollution as perfectly additive. In")
    lines.append("   practice, atmospheric chemistry is more complex, but for a comparative index")
    lines.append("   this is a reasonable first-order approximation.")
    lines.append("8. **CRITICAL: ESA WorldCover urban canopy limitation.** The ESA WorldCover 10m")
    lines.append("   land cover classifies each pixel into a single dominant class. In suburban")
    lines.append("   areas like Chapel Hill (which has ~55% city-wide tree canopy per American")
    lines.append("   Forests estimates), neighborhoods with scattered trees along streets and in")
    lines.append("   yards are classified as \"Built-up\" (class 50) rather than \"Tree cover\" (class 10).")
    lines.append("   This means **tree canopy mitigation is significantly underestimated for urban")
    lines.append("   and suburban schools** (most show 0% canopy within 500m) while being")
    lines.append("   accurately captured for schools near contiguous forest. A high-resolution")
    lines.append("   tree canopy cover dataset (e.g., USDA Forest Service Urban Tree Canopy,")
    lines.append("   LiDAR-derived canopy height) would substantially improve the mitigation")
    lines.append("   analysis. **The raw pollution index (without mitigation) is the more")
    lines.append("   reliable metric for comparing schools.**")
    lines.append("")

    lines.append("---")
    lines.append("")
    lines.append("## Sources")
    lines.append("")
    lines.append("### Methodology & Data")
    lines.append("- Karner, A. A., Eisinger, D. S., & Niemeier, D. A. (2010). Near-roadway air quality: ")
    lines.append("  Synthesizing the findings from real-world data. *Environ Sci Technol*, 44(14). DOI: 10.1021/es100008x")
    lines.append("- Health Effects Institute. (2010). Traffic-Related Air Pollution: A Critical Review ")
    lines.append("  of the Literature on Emissions, Exposure, and Health Effects. HEI Special Report 17.")
    lines.append("- Nowak, D. J., et al. (2014). Tree and forest effects on air quality and human health ")
    lines.append("  in the United States. *Environ Pollution*, 193.")
    lines.append("- Impact Observatory / Esri. (2023). 10m Annual Land Use Land Cover.")
    lines.append("- OpenStreetMap contributors. Road network data.")
    lines.append("- Boogaard, H., et al. (2019). Concentration decay rates for near-road air pollutants. ")
    lines.append("  *Int J Hyg Environ Health*, 222(7). [λ = 0.0026 BC, 0.0027 NOx]")
    lines.append("- Hoek, G., et al. (2008). Land-use regression models for intraurban air pollution. ")
    lines.append("  *Atmos Environ*, 42(33). [Road-class-as-AADT-proxy precedent]")
    lines.append("")
    lines.append("### Proximity-Based Health Evidence")
    lines.append("- Yu, M., et al. (2025). Residential proximity to major roads and respiratory disease risk. ")
    lines.append("  *Clin Rev Allergy Immunol*, 68, 5. DOI: 10.1007/s12016-024-09010-1")
    lines.append("- McConnell, R., et al. (2006). Traffic, susceptibility, and childhood asthma. ")
    lines.append("  *Environ Health Perspect*, 114(5), 766–772. PMID: 16675435")
    lines.append("- Gauderman, W. J., et al. (2007). Effect of exposure to traffic on lung development. ")
    lines.append("  *Lancet*, 369(9561), 571–577. PMID: 17258668")
    lines.append("- Freid, R. D., et al. (2021). Residential proximity to major roadways and asthma ")
    lines.append("  phenotypes in children. *Int J Environ Res Public Health*, 18(14), 7746.")
    lines.append("- Nishimura, K. K., et al. (2020). Early-life air pollution and asthma risk in ")
    lines.append("  minority children. *J Allergy Clin Immunol*, 131(3), 684–690. PMID: 32007569")
    lines.append("")
    lines.append("### Dispersion Model Evidence")
    lines.append("- McConnell, R., et al. (2010). Childhood incident asthma and traffic-related air ")
    lines.append("  pollution at home and school. *Environ Health Perspect*, 118(7), 1021–1026. PMID: 20064776")
    lines.append("- Islam, T., et al. (2019). Non-freeway near-road air pollution and bronchitic symptoms. ")
    lines.append("  *Am J Respir Crit Care Med*, 180(3), 215–222. PMID: 30092140")
    lines.append("")
    lines.append("### Cognitive & Academic Effects")
    lines.append("- Sunyer, J., et al. (2015). Traffic-related air pollution in schools and cognitive ")
    lines.append("  development. *PLoS Med*, 12(3), e1001792. PMID: 25734425")
    lines.append("- Heissel, J. A., Persico, C., & Simon, D. (2022). Does pollution drive achievement? ")
    lines.append("  *J Human Resources*, 57(3), 747–776. DOI: 10.3368/jhr.59.3.0521-11689R2")
    lines.append("- Kweon, B.-S., et al. (2018). Proximity of public schools to major highways and ")
    lines.append("  students' performance. *Environ Plan B*, 45(2), 312–329. DOI: 10.1177/2399808317714113")
    lines.append("- Requia, W. J., et al. (2021). Neighborhood traffic-related air pollution and academic ")
    lines.append("  performance in Brazil. *Environ Res*, 201, 111036. DOI: 10.1016/j.envres.2021.111036")
    lines.append("- Stenson, C., et al. (2021). Impact of traffic-related air pollution on child academic ")
    lines.append("  performance: systematic review. *Environ Int*, 155, 106696. DOI: 10.1016/j.envint.2021.106696")
    lines.append("")
    lines.append("### Closure/Busing Evidence")
    lines.append("- Austin, W., Heutel, G., & Kreisman, D. (2019). School bus emissions, student health ")
    lines.append("  and academic performance. *Econ Educ Rev*, 70, 109–126. DOI: 10.1016/j.econedurev.2019.03.003")
    lines.append("- Persico, C. L., & Venator, J. (2021). Effects of local industrial pollution on students ")
    lines.append("  and schools. *J Human Resources*, 56(2), 406–445. DOI: 10.3368/jhr.57.4.1119-10542R2")
    lines.append("- Detroit busing exposure study. PMC8715954.")
    lines.append("")
    lines.append("### Comprehensive Reviews & Regulatory")
    lines.append("- Health Effects Institute. (2022). Long-Term Exposure to Traffic-Related Air Pollution. ")
    lines.append("  HEI Special Report 23.")
    lines.append("- Health Canada. (2025). Human Health Risk Assessment for Traffic-Related Air Pollution.")
    lines.append("- U.S. EPA. (2011). School Siting Guidelines. EPA-100-K-11-004.")
    lines.append("- U.S. EPA. (2015). Best Practices for Reducing Near-Road Pollution Exposure at Schools.")
    lines.append("- WHO. (2013). REVIHAAP Technical Report.")
    lines.append("")
    lines.append("---")
    lines.append("")
    lines.append("*Analysis generated by src/road_pollution.py*")
    lines.append("")

    out.write_text("\n".join(lines), encoding="utf-8")
    _progress(f"Saved {out}")


# ---------------------------------------------------------------------------
# 14. Create pollution comparison chart
# ---------------------------------------------------------------------------
def create_pollution_chart(df: pd.DataFrame):
    """Vertical grouped bar chart: raw vs mitigated for all 11 schools, 500m radius."""
    df_sorted = df.sort_values("raw_500m", ascending=False)

    fig, ax = plt.subplots(figsize=(14, 7))

    schools = df_sorted["school"].tolist()
    short_names = [s.replace(" Elementary", "").replace(" Bilingue", "") for s in schools]

    x = np.arange(len(schools))
    width = 0.35

    raw_vals = df_sorted["raw_500m"].values
    net_vals = df_sorted["net_500m"].values

    raw_colors = "#7f8c8d"
    net_colors = "#2c3e50"

    bars_raw = ax.bar(x - width/2, raw_vals, width,
                      label="Raw Pollution Index", color=raw_colors, alpha=0.5)
    bars_net = ax.bar(x + width/2, net_vals, width,
                      label="Net (after tree canopy mitigation)", color=net_colors, alpha=0.9)

    # Add value labels above bars
    for bar, val in zip(bars_raw, raw_vals):
        ax.text(bar.get_x() + bar.get_width()/2, bar.get_height() + 0.3,
                f"{val:.1f}", ha="center", va="bottom", fontsize=7, color="#666")
    for bar, val in zip(bars_net, net_vals):
        ax.text(bar.get_x() + bar.get_width()/2, bar.get_height() + 0.3,
                f"{val:.1f}", ha="center", va="bottom", fontsize=7)


    ax.set_xticks(x)
    ax.set_xticklabels(short_names, fontsize=9, rotation=45, ha="right")
    ax.set_ylabel(
        r"TRAP Exposure Index  ($P = \Sigma\, W_i \cdot e^{-\lambda d_i}$,  dimensionless)",
        fontsize=11, fontweight="bold",
    )
    ax.set_title(
        "Road Pollution Exposure by School (500m Radius)\n"
        "Higher = greater traffic-related air pollution exposure",
        fontsize=14, fontweight="bold", pad=20,
    )
    ax.legend(loc="upper right", fontsize=10)
    ax.grid(True, axis="y", alpha=0.3)
    ax.set_axisbelow(True)

    plt.tight_layout()
    plt.subplots_adjust(bottom=0.22)
    path = ASSETS_CHARTS / "road_pollution_comparison.png"
    plt.savefig(path, dpi=150, bbox_inches="tight")
    plt.close()
    _progress(f"Saved {path}")


# ---------------------------------------------------------------------------
# 15a. Helpers for interactive TRAP hover/click
# ---------------------------------------------------------------------------
def _grid_to_js_data(grid: np.ndarray, bounds_wgs84: tuple) -> str:
    """Serialize pollution grid as base64 Float32Array for JavaScript lookup."""
    import base64

    gmax = float(np.percentile(grid[grid > 0], 99)) if np.any(grid > 0) else 1.0
    # Quantize to 2 decimal places to save space
    quantized = np.round(grid, 2).astype(np.float32)
    ny, nx = quantized.shape
    west, south, east, north = bounds_wgs84
    b64 = base64.b64encode(quantized.tobytes()).decode()

    return (
        f"var GRID_B64 = '{b64}';\n"
        f"var GRID_NX = {nx};\n"
        f"var GRID_NY = {ny};\n"
        f"var GRID_BOUNDS = [{west}, {south}, {east}, {north}];\n"
        f"var GRID_MAX = {gmax:.4f};\n"
    )


def _roads_to_js_data(roads_gdf: gpd.GeoDataFrame) -> str:
    """Serialize road segment centroids + metadata as JS arrays for click lookup.

    Also embeds simplified road geometries as GeoJSON for highlighting.
    """
    import json

    to_wgs = None
    if roads_gdf.crs and roads_gdf.crs != CRS_WGS84:
        roads_wgs = roads_gdf.to_crs(CRS_WGS84)
    else:
        roads_wgs = roads_gdf

    lats, lons, weights, classes, names = [], [], [], [], []
    geojson_features = []

    for idx, row in roads_wgs.iterrows():
        geom = row.geometry
        if geom is None or geom.is_empty:
            continue
        centroid = geom.centroid
        lats.append(round(centroid.y, 5))
        lons.append(round(centroid.x, 5))
        weights.append(float(row.get("weight", 0)))
        classes.append(str(row.get("highway", "")))
        name = row.get("name", "")
        if isinstance(name, list):
            name = name[0] if name else ""
        if pd.isna(name):
            name = ""
        names.append(str(name))

        # Simplified geometry for highlighting (max 5 coord pairs)
        try:
            simplified = geom.simplify(0.001, preserve_topology=True)
            if simplified.geom_type == "MultiLineString":
                coords = [list(line.coords) for line in simplified.geoms]
                coords = [[[round(c[0], 5), round(c[1], 5)] for c in seg] for seg in coords]
            else:
                coords = [[[round(c[0], 5), round(c[1], 5)] for c in simplified.coords]]
            geojson_features.append({
                "type": "Feature",
                "geometry": {
                    "type": "MultiLineString" if len(coords) > 1 else "LineString",
                    "coordinates": coords if len(coords) > 1 else coords[0],
                },
                "properties": {"i": len(lats) - 1},
            })
        except Exception:
            geojson_features.append(None)

    # Filter out None features
    geojson_features = [f for f in geojson_features if f is not None]

    geojson = json.dumps({
        "type": "FeatureCollection",
        "features": geojson_features,
    }, separators=(",", ":"))

    return (
        f"var ROAD_LATS = {json.dumps(lats)};\n"
        f"var ROAD_LONS = {json.dumps(lons)};\n"
        f"var ROAD_WEIGHTS = {json.dumps([round(w, 4) for w in weights])};\n"
        f"var ROAD_CLASSES = {json.dumps(classes)};\n"
        f"var ROAD_NAMES = {json.dumps(names)};\n"
        f"var ROAD_GEOJSON = {geojson};\n"
    )


_TRAP_INTERACTION_JS = """
<div id="trap-info" style="position:fixed; bottom:30px; left:10px; z-index:1000;
     background:white; padding:10px 14px; border-radius:5px;
     box-shadow:2px 2px 5px rgba(0,0,0,0.3); font-family:monospace; font-size:13px;
     max-height:300px; overflow-y:auto; min-width:220px;">
  <b>TRAP Index:</b> <span id="trap-val">&mdash;</span><br>
  <small>Hover for value &middot; Click for roads</small>
  <div id="trap-roads" style="display:none; margin-top:6px; border-top:1px solid #ccc; padding-top:6px;"></div>
</div>
<script>
(function() {
  // Decode base64 Float32Array grid
  var raw = atob(GRID_B64);
  var bytes = new Uint8Array(raw.length);
  for (var k = 0; k < raw.length; k++) bytes[k] = raw.charCodeAt(k);
  var GRID_DATA = new Float32Array(bytes.buffer);

  var highlightLayer = null;

  // Find the Leaflet map instance
  var mapEl = document.querySelector('.folium-map');
  if (!mapEl) return;
  var mapId = mapEl.id;
  var map = window[mapId] || null;
  // Fallback: search for L.Map instances
  if (!map) {
    for (var key in window) {
      if (window[key] instanceof L.Map) { map = window[key]; break; }
    }
  }
  if (!map) return;

  function gridLookup(lat, lng) {
    var west = GRID_BOUNDS[0], south = GRID_BOUNDS[1],
        east = GRID_BOUNDS[2], north = GRID_BOUNDS[3];
    if (lng < west || lng > east || lat < south || lat > north) return null;
    var i = Math.floor((lng - west) / (east - west) * GRID_NX);
    var j = Math.floor((north - lat) / (north - south) * GRID_NY);
    if (i < 0 || i >= GRID_NX || j < 0 || j >= GRID_NY) return null;
    return GRID_DATA[j * GRID_NX + i];
  }

  map.on('mousemove', function(e) {
    var val = gridLookup(e.latlng.lat, e.latlng.lng);
    var el = document.getElementById('trap-val');
    if (val !== null && val > 0.001) {
      el.textContent = val.toFixed(2);
    } else {
      el.textContent = '0.00';
    }
  });

  map.on('mouseout', function() {
    document.getElementById('trap-val').innerHTML = '&mdash;';
  });

  // Haversine distance in meters
  function haversine(lat1, lon1, lat2, lon2) {
    var R = 6371000;
    var dLat = (lat2 - lat1) * Math.PI / 180;
    var dLon = (lon2 - lon1) * Math.PI / 180;
    var a = Math.sin(dLat/2) * Math.sin(dLat/2) +
            Math.cos(lat1 * Math.PI / 180) * Math.cos(lat2 * Math.PI / 180) *
            Math.sin(dLon/2) * Math.sin(dLon/2);
    return R * 2 * Math.atan2(Math.sqrt(a), Math.sqrt(1 - a));
  }

  map.on('click', function(e) {
    var clat = e.latlng.lat, clng = e.latlng.lng;
    var val = gridLookup(clat, clng);
    if (val !== null && val > 0.001) {
      document.getElementById('trap-val').textContent = val.toFixed(2);
    }

    // Find contributing roads within 1000m
    var contributors = [];
    for (var k = 0; k < ROAD_LATS.length; k++) {
      var d = haversine(clat, clng, ROAD_LATS[k], ROAD_LONS[k]);
      if (d <= 1000) {
        var contribution = ROAD_WEIGHTS[k] * Math.exp(-0.003 * d);
        if (contribution > 0.0001) {
          contributors.push({
            idx: k,
            name: ROAD_NAMES[k] || '(unnamed)',
            cls: ROAD_CLASSES[k],
            dist: d,
            contrib: contribution
          });
        }
      }
    }
    contributors.sort(function(a, b) { return b.contrib - a.contrib; });
    var totalContrib = contributors.reduce(function(s, c) { return s + c.contrib; }, 0);

    var roadsDiv = document.getElementById('trap-roads');
    if (contributors.length === 0) {
      roadsDiv.style.display = 'block';
      roadsDiv.innerHTML = '<small>No contributing roads within 1000m</small>';
    } else {
      var top = contributors.slice(0, 8);
      var html = '<b>Contributing roads (1000m):</b><br>';
      for (var m = 0; m < top.length; m++) {
        var c = top[m];
        var pct = totalContrib > 0 ? (c.contrib / totalContrib * 100).toFixed(0) : '0';
        html += '<small>' + (m+1) + '. ' + c.name + ' <i>(' + c.cls + ')</i> &mdash; ' +
                Math.round(c.dist) + 'm &mdash; ' + c.contrib.toFixed(2) + ' (' + pct + '%)</small><br>';
      }
      if (contributors.length > 8) {
        html += '<small>... +' + (contributors.length - 8) + ' more</small>';
      }
      roadsDiv.style.display = 'block';
      roadsDiv.innerHTML = html;
    }

    // Highlight contributing roads on map
    if (highlightLayer) { map.removeLayer(highlightLayer); highlightLayer = null; }
    if (contributors.length > 0 && typeof ROAD_GEOJSON !== 'undefined') {
      var topIndices = new Set(contributors.slice(0, 8).map(function(c) { return c.idx; }));
      var features = ROAD_GEOJSON.features.filter(function(f) {
        return f && f.properties && topIndices.has(f.properties.i);
      });
      if (features.length > 0) {
        highlightLayer = L.geoJSON({type: 'FeatureCollection', features: features}, {
          style: { color: '#ff00ff', weight: 4, opacity: 0.8 }
        }).addTo(map);
      }
    }
  });
})();
</script>
"""


# ---------------------------------------------------------------------------
# 15c. Create county-wide folium maps
# ---------------------------------------------------------------------------
def _make_county_map(
    grid: np.ndarray,
    bounds_wgs84: tuple,
    df: pd.DataFrame,
    title: str,
    score_col: str,
    rank_col: str,
    filename: str,
    radius: int = 500,
    roads_gdf: gpd.GeoDataFrame = None,
):
    """Create a folium map with a pollution raster overlay and school markers."""
    import branca.colormap as cm

    m = folium.Map(location=CHAPEL_HILL_CENTER, zoom_start=11, tiles="cartodbpositron")

    # Normalize grid for image overlay
    gmax = np.percentile(grid[grid > 0], 99) if np.any(grid > 0) else 1
    normalized = np.clip(grid / gmax, 0, 1)

    # Create RGBA image: green(low) -> yellow -> red(high)
    ny, nx = grid.shape
    rgba = np.zeros((ny, nx, 4), dtype=np.uint8)

    for j in range(ny):
        for i in range(nx):
            val = normalized[j, i]
            if val < 0.001:
                rgba[j, i] = [0, 0, 0, 0]  # transparent
            else:
                # Green -> Yellow -> Red
                if val < 0.5:
                    r = int(255 * (val / 0.5))
                    g = 200
                else:
                    r = 255
                    g = int(200 * (1 - (val - 0.5) / 0.5))
                b = 0
                a = int(120 + 80 * val)  # semi-transparent
                rgba[j, i] = [r, g, b, a]

    # Save as temporary PNG for overlay
    import io
    from PIL import Image

    img = Image.fromarray(rgba, "RGBA")
    buf = io.BytesIO()
    img.save(buf, format="PNG")
    buf.seek(0)
    import base64
    img_b64 = base64.b64encode(buf.read()).decode()
    img_url = f"data:image/png;base64,{img_b64}"

    # bounds_wgs84 = (west, south, east, north)
    west, south, east, north = bounds_wgs84
    folium.raster_layers.ImageOverlay(
        image=img_url,
        bounds=[[south, west], [north, east]],
        opacity=0.7,
        name="Pollution Heatmap",
    ).add_to(m)

    # Add school markers (color-coded CircleMarker by TRAP score)
    schools_group = folium.FeatureGroup(name="Schools", show=True)
    norm_col_for_color = f"raw_norm_{radius}m"
    for _, row in df.iterrows():
        # Build column names explicitly from radius (Bug F fix)
        prefix = score_col.rsplit("_", 1)[0]  # "raw" or "net"
        norm_col = f"{prefix}_norm_{radius}m"
        canopy_col = f"canopy_{radius}m"

        score_for_color = row.get(norm_col_for_color, 50)
        color_hex = _score_to_color(score_for_color)

        popup_html = f"""
        <b>{row['school']}</b><br>
        <hr style="margin:4px 0;">
        <b>Pollution Score:</b> {row[score_col]:.2f}<br>
        <b>Normalized:</b> {row.get(norm_col, 'N/A')}<br>
        <b>Rank:</b> #{int(row[rank_col])} of {len(df)}<br>
        <b>Tree Canopy:</b> {row.get(canopy_col, 0)*100:.1f}%
        """

        folium.CircleMarker(
            location=[row["lat"], row["lon"]],
            radius=10,
            color="#333333",
            weight=2,
            fillColor=color_hex,
            fillOpacity=1.0,
            popup=folium.Popup(popup_html, max_width=250),
            tooltip=row["school"],
        ).add_to(schools_group)

    schools_group.add_to(m)

    # Add layer control
    folium.LayerControl().add_to(m)

    # Add colormap legend
    colormap = cm.LinearColormap(
        colors=["#00c800", "#ffff00", "#ff0000"],
        vmin=0,
        vmax=round(gmax, 1),
        caption="Road Pollution Index",
    )
    colormap.add_to(m)

    # Title
    title_html = f"""
    <div style="position: fixed; top: 10px; left: 50%; transform: translateX(-50%);
                z-index: 1000; background-color: white; padding: 10px 20px;
                border-radius: 5px; box-shadow: 2px 2px 5px rgba(0,0,0,0.3);">
        <h3 style="margin: 0;">{title}</h3>
        <p style="margin: 5px 0 0 0; font-size: 12px; color: #666;">
            Road-classification-weighted pollution model (proxy AADT, NOT absolute health risk)
        </p>
    </div>
    """
    m.get_root().html.add_child(folium.Element(title_html))

    # Inject interactive TRAP hover/click JS if roads data available
    if roads_gdf is not None:
        _progress("Embedding grid + road data for interactive hover/click ...")
        grid_js = _grid_to_js_data(grid, bounds_wgs84)
        roads_js = _roads_to_js_data(roads_gdf)
        js_block = f"<script>\n{grid_js}\n{roads_js}\n</script>\n{_TRAP_INTERACTION_JS}"
        m.get_root().html.add_child(folium.Element(js_block))

    path = ASSETS_MAPS / filename
    m.save(str(path))
    _progress(f"Saved {path}")


def create_county_maps(
    raw_grid: np.ndarray,
    net_grid: np.ndarray,
    bounds_wgs84: tuple,
    df: pd.DataFrame,
    roads_gdf: gpd.GeoDataFrame = None,
):
    """Create both raw and net pollution maps."""
    _make_county_map(
        raw_grid, bounds_wgs84, df,
        title="Road Pollution Exposure — Raw (No Mitigation)",
        score_col="raw_500m",
        rank_col="rank_raw_500m",
        filename="road_pollution_raw_map.html",
        radius=500,
        roads_gdf=roads_gdf,
    )
    _make_county_map(
        net_grid, bounds_wgs84, df,
        title="Road Pollution Exposure — After Tree Canopy Mitigation",
        score_col="net_500m",
        rank_col="rank_net_500m",
        filename="road_pollution_net_map.html",
        radius=500,
        roads_gdf=roads_gdf,
    )


# ---------------------------------------------------------------------------
# 15b. Tree canopy standalone map
# ---------------------------------------------------------------------------
def create_tree_canopy_map(lulc_path: Path, schools: gpd.GeoDataFrame):
    """Create standalone tree canopy map showing ESA WorldCover tree cover."""
    import base64
    import io
    from PIL import Image

    _progress("Creating tree canopy map ...")
    m = folium.Map(location=CHAPEL_HILL_CENTER, zoom_start=12, tiles="cartodbpositron")

    try:
        with rasterio.open(lulc_path) as src:
            to_wgs = Transformer.from_crs(src.crs, CRS_WGS84, always_xy=True)
            left, bottom, right, top = src.bounds
            w_lon, s_lat = to_wgs.transform(left, bottom)
            e_lon, n_lat = to_wgs.transform(right, top)

            # Downsample for map overlay (10m resolution not needed)
            step = max(1, max(src.height, src.width) // 2000)
            data = src.read(1, out_shape=(src.height // step, src.width // step))
            h, w = data.shape
            rgba = np.zeros((h, w, 4), dtype=np.uint8)
            tree_mask = data == TREE_CLASS
            rgba[tree_mask] = [34, 139, 34, 160]  # forest green, semi-transparent

            img = Image.fromarray(rgba, "RGBA")
            buf = io.BytesIO()
            img.save(buf, format="PNG")
            buf.seek(0)
            img_b64 = base64.b64encode(buf.read()).decode()
            img_url = f"data:image/png;base64,{img_b64}"

            folium.raster_layers.ImageOverlay(
                image=img_url,
                bounds=[[s_lat, w_lon], [n_lat, e_lon]],
                opacity=0.6,
                name="Tree Canopy (ESA WorldCover)",
            ).add_to(m)
    except Exception as e:
        _progress(f"Warning: Could not generate canopy overlay: {e}")

    _add_school_markers(m, schools)
    folium.LayerControl().add_to(m)

    title_html = """
    <div style="position: fixed; top: 10px; left: 50%; transform: translateX(-50%);
                z-index: 1000; background-color: white; padding: 10px 20px;
                border-radius: 5px; box-shadow: 2px 2px 5px rgba(0,0,0,0.3);">
        <h3 style="margin: 0;">Tree Canopy Cover — ESA WorldCover 2021</h3>
        <p style="margin: 5px 0 0 0; font-size: 12px; color: #666;">
            ESA WorldCover V2 10m Land Cover (2021)
        </p>
    </div>
    """
    m.get_root().html.add_child(folium.Element(title_html))

    path = ASSETS_MAPS / "tree_canopy_map.html"
    m.save(str(path))
    _progress(f"Saved {path}")


# ---------------------------------------------------------------------------
# 15c. Combined toggle map with all layers
# ---------------------------------------------------------------------------
def _grid_to_image_url(grid: np.ndarray) -> str:
    """Convert a pollution grid to a base64 PNG data URL (green-yellow-red)."""
    import base64
    import io
    from PIL import Image

    gmax = np.percentile(grid[grid > 0], 99) if np.any(grid > 0) else 1
    normalized = np.clip(grid / gmax, 0, 1)

    ny, nx = grid.shape
    rgba = np.zeros((ny, nx, 4), dtype=np.uint8)
    for j in range(ny):
        for i in range(nx):
            val = normalized[j, i]
            if val < 0.001:
                rgba[j, i] = [0, 0, 0, 0]
            else:
                if val < 0.5:
                    r = int(255 * (val / 0.5))
                    g = 200
                else:
                    r = 255
                    g = int(200 * (1 - (val - 0.5) / 0.5))
                b = 0
                a = int(120 + 80 * val)
                rgba[j, i] = [r, g, b, a]

    img = Image.fromarray(rgba, "RGBA")
    buf = io.BytesIO()
    img.save(buf, format="PNG")
    buf.seek(0)
    img_b64 = base64.b64encode(buf.read()).decode()
    return f"data:image/png;base64,{img_b64}"


def create_combined_map(
    raw_grid: np.ndarray,
    net_grid: np.ndarray,
    lulc_path: Path,
    bounds_wgs84: tuple,
    df: pd.DataFrame,
    roads_gdf: gpd.GeoDataFrame = None,
):
    """Create a single map with all analysis layers as toggleable overlays."""
    import base64
    import io
    from PIL import Image

    _progress("Creating combined toggle map ...")
    m = folium.Map(location=CHAPEL_HILL_CENTER, zoom_start=12, tiles="cartodbpositron")

    west, south, east, north = bounds_wgs84
    overlay_bounds = [[south, west], [north, east]]

    # Layer 1: Raw pollution
    raw_group = folium.FeatureGroup(name="Raw Pollution", show=True)
    raw_url = _grid_to_image_url(raw_grid)
    folium.raster_layers.ImageOverlay(
        image=raw_url, bounds=overlay_bounds, opacity=0.7,
    ).add_to(raw_group)
    raw_group.add_to(m)

    # Layer 2: Tree canopy
    tree_group = folium.FeatureGroup(name="Tree Canopy", show=False)
    try:
        with rasterio.open(lulc_path) as src:
            to_wgs = Transformer.from_crs(src.crs, CRS_WGS84, always_xy=True)
            left, bottom, right, top = src.bounds
            w_lon, s_lat = to_wgs.transform(left, bottom)
            e_lon, n_lat = to_wgs.transform(right, top)

            # Downsample for map overlay (10m resolution not needed)
            step = max(1, max(src.height, src.width) // 2000)
            data = src.read(1, out_shape=(src.height // step, src.width // step))
            h, w = data.shape
            rgba = np.zeros((h, w, 4), dtype=np.uint8)
            tree_mask = data == TREE_CLASS
            rgba[tree_mask] = [34, 139, 34, 160]

            img = Image.fromarray(rgba, "RGBA")
            buf = io.BytesIO()
            img.save(buf, format="PNG")
            buf.seek(0)
            img_b64 = base64.b64encode(buf.read()).decode()
            tree_url = f"data:image/png;base64,{img_b64}"

            folium.raster_layers.ImageOverlay(
                image=tree_url,
                bounds=[[s_lat, w_lon], [n_lat, e_lon]],
                opacity=0.6,
            ).add_to(tree_group)
    except Exception as e:
        _progress(f"Warning: Could not add canopy layer to combined map: {e}")
    tree_group.add_to(m)

    # Layer 3: Net pollution
    net_group = folium.FeatureGroup(name="Net Pollution", show=False)
    net_url = _grid_to_image_url(net_grid)
    folium.raster_layers.ImageOverlay(
        image=net_url, bounds=overlay_bounds, opacity=0.7,
    ).add_to(net_group)
    net_group.add_to(m)

    # Layer 4: School markers (color-coded CircleMarker by TRAP score)
    schools_group = folium.FeatureGroup(name="Schools", show=True)
    for _, row in df.iterrows():
        score_for_color = row.get("raw_norm_500m", 50)
        color_hex = _score_to_color(score_for_color)

        popup_html = f"""
        <b>{row['school']}</b><br>
        <hr style="margin:4px 0;">
        <b>Raw (500m):</b> {row['raw_500m']:.2f} (rank #{int(row['rank_raw_500m'])})<br>
        <b>Net (500m):</b> {row['net_500m']:.2f} (rank #{int(row['rank_net_500m'])})<br>
        <b>Canopy:</b> {row.get('canopy_500m', 0)*100:.1f}%
        """
        folium.CircleMarker(
            location=[row["lat"], row["lon"]],
            radius=10,
            color="#333333",
            weight=2,
            fillColor=color_hex,
            fillOpacity=1.0,
            popup=folium.Popup(popup_html, max_width=250),
            tooltip=row["school"],
        ).add_to(schools_group)
    schools_group.add_to(m)

    # Layer control (expanded)
    folium.LayerControl(collapsed=False).add_to(m)

    # Title
    title_html = """
    <div style="position: fixed; top: 10px; left: 50%; transform: translateX(-50%);
                z-index: 1000; background-color: white; padding: 10px 20px;
                border-radius: 5px; box-shadow: 2px 2px 5px rgba(0,0,0,0.3);">
        <h3 style="margin: 0;">Road Pollution Analysis — Combined Layers</h3>
        <p style="margin: 5px 0 0 0; font-size: 12px; color: #666;">
            Toggle layers on/off using the control panel at right
        </p>
    </div>
    """
    m.get_root().html.add_child(folium.Element(title_html))

    # Inject interactive TRAP hover/click JS (uses raw grid as primary layer)
    if roads_gdf is not None:
        _progress("Embedding grid + road data for interactive hover/click ...")
        grid_js = _grid_to_js_data(raw_grid, bounds_wgs84)
        roads_js = _roads_to_js_data(roads_gdf)
        js_block = f"<script>\n{grid_js}\n{roads_js}\n</script>\n{_TRAP_INTERACTION_JS}"
        m.get_root().html.add_child(folium.Element(js_block))

    path = ASSETS_MAPS / "road_pollution_combined_map.html"
    m.save(str(path))
    _progress(f"Saved {path}")


# ---------------------------------------------------------------------------
# 16. Debug maps
# ---------------------------------------------------------------------------
def _add_school_markers(m, schools, df=None):
    """Add school markers to a folium map (for debug maps).

    If *df* is provided and contains ``raw_norm_500m``, markers are color-coded
    by TRAP score.  Otherwise a uniform blue color is used for all schools.
    """
    score_lookup = {}
    if df is not None and "raw_norm_500m" in df.columns:
        for _, r in df.iterrows():
            score_lookup[r["school"]] = r["raw_norm_500m"]

    for _, row in schools.iterrows():
        name = row["school"]
        if score_lookup:
            color_hex = _score_to_color(score_lookup.get(name, 50))
        else:
            color_hex = "#3388ff"

        folium.CircleMarker(
            location=[row["lat"], row["lon"]],
            radius=10,
            color="#333333",
            weight=2,
            fillColor=color_hex,
            fillOpacity=1.0,
            popup=name,
            tooltip=name,
        ).add_to(m)


def _add_debug_title(m, title):
    """Add a title overlay to a folium debug map."""
    html = f"""
    <div style="position: fixed; top: 10px; left: 50%; transform: translateX(-50%);
                z-index: 1000; background-color: white; padding: 8px 16px;
                border-radius: 5px; box-shadow: 2px 2px 5px rgba(0,0,0,0.3);
                font-family: sans-serif;">
        <h4 style="margin: 0;">{title}</h4>
    </div>
    """
    m.get_root().html.add_child(folium.Element(html))


def generate_debug_maps(
    schools,
    roads,
    road_points,
    lulc_path,
    df,
    raw_grid=None,
    net_grid=None,
    bounds=None,
):
    """
    Generate intermediate debug maps for visual verification.
    Each map is saved to assets/maps/debug/.
    """
    print("\n  Generating debug maps ...")

    # --- debug_01: Roads colored by highway class ---
    _progress("debug_01: Roads by class ...")
    m1 = folium.Map(location=CHAPEL_HILL_CENTER, zoom_start=12, tiles="cartodbpositron")
    for _, row in roads.iterrows():
        hw = row["highway"]
        color = ROAD_COLORS.get(hw, "#333333")
        weight = ROAD_LINE_WIDTHS.get(hw, 1)
        geom = row.geometry
        if geom.geom_type == "MultiLineString":
            for line in geom.geoms:
                coords_ll = [(c[1], c[0]) for c in line.coords]
                folium.PolyLine(
                    coords_ll, color=color, weight=weight, opacity=0.8,
                    popup=f"{hw}: w={row['weight']}",
                ).add_to(m1)
        else:
            coords_ll = [(c[1], c[0]) for c in geom.coords]
            folium.PolyLine(
                coords_ll, color=color, weight=weight, opacity=0.8,
                popup=f"{hw}: w={row['weight']}",
            ).add_to(m1)
    _add_school_markers(m1, schools)
    # Legend
    legend_items = [
        ("motorway", "#e41a1c"), ("trunk", "#ff7f00"), ("primary", "#377eb8"),
        ("secondary", "#4daf4a"), ("tertiary", "#984ea3"),
        ("unclassified", "#b07d2b"), ("residential", "#999999"),
        ("service", "#dddddd"),
    ]
    legend_html = '<div style="position:fixed;bottom:30px;left:10px;z-index:1000;background:white;padding:10px;border-radius:5px;box-shadow:2px 2px 5px rgba(0,0,0,0.3);font-size:12px;">'
    legend_html += "<b>Road Class</b><br>"
    for name, color in legend_items:
        legend_html += f'<span style="color:{color};">&#9644;</span> {name}<br>'
    legend_html += "</div>"
    m1.get_root().html.add_child(folium.Element(legend_html))
    _add_debug_title(m1, "Debug 01: Roads by Highway Class")
    m1.save(str(ASSETS_MAPS_DEBUG / "debug_01_roads_by_class.html"))

    # --- debug_02: Discretized road points (sample) ---
    _progress("debug_02: Road points (sampled) ...")
    m2 = folium.Map(location=CHAPEL_HILL_CENTER, zoom_start=12, tiles="cartodbpositron")
    rp_wgs = road_points.to_crs(CRS_WGS84)
    # Sample to keep map responsive
    sample_n = min(3000, len(rp_wgs))
    rp_sample = rp_wgs.sample(n=sample_n, random_state=42)
    for _, row in rp_sample.iterrows():
        folium.CircleMarker(
            location=[row.geometry.y, row.geometry.x],
            radius=2,
            color="#e41a1c",
            fill=True,
            fill_opacity=0.6,
            weight=0,
        ).add_to(m2)
    _add_school_markers(m2, schools)
    _add_debug_title(m2, f"Debug 02: Discretized Road Points (sample {sample_n}/{len(rp_wgs)})")
    m2.save(str(ASSETS_MAPS_DEBUG / "debug_02_road_points.html"))

    # --- debug_03: School buffers ---
    _progress("debug_03: School buffers ...")
    m3 = folium.Map(location=CHAPEL_HILL_CENTER, zoom_start=12, tiles="cartodbpositron")
    for _, row in schools.iterrows():
        for radius in RADII:
            folium.Circle(
                location=[row["lat"], row["lon"]],
                radius=radius,
                color="blue",
                fill=False,
                weight=2 if radius == 500 else 1,
                opacity=0.7,
                dash_array="5" if radius == 1000 else None,
                popup=f"{row['school']} — {radius}m buffer",
            ).add_to(m3)
    _add_school_markers(m3, schools)
    _add_debug_title(m3, "Debug 03: School Locations + 500m/1000m Buffers")
    m3.save(str(ASSETS_MAPS_DEBUG / "debug_03_school_buffers.html"))

    # --- debug_04: School raw scores as graduated circles ---
    _progress("debug_04: School raw scores ...")
    m4 = folium.Map(location=CHAPEL_HILL_CENTER, zoom_start=12, tiles="cartodbpositron")
    max_raw = df["raw_500m"].max() if df["raw_500m"].max() > 0 else 1
    for _, row in df.iterrows():
        # Scale circle radius: 5px min, 40px max
        scaled = 5 + 35 * (row["raw_500m"] / max_raw)
        folium.CircleMarker(
            location=[row["lat"], row["lon"]],
            radius=scaled,
            color="#2c3e50",
            fill=True,
            fill_color="#3498db",
            fill_opacity=0.5,
            weight=2,
            popup=(
                f"<b>{row['school']}</b><br>"
                f"Raw 500m: {row['raw_500m']:.2f}<br>"
                f"Rank: #{int(row['rank_raw_500m'])}"
            ),
        ).add_to(m4)
    _add_debug_title(m4, "Debug 04: Raw Pollution Scores (500m, circle size = score)")
    m4.save(str(ASSETS_MAPS_DEBUG / "debug_04_school_raw_scores.html"))

    # --- debug_05: Tree canopy overlay ---
    _progress("debug_05: Tree canopy ...")
    m5 = folium.Map(location=CHAPEL_HILL_CENTER, zoom_start=12, tiles="cartodbpositron")
    try:
        import base64
        import io
        from PIL import Image

        with rasterio.open(lulc_path) as src:
            from pyproj import Transformer as T
            to_wgs = T.from_crs(src.crs, CRS_WGS84, always_xy=True)
            # Get raster bounds in WGS84
            left, bottom, right, top = src.bounds
            w_lon, s_lat = to_wgs.transform(left, bottom)
            e_lon, n_lat = to_wgs.transform(right, top)

            # Downsample for map overlay (10m resolution not needed)
            step = max(1, max(src.height, src.width) // 2000)
            data = src.read(1, out_shape=(src.height // step, src.width // step))
            h, w = data.shape
            rgba = np.zeros((h, w, 4), dtype=np.uint8)
            tree_mask = data == TREE_CLASS
            rgba[tree_mask] = [34, 139, 34, 160]  # forest green, semi-transparent

            img = Image.fromarray(rgba, "RGBA")
            buf = io.BytesIO()
            img.save(buf, format="PNG")
            buf.seek(0)
            img_b64 = base64.b64encode(buf.read()).decode()
            img_url = f"data:image/png;base64,{img_b64}"

            folium.raster_layers.ImageOverlay(
                image=img_url,
                bounds=[[s_lat, w_lon], [n_lat, e_lon]],
                opacity=0.6,
                name="Tree Canopy (ESA WorldCover)",
            ).add_to(m5)
    except Exception as e:
        _progress(f"  Warning: Could not generate canopy overlay: {e}")
    _add_school_markers(m5, schools)
    folium.LayerControl().add_to(m5)
    _add_debug_title(m5, "Debug 05: Tree Canopy (ESA WorldCover 2021)")
    m5.save(str(ASSETS_MAPS_DEBUG / "debug_05_tree_canopy.html"))

    # --- debug_06 & debug_07: Grid overlays (reuse _make_county_map logic) ---
    if raw_grid is not None and bounds is not None:
        _progress("debug_06: Grid raw pollution ...")
        _make_county_map(
            raw_grid, bounds, df,
            title="Debug 06: Raw Pollution Grid",
            score_col="raw_500m",
            rank_col="rank_raw_500m",
            filename=str(Path("debug") / "debug_06_grid_raw.html"),
            radius=500,
        )
        _progress("debug_07: Grid net pollution ...")
        _make_county_map(
            net_grid, bounds, df,
            title="Debug 07: Net Pollution Grid (after canopy mitigation)",
            score_col="net_500m",
            rank_col="rank_net_500m",
            filename=str(Path("debug") / "debug_07_grid_net.html"),
            radius=500,
        )
    else:
        _progress("  Skipping debug_06/07 (no grid data available)")

    _progress("All debug maps saved to assets/maps/debug/")


# ---------------------------------------------------------------------------
# Road network diagnostic
# ---------------------------------------------------------------------------
def diagnose_road_network(schools: gpd.GeoDataFrame, roads: gpd.GeoDataFrame):
    """
    Compare drive_service network against all-highway network near each school.

    Downloads a comparison network with custom_filter='["highway"]' (all highway
    tags) within a 1200m buffer of each school, then reports:
    - Segment counts and total length by highway class (drive_service vs all)
    - Excluded road types within 500m and 1000m of each school
    - Estimated score impact of excluded roads

    Writes results to data/processed/road_network_diagnostic.md.
    Only runs when --diagnose-roads flag is passed.
    """
    import osmnx as ox
    from datetime import datetime

    _progress("Starting road network diagnostic ...")

    schools_utm = schools.to_crs(CRS_UTM17N)
    roads_utm = roads.to_crs(CRS_UTM17N)

    lines = []
    lines.append("# Road Network Diagnostic Report")
    lines.append("")
    lines.append(f"**Generated:** {datetime.now().strftime('%Y-%m-%d %H:%M')}")
    lines.append(f"**Method:** Compare `drive_service` (analysis network) vs all `highway` tags")
    lines.append("")
    lines.append("This diagnostic downloads an unrestricted road network (all OSM `highway` tags)")
    lines.append("within 1200m of each school and compares it against the `drive_service` network")
    lines.append("used in the TRAP analysis. The goal is to quantify what road types are excluded")
    lines.append("and whether they could meaningfully affect pollution scores.")
    lines.append("")
    lines.append("---")
    lines.append("")

    # Overall summary of the drive_service network
    lines.append("## Drive-Service Network Summary")
    lines.append("")
    if "highway" in roads.columns:
        type_counts = roads["highway"].value_counts()
        lines.append("| Highway Type | Segments |")
        lines.append("|-------------|----------|")
        for hw_type, count in type_counts.items():
            lines.append(f"| {hw_type} | {count:,} |")
    lines.append("")
    lines.append(f"**Total segments in analysis:** {len(roads):,}")
    lines.append("")
    lines.append("---")
    lines.append("")

    # Per-school diagnostic
    lines.append("## Per-School Comparison")
    lines.append("")

    for idx, (_, school) in enumerate(schools.iterrows()):
        name = school["school"]
        _progress(f"  [{idx+1}/{len(schools)}] Diagnosing {name} ...")

        school_wgs = school.geometry
        school_utm_pt = schools_utm.iloc[idx].geometry

        # Download all-highway network within 1200m
        try:
            G_all = ox.graph_from_point(
                (school_wgs.y, school_wgs.x),
                dist=1200,
                custom_filter='["highway"]',
                simplify=True,
            )
            all_edges = ox.graph_to_gdfs(G_all, nodes=False, edges=True)

            # Flatten highway column
            def _first(val):
                return val[0] if isinstance(val, list) else val

            all_edges["highway"] = all_edges["highway"].apply(_first)
            all_edges_utm = all_edges.to_crs(CRS_UTM17N)
        except Exception as e:
            lines.append(f"### {name}")
            lines.append("")
            lines.append(f"*Could not download comparison network: {e}*")
            lines.append("")
            continue

        lines.append(f"### {name}")
        lines.append("")

        for radius in [500, 1000]:
            # Filter both networks to this radius from school
            from shapely.geometry import Point as ShapelyPoint
            buffer = school_utm_pt.buffer(radius)

            # drive_service roads within radius
            ds_near = roads_utm[roads_utm.geometry.intersects(buffer)]
            ds_types = ds_near["highway"].value_counts() if len(ds_near) > 0 else pd.Series(dtype=int)

            # All-highway roads within radius
            all_near = all_edges_utm[all_edges_utm.geometry.intersects(buffer)]
            all_types = all_near["highway"].value_counts() if len(all_near) > 0 else pd.Series(dtype=int)

            # Find excluded types
            all_type_set = set(all_types.index) if len(all_types) > 0 else set()
            ds_type_set = set(ds_types.index) if len(ds_types) > 0 else set()
            excluded_types = all_type_set - ds_type_set

            # Also count segments in all_types that don't appear in ds_types
            excluded_segments = sum(
                all_types.get(t, 0) for t in excluded_types
            )

            lines.append(f"**{radius}m radius:**")
            lines.append(f"- Drive-service segments: {len(ds_near)}")
            lines.append(f"- All-highway segments: {len(all_near)}")
            lines.append(f"- Excluded types: {sorted(excluded_types) if excluded_types else 'none'}")
            lines.append(f"- Excluded segments: {excluded_segments}")

            if excluded_types and len(all_near) > 0:
                lines.append("")
                lines.append(f"  | Excluded Type | Segments | Avg Length (m) |")
                lines.append(f"  |--------------|----------|----------------|")
                for etype in sorted(excluded_types):
                    et_mask = all_near["highway"] == etype
                    et_segs = all_near[et_mask]
                    avg_len = et_segs.geometry.length.mean()
                    lines.append(f"  | {etype} | {len(et_segs)} | {avg_len:.0f} |")

            # Estimate score impact of excluded roads
            # Use weight=0 (excluded) vs hypothetical residential weight (0.01)
            if excluded_segments > 0:
                # Conservative: assume excluded roads are pedestrian/cycle
                # with negligible motor traffic. Even if we assigned them
                # residential weight (0.01), estimate the max contribution
                hypothetical_contribution = 0.0
                for etype in excluded_types:
                    et_mask = all_near["highway"] == etype
                    et_segs = all_near[et_mask]
                    for _, seg in et_segs.iterrows():
                        centroid = seg.geometry.centroid
                        dist = school_utm_pt.distance(centroid)
                        # Use minimal weight (residential = 0.01)
                        hypothetical_contribution += 0.01 * np.exp(-LAMBDA * dist)

                lines.append(
                    f"- **Estimated max impact if excluded roads were residential-weighted:** "
                    f"{hypothetical_contribution:.4f}"
                )
            lines.append("")

        lines.append("")

    # Summary
    lines.append("---")
    lines.append("")
    lines.append("## Interpretation")
    lines.append("")
    lines.append("Excluded road types (footway, cycleway, path, pedestrian, steps, track, etc.)")
    lines.append("are non-motor-vehicle roads that do not generate significant traffic-related")
    lines.append("air pollution. Their exclusion from the TRAP analysis is methodologically correct.")
    lines.append("")
    lines.append("The 'estimated max impact' assumes all excluded roads carry residential-level")
    lines.append("traffic (weight=0.01), which is a substantial overestimate for pedestrian paths")
    lines.append("and cycleways. The actual impact of excluding these roads is effectively zero")
    lines.append("for pollution modeling purposes.")
    lines.append("")

    out = DATA_PROCESSED / "road_network_diagnostic.md"
    out.write_text("\n".join(lines), encoding="utf-8")
    _progress(f"Wrote diagnostic report to {out}")


# ---------------------------------------------------------------------------
# Main
# ---------------------------------------------------------------------------
def main():
    parser = argparse.ArgumentParser(description="Road pollution exposure analysis")
    parser.add_argument(
        "--cache-only", action="store_true",
        help="Use only cached data; do not download anything",
    )
    parser.add_argument(
        "--grid-resolution", type=int, default=DEFAULT_GRID_RESOLUTION,
        help=f"County grid resolution in meters (default {DEFAULT_GRID_RESOLUTION})",
    )
    parser.add_argument(
        "--skip-grid", action="store_true",
        help="Skip county-wide grid generation (faster, but no maps)",
    )
    parser.add_argument(
        "--debug-maps", action="store_true",
        help="Generate intermediate debug maps for visual verification",
    )
    parser.add_argument(
        "--diagnose-roads", action="store_true",
        help="Generate road network diagnostic report comparing drive_service vs all-highway networks",
    )
    args = parser.parse_args()

    print("=" * 60)
    print("Road Pollution Exposure Analysis")
    print("CHCCS Elementary Schools — Orange County, NC")
    print("=" * 60)

    ensure_directories()

    # 1. Download / load school locations (NCES)
    print("\n[1/10] Loading school locations ...")
    download_school_locations(cache_only=args.cache_only)
    schools = load_schools()
    print(f"  Loaded {len(schools)} schools")

    # 2. Download road network
    print("\n[2/10] Loading road network ...")
    roads_raw = download_road_network(cache_only=args.cache_only)

    # 3. Filter and prepare
    print("\n[3/10] Filtering roads ...")
    roads = filter_and_prepare_roads(roads_raw)

    # 3b. Apply NCDOT AADT overrides
    print("\n[4/10] Applying NCDOT AADT weight overrides ...")
    try:
        aadt_stations = download_ncdot_aadt(cache_only=args.cache_only)
        roads = apply_aadt_overrides(roads, aadt_stations)
    except Exception as e:
        _progress(f"AADT override skipped: {e}")
        _progress("Continuing with proxy weights only.")
        roads["weight_source"] = "proxy"

    # 4. Discretize
    print("\n[5/10] Discretizing roads into sub-segments ...")
    road_points = discretize_roads(roads)

    # 5. Download LULC
    print("\n[6/10] Loading land cover data ...")
    lulc_path = download_esa_worldcover(cache_only=args.cache_only)

    # 6. Run school analysis
    print("\n[7/10] Analyzing pollution exposure for each school ...")
    df = run_school_analysis(schools, road_points, lulc_path)
    df = normalize_and_rank(df)

    # 7. Save outputs
    print("\n[8/10] Saving results ...")
    save_results_csv(df)
    generate_analysis_markdown(df, roads)
    create_pollution_chart(df)

    # 8. County-wide grid and maps
    raw_grid, net_grid, bounds = None, None, None
    if not args.skip_grid:
        print("\n[9/10] Generating county-wide pollution maps ...")
        raw_grid, net_grid, bounds = generate_county_grid(
            road_points, roads, lulc_path, resolution=args.grid_resolution
        )
        create_county_maps(raw_grid, net_grid, bounds, df, roads_gdf=roads)
    else:
        print("\n[9/10] Skipping county grid (--skip-grid)")

    # 9. Additional standard maps
    print("\n[10/10] Generating additional maps ...")
    create_tree_canopy_map(lulc_path, schools)
    if raw_grid is not None:
        create_combined_map(raw_grid, net_grid, lulc_path, bounds, df, roads_gdf=roads)

    # Debug maps (if requested)
    if args.debug_maps:
        print("\n[DEBUG] Generating debug maps ...")
        generate_debug_maps(
            schools, roads, road_points, lulc_path, df,
            raw_grid=raw_grid, net_grid=net_grid, bounds=bounds,
        )

    # Road network diagnostic (if requested)
    if args.diagnose_roads:
        print("\n[DIAGNOSTIC] Generating road network diagnostic ...")
        diagnose_road_network(schools, roads)

    # Summary
    print("\n" + "=" * 60)
    print("Analysis complete!")
    print("=" * 60)
    print(f"\nOutputs:")
    print(f"  CSV:   {DATA_PROCESSED / 'road_pollution_scores.csv'}")
    print(f"  MD:    {DATA_PROCESSED / 'ROAD_POLLUTION.md'}")
    print(f"  Chart: {ASSETS_CHARTS / 'road_pollution_comparison.png'}")
    if not args.skip_grid:
        print(f"  Map:   {ASSETS_MAPS / 'road_pollution_raw_map.html'}")
        print(f"  Map:   {ASSETS_MAPS / 'road_pollution_net_map.html'}")
        print(f"  Map:   {ASSETS_MAPS / 'road_pollution_combined_map.html'}")
    print(f"  Map:   {ASSETS_MAPS / 'tree_canopy_map.html'}")
    if args.debug_maps:
        print(f"  Debug: {ASSETS_MAPS_DEBUG / 'debug_*.html'}")
    if args.diagnose_roads:
        print(f"  Diag:  {DATA_PROCESSED / 'road_network_diagnostic.md'}")

    # Print quick summary
    print("\nQuick summary (500m radius, net pollution):")
    for _, row in df.sort_values("rank_net_500m").iterrows():
        print(f"  #{int(row['rank_net_500m']):2d}  {row['school']:30s}  "
              f"Net={row['net_norm_500m']:5.1f}  "
              f"Canopy={row['canopy_500m']*100:4.1f}%")

    print("=" * 60)


if __name__ == "__main__":
    main()
