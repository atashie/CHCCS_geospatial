"""Generate a scrollytelling methodology page for the socioeconomic analysis.

This module creates an interactive scrollytelling HTML page that walks readers
through the Census-based socioeconomic analysis methodology step by step,
explaining areal interpolation, dasymetric weighting, dot-density mapping,
and zone-level aggregation — using Northside Elementary as the illustrative
focus school (consistent with closure_story.py).

Architecture mirrors closure_story.py: two-column layout (45% narrative /
55% Leaflet map) with Scrollama-driven step transitions.

Usage:
    python src/socioeconomic_story.py
    python src/socioeconomic_story.py --cache-only   # same behavior (all cache-only)

Output:
    assets/maps/socioeconomic_methodology.html
"""

from __future__ import annotations

import argparse
import json
import warnings
from pathlib import Path

import geopandas as gpd
import numpy as np
import pandas as pd
from shapely.geometry import mapping

from school_socioeconomic_analysis import (
    _build_nearest_zones,
    OUTPUT_DOT_ZONE_CSV,
)

warnings.filterwarnings("ignore", category=FutureWarning)

# ---------------------------------------------------------------------------
# Path constants
# ---------------------------------------------------------------------------
PROJECT_ROOT = Path(__file__).resolve().parent.parent
DATA_RAW = PROJECT_ROOT / "data" / "raw"
DATA_PROCESSED = PROJECT_ROOT / "data" / "processed"
DATA_CACHE = PROJECT_ROOT / "data" / "cache"
ASSETS_MAPS = PROJECT_ROOT / "assets" / "maps"
OUTPUT_HTML = ASSETS_MAPS / "socioeconomic_methodology.html"

SCHOOL_CSV = DATA_CACHE / "nces_school_locations.csv"
DISTRICT_CACHE = DATA_CACHE / "chccs_district_boundary.gpkg"
CHCCS_SHP = DATA_RAW / "properties" / "CHCCS" / "CHCCS.shp"
PARCEL_POLYS = DATA_RAW / "properties" / "combined_data_polys.gpkg"
ACS_CACHE = DATA_CACHE / "census_acs_blockgroups.gpkg"
DECENNIAL_CACHE = DATA_CACHE / "census_decennial_blocks.gpkg"
AH_CACHE = DATA_CACHE / "affordable_housing.gpkg"
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

NORTHSIDE_NAME = "Northside Elementary"
NORTHSIDE_BBOX_PAD = 0.020  # degrees of lat/lon padding around Northside

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

# Dot-density race categories and colors (censusdots.com scheme)
RACE_CATEGORIES = {
    "white_alone": ("#3b5fc0", "White"),
    "black_alone": ("#41ae76", "Black"),
    "hispanic_total": ("#f2c94c", "Hispanic/Latino"),
    "asian_alone": ("#e74c3c", "Asian"),
    "two_plus": ("#9b59b6", "Multiracial"),
    "other_race": ("#a0522d", "Native American/Other"),
}

# Residential land-use code prefixes for parcel filtering
RESIDENTIAL_LUC_PREFIXES = ("100", "110", "120", "630", "EXH")


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
# Data loading functions (all cache-only)
# ---------------------------------------------------------------------------
def load_schools() -> pd.DataFrame:
    """Load NCES school locations from cache CSV."""
    if not SCHOOL_CSV.exists():
        raise FileNotFoundError(
            f"School locations not found: {SCHOOL_CSV}\n"
            "Run: python src/road_pollution.py  (to download NCES data)"
        )
    return pd.read_csv(SCHOOL_CSV)


def get_northside(schools: pd.DataFrame) -> dict:
    """Return dict with lat, lon, school for Northside Elementary."""
    ns = schools[schools["school"] == NORTHSIDE_NAME].iloc[0]
    return {"lat": float(ns["lat"]), "lon": float(ns["lon"]),
            "school": NORTHSIDE_NAME}


def get_bbox(center: dict, pad: float = NORTHSIDE_BBOX_PAD) -> tuple:
    """Return (south, north, west, east) bbox around center."""
    return (
        center["lat"] - pad, center["lat"] + pad,
        center["lon"] - pad, center["lon"] + pad,
    )


def load_district_boundary() -> gpd.GeoDataFrame:
    """Load CHCCS district boundary polygon."""
    if not DISTRICT_CACHE.exists():
        raise FileNotFoundError(
            f"District boundary not found: {DISTRICT_CACHE}\n"
            "Run: python src/school_desert.py  (to download boundary)"
        )
    return gpd.read_file(DISTRICT_CACHE)


def load_attendance_zones() -> gpd.GeoDataFrame:
    """Load and dissolve attendance zone polygons by ENAME."""
    if not CHCCS_SHP.exists():
        raise FileNotFoundError(f"CHCCS shapefile not found: {CHCCS_SHP}")
    raw = gpd.read_file(CHCCS_SHP).to_crs(CRS_WGS84)
    zones = raw.dissolve(by="ENAME").reset_index()
    zones["school"] = zones["ENAME"].map(_ENAME_TO_SCHOOL)
    zones = zones[zones["school"].notna()].copy()
    zones = zones[["school", "ENAME", "geometry"]].copy()
    return zones


def load_block_groups() -> gpd.GeoDataFrame:
    """Load ACS block group data with derived metrics."""
    if not ACS_CACHE.exists():
        raise FileNotFoundError(
            f"ACS block group cache not found: {ACS_CACHE}\n"
            "Run: python src/school_socioeconomic_analysis.py"
        )
    bg = gpd.read_file(ACS_CACHE)
    # Compute derived metrics inline (subset needed for visualization)
    # Coerce all numeric columns needed for visualisation and drive-zone analysis
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
    # Replace Census sentinel for median income
    bg["median_hh_income"] = bg["median_hh_income"].where(
        bg["median_hh_income"] > 0, np.nan
    )
    # Poverty columns
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
    # Derived columns needed by aggregate_zone_demographics for drive-zone analysis
    if "vehicles_zero_owner" in bg.columns and "vehicles_zero_renter" in bg.columns:
        bg["vehicles_zero"] = bg["vehicles_zero_owner"] + bg["vehicles_zero_renter"]
    elif "vehicles_zero" not in bg.columns:
        bg["vehicles_zero"] = 0

    # Low-income households (< $50k)
    low_income_cols = [f"income_{s}" for s in [
        "lt_10k", "10k_15k", "15k_20k", "20k_25k", "25k_30k",
        "30k_35k", "35k_40k", "40k_45k", "45k_50k",
    ]]
    avail_li = [c for c in low_income_cols if c in bg.columns]
    bg["hh_below_50k"] = bg[avail_li].sum(axis=1) if avail_li else 0

    # Single-parent families
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

    return bg


def load_blocks(bbox: tuple | None = None) -> gpd.GeoDataFrame:
    """Load Decennial Census blocks, optionally filtered to bbox."""
    if not DECENNIAL_CACHE.exists():
        raise FileNotFoundError(
            f"Decennial block cache not found: {DECENNIAL_CACHE}\n"
            "Run: python src/school_socioeconomic_analysis.py"
        )
    blocks = gpd.read_file(DECENNIAL_CACHE)
    blocks = blocks.to_crs(CRS_WGS84)
    if bbox is not None:
        south, north, west, east = bbox
        blocks = blocks.cx[west:east, south:north]
    return blocks.copy()


def load_residential_parcels(bbox: tuple | None = None) -> gpd.GeoDataFrame:
    """Load residential parcels, filtered to improved residential.

    If *bbox* is provided, clips to (south, north, west, east).
    """
    if not PARCEL_POLYS.exists():
        _progress("Parcel data not found, skipping")
        return gpd.GeoDataFrame()
    parcels = gpd.read_file(PARCEL_POLYS)
    parcels = parcels.to_crs(CRS_WGS84)
    # Filter to improved residential
    mask = parcels.get("is_residential", pd.Series(False, index=parcels.index))
    if "imp_vac" in parcels.columns:
        mask = mask & parcels["imp_vac"].str.contains(
            "Improved", case=False, na=False
        )
    parcels = parcels[mask].copy()
    if bbox is not None:
        south, north, west, east = bbox
        parcels = parcels.cx[west:east, south:north].copy()
    return parcels


def load_walk_zones() -> gpd.GeoDataFrame:
    """Load CHCCS walk zone polygons (ESWALK=='Y')."""
    if not CHCCS_SHP.exists():
        return gpd.GeoDataFrame()
    raw = gpd.read_file(CHCCS_SHP).to_crs(CRS_WGS84)
    walk = raw[raw["ESWALK"] == "Y"].copy()
    if walk.empty:
        return gpd.GeoDataFrame()
    walk = walk.dissolve(by="ENAME").reset_index()
    walk["school"] = walk["ENAME"].map(_ENAME_TO_SCHOOL)
    walk = walk[walk["school"].notna()].copy()
    return walk[["school", "geometry"]].copy()


def load_affordable_housing() -> gpd.GeoDataFrame:
    """Load affordable housing points."""
    if not AH_CACHE.exists():
        _progress("Affordable housing cache not found, skipping")
        return gpd.GeoDataFrame()
    return gpd.read_file(AH_CACHE)


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
    if not ZONE_DEMOGRAPHICS_CSV.exists():
        _progress("Zone demographics CSV not found, skipping")
        return pd.DataFrame()
    return pd.read_csv(ZONE_DEMOGRAPHICS_CSV)


