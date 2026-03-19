"""Generate CHCCS demographics editorial scrollytelling page.

This module creates an interactive scrollytelling HTML page comparing the
demographic profiles of Ephesus Elementary and Seawell Elementary attendance
zones, using Census data, drive-time analysis, dot-density mapping,
age distribution choropleth, and planned development analysis.

Key insight: Seawell's attendance zone looks economically vulnerable, but
drive-time aggregation reveals much of that vulnerable population is actually
closer to other schools. Meanwhile, Ephesus serves a larger, more diverse
population with more young children and affordable housing.

Siloed in example_stories/ to keep editorial content separate from neutral
methodology pages in src/.

Architecture mirrors src/socioeconomic_story.py: two-column layout (45%
narrative / 55% Leaflet map) with Scrollama-driven step transitions.

Usage:
    python example_stories/chccs_demographics_story.py
    python example_stories/chccs_demographics_story.py --cache-only

Output:
    example_stories/chccs_demographics.html
"""

from __future__ import annotations

import argparse
import json
import sys
import warnings
from pathlib import Path

import geopandas as gpd
import numpy as np
import pandas as pd
from shapely.geometry import mapping

# ---------------------------------------------------------------------------
# Path setup — import from src/
# ---------------------------------------------------------------------------
PROJECT_ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(PROJECT_ROOT / "src"))

from school_socioeconomic_analysis import (
    _build_nearest_zones,
    OUTPUT_DOT_ZONE_CSV,
)

warnings.filterwarnings("ignore", category=FutureWarning)

# ---------------------------------------------------------------------------
# Path constants
# ---------------------------------------------------------------------------
DATA_RAW = PROJECT_ROOT / "data" / "raw"
DATA_PROCESSED = PROJECT_ROOT / "data" / "processed"
DATA_CACHE = PROJECT_ROOT / "data" / "cache"
OUTPUT_DIR = Path(__file__).resolve().parent
OUTPUT_HTML = OUTPUT_DIR / "chccs_demographics.html"

SCHOOL_CSV = DATA_CACHE / "nces_school_locations.csv"
DISTRICT_CACHE = DATA_CACHE / "chccs_district_boundary.gpkg"
CHCCS_SHP = DATA_RAW / "properties" / "CHCCS" / "CHCCS.shp"
PARCEL_POLYS = DATA_RAW / "properties" / "combined_data_polys.gpkg"
ACS_CACHE = DATA_CACHE / "census_acs_blockgroups.gpkg"
DECENNIAL_CACHE = DATA_CACHE / "census_decennial_blocks.gpkg"
AH_CACHE = DATA_CACHE / "affordable_housing.gpkg"
MLS_CACHE = DATA_CACHE / "mls_home_sales.gpkg"
DEV_CACHE = DATA_CACHE / "planned_developments.gpkg"
SAPFOTAC_CSV = DATA_RAW / "properties" / "planned" / "SAPFOTAC_2025_future_residential.csv"
ZONE_DEMOGRAPHICS_CSV = DATA_PROCESSED / "census_school_demographics.csv"
GRID_CSV = DATA_PROCESSED / "school_desert_grid.csv"

# ---------------------------------------------------------------------------
# Domain constants
# ---------------------------------------------------------------------------
CRS_WGS84 = "EPSG:4326"
CRS_UTM17N = "EPSG:32617"
CHAPEL_HILL_CENTER = [35.9132, -79.0558]

EPHESUS_NAME = "Ephesus Elementary"
SEAWELL_NAME = "Seawell Elementary"

# Colors — Ephesus = red, Seawell = blue, combined/actual = purple
EPHESUS_COLOR = "#C62828"   # red
SEAWELL_COLOR = "#1565C0"   # blue
GLENWOOD_COLOR = "#2E7D32"  # green
ACTUAL_COLOR = "#6A1B9A"    # purple (for combined/actual values)
OTHER_COLOR = "#cccccc"     # muted gray

# ENAME → standard school name mapping
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

# Dot-density race categories and colors (censusdots.com scheme)
RACE_CATEGORIES = {
    "white_alone": ("#3b5fc0", "White"),
    "black_alone": ("#41ae76", "Black"),
    "hispanic_total": ("#f2c94c", "Hispanic/Latino"),
    "asian_alone": ("#e74c3c", "Asian"),
    "two_plus": ("#9b59b6", "Multiracial"),
    "other_race": ("#a0522d", "Native American/Other"),
}


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
            "Run: python src/school_desert.py  (to download boundary)"
        )
    return gpd.read_file(DISTRICT_CACHE)


def load_attendance_zones() -> gpd.GeoDataFrame:
    if not CHCCS_SHP.exists():
        raise FileNotFoundError(f"CHCCS shapefile not found: {CHCCS_SHP}")
    raw = gpd.read_file(CHCCS_SHP).to_crs(CRS_WGS84)
    zones = raw.dissolve(by="ENAME").reset_index()
    zones["school"] = zones["ENAME"].map(_ENAME_TO_SCHOOL)
    zones = zones[zones["school"].notna()].copy()
    zones = zones[["school", "ENAME", "geometry"]].copy()
    return zones


def load_block_groups() -> gpd.GeoDataFrame:
    if not ACS_CACHE.exists():
        raise FileNotFoundError(
            f"ACS block group cache not found: {ACS_CACHE}\n"
            "Run: python src/school_socioeconomic_analysis.py"
        )
    bg = gpd.read_file(ACS_CACHE)
    numeric_cols = [
        "total_pop", "race_total", "white_nh", "black_nh", "asian_nh",
        "hispanic", "aian_nh", "nhpi_nh", "other_nh", "two_plus_nh",
        "poverty_universe", "tenure_total", "tenure_owner", "tenure_renter",
        "median_hh_income", "vehicles_total_hh",
        "vehicles_zero_owner", "vehicles_zero_renter",
        "male_under_5", "female_under_5", "male_5_9", "female_5_9",
        "income_total", "hh_below_50k",
        "families_with_kids", "single_parent_with_kids",
    ]
    for col in numeric_cols:
        if col in bg.columns:
            bg[col] = pd.to_numeric(bg[col], errors="coerce").fillna(0)
    bg["median_hh_income"] = bg["median_hh_income"].where(
        bg["median_hh_income"] > 0, np.nan
    )
    pov_cols = ["poverty_lt_050", "poverty_050_099", "poverty_100_124",
                "poverty_125_149", "poverty_150_184"]
    for c in pov_cols:
        if c in bg.columns:
            bg[c] = pd.to_numeric(bg[c], errors="coerce").fillna(0)
    bg["below_185_pov"] = bg[pov_cols].sum(axis=1)
    bg["pct_below_185_poverty"] = np.where(
        bg["poverty_universe"] > 0,
        bg["below_185_pov"] / bg["poverty_universe"] * 100, 0
    )
    bg["pct_minority"] = np.where(
        bg["race_total"] > 0,
        (1 - bg["white_nh"] / bg["race_total"]) * 100, 0
    )
    bg["pct_renter"] = np.where(
        bg["tenure_total"] > 0,
        bg["tenure_renter"] / bg["tenure_total"] * 100, 0
    )
    if "vehicles_zero_owner" in bg.columns and "vehicles_zero_renter" in bg.columns:
        bg["vehicles_zero"] = bg["vehicles_zero_owner"] + bg["vehicles_zero_renter"]
    elif "vehicles_zero" not in bg.columns:
        bg["vehicles_zero"] = 0
    low_income_cols = [f"income_{s}" for s in [
        "lt_10k", "10k_15k", "15k_20k", "20k_25k", "25k_30k",
        "30k_35k", "35k_40k", "40k_45k", "45k_50k",
    ]]
    avail_li = [c for c in low_income_cols if c in bg.columns]
    bg["hh_below_50k"] = bg[avail_li].sum(axis=1) if avail_li else 0
    for col in ["male_hholder_with_kids", "female_hholder_with_kids",
                "married_with_kids"]:
        if col in bg.columns:
            bg[col] = pd.to_numeric(bg[col], errors="coerce").fillna(0)
        else:
            bg[col] = 0
    bg["single_parent_with_kids"] = (
        bg["male_hholder_with_kids"] + bg["female_hholder_with_kids"]
    )
    bg["families_with_kids"] = (
        bg["married_with_kids"] + bg["male_hholder_with_kids"]
        + bg["female_hholder_with_kids"]
    )
    # Age percentages for choropleth
    bg["young_children"] = bg["male_under_5"] + bg["female_under_5"]
    bg["elementary_age"] = bg["male_5_9"] + bg["female_5_9"]
    bg["pct_young_children"] = np.where(
        bg["total_pop"] > 0,
        bg["young_children"] / bg["total_pop"] * 100, 0
    )
    bg["pct_elementary_age"] = np.where(
        bg["total_pop"] > 0,
        bg["elementary_age"] / bg["total_pop"] * 100, 0
    )
    return bg


def load_blocks() -> gpd.GeoDataFrame:
    if not DECENNIAL_CACHE.exists():
        raise FileNotFoundError(
            f"Decennial block cache not found: {DECENNIAL_CACHE}\n"
            "Run: python src/school_socioeconomic_analysis.py"
        )
    blocks = gpd.read_file(DECENNIAL_CACHE)
    blocks = blocks.to_crs(CRS_WGS84)
    return blocks.copy()


def load_residential_parcels() -> gpd.GeoDataFrame:
    if not PARCEL_POLYS.exists():
        _progress("Parcel data not found, skipping")
        return gpd.GeoDataFrame()
    parcels = gpd.read_file(PARCEL_POLYS)
    parcels = parcels.to_crs(CRS_WGS84)
    mask = parcels.get("is_residential", pd.Series(False, index=parcels.index))
    if "imp_vac" in parcels.columns:
        mask = mask & parcels["imp_vac"].str.contains(
            "Improved", case=False, na=False
        )
    parcels = parcels[mask].copy()
    return parcels


def load_affordable_housing() -> gpd.GeoDataFrame:
    if not AH_CACHE.exists():
        _progress("Affordable housing cache not found, skipping")
        return gpd.GeoDataFrame()
    return gpd.read_file(AH_CACHE)


def load_mls_data() -> gpd.GeoDataFrame:
    if not MLS_CACHE.exists():
        _progress("MLS home sales cache not found, skipping")
        return gpd.GeoDataFrame()
    return gpd.read_file(MLS_CACHE)


def load_planned_dev() -> gpd.GeoDataFrame:
    if not DEV_CACHE.exists():
        _progress("Planned developments cache not found, skipping")
        return gpd.GeoDataFrame()
    return gpd.read_file(DEV_CACHE)


def load_zone_demographics() -> pd.DataFrame:
    """Load attendance-zone demographics, preferring dot-level aggregation.

    Core Census metrics come from the dot-zone CSV (``School Zones`` rows) so
    values match the interactive map exactly.  Supplemental columns (AH, MLS,
    dev, income brackets) are merged from ``census_school_demographics.csv``.
    """
    dot_csv = Path(OUTPUT_DOT_ZONE_CSV)
    if dot_csv.exists():
        _all = pd.read_csv(dot_csv)
        dot_demo = _all[_all["zone_type"] == "School Zones"].copy()
        dot_demo = dot_demo.drop(columns=["zone_type"], errors="ignore")
        # Merge supplemental columns from the BG-fragment CSV
        if ZONE_DEMOGRAPHICS_CSV.exists():
            bg_demo = pd.read_csv(ZONE_DEMOGRAPHICS_CSV)
            extra_cols = [c for c in bg_demo.columns
                          if c not in dot_demo.columns and c != "school"]
            if extra_cols:
                dot_demo = dot_demo.merge(
                    bg_demo[["school"] + extra_cols], on="school", how="left",
                )
        _progress(f"Loaded zone demographics from {dot_csv.name} (dot-level)")
        return dot_demo
    # Fallback: BG-fragment CSV
    if not ZONE_DEMOGRAPHICS_CSV.exists():
        _progress("Zone demographics CSV not found, skipping")
        return pd.DataFrame()
    return pd.read_csv(ZONE_DEMOGRAPHICS_CSV)


