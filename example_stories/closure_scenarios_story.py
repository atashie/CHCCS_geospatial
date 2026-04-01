"""Generate an editorial scrollytelling page on school closure scenarios.

Third and final story in the Ephesus-focused editorial series. Examines
student movement under closure scenarios — traffic redistribution,
transportation costs, and the school desert risk of closing eastern schools.

Siloed in example_stories/ to keep editorial content separate from neutral
methodology pages in src/.

Architecture mirrors environmental_conditions_story.py: two-column layout
(45% narrative / 55% Leaflet map) with Scrollama-driven step transitions.

Traffic visualization directly replicates the workflow from
school_closure_analysis.py / school_closure_analysis.html:
  - Full road network GeoJSON (ALL edges from _graph_to_geojson_with_ids)
  - Dense Float32Array traffic per edge, base64-encoded
  - Absolute traffic view (sequential YlOrRd, p95 normalization)
  - Shows ~5,300 connected edges per scenario (not sparse diff view)

Usage:
    python example_stories/closure_scenarios_story.py
    python example_stories/closure_scenarios_story.py --cache-only

Output:
    example_stories/closure_scenarios.html
"""

from __future__ import annotations

import argparse
import base64
import json
import sys
import warnings
from pathlib import Path

import geopandas as gpd
import numpy as np
import pandas as pd
from shapely.geometry import Point, box, mapping

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

WALK_BIKE_TALLY_PNG = PROJECT_ROOT / "assets" / "charts" / "safe_routes_walk_bike_tally.png"
SCHOOL_CSV = DATA_CACHE / "nces_school_locations.csv"
DISTRICT_CACHE = DATA_CACHE / "chccs_district_boundary.gpkg"
TRAFFIC_CSV = DATA_PROCESSED / "school_closure_traffic.csv"
ASSIGNMENTS_CSV = DATA_PROCESSED / "school_closure_assignments.csv"
PIXEL_CHILDREN_CSV = DATA_CACHE / "closure_analysis" / "pixel_children.csv"
NETWORK_GRAPHML = DATA_CACHE / "network_drive.graphml"
BLOCKGROUPS_GPKG = DATA_CACHE / "tiger_blockgroups_orange.gpkg"
CHILDREN_BG_CSV = DATA_CACHE / "closure_analysis" / "children_blockgroups.csv"
GRID_CSV = DATA_PROCESSED / "school_desert_grid.csv"

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
}
DEFAULT_COLOR = "#888888"

# 11-school color map for walk-zone fills
ZONE_COLORS = {
    "Carrboro Elementary": "#e41a1c",
    "Ephesus Elementary": "#C62828",
    "Estes Hills Elementary": "#377eb8",
    "Frank Porter Graham Bilingue": "#FF8F00",
    "Glenwood Elementary": "#2E7D32",
    "McDougle Elementary": "#984ea3",
    "Morris Grove Elementary": "#ff7f00",
    "Northside Elementary": "#a65628",
    "Rashkis Elementary": "#f781bf",
    "Scroggs Elementary": "#66c2a5",
    "Seawell Elementary": "#1565C0",
}

# UNC Carolina Demography / Carolina Population Center (PMR2 Forecast)
# Pre-Woolpert capacity figures
ENROLLMENT_PROJECTIONS = [
    {"school": "Carrboro Elementary",              "capacity": 518, "enroll_2030": 398, "util_2030": 77},
    {"school": "Ephesus Elementary",               "capacity": 436, "enroll_2030": 375, "util_2030": 86},
    {"school": "Estes Hills Elementary",           "capacity": 516, "enroll_2030": 359, "util_2030": 70},
    {"school": "Frank Porter Graham Bilingue",     "capacity": 522, "enroll_2030": 508, "util_2030": 97},
    {"school": "Glenwood Elementary",              "capacity": 412, "enroll_2030": 420, "util_2030": 102},
    {"school": "McDougle Elementary",              "capacity": 548, "enroll_2030": 517, "util_2030": 94},
    {"school": "Morris Grove Elementary",          "capacity": 568, "enroll_2030": 340, "util_2030": 60},
    {"school": "Northside Elementary",             "capacity": 568, "enroll_2030": 299, "util_2030": 53},
    {"school": "Rashkis Elementary",               "capacity": 568, "enroll_2030": 254, "util_2030": 45},
    {"school": "Scroggs Elementary",               "capacity": 558, "enroll_2030": 286, "util_2030": 51},
    {"school": "Seawell Elementary",               "capacity": 450, "enroll_2030": 325, "util_2030": 72},
]


# ---------------------------------------------------------------------------
# Utility functions
# ---------------------------------------------------------------------------
def _progress(msg: str):
    print(f"  ... {msg}")


def _encode_image(path: Path) -> str:
    """Base64-encode an image file for inline embedding."""
    if not path.exists():
        return ""
    data = path.read_bytes()
    return base64.b64encode(data).decode("ascii")


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


WORKING_MAP_HTML = PROJECT_ROOT / "assets" / "maps" / "school_closure_analysis.html"


def extract_from_working_map() -> dict:
    """Extract ROAD_GEOJSON and traffic base64 strings.

    Tries two sources in order:
    1. The existing closure_scenarios.html (if it already has embedded data)
    2. The working school_closure_analysis.html (legacy format)
    """
    import re

    needed_keys = [
        "baseline|nearest|0_4",
        "baseline|nearest|5_9",
        "no_seawell|nearest|0_4",
        "no_seawell|nearest|5_9",
        "no_ephesus|nearest|0_4",
        "no_ephesus|nearest|5_9",
    ]

    # Try existing output HTML first (it already has the data we need)
    for source in [OUTPUT_HTML, WORKING_MAP_HTML]:
        if not source.exists():
            continue
        html = source.read_text(encoding="utf-8")

        road_match = re.search(
            r"var ROAD_GEOJSON = (\{.*?\});\s*$", html, re.MULTILINE
        )
        if not road_match:
            # Try NETWORK_GEOJSON (newer format — dict keyed by mode)
            net_match = re.search(
                r'var NETWORK_GEOJSON = (\{"drive":\{.*?\})\};', html
            )
            if net_match:
                # Extract just the drive sub-object
                try:
                    full = json.loads(net_match.group(1) + "}")
                    road_match_str = json.dumps(
                        full["drive"], separators=(",", ":")
                    )
                except Exception:
                    continue
            else:
                continue
        else:
            road_match_str = road_match.group(1)

        traffic_match = re.search(
            r"var TRAFFIC_(?:ARRAYS_)?B64 = (\{.*?\});\s*$",
            html, re.MULTILINE,
        )
        if not traffic_match:
            continue
        traffic_arrays = json.loads(traffic_match.group(1))

        n_edges_match = re.search(r"var N_EDGES = (\d+);", html)
        n_edges = int(n_edges_match.group(1)) if n_edges_match else None

        subset = {}
        missing = False
        for k in needed_keys:
            if k not in traffic_arrays:
                missing = True
                break
            subset[k] = traffic_arrays[k]
        if missing:
            continue

        _progress(f"Extracted data from {source.name}")
        return {
            "road_geojson_str": road_match_str,
            "traffic_b64": subset,
            "n_edges": n_edges,
        }

    raise FileNotFoundError(
        "Could not extract road/traffic data from any source.\n"
        "Ensure closure_scenarios.html or school_closure_analysis.html "
        "contains embedded ROAD_GEOJSON and TRAFFIC_B64 data."
    )


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