# ---------------------------------------------------------------------------
# Processing functions
# ---------------------------------------------------------------------------
def compute_fragments(
    northside_zone: gpd.GeoDataFrame,
    block_groups: gpd.GeoDataFrame,
    parcels: gpd.GeoDataFrame,
) -> tuple[str, str]:
    """Compute zone-BG intersection fragments for Northside with both weight types.

    Returns (fragments_area_json, fragments_dasy_json) as GeoJSON strings.
    """
    zones_utm = northside_zone.to_crs(CRS_UTM17N)
    bg_utm = block_groups.to_crs(CRS_UTM17N)
    bg_utm["bg_area"] = bg_utm.geometry.area

    # Overlay to get fragments
    fragments = gpd.overlay(zones_utm, bg_utm, how="intersection")
    fragments["frag_area"] = fragments.geometry.area

    # Area weights
    fragments["weight_area"] = (
        fragments["frag_area"] / fragments["bg_area"]
    ).clip(upper=1.0)

    # Dasymetric weights (if parcels available)
    use_dasy = len(parcels) > 0
    if use_dasy:
        parcels_utm = parcels.to_crs(CRS_UTM17N)
        parcel_sindex = parcels_utm.sindex

        # Residential area in each full block group
        bg_res_areas = np.zeros(len(bg_utm))
        for i, geom in enumerate(bg_utm.geometry):
            candidates = list(parcel_sindex.intersection(geom.bounds))
            if candidates:
                clipped = parcels_utm.iloc[candidates].intersection(geom)
                bg_res_areas[i] = clipped.area.sum()
        bg_utm["bg_res_area"] = bg_res_areas

        # Re-overlay to pick up bg_res_area
        fragments = gpd.overlay(zones_utm, bg_utm, how="intersection")
        fragments["frag_area"] = fragments.geometry.area
        fragments["weight_area"] = (
            fragments["frag_area"] / fragments["bg_area"]
        ).clip(upper=1.0)

        # Residential area in each fragment
        frag_res_areas = np.zeros(len(fragments))
        for i, geom in enumerate(fragments.geometry):
            candidates = list(parcel_sindex.intersection(geom.bounds))
            if candidates:
                clipped = parcels_utm.iloc[candidates].intersection(geom)
                frag_res_areas[i] = clipped.area.sum()
        fragments["frag_res_area"] = frag_res_areas

        fragments["weight_dasy"] = np.where(
            fragments["bg_res_area"] > 0,
            (fragments["frag_res_area"] / fragments["bg_res_area"]).clip(upper=1.0),
            fragments["weight_area"],
        )
    else:
        fragments["weight_dasy"] = fragments["weight_area"]

    # Build two GeoJSON strings
    fragments_wgs = fragments.to_crs(CRS_WGS84)

    # Area-weighted version
    area_json = gdf_to_geojson_str(
        fragments_wgs,
        properties=["weight_area", "total_pop", "GEOID"],
        simplify_m=10,
    )

    # Dasymetric version
    dasy_json = gdf_to_geojson_str(
        fragments_wgs,
        properties=["weight_dasy", "weight_area", "total_pop", "GEOID",
                     "frag_res_area" if use_dasy else "frag_area"],
        simplify_m=10,
    )

    return area_json, dasy_json


def generate_dots(
    blocks: gpd.GeoDataFrame,
    parcels: gpd.GeoDataFrame,
) -> str:
    """Generate dot-density data for the given blocks.

    Returns JSON string: [[lat, lon, raceIdx], ...] (compact).
    Uses 1:1 dot-to-person ratio, constrained to residential parcels.
    """
    if blocks.empty:
        return "[]"

    blocks_utm = blocks.to_crs(CRS_UTM17N)

    # Prepare parcels
    use_parcels = len(parcels) > 0
    if use_parcels:
        parcels_utm = parcels.to_crs(CRS_UTM17N)
        parcel_sindex = parcels_utm.sindex

    race_keys = list(RACE_CATEGORIES.keys())
    rng = np.random.default_rng(42)

    # Ensure other_race exists
    if "other_race" not in blocks_utm.columns:
        other_cols = []
        for c in ["aian_alone", "nhpi_alone", "other_alone"]:
            if c in blocks_utm.columns:
                other_cols.append(c)
        if other_cols:
            blocks_utm["other_race"] = blocks_utm[other_cols].sum(axis=1).clip(lower=0)
        else:
            blocks_utm["other_race"] = 0

    raw_dots = []  # [(x_utm, y_utm, race_idx), ...]

    for _, block in blocks_utm.iterrows():
        block_geom = block.geometry
        if block_geom is None or block_geom.is_empty:
            continue

        # Determine placement geometry
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
                # Fallback: rejection sampling
                pt_list = _random_points_fallback(placement_geom, count, rng)

            for pt in pt_list:
                raw_dots.append((pt.x, pt.y, race_idx))

    if not raw_dots:
        return "[]"

    # Convert UTM to WGS84
    from pyproj import Transformer
    transformer = Transformer.from_crs(CRS_UTM17N, CRS_WGS84, always_xy=True)

    dots_wgs = []
    xs = [d[0] for d in raw_dots]
    ys = [d[1] for d in raw_dots]
    race_idxs = [d[2] for d in raw_dots]
    lons, lats = transformer.transform(xs, ys)

    for i in range(len(raw_dots)):
        dots_wgs.append([
            round(lats[i], 5), round(lons[i], 5), race_idxs[i]
        ])

    _progress(f"Generated {len(dots_wgs):,} dots")
    return json.dumps(dots_wgs, separators=(",", ":"))


