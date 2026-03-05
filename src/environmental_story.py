"""
Environmental Analysis Scrollytelling Page Generator

Builds an interactive scrollytelling HTML page that walks through how the
environmental analysis map is constructed — from raw data sources through
processing to the final combined visualization. Uses Glenwood Elementary
(highest net TRAP score) as a concrete running example.

Output:
    assets/maps/environmental_methodology.html

Usage:
    python src/environmental_story.py [--cache-only]
"""

import argparse
import base64
import io
import json
import sys
import warnings
from pathlib import Path

import geopandas as gpd
import matplotlib.colors as mcolors
import matplotlib.pyplot as plt
import numpy as np
import pandas as pd
import rasterio
from PIL import Image
from pyproj import Transformer
from shapely.geometry import Point, box, mapping

warnings.filterwarnings("ignore", category=FutureWarning)

# ---------------------------------------------------------------------------
# Paths
# ---------------------------------------------------------------------------
PROJECT_ROOT = Path(__file__).parent.parent
DATA_PROCESSED = PROJECT_ROOT / "data" / "processed"
DATA_CACHE = PROJECT_ROOT / "data" / "cache"
ASSETS_MAPS = PROJECT_ROOT / "assets" / "maps"

SCHOOL_CSV = DATA_CACHE / "nces_school_locations.csv"
ROAD_CACHE = DATA_CACHE / "osm_roads_orange_county_buffered.gpkg"
AADT_CACHE = DATA_CACHE / "ncdot_aadt_orange_county.gpkg"
LULC_CACHE = DATA_CACHE / "esa_worldcover_orange_county.tif"
TRAP_GRIDS_CACHE = DATA_CACHE / "trap_grids.npz"
UHI_GRID_CACHE = DATA_CACHE / "uhi_grid.npz"
DISTRICT_CACHE = DATA_CACHE / "chccs_district_boundary.gpkg"
FLOOD_CACHE = DATA_CACHE / "fema_flood_zones.gpkg"

OUTPUT_HTML = ASSETS_MAPS / "environmental_methodology.html"

CRS_WGS84 = "EPSG:4326"
CRS_UTM17N = "EPSG:32617"

# Glenwood bbox: ±0.015° around school location
GLENWOOD_NAME = "Glenwood Elementary"
GLENWOOD_BBOX_PAD = 0.015  # degrees

# Road weight constants (mirrored from road_pollution.py for reference only)
ROAD_WEIGHTS = {
    "motorway": 1.000, "motorway_link": 0.800,
    "trunk": 0.600, "trunk_link": 0.480,
    "primary": 0.300, "primary_link": 0.240,
    "secondary": 0.150, "secondary_link": 0.120,
    "tertiary": 0.060, "tertiary_link": 0.048,
    "unclassified": 0.020,
    "residential": 0.010,
    "service": 0.005, "living_street": 0.005,
}

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

# UHI weights (mirrored from environmental_map.py)
UHI_WEIGHTS = {
    10: -0.60, 20: -0.30, 30: -0.10, 40: -0.05,
    50: +1.00, 60: +0.40, 80: -0.50, 90: -0.40, 95: -0.40,
}
UHI_CLASS_LABELS = {
    10: "Tree cover", 20: "Shrubland", 30: "Herbaceous",
    40: "Cropland", 50: "Built-up", 60: "Bare/sparse",
    80: "Water", 90: "Wetland", 95: "Woody wetland",
}
UHI_CLASS_COLORS = {
    10: "#228B22", 20: "#8B6914", 30: "#90EE90",
    40: "#FFD700", 50: "#FF0000", 60: "#D2B48C",
    80: "#0000FF", 90: "#00CED1", 95: "#006400",
}

LAMBDA = 0.003  # decay rate
ALPHA = 0.56    # tree canopy mitigation
MAX_MITIGATION = 0.80


def _progress(msg: str):
    print(f"  ... {msg}")


# ---------------------------------------------------------------------------
# Data loading
# ---------------------------------------------------------------------------
def load_schools() -> pd.DataFrame:
    """Load school locations from NCES cache."""
    if not SCHOOL_CSV.exists():
        raise FileNotFoundError(
            f"School locations not found at {SCHOOL_CSV}. "
            "Run road_pollution.py first to download."
        )
    return pd.read_csv(SCHOOL_CSV)


def get_glenwood(schools: pd.DataFrame) -> dict:
    """Get Glenwood Elementary row as dict."""
    row = schools[schools["school"] == GLENWOOD_NAME].iloc[0]
    return {"lat": row["lat"], "lon": row["lon"], "school": row["school"]}


def get_glenwood_bbox(glenwood: dict) -> tuple:
    """Return (west, south, east, north) bbox around Glenwood."""
    return (
        glenwood["lon"] - GLENWOOD_BBOX_PAD,
        glenwood["lat"] - GLENWOOD_BBOX_PAD,
        glenwood["lon"] + GLENWOOD_BBOX_PAD,
        glenwood["lat"] + GLENWOOD_BBOX_PAD,
    )


def load_roads_near_glenwood(bbox: tuple) -> gpd.GeoDataFrame:
    """Load and clip roads to Glenwood bbox, add weight column."""
    if not ROAD_CACHE.exists():
        raise FileNotFoundError(f"Road cache not found at {ROAD_CACHE}")
    roads = gpd.read_file(ROAD_CACHE, bbox=bbox)
    # Filter to weighted road types and add weight
    mask = roads["highway"].isin(ROAD_WEIGHTS)
    roads = roads[mask].copy()
    roads["weight"] = roads["highway"].map(ROAD_WEIGHTS)
    _progress(f"Loaded {len(roads)} road segments near Glenwood")
    return roads


def load_aadt_near_glenwood(glenwood: dict, radius_m: float = 2000) -> gpd.GeoDataFrame:
    """Load AADT stations within radius of Glenwood."""
    if not AADT_CACHE.exists():
        _progress("No AADT cache found, skipping AADT stations")
        return gpd.GeoDataFrame()
    aadt = gpd.read_file(AADT_CACHE)
    pt = Point(glenwood["lon"], glenwood["lat"])
    aadt_utm = aadt.to_crs(CRS_UTM17N)
    pt_utm = gpd.GeoSeries([pt], crs=CRS_WGS84).to_crs(CRS_UTM17N).iloc[0]
    dists = aadt_utm.geometry.distance(pt_utm)
    nearby = aadt[dists <= radius_m].copy()
    _progress(f"Found {len(nearby)} AADT stations near Glenwood")
    return nearby


def load_district_boundary() -> gpd.GeoDataFrame:
    """Load district boundary polygon."""
    if not DISTRICT_CACHE.exists():
        raise FileNotFoundError(f"District boundary not found at {DISTRICT_CACHE}")
    return gpd.read_file(DISTRICT_CACHE)