def build_nearest_walk_zones(district: gpd.GeoDataFrame) -> gpd.GeoDataFrame:
    """Build dissolved nearest-walk-time zone polygons from school_desert_grid.csv.

    Replicates the _build_nearest_zones pattern from
    school_socioeconomic_analysis.py: reads baseline/walk rows, buffers each
    grid point by 55 m squares, dissolves by nearest_school, clips to the
    district boundary, and adds a color property from ZONE_COLORS.
    """
    if not GRID_CSV.exists():
        raise FileNotFoundError(
            f"Grid CSV not found: {GRID_CSV}\n"
            "Run: python src/school_desert.py"
        )
    df = pd.read_csv(GRID_CSV)
    df = df[(df["scenario"] == "baseline") & (df["mode"] == "walk")].copy()
    df = df.dropna(subset=["nearest_school"])
    if df.empty:
        raise RuntimeError("No baseline/walk rows with nearest_school in grid CSV")

    pts = gpd.GeoDataFrame(
        df, geometry=gpd.points_from_xy(df["lon"], df["lat"]), crs=CRS_WGS84,
    ).to_crs(CRS_UTM17N)

    half = 55
    pts["geometry"] = [box(g.x - half, g.y - half, g.x + half, g.y + half)
                       for g in pts.geometry]
    dissolved = pts.dissolve(by="nearest_school").reset_index()
    dissolved = dissolved.rename(columns={"nearest_school": "school"})

    dist_utm = district.to_crs(CRS_UTM17N)
    dissolved = gpd.clip(dissolved, dist_utm)
    mask = dissolved.geometry.geom_type.isin(["Polygon", "MultiPolygon"])
    dissolved = dissolved[mask].copy()

    dissolved = dissolved[["school", "geometry"]].to_crs(CRS_WGS84)
    dissolved["color"] = dissolved["school"].map(ZONE_COLORS).fillna("#888")
    _progress(f"Built {len(dissolved)} nearest-walk zones")
    return dissolved


# ---------------------------------------------------------------------------
# Traffic metrics (computed from extracted base64 arrays)
# ---------------------------------------------------------------------------
def count_significant_edges(diff_arr: np.ndarray, threshold: float = 3.0) -> int:
    """Count edges with |diff| >= threshold."""
    return int(np.sum(np.abs(diff_arr) >= threshold))


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
# Find top-delta roads for narrative
# ---------------------------------------------------------------------------
def find_road_deltas(diff_arr: np.ndarray,
                     graph_geojson: dict,
                     road_names: list,
                     max_roads: set = None) -> list:
    """Get peak traffic change for specific named roads.

    Returns list of dicts: [{"name": str, "delta": float}, ...]
    in the same order as road_names. For each road, reports the
    single edge with the largest absolute change — unless the road
    is in *max_roads*, in which case it reports the most-positive
    delta (highest traffic increase).
    """
    if max_roads is None:
        max_roads = set()
    road_peak = {}  # name → delta with largest |delta| (or max delta)
    name_set = set(road_names)
    for i in range(min(len(diff_arr), len(graph_geojson["features"]))):
        delta = float(diff_arr[i])
        name = graph_geojson["features"][i]["properties"].get("name", "")
        if name in name_set:
            if name in max_roads:
                # Use most-positive delta for max_roads
                if name not in road_peak or delta > road_peak[name]:
                    road_peak[name] = delta
            else:
                if name not in road_peak or abs(delta) > abs(road_peak[name]):
                    road_peak[name] = delta
    return [{"name": n, "delta": round(road_peak.get(n, 0), 1)}
            for n in road_names if n in road_peak]


# ---------------------------------------------------------------------------
# HTML builder
# ---------------------------------------------------------------------------
def build_html(data: dict) -> str:
    """Build the complete HTML page from pre-computed data."""
    def _format_top_roads(roads):
        if not roads:
            return ""
        items = []
        for r in roads:
            direction = "increase" if r["delta"] > 0 else "decrease"
            items.append(
                f'<li><strong>{r["name"]}</strong>: '
                f'{direction} in student traffic</li>'
            )
        return (
            '<p style="margin-bottom:4px;">Roads with the largest traffic change:</p>'
            '<ul style="margin:0 0 12px 20px;line-height:1.7;font-size:0.92em;">'
            + "".join(items) + "</ul>"
        )

    seawell_roads_59 = _format_top_roads(data.get("seawell_top_roads_59", []))
    seawell_roads_04 = _format_top_roads(data.get("seawell_top_roads_04", []))
    ephesus_roads_59 = _format_top_roads(data.get("ephesus_top_roads_59", []))
    ephesus_roads_04 = _format_top_roads(data.get("ephesus_top_roads_04", []))

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

