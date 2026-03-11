"""Generate an editorial scrollytelling page on school closure scenarios.

Third and final story in the Ephesus-focused editorial series. Examines
student movement under closure scenarios — traffic redistribution,
transportation costs, and the school desert risk of closing eastern schools.

Siloed in example_stories/ to keep editorial content separate from neutral
methodology pages in src/.

Architecture mirrors environmental_conditions_story.py: two-column layout
(45% narrative / 55% Leaflet map) with Scrollama-driven step transitions.

Usage:
    python example_stories/closure_scenarios_story.py
    python example_stories/closure_scenarios_story.py --cache-only

Output:
    example_stories/closure_scenarios.html
"""

from __future__ import annotations

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
from PIL import Image
from shapely.geometry import Point, mapping
from shapely.ops import unary_union

# ---------------------------------------------------------------------------
# Path setup — import from src/
# ---------------------------------------------------------------------------
PROJECT_ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(PROJECT_ROOT / "src"))

warnings.filterwarnings("ignore", category=FutureWarning)

# ---------------------------------------------------------------------------
# Path constants
# ---------------------------------------------------------------------------
DATA_RAW = PROJECT_ROOT / "data" / "raw"
DATA_PROCESSED = PROJECT_ROOT / "data" / "processed"
DATA_CACHE = PROJECT_ROOT / "data" / "cache"
OUTPUT_DIR = Path(__file__).resolve().parent
OUTPUT_HTML = OUTPUT_DIR / "closure_scenarios.html"

SCHOOL_CSV = DATA_CACHE / "nces_school_locations.csv"
DISTRICT_CACHE = DATA_CACHE / "chccs_district_boundary.gpkg"
FLOOD_CACHE = DATA_CACHE / "fema_flood_zones.gpkg"
PARCEL_POLYS = DATA_RAW / "properties" / "combined_data_polys.gpkg"
TRAFFIC_CSV = DATA_PROCESSED / "school_closure_traffic.csv"
ASSIGNMENTS_CSV = DATA_PROCESSED / "school_closure_assignments.csv"
PIXEL_CHILDREN_CSV = DATA_CACHE / "closure_analysis" / "pixel_children.csv"
NETWORK_GRAPHML = DATA_CACHE / "network_drive.graphml"
BLOCKGROUPS_GPKG = DATA_CACHE / "tiger_blockgroups_orange.gpkg"
CHILDREN_BG_CSV = DATA_CACHE / "closure_analysis" / "children_blockgroups.csv"

# ---------------------------------------------------------------------------
# Domain constants
# ---------------------------------------------------------------------------
CRS_WGS84 = "EPSG:4326"
CRS_UTM17N = "EPSG:32617"
CHAPEL_HILL_CENTER = [35.9132, -79.0558]

# Color scheme
SCHOOL_COLORS = {
    "Ephesus Elementary": "#C62828",
    "Glenwood Elementary": "#2E7D32",
    "Seawell Elementary": "#1565C0",
    "Frank Porter Graham Bilingue": "#FF8F00",
    "New FPG Location": "#FF8F00",
}
DEFAULT_COLOR = "#888888"

FLOOD_100YR = "#6baed6"
FLOOD_500YR = "#bdd7e7"
SCHOOL_FILL = "#d4edda"
SCHOOL_EDGE = "#155724"


# ---------------------------------------------------------------------------
# Utility functions
# ---------------------------------------------------------------------------
def _progress(msg: str):
    print(f"  ... {msg}")


def _round_coords(geom_dict: dict, precision: int = 4) -> dict:
    """Round coordinates in a GeoJSON geometry dict to reduce file size."""
    def _round(coords):
        if isinstance(coords[0], (list, tuple)):
            return [_round(c) for c in coords]
        return [round(c, precision) for c in coords]

    result = dict(geom_dict)
    if "coordinates" in result:
        result["coordinates"] = _round(result["coordinates"])
    return result


def gdf_to_geojson_str(
    gdf: gpd.GeoDataFrame,
    properties: list = None,
    simplify_m: float = None,
) -> str:
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
                        float(val)
                        if isinstance(val, (np.integer, np.floating))
                        else val
                    )
        features.append({
            "type": "Feature",
            "geometry": _round_coords(mapping(row.geometry)),
            "properties": props,
        })
    fc = {"type": "FeatureCollection", "features": features}
    return json.dumps(fc, separators=(",", ":"))


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
    active = grid > 0.001
    alpha_vals = np.where(
        active,
        np.clip(120 + 80 * normalized, 0, 255).astype(np.uint8),
        0,
    )
    rgba[..., 3] = alpha_vals

    img = Image.fromarray(rgba, "RGBA")
    buf = io.BytesIO()
    img.save(buf, format="PNG")
    buf.seek(0)
    b64 = base64.b64encode(buf.read()).decode()
    return f"data:image/png;base64,{b64}"


# ---------------------------------------------------------------------------
# Data loading functions
# ---------------------------------------------------------------------------
def load_schools() -> pd.DataFrame:
    if not SCHOOL_CSV.exists():
        raise FileNotFoundError(
            f"School locations not found: {SCHOOL_CSV}\n"
            "Run: python src/road_pollution.py  (to download NCES data)"
        )
    return pd.read_csv(SCHOOL_CSV)


def load_district_boundary() -> gpd.GeoDataFrame:
    if not DISTRICT_CACHE.exists():
        raise FileNotFoundError(
            f"District boundary not found: {DISTRICT_CACHE}\n"
            "Run: python src/school_desert.py"
        )
    return gpd.read_file(DISTRICT_CACHE)


def load_flood_zones() -> gpd.GeoDataFrame:
    if not FLOOD_CACHE.exists():
        raise FileNotFoundError(
            f"Flood zones not found: {FLOOD_CACHE}\n"
            "Run: python src/flood_map.py"
        )
    return gpd.read_file(FLOOD_CACHE)


def classify_flood_zones(flood: gpd.GeoDataFrame):
    """Split flood zones into 100-year and 500-year."""
    zone_100 = flood["FLD_ZONE"].isin(["A", "AE", "AO", "AH"])
    flood_100 = flood[zone_100].copy()
    _progress(f"100-year flood zone features: {len(flood_100)}")

    zone_500 = flood["ZONE_SUBTY"].str.contains("0.2 PCT", case=False, na=False)
    flood_500 = flood[zone_500].copy()
    _progress(f"500-year flood zone features: {len(flood_500)}")

    return flood_100, flood_500