def load_flood_near_glenwood(bbox: tuple):
    """Load and classify flood zones near Glenwood. Returns (flood_100, flood_500)."""
    if not FLOOD_CACHE.exists():
        raise FileNotFoundError(f"Flood cache not found at {FLOOD_CACHE}")
    flood = gpd.read_file(FLOOD_CACHE, bbox=bbox)
    if len(flood) == 0:
        return gpd.GeoDataFrame(), gpd.GeoDataFrame()
    flood_100 = flood[flood["FLD_ZONE"].isin(["A", "AE", "AO", "AH"])].copy()
    flood_500 = flood[flood["ZONE_SUBTY"].str.contains("0.2 PCT", na=False)].copy()
    _progress(f"Flood zones near Glenwood: {len(flood_100)} 100-yr, {len(flood_500)} 500-yr")
    return flood_100, flood_500


def load_school_property_glenwood() -> gpd.GeoDataFrame:
    """Load Glenwood school property parcel."""
    polys_path = PROJECT_ROOT / "data" / "raw" / "properties" / "combined_data_polys.gpkg"
    if not polys_path.exists() or not SCHOOL_CSV.exists():
        _progress("School properties not available, skipping")
        return gpd.GeoDataFrame()
    schools = pd.read_csv(SCHOOL_CSV)
    gw = schools[schools["school"] == GLENWOOD_NAME].iloc[0]
    parcels = gpd.read_file(polys_path)
    pt = Point(gw["lon"], gw["lat"])
    # Find parcel containing the school point
    containing = parcels[parcels.geometry.contains(pt)]
    if len(containing) > 0:
        result = containing.head(1).copy()
        result["school_name"] = GLENWOOD_NAME
        return result
    # Fallback: nearest centroid
    dists = parcels.geometry.centroid.distance(pt)
    nearest = parcels.iloc[[dists.idxmin()]].copy()
    nearest["school_name"] = GLENWOOD_NAME
    return nearest


def crop_grid(grid: np.ndarray, grid_bounds: tuple, crop_bbox: tuple):
    """Crop a 2D numpy grid to a bbox. Returns (cropped_grid, cropped_bounds)."""
    west, south, east, north = grid_bounds
    cw, cs, ce, cn = crop_bbox
    ny, nx = grid.shape

    # Compute pixel indices
    dx = (east - west) / nx
    dy = (north - south) / ny

    i0 = max(0, int((cw - west) / dx))
    i1 = min(nx, int((ce - west) / dx))
    j0 = max(0, int((north - cn) / dy))  # note: grid rows go north→south
    j1 = min(ny, int((north - cs) / dy))

    cropped = grid[j0:j1, i0:i1]
    cropped_bounds = (
        west + i0 * dx,
        north - j1 * dy,
        west + i1 * dx,
        north - j0 * dy,
    )
    _progress(f"Cropped grid {grid.shape} -> {cropped.shape}")
    return cropped, cropped_bounds


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

    ny, nx = grid.shape
    rgba = np.zeros((ny, nx, 4), dtype=np.uint8)
    rgba[..., :3] = (mapped[..., :3] * 255).astype(np.uint8)
    # Alpha: transparent for near-zero, else scaled
    active = grid > 0.001
    alpha_vals = np.where(active, np.clip(120 + 80 * normalized, 0, 255).astype(np.uint8), 0)
    rgba[..., 3] = alpha_vals

    img = Image.fromarray(rgba, "RGBA")
    buf = io.BytesIO()
    img.save(buf, format="PNG")
    buf.seek(0)
    b64 = base64.b64encode(buf.read()).decode()
    return f"data:image/png;base64,{b64}"