.traffic-legend {{
  display: flex; gap: 16px; align-items: center;
  margin: 12px 0; padding: 10px 14px;
  background: #f9f9f9; border-radius: 6px;
  font-size: 0.85em;
}}
.traffic-legend-item {{
  display: flex; align-items: center; gap: 4px;
}}
.traffic-legend-swatch {{
  display: inline-block; width: 24px; height: 4px; border-radius: 2px;
}}

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

  <!-- Step 0: Introduction — From Demographics to Movement -->
  <div class="step" data-step="0">
    <div class="step-number">1</div>
    <h2>School Closure Impact on Travel Patterns</h2>
    <p>While another story
    examined <em>who lives where</em> across the district, this story
    examines <strong>what happens when schools close and children must
    relocate</strong>.</p>
    <p><strong>Transportation costs</strong> are a specific school closure
    criterion. We analyze those costs by examining traffic burden
    and the conversion of walkers to car or bus riders.
    The maps that follow visualize where students are currently enrolled
    and which schools have capacity to absorb them under closure
    scenarios.</p>

    <details>
      <summary>How we measure impact</summary>
      <p>For every 100-meter pixel in the district, Dijkstra&rsquo;s
      shortest-path algorithm computes driving time to each school via the
      OpenStreetMap road network. When a school closes, students route to
      their next-nearest school. Census child counts (ACS) are distributed
      to each pixel using dasymetric allocation, then traced along shortest
      paths to estimate traffic volume per road segment.</p>
    </details>

    <div class="limitation">
      <strong>Note:</strong> Glenwood Elementary is a
      district-wide magnet school
      whose programs serve students from across the district rather than
      a specific neighborhood. Because closure scenarios depend on
      neighborhood-level travel patterns, Glenwood is not highlighted in this
      analysis.
    </div>
  </div>

  <!-- Step 1: Capacity Overview -->
  <div class="step" data-step="1">
    <div class="step-number">2</div>
    <h2>Future School Capacity at a Glance</h2>
    <p>Enrollment numbers help inform current and future transportation
    costs. <span class="seawell-label">Seawell</span> has the lowest
    projected enrollment number among the schools considered for closure
    with 325 in the year 2030. Therefore, Seawell may have the fewest
    students affected by closure, indicating the fewest displaced
    students. LEAP is a districtwide program for 4th and 5th graders
    that already pulls many of these students from other attendance
    zones.</p>

    <div class="source">
      <strong>Source:</strong> Projected 2030 enrollment from the
      PMR2 Forecast (UNC Carolina Demography); capacity figures are
      pre-Woolpert.
    </div>
    <div class="limitation">
      <strong>Limitation:</strong> These are projections, not actuals.
      Actual numbers may differ.
    </div>
  </div>

  <!-- Step 2: Walker Conversion / Transportation Costs -->
  <div class="step" data-step="2">
    <div class="step-number">3</div>
    <h2>Transportation: Who Can Walk?</h2>
    <p>Transportation costs can also be examined by the net change in
    students needing a change in mode of transportation. The biggest
    increase in cost would be the conversion of walk zone students to
    school bus eligible students.</p>
    <p>The 2025 Chapel Hill Safe Routes to School Action Plan measured
    how many students live within 0.5 miles of each school:</p>
    <ul style="margin:8px 0 12px 20px;line-height:1.8;">
      <li><span class="ephesus-label">Ephesus</span>: highest in district
        with <strong>99 students</strong></li>
      <li><span class="seawell-label">Seawell</span>: lowest in district
        with <strong>0 students</strong></li>
    </ul>

    <div class="insight">
      <strong>Closure cost asymmetry:</strong> Closing
      <span class="ephesus-label">Ephesus</span> converts at least 99
      walkers into bus/car riders. Given that the majority of
      <span class="seawell-label">Seawell</span> students live &gt;1 mile
      away, the expectation is that there will be significantly fewer
      conversions.
    </div>

  </div>

  <!-- Step 3: Transportation Cost Estimate -->
  <div class="step" data-step="3">
    <div class="step-number">3b</div>
    <h2>Estimating Added Transportation Costs</h2>
    <p>On average, it costs <strong>$900 per student annually</strong>
    for a bus rider (NC state average values). If we converted all
    students potentially eligible for walking (living within 1 mile of
    school) to bus riders, we can roughly calculate the added
    transportation cost for a school closure.</p>

    <h3><span class="ephesus-label">Ephesus</span> Closure</h3>
    <p>99 students live within 0.5 miles; 93 live within 0.5&ndash;1 mile.<br>
    192 total students &times; $900 = <strong>$172,800</strong> added
    annually</p>

    <h3><span class="seawell-label">Seawell</span> Closure</h3>
    <p>0 students live within 0.5 miles; 60 live within 0.5&ndash;1 mile.<br>
    60 total students &times; $900 = <strong>$54,000</strong> added
    annually</p>

    <div class="insight">
      <strong>Summary:</strong> The transportation costs are
      significant &mdash; closing Ephesus costs roughly
      <strong>$119,000 more per year</strong> in new bus riders alone
      compared to closing Seawell.
    </div>

    <p>Importantly, these figures represent only the
    <strong>net change</strong> &mdash; the additional cost of
    converting current walkers to bus riders. The total transportation
    cost for either closure would be even greater, as it includes
    rerouting all displaced students, not just those who currently
    walk.</p>

    <p>In particular, given Ephesus&rsquo;s increasing number of
    school-aged children &mdash; the number of children aged
    0&ndash;4 is greater than the number aged 5&ndash;9, as seen in
    the Demographics story &mdash; the additional
    <strong>net change</strong> in costs may be expected to increase
    in coming years.</p>
  </div>

  <!-- Step 4: Random Sampling of Travel Mode -->
  <div class="step" data-step="4">
    <div class="step-number">4</div>
    <h2>Random Sampling of Travel Mode</h2>
    <p>Proximity to school does not always capture travel mode choice.
    However, random sampling shows that
    <span class="ephesus-label">Ephesus</span> has the second most
    active travelers (walkers + bikers) in Chapel Hill elementary
    schools (comparable data for elementary schools in Carrboro were
    not readily available).</p>

    <div class="source">
      <strong>Source:</strong> Chapel Hill Safe Routes to School Action Plan
      (adopted June 11, 2025) &mdash; arrival/dismissal tally counts
      (Fall 2024)
    </div>
  </div>

  <!-- Step 5: Methodology — Reading the traffic maps -->
  <div class="step" data-step="5">
    <div class="step-number">5</div>
    <h2>From Walkers to Roads: How We Model Traffic</h2>

    <h3>Reading the traffic maps</h3>
    <p>The maps on the following slides color each road by how much its
    student traffic changes when a school closes:</p>
    <div class="traffic-legend" style="flex-direction:column;gap:8px;align-items:flex-start;">
      <div style="display:flex;align-items:center;gap:8px;">
        <div style="width:80px;height:6px;border-radius:3px;background:linear-gradient(to right, rgb(255,255,204), rgb(254,178,76), rgb(240,59,32), rgb(189,0,38));"></div>
        <span style="font-size:0.85em;"><strong>Red/orange</strong> &mdash;
          more children on this road after closure</span>
      </div>
      <div style="display:flex;align-items:center;gap:8px;">
        <div style="width:80px;height:6px;border-radius:3px;background:rgb(49,130,189);"></div>
        <span style="font-size:0.85em;"><strong>Blue</strong> &mdash;
          fewer children (traffic that used to go to the closed school
          disappears from its access roads)</span>
      </div>
      <div style="display:flex;align-items:center;gap:8px;">
        <div style="width:80px;height:2px;background:transparent;"></div>
        <span style="font-size:0.85em;">Thicker lines = larger change.
          Thin, faint lines = small change.</span>
      </div>
    </div>

  </div>

  <!-- Step 6: Seawell Closure — Children 5-9 -->
  <div class="step" data-step="6">
    <div class="step-number">6</div>
    <h2>Seawell Closure: Current Students (Ages 5&ndash;9)</h2>
    <p>If <span class="seawell-label">Seawell</span> closes, its
    elementary-age students (5&ndash;9) redistribute to nearby schools.
    The map shows the <em>change</em> in student traffic compared to the
    baseline (all schools open):</p>
    <div class="traffic-legend">
      <div class="traffic-legend-item">
        <span class="traffic-legend-swatch" style="background:rgb(215,48,39);"></span>
        More children on road
      </div>
      <div class="traffic-legend-item">
        <span class="traffic-legend-swatch" style="background:rgb(49,130,189);"></span>
        Fewer children on road
      </div>
    </div>
    <div class="note" style="background:#f0f4f8;border-left:3px solid #90a4ae;padding:10px 14px;margin:12px 0;font-size:0.88em;color:#546e7a;border-radius:0 4px 4px 0;">
      <strong>Reading the map:</strong> The faint, thin lines on the map
      indicate that traffic impacts are light &mdash; the lighter and thinner
      the line, the smaller the change in student traffic on that road segment.
    </div>
    {seawell_roads_59}
    <div class="limitation">
      <strong>LEAP program context (editorial):</strong> Seawell currently
      hosts the LEAP program &mdash; a district-wide accelerated learning
      program where students are already bussed from across the district.
      These students already travel long distances; closure shifts their
      routes but doesn&rsquo;t fundamentally change their travel burden.
      The inconvenience falls most heavily on <em>attendance zone
      families</em> who currently walk or drive short distances. No LEAP
      enrollment data exists in this dataset &mdash; this context is
      editorial, not computed.
    </div>
  </div>

  <!-- Step 7: Seawell Closure — Children 0-4 -->
  <div class="step" data-step="7">
    <div class="step-number">7</div>
    <h2>Seawell Closure: Future Students (Ages 0&ndash;4)</h2>
    <p>Now the same scenario viewed through the lens of children under 5
    &mdash; future kindergarteners who will need school capacity in
    coming years.</p>
    <div class="note" style="background:#f0f4f8;border-left:3px solid #90a4ae;padding:10px 14px;margin:12px 0;font-size:0.88em;color:#546e7a;border-radius:0 4px 4px 0;">
      <strong>Reading the map:</strong> The faint, thin lines on the map
      indicate that traffic impacts are light &mdash; the lighter and thinner
      the line, the smaller the change in student traffic on that road segment.
    </div>
    {seawell_roads_04}
    <p>The pattern is similar to the 5&ndash;9 analysis but at lower
    magnitude. Once again, Seawell&rsquo;s zone is not expected to have as
    high an enrollment of elementary-age students per ACS Census data
    &mdash; fewer young children live near Seawell compared to schools in
    the eastern district.</p>
    <p>For a deeper look at the demographic patterns behind these numbers,
    see the Socioeconomic Demographics story.</p>
  </div>

  <!-- Step 8: Ephesus Closure — Children 5-9 -->
  <div class="step" data-step="8">
    <div class="step-number">8</div>
    <h2>Ephesus Closure: Current Students (Ages 5&ndash;9)</h2>
    <p>If <span class="ephesus-label">Ephesus</span> closes, the
    traffic redistribution is substantially wider. The map shows changes
    for elementary-age children (5&ndash;9):</p>
    <div class="traffic-legend">
      <div class="traffic-legend-item">
        <span class="traffic-legend-swatch" style="background:rgb(215,48,39);"></span>
        More children on road
      </div>
      <div class="traffic-legend-item">
        <span class="traffic-legend-swatch" style="background:rgb(49,130,189);"></span>
        Fewer children on road
      </div>
    </div>
    {ephesus_roads_59}
    <p>The wider spread of affected roads reflects Ephesus&rsquo;s position
    in one of the most population-dense areas of the district.</p>

    <p>Notice that the routing algorithm redirects most of the southern
    Ephesus attendance zone toward
    <span class="glenwood-label">Glenwood</span> rather than Rashkis, even
    though Rashkis is geographically closer. This is because Glenwood is
    more readily accessible via the road network, while Rashkis is nestled
    at the back of a subdivision with limited through-routes.</p>

    <p><u>This has a compounding implication.</u> Closing
    <span class="glenwood-label">Glenwood</span> <em>in addition to</em>
    <span class="ephesus-label">Ephesus</span> would impact high
    population-density areas with a high proportion of young children.</p>

    <div class="limitation">
      <strong>Attendance zone overlap:</strong> A significant portion of
      Rashkis&rsquo;s current attendance zone is &ldquo;borrowed&rdquo;
      from the Ephesus drive zone &mdash; meaning students in those areas
      already share the same traffic corridors. Closing Ephesus would
      concentrate even more student traffic onto those already-shared roads.
    </div>
  </div>

  <!-- Step 9: Ephesus Closure — Children 0-4 -->
  <div class="step" data-step="9">
    <div class="step-number">9</div>
    <h2>Ephesus Closure: Future Students (Ages 0&ndash;4)</h2>
    <p>For children under 5, the Ephesus closure creates even larger
    per-road impacts than the 5&ndash;9 analysis. As rising kindergarteners
    enter elementary school, the traffic burden from an Ephesus closure would
    be expected to grow worse over time &mdash; not better.</p>
    {ephesus_roads_04}
    <p>These are future kindergarteners &mdash; removing capacity in
    Ephesus&rsquo;s zone means removing it where future demand is
    greatest.</p>
    <div class="insight">
      <strong>Comparing closure scenarios:</strong> Across both age groups,
      closing <span class="ephesus-label">Ephesus</span> produces a wider
      spread of affected roads, higher per-road traffic increases, and
      concentrates the burden on already-busy corridors. Closing
      <span class="seawell-label">Seawell</span> redistributes fewer
      students across a smaller, less congested portion of the network.
      The traffic evidence reinforces the transportation cost gap identified
      by the walkability analysis.
    </div>
  </div>

  <!-- Step 11: Summary — What the Data Shows -->
  <div class="step" data-step="11">
    <div class="step-number">10</div>
    <h2>What the Data Shows</h2>
    <p>Three key findings from the closure analysis:</p>
    <ol style="margin:8px 0 12px 20px;line-height:1.8;">
      <li><strong>Ephesus closure creates larger traffic impacts than
        Seawell</strong> across the road network</li>
      <li><strong>Closing Ephesus converts significantly more walkers to
        bus/car riders</strong> than closing Seawell</li>
      <li><strong>Closing eastern schools (Ephesus and Glenwood) creates a school desert</strong>
        in the most child-dense part of the district</li>
    </ol>
    <p style="margin-top:16px;font-size:0.85em;color:#888;">
      <strong>Data sources:</strong> NCES EDGE 2023-24 &bull; ACS 5-Year
      &bull; OpenStreetMap road network &bull;
      Orange County parcel data &bull;
      UNC Carolina Demography (PMR2 Forecast)
    </p>
    <p style="margin-top:24px;font-size:0.9em;color:#666;text-align:center;">
      Return to the homepage for interactive maps and full methodology guides.
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
  <div id="walker-panel" style="position:absolute;top:0;left:0;width:100%;height:100%;
    z-index:500;background:#fff;display:none;overflow-y:auto;padding:40px 28px;">
    <div style="text-align:center;margin:0 0 16px;">
      <h3 style="margin:0 0 4px;font-size:1.15em;color:#333;">Students within 0.5 miles of school</h3>
      <p style="margin:0;font-size:0.82em;color:#777;">Chapel Hill Safe Routes to School Action Plan (2025)</p>
    </div>
    <div id="walker-bars" style="max-width:500px;margin:0 auto;">
      <div style="display:flex;align-items:center;margin:6px 0;">
        <div style="width:110px;font-size:0.85em;font-weight:bold;color:#C62828;">Ephesus</div>
        <div style="flex:1;background:#eee;border-radius:3px;height:22px;position:relative;">
          <div style="width:100%;background:#C62828;height:100%;border-radius:3px;"></div>
          <span style="position:absolute;right:6px;top:2px;font-size:0.8em;color:#fff;font-weight:bold;">99 (24.7%)</span>
        </div>
      </div>
      <div style="display:flex;align-items:center;margin:6px 0;">
        <div style="width:110px;font-size:0.85em;color:#333;">Scroggs</div>
        <div style="flex:1;background:#eee;border-radius:3px;height:22px;position:relative;">
          <div style="width:95%;background:#66c2a5;height:100%;border-radius:3px;"></div>
          <span style="position:absolute;right:6px;top:2px;font-size:0.8em;color:#333;font-weight:bold;">94 (23.2%)</span>
        </div>
      </div>
      <div style="display:flex;align-items:center;margin:6px 0;">
        <div style="width:110px;font-size:0.85em;color:#333;">Rashkis</div>
        <div style="flex:1;background:#eee;border-radius:3px;height:22px;position:relative;">
          <div style="width:66%;background:#f781bf;height:100%;border-radius:3px;"></div>
          <span style="position:absolute;right:6px;top:2px;font-size:0.8em;color:#333;font-weight:bold;">65 (14.9%)</span>
        </div>
      </div>
      <div style="display:flex;align-items:center;margin:6px 0;">
        <div style="width:110px;font-size:0.85em;color:#333;">Northside</div>
        <div style="flex:1;background:#eee;border-radius:3px;height:22px;position:relative;">
          <div style="width:65%;background:#a65628;height:100%;border-radius:3px;"></div>
          <span style="position:absolute;right:6px;top:2px;font-size:0.8em;color:#333;font-weight:bold;">64 (16.0%)</span>
        </div>
      </div>
      <div style="display:flex;align-items:center;margin:6px 0;">
        <div style="width:110px;font-size:0.85em;color:#333;">FPG</div>
        <div style="flex:1;background:#eee;border-radius:3px;height:22px;position:relative;">
          <div style="width:35%;background:#FF8F00;height:100%;border-radius:3px;"></div>
          <span style="position:absolute;right:6px;top:2px;font-size:0.8em;color:#333;font-weight:bold;">35 (6.6%)</span>
        </div>
      </div>
      <div style="display:flex;align-items:center;margin:6px 0;">
        <div style="width:110px;font-size:0.85em;color:#333;">Estes Hills</div>
        <div style="flex:1;background:#eee;border-radius:3px;height:22px;position:relative;">
          <div style="width:33%;background:#377eb8;height:100%;border-radius:3px;"></div>
          <span style="position:absolute;right:6px;top:2px;font-size:0.8em;color:#333;font-weight:bold;">33 (9.3%)</span>
        </div>
      </div>
      <div style="display:flex;align-items:center;margin:6px 0;">
        <div style="width:110px;font-size:0.85em;color:#333;">Glenwood</div>
        <div style="flex:1;background:#eee;border-radius:3px;height:22px;position:relative;">
          <div style="width:6%;background:#2E7D32;height:100%;border-radius:3px;min-width:4px;"></div>
          <span style="position:absolute;right:6px;top:2px;font-size:0.8em;color:#333;font-weight:bold;">6 (1.5%)</span>
        </div>
      </div>
      <div style="display:flex;align-items:center;margin:6px 0;">
        <div style="width:110px;font-size:0.85em;font-weight:bold;color:#1565C0;">Seawell</div>
        <div style="flex:1;background:#eee;border-radius:3px;height:22px;position:relative;">
          <span style="position:absolute;right:6px;top:2px;font-size:0.8em;color:#333;font-weight:bold;">0 (0%)</span>
        </div>
      </div>
    </div>
    <div style="text-align:center;margin:24px 0 12px;">
      <h3 style="margin:0 0 4px;font-size:1.15em;color:#333;">Students within 1 mile of school</h3>
      <p style="margin:0;font-size:0.82em;color:#777;">Chapel Hill Safe Routes to School Action Plan (2025)</p>
    </div>
    <div id="walker-bars-1mi" style="max-width:500px;margin:0 auto;">
      <div style="display:flex;align-items:center;margin:6px 0;">
        <div style="width:110px;font-size:0.85em;font-weight:bold;color:#C62828;">Ephesus</div>
        <div style="flex:1;background:#eee;border-radius:3px;height:22px;position:relative;">
          <div style="width:100%;background:#C62828;height:100%;border-radius:3px;"></div>
          <span style="position:absolute;right:6px;top:2px;font-size:0.8em;color:#fff;font-weight:bold;">192 (47.9%)</span>
        </div>
      </div>
      <div style="display:flex;align-items:center;margin:6px 0;">
        <div style="width:110px;font-size:0.85em;color:#333;">Scroggs</div>
        <div style="flex:1;background:#eee;border-radius:3px;height:22px;position:relative;">
          <div style="width:100%;background:#66c2a5;height:100%;border-radius:3px;"></div>
          <span style="position:absolute;right:6px;top:2px;font-size:0.8em;color:#fff;font-weight:bold;">192 (47.4%)</span>
        </div>
      </div>
      <div style="display:flex;align-items:center;margin:6px 0;">
        <div style="width:110px;font-size:0.85em;color:#333;">Estes Hills</div>
        <div style="flex:1;background:#eee;border-radius:3px;height:22px;position:relative;">
          <div style="width:55.2%;background:#377eb8;height:100%;border-radius:3px;"></div>
          <span style="position:absolute;right:6px;top:2px;font-size:0.8em;color:#333;font-weight:bold;">106 (29.9%)</span>
        </div>
      </div>
      <div style="display:flex;align-items:center;margin:6px 0;">
        <div style="width:110px;font-size:0.85em;color:#333;">Northside</div>
        <div style="flex:1;background:#eee;border-radius:3px;height:22px;position:relative;">
          <div style="width:43.2%;background:#a65628;height:100%;border-radius:3px;"></div>
          <span style="position:absolute;right:6px;top:2px;font-size:0.8em;color:#333;font-weight:bold;">83 (20.8%)</span>
        </div>
      </div>
      <div style="display:flex;align-items:center;margin:6px 0;">
        <div style="width:110px;font-size:0.85em;color:#333;">Rashkis</div>
        <div style="flex:1;background:#eee;border-radius:3px;height:22px;position:relative;">
          <div style="width:40.6%;background:#f781bf;height:100%;border-radius:3px;"></div>
          <span style="position:absolute;right:6px;top:2px;font-size:0.8em;color:#333;font-weight:bold;">78 (17.9%)</span>
        </div>
      </div>
      <div style="display:flex;align-items:center;margin:6px 0;">
        <div style="width:110px;font-size:0.85em;color:#333;">FPG</div>
        <div style="flex:1;background:#eee;border-radius:3px;height:22px;position:relative;">
          <div style="width:49.0%;background:#FF8F00;height:100%;border-radius:3px;"></div>
          <span style="position:absolute;right:6px;top:2px;font-size:0.8em;color:#333;font-weight:bold;">94 (17.7%)</span>
        </div>
      </div>
      <div style="display:flex;align-items:center;margin:6px 0;">
        <div style="width:110px;font-size:0.85em;font-weight:bold;color:#1565C0;">Seawell</div>
        <div style="flex:1;background:#eee;border-radius:3px;height:22px;position:relative;">
          <div style="width:31.3%;background:#1565C0;height:100%;border-radius:3px;"></div>
          <span style="position:absolute;right:6px;top:2px;font-size:0.8em;color:#333;font-weight:bold;">60 (16.0%)</span>
        </div>
      </div>
      <div style="display:flex;align-items:center;margin:6px 0;">
        <div style="width:110px;font-size:0.85em;color:#333;">Glenwood</div>
        <div style="flex:1;background:#eee;border-radius:3px;height:22px;position:relative;">
          <div style="width:14.6%;background:#2E7D32;height:100%;border-radius:3px;min-width:4px;"></div>
          <span style="position:absolute;right:6px;top:2px;font-size:0.8em;color:#333;font-weight:bold;">28 (7.0%)</span>
        </div>
      </div>
    </div>
    <div class="source" style="margin:20px 24px 0;">
      <strong>Source:</strong> Chapel Hill Safe Routes to School Action Plan
      (adopted June 11, 2025; funded by NCDOT SRTS grant)
    </div>
  </div>
  <div id="tally-panel" style="position:absolute;top:0;left:0;width:100%;height:100%;
    z-index:500;background:#fff;display:none;overflow-y:auto;padding:40px 28px;
    display:none;align-items:center;justify-content:center;">
    <div style="text-align:center;">
      <h3 style="margin:0 0 8px;font-size:1.15em;color:#333;">Walk &amp; Bike Tally Counts by School</h3>
      <p style="margin:0 0 16px;font-size:0.82em;color:#777;">Arrival/dismissal random tally (Fall 2024)</p>
      <img src="data:image/png;base64,{data["walk_bike_tally_b64"]}"
        alt="Walk and Bike tally counts by school"
        style="max-width:95%;border-radius:6px;box-shadow:0 1px 4px rgba(0,0,0,0.12);" />
      <p style="font-size:0.8em;color:#888;margin-top:8px;">
        Source: Chapel Hill Safe Routes to School Action Plan (adopted June 11, 2025)</p>
    </div>
  </div>