# ---------------------------------------------------------------------------
# Dot density generation (district-wide)
# ---------------------------------------------------------------------------
def generate_dots(
    blocks: gpd.GeoDataFrame,
    parcels: gpd.GeoDataFrame,
) -> str:
    """Generate dot-density data. Returns JSON: [[lat, lon, raceIdx], ...]."""
    if blocks.empty:
        return "[]"

    blocks_utm = blocks.to_crs(CRS_UTM17N)
    use_parcels = len(parcels) > 0
    if use_parcels:
        parcels_utm = parcels.to_crs(CRS_UTM17N)
        parcel_sindex = parcels_utm.sindex

    race_keys = list(RACE_CATEGORIES.keys())
    rng = np.random.default_rng(42)

    if "other_race" not in blocks_utm.columns:
        other_cols = []
        for c in ["aian_alone", "nhpi_alone", "other_alone"]:
            if c in blocks_utm.columns:
                other_cols.append(c)
        if other_cols:
            blocks_utm["other_race"] = blocks_utm[other_cols].sum(axis=1).clip(lower=0)
        else:
            blocks_utm["other_race"] = 0

    raw_dots = []

    for _, block in blocks_utm.iterrows():
        block_geom = block.geometry
        if block_geom is None or block_geom.is_empty:
            continue
        placement_geom = block_geom
        if use_parcels:
            candidates = list(parcel_sindex.intersection(block_geom.bounds))
            if candidates:
                try:
                    parcel_union = parcels_utm.iloc[candidates].union_all()
                except AttributeError:
                    parcel_union = parcels_utm.iloc[candidates].unary_union
                intersection = block_geom.intersection(parcel_union)
                if not intersection.is_empty and intersection.area > 10:
                    placement_geom = intersection
        if placement_geom.area < 10:
            continue

        for race_idx, race_col in enumerate(race_keys):
            count = int(block.get(race_col, 0))
            if count <= 0:
                continue
            try:
                from shapely import random_points as _shp_random_points
                pts = _shp_random_points(placement_geom, count, rng=rng)
                if hasattr(pts, "geoms"):
                    pt_list = list(pts.geoms)
                else:
                    pt_list = [pts] if not hasattr(pts, "__len__") else list(pts)
            except (ImportError, TypeError):
                pt_list = _random_points_fallback(placement_geom, count, rng)
            for pt in pt_list:
                raw_dots.append((pt.x, pt.y, race_idx))

    if not raw_dots:
        return "[]"

    from pyproj import Transformer
    transformer = Transformer.from_crs(CRS_UTM17N, CRS_WGS84, always_xy=True)
    xs = [d[0] for d in raw_dots]
    ys = [d[1] for d in raw_dots]
    race_idxs = [d[2] for d in raw_dots]
    lons, lats = transformer.transform(xs, ys)
    dots_wgs = []
    for i in range(len(raw_dots)):
        dots_wgs.append([round(lats[i], 5), round(lons[i], 5), race_idxs[i]])
    _progress(f"Generated {len(dots_wgs):,} dots")
    return json.dumps(dots_wgs, separators=(",", ":"))


def _random_points_fallback(geom, n: int, rng) -> list:
    from shapely.geometry import Point as ShapelyPoint
    points = []
    bounds = geom.bounds
    max_attempts = n * 20
    attempts = 0
    while len(points) < n and attempts < max_attempts:
        x = rng.uniform(bounds[0], bounds[2])
        y = rng.uniform(bounds[1], bounds[3])
        pt = ShapelyPoint(x, y)
        if geom.contains(pt):
            points.append(pt)
        attempts += 1
    return points


