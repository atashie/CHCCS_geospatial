"""
School Socioeconomic Analysis — Census demographics by attendance zone.

Downloads ACS 5-Year (block group) and 2020 Decennial (block) Census data,
overlays CHCCS attendance zone boundaries, and produces:
  - Per-school-zone demographic profiles (income, poverty, race, vehicles, etc.)
  - Interactive Folium map with choropleth + dot-density layers
  - Static comparison charts
  - Methodology documentation

Usage:
    python src/school_socioeconomic_analysis.py
    python src/school_socioeconomic_analysis.py --cache-only
    python src/school_socioeconomic_analysis.py --skip-dots --skip-maps

Output:
    data/processed/census_school_demographics.csv
    data/processed/census_blockgroup_profiles.csv
    assets/maps/school_socioeconomic_map.html
    assets/charts/socioeconomic_*.png
    docs/socioeconomic/SOCIOECONOMIC_ANALYSIS.md
"""

from __future__ import annotations

import argparse
import os
import sys
import tempfile
import warnings
import zipfile
from pathlib import Path

import folium
import geopandas as gpd
import matplotlib
import matplotlib.pyplot as plt
import numpy as np
import pandas as pd
import requests
from shapely.geometry import Point, box

warnings.filterwarnings("ignore", category=FutureWarning)
matplotlib.use("Agg")

# ---------------------------------------------------------------------------
# Paths
# ---------------------------------------------------------------------------
PROJECT_ROOT = Path(__file__).resolve().parent.parent
DATA_RAW = PROJECT_ROOT / "data" / "raw"
DATA_PROCESSED = PROJECT_ROOT / "data" / "processed"
DATA_CACHE = PROJECT_ROOT / "data" / "cache"
ASSETS_CHARTS = PROJECT_ROOT / "assets" / "charts"
ASSETS_MAPS = PROJECT_ROOT / "assets" / "maps"

SCHOOL_CSV = DATA_CACHE / "nces_school_locations.csv"
DISTRICT_CACHE = DATA_CACHE / "chccs_district_boundary.gpkg"
CHCCS_SHP = DATA_RAW / "properties" / "CHCCS" / "CHCCS.shp"
PARCEL_POLYS = DATA_RAW / "properties" / "combined_data_polys.gpkg"

ACS_CACHE = DATA_CACHE / "census_acs_blockgroups.gpkg"
DECENNIAL_CACHE = DATA_CACHE / "census_decennial_blocks.gpkg"
TIGER_BG_CACHE = DATA_CACHE / "tiger_bg_37.zip"
TIGER_BLOCK_CACHE = DATA_CACHE / "tiger_blocks_37135.zip"

OUTPUT_MAP = ASSETS_MAPS / "school_socioeconomic_map.html"
OUTPUT_SCHOOL_CSV = DATA_PROCESSED / "census_school_demographics.csv"
OUTPUT_BG_CSV = DATA_PROCESSED / "census_blockgroup_profiles.csv"
OUTPUT_DOC = PROJECT_ROOT / "docs" / "socioeconomic" / "SOCIOECONOMIC_ANALYSIS.md"

# ---------------------------------------------------------------------------
# Constants
# ---------------------------------------------------------------------------
CRS_WGS84 = "EPSG:4326"
CRS_UTM17N = "EPSG:32617"

CHAPEL_HILL_CENTER = [35.9132, -79.0558]

# Isochrone zone definitions: (mode, threshold_seconds, display_label)
ISOCHRONE_DEFS = [
    ("drive", 300, "Within 5-min Drive"),
    ("bike", 600, "Within 10-min Bike"),
    ("walk", 900, "Within 15-min Walk"),
]

# Orange County, NC FIPS (37 = NC, 135 = Orange County)
# Note: 063 is Durham County — a common mistake
STATE_FIPS = "37"
COUNTY_FIPS = "135"

# Census API base URLs
ACS_BASE_URL = "https://api.census.gov/data/2022/acs/acs5"
DECENNIAL_BASE_URL = "https://api.census.gov/data/2020/dec/pl"

# TIGER/Line geometry URLs
TIGER_BG_URL = "https://www2.census.gov/geo/tiger/TIGER2023/BG/tl_2023_37_bg.zip"
TIGER_BLOCK_URL = (
    "https://www2.census.gov/geo/tiger/TIGER2020PL/STATE/"
    "37_NORTH_CAROLINA/37135/tl_2020_37135_tabblock20.zip"
)

# ACS 5-Year variables to fetch (block group level)
_ACS_VARIABLES = {
    # Total population
    "B01001_001E": "total_pop",
    # Age groups: young children (0-4) and elementary-age (5-9)
    "B01001_003E": "male_under_5",
    "B01001_027E": "female_under_5",
    "B01001_004E": "male_5_9",
    "B01001_028E": "female_5_9",
    # Race/ethnicity (Hispanic origin by race)
    "B03002_001E": "race_total",
    "B03002_003E": "white_nh",
    "B03002_004E": "black_nh",
    "B03002_005E": "aian_nh",
    "B03002_006E": "asian_nh",
    "B03002_007E": "nhpi_nh",
    "B03002_008E": "other_nh",
    "B03002_009E": "two_plus_nh",
    "B03002_012E": "hispanic",
    # Median household income
    "B19013_001E": "median_hh_income",
    # Income brackets (for distribution)
    "B19001_001E": "income_total",
    "B19001_002E": "income_lt_10k",
    "B19001_003E": "income_10k_15k",
    "B19001_004E": "income_15k_20k",
    "B19001_005E": "income_20k_25k",
    "B19001_006E": "income_25k_30k",
    "B19001_007E": "income_30k_35k",
    "B19001_008E": "income_35k_40k",
    "B19001_009E": "income_40k_45k",
    "B19001_010E": "income_45k_50k",
    "B19001_011E": "income_50k_60k",
    "B19001_012E": "income_60k_75k",
    "B19001_013E": "income_75k_100k",
    "B19001_014E": "income_100k_125k",
    "B19001_015E": "income_125k_150k",
    "B19001_016E": "income_150k_200k",
    "B19001_017E": "income_200k_plus",
    # Poverty ratio (C17002)
    "C17002_001E": "poverty_universe",
    "C17002_002E": "poverty_lt_050",
    "C17002_003E": "poverty_050_099",
    "C17002_004E": "poverty_100_124",
    "C17002_005E": "poverty_125_149",
    "C17002_006E": "poverty_150_184",
    # Tenure (owner vs renter)
    "B25003_001E": "tenure_total",
    "B25003_002E": "tenure_owner",
    "B25003_003E": "tenure_renter",
    # Vehicles available by tenure (B25044) — B08201 not available at BG level
    "B25044_001E": "vehicles_total_hh",
    "B25044_003E": "vehicles_zero_owner",
    "B25044_010E": "vehicles_zero_renter",
    # Family type by children (B11003)
    "B11003_001E": "family_total",
    "B11003_003E": "married_with_kids",
    "B11003_010E": "male_hholder_with_kids",
    "B11003_016E": "female_hholder_with_kids",
}

