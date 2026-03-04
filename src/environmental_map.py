"""
Consolidated Environmental Analysis Map for CHCCS Elementary Schools

Combines traffic-related air pollution (TRAP), FEMA flood plains, tree canopy,
and an Urban Heat Island (UHI) proxy into a single interactive HTML map with
toggleable layers.

UHI proxy uses ESA WorldCover land cover classes as thermal contributors:
- Built-up surfaces contribute heat (impervious)
- Tree cover provides cooling (evapotranspiration + shading)
- Water bodies buffer temperatures
This is a PROXY based on land cover, NOT measured surface temperature.

Literature basis for UHI weights:
- Oke, T. R. (1982). The energetic basis of the urban heat island.
- Stewart, I. D. & Oke, T. R. (2012). Local Climate Zones for urban
  temperature studies. Bull. Amer. Meteor. Soc.

Outputs:
- assets/maps/chccs_environmental_analysis.html
- data/processed/uhi_proxy_scores.csv
- data/cache/trap_grids.npz (cached TRAP grids)
- data/cache/uhi_grid.npz (cached UHI grid)

Usage:
    python src/environmental_map.py [--cache-only] [--grid-resolution N]
"""

import argparse
import sys
import warnings
from pathlib import Path

import folium
import geopandas as gpd
import matplotlib.colors as mcolors
import matplotlib.pyplot as plt
import numpy as np
import pandas as pd
import rasterio
from pyproj import Transformer
from shapely.geometry import Point, box

warnings.filterwarnings("ignore", category=FutureWarning)

# ---------------------------------------------------------------------------
# Paths
# ---------------------------------------------------------------------------
PROJECT_ROOT = Path(__file__).parent.parent
DATA_PROCESSED = PROJECT_ROOT / "data" / "processed"
DATA_CACHE = PROJECT_ROOT / "data" / "cache"
ASSETS_MAPS = PROJECT_ROOT / "assets" / "maps"

SCHOOL_CSV = DATA_CACHE / "nces_school_locations.csv"
LULC_CACHE = DATA_CACHE / "esa_worldcover_orange_county.tif"
TRAP_GRIDS_CACHE = DATA_CACHE / "trap_grids.npz"
UHI_GRID_CACHE = DATA_CACHE / "uhi_grid.npz"
UHI_SCORES_CSV = DATA_PROCESSED / "uhi_proxy_scores.csv"
OUTPUT_MAP = ASSETS_MAPS / "chccs_environmental_analysis.html"

# CRS
CRS_WGS84 = "EPSG:4326"
CRS_UTM17N = "EPSG:32617"

# Chapel Hill center for maps
CHAPEL_HILL_CENTER = [35.9132, -79.0558]

# ESA WorldCover V2 2021 class codes
TREE_CLASS = 10

# Analysis radii (meters)
RADII = [500, 1000]

# ---------------------------------------------------------------------------
# UHI Proxy Weights (literature-based)
# ---------------------------------------------------------------------------
# Thermal contribution weights by ESA WorldCover land cover class.
# Positive = heat contributor, Negative = cooling effect.
# Reference: Oke (1982), Stewart & Oke (2012)
UHI_WEIGHTS = {
    10: -0.60,   # Tree cover — cooling via evapotranspiration + shading
    20: -0.30,   # Shrubland — partial cooling
    30: -0.10,   # Herbaceous vegetation — minimal cooling
    40: -0.05,   # Cropland — minimal cooling
    50: +1.00,   # Built-up — reference heating class (impervious surfaces)
    60: +0.40,   # Bare/sparse vegetation — heat absorption
    80: -0.50,   # Permanent water bodies — thermal buffering
    90: -0.40,   # Herbaceous wetland — cooling
    95: -0.40,   # Mangroves / woody wetland — cooling
}

# UHI normalization bounds (maps raw weighted sum to 0-100 scale)
UHI_WEIGHT_MIN = -0.60   # Coolest possible (100% tree cover)
UHI_WEIGHT_MAX = +1.00   # Hottest possible (100% built-up)

# Road styling (subset for display — tertiary and above)
DISPLAY_ROAD_CLASSES = {
    "motorway", "motorway_link",
    "trunk", "trunk_link",
    "primary", "primary_link",
    "secondary", "secondary_link",
    "tertiary", "tertiary_link",
}

ROAD_COLORS = {
    "motorway": "#e41a1c", "motorway_link": "#e41a1c",
    "trunk": "#ff7f00", "trunk_link": "#ff7f00",
    "primary": "#377eb8", "primary_link": "#377eb8",
    "secondary": "#4daf4a", "secondary_link": "#4daf4a",
    "tertiary": "#984ea3", "tertiary_link": "#984ea3",
}

ROAD_LINE_WIDTHS = {
    "motorway": 4, "motorway_link": 3,
    "trunk": 3.5, "trunk_link": 2.5,
    "primary": 3, "primary_link": 2,
    "secondary": 2.5, "secondary_link": 1.5,
    "tertiary": 2, "tertiary_link": 1.5,
}


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------
def _progress(msg: str):
    """Print a progress message."""
    print(f"  ... {msg}")


def _score_to_color(normalized_score: float) -> str:
    """Convert a normalized score (0-100) to a YlOrRd hex color."""
    cmap = plt.get_cmap("YlOrRd")
    val = max(0.0, min(1.0, normalized_score / 100.0))
    return mcolors.rgb2hex(cmap(val))


def _uhi_to_color(normalized_score: float) -> str:
    """Convert UHI score (0-100) to a RdYlBu_r hex color."""
    cmap = plt.get_cmap("RdYlBu_r")
    val = max(0.0, min(1.0, normalized_score / 100.0))
    return mcolors.rgb2hex(cmap(val))


def ensure_directories():
    """Create output directories if they don't exist."""
    for d in [DATA_PROCESSED, DATA_CACHE, ASSETS_MAPS]:
        d.mkdir(parents=True, exist_ok=True)


# ---------------------------------------------------------------------------
# Grid caching (TRAP grids)
# ---------------------------------------------------------------------------
def save_grids(raw_grid, net_grid, bounds, path):
    """Save TRAP grids as compressed numpy archive."""
    np.savez_compressed(
        path,
        raw_grid=raw_grid,
        net_grid=net_grid,
        bounds=np.array(bounds),
    )
    _progress(f"Cached TRAP grids to {path}")


def load_grids(path):
    """Load cached TRAP grids. Returns (raw_grid, net_grid, bounds) or None."""
    if not path.exists():
        return None
    data = np.load(path)
    raw_grid = data["raw_grid"]
    net_grid = data["net_grid"]
    bounds = tuple(data["bounds"])
    _progress(f"Loaded cached TRAP grids from {path} ({raw_grid.shape})")
    return raw_grid, net_grid, bounds