# ---------------------------------------------------------------------------
# HTML builder
# ---------------------------------------------------------------------------
def build_html(data: dict) -> str:
    """Build the editorial scrollytelling HTML (18 slides, data-step 0-17)."""

    race_colors_js = json.dumps(
        [v[0] for v in RACE_CATEGORIES.values()], separators=(",", ":")
    )
    race_labels_js = json.dumps(
        [v[1] for v in RACE_CATEGORIES.values()], separators=(",", ":")
    )

    zone_stats = data.get("zone_stats", "[]")
    drive_stats = data.get("drive_stats", "[]")
    ephesus_info = data.get("ephesus", {})
    seawell_info = data.get("seawell", {})
    has_drive_data = data.get("has_drive_data", False)

    html = f"""<!DOCTYPE html>
<html lang="en">
<head>
<meta charset="utf-8">
<meta name="viewport" content="width=device-width, initial-scale=1">
<title>CHCCS Demographics: Understanding Our Communities &mdash; CHCCS District Analysis</title>
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

.dot-legend {{
  display: flex;
  flex-wrap: wrap;
  gap: 8px;
  margin: 10px 0;
}}

.dot-legend-item {{
  display: flex;
  align-items: center;
  gap: 4px;
  font-size: 0.85em;
}}

.dot-legend-swatch {{
  width: 12px; height: 12px;
  border-radius: 50%;
  display: inline-block;
}}

.legend-bar {{
  height: 14px;
  flex: 1;
  border-radius: 3px;
}}

.ephesus-label {{ color: {EPHESUS_COLOR}; font-weight: bold; }}
.seawell-label {{ color: {SEAWELL_COLOR}; font-weight: bold; }}

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

  <!-- Step 0: Welcome -->
  <div class="step" data-step="0">
    <div class="step-number">1</div>
    <h2>CHCCS Demographics: Understanding Our Communities</h2>
    <p>As CHCCS evaluates potential school closures, the district has established
    criteria that include <strong>&ldquo;Inconvenience or Hardship&rdquo;</strong>
    and <strong>&ldquo;Anticipated Enrollment.&rdquo;</strong> Understanding the
    demographic profile of each school&rsquo;s surrounding community is essential
    to applying these criteria fairly.</p>
    <p>This page presents a data-driven demographic analysis using Census data,
    drive-time modeling, and dot-density mapping &mdash; examining socioeconomic
    status, racial/ethnic composition, age distribution, and real estate trends
    across school communities.</p>
    <p>All data and methodology are open. This analysis uses the same Census-based
    framework applied equally to all 11 CHCCS elementary schools.</p>
    <div class="source">
      <strong>Data:</strong> NCES EDGE 2023-24 &bull; Census ACS 5-Year (2020&ndash;2024)
      &bull; 2020 Decennial Census &bull; OSM road network &bull; Orange County parcels
    </div>
  </div>

  <!-- Step 1: Attendance Zones Explained -->
  <div class="step" data-step="1">
    <div class="step-number">2</div>
    <h2>Attendance Zones</h2>
    <p>Each CHCCS elementary school has an <strong>attendance zone</strong> &mdash;
    a geographic boundary drawn by CHCCS. Students living inside
    a zone are assigned to that school.</p>
    <p>These boundaries were designed for <em>enrollment management</em>, not
    to follow natural travel patterns or community boundaries. They are
    periodically redrawn &mdash; and <strong>will be redrawn after any
    closure</strong>.</p>
    <p>The map shows all 11 attendance zones with colored fills and school
    locations.</p>
    <div class="limitation">
      <strong>Note:</strong> Glenwood Elementary is a partial magnet school.
      FPG Bilingue is a district-wide magnet school, so its zone covers
      the entire district rather than a specific neighborhood.
    </div>
  </div>

  <!-- Step 2: Drive-Time Zones Explained -->
  <div class="step" data-step="2">
    <div class="step-number">3</div>
    <h2>Drive-Time Zones</h2>
    <p>A more policy-relevant way to assign territory: <strong>nearest school
    by driving time</strong>, computed via Dijkstra shortest-path on the
    OpenStreetMap road network. These maps are drawn entirely through a computing
    process &mdash; no manual boundary-drawing is involved.</p>
    <p><em>Drive-time zones answer: &ldquo;If every family drove to the closest
    school, which school would serve this location?&rdquo; Since any closure
    triggers rezoning, drive-time zones better predict where displaced students
    would go.</em></p>
    <p>The map now shows drive-time zones instead of attendance zones.
    Notice that Seawell&rsquo;s drive-time zone is compact due to the local
    road network. Some concave pocket neighborhoods on the zone&rsquo;s edges
    have simpler, quicker access to Morris Grove and McDougle Elementary on
    the west, and Estes Hills Elementary on the east.</p>
    <div class="limitation">
      <strong>Note:</strong> A large drive-time zone means that a school is more
      accessible to more people &mdash; more households have that school as their
      closest option by driving time.
    </div>
  </div>

  <!-- Step 3: Neighborhood Schools Compared -->
  <div class="step" data-step="3">
    <div class="step-number">4</div>
    <h2>Neighborhood Schools Compared</h2>
    <p>As a district-wide magnet school, Glenwood&rsquo;s programs serve students
    from across the district rather than a specific neighborhood. Because this
    analysis focuses on neighborhood-level demographics, Glenwood is not directly
    comparable and is set aside from the focal comparison going forward.</p>
    <p>Making direct comparisons between two schools &mdash; each with its own
    history, strengths, and deeply committed community &mdash; is inherently
    difficult. We approach this comparison with respect for both
    <span class="ephesus-label">Ephesus Elementary</span> and
    <span class="seawell-label">Seawell Elementary</span>, and for the families
    they serve.</p>
    <p>The map highlights their <strong>drive-time zones</strong> as solid borders
    (<span class="ephesus-label">red</span> for Ephesus,
    <span class="seawell-label">blue</span> for Seawell) with their
    <strong>attendance zones</strong> shown as dashed overlays for comparison.</p>
    <p>Notice how the zones differ: Seawell&rsquo;s attendance zone extends into
    areas that are actually closer to other schools by driving time &mdash; a large
    zone, but sparsely populated in its outer reaches.</p>
    <p>This analysis considers <strong>both</strong> attendance and drive-time
    zones to limit bias introduced by CHCCS-drawn attendance boundaries.</p>
    <div class="limitation">
      <strong>Note:</strong> Glenwood&rsquo;s location was maintained in map
      calculations as another available CHCCS site.
    </div>
  </div>

  <!-- Step 4: Understanding the Data -->
  <div class="step" data-step="4">
    <div class="step-number">5</div>
    <h2>Understanding the Data</h2>
    <p>This analysis uses <strong>ACS Census data</strong> &mdash; the American
    Community Survey&rsquo;s 5-year estimates &mdash; aggregated by drive-time zone
    to evaluate CHCCS&rsquo;s closure criteria:</p>
    <ul style="margin-left:1.2em;padding-left:0;">
      <li>Inconvenience or Hardship</li>
      <li>Anticipated Enrollment</li>
    </ul>
    <p>Census data reflects the demographics of the <em>community surrounding</em>
    each school, which may differ from current enrollment demographics.
    This distinction matters: Seawell&rsquo;s current student body includes the
    district-wide LEAP (Launching Equity through Achievement and
    Potential) program whose students may present a very different demographic makeup from
    the surrounding community. Census data captures who <em>lives</em> near the
    school, regardless of enrollment programs.</p>
    <div class="limitation">
      <strong>Note:</strong> ACS 5-year estimates (2020&ndash;2024) carry margins
      of error, especially at the block-group level. All values should be treated
      as approximations, not exact counts.
    </div>
  </div>

  <!-- ========== SECTION 1: SOCIOECONOMIC STATUS (Steps 5-8) ========== -->

  <!-- Step 5: Poverty bar charts (all 11 schools) -->
  <div class="step" data-step="5">
    <div class="step-number">6</div>
    <h2>Poverty: All 11 Schools</h2>
    <p>How many people in economic hardship live nearest to each school?</p>
    <p>The bar charts show <strong>people below 185% poverty</strong> (the
    Free/Reduced-price Lunch threshold &mdash; 185% of the federal poverty level
    is the standard definition of &ldquo;low income&rdquo; used in federal programs)
    for all 11 schools. The left panel
    uses <strong>nearest-drive zones</strong>; the right uses official
    <strong>attendance zones</strong>.</p>
    <p>We lead with <em>counts</em> rather than percentages because we care about
    real human impact. A school zone with a high poverty <em>rate</em> but few
    residents may affect fewer families than a zone with a moderate rate but many
    more people.</p>
    <div class="limitation">
      <strong>Why counts matter:</strong> Seawell&rsquo;s attendance zone is
      geographically large but sparsely populated in its outer reaches.
      Percentages can overstate impact when the denominator is small.
    </div>
  </div>

  <!-- Step 6: Seawell SES close-up -->
  <div class="step" data-step="6">
    <div class="step-number">7</div>
    <h2>Seawell: Socioeconomic Profile</h2>
    <p>Seawell&rsquo;s zone shows meaningful economic vulnerability.</p>
    <p>Zooming into the <span class="seawell-label">Seawell</span> drive-time
    zone (solid blue border) with its attendance zone shown as a dashed overlay.</p>
    <div id="seawell-ses-metrics">
    </div>
  </div>

  <!-- Step 7: Ephesus SES close-up -->
  <div class="step" data-step="7">
    <div class="step-number">8</div>
    <h2>Ephesus: Socioeconomic Profile</h2>
    <p>In contrast, Ephesus has a much larger drive zone which indicates
    that it is not only more accessible to a larger population but also that
    there are fewer adjacent schools.</p>
    <p>Now the <span class="ephesus-label">Ephesus</span> drive-time zone
    (solid red border) with its attendance zone as dashed overlay.</p>
    <div id="ephesus-ses-metrics">
    </div>
  </div>

  <!-- Step 8: SES Summary -->
  <div class="step" data-step="8">
    <div class="step-number">9</div>
    <h2>Socioeconomic Summary</h2>
    <p>Both schools are optimally situated to potentially serve communities with
    economic need. In absolute terms, more economically vulnerable residents
    live within the Ephesus zone:</p>
    <div id="ses-summary-text">
    </div>
    <p>Under CHCCS&rsquo;s &ldquo;Inconvenience or Hardship&rdquo; criterion,
    Ephesus&rsquo;s geographic positioning places it closer to more people
    overall, more people in poverty, and more affordable-housing residents,
    suggesting greater potential hardship from closure.</p>
  </div>

  <!-- ========== SECTION 2: RACE/ETHNICITY (Steps 9-10) ========== -->

  <!-- Step 9: District-wide dots -->
  <div class="step" data-step="9">
    <div class="step-number">10</div>
    <h2>Racial Dot Density: District Overview</h2>
    <p>Each dot represents <strong>one person</strong> from the 2020 Census,
    placed randomly within their Census block (constrained to residential
    parcels). Six race/ethnicity categories:</p>
    <div class="dot-legend">
      <div class="dot-legend-item">
        <span class="dot-legend-swatch" style="background:#3b5fc0;"></span> White
      </div>
      <div class="dot-legend-item">
        <span class="dot-legend-swatch" style="background:#41ae76;"></span> Black
      </div>
      <div class="dot-legend-item">
        <span class="dot-legend-swatch" style="background:#f2c94c;"></span> Hispanic/Latino
      </div>
      <div class="dot-legend-item">
        <span class="dot-legend-swatch" style="background:#e74c3c;"></span> Asian
      </div>
      <div class="dot-legend-item">
        <span class="dot-legend-swatch" style="background:#9b59b6;"></span> Multiracial
      </div>
      <div class="dot-legend-item">
        <span class="dot-legend-swatch" style="background:#a0522d;"></span> Native Am./Other
      </div>
    </div>
    <p>The full district contains approximately 95,000 dots. Patterns of
    residential segregation are visible at this scale.</p>
    <div class="limitation">
      <strong>Note:</strong> The dot-density map uses 2020 Decennial Census data
      (block-level race counts), while the socioeconomic metrics on other slides
      use ACS 5-Year 2020&ndash;2024 estimates. Block-level race data is only
      available from the Decennial Census.
    </div>
  </div>

  <!-- Step 10: Minority count bar charts (all 11 schools) -->
  <div class="step" data-step="10">
    <div class="step-number">11</div>
    <h2>Minority Residents: All 11 Schools</h2>
    <p>How many minority residents live nearest to each school?</p>
    <p>The bar charts show <strong>minority residents</strong> (non-White)
    for all 11 schools &mdash; drive-time zones on the left, attendance zones
    on the right. Counts, not percentages.</p>
    <p>Notably, Ephesus has a higher minority population overall, and nearly
    <strong>twice the Hispanic population</strong> of Seawell.</p>
  </div>

  <!-- ========== SECTION 3: AGE DISTRIBUTION (Steps 11-13) ========== -->

  <!-- Step 11: Young children choropleth -->
  <div class="step" data-step="11">
    <div class="step-number">12</div>
    <h2>Where Are the Youngest Children?</h2>
    <p>The Board&rsquo;s &ldquo;Anticipated Enrollment&rdquo; criterion
    depends on where young children live today. The map shows block groups
    colored by <strong>% children ages 0&ndash;4</strong> (under-5 population
    as share of total population).</p>
    <p>Darker colors indicate higher concentrations of very young children
    who will enter elementary school in coming years. Both school zone outlines
    are shown for reference.</p>
    <p>Since this is Census data from 2024, these children would be entering
    or in elementary school in 2029. Therefore, this information matters more
    than data on children ages 5&ndash;9.</p>
    <div class="source">
      <strong>Data:</strong> ACS 5-Year 2020&ndash;2024, tables B01001
      (age by sex)
    </div>
  </div>

  <!-- Step 12: Young children count bars -->
  <div class="step" data-step="12">
    <div class="step-number">13</div>
    <h2>Young Children: All 11 Schools</h2>
    <p>The bar charts show <strong>young children (ages 0&ndash;4)</strong>
    by school zone &mdash; drive-time zones on the left, attendance zones on
    the right. Counts, not percentages.</p>
    <div class="metric-box" id="age-comparison-metrics">
    </div>
  </div>

  <!-- ========== REAL ESTATE (Steps 13-14) ========== -->

  <!-- Step 13: Home Sales -->
  <div class="step" data-step="13">
    <div class="step-number">14</div>
    <h2>Where Are Homes Selling?</h2>
    <p>School enrollment depends on where families move. <strong>MLS home
    sales data</strong> (2023&ndash;2025) reveals which zones attract the
    most new residents.</p>
    <div id="sales-comparison-metrics">
    </div>
    <div class="source">
      <strong>Data:</strong> Triangle MLS closed sales 2023&ndash;2025.
      Covers listed sales only (not rentals or FSBO).
    </div>
  </div>

  <!-- Step 14: Median Prices -->
  <div class="step" data-step="14">
    <div class="step-number">15</div>
    <h2>Can Young Families Afford to Move In?</h2>
    <p>Declining CHCCS enrollment is partly driven by housing costs. When
    median home prices exceed what young families can afford, zones lose
    the demographic pipeline that sustains enrollment.</p>
    <div id="price-comparison-metrics">
    </div>
    <div class="limitation">
      <strong>Limitation:</strong> MLS covers listed sales only &mdash;
      not rentals or for-sale-by-owner transactions.
    </div>
  </div>

  <!-- Step 15: Planned Developments (CH Active Dev) -->
  <div class="step" data-step="15">
    <div class="step-number">16</div>
    <h2>Where Is Growth Headed?</h2>
    <p>The Town of Chapel Hill&rsquo;s
    <a href="https://www.chapelhillnc.gov/Business-and-Development/Active-Development"
    target="_blank" style="color:#1565C0;">Active Development</a> page lists
    <strong>29 planned residential developments</strong> within the CHCCS
    district, representing thousands of new housing units. Each circle is
    colored by expected unit count:</p>
    <div class="dot-legend">
      <div class="dot-legend-item">
        <span class="dot-legend-swatch" style="background:#d73027;"></span> 400+ units
      </div>
      <div class="dot-legend-item">
        <span class="dot-legend-swatch" style="background:#fc8d59;"></span> 150&ndash;400 units
      </div>
      <div class="dot-legend-item">
        <span class="dot-legend-swatch" style="background:#fee090;"></span> 50&ndash;150 units
      </div>
      <div class="dot-legend-item">
        <span class="dot-legend-swatch" style="background:#91bfdb;"></span> &lt;50 units
      </div>
    </div>
    <div id="dev-comparison-metrics">
    </div>
    <div class="limitation">
      <strong>Note:</strong> This dataset covers Chapel Hill only and shows
      expected housing units &mdash; it does not estimate how many
      <em>students</em> those units will generate.
    </div>
    <div class="source">
      <strong>Data:</strong> Town of Chapel Hill
      <a href="https://www.chapelhillnc.gov/Business-and-Development/Active-Development" target="_blank">Active Development</a> page,
      hand-transcribed March 12, 2026
    </div>
  </div>

  <!-- Step 16: Planned Developments (SAPFOTAC) -->
  <div class="step" data-step="16">
    <div class="step-number">17</div>
    <h2>Student Impact of Growth</h2>
    <p>The <strong>SAPFOTAC 2025 Annual Report</strong> (certified June 3, 2025)
    provides a complementary view: 21 future residential projects with
    <strong>projected student yields</strong> &mdash; how many elementary,
    middle, and high school students each development is expected to
    generate.</p>
    <div id="sapfotac-comparison-metrics">
    </div>
    <div class="limitation">
      <strong>Why do the two slides differ?</strong> The datasets come from
      different sources collected at different times. <strong>16a</strong>
      (CH Active Dev) is hand-transcribed from the Town of Chapel Hill
      active development website
      (<a href="https://www.chapelhillnc.gov/Business-and-Development/Active-Development"
      target="_blank" style="color:#1565C0;">chapelhillnc.gov</a>,
      March 2026). <strong>16b</strong>
      (SAPFOTAC) is published by the school district&rsquo;s advisory
      committee (June 2025) and covers Chapel Hill + Carrboro. Some
      projects appear in both datasets. Student yield estimates are
      model-based (using district generation rates), not actual
      enrollment. Carrboro&rsquo;s planned development data is not as
      readily accessible as Chapel Hill&rsquo;s.
    </div>
    <div class="source">
      <strong>Data:</strong> SAPFOTAC 2025 Annual Report,
      certified June 3, 2025
    </div>
  </div>

  <!-- ========== CONCLUSION (Step 17) ========== -->

  <!-- Step 17: Summary -->
  <div class="step" data-step="17">
    <div class="step-number">18</div>
    <h2>Summary</h2>

    <div id="final-summary-text">
    </div>
    <p style="margin-top:24px;font-size:0.9em;color:#666;text-align:center;">
      Return to the <a href="../index.html">homepage</a> for interactive maps
      and full methodology guides.
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
    <div style="display:flex;gap:16px;">
      <div style="flex:1;">
        <h4 id="chart-left-title" style="text-align:center;margin:0 0 8px;font-size:0.95em;color:#555;"></h4>
        <div id="chart-left"></div>
      </div>
      <div id="chart-right-col" style="flex:1;">
        <h4 id="chart-right-title" style="text-align:center;margin:0 0 8px;font-size:0.95em;color:#555;"></h4>
        <div id="chart-right"></div>
      </div>
    </div>
    <p id="chart-footer" style="text-align:center;margin:16px 0 0;font-size:0.8em;color:#999;line-height:1.4;"></p>
  </div>
</div>

<script src="https://unpkg.com/leaflet@1.9.3/dist/leaflet.js"></script>
<script src="https://unpkg.com/scrollama@3.2.0/build/scrollama.min.js"></script>
<script>
// === Embedded data ===
var SCHOOLS = {data["schools_json"]};
var DISTRICT = {data["district_json"]};
var ZONES = {data["zones_json"]};
var BG = {data["bg_json"]};
var DOT_DATA = {data["dot_data"]};
var AH = {data["ah_json"]};
var MLS = {data["mls_json"]};
var DEV = {data["dev_json"]};
var SAPFOTAC = {data["sapfotac_json"]};
var ZONE_STATS = {zone_stats};
var DRIVE_STATS = {drive_stats};
var DRIVE_ZONES = {data.get("drive_zones_json", '{{"type":"FeatureCollection","features":[]}}')};

var RACE_COLORS = {race_colors_js};
var RACE_LABELS = {race_labels_js};

var EPHESUS_COLOR = "{EPHESUS_COLOR}";
var SEAWELL_COLOR = "{SEAWELL_COLOR}";
var GLENWOOD_COLOR = "{GLENWOOD_COLOR}";
var ACTUAL_COLOR = "{ACTUAL_COLOR}";
var OTHER_COLOR = "{OTHER_COLOR}";

var EPHESUS = {json.dumps(ephesus_info)};
var SEAWELL = {json.dumps(seawell_info)};

// AMI colors for affordable housing
var AMI_COLORS = {{
  "0-30%": "#d73027", "30-60%": "#fc8d59",
  "60-80%": "#fee090", "80%+": "#91bfdb"
}};

// === Helper: find stats for a school ===
function findSchool(statsArray, name) {{
  for (var i = 0; i < statsArray.length; i++) {{
    if (statsArray[i].school && statsArray[i].school.indexOf(name) >= 0) return statsArray[i];
  }}
  return null;
}}

// === Bar chart builder with count/pct modes ===
function renderBars(containerId, data, metric, options) {{
  options = options || {{}};
  var mode = options.mode || "count";
  var html = "";
  data.forEach(function(d) {{
    var val = d[metric] || 0;
    var maxVal = d._maxVal || 1;
    var width = (val / maxVal * 100).toFixed(1);
    var label = d.school.replace(" Elementary", "").replace(" Bilingue", "");
    var barColor = OTHER_COLOR;
    if (d.school.indexOf("Ephesus") >= 0) barColor = EPHESUS_COLOR;
    else if (d.school.indexOf("Seawell") >= 0) barColor = SEAWELL_COLOR;
    var isHighlight = (d.school.indexOf("Ephesus") >= 0 || d.school.indexOf("Seawell") >= 0);
    var fontWeight = isHighlight ? "bold" : "normal";
    var fontColor = (d.school.indexOf("Ephesus") >= 0) ? EPHESUS_COLOR
                  : (d.school.indexOf("Seawell") >= 0) ? SEAWELL_COLOR : "#555";
    var valText;
    if (mode === "pct") {{
      valText = val.toFixed(1) + "%";
    }} else if (mode === "dollar") {{
      valText = "$" + Math.round(val).toLocaleString();
    }} else {{
      valText = Math.round(val).toLocaleString();
    }}
    html += '<div style="display:flex;align-items:center;margin:3px 0;font-size:0.78em;">'
      + '<div style="width:100px;text-align:right;padding-right:6px;color:' + fontColor + ';font-weight:' + fontWeight + ';white-space:nowrap;overflow:hidden;text-overflow:ellipsis;">'
      + label + '</div>'
      + '<div style="flex:1;background:#eee;border-radius:3px;height:16px;position:relative;">'
      + '<div style="width:' + width + '%;height:100%;background:' + barColor + ';border-radius:3px;"></div>'
      + '</div>'
      + '<div style="width:60px;text-align:right;padding-left:4px;color:#555;font-size:0.9em;">'
      + valText + '</div></div>';
  }});
  document.getElementById(containerId).innerHTML = html;
}}

// === Dual-panel chart: drive-time left, attendance right ===
function dualPanelChart(title, subtitle, footer, metric, options) {{
  options = options || {{}};
  var mode = options.mode || "count";
  var transform = options.transform || null;
  document.getElementById("chart-panel").style.display = "block";
  var titleEl = document.querySelector("#chart-title h3");
  var subtitleEl = document.querySelector("#chart-title p");
  titleEl.textContent = title;
  subtitleEl.textContent = subtitle;
  document.getElementById("chart-left-title").innerHTML = 'Nearest-Drive Zones <span style="font-weight:normal;font-size:0.85em;">(Dijkstra)</span>';
  document.getElementById("chart-right-title").innerHTML = 'Attendance Zones <span style="font-weight:normal;font-size:0.85em;">(current)</span>';
  document.getElementById("chart-right-col").style.display = "block";
  document.getElementById("chart-footer").textContent = footer;

  // Prepare drive data (left panel — primary)
  var driveData = [];
  if (DRIVE_STATS && DRIVE_STATS.length) {{
    driveData = DRIVE_STATS.map(function(s) {{
      var rec = {{ school: s.school }};
      if (transform) {{ rec[metric] = transform(s); }}
      else {{ rec[metric] = s[metric] || 0; }}
      return rec;
    }});
  }}
  driveData.sort(function(a, b) {{ return b[metric] - a[metric]; }});
  var order = driveData.map(function(d) {{ return d.school; }});

  // Prepare zone data (right panel)
  var zoneData = ZONE_STATS.map(function(s) {{
    var rec = {{ school: s.school }};
    if (transform) {{ rec[metric] = transform(s); }}
    else {{ rec[metric] = s[metric] || 0; }}
    return rec;
  }});

  // Align zone data to drive order
  var zoneMap = {{}};
  zoneData.forEach(function(d) {{ zoneMap[d.school] = d[metric]; }});
  var zoneAligned = order.map(function(name) {{
    return {{ school: name }};
  }});
  zoneAligned.forEach(function(d) {{ d[metric] = zoneMap[d.school] || 0; }});

  // Compute shared max for consistent bar widths
  var allVals = driveData.map(function(d) {{ return d[metric]; }})
    .concat(zoneData.map(function(d) {{ return d[metric]; }}));
  var maxVal = Math.max.apply(null, allVals) || 1;
  driveData.forEach(function(d) {{ d._maxVal = maxVal; }});
  zoneAligned.forEach(function(d) {{ d._maxVal = maxVal; }});

  if (driveData.length) {{
    renderBars("chart-left", driveData, metric, {{ mode: mode }});
  }} else {{
    document.getElementById("chart-left").innerHTML = '<p style="color:#999;font-size:0.85em;text-align:center;">Drive data not available.<br>Run school_desert.py to generate.</p>';
  }}
  renderBars("chart-right", zoneAligned, metric, {{ mode: mode }});
}}

function showPovertyCharts() {{
  dualPanelChart(
    "People Below 185% Poverty by School",
    "Count of residents below the Free/Reduced Lunch threshold",
    "Same Census data, two zone definitions. Counts show real human impact.",
    "below_185_pov",
    {{ mode: "count" }}
  );
}}

function showMinorityCharts() {{
  dualPanelChart(
    "Minority Residents by School Zone",
    "Count of non-White residents nearest each school",
    "Closing the school with more minority residents has greater equity impact.",
    "minority_count",
    {{ mode: "count", transform: function(s) {{ return (s.race_total || 0) - (s.white_nh || 0); }} }}
  );
}}

function showAgeCharts() {{
  dualPanelChart(
    "Young Children (0\u20134) by School Zone",
    "Count of children under 5 — future kindergarten demand",
    "More young children means higher future enrollment demand in that area.",
    "young_children",
    {{ mode: "count", transform: function(s) {{ return (s.male_under_5 || 0) + (s.female_under_5 || 0); }} }}
  );
}}

function showDevCharts() {{
  dualPanelChart(
    "Planned Developments by School Zone (CH Active Dev)",
    "Total expected housing units from planned developments",
    "Source: Town of Chapel Hill Active Development page (March 2026). Chapel Hill projects only.",
    "dev_total_units",
    {{ mode: "count" }}
  );
}}

function showSapfotacCharts() {{
  dualPanelChart(
    "Projected Elementary Students by School Zone (SAPFOTAC)",
    "Estimated new elementary students from planned developments",
    "Source: SAPFOTAC 2025 Annual Report. Student yields based on district generation rates, not actual enrollment.",
    "sapfotac_elem_students",
    {{ mode: "count" }}
  );
}}

// === Dynamic metric boxes ===
function fmt(val) {{ return Math.round(val).toLocaleString(); }}
function pctNote(val) {{ return '<div class="metric-label" style="font-size:0.75em;color:#999;">(' + val.toFixed(1) + '% of zone)</div>'; }}

function buildTwoRowMetricTable(metric, label, mode) {{
  // Ephesus and Seawell only — two rows: drive zones (top), attendance zones (bottom)
  var driveSrc = (DRIVE_STATS && DRIVE_STATS.length) ? DRIVE_STATS : ZONE_STATS;
  var ephD = findSchool(driveSrc, "Ephesus") || {{}};
  var seaD = findSchool(driveSrc, "Seawell") || {{}};
  var ephZ = findSchool(ZONE_STATS, "Ephesus") || {{}};
  var seaZ = findSchool(ZONE_STATS, "Seawell") || {{}};

  function fmtVal(v) {{
    if (mode === "dollar") return "$" + fmt(v);
    return fmt(v);
  }}

  var ephDVal = ephD[metric] || 0;
  var seaDVal = seaD[metric] || 0;
  var ephZVal = ephZ[metric] || 0;
  var seaZVal = seaZ[metric] || 0;

  // Table layout: header row + two data sections with clear labels
  var html = '<table style="width:100%;border-collapse:collapse;margin:8px 0;font-size:0.9em;">'
    + '<thead><tr>'
    + '<th style="text-align:left;padding:4px 8px;color:#555;font-size:0.85em;width:40%;"></th>'
    + '<th style="text-align:center;padding:4px 8px;color:' + EPHESUS_COLOR + ';font-weight:bold;">Ephesus</th>'
    + '<th style="text-align:center;padding:4px 8px;color:' + SEAWELL_COLOR + ';font-weight:bold;">Seawell</th>'
    + '</tr></thead><tbody>'
    + '<tr style="background:#f5f5f5;">'
    + '<td style="padding:6px 8px;font-weight:bold;color:#555;">Drive Zones</td>'
    + '<td style="text-align:center;padding:6px 8px;font-size:1.2em;font-weight:bold;color:' + EPHESUS_COLOR + ';">' + fmtVal(ephDVal) + '</td>'
    + '<td style="text-align:center;padding:6px 8px;font-size:1.2em;font-weight:bold;color:' + SEAWELL_COLOR + ';">' + fmtVal(seaDVal) + '</td>'
    + '</tr>'
    + '<tr>'
    + '<td style="padding:6px 8px;font-weight:bold;color:#555;">Attendance Zones</td>'
    + '<td style="text-align:center;padding:6px 8px;font-size:1.2em;font-weight:bold;color:' + EPHESUS_COLOR + ';">' + fmtVal(ephZVal) + '</td>'
    + '<td style="text-align:center;padding:6px 8px;font-size:1.2em;font-weight:bold;color:' + SEAWELL_COLOR + ';">' + fmtVal(seaZVal) + '</td>'
    + '</tr>'
    + '</tbody></table>';
  return html;
}}

function populateMetrics() {{
  // Use drive stats if available, fall back to zone stats
  var src = (DRIVE_STATS && DRIVE_STATS.length) ? DRIVE_STATS : ZONE_STATS;
  var zoneSrc = ZONE_STATS;
  var eph = findSchool(src, "Ephesus") || {{}};
  var sea = findSchool(src, "Seawell") || {{}};
  var ephZ = findSchool(zoneSrc, "Ephesus") || {{}};
  var seaZ = findSchool(zoneSrc, "Seawell") || {{}};

  // Seawell SES metrics (step 6) — table format
  var el = document.getElementById("seawell-ses-metrics");
  if (el) {{
    var seaZPov = seaZ.below_185_pov || 0;
    var seaZAH = seaZ.ah_total_units || 0;
    var seaDPov = sea.below_185_pov || 0;
    var seaDAH = sea.ah_total_units || 0;
    el.innerHTML = '<table style="width:100%;border-collapse:collapse;margin:8px 0;font-size:0.9em;">'
      + '<thead><tr>'
      + '<th style="text-align:left;padding:4px 8px;color:#555;font-size:0.85em;width:40%;"></th>'
      + '<th style="text-align:center;padding:4px 8px;color:#555;font-weight:bold;">Households below 185% Poverty</th>'
      + '<th style="text-align:center;padding:4px 8px;color:#555;font-weight:bold;">Affordable Housing Units</th>'
      + '</tr></thead><tbody>'
      + '<tr style="background:#f5f5f5;">'
      + '<td style="padding:6px 8px;font-weight:bold;color:#555;">Drive Zone</td>'
      + '<td style="text-align:center;padding:6px 8px;font-size:1.2em;font-weight:bold;color:{SEAWELL_COLOR};">' + fmt(seaDPov) + '</td>'
      + '<td style="text-align:center;padding:6px 8px;font-size:1.2em;font-weight:bold;color:{SEAWELL_COLOR};">' + fmt(seaDAH) + '</td>'
      + '</tr>'
      + '<tr>'
      + '<td style="padding:6px 8px;font-weight:bold;color:#555;">Attendance Zone</td>'
      + '<td style="text-align:center;padding:6px 8px;font-size:1.2em;font-weight:bold;color:{SEAWELL_COLOR};">' + fmt(seaZPov) + '</td>'
      + '<td style="text-align:center;padding:6px 8px;font-size:1.2em;font-weight:bold;color:{SEAWELL_COLOR};">' + fmt(seaZAH) + '</td>'
      + '</tr>'
      + '</tbody></table>';
  }}

  // Ephesus SES metrics (step 7) — table format
  el = document.getElementById("ephesus-ses-metrics");
  if (el) {{
    var ephZPov = ephZ.below_185_pov || 0;
    var ephZAH = ephZ.ah_total_units || 0;
    var ephDPov = eph.below_185_pov || 0;
    var ephDAH = eph.ah_total_units || 0;
    el.innerHTML = '<table style="width:100%;border-collapse:collapse;margin:8px 0;font-size:0.9em;">'
      + '<thead><tr>'
      + '<th style="text-align:left;padding:4px 8px;color:#555;font-size:0.85em;width:40%;"></th>'
      + '<th style="text-align:center;padding:4px 8px;color:#555;font-weight:bold;">Households below 185% Poverty</th>'
      + '<th style="text-align:center;padding:4px 8px;color:#555;font-weight:bold;">Affordable Housing Units</th>'
      + '</tr></thead><tbody>'
      + '<tr style="background:#f5f5f5;">'
      + '<td style="padding:6px 8px;font-weight:bold;color:#555;">Drive Zone</td>'
      + '<td style="text-align:center;padding:6px 8px;font-size:1.2em;font-weight:bold;color:{EPHESUS_COLOR};">' + fmt(ephDPov) + '</td>'
      + '<td style="text-align:center;padding:6px 8px;font-size:1.2em;font-weight:bold;color:{EPHESUS_COLOR};">' + fmt(ephDAH) + '</td>'
      + '</tr>'
      + '<tr>'
      + '<td style="padding:6px 8px;font-weight:bold;color:#555;">Attendance Zone</td>'
      + '<td style="text-align:center;padding:6px 8px;font-size:1.2em;font-weight:bold;color:{EPHESUS_COLOR};">' + fmt(ephZPov) + '</td>'
      + '<td style="text-align:center;padding:6px 8px;font-size:1.2em;font-weight:bold;color:{EPHESUS_COLOR};">' + fmt(ephZAH) + '</td>'
      + '</tr>'
      + '</tbody></table>';
  }}

  // SES summary (step 8)
  el = document.getElementById("ses-summary-text");
  if (el) {{
    var ephPov2 = eph.below_185_pov || ephZ.below_185_pov || 0;
    var seaPov2 = sea.below_185_pov || seaZ.below_185_pov || 0;
    var ephPop2 = eph.total_pop || ephZ.total_pop || 0;
    var seaPop2 = sea.total_pop || seaZ.total_pop || 0;
    var ephInc = eph.median_hh_income || ephZ.median_hh_income || 0;
    var seaInc = sea.median_hh_income || seaZ.median_hh_income || 0;
    var ephDAH2 = eph.ah_total_units || 0;
    var ephZAH2 = ephZ.ah_total_units || 0;
    var seaDAH2 = sea.ah_total_units || 0;
    var seaZAH2 = seaZ.ah_total_units || 0;
    var ephZInc = ephZ.median_hh_income || 0;
    var seaZInc = seaZ.median_hh_income || 0;
    el.innerHTML = '<ul style="margin:8px 0 8px 20px;line-height:1.8;">'
      + '<li><span class="ephesus-label">Ephesus</span> is more accessible to a larger total population</li>'
      + '<li><span class="ephesus-label">Ephesus</span> serves more people requiring government assistance programs</li>'
      + '<li><span class="ephesus-label">Ephesus</span> has more affordable housing units</li>'
      + '</ul>'
      + '<table style="width:100%;border-collapse:collapse;margin:12px 0;font-size:0.9em;">'
      + '<thead><tr>'
      + '<th style="text-align:left;padding:4px 8px;color:#555;font-size:0.85em;width:40%;">Median HH Income</th>'
      + '<th style="text-align:center;padding:4px 8px;color:{EPHESUS_COLOR};font-weight:bold;">Ephesus</th>'
      + '<th style="text-align:center;padding:4px 8px;color:{SEAWELL_COLOR};font-weight:bold;">Seawell</th>'
      + '</tr></thead><tbody>'
      + '<tr style="background:#f5f5f5;">'
      + '<td style="padding:6px 8px;font-weight:bold;color:#555;">Drive Zone</td>'
      + '<td style="text-align:center;padding:6px 8px;font-size:1.1em;font-weight:bold;color:{EPHESUS_COLOR};">$' + fmt(ephInc) + '</td>'
      + '<td style="text-align:center;padding:6px 8px;font-size:1.1em;font-weight:bold;color:{SEAWELL_COLOR};">$' + fmt(seaInc) + '</td>'
      + '</tr>'
      + '<tr>'
      + '<td style="padding:6px 8px;font-weight:bold;color:#555;">Attendance Zone</td>'
      + '<td style="text-align:center;padding:6px 8px;font-size:1.1em;font-weight:bold;color:{EPHESUS_COLOR};">$' + fmt(ephZInc) + '</td>'
      + '<td style="text-align:center;padding:6px 8px;font-size:1.1em;font-weight:bold;color:{SEAWELL_COLOR};">$' + fmt(seaZInc) + '</td>'
      + '</tr>'
      + '</tbody></table>'
      + '<p style="margin-top:8px;font-size:0.85em;color:#666;">The difference between zone types reflects how boundary definitions affect which block groups are captured. Drive zones measure geographic accessibility; attendance zones reflect CHCCS-drawn boundaries.</p>'
      + '<p style="margin-top:8px;">Seawell&rsquo;s attendance zone may continue to be intentionally delineated by CHCCS to capture a different demographic, as is already evident in these maps. This analysis chose to objectively review the data to answer: <em>which school is most accessible to which population?</em></p>';
  }}

  // Age comparison metrics (step 12)
  el = document.getElementById("age-comparison-metrics");
  if (el) {{
    var ephYoung = (eph.male_under_5 || ephZ.male_under_5 || 0) + (eph.female_under_5 || ephZ.female_under_5 || 0);
    var seaYoung = (sea.male_under_5 || seaZ.male_under_5 || 0) + (sea.female_under_5 || seaZ.female_under_5 || 0);
    el.innerHTML = '<div class="metric" style="border:2px solid {EPHESUS_COLOR};">'
      + '<div class="metric-value" style="color:{EPHESUS_COLOR};">~' + fmt(ephYoung) + '</div>'
      + '<div class="metric-label">Ephesus: Children Under 5</div></div>'
      + '<div class="metric" style="border:2px solid {SEAWELL_COLOR};">'
      + '<div class="metric-value" style="color:{SEAWELL_COLOR};">~' + fmt(seaYoung) + '</div>'
      + '<div class="metric-label">Seawell: Children Under 5</div></div>';
  }}

  // Sales comparison metrics (step 13) — two-row table
  el = document.getElementById("sales-comparison-metrics");
  if (el) {{
    el.innerHTML = buildTwoRowMetricTable("mls_total_sales", "Homes Sold", "count");
  }}

  // Price comparison metrics (step 14) — two-row table
  el = document.getElementById("price-comparison-metrics");
  if (el) {{
    el.innerHTML = buildTwoRowMetricTable("mls_median_price", "Median Price", "dollar");
  }}

  // Planned dev metrics (step 15)
  el = document.getElementById("dev-comparison-metrics");
  if (el) {{
    el.innerHTML = buildTwoRowMetricTable("dev_total_units", "Expected Units", "count");
  }}

  el = document.getElementById("sapfotac-comparison-metrics");
  if (el) {{
    el.innerHTML = buildTwoRowMetricTable("sapfotac_elem_students", "Projected Elem. Students", "count");
  }}

  // Final summary (step 17)
  el = document.getElementById("final-summary-text");
  if (el) {{
    el.innerHTML = '<p>For the future of CHCCS, we need to be sure school locations are '
      + 'easily accessible to ALL populations. This analysis demonstrates:</p>'
      + '<ul style="line-height:1.8;margin-left:1.5em;padding-left:0.5em;">'
      + '<li>Ephesus is <strong>accessible to more people</strong> '
      + '(due to access to major roadways and geographic location far enough from other nearby schools)</li>'
      + '<li>Ephesus serves a population '
      + 'skewed to <strong>lower income communities</strong></li>'
      + '<li>Ephesus is accessible to a <strong>greater '
      + 'diversity</strong> of people</li>'
      + '<li>Ephesus is accessible to <strong>more young children</strong> '
      + 'which may drive future enrollment increases</li>'
      + '<li>Homes around Ephesus are more <strong>affordable</strong> '
      + 'which means younger families are more likely to be able to afford living near Ephesus</li>'
      + '<li><strong>Planned developments</strong> near Ephesus consistently show more '
      + 'housing units across all types, with projected student yields signaling future '
      + 'enrollment growth in the Ephesus area</li>'
      + '</ul>';
  }}
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

// === Layer factories ===
var layers = {{}};

// Schools
layers.schools = L.geoJSON(SCHOOLS, {{
  pointToLayer: function(f, ll) {{
    var isE = f.properties.school && f.properties.school.indexOf("Ephesus") >= 0;
    var isS = f.properties.school && f.properties.school.indexOf("Seawell") >= 0;
    var isG = f.properties.school && f.properties.school.indexOf("Glenwood") >= 0;
    var color = isE ? EPHESUS_COLOR : (isS ? SEAWELL_COLOR : (isG ? GLENWOOD_COLOR : "#888"));
    var radius = (isE || isS || isG) ? 8 : 5;
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

// Schools (Glenwood gray — for steps 3+)
layers.schoolsGw = L.geoJSON(SCHOOLS, {{
  pointToLayer: function(f, ll) {{
    var isE = f.properties.school && f.properties.school.indexOf("Ephesus") >= 0;
    var isS = f.properties.school && f.properties.school.indexOf("Seawell") >= 0;
    var color = isE ? EPHESUS_COLOR : (isS ? SEAWELL_COLOR : "#888");
    var radius = (isE || isS) ? 8 : 5;
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

// District boundary
layers.district = L.geoJSON(DISTRICT, {{
  style: {{ color: "#333", weight: 2, dashArray: "6 4", fillOpacity: 0 }}
}});

// All attendance zones (colored fill for step 1)
layers.zonesColored = L.geoJSON(ZONES, {{
  style: function(f) {{
    var name = f.properties.school || "";
    if (name.indexOf("Ephesus") >= 0) {{
      return {{ color: EPHESUS_COLOR, weight: 2, fillColor: EPHESUS_COLOR, fillOpacity: 0.15 }};
    }} else if (name.indexOf("Seawell") >= 0) {{
      return {{ color: SEAWELL_COLOR, weight: 2, fillColor: SEAWELL_COLOR, fillOpacity: 0.15 }};
    }} else if (name.indexOf("Glenwood") >= 0) {{
      return {{ color: GLENWOOD_COLOR, weight: 2, fillColor: GLENWOOD_COLOR, fillOpacity: 0.15 }};
    }}
    return {{ color: "#aaa", weight: 1, fillColor: "#ddd", fillOpacity: 0.08 }};
  }}
}});

// All attendance zones (faint gray)
layers.zonesFaint = L.geoJSON(ZONES, {{
  style: function(f) {{
    var name = f.properties.school || "";
    if (name.indexOf("Ephesus") >= 0 || name.indexOf("Seawell") >= 0) {{
      return {{ weight: 0, fillOpacity: 0 }};
    }}
    return {{ color: "#aaa", weight: 1, fillColor: "#ddd", fillOpacity: 0.08, dashArray: "4 3" }};
  }}
}});

// Ephesus attendance zone
var ephesusZoneData = {{
  type: "FeatureCollection",
  features: ZONES.features.filter(function(f) {{
    return f.properties.school && f.properties.school.indexOf("Ephesus") >= 0;
  }})
}};
layers.ephesusZone = L.geoJSON(ephesusZoneData, {{
  style: {{ color: EPHESUS_COLOR, weight: 3, fillOpacity: 0.1, fillColor: EPHESUS_COLOR }}
}});
layers.ephesusZoneFaint = L.geoJSON(ephesusZoneData, {{
  style: {{ color: EPHESUS_COLOR, weight: 4, fillOpacity: 0, dashArray: "4 3" }},
  interactive: false
}});
// Ephesus attendance zone dashed overlay (for comparison with drive zones)
layers.ephesusAttDashed = L.geoJSON(ephesusZoneData, {{
  style: {{ color: EPHESUS_COLOR, weight: 2, fillOpacity: 0, dashArray: "8 5" }}
}});

// Seawell attendance zone
var seawellZoneData = {{
  type: "FeatureCollection",
  features: ZONES.features.filter(function(f) {{
    return f.properties.school && f.properties.school.indexOf("Seawell") >= 0;
  }})
}};
layers.seawellZone = L.geoJSON(seawellZoneData, {{
  style: {{ color: SEAWELL_COLOR, weight: 3, fillOpacity: 0.1, fillColor: SEAWELL_COLOR }}
}});
layers.seawellZoneFaint = L.geoJSON(seawellZoneData, {{
  style: {{ color: SEAWELL_COLOR, weight: 4, fillOpacity: 0, dashArray: "4 3" }},
  interactive: false
}});
// Seawell attendance zone dashed overlay
layers.seawellAttDashed = L.geoJSON(seawellZoneData, {{
  style: {{ color: SEAWELL_COLOR, weight: 2, fillOpacity: 0, dashArray: "8 5" }}
}});

// Glenwood attendance zone
var glenwoodZoneData = {{
  type: "FeatureCollection",
  features: ZONES.features.filter(function(f) {{
    return f.properties.school && f.properties.school.indexOf("Glenwood") >= 0;
  }})
}};
layers.glenwoodZoneFaint = L.geoJSON(glenwoodZoneData, {{
  style: {{ color: "#aaa", weight: 4, fillOpacity: 0, dashArray: "4 3" }},
  interactive: false
}});
layers.glenwoodAttDashed = L.geoJSON(glenwoodZoneData, {{
  style: {{ color: "#aaa", weight: 2, fillOpacity: 0, dashArray: "8 5" }}
}});

// Both attendance zones highlighted
layers.bothZones = L.layerGroup([
  L.geoJSON(ephesusZoneData, {{
    style: {{ color: EPHESUS_COLOR, weight: 3, fillOpacity: 0.12, fillColor: EPHESUS_COLOR }}
  }}),
  L.geoJSON(seawellZoneData, {{
    style: {{ color: SEAWELL_COLOR, weight: 3, fillOpacity: 0.12, fillColor: SEAWELL_COLOR }}
  }})
]);

// Drive-time zones (all, for step 2)
layers.driveZones = L.geoJSON(DRIVE_ZONES, {{
  style: function(f) {{
    var name = f.properties.school || "";
    if (name.indexOf("Ephesus") >= 0) {{
      return {{ color: EPHESUS_COLOR, weight: 2, fillColor: EPHESUS_COLOR, fillOpacity: 0.15 }};
    }} else if (name.indexOf("Seawell") >= 0) {{
      return {{ color: SEAWELL_COLOR, weight: 2, fillColor: SEAWELL_COLOR, fillOpacity: 0.15 }};
    }} else if (name.indexOf("Glenwood") >= 0) {{
      return {{ color: GLENWOOD_COLOR, weight: 2, fillColor: GLENWOOD_COLOR, fillOpacity: 0.15 }};
    }}
    return {{ color: "#ccc", weight: 1, fillColor: "#eee", fillOpacity: 0.05 }};
  }},
  onEachFeature: function(f, layer) {{
    layer.bindTooltip(f.properties.school || "");
  }}
}});

// Drive-time zones (Glenwood gray — for steps 3+)
layers.driveZonesGw = L.geoJSON(DRIVE_ZONES, {{
  style: function(f) {{
    var name = f.properties.school || "";
    if (name.indexOf("Ephesus") >= 0) {{
      return {{ color: EPHESUS_COLOR, weight: 2, fillColor: EPHESUS_COLOR, fillOpacity: 0.15 }};
    }} else if (name.indexOf("Seawell") >= 0) {{
      return {{ color: SEAWELL_COLOR, weight: 2, fillColor: SEAWELL_COLOR, fillOpacity: 0.15 }};
    }}
    return {{ color: "#ccc", weight: 1, fillColor: "#eee", fillOpacity: 0.05 }};
  }},
  onEachFeature: function(f, layer) {{
    layer.bindTooltip(f.properties.school || "");
  }}
}});

// Per-school drive-time zone layers
var ephesusDriveData = {{
  type: "FeatureCollection",
  features: DRIVE_ZONES.features.filter(function(f) {{
    return f.properties.school && f.properties.school.indexOf("Ephesus") >= 0;
  }})
}};
var seawellDriveData = {{
  type: "FeatureCollection",
  features: DRIVE_ZONES.features.filter(function(f) {{
    return f.properties.school && f.properties.school.indexOf("Seawell") >= 0;
  }})
}};
var glenwoodDriveData = {{
  type: "FeatureCollection",
  features: DRIVE_ZONES.features.filter(function(f) {{
    return f.properties.school && f.properties.school.indexOf("Glenwood") >= 0;
  }})
}};

layers.ephesusDriveZone = L.geoJSON(ephesusDriveData, {{
  style: {{ color: EPHESUS_COLOR, weight: 3, fillColor: EPHESUS_COLOR, fillOpacity: 0.1 }}
}});
layers.seawellDriveZone = L.geoJSON(seawellDriveData, {{
  style: {{ color: SEAWELL_COLOR, weight: 3, fillColor: SEAWELL_COLOR, fillOpacity: 0.1 }}
}});
layers.glenwoodDriveZone = L.geoJSON(glenwoodDriveData, {{
  style: {{ color: "#aaa", weight: 3, fillColor: "#aaa", fillOpacity: 0.1 }}
}});
layers.bothDriveZones = L.layerGroup([
  L.geoJSON(ephesusDriveData, {{
    style: {{ color: EPHESUS_COLOR, weight: 3, fillColor: EPHESUS_COLOR, fillOpacity: 0.1 }}
  }}),
  L.geoJSON(seawellDriveData, {{
    style: {{ color: SEAWELL_COLOR, weight: 3, fillColor: SEAWELL_COLOR, fillOpacity: 0.1 }}
  }}),
  L.geoJSON(glenwoodDriveData, {{
    style: {{ color: "#ccc", weight: 3, fillColor: "#ccc", fillOpacity: 0.1 }}
  }})
]);

// Affordable housing
layers.affordableHousing = L.geoJSON(AH, {{
  pointToLayer: function(f, ll) {{
    var ami = f.properties.AMIServed || "";
    var color = "#91bfdb";
    if (ami.indexOf("0-30") >= 0) color = "#d73027";
    else if (ami.indexOf("30-60") >= 0) color = "#fc8d59";
    else if (ami.indexOf("60-80") >= 0) color = "#fee090";
    return L.circleMarker(ll, {{
      radius: 5,
      fillColor: color,
      color: "#333",
      weight: 1,
      fillOpacity: 0.8,
    }});
  }},
  onEachFeature: function(f, layer) {{
    var p = f.properties;
    layer.bindTooltip(
      (p.ProjectName || "Affordable Housing") + "<br>" +
      (p.TotalUnits ? "Units: " + p.TotalUnits + "<br>" : "") +
      "AMI: " + (p.AMIServed || "Unknown")
    );
  }}
}});

// MLS home sales — dots for location density (step 13)
layers.mlsSales = L.geoJSON(MLS, {{
  pointToLayer: function(f, ll) {{
    return L.circleMarker(ll, {{
      radius: 3,
      fillColor: "#e67e22",
      color: "#b5651d",
      weight: 0.5,
      fillOpacity: 0.6,
    }});
  }},
  onEachFeature: function(f, layer) {{
    var p = f.properties;
    layer.bindTooltip("$" + Math.round(p.close_price || 0).toLocaleString());
  }}
}});

// MLS home prices — dots colored by price (step 14)
function priceColor(price) {{
  // Green (affordable) → Yellow → Red (expensive)
  // $200k = green, $600k = yellow, $1M+ = red
  var t = Math.min(Math.max((price - 200000) / 800000, 0), 1);
  if (t < 0.5) {{
    var s = t * 2;
    var r = Math.round(50 + s * 205);
    var g = Math.round(180 - s * 20);
    var b = Math.round(50);
    return "rgb(" + r + "," + g + "," + b + ")";
  }} else {{
    var s = (t - 0.5) * 2;
    var r = Math.round(255);
    var g = Math.round(160 - s * 160);
    var b = Math.round(50 - s * 50);
    return "rgb(" + r + "," + g + "," + b + ")";
  }}
}}
layers.mlsPrices = L.geoJSON(MLS, {{
  pointToLayer: function(f, ll) {{
    var price = f.properties.close_price || 0;
    return L.circleMarker(ll, {{
      radius: 4,
      fillColor: priceColor(price),
      color: "#555",
      weight: 0.5,
      fillOpacity: 0.7,
    }});
  }},
  onEachFeature: function(f, layer) {{
    var p = f.properties;
    layer.bindTooltip("$" + Math.round(p.close_price || 0).toLocaleString());
  }}
}});

// Planned developments — color by unit count (blue→yellow→red), matching methodology map
var DEV_MAX_UNITS = 1;
if (DEV && DEV.features) {{
  DEV.features.forEach(function(f) {{
    var u = f.properties.expected_units || 0;
    if (u > DEV_MAX_UNITS) DEV_MAX_UNITS = u;
  }});
}}
function devColor(units) {{
  var frac = Math.min(units / DEV_MAX_UNITS, 1.0);
  // 4-stop gradient: #91bfdb → #fee090 → #fc8d59 → #d73027
  var stops = [
    [0.0,  0x91, 0xbf, 0xdb],
    [0.33, 0xfe, 0xe0, 0x90],
    [0.66, 0xfc, 0x8d, 0x59],
    [1.0,  0xd7, 0x30, 0x27]
  ];
  var i = 0;
  for (var s = 1; s < stops.length; s++) {{
    if (frac <= stops[s][0]) {{ i = s - 1; break; }}
    i = s - 1;
  }}
  var t = (frac - stops[i][0]) / (stops[i+1][0] - stops[i][0]);
  var r = Math.round(stops[i][1] + t * (stops[i+1][1] - stops[i][1]));
  var g = Math.round(stops[i][2] + t * (stops[i+1][2] - stops[i][2]));
  var b = Math.round(stops[i][3] + t * (stops[i+1][3] - stops[i][3]));
  return "rgb(" + r + "," + g + "," + b + ")";
}}
layers.devMarkers = L.geoJSON(DEV, {{
  pointToLayer: function(f, ll) {{
    var units = f.properties.expected_units || 0;
    var color = devColor(units);
    return L.circleMarker(ll, {{
      radius: 10,
      fillColor: color,
      color: "#555",
      weight: 1.5,
      fillOpacity: 0.85,
    }});
  }},
  onEachFeature: function(f, layer) {{
    var p = f.properties;
    var units = p.expected_units || 0;
    layer.bindTooltip(
      (p.name || "Development") + "<br>" +
      units.toLocaleString() + " units"
    );
  }}
}});

// SAPFOTAC planned developments — same color scheme, keyed by total_units_remaining
var SAP_MAX_UNITS = 1;
if (SAPFOTAC && SAPFOTAC.features) {{
  SAPFOTAC.features.forEach(function(f) {{
    var u = f.properties.total_units_remaining || 0;
    if (u > SAP_MAX_UNITS) SAP_MAX_UNITS = u;
  }});
}}
function sapColor(units) {{
  var frac = Math.min(units / SAP_MAX_UNITS, 1.0);
  var stops = [
    [0.0,  0x91, 0xbf, 0xdb],
    [0.33, 0xfe, 0xe0, 0x90],
    [0.66, 0xfc, 0x8d, 0x59],
    [1.0,  0xd7, 0x30, 0x27]
  ];
  var i = 0;
  for (var s = 1; s < stops.length; s++) {{
    if (frac <= stops[s][0]) {{ i = s - 1; break; }}
    i = s - 1;
  }}
  var t = (frac - stops[i][0]) / (stops[i+1][0] - stops[i][0]);
  var r = Math.round(stops[i][1] + t * (stops[i+1][1] - stops[i][1]));
  var g = Math.round(stops[i][2] + t * (stops[i+1][2] - stops[i][2]));
  var b = Math.round(stops[i][3] + t * (stops[i+1][3] - stops[i][3]));
  return "rgb(" + r + "," + g + "," + b + ")";
}}
layers.sapfotacMarkers = L.geoJSON(SAPFOTAC, {{
  pointToLayer: function(f, ll) {{
    var units = f.properties.total_units_remaining || 0;
    var color = sapColor(units);
    return L.circleMarker(ll, {{
      radius: 10,
      fillColor: color,
      color: "#555",
      weight: 1.5,
      fillOpacity: 0.85,
    }});
  }},
  onEachFeature: function(f, layer) {{
    var p = f.properties;
    var units = p.total_units_remaining || 0;
    var elem = p.students_elementary || 0;
    var mid = p.students_middle || 0;
    var high = p.students_high || 0;
    layer.bindTooltip(
      "<b>" + (p.project || "Development") + "</b><br>" +
      units.toLocaleString() + " units<br>" +
      "Students \u2014 Elem: " + elem + ", Mid: " + mid + ", High: " + high
    );
  }}
}});

// Block groups choropleth (for age steps)
function bgChoropleth(metric, colorFn) {{
  return L.geoJSON(BG, {{
    style: function(f) {{
      var val = f.properties[metric] || 0;
      return {{
        color: "#666",
        weight: 0.5,
        fillColor: colorFn(val),
        fillOpacity: 0.55,
      }};
    }},
    onEachFeature: function(f, layer) {{
      var p = f.properties;
      layer.bindTooltip(
        "GEOID: " + (p.GEOID || "?") + "<br>" +
        metric + ": " + (p[metric] || 0).toFixed(1) + "%"
      );
    }}
  }});
}}

function ylOrRd(val) {{
  // YlOrRd-like: 0% → pale yellow, 10%+ → dark red
  var t = Math.min(val / 10, 1);
  var r = Math.round(255);
  var g = Math.round(255 - t * 180);
  var b = Math.round(200 - t * 200);
  return "rgb(" + r + "," + g + "," + b + ")";
}}

layers.bgYoungChildren = bgChoropleth("pct_young_children", ylOrRd);
layers.bgElementaryAge = bgChoropleth("pct_elementary_age", ylOrRd);

// Dots (canvas rendered — deferred)
var dotCanvas = L.canvas({{ padding: 0.5 }});
layers.dots = L.layerGroup();
var dotsLoaded = false;
function ensureDotsLoaded() {{
  if (dotsLoaded) return;
  dotsLoaded = true;
  var d = DOT_DATA;
  for (var i = 0; i < d.length; i++) {{
    L.circleMarker([d[i][0], d[i][1]], {{
      radius: 1.5,
      fillColor: RACE_COLORS[d[i][2]],
      color: RACE_COLORS[d[i][2]],
      weight: 0,
      fillOpacity: 0.7,
      renderer: dotCanvas
    }}).addTo(layers.dots);
  }}
}}

// === Helper: zoom to a zone (GeoJSON data) ===
function zoomToZone(zoneGeoJSON) {{
  var bounds = L.geoJSON(zoneGeoJSON).getBounds();
  if (bounds.isValid()) {{
    map.fitBounds(bounds.pad(0.1));
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
    case 0: // Welcome — district + all schools
      layers.district.addTo(map);
      layers.schools.addTo(map);
      districtView();
      break;

    case 1: // Attendance Zones — all zones colored + schools
      layers.district.addTo(map);
      layers.zonesColored.addTo(map);
      layers.schools.addTo(map);
      districtView();
      break;

    case 2: // Drive-Time Zones — drive zones + schools
      layers.district.addTo(map);
      layers.driveZones.addTo(map);
      layers.schools.addTo(map);
      districtView();
      break;

    case 3: // Neighborhood Schools — both drive zones bold + attendance dashed
      layers.bothDriveZones.addTo(map);
      layers.ephesusAttDashed.addTo(map);
      layers.seawellAttDashed.addTo(map);
      layers.glenwoodAttDashed.addTo(map);
      layers.schoolsGw.addTo(map);
      districtView();
      break;

    case 4: // Understanding the Data — same map as step 3
      layers.bothDriveZones.addTo(map);
      layers.ephesusAttDashed.addTo(map);
      layers.seawellAttDashed.addTo(map);
      layers.glenwoodAttDashed.addTo(map);
      layers.schoolsGw.addTo(map);
      districtView();
      break;

    case 5: // Poverty bar charts (all 11)
      showPovertyCharts();
      break;

    case 6: // Seawell SES close-up
      layers.seawellDriveZone.addTo(map);
      layers.seawellAttDashed.addTo(map);
      layers.affordableHousing.addTo(map);
      layers.schoolsGw.addTo(map);
      zoomToZone(seawellDriveData.features.length ? seawellDriveData : seawellZoneData);
      break;

    case 7: // Ephesus SES close-up
      layers.ephesusDriveZone.addTo(map);
      layers.ephesusAttDashed.addTo(map);
      layers.affordableHousing.addTo(map);
      layers.schoolsGw.addTo(map);
      zoomToZone(ephesusDriveData.features.length ? ephesusDriveData : ephesusZoneData);
      break;

    case 8: // SES summary
      layers.bothDriveZones.addTo(map);
      layers.affordableHousing.addTo(map);
      layers.schoolsGw.addTo(map);
      districtView();
      break;

    case 9: // District-wide dots
      ensureDotsLoaded();
      layers.dots.addTo(map);
      layers.district.addTo(map);
      layers.schoolsGw.addTo(map);
      districtView();
      break;

    case 10: // Minority count bar charts (all 11) + race summary
      showMinorityCharts();
      break;

    case 11: // Young children choropleth
      layers.bgYoungChildren.addTo(map);
      layers.ephesusZoneFaint.addTo(map);
      layers.seawellZoneFaint.addTo(map);
      layers.glenwoodZoneFaint.addTo(map);
      layers.schoolsGw.addTo(map);
      districtView();
      break;

    case 12: // Young children count bars
      showAgeCharts();
      break;

    case 13: // Home sales — drive zones + MLS dots
      layers.driveZonesGw.addTo(map);
      layers.mlsSales.addTo(map);
      layers.schoolsGw.addTo(map);
      districtView();
      break;

    case 14: // Median prices — drive zones + price-colored dots
      layers.driveZonesGw.addTo(map);
      layers.mlsPrices.addTo(map);
      layers.schoolsGw.addTo(map);
      districtView();
      break;

    case 15: // Planned dev (CH Active Dev) — drive zones + dev markers
      layers.driveZonesGw.addTo(map);
      layers.devMarkers.addTo(map);
      layers.schoolsGw.addTo(map);
      districtView();
      showDevCharts();
      break;

    case 16: // Planned dev (SAPFOTAC) — drive zones + SAPFOTAC markers
      layers.driveZonesGw.addTo(map);
      layers.sapfotacMarkers.addTo(map);
      layers.schoolsGw.addTo(map);
      districtView();
      showSapfotacCharts();
      break;

    case 17: // Final summary
      ensureDotsLoaded();
      layers.dots.addTo(map);
      layers.bothDriveZones.addTo(map);
      layers.district.addTo(map);
      layers.schoolsGw.addTo(map);
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
// Populate dynamic metrics after data is available, then show step 0
populateMetrics();
setTimeout(function() {{ handleStep(0); }}, 100);
</script>
</body>
</html>"""

    return html