</div>

<script src="https://unpkg.com/leaflet@1.9.3/dist/leaflet.js"></script>
<script src="https://unpkg.com/scrollama@3.2.0/build/scrollama.min.js"></script>
<script>
// === Embedded data ===
var SCHOOLS = {data["schools_json"]};
var DISTRICT = {data["district_json"]};
var ROAD_GEOJSON = {data["road_geojson"]};
var BLOCK_GROUPS = {data["blockgroups_json"]};
var CHILDREN_DATA = {data["children_chart_data"]};
var ENROLLMENT_DATA = {data["enrollment_json"]};
var WALK_ZONES = {data["walk_zones_json"]};

// Traffic base64 arrays — extracted directly from school_closure_analysis.html
var TRAFFIC_B64 = {json.dumps(data["traffic_b64"])};
var DIFF_CLAMP = 300;
var N_EDGES = {data["n_edges"]};

var SCHOOL_COLORS = {{
  "Ephesus Elementary": "#C62828",
  "Glenwood Elementary": "#2E7D32",
  "Seawell Elementary": "#1565C0"
}};
var DEFAULT_COLOR = "#888888";

// === Decode base64 Float32Array — identical to school_closure_analysis.html ===
function b64ToFloat32(b64) {{
  var raw = atob(b64);
  var buf = new ArrayBuffer(raw.length);
  var u8 = new Uint8Array(buf);
  for (var i = 0; i < raw.length; i++) u8[i] = raw.charCodeAt(i);
  return new Float32Array(buf);
}}