def _random_points_fallback(geom, n: int, rng) -> list:
    """Rejection-sample random points within a geometry."""
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
    """Build the scrollytelling HTML page with 21 steps (incl. SAPFOTAC + bar chart)."""

    race_colors_js = json.dumps(
        [v[0] for v in RACE_CATEGORIES.values()], separators=(",", ":")
    )
    race_labels_js = json.dumps(
        [v[1] for v in RACE_CATEGORIES.values()], separators=(",", ":")
    )

    # Zone stats for popup display
    zone_stats = data.get("zone_stats", "[]")
    drive_stats = data.get("drive_stats", "[]")

    html = f"""<!DOCTYPE html>
<html lang="en">
<head>
<meta charset="utf-8">
<meta name="viewport" content="width=device-width, initial-scale=1">
<title>How the Socioeconomic Map Works &mdash; CHCCS District Analysis</title>
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
  border-left: 4px solid #2196F3;
  opacity: 0.3;
  transition: opacity 0.4s ease, border-color 0.3s ease;
}}

.step:first-child {{ margin-top: 40vh; }}
.step:last-child {{ margin-bottom: 60vh; }}
.step.is-active {{ opacity: 1; border-color: #1565C0; }}

.step-number {{
  display: inline-block;
  width: 28px; height: 28px;
  background: #2196F3;
  color: white;
  border-radius: 50%;
  text-align: center;
  line-height: 28px;
  font-weight: bold;
  font-size: 14px;
  margin-bottom: 10px;
}}

h2 {{ color: #1565C0; margin: 10px 0 15px; font-size: 1.3em; }}
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

.formula {{
  background: #f5f5f5;
  padding: 10px 15px;
  border-radius: 4px;
  margin: 10px 0;
  font-family: 'Consolas', 'Courier New', monospace;
  font-size: 0.9em;
  overflow-x: auto;
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
  color: #1565C0;
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

.weight-legend {{
  display: flex;
  align-items: center;
  gap: 8px;
  margin: 8px 0;
  font-size: 0.85em;
}}

.legend-bar {{
  height: 14px;
  flex: 1;
  border-radius: 3px;
}}

.legend-labels {{
  display: flex;
  justify-content: space-between;
  font-size: 0.8em;
  color: #666;
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

  <!-- Step 0: Intro -->
  <div class="step" data-step="0">
    <div class="step-number">1</div>
    <h2>How the Socioeconomic Map Works</h2>
    <p>The CHCCS socioeconomic analysis characterizes the demographic profiles
    of elementary school attendance zones using U.S. Census data. This page
    walks through the methodology step by step.</p>
    <p>We&rsquo;ll cover:</p>
    <ul style="margin: 10px 0 10px 20px; line-height: 1.8;">
      <li>How Census data is mapped to school zones</li>
      <li>Why using residential land improves accuracy</li>
      <li>How the dot-density map is generated</li>
      <li>Key limitations you should know about</li>
    </ul>
    <p>The map on the right shows the CHCCS district boundary and all 11
    elementary schools.</p>
    <div class="source">
      <strong>Data:</strong> NCES EDGE School Locations 2023-24 (LEAID 3700720)
      &bull; Census TIGER/Line Unified School Districts 2024
    </div>
  </div>

  <!-- Step 1: Schools & Attendance Zones -->
  <div class="step" data-step="1">
    <div class="step-number">2</div>
    <h2>Schools &amp; Attendance Zones</h2>
    <p>Each CHCCS elementary school has an <strong>attendance zone</strong> &mdash;
    the geographic area from which students are assigned to that school by
    default. These zones tile the district with no gaps or overlaps.</p>
    <p>The colored polygons show the 10 attendance zones from the CHCCS
    administrative shapefile. (Frank Porter Graham Bilingue is a district-wide
    magnet school and may not have a distinct zone polygon.)</p>
    <div class="source">
      <strong>Data:</strong> CHCCS elementary attendance zone shapefile,
      dissolved by ENAME field.
    </div>
    <div class="limitation">
      <strong>Limitation:</strong> Attendance zones define <em>default</em>
      assignment. Actual enrollment differs due to transfers, charter/private
      school attendance, and the FPG magnet program.
    </div>
  </div>

  <!-- Step 2: Census Block Groups -->
  <div class="step" data-step="2">
    <div class="step-number">3</div>
    <h2>Census Block Groups</h2>
    <p>Demographic data comes from the <strong>American Community Survey
    (ACS) 5-Year Estimates</strong> (2020&ndash;2024). The Census Bureau
    publishes this data at the <strong>block group</strong> level &mdash;
    neighborhood-sized areas of roughly 600&ndash;3,000 people.</p>
    <p>The map now shows block group boundaries (blue outlines) around the
    Northside Elementary area. Notice how they <em>don&rsquo;t line up</em>
    with school zone boundaries.</p>
    <div class="source">
      <strong>Data:</strong> U.S. Census Bureau, ACS 5-Year 2020&ndash;2024
      &bull; TIGER/Line 2024 block group geometries (Orange County, NC)
    </div>
  </div>

  <!-- Step 3: The Mismatch -->
  <div class="step" data-step="3">
    <div class="step-number">4</div>
    <h2>The Mismatch: Zones vs. Block Groups</h2>
    <p>Here&rsquo;s the core problem: <strong>Census neighborhoods and school
    zones don&rsquo;t share boundaries.</strong> The Census Bureau draws block
    groups based on population size and county lines. The school board draws
    attendance zones based on enrollment and geography. Neither was designed
    to match the other.</p>
    <p>The <span style="color:#e41a1c;font-weight:bold;">red outline</span>
    shows Northside&rsquo;s zone boundary cutting across multiple block groups.
    A single block group may span multiple zones, and a single zone may contain
    parts of many block groups.</p>
    <p>So how do we figure out the demographics of a school zone when the
    Census data is organized by completely different boundaries? That&rsquo;s
    the challenge the next steps address.</p>
    <details>
      <summary>Technical background</summary>
      <p>This is formally known as the <strong>areal interpolation problem</strong>,
      first defined by Goodchild &amp; Lam (1980): how do you transfer data from
      one set of geographic units to another?</p>
    </details>
  </div>

  <!-- Step 4: Area Weighting -->
  <div class="step" data-step="4">
    <div class="step-number">5</div>
    <h2>Simple Area Weighting</h2>
    <p>The simplest approach: where a block group is split by a zone
    boundary, <strong>divide the population based on land area</strong>. If
    40% of a block group&rsquo;s area falls inside a school zone, that zone
    gets 40% of the people.</p>
    <p>Think of it like <strong>spreading peanut butter on toast</strong>:
    the population is smeared uniformly across the entire block group, and
    you get whatever share of peanut butter matches your share of the bread.</p>
    <details>
      <summary>See the formula</summary>
      <div class="formula">
        &#374;_t = &Sigma;_s (A_st / A_s) &times; Y_s
      </div>
      <p>where A_st is the overlap area between source zone <em>s</em> and
      target zone <em>t</em>, and A_s is the total area of source zone
      <em>s</em>.</p>
    </details>
    <p>The colored fragments show each zone&ndash;block group intersection
    piece, shaded by its area weight (darker = higher fraction of the block
    group&rsquo;s area).</p>
    <div class="limitation">
      <strong>Problem:</strong> People don&rsquo;t live in parking lots, parks,
      or commercial buildings. A block group that is half apartments and half
      shopping mall will have its population spread across both, even though
      100% of residents live in the apartment half.
    </div>
  </div>

  <!-- Step 5: Residential Parcels -->
  <div class="step" data-step="5">
    <div class="step-number">6</div>
    <h2>Residential Parcels</h2>
    <p>To fix the &ldquo;peanut butter&rdquo; problem, we bring in
    additional information: <strong>property boundaries</strong> from the
    Orange County Tax Assessor that show exactly where homes are.</p>
    <p>The <span style="color:#27ae60;font-weight:bold;">green polygons</span>
    show improved residential parcels &mdash; properties with homes where
    people actually live. Notice how they cluster in neighborhoods, leaving
    commercial areas, parks, and institutional land empty.</p>
    <p>We filter to only improved residential properties, excluding vacant
    lots, commercial buildings, and other non-residential land.</p>
    <details>
      <summary>Land-use filter details</summary>
      <p>Parcels are selected where <code>is_residential = True</code> and
      <code>imp_vac contains &quot;Improved&quot;</code>. Residential land-use
      codes include 100, 110, 120, 630, and EXH prefixes.</p>
    </details>
    <div class="source">
      <strong>Data:</strong> Orange County, NC Tax Assessor parcel data
      &bull; Residential land-use codes: 100, 110, 120, 630, EXH prefixes
    </div>
  </div>

  <!-- Step 6: Dasymetric Weighting -->
  <div class="step" data-step="6">
    <div class="step-number">7</div>
    <h2>Dasymetric Weighting</h2>
    <p>Instead of treating all land equally, we now <strong>only count
    residential land</strong> when splitting population between zones. This
    technique is called <strong>dasymetric mapping</strong> &mdash; it uses
    extra information (here, where homes are) to place people more
    accurately.</p>
    <p>The fragments are now colored by their <strong>residential-land
    weight</strong>. Compare with the previous step: areas with more homes
    get higher weights, while areas covering commercial or institutional
    land get lower weights.</p>
    <details>
      <summary>See the formula</summary>
      <div class="formula">
        &#374;_t = &Sigma;_s (R_st / R_s) &times; Y_s
      </div>
      <p>where R_st is the residential parcel area within the intersection of
      source zone <em>s</em> and target zone <em>t</em>, and R_s is the total
      residential parcel area in source zone <em>s</em>.</p>
    </details>
    <details>
      <summary>Technical details</summary>
      <p>An R-tree spatial index on parcel polygons enables efficient
      intersection queries. For each geometry, candidate parcels are identified
      via bounding-box lookup, then precisely clipped, and their areas summed.</p>
      <p>When a block group has no residential parcels (R_s = 0), the algorithm
      falls back to plain area weighting. In the CHCCS district, 179 of 184
      fragments received dasymetric weights; 5 used the area-weighted fallback.</p>
    </details>
    <details>
      <summary>References</summary>
      <p>Wright, J. K. (1936). A method of mapping densities of population.
      <em>Geographical Review</em>, 26(1), 103&ndash;110.</p>
      <p>Goodchild, M. F. &amp; Lam, N. S. (1980). Areal interpolation.
      <em>Geo-Processing</em>, 1, 297&ndash;312.</p>
      <p>Mennis, J. (2003). Generating surface models of population using
      dasymetric mapping. <em>The Professional Geographer</em>, 55(1),
      31&ndash;42.</p>
    </details>
  </div>

  <!-- Step 7: Derived Metrics -->
  <div class="step" data-step="7">
    <div class="step-number">8</div>
    <h2>From Counts to Metrics</h2>
    <p>The ACS provides <strong>raw counts</strong> (population, households
    below poverty, renter-occupied units, etc.) at the block group level. We
    compute 10 derived metrics from these counts:</p>
    <ul style="margin: 8px 0 8px 20px; line-height: 1.7; font-size: 0.95em;">
      <li>% Below 185% poverty &mdash; a proxy for Free/Reduced-price Lunch
      (FRL) eligibility</li>
      <li>% Minority (non-White, non-Hispanic)</li>
      <li>% Black &bull; % Hispanic</li>
      <li>% Renter-occupied housing</li>
      <li>% Zero-vehicle households</li>
      <li>% Single-parent families</li>
      <li>% Elementary-age (5&ndash;9) &bull; % Young children (0&ndash;4)</li>
      <li>Median household income</li>
    </ul>
    <p>The map shows block groups colored by <strong>% below 185% poverty</strong>
    (the FRL eligibility proxy). Darker red = higher poverty concentration.</p>
    <details>
      <summary>ACS table codes</summary>
      <p>C17002 (poverty ratio), B03002 (race/ethnicity), B25003 (tenure),
      B25044 (vehicles), B11003 (family type), B01001 (age/sex), B19013
      (median income)</p>
    </details>
    <div class="source">
      <strong>Data:</strong> ACS 5-Year 2020&ndash;2024
    </div>
  </div>

  <!-- Step 8: Why Recompute -->
  <div class="step" data-step="8">
    <div class="step-number">9</div>
    <h2>Why Recompute, Not Average?</h2>
    <p>A critical detail: when aggregating block groups into zones, we
    <strong>sum the counts first, then recompute percentages</strong> &mdash;
    we never average percentages directly.</p>
    <p>Why? Consider two block groups overlapping a zone:</p>
    <table style="margin: 10px 0; border-collapse: collapse; width: 100%;
                  font-size: 0.9em;">
      <tr style="background: #e3f2fd;">
        <th style="padding: 6px; text-align: left;">Block Group</th>
        <th style="padding: 6px;">Population</th>
        <th style="padding: 6px;">Below 185% FPL</th>
        <th style="padding: 6px;">% Poverty</th>
      </tr>
      <tr>
        <td style="padding: 6px;">BG A (weight 0.8)</td>
        <td style="padding: 6px; text-align: center;">2,000</td>
        <td style="padding: 6px; text-align: center;">600</td>
        <td style="padding: 6px; text-align: center;">30%</td>
      </tr>
      <tr style="background: #f5f5f5;">
        <td style="padding: 6px;">BG B (weight 0.3)</td>
        <td style="padding: 6px; text-align: center;">500</td>
        <td style="padding: 6px; text-align: center;">25</td>
        <td style="padding: 6px; text-align: center;">5%</td>
      </tr>
    </table>
    <p><strong>Wrong (averaging the percentages):</strong>
    (30% &times; 0.8 + 5% &times; 0.3) / (0.8 + 0.3) = 23.2%</p>
    <p><strong>Correct (adding up the people first):</strong>
    (600 &times; 0.8 + 25 &times; 0.3) / (2000 &times; 0.8 + 500 &times; 0.3)
    = 487.5 / 1750 = <strong>27.9%</strong></p>
    <p>Averaging percentages gives a tiny group of 500 people nearly as much
    influence as a group of 2,000 &mdash; a well-known statistical pitfall
    that distorts the true picture.</p>
    <details>
      <summary>Reference</summary>
      <p>Robinson, W. S. (1950). Ecological correlations and the behavior
      of individuals. <em>American Sociological Review</em>, 15(3),
      351&ndash;357.</p>
    </details>
  </div>

  <!-- Step 9: Block-Level Race Data -->
  <div class="step" data-step="9">
    <div class="step-number">10</div>
    <h2>Block-Level Race Data (Decennial)</h2>
    <p>For the dot-density map, we need <strong>finer detail</strong> than
    block groups. The 2020 Decennial Census provides race/ethnicity counts at
    the <strong>block level</strong> &mdash; much smaller areas, often just a
    few city blocks.</p>
    <p>The map shows Census block boundaries (dashed outlines) within the
    Northside area. Each block has population counts for 6 race categories.</p>
    <details>
      <summary>Census table codes</summary>
      <p>2020 Census P.L. 94-171 Redistricting Data, tables P1 (race)
      and P2 (Hispanic origin)</p>
    </details>
    <div class="source">
      <strong>Data:</strong> 2020 Decennial Census, block-level race data
      &bull; TIGER/Line 2020 block geometries
    </div>
    <div class="limitation">
      <strong>Limitation:</strong> To protect people&rsquo;s privacy, the
      Census Bureau adds small random changes to block-level counts (a
      technique called &ldquo;differential privacy&rdquo;). A block with
      3 Asian residents might be published as 0 or 7. The overall geographic
      patterns remain reliable despite this noise in individual blocks.
    </div>
  </div>

  <!-- Step 10: Downscaling ACS to Blocks -->
  <div class="step" data-step="10">
    <div class="step-number">11</div>
    <h2>Downscaling ACS to Blocks</h2>
    <p>The Decennial Census gives us block-level <em>race</em> data, but the
    ACS&rsquo;s <em>socioeconomic</em> data (income, poverty, etc.) is only
    available at the larger block-group level. To create detailed
    color-coded maps, we split the block-group data down to individual
    blocks.</p>
    <p>Each small block gets a share of its neighborhood&rsquo;s data, based
    on how much residential land it contains. Counts like population are
    divided proportionally, while median income is copied directly (you
    can&rsquo;t split a median).</p>
    <details>
      <summary>See the formula</summary>
      <div class="formula">
        block_count = bg_count &times; (block_res_area / bg_res_area)
      </div>
      <p>Each block inherits a fraction of its parent block group&rsquo;s
      counts, weighted by residential parcel area. Percentages are
      recomputed from the downscaled counts.</p>
    </details>
    <p>The map shows blocks colored by downscaled <strong>% below 185%
    poverty</strong>.</p>
    <div class="limitation">
      <strong>Limitation:</strong> Downscaling adds another layer of
      estimation uncertainty. Block-level poverty values are
      <em>model outputs</em>, not direct observations.
    </div>
  </div>

  <!-- Step 11: Dot-Density Map -->
  <div class="step" data-step="11">
    <div class="step-number">12</div>
    <h2>Dot-Density Map: One Dot Per Person</h2>
    <p>The dot-density layer represents every person in the 2020 Census as
    a <strong>single colored dot</strong>, placed randomly within their
    Census block. Six race/ethnicity categories are shown:</p>
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
    <p>At 1:1 ratio, the full district map contains ~95,000 dots. This page
    shows only the Northside area to keep the file manageable.</p>
    <div class="source">
      <strong>Data:</strong> 2020 Decennial Census, P.L. 94-171 block-level
      race data &bull; Color scheme from censusdots.com
    </div>
  </div>

  <!-- Step 12: Constraining Dots to Parcels -->
  <div class="step" data-step="12">
    <div class="step-number">13</div>
    <h2>Constraining Dots to Parcels</h2>
    <p>Instead of scattering dots randomly across entire Census blocks &mdash;
    including roads, parking lots, and commercial buildings &mdash; we
    <strong>place dots only on residential land</strong>. Each dot is
    constrained to the overlap between its Census block and nearby
    residential parcels.</p>
    <p>Compare the dots with the green parcel outlines: dots cluster on
    residential land, not on roads or institutional areas.</p>
    <details>
      <summary>Algorithm details</summary>
      <p>For each block, a placement geometry is computed as
      <code>block &cap; union(nearby_residential_parcels)</code>. If the
      intersection area is &lt; 10 m&sup2;, the full block is used as
      fallback. Random points are generated via <code>shapely.random_points()
      </code> with a fixed seed (<code>rng = np.random.default_rng(42)</code>)
      for reproducibility.</p>
    </details>
    <div class="limitation">
      <strong>Remaining limitation:</strong> Within residential parcels, dots
      are placed uniformly. A 200-unit apartment complex and a 1-acre
      single-family lot receive the same dot density per unit area, despite
      very different population densities.
    </div>
  </div>

  <!-- Step 13: Zone Aggregation -->
  <div class="step" data-step="13">
    <div class="step-number">14</div>
    <h2>Zone Aggregation</h2>
    <p>The final step: <strong>add up all the weighted pieces</strong> to get
    a demographic profile for each school&rsquo;s attendance zone. Counts are
    summed first, then percentages are calculated from those totals.</p>
    <p>The map shows attendance zones colored by <strong>% below 185%
    poverty</strong> (the FRL eligibility proxy). Click any zone to see its
    full demographic profile.</p>
    <div class="limitation">
      <strong>Key finding:</strong> The residential-land weighting produced
      meaningful shifts. For example, one Title I school zone shifted from
      19.2% to 23.5% poverty (+4.3 percentage points) after weighting by
      residential land, moving closer to the known 30&ndash;36% FRL rate.
    </div>
  </div>

  <!-- Step 14: MLS Home Sales -->
  <div class="step" data-step="14">
    <div class="step-number">15</div>
    <h2>MLS Home Sales</h2>
    <p>The map shows <strong>MLS home sale records</strong> (2023&ndash;2025)
    across the district, color-coded by price quartile. Each dot is a closed
    sale.</p>
    <p>Home sale data adds a market-level perspective to Census demographics.
    The production map lets users switch between zone definitions and see
    how sale counts, median prices, and price-per-square-foot shift when
    boundaries are redrawn by proximity rather than official assignment.</p>
    <div class="source">
      <strong>Data:</strong> Triangle MLS, closed sales 2023&ndash;2025
      within CHCCS district
    </div>
  </div>

  <!-- Step 15: Affordable Housing -->
  <div class="step" data-step="15">
    <div class="step-number">16</div>
    <h2>Affordable Housing</h2>
    <p>The map shows <strong>affordable housing locations</strong> across
    the district, color-coded by AMI (Area Median Income) band. Each dot
    is a subsidized unit.</p>
    <p>Affordable housing is unevenly distributed across the district.
    The production map aggregates unit counts per zone, allowing
    comparison of how many subsidized units fall within each school&rsquo;s
    catchment area under different zone definitions.</p>
    <div class="source">
      <strong>Data:</strong> Town of Chapel Hill ArcGIS (2025)
    </div>
  </div>

  <!-- Step 16: MLS Home Sales -->
  <div class="step" data-step="16">
    <div class="step-number">17</div>
    <h2>MLS Home Sales</h2>
    <p>The map can overlay <strong>MLS home sale records</strong> from the
    Triangle MLS (2023&ndash;2025). Each dot represents a closed sale,
    color-coded by <strong>price quartile</strong>:</p>
    <div class="dot-legend">
      <div class="dot-legend-item">
        <span class="dot-legend-swatch" style="background:#2166ac;"></span> Bottom 25%
      </div>
      <div class="dot-legend-item">
        <span class="dot-legend-swatch" style="background:#67a9cf;"></span> 25th&ndash;50th pctl
      </div>
      <div class="dot-legend-item">
        <span class="dot-legend-swatch" style="background:#fc8d59;"></span> 50th&ndash;75th pctl
      </div>
      <div class="dot-legend-item">
        <span class="dot-legend-swatch" style="background:#b2182b;"></span> Top 25%
      </div>
    </div>
    <p>Addresses are geocoded using the <strong>Census Bureau batch geocoder
    </strong> (primary) with Nominatim fallback, then clipped to the CHCCS
    district boundary. Three metrics are computed per zone:</p>
    <ul style="margin: 4px 0 12px 20px; line-height: 1.7; font-size: 0.93em;">
      <li><strong>Homes Sold</strong> &mdash; total count of closed sales</li>
      <li><strong>Median Home Price</strong> &mdash; median close price</li>
      <li><strong>Median Price/SqFt</strong> &mdash; median close price per square foot</li>
    </ul>
    <h3>Limitations</h3>
    <ul style="margin: 4px 0 12px 20px; line-height: 1.7; font-size: 0.93em;">
      <li>MLS data covers only listed sales &mdash; FSBO and off-market
      transactions are excluded.</li>
      <li>Geocoding accuracy varies; some addresses may map to approximate
      locations.</li>
      <li>Blocks with few sales may show volatile medians (small sample size).</li>
      <li>Sales span three years (2023&ndash;2025) and do not reflect
      single-point-in-time pricing.</li>
    </ul>
    <div class="source">
      <strong>Data:</strong> Triangle MLS, closed sales 2023&ndash;2025
      within CHCCS district
    </div>
  </div>

  <!-- Step 17: Planned Developments (CH Active Dev) -->
  <div class="step" data-step="17">
    <div class="step-number">18</div>
    <h2>Planned Developments (CH Active Dev)</h2>
    <p>The map can overlay <strong>planned residential developments</strong>
    from the Town of Chapel Hill&rsquo;s
    <a href="https://www.chapelhillnc.gov/Business-and-Development/Active-Development"
    target="_blank" style="color:#1565C0;">Active Development</a> page
    (hand-transcribed March 12, 2026). Each circle represents one development,
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
    <p>Developments are geocoded using the <strong>Census Bureau batch geocoder
    </strong> (primary) with Nominatim fallback, then clipped to the CHCCS
    district boundary. Two metrics are computed per zone:</p>
    <ul style="margin: 4px 0 12px 20px; line-height: 1.7; font-size: 0.93em;">
      <li><strong>Total Expected Units</strong> &mdash; sum of expected units
      across all developments in the zone</li>
      <li><strong>Number of Developments</strong> &mdash; count of projects
      in the zone</li>
    </ul>
    <h3>Limitations</h3>
    <ul style="margin: 4px 0 12px 20px; line-height: 1.7; font-size: 0.93em;">
      <li>Covers <strong>Chapel Hill only</strong> &mdash; no Carrboro or
      unincorporated Orange County projects.</li>
      <li>Projects at various approval stages &mdash; some may not proceed
      or may change scope.</li>
      <li>Unit counts are estimates from planning documents, not final
      construction figures.</li>
      <li>Geocoding is approximate (road-segment interpolation, not exact
      site boundaries).</li>
    </ul>
    <div class="source">
      <strong>Data:</strong> Town of Chapel Hill Active Development page,
      hand-transcribed March 12, 2026
    </div>
  </div>

  <!-- Step 18: Planned Developments (SAPFOTAC) -->
  <div class="step" data-step="18">
    <div class="step-number">19</div>
    <h2>Planned Developments (SAPFOTAC)</h2>
    <p>A supplementary dataset from the <strong>SAPFOTAC 2025 Annual Report</strong>
    (Student Attendance Projections and Facility Optimization Technical Advisory
    Committee, certified June&nbsp;3, 2025) provides 21 future residential projects
    with <strong>projected student yields</strong> &mdash; estimates of elementary,
    middle, and high school students each development will generate.</p>
    <p>The same blue&ndash;to&ndash;red color scheme applies, scaled by
    remaining housing units. Click a marker for per-project detail including
    student yield breakdowns.</p>
    <h3>How it differs from CH Active Dev</h3>
    <ul style="margin: 4px 0 12px 20px; line-height: 1.7; font-size: 0.93em;">
      <li><strong>Adds student yield projections</strong> (elementary, middle,
      high) &mdash; not available in CH Active Dev.</li>
      <li><strong>Covers Chapel Hill + Carrboro</strong> (e.g., Jade Creek,
      Newbury).</li>
      <li><strong>Different vintage</strong> &mdash; certified June 2025
      vs. March 2026 for CH Active Dev.</li>
      <li><strong>Some overlap</strong> &mdash; projects like Gateway,
      South Creek, and Aura Chapel Hill appear in both sources.
      The datasets are <em>not</em> deduplicated.</li>
    </ul>
    <h3>Limitations</h3>
    <ul style="margin: 4px 0 12px 20px; line-height: 1.7; font-size: 0.93em;">
      <li>Student yields are <strong>model estimates</strong> based on
      generation rates, not actual enrollment.</li>
      <li>Geocoding is approximate (same Census + Nominatim pipeline).</li>
      <li>Some projects fall outside the CHCCS district boundary and are
      not assigned to any zone in bar-chart aggregation.</li>
    </ul>
    <div class="source">
      <strong>Data:</strong> SAPFOTAC 2025 Annual Report, certified
      June 3, 2025
    </div>
  </div>

  <!-- Step 19: Complete Map — Bar Chart Comparison -->
  <div class="step" data-step="19">
    <div class="step-number">20</div>
    <h2>Zone Definitions Matter</h2>
    <p>The bar charts show <strong>% below 185% poverty</strong> for each
    school under two different zone definitions &mdash; computed from the
    same underlying Census data using the same dasymetric methodology.</p>
    <p><strong>School Zones</strong> use the official CHCCS attendance
    boundaries. <strong>Nearest Drive</strong> assigns each location to
    whichever school is closest by driving distance (Dijkstra shortest-path
    on the OpenStreetMap road network).</p>
    <p>The differences illustrate how boundary definitions affect
    school-level demographic composition. A school whose official zone
    extends into a lower-income area may show a very different poverty
    rate when zone boundaries are redrawn by proximity.</p>
    <p>The full production map lets users switch between 5 zone types
    interactively, with bar charts that update in real time.</p>
  </div>

  <!-- Step 20: Limitations -->
  <div class="step" data-step="20">
    <div class="step-number">21</div>
    <h2>Limitations &amp; Caveats</h2>
    <p>The socioeconomic analysis has 26 documented limitations. Key ones:</p>
    <h3>Data Quality</h3>
    <ul style="margin: 4px 0 12px 20px; line-height: 1.7; font-size: 0.93em;">
      <li><strong>ACS margins of error</strong> are not tracked or displayed.
      For small block groups, confidence intervals can be wide.</li>
      <li><strong>5-year rolling average</strong> masks recent demographic
      shifts (new housing, gentrification).</li>
      <li><strong>Privacy protections in Census data</strong> can distort
      small race counts at the block level.</li>
    </ul>
    <h3>Geographic Alignment</h3>
    <ul style="margin: 4px 0 12px 20px; line-height: 1.7; font-size: 0.93em;">
      <li><strong>Zone &ne; enrollment:</strong> Zone demographics describe
      <em>residents</em>, not enrolled students. There is a known gap of
      roughly 10 percentage points between zone poverty estimates and
      actual school FRL rates.</li>
      <li><strong>Temporal mismatch:</strong> ACS (2020&ndash;2024), Decennial
      (2020), parcels (current), zones (current) use different vintages.</li>
      <li><strong>Edge distortion:</strong> Block groups at district edges
      extend into neighboring districts; clipping creates slivers with
      potentially unrepresentative demographics.</li>
    </ul>
    <h3>Methodological</h3>
    <ul style="margin: 4px 0 12px 20px; line-height: 1.7; font-size: 0.93em;">
      <li><strong>Median income approximation:</strong> Weighted average of
      block group medians &ne; true zone median.</li>
      <li><strong>Uniform dot placement</strong> within parcels ignores
      density variation (apartments vs. single-family).</li>
      <li><strong>No ACS margins of error</strong> propagated through
      interpolation.</li>
    </ul>
    <details>
      <summary>Full documentation</summary>
      <p>The complete list of 26 limitations with references and validation
      results is available in the project documentation.</p>
    </details>
    <div class="limitation">
      <strong>Bottom line:</strong> The analysis provides useful <em>relative</em>
      comparisons between zones (which zones have higher/lower poverty,
      more/less diversity) but the absolute numbers carry meaningful
      uncertainty. Treat all values as estimates, not precise counts.
    </div>
  </div>

</div> <!-- end scroll-container -->
<div id="map-container">
  <div id="map"></div>
  <div id="map-dim"></div>
  <div id="chart-panel" style="position:absolute;top:0;left:0;width:100%;height:100%;
    z-index:500;background:#fff;display:none;overflow-y:auto;padding:24px 20px;">
    <h3 style="text-align:center;margin:0 0 4px;font-size:1.15em;color:#333;">
      % Below 185% Poverty by School</h3>
    <p style="text-align:center;margin:0 0 16px;font-size:0.82em;color:#777;">
      Same Census data, two different zone definitions</p>
    <div style="display:flex;gap:16px;">
      <div style="flex:1;">
        <h4 style="text-align:center;margin:0 0 8px;font-size:0.95em;color:#555;">
          School Zones <span style="font-weight:normal;font-size:0.85em;">(official attendance)</span></h4>
        <div id="chart-school-zones"></div>
      </div>
      <div style="flex:1;">
        <h4 style="text-align:center;margin:0 0 8px;font-size:0.95em;color:#555;">
          Nearest Drive <span style="font-weight:normal;font-size:0.85em;">(Dijkstra shortest-path)</span></h4>
        <div id="chart-nearest-drive"></div>
      </div>
    </div>
    <p style="text-align:center;margin:16px 0 0;font-size:0.8em;color:#999;line-height:1.4;">
      Bars show % of population below 185% of the federal poverty line within each zone.
      <br>Different zone definitions produce different school-level demographics from the same underlying data.</p>
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
var BLOCKS = {data["blocks_json"]};
var PARCELS = {data["parcels_json"]};
var FRAGMENTS_AREA = {data["fragments_area_json"]};
var FRAGMENTS_DASY = {data["fragments_dasy_json"]};
var DOT_DATA = {data["dot_data"]};
var AH = {data["ah_json"]};
var MLS_SALES = {data["mls_json"]};
var PLANNED_DEV = {data["dev_json"]};
var SAPFOTAC_DEV = {data["sapfotac_json"]};
var ZONE_STATS = {zone_stats};
var DRIVE_STATS = {drive_stats};
var WALK_ZONES = {data["walk_zones_json"]};
var NORTHSIDE = {json.dumps(data["northside"])};

var RACE_COLORS = {race_colors_js};
var RACE_LABELS = {race_labels_js};

// AMI colors for affordable housing
var AMI_COLORS = {{
  "0-30%": "#d73027", "30-60%": "#fc8d59",
  "60-80%": "#fee090", "80%+": "#91bfdb"
}};

// === Bar chart builder ===
function buildBarCharts() {{
  var zoneData = ZONE_STATS.map(function(s) {{
    return {{ school: s.school, pct: s.pct_below_185_poverty || 0 }};
  }});
  zoneData.sort(function(a, b) {{ return b.pct - a.pct; }});
  var order = zoneData.map(function(d) {{ return d.school; }});

  var driveMap = {{}};
  DRIVE_STATS.forEach(function(s) {{ driveMap[s.school] = s.pct_below_185_poverty || 0; }});
  var driveData = order.map(function(name) {{
    return {{ school: name, pct: driveMap[name] || 0 }};
  }});

  var allVals = zoneData.map(function(d){{ return d.pct; }})
    .concat(driveData.map(function(d){{ return d.pct; }}));
  var maxVal = Math.max.apply(null, allVals) || 1;

  function renderBars(container, data) {{
    var html = "";
    data.forEach(function(d) {{
      var pct = d.pct.toFixed(1);
      var width = (d.pct / maxVal * 100).toFixed(1);
      var label = d.school.replace(" Elementary", "").replace(" Bilingue", "");
      html += '<div style="display:flex;align-items:center;margin:3px 0;font-size:0.78em;">'
        + '<div style="width:90px;text-align:right;padding-right:6px;color:#555;white-space:nowrap;overflow:hidden;text-overflow:ellipsis;">'
        + label + '</div>'
        + '<div style="flex:1;background:#eee;border-radius:3px;height:16px;position:relative;">'
        + '<div style="width:' + width + '%;height:100%;background:#4a9ebb;border-radius:3px;"></div>'
        + '</div>'
        + '<div style="width:40px;text-align:right;padding-left:4px;color:#555;font-size:0.9em;">'
        + pct + '%</div></div>';
    }});
    document.getElementById(container).innerHTML = html;
  }}

  renderBars("chart-school-zones", zoneData);
  renderBars("chart-nearest-drive", driveData);
}}
buildBarCharts();

// === Map setup ===
var map = L.map("map", {{
  center: [NORTHSIDE.lat, NORTHSIDE.lon],
  zoom: 13,
  scrollWheelZoom: false,
  zoomControl: true,
}});

L.tileLayer("https://{{s}}.basemaps.cartocdn.com/light_all/{{z}}/{{x}}/{{y}}@2x.png", {{
  attribution: '&copy; <a href="https://carto.com/">CARTO</a> &copy; <a href="https://www.openstreetmap.org/copyright">OSM</a>',
  maxZoom: 19,
}}).addTo(map);

var dimOverlay = document.getElementById("map-dim");
var ns = [NORTHSIDE.lat, NORTHSIDE.lon];

// Compute district bounds for fitBounds
var districtBounds = L.geoJSON(DISTRICT).getBounds();
function districtView() {{
  map.fitBounds(districtBounds.pad(0.05));
}}

// Zone colors (consistent palette)
var zoneColors = [
  "#e41a1c","#377eb8","#4daf4a","#984ea3","#ff7f00",
  "#a65628","#f781bf","#999999","#66c2a5","#fc8d62","#8da0cb"
];

// === Layer factories ===
var layers = {{}};

// Schools
layers.schools = L.geoJSON(SCHOOLS, {{
  pointToLayer: function(f, ll) {{
    var isNS = f.properties.school === NORTHSIDE.school;
    return L.circleMarker(ll, {{
      radius: isNS ? 8 : 6,
      fillColor: isNS ? "#e41a1c" : "#2196F3",
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

// Attendance zones (colored fill)
layers.zones = L.geoJSON(ZONES, {{
  style: function(f) {{
    var idx = 0;
    ZONES.features.forEach(function(feat, i) {{
      if (feat.properties.school === f.properties.school) idx = i;
    }});
    return {{
      color: zoneColors[idx % zoneColors.length],
      weight: 2,
      fillColor: zoneColors[idx % zoneColors.length],
      fillOpacity: 0.15,
    }};
  }},
  onEachFeature: function(f, layer) {{
    layer.bindTooltip(f.properties.school);
  }}
}});

// Attendance zones (outline only, faint)
layers.zonesFaint = L.geoJSON(ZONES, {{
  style: {{ color: "#999", weight: 1, fillOpacity: 0, dashArray: "4 3" }}
}});

// Northside zone (red highlight)
var northsideZoneData = {{
  type: "FeatureCollection",
  features: ZONES.features.filter(function(f) {{
    return f.properties.school === NORTHSIDE.school;
  }})
}};
layers.northsideZone = L.geoJSON(northsideZoneData, {{
  style: {{ color: "#e41a1c", weight: 3, fillOpacity: 0.05, fillColor: "#e41a1c" }}
}});

// Northside zone (faint)
layers.northsideZoneFaint = L.geoJSON(northsideZoneData, {{
  style: {{ color: "#e41a1c", weight: 1, fillOpacity: 0.02, dashArray: "4 3" }}
}});

// Block groups (outline)
layers.blockGroups = L.geoJSON(BG, {{
  style: {{ color: "#2196F3", weight: 1.5, fillOpacity: 0 }}
}});

// Block groups colored by poverty
layers.bgPoverty = L.geoJSON(BG, {{
  style: function(f) {{
    var pov = f.properties.pct_below_185_poverty || 0;
    var intensity = Math.min(pov / 40, 1);
    var r = Math.round(255);
    var g = Math.round(255 - intensity * 200);
    var b = Math.round(255 - intensity * 200);
    return {{
      color: "#666",
      weight: 1,
      fillColor: "rgb(" + r + "," + g + "," + b + ")",
      fillOpacity: 0.6,
    }};
  }},
  onEachFeature: function(f, layer) {{
    var p = f.properties;
    layer.bindTooltip(
      "GEOID: " + (p.GEOID || "?") + "<br>" +
      "Pop: " + (p.total_pop || 0) + "<br>" +
      "Poverty: " + (p.pct_below_185_poverty || 0).toFixed(1) + "%"
    );
  }}
}});

// Block groups colored (for mismatch step)
layers.bgColored = L.geoJSON(BG, {{
  style: function(f, i) {{
    var colors = ["#a6cee3","#b2df8a","#fdbf6f","#cab2d6","#fb9a99",
                  "#ffff99","#e5d8bd","#b3cde3","#decbe4","#fed9a6"];
    var idx = BG.features.indexOf(f) % colors.length;
    return {{
      color: "#666",
      weight: 1.5,
      fillColor: colors[idx],
      fillOpacity: 0.35,
    }};
  }}
}});

// Census blocks (outline)
layers.blocks = L.geoJSON(BLOCKS, {{
  style: {{ color: "#ff9800", weight: 1, dashArray: "3 3", fillOpacity: 0 }}
}});

// Blocks colored by poverty (downscaled)
layers.blocksPoverty = L.geoJSON(BLOCKS, {{
  style: function(f) {{
    var pov = f.properties.pct_below_185_poverty || 0;
    var intensity = Math.min(pov / 40, 1);
    var r = Math.round(255);
    var g = Math.round(255 - intensity * 200);
    var b = Math.round(255 - intensity * 200);
    return {{
      color: "#666",
      weight: 0.5,
      fillColor: "rgb(" + r + "," + g + "," + b + ")",
      fillOpacity: 0.6,
    }};
  }},
  onEachFeature: function(f, layer) {{
    var p = f.properties;
    layer.bindTooltip(
      "Block: " + (p.GEOID20 || "?") + "<br>" +
      "Pop: " + (p.total_pop || 0) + "<br>" +
      "Poverty: " + (p.pct_below_185_poverty || 0).toFixed(1) + "%"
    );
  }}
}});

// Parcels (deferred — 10K+ polygons)
var parcelsLoaded = false;
function ensureParcelsLoaded() {{
  if (parcelsLoaded) return;
  parcelsLoaded = true;
  L.geoJSON(PARCELS, {{
    style: {{ color: "#27ae60", weight: 1, fillColor: "#27ae60", fillOpacity: 0.25 }}
  }}).addTo(layers.parcels);
  L.geoJSON(PARCELS, {{
    style: {{ color: "#27ae60", weight: 0.5, fillColor: "#27ae60", fillOpacity: 0.08 }}
  }}).addTo(layers.parcelsFaint);
}}
layers.parcels = L.layerGroup();
layers.parcelsFaint = L.layerGroup();

// Fragments - area weighted
layers.fragmentsArea = L.geoJSON(FRAGMENTS_AREA, {{
  style: function(f) {{
    var w = f.properties.weight_area || 0;
    var intensity = Math.min(w, 1);
    return {{
      color: "#333",
      weight: 1.5,
      fillColor: "rgb(" + Math.round(255 - intensity * 200) + "," +
                 Math.round(200 - intensity * 100) + "," +
                 Math.round(100 + intensity * 50) + ")",
      fillOpacity: 0.5,
    }};
  }},
  onEachFeature: function(f, layer) {{
    layer.bindTooltip("Area weight: " + (f.properties.weight_area || 0).toFixed(3));
  }}
}});

// Fragments - dasymetric weighted
layers.fragmentsDasy = L.geoJSON(FRAGMENTS_DASY, {{
  style: function(f) {{
    var w = f.properties.weight_dasy || 0;
    var intensity = Math.min(w, 1);
    return {{
      color: "#333",
      weight: 1.5,
      fillColor: "rgb(" + Math.round(50 + intensity * 100) + "," +
                 Math.round(100 + intensity * 80) + "," +
                 Math.round(200 - intensity * 50) + ")",
      fillOpacity: 0.5,
    }};
  }},
  onEachFeature: function(f, layer) {{
    var p = f.properties;
    layer.bindTooltip(
      "Dasy weight: " + (p.weight_dasy || 0).toFixed(3) + "<br>" +
      "Area weight: " + (p.weight_area || 0).toFixed(3)
    );
  }}
}});

// Dots (canvas rendered — deferred to avoid blocking page load)
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

// Zones choropleth (poverty)
layers.zonesPoverty = (function() {{
  // Build lookup from ZONE_STATS
  var statsLookup = {{}};
  if (Array.isArray(ZONE_STATS)) {{
    ZONE_STATS.forEach(function(s) {{ statsLookup[s.school] = s; }});
  }}
  return L.geoJSON(ZONES, {{
    style: function(f) {{
      var s = statsLookup[f.properties.school];
      var pov = s ? (s.pct_below_185_poverty || 0) : 0;
      var intensity = Math.min(pov / 30, 1);
      var r = Math.round(255);
      var g = Math.round(255 - intensity * 200);
      var b = Math.round(255 - intensity * 200);
      return {{
        color: "#333",
        weight: 2,
        fillColor: "rgb(" + r + "," + g + "," + b + ")",
        fillOpacity: 0.45,
      }};
    }},
    onEachFeature: function(f, layer) {{
      var s = statsLookup[f.properties.school];
      if (s) {{
        layer.bindTooltip(
          "<b>" + f.properties.school + "</b><br>" +
          "Pop: " + (s.total_pop || "?") + "<br>" +
          "Poverty: " + (s.pct_below_185_poverty || 0).toFixed(1) + "%<br>" +
          "Minority: " + (s.pct_minority || 0).toFixed(1) + "%<br>" +
          "Income: $" + (s.median_hh_income || 0).toLocaleString()
        );
      }} else {{
        layer.bindTooltip(f.properties.school);
      }}
    }}
  }});
}})();

// Walk zones
layers.walkZones = L.geoJSON(WALK_ZONES, {{
  style: {{
    color: "#27ae60",
    weight: 2,
    dashArray: "6 4",
    fillColor: "#27ae60",
    fillOpacity: 0.05,
  }},
  onEachFeature: function(f, layer) {{
    layer.bindTooltip(f.properties.school + " (walk zone)");
  }}
}});

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
      "AMI: " + (p.AMIServed || "Unknown")
    );
  }}
}});

// MLS home sales
if (MLS_SALES && MLS_SALES.features && MLS_SALES.features.length > 0) {{
  // Compute quartiles
  var prices = MLS_SALES.features.map(function(f) {{ return f.properties.close_price || 0; }}).sort(function(a,b){{ return a-b; }});
  var q25 = prices[Math.floor(prices.length * 0.25)];
  var q50 = prices[Math.floor(prices.length * 0.50)];
  var q75 = prices[Math.floor(prices.length * 0.75)];

  layers.mlsSales = L.geoJSON(MLS_SALES, {{
    pointToLayer: function(f, ll) {{
      var price = f.properties.close_price || 0;
      var color = "#b2182b";
      if (price <= q25) color = "#2166ac";
      else if (price <= q50) color = "#67a9cf";
      else if (price <= q75) color = "#fc8d59";
      return L.circleMarker(ll, {{
        radius: 4,
        fillColor: color,
        color: "#333",
        weight: 1,
        fillOpacity: 0.7,
      }});
    }},
    onEachFeature: function(f, layer) {{
      var p = f.properties;
      layer.bindTooltip(
        (p.address || "Sale") + "<br>" +
        "$" + (p.close_price || 0).toLocaleString()
      );
    }}
  }});
}} else {{
  layers.mlsSales = null;
}}

// Planned developments — color by unit count (blue→yellow→red)
var DEV_MAX_UNITS = 1;
if (PLANNED_DEV && PLANNED_DEV.features) {{
  PLANNED_DEV.features.forEach(function(f) {{
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
if (PLANNED_DEV && PLANNED_DEV.features && PLANNED_DEV.features.length > 0) {{
  layers.plannedDev = L.geoJSON(PLANNED_DEV, {{
    pointToLayer: function(f, ll) {{
      var units = f.properties.expected_units || 0;
      var color = devColor(units);
      return L.circleMarker(ll, {{
        radius: 10,
        fillColor: color,
        color: '#555',
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
}} else {{
  layers.plannedDev = null;
}}

// SAPFOTAC developments — same devColor function, keyed by total_units_remaining
var SAP_MAX_UNITS = 1;
if (SAPFOTAC_DEV && SAPFOTAC_DEV.features) {{
  SAPFOTAC_DEV.features.forEach(function(f) {{
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
if (SAPFOTAC_DEV && SAPFOTAC_DEV.features && SAPFOTAC_DEV.features.length > 0) {{
  layers.sapfotacDev = L.geoJSON(SAPFOTAC_DEV, {{
    pointToLayer: function(f, ll) {{
      var units = f.properties.total_units_remaining || 0;
      var color = sapColor(units);
      return L.circleMarker(ll, {{
        radius: 10,
        fillColor: color,
        color: '#555',
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
        "Students — Elem: " + elem + ", Mid: " + mid + ", High: " + high
      );
    }}
  }});
}} else {{
  layers.sapfotacDev = null;
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
    case 0: // Intro
      layers.district.addTo(map);
      layers.schools.addTo(map);
      districtView();
      break;

    case 1: // Schools & Zones
      layers.district.addTo(map);
      layers.zones.addTo(map);
      layers.schools.addTo(map);
      districtView();
      break;

    case 2: // Block Groups
      layers.district.addTo(map);
      layers.blockGroups.addTo(map);
      layers.zonesFaint.addTo(map);
      layers.schools.addTo(map);
      map.setView(ns, 13);
      break;

    case 3: // Mismatch
      layers.bgColored.addTo(map);
      layers.northsideZone.addTo(map);
      layers.schools.addTo(map);
      map.setView(ns, 13);
      break;

    case 4: // Area weighting
      layers.fragmentsArea.addTo(map);
      layers.northsideZone.addTo(map);
      layers.schools.addTo(map);
      map.setView(ns, 14);
      break;

    case 5: // Parcels
      ensureParcelsLoaded();
      layers.parcels.addTo(map);
      layers.blockGroups.addTo(map);
      layers.schools.addTo(map);
      map.setView(ns, 15);
      break;

    case 6: // Dasymetric
      ensureParcelsLoaded();
      layers.fragmentsDasy.addTo(map);
      layers.parcelsFaint.addTo(map);
      layers.schools.addTo(map);
      map.setView(ns, 14);
      break;

    case 7: // Derived metrics (poverty choropleth)
      layers.bgPoverty.addTo(map);
      layers.northsideZoneFaint.addTo(map);
      layers.schools.addTo(map);
      map.setView(ns, 13);
      break;

    case 8: // Why recompute (same view as 7)
      layers.bgPoverty.addTo(map);
      layers.northsideZoneFaint.addTo(map);
      layers.schools.addTo(map);
      map.setView(ns, 13);
      break;

    case 9: // Blocks
      layers.blocks.addTo(map);
      layers.blockGroups.addTo(map);
      layers.schools.addTo(map);
      map.setView(ns, 15);
      break;

    case 10: // Block choropleth
      layers.blocksPoverty.addTo(map);
      layers.northsideZoneFaint.addTo(map);
      layers.schools.addTo(map);
      map.setView(ns, 14);
      break;

    case 11: // Dots
      ensureDotsLoaded();
      layers.dots.addTo(map);
      layers.northsideZoneFaint.addTo(map);
      layers.schools.addTo(map);
      map.setView(ns, 14);
      break;

    case 12: // Dots + parcels
      ensureDotsLoaded();
      ensureParcelsLoaded();
      layers.dots.addTo(map);
      layers.parcels.addTo(map);
      layers.blocks.addTo(map);
      layers.schools.addTo(map);
      map.setView(ns, 16);
      break;

    case 13: // Zone aggregation
      layers.zonesPoverty.addTo(map);
      layers.schools.addTo(map);
      districtView();
      break;

    case 14: // MLS home sales
      if (layers.mlsSales) layers.mlsSales.addTo(map);
      layers.zones.addTo(map);
      layers.schools.addTo(map);
      districtView();
      break;

    case 15: // Affordable housing
      layers.affordableHousing.addTo(map);
      layers.zones.addTo(map);
      layers.schools.addTo(map);
      districtView();
      break;

    case 16: // MLS home sales
      if (layers.mlsSales) layers.mlsSales.addTo(map);
      layers.zones.addTo(map);
      layers.schools.addTo(map);
      districtView();
      break;

    case 17: // Planned developments (CH Active Dev)
      if (layers.plannedDev) layers.plannedDev.addTo(map);
      layers.zones.addTo(map);
      layers.schools.addTo(map);
      districtView();
      break;

    case 18: // Planned developments (SAPFOTAC)
      if (layers.sapfotacDev) layers.sapfotacDev.addTo(map);
      layers.zones.addTo(map);
      layers.schools.addTo(map);
      districtView();
      break;

    case 19: // Complete map — bar chart comparison
      document.getElementById("chart-panel").style.display = "block";
      break;

    case 20: // Limitations
      ensureDotsLoaded();
      layers.dots.addTo(map);
      layers.zones.addTo(map);
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
</body>
</html>"""

    return html