# ---------------------------------------------------------------------------
# Main orchestrator
# ---------------------------------------------------------------------------
def main():
    parser = argparse.ArgumentParser(
        description="Generate CHCCS demographics editorial scrollytelling page"
    )
    parser.add_argument("--cache-only", action="store_true",
                        help="Only use cached data (default behavior)")
    parser.parse_args()

    print("=" * 60)
    print("CHCCS Demographics: Editorial Story Generator")
    print("=" * 60)

    # [1/12] Load schools
    print("\n[1/12] Loading school locations ...")
    schools = load_schools()
    schools_gdf = gpd.GeoDataFrame(
        schools,
        geometry=gpd.points_from_xy(schools.lon, schools.lat),
        crs=CRS_WGS84,
    )
    schools_json = gdf_to_geojson_str(schools_gdf, properties=["school"])

    # Find Ephesus and Seawell info
    eph_row = schools[schools["school"] == EPHESUS_NAME].iloc[0]
    sea_row = schools[schools["school"] == SEAWELL_NAME].iloc[0]
    ephesus_info = {"lat": float(eph_row["lat"]), "lon": float(eph_row["lon"]),
                    "school": EPHESUS_NAME}
    seawell_info = {"lat": float(sea_row["lat"]), "lon": float(sea_row["lon"]),
                    "school": SEAWELL_NAME}
    _progress(f"Loaded {len(schools)} schools")

    # [2/12] District boundary
    print("[2/12] Loading district boundary ...")
    district = load_district_boundary()
    district_json = gdf_to_geojson_str(district, simplify_m=50)

    # [3/12] Attendance zones
    print("[3/12] Loading attendance zones ...")
    zones = load_attendance_zones()
    zones_json = gdf_to_geojson_str(zones, properties=["school"], simplify_m=20)
    _progress(f"Loaded {len(zones)} zones")

    # [4/12] Block groups
    print("[4/12] Loading ACS block groups ...")
    bg = load_block_groups()
    bg_clipped = gpd.clip(bg, district.to_crs(bg.crs))
    bg_clipped = bg_clipped[
        bg_clipped.geometry.geom_type.isin(["Polygon", "MultiPolygon"])
    ].copy()
    bg_json = gdf_to_geojson_str(
        bg_clipped,
        properties=["GEOID", "total_pop", "pct_below_185_poverty",
                     "pct_minority", "pct_renter", "median_hh_income",
                     "pct_young_children", "pct_elementary_age"],
        simplify_m=20,
    )
    _progress(f"Loaded {len(bg_clipped)} block groups")

    # [5/12] Census blocks (district-wide for dots)
    print("[5/12] Loading Decennial Census blocks ...")
    all_blocks = load_blocks()
    all_blocks = gpd.sjoin(
        all_blocks, district.to_crs(all_blocks.crs),
        how="inner", predicate="intersects",
    ).drop(columns=["index_right"], errors="ignore")
    _progress(f"Loaded {len(all_blocks)} blocks (district-wide)")

    # [6/12] Residential parcels
    print("[6/12] Loading residential parcels ...")
    all_parcels = load_residential_parcels()
    _progress(f"Loaded {len(all_parcels)} residential parcels")

    # [7/12] Generate dots
    print("[7/12] Generating dot-density data (district-wide) ...")
    dot_data = generate_dots(all_blocks, all_parcels)

    # [7b/12] Load MLS data
    print("[7b/12] Loading MLS home sales ...")
    mls_data = load_mls_data()
    _progress(f"Loaded {len(mls_data)} MLS home sales")

    # [7c/12] Load planned developments
    print("[7c/12] Loading planned developments ...")
    dev_data = load_planned_dev()
    _progress(f"Loaded {len(dev_data)} planned developments")

    # [7d/12] MLS GeoJSON for map layers
    mls_json = gdf_to_geojson_str(
        mls_data, properties=["close_price"],
    ) if len(mls_data) > 0 else '{"type":"FeatureCollection","features":[]}'
    _progress(f"Serialized {len(mls_data)} MLS points for map")

    # [7e/12] Planned dev GeoJSON for map layers
    dev_json = gdf_to_geojson_str(
        dev_data, properties=["name", "expected_units"],
    ) if len(dev_data) > 0 else '{"type":"FeatureCollection","features":[]}'
    _progress(f"Serialized {len(dev_data)} planned dev points for map")

    # [7f/12] Load SAPFOTAC planned developments
    print("[7f/12] Loading SAPFOTAC planned developments ...")
    sapfotac_json = '{"type":"FeatureCollection","features":[]}'
    sapfotac_data = gpd.GeoDataFrame()
    if SAPFOTAC_CSV.exists():
        sap_df = pd.read_csv(SAPFOTAC_CSV)
        sap_df = sap_df.dropna(subset=["lat", "lon"])
        sapfotac_data = gpd.GeoDataFrame(
            sap_df,
            geometry=gpd.points_from_xy(sap_df["lon"], sap_df["lat"]),
            crs=CRS_WGS84,
        )
        sapfotac_json = gdf_to_geojson_str(
            sapfotac_data,
            properties=["project", "address", "total_units_remaining",
                         "students_elementary", "students_middle", "students_high"],
        )
        _progress(f"Loaded {len(sapfotac_data)} SAPFOTAC planned developments")
    else:
        _progress("SAPFOTAC CSV not found, skipping")

    # [8/12] Affordable housing
    print("[8/12] Loading affordable housing ...")
    ah = load_affordable_housing()
    ah_json = gdf_to_geojson_str(
        ah,
        properties=["ProjectName", "AMIServed", "TotalUnits", "UnitType"],
    ) if len(ah) > 0 else '{"type":"FeatureCollection","features":[]}'
    _progress(f"Loaded {len(ah)} affordable housing records")

    # [9/12] Zone demographics — expanded stat_cols with counts
    print("[9/12] Loading zone demographics ...")
    zone_demo = load_zone_demographics()
    stat_cols = [
        "school", "total_pop", "median_hh_income",
        # Counts
        "below_185_pov", "poverty_universe",
        "race_total", "white_nh", "black_nh", "hispanic", "asian_nh", "two_plus_nh",
        "male_under_5", "female_under_5", "male_5_9", "female_5_9",
        "tenure_total", "tenure_renter", "vehicles_zero",
        "families_with_kids", "single_parent_with_kids",
        "ah_total_units",
        "mls_total_sales", "mls_median_price",
        "dev_total_units", "dev_count",
        "sapfotac_total_units", "sapfotac_count", "sapfotac_elem_students",
        # Percentages (secondary)
        "pct_below_185_poverty", "pct_minority", "pct_black",
        "pct_hispanic", "pct_renter", "pct_zero_vehicle",
        "pct_elementary_age", "pct_young_children", "pct_single_parent",
    ]
    if len(zone_demo) > 0:
        avail_cols = [c for c in stat_cols if c in zone_demo.columns]
        zone_stats = zone_demo[avail_cols].to_dict("records")
        for rec in zone_stats:
            for k, v in rec.items():
                if isinstance(v, (np.integer, np.floating)):
                    rec[k] = float(v)
                elif pd.isna(v):
                    rec[k] = 0
        zone_stats_json = json.dumps(zone_stats, separators=(",", ":"))
    else:
        zone_stats_json = "[]"

    # [10/12] Nearest-drive zone demographics
    print("[10/12] Loading nearest-drive zone demographics ...")
    drive_stats_json = "[]"
    drive_zones_json = '{"type":"FeatureCollection","features":[]}'
    has_drive_data = False
    try:
        drive_zones = _build_nearest_zones(GRID_CSV, "drive", district)
        if drive_zones is not None and len(drive_zones) > 0:
            drive_zones_json = gdf_to_geojson_str(
                drive_zones, properties=["school"], simplify_m=30
            )
            # Read pre-computed dot-based demographics (matches interactive map)
            dot_zone_csv = Path(OUTPUT_DOT_ZONE_CSV)
            if dot_zone_csv.exists():
                _all = pd.read_csv(dot_zone_csv)
                drive_demo = _all[_all["zone_type"] == "Nearest Drive"].copy()
                drive_demo = drive_demo.drop(columns=["zone_type"], errors="ignore")
                _progress(f"Loaded {len(drive_demo)} drive zone rows from {dot_zone_csv.name}")
            else:
                raise FileNotFoundError(
                    f"{dot_zone_csv} not found — run school_socioeconomic_analysis.py first"
                )
            # Add MLS spatial join to drive zones
            if len(mls_data) > 0 and len(drive_demo) > 0:
                mls_wgs = mls_data.to_crs(CRS_WGS84)
                dz_wgs = drive_zones.to_crs(CRS_WGS84)
                mls_joined = gpd.sjoin(mls_wgs, dz_wgs[["school", "geometry"]],
                                       how="left", predicate="within")
                mls_agg = (mls_joined.dropna(subset=["school"]).groupby("school")
                           .agg(mls_total_sales=("close_price", "size"),
                                mls_median_price=("close_price", "median"))
                           .reset_index())
                drive_demo = drive_demo.merge(mls_agg, on="school", how="left")
                drive_demo["mls_total_sales"] = drive_demo["mls_total_sales"].fillna(0).astype(int)
                _progress(f"Added MLS data to {len(mls_agg)} drive zones")
            # Add affordable housing spatial join to drive zones
            if len(ah) > 0 and len(drive_demo) > 0:
                ah_wgs = ah.to_crs(CRS_WGS84)
                dz_wgs2 = drive_zones.to_crs(CRS_WGS84)
                ah_joined = gpd.sjoin(ah_wgs, dz_wgs2[["school", "geometry"]],
                                      how="left", predicate="within")
                ah_agg = (ah_joined.dropna(subset=["school"]).groupby("school")
                          .size().reset_index(name="ah_total_units"))
                drive_demo = drive_demo.merge(ah_agg, on="school", how="left")
                drive_demo["ah_total_units"] = drive_demo["ah_total_units"].fillna(0).astype(int)
                _progress(f"Added affordable housing data to {len(ah_agg)} drive zones")
            # Add planned dev spatial join to drive zones
            if len(dev_data) > 0 and len(drive_demo) > 0:
                dev_wgs = dev_data.to_crs(CRS_WGS84)
                dz_wgs3 = drive_zones.to_crs(CRS_WGS84)
                dev_joined = gpd.sjoin(dev_wgs, dz_wgs3[["school", "geometry"]],
                                       how="left", predicate="within")
                dev_agg = (dev_joined.dropna(subset=["school"]).groupby("school")
                           .agg(dev_total_units=("expected_units", "sum"),
                                dev_count=("expected_units", "size"))
                           .reset_index())
                drive_demo = drive_demo.merge(dev_agg, on="school", how="left")
                drive_demo["dev_total_units"] = drive_demo["dev_total_units"].fillna(0).astype(int)
                drive_demo["dev_count"] = drive_demo["dev_count"].fillna(0).astype(int)
                _progress(f"Added planned dev data to {len(dev_agg)} drive zones")
            # Add SAPFOTAC spatial join to drive zones
            if len(sapfotac_data) > 0 and len(drive_demo) > 0:
                sap_wgs = sapfotac_data.to_crs(CRS_WGS84)
                dz_wgs4 = drive_zones.to_crs(CRS_WGS84)
                sap_joined = gpd.sjoin(sap_wgs, dz_wgs4[["school", "geometry"]],
                                       how="left", predicate="within")
                sap_agg = (sap_joined.dropna(subset=["school"]).groupby("school")
                           .agg(sapfotac_total_units=("total_units_remaining", "sum"),
                                sapfotac_count=("total_units_remaining", "size"),
                                sapfotac_elem_students=("students_elementary", "sum"))
                           .reset_index())
                drive_demo = drive_demo.merge(sap_agg, on="school", how="left")
                drive_demo["sapfotac_total_units"] = drive_demo["sapfotac_total_units"].fillna(0).astype(int)
                drive_demo["sapfotac_count"] = drive_demo["sapfotac_count"].fillna(0).astype(int)
                drive_demo["sapfotac_elem_students"] = drive_demo["sapfotac_elem_students"].fillna(0).astype(int)
                _progress(f"Added SAPFOTAC data to {len(sap_agg)} drive zones")
            if len(drive_demo) > 0:
                avail_drive = [c for c in stat_cols if c in drive_demo.columns]
                drive_recs = drive_demo[avail_drive].to_dict("records")
                for rec in drive_recs:
                    for k, v in rec.items():
                        if isinstance(v, (np.integer, np.floating)):
                            rec[k] = float(v)
                        elif pd.isna(v):
                            rec[k] = 0
                drive_stats_json = json.dumps(drive_recs, separators=(",", ":"))
                has_drive_data = True
                _progress(f"Computed drive demographics for {len(drive_demo)} zones")
            else:
                _progress("WARNING: aggregate_zone_demographics returned empty")
        else:
            _progress("WARNING: No drive zones built (grid CSV missing or empty)")
    except Exception as e:
        _progress(f"WARNING: Could not compute drive demographics: {e}")

    # [11/12] Prepare age data (already in bg_json via properties)
    print("[11/12] Age choropleth data ready (embedded in block groups) ...")

    # [12/12] Build HTML
    print("[12/12] Building HTML ...")
    data = {
        "schools_json": schools_json,
        "district_json": district_json,
        "zones_json": zones_json,
        "bg_json": bg_json,
        "dot_data": dot_data,
        "ah_json": ah_json,
        "mls_json": mls_json,
        "dev_json": dev_json,
        "sapfotac_json": sapfotac_json,
        "zone_stats": zone_stats_json,
        "drive_stats": drive_stats_json,
        "drive_zones_json": drive_zones_json,
        "ephesus": ephesus_info,
        "seawell": seawell_info,
        "has_drive_data": has_drive_data,
    }

    html = build_html(data)

    OUTPUT_DIR.mkdir(parents=True, exist_ok=True)
    OUTPUT_HTML.write_text(html, encoding="utf-8")

    size_mb = OUTPUT_HTML.stat().st_size / (1024 * 1024)
    print(f"\nOutput: {OUTPUT_HTML}")
    print(f"Size: {size_mb:.1f} MB")
    print("Done!")


if __name__ == "__main__":
    main()