def load_school_properties(schools: pd.DataFrame) -> gpd.GeoDataFrame:
    """Load parcel polygons matching school locations."""
    if not PARCEL_POLYS.exists():
        raise FileNotFoundError(
            f"Parcel data not found: {PARCEL_POLYS}\n"
            "Run: python src/property_data.py"
        )
    parcels = gpd.read_file(PARCEL_POLYS).to_crs(CRS_WGS84)

    results = []
    for _, s in schools.iterrows():
        pt = Point(s.lon, s.lat)
        containing = parcels[parcels.geometry.contains(pt)]
        if len(containing) > 0:
            row = containing.iloc[0].copy()
        else:
            # Nearest centroid fallback
            dists = parcels.geometry.centroid.distance(pt)
            row = parcels.iloc[dists.idxmin()].copy()
        row["school_name"] = s.school
        row["school_lat"] = s.lat
        row["school_lon"] = s.lon
        results.append(row)

    return gpd.GeoDataFrame(results, crs=CRS_WGS84)


def load_traffic_data() -> pd.DataFrame:
    """Load school closure traffic CSV."""
    if not TRAFFIC_CSV.exists():
        raise FileNotFoundError(
            f"Traffic data not found: {TRAFFIC_CSV}\n"
            "Run: python src/school_closure_analysis.py"
        )
    return pd.read_csv(TRAFFIC_CSV)


def load_assignments() -> pd.DataFrame:
    """Load closure assignments CSV."""
    if not ASSIGNMENTS_CSV.exists():
        raise FileNotFoundError(
            f"Assignments not found: {ASSIGNMENTS_CSV}\n"
            "Run: python src/school_closure_analysis.py"
        )
    return pd.read_csv(ASSIGNMENTS_CSV)


def load_pixel_children() -> pd.DataFrame:
    """Load pixel-level children counts."""
    if not PIXEL_CHILDREN_CSV.exists():
        raise FileNotFoundError(
            f"Pixel children not found: {PIXEL_CHILDREN_CSV}\n"
            "Run: python src/school_closure_analysis.py"
        )
    return pd.read_csv(PIXEL_CHILDREN_CSV)


def load_road_graph():
    """Load OSMnx drive graph and convert to GeoJSON with edge IDs."""
    if not NETWORK_GRAPHML.exists():
        raise FileNotFoundError(
            f"Road network not found: {NETWORK_GRAPHML}\n"
            "Run: python src/school_closure_analysis.py"
        )
    import osmnx as ox
    G = ox.load_graphml(NETWORK_GRAPHML)
    from school_closure_analysis import _graph_to_geojson_with_ids
    return _graph_to_geojson_with_ids(G)


def load_block_groups() -> gpd.GeoDataFrame:
    """Load Tiger block groups with children counts."""
    if not BLOCKGROUPS_GPKG.exists():
        raise FileNotFoundError(
            f"Block groups not found: {BLOCKGROUPS_GPKG}\n"
            "Run: python src/school_closure_analysis.py"
        )
    bg = gpd.read_file(BLOCKGROUPS_GPKG).to_crs(CRS_WGS84)

    if CHILDREN_BG_CSV.exists():
        children = pd.read_csv(CHILDREN_BG_CSV)
        children["GEOID"] = children["GEOID"].astype(str)
        bg["GEOID"] = bg["GEOID"].astype(str)
        bg = bg.merge(children, on="GEOID", how="left")
        bg["children_0_4"] = bg["children_0_4"].fillna(0)
        bg["children_5_9"] = bg["children_5_9"].fillna(0)
    else:
        bg["children_0_4"] = 0
        bg["children_5_9"] = 0

    return bg


# ---------------------------------------------------------------------------
# Traffic delta computation
# ---------------------------------------------------------------------------
def compute_traffic_delta(traffic: pd.DataFrame, scenario: str,
                          graph_geojson: dict) -> str:
    """Compute traffic delta vs baseline, return filtered GeoJSON string."""
    nearest = traffic[traffic["zone"] == "nearest"]
    # Sum both age groups per edge
    base = (nearest[nearest["scenario"] == "baseline"]
            .groupby("edge_idx")["children"].sum())
    closure = (nearest[nearest["scenario"] == scenario]
               .groupby("edge_idx")["children"].sum())
    # Compute delta
    all_edges = set(base.index) | set(closure.index)
    features = []
    for edge_idx in all_edges:
        b = base.get(edge_idx, 0.0)
        c = closure.get(edge_idx, 0.0)
        delta = c - b
        if abs(delta) < 3.0:
            continue
        idx = int(edge_idx)
        if idx >= len(graph_geojson["features"]):
            continue
        feat = graph_geojson["features"][idx]
        features.append({
            "type": "Feature",
            "geometry": feat["geometry"],
            "properties": {
                "delta": round(float(delta), 1),
                "name": feat["properties"].get("name", ""),
            },
        })
    fc = {"type": "FeatureCollection", "features": features}
    return json.dumps(fc, separators=(",", ":"))


def count_significant_edges(traffic: pd.DataFrame, scenario: str) -> int:
    """Count edges with meaningful traffic delta (>= 3 children)."""
    nearest = traffic[traffic["zone"] == "nearest"]
    base = (nearest[nearest["scenario"] == "baseline"]
            .groupby("edge_idx")["children"].sum())
    closure = (nearest[nearest["scenario"] == scenario]
               .groupby("edge_idx")["children"].sum())
    all_edges = set(base.index) | set(closure.index)
    count = 0
    for edge_idx in all_edges:
        b = base.get(edge_idx, 0.0)
        c = closure.get(edge_idx, 0.0)
        if abs(c - b) >= 3.0:
            count += 1
    return count


# ---------------------------------------------------------------------------
# Drive-time delta heatmap
# ---------------------------------------------------------------------------
def compute_drivetime_delta_png(assignments: pd.DataFrame,
                                scenario: str) -> tuple:
    """Compute drive-time delta heatmap for a closure scenario.

    Returns (base64_png, bounds) or (None, None).
    """
    base = assignments[
        (assignments["scenario"] == "baseline") &
        (assignments["mode"] == "drive")
    ][["grid_id", "lat", "lon", "min_time_minutes"]].copy()
    base = base.rename(columns={"min_time_minutes": "base_time"})

    closure = assignments[
        (assignments["scenario"] == scenario) &
        (assignments["mode"] == "drive")
    ][["grid_id", "min_time_minutes"]].copy()
    closure = closure.rename(columns={"min_time_minutes": "closure_time"})

    merged = base.merge(closure, on="grid_id", how="inner")
    # Filter to finite values
    merged = merged[
        np.isfinite(merged["base_time"]) &
        np.isfinite(merged["closure_time"])
    ].copy()
    merged["delta"] = merged["closure_time"] - merged["base_time"]
    # Only show increases
    merged["delta_pos"] = merged["delta"].clip(lower=0)
    merged = merged[merged["delta_pos"] > 0.01].copy()

    if len(merged) == 0:
        return None, None

    from school_closure_analysis import rasterize_grid

    values_2d, grid_meta, bounds = rasterize_grid(
        merged, "delta_pos", resolution_m=100
    )
    if values_2d is None:
        return None, None

    # Replace NaN with 0 for colorization
    values_2d = np.nan_to_num(values_2d, nan=0.0)

    png = grid_to_base64_png(values_2d, colormap="Oranges")
    # bounds = [[minlat, minlon], [maxlat, maxlon]]
    return png, bounds