# ---------------------------------------------------------------------------
# UHI proxy computation
# ---------------------------------------------------------------------------
def calculate_uhi_grid(lulc_path, grid_bbox_wgs, resolution=100):
    """
    Compute a UHI proxy grid from ESA WorldCover land cover classes.

    For each grid cell (~100m), reads underlying 10m LULC pixels and computes
    an area-weighted sum of UHI thermal weights. Result is normalized to 0-100
    where 0 = coolest (all tree cover) and 100 = hottest (all built-up).

    Returns: (uhi_grid, bounds_wgs84) matching the TRAP grid structure.
    """
    # Check for cached grid
    if UHI_GRID_CACHE.exists():
        data = np.load(UHI_GRID_CACHE)
        uhi_grid = data["uhi_grid"]
        bounds = tuple(data["bounds"])
        _progress(f"Loaded cached UHI grid from {UHI_GRID_CACHE} ({uhi_grid.shape})")
        return uhi_grid, bounds

    _progress(f"Computing UHI proxy grid at {resolution}m resolution ...")

    to_utm = Transformer.from_crs(CRS_WGS84, CRS_UTM17N, always_xy=True)

    # Compute grid dimensions from bbox
    sw_u = to_utm.transform(grid_bbox_wgs[0], grid_bbox_wgs[1])
    ne_u = to_utm.transform(grid_bbox_wgs[2], grid_bbox_wgs[3])
    width_m = ne_u[0] - sw_u[0]
    height_m = ne_u[1] - sw_u[1]

    nx = int(round(width_m / resolution))
    ny = int(round(height_m / resolution))
    _progress(f"UHI grid size: {nx} x {ny} = {nx * ny:,} cells")

    # Build WGS84 grid cell centers
    dx = (grid_bbox_wgs[2] - grid_bbox_wgs[0]) / nx
    dy = (grid_bbox_wgs[3] - grid_bbox_wgs[1]) / ny
    xs_wgs = np.linspace(grid_bbox_wgs[0] + dx / 2, grid_bbox_wgs[2] - dx / 2, nx)
    ys_wgs = np.linspace(grid_bbox_wgs[3] - dy / 2, grid_bbox_wgs[1] + dy / 2, ny)

    bounds_wgs84 = (grid_bbox_wgs[0], grid_bbox_wgs[1],
                    grid_bbox_wgs[2], grid_bbox_wgs[3])

    uhi_grid = np.full((ny, nx), np.nan, dtype=np.float32)

    with rasterio.open(lulc_path) as src:
        to_raster = Transformer.from_crs(CRS_WGS84, src.crs, always_xy=True)
        half = resolution  # buffer in raster CRS units (meters)

        report_interval = max(1, ny // 10)

        for j in range(ny):
            if j % report_interval == 0:
                _progress(f"  UHI row {j}/{ny} ({j / ny * 100:.0f}%)")
            row_lat = ys_wgs[j]
            rx_arr, ry_arr = to_raster.transform(xs_wgs, np.full(nx, row_lat))

            for i in range(nx):
                rx, ry = rx_arr[i], ry_arr[i]
                try:
                    window = src.window(
                        rx - half, ry - half,
                        rx + half, ry + half,
                    )
                    data = src.read(1, window=window)
                    if data.size == 0:
                        continue

                    valid_mask = data > 0
                    valid_count = np.sum(valid_mask)
                    if valid_count == 0:
                        continue

                    # Compute area-weighted UHI index
                    weighted_sum = 0.0
                    for class_code, weight in UHI_WEIGHTS.items():
                        class_count = np.sum(data == class_code)
                        if class_count > 0:
                            weighted_sum += weight * class_count

                    raw_uhi = weighted_sum / valid_count

                    # Normalize to 0-100
                    uhi_norm = (raw_uhi - UHI_WEIGHT_MIN) / (UHI_WEIGHT_MAX - UHI_WEIGHT_MIN) * 100
                    uhi_grid[j, i] = max(0.0, min(100.0, uhi_norm))

                except Exception:
                    pass

    # Replace NaN with 0 for areas outside LULC coverage
    uhi_grid = np.nan_to_num(uhi_grid, nan=0.0)

    # Cache
    np.savez_compressed(
        UHI_GRID_CACHE,
        uhi_grid=uhi_grid,
        bounds=np.array(bounds_wgs84),
    )
    _progress(f"Cached UHI grid to {UHI_GRID_CACHE}")

    return uhi_grid, bounds_wgs84


def calculate_uhi_school_scores(schools_df, lulc_path, radii=None):
    """
    Compute UHI proxy scores for each school at specified radii.

    Uses the same windowed-raster pattern as calculate_tree_canopy() in
    road_pollution.py. For each school buffer: reads pixels, computes
    weighted UHI index, normalizes to 0-100.

    Returns DataFrame with school name, lat, lon, and UHI scores.
    """
    if radii is None:
        radii = RADII

    results = []

    with rasterio.open(lulc_path) as src:
        to_raster = Transformer.from_crs(CRS_WGS84, src.crs, always_xy=True)

        for _, school in schools_df.iterrows():
            name = school["school"]
            row_data = {"school": name, "lat": school["lat"], "lon": school["lon"]}

            for radius in radii:
                rx, ry = to_raster.transform(school["lon"], school["lat"])
                try:
                    window = src.window(
                        rx - radius, ry - radius,
                        rx + radius, ry + radius,
                    )
                    data = src.read(1, window=window)

                    if data.size == 0:
                        row_data[f"uhi_{radius}m"] = 0.0
                        continue

                    valid_mask = data > 0
                    valid_count = np.sum(valid_mask)
                    if valid_count == 0:
                        row_data[f"uhi_{radius}m"] = 0.0
                        continue

                    weighted_sum = 0.0
                    for class_code, weight in UHI_WEIGHTS.items():
                        class_count = np.sum(data == class_code)
                        if class_count > 0:
                            weighted_sum += weight * class_count

                    raw_uhi = weighted_sum / valid_count
                    uhi_norm = (raw_uhi - UHI_WEIGHT_MIN) / (UHI_WEIGHT_MAX - UHI_WEIGHT_MIN) * 100
                    row_data[f"uhi_{radius}m"] = round(max(0.0, min(100.0, uhi_norm)), 1)

                except Exception:
                    row_data[f"uhi_{radius}m"] = 0.0

            results.append(row_data)

    df = pd.DataFrame(results)

    # Add ranks
    for radius in radii:
        col = f"uhi_{radius}m"
        df[f"rank_uhi_{radius}m"] = df[col].rank(ascending=False, method="min").astype(int)

    return df


# ---------------------------------------------------------------------------
# Map layer builders
# ---------------------------------------------------------------------------
def _grid_to_rgba(grid, colormap="YlOrRd", alpha_base=120, alpha_scale=80,
                   vmin=None, vmax=None, district_mask=None):
    """Convert a numpy grid to an RGBA image array using matplotlib colormaps.

    Parameters
    ----------
    grid : numpy array
        2D grid of values.
    colormap : str
        Matplotlib colormap name (e.g. "YlOrRd", "RdYlBu_r").
    alpha_base : int
        Base alpha for non-zero cells.
    alpha_scale : int
        Additional alpha scaled by value.
    vmin, vmax : float or None
        Normalization range. If None, uses 5th/95th percentile of nonzero values.
    district_mask : numpy array or None
        Boolean mask (True = inside district). Cells outside are transparent.

    Returns
    -------
    rgba : numpy uint8 array (ny, nx, 4)
    gmax : float — the upper normalization bound used
    """
    # Apply district mask: zero out cells outside the district
    if district_mask is not None:
        grid = grid.copy()
        grid[~district_mask] = 0

    # Determine normalization range
    nonzero = grid[grid > 0]
    if vmin is None or vmax is None:
        if len(nonzero) > 0:
            auto_vmin = np.percentile(nonzero, 5)
            auto_vmax = np.percentile(nonzero, 95)
        else:
            auto_vmin, auto_vmax = 0.0, 1.0
        if vmin is None:
            vmin = auto_vmin
        if vmax is None:
            vmax = auto_vmax

    gmax = vmax

    norm = mcolors.Normalize(vmin=vmin, vmax=vmax, clip=True)
    cmap = plt.get_cmap(colormap)

    # Vectorized colormap application
    normalized = norm(grid)
    mapped = cmap(normalized)  # (ny, nx, 4) float 0-1

    ny, nx = grid.shape
    rgba = np.zeros((ny, nx, 4), dtype=np.uint8)

    # Set RGB from colormap
    rgba[..., :3] = (mapped[..., :3] * 255).astype(np.uint8)

    # Set alpha: zero for near-zero cells, scaled for others
    active = grid > 0.001
    alpha_vals = np.where(
        active,
        np.clip(alpha_base + alpha_scale * normalized, 0, 255).astype(np.uint8),
        0,
    )
    rgba[..., 3] = alpha_vals

    return rgba, gmax


def _rgba_to_image_url(rgba):
    """Convert RGBA array to base64 PNG data URL."""
    import base64
    import io
    from PIL import Image

    img = Image.fromarray(rgba, "RGBA")
    buf = io.BytesIO()
    img.save(buf, format="PNG")
    buf.seek(0)
    b64 = base64.b64encode(buf.read()).decode()
    return f"data:image/png;base64,{b64}"


def _create_district_mask(bounds_wgs84, grid_shape, district_gdf, buffer_m=200):
    """Build a boolean mask for grid cells inside the district boundary.

    Parameters
    ----------
    bounds_wgs84 : tuple
        (west, south, east, north) in WGS84.
    grid_shape : tuple
        (ny, nx) grid dimensions.
    district_gdf : GeoDataFrame
        District boundary polygon(s).
    buffer_m : float
        Buffer distance in meters applied in UTM for edge smoothing.

    Returns
    -------
    mask : numpy bool array (ny, nx), True = inside district
    """
    from shapely.prepared import prep

    ny, nx = grid_shape
    west, south, east, north = bounds_wgs84

    # Buffer the district in UTM, then reproject back to WGS84 for grid queries
    district_utm = district_gdf.to_crs(CRS_UTM17N)
    buffered_utm = district_utm.geometry.buffer(buffer_m)
    buffered_wgs = (
        gpd.GeoSeries(buffered_utm, crs=CRS_UTM17N)
        .to_crs(CRS_WGS84)
    )
    district_poly = buffered_wgs.union_all()
    prepared = prep(district_poly)

    # Build grid cell centers in WGS84
    xs = np.linspace(west, east, nx)
    ys = np.linspace(north, south, ny)

    mask = np.zeros((ny, nx), dtype=bool)
    for j in range(ny):
        points = [Point(xs[i], ys[j]) for i in range(nx)]
        mask[j, :] = [prepared.contains(p) for p in points]

    return mask


def _add_raster_layer(map_obj, grid, bounds_wgs84, layer_name, colormap,
                      show=True, opacity=0.7, district_mask=None,
                      vmin=None, vmax=None, add_to_map=True):
    """Add a raster overlay as a toggleable FeatureGroup.

    If add_to_map=False, returns the FeatureGroup without adding to map.
    """
    rgba, gmax = _grid_to_rgba(grid, colormap=colormap,
                                district_mask=district_mask,
                                vmin=vmin, vmax=vmax)
    img_url = _rgba_to_image_url(rgba)

    west, south, east, north = bounds_wgs84
    group = folium.FeatureGroup(name=layer_name, show=show)
    folium.raster_layers.ImageOverlay(
        image=img_url,
        bounds=[[south, west], [north, east]],
        opacity=opacity,
    ).add_to(group)
    if add_to_map:
        group.add_to(map_obj)
    return group, gmax


def _add_tree_canopy_layer(map_obj, lulc_path, show=False, district_gdf=None):
    """Add tree canopy overlay from ESA WorldCover, clipped to district."""
    group = folium.FeatureGroup(name="Tree Canopy (ESA WorldCover)", show=show)
    try:
        with rasterio.open(lulc_path) as src:
            to_wgs = Transformer.from_crs(src.crs, CRS_WGS84, always_xy=True)
            left, bottom, right, top = src.bounds
            w_lon, s_lat = to_wgs.transform(left, bottom)
            e_lon, n_lat = to_wgs.transform(right, top)

            step = max(1, max(src.height, src.width) // 2000)
            data = src.read(1, out_shape=(src.height // step, src.width // step))
            h, w = data.shape
            rgba = np.zeros((h, w, 4), dtype=np.uint8)
            tree_mask = data == TREE_CLASS
            rgba[tree_mask] = [34, 139, 34, 160]

            # Clip to district boundary if provided
            if district_gdf is not None:
                canopy_mask = _create_district_mask(
                    (w_lon, s_lat, e_lon, n_lat), (h, w), district_gdf,
                    buffer_m=200,
                )
                rgba[~canopy_mask] = [0, 0, 0, 0]

            img_url = _rgba_to_image_url(rgba)
            folium.raster_layers.ImageOverlay(
                image=img_url,
                bounds=[[s_lat, w_lon], [n_lat, e_lon]],
                opacity=0.6,
            ).add_to(group)
    except Exception as e:
        _progress(f"Warning: Could not add canopy layer: {e}")

    group.add_to(map_obj)


def _add_roads_layer(map_obj, roads_gdf, district_gdf=None):
    """Add road network as vector PolyLines (tertiary+ only)."""
    group = folium.FeatureGroup(name="Road Network (tertiary+)", show=True)

    # Filter to display classes only
    display_roads = roads_gdf[roads_gdf["highway"].isin(DISPLAY_ROAD_CLASSES)].copy()

    # Clip to district boundary with 2km buffer if provided
    if district_gdf is not None:
        district_utm = district_gdf.to_crs(CRS_UTM17N)
        buffered = district_utm.geometry.buffer(2000)
        clip_gdf = gpd.GeoDataFrame(geometry=buffered, crs=CRS_UTM17N).to_crs(
            display_roads.crs if display_roads.crs else CRS_WGS84
        )
        display_roads = gpd.clip(display_roads, clip_gdf)

    _progress(f"Adding {len(display_roads)} road segments to map ...")

    for _, row in display_roads.iterrows():
        hw = row["highway"]
        color = ROAD_COLORS.get(hw, "#666666")
        weight = ROAD_LINE_WIDTHS.get(hw, 1)
        name = row.get("name", "")
        if isinstance(name, list):
            name = name[0] if name else ""
        if pd.isna(name):
            name = ""

        geom = row.geometry
        if geom is None or geom.is_empty:
            continue

        lines = []
        if geom.geom_type == "MultiLineString":
            lines = list(geom.geoms)
        else:
            lines = [geom]

        for line in lines:
            coords_ll = [(c[1], c[0]) for c in line.coords]
            tooltip = f"{hw}: {name}" if name else hw
            folium.PolyLine(
                coords_ll,
                color=color,
                weight=weight,
                opacity=0.7,
                tooltip=tooltip,
            ).add_to(group)

    group.add_to(map_obj)


def _add_flood_layer(map_obj, flood_100, flood_500, school_props, overlaps, show=True, add_to_control=True):
    """Add flood plains as a toggleable layer with school property overlaps."""
    import json

    # If add_to_control=False, add elements directly to map (no toggle in LayerControl)
    if add_to_control:
        group = folium.FeatureGroup(name="FEMA Flood Plains", show=show)
    else:
        group = map_obj  # Add directly to map

    # Simplify geometries for smaller GeoJSON
    simplify_tol = 0.0001

    # 500-year zones
    if len(flood_500) > 0:
        f500 = flood_500.copy()
        f500["geometry"] = f500.geometry.simplify(simplify_tol)
        for _, row in f500.iterrows():
            geom = row.geometry
            if geom.is_empty:
                continue
            geojson = json.loads(gpd.GeoSeries([geom], crs=CRS_WGS84).to_json())
            folium.GeoJson(
                geojson,
                style_function=lambda x: {
                    "fillColor": "#bdd7e7",
                    "color": "#6baed6",
                    "weight": 0.5,
                    "fillOpacity": 0.25,
                },
                tooltip="500-year flood zone",
            ).add_to(group)

    # 100-year zones
    if len(flood_100) > 0:
        f100 = flood_100.copy()
        f100["geometry"] = f100.geometry.simplify(simplify_tol)
        for _, row in f100.iterrows():
            geom = row.geometry
            if geom.is_empty:
                continue
            geojson = json.loads(gpd.GeoSeries([geom], crs=CRS_WGS84).to_json())
            folium.GeoJson(
                geojson,
                style_function=lambda x: {
                    "fillColor": "#6baed6",
                    "color": "#2171b5",
                    "weight": 0.5,
                    "fillOpacity": 0.4,
                },
                tooltip="100-year flood zone",
            ).add_to(group)

    # Overlap polygons (red)
    if len(overlaps) > 0:
        for _, row in overlaps.iterrows():
            geom = row.geometry
            if geom.is_empty:
                continue
            geojson = json.loads(gpd.GeoSeries([geom], crs=CRS_WGS84).to_json())
            popup_html = (
                f"<b>{row['school_name']}</b><br>"
                f"{row['flood_type']} overlap<br>"
                f"{row['overlap_acres']:.2f} acres ({row['overlap_pct']:.1f}%)"
            )
            folium.GeoJson(
                geojson,
                style_function=lambda x: {
                    "fillColor": "#e6031b",
                    "color": "#e6031b",
                    "weight": 1,
                    "fillOpacity": 0.6,
                },
                tooltip=f"{row['school_name']}: {row['flood_type']} overlap",
                popup=folium.Popup(popup_html, max_width=250),
            ).add_to(group)

    group.add_to(map_obj)


def _add_school_properties_layer(map_obj, school_props, all_metrics, add_to_control=True):
    """Add school property polygons with rich popups aggregating all metrics."""
    import json

    # If add_to_control=False, add elements directly to map (no toggle in LayerControl)
    if add_to_control:
        group = folium.FeatureGroup(name="School Properties", show=True)
    else:
        group = map_obj  # Add directly to map

    for _, row in school_props.iterrows():
        name = row["school_name"]
        geom = row.geometry
        if geom.is_empty:
            continue

        geojson = json.loads(gpd.GeoSeries([geom], crs=CRS_WGS84).to_json())

        # Build rich popup from all metrics
        popup_lines = [f"<b>{name}</b>"]
        popup_lines.append(f"<hr style='margin:4px 0;'>")

        # Property info
        if "CALC_ACRES" in row.index and not pd.isna(row["CALC_ACRES"]):
            popup_lines.append(f"<b>Property:</b> {row['CALC_ACRES']:.1f} acres")

        # Look up metrics for this school
        metrics = all_metrics.get(name, {})

        # Flood
        flood_info = metrics.get("flood", [])
        if flood_info:
            for fi in flood_info:
                popup_lines.append(
                    f"<b>{fi['type']}:</b> {fi['acres']:.2f} ac ({fi['pct']:.1f}%)"
                )
        else:
            popup_lines.append("<b>Flood overlap:</b> None")

        # TRAP
        if "raw_500m" in metrics:
            popup_lines.append(
                f"<b>TRAP Raw (500m):</b> {metrics['raw_500m']:.2f} "
                f"(rank #{metrics.get('rank_raw_500m', 'N/A')})"
            )
        if "net_500m" in metrics:
            popup_lines.append(
                f"<b>TRAP Net (500m):</b> {metrics['net_500m']:.2f}"
            )

        # Tree canopy
        if "canopy_500m" in metrics:
            popup_lines.append(
                f"<b>Tree canopy (500m):</b> {metrics['canopy_500m'] * 100:.1f}%"
            )

        # UHI
        if "uhi_500m" in metrics:
            popup_lines.append(
                f"<b>UHI proxy (500m):</b> {metrics['uhi_500m']:.1f} "
                f"(rank #{metrics.get('rank_uhi_500m', 'N/A')})"
            )

        popup_html = "<br>".join(popup_lines)

        folium.GeoJson(
            geojson,
            style_function=lambda x: {
                "fillColor": "#d4edda",
                "color": "#155724",
                "weight": 1.5,
                "fillOpacity": 0.4,
            },
            tooltip=name,
            popup=folium.Popup(popup_html, max_width=300),
        ).add_to(group)

    # Only call add_to if we created a separate FeatureGroup
    if add_to_control:
        group.add_to(map_obj)


def _add_school_markers_for_layer(map_obj, schools_df, score_col, norm_col,
                                  color_func, layer_name, show=True, add_to_map=True):
    """Add school CircleMarkers color-coded by a specific metric.

    If add_to_map=False, returns the FeatureGroup without adding to map.
    """
    group = folium.FeatureGroup(name=f"{layer_name} — Schools", show=show)

    for _, row in schools_df.iterrows():
        score = row.get(norm_col, row.get(score_col, 50))
        color_hex = color_func(score)

        popup_html = (
            f"<b>{row['school']}</b><br>"
            f"<b>{layer_name}:</b> {row.get(score_col, 'N/A')}"
        )

        folium.CircleMarker(
            location=[row["lat"], row["lon"]],
            radius=6,
            color="#333333",
            weight=2,
            fillColor=color_hex,
            fillOpacity=1.0,
            popup=folium.Popup(popup_html, max_width=250),
            tooltip=f"{row['school']}",
        ).add_to(group)

    if add_to_map:
        group.add_to(map_obj)
    return group


# ---------------------------------------------------------------------------
# Legend HTML
# ---------------------------------------------------------------------------
def _build_legend_html_only():
    """Build legend HTML divs only (JavaScript toggle is handled in control panel)."""
    return """
<style>
  .env-legend {
    position: fixed; bottom: 30px; left: 10px; z-index: 1000;
    background: white; padding: 10px 14px; border-radius: 5px;
    box-shadow: 2px 2px 5px rgba(0,0,0,0.3); font-size: 12px;
    max-width: 220px; display: none;
  }
  .env-legend .legend-title { font-weight: bold; margin-bottom: 4px; }
  .env-legend .legend-item { margin: 2px 0; }
  .legend-bar {
    display: inline-block; width: 60px; height: 12px;
    vertical-align: middle; border: 1px solid #ccc;
  }
  .legend-swatch {
    display: inline-block; width: 14px; height: 14px;
    vertical-align: middle; border: 1px solid #ccc; margin-right: 4px;
  }
</style>

<div class="env-legend" id="legend-trap">
  <div class="legend-title">TRAP Exposure Index</div>
  <div class="legend-item">
    <span class="legend-bar" style="background: linear-gradient(to right, #ffffb2, #fd8d3c, #bd0026);"></span>
    Low &rarr; High
  </div>
  <div class="legend-item" style="margin-top:4px; font-size:11px; color:#666;">
    Relative index, not absolute health risk
  </div>
</div>

<div class="env-legend" id="legend-flood">
  <div class="legend-title">FEMA Flood Zones</div>
  <div class="legend-item"><span class="legend-swatch" style="background:#6baed6;opacity:0.6;"></span> 100-year</div>
  <div class="legend-item"><span class="legend-swatch" style="background:#bdd7e7;opacity:0.4;"></span> 500-year</div>
  <div class="legend-item"><span class="legend-swatch" style="background:#e6031b;opacity:0.7;"></span> School overlap</div>
</div>

<div class="env-legend" id="legend-tree">
  <div class="legend-title">Tree Canopy</div>
  <div class="legend-item"><span class="legend-swatch" style="background:#228b22;opacity:0.6;"></span> Tree cover (ESA 10m)</div>
  <div class="legend-item" style="margin-top:4px; font-size:11px; color:#666;">
    ESA WorldCover V2 2021
  </div>
</div>

<div class="env-legend" id="legend-uhi">
  <div class="legend-title">UHI Proxy Index</div>
  <div class="legend-item">
    <span class="legend-bar" style="background: linear-gradient(to right, #4575b4, #ffffbf, #d73027);"></span>
    Cool &rarr; Hot
  </div>
  <div class="legend-item" style="margin-top:4px; font-size:11px; color:#666;">
    Land-cover proxy, NOT measured temperature
  </div>
</div>
"""


def _build_legend_js():
    """Build JavaScript for dynamic legend switching based on active layers (deprecated)."""
    return _build_legend_html_only()


# ---------------------------------------------------------------------------
# Map assembly
# ---------------------------------------------------------------------------
def create_environmental_map(
    trap_scores,
    raw_grid, net_grid, trap_bounds,
    lulc_path,
    roads_gdf,
    school_props,
    flood_100, flood_500, overlaps,
    uhi_grid, uhi_bounds,
    uhi_scores,
    district_gdf=None,
    schools_df=None,
):
    """
    Assemble the consolidated environmental analysis map.

    Layer order (bottom to top):
    0. District boundary (always on)
    1. School properties (always on)
    2. Road network (always on, tertiary+)
    3. Flood plains (toggle, off by default)
    4. Raw air pollution raster (toggle, on by default)
    5. Tree cover (toggle, off by default)
    6. Net air pollution raster (toggle, off by default)
    7. UHI proxy raster (toggle, off by default)
    8. Schools — fixed blue markers (always on)
    + Metric-colored school markers per raster layer
    """
    _progress("Assembling consolidated environmental map ...")
    m = folium.Map(
        location=CHAPEL_HILL_CENTER, zoom_start=12,
        tiles="cartodbpositron",
    )

    # Build district mask for raster clipping
    district_mask = None
    if district_gdf is not None:
        _progress("Building district mask for raster clipping ...")
        district_mask = _create_district_mask(
            trap_bounds, raw_grid.shape, district_gdf, buffer_m=200,
        )

    # --- Build aggregated metrics dict for school property popups ---
    all_metrics = {}
    for _, row in trap_scores.iterrows():
        name = row["school"]
        all_metrics[name] = {
            "raw_500m": row.get("raw_500m", 0),
            "net_500m": row.get("net_500m", 0),
            "canopy_500m": row.get("canopy_500m", 0),
            "rank_raw_500m": int(row.get("rank_raw_500m", 0)),
        }

    # Merge UHI scores
    for _, row in uhi_scores.iterrows():
        name = row["school"]
        if name in all_metrics:
            all_metrics[name]["uhi_500m"] = row.get("uhi_500m", 0)
            all_metrics[name]["rank_uhi_500m"] = int(row.get("rank_uhi_500m", 0))
        else:
            all_metrics[name] = {
                "uhi_500m": row.get("uhi_500m", 0),
                "rank_uhi_500m": int(row.get("rank_uhi_500m", 0)),
            }

    # Merge flood overlap info
    if len(overlaps) > 0:
        for _, row in overlaps.iterrows():
            name = row["school_name"]
            if name not in all_metrics:
                all_metrics[name] = {}
            if "flood" not in all_metrics[name]:
                all_metrics[name]["flood"] = []
            all_metrics[name]["flood"].append({
                "type": row["flood_type"],
                "acres": row["overlap_acres"],
                "pct": row["overlap_pct"],
            })

    # Layer 0: District boundary (always on, no toggle)
    if district_gdf is not None:
        folium.GeoJson(
            district_gdf.to_crs(CRS_WGS84).__geo_interface__,
            style_function=lambda x: {
                "fillColor": "transparent",
                "color": "#333333",
                "weight": 2,
                "dashArray": "5,5",
            },
        ).add_to(m)

    # Layer 1: School properties (always on, no toggle)
    _add_school_properties_layer(m, school_props, all_metrics, add_to_control=False)

    # Layer 2: Road network (always on, tertiary+)
    _add_roads_layer(m, roads_gdf, district_gdf=district_gdf)

    # Layer 3: Flood plains (toggle, on by default)
    _add_flood_layer(m, flood_100, flood_500, school_props, overlaps, show=True)

    # Layer 4: Tree canopy (separate checkbox toggle, off by default)
    _add_tree_canopy_layer(m, lulc_path, show=False, district_gdf=district_gdf)

    # === Environmental Raster Layers (radio button group - mutually exclusive) ===
    # Create all raster + marker layers but don't add to LayerControl
    # Net Air Pollution is the default selection

    # Raw air pollution raster + markers
    raw_raster_fg, _ = _add_raster_layer(
        m, raw_grid, trap_bounds, "Raw Air Pollution",
        colormap="YlOrRd", show=False, opacity=0.7,
        district_mask=district_mask, add_to_map=False
    )
    raw_raster_fg.add_to(m)
    raw_markers_fg = _add_school_markers_for_layer(
        m, trap_scores, "raw_500m", "raw_norm_500m",
        _score_to_color, "Raw Air Pollution", show=False, add_to_map=False
    )
    raw_markers_fg.add_to(m)

    # Net air pollution raster + markers (DEFAULT)
    net_raster_fg, _ = _add_raster_layer(
        m, net_grid, trap_bounds, "Net Air Pollution",
        colormap="YlOrRd", show=True, opacity=0.7,
        district_mask=district_mask, add_to_map=False
    )
    net_raster_fg.add_to(m)
    net_markers_fg = _add_school_markers_for_layer(
        m, trap_scores, "net_500m", "net_norm_500m",
        _score_to_color, "Net Air Pollution", show=True, add_to_map=False
    )
    net_markers_fg.add_to(m)

    # UHI proxy raster + markers
    uhi_raster_fg, _ = _add_raster_layer(
        m, uhi_grid, uhi_bounds, "UHI Proxy (Land Cover)",
        colormap="RdYlBu_r", show=False, opacity=0.7,
        district_mask=district_mask, vmin=0, vmax=100, add_to_map=False
    )
    uhi_raster_fg.add_to(m)
    uhi_markers_fg = _add_school_markers_for_layer(
        m, uhi_scores, "uhi_500m", "uhi_500m",
        _uhi_to_color, "UHI Proxy (Land Cover)", show=False, add_to_map=False
    )
    uhi_markers_fg.add_to(m)

    # Get JS variable names for custom control
    raw_raster_name = raw_raster_fg.get_name()
    raw_markers_name = raw_markers_fg.get_name()
    net_raster_name = net_raster_fg.get_name()
    net_markers_name = net_markers_fg.get_name()
    uhi_raster_name = uhi_raster_fg.get_name()
    uhi_markers_name = uhi_markers_fg.get_name()

    # Layer control for Flood and Tree Canopy only
    folium.LayerControl(collapsed=False).add_to(m)

    # Banner with FAQ button (matching socioeconomic map style)
    banner_html = """
    <style>
        #env-banner {
            position: fixed; top: 0; left: 0; right: 0; z-index: 1000;
            background: white; padding: 10px 20px;
            border-bottom: 1px solid #dee2e6;
            display: flex; justify-content: center; align-items: center;
            text-align: center;
        }
        #env-banner h1 { margin: 0; font-size: 18px; font-weight: 600; color: #333; }
        #env-banner .subtitle { margin: 2px 0 0 0; font-size: 12px; color: #666; display: inline; }
        .faq-btn {
            display: inline-flex; align-items: center; gap: 3px;
            padding: 2px 8px; background: #2196F3; color: white;
            border: none; border-radius: 3px; font-size: 11px;
            font-weight: bold; cursor: pointer; margin-left: 10px;
            vertical-align: middle;
        }
        .faq-btn:hover { background: #1976D2; }
        .faq-btn .faq-icon { font-size: 13px; }
        .faq-panel {
            display: none; position: fixed; top: 60px; left: 20px; z-index: 1002;
            background: white; padding: 12px 15px; border-radius: 6px;
            box-shadow: 0 4px 12px rgba(0,0,0,0.3); max-width: 400px;
            max-height: 70vh; overflow-y: auto; font-size: 12px; line-height: 1.5;
        }
        .faq-panel.visible { display: block; }
        .faq-panel h5 { margin: 0 0 10px 0; padding-bottom: 6px; border-bottom: 1px solid #eee; font-size: 13px; }
        .faq-panel .faq-item { margin-bottom: 12px; }
        .faq-panel .faq-q { font-weight: bold; color: #333; margin-bottom: 3px; }
        .faq-panel .faq-a { color: #555; }
        .faq-close {
            position: absolute; top: 6px; right: 10px; cursor: pointer;
            font-size: 18px; color: #999; line-height: 1;
        }
        .faq-close:hover { color: #333; }
    </style>
    <div id="env-banner">
        <div>
            <h1>CHCCS Environmental Analysis</h1>
            <p class="subtitle">Screening-level indices for air quality, flood risk, and urban heat
                <button class="faq-btn" onclick="toggleFaqPanel()" title="Click for FAQ">
                    <span class="faq-icon">?</span> Help
                </button>
            </p>
        </div>
    </div>
    <div class="faq-panel" id="faq-panel">
        <span class="faq-close" onclick="toggleFaqPanel()">&times;</span>
        <h5>Frequently Asked Questions</h5>
        <div class="faq-item">
            <div class="faq-q">What is TRAP (Traffic-Related Air Pollution)?</div>
            <div class="faq-a">TRAP is a screening index based on proximity to roads, weighted by estimated traffic volume (AADT).
            Higher values indicate greater potential exposure to vehicle emissions. This is a <b>relative</b> index, not an absolute health risk measurement.</div>
        </div>
        <div class="faq-item">
            <div class="faq-q">What is Raw vs Net Air Pollution?</div>
            <div class="faq-a"><b>Raw:</b> TRAP exposure based only on road proximity and traffic volume.<br>
            <b>Net:</b> Raw TRAP minus a tree canopy mitigation factor (trees absorb pollutants). Net values can be lower where tree cover is high.</div>
        </div>
        <div class="faq-item">
            <div class="faq-q">What is UHI Proxy?</div>
            <div class="faq-a">Urban Heat Island (UHI) Proxy is estimated from land cover classification (ESA WorldCover), NOT from measured temperatures.
            Impervious surfaces (roads, buildings) score higher; tree canopy scores lower. It indicates <b>potential</b> heat exposure, not actual temperature.</div>
        </div>
        <div class="faq-item">
            <div class="faq-q">What do FEMA flood zones mean?</div>
            <div class="faq-a"><b>100-year zone:</b> 1% annual chance of flooding (Zone AE).<br>
            <b>500-year zone:</b> 0.2% annual chance (Zone X shaded).<br>
            Red overlay shows where school property parcels intersect flood zones.</div>
        </div>
        <div class="faq-item">
            <div class="faq-q">Are these absolute risk assessments?</div>
            <div class="faq-a"><b>No.</b> All indices are <b>comparative screening tools</b> for identifying areas of potential concern.
            They should not be interpreted as definitive health or safety risk levels. Professional assessments require site-specific data collection.</div>
        </div>
    </div>
    <script>
        window.toggleFaqPanel = function() {
            var panel = document.getElementById('faq-panel');
            if (panel) panel.classList.toggle('visible');
        };
        document.addEventListener('click', function(e) {
            var panel = document.getElementById('faq-panel');
            var btn = document.querySelector('.faq-btn');
            if (panel && panel.classList.contains('visible') &&
                !panel.contains(e.target) && !btn.contains(e.target)) {
                panel.classList.remove('visible');
            }
        });
    </script>
    """
    m.get_root().html.add_child(folium.Element(banner_html))

    # Custom control panel for environmental layers
    ctrl_panel_html = f"""
    <style>
        #env-ctrl-panel {{
            position: fixed; top: 60px; right: 10px; z-index: 1001;
            width: 200px; background: white; border-radius: 6px;
            box-shadow: 0 2px 8px rgba(0,0,0,0.25); font-size: 12px;
            padding: 10px;
        }}
        #env-ctrl-panel .ctrl-section {{
            margin-bottom: 8px; padding-bottom: 6px;
            border-bottom: 1px solid #eee;
        }}
        #env-ctrl-panel .ctrl-section:last-child {{
            margin-bottom: 0; padding-bottom: 0; border-bottom: none;
        }}
        #env-ctrl-panel b {{
            font-size: 11px; text-transform: uppercase; color: #555;
            letter-spacing: 0.5px;
        }}
        #env-ctrl-panel label {{
            display: block; margin: 3px 0; cursor: pointer; font-size: 11px;
        }}
        #env-ctrl-panel input[type="radio"],
        #env-ctrl-panel input[type="checkbox"] {{
            margin-right: 4px; vertical-align: middle;
        }}
        /* Hide the default Folium LayerControl */
        .leaflet-control-layers {{
            display: none !important;
        }}
    </style>
    <div id="env-ctrl-panel">
        <div class="ctrl-section">
            <b>Environmental Layer</b>
            <label><input type="radio" name="env-layer" value="none"> None</label>
            <label><input type="radio" name="env-layer" value="raw"> Raw Air Pollution</label>
            <label><input type="radio" name="env-layer" value="net" checked> Net Air Pollution</label>
            <label><input type="radio" name="env-layer" value="uhi"> UHI Proxy (Land Cover)</label>
        </div>
        <div class="ctrl-section">
            <b>Additional Layers</b>
            <label><input type="checkbox" id="tree-toggle"> Tree Canopy</label>
        </div>
    </div>
    <script>
    document.addEventListener('DOMContentLoaded', function() {{
        // Get map reference
        var mapEl = document.querySelector('.folium-map');
        if (!mapEl) return;
        var map = window[mapEl.id] || null;
        if (!map) {{
            for (var key in window) {{
                if (window[key] instanceof L.Map) {{ map = window[key]; break; }}
            }}
        }}
        if (!map) return;

        // Raster + marker layer references
        var rawRaster = {raw_raster_name};
        var rawMarkers = {raw_markers_name};
        var netRaster = {net_raster_name};
        var netMarkers = {net_markers_name};
        var uhiRaster = {uhi_raster_name};
        var uhiMarkers = {uhi_markers_name};

        var rasterLayers = {{
            'raw': [rawRaster, rawMarkers],
            'net': [netRaster, netMarkers],
            'uhi': [uhiRaster, uhiMarkers]
        }};

        var legendMap = {{
            'raw': 'legend-trap',
            'net': 'legend-trap',
            'uhi': 'legend-uhi'
        }};

        function updateEnvLayer(selected) {{
            // Hide all raster/marker layers
            for (var key in rasterLayers) {{
                var layers = rasterLayers[key];
                if (map.hasLayer(layers[0])) map.removeLayer(layers[0]);
                if (map.hasLayer(layers[1])) map.removeLayer(layers[1]);
            }}
            // Show selected (if not "none")
            if (selected && selected !== 'none' && rasterLayers[selected]) {{
                rasterLayers[selected][0].addTo(map);
                rasterLayers[selected][1].addTo(map);
            }}
            // Update environmental layer legend (hide TRAP/UHI legends, then show selected)
            var envLegends = ['legend-trap', 'legend-uhi'];
            envLegends.forEach(function(id) {{
                var el = document.getElementById(id);
                if (el) el.style.display = 'none';
            }});
            if (selected && selected !== 'none' && legendMap[selected]) {{
                var legendEl = document.getElementById(legendMap[selected]);
                if (legendEl) legendEl.style.display = 'block';
            }}
            // Flood legend always stays visible (flood plains always on)
        }}

        // Radio button listeners
        document.querySelectorAll('input[name="env-layer"]').forEach(function(radio) {{
            radio.addEventListener('change', function() {{
                updateEnvLayer(this.value);
            }});
        }});

        // Tree canopy toggle
        var treeLayer = null;
        map.eachLayer(function(layer) {{
            if (layer.options && layer.options.name === 'Tree Canopy (ESA WorldCover)') {{
                treeLayer = layer;
            }}
        }});
        document.getElementById('tree-toggle').addEventListener('change', function() {{
            if (treeLayer) {{
                if (this.checked) {{
                    if (!map.hasLayer(treeLayer)) treeLayer.addTo(map);
                    var legendEl = document.getElementById('legend-tree');
                    if (legendEl) legendEl.style.display = 'block';
                }} else {{
                    if (map.hasLayer(treeLayer)) map.removeLayer(treeLayer);
                    var legendEl = document.getElementById('legend-tree');
                    if (legendEl) legendEl.style.display = 'none';
                }}
            }}
        }});

        // Initialize: show net air pollution legend and flood legend (flood always on)
        setTimeout(function() {{
            updateEnvLayer('net');
            // Always show flood legend (flood plains always visible)
            var floodLegend = document.getElementById('legend-flood');
            if (floodLegend) floodLegend.style.display = 'block';
        }}, 100);
    }});
    </script>
    """
    m.get_root().html.add_child(folium.Element(ctrl_panel_html))

    # Dynamic legends (keep for legend divs, but remove old toggle logic)
    m.get_root().html.add_child(folium.Element(_build_legend_html_only()))

    # Save
    m.save(str(OUTPUT_MAP))
    _progress(f"Saved {OUTPUT_MAP}")


# ---------------------------------------------------------------------------
# Main orchestrator
# ---------------------------------------------------------------------------
def main():
    parser = argparse.ArgumentParser(
        description="Consolidated environmental analysis map for CHCCS schools"
    )
    parser.add_argument(
        "--cache-only", action="store_true",
        help="Use only cached data; do not download anything",
    )
    parser.add_argument(
        "--grid-resolution", type=int, default=100,
        help="Grid resolution in meters (default 100)",
    )
    args = parser.parse_args()

    print("=" * 60)
    print("Consolidated Environmental Analysis Map")
    print("CHCCS Elementary Schools")
    print("=" * 60)

    ensure_directories()

    # Import reusable functions from sibling modules
    from road_pollution import (
        download_school_locations,
        download_road_network,
        filter_and_prepare_roads,
        download_ncdot_aadt,
        apply_aadt_overrides,
        discretize_roads,
        download_esa_worldcover,
        generate_county_grid,
    )
    from flood_map import (
        download_flood_zones,
        load_school_properties,
        classify_flood_zones,
        compute_overlaps,
    )

    # ---- Step 1: School locations ----
    print("\n[1/10] Loading school locations ...")
    download_school_locations(cache_only=args.cache_only)
    schools_df = pd.read_csv(SCHOOL_CSV)
    # Exclude hypothetical locations — real 11 schools only
    schools_gdf = gpd.GeoDataFrame(
        schools_df,
        geometry=gpd.points_from_xy(schools_df["lon"], schools_df["lat"]),
        crs=CRS_WGS84,
    )
    print(f"  Loaded {len(schools_gdf)} schools")

    # ---- Step 2: School properties ----
    print("\n[2/10] Loading school properties ...")
    school_props = load_school_properties()

    # ---- Step 3: Road network ----
    print("\n[3/10] Loading road network ...")
    roads_raw = download_road_network(cache_only=args.cache_only)
    roads = filter_and_prepare_roads(roads_raw)

    # Apply AADT overrides
    print("\n[4/10] Applying NCDOT AADT overrides ...")
    try:
        aadt_stations = download_ncdot_aadt(cache_only=args.cache_only)
        roads = apply_aadt_overrides(roads, aadt_stations)
    except Exception as e:
        _progress(f"AADT override skipped: {e}")
        roads["weight_source"] = "proxy"

    # ---- Step 4: ESA WorldCover ----
    print("\n[5/10] Loading ESA WorldCover ...")
    lulc_path = download_esa_worldcover(cache_only=args.cache_only)

    # ---- Step 5: TRAP grids ----
    print("\n[6/10] Loading/computing TRAP grids ...")
    cached = load_grids(TRAP_GRIDS_CACHE)
    if cached is not None:
        raw_grid, net_grid, trap_bounds = cached
    else:
        _progress("No cached TRAP grids found. Computing (this takes 10-20 min) ...")
        road_points = discretize_roads(roads)
        raw_grid, net_grid, trap_bounds = generate_county_grid(
            road_points, roads, lulc_path, resolution=args.grid_resolution
        )
        save_grids(raw_grid, net_grid, trap_bounds, TRAP_GRIDS_CACHE)

    # ---- Step 6: TRAP school scores ----
    print("\n[7/10] Loading TRAP school scores ...")
    trap_scores_path = DATA_PROCESSED / "road_pollution_scores.csv"
    if trap_scores_path.exists():
        trap_scores = pd.read_csv(trap_scores_path)
        # Exclude hypothetical locations
        trap_scores = trap_scores[
            trap_scores["school"].isin(schools_df["school"].values)
        ].copy()
        _progress(f"Loaded {len(trap_scores)} school scores from {trap_scores_path}")
    else:
        raise FileNotFoundError(
            f"TRAP scores not found at {trap_scores_path}. "
            "Run src/road_pollution.py first."
        )

    # ---- Step 7: Flood zones ----
    print("\n[8/10] Loading flood zones ...")
    bounds = school_props.total_bounds
    buf = 0.01
    flood_bbox = (bounds[0] - buf, bounds[1] - buf, bounds[2] + buf, bounds[3] + buf)
    flood = download_flood_zones(flood_bbox)
    flood_100, flood_500 = classify_flood_zones(flood)
    flood_overlaps = compute_overlaps(school_props, flood_100, flood_500)

    # ---- Step 8: UHI proxy ----
    print("\n[9/10] Computing UHI proxy ...")
    # Use same grid extent as TRAP grids
    uhi_grid, uhi_bounds = calculate_uhi_grid(
        lulc_path, trap_bounds, resolution=args.grid_resolution
    )
    uhi_scores = calculate_uhi_school_scores(schools_df, lulc_path)

    # Save UHI scores
    uhi_scores.to_csv(UHI_SCORES_CSV, index=False)
    _progress(f"Saved {UHI_SCORES_CSV}")

    # ---- Step 9: Assemble map ----
    print("\n[10/10] Assembling consolidated map ...")

    # Load district boundary for clipping and display
    district_path = DATA_CACHE / "chccs_district_boundary.gpkg"
    district_gdf = None
    if district_path.exists():
        district_gdf = gpd.read_file(district_path)
        assert district_gdf.crs is not None, "District boundary missing CRS"
        _progress(f"Loaded district boundary: CRS={district_gdf.crs}, "
                  f"{len(district_gdf)} features")

    create_environmental_map(
        trap_scores=trap_scores,
        raw_grid=raw_grid,
        net_grid=net_grid,
        trap_bounds=trap_bounds,
        lulc_path=lulc_path,
        roads_gdf=roads,
        school_props=school_props,
        flood_100=flood_100,
        flood_500=flood_500,
        overlaps=flood_overlaps,
        uhi_grid=uhi_grid,
        uhi_bounds=uhi_bounds,
        uhi_scores=uhi_scores,
        district_gdf=district_gdf,
        schools_df=schools_df,
    )

    # ---- Summary ----
    print("\n" + "=" * 60)
    print("Environmental analysis complete!")
    print("=" * 60)
    print(f"\nOutputs:")
    print(f"  Map:  {OUTPUT_MAP}")
    print(f"  UHI:  {UHI_SCORES_CSV}")
    print(f"  Cache: {TRAP_GRIDS_CACHE}")
    print(f"  Cache: {UHI_GRID_CACHE}")

    # Quick UHI summary
    print("\nUHI Proxy Summary (500m radius):")
    for _, row in uhi_scores.sort_values("rank_uhi_500m").iterrows():
        print(f"  #{int(row['rank_uhi_500m']):2d}  {row['school']:30s}  "
              f"UHI={row['uhi_500m']:5.1f}")

    print("=" * 60)


if __name__ == "__main__":
    main()