def load_lulc_near_glenwood(bbox: tuple) -> tuple:
    """Load ESA WorldCover crop near Glenwood. Returns (rgba_b64, raster_bounds)."""
    if not LULC_CACHE.exists():
        _progress("LULC cache not found, skipping")
        return None, None
    with rasterio.open(LULC_CACHE) as src:
        to_raster = Transformer.from_crs(CRS_WGS84, src.crs, always_xy=True)
        to_wgs = Transformer.from_crs(src.crs, CRS_WGS84, always_xy=True)
        # Convert bbox to raster CRS
        r_left, r_bottom = to_raster.transform(bbox[0], bbox[1])
        r_right, r_top = to_raster.transform(bbox[2], bbox[3])
        window = src.window(r_left, r_bottom, r_right, r_top)
        data = src.read(1, window=window)

        # Get actual bounds in WGS84
        win_transform = src.window_transform(window)
        h, w = data.shape
        corners_x = [win_transform.c, win_transform.c + win_transform.a * w]
        corners_y = [win_transform.f, win_transform.f + win_transform.e * h]
        w_lon, s_lat = to_wgs.transform(min(corners_x), min(corners_y))
        e_lon, n_lat = to_wgs.transform(max(corners_x), max(corners_y))

        # Create an RGBA image: color by land cover class
        rgba = np.zeros((h, w, 4), dtype=np.uint8)
        for class_code, color_hex in UHI_CLASS_COLORS.items():
            mask = data == class_code
            if not np.any(mask):
                continue
            r = int(color_hex[1:3], 16)
            g = int(color_hex[3:5], 16)
            b = int(color_hex[5:7], 16)
            rgba[mask] = [r, g, b, 180]

        # Downsample to manageable size
        max_dim = 200
        step_size = max(1, max(h, w) // max_dim)
        if step_size > 1:
            rgba = rgba[::step_size, ::step_size]

        img = Image.fromarray(rgba, "RGBA")
        buf = io.BytesIO()
        img.save(buf, format="PNG")
        buf.seek(0)
        b64 = base64.b64encode(buf.read()).decode()

        _progress(f"LULC crop: {data.shape} -> {rgba.shape[:2]}")
        return f"data:image/png;base64,{b64}", (w_lon, s_lat, e_lon, n_lat)


def gdf_to_geojson_str(gdf: gpd.GeoDataFrame, properties: list = None,
                       simplify_m: float = None) -> str:
    """Convert GeoDataFrame to compact GeoJSON string.

    If simplify_m is set, simplifies geometries in UTM by that many meters
    before converting back to WGS84, reducing output size.
    """
    if len(gdf) == 0:
        return '{"type":"FeatureCollection","features":[]}'
    gdf = gdf.to_crs(CRS_WGS84)
    if simplify_m:
        gdf = gdf.copy()
        gdf_utm = gdf.to_crs(CRS_UTM17N)
        gdf_utm["geometry"] = gdf_utm.geometry.simplify(simplify_m, preserve_topology=True)
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
                    props[p] = val if not isinstance(val, (np.integer, np.floating)) else float(val)
        features.append({
            "type": "Feature",
            "geometry": mapping(row.geometry),
            "properties": props,
        })
    fc = {"type": "FeatureCollection", "features": features}
    return json.dumps(fc, separators=(",", ":"))


def roads_to_geojson_str(roads: gpd.GeoDataFrame) -> str:
    """Convert roads GDF to GeoJSON with highway, weight, color."""
    return gdf_to_geojson_str(roads, properties=["highway", "weight"], simplify_m=5)


# ---------------------------------------------------------------------------
# HTML template
# ---------------------------------------------------------------------------
def build_html(data: dict) -> str:
    """Build the complete scrollytelling HTML page."""

    # Data variables to embed
    schools_json = data["schools_json"]
    district_json = data["district_json"]
    roads_json = data["roads_json"]
    aadt_json = data["aadt_json"]
    glenwood = data["glenwood"]
    trap_raw_b64 = data["trap_raw_b64"]
    trap_net_b64 = data["trap_net_b64"]
    trap_bounds = data["trap_bounds"]
    lulc_b64 = data.get("lulc_b64", "")
    lulc_bounds = data.get("lulc_bounds", (0, 0, 0, 0))
    uhi_b64 = data["uhi_b64"]
    uhi_bounds = data["uhi_bounds"]
    flood_100_json = data["flood_100_json"]
    flood_500_json = data["flood_500_json"]
    school_property_json = data["school_property_json"]

    html = f"""<!DOCTYPE html>
<html lang="en">
<head>
<meta charset="UTF-8">
<meta name="viewport" content="width=device-width, initial-scale=1.0">
<title>How the Environmental Analysis Map Works — CHCCS District</title>
<link rel="stylesheet" href="https://unpkg.com/leaflet@1.9.3/dist/leaflet.css" />
<style>
* {{ margin: 0; padding: 0; box-sizing: border-box; }}
body {{ font-family: -apple-system, BlinkMacSystemFont, "Segoe UI", Roboto, sans-serif; background: #f8f9fa; }}

#map-container {{
  position: fixed;
  top: 0;
  right: 0;
  width: 55%;
  height: 100vh;
  z-index: 1;
}}

#map {{
  width: 100%;
  height: 100%;
}}

.scroll-container {{
  position: relative;
  width: 45%;
  padding: 0 30px;
  z-index: 2;
}}

.step {{
  min-height: 80vh;
  padding: 30px 20px;
  margin: 20px 0;
  background: rgba(255,255,255,0.95);
  border-radius: 8px;
  border-left: 4px solid #2196F3;
  box-shadow: 0 2px 8px rgba(0,0,0,0.08);
  opacity: 0.3;
  transition: opacity 0.4s ease;
}}

.step.is-active {{
  opacity: 1;
  border-left-color: #1565C0;
  box-shadow: 0 4px 16px rgba(0,0,0,0.12);
}}

.step:first-child {{
  margin-top: 40vh;
}}

.step:last-child {{
  margin-bottom: 60vh;
}}

.step h2 {{
  font-size: 1.25rem;
  color: #1565C0;
  margin-bottom: 12px;
  line-height: 1.3;
}}

.step p {{
  font-size: 0.95rem;
  color: #333;
  line-height: 1.6;
  margin-bottom: 10px;
}}

.step .source {{
  font-size: 0.8rem;
  color: #666;
  background: #f0f4f8;
  padding: 6px 10px;
  border-radius: 4px;
  margin-top: 8px;
}}

.step .source a {{ color: #1565C0; }}

.step .limitation {{
  font-size: 0.85rem;
  background: #fff8e1;
  border-left: 3px solid #ffc107;
  padding: 8px 12px;
  margin-top: 10px;
  border-radius: 0 4px 4px 0;
  color: #5d4037;
}}

.step details {{
  margin-top: 10px;
  font-size: 0.85rem;
}}

.step details summary {{
  cursor: pointer;
  color: #1565C0;
  font-weight: 500;
}}

.step details > div {{
  padding: 8px 12px;
  background: #f5f7fa;
  border-radius: 4px;
  margin-top: 6px;
  font-family: "SFMono-Regular", Consolas, monospace;
  font-size: 0.8rem;
  line-height: 1.5;
}}

.step table {{
  border-collapse: collapse;
  font-size: 0.82rem;
  margin: 10px 0;
  width: 100%;
}}
.step th, .step td {{
  padding: 4px 8px;
  border: 1px solid #dee2e6;
  text-align: left;
}}
.step th {{ background: #e8eaf6; font-weight: 600; }}

.step-number {{
  display: inline-block;
  background: #1565C0;
  color: white;
  width: 24px;
  height: 24px;
  border-radius: 50%;
  text-align: center;
  font-size: 0.75rem;
  line-height: 24px;
  font-weight: bold;
  margin-right: 8px;
  vertical-align: middle;
}}

.intro-header {{
  font-size: 1.5rem !important;
  color: #0d47a1 !important;
}}

/* Map overlay for step 13 dimming */
#map-dim {{
  position: absolute;
  top: 0; left: 0; right: 0; bottom: 0;
  background: rgba(255,255,255,0.4);
  z-index: 1000;
  pointer-events: none;
  display: none;
}}

@media (max-width: 900px) {{
  .scroll-container {{ width: 100%; padding: 0 15px; }}
  #map-container {{ position: fixed; top: 0; left: 0; width: 100%; height: 40vh; }}
  .step {{ min-height: auto; background: rgba(255,255,255,0.97); }}
  .step:first-child {{ margin-top: 42vh; }}
  .step:last-child {{ margin-bottom: 20px; }}
}}
</style>
</head>
<body>

<div class="scroll-container">

    <!-- Step 0: Title / Introduction -->
    <div class="step" data-step="0">
      <h2 class="intro-header">How the Environmental Analysis Map Works</h2>
      <p>This walkthrough shows, step by step, how we build the
      <a href="chccs_environmental_analysis.html" target="_blank">CHCCS Environmental Analysis Map</a>
      — from raw public data to the final combined visualization.</p>
      <p>We use <strong>Glenwood Elementary</strong> as a running example because
      it has the highest net traffic-related air pollution (TRAP) score among the
      11 CHCCS elementary schools. The same process applies equally to every school.</p>
      <p>The map on the right shows the CHCCS school district boundary. All data
      is loaded from public sources and cached locally.</p>
      <div class="source">Data: CHCCS district boundary from Census TIGER/Line shapefiles</div>
    </div>

    <!-- Step 1: School Locations -->
    <div class="step" data-step="1">
      <h2><span class="step-number">1</span>Starting Point: School Locations</h2>
      <p>We begin with the locations of all 11 CHCCS elementary schools, downloaded
      from the <strong>National Center for Education Statistics (NCES)</strong>
      EDGE Public School Locations dataset (2023-24 school year).</p>
      <p>The blue marker highlights <strong>Glenwood Elementary</strong>, our
      focus school. All other schools appear as smaller gray markers.</p>
      <div class="source">Source: <a href="https://nces.ed.gov/programs/edge/Geographic/SchoolLocations" target="_blank">NCES EDGE</a> 2023-24 (LEAID 3700720)</div>
      <div class="limitation">These are building centroid coordinates, not property boundaries.
      A school's environmental exposure depends on its full property extent, not just one point.</div>
    </div>

    <!-- Step 2: Road Network -->
    <div class="step" data-step="2">
      <h2><span class="step-number">2</span>Data: Road Network</h2>
      <p>We download the complete road network from <strong>OpenStreetMap</strong>
      via the OSMnx library. Roads are color-coded by their OSM classification
      (motorway, primary, secondary, etc.).</p>
      <p>Each road type receives a <strong>proxy weight</strong> reflecting its
      expected traffic volume. Motorways (weight 1.0) carry the most traffic;
      residential streets (0.01) carry the least.</p>
      <table>
        <tr><th>Road Type</th><th>Weight</th><th>Example</th></tr>
        <tr><td>Motorway</td><td>1.000</td><td>I-40</td></tr>
        <tr><td>Primary</td><td>0.300</td><td>US-15-501</td></tr>
        <tr><td>Secondary</td><td>0.150</td><td>Estes Dr</td></tr>
        <tr><td>Tertiary</td><td>0.060</td><td>Weaver Dairy Rd</td></tr>
        <tr><td>Residential</td><td>0.010</td><td>Local streets</td></tr>
      </table>
      <div class="source">Source: <a href="https://www.openstreetmap.org/" target="_blank">OpenStreetMap</a> via OSMnx (network_type="drive_service")</div>
      <div class="limitation">OSM road classifications are community-maintained and may be
      inconsistent. Weights are proxies, not measured traffic volumes.</div>
      <details>
        <summary>Full road weight table</summary>
        <div>motorway: 1.000, motorway_link: 0.800<br>
        trunk: 0.600, trunk_link: 0.480<br>
        primary: 0.300, primary_link: 0.240<br>
        secondary: 0.150, secondary_link: 0.120<br>
        tertiary: 0.060, tertiary_link: 0.048<br>
        unclassified: 0.020<br>
        residential: 0.010<br>
        service: 0.005, living_street: 0.005</div>
      </details>
    </div>

    <!-- Step 3: AADT Stations -->
    <div class="step" data-step="3">
      <h2><span class="step-number">3</span>Data: Traffic Counts (AADT)</h2>
      <p>Where available, we replace proxy weights with <strong>actual traffic
      counts</strong> from the North Carolina Department of Transportation (NCDOT).
      AADT = Annual Average Daily Traffic.</p>
      <p>Each orange diamond on the map marks an NCDOT counting station.
      When a station is within 50 meters of an OSM road segment, its measured
      traffic count overrides the proxy weight.</p>
      <p>The AADT-derived weight is: <code>AADT / 50,000</code> (where 50,000 is the
      motorway reference baseline).</p>
      <div class="source">Source: <a href="https://connect.ncdot.gov/resources/State-Mapping/Pages/Traffic-Volume-Maps.aspx" target="_blank">NCDOT AADT Stations</a> (ArcGIS REST API, Orange County)</div>
      <div class="limitation">AADT stations cover major roads well but leave many minor roads
      with only proxy weights. Coverage is sparse in residential areas.</div>
    </div>

    <!-- Step 4: Weight Assignment -->
    <div class="step" data-step="4">
      <h2><span class="step-number">4</span>Processing: Final Road Weights</h2>
      <p>Roads are now recolored by their <strong>final weight</strong> — a blend of
      proxy classifications and AADT overrides where available. Brighter red = higher weight
      = more expected pollution.</p>
      <p>This gives us a pollution <em>source</em> map: every road segment has a weight
      proportional to its expected traffic-related emissions (NOx, black carbon,
      ultrafine particles).</p>
      <div class="limitation">Even with AADT overrides, weights remain approximations.
      Actual emissions depend on vehicle fleet composition, congestion, grade, and
      meteorology — none of which are modeled here.</div>
    </div>

    <!-- Step 5: Exponential Decay -->
    <div class="step" data-step="5">
      <h2><span class="step-number">5</span>Processing: Distance Decay</h2>
      <p>Traffic pollution doesn't stay on the road — it disperses with distance.
      We model this as <strong>exponential decay</strong>: pollution drops off
      rapidly in the first 100-200 meters, then tails off more slowly.</p>
      <p>The concentric rings show decay distances from Glenwood Elementary:</p>
      <table>
        <tr><th>Distance</th><th>Remaining</th></tr>
        <tr><td>100 m</td><td>74%</td></tr>
        <tr><td>250 m</td><td>47%</td></tr>
        <tr><td>500 m</td><td>22%</td></tr>
        <tr><td>1,000 m</td><td>5%</td></tr>
      </table>
      <p>The school's TRAP score sums contributions from <em>every</em> nearby road
      segment, each decayed by distance.</p>
      <div class="source">Literature: Karner et al. (2010), HEI consensus on 300-500m primary impact zone</div>
      <details>
        <summary>Decay formula</summary>
        <div>contribution = weight &times; exp(-&lambda; &times; distance)<br>
        &lambda; = 0.003 m<sup>-1</sup> (composite NOx/BC/UFP rate)<br>
        Total score = &Sigma; contributions from all road sub-segments within radius</div>
      </details>
    </div>

    <!-- Step 6: Raw Pollution Grid -->
    <div class="step" data-step="6">
      <h2><span class="step-number">6</span>Result: Raw Pollution Grid</h2>
      <p>Applying the decay model to every road segment produces a <strong>continuous
      pollution surface</strong> across the district at 100-meter resolution.</p>
      <p>Warmer colors (yellow &rarr; red) indicate higher cumulative TRAP exposure.
      The hottest zones follow major road corridors.</p>
      <p>This is the <em>raw</em> pollution index — before accounting for any
      mitigating factors like tree cover.</p>
      <div class="source">Computed: Sum of weighted exponential decay from all road segments at 100m grid resolution</div>
      <div class="limitation">This is a RELATIVE index for comparing locations, not an
      absolute health risk measure. Grid resolution (100m) smooths sub-block variation.</div>
    </div>

    <!-- Step 7: Satellite Land Cover -->
    <div class="step" data-step="7">
      <h2><span class="step-number">7</span>Data: Satellite Land Cover</h2>
      <p>To account for tree canopy mitigation, we use the <strong>ESA WorldCover</strong>
      satellite land cover dataset (10-meter resolution, 2021).</p>
      <p>The map shows land cover classes near Glenwood: green = tree cover,
      red = built-up/impervious surfaces, blue = water, etc.</p>
      <p>Trees reduce air pollution through particle deposition on leaves and
      aerodynamic dispersion. More canopy = more pollution reduction.</p>
      <div class="source">Source: <a href="https://esa-worldcover.org/" target="_blank">ESA WorldCover V2 2021</a> via Microsoft Planetary Computer (10m resolution)</div>
      <div class="limitation">10m satellite classification has accuracy limits.
      Deciduous trees provide less mitigation in winter. The dataset is from 2021 and
      doesn't reflect recent development or tree removal.</div>
    </div>

    <!-- Step 8: Tree Mitigation -->
    <div class="step" data-step="8">
      <h2><span class="step-number">8</span>Processing: Tree Canopy Mitigation</h2>
      <p>We compute <strong>tree canopy percentage</strong> around each grid cell
      and reduce the raw pollution score accordingly:</p>
      <p><code>net = raw &times; (1 &minus; &alpha; &times; canopy%)</code></p>
      <p>With &alpha; = 0.56 (2.8% PM2.5 reduction per 5% canopy increase,
      from Nowak et al. 2014) and a maximum 80% reduction cap.</p>
      <p>The result is the <em>net</em> pollution grid — raw scores reduced by
      local tree cover. Areas with dense canopy show noticeably lower values.</p>
      <div class="source">Literature: Nowak et al. (2014), urban tree air quality effects meta-analysis</div>
      <div class="limitation">Mitigation factors are from national meta-analyses, not
      Chapel Hill-specific measurements. Actual mitigation depends on tree species,
      leaf area, wind patterns, and season.</div>
      <details>
        <summary>Mitigation formula</summary>
        <div>mitigation = min(&alpha; &times; canopy_fraction, MAX_MITIGATION)<br>
        &alpha; = 0.56 (literature-based)<br>
        MAX_MITIGATION = 0.80 (cap at 80%)<br>
        net_score = raw_score &times; (1 &minus; mitigation)</div>
      </details>
    </div>

    <!-- Step 9: Flood Zones -->
    <div class="step" data-step="9">
      <h2><span class="step-number">9</span>Data: FEMA Flood Zones</h2>
      <p>Flood risk is the second environmental layer. We load <strong>FEMA
      National Flood Hazard Layer</strong> polygons, classified into:</p>
      <ul style="margin: 8px 0 8px 20px; font-size: 0.92rem;">
        <li><span style="color:#6baed6;">Blue</span>: 100-year flood zone (1% annual chance)</li>
        <li><span style="color:#bdd7e7;">Light blue</span>: 500-year flood zone (0.2% annual chance)</li>
      </ul>
      <p>These polygons show areas with significant flood risk according to
      FEMA's flood insurance rate maps.</p>
      <div class="source">Source: <a href="https://www.fema.gov/flood-maps/national-flood-hazard-layer" target="_blank">FEMA NFHL</a> (ArcGIS REST API, layer 28 = S_FLD_HAZ_AR)</div>
      <div class="limitation">FEMA maps may not reflect recent development, drainage
      improvements, or climate change impacts on flood frequency.</div>
    </div>

    <!-- Step 10: Flood Overlap -->
    <div class="step" data-step="10">
      <h2><span class="step-number">10</span>Processing: School Property Flood Overlap</h2>
      <p>We overlay flood zones on <strong>school property parcels</strong> from
      Orange County GIS data to determine what percentage of each school's land
      falls within a flood zone.</p>
      <p>The green polygon shows Glenwood's property boundary. Any red-highlighted
      area indicates overlap with a flood zone.</p>
      <p>This intersection is computed in UTM coordinates (EPSG:32617) for
      accurate area measurements, then converted to acres.</p>
      <div class="source">Parcels: Orange County GIS. Flood: FEMA NFHL. Intersection computed in EPSG:32617 (UTM 17N).</div>
      <div class="limitation">Parcel boundaries may not exactly match school-owned land.
      Flood overlap doesn't account for elevation, drainage, or building placement
      within the parcel.</div>
    </div>

    <!-- Step 11: UHI Proxy -->
    <div class="step" data-step="11">
      <h2><span class="step-number">11</span>Processing: Urban Heat Island Proxy</h2>
      <p>The third environmental layer uses the same ESA WorldCover data to estimate
      <strong>relative heat exposure</strong> (Urban Heat Island effect).</p>
      <p>Each land cover class receives a thermal weight: built-up surfaces
      contribute heat (+1.0), while trees provide cooling (-0.6). The weighted
      sum is normalized to a 0-100 scale.</p>
      <table>
        <tr><th>Land Cover</th><th>Weight</th><th>Effect</th></tr>
        <tr><td>Built-up</td><td>+1.00</td><td>Heat source</td></tr>
        <tr><td>Bare/sparse</td><td>+0.40</td><td>Heat absorption</td></tr>
        <tr><td>Tree cover</td><td>&minus;0.60</td><td>Cooling</td></tr>
        <tr><td>Water</td><td>&minus;0.50</td><td>Thermal buffer</td></tr>
      </table>
      <div class="source">Literature: Oke (1982), Stewart &amp; Oke (2012) Local Climate Zones</div>
      <div class="limitation">This is a PROXY based on land cover, NOT measured surface
      temperature. Actual UHI depends on building height, wind, albedo, and
      anthropogenic heat — none modeled here.</div>
      <details>
        <summary>All UHI weights</summary>
        <div>Tree cover (10): -0.60<br>
        Shrubland (20): -0.30<br>
        Herbaceous (30): -0.10<br>
        Cropland (40): -0.05<br>
        Built-up (50): +1.00<br>
        Bare/sparse (60): +0.40<br>
        Water (80): -0.50<br>
        Wetland (90): -0.40<br>
        Woody wetland (95): -0.40</div>
      </details>
    </div>

    <!-- Step 12: Combined Map -->
    <div class="step" data-step="12">
      <h2><span class="step-number">12</span>Combined: All Layers Together</h2>
      <p>The final environmental analysis map combines all three layers:</p>
      <ol style="margin: 8px 0 8px 20px; font-size: 0.92rem;">
        <li><strong>Net TRAP</strong> — traffic pollution (after tree mitigation)</li>
        <li><strong>Flood zones</strong> — FEMA 100-year and 500-year</li>
        <li><strong>UHI proxy</strong> — relative heat exposure from land cover</li>
      </ol>
      <p>In the full map, users toggle between layers using radio buttons
      (TRAP, UHI) and checkboxes (flood, tree canopy). This zoom-out shows the
      district-wide view with all data visible.</p>
      <p>Each school receives scores at 500m and 1,000m radii, enabling
      direct comparison across all 11 schools.</p>
      <div class="source">All layers combined. See <a href="chccs_environmental_analysis.html" target="_blank">the full interactive map</a>.</div>
    </div>

    <!-- Step 13: Limitations -->
    <div class="step" data-step="13">
      <h2><span class="step-number">&bull;</span>Limitations &amp; Context</h2>
      <p>This analysis has important limitations that users should understand:</p>
      <ul style="margin: 8px 0; padding-left: 20px; font-size: 0.92rem; line-height: 1.7;">
        <li><strong>Relative, not absolute:</strong> Scores compare schools to each other
        — they are not health risk assessments</li>
        <li><strong>Proxy-based:</strong> Road weights, UHI weights, and mitigation factors
        come from literature, not local measurements</li>
        <li><strong>Static snapshot:</strong> Data from 2021-2024 does not reflect
        ongoing development or seasonal variation</li>
        <li><strong>2D analysis:</strong> Does not account for building ventilation,
        indoor air filtration, or actual student time outdoors</li>
        <li><strong>Grid resolution:</strong> 100m cells smooth out variation within
        a single school property</li>
      </ul>
      <p>For the full list of 23 known limitations, see the
      <a href="../docs/ENVIRONMENTAL_ANALYSIS_README.md" target="_blank">Environmental
      Analysis documentation</a>.</p>
      <p style="margin-top: 15px; font-size: 0.85rem; color: #666;">
      All source code is available in this repository. Each data source is documented
      with its provider, access date, and refresh method.</p>
    </div>

</div><!-- end scroll-container -->

<div id="map-container">
  <div id="map"></div>
  <div id="map-dim"></div>
</div>

<script src="https://unpkg.com/leaflet@1.9.3/dist/leaflet.js"></script>
<script src="https://unpkg.com/scrollama@3.2.0/build/scrollama.js"></script>
<script>
// =========================================================================
// Embedded data
// =========================================================================
var SCHOOLS = {schools_json};
var DISTRICT = {district_json};
var ROADS = {roads_json};
var AADT = {aadt_json};
var GLENWOOD = {json.dumps(glenwood)};
var TRAP_RAW_B64 = "{trap_raw_b64}";
var TRAP_NET_B64 = "{trap_net_b64}";
var TRAP_BOUNDS = {json.dumps(list(trap_bounds))};
var LULC_B64 = "{lulc_b64}";
var LULC_BOUNDS = {json.dumps(list(lulc_bounds))};
var UHI_B64 = "{uhi_b64}";
var UHI_BOUNDS = {json.dumps(list(uhi_bounds))};
var FLOOD_100 = {flood_100_json};
var FLOOD_500 = {flood_500_json};
var SCHOOL_PROPERTY = {school_property_json};

// Road colors by highway type
var ROAD_COLORS = {json.dumps(ROAD_COLORS)};

// =========================================================================
// Map setup
// =========================================================================
var map = L.map('map', {{
  zoomControl: true,
  scrollWheelZoom: false
}}).setView([35.9132, -79.0558], 12);

L.tileLayer('https://{{s}}.basemaps.cartocdn.com/light_all/{{z}}/{{x}}/{{y}}@2x.png', {{
  attribution: '&copy; <a href="https://www.openstreetmap.org/copyright">OSM</a> &copy; <a href="https://carto.com/">CARTO</a>',
  maxZoom: 19,
  subdomains: 'abcd'
}}).addTo(map);

// =========================================================================
// Layer factories
// =========================================================================
var layers = {{}};

// District boundary
layers.district = L.geoJSON(DISTRICT, {{
  style: {{ fillColor: 'transparent', color: '#333', weight: 2, dashArray: '5,5' }}
}});

// School markers
layers.schools = L.layerGroup();
var glenwoodMarker = null;
SCHOOLS.features.forEach(function(f) {{
  var ll = [f.geometry.coordinates[1], f.geometry.coordinates[0]];
  var isGlenwood = f.properties.school === 'Glenwood Elementary';
  var marker = L.circleMarker(ll, {{
    radius: isGlenwood ? 10 : 5,
    fillColor: isGlenwood ? '#1565C0' : '#999',
    color: isGlenwood ? '#0d47a1' : '#666',
    weight: isGlenwood ? 3 : 1,
    fillOpacity: isGlenwood ? 0.9 : 0.5
  }}).bindTooltip(f.properties.school, {{ permanent: isGlenwood, direction: 'top', offset: [0, -8] }});
  marker.addTo(layers.schools);
  if (isGlenwood) glenwoodMarker = marker;
}});

// Roads colored by type
layers.roadsByType = L.layerGroup();
ROADS.features.forEach(function(f) {{
  var hw = f.properties.highway;
  var coords = f.geometry.coordinates.map(function(c) {{ return [c[1], c[0]]; }});
  if (f.geometry.type === 'MultiLineString') {{
    f.geometry.coordinates.forEach(function(line) {{
      var latlngs = line.map(function(c) {{ return [c[1], c[0]]; }});
      L.polyline(latlngs, {{ color: ROAD_COLORS[hw] || '#ccc', weight: 2, opacity: 0.7 }}).addTo(layers.roadsByType);
    }});
  }} else {{
    L.polyline(coords, {{ color: ROAD_COLORS[hw] || '#ccc', weight: 2, opacity: 0.7 }}).addTo(layers.roadsByType);
  }}
}});

// Roads colored by weight (continuous scale)
layers.roadsByWeight = L.layerGroup();
ROADS.features.forEach(function(f) {{
  var w = f.properties.weight || 0;
  var intensity = Math.min(1, w / 0.3);
  var r = Math.round(255 * intensity);
  var g = Math.round(200 * (1 - intensity));
  var color = 'rgb(' + r + ',' + g + ',0)';
  var lineCoords;
  if (f.geometry.type === 'MultiLineString') {{
    f.geometry.coordinates.forEach(function(line) {{
      lineCoords = line.map(function(c) {{ return [c[1], c[0]]; }});
      L.polyline(lineCoords, {{ color: color, weight: Math.max(1, w * 6), opacity: 0.8 }}).addTo(layers.roadsByWeight);
    }});
  }} else {{
    lineCoords = f.geometry.coordinates.map(function(c) {{ return [c[1], c[0]]; }});
    L.polyline(lineCoords, {{ color: color, weight: Math.max(1, w * 6), opacity: 0.8 }}).addTo(layers.roadsByWeight);
  }}
}});

// AADT stations
layers.aadt = L.layerGroup();
if (AADT.features) {{
  AADT.features.forEach(function(f) {{
    var ll = [f.geometry.coordinates[1], f.geometry.coordinates[0]];
    var aadt = f.properties.aadt || '?';
    L.marker(ll, {{
      icon: L.divIcon({{
        html: '<div style="width:14px;height:14px;background:#ff8c00;transform:rotate(45deg);border:2px solid #cc6600;"></div>',
        iconSize: [14, 14],
        iconAnchor: [7, 7],
        className: ''
      }})
    }}).bindTooltip('AADT: ' + Number(aadt).toLocaleString(), {{ direction: 'top' }}).addTo(layers.aadt);
  }});
}}

// Decay rings
layers.decayRings = L.layerGroup();
var ringDistances = [100, 250, 500, 1000];
var ringLabels = ['100m (74%)', '250m (47%)', '500m (22%)', '1000m (5%)'];
var ringColors = ['#d32f2f', '#f57c00', '#fbc02d', '#81c784'];
ringDistances.forEach(function(d, i) {{
  L.circle([GLENWOOD.lat, GLENWOOD.lon], {{
    radius: d,
    color: ringColors[i],
    weight: 2,
    fill: false,
    dashArray: i > 0 ? '5,5' : null
  }}).bindTooltip(ringLabels[i], {{ permanent: true, direction: 'right' }}).addTo(layers.decayRings);
}});

// TRAP raw raster
var trapW = TRAP_BOUNDS[0], trapS = TRAP_BOUNDS[1], trapE = TRAP_BOUNDS[2], trapN = TRAP_BOUNDS[3];
layers.trapRaw = L.imageOverlay(TRAP_RAW_B64, [[trapS, trapW], [trapN, trapE]], {{ opacity: 0.7 }});

// TRAP net raster
layers.trapNet = L.imageOverlay(TRAP_NET_B64, [[trapS, trapW], [trapN, trapE]], {{ opacity: 0.7 }});

// LULC raster
if (LULC_B64) {{
  var lW = LULC_BOUNDS[0], lS = LULC_BOUNDS[1], lE = LULC_BOUNDS[2], lN = LULC_BOUNDS[3];
  layers.lulc = L.imageOverlay(LULC_B64, [[lS, lW], [lN, lE]], {{ opacity: 0.7 }});
}}

// UHI raster
var uW = UHI_BOUNDS[0], uS = UHI_BOUNDS[1], uE = UHI_BOUNDS[2], uN = UHI_BOUNDS[3];
layers.uhi = L.imageOverlay(UHI_B64, [[uS, uW], [uN, uE]], {{ opacity: 0.7 }});

// Flood zones
layers.flood100 = L.geoJSON(FLOOD_100, {{
  style: {{ fillColor: '#6baed6', color: '#2171b5', weight: 1, fillOpacity: 0.4 }}
}});
layers.flood500 = L.geoJSON(FLOOD_500, {{
  style: {{ fillColor: '#bdd7e7', color: '#6baed6', weight: 1, fillOpacity: 0.3 }}
}});

// School property
layers.schoolProperty = L.geoJSON(SCHOOL_PROPERTY, {{
  style: {{ fillColor: '#d4edda', color: '#155724', weight: 2, fillOpacity: 0.5 }}
}});

// =========================================================================
// Step handler
// =========================================================================
var currentStep = -1;
var glenwoodBounds = [[GLENWOOD.lat - 0.015, GLENWOOD.lon - 0.015],
                       [GLENWOOD.lat + 0.015, GLENWOOD.lon + 0.015]];

function clearAllLayers() {{
  Object.keys(layers).forEach(function(k) {{
    if (map.hasLayer(layers[k])) map.removeLayer(layers[k]);
  }});
  document.getElementById('map-dim').style.display = 'none';
}}

function handleStep(step) {{
  if (step === currentStep) return;
  currentStep = step;
  clearAllLayers();

  switch(step) {{
    case 0: // District overview
      layers.district.addTo(map);
      map.setView([35.9132, -79.0558], 12);
      break;

    case 1: // School locations
      layers.district.addTo(map);
      layers.schools.addTo(map);
      map.fitBounds(glenwoodBounds);
      setTimeout(function() {{ map.setZoom(14); }}, 300);
      break;

    case 2: // Road network by type
      layers.district.addTo(map);
      layers.schools.addTo(map);
      layers.roadsByType.addTo(map);
      map.fitBounds(glenwoodBounds);
      break;

    case 3: // AADT stations
      layers.district.addTo(map);
      layers.schools.addTo(map);
      layers.roadsByType.addTo(map);
      layers.aadt.addTo(map);
      map.fitBounds(glenwoodBounds);
      break;

    case 4: // Roads by weight
      layers.district.addTo(map);
      layers.schools.addTo(map);
      layers.roadsByWeight.addTo(map);
      map.fitBounds(glenwoodBounds);
      break;

    case 5: // Decay rings
      layers.district.addTo(map);
      layers.schools.addTo(map);
      layers.roadsByWeight.addTo(map);
      layers.decayRings.addTo(map);
      map.fitBounds([[GLENWOOD.lat - 0.012, GLENWOOD.lon - 0.015],
                      [GLENWOOD.lat + 0.012, GLENWOOD.lon + 0.015]]);
      break;

    case 6: // Raw TRAP grid
      layers.district.addTo(map);
      layers.schools.addTo(map);
      layers.trapRaw.addTo(map);
      map.fitBounds(glenwoodBounds);
      break;

    case 7: // LULC
      layers.district.addTo(map);
      layers.schools.addTo(map);
      if (layers.lulc) layers.lulc.addTo(map);
      map.fitBounds(glenwoodBounds);
      break;

    case 8: // Net TRAP grid (after mitigation)
      layers.district.addTo(map);
      layers.schools.addTo(map);
      layers.trapNet.addTo(map);
      map.fitBounds(glenwoodBounds);
      break;

    case 9: // Flood zones
      layers.district.addTo(map);
      layers.schools.addTo(map);
      layers.flood100.addTo(map);
      layers.flood500.addTo(map);
      map.fitBounds(glenwoodBounds);
      break;

    case 10: // Flood overlap with school property
      layers.district.addTo(map);
      layers.schools.addTo(map);
      layers.schoolProperty.addTo(map);
      layers.flood100.addTo(map);
      layers.flood500.addTo(map);
      map.fitBounds(glenwoodBounds);
      map.setZoom(16);
      break;

    case 11: // UHI proxy
      layers.district.addTo(map);
      layers.schools.addTo(map);
      layers.uhi.addTo(map);
      map.fitBounds(glenwoodBounds);
      break;

    case 12: // Combined — all layers, zoom out
      layers.district.addTo(map);
      layers.schools.addTo(map);
      layers.trapNet.addTo(map);
      layers.flood100.addTo(map);
      layers.flood500.addTo(map);
      map.setView([35.9132, -79.0558], 12);
      break;

    case 13: // Limitations — dim
      layers.district.addTo(map);
      layers.schools.addTo(map);
      layers.trapNet.addTo(map);
      layers.flood100.addTo(map);
      map.setView([35.9132, -79.0558], 12);
      document.getElementById('map-dim').style.display = 'block';
      break;
  }}
}}

// =========================================================================
// Scrollama setup
// =========================================================================
var scroller = scrollama();

scroller.setup({{
  step: '.step',
  offset: 0.5,
  progress: false
}}).onStepEnter(function(response) {{
  // Mark active step
  document.querySelectorAll('.step').forEach(function(el) {{
    el.classList.remove('is-active');
  }});
  response.element.classList.add('is-active');
  handleStep(parseInt(response.element.dataset.step));
}});

// Handle resize
window.addEventListener('resize', scroller.resize);

// Initial state — mark step 0 active and trigger map
document.querySelector('.step[data-step="0"]').classList.add('is-active');
// Leaflet needs the container to be visible and sized before rendering
setTimeout(function() {{
  map.invalidateSize();
  handleStep(0);
}}, 100);
</script>
</body>
</html>"""
    return html


# ---------------------------------------------------------------------------
# Main
# ---------------------------------------------------------------------------
def main():
    parser = argparse.ArgumentParser(description="Generate environmental methodology scrollytelling page")
    parser.add_argument("--cache-only", action="store_true", help="Only use cached data")
    args = parser.parse_args()

    print("=" * 60)
    print("Environmental Analysis Scrollytelling Page Generator")
    print("=" * 60)

    ASSETS_MAPS.mkdir(parents=True, exist_ok=True)

    # 1. Load schools
    _progress("Loading school locations ...")
    schools = load_schools()
    glenwood = get_glenwood(schools)
    bbox = get_glenwood_bbox(glenwood)
    _progress(f"Glenwood at ({glenwood['lat']:.4f}, {glenwood['lon']:.4f})")

    # Schools as GeoJSON
    schools_gdf = gpd.GeoDataFrame(
        schools,
        geometry=gpd.points_from_xy(schools["lon"], schools["lat"]),
        crs=CRS_WGS84,
    )
    schools_json = gdf_to_geojson_str(schools_gdf, properties=["school"])

    # 2. District boundary
    _progress("Loading district boundary ...")
    district = load_district_boundary()
    district_json = gdf_to_geojson_str(district, simplify_m=50)

    # 3. Roads near Glenwood
    _progress("Loading roads near Glenwood ...")
    roads = load_roads_near_glenwood(bbox)
    roads_json = roads_to_geojson_str(roads)

    # 4. AADT stations
    _progress("Loading AADT stations ...")
    aadt = load_aadt_near_glenwood(glenwood)
    aadt_json = gdf_to_geojson_str(aadt, properties=["aadt", "location_desc", "aadt_year"])

    # 5. TRAP grids
    _progress("Loading TRAP grids ...")
    if not TRAP_GRIDS_CACHE.exists():
        raise FileNotFoundError(
            f"TRAP grids cache not found at {TRAP_GRIDS_CACHE}. "
            "Run environmental_map.py first."
        )
    trap_data = np.load(TRAP_GRIDS_CACHE)
    raw_grid = trap_data["raw_grid"]
    net_grid = trap_data["net_grid"]
    trap_bounds_full = tuple(trap_data["bounds"])
    _progress(f"Full TRAP grid: {raw_grid.shape}")

    # Crop to Glenwood area
    raw_crop, raw_bounds = crop_grid(raw_grid, trap_bounds_full, bbox)
    net_crop, net_bounds = crop_grid(net_grid, trap_bounds_full, bbox)
    trap_raw_b64 = grid_to_base64_png(raw_crop, "YlOrRd")
    trap_net_b64 = grid_to_base64_png(net_crop, "YlOrRd")

    # 6. LULC
    _progress("Loading ESA WorldCover ...")
    lulc_b64, lulc_bounds = load_lulc_near_glenwood(bbox)
    if lulc_bounds is None:
        lulc_bounds = (0, 0, 0, 0)
        lulc_b64 = ""

    # 7. UHI grid
    _progress("Loading UHI grid ...")
    if not UHI_GRID_CACHE.exists():
        raise FileNotFoundError(
            f"UHI grid cache not found at {UHI_GRID_CACHE}. "
            "Run environmental_map.py first."
        )
    uhi_data = np.load(UHI_GRID_CACHE)
    uhi_grid = uhi_data["uhi_grid"]
    uhi_bounds_full = tuple(uhi_data["bounds"])
    uhi_crop, uhi_crop_bounds = crop_grid(uhi_grid, uhi_bounds_full, bbox)
    uhi_b64 = grid_to_base64_png(uhi_crop, "RdYlBu_r", vmin=0, vmax=100)

    # 8. Flood zones
    _progress("Loading flood zones ...")
    flood_100, flood_500 = load_flood_near_glenwood(bbox)
    flood_100_json = gdf_to_geojson_str(flood_100, properties=["FLD_ZONE"], simplify_m=10)
    flood_500_json = gdf_to_geojson_str(flood_500, properties=["FLD_ZONE", "ZONE_SUBTY"], simplify_m=10)

    # 9. School property
    _progress("Loading school property ...")
    school_prop = load_school_property_glenwood()
    school_property_json = gdf_to_geojson_str(school_prop, properties=["school_name"])

    # 10. Build HTML
    _progress("Building HTML ...")
    data = {
        "schools_json": schools_json,
        "district_json": district_json,
        "roads_json": roads_json,
        "aadt_json": aadt_json,
        "glenwood": glenwood,
        "trap_raw_b64": trap_raw_b64,
        "trap_net_b64": trap_net_b64,
        "trap_bounds": raw_bounds,
        "lulc_b64": lulc_b64,
        "lulc_bounds": lulc_bounds,
        "uhi_b64": uhi_b64,
        "uhi_bounds": uhi_crop_bounds,
        "flood_100_json": flood_100_json,
        "flood_500_json": flood_500_json,
        "school_property_json": school_property_json,
    }
    html = build_html(data)

    OUTPUT_HTML.write_text(html, encoding="utf-8")
    size_kb = OUTPUT_HTML.stat().st_size / 1024
    _progress(f"Written {OUTPUT_HTML} ({size_kb:.0f} KB)")
    print(f"\nDone! Open {OUTPUT_HTML} in a browser.")


if __name__ == "__main__":
    main()