# ---------------------------------------------------------------------------
# Baseline heatmap
# ---------------------------------------------------------------------------
def compute_baseline_heatmap_png(assignments: pd.DataFrame) -> tuple:
    """Create baseline drive-time heatmap. Returns (base64_png, bounds)."""
    base = assignments[
        (assignments["scenario"] == "baseline") &
        (assignments["mode"] == "drive")
    ][["grid_id", "lat", "lon", "min_time_minutes"]].copy()
    base = base[np.isfinite(base["min_time_minutes"])].copy()

    if len(base) == 0:
        return None, None

    from school_closure_analysis import rasterize_grid

    values_2d, grid_meta, bounds = rasterize_grid(
        base, "min_time_minutes", resolution_m=100
    )
    if values_2d is None:
        return None, None

    values_2d = np.nan_to_num(values_2d, nan=0.0)
    png = grid_to_base64_png(values_2d, colormap="YlOrRd")
    return png, bounds


# ---------------------------------------------------------------------------
# Children by nearest school
# ---------------------------------------------------------------------------
def compute_children_by_school(pixels: pd.DataFrame,
                               assignments: pd.DataFrame) -> list:
    """Compute children 0-4 and 5-9 by nearest-drive school.

    Returns list of dicts sorted by children_0_4 descending.
    """
    base_drive = assignments[
        (assignments["scenario"] == "baseline") &
        (assignments["mode"] == "drive")
    ][["grid_id", "nearest_school"]].copy()

    merged = base_drive.merge(pixels, on="grid_id", how="left")
    merged["children_0_4"] = merged["children_0_4"].fillna(0)
    merged["children_5_9"] = merged["children_5_9"].fillna(0)

    by_school = (merged.groupby("nearest_school")
                 [["children_0_4", "children_5_9"]].sum()
                 .round(0).reset_index())
    by_school = by_school.sort_values("children_0_4", ascending=False)

    records = []
    for _, row in by_school.iterrows():
        records.append({
            "school": row["nearest_school"],
            "children_0_4": int(row["children_0_4"]),
            "children_5_9": int(row["children_5_9"]),
        })
    return records