// === trafficColor — IDENTICAL to school_closure_analysis.html ===
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

// === Road layer — IDENTICAL pattern to school_closure_analysis.html ===
// Created ONCE with transparent style, then restyled via eachLayer(setStyle)
var roadLayer = L.geoJSON(ROAD_GEOJSON, {{
  style: {{ color: 'transparent', weight: 0, opacity: 0 }},
  onEachFeature: function(feature, layer) {{
    layer.on('mouseover', function(e) {{
      var idx = feature.properties.idx;
      var name = feature.properties.name || 'Unnamed road';
      if (currentTrafficArr && idx < currentTrafficArr.length) {{
        var val = currentTrafficArr[idx];
        if (val !== 0) {{
          var tip = name + ': ' + val.toFixed(1) + ' children';
          layer.bindTooltip(tip).openTooltip();
        }}
      }}
    }});
  }}
}});
var currentTrafficArr = null;

// === Apply traffic styling to roadLayer — same as updatePart2() ===
// nearest routing, diff view — matching working map
function showTrafficDiff(scenarioKey, ageGroup) {{
  var ag = ageGroup || '0_4';
  var scenArr = b64ToFloat32(TRAFFIC_B64[scenarioKey + '|nearest|' + ag]);
  var baseArr = b64ToFloat32(TRAFFIC_B64['baseline|nearest|' + ag]);

  // Compute diff: scenario - baseline (identical to working map)
  var diffArr = new Float32Array(N_EDGES);
  for (var i = 0; i < N_EDGES; i++) {{
    diffArr[i] = scenArr[i] - baseArr[i];
  }}

  // Apply style to each road via eachLayer — identical to working map
  roadLayer.eachLayer(function(layer) {{
    var idx = layer.feature.properties.idx;
    var val = idx < diffArr.length ? diffArr[idx] : 0;
    layer.setStyle(trafficColor(val, DIFF_CLAMP, true));
  }});
  currentTrafficArr = diffArr;
}}