# ---------------------------------------------------------------------------
# Main orchestrator
# ---------------------------------------------------------------------------
def main():
    parser = argparse.ArgumentParser(
        description="Generate socioeconomic methodology scrollytelling page"
    )
    parser.add_argument("--cache-only", action="store_true",
                        help="Only use cached data (default behavior)")
    parser.parse_args()

    print("=" * 60)
    print("Socioeconomic Methodology Scrollytelling Page Generator")
    print("=" * 60)

    # Step 1: Load schools
    print("\n[1/13] Loading school locations ...")
    schools = load_schools()
    northside = get_northside(schools)
    bbox = get_bbox(northside)

    # Convert schools to GeoDataFrame for GeoJSON
    schools_gdf = gpd.GeoDataFrame(
        schools,
        geometry=gpd.points_from_xy(schools.lon, schools.lat),
        crs=CRS_WGS84,
    )
    schools_json = gdf_to_geojson_str(schools_gdf, properties=["school"])
    _progress(f"Loaded {len(schools)} schools, focus: {NORTHSIDE_NAME}")

    # Step 2: District boundary
    print("[2/13] Loading district boundary ...")
    district = load_district_boundary()
    district_json = gdf_to_geojson_str(district, simplify_m=50)

    # Step 3: Attendance zones
    print("[3/13] Loading attendance zones ...")
    zones = load_attendance_zones()
    zones_json = gdf_to_geojson_str(zones, properties=["school"], simplify_m=20)
    _progress(f"Loaded {len(zones)} zones")

    # Step 4: Block groups
    print("[4/13] Loading ACS block groups ...")
    bg = load_block_groups()
    # Clip to district
    bg_clipped = gpd.clip(bg, district.to_crs(bg.crs))
    # Filter to polygons only
    bg_clipped = bg_clipped[
        bg_clipped.geometry.geom_type.isin(["Polygon", "MultiPolygon"])
    ].copy()
    bg_json = gdf_to_geojson_str(
        bg_clipped,
        properties=["GEOID", "total_pop", "pct_below_185_poverty",
                     "pct_minority", "pct_renter", "median_hh_income"],
        simplify_m=20,
    )
    _progress(f"Loaded {len(bg_clipped)} block groups")

    # Step 5: Census blocks
    print("[5/13] Loading Decennial Census blocks ...")
    blocks = load_blocks(bbox)
    # Downscale BG metrics to blocks for choropleth
    if "pct_below_185_poverty" not in blocks.columns:
        # Simple parent-BG lookup for poverty metric
        blocks["parent_bg"] = blocks["GEOID20"].str[:12]
        bg_pov = bg_clipped[["GEOID", "pct_below_185_poverty"]].copy()
        bg_pov = bg_pov.rename(columns={"GEOID": "parent_bg"})
        blocks = blocks.merge(bg_pov, on="parent_bg", how="left")
        blocks["pct_below_185_poverty"] = blocks["pct_below_185_poverty"].fillna(0)
    blocks_json = gdf_to_geojson_str(
        blocks,
        properties=["GEOID20", "total_pop", "pct_below_185_poverty"],
        simplify_m=5,
    )
    _progress(f"Loaded {len(blocks)} blocks in Northside area")

    # Step 6: Residential parcels (use tight bbox to limit to ~2-4K parcels)
    print("[6/13] Loading residential parcels ...")
    parcels = load_residential_parcels(bbox)
    parcels_json = gdf_to_geojson_str(parcels, simplify_m=5)
    _progress(f"Loaded {len(parcels)} residential parcels")

    # Step 7: Compute fragments
    print("[7/13] Computing zone-BG fragments for Northside ...")
    northside_zone = zones[zones["school"] == NORTHSIDE_NAME].copy()
    if len(northside_zone) > 0:
        # Get block groups that intersect Northside zone
        ns_zone_buffered = northside_zone.to_crs(CRS_UTM17N).buffer(500)
        ns_zone_buffered = gpd.GeoDataFrame(
            geometry=ns_zone_buffered, crs=CRS_UTM17N
        ).to_crs(bg_clipped.crs)
        bg_ns = gpd.sjoin(bg_clipped, ns_zone_buffered, how="inner",
                          predicate="intersects")
        # Drop duplicate index column from sjoin
        bg_ns = bg_ns.drop(columns=["index_right"], errors="ignore")
        fragments_area_json, fragments_dasy_json = compute_fragments(
            northside_zone, bg_ns, parcels
        )
        _progress("Computed area and dasymetric fragment weights")
    else:
        fragments_area_json = '{"type":"FeatureCollection","features":[]}'
        fragments_dasy_json = fragments_area_json
        _progress("WARNING: Northside zone not found in shapefile")

    # Step 8: Generate dots (district-wide)
    print("[8/13] Generating dot-density data (district-wide) ...")
    all_blocks = load_blocks()  # full cache, no bbox
    all_blocks = gpd.sjoin(
        all_blocks, district.to_crs(all_blocks.crs),
        how="inner", predicate="intersects",
    ).drop(columns=["index_right"], errors="ignore")
    all_parcels = load_residential_parcels()  # full set, no bbox
    dot_data = generate_dots(all_blocks, all_parcels)

    # Step 9: Walk zones
    print("[9/13] Loading walk zones ...")
    walk_zones = load_walk_zones()
    walk_zones_json = gdf_to_geojson_str(
        walk_zones, properties=["school"], simplify_m=20
    )
    _progress(f"Loaded {len(walk_zones)} walk zones")

    # Step 10: Affordable housing
    print("[10/13] Loading affordable housing ...")
    ah = load_affordable_housing()
    ah_json = gdf_to_geojson_str(
        ah,
        properties=["ProjectName", "AMIServed", "UnitType"],
    ) if len(ah) > 0 else '{"type":"FeatureCollection","features":[]}'
    _progress(f"Loaded {len(ah)} affordable housing records")

    # Step 10b: MLS home sales
    print("[10b/13] Loading MLS home sales ...")
    mls_json = '{"type":"FeatureCollection","features":[]}'
    mls_cache = DATA_CACHE / "mls_home_sales.gpkg"
    if mls_cache.exists():
        mls_gdf = gpd.read_file(mls_cache)
        mls_json = gdf_to_geojson_str(
            mls_gdf,
            properties=["address", "close_price", "price_per_sqft"],
        )
        _progress(f"Loaded {len(mls_gdf)} MLS home sales")
    else:
        _progress("MLS cache not found, skipping")

    # Step 10c: Planned developments
    print("[10c/14] Loading planned developments ...")
    dev_json = '{"type":"FeatureCollection","features":[]}'
    if DEV_CACHE.exists():
        dev_gdf = gpd.read_file(DEV_CACHE)
        dev_json = gdf_to_geojson_str(
            dev_gdf,
            properties=["name", "address", "expected_units"],
        )
        _progress(f"Loaded {len(dev_gdf)} planned developments")
    else:
        _progress("Planned developments cache not found, skipping")

    # Step 10d: SAPFOTAC planned developments
    print("[10d/14] Loading SAPFOTAC planned developments ...")
    sapfotac_json = '{"type":"FeatureCollection","features":[]}'
    if SAPFOTAC_CSV.exists():
        sap_df = pd.read_csv(SAPFOTAC_CSV)
        sap_df = sap_df.dropna(subset=["lat", "lon"])
        sap_gdf = gpd.GeoDataFrame(
            sap_df,
            geometry=gpd.points_from_xy(sap_df["lon"], sap_df["lat"]),
            crs=CRS_WGS84,
        )
        sapfotac_json = gdf_to_geojson_str(
            sap_gdf,
            properties=["project", "address", "total_units_remaining",
                         "students_elementary", "students_middle", "students_high"],
        )
        _progress(f"Loaded {len(sap_gdf)} SAPFOTAC planned developments")
    else:
        _progress("SAPFOTAC CSV not found, skipping")

    # Step 11: Zone demographics
    print("[11/14] Loading zone demographics ...")
    zone_demo = load_zone_demographics()
    stat_cols = ["school", "total_pop", "median_hh_income",
                 "pct_below_185_poverty", "pct_minority", "pct_renter",
                 "pct_zero_vehicle", "pct_elementary_age",
                 "ah_total_units", "mls_total_sales", "mls_median_price",
                 "mls_median_ppsf"]
    if len(zone_demo) > 0:
        avail_cols = [c for c in stat_cols if c in zone_demo.columns]
        zone_stats = zone_demo[avail_cols].to_dict("records")
        # Convert numpy types
        for rec in zone_stats:
            for k, v in rec.items():
                if isinstance(v, (np.integer, np.floating)):
                    rec[k] = float(v)
                elif pd.isna(v):
                    rec[k] = 0
        zone_stats_json = json.dumps(zone_stats, separators=(",", ":"))
    else:
        zone_stats_json = "[]"

    # Step 12: Nearest-drive zone demographics
    print("[12/13] Loading nearest-drive zone demographics ...")
    drive_stats_json = "[]"
    try:
        dot_zone_csv = Path(OUTPUT_DOT_ZONE_CSV)
        if dot_zone_csv.exists():
            _all = pd.read_csv(dot_zone_csv)
            drive_demo = _all[_all["zone_type"] == "Nearest Drive"].copy()
            drive_demo = drive_demo.drop(columns=["zone_type"], errors="ignore")
            _progress(f"Loaded {len(drive_demo)} drive zone rows from {dot_zone_csv.name}")
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
                _progress(f"Computed drive demographics for {len(drive_demo)} zones")
            else:
                _progress("WARNING: dot-zone demographics CSV has no Nearest Drive rows")
        else:
            _progress(f"WARNING: {dot_zone_csv} not found — run school_socioeconomic_analysis.py first")
    except Exception as e:
        _progress(f"WARNING: Could not load drive demographics: {e}")
        drive_stats_json = "[]"

    # Step 13: Build HTML
    print("[13/13] Building HTML ...")
    data = {
        "schools_json": schools_json,
        "district_json": district_json,
        "zones_json": zones_json,
        "bg_json": bg_json,
        "blocks_json": blocks_json,
        "parcels_json": parcels_json,
        "fragments_area_json": fragments_area_json,
        "fragments_dasy_json": fragments_dasy_json,
        "dot_data": dot_data,
        "ah_json": ah_json,
        "mls_json": mls_json,
        "dev_json": dev_json,
        "sapfotac_json": sapfotac_json,
        "walk_zones_json": walk_zones_json,
        "zone_stats": zone_stats_json,
        "drive_stats": drive_stats_json,
        "northside": northside,
    }

    html = build_html(data)

    ASSETS_MAPS.mkdir(parents=True, exist_ok=True)
    OUTPUT_HTML.write_text(html, encoding="utf-8")

    size_mb = OUTPUT_HTML.stat().st_size / (1024 * 1024)
    print(f"\nOutput: {OUTPUT_HTML}")
    print(f"Size: {size_mb:.1f} MB")
    print("Done!")


if __name__ == "__main__":
    main()