# ---------------------------------------------------------------------------
# HTML builder
# ---------------------------------------------------------------------------
def build_html(data: dict) -> str:
    """Build the complete HTML page from pre-computed data."""
    # Extract pre-computed data
    children_data = json.loads(data["children_chart_data"])
    ephesus_kids = next(
        (d for d in children_data if "Ephesus" in d["school"]),
        {"children_0_4": 634, "children_5_9": 273}
    )
    seawell_kids = next(
        (d for d in children_data if "Seawell" in d["school"]),
        {"children_0_4": 123, "children_5_9": 273}
    )
    kids_ratio = round(ephesus_kids["children_0_4"] / max(seawell_kids["children_0_4"], 1), 0)

    seawell_edges = data["seawell_edges"]
    ephesus_edges = data["ephesus_edges"]
    edge_ratio = round(ephesus_edges / max(seawell_edges, 1), 1)

    # Narrative data for chart
    trap_data_js = data["children_chart_data"]

    html = f"""<!DOCTYPE html>
<html lang="en">
<head>
<meta charset="UTF-8">
<meta name="viewport" content="width=device-width, initial-scale=1.0">
<title>School Closure Scenarios &mdash; CHCCS Geospatial Analysis</title>
<link rel="stylesheet" href="https://unpkg.com/leaflet@1.9.3/dist/leaflet.css" />
<style>
* {{ margin: 0; padding: 0; box-sizing: border-box; }}
body {{ font-family: 'Segoe UI', Tahoma, Geneva, Verdana, sans-serif;
       background: #f5f5f5; overflow-x: hidden; }}

.scroll-container {{
  width: 45%;
  padding: 0 30px;
  position: relative;
  z-index: 10;
}}

#map-container {{
  position: fixed;
  top: 0; right: 0;
  width: 55%;
  height: 100vh;
  z-index: 5;
}}

#map {{ width: 100%; height: 100%; }}

#map-dim {{
  position: absolute;
  top: 0; left: 0;
  width: 100%; height: 100%;
  background: rgba(255,255,255,0.4);
  z-index: 1000;
  pointer-events: none;
  display: none;
}}

.step {{
  min-height: 80vh;
  padding: 30px;
  margin: 20px 0;
  background: white;
  border-radius: 8px;
  box-shadow: 0 2px 8px rgba(0,0,0,0.1);
  border-left: 4px solid #666;
  opacity: 0.3;
  transition: opacity 0.4s ease, border-color 0.3s ease;
}}

.step:first-child {{ margin-top: 40vh; }}
.step:last-child {{ margin-bottom: 60vh; }}
.step.is-active {{ opacity: 1; border-color: #333; }}

.step-number {{
  display: inline-block;
  width: 28px; height: 28px;
  background: #555;
  color: white;
  border-radius: 50%;
  text-align: center;
  line-height: 28px;
  font-weight: bold;
  font-size: 14px;
  margin-bottom: 10px;
}}

h2 {{ color: #333; margin: 10px 0 15px; font-size: 1.3em; }}
h3 {{ color: #333; margin: 15px 0 8px; font-size: 1.1em; }}
p {{ line-height: 1.6; margin: 10px 0; color: #333; }}

.source {{
  background: #e3f2fd;
  padding: 12px 15px;
  border-radius: 6px;
  margin: 12px 0;
  font-size: 0.9em;
}}

.limitation {{
  background: #fff8e1;
  padding: 12px 15px;
  border-radius: 6px;
  margin: 12px 0;
  border-left: 3px solid #ffc107;
  font-size: 0.9em;
}}

.insight {{
  background: #e8f5e9;
  padding: 12px 15px;
  border-radius: 6px;
  margin: 12px 0;
  border-left: 3px solid #4caf50;
  font-size: 0.9em;
}}

.metric-box {{
  display: flex;
  gap: 12px;
  flex-wrap: wrap;
  margin: 12px 0;
}}

.metric {{
  flex: 1;
  min-width: 120px;
  padding: 12px;
  background: #f5f5f5;
  border-radius: 6px;
  text-align: center;
}}

.metric-value {{
  font-size: 1.4em;
  font-weight: bold;
  color: #333;
}}

.metric-label {{
  font-size: 0.8em;
  color: #666;
  margin-top: 4px;
}}

details {{
  margin: 10px 0;
  padding: 8px 12px;
  background: #fafafa;
  border-radius: 4px;
  border: 1px solid #e0e0e0;
}}

summary {{
  cursor: pointer;
  font-weight: bold;
  color: #555;
  padding: 4px 0;
}}

details[open] summary {{ margin-bottom: 8px; }}

a {{ color: #1565C0; }}

.ephesus-label {{ color: #C62828; font-weight: bold; }}
.glenwood-label {{ color: #2E7D32; font-weight: bold; }}
.seawell-label {{ color: #1565C0; font-weight: bold; }}
.fpg-label {{ color: #FF8F00; font-weight: bold; }}

@media (max-width: 900px) {{
  .scroll-container {{ width: 100%; }}
  #map-container {{
    position: relative;
    width: 100%;
    height: 40vh;
  }}
}}
</style>
</head>
<body>
<div class="scroll-container">

  <!-- Step 0: From Demographics to Movement -->
  <div class="step" data-step="0">
    <div class="step-number">1</div>
    <h2>From Demographics to Movement</h2>
    <p>The <a href="https://atashie.github.io/CHCCS_geospatial/example_stories/environmental_conditions.html">previous stories</a>
    examined <em>environmental conditions</em> and
    <a href="https://atashie.github.io/CHCCS_geospatial/example_stories/ephesus_seawell_comparison.html"><em>who lives where</em></a>.
    This story examines <strong>what happens when children must move</strong>.</p>
    <p>School closure redirects hundreds of students, increasing travel times
    and road congestion. Only three schools are considered for closure &mdash;
    <span class="ephesus-label">Ephesus</span>,
    <span class="glenwood-label">Glenwood</span>, and
    <span class="seawell-label">Seawell</span>. We analyze what each
    closure means for transportation.</p>
  </div>

  <!-- Step 1: Methodology Overview -->
  <div class="step" data-step="1">
    <div class="step-number">2</div>
    <h2>How We Measure Impact</h2>
    <p>For every 100-meter pixel in the district, Dijkstra&rsquo;s shortest-path
    algorithm computes driving time to each school. When a school closes,
    students route to their next-nearest school. The difference = extra travel time.</p>
    <p>Traffic volume is estimated by distributing Census child counts
    (dasymetric allocation) to each pixel, then tracing each pixel&rsquo;s
    route to its nearest school. The number of children per road segment
    becomes our traffic proxy.</p>
    <div class="source">
      <strong>Full methodology:</strong>
      <a href="https://atashie.github.io/CHCCS_geospatial/assets/maps/closure_methodology.html">School Closure Methodology</a>
      &bull;
      <strong>Interactive map:</strong>
      <a href="https://atashie.github.io/CHCCS_geospatial/assets/maps/school_closure_analysis.html">School Closure Analysis</a>
    </div>
  </div>

  <!-- ========== SEAWELL CLOSURE (Steps 2-3) ========== -->

  <!-- Step 2: Seawell Traffic Impact -->
  <div class="step" data-step="2">
    <div class="step-number">3</div>
    <h2>Seawell Closure: Traffic Impact</h2>
    <p>When <span class="seawell-label">Seawell</span> closes, its students
    redistribute to nearby schools. The map shows road segments where traffic
    changes: <strong style="color:#C62828;">red = more children</strong>,
    <strong style="color:#1565C0;">blue = fewer children</strong>.</p>
    <div class="metric-box">
      <div class="metric">
        <div class="metric-value">{seawell_edges}</div>
        <div class="metric-label">road segments affected</div>
      </div>
    </div>
    <div class="limitation">
      <strong>LEAP program note:</strong> Seawell currently hosts the LEAP
      program &mdash; students bussed from across the district. These students
      already commute long distances; closure shifts their routes but doesn&rsquo;t
      fundamentally change their travel burden. The inconvenience falls most
      heavily on local community school families who currently walk or drive
      short distances.
    </div>
  </div>

  <!-- Step 3: Seawell Drive-Time Increase -->
  <div class="step" data-step="3">
    <div class="step-number">4</div>
    <h2>Seawell Closure: Drive-Time Increase</h2>
    <p>The heatmap shows areas where driving time <em>increases</em> when
    Seawell closes. Orange intensity reflects additional minutes to reach the
    next-nearest school.</p>
    <div class="metric-box">
      <div class="metric">
        <div class="metric-value">{seawell_kids["children_5_9"]}</div>
        <div class="metric-label">children 5&ndash;9 in Seawell drive-time zone</div>
      </div>
      <div class="metric">
        <div class="metric-value">{seawell_kids["children_0_4"]}</div>
        <div class="metric-label">children 0&ndash;4 (future K)</div>
      </div>
    </div>
  </div>

  <!-- ========== EPHESUS CLOSURE (Steps 4-5) ========== -->

  <!-- Step 4: Ephesus Traffic Impact -->
  <div class="step" data-step="4">
    <div class="step-number">5</div>
    <h2>Ephesus Closure: Traffic Impact</h2>
    <p>When <span class="ephesus-label">Ephesus</span> closes,
    <strong>{ephesus_edges} road segments</strong> see meaningful traffic
    changes &mdash; <strong>{edge_ratio}x more widespread</strong> than
    Seawell&rsquo;s {seawell_edges}. Students redistribute across a larger area
    because Ephesus serves the district&rsquo;s most population-dense
    neighborhood.</p>
    <div class="metric-box">
      <div class="metric">
        <div class="metric-value">{ephesus_edges}</div>
        <div class="metric-label">road segments affected</div>
      </div>
      <div class="metric">
        <div class="metric-value">{edge_ratio}x</div>
        <div class="metric-label">more than Seawell</div>
      </div>
    </div>
  </div>

  <!-- Step 5: Children 0-4 Bar Chart -->
  <div class="step" data-step="5">
    <div class="step-number">6</div>
    <h2>Young Children by Nearest School</h2>
    <p><span class="ephesus-label">Ephesus</span> has
    <strong>{ephesus_kids["children_0_4"]} children under 5</strong> in its
    drive-time zone &mdash; compared to Seawell&rsquo;s {seawell_kids["children_0_4"]}.
    These are future kindergarteners.</p>
    <p>Closing Ephesus removes capacity where future enrollment demand is
    highest. This disparity exists because Ephesus serves the most
    population-dense area of the district.</p>
    <div class="insight">
      <strong>Key finding:</strong> Ephesus has ~{int(kids_ratio)}x more
      children under 5 than Seawell ({ephesus_kids["children_0_4"]} vs
      {seawell_kids["children_0_4"]}). Closing it removes elementary capacity
      precisely where future demand is greatest.
    </div>
  </div>

  <!-- ========== SCHOOL DESERT (Steps 6-8) ========== -->

  <!-- Step 6: Where the Children Live -->
  <div class="step" data-step="6">
    <div class="step-number">7</div>
    <h2>Where the Children Live</h2>
    <p>The choropleth shows the concentration of elementary-age children
    (ages 5&ndash;9) by Census block group. The eastern part of the district
    &mdash; around <span class="ephesus-label">Ephesus</span>,
    <span class="glenwood-label">Glenwood</span>, and Rashkis &mdash; has
    the highest concentration of school-age children.</p>
    <p>This is where elementary capacity is needed most.</p>
  </div>

  <!-- Step 7: School Desert Scenario -->
  <div class="step" data-step="7">
    <div class="step-number">8</div>
    <h2>The School Desert Scenario</h2>
    <p>Now imagine closing <strong>both</strong>
    <span class="ephesus-label">Ephesus</span> and
    <span class="glenwood-label">Glenwood</span>.</p>
    <p>This removes two schools from the most population-dense part of the
    eastern district. The remaining nearby school &mdash; Rashkis &mdash;
    is not only climate-vulnerable (adjacent to FEMA AE 100-year flood zone)
    but also has limited traffic access.</p>
    <p>Meadowmont Lane is essentially the only vehicular corridor, creating
    extreme congestion if hundreds of additional families must funnel
    through it.</p>
  </div>

  <!-- Step 8: Rashkis Vulnerability -->
  <div class="step" data-step="8">
    <div class="step-number">9</div>
    <h2>Rashkis: Flood Risk &amp; Access</h2>
    <p>Rashkis sits adjacent to a FEMA AE flood zone (7.1% of property in
    100-year zone, 4.8% in 500-year). Its road access is constrained to a
    single corridor.</p>
    <p>If eastern schools close and Rashkis absorbs their students, you get a
    flood-vulnerable school overwhelmed with traffic on a single access
    corridor. This is not just inconvenience &mdash; it is a
    <strong>safety concern</strong>.</p>
    <div class="source">
      <strong>See also:</strong>
      <a href="https://atashie.github.io/CHCCS_geospatial/example_stories/environmental_conditions.html">Environmental Conditions story</a>
      (flood risk analysis for all 11 schools)
    </div>
  </div>

  <!-- ========== CONCLUSION (Step 9) ========== -->

  <!-- Step 9: Summary -->
  <div class="step" data-step="9">
    <div class="step-number">10</div>
    <h2>What the Data Shows</h2>
    <p>Three key findings from the closure analysis:</p>
    <ol style="margin:8px 0 12px 20px;line-height:1.8;">
      <li><strong>Ephesus closure is more disruptive:</strong>
        {ephesus_edges} affected road segments vs {seawell_edges} for Seawell;
        ~{int(kids_ratio)}x more young children in its zone</li>
      <li><strong>Traffic burden falls on community school families</strong>,
        not already-bussed LEAP students at Seawell</li>
      <li><strong>Closing eastern schools creates a school desert</strong>
        in the most population-dense area, overloading a flood-vulnerable
        school with constrained access</li>
    </ol>
    <div class="source">
      <strong>Interactive closure map:</strong>
      <a href="https://atashie.github.io/CHCCS_geospatial/assets/maps/school_closure_analysis.html">School Closure Analysis</a><br>
      <strong>Environmental story:</strong>
      <a href="https://atashie.github.io/CHCCS_geospatial/example_stories/environmental_conditions.html">Environmental Conditions</a><br>
      <strong>Demographics story:</strong>
      <a href="https://atashie.github.io/CHCCS_geospatial/example_stories/ephesus_seawell_comparison.html">Ephesus vs. Seawell</a>
    </div>
    <p style="margin-top:16px;font-size:0.85em;color:#888;">
      <strong>Data sources:</strong> NCES EDGE 2023-24 &bull; ACS 5-Year
      &bull; OpenStreetMap road network &bull; FEMA NFHL &bull;
      Orange County parcel data
    </p>
  </div>

</div> <!-- end scroll-container -->
<div id="map-container">
  <div id="map"></div>
  <div id="map-dim"></div>
  <div id="chart-panel" style="position:absolute;top:0;left:0;width:100%;height:100%;
    z-index:500;background:#fff;display:none;overflow-y:auto;padding:24px 20px;">
    <div id="chart-title" style="text-align:center;margin:0 0 16px;">
      <h3 style="margin:0 0 4px;font-size:1.15em;color:#333;"></h3>
      <p style="margin:0;font-size:0.82em;color:#777;"></p>
    </div>
    <div id="chart-bars"></div>
    <p id="chart-footer" style="text-align:center;margin:16px 0 0;font-size:0.8em;color:#999;line-height:1.4;"></p>
  </div>
</div>

<script src="https://unpkg.com/leaflet@1.9.3/dist/leaflet.js"></script>
<script src="https://unpkg.com/scrollama@3.2.0/build/scrollama.min.js"></script>
<script>
// === Embedded data ===
var SCHOOLS = {data["schools_json"]};
var DISTRICT = {data["district_json"]};
var TRAFFIC_SEAWELL = {data["traffic_seawell_json"]};
var TRAFFIC_EPHESUS = {data["traffic_ephesus_json"]};
var BLOCK_GROUPS = {data["blockgroups_json"]};
var FLOOD_100 = {data["flood_100_json"]};
var FLOOD_500 = {data["flood_500_json"]};
var RASHKIS_PROP = {data["rashkis_prop_json"]};
var CHILDREN_DATA = {data["children_chart_data"]};

var BASELINE_URL = "{data["baseline_png"]}";
var BASELINE_BOUNDS = {json.dumps(data["baseline_bounds"])};
var DELTA_SEAWELL_URL = "{data["delta_seawell_png"]}";
var DELTA_SEAWELL_BOUNDS = {json.dumps(data["delta_seawell_bounds"])};

var SCHOOL_COLORS = {{
  "Ephesus Elementary": "#C62828",
  "Glenwood Elementary": "#2E7D32",
  "Seawell Elementary": "#1565C0",
  "Frank Porter Graham Bilingue": "#FF8F00",
  "New FPG Location": "#FF8F00"
}};
var DEFAULT_COLOR = "#888888";

// === Bar chart builder ===
function renderBars(containerId, data, metric, options) {{
  options = options || {{}};
  var maxVal = 0;
  data.forEach(function(d) {{ if (d[metric] > maxVal) maxVal = d[metric]; }});
  if (maxVal === 0) maxVal = 1;
  var html = "";
  data.forEach(function(d) {{
    var val = d[metric] || 0;
    var width = (val / maxVal * 100).toFixed(1);
    var label = d.school.replace(" Elementary", "").replace(" Bilingue", "");
    var barColor = SCHOOL_COLORS[d.school] || DEFAULT_COLOR;
    var isHighlight = !!SCHOOL_COLORS[d.school];
    var fontWeight = isHighlight ? "bold" : "normal";
    var fontColor = isHighlight ? barColor : "#555";
    var valText = val.toString();
    html += '<div style="display:flex;align-items:center;margin:4px 0;font-size:0.82em;padding:2px 4px;border-radius:4px;">'
      + '<div style="width:140px;text-align:right;padding-right:8px;color:' + fontColor + ';font-weight:' + fontWeight + ';white-space:nowrap;overflow:hidden;text-overflow:ellipsis;">'
      + label + '</div>'
      + '<div style="flex:1;background:#eee;border-radius:3px;height:18px;position:relative;">'
      + '<div style="width:' + width + '%;height:100%;background:' + barColor + ';border-radius:3px;opacity:0.85;"></div>'
      + '</div>'
      + '<div style="width:50px;text-align:right;padding-left:6px;color:#555;font-size:0.95em;">'
      + valText + '</div></div>';
  }});
  document.getElementById(containerId).innerHTML = html;
}}

function showChart(title, subtitle, footer, data, metric) {{
  document.getElementById("chart-panel").style.display = "block";
  var titleEl = document.querySelector("#chart-title h3");
  var subtitleEl = document.querySelector("#chart-title p");
  titleEl.textContent = title;
  subtitleEl.textContent = subtitle;
  document.getElementById("chart-footer").textContent = footer;
  renderBars("chart-bars", data, metric, {{}});
}}

function showChildrenChart() {{
  showChart(
    "Children Under 5 by Nearest School (Drive Time)",
    "Dasymetric allocation of ACS estimates to 100m grid, routed to nearest school",
    "Higher values = more future kindergarteners in that school's drive-time zone.",
    CHILDREN_DATA, "children_0_4"
  );
}}

// === Map setup ===
var map = L.map("map", {{
  center: [{CHAPEL_HILL_CENTER[0]}, {CHAPEL_HILL_CENTER[1]}],
  zoom: 12,
  scrollWheelZoom: false,
  zoomControl: true,
}});

L.tileLayer("https://{{s}}.basemaps.cartocdn.com/light_all/{{z}}/{{x}}/{{y}}@2x.png", {{
  attribution: '&copy; <a href="https://carto.com/">CARTO</a> &copy; <a href="https://www.openstreetmap.org/copyright">OSM</a>',
  maxZoom: 19,
}}).addTo(map);

var dimOverlay = document.getElementById("map-dim");
var districtBounds = L.geoJSON(DISTRICT).getBounds();

function districtView() {{
  map.fitBounds(districtBounds.pad(0.05));
}}

// === Closed-school X marker ===
function makeClosedMarker(lat, lon) {{
  return L.marker([lat, lon], {{
    icon: L.divIcon({{
      className: '',
      html: '<svg width="40" height="40"><line x1="5" y1="5" x2="35" y2="35" stroke="#C62828" stroke-width="6"/><line x1="35" y1="5" x2="5" y2="35" stroke="#C62828" stroke-width="6"/></svg>',
      iconSize: [40, 40],
      iconAnchor: [20, 20]
    }})
  }});
}}

// === Find school coords ===
function findSchool(name) {{
  var result = null;
  SCHOOLS.features.forEach(function(f) {{
    if (f.properties.school && f.properties.school.indexOf(name) >= 0) {{
      result = f;
    }}
  }});
  return result;
}}

// === Layer factories ===
var layers = {{}};

// District boundary
layers.district = L.geoJSON(DISTRICT, {{
  style: {{ color: "#333", weight: 2, dashArray: "6 4", fillOpacity: 0 }}
}});

// Schools (colored by role)
layers.schools = L.geoJSON(SCHOOLS, {{
  pointToLayer: function(f, ll) {{
    var name = f.properties.school || "";
    var color = SCHOOL_COLORS[name] || "#888";
    var radius = SCHOOL_COLORS[name] ? 8 : 5;
    return L.circleMarker(ll, {{
      radius: radius,
      fillColor: color,
      color: "#fff",
      weight: 2,
      fillOpacity: 0.9,
    }});
  }},
  onEachFeature: function(f, layer) {{
    layer.bindTooltip(f.properties.school, {{permanent: false}});
  }}
}});

// Schools with permanent labels
layers.schoolsLabeled = L.geoJSON(SCHOOLS, {{
  pointToLayer: function(f, ll) {{
    var name = f.properties.school || "";
    var color = SCHOOL_COLORS[name] || "#888";
    var radius = SCHOOL_COLORS[name] ? 7 : 4;
    return L.circleMarker(ll, {{
      radius: radius,
      fillColor: color,
      color: "#fff",
      weight: 2,
      fillOpacity: 0.9,
    }});
  }},
  onEachFeature: function(f, layer) {{
    var label = f.properties.school.replace(" Elementary", "").replace(" Bilingue", "");
    layer.bindTooltip(label, {{
      permanent: true,
      direction: "right",
      offset: [10, 0],
      className: "school-label-tip"
    }});
  }}
}});

// Traffic delta layers (diverging red/blue)
function trafficDeltaStyle(f) {{
  var d = f.properties.delta || 0;
  if (Math.abs(d) < 1) return {{ weight: 0, opacity: 0 }};
  var maxDelta = 30;
  var t = Math.min(1, Math.abs(d) / maxDelta);
  var w = 1 + 4 * t;
  if (d > 0) return {{ color: "rgb(255," + Math.round(100*(1-t)) + ",0)", weight: w, opacity: 0.7 }};
  return {{ color: "rgb(0," + Math.round(100*(1-t)) + ",255)", weight: w, opacity: 0.7 }};
}}

function trafficTooltip(f, layer) {{
  var d = f.properties.delta || 0;
  var name = f.properties.name || "";
  var prefix = d > 0 ? "+" : "";
  var tip = prefix + d.toFixed(1) + " children";
  if (name) tip = name + ": " + tip;
  layer.bindTooltip(tip);
}}

layers.trafficSeawell = L.geoJSON(TRAFFIC_SEAWELL, {{
  style: trafficDeltaStyle,
  onEachFeature: trafficTooltip
}});

layers.trafficEphesus = L.geoJSON(TRAFFIC_EPHESUS, {{
  style: trafficDeltaStyle,
  onEachFeature: trafficTooltip
}});

// Baseline heatmap
var baselineBoundsLL = L.latLngBounds(
  [BASELINE_BOUNDS[0][0], BASELINE_BOUNDS[0][1]],
  [BASELINE_BOUNDS[1][0], BASELINE_BOUNDS[1][1]]
);
layers.baselineHeatmap = L.imageOverlay(BASELINE_URL, baselineBoundsLL, {{ opacity: 0.7 }});

// Drive-time delta (Seawell)
var deltaSeawellBoundsLL = L.latLngBounds(
  [DELTA_SEAWELL_BOUNDS[0][0], DELTA_SEAWELL_BOUNDS[0][1]],
  [DELTA_SEAWELL_BOUNDS[1][0], DELTA_SEAWELL_BOUNDS[1][1]]
);
layers.deltaSeawell = L.imageOverlay(DELTA_SEAWELL_URL, deltaSeawellBoundsLL, {{ opacity: 0.7 }});

// Block group choropleth
var bgMax = 0;
BLOCK_GROUPS.features.forEach(function(f) {{
  var v = f.properties.children_5_9 || 0;
  if (v > bgMax) bgMax = v;
}});

function bgStyle(f) {{
  var v = f.properties.children_5_9 || 0;
  var t = bgMax > 0 ? v / bgMax : 0;
  // YlOrRd-inspired scale
  var r = Math.round(255);
  var g = Math.round(255 - 180 * t);
  var b = Math.round(200 - 200 * t);
  return {{
    fillColor: "rgb(" + r + "," + g + "," + b + ")",
    fillOpacity: 0.5,
    color: "#999",
    weight: 0.5
  }};
}}

layers.blockGroups = L.geoJSON(BLOCK_GROUPS, {{
  style: bgStyle,
  onEachFeature: function(f, layer) {{
    var p = f.properties;
    layer.bindTooltip(
      "Children 5-9: " + (p.children_5_9 || 0)
      + "<br>Children 0-4: " + (p.children_0_4 || 0)
    );
  }}
}});

// Flood zones (Rashkis area)
layers.flood100 = L.geoJSON(FLOOD_100, {{
  style: {{ fillColor: "#6baed6", fillOpacity: 0.4, color: "#6baed6", weight: 0.5 }}
}});
layers.flood500 = L.geoJSON(FLOOD_500, {{
  style: {{ fillColor: "#bdd7e7", fillOpacity: 0.3, color: "#bdd7e7", weight: 0.5 }}
}});

// Rashkis property
layers.rashkisProperty = L.geoJSON(RASHKIS_PROP, {{
  style: {{ fillColor: "#d4edda", fillOpacity: 0.6, color: "#155724", weight: 1.5 }},
  onEachFeature: function(f, layer) {{
    layer.bindTooltip("Rashkis Elementary");
  }}
}});

// Closed-school X markers
var seawellSchool = findSchool("Seawell");
var ephesusSchool = findSchool("Ephesus");
var glenwoodSchool = findSchool("Glenwood");

if (seawellSchool) {{
  var sc = seawellSchool.geometry.coordinates;
  layers.closedXSeawell = makeClosedMarker(sc[1], sc[0]);
}}
if (ephesusSchool) {{
  var ec = ephesusSchool.geometry.coordinates;
  layers.closedXEphesus = makeClosedMarker(ec[1], ec[0]);
}}
if (glenwoodSchool) {{
  var gc = glenwoodSchool.geometry.coordinates;
  layers.closedXGlenwood = makeClosedMarker(gc[1], gc[0]);
}}

// === Zoom helpers ===
function zoomToSchool(name, padRatio) {{
  var feat = findSchool(name);
  if (!feat) return;
  var c = feat.geometry.coordinates;
  map.setView([c[1], c[0]], 14);
}}

function zoomToRashkis() {{
  var feat = findSchool("Rashkis");
  if (!feat) return;
  var c = feat.geometry.coordinates;
  map.setView([c[1], c[0]], 15);
}}

function zoomToEast() {{
  // Eastern district: Ephesus, Glenwood, Rashkis area
  var e = findSchool("Ephesus");
  var r = findSchool("Rashkis");
  if (e && r) {{
    var group = L.geoJSON({{ type: "FeatureCollection", features: [e, r] }});
    map.fitBounds(group.getBounds().pad(0.4));
  }}
}}

// === Step handler ===
var currentStep = -1;

function clearAllLayers() {{
  Object.keys(layers).forEach(function(k) {{
    if (map.hasLayer(layers[k])) map.removeLayer(layers[k]);
  }});
  dimOverlay.style.display = "none";
  document.getElementById("chart-panel").style.display = "none";
}}

function handleStep(idx) {{
  if (idx === currentStep) return;
  currentStep = idx;
  clearAllLayers();

  switch(idx) {{
    case 0: // District Overview
      layers.district.addTo(map);
      layers.schools.addTo(map);
      dimOverlay.style.display = "block";
      districtView();
      break;

    case 1: // Methodology
      layers.district.addTo(map);
      layers.baselineHeatmap.addTo(map);
      layers.schools.addTo(map);
      districtView();
      break;

    case 2: // Seawell Traffic
      layers.trafficSeawell.addTo(map);
      layers.schools.addTo(map);
      if (layers.closedXSeawell) layers.closedXSeawell.addTo(map);
      zoomToSchool("Seawell");
      break;

    case 3: // Seawell Drive-Time Delta
      layers.deltaSeawell.addTo(map);
      layers.schools.addTo(map);
      if (layers.closedXSeawell) layers.closedXSeawell.addTo(map);
      zoomToSchool("Seawell");
      break;

    case 4: // Ephesus Traffic
      layers.trafficEphesus.addTo(map);
      layers.schools.addTo(map);
      if (layers.closedXEphesus) layers.closedXEphesus.addTo(map);
      zoomToSchool("Ephesus");
      break;

    case 5: // Children 0-4 Chart
      showChildrenChart();
      break;

    case 6: // Block Group Choropleth
      layers.district.addTo(map);
      layers.blockGroups.addTo(map);
      layers.schoolsLabeled.addTo(map);
      districtView();
      break;

    case 7: // School Desert
      layers.blockGroups.addTo(map);
      layers.schoolsLabeled.addTo(map);
      layers.flood100.addTo(map);
      if (layers.closedXEphesus) layers.closedXEphesus.addTo(map);
      if (layers.closedXGlenwood) layers.closedXGlenwood.addTo(map);
      zoomToEast();
      break;

    case 8: // Rashkis Vulnerability
      layers.flood500.addTo(map);
      layers.flood100.addTo(map);
      layers.rashkisProperty.addTo(map);
      layers.schools.addTo(map);
      zoomToRashkis();
      break;

    case 9: // Summary
      layers.district.addTo(map);
      layers.schools.addTo(map);
      dimOverlay.style.display = "block";
      districtView();
      break;
  }}
}}

// === Scrollama ===
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
setTimeout(function() {{ handleStep(0); }}, 100);
</script>
<style>
.school-label-tip {{
  background: rgba(255,255,255,0.85);
  border: none;
  box-shadow: 0 1px 3px rgba(0,0,0,0.2);
  font-size: 11px;
  font-weight: bold;
  padding: 2px 6px;
}}
</style>
</body>
</html>"""

    return html