// === Reset road layer to transparent ===
function hideTraffic() {{
  roadLayer.eachLayer(function(layer) {{
    layer.setStyle({{ color: 'transparent', weight: 0, opacity: 0 }});
  }});
  currentTrafficArr = null;
}}

// === Bar chart builder ===
function renderBars(containerId, data, metric) {{
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
    html += '<div style="display:flex;align-items:center;margin:4px 0;font-size:0.82em;padding:2px 4px;border-radius:4px;">'
      + '<div style="width:140px;text-align:right;padding-right:8px;color:' + fontColor + ';font-weight:' + fontWeight + ';white-space:nowrap;overflow:hidden;text-overflow:ellipsis;">'
      + label + '</div>'
      + '<div style="flex:1;background:#eee;border-radius:3px;height:18px;position:relative;">'
      + '<div style="width:' + width + '%;height:100%;background:' + barColor + ';border-radius:3px;opacity:0.85;"></div>'
      + '</div>'
      + '<div style="width:50px;text-align:right;padding-left:6px;color:#555;font-size:0.95em;">'
      + val + '</div></div>';
  }});
  document.getElementById(containerId).innerHTML = html;
}}

function showChart() {{
  document.getElementById("chart-panel").style.display = "block";
  var titleEl = document.querySelector("#chart-title h3");
  var subtitleEl = document.querySelector("#chart-title p");
  titleEl.textContent = "Children Under 5 by Nearest School (Drive Time)";
  subtitleEl.textContent = "Dasymetric allocation of ACS estimates to 100m grid, routed to nearest school";
  document.getElementById("chart-footer").textContent =
    "Higher values = more future kindergarteners in that school's drive-time zone.";
  renderBars("chart-bars", CHILDREN_DATA, "children_0_4");
}}

// === Enrollment chart ===
function renderEnrollmentChart(containerId, data) {{
  var maxScale = 110;
  var html = "";
  data.forEach(function(d) {{
    var util = d.util_2030;
    var width = (util / maxScale * 100).toFixed(1);
    var label = d.school.replace(" Elementary", "").replace(" Bilingue", "");
    var barColor;
    if (util > 100) barColor = "#C62828";
    else if (util >= 90) barColor = "#F9A825";
    else if (util >= 75) barColor = "#757575";
    else barColor = "#1565C0";
    var fontColor = SCHOOL_COLORS[d.school] ? (SCHOOL_COLORS[d.school]) : "#555";
    var fontWeight = SCHOOL_COLORS[d.school] ? "bold" : "normal";
    html += '<div style="display:flex;align-items:center;margin:4px 0;font-size:0.82em;padding:2px 4px;border-radius:4px;">'
      + '<div style="width:140px;text-align:right;padding-right:8px;color:' + fontColor + ';font-weight:' + fontWeight + ';white-space:nowrap;overflow:hidden;text-overflow:ellipsis;">'
      + label + '</div>'
      + '<div style="flex:1;background:#eee;border-radius:3px;height:18px;position:relative;">'
      + '<div style="width:' + width + '%;height:100%;background:' + barColor + ';border-radius:3px;opacity:0.85;"></div>'
      + '<div style="position:absolute;left:' + (100/maxScale*100).toFixed(1) + '%;top:0;height:100%;border-left:2px dashed #C62828;"></div>'
      + '</div>'
      + '<div style="width:50px;text-align:right;padding-left:6px;color:#555;font-size:0.95em;">'
      + util + '%</div></div>';
  }});
  document.getElementById(containerId).innerHTML = html;
}}

function showEnrollmentChart() {{
  document.getElementById("chart-panel").style.display = "block";
  var titleEl = document.querySelector("#chart-title h3");
  var subtitleEl = document.querySelector("#chart-title p");
  titleEl.textContent = "Projected 2030 Utilization by School";
  subtitleEl.textContent = "UNC Carolina Demography PMR2 Forecast \u2014 pre-Woolpert capacity";
  document.getElementById("chart-footer").innerHTML =
    '<span style="display:inline-block;width:12px;height:12px;background:#1565C0;border-radius:2px;vertical-align:middle;"></span> &lt;75%&ensp;'
    + '<span style="display:inline-block;width:12px;height:12px;background:#757575;border-radius:2px;vertical-align:middle;"></span> 75\u201390%&ensp;'
    + '<span style="display:inline-block;width:12px;height:12px;background:#F9A825;border-radius:2px;vertical-align:middle;"></span> 90\u2013100%&ensp;'
    + '<span style="display:inline-block;width:12px;height:12px;background:#C62828;border-radius:2px;vertical-align:middle;"></span> &gt;100%&ensp;'
    + '<span style="color:#C62828;">---|</span> 100% capacity';
  renderEnrollmentChart("chart-bars", ENROLLMENT_DATA);
}}