# Decennial P.L. 94-171 variables (block level — race only)
_DECENNIAL_VARIABLES = {
    "P1_001N": "total_pop",
    "P1_003N": "white_alone",
    "P1_004N": "black_alone",
    "P1_005N": "aian_alone",
    "P1_006N": "asian_alone",
    "P1_007N": "nhpi_alone",
    "P1_008N": "other_alone",
    "P1_009N": "two_plus",
    "P2_002N": "hispanic_total",
    "P2_005N": "white_nh",  # White alone, not Hispanic/Latino — total pop (P4_005N was 18+ only)
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

# Chart styling
BAR_COLOR = "#2196F3"
NEUTRAL_COLOR = "#C0C0C0"

plt.rcParams["font.family"] = "sans-serif"
plt.rcParams["font.sans-serif"] = ["Segoe UI", "Tahoma", "DejaVu Sans"]
plt.style.use("seaborn-v0_8-whitegrid")

# ENAME → project school name mapping
# CHCCS.shp ENAME values use full names (e.g., "Carrboro Elementary")
# This map handles both full names and possible abbreviations.
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


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _progress(msg: str):
    print(f"  ... {msg}")


def ensure_directories():
    """Create output directories if needed."""
    for d in [DATA_CACHE, DATA_PROCESSED, ASSETS_CHARTS, ASSETS_MAPS,
              OUTPUT_DOC.parent]:
        d.mkdir(parents=True, exist_ok=True)


def _get_census_api_key() -> str | None:
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


def _census_get(base_url: str, get_vars: list[str], for_geo: str,
                in_geo: str | None = None) -> pd.DataFrame:
    """Make a Census API request and return a DataFrame.

    Parameters
    ----------
    base_url : str  — e.g. ACS_BASE_URL
    get_vars : list — variable names to fetch
    for_geo  : str  — e.g. "block group:*"
    in_geo   : str  — e.g. "state:37+county:063"
    """
    # Census API has a 50-variable limit per request; chunk if needed
    chunk_size = 48  # leave room for NAME
    all_chunks = []
    key = _get_census_api_key()
    if not key:
        _progress("NOTE: No CENSUS_API_KEY found. Using unauthenticated API access (500 req/day limit).")

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
            raise RuntimeError(f"Census API returned no data rows for {for_geo}")

        header = data[0]
        rows = data[1:]
        df = pd.DataFrame(rows, columns=header)

        # Convert numeric columns (Census returns strings)
        for col in chunk:
            if col in df.columns:
                df[col] = pd.to_numeric(df[col], errors="coerce")

        all_chunks.append(df)

    if len(all_chunks) == 1:
        return all_chunks[0]

    # Merge chunks on geography columns
    result = all_chunks[0]
    geo_cols = [c for c in result.columns
                if c in ("state", "county", "tract", "block group", "block", "NAME")]
    for chunk_df in all_chunks[1:]:
        new_cols = [c for c in chunk_df.columns if c not in result.columns]
        result = result.merge(chunk_df[geo_cols + new_cols], on=geo_cols, how="left")
    return result


# ═══════════════════════════════════════════════════════════════════════════
# Section 2: Census API data fetching
# ═══════════════════════════════════════════════════════════════════════════

def fetch_acs_blockgroup_data(cache_only: bool = False) -> gpd.GeoDataFrame:
    """Fetch ACS 5-Year block group data for Orange County and join with TIGER geometry.

    Returns a GeoDataFrame with all ACS variables and block group polygons.
    Cached to data/cache/census_acs_blockgroups.gpkg.
    """
    if ACS_CACHE.exists():
        _progress(f"Loading cached ACS block group data from {ACS_CACHE}")
        return gpd.read_file(ACS_CACHE)

    if cache_only:
        raise FileNotFoundError(
            f"ACS cache not found at {ACS_CACHE}. Run without --cache-only."
        )

    # Download TIGER block group geometries first
    bg_gdf = download_tiger_blockgroups()

    # Fetch ACS data
    _progress("Fetching ACS 5-Year block group data from Census API ...")
    acs_vars = list(_ACS_VARIABLES.keys())
    df = _census_get(
        ACS_BASE_URL, acs_vars,
        for_geo="block group:*",
        in_geo=f"state:{STATE_FIPS}+county:{COUNTY_FIPS}",
    )

    # Rename variables to friendly names
    df = df.rename(columns=_ACS_VARIABLES)

    # Build GEOID for join: state + county + tract + block group
    df["GEOID"] = df["state"] + df["county"] + df["tract"] + df["block group"]

    _progress(f"  Fetched {len(df)} block groups from ACS")

    # Join with geometry
    merged = bg_gdf.merge(df, on="GEOID", how="inner")
    _progress(f"  Joined {len(merged)} block groups with geometry")

    # Cache
    merged.to_file(ACS_CACHE, driver="GPKG")
    _progress(f"  Cached to {ACS_CACHE}")
    return merged


def fetch_decennial_block_data(cache_only: bool = False) -> gpd.GeoDataFrame:
    """Fetch 2020 Decennial P.L. 94-171 block-level race data for Orange County.

    Returns a GeoDataFrame with race variables and block polygons.
    Cached to data/cache/census_decennial_blocks.gpkg.
    """
    if DECENNIAL_CACHE.exists():
        _progress(f"Loading cached Decennial block data from {DECENNIAL_CACHE}")
        return gpd.read_file(DECENNIAL_CACHE)

    if cache_only:
        raise FileNotFoundError(
            f"Decennial cache not found at {DECENNIAL_CACHE}. Run without --cache-only."
        )

    # Download TIGER block geometries first
    block_gdf = download_tiger_blocks()

    # Fetch Decennial data
    _progress("Fetching 2020 Decennial block data from Census API ...")
    dec_vars = list(_DECENNIAL_VARIABLES.keys())
    df = _census_get(
        DECENNIAL_BASE_URL, dec_vars,
        for_geo="block:*",
        in_geo=f"state:{STATE_FIPS}+county:{COUNTY_FIPS}",
    )

    # Rename variables
    df = df.rename(columns=_DECENNIAL_VARIABLES)

    # Build GEOID: state + county + tract + block
    df["GEOID20"] = df["state"] + df["county"] + df["tract"] + df["block"]

    # Compute "other_race" from P1 variables directly (AIAN + NHPI + Some Other Race)
    # Avoids double-counting: P1 (race alone) and P2 (Hispanic origin) overlap,
    # so subtracting both from total_pop would undercount.
    for col in ["total_pop", "white_alone", "black_alone", "asian_alone",
                "hispanic_total", "two_plus", "aian_alone", "nhpi_alone",
                "other_alone", "white_nh"]:
        if col in df.columns:
            df[col] = pd.to_numeric(df[col], errors="coerce").fillna(0).astype(int)

    df["other_race"] = (df["aian_alone"] + df["nhpi_alone"] + df["other_alone"]).clip(lower=0)

    # % minority using P2_005N (White alone, not Hispanic/Latino, all ages)
    df["pct_minority"] = np.where(
        df["total_pop"] > 0,
        (1 - df["white_nh"] / df["total_pop"]) * 100,
        0,
    )

    _progress(f"  Fetched {len(df)} blocks from Decennial Census")

    # Join with geometry
    merged = block_gdf.merge(df, on="GEOID20", how="inner")
    _progress(f"  Joined {len(merged)} blocks with geometry")

    # Drop blocks with zero population (saves space and time)
    merged = merged[merged["total_pop"] > 0].copy()
    _progress(f"  {len(merged)} blocks with population > 0")

    # Cache
    merged.to_file(DECENNIAL_CACHE, driver="GPKG")
    _progress(f"  Cached to {DECENNIAL_CACHE}")
    return merged


# ═══════════════════════════════════════════════════════════════════════════
# Section 3: TIGER geometry download
# ═══════════════════════════════════════════════════════════════════════════

def download_tiger_blockgroups() -> gpd.GeoDataFrame:
    """Download NC TIGER/Line block group shapefile, filter to Orange County."""
    bg_gpkg = DATA_CACHE / "tiger_blockgroups_orange.gpkg"
    if bg_gpkg.exists():
        _progress(f"Loading cached block group geometries from {bg_gpkg}")
        return gpd.read_file(bg_gpkg)

    _progress("Downloading TIGER/Line block group shapefile for NC ...")
    resp = requests.get(TIGER_BG_URL, timeout=180)
    resp.raise_for_status()

    with tempfile.TemporaryDirectory() as tmpdir:
        zip_path = Path(tmpdir) / "bg.zip"
        zip_path.write_bytes(resp.content)
        _progress(f"  Downloaded {len(resp.content) / 1e6:.1f} MB")

        with zipfile.ZipFile(zip_path, "r") as zf:
            zf.extractall(tmpdir)

        shp_files = list(Path(tmpdir).glob("*.shp"))
        if not shp_files:
            raise FileNotFoundError("No .shp in TIGER block group zip")

        gdf = gpd.read_file(shp_files[0])

    # Filter to Orange County (COUNTYFP == "063")
    gdf = gdf[gdf["COUNTYFP"] == COUNTY_FIPS].copy()
    gdf = gdf.to_crs(CRS_WGS84)

    # Keep only essential columns
    keep_cols = ["GEOID", "TRACTCE", "BLKGRPCE", "ALAND", "AWATER", "geometry"]
    gdf = gdf[[c for c in keep_cols if c in gdf.columns]].drop_duplicates(
        subset=["GEOID"]
    )

    gdf.to_file(bg_gpkg, driver="GPKG")
    _progress(f"  Cached {len(gdf)} Orange County block groups to {bg_gpkg}")
    return gdf


def download_tiger_blocks() -> gpd.GeoDataFrame:
    """Download Orange County TIGER/Line 2020 block shapefile."""
    block_gpkg = DATA_CACHE / "tiger_blocks_orange.gpkg"
    if block_gpkg.exists():
        _progress(f"Loading cached block geometries from {block_gpkg}")
        return gpd.read_file(block_gpkg)

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

    # Keep essential columns
    keep_cols = ["GEOID20", "TRACTCE20", "BLOCKCE20", "ALAND20", "AWATER20", "geometry"]
    gdf = gdf[[c for c in keep_cols if c in gdf.columns]].copy()

    gdf.to_file(block_gpkg, driver="GPKG")
    _progress(f"  Cached {len(gdf)} Orange County blocks to {block_gpkg}")
    return gdf


# ═══════════════════════════════════════════════════════════════════════════
# Section 4: Attendance zone loading
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
    return gdf


def load_district_boundary(schools: gpd.GeoDataFrame) -> gpd.GeoDataFrame:
    """Load CHCCS district boundary (cached by school_desert.py)."""
    if DISTRICT_CACHE.exists():
        _progress(f"Loading cached district boundary from {DISTRICT_CACHE}")
        return gpd.read_file(DISTRICT_CACHE)

    # Fallback: convex hull around schools with 3km buffer
    _progress("District boundary not cached — creating convex hull fallback")
    schools_utm = schools.to_crs(CRS_UTM17N)
    hull = schools_utm.union_all().convex_hull
    buffered = hull.buffer(3000)
    gdf = gpd.GeoDataFrame(geometry=[buffered], crs=CRS_UTM17N).to_crs(CRS_WGS84)
    return gdf


def load_attendance_zones() -> gpd.GeoDataFrame | None:
    """Load CHCCS attendance zones from shapefile, dissolve by ENAME.

    Returns a GeoDataFrame with one row per elementary school zone,
    with column 'school' matching the NCES school name convention.
    Returns None if shapefile not found.
    """
    if not CHCCS_SHP.exists():
        _progress(f"Attendance zone shapefile not found at {CHCCS_SHP}")
        return None

    _progress("Loading attendance zones from CHCCS shapefile ...")
    raw = gpd.read_file(CHCCS_SHP)
    _progress(f"  Raw shapefile: {len(raw)} features")

    # Use ALL features (not just walk zones) to get full attendance zones
    raw = raw.to_crs(CRS_WGS84)

    # Dissolve by ENAME to get one polygon per school
    zones = raw.dissolve(by="ENAME").reset_index()
    _progress(f"  Dissolved to {len(zones)} attendance zones")

    # Map ENAME values to standard school names
    zones["school"] = zones["ENAME"].map(_ENAME_TO_SCHOOL)

    # Log any unmapped zones
    unmapped = zones[zones["school"].isna()]
    if len(unmapped) > 0:
        for _, row in unmapped.iterrows():
            _progress(f"  WARNING: Unmapped ENAME '{row['ENAME']}' — skipping")
        zones = zones[zones["school"].notna()].copy()

    zones = zones[["school", "ENAME", "geometry"]].copy()
    _progress(f"  Final: {len(zones)} elementary school attendance zones:")
    for _, row in zones.iterrows():
        _progress(f"    {row['school']}")

    return zones


def _load_walk_zones() -> gpd.GeoDataFrame | None:
    """Load elementary walk zone polygons from CHCCS shapefile (ESWALK=='Y').

    Returns GeoDataFrame with columns [school, geometry] in WGS84,
    one row per school that has walk-eligible segments.  Returns None
    if the shapefile is missing.
    """
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


GRID_CSV = DATA_PROCESSED / "school_desert_grid.csv"


def _build_nearest_zones(
    grid_csv: Path, mode: str, district: gpd.GeoDataFrame,
) -> gpd.GeoDataFrame | None:
    """Create dissolved zone polygons from school_desert_grid.csv nearest_school.

    Reads baseline rows for *mode*, buffers each grid point by 55 m,
    dissolves by nearest_school, clips to the district boundary, and
    returns a GeoDataFrame with columns [school, geometry] in WGS84.
    """
    if not grid_csv.exists():
        _progress(f"Grid CSV not found: {grid_csv}")
        return None

    df = pd.read_csv(grid_csv)
    df = df[(df["scenario"] == "baseline") & (df["mode"] == mode)].copy()
    df = df.dropna(subset=["nearest_school"])
    if df.empty:
        _progress(f"No baseline/{mode} rows with nearest_school")
        return None

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
    # Keep only polygon geometries after clipping
    mask = dissolved.geometry.geom_type.isin(["Polygon", "MultiPolygon"])
    dissolved = dissolved[mask].copy()

    dissolved = dissolved[["school", "geometry"]].to_crs(CRS_WGS84)
    _progress(f"Built {len(dissolved)} nearest-{mode} zones")
    return dissolved


def _compute_per_school_grid_times(
    mode: str, schools: pd.DataFrame, district: gpd.GeoDataFrame,
) -> pd.DataFrame | None:
    """Compute travel time from each school to each grid point via edge-interpolated snapping.

    Uses the same grid-based, edge-interpolated methodology as school_desert.py:
    rasterize district at 100m, snap each grid cell to the nearest road edge,
    compute per-school travel times using Dijkstra + edge-fraction interpolation.

    Returns DataFrame with columns: grid_id, lat, lon, <School1>, <School2>, ...
    where each school column contains travel time in seconds (NaN if unreachable).

    Caches result to data/cache/isochrone_grid_{mode}.csv.
    """
    cache_path = DATA_CACHE / f"isochrone_grid_{mode}.csv"
    if cache_path.exists():
        _progress(f"Loading cached per-school grid times: {cache_path.name}")
        return pd.read_csv(cache_path)

    import osmnx as ox
    import shapely

    sys.path.insert(0, str(Path(__file__).resolve().parent))
    from school_desert import (
        _add_travel_time_weights, _build_edge_index, _ensure_bidirectional,
        compute_school_travel_times, create_grid,
        WALK_SPEED_MPS, BIKE_SPEED_MPS, DEFAULT_DRIVE_EFFECTIVE_MPH,
        GRID_RESOLUTION_M,
    )

    graph_path = DATA_CACHE / f"network_{mode}.graphml"
    if not graph_path.exists():
        _progress(f"Network graph {graph_path} not found — run school_desert.py first")
        return None

    _progress(f"Computing per-school grid travel times: {mode} ...")
    G = ox.load_graphml(graph_path)
    _add_travel_time_weights(G, mode)
    _ensure_bidirectional(G)

    # Convert schools to GeoDataFrame if needed
    if not isinstance(schools, gpd.GeoDataFrame):
        schools_gdf = gpd.GeoDataFrame(
            schools,
            geometry=gpd.points_from_xy(schools["lon"], schools["lat"]),
            crs=CRS_WGS84,
        )
    else:
        schools_gdf = schools

    # Full-graph Dijkstra from each school
    travel_times = compute_school_travel_times(G, schools_gdf)
    school_names = list(travel_times.keys())

    # Create 100m grid over district
    grid = create_grid(district.geometry.iloc[0])
    n_points = len(grid)
    _progress(f"  Grid: {n_points} points at 100m resolution")

    # Build edge spatial index for snap
    eidx = _build_edge_index(G)
    cos_lat = eidx["cos_lat"]

    grid_lons = grid["lon"].values
    grid_lats = grid["lat"].values

    # Batch query: nearest edge for every grid point
    query_pts = shapely.points(grid_lons * cos_lat, grid_lats)
    nearest_ei = eidx["tree"].nearest(query_pts)

    # Vectorized perpendicular distance (scaled degrees → meters)
    matched_geoms = np.array(eidx["scaled_geoms"], dtype=object)[nearest_ei]
    access_dist_m = shapely.distance(query_pts, matched_geoms) * 111_320.0

    # Vectorized fraction along matched edge
    snap_fracs = shapely.line_locate_point(matched_geoms, query_pts, normalized=True)

    # Per-point endpoint IDs and edge travel times
    snap_start = eidx["start_nodes"][nearest_ei]
    snap_end = eidx["end_nodes"][nearest_ei]
    snap_etime = eidx["edge_times"][nearest_ei]

    # Access-leg speed (same factors as school_desert.py)
    ACCESS_SPEED_FACTOR = {"walk": 0.9, "bike": 0.8, "drive": 0.2}[mode]
    access_speed = ACCESS_SPEED_FACTOR * {
        "walk": WALK_SPEED_MPS, "bike": BIKE_SPEED_MPS,
        "drive": DEFAULT_DRIVE_EFFECTIVE_MPH * 0.44704,
    }[mode]
    max_access_m = 2 * GRID_RESOLUTION_M

    # Compute per-school travel time for each grid point
    school_times = {s: np.full(n_points, np.nan) for s in school_names}

    for i in range(n_points):
        if access_dist_m[i] > max_access_m:
            continue  # too far from any road — unreachable

        u_node = snap_start[i]
        v_node = snap_end[i]
        f = snap_fracs[i]
        e_time = snap_etime[i]
        access_time_s = access_dist_m[i] / access_speed

        for school_name in school_names:
            t_u = travel_times[school_name].get(u_node, np.inf)
            t_v = travel_times[school_name].get(v_node, np.inf)
            via_u = t_u + f * e_time
            via_v = t_v + (1.0 - f) * e_time
            total = min(via_u, via_v) + access_time_s
            if total < np.inf:
                school_times[school_name][i] = total

    # Build result DataFrame
    result = pd.DataFrame({
        "grid_id": grid["grid_id"].values,
        "lat": grid_lats,
        "lon": grid_lons,
    })
    for s in school_names:
        result[s] = school_times[s]

    result.to_csv(cache_path, index=False)
    _progress(f"  Saved per-school grid times ({n_points} pts × {len(school_names)} schools) to {cache_path.name}")
    return result


def _build_isochrone_zones(
    mode: str, threshold_seconds: int,
    schools: pd.DataFrame, district: gpd.GeoDataFrame,
) -> gpd.GeoDataFrame | None:
    """Build per-school isochrone polygons via grid-based edge-interpolated travel times.

    Polygons OVERLAP — a location reachable from multiple schools
    will appear in multiple school polygons.

    Uses ~16K uniformly-spaced grid points with edge-interpolated travel times
    instead of sparse graph nodes with concave hull approximation.

    Caches result to data/cache/isochrone_v2_{mode}_{threshold}s.gpkg.
    """
    cache_path = DATA_CACHE / f"isochrone_v2_{mode}_{int(threshold_seconds)}s.gpkg"
    if cache_path.exists():
        _progress(f"Loading cached isochrone: {cache_path.name}")
        return gpd.read_file(cache_path)

    _progress(f"Building isochrone zones: {mode} @ {threshold_seconds}s ...")

    # Get per-school grid travel times
    grid_df = _compute_per_school_grid_times(mode, schools, district)
    if grid_df is None:
        return None

    # School columns are everything after grid_id, lat, lon
    school_cols = [c for c in grid_df.columns if c not in ("grid_id", "lat", "lon")]

    rows = []
    for school_name in school_cols:
        # Filter grid points reachable within threshold
        mask = grid_df[school_name] <= threshold_seconds
        reachable = grid_df[mask]
        if len(reachable) < 3:
            _progress(f"  {school_name}: only {len(reachable)} reachable grid pts, skipping")
            continue

        # Convert to UTM points, buffer each by half grid cell into squares, dissolve
        pts = gpd.GeoDataFrame(
            geometry=gpd.points_from_xy(reachable["lon"], reachable["lat"]),
            crs=CRS_WGS84,
        ).to_crs(CRS_UTM17N)
        half = 55  # slightly over half of 100m grid cell
        pts["geometry"] = [box(g.x - half, g.y - half, g.x + half, g.y + half)
                           for g in pts.geometry]
        dissolved = pts.dissolve().reset_index(drop=True)
        dissolved["school"] = school_name
        rows.append(dissolved)
        _progress(f"  {school_name}: {len(reachable)} reachable grid pts")

    if not rows:
        _progress(f"  No isochrone zones built for {mode}")
        return None

    result = gpd.GeoDataFrame(pd.concat(rows, ignore_index=True), crs=CRS_UTM17N)

    # Clip to district boundary
    dist_utm = district.to_crs(CRS_UTM17N)
    result = gpd.clip(result, dist_utm)
    mask = result.geometry.geom_type.isin(["Polygon", "MultiPolygon"])
    result = result[mask].copy()
    result = result[["school", "geometry"]].to_crs(CRS_WGS84)

    result.to_file(cache_path, driver="GPKG")
    _progress(f"  Saved {len(result)} isochrone zones to {cache_path.name}")
    return result


# ═══════════════════════════════════════════════════════════════════════════
# Section 5: Spatial analysis
# ═══════════════════════════════════════════════════════════════════════════

def clip_to_district(gdf: gpd.GeoDataFrame,
                     district: gpd.GeoDataFrame) -> gpd.GeoDataFrame:
    """Clip a GeoDataFrame to the district boundary.

    Filters out non-polygon geometries that can result from edge clipping.
    """
    from shapely.geometry import MultiPolygon, Polygon

    clipped = gpd.clip(gdf, district.to_crs(gdf.crs))
    # gpd.clip can produce points/lines at boundaries; keep only polygons
    mask = clipped.geometry.geom_type.isin(["Polygon", "MultiPolygon"])
    return clipped[mask].copy()


def compute_derived_metrics(bg: gpd.GeoDataFrame) -> gpd.GeoDataFrame:
    """Add derived percentage columns to block group data."""
    bg = bg.copy()

    # Replace Census sentinel -666666666 for suppressed median income with NaN
    if "median_hh_income" in bg.columns:
        bg["median_hh_income"] = bg["median_hh_income"].where(bg["median_hh_income"] > 0, np.nan)

    # % young children (0-4)
    bg["pct_young_children"] = np.where(
        bg["total_pop"] > 0,
        (bg["male_under_5"] + bg["female_under_5"]) / bg["total_pop"] * 100,
        0,
    )

    # % elementary-age children (5-9)
    bg["pct_elementary_age"] = np.where(
        bg["total_pop"] > 0,
        (bg["male_5_9"] + bg["female_5_9"]) / bg["total_pop"] * 100,
        0,
    )

    # % minority (non-white non-Hispanic)
    bg["pct_minority"] = np.where(
        bg["race_total"] > 0,
        (1 - bg["white_nh"] / bg["race_total"]) * 100,
        0,
    )

    # % Hispanic
    bg["pct_hispanic"] = np.where(
        bg["race_total"] > 0,
        bg["hispanic"] / bg["race_total"] * 100,
        0,
    )

    # % Black
    bg["pct_black"] = np.where(
        bg["race_total"] > 0,
        bg["black_nh"] / bg["race_total"] * 100,
        0,
    )

    # % below 185% poverty (FRL proxy): sum of ratios < 1.85
    poverty_cols = ["poverty_lt_050", "poverty_050_099", "poverty_100_124",
                    "poverty_125_149", "poverty_150_184"]
    bg["below_185_pov"] = bg[poverty_cols].sum(axis=1)
    bg["pct_below_185_poverty"] = np.where(
        bg["poverty_universe"] > 0,
        bg["below_185_pov"] / bg["poverty_universe"] * 100,
        0,
    )

    # % renter-occupied
    bg["pct_renter"] = np.where(
        bg["tenure_total"] > 0,
        bg["tenure_renter"] / bg["tenure_total"] * 100,
        0,
    )

    # % zero-vehicle households (sum owner + renter zero-vehicle)
    bg["vehicles_zero"] = bg.get("vehicles_zero_owner", 0) + bg.get("vehicles_zero_renter", 0)
    bg["pct_zero_vehicle"] = np.where(
        bg["vehicles_total_hh"] > 0,
        bg["vehicles_zero"] / bg["vehicles_total_hh"] * 100,
        0,
    )

    # % single-parent families with children
    bg["single_parent_with_kids"] = bg["male_hholder_with_kids"] + bg["female_hholder_with_kids"]
    bg["families_with_kids"] = (
        bg["married_with_kids"] + bg["male_hholder_with_kids"] + bg["female_hholder_with_kids"]
    )
    bg["pct_single_parent"] = np.where(
        bg["families_with_kids"] > 0,
        bg["single_parent_with_kids"] / bg["families_with_kids"] * 100,
        0,
    )

    # % low-income households (< $50k)
    low_income_cols = [f"income_{s}" for s in [
        "lt_10k", "10k_15k", "15k_20k", "20k_25k", "25k_30k",
        "30k_35k", "35k_40k", "40k_45k", "45k_50k",
    ]]
    bg["hh_below_50k"] = bg[low_income_cols].sum(axis=1)
    bg["pct_low_income"] = np.where(
        bg["income_total"] > 0,
        bg["hh_below_50k"] / bg["income_total"] * 100,
        0,
    )

    return bg


def _compute_residential_area(
    geom_series: gpd.GeoSeries,
    parcel_sindex,
    parcels_utm: gpd.GeoDataFrame,
) -> np.ndarray:
    """Compute total residential parcel area within each geometry using spatial index.

    Returns an array of residential area values, one per input geometry.
    """
    res_areas = np.zeros(len(geom_series))
    for i, geom in enumerate(geom_series):
        if geom is None or geom.is_empty:
            continue
        candidates = list(parcel_sindex.intersection(geom.bounds))
        if not candidates:
            continue
        clipped = parcels_utm.iloc[candidates].intersection(geom)
        res_areas[i] = clipped.area.sum()
    return res_areas


def downscale_bg_to_blocks(
    bg: gpd.GeoDataFrame,
    blocks: gpd.GeoDataFrame,
    parcels: gpd.GeoDataFrame | None = None,
) -> gpd.GeoDataFrame:
    """Dasymetric downscaling of ACS block-group metrics to Census blocks.

    Race/ethnicity (pct_minority) is already real Decennial data on blocks.
    The other 4 metrics are estimated by distributing BG counts to child blocks
    proportionally to residential parcel area within each block.

    Parameters
    ----------
    bg : GeoDataFrame — ACS block groups with derived metrics (from compute_derived_metrics)
    blocks : GeoDataFrame — Decennial blocks (already has pct_minority, total_pop)
    parcels : GeoDataFrame | None — residential parcel polygons for dasymetric weights

    Returns
    -------
    GeoDataFrame — blocks enriched with downscaled ACS columns
    """
    _progress("Downscaling ACS block-group metrics to block level ...")

    blocks = blocks.copy()

    # Derive parent block-group GEOID from block GEOID (first 12 chars)
    blocks["parent_bg"] = blocks["GEOID20"].str[:12]

    # Build lookup of BG metrics keyed by GEOID
    bg_lookup = bg.set_index("GEOID") if "GEOID" in bg.columns else bg

    # ── Compute dasymetric weights ────────────────────────────────────
    blocks_utm = blocks.to_crs(CRS_UTM17N)
    blocks_utm["block_area"] = blocks_utm.geometry.area

    # Residential area per block (if parcels available)
    use_res = False
    if parcels is not None:
        mask = parcels["is_residential"] == True
        if "imp_vac" in parcels.columns:
            mask = mask & parcels["imp_vac"].str.contains("Improved", case=False, na=False)
        res_parcels = parcels[mask].copy()
        if len(res_parcels) > 0:
            parcels_utm = res_parcels.to_crs(CRS_UTM17N)
            parcel_sindex = parcels_utm.sindex
            _progress("  Computing residential area per block ...")
            blocks_utm["block_res_area"] = _compute_residential_area(
                blocks_utm.geometry, parcel_sindex, parcels_utm,
            )
            use_res = True

    # Sum block areas per parent BG for weight denominator
    if use_res:
        bg_res_totals = blocks_utm.groupby("parent_bg")["block_res_area"].sum()
        bg_area_totals = blocks_utm.groupby("parent_bg")["block_area"].sum()
        # Weight: residential area where available, fallback to plain area
        weights = []
        for _, row in blocks_utm.iterrows():
            bg_id = row["parent_bg"]
            bg_res = bg_res_totals.get(bg_id, 0)
            if bg_res > 0:
                weights.append(row["block_res_area"] / bg_res)
            else:
                bg_a = bg_area_totals.get(bg_id, 1)
                weights.append(row["block_area"] / bg_a)
        blocks["weight"] = weights
        n_res = sum(1 for w, r in zip(weights, blocks_utm["block_res_area"]) if r > 0)
        _progress(f"  Weights: {n_res} residential-area, {len(weights) - n_res} area-fallback")
    else:
        bg_area_totals = blocks_utm.groupby("parent_bg")["block_area"].sum()
        blocks["weight"] = [
            row["block_area"] / bg_area_totals.get(row["parent_bg"], 1)
            for _, row in blocks_utm.iterrows()
        ]
        _progress("  Using plain area weights (no parcel data)")

    n_over = (blocks["weight"] > 1.0).sum()
    if n_over > 0:
        _progress(f"  WARNING: {n_over} blocks had weight > 1.0 (max {blocks['weight'].max():.4f}), clipped")
    blocks["weight"] = blocks["weight"].clip(upper=1.0)

    # ── Downscale extensive counts from parent BG ─────────────────────
    # Metrics: (numerator_col, denominator_col, pct_col)
    downscale_specs = [
        ("below_185_pov", "poverty_universe", "pct_below_185_poverty"),
        ("tenure_renter", "tenure_total", "pct_renter"),
        ("vehicles_zero", "vehicles_total_hh", "pct_zero_vehicle"),
        # elementary age: numerator is (male_5_9 + female_5_9), denominator is total_pop
    ]

    # Map parent BG values onto blocks
    for num_col, den_col, pct_col in downscale_specs:
        bg_num = bg_lookup[num_col] if num_col in bg_lookup.columns else pd.Series(dtype=float)
        bg_den = bg_lookup[den_col] if den_col in bg_lookup.columns else pd.Series(dtype=float)
        blocks[f"_bg_{num_col}"] = blocks["parent_bg"].map(bg_num).fillna(0)
        blocks[f"_bg_{den_col}"] = blocks["parent_bg"].map(bg_den).fillna(0)
        blocks[num_col] = blocks[f"_bg_{num_col}"] * blocks["weight"]
        blocks[den_col] = blocks[f"_bg_{den_col}"] * blocks["weight"]
        blocks[pct_col] = np.where(
            blocks[den_col] > 0,
            blocks[num_col] / blocks[den_col] * 100,
            0,
        )
        # Clean up temp columns
        blocks.drop(columns=[f"_bg_{num_col}", f"_bg_{den_col}"], inplace=True)

    # Elementary age: (male_5_9 + female_5_9) / total_pop
    for col in ("male_5_9", "female_5_9"):
        bg_vals = bg_lookup[col] if col in bg_lookup.columns else pd.Series(dtype=float)
        blocks[col] = blocks["parent_bg"].map(bg_vals).fillna(0) * blocks["weight"]
    bg_tp = bg_lookup["total_pop"] if "total_pop" in bg_lookup.columns else pd.Series(dtype=float)
    blocks["_bg_total_pop"] = blocks["parent_bg"].map(bg_tp).fillna(0)
    blocks["est_total_pop"] = blocks["_bg_total_pop"] * blocks["weight"]
    blocks["pct_elementary_age"] = np.where(
        blocks["est_total_pop"] > 0,
        (blocks["male_5_9"] + blocks["female_5_9"]) / blocks["est_total_pop"] * 100,
        0,
    )
    blocks.drop(columns=["_bg_total_pop", "male_5_9", "female_5_9"], inplace=True)

    # Young children (0-4): (male_under_5 + female_under_5) / total_pop
    for col in ("male_under_5", "female_under_5"):
        bg_vals = bg_lookup[col] if col in bg_lookup.columns else pd.Series(dtype=float)
        blocks[col] = blocks["parent_bg"].map(bg_vals).fillna(0) * blocks["weight"]
    bg_tp2 = bg_lookup["total_pop"] if "total_pop" in bg_lookup.columns else pd.Series(dtype=float)
    blocks["_bg_total_pop"] = blocks["parent_bg"].map(bg_tp2).fillna(0)
    blocks["est_total_pop2"] = blocks["_bg_total_pop"] * blocks["weight"]
    blocks["pct_young_children"] = np.where(
        blocks["est_total_pop2"] > 0,
        (blocks["male_under_5"] + blocks["female_under_5"]) / blocks["est_total_pop2"] * 100,
        0,
    )
    blocks.drop(columns=["_bg_total_pop", "est_total_pop2", "male_under_5", "female_under_5"], inplace=True)

    # Median income: propagate parent BG value (not downscaled — median is not extensive)
    if "median_hh_income" in bg_lookup.columns:
        bg_income = bg_lookup["median_hh_income"]
        blocks["median_hh_income"] = blocks["parent_bg"].map(bg_income)

    # Clean up
    blocks.drop(columns=["weight", "parent_bg"], inplace=True)

    # Clip block geometries to residential parcels for dasymetric display
    if use_res and len(res_parcels) > 0:
        _progress("  Clipping block geometries to residential parcels ...")
        res_parcels_utm2 = res_parcels.to_crs(CRS_UTM17N)
        res_union = res_parcels_utm2.geometry.union_all()
        blocks_utm2 = blocks.to_crs(CRS_UTM17N)
        blocks["dasymetric_geometry"] = blocks_utm2.geometry.intersection(res_union).to_crs(CRS_WGS84)
        # Drop blocks with empty intersection
        empty_mask = blocks["dasymetric_geometry"].is_empty | blocks["dasymetric_geometry"].isna()
        n_before = len(blocks)
        blocks = blocks[~empty_mask].copy()
        _progress(f"  Clipped: {n_before} -> {len(blocks)} blocks with residential area")

    _progress(f"  Downscaled 5 ACS metrics to {len(blocks)} blocks")
    return blocks


def intersect_zones_with_blockgroups(
    zones: gpd.GeoDataFrame,
    bg: gpd.GeoDataFrame,
    parcels: gpd.GeoDataFrame | None = None,
) -> gpd.GeoDataFrame:
    """Dasymetric area-weighted interpolation of block group data to attendance zones.

    When residential parcel data is provided, population is allocated proportionally
    to the residential footprint area within each zone × block group fragment:
        weight = fragment_residential_area / bg_residential_area

    This concentrates population in areas where people actually live, rather than
    assuming uniform distribution across the entire block group area.

    Falls back to plain area-weighted interpolation (weight = fragment_area / bg_area)
    when parcels are unavailable or when a block group has no residential parcels.
    """
    _progress("Performing area-weighted interpolation ...")

    # Work in UTM for accurate area calculations
    zones_utm = zones.to_crs(CRS_UTM17N)
    bg_utm = bg.to_crs(CRS_UTM17N)

    # Compute total area of each block group
    bg_utm["bg_area"] = bg_utm.geometry.area

    # Prepare residential parcels for dasymetric weighting
    use_dasymetric = False
    parcels_utm = None
    parcel_sindex = None

    if parcels is not None:
        _progress("  Using dasymetric weighting (residential parcel area)")
        # Filter to improved residential parcels only
        mask = parcels["is_residential"] == True
        if "imp_vac" in parcels.columns:
            mask = mask & parcels["imp_vac"].str.contains("Improved", case=False, na=False)
        res_parcels = parcels[mask].copy()
        _progress(f"  Filtered to {len(res_parcels):,} improved residential parcels")

        if len(res_parcels) > 0:
            parcels_utm = res_parcels.to_crs(CRS_UTM17N)
            parcel_sindex = parcels_utm.sindex

            # Compute residential area within each block group
            _progress("  Computing residential area per block group ...")
            bg_utm["bg_res_area"] = _compute_residential_area(
                bg_utm.geometry, parcel_sindex, parcels_utm,
            )
            n_with_res = (bg_utm["bg_res_area"] > 0).sum()
            n_total = len(bg_utm)
            _progress(f"  {n_with_res}/{n_total} block groups have residential parcels")
            use_dasymetric = True

    # Overlay (intersection) — creates fragments where zones and BGs overlap
    fragments = gpd.overlay(zones_utm, bg_utm, how="intersection")
    fragments["frag_area"] = fragments.geometry.area

    if use_dasymetric:
        # Compute residential area within each fragment
        _progress("  Computing residential area per fragment ...")
        fragments["frag_res_area"] = _compute_residential_area(
            fragments.geometry, parcel_sindex, parcels_utm,
        )

        # Dasymetric weight: proportion of BG's residential area in this fragment
        # Fallback to plain area weight where BG has no residential parcels
        fragments["weight"] = np.where(
            fragments["bg_res_area"] > 0,
            fragments["frag_res_area"] / fragments["bg_res_area"],
            fragments["frag_area"] / fragments["bg_area"],
        )

        n_dasymetric = (fragments["bg_res_area"] > 0).sum()
        n_fallback = len(fragments) - n_dasymetric
        _progress(f"  Weights: {n_dasymetric} dasymetric, {n_fallback} area-fallback")
    else:
        # Plain area-weighted interpolation (no parcel data)
        if parcels is None:
            _progress("  No parcel data — using plain area-weighted interpolation")
        fragments["weight"] = fragments["frag_area"] / fragments["bg_area"]

    n_over = (fragments["weight"] > 1.0).sum()
    if n_over > 0:
        _progress(f"  WARNING: {n_over} fragments had weight > 1.0 (max {fragments['weight'].max():.4f}), clipped")
    fragments["weight"] = fragments["weight"].clip(upper=1.0)

    _progress(f"  Created {len(fragments)} zone × block group fragments")

    return fragments


def aggregate_zone_demographics(
    fragments: gpd.GeoDataFrame,
    zones: gpd.GeoDataFrame,
) -> pd.DataFrame:
    """Aggregate area-weighted block group data to per-zone summaries."""
    _progress("Aggregating demographics by attendance zone ...")

    # Population-weighted columns to sum
    income_bracket_cols = [
        "income_lt_10k", "income_10k_15k", "income_15k_20k", "income_20k_25k",
        "income_25k_30k", "income_30k_35k", "income_35k_40k", "income_40k_45k",
        "income_45k_50k", "income_50k_60k", "income_60k_75k", "income_75k_100k",
        "income_100k_125k", "income_125k_150k", "income_150k_200k", "income_200k_plus",
    ]
    count_cols = [
        "total_pop", "male_under_5", "female_under_5", "male_5_9", "female_5_9",
        "race_total", "white_nh", "black_nh", "asian_nh", "hispanic",
        "aian_nh", "nhpi_nh", "other_nh", "two_plus_nh",
        "poverty_universe", "below_185_pov",
        "tenure_total", "tenure_owner", "tenure_renter",
        "vehicles_total_hh", "vehicles_zero",
        "income_total", "hh_below_50k",
        "families_with_kids", "single_parent_with_kids",
    ] + income_bracket_cols

    # Apply weights and aggregate
    if len(fragments) == 0:
        _progress("  WARNING: No zone x block group fragments — check CRS and spatial overlap")
        return pd.DataFrame()

    records = []
    for school in sorted(fragments["school"].unique()):
        zone_frags = fragments[fragments["school"] == school]
        row = {"school": school}

        for col in count_cols:
            if col in zone_frags.columns:
                row[col] = (zone_frags[col] * zone_frags["weight"]).sum()

        # Weighted median income (population-weighted average of medians — approximate)
        if "median_hh_income" in zone_frags.columns:
            valid = zone_frags[zone_frags["median_hh_income"] > 0]
            if len(valid) > 0:
                weighted_income = (valid["median_hh_income"] * valid["total_pop"] * valid["weight"]).sum()
                total_weight = (valid["total_pop"] * valid["weight"]).sum()
                row["median_hh_income"] = weighted_income / total_weight if total_weight > 0 else 0
            else:
                row["median_hh_income"] = np.nan
        records.append(row)

    result = pd.DataFrame(records)

    # Compute derived percentages
    result["pct_minority"] = np.where(
        result["race_total"] > 0,
        (1 - result["white_nh"] / result["race_total"]) * 100, 0
    )
    result["pct_black"] = np.where(
        result["race_total"] > 0,
        result["black_nh"] / result["race_total"] * 100, 0
    )
    result["pct_hispanic"] = np.where(
        result["race_total"] > 0,
        result["hispanic"] / result["race_total"] * 100, 0
    )
    result["pct_below_185_poverty"] = np.where(
        result["poverty_universe"] > 0,
        result["below_185_pov"] / result["poverty_universe"] * 100, 0
    )
    result["pct_renter"] = np.where(
        result["tenure_total"] > 0,
        result["tenure_renter"] / result["tenure_total"] * 100, 0
    )
    result["pct_zero_vehicle"] = np.where(
        result["vehicles_total_hh"] > 0,
        result["vehicles_zero"] / result["vehicles_total_hh"] * 100, 0
    )
    result["pct_low_income"] = np.where(
        result["income_total"] > 0,
        result["hh_below_50k"] / result["income_total"] * 100, 0
    )
    result["pct_single_parent"] = np.where(
        result["families_with_kids"] > 0,
        result["single_parent_with_kids"] / result["families_with_kids"] * 100, 0
    )
    result["pct_elementary_age"] = np.where(
        result["total_pop"] > 0,
        (result["male_5_9"] + result["female_5_9"]) / result["total_pop"] * 100, 0
    )
    result["pct_young_children"] = np.where(
        result["total_pop"] > 0,
        (result["male_under_5"] + result["female_under_5"]) / result["total_pop"] * 100, 0
    )

    # Population conservation check
    if "total_pop" in fragments.columns:
        zone_total = result["total_pop"].sum()
        bg_total = (fragments["total_pop"] * fragments["weight"]).sum()
        diff_pct = abs(zone_total - bg_total) / bg_total * 100 if bg_total > 0 else 0
        if diff_pct > 1:
            _progress(f"  WARNING: Population conservation error: zones={zone_total:.0f}, "
                      f"source={bg_total:.0f} ({diff_pct:.1f}% difference)")

    # Round for readability
    pct_cols = [c for c in result.columns if c.startswith("pct_")]
    for col in pct_cols:
        result[col] = result[col].round(1)
    result["median_hh_income"] = result["median_hh_income"].round(0).astype("Int64")
    result["total_pop"] = result["total_pop"].round(0).astype(int)

    _progress(f"  Aggregated demographics for {len(result)} zones")
    return result


# ═══════════════════════════════════════════════════════════════════════════
# Section 6: Dot-density map generation
# ═══════════════════════════════════════════════════════════════════════════

def generate_racial_dots(
    blocks: gpd.GeoDataFrame,
    dots_per_person: int = 5,
    parcels: gpd.GeoDataFrame | None = None,
) -> dict:
    """Generate dot-density points for racial/ethnic categories with block index.

    One dot represents `dots_per_person` people. Dots are placed randomly
    within Census blocks, constrained to residential parcel polygons when
    available (dasymetric refinement).

    Returns dict with keys:
      - "dots": list of [lat, lon, race_idx, block_idx] quads
      - "block_geoids": list of GEOID20 strings indexed by block_idx
      - "n_blocks": int — number of blocks that produced dots
    """
    from shapely import Point as ShapelyPoint

    _progress(f"Generating dot-density layer (1 dot = {dots_per_person} people) ...")

    blocks_utm = blocks.to_crs(CRS_UTM17N)

    # Prepare residential mask if parcels available
    parcel_sindex = None
    parcels_utm = None
    if parcels is not None:
        _progress("  Using dasymetric placement (constraining dots to residential parcels)")
        mask = parcels["is_residential"] == True
        if "imp_vac" in parcels.columns:
            mask = mask & parcels["imp_vac"].str.contains("Improved", case=False, na=False)
        res_parcels = parcels[mask].copy()
        _progress(f"  Filtered to {len(res_parcels):,} improved residential parcels")
        if len(res_parcels) > 0:
            parcels_utm = res_parcels.to_crs(CRS_UTM17N)
            parcel_sindex = parcels_utm.sindex

    # Race column → race_idx mapping (matches RACE_CATEGORIES order)
    race_keys = list(RACE_CATEGORIES.keys())
    race_col_to_idx = {k: i for i, k in enumerate(race_keys)}

    raw_dots = []  # [x_utm, y_utm, race_idx, block_idx]
    block_geoids = []  # block_geoids[block_idx] = GEOID20
    block_idx_counter = 0

    rng = np.random.default_rng(42)
    total_blocks = len(blocks_utm)

    for idx, (_, block) in enumerate(blocks_utm.iterrows()):
        if idx % 500 == 0 and idx > 0:
            _progress(f"  Processed {idx}/{total_blocks} blocks ({len(raw_dots):,} dots so far)")

        block_geom = block.geometry
        if block_geom.is_empty or not block_geom.is_valid:
            continue

        # Determine placement region: intersection of block with parcels, or full block
        placement_geom = block_geom
        if parcels_utm is not None:
            candidates = list(parcel_sindex.intersection(block_geom.bounds))
            if candidates:
                parcel_union = parcels_utm.iloc[candidates].union_all()
                intersection = block_geom.intersection(parcel_union)
                if not intersection.is_empty and intersection.area > 10:
                    placement_geom = intersection

        # Skip tiny geometries
        if placement_geom.area < 10:
            continue

        # Check if this block will produce any dots
        block_has_dots = False
        for race_col in race_keys:
            count = int(block.get(race_col, 0))
            if count // dots_per_person > 0:
                block_has_dots = True
                break

        if not block_has_dots:
            continue

        # Assign block index
        bidx = block_idx_counter
        block_idx_counter += 1
        block_geoids.append(block.get("GEOID20", ""))

        for race_col in race_keys:
            race_idx = race_col_to_idx[race_col]
            count = int(block.get(race_col, 0))
            n_dots = count // dots_per_person
            if n_dots <= 0:
                continue

            # Generate random points within placement geometry
            try:
                from shapely import random_points
                pts = random_points(placement_geom, n_dots, rng=rng)
            except (ImportError, TypeError):
                # Fallback for older Shapely
                pts = _random_points_fallback(placement_geom, n_dots, rng)

            # Handle single point vs. multipoint
            if hasattr(pts, "geoms"):
                point_list = list(pts.geoms)
            elif hasattr(pts, "__iter__") and not isinstance(pts, ShapelyPoint):
                point_list = list(pts)
            else:
                point_list = [pts]

            for pt in point_list:
                if hasattr(pt, "x"):
                    raw_dots.append([pt.x, pt.y, race_idx, bidx])

    _progress(f"  Generated {len(raw_dots):,} dots across {block_idx_counter} blocks")

    # Convert UTM dots back to WGS84: [lat, lon, race_idx, block_idx]
    dots = []
    if raw_dots:
        from pyproj import Transformer
        transformer = Transformer.from_crs(CRS_UTM17N, CRS_WGS84, always_xy=True)
        for d in raw_dots:
            lon, lat = transformer.transform(d[0], d[1])
            dots.append([round(lat, 5), round(lon, 5), d[2], d[3]])

    return {
        "dots": dots,
        "block_geoids": block_geoids,
        "n_blocks": block_idx_counter,
    }


def _random_points_fallback(geom, n: int, rng) -> list:
    """Fallback random point generation for older Shapely."""
    from shapely.geometry import Point as ShapelyPoint
    minx, miny, maxx, maxy = geom.bounds
    points = []
    max_attempts = n * 20
    attempts = 0
    while len(points) < n and attempts < max_attempts:
        x = rng.uniform(minx, maxx)
        y = rng.uniform(miny, maxy)
        pt = ShapelyPoint(x, y)
        if geom.contains(pt):
            points.append(pt)
        attempts += 1
    return points


# Metric dot-map configuration: (metric_col, display_name, colormap, prefix, suffix, fmt)
METRIC_DOT_SPECS = [
    ("median_hh_income", "Median HH Income", "YlGn", "$", "", ",.0f"),
    ("pct_below_185_poverty", "% Below 185% Poverty", "YlOrRd", "", "%", ".0f"),
    ("pct_minority", "% Minority", "PuBuGn", "", "%", ".0f"),
    ("pct_renter", "% Renter-Occupied", "OrRd", "", "%", ".0f"),
    ("pct_zero_vehicle", "% Zero-Vehicle HH", "Reds", "", "%", ".0f"),
    ("pct_elementary_age", "% Elementary Age (5-9)", "BuPu", "", "%", ".1f"),
    ("pct_young_children", "% Young Children (0-4)", "PuRd", "", "%", ".1f"),
]


# ═══════════════════════════════════════════════════════════════════════════
# Section 7: Choropleth helpers (for Folium GeoJson styling)
# ═══════════════════════════════════════════════════════════════════════════

def _make_choropleth_style(gdf: gpd.GeoDataFrame, column: str,
                           cmap_name: str = "YlOrRd",
                           vmin: float = None, vmax: float = None):
    """Return a Folium style_function for choropleth coloring of a GeoDataFrame column."""
    import matplotlib.colors as mcolors

    vals = gdf[column].dropna()
    if vmin is None:
        vmin = vals.quantile(0.05)
    if vmax is None:
        vmax = vals.quantile(0.95)

    norm = mcolors.Normalize(vmin=vmin, vmax=vmax)
    cmap = plt.get_cmap(cmap_name)

    color_lookup = {}
    for idx, row in gdf.iterrows():
        val = row[column]
        if pd.isna(val):
            color_lookup[idx] = "#cccccc"
        else:
            rgba = cmap(norm(val))
            color_lookup[idx] = mcolors.rgb2hex(rgba)

    def style_fn(feature):
        fid = feature.get("id")
        # Folium uses string IDs from the GeoJSON
        return {
            "fillColor": color_lookup.get(fid, "#cccccc"),
            "fillOpacity": 0.6,
            "color": "#333333",
            "weight": 0.5,
        }

    return style_fn, vmin, vmax, cmap, norm


def _build_legend_html(title: str, cmap_name: str, vmin: float, vmax: float,
                       fmt: str = ".0f", prefix: str = "", suffix: str = "") -> str:
    """Build an HTML gradient legend bar."""
    import matplotlib.colors as mcolors

    cmap = plt.get_cmap(cmap_name)
    n_stops = 6
    gradient_stops = []
    for i in range(n_stops):
        frac = i / (n_stops - 1)
        rgba = cmap(frac)
        hex_color = mcolors.rgb2hex(rgba)
        gradient_stops.append(f"{hex_color} {frac*100:.0f}%")

    gradient_css = f"linear-gradient(to right, {', '.join(gradient_stops)})"

    labels = []
    for i in range(n_stops):
        frac = i / (n_stops - 1)
        val = vmin + frac * (vmax - vmin)
        labels.append(f"{prefix}{val:{fmt}}{suffix}")

    labels_html = "".join(
        f'<span style="flex:1; text-align:center; font-size:10px;">{lbl}</span>'
        for lbl in labels
    )

    return f"""
    <div style="padding: 6px 10px; background: white; border-radius: 4px;
                box-shadow: 0 1px 4px rgba(0,0,0,0.3); max-width: 300px;">
        <div style="font-weight: bold; font-size: 12px; margin-bottom: 4px;">{title}</div>
        <div style="height: 14px; background: {gradient_css}; border-radius: 2px;"></div>
        <div style="display: flex; justify-content: space-between; margin-top: 2px;">
            {labels_html}
        </div>
    </div>
    """


# ═══════════════════════════════════════════════════════════════════════════
# Section 8: Interactive Folium map
# ═══════════════════════════════════════════════════════════════════════════

def create_socioeconomic_map(
    bg: gpd.GeoDataFrame,
    zones: gpd.GeoDataFrame | None,
    schools: gpd.GeoDataFrame,
    district: gpd.GeoDataFrame,
    zone_demographics: pd.DataFrame | None,
    racial_dots: dict | None = None,
    dots_per_person: int = 5,
    enriched_blocks: gpd.GeoDataFrame | None = None,
    affordable_housing: gpd.GeoDataFrame | None = None,
) -> folium.Map:
    """Create interactive map with choropleth, dot-density, and zone overlays."""
    _progress("Creating interactive socioeconomic map ...")

    m = folium.Map(
        location=CHAPEL_HILL_CENTER,
        zoom_start=12,
        tiles="cartodbpositron",
        control_scale=True,
        prefer_canvas=True,
    )

    # -- District boundary --
    folium.GeoJson(
        district.to_crs(CRS_WGS84).__geo_interface__,
        name="District Boundary",
        style_function=lambda x: {
            "fillColor": "transparent",
            "color": "#333333",
            "weight": 2,
            "dashArray": "5,5",
        },
    ).add_to(m)

    # -- Choropleth layers (block group level) --
    choropleth_layers = [
        ("Median Income (Block Group)", "median_hh_income", "YlGn", "$", "", ",.0f"),
        ("% Below 185% Poverty (Block Group)", "pct_below_185_poverty", "YlOrRd", "", "%", ".0f"),
        ("% Minority (Block Group)", "pct_minority", "PuBuGn", "", "%", ".0f"),
        ("% Renter-Occupied (Block Group)", "pct_renter", "OrRd", "", "%", ".0f"),
        ("% Zero-Vehicle HH (Block Group)", "pct_zero_vehicle", "Reds", "", "%", ".0f"),
        ("% Elementary Age 5-9 (Block Group)", "pct_elementary_age", "BuPu", "", "%", ".1f"),
        ("% Young Children 0-4 (Block Group)", "pct_young_children", "PuRd", "", "%", ".1f"),
    ]

    bg_wgs = bg.to_crs(CRS_WGS84).copy()
    bg_fg_names = []  # JS variable names for BG FeatureGroups

    for layer_name, col, cmap_name, prefix, suffix, fmt in choropleth_layers:
        if col not in bg_wgs.columns:
            continue

        fg = folium.FeatureGroup(name=layer_name, show=False)
        bg_fg_names.append(fg.get_name())

        # Build style function
        vals = bg_wgs[col].dropna()
        vmin = vals.quantile(0.05)
        vmax = vals.quantile(0.95)

        import matplotlib.colors as mcolors
        norm = mcolors.Normalize(vmin=vmin, vmax=vmax)
        cmap = plt.get_cmap(cmap_name)

        # Add each block group as a separate feature with popup
        for _, row in bg_wgs.iterrows():
            val = row.get(col)
            if pd.isna(val):
                fill = "#cccccc"
            else:
                fill = mcolors.rgb2hex(cmap(norm(val)))

            popup_text = f"<b>{col.replace('_', ' ').title()}</b>: {prefix}{val:{fmt}}{suffix}"
            if "total_pop" in row:
                popup_text += f"<br>Pop: {int(row['total_pop']):,}"

            folium.GeoJson(
                gpd.GeoDataFrame([row], crs=CRS_WGS84).__geo_interface__,
                style_function=lambda x, fc=fill: {
                    "fillColor": fc,
                    "fillOpacity": 0.6,
                    "color": "#666",
                    "weight": 0.5,
                },
                popup=folium.Popup(popup_text, max_width=200),
            ).add_to(fg)

        fg.add_to(m)

    # -- Block-level choropleth layers --
    blk_fg_names = []  # default empty; populated below if enriched_blocks available
    if enriched_blocks is not None:
        import matplotlib.colors as mcolors

        block_layers = [
            ("Median Income (Block est.)", "median_hh_income", "YlGn", "$", "", ",.0f", True),
            ("% Minority (Block)", "pct_minority", "PuBuGn", "", "%", ".0f", False),
            ("% Below 185% Poverty (Block est.)", "pct_below_185_poverty", "YlOrRd", "", "%", ".0f", True),
            ("% Renter-Occupied (Block est.)", "pct_renter", "OrRd", "", "%", ".0f", True),
            ("% Zero-Vehicle HH (Block est.)", "pct_zero_vehicle", "Reds", "", "%", ".0f", True),
            ("% Elementary Age (Block est.)", "pct_elementary_age", "BuPu", "", "%", ".1f", True),
            ("% Young Children (Block est.)", "pct_young_children", "PuRd", "", "%", ".1f", True),
        ]

        blk_wgs = enriched_blocks.copy()
        # Use dasymetric (residential-clipped) geometry if available
        if "dasymetric_geometry" in blk_wgs.columns:
            blk_wgs = blk_wgs[~blk_wgs["dasymetric_geometry"].is_empty].copy()
            blk_wgs = blk_wgs.set_geometry("dasymetric_geometry").drop(columns=["geometry"])
            blk_wgs = blk_wgs.rename_geometry("geometry")
        blk_wgs = blk_wgs.to_crs(CRS_WGS84)
        # Reduce coordinate precision for smaller HTML
        blk_wgs.geometry = blk_wgs.geometry.simplify(0.0001, preserve_topology=True)
        blk_fg_names = []  # JS variable names for Block FeatureGroups

        for layer_name, col, cmap_name, prefix, suffix, fmt, is_estimate in block_layers:
            if col not in blk_wgs.columns:
                continue

            vals = blk_wgs[col].dropna()
            if len(vals) == 0:
                continue

            vmin = vals.quantile(0.05)
            vmax = vals.quantile(0.95)
            if vmax <= vmin:
                vmax = vmin + 1

            norm = mcolors.Normalize(vmin=vmin, vmax=vmax)
            cmap = plt.get_cmap(cmap_name)

            # Pre-compute fill colors for style_function
            fill_colors = {}
            for idx, row in blk_wgs.iterrows():
                val = row.get(col)
                if pd.isna(val):
                    fill_colors[str(idx)] = "#cccccc"
                else:
                    fill_colors[str(idx)] = mcolors.rgb2hex(cmap(norm(val)))

            # Build GeoJSON with properties for popup
            subset = blk_wgs[["geometry", col, "total_pop", "GEOID20"]].copy()
            subset["_fill"] = [fill_colors.get(str(i), "#cccccc") for i in subset.index]

            # Use efficient single GeoJson with style_function
            fg = folium.FeatureGroup(name=layer_name, show=False)
            blk_fg_names.append(fg.get_name())

            geojson_data = subset.__geo_interface__
            # Inject fill colors into feature properties
            for feature in geojson_data["features"]:
                fid = feature.get("id", "")
                feature["properties"]["_fill"] = fill_colors.get(str(fid), "#cccccc")

            source_note = "Estimated from block group data" if is_estimate else "2020 Decennial Census"

            def make_style_fn(fc_map):
                def style_fn(feature):
                    return {
                        "fillColor": feature["properties"].get("_fill", "#cccccc"),
                        "fillOpacity": 0.6,
                        "color": "#666",
                        "weight": 1.0,
                    }
                return style_fn

            def make_popup_fn(col_name, pfx, sfx, f, note):
                def popup_fn(feature):
                    props = feature.get("properties", {})
                    val = props.get(col_name, 0)
                    pop = props.get("total_pop", 0)
                    geoid = props.get("GEOID20", "")
                    text = (
                        f"<b>{col_name.replace('_', ' ').title()}</b>: "
                        f"{pfx}{val:{f}}{sfx}<br>"
                        f"Pop: {int(pop):,}<br>"
                        f"Block: {geoid}<br>"
                        f"<i>{note}</i>"
                    )
                    return folium.Popup(text, max_width=250)
                return popup_fn

            popup_fn = make_popup_fn(col, prefix, suffix, fmt, source_note)
            style_fn = make_style_fn(fill_colors)

            folium.GeoJson(
                geojson_data,
                style_function=style_fn,
                highlight_function=lambda x: {
                    "fillOpacity": 0.85,
                    "color": "#000000",
                    "weight": 3,
                },
                popup=folium.GeoJsonPopup(
                    fields=[col, "total_pop", "GEOID20"],
                    aliases=[
                        col.replace("_", " ").title(),
                        "Population",
                        "Block GEOID",
                    ],
                    labels=True,
                ),
                tooltip=folium.GeoJsonTooltip(
                    fields=[col],
                    aliases=[layer_name],
                    style="font-size: 11px;",
                ),
            ).add_to(fg)

            fg.add_to(m)

        _progress(f"  Added {len(block_layers)} block-level choropleth layers")

    # -- Unified population dots FeatureGroup --
    has_dots = racial_dots is not None and len(racial_dots.get("dots", [])) > 0
    dot_fg = None
    if has_dots:
        dot_fg = folium.FeatureGroup(name="Population Dots", show=True)
        dot_fg.add_to(m)

    # -- Zone overlays (5 types) --
    zone_colors = [
        "#e41a1c", "#377eb8", "#4daf4a", "#984ea3", "#ff7f00",
        "#a65628", "#f781bf", "#999999", "#66c2a5", "#fc8d62", "#8da0cb",
    ]

    # Build a consistent colour mapping: school name → colour
    all_school_names = sorted(schools["school"].tolist())
    school_color_map = {s: zone_colors[i % len(zone_colors)] for i, s in enumerate(all_school_names)}

    def _make_zone_fg(gdf, label, show=False, is_multi=False):
        """Create a FeatureGroup of zone polygons from a GeoDataFrame."""
        fg = folium.FeatureGroup(name=label, show=show)
        names = []
        for _, row in gdf.iterrows():
            sn = row["school"]
            names.append(sn)
            c = school_color_map.get(sn, "#888888")
            kwargs = dict(
                data=gpd.GeoDataFrame([row], crs=CRS_WGS84).__geo_interface__,
                style_function=lambda x, c=c: {
                    "fillColor": c, "fillOpacity": 0.08,
                    "color": c, "weight": 2.5,
                },
            )
            if not is_multi:
                kwargs["popup"] = folium.Popup(f"<b>{sn}</b>", max_width=300)
                kwargs["tooltip"] = sn
            folium.GeoJson(**kwargs).add_to(fg)
        fg.add_to(m)
        return fg, names

    # Load extra zone GDFs
    walk_zones_gdf = _load_walk_zones()

    # Build isochrone zones (overlapping — a location can be in multiple school zones)
    isochrone_gdfs = {}
    for iso_mode, iso_thresh, iso_label in ISOCHRONE_DEFS:
        isochrone_gdfs[iso_label] = _build_isochrone_zones(
            iso_mode, iso_thresh, schools, district
        )

    # Build list of zone type dicts: (key, label, gdf, is_multi)
    zone_type_defs = [
        ("school", "Current CHCCS Zones", zones, False),
        ("walk_zone", "Walk Zones", walk_zones_gdf, False),
    ] + [
        (f"iso_{m}_{t}", label, isochrone_gdfs.get(label), True)
        for m, t, label in ISOCHRONE_DEFS
    ]

    zone_types = []  # [{key, label, fg_name, names, gdf, is_multi}, ...]
    for zt_key, zt_label, zt_gdf, zt_multi in zone_type_defs:
        if zt_gdf is not None and len(zt_gdf) > 0:
            show_initial = (zt_key == "school")
            fg, names = _make_zone_fg(zt_gdf, zt_label, show=show_initial, is_multi=zt_multi)
            zone_types.append({
                "key": zt_key, "label": zt_label,
                "fg_name": fg.get_name(), "names": sorted(names),
                "gdf": zt_gdf, "is_multi": zt_multi,
            })
        else:
            _progress(f"  Skipping zone type '{zt_label}' — no data")

    # Backward-compat: zone_names_list for the first zone type (school zones)
    zone_names_list = zone_types[0]["names"] if zone_types else []

    # Master school list — always all 11, for barplot y-axes
    master_school_names = sorted(schools["school"].tolist())
    master_idx = {name: i for i, name in enumerate(master_school_names)}

    # -- School markers --
    school_fg = folium.FeatureGroup(name="Schools", show=True)
    for _, row in schools.iterrows():
        folium.CircleMarker(
            location=[row["lat"], row["lon"]],
            radius=6,
            color="#333333",
            weight=2,
            fillColor="#2196F3",
            fillOpacity=1.0,
            popup=folium.Popup(
                f"<b>{row['school']}</b><br>{row.get('address', '')}",
                max_width=200,
            ),
            tooltip=row["school"],
        ).add_to(school_fg)
    school_fg.add_to(m)

    # -- Affordable housing layer --
    housing_fg = None
    if affordable_housing is not None and len(affordable_housing) > 0:
        housing_fg = folium.FeatureGroup(name="Affordable Housing", show=False)
        for _, row in affordable_housing.iterrows():
            folium.CircleMarker(
                location=[row.geometry.y, row.geometry.x],
                radius=3, color="#e74c3c", fillColor="#e74c3c", fillOpacity=0.6,
                popup=folium.Popup(
                    f"<b>{row.get('ProjectName', '')}</b><br>"
                    f"Type: {row.get('ProjectType', '')}<br>"
                    f"AMI: {row.get('AMIServed', '')}",
                    max_width=250,
                ),
            ).add_to(housing_fg)
        housing_fg.add_to(m)
        _progress(f"  Added {len(affordable_housing)} affordable housing markers")

    # -- Custom control panel replaces Folium LayerControl --

    # -- Title --
    title_html = """
    <div style="position: fixed; top: 10px; left: 50%; transform: translateX(-50%);
                z-index: 1000; background-color: white; padding: 10px 20px;
                border-radius: 5px; box-shadow: 2px 2px 5px rgba(0,0,0,0.3);">
        <h3 style="margin: 0;">CHCCS Socioeconomic Analysis by Attendance Zone</h3>
        <p style="margin: 5px 0 0 0; font-size: 12px; color: #666;">
            Census ACS 2022 5-Year &bull; Use controls at right to explore
            &bull; Scroll down for summary plots
        </p>
    </div>
    """
    m.get_root().html.add_child(folium.Element(title_html))

    # -- Unified dot-density layer with custom control panel --
    if dot_fg is not None:
        import json as _json
        import matplotlib.colors as mcolors

        dot_data = racial_dots["dots"]          # [[lat, lon, raceIdx, blockIdx], ...]
        block_geoids = racial_dots["block_geoids"]
        n_blocks = racial_dots["n_blocks"]

        _progress(f"  Adding {len(dot_data):,} population dot markers (unified layer) ...")

        # ── Build block_colors and block_values from enriched_blocks ──
        block_colors = [["#cccccc"] * len(METRIC_DOT_SPECS) for _ in range(n_blocks)]
        block_values = [[None] * len(METRIC_DOT_SPECS) for _ in range(n_blocks)]
        metric_legends = {}
        metric_ranges = []  # [(vmin, vmax), ...] for histogram axis scaling

        if enriched_blocks is not None and n_blocks > 0:
            eb_lookup = enriched_blocks.set_index("GEOID20") if "GEOID20" in enriched_blocks.columns else None

            for metric_idx, (metric_col, display_name, cmap_name, prefix, suffix, fmt) in enumerate(METRIC_DOT_SPECS):
                vals = enriched_blocks[metric_col].dropna() if metric_col in enriched_blocks.columns else pd.Series(dtype=float)
                if len(vals) > 0:
                    vmin = float(vals.quantile(0.05))
                    vmax = float(vals.quantile(0.95))
                else:
                    vmin, vmax = 0.0, 1.0
                if vmax <= vmin:
                    vmax = vmin + 1

                metric_ranges.append((round(vmin, 2), round(vmax, 2)))

                norm = mcolors.Normalize(vmin=vmin, vmax=vmax)
                cmap = plt.get_cmap(cmap_name)

                if eb_lookup is not None:
                    for bidx in range(n_blocks):
                        geoid = block_geoids[bidx]
                        if geoid in eb_lookup.index:
                            val = eb_lookup.at[geoid, metric_col] if metric_col in eb_lookup.columns else np.nan
                            if not pd.isna(val):
                                block_colors[bidx][metric_idx] = mcolors.rgb2hex(cmap(norm(val)))
                                block_values[bidx][metric_idx] = round(float(val), 2)

                metric_legends[display_name] = _build_legend_html(
                    display_name, cmap_name, vmin, vmax,
                    fmt=fmt, prefix=prefix, suffix=suffix,
                )

            _progress(f"  Pre-computed colors for {len(METRIC_DOT_SPECS)} metrics × {n_blocks} blocks")

        # ── Dot → zone spatial joins (all zone types) ──
        # Build dot-point GeoDataFrame once, reuse for every zone type
        all_dot_zones = []   # list of int-lists, one per zone type
        all_zone_names = []  # list of name-lists, one per zone type

        pts_gdf = None
        if len(dot_data) > 0 and len(zone_types) > 0:
            pts_gdf = gpd.GeoDataFrame(
                geometry=gpd.points_from_xy(
                    [d[1] for d in dot_data],
                    [d[0] for d in dot_data],
                ),
                crs=CRS_WGS84,
            )

        zone_type_is_multi = []  # track which zone types are multi-school
        for zt in zone_types:
            zt_names = zt["names"]
            all_zone_names.append(zt_names)
            is_multi = zt.get("is_multi", False)
            zone_type_is_multi.append(is_multi)

            if pts_gdf is not None and len(zt_names) > 0:
                zt_gdf_wgs = zt["gdf"].to_crs(CRS_WGS84)
                joined = gpd.sjoin(
                    pts_gdf, zt_gdf_wgs[["school", "geometry"]],
                    how="left", predicate="within",
                )

                if is_multi:
                    # Multi-school: a dot can be in multiple overlapping isochrones
                    grouped = joined.groupby(joined.index)["school"].apply(list)
                    indices = []
                    for i in range(len(dot_data)):
                        if i in grouped.index:
                            idx_list = [master_idx[s] for s in grouped.loc[i]
                                        if pd.notna(s) and s in master_idx]
                            indices.append(idx_list or [])
                        else:
                            indices.append([])
                    n_assigned = sum(1 for z in indices if len(z) > 0)
                    _progress(f"  Assigned {n_assigned:,} dots to {len(zt_names)} {zt['label']} zones (multi)")
                else:
                    joined = joined[~joined.index.duplicated(keep="first")]
                    # Map to master school indices (not zone-local indices)
                    indices = joined["school"].map(master_idx).fillna(-1).astype(int).tolist()
                    n_assigned = sum(1 for z in indices if z >= 0)
                    _progress(f"  Assigned {n_assigned:,} dots to {len(zt_names)} {zt['label']} zones")
            else:
                if is_multi:
                    indices = [[] for _ in range(len(dot_data))]
                else:
                    indices = [-1] * len(dot_data)

            all_dot_zones.append(indices)

        # Backward-compat aliases for first zone type
        dot_zone_indices = all_dot_zones[0] if all_dot_zones else [-1] * len(dot_data)
        zone_names_for_js = all_zone_names[0] if all_zone_names else []

        # ── Race legend HTML ──
        legend_items = "".join(
            f'<span style="display:inline-block; width:10px; height:10px; '
            f'background:{color}; border-radius:50%; margin-right:4px;"></span>'
            f'{label}&nbsp;&nbsp;'
            for race_key, (color, label) in RACE_CATEGORIES.items()
        )
        race_legend_html = (
            f'<div style="padding: 6px 10px; background: white; border-radius: 4px;'
            f' box-shadow: 0 1px 4px rgba(0,0,0,0.3); max-width: 300px; font-size: 11px;">'
            f'<b>Race/Ethnicity</b> (1 dot = {dots_per_person} '
            f'{"person" if dots_per_person == 1 else "people"})<br>'
            f'{legend_items}</div>'
        )

        all_legends = {"Race/Ethnicity": race_legend_html}
        all_legends.update(metric_legends)
        # Housing legend
        housing_legend_html = (
            '<div style="padding: 6px 10px; background: white; border-radius: 4px;'
            ' box-shadow: 0 1px 4px rgba(0,0,0,0.3); max-width: 200px; font-size: 11px;">'
            '<b>Affordable Housing</b><br>'
            '<span style="display:inline-block; width:10px; height:10px; '
            'background:#e74c3c; border-radius:50%; margin-right:4px;"></span>'
            'Housing Unit</div>'
        )
        all_legends["Affordable Housing"] = housing_legend_html

        # ── JS data serialization ──
        dot_fg_name = dot_fg.get_name()
        map_name = m.get_name()

        race_colors_list = [color for color, label in RACE_CATEGORIES.values()]
        metric_names = ["Race/Ethnicity"] + [spec[1] for spec in METRIC_DOT_SPECS] + ["Affordable Housing"]
        metric_prefixes = [""] + [spec[3] for spec in METRIC_DOT_SPECS] + [""]
        metric_suffixes = [""] + [spec[4] for spec in METRIC_DOT_SPECS] + [""]

        # Pre-compute affordable housing counts per school per zone type
        housing_counts = {}  # {zone_type_idx: {school_idx: count}}
        if affordable_housing is not None and len(affordable_housing) > 0:
            ah_gdf = affordable_housing.to_crs(CRS_WGS84)
            for zi, zt in enumerate(zone_types):
                zt_gdf_wgs = zt["gdf"].to_crs(CRS_WGS84)
                try:
                    joined = gpd.sjoin(ah_gdf, zt_gdf_wgs[["school", "geometry"]], predicate="within")
                    if zt.get("is_multi", False):
                        counts = joined.groupby("school").size().to_dict()
                    else:
                        joined = joined[~joined.index.duplicated(keep="first")]
                        counts = joined.groupby("school").size().to_dict()
                except Exception:
                    counts = {}
                housing_counts[zi] = [counts.get(s, 0) for s in master_school_names]
        else:
            for zi in range(len(zone_types)):
                housing_counts[zi] = [0] * len(master_school_names)

        # Metric radio button labels
        radio_html = ""
        for i, name in enumerate(metric_names):
            checked = ' checked' if i == 1 else ''
            radio_html += (
                f'<label style="display:block;margin:1px 0;cursor:pointer;">'
                f'<input type="radio" name="metric" value="{i}"{checked}> '
                f'{name}</label>'
            )

        # Zone-type radio button labels
        zone_type_radio_html = ""
        for zi, zt in enumerate(zone_types):
            checked = ' checked' if zi == 0 else ''
            zone_type_radio_html += (
                f'<label style="display:block;margin:1px 0;cursor:pointer;">'
                f'<input type="radio" name="zonetype" value="{zi}"{checked}> '
                f'{zt["label"]}</label>'
            )

        data_js = _json.dumps(dot_data, separators=(",", ":"))
        race_colors_js = _json.dumps(race_colors_list)
        block_colors_js = _json.dumps(block_colors, separators=(",", ":"))
        block_values_js = _json.dumps(block_values, separators=(",", ":"))
        legends_js = _json.dumps(all_legends, separators=(",", ":"))
        metric_names_js = _json.dumps(metric_names, separators=(",", ":"))
        metric_prefixes_js = _json.dumps(metric_prefixes, separators=(",", ":"))
        metric_suffixes_js = _json.dumps(metric_suffixes, separators=(",", ":"))
        # Add a dummy range for affordable housing (not used, but keeps indices safe)
        metric_ranges.append((0, 1))
        metric_ranges_js = _json.dumps(metric_ranges, separators=(",", ":"))

        # Multi-zone-type arrays
        all_dot_zones_js = _json.dumps(all_dot_zones, separators=(",", ":"))
        all_zone_names_js = _json.dumps(all_zone_names, separators=(",", ":"))
        master_schools_js = _json.dumps(master_school_names, separators=(",", ":"))
        zone_fg_names_js = "[" + ",".join(zt["fg_name"] for zt in zone_types) + "]"
        zone_type_labels_js = _json.dumps([zt["label"] for zt in zone_types], separators=(",", ":"))
        zone_type_is_multi_js = _json.dumps(zone_type_is_multi, separators=(",", ":"))
        school_color_map_js = _json.dumps(school_color_map, separators=(",", ":"))
        housing_counts_js = _json.dumps(housing_counts, separators=(",", ":"))
        housing_fg_name_js = housing_fg.get_name() if housing_fg is not None else "null"

        # BG / Block layer JS refs
        bg_layers_js = "[" + ",".join(bg_fg_names) + "]"
        blk_layers_js = "[" + ",".join(blk_fg_names) + "]"

        custom_ui = f"""
        <style>
            #ctrl-panel {{
                position: fixed; top: 60px; right: 10px; z-index: 1001;
                width: 200px; background: white; border-radius: 6px;
                box-shadow: 0 2px 8px rgba(0,0,0,0.25); font-size: 12px;
                padding: 10px; max-height: calc(100vh - 80px); overflow-y: auto;
            }}
            #ctrl-panel .ctrl-section {{
                margin-bottom: 8px; padding-bottom: 6px;
                border-bottom: 1px solid #eee;
            }}
            #ctrl-panel .ctrl-section:last-child {{
                margin-bottom: 0; padding-bottom: 0; border-bottom: none;
            }}
            #ctrl-panel b {{
                font-size: 11px; text-transform: uppercase; color: #555;
                letter-spacing: 0.5px;
            }}
            #ctrl-panel label {{
                font-size: 11px; line-height: 1.5;
            }}
            #ctrl-panel input[type="radio"],
            #ctrl-panel input[type="checkbox"] {{
                margin-right: 4px; vertical-align: middle;
            }}
            #zone-strip {{
                position: relative; z-index: 1001;
                background: rgba(255,255,255,0.96);
                border-top: 2px solid #999; overflow-x: hidden;
                padding: 8px 10px;
                display: flex; flex-wrap: wrap; gap: 8px;
                align-content: flex-start;
                min-height: 60px;
            }}
            .zone-card {{
                flex: 0 0 190px;
                padding: 6px 8px;
                background: #f9f9f9; border-radius: 5px; border: 1px solid #ddd;
            }}
            .zone-card-name {{
                font-weight: bold; font-size: 12px; margin-bottom: 2px;
                white-space: nowrap; overflow: hidden; text-overflow: ellipsis;
            }}
            .zone-card-avg {{
                font-size: 11px; color: #555; margin-bottom: 3px;
                min-height: 16px;
            }}
            .zone-card canvas {{
                display: block;
            }}
            #dot-legend-box {{
                position: fixed; top: 50%; left: 10px; z-index: 1002;
                transform: translateY(-50%);
            }}
            .barplot-title {{
                font-weight: bold; font-size: 12px; text-align: center;
                margin-bottom: 4px; color: #555;
            }}
            /* Scroll layout: map fills viewport, charts flow below */
            html, body {{
                height: auto !important;
                min-height: 100vh;
                overflow-x: hidden;
            }}
            .folium-map {{
                height: 100vh !important;
            }}
        </style>
        <div id="ctrl-panel">
            <div class="ctrl-section">
                <b>Metric</b>
                {radio_html}
            </div>
            <div class="ctrl-section">
                <b>Layers</b>
                <div id="layer-dots-group" style="display:none">
                    <label style="display:block;margin:1px 0;cursor:pointer;">
                        <input type="checkbox" id="chk-dots" checked> Population Dots
                    </label>
                </div>
                <div id="layer-census-group">
                    <label style="display:block;margin:1px 0;cursor:pointer;">
                        <input type="checkbox" id="chk-bg"> Raw Data (Block Group)
                    </label>
                    <label style="display:block;margin:1px 0;cursor:pointer;">
                        <input type="checkbox" id="chk-blk"> Dasymetric (Block)
                    </label>
                </div>
                <div id="layer-housing-group" style="display:none">
                    <label style="display:block;margin:1px 0;cursor:pointer;">
                        <input type="checkbox" id="chk-housing" checked> Housing Units
                    </label>
                </div>
            </div>
            <div class="ctrl-section">
                <b>Community Zones</b>
                <div id="zone-type-radios" style="margin-left:4px">
                    {zone_type_radio_html}
                </div>
            </div>
        </div>
        <div id="zone-strip"></div>
        <div id="dot-legend-box"></div>
        <script>
        document.addEventListener('DOMContentLoaded', function() {{
            var map = {map_name};
            var dotFg = {dot_fg_name};
            var zoneFgs = {zone_fg_names_js};
            var bgLayers = {bg_layers_js};
            var blkLayers = {blk_layers_js};
            var dots = {data_js};
            var allDotZones = {all_dot_zones_js};
            var allZoneNames = {all_zone_names_js};
            var zoneTypeIsMulti = {zone_type_is_multi_js};
            var housingFg = {housing_fg_name_js};
            var housingCounts = {housing_counts_js};
            var masterSchools = {master_schools_js};
            var schoolColorMap = {school_color_map_js};
            var blockColors = {block_colors_js};
            var blockValues = {block_values_js};
            var raceColors = {race_colors_js};
            var legends = {legends_js};
            var metricNames = {metric_names_js};
            var metricPrefixes = {metric_prefixes_js};
            var metricSuffixes = {metric_suffixes_js};
            var metricRanges = {metric_ranges_js};
            var markers = [];
            var currentMetric = 1;
            var currentZoneType = 0;

            // ── Create dot markers ──
            for (var i = 0; i < dots.length; i++) {{
                var c = blockColors[dots[i][3]][0];
                var marker = L.circleMarker([dots[i][0], dots[i][1]], {{
                    radius: 1.5, fillColor: c, color: c, weight: 0, fillOpacity: 0.7
                }});
                marker.addTo(dotFg);
                markers.push(marker);
            }}
            if (!map.hasLayer(dotFg)) dotFg.addTo(map);

            var legendBox = document.getElementById('dot-legend-box');
            var zoneStrip = document.getElementById('zone-strip');

            // Move zone-strip after the map so it scrolls below
            var mapDiv = document.querySelector('.folium-map');
            if (mapDiv) mapDiv.parentElement.appendChild(zoneStrip);

            // ── Zone card rebuild ──
            var currentLayout = '';  // 'histogram', 'barplot', or 'race'
            function rebuildZoneCards() {{
                zoneStrip.innerHTML = '';
                var isRace = (currentMetric === 0);
                var lastMetricIdx2 = metricNames.length - 1;
                var isHousing2 = (currentMetric === lastMetricIdx2 && metricNames[lastMetricIdx2] === 'Affordable Housing');
                var isIncome = (!isRace && !isHousing2 && metricPrefixes[currentMetric] === '$');

                if (isRace) {{
                    currentLayout = 'race';
                    // Race placeholder — just one card
                    var card = document.createElement('div');
                    card.className = 'zone-card';
                    card.innerHTML = '<div class="zone-card-avg" style="padding:10px;">Select a metric to see zone distributions</div>';
                    zoneStrip.appendChild(card);
                }} else if (isHousing2) {{
                    currentLayout = 'barplot';
                    zoneStrip.innerHTML =
                        '<div id="barplot-panel" style="display:flex;gap:12px;width:100%;">' +
                        '  <div style="flex:1;">' +
                        '    <div class="barplot-title">Affordable Housing Units</div>' +
                        '    <canvas id="bar-left" width="460" height="320"></canvas>' +
                        '  </div>' +
                        '  <div style="flex:1;">' +
                        '    <canvas id="bar-right" width="460" height="320"></canvas>' +
                        '  </div>' +
                        '</div>';
                }} else if (isIncome) {{
                    currentLayout = 'histogram';
                    for (var i = 0; i < masterSchools.length; i++) {{
                        var card = document.createElement('div');
                        card.className = 'zone-card';
                        card.innerHTML =
                            '<div class="zone-card-name">' + masterSchools[i] + '</div>' +
                            '<div class="zone-card-avg" id="zone-avg-' + i + '"></div>' +
                            '<canvas id="zone-hist-' + i + '" width="170" height="95"></canvas>';
                        zoneStrip.appendChild(card);
                    }}
                }} else {{
                    currentLayout = 'barplot';
                    zoneStrip.innerHTML =
                        '<div id="barplot-panel" style="display:flex;gap:12px;width:100%;">' +
                        '  <div style="flex:1;">' +
                        '    <div class="barplot-title">Mean %</div>' +
                        '    <canvas id="bar-left" width="460" height="320"></canvas>' +
                        '  </div>' +
                        '  <div style="flex:1;">' +
                        '    <div class="barplot-title">Estimated Population</div>' +
                        '    <canvas id="bar-right" width="460" height="320"></canvas>' +
                        '  </div>' +
                        '</div>';
                }}
            }}

            // Build initial zone cards
            rebuildZoneCards();

            // ── Core functions ──
            function recolorDots() {{
                if (currentMetric === 0) {{
                    for (var i = 0; i < markers.length; i++) {{
                        var c = raceColors[dots[i][2]];
                        markers[i].setStyle({{fillColor: c, color: c}});
                    }}
                }} else {{
                    var mi = currentMetric - 1;
                    for (var i = 0; i < markers.length; i++) {{
                        var c = blockColors[dots[i][3]][mi];
                        markers[i].setStyle({{fillColor: c, color: c}});
                    }}
                }}
            }}

            function updateLegend() {{
                legendBox.innerHTML = legends[metricNames[currentMetric]] || '';
            }}

            function updateBGLayer() {{
                for (var i = 0; i < bgLayers.length; i++) {{
                    if (map.hasLayer(bgLayers[i])) map.removeLayer(bgLayers[i]);
                }}
                var chk = document.getElementById('chk-bg');
                if (chk.checked && currentMetric > 0) {{
                    var idx = currentMetric - 1;
                    if (idx < bgLayers.length) bgLayers[idx].addTo(map);
                }}
            }}

            function updateBlkLayer() {{
                for (var i = 0; i < blkLayers.length; i++) {{
                    if (map.hasLayer(blkLayers[i])) map.removeLayer(blkLayers[i]);
                }}
                var chk = document.getElementById('chk-blk');
                if (chk.checked && currentMetric > 0) {{
                    var idx = currentMetric - 1;
                    if (idx < blkLayers.length) blkLayers[idx].addTo(map);
                }}
            }}

            function toggleDots() {{
                var chk = document.getElementById('chk-dots');
                if (chk.checked) {{
                    if (!map.hasLayer(dotFg)) dotFg.addTo(map);
                }} else {{
                    if (map.hasLayer(dotFg)) map.removeLayer(dotFg);
                }}
            }}

            function switchZoneType(idx) {{
                // Hide all zone FeatureGroups
                for (var i = 0; i < zoneFgs.length; i++) {{
                    if (map.hasLayer(zoneFgs[i])) map.removeLayer(zoneFgs[i]);
                }}
                currentZoneType = idx;
                zoneFgs[idx].addTo(map);
                zoneStrip.style.display = 'flex';
                rebuildZoneCards();
                updateHistograms();
            }}

            function toggleZones() {{
                // Zones are always shown; toggle via zone-type radios
            }}

            function fmtAxis(v, prefix, suffix) {{
                if (prefix === '$') {{
                    if (v >= 1000) return '$' + (v/1000).toFixed(0) + 'k';
                    return '$' + v.toFixed(0);
                }}
                return prefix + v.toFixed(0) + suffix;
            }}

            function drawBarplot(canvasId, labels, values, fmt) {{
                var canvas = document.getElementById(canvasId);
                if (!canvas) return;
                var ctx = canvas.getContext('2d');
                var W = canvas.width, H = canvas.height;
                ctx.clearRect(0, 0, W, H);

                // Build sorted index array (descending by value)
                var idx = [];
                for (var i = 0; i < labels.length; i++) idx.push(i);
                idx.sort(function(a, b) {{ return values[b] - values[a]; }});

                var n = labels.length;
                var leftPad = 100;  // space for labels
                var rightPad = 60;  // space for value labels
                var topPad = 4;
                var botPad = 4;
                var barArea = W - leftPad - rightPad;
                var barH = Math.max(4, Math.floor((H - topPad - botPad) / n) - 3);
                var gap = Math.max(1, Math.floor(((H - topPad - botPad) - barH * n) / Math.max(n - 1, 1)));

                var maxVal = 0;
                for (var i = 0; i < values.length; i++) {{
                    if (values[i] > maxVal) maxVal = values[i];
                }}
                if (maxVal === 0) maxVal = 1;

                // Short name helper
                function shortName(s) {{
                    return s.replace(' Elementary', '').replace(' Bilingue', '');
                }}

                ctx.font = '11px sans-serif';
                ctx.textBaseline = 'middle';

                for (var rank = 0; rank < n; rank++) {{
                    var si = idx[rank];
                    var y = topPad + rank * (barH + gap);
                    var barW = (values[si] / maxVal) * barArea;
                    if (barW < 0) barW = 0;

                    ctx.fillStyle = '#6baed6';
                    ctx.fillRect(leftPad, y, barW, barH);

                    // School label (left)
                    ctx.fillStyle = '#333';
                    ctx.font = '11px sans-serif';
                    ctx.textAlign = 'right';
                    ctx.fillText(shortName(labels[si]), leftPad - 6, y + barH / 2);

                    // Value label (right of bar)
                    ctx.textAlign = 'left';
                    ctx.fillStyle = '#333';
                    ctx.font = '10px sans-serif';
                    var lbl;
                    if (fmt === 'pct') {{
                        lbl = values[si].toFixed(1) + '%';
                    }} else if (fmt === 'count') {{
                        lbl = values[si].toLocaleString();
                    }} else if (fmt === 'dollar') {{
                        lbl = values[si] >= 1000 ? '$' + (values[si]/1000).toFixed(0) + 'k' : '$' + values[si].toFixed(0);
                    }} else {{
                        lbl = values[si].toFixed(1);
                    }}
                    ctx.fillText(lbl, leftPad + barW + 4, y + barH / 2);
                }}
                ctx.textAlign = 'left';
            }}

            function drawHistogram(canvasId, values, vmin, vmax, prefix, suffix, globalMax) {{
                var canvas = document.getElementById(canvasId);
                if (!canvas) return;
                var ctx = canvas.getContext('2d');
                ctx.clearRect(0, 0, canvas.width, canvas.height);

                if (values.length === 0) {{
                    ctx.fillStyle = '#999';
                    ctx.font = '11px sans-serif';
                    ctx.fillText('No data', 10, 50);
                    return;
                }}

                var nBins = 15;
                var bins = new Array(nBins).fill(0);
                var binWidth = (vmax - vmin) / nBins;
                if (binWidth <= 0) return;
                for (var k = 0; k < values.length; k++) {{
                    var b = Math.min(Math.floor((values[k] - vmin) / binWidth), nBins - 1);
                    if (b >= 0) bins[b]++;
                }}
                var maxCount = globalMax > 0 ? globalMax : Math.max.apply(null, bins);
                if (maxCount === 0) return;

                var axisH = 14;
                var topPad = 14;
                var barW = canvas.width / nBins;
                var chartH = canvas.height - axisH - topPad;
                for (var i = 0; i < nBins; i++) {{
                    var barH = (bins[i] / maxCount) * chartH;
                    ctx.fillStyle = '#6baed6';
                    ctx.fillRect(i * barW, topPad + chartH - barH, barW - 1, barH);
                }}

                // X-axis labels: min, mid, max
                ctx.fillStyle = '#666';
                ctx.font = '9px sans-serif';
                var yLbl = topPad + chartH + axisH - 2;
                var mid = (vmin + vmax) / 2;
                ctx.textAlign = 'left';
                ctx.fillText(fmtAxis(vmin, prefix, suffix), 0, yLbl);
                ctx.textAlign = 'center';
                ctx.fillText(fmtAxis(mid, prefix, suffix), canvas.width / 2, yLbl);
                ctx.textAlign = 'right';
                ctx.fillText(fmtAxis(vmax, prefix, suffix), canvas.width, yLbl);
                ctx.textAlign = 'left';

                // Red median line
                var sorted = values.slice().sort(function(a, b) {{ return a - b; }});
                var median = sorted[Math.floor(sorted.length / 2)];
                var medX = ((median - vmin) / (vmax - vmin)) * canvas.width;
                medX = Math.max(2, Math.min(medX, canvas.width - 2));
                ctx.strokeStyle = '#d32f2f';
                ctx.lineWidth = 2;
                ctx.beginPath();
                ctx.moveTo(medX, topPad);
                ctx.lineTo(medX, topPad + chartH);
                ctx.stroke();

                // Median label
                ctx.fillStyle = '#d32f2f';
                ctx.font = '10px sans-serif';
                var lbl = 'med: ' + fmtAxis(median, prefix, suffix);
                var lblX = medX + 3;
                if (lblX + ctx.measureText(lbl).width > canvas.width) lblX = medX - ctx.measureText(lbl).width - 3;
                ctx.fillText(lbl, lblX, topPad - 3);
            }}

            function updateHistograms() {{
                var dotZones = allDotZones[currentZoneType];
                if (currentMetric === 0) return;  // race mode: nothing to draw

                var lastMetricIdx = metricNames.length - 1;
                var isHousing = (currentMetric === lastMetricIdx && metricNames[lastMetricIdx] === 'Affordable Housing');

                if (isHousing) {{
                    // Affordable Housing: show barplot of unit counts per school
                    var counts = housingCounts[String(currentZoneType)] || [];
                    drawBarplot('bar-left', masterSchools, counts, 'count');
                    // Right chart: leave empty or reuse
                    var canvas = document.getElementById('bar-right');
                    if (canvas) {{
                        var ctx = canvas.getContext('2d');
                        ctx.clearRect(0, 0, canvas.width, canvas.height);
                    }}
                    return;
                }}

                var mi = currentMetric - 1;
                var vmin = metricRanges[mi][0];
                var vmax = metricRanges[mi][1];
                var pfx = metricPrefixes[currentMetric];
                var sfx = metricSuffixes[currentMetric];
                var isIncome = (pfx === '$');
                var nSchools = masterSchools.length;

                // Collect per-school values (always all 11 schools via master indices)
                var schoolVals = [];
                var schoolDotCounts = [];
                for (var si = 0; si < nSchools; si++) {{
                    schoolVals.push([]);
                    schoolDotCounts.push(0);
                }}
                var isMulti = zoneTypeIsMulti[currentZoneType];
                for (var i = 0; i < dots.length; i++) {{
                    if (isMulti) {{
                        // Multi-school: dotZones[i] is an array of school indices
                        var siList = dotZones[i];
                        for (var j = 0; j < siList.length; j++) {{
                            var si = siList[j];
                            if (si >= 0 && si < nSchools) {{
                                schoolDotCounts[si]++;
                                var v = blockValues[dots[i][3]][mi];
                                if (v !== null) schoolVals[si].push(v);
                            }}
                        }}
                    }} else {{
                        var si = dotZones[i];
                        if (si >= 0 && si < nSchools) {{
                            schoolDotCounts[si]++;
                            var v = blockValues[dots[i][3]][mi];
                            if (v !== null) schoolVals[si].push(v);
                        }}
                    }}
                }}

                if (isIncome) {{
                    // Histogram mode — per-school cards
                    var nBins = 15;
                    var binWidth = (vmax - vmin) / nBins;
                    var globalMaxBin = 0;
                    for (var si = 0; si < nSchools; si++) {{
                        if (schoolVals[si].length > 0 && binWidth > 0) {{
                            var bins = new Array(nBins).fill(0);
                            for (var k = 0; k < schoolVals[si].length; k++) {{
                                var b = Math.min(Math.floor((schoolVals[si][k] - vmin) / binWidth), nBins - 1);
                                if (b >= 0) bins[b]++;
                            }}
                            var mx = Math.max.apply(null, bins);
                            if (mx > globalMaxBin) globalMaxBin = mx;
                        }}
                    }}
                    for (var si = 0; si < nSchools; si++) {{
                        drawHistogram('zone-hist-' + si, schoolVals[si], vmin, vmax, pfx, sfx, globalMaxBin);
                        var avgEl = document.getElementById('zone-avg-' + si);
                        if (avgEl) {{
                            if (schoolVals[si].length > 0) {{
                                var sum = 0;
                                for (var k = 0; k < schoolVals[si].length; k++) sum += schoolVals[si][k];
                                var avg = sum / schoolVals[si].length;
                                avgEl.textContent = 'Avg: ' + pfx + avg.toFixed(1) + sfx + ' (n=' + schoolVals[si].length + ')';
                            }} else {{
                                avgEl.textContent = 'No data';
                            }}
                        }}
                    }}
                }} else {{
                    // Barplot mode — two side-by-side barplots
                    var meanPcts = [], estCounts = [];
                    for (var si = 0; si < nSchools; si++) {{
                        var mean = 0;
                        if (schoolVals[si].length > 0) {{
                            var sum = 0;
                            for (var k = 0; k < schoolVals[si].length; k++) sum += schoolVals[si][k];
                            mean = sum / schoolVals[si].length;
                        }}
                        meanPcts.push(mean);
                        estCounts.push(Math.round(schoolDotCounts[si] * mean / 100));
                    }}
                    drawBarplot('bar-left', masterSchools, meanPcts, 'pct');
                    drawBarplot('bar-right', masterSchools, estCounts, 'count');
                }}
            }}

            function removeHousingLayer() {{
                if (typeof housingFg !== 'undefined' && map.hasLayer(housingFg)) map.removeLayer(housingFg);
            }}

            function updateMetric(idx) {{
                var prevLayout = currentLayout;
                currentMetric = idx;
                var lastMetricIdx = metricNames.length - 1;
                var isRace = (idx === 0);
                var isHousing = (idx === lastMetricIdx && metricNames[lastMetricIdx] === 'Affordable Housing');
                var isIncome = (!isRace && !isHousing && metricPrefixes[idx] === '$');
                var needLayout = isRace ? 'race' : (isHousing ? 'barplot' : (isIncome ? 'histogram' : 'barplot'));
                if (needLayout !== prevLayout) {{
                    rebuildZoneCards();
                }}

                // Show/hide layer control groups
                document.getElementById('layer-dots-group').style.display = isRace ? 'block' : 'none';
                document.getElementById('layer-census-group').style.display = (!isRace && !isHousing) ? 'block' : 'none';
                document.getElementById('layer-housing-group').style.display = isHousing ? 'block' : 'none';

                // Toggle map layers accordingly
                if (isRace) {{
                    // Show dots, hide bg/blk/housing
                    if (document.getElementById('chk-dots').checked && !map.hasLayer(dotFg)) dotFg.addTo(map);
                    for (var i = 0; i < bgLayers.length; i++) {{
                        if (map.hasLayer(bgLayers[i])) map.removeLayer(bgLayers[i]);
                    }}
                    for (var i = 0; i < blkLayers.length; i++) {{
                        if (map.hasLayer(blkLayers[i])) map.removeLayer(blkLayers[i]);
                    }}
                    removeHousingLayer();
                    recolorDots();
                }} else if (isHousing) {{
                    // Show housing, hide dots/bg/blk
                    if (map.hasLayer(dotFg)) map.removeLayer(dotFg);
                    for (var i = 0; i < bgLayers.length; i++) {{
                        if (map.hasLayer(bgLayers[i])) map.removeLayer(bgLayers[i]);
                    }}
                    for (var i = 0; i < blkLayers.length; i++) {{
                        if (map.hasLayer(blkLayers[i])) map.removeLayer(blkLayers[i]);
                    }}
                    if (typeof housingFg !== 'undefined' && document.getElementById('chk-housing').checked) {{
                        housingFg.addTo(map);
                    }}
                }} else {{
                    // Census metric: show bg/blk per checkboxes, hide dots/housing
                    if (map.hasLayer(dotFg)) map.removeLayer(dotFg);
                    removeHousingLayer();
                    recolorDots();
                    updateBGLayer();
                    updateBlkLayer();
                }}

                updateLegend();
                updateHistograms();
            }}

            // ── Event listeners ──
            var radios = document.querySelectorAll('input[name="metric"]');
            for (var r = 0; r < radios.length; r++) {{
                radios[r].addEventListener('change', function() {{
                    updateMetric(parseInt(this.value));
                }});
            }}

            var ztRadios = document.querySelectorAll('input[name="zonetype"]');
            for (var r = 0; r < ztRadios.length; r++) {{
                ztRadios[r].addEventListener('change', function() {{
                    switchZoneType(parseInt(this.value));
                }});
            }}

            document.getElementById('chk-dots').addEventListener('change', toggleDots);
            document.getElementById('chk-bg').addEventListener('change', function() {{ updateBGLayer(); }});
            document.getElementById('chk-blk').addEventListener('change', function() {{ updateBlkLayer(); }});
            document.getElementById('chk-housing').addEventListener('change', function() {{
                if (typeof housingFg !== 'undefined') {{
                    if (this.checked) {{ housingFg.addTo(map); }} else {{ if (map.hasLayer(housingFg)) map.removeLayer(housingFg); }}
                }}
            }});
            // Zones always visible — toggled by zone-type radios only

            // ── Multi-school click popup (ray-casting point-in-polygon) ──
            function pointInRing(lat, lng, ring) {{
                var inside = false;
                for (var i = 0, j = ring.length - 1; i < ring.length; j = i++) {{
                    var yi = ring[i][1], xi = ring[i][0];
                    var yj = ring[j][1], xj = ring[j][0];
                    if ((yi > lat) !== (yj > lat) &&
                        lng < (xj - xi) * (lat - yi) / (yj - yi) + xi) {{
                        inside = !inside;
                    }}
                }}
                return inside;
            }}
            function pointInGeometry(lat, lng, geom) {{
                if (geom.type === 'Polygon') {{
                    if (!pointInRing(lat, lng, geom.coordinates[0])) return false;
                    for (var h = 1; h < geom.coordinates.length; h++) {{
                        if (pointInRing(lat, lng, geom.coordinates[h])) return false;
                    }}
                    return true;
                }} else if (geom.type === 'MultiPolygon') {{
                    for (var p = 0; p < geom.coordinates.length; p++) {{
                        var poly = geom.coordinates[p];
                        if (pointInRing(lat, lng, poly[0])) {{
                            var inHole = false;
                            for (var h = 1; h < poly.length; h++) {{
                                if (pointInRing(lat, lng, poly[h])) {{ inHole = true; break; }}
                            }}
                            if (!inHole) return true;
                        }}
                    }}
                }}
                return false;
            }}
            function findSchoolsAt(lat, lng) {{
                var schools = [];
                zoneFgs[currentZoneType].eachLayer(function(geoJsonGroup) {{
                    geoJsonGroup.eachLayer(function(layer) {{
                        if (layer.feature && layer.feature.geometry) {{
                            if (pointInGeometry(lat, lng, layer.feature.geometry)) {{
                                var sn = layer.feature.properties.school;
                                if (sn && schools.indexOf(sn) === -1) schools.push(sn);
                            }}
                        }}
                    }});
                }});
                schools.sort();
                return schools;
            }}
            function schoolsHtml(schools) {{
                var html = '<b>Schools within range:</b><br>';
                for (var i = 0; i < schools.length; i++) {{
                    var c = schoolColorMap[schools[i]] || '#888';
                    html += '<span style="color:' + c + '">&#9632;</span> ' + schools[i] + '<br>';
                }}
                return html;
            }}
            map.on('click', function(e) {{
                if (!zoneTypeIsMulti[currentZoneType]) return;
                var schools = findSchoolsAt(e.latlng.lat, e.latlng.lng);
                if (schools.length > 0) {{
                    L.popup().setLatLng(e.latlng).setContent(schoolsHtml(schools)).openOn(map);
                }}
            }});
            var multiTooltip = L.tooltip({{sticky: false, direction: 'top', offset: [0, -10]}});
            var multiTooltipOpen = false;
            map.on('mousemove', function(e) {{
                if (!zoneTypeIsMulti[currentZoneType]) {{
                    if (multiTooltipOpen) {{ map.closeTooltip(multiTooltip); multiTooltipOpen = false; }}
                    return;
                }}
                var schools = findSchoolsAt(e.latlng.lat, e.latlng.lng);
                if (schools.length > 0) {{
                    multiTooltip.setLatLng(e.latlng).setContent(schoolsHtml(schools));
                    if (!multiTooltipOpen) {{ multiTooltip.addTo(map); multiTooltipOpen = true; }}
                }} else if (multiTooltipOpen) {{
                    map.closeTooltip(multiTooltip);
                    multiTooltipOpen = false;
                }}
            }});
            map.on('mouseout', function() {{
                if (multiTooltipOpen) {{ map.closeTooltip(multiTooltip); multiTooltipOpen = false; }}
            }});

            // ── Initial state ──
            // Trigger updateMetric to set layer visibility for initial metric
            updateMetric(currentMetric);
        }});
        </script>
        """
        m.get_root().html.add_child(folium.Element(custom_ui))

    return m



# ═══════════════════════════════════════════════════════════════════════════
# Section 9: Static charts
# ═══════════════════════════════════════════════════════════════════════════

def create_comparison_charts(zone_demographics: pd.DataFrame):
    """Create static bar charts comparing demographics across attendance zones."""
    _progress("Creating static comparison charts ...")

    metrics = [
        ("pct_below_185_poverty", "% Below 185% Poverty (FRL Proxy)", "%"),
        ("pct_minority", "% Minority", "%"),
        ("median_hh_income", "Median Household Income", "$"),
        ("pct_renter", "% Renter-Occupied", "%"),
        ("pct_zero_vehicle", "% Zero-Vehicle Households", "%"),
        ("pct_single_parent", "% Single-Parent Families", "%"),
    ]

    for col, title, unit in metrics:
        if col not in zone_demographics.columns:
            continue

        fig, ax = plt.subplots(figsize=(10, 6))
        df_sorted = zone_demographics.sort_values(col, ascending=True)

        # Shorten school names for chart
        labels = [s.replace(" Elementary", "").replace(" Bilingue", "")
                  for s in df_sorted["school"]]

        bars = ax.barh(labels, df_sorted[col], color=BAR_COLOR, edgecolor="white")

        # Value labels
        for bar, val in zip(bars, df_sorted[col]):
            if unit == "$":
                label = f"${val:,.0f}"
            else:
                label = f"{val:.1f}%"
            ax.text(bar.get_width() + (ax.get_xlim()[1] * 0.01), bar.get_y() + bar.get_height() / 2,
                    label, va="center", fontsize=9)

        ax.set_title(title, fontsize=14, fontweight="bold")
        ax.set_xlabel(f"{title}" if unit != "$" else "Dollars", fontsize=11)

        plt.tight_layout()
        plt.subplots_adjust(left=0.28)
        out_path = ASSETS_CHARTS / f"socioeconomic_{col}.png"
        fig.savefig(out_path, dpi=150, bbox_inches="tight")
        plt.close(fig)
        _progress(f"  Saved {out_path.name}")

    # Income distribution chart (district-wide)
    _create_income_distribution_chart(zone_demographics)


def _create_income_distribution_chart(zone_demographics: pd.DataFrame):
    """Create income distribution chart showing district-wide household income."""
    income_brackets = [
        ("income_lt_10k", "<$10k"),
        ("income_10k_15k", "$10-15k"),
        ("income_15k_20k", "$15-20k"),
        ("income_20k_25k", "$20-25k"),
        ("income_25k_30k", "$25-30k"),
        ("income_30k_35k", "$30-35k"),
        ("income_35k_40k", "$35-40k"),
        ("income_40k_45k", "$40-45k"),
        ("income_45k_50k", "$45-50k"),
        ("income_50k_60k", "$50-60k"),
        ("income_60k_75k", "$60-75k"),
        ("income_75k_100k", "$75-100k"),
        ("income_100k_125k", "$100-125k"),
        ("income_125k_150k", "$125-150k"),
        ("income_150k_200k", "$150-200k"),
        ("income_200k_plus", "$200k+"),
    ]

    # Check which columns we have
    available = [(col, label) for col, label in income_brackets
                 if col in zone_demographics.columns]
    if not available:
        _progress("  Skipping income distribution chart (no bracket data)")
        return

    cols = [c for c, _ in available]
    labels = [l for _, l in available]

    # Compute district totals (sum across all zones)
    district_totals = zone_demographics[cols].sum()
    dist_total = district_totals.sum()
    dist_pct = (district_totals / dist_total * 100) if dist_total > 0 else district_totals * 0

    fig, ax = plt.subplots(figsize=(12, 6))
    x = np.arange(len(labels))

    ax.bar(x, dist_pct.values, color=BAR_COLOR, alpha=0.85)

    ax.set_ylabel("% of Households", fontsize=11)
    ax.set_title("Household Income Distribution: CHCCS District",
                 fontsize=14, fontweight="bold")
    ax.set_xticks(x)
    ax.set_xticklabels(labels, rotation=45, ha="right", fontsize=9)

    plt.tight_layout()
    out_path = ASSETS_CHARTS / "socioeconomic_income_distribution.png"
    fig.savefig(out_path, dpi=150, bbox_inches="tight")
    plt.close(fig)
    _progress(f"  Saved {out_path.name}")


# ═══════════════════════════════════════════════════════════════════════════
# Section 10: Documentation generation
# ═══════════════════════════════════════════════════════════════════════════

def generate_methodology_doc(
    zone_demographics: pd.DataFrame,
    bg_profiles: gpd.GeoDataFrame,
):
    """Generate SOCIOECONOMIC_ANALYSIS.md with methodology and results."""
    _progress("Generating methodology documentation ...")

    # Build demographics table
    display_cols = [
        "school", "total_pop", "median_hh_income",
        "pct_below_185_poverty", "pct_minority", "pct_black", "pct_hispanic",
        "pct_renter", "pct_zero_vehicle", "pct_single_parent", "pct_elementary_age",
    ]
    available_cols = [c for c in display_cols if c in zone_demographics.columns]
    table_df = zone_demographics[available_cols].copy()

    # Format the table as markdown (without tabulate dependency)
    try:
        table_md = table_df.to_markdown(index=False, floatfmt=".1f")
    except ImportError:
        # Fallback: build markdown table manually
        headers = "| " + " | ".join(str(c) for c in table_df.columns) + " |"
        sep = "| " + " | ".join("---" for _ in table_df.columns) + " |"
        rows = []
        for _, row in table_df.iterrows():
            vals = []
            for c in table_df.columns:
                v = row[c]
                if isinstance(v, float):
                    vals.append(f"{v:.1f}")
                else:
                    vals.append(str(v))
            rows.append("| " + " | ".join(vals) + " |")
        table_md = "\n".join([headers, sep] + rows)

    doc = f"""# Socioeconomic Analysis: CHCCS Attendance Zones

## Purpose

This analysis provides neighborhood-level socioeconomic data for each CHCCS
elementary school attendance zone. It uses US Census Bureau data to characterize
the populations served by each school, enabling informed discussion about the
equity implications of school closure decisions.

## Data Sources

### ACS 5-Year Estimates (2022, Block Group Level)

**API Endpoint:** `{ACS_BASE_URL}`
**Geography:** Block groups in Orange County, NC (FIPS {STATE_FIPS}{COUNTY_FIPS})

| Census Table | Description | Key Metric |
|---|---|---|
| B01001 | Population by age and sex | % elementary-age children (5-9) |
| B03002 | Hispanic origin by race | Racial/ethnic composition |
| B19013 | Median household income | Income levels by block group |
| B19001 | Household income brackets | Income distribution (16 bins) |
| C17002 | Ratio of income to poverty level | % below 185% poverty (FRL proxy) |
| B25003 | Tenure (owner vs. renter) | % renter-occupied |
| B25044 | Tenure by vehicles available | % zero-vehicle households |
| B11003 | Family type by presence of children | % single-parent families |

### 2020 Decennial Census P.L. 94-171 (Block Level)

**API Endpoint:** `{DECENNIAL_BASE_URL}`
**Geography:** Census blocks in Orange County, NC

| Census Table | Description |
|---|---|
| P1 | Total population by race (7 categories) |
| P2 | Hispanic/Latino origin by race |

Used exclusively for dot-density visualization (highest spatial resolution).

### TIGER/Line Geometries

- **Block groups:** `{TIGER_BG_URL}`
- **Blocks:** `{TIGER_BLOCK_URL}`

### Local Data

- **Attendance zone boundaries:** `data/raw/properties/CHCCS/CHCCS.shp` (dissolved by ENAME field)
- **District boundary:** `data/cache/chccs_district_boundary.gpkg`
- **School locations:** `data/cache/nces_school_locations.csv` (NCES EDGE 2023-24)
- **Residential parcels:** `data/raw/properties/combined_data_polys.gpkg` (for dasymetric dot placement)

## Variable Definitions

| Variable | Census Source | Definition |
|---|---|---|
| `total_pop` | B01001_001E | Total population |
| `pct_elementary_age` | B01001_004E + B01001_028E | % of population aged 5-9 |
| `pct_minority` | 1 - (B03002_003E / B03002_001E) | % non-white non-Hispanic |
| `pct_black` | B03002_004E / B03002_001E | % Black non-Hispanic |
| `pct_hispanic` | B03002_012E / B03002_001E | % Hispanic/Latino |
| `median_hh_income` | B19013_001E | Median household income (dollars) |
| `pct_below_185_poverty` | Sum(C17002_002-007) / C17002_001E | % below 185% FPL (FRL eligibility proxy) |
| `pct_renter` | B25003_003E / B25003_001E | % renter-occupied housing units |
| `pct_zero_vehicle` | (B25044_003E + B25044_010E) / B25044_001E | % households with zero vehicles |
| `pct_single_parent` | (B11003_010E + B11003_016E) / families-with-kids | % single-parent among families with children |
| `pct_low_income` | Sum(B19001_002-010) / B19001_001E | % households with income < $50,000 |

## Methodology

### Area-Weighted Interpolation

Census block groups do not align with CHCCS attendance zone boundaries. To estimate
demographics for each school zone, we use **area-weighted interpolation**:

1. Compute the geometric intersection of each block group with each attendance zone
2. Calculate the proportion of each block group's area that falls within each zone
3. Allocate block group population proportionally:
   `zone_pop = Sum(bg_pop x overlap_area / bg_area)`

**Assumption:** Population is uniformly distributed within each block group. This is
a standard approach but introduces error where population density varies significantly
within a block group (e.g., if one half is residential and the other is commercial).

### Median Income Estimation

Median household income for each zone is approximated as the population-weighted
average of block group medians, which is less precise than true median calculation
but provides a reasonable estimate given the available data.

### Dot-Density Map

The racial dot-density map uses 2020 Decennial Census block-level data (the highest
available spatial resolution). Each dot represents approximately 5 people of a given
racial/ethnic group.

**Dasymetric refinement:** When residential parcel polygon data is available, dots are
constrained to the intersection of Census blocks with residential parcels. This prevents
dots from being placed in parks, roads, commercial areas, or other non-residential land.
When parcels are unavailable, dots are placed randomly within Census block boundaries.

## Limitations

1. **ACS Margins of Error:** ACS 5-Year estimates have sampling error, particularly
   for small block groups. Margins of error are not displayed but should be considered
   when interpreting small differences between zones.

2. **Disclosure Avoidance:** 2020 Decennial block data includes differential privacy
   noise injected by the Census Bureau. This can cause small counts to be inaccurate
   at the block level. Block data is used only for dot-density visualization, not
   statistical reporting.

3. **5-Year Rolling Average:** ACS 2022 5-Year estimates represent data collected
   2018-2022, not a single point in time.

4. **Attendance Zone vs. Actual Enrollment:** Demographics of an attendance zone
   describe the resident population, not actual school enrollment. Families may
   choose charter, private, or magnet schools, and transfer policies allow enrollment
   outside the home zone.

5. **Area-Weighting Assumptions:** Uniform population distribution within block groups
   is assumed. Dasymetric refinement at the block level (for dots) partially addresses
   this but is not applied to block group statistics.

6. **Temporal Mismatch:** ACS data (2018-2022), Decennial data (2020), and attendance
   zone boundaries (current) may not perfectly align temporally.

## Results: Per-School-Zone Demographics

{table_md}

*All percentages rounded to 1 decimal place. Population counts are area-weighted estimates.*

## Intellectual Honesty Notes

- This analysis uses the best available public data but is subject to the limitations
  described above. Small differences between zones (< 5 percentage points) may not be
  statistically significant given ACS margins of error.
- Median household income is approximated, not computed from microdata.
- The 185% poverty threshold is a proxy for Free/Reduced Lunch eligibility. Actual FRL
  enrollment may differ due to application rates, direct certification, and CEP status.
- Zone boundaries represent geographic districts; actual school populations differ due
  to school choice, transfers, and magnet/charter enrollment.

## Stage 2: Planned Analysis (Future Work)

**Socioeconomic x School Desert / Walk Zone Overlay**

Stage 2 will cross-reference the Census demographic data from this analysis with:
- School desert masks (travel-time increase areas from `school_desert_grid.csv`) per closure scenario
- Walk zone masks (from CHCCS shapefile `ESWALK=="Y"` features)

This will answer:
- "What are the income, racial, vehicle-access, and poverty profiles of households
  whose travel time increases under each school closure scenario?"
- "What are the demographics of families within walk zones of schools proposed for closure?"

Stage 2 plans will be developed separately after Stage 1 is validated.

---

*Generated by `src/school_socioeconomic_analysis.py`*
*Census data accessed via api.census.gov*
"""

    OUTPUT_DOC.parent.mkdir(parents=True, exist_ok=True)
    OUTPUT_DOC.write_text(doc, encoding="utf-8")
    _progress(f"  Saved {OUTPUT_DOC}")


# ═══════════════════════════════════════════════════════════════════════════
# Section 11: main() with argparse
# ═══════════════════════════════════════════════════════════════════════════

def main():
    parser = argparse.ArgumentParser(
        description="School socioeconomic analysis using Census data",
    )
    parser.add_argument("--cache-only", action="store_true",
                        help="Skip downloads, use cached data only")
    parser.add_argument("--skip-maps", action="store_true",
                        help="Skip interactive map generation")
    parser.add_argument("--skip-dots", action="store_true",
                        help="Skip dot-density generation (slow)")
    parser.add_argument("--skip-charts", action="store_true",
                        help="Skip static chart generation")
    parser.add_argument("--dots-per-person", type=int, default=1,
                        help="People per dot in dot-density map (default: 1)")
    args = parser.parse_args()

    print("=" * 60)
    print("School Socioeconomic Analysis")
    print("  Census ACS + Decennial -> Attendance Zone Demographics")
    print("=" * 60)

    ensure_directories()

    # ── 1. Load school locations ──────────────────────────────────────
    print("\n[1/8] Loading school locations ...")
    schools = load_schools()
    _progress(f"Loaded {len(schools)} schools")

    # ── 2. Load district boundary ─────────────────────────────────────
    print("\n[2/8] Loading district boundary ...")
    district = load_district_boundary(schools)

    # ── 3. Load attendance zones ──────────────────────────────────────
    print("\n[3/8] Loading attendance zones ...")
    zones = load_attendance_zones()
    if zones is None:
        print("  WARNING: No attendance zone shapefile found.")
        print("  Will produce block-group-level analysis only (no per-zone aggregation).")

    # ── 4. Fetch Census data ──────────────────────────────────────────
    print("\n[4/8] Fetching ACS block group data ...")
    bg = fetch_acs_blockgroup_data(cache_only=args.cache_only)

    print("\n[5/8] Fetching Decennial block data ...")
    blocks = fetch_decennial_block_data(cache_only=args.cache_only)

    # ── 5. Spatial analysis ───────────────────────────────────────────
    print("\n[6/8] Performing spatial analysis ...")

    # Clip to district
    bg_clipped = clip_to_district(bg, district)
    _progress(f"Clipped to {len(bg_clipped)} block groups within district")

    # Compute derived metrics on block groups
    bg_clipped = compute_derived_metrics(bg_clipped)

    # Save block group profiles
    bg_export_cols = [
        "GEOID", "total_pop", "median_hh_income",
        "pct_young_children", "pct_elementary_age",
        "pct_minority", "pct_black", "pct_hispanic",
        "pct_below_185_poverty", "pct_renter", "pct_zero_vehicle",
        "pct_single_parent", "pct_low_income",
    ]
    bg_export = bg_clipped[[c for c in bg_export_cols if c in bg_clipped.columns]].copy()
    bg_export.to_csv(OUTPUT_BG_CSV, index=False)
    _progress(f"Saved block group profiles to {OUTPUT_BG_CSV}")

    # Load residential parcels (used for both dasymetric interpolation and dot placement)
    parcels = None
    if PARCEL_POLYS.exists():
        _progress("Loading residential parcel polygons ...")
        parcels = gpd.read_file(PARCEL_POLYS)
        parcels = clip_to_district(parcels, district)
        _progress(f"  Loaded {len(parcels):,} parcels within district")
    else:
        _progress("Parcel polygons not found — dasymetric weighting unavailable")

    # Per-zone aggregation (if we have attendance zones)
    zone_demographics = None
    if zones is not None:
        fragments = intersect_zones_with_blockgroups(zones, bg_clipped, parcels=parcels)
        zone_demographics = aggregate_zone_demographics(fragments, zones)

        # Save per-school demographics
        zone_demographics.to_csv(OUTPUT_SCHOOL_CSV, index=False)
        _progress(f"Saved per-school demographics to {OUTPUT_SCHOOL_CSV}")

        # Print summary
        print("\n  Per-Zone Summary:")
        print("  " + "-" * 80)
        for _, row in zone_demographics.iterrows():
            print(f"  {row['school']:35s}  Pop: {int(row['total_pop']):>6,}  "
                  f"Income: ${int(row['median_hh_income']):>7,}  "
                  f"Poverty: {row['pct_below_185_poverty']:>5.1f}%  "
                  f"Minority: {row['pct_minority']:>5.1f}%")

    # ── 6a. Downscale ACS metrics to block level ─────────────────────
    blocks_clipped = clip_to_district(blocks, district)
    _progress(f"Clipped to {len(blocks_clipped)} blocks within district")

    enriched_blocks = None
    if not args.skip_maps:
        print("\n  Downscaling ACS block-group metrics to blocks ...")
        enriched_blocks = downscale_bg_to_blocks(bg_clipped, blocks_clipped, parcels=parcels)

    # ── 6b. Dot-density generation ────────────────────────────────────
    racial_dots = None
    if not args.skip_dots and not args.skip_maps:
        print("\n[7/8] Generating dot-density layer ...")

        if parcels is None:
            _progress("Parcel polygons not available — using random-in-block placement")

        racial_dots = generate_racial_dots(
            blocks_clipped,
            dots_per_person=args.dots_per_person,
            parcels=parcels,
        )
    else:
        print("\n[7/8] Skipping dot-density generation")

    # ── 6c. Load affordable housing ──────────────────────────────────
    affordable_housing = None
    ah_path = DATA_CACHE / "affordable_housing.gpkg"
    if ah_path.exists():
        _progress("Loading affordable housing data ...")
        affordable_housing = gpd.read_file(ah_path)
        # Keep only point geometries within district
        affordable_housing = affordable_housing[affordable_housing.geometry.geom_type == "Point"].copy()
        dist_wgs = district.to_crs(CRS_WGS84)
        affordable_housing = gpd.sjoin(
            affordable_housing.to_crs(CRS_WGS84),
            dist_wgs[["geometry"]],
            predicate="within",
            how="inner",
        ).drop(columns=["index_right"], errors="ignore")
        _progress(f"  Loaded {len(affordable_housing)} affordable housing units within district")
    else:
        _progress("No affordable housing data found (run affordable_housing.py first)")

    # ── 7. Interactive map ────────────────────────────────────────────
    if not args.skip_maps:
        print("\n[8/8] Creating interactive map ...")
        fmap = create_socioeconomic_map(
            bg=bg_clipped,
            zones=zones,
            schools=schools,
            district=district,
            zone_demographics=zone_demographics,
            racial_dots=racial_dots,
            dots_per_person=args.dots_per_person,
            enriched_blocks=enriched_blocks,
            affordable_housing=affordable_housing,
        )
        fmap.save(str(OUTPUT_MAP))
        _progress(f"Saved {OUTPUT_MAP}")
    else:
        print("\n[8/8] Skipping map generation")

    # ── 8. Static charts ──────────────────────────────────────────────
    if not args.skip_charts and zone_demographics is not None:
        print("\nCreating comparison charts ...")
        create_comparison_charts(zone_demographics)
    elif args.skip_charts:
        print("\nSkipping chart generation")

    # ── 9. Documentation ──────────────────────────────────────────────
    if zone_demographics is not None:
        print("\nGenerating methodology documentation ...")
        generate_methodology_doc(zone_demographics, bg_clipped)

    # ── Done ──────────────────────────────────────────────────────────
    print("\n" + "=" * 60)
    print("Socioeconomic analysis complete!")
    print(f"  Map:    {OUTPUT_MAP}")
    print(f"  Data:   {OUTPUT_SCHOOL_CSV}")
    print(f"  Docs:   {OUTPUT_DOC}")
    print("=" * 60)


if __name__ == "__main__":
    main()