# ---------------------------------------------------------------------------
# Main orchestrator
# ---------------------------------------------------------------------------
def main():
    parser = argparse.ArgumentParser(
        description="Generate school closure scenarios editorial scrollytelling page"
    )
    parser.add_argument("--cache-only", action="store_true",
                        help="Only use cached data (default behavior)")
    parser.parse_args()

    print("=" * 60)
    print("School Closure Scenarios: Editorial Story Generator")
    print("=" * 60)

    # [1/10] Load schools
    print("\n[1/10] Loading school locations ...")
    schools = load_schools()
    schools_gdf = gpd.GeoDataFrame(
        schools,
        geometry=gpd.points_from_xy(schools.lon, schools.lat),
        crs=CRS_WGS84,
    )
    schools_json = gdf_to_geojson_str(schools_gdf, properties=["school"])
    _progress(f"Loaded {len(schools)} schools")

    # [2/10] District boundary
    print("[2/10] Loading district boundary ...")
    district = load_district_boundary()
    district_json = gdf_to_geojson_str(district, simplify_m=50)

    # [3/10] Road graph → GeoJSON for traffic overlay
    print("[3/10] Loading road network ...")
    graph_geojson, edge_id_map = load_road_graph()
    _progress(f"Road graph: {len(graph_geojson['features'])} edges")

    # [4/10] Traffic delta GeoJSON
    print("[4/10] Computing traffic deltas ...")
    traffic = load_traffic_data()
    traffic_seawell_json = compute_traffic_delta(traffic, "no_seawell", graph_geojson)
    traffic_ephesus_json = compute_traffic_delta(traffic, "no_ephesus", graph_geojson)
    seawell_edges = count_significant_edges(traffic, "no_seawell")
    ephesus_edges = count_significant_edges(traffic, "no_ephesus")
    _progress(f"Seawell closure: {seawell_edges} significant edges")
    _progress(f"Ephesus closure: {ephesus_edges} significant edges")

    # [5/10] Drive-time assignments → heatmaps
    print("[5/10] Computing drive-time heatmaps ...")
    assignments = load_assignments()

    baseline_png, baseline_bounds = compute_baseline_heatmap_png(assignments)
    _progress("Baseline heatmap generated")

    delta_seawell_png, delta_seawell_bounds = compute_drivetime_delta_png(
        assignments, "no_seawell"
    )
    _progress("Seawell drive-time delta heatmap generated")

    # [6/10] Children by nearest school
    print("[6/10] Computing children by nearest school ...")
    pixels = load_pixel_children()
    children_by_school = compute_children_by_school(pixels, assignments)
    children_chart_data = json.dumps(children_by_school, separators=(",", ":"))
    for rec in children_by_school:
        _progress(f"  {rec['school']}: {rec['children_0_4']} (0-4), {rec['children_5_9']} (5-9)")

    # [7/10] Block groups for choropleth
    print("[7/10] Loading block groups ...")
    bg = load_block_groups()
    # Clip to district
    dist_union = (district.to_crs(CRS_UTM17N).buffer(500)
                  .to_crs(CRS_WGS84))
    dist_poly = (dist_union.union_all()
                 if hasattr(dist_union, "union_all")
                 else dist_union.unary_union)
    bg_clip = gpd.clip(bg, dist_poly)
    blockgroups_json = gdf_to_geojson_str(
        bg_clip,
        properties=["GEOID", "children_0_4", "children_5_9"],
        simplify_m=20,
    )
    _progress(f"Block groups: {len(bg_clip)} features")

    # [8/10] Flood zones (clipped to Rashkis area)
    print("[8/10] Loading flood zones ...")
    flood = load_flood_zones()
    flood_100, flood_500 = classify_flood_zones(flood)

    # Clip to Rashkis + buffer area
    rashkis = schools[schools["school"].str.contains("Rashkis", case=False)]
    if len(rashkis) > 0:
        r = rashkis.iloc[0]
        rashkis_pt = Point(r.lon, r.lat)
        rashkis_buf = (gpd.GeoDataFrame(
            geometry=[rashkis_pt], crs=CRS_WGS84
        ).to_crs(CRS_UTM17N).buffer(2000).to_crs(CRS_WGS84))
        rashkis_poly = rashkis_buf.union_all() if hasattr(rashkis_buf, "union_all") else rashkis_buf.unary_union

        if len(flood_100) > 0:
            f100 = flood_100.copy()
            f100["geometry"] = f100.geometry.make_valid()
            flood_100_clip = gpd.clip(f100, rashkis_poly)
        else:
            flood_100_clip = flood_100

        if len(flood_500) > 0:
            f500 = flood_500.copy()
            f500["geometry"] = f500.geometry.make_valid()
            flood_500_clip = gpd.clip(f500, rashkis_poly)
        else:
            flood_500_clip = flood_500
    else:
        flood_100_clip = flood_100
        flood_500_clip = flood_500

    flood_100_json = gdf_to_geojson_str(flood_100_clip, simplify_m=10)
    flood_500_json = gdf_to_geojson_str(flood_500_clip, simplify_m=10)
    _progress(f"Flood zones near Rashkis: {len(flood_100_clip)} 100-yr, {len(flood_500_clip)} 500-yr")

    # [9/10] Rashkis school property
    print("[9/10] Loading Rashkis school property ...")
    school_props = load_school_properties(schools)
    rashkis_prop = school_props[
        school_props["school_name"].str.contains("Rashkis", case=False)
    ]
    rashkis_prop_json = gdf_to_geojson_str(
        rashkis_prop,
        properties=["school_name"],
        simplify_m=5,
    )
    _progress(f"Rashkis property: {len(rashkis_prop)} features")

    # [10/10] Build HTML
    print("[10/10] Building HTML ...")
    data = {
        "schools_json": schools_json,
        "district_json": district_json,
        "traffic_seawell_json": traffic_seawell_json,
        "traffic_ephesus_json": traffic_ephesus_json,
        "blockgroups_json": blockgroups_json,
        "flood_100_json": flood_100_json,
        "flood_500_json": flood_500_json,
        "rashkis_prop_json": rashkis_prop_json,
        "children_chart_data": children_chart_data,
        "baseline_png": baseline_png,
        "baseline_bounds": baseline_bounds,
        "delta_seawell_png": delta_seawell_png,
        "delta_seawell_bounds": delta_seawell_bounds,
        "seawell_edges": seawell_edges,
        "ephesus_edges": ephesus_edges,
    }
    html = build_html(data)

    OUTPUT_HTML.parent.mkdir(parents=True, exist_ok=True)
    OUTPUT_HTML.write_text(html, encoding="utf-8")
    size_mb = OUTPUT_HTML.stat().st_size / (1024 * 1024)
    print(f"\nSaved -> {OUTPUT_HTML}  ({size_mb:.1f} MB)")
    print("Done!")


if __name__ == "__main__":
    main()