// === Map setup ===
var map = L.map("map", {{
  center: [{CHAPEL_HILL_CENTER[0]}, {CHAPEL_HILL_CENTER[1]}],
  zoom: 12,
  scrollWheelZoom: false,
  zoomControl: true,
  preferCanvas: true,
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

// === Closed-school marker — faded circle with dashed outline ===
function makeClosedMarker(lat, lon, color) {{
  return L.circleMarker([lat, lon], {{
    radius: 9,
    fillColor: color || '#888',
    color: color || '#888',
    weight: 2,
    dashArray: '4 4',
    fillOpacity: 0.25,
    opacity: 0.5,
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

// Schools (colored circles)
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
    var name = f.properties.school;
    var tip = "<strong>" + name + "</strong>";
    var enr = ENROLLMENT_DATA.find(function(d) {{ return d.school === name; }});
    if (enr) {{
        tip += "<br>Projected Enrollment (2030): " + enr.enroll_2030
            + "<br>Capacity: " + enr.capacity
            + "<br>% Occupied (2030): " + enr.util_2030 + "%";
    }}
    layer.bindTooltip(tip, {{permanent: false}});
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
    var name = f.properties.school;
    var tip = "<strong>" + name + "</strong>";
    var enr = ENROLLMENT_DATA.find(function(d) {{ return d.school === name; }});
    if (enr) {{
        tip += "<br>Projected Enrollment (2030): " + enr.enroll_2030
            + "<br>Capacity: " + enr.capacity
            + "<br>% Occupied (2030): " + enr.util_2030 + "%";
    }}
    layer.bindTooltip(tip, {{permanent: false}});
  }}
}});

// Capacity label layer — DivIcon markers showing enrollment/capacity boxes
// Per-school offsets [anchorX, anchorY] to prevent overlapping labels
var capLabelOffsets = {{
  // Offsets: [anchorX, anchorY]. +X=label LEFT, -X=label RIGHT, +Y=label UP, -Y=label DOWN
  // anchorY ~22 = centered vertically on dot (half label height)
  "Morris Grove Elementary":          [-12, 22],   // right, centered
  "Seawell Elementary":               [85, 27],    // left, nudged up
  "Estes Hills Elementary":           [85, 22],    // left, centered
  "McDougle Elementary":              [85, 22],    // left, centered
  "Carrboro Elementary":              [85, 22],    // left, centered
  "Northside Elementary":             [-12, 27],   // right, nudged up
  "Ephesus Elementary":               [-12, 22],   // right, centered
  "Glenwood Elementary":              [85, 17],    // left, nudged down
  "Rashkis Elementary":               [-12, 22],   // right, centered
  "Frank Porter Graham Bilingue":     [105, 22],   // left, centered
  "Scroggs Elementary":               [-12, 22],   // right, centered
}};
layers.capacityLabels = L.layerGroup();
SCHOOLS.features.forEach(function(f) {{
  var name = f.properties.school || "";
  var enr = ENROLLMENT_DATA.find(function(d) {{ return d.school === name; }});
  if (!enr) return;
  var c = f.geometry.coordinates;
  var label = name.replace(" Elementary", "").replace(" Bilingue", "");
  var utilColor = SCHOOL_COLORS[name]
    ? SCHOOL_COLORS[name]
    : (enr.util_2030 > 100 ? "#C62828" : enr.util_2030 >= 90 ? "#F9A825" : "#555");
  var anchor = capLabelOffsets[name] || [-10, 25];
  var html = '<div style="font-size:11.5px;line-height:1.4;white-space:nowrap;font-weight:bold;text-align:center;'
    + 'color:#333;text-shadow:1px 1px 2px #fff, -1px -1px 2px #fff, 1px -1px 2px #fff, -1px 1px 2px #fff, 0 0 4px #fff;">'
    + '<div style="font-size:12px;margin-bottom:1px;">' + label + '</div>'
    + '<div>' + enr.enroll_2030 + ' out of ' + enr.capacity + '</div>'
    + '<div style="font-weight:900;font-size:12.5px;">' + enr.util_2030 + '%</div>'
    + '</div>';
  var icon = L.divIcon({{
    className: '',
    html: html,
    iconSize: [0, 0],
    iconAnchor: anchor
  }});
  L.marker([c[1], c[0]], {{ icon: icon }}).addTo(layers.capacityLabels);
}});

// Road layer is global (roadLayer), managed via showTrafficDiff/hideTraffic

// Block group choropleth
var bgMax = 0;
BLOCK_GROUPS.features.forEach(function(f) {{
  var v = f.properties.children_5_9 || 0;
  if (v > bgMax) bgMax = v;
}});

function bgStyle(f) {{
  var v = f.properties.children_5_9 || 0;
  var t = bgMax > 0 ? v / bgMax : 0;
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

// Walk zones — nearest walk-time polygons
layers.walkZones = L.geoJSON(WALK_ZONES, {{
  style: function(f) {{
    return {{
      fillColor: f.properties.color || "#888",
      fillOpacity: 0.25,
      color: f.properties.color || "#888",
      weight: 2,
      opacity: 0.7
    }};
  }},
  onEachFeature: function(f, layer) {{
    layer.bindTooltip(f.properties.school, {{sticky: true}});
  }}
}});

// Closed-school X markers
var seawellSchool = findSchool("Seawell");
var ephesusSchool = findSchool("Ephesus");
var glenwoodSchool = findSchool("Glenwood");

if (seawellSchool) {{
  var sc = seawellSchool.geometry.coordinates;
  layers.closedXSeawell = makeClosedMarker(sc[1], sc[0], '#1565C0');
}}
if (ephesusSchool) {{
  var ec = ephesusSchool.geometry.coordinates;
  layers.closedXEphesus = makeClosedMarker(ec[1], ec[0], '#C62828');
}}
if (glenwoodSchool) {{
  var gc = glenwoodSchool.geometry.coordinates;
  layers.closedXGlenwood = makeClosedMarker(gc[1], gc[0], '#2E7D32');
}}

// === Zoom helpers ===
function zoomToSchool(name) {{
  var feat = findSchool(name);
  if (!feat) return;
  var c = feat.geometry.coordinates;
  map.setView([c[1], c[0]], 14);
}}

function zoomToEast() {{
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
  // Road layer is persistent — just hide its styling
  hideTraffic();
  dimOverlay.style.display = "none";
  document.getElementById("chart-panel").style.display = "none";
  document.getElementById("walker-panel").style.display = "none";
  document.getElementById("tally-panel").style.display = "none";
}}

function handleStep(idx) {{
  if (idx === currentStep) return;
  currentStep = idx;
  clearAllLayers();

  switch(idx) {{
    case 0: // Introduction: district + all schools + dim overlay
      layers.district.addTo(map);
      layers.schools.addTo(map);
      dimOverlay.style.display = "block";
      districtView();
      break;

    case 1: // Capacity Overview — schools + capacity labels
      layers.district.addTo(map);
      layers.schools.addTo(map);
      layers.capacityLabels.addTo(map);
      districtView();
      break;

    case 2: // Walker Conversion — barplot panel
      document.getElementById("walker-panel").style.display = "block";
      break;

    case 3: // Transportation Cost — same panel as walker
      document.getElementById("walker-panel").style.display = "block";
      break;

    case 4: // Random Sampling — tally image panel
      document.getElementById("tally-panel").style.display = "flex";
      break;

    case 5: // Methodology — district + schools + dim
      layers.district.addTo(map);
      layers.schools.addTo(map);
      dimOverlay.style.display = "block";
      districtView();
      break;

    case 6: // Seawell Traffic — Children 5-9
      showTrafficDiff("no_seawell", "5_9");
      layers.schools.addTo(map);
      if (layers.closedXSeawell) layers.closedXSeawell.addTo(map);
      districtView();
      break;

    case 7: // Seawell Traffic — Children 0-4
      showTrafficDiff("no_seawell", "0_4");
      layers.schools.addTo(map);
      if (layers.closedXSeawell) layers.closedXSeawell.addTo(map);
      districtView();
      break;

    case 8: // Ephesus Traffic — Children 5-9
      showTrafficDiff("no_ephesus", "5_9");
      layers.schools.addTo(map);
      if (layers.closedXEphesus) layers.closedXEphesus.addTo(map);
      districtView();
      break;

    case 9: // Ephesus Traffic — Children 0-4
      showTrafficDiff("no_ephesus", "0_4");
      layers.schools.addTo(map);
      if (layers.closedXEphesus) layers.closedXEphesus.addTo(map);
      districtView();
      break;

    case 11: // Summary
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
// Add roadLayer to map once (always present, styled dynamically)
roadLayer.addTo(map);
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

    # [3/10] Extract road network + traffic arrays from working map
    print("[3/10] Extracting from working school_closure_analysis.html ...")
    working = extract_from_working_map()
    road_geojson_str = working["road_geojson_str"]
    traffic_b64 = working["traffic_b64"]
    n_edges = working["n_edges"]
    _progress(f"ROAD_GEOJSON: {len(road_geojson_str) / 1024:.0f} KB, "
              f"N_EDGES: {n_edges}")
    _progress(f"Traffic arrays extracted: {list(traffic_b64.keys())}")

    # [4/10] Compute diff metrics from the extracted arrays
    print("[4/10] Computing traffic metrics ...")

    def _decode_b64(key):
        raw = base64.b64decode(traffic_b64[key])
        return np.frombuffer(raw, dtype=np.float32)

    baseline_04 = _decode_b64("baseline|nearest|0_4")
    baseline_59 = _decode_b64("baseline|nearest|5_9")
    seawell_04 = _decode_b64("no_seawell|nearest|0_4")
    seawell_59 = _decode_b64("no_seawell|nearest|5_9")
    ephesus_04 = _decode_b64("no_ephesus|nearest|0_4")
    ephesus_59 = _decode_b64("no_ephesus|nearest|5_9")

    # Diffs per age group
    diff_seawell_59 = seawell_59 - baseline_59
    diff_seawell_04 = seawell_04 - baseline_04
    diff_ephesus_59 = ephesus_59 - baseline_59
    diff_ephesus_04 = ephesus_04 - baseline_04

    # Curated roads per scenario (consistent across age groups)
    SEAWELL_ROADS = [
        "Seawell School Road",
        "North Estes Drive",
        "Martin Luther King Junior Boulevard",
    ]
    EPHESUS_ROADS = [
        "Ephesus Church Road",
        "East Franklin Street",
        "North Fordham Boulevard",
    ]

    graph_geojson = json.loads(road_geojson_str)
    seawell_top_roads_59 = find_road_deltas(diff_seawell_59, graph_geojson, SEAWELL_ROADS)
    seawell_top_roads_04 = find_road_deltas(diff_seawell_04, graph_geojson, SEAWELL_ROADS)
    ephesus_max_roads = {"North Fordham Boulevard"}
    ephesus_top_roads_59 = find_road_deltas(diff_ephesus_59, graph_geojson, EPHESUS_ROADS, max_roads=ephesus_max_roads)
    ephesus_top_roads_04 = find_road_deltas(diff_ephesus_04, graph_geojson, EPHESUS_ROADS, max_roads=ephesus_max_roads)
    _progress(f"Seawell 5-9: {seawell_top_roads_59}")
    _progress(f"Seawell 0-4: {seawell_top_roads_04}")
    _progress(f"Ephesus 5-9: {ephesus_top_roads_59}")
    _progress(f"Ephesus 0-4: {ephesus_top_roads_04}")

    # Edge counts (0-4, for narrative comparison)
    seawell_edges = count_significant_edges(diff_seawell_04, threshold=3.0)
    ephesus_edges = count_significant_edges(diff_ephesus_04, threshold=3.0)

    # [5/10] Children by nearest school
    print("[5/10] Computing children by nearest school ...")
    assignments = load_assignments()
    pixels = load_pixel_children()
    children_by_school = compute_children_by_school(pixels, assignments)
    children_chart_data = json.dumps(children_by_school, separators=(",", ":"))
    for rec in children_by_school:
        _progress(f"  {rec['school']}: {rec['children_0_4']} (0-4), {rec['children_5_9']} (5-9)")

    # [6/10] Enrollment projections
    print("[6/10] Preparing enrollment projections ...")
    enrollment_sorted = sorted(
        ENROLLMENT_PROJECTIONS, key=lambda d: d["util_2030"], reverse=True
    )
    enrollment_json = json.dumps(enrollment_sorted, separators=(",", ":"))
    # Compute summary stats for capacity overview slide
    below_cap = [d for d in ENROLLMENT_PROJECTIONS if d["util_2030"] <= 100]
    total_spare = sum(d["capacity"] - d["enroll_2030"] for d in below_cap)
    _progress(f"Enrollment projections: {len(enrollment_sorted)} schools, "
              f"{len(below_cap)} below capacity, {total_spare} total spare seats")

    # [7/10] Nearest walk-time zones
    print("[7/10] Building nearest walk-time zones ...")
    walk_zones_gdf = build_nearest_walk_zones(district)
    walk_zones_json = gdf_to_geojson_str(
        walk_zones_gdf, properties=["school", "color"], simplify_m=30
    )

    # [8/10] Block groups for choropleth
    print("[8/10] Loading block groups ...")
    bg = load_block_groups()
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

    # [9/10] Build HTML
    print("[9/10] Building HTML ...")
    data = {
        "schools_json": schools_json,
        "district_json": district_json,
        "road_geojson": road_geojson_str,
        "traffic_b64": traffic_b64,
        "n_edges": n_edges,
        "blockgroups_json": blockgroups_json,
        "children_chart_data": children_chart_data,
        "seawell_edges": seawell_edges,
        "ephesus_edges": ephesus_edges,
        "seawell_top_roads_59": seawell_top_roads_59,
        "seawell_top_roads_04": seawell_top_roads_04,
        "ephesus_top_roads_59": ephesus_top_roads_59,
        "ephesus_top_roads_04": ephesus_top_roads_04,
        "enrollment_json": enrollment_json,
        "total_spare": total_spare,
        "below_cap_count": len(below_cap),
        "walk_zones_json": walk_zones_json,
        "walk_bike_tally_b64": _encode_image(WALK_BIKE_TALLY_PNG),
    }
    html = build_html(data)

    OUTPUT_HTML.parent.mkdir(parents=True, exist_ok=True)
    OUTPUT_HTML.write_text(html, encoding="utf-8")
    size_mb = OUTPUT_HTML.stat().st_size / (1024 * 1024)
    print(f"\nSaved -> {OUTPUT_HTML}  ({size_mb:.1f} MB)")
    print("Done!")


if __name__ == "__main__":
    main()
