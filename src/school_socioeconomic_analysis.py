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

MLS_CACHE = DATA_CACHE / "mls_home_sales.gpkg"
DEV_CACHE = DATA_CACHE / "planned_developments.gpkg"
SAPFOTAC_CSV = DATA_RAW / "properties" / "planned" / "SAPFOTAC_2025_future_residential.csv"

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

# Orange County, NC FIPS (37 = NC, 135 = Orange County)
# Note: 063 is Durham County — a common mistake
STATE_FIPS = "37"
COUNTY_FIPS = "135"

# Census API base URLs
ACS_YEAR = 2024
ACS_BASE_URL = f"https://api.census.gov/data/{ACS_YEAR}/acs/acs5"
DECENNIAL_BASE_URL = "https://api.census.gov/data/2020/dec/pl"

# TIGER/Line geometry URLs
TIGER_BG_URL = "https://www2.census.gov/geo/tiger/TIGER2024/BG/tl_2024_37_bg.zip"
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
    ("median_hh_income", "Median Household Income", "YlGn", "$", "", ",.0f"),
    ("pct_below_185_poverty", "% Below 185% Poverty (Free/Reduced Lunch Proxy)", "YlOrRd", "", "%", ".0f"),
    ("pct_minority", "% Minority (Non-White NH)", "PuBuGn", "", "%", ".0f"),
    ("pct_zero_vehicle", "% Households with No Vehicle", "Reds", "", "%", ".0f"),
    ("pct_elementary_age", "% Population Aged 5-9", "BuPu", "", "%", ".1f"),
    ("pct_young_children", "% Population Aged 0-4", "PuRd", "", "%", ".1f"),
    ("ah_units", "Affordable Housing Units", "Blues", "", " units", ",.0f"),
    ("mls_housing", "Housing Market (2023–2025)", "YlGnBu", "", "", ""),
    ("planned_dev", "Planned Developments (CH Active Dev)", "YlGnBu", "", " units", ",.0f"),
    ("sapfotac_dev", "Planned Developments (SAPFOTAC)", "YlGnBu", "", " units", ",.0f"),
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
                       fmt: str = ".0f", prefix: str = "", suffix: str = "",
                       wide: bool = False) -> str:
    """Build an HTML gradient legend bar."""
    import matplotlib.colors as mcolors

    cmap = plt.get_cmap(cmap_name)
    n_stops = 5  # Max 5 labels for cleaner legend
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

    label_font = "13px" if wide else "12px"
    labels_html = "".join(
        f'<span style="flex:1; text-align:center; font-size:{label_font};">{lbl}</span>'
        for lbl in labels
    )

    max_w = "420px" if wide else "320px"
    return f"""
    <div style="padding: 10px 14px; background: white; border-radius: 5px;
                box-shadow: 0 2px 6px rgba(0,0,0,0.3); max-width: {max_w};">
        <div style="font-weight: bold; font-size: 13px; margin-bottom: 6px;">{title}</div>
        <div style="height: 16px; background: {gradient_css}; border-radius: 3px;"></div>
        <div style="display: flex; justify-content: space-between; margin-top: 4px;">
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
    mls_data: gpd.GeoDataFrame | None = None,
    planned_dev: gpd.GeoDataFrame | None = None,
    sapfotac_dev: gpd.GeoDataFrame | None = None,
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

        # Order must match METRIC_DOT_SPECS for correct layer indexing
        block_layers = [
            ("Median Income (Block est.)", "median_hh_income", "YlGn", "$", "", ",.0f", True),
            ("% Below 185% Poverty (Block est.)", "pct_below_185_poverty", "YlOrRd", "", "%", ".0f", True),
            ("% Minority (Block)", "pct_minority", "PuBuGn", "", "%", ".0f", False),
            ("% Zero-Vehicle HH (Block est.)", "pct_zero_vehicle", "Reds", "", "%", ".0f", True),
            ("% Elementary Age (Block est.)", "pct_elementary_age", "BuPu", "", "%", ".1f", True),
            ("% Young Children (Block est.)", "pct_young_children", "PuRd", "", "%", ".1f", True),
        ]

        blk_wgs = enriched_blocks.to_crs(CRS_WGS84).copy()
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

    def _make_zone_fg(gdf, label, show=False):
        """Create a FeatureGroup of zone polygons from a GeoDataFrame."""
        fg = folium.FeatureGroup(name=label, show=show)
        names = []
        for _, row in gdf.iterrows():
            sn = row["school"]
            names.append(sn)
            c = school_color_map.get(sn, "#888888")
            folium.GeoJson(
                gpd.GeoDataFrame([row], crs=CRS_WGS84).__geo_interface__,
                style_function=lambda x, c=c: {
                    "fillColor": c, "fillOpacity": 0.08,
                    "color": c, "weight": 2.5,
                },
                popup=folium.Popup(f"<b>{sn}</b>", max_width=300),
                tooltip=sn,
            ).add_to(fg)
        fg.add_to(m)
        return fg, names

    # Load extra zone GDFs
    walk_zones_gdf = _load_walk_zones()
    walk_nearest_gdf = _build_nearest_zones(GRID_CSV, "walk", district)
    bike_nearest_gdf = _build_nearest_zones(GRID_CSV, "bike", district)
    drive_nearest_gdf = _build_nearest_zones(GRID_CSV, "drive", district)

    # Build list of zone type dicts: (key, label, gdf)
    zone_type_defs = [
        ("school", "School Zones", zones),
        ("walk_zone", "Walk Zones", walk_zones_gdf),
        ("walk", "Nearest Walk", walk_nearest_gdf),
        ("bike", "Nearest Bike", bike_nearest_gdf),
        ("drive", "Nearest Drive", drive_nearest_gdf),
    ]

    zone_types = []  # [{key, label, fg_name, names}, ...]
    active_zone_gdfs = []  # parallel list of GDFs for MLS spatial joins
    for zt_key, zt_label, zt_gdf in zone_type_defs:
        if zt_gdf is not None and len(zt_gdf) > 0:
            show_initial = (zt_key == "school")
            fg, names = _make_zone_fg(zt_gdf, zt_label, show=show_initial)
            zone_types.append({
                "key": zt_key, "label": zt_label,
                "fg_name": fg.get_name(), "names": sorted(names),
                "gdf": zt_gdf,
            })
            active_zone_gdfs.append(zt_gdf)
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
    if affordable_housing is not None and len(affordable_housing) > 0:
        ah_fg = folium.FeatureGroup(name="Affordable Housing", show=False)

        # AMI color scale
        ami_colors = {
            "0-30%": "#d73027",    # Deep red (deeply affordable)
            "30-60%": "#fc8d59",   # Orange
            "60-80%": "#fee090",   # Light yellow
            "80%+": "#91bfdb",     # Light blue
        }

        for _, row in affordable_housing.iterrows():
            ami = row.get("AMIServed", "Unknown")
            if pd.isna(ami):
                ami = "Unknown"
            color = ami_colors.get(ami, "#808080")

            project_name = row.get("ProjectName", "Unknown")
            unit_type = row.get("UnitType", "N/A")
            bedrooms = row.get("Bedrooms", "N/A")
            rental_own = row.get("RentalOwnership", "N/A")

            popup_html = f"""
            <b>{project_name}</b><br>
            AMI Level: {ami}<br>
            Type: {unit_type}<br>
            Bedrooms: {bedrooms}<br>
            {rental_own}
            """

            folium.CircleMarker(
                location=[row.geometry.y, row.geometry.x],
                radius=5,
                color=color,
                weight=1,
                fillColor=color,
                fillOpacity=0.8,
                popup=folium.Popup(popup_html, max_width=200),
            ).add_to(ah_fg)

        ah_fg.add_to(m)
        _progress(f"Added {len(affordable_housing)} affordable housing markers")
    else:
        ah_fg = None

    # -- MLS home sales layer --
    if mls_data is not None and len(mls_data) > 0:
        mls_fg = folium.FeatureGroup(name="MLS Home Sales", show=False)

        # Price quartile colors
        q25 = mls_data["close_price"].quantile(0.25)
        q50 = mls_data["close_price"].quantile(0.50)
        q75 = mls_data["close_price"].quantile(0.75)

        def _mls_color(price):
            if price <= q25:
                return "#2166ac"   # Blue (lowest quartile)
            elif price <= q50:
                return "#67a9cf"   # Light blue
            elif price <= q75:
                return "#fc8d59"   # Orange
            else:
                return "#b2182b"   # Red (highest quartile)

        for _, row in mls_data.iterrows():
            price = row.get("close_price", 0)
            addr = row.get("address", "Unknown")
            date = row.get("close_date", "")
            color = _mls_color(price)
            bedrooms = row.get("bedrooms", None)

            date_str = ""
            if pd.notna(date):
                try:
                    date_str = pd.Timestamp(date).strftime("%m/%d/%Y")
                except Exception:
                    date_str = str(date)

            br_popup = ""
            br_tooltip = ""
            if bedrooms is not None and pd.notna(bedrooms):
                br_popup = f"Bedrooms: {int(bedrooms)}<br>"
                br_tooltip = f" &bull; {int(bedrooms)} BR"

            popup_html = f"""
            <b>{addr}</b><br>
            Price: ${price:,.0f}<br>
            {br_popup}Date: {date_str}
            """

            tooltip_html = (
                f"Price: ${price:,.0f}{br_tooltip}<br>"
                f"Date: {date_str}"
            )

            folium.CircleMarker(
                location=[row.geometry.y, row.geometry.x],
                radius=4,
                color=color,
                weight=1,
                fillColor=color,
                fillOpacity=0.7,
                tooltip=folium.Tooltip(tooltip_html),
            ).add_to(mls_fg)

        mls_fg.add_to(m)
        _progress(f"Added {len(mls_data)} MLS home sale markers")
    else:
        mls_fg = None

    # -- Planned developments layer --
    if planned_dev is not None and len(planned_dev) > 0:
        import math
        import matplotlib.colors as mcolors
        dev_fg = folium.FeatureGroup(name="Planned Developments (CH Active Dev)", show=False)

        # Color scale by unit count: light blue (few) → yellow → red (many)
        # Same palette as Affordable Housing AMI colors
        dev_unit_vals = planned_dev["expected_units"].dropna()
        dev_max_units = float(dev_unit_vals.max()) if len(dev_unit_vals) > 0 else 1
        if dev_max_units <= 0:
            dev_max_units = 1
        dev_color_stops = [
            (0.0, "#91bfdb"),   # light blue (fewest units)
            (0.33, "#fee090"),  # light yellow
            (0.66, "#fc8d59"),  # orange
            (1.0, "#d73027"),   # deep red (most units)
        ]
        dev_cmap = mcolors.LinearSegmentedColormap.from_list(
            "dev_units", [(s, c) for s, c in dev_color_stops]
        )

        for _, row in planned_dev.iterrows():
            name = row.get("name", "Unknown")
            addr = row.get("address", "Unknown")
            units = row.get("expected_units", 0)
            if pd.isna(units):
                units = 0
            units = int(units)

            frac = min(units / dev_max_units, 1.0)
            color = mcolors.rgb2hex(dev_cmap(frac))

            popup_html = f"""
            <b>{name}</b><br>
            {addr}<br>
            Expected units: {units:,}
            """

            tooltip_html = f"{name} &bull; {units:,} units"

            folium.CircleMarker(
                location=[row.geometry.y, row.geometry.x],
                radius=10,
                color="#555",
                weight=1.5,
                fillColor=color,
                fillOpacity=0.85,
                popup=folium.Popup(popup_html, max_width=200),
                tooltip=folium.Tooltip(tooltip_html),
            ).add_to(dev_fg)

        dev_fg.add_to(m)
        _progress(f"Added {len(planned_dev)} planned development markers")
    else:
        dev_fg = None

    # -- SAPFOTAC planned developments layer --
    if sapfotac_dev is not None and len(sapfotac_dev) > 0:
        import math
        import matplotlib.colors as mcolors
        sap_fg = folium.FeatureGroup(name="Planned Developments (SAPFOTAC)", show=False)

        sap_unit_vals = sapfotac_dev["total_units_remaining"].dropna()
        sap_max_units = float(sap_unit_vals.max()) if len(sap_unit_vals) > 0 else 1
        if sap_max_units <= 0:
            sap_max_units = 1
        sap_color_stops = [
            (0.0, "#91bfdb"),
            (0.33, "#fee090"),
            (0.66, "#fc8d59"),
            (1.0, "#d73027"),
        ]
        sap_cmap = mcolors.LinearSegmentedColormap.from_list(
            "sap_units", [(s, c) for s, c in sap_color_stops]
        )

        for _, row in sapfotac_dev.iterrows():
            name = row.get("project", "Unknown")
            addr = row.get("address", "Unknown")
            units = row.get("total_units_remaining", 0)
            elem = row.get("students_elementary", 0)
            mid = row.get("students_middle", 0)
            high = row.get("students_high", 0)
            if pd.isna(units):
                units = 0
            units = int(units)
            if pd.isna(elem):
                elem = 0
            if pd.isna(mid):
                mid = 0
            if pd.isna(high):
                high = 0

            frac = min(units / sap_max_units, 1.0)
            color = mcolors.rgb2hex(sap_cmap(frac))

            popup_html = f"""
            <b>{name}</b><br>
            {addr}<br>
            Units remaining: {units:,}<br>
            Students — Elem: {int(elem)}, Mid: {int(mid)}, High: {int(high)}
            """

            tooltip_html = f"{name} &bull; {units:,} units"

            folium.CircleMarker(
                location=[row.geometry.y, row.geometry.x],
                radius=10,
                color="#555",
                weight=1.5,
                fillColor=color,
                fillOpacity=0.85,
                popup=folium.Popup(popup_html, max_width=220),
                tooltip=folium.Tooltip(tooltip_html),
            ).add_to(sap_fg)

        sap_fg.add_to(m)
        _progress(f"Added {len(sapfotac_dev)} SAPFOTAC planned development markers")
    else:
        sap_fg = None

    # -- Custom control panel replaces Folium LayerControl --

    # -- Banner with FAQ button (matching school_closure_analysis.html style) --
    banner_html = """
    <style>
        #socio-banner {
            position: fixed; top: 0; left: 0; right: 0; z-index: 1000;
            background: white; padding: 10px 20px;
            border-bottom: 1px solid #dee2e6;
            display: flex; justify-content: center; align-items: center;
            text-align: center;
        }
        #socio-banner h1 { margin: 0; font-size: 18px; font-weight: 600; color: #333; }
        #socio-banner .subtitle { margin: 2px 0 0 0; font-size: 12px; color: #666; display: inline; }
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
            box-shadow: 0 4px 12px rgba(0,0,0,0.3); max-width: 380px;
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
    <div id="socio-banner">
        <div>
            <h1>CHCCS Socioeconomic Analysis</h1>
            <p class="subtitle">Census ACS __ACS_YEAR__ 5-Year Estimates &mdash; Scroll down for zone comparison charts
                <button class="faq-btn" onclick="toggleFaqPanel()" title="Click for FAQ">
FAQ
                </button>
            </p>
        </div>
    </div>
    <div class="faq-panel" id="faq-panel">
        <span class="faq-close" onclick="toggleFaqPanel()">&times;</span>
        <h5>Frequently Asked Questions</h5>
        <div class="faq-item">
            <div class="faq-q">What does "Census ACS __ACS_YEAR__ 5-Year" mean?</div>
            <div class="faq-a"><b>ACS</b> = American Community Survey, an ongoing Census Bureau survey.
            <b>5-Year</b> means data averaged over __ACS_RANGE__ for statistical reliability in small areas like block groups.
            This is official data collected and published by the U.S. Census Bureau.</div>
        </div>
        <div class="faq-item">
            <div class="faq-q">What is an Attendance Zone?</div>
            <div class="faq-a">A geographic boundary determining which school students are assigned to by default.
            <b>Important:</b> Zone demographics show who <i>lives</i> in each zone, not who <i>attends</i> each school (families may use school choice, transfers, or magnets).</div>
        </div>
        <div class="faq-item">
            <div class="faq-q">What are Nearest Walk/Bike/Drive zones?</div>
            <div class="faq-a">Areas grouped by which school is closest via that travel mode, using actual road networks (Dijkstra shortest-path algorithm).
            <b>Walk:</b> 2.5 mph &bull; <b>Bike:</b> 12 mph &bull; <b>Drive:</b> 18-60 mph depending on road type.</div>
        </div>
        <div class="faq-item">
            <div class="faq-q">What are "Block Groups" vs "Blocks (est.)"?</div>
            <div class="faq-a"><b>Block Groups</b> = Census block groups (~1,500 people), the native ACS geography.
            <b>Blocks (est.)</b> = Estimated block-level values interpolated from block groups using residential parcel area weighting (dasymetric method).</div>
        </div>
        <div class="faq-item">
            <div class="faq-q">Who is counted as "Minority"?</div>
            <div class="faq-a">All non-White-Non-Hispanic residents: Black, Hispanic/Latino, Asian, Multiracial, Native American, Pacific Islander, and other races.
            Calculated as 100% minus % White Non-Hispanic.</div>
        </div>
        <div class="faq-item">
            <div class="faq-q">Why is % Minority different from Race/Ethnicity dots?</div>
            <div class="faq-a">The <b>dots</b> show each racial/ethnic group as separate colors for detailed visualization.
            <b>% Minority</b> aggregates all non-White-NH groups into one metric for quick zone-to-zone comparison.</div>
        </div>
        <div class="faq-item">
            <div class="faq-q">What does "% Population Aged 5-9" measure?</div>
            <div class="faq-a">The percentage of <b>all residents</b> (not just children) who are aged 5-9.
            Denominator = total population of all ages in that area.</div>
        </div>
        <div class="faq-item">
            <div class="faq-q">What does "% Below 185% Poverty (FRL Proxy)" mean?</div>
            <div class="faq-a">The percentage of residents with household income below <b>185% of the federal poverty line</b>.
            This threshold is the eligibility cutoff for <b>Free/Reduced-price Lunch (FRL)</b> in public schools, making it a useful proxy for school-level economic disadvantage.</div>
        </div>
        <div class="faq-item">
            <div class="faq-q">Why do some areas that look close on the map belong to different drive zones?</div>
            <div class="faq-a">Drive zones are based on actual driving distance along roads, not straight-line distance.
            Two places might look close on a map but be far apart by car if there's no direct road connecting them &mdash;
            they may need to go around a highway, river, or neighborhood without a through-street.
            So the "nearest school by driving" depends on which roads are available, not just how close things appear.</div>
        </div>
        <div class="faq-item">
            <div class="faq-q">Why are there gaps or "donut holes" in the nearest walk/bike/drive zones?</div>
            <div class="faq-a">The travel-time zones are built from a 100-meter grid. Each grid pixel is &ldquo;snapped&rdquo; to the nearest road or path in the OpenStreetMap network. Pixels that are more than 200 meters from any road (e.g., in parks, forests, or large undeveloped areas) cannot be assigned a travel time, so they appear as gaps. These holes do not mean nobody lives there &mdash; they simply reflect areas where our road-network model has no nearby edge to connect to.</div>
        </div>
        <div class="faq-item">
            <div class="faq-q">Why do some population dots appear outside the CHCCS district boundary?</div>
            <div class="faq-a">Population dots are placed within Census block boundaries, and some Census blocks extend beyond the CHCCS district perimeter. When a block only partially overlaps the district, its entire population is still represented with dots scattered across the full block geometry. This means a few dots may fall just outside the dashed district boundary line.</div>
        </div>
        <div class="faq-item">
            <div class="faq-q">What is "Planned Developments (SAPFOTAC)"?</div>
            <div class="faq-a">Data from the <b>SAPFOTAC 2025 Annual Report</b> (School Adequate Public Facilities
            Ordinance Technical Advisory Committee), certified June 3, 2025. It lists 21 future residential projects
            with projected <b>student yields</b> &mdash; the estimated number of elementary, middle, and high school
            students each development will generate, based on the district&rsquo;s student generation rates.<br><br>
            The bar charts show three breakdowns per zone: total projects, total housing units remaining, and projected
            elementary students. Click a marker to see per-project detail including all three student-yield figures.<br><br>
            <b>Why does it differ from CH Active Dev?</b> The two datasets come from different sources collected at
            different times. <b>CH Active Dev</b> is hand-transcribed from the Town of Chapel Hill
            <a href="https://www.chapelhillnc.gov/Business-and-Development/Active-Development" target="_blank"
            style="color:#2166ac;">Active Development</a> page (March 2026) and covers Chapel Hill only; it has unit
            counts but no student yield estimates. <b>SAPFOTAC</b> is published by the school district&rsquo;s advisory
            committee and covers the full CHCCS boundary (Chapel Hill + Carrboro); it adds student yield projections
            but may reflect an earlier point in time. Some projects appear in both sources; the datasets are not
            deduplicated.</div>
        </div>
        <div class="faq-item">
            <div class="faq-q">Where can I learn more about the methodology?</div>
            <div class="faq-a">See the <a href="socioeconomic_methodology.html" target="_blank" style="color:#2166ac;">Socioeconomic Methodology</a> page for detailed documentation of data sources, processing steps, and limitations.</div>
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
    """.replace("__ACS_YEAR__", str(ACS_YEAR)).replace(
        "__ACS_RANGE__", f"{ACS_YEAR - 4}-{ACS_YEAR}"
    )
    m.get_root().html.add_child(folium.Element(banner_html))

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
                    wide=(prefix == "$"),
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

        for zt in zone_types:
            zt_names = zt["names"]
            all_zone_names.append(zt_names)

            if pts_gdf is not None and len(zt_names) > 0:
                zt_gdf_wgs = zt["gdf"].to_crs(CRS_WGS84)
                joined = gpd.sjoin(
                    pts_gdf, zt_gdf_wgs[["school", "geometry"]],
                    how="left", predicate="within",
                )
                joined = joined[~joined.index.duplicated(keep="first")]
                # Map to master school indices (not zone-local indices)
                indices = joined["school"].map(master_idx).fillna(-1).astype(int).tolist()
                n_assigned = sum(1 for z in indices if z >= 0)
                _progress(f"  Assigned {n_assigned:,} dots to {len(zt_names)} {zt['label']} zones")
            else:
                indices = [-1] * len(dot_data)

            all_dot_zones.append(indices)

        # Backward-compat aliases for first zone type
        dot_zone_indices = all_dot_zones[0] if all_dot_zones else [-1] * len(dot_data)
        zone_names_for_js = all_zone_names[0] if all_zone_names else []

        # ── Race legend HTML ──
        legend_items = "".join(
            f'<span style="display:inline-block; width:12px; height:12px; '
            f'background:{color}; border-radius:50%; margin-right:5px;"></span>'
            f'{label}&nbsp;&nbsp;'
            for race_key, (color, label) in RACE_CATEGORIES.items()
        )
        race_legend_html = (
            f'<div style="padding: 10px 14px; background: white; border-radius: 5px;'
            f' box-shadow: 0 2px 6px rgba(0,0,0,0.3); max-width: 320px; font-size: 13px;">'
            f'<b style="font-size: 13px;">Race/Ethnicity</b> (1 dot = {dots_per_person} '
            f'{"person" if dots_per_person == 1 else "people"})<br>'
            f'{legend_items}</div>'
        )

        all_legends = {"Race/Ethnicity": race_legend_html}
        all_legends.update(metric_legends)

        # Override AH legend with categorical AMI legend (not a color ramp)
        ah_legend_html = """
        <div style="padding: 10px 14px; background: white; border-radius: 5px;
                    box-shadow: 0 2px 6px rgba(0,0,0,0.3); max-width: 320px; font-size: 13px;">
            <div style="font-weight: bold; margin-bottom: 8px;">Affordable Housing by AMI (Area Median Income) Level</div>
            <div style="display: flex; align-items: center; margin: 4px 0;">
                <span style="display:inline-block; width:14px; height:14px; background:#d73027;
                      border-radius:50%; margin-right:8px;"></span>0-30% AMI (Deeply Affordable)
            </div>
            <div style="display: flex; align-items: center; margin: 4px 0;">
                <span style="display:inline-block; width:14px; height:14px; background:#fc8d59;
                      border-radius:50%; margin-right:8px;"></span>30-60% AMI
            </div>
            <div style="display: flex; align-items: center; margin: 4px 0;">
                <span style="display:inline-block; width:14px; height:14px; background:#fee090;
                      border-radius:50%; margin-right:8px;"></span>60-80% AMI
            </div>
            <div style="display: flex; align-items: center; margin: 4px 0;">
                <span style="display:inline-block; width:14px; height:14px; background:#91bfdb;
                      border-radius:50%; margin-right:8px;"></span>80%+ AMI
            </div>
        </div>
        """
        all_legends["Affordable Housing Units"] = ah_legend_html

        # Override MLS legends with price quartile legend
        mls_legend_html = """
        <div style="padding: 10px 14px; background: white; border-radius: 5px;
                    box-shadow: 0 2px 6px rgba(0,0,0,0.3); max-width: 320px; font-size: 13px;">
            <div style="font-weight: bold; margin-bottom: 8px;">MLS Home Sales by Price Quartile</div>
            <div style="display: flex; align-items: center; margin: 4px 0;">
                <span style="display:inline-block; width:14px; height:14px; background:#2166ac;
                      border-radius:50%; margin-right:8px;"></span>Bottom 25%
            </div>
            <div style="display: flex; align-items: center; margin: 4px 0;">
                <span style="display:inline-block; width:14px; height:14px; background:#67a9cf;
                      border-radius:50%; margin-right:8px;"></span>25th–50th percentile
            </div>
            <div style="display: flex; align-items: center; margin: 4px 0;">
                <span style="display:inline-block; width:14px; height:14px; background:#fc8d59;
                      border-radius:50%; margin-right:8px;"></span>50th–75th percentile
            </div>
            <div style="display: flex; align-items: center; margin: 4px 0;">
                <span style="display:inline-block; width:14px; height:14px; background:#b2182b;
                      border-radius:50%; margin-right:8px;"></span>Top 25%
            </div>
            <div style="margin-top: 6px; font-size: 11px; color: #666;">
                Source: Triangle MLS (2023–2025)
            </div>
        </div>
        """
        for mls_metric_name in ["Homes Sold (2023\u20132025)", "Median Home Price (2023\u20132025)"]:
            all_legends[mls_metric_name] = mls_legend_html

        # Override Planned Developments (CH Active Dev) legend with unit-count legend
        dev_legend_html = """
        <div style="padding: 10px 14px; background: white; border-radius: 5px;
                    box-shadow: 0 2px 6px rgba(0,0,0,0.3); max-width: 320px; font-size: 13px;">
            <div style="font-weight: bold; margin-bottom: 8px;">Planned Developments (CH Active Dev) by Expected Unit Count</div>
            <div style="display: flex; align-items: center; margin: 4px 0;">
                <span style="display:inline-block; width:14px; height:14px; background:#d73027;
                      border-radius:50%; margin-right:8px;"></span>400+ units
            </div>
            <div style="display: flex; align-items: center; margin: 4px 0;">
                <span style="display:inline-block; width:14px; height:14px; background:#fc8d59;
                      border-radius:50%; margin-right:8px;"></span>150–400 units
            </div>
            <div style="display: flex; align-items: center; margin: 4px 0;">
                <span style="display:inline-block; width:14px; height:14px; background:#fee090;
                      border-radius:50%; margin-right:8px;"></span>50–150 units
            </div>
            <div style="display: flex; align-items: center; margin: 4px 0;">
                <span style="display:inline-block; width:14px; height:14px; background:#91bfdb;
                      border-radius:50%; margin-right:8px;"></span>&lt;50 units
            </div>
            <div style="margin-top: 6px; font-size: 11px; color: #666;">
                Source: Chapel Hill Active Development (hand-transcribed March 12, 2026)
            </div>
        </div>
        """
        all_legends["Planned Developments (CH Active Dev)"] = dev_legend_html

        # SAPFOTAC Planned Developments legend
        sap_legend_html = """
        <div style="padding: 10px 14px; background: white; border-radius: 5px;
                    box-shadow: 0 2px 6px rgba(0,0,0,0.3); max-width: 320px; font-size: 13px;">
            <div style="font-weight: bold; margin-bottom: 8px;">Planned Developments (SAPFOTAC) by Unit Count</div>
            <div style="display: flex; align-items: center; margin: 4px 0;">
                <span style="display:inline-block; width:14px; height:14px; background:#d73027;
                      border-radius:50%; margin-right:8px;"></span>400+ units
            </div>
            <div style="display: flex; align-items: center; margin: 4px 0;">
                <span style="display:inline-block; width:14px; height:14px; background:#fc8d59;
                      border-radius:50%; margin-right:8px;"></span>150–400 units
            </div>
            <div style="display: flex; align-items: center; margin: 4px 0;">
                <span style="display:inline-block; width:14px; height:14px; background:#fee090;
                      border-radius:50%; margin-right:8px;"></span>50–150 units
            </div>
            <div style="display: flex; align-items: center; margin: 4px 0;">
                <span style="display:inline-block; width:14px; height:14px; background:#91bfdb;
                      border-radius:50%; margin-right:8px;"></span>&lt;50 units
            </div>
            <div style="margin-top: 6px; font-size: 11px; color: #666;">
                Source: SAPFOTAC 2025 Annual Report (certified June 3, 2025)
            </div>
        </div>
        """
        all_legends["Planned Developments (SAPFOTAC)"] = sap_legend_html

        # ── JS data serialization ──
        dot_fg_name = dot_fg.get_name()
        map_name = m.get_name()
        ah_fg_name = ah_fg.get_name() if ah_fg is not None else "null"
        mls_fg_name = mls_fg.get_name() if mls_fg is not None else "null"
        dev_fg_name = dev_fg.get_name() if dev_fg is not None else "null"
        sap_fg_name = sap_fg.get_name() if sap_fg is not None else "null"
        school_fg_name = school_fg.get_name()

        race_colors_list = [color for color, label in RACE_CATEGORIES.values()]
        metric_names = ["Race/Ethnicity"] + [spec[1] for spec in METRIC_DOT_SPECS]
        metric_prefixes = [""] + [spec[3] for spec in METRIC_DOT_SPECS]
        metric_suffixes = [""] + [spec[4] for spec in METRIC_DOT_SPECS]

        # Metric radio button labels with subsection headers
        radio_html = ""
        for i, name in enumerate(metric_names):
            # Insert subsection headers
            if i == 0:
                radio_html += (
                    '<div style="font-size:9px;text-transform:uppercase;color:#888;'
                    'letter-spacing:0.8px;margin:4px 0 2px 0;">ACS Census</div>'
                )
            elif name == "Affordable Housing Units":
                radio_html += (
                    '<div style="font-size:9px;text-transform:uppercase;color:#888;'
                    'letter-spacing:0.8px;margin:6px 0 2px 0;padding-top:4px;'
                    'border-top:1px solid #ddd;">Housing</div>'
                )
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
        metric_ranges_js = _json.dumps(metric_ranges, separators=(",", ":"))

        # Multi-zone-type arrays
        all_dot_zones_js = _json.dumps(all_dot_zones, separators=(",", ":"))
        all_zone_names_js = _json.dumps(all_zone_names, separators=(",", ":"))
        master_schools_js = _json.dumps(master_school_names, separators=(",", ":"))
        zone_fg_names_js = "[" + ",".join(zt["fg_name"] for zt in zone_types) + "]"
        zone_type_labels_js = _json.dumps([zt["label"] for zt in zone_types], separators=(",", ":"))

        # Zone-level affordable housing totals (in master_school_names order)
        ah_by_zone_list = []
        if zone_demographics is not None and "ah_total_units" in zone_demographics.columns:
            zd_dict = zone_demographics.set_index("school")["ah_total_units"].to_dict()
            ah_by_zone_list = [int(zd_dict.get(s, 0)) for s in master_school_names]
        else:
            ah_by_zone_list = [0] * len(master_school_names)
        ah_by_zone_js = _json.dumps(ah_by_zone_list, separators=(",", ":"))

        # Zone-level MLS aggregates — per zone type (nested lists)
        def _mls_by_zone_type(mls_gdf, zone_gdf, school_names):
            """Spatial-join MLS points to zone polygons, return (sales, prices, ppsf, bedrooms_by_zone)."""
            n = len(school_names)
            empty_beds = [[] for _ in range(n)]
            if mls_gdf is None or zone_gdf is None or len(mls_gdf) == 0 or len(zone_gdf) == 0:
                return [0] * n, [0] * n, [0] * n, empty_beds
            mls_wgs = mls_gdf.to_crs(CRS_WGS84)
            zones_wgs = zone_gdf.to_crs(CRS_WGS84)
            joined = gpd.sjoin(mls_wgs, zones_wgs[["school", "geometry"]],
                               how="left", predicate="within")
            valid = joined.dropna(subset=["school"])
            agg_cols = dict(
                sales=("close_price", "size"),
                median_price=("close_price", "median"),
            )
            if "price_per_sqft" in valid.columns:
                agg_cols["median_ppsf"] = ("price_per_sqft", "median")
            agg = valid.groupby("school").agg(**agg_cols).reset_index()
            agg_dict = agg.set_index("school")
            sales = [int(agg_dict.loc[s, "sales"]) if s in agg_dict.index else 0
                     for s in school_names]
            prices = [round(float(agg_dict.loc[s, "median_price"])) if s in agg_dict.index else 0
                      for s in school_names]
            ppsf = [round(float(agg_dict.loc[s, "median_ppsf"])) if s in agg_dict.index and "median_ppsf" in agg_dict.columns else 0
                    for s in school_names]
            # Collect raw bedroom values per zone
            bedrooms_by_zone = [[] for _ in range(n)]
            has_bedrooms = "bedrooms" in valid.columns
            if has_bedrooms:
                name_to_idx = {s: i for i, s in enumerate(school_names)}
                for _, row in valid.iterrows():
                    si = name_to_idx.get(row["school"])
                    if si is not None and pd.notna(row.get("bedrooms")):
                        bedrooms_by_zone[si].append(int(row["bedrooms"]))
            return sales, prices, ppsf, bedrooms_by_zone

        all_mls_sales = []  # list of lists, one per zone type
        all_mls_prices = []
        all_mls_ppsf = []
        all_mls_bedrooms = []
        for zt_gdf in active_zone_gdfs:
            sales, prices, ppsf, bedrooms = _mls_by_zone_type(mls_data, zt_gdf, master_school_names)
            all_mls_sales.append(sales)
            all_mls_prices.append(prices)
            all_mls_ppsf.append(ppsf)
            all_mls_bedrooms.append(bedrooms)
        if not all_mls_sales:
            n = len(master_school_names)
            all_mls_sales = [[0] * n]
            all_mls_prices = [[0] * n]
            all_mls_ppsf = [[0] * n]
            all_mls_bedrooms = [[[] for _ in range(n)]]
        mls_sales_by_zone_js = _json.dumps(all_mls_sales, separators=(",", ":"))
        mls_price_by_zone_js = _json.dumps(all_mls_prices, separators=(",", ":"))
        mls_ppsf_by_zone_js = _json.dumps(all_mls_ppsf, separators=(",", ":"))
        mls_bedrooms_by_zone_js = _json.dumps(all_mls_bedrooms, separators=(",", ":"))

        # Zone-level planned development aggregates — per zone type
        def _dev_by_zone_type(dev_gdf, zone_gdf, school_names):
            """Spatial-join planned dev points to zone polygons, return (units, counts)."""
            n = len(school_names)
            if dev_gdf is None or zone_gdf is None or len(dev_gdf) == 0 or len(zone_gdf) == 0:
                return [0] * n, [0] * n
            dev_wgs = dev_gdf.to_crs(CRS_WGS84)
            zones_wgs = zone_gdf.to_crs(CRS_WGS84)
            joined = gpd.sjoin(dev_wgs, zones_wgs[["school", "geometry"]],
                               how="left", predicate="within")
            valid = joined.dropna(subset=["school"])
            agg = valid.groupby("school").agg(
                total_units=("expected_units", "sum"),
                dev_count=("expected_units", "size"),
            ).reset_index()
            agg_dict = agg.set_index("school")
            units = [int(agg_dict.loc[s, "total_units"]) if s in agg_dict.index else 0
                     for s in school_names]
            counts = [int(agg_dict.loc[s, "dev_count"]) if s in agg_dict.index else 0
                      for s in school_names]
            return units, counts

        all_dev_units = []
        all_dev_counts = []
        for zt_gdf in active_zone_gdfs:
            units, counts = _dev_by_zone_type(planned_dev, zt_gdf, master_school_names)
            all_dev_units.append(units)
            all_dev_counts.append(counts)
        if not all_dev_units:
            n = len(master_school_names)
            all_dev_units = [[0] * n]
            all_dev_counts = [[0] * n]
        dev_units_by_zone_js = _json.dumps(all_dev_units, separators=(",", ":"))
        dev_counts_by_zone_js = _json.dumps(all_dev_counts, separators=(",", ":"))

        # Zone-level SAPFOTAC aggregates — per zone type
        def _sapfotac_by_zone_type(sap_gdf, zone_gdf, school_names):
            """Spatial-join SAPFOTAC dev points to zone polygons, return (units, counts, elementary)."""
            n = len(school_names)
            if sap_gdf is None or zone_gdf is None or len(sap_gdf) == 0 or len(zone_gdf) == 0:
                return [0] * n, [0] * n, [0] * n
            sap_wgs = sap_gdf.to_crs(CRS_WGS84)
            zones_wgs = zone_gdf.to_crs(CRS_WGS84)
            joined = gpd.sjoin(sap_wgs, zones_wgs[["school", "geometry"]],
                               how="left", predicate="within")
            valid = joined.dropna(subset=["school"])
            agg = valid.groupby("school").agg(
                total_units=("total_units_remaining", "sum"),
                sap_count=("total_units_remaining", "size"),
                elem_students=("students_elementary", "sum"),
            ).reset_index()
            agg_dict = agg.set_index("school")
            units = [int(agg_dict.loc[s, "total_units"]) if s in agg_dict.index else 0
                     for s in school_names]
            counts = [int(agg_dict.loc[s, "sap_count"]) if s in agg_dict.index else 0
                      for s in school_names]
            elementary = [int(agg_dict.loc[s, "elem_students"]) if s in agg_dict.index else 0
                          for s in school_names]
            return units, counts, elementary

        all_sapfotac_units = []
        all_sapfotac_counts = []
        all_sapfotac_elem = []
        for zt_gdf in active_zone_gdfs:
            units, counts, elementary = _sapfotac_by_zone_type(sapfotac_dev, zt_gdf, master_school_names)
            all_sapfotac_units.append(units)
            all_sapfotac_counts.append(counts)
            all_sapfotac_elem.append(elementary)
        if not all_sapfotac_units:
            n = len(master_school_names)
            all_sapfotac_units = [[0] * n]
            all_sapfotac_counts = [[0] * n]
            all_sapfotac_elem = [[0] * n]
        sapfotac_units_by_zone_js = _json.dumps(all_sapfotac_units, separators=(",", ":"))
        sapfotac_counts_by_zone_js = _json.dumps(all_sapfotac_counts, separators=(",", ":"))
        sapfotac_elem_by_zone_js = _json.dumps(all_sapfotac_elem, separators=(",", ":"))

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
                position: fixed; top: 50%; left: 10px; transform: translateY(-50%); z-index: 1002;
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
                height: 90vh !important;
            }}
        </style>
        <div id="ctrl-panel">
            <div class="ctrl-section">
                <b>Metric</b>
                {radio_html}
            </div>
            <div class="ctrl-section" id="layers-section">
                <b>Layers</b>
                <label style="display:block;margin:1px 0;cursor:pointer;">
                    <input type="radio" name="layer" value="none"> None
                </label>
                <label style="display:block;margin:1px 0;cursor:pointer;">
                    <input type="radio" name="layer" value="bg"> Block Groups
                </label>
                <label style="display:block;margin:1px 0;cursor:pointer;">
                    <input type="radio" name="layer" value="blk" checked> Blocks (est.)
                </label>
                <label style="display:block;margin:1px 0;cursor:pointer;">
                    <input type="radio" name="layer" value="dots"> Population Dots (est.)
                </label>
            </div>
            <div class="ctrl-section">
                <b>School Community Zones</b>
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
            var ahFg = {ah_fg_name};
            var mlsFg = {mls_fg_name};
            var devFg = {dev_fg_name};
            var sapFg = {sap_fg_name};
            var schoolFg = {school_fg_name};
            var dots = {data_js};
            var allDotZones = {all_dot_zones_js};
            var allZoneNames = {all_zone_names_js};
            var masterSchools = {master_schools_js};
            var ahByZone = {ah_by_zone_js};
            var mlsSalesByZone = {mls_sales_by_zone_js};
            var mlsPriceByZone = {mls_price_by_zone_js};
            var mlsPpsfByZone = {mls_ppsf_by_zone_js};
            var mlsBedroomsByZone = {mls_bedrooms_by_zone_js};
            var devUnitsByZone = {dev_units_by_zone_js};
            var devCountsByZone = {dev_counts_by_zone_js};
            var sapfotacUnitsByZone = {sapfotac_units_by_zone_js};
            var sapfotacCountsByZone = {sapfotac_counts_by_zone_js};
            var sapfotacElemByZone = {sapfotac_elem_by_zone_js};
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
            // Dots not added to map initially - only shown in Race/Ethnicity mode

            var legendBox = document.getElementById('dot-legend-box');
            var zoneStrip = document.getElementById('zone-strip');

            // Move zone-strip after the map so it scrolls below
            var mapDiv = document.querySelector('.folium-map');
            if (mapDiv) mapDiv.parentElement.appendChild(zoneStrip);

            // ── Zone card rebuild ──
            var currentLayout = '';  // 'histogram', 'barplot', 'housing', 'mls', 'dev', or 'race'
            var lastMetricIdx = metricNames.length - 1;

            // Helper: detect special metric types by name
            function isHousingMetric(idx) {{
                return metricNames[idx] === 'Affordable Housing Units';
            }}
            function isMlsMetric(idx) {{
                var n = metricNames[idx];
                return n && n.indexOf('Housing Market') === 0;
            }}
            function isDevMetric(idx) {{
                return metricNames[idx] === 'Planned Developments (CH Active Dev)';
            }}
            function isSapfotacMetric(idx) {{
                return metricNames[idx] === 'Planned Developments (SAPFOTAC)';
            }}
            function isCountOrZoneMetric(idx) {{
                return isHousingMetric(idx) || isMlsMetric(idx) || isDevMetric(idx) || isSapfotacMetric(idx);
            }}

            function rebuildZoneCards() {{
                zoneStrip.innerHTML = '';
                var isRace = (currentMetric === 0);
                var isHousing = isHousingMetric(currentMetric);
                var isMls = isMlsMetric(currentMetric);
                var isDev = isDevMetric(currentMetric);
                var isSapfotac = isSapfotacMetric(currentMetric);
                var isIncome = (!isRace && !isHousing && !isMls && !isDev && !isSapfotac && metricPrefixes[currentMetric] === '$');
                var isPct = (!isRace && !isIncome && !isHousing && !isMls && !isDev && !isSapfotac);

                if (isRace) {{
                    currentLayout = 'race';
                    var card = document.createElement('div');
                    card.className = 'zone-card';
                    card.innerHTML = '<div class="zone-card-avg" style="padding:10px;">Select a metric to see zone distributions</div>';
                    zoneStrip.appendChild(card);
                }} else if (isSapfotac) {{
                    currentLayout = 'sapfotac';
                    zoneStrip.innerHTML =
                        '<div id="sap-grid" style="display:grid;grid-template-columns:1fr 1fr;gap:10px;max-width:960px;margin:0 auto;padding:8px;">' +
                        '  <div><div class="barplot-title">Total Projects by Zone</div>' +
                        '    <canvas id="bar-sap-count" width="440" height="320"></canvas></div>' +
                        '  <div><div class="barplot-title">Total Units by Zone</div>' +
                        '    <canvas id="bar-sap-units" width="440" height="320"></canvas></div>' +
                        '  <div><div class="barplot-title">Elementary Students by Zone</div>' +
                        '    <canvas id="bar-sap-elem" width="440" height="320"></canvas></div>' +
                        '  <div></div>' +
                        '</div>';
                }} else if (isDev) {{
                    currentLayout = 'dev';
                    zoneStrip.innerHTML =
                        '<div id="dev-grid" style="display:grid;grid-template-columns:1fr 1fr;gap:10px;max-width:960px;margin:0 auto;padding:8px;">' +
                        '  <div><div class="barplot-title">Total Expected Units by Zone</div>' +
                        '    <canvas id="bar-dev-units" width="440" height="320"></canvas></div>' +
                        '  <div><div class="barplot-title">Number of Developments by Zone</div>' +
                        '    <canvas id="bar-dev-count" width="440" height="320"></canvas></div>' +
                        '</div>';
                }} else if (isHousing) {{
                    currentLayout = 'housing';
                    zoneStrip.innerHTML =
                        '<div id="barplot-panel" style="display:flex;gap:12px;width:100%;">' +
                        '  <div style="flex:1;max-width:500px;margin:0 auto;">' +
                        '    <div class="barplot-title">Total Affordable Housing Units by Zone</div>' +
                        '    <canvas id="bar-ah" width="460" height="320"></canvas>' +
                        '  </div>' +
                        '</div>';
                }} else if (isMls) {{
                    currentLayout = 'mls';
                    zoneStrip.innerHTML =
                        '<div id="mls-grid" style="display:grid;grid-template-columns:1fr 1fr;gap:10px;max-width:960px;margin:0 auto;padding:8px;">' +
                        '  <div><div class="barplot-title">Homes Sold</div>' +
                        '    <canvas id="bar-mls-sales" width="440" height="320"></canvas></div>' +
                        '  <div><div class="barplot-title">Median Home Price</div>' +
                        '    <canvas id="bar-mls-price" width="440" height="320"></canvas></div>' +
                        '  <div><div class="barplot-title">Median Price / Sq Ft</div>' +
                        '    <canvas id="bar-mls-ppsf" width="440" height="320"></canvas></div>' +
                        '  <div><div class="barplot-title">Bedrooms Distribution</div>' +
                        '    <canvas id="hist-bedrooms" width="440" height="320"></canvas></div>' +
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

            function updateLayerToggle() {{
                // Remove all BG and Block layers first
                for (var i = 0; i < bgLayers.length; i++) {{
                    if (map.hasLayer(bgLayers[i])) map.removeLayer(bgLayers[i]);
                }}
                for (var i = 0; i < blkLayers.length; i++) {{
                    if (map.hasLayer(blkLayers[i])) map.removeLayer(blkLayers[i]);
                }}
                // Get selected layer radio value
                var selectedRadio = document.querySelector('input[name="layer"]:checked');
                var selected = selectedRadio ? selectedRadio.value : 'none';

                // Handle Population Dots layer option
                var isRace = (currentMetric === 0);
                var isMarkerMetric = isCountOrZoneMetric(currentMetric);
                if (selected === 'dots') {{
                    // Show dots when "Population Dots" layer selected (except for AH/MLS metrics)
                    if (!isMarkerMetric && !map.hasLayer(dotFg)) dotFg.addTo(map);
                    if (isMarkerMetric && map.hasLayer(dotFg)) map.removeLayer(dotFg);
                }} else {{
                    // For other layers, dots only show for Race/Ethnicity metric
                    if (isRace) {{
                        if (!map.hasLayer(dotFg)) dotFg.addTo(map);
                    }} else {{
                        if (map.hasLayer(dotFg)) map.removeLayer(dotFg);
                    }}
                }}

                // Add selected choropleth layer (if not 'none', not 'dots', and not Race/Ethnicity mode)
                if (selected !== 'none' && selected !== 'dots' && currentMetric > 0 && !isMarkerMetric) {{
                    var idx = currentMetric - 1;
                    if (selected === 'bg' && idx < bgLayers.length) bgLayers[idx].addTo(map);
                    if (selected === 'blk' && idx < blkLayers.length) blkLayers[idx].addTo(map);
                }}

                // Ensure school markers stay on top
                if (schoolFg) schoolFg.bringToFront();
            }}

            function switchZoneType(idx) {{
                // Hide all zone FeatureGroups
                for (var i = 0; i < zoneFgs.length; i++) {{
                    if (map.hasLayer(zoneFgs[i])) map.removeLayer(zoneFgs[i]);
                }}
                currentZoneType = idx;
                zoneFgs[idx].addTo(map);
                // Ensure school markers stay on top
                if (schoolFg) schoolFg.bringToFront();
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

            // Zone colors for bedroom histogram stacking
            var zoneHslColors = (function() {{
                var colors = [];
                var n = masterSchools.length;
                for (var i = 0; i < n; i++) {{
                    var hue = Math.round(i * 360 / n);
                    colors.push('hsl(' + hue + ',60%,55%)');
                }}
                return colors;
            }})();

            function drawBedroomHistogram(canvasId, bedroomArrays, zoneNames) {{
                var canvas = document.getElementById(canvasId);
                if (!canvas) return;
                var ctx = canvas.getContext('2d');
                var W = canvas.width, H = canvas.height;
                ctx.clearRect(0, 0, W, H);

                // Check if any data exists
                var hasData = false;
                for (var i = 0; i < bedroomArrays.length; i++) {{
                    if (bedroomArrays[i] && bedroomArrays[i].length > 0) {{ hasData = true; break; }}
                }}
                if (!hasData) {{
                    ctx.fillStyle = '#999'; ctx.font = '12px sans-serif';
                    ctx.fillText('No bedroom data available', 20, H / 2);
                    return;
                }}

                var binLabels = ['1 BR', '2 BR', '3 BR', '4 BR', '5+ BR'];
                var nBins = binLabels.length;
                var nZones = zoneNames.length;

                // Count per bin per zone
                var counts = [];  // [bin][zone]
                for (var b = 0; b < nBins; b++) {{
                    counts.push(new Array(nZones).fill(0));
                }}
                for (var zi = 0; zi < nZones; zi++) {{
                    var arr = bedroomArrays[zi] || [];
                    for (var k = 0; k < arr.length; k++) {{
                        var br = arr[k];
                        var bin = (br >= 5) ? 4 : (br >= 1 ? br - 1 : 0);
                        counts[bin][zi]++;
                    }}
                }}

                // Find max stacked height
                var maxStack = 0;
                for (var b = 0; b < nBins; b++) {{
                    var s = 0;
                    for (var zi = 0; zi < nZones; zi++) s += counts[b][zi];
                    if (s > maxStack) maxStack = s;
                }}
                if (maxStack === 0) maxStack = 1;

                var leftPad = 35, rightPad = 10, topPad = 8, botPad = 55;
                var chartW = W - leftPad - rightPad;
                var chartH = H - topPad - botPad;
                var barW = Math.floor(chartW / nBins) - 4;

                // Draw bars
                for (var b = 0; b < nBins; b++) {{
                    var x = leftPad + b * (barW + 4) + 2;
                    var yOff = 0;
                    for (var zi = 0; zi < nZones; zi++) {{
                        if (counts[b][zi] === 0) continue;
                        var segH = (counts[b][zi] / maxStack) * chartH;
                        ctx.fillStyle = zoneHslColors[zi];
                        ctx.fillRect(x, topPad + chartH - yOff - segH, barW, segH);
                        yOff += segH;
                    }}
                    // Bin label
                    ctx.fillStyle = '#333'; ctx.font = '10px sans-serif';
                    ctx.textAlign = 'center';
                    ctx.fillText(binLabels[b], x + barW / 2, topPad + chartH + 14);
                }}

                // Y-axis label
                ctx.fillStyle = '#999'; ctx.font = '10px sans-serif';
                ctx.textAlign = 'right';
                ctx.fillText(maxStack.toString(), leftPad - 4, topPad + 6);
                ctx.fillText('0', leftPad - 4, topPad + chartH);

                // Compact legend (below bin labels)
                ctx.font = '8px sans-serif'; ctx.textAlign = 'left';
                var lx = leftPad, ly = topPad + chartH + 26;
                function shortN(s) {{ return s.replace(' Elementary', '').replace(' Bilingue', ''); }}
                for (var zi = 0; zi < nZones; zi++) {{
                    ctx.fillStyle = zoneHslColors[zi];
                    ctx.fillRect(lx, ly, 8, 8);
                    ctx.fillStyle = '#333';
                    ctx.fillText(shortN(zoneNames[zi]), lx + 10, ly + 7);
                    lx += ctx.measureText(shortN(zoneNames[zi])).width + 16;
                    if (lx > W - 40) {{ lx = leftPad; ly += 11; }}
                }}
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

                var mi = currentMetric - 1;
                var pfx = metricPrefixes[currentMetric];
                var sfx = metricSuffixes[currentMetric];
                var isHousing = isHousingMetric(currentMetric);
                var isMls = isMlsMetric(currentMetric);
                var isDev = isDevMetric(currentMetric);
                var isSapfotac = isSapfotacMetric(currentMetric);
                var isIncome = (!isHousing && !isMls && !isDev && !isSapfotac && pfx === '$');
                var nSchools = masterSchools.length;
                var zoneNames = allZoneNames[currentZoneType];
                var nZones = zoneNames.length;

                // Special case: Affordable Housing - compute zone totals dynamically
                // NOTE: dotZones[i] are master school indices (0..nSchools-1),
                // so we must use nSchools (not nZones) for array sizing and
                // masterSchools for labels to keep labels aligned with values.
                if (isHousing) {{
                    // Sum ah_units per zone (using unique blocks per zone)
                    var ahMi = mi;  // blockValues index for ah_units
                    var zoneTotals = [];
                    var seenBlocks = [];  // Track which blocks counted per zone
                    for (var zi = 0; zi < nSchools; zi++) {{
                        zoneTotals.push(0);
                        seenBlocks.push({{}});
                    }}
                    for (var i = 0; i < dots.length; i++) {{
                        var zi = dotZones[i];
                        if (zi >= 0 && zi < nSchools) {{
                            var bi = dots[i][3];  // block index
                            if (!seenBlocks[zi][bi]) {{
                                seenBlocks[zi][bi] = true;
                                var ahVal = blockValues[bi] && blockValues[bi][ahMi];
                                if (ahVal !== null && ahVal !== undefined) {{
                                    zoneTotals[zi] += ahVal;
                                }}
                            }}
                        }}
                    }}
                    drawBarplot('bar-ah', masterSchools, zoneTotals, 'count');
                    return;
                }}

                // Special case: Planned Developments — draw 2 charts
                // Data arrays are indexed by masterSchools, so use masterSchools as labels
                if (isDev) {{
                    drawBarplot('bar-dev-units', masterSchools, devUnitsByZone[currentZoneType], 'count');
                    drawBarplot('bar-dev-count', masterSchools, devCountsByZone[currentZoneType], 'count');
                    return;
                }}

                // Special case: SAPFOTAC Planned Developments — draw 3 charts
                if (isSapfotac) {{
                    drawBarplot('bar-sap-count', masterSchools, sapfotacCountsByZone[currentZoneType], 'count');
                    drawBarplot('bar-sap-units', masterSchools, sapfotacUnitsByZone[currentZoneType], 'count');
                    drawBarplot('bar-sap-elem', masterSchools, sapfotacElemByZone[currentZoneType], 'count');
                    return;
                }}

                // Special case: MLS Housing Market — draw all 4 charts
                // Data arrays are indexed by masterSchools, so use masterSchools as labels
                if (isMls) {{
                    drawBarplot('bar-mls-sales', masterSchools, mlsSalesByZone[currentZoneType], 'count');
                    drawBarplot('bar-mls-price', masterSchools, mlsPriceByZone[currentZoneType], 'dollar');
                    drawBarplot('bar-mls-ppsf', masterSchools, mlsPpsfByZone[currentZoneType], 'dollar');
                    drawBedroomHistogram('hist-bedrooms', mlsBedroomsByZone[currentZoneType], masterSchools);
                    return;
                }}

                var vmin = metricRanges[mi][0];
                var vmax = metricRanges[mi][1];

                // Collect per-school values (always all 11 schools via master indices)
                var schoolVals = [];
                var schoolDotCounts = [];
                for (var si = 0; si < nSchools; si++) {{
                    schoolVals.push([]);
                    schoolDotCounts.push(0);
                }}
                for (var i = 0; i < dots.length; i++) {{
                    var si = dotZones[i];
                    if (si >= 0 && si < nSchools) {{
                        schoolDotCounts[si]++;
                        var v = blockValues[dots[i][3]][mi];
                        if (v !== null) schoolVals[si].push(v);
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

            function updateMetric(idx) {{
                var prevLayout = currentLayout;
                currentMetric = idx;
                // Determine what the new layout should be
                var isRace = (idx === 0);
                var isHousing = isHousingMetric(idx);
                var isMls = isMlsMetric(idx);
                var isDev = isDevMetric(idx);
                var isSapfotac = isSapfotacMetric(idx);
                var isIncome = (!isRace && !isHousing && !isMls && !isDev && !isSapfotac && metricPrefixes[idx] === '$');
                var needLayout = isRace ? 'race' : (isSapfotac ? 'sapfotac' : (isDev ? 'dev' : (isHousing ? 'housing' : (isMls ? 'mls' : (isIncome ? 'histogram' : 'barplot')))));
                if (needLayout !== prevLayout) {{
                    rebuildZoneCards();
                }}

                // Hide Layers section for Race/Ethnicity, AH, MLS, Dev, and SAPFOTAC metrics
                var layersSection = document.getElementById('layers-section');
                if (layersSection) {{
                    layersSection.style.display = (isRace || isHousing || isMls || isDev || isSapfotac) ? 'none' : 'block';
                }}

                // Affordable Housing markers only visible in AH mode
                if (isHousing && ahFg) {{
                    if (!map.hasLayer(ahFg)) ahFg.addTo(map);
                }} else if (ahFg) {{
                    if (map.hasLayer(ahFg)) map.removeLayer(ahFg);
                }}

                // MLS markers visible in MLS modes
                if (isMls && mlsFg) {{
                    if (!map.hasLayer(mlsFg)) mlsFg.addTo(map);
                }} else if (mlsFg) {{
                    if (map.hasLayer(mlsFg)) map.removeLayer(mlsFg);
                }}

                // Planned development markers visible in Dev mode
                if (isDev && devFg) {{
                    if (!map.hasLayer(devFg)) devFg.addTo(map);
                }} else if (devFg) {{
                    if (map.hasLayer(devFg)) map.removeLayer(devFg);
                }}

                // SAPFOTAC markers visible in SAPFOTAC mode
                if (isSapfotac && sapFg) {{
                    if (!map.hasLayer(sapFg)) sapFg.addTo(map);
                }} else if (sapFg) {{
                    if (map.hasLayer(sapFg)) map.removeLayer(sapFg);
                }}

                recolorDots();
                updateLegend();
                updateLayerToggle();  // Handles dot/choropleth visibility based on layer selection
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

            var layerRadios = document.querySelectorAll('input[name="layer"]');
            for (var r = 0; r < layerRadios.length; r++) {{
                layerRadios[r].addEventListener('change', function() {{ updateLayerToggle(); }});
            }}
            // Zones always visible — toggled by zone-type radios only

            // ── Initial state ──
            updateMetric(0);  // Initialize with Race/Ethnicity selected
            // Ensure school markers start on top
            if (schoolFg) schoolFg.bringToFront();
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
        ("pct_below_185_poverty", "% Below 185% Poverty (Free/Reduced Lunch Proxy)", "%"),
        ("pct_minority", "% Minority", "%"),
        ("median_hh_income", "Median Household Income", "$"),
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

### ACS 5-Year Estimates ({ACS_YEAR}, Block Group Level)

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
- **Affordable housing:** `data/cache/affordable_housing.gpkg` (Town of Chapel Hill ArcGIS, 2025)
- **MLS home sales:** `data/cache/mls_home_sales.gpkg` (Triangle MLS 2023-2025, geocoded)
- **Planned developments:** `data/cache/planned_developments.gpkg` (Town of Chapel Hill, geocoded)

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

3. **5-Year Rolling Average:** ACS {ACS_YEAR} 5-Year estimates represent data collected
   {ACS_YEAR - 4}-{ACS_YEAR}, not a single point in time.

4. **Attendance Zone vs. Actual Enrollment:** Demographics of an attendance zone
   describe the resident population, not actual school enrollment. Families may
   choose charter, private, or magnet schools, and transfer policies allow enrollment
   outside the home zone.

5. **Area-Weighting Assumptions:** Uniform population distribution within block groups
   is assumed. Dasymetric refinement at the block level (for dots) partially addresses
   this but is not applied to block group statistics.

6. **Temporal Mismatch:** ACS data ({ACS_YEAR - 4}-{ACS_YEAR}), Decennial data (2020), and attendance
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

    # ── 5a. Load affordable housing data ───────────────────────────────
    affordable_housing = None
    ah_path = DATA_CACHE / "affordable_housing.gpkg"
    if ah_path.exists():
        affordable_housing = gpd.read_file(ah_path)
        _progress(f"Loaded {len(affordable_housing)} affordable housing units")
    else:
        _progress("Note: affordable_housing.gpkg not found (run affordable_housing.py to download)")

    # ── 5b. Load MLS home sales data ──────────────────────────────────
    mls_data = None
    if MLS_CACHE.exists():
        mls_data = gpd.read_file(MLS_CACHE)
        _progress(f"Loaded {len(mls_data)} MLS home sales")
    else:
        _progress("Note: mls_home_sales.gpkg not found (run mls_geocode.py to create)")

    # ── 5c. Load planned developments data ─────────────────────────────
    planned_dev = None
    if DEV_CACHE.exists():
        planned_dev = gpd.read_file(DEV_CACHE)
        _progress(f"Loaded {len(planned_dev)} planned developments")
    else:
        _progress("Note: planned_developments.gpkg not found (run planned_dev_geocode.py to create)")

    # ── 5d. Load SAPFOTAC planned developments data ────────────────────
    sapfotac_dev = None
    if SAPFOTAC_CSV.exists():
        df = pd.read_csv(SAPFOTAC_CSV)
        df = df.dropna(subset=["lat", "lon"])
        sapfotac_dev = gpd.GeoDataFrame(
            df, geometry=gpd.points_from_xy(df["lon"], df["lat"]), crs=CRS_WGS84,
        )
        _progress(f"Loaded {len(sapfotac_dev)} SAPFOTAC planned developments")
    else:
        _progress("Note: SAPFOTAC CSV not found (data/raw/properties/planned/SAPFOTAC_2025_future_residential.csv)")

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

        # Add affordable housing counts per zone
        if affordable_housing is not None and len(affordable_housing) > 0:
            # Ensure CRS matches
            ah_wgs = affordable_housing.to_crs(CRS_WGS84)
            zones_wgs = zones.to_crs(CRS_WGS84)

            # Spatial join: which zone does each unit fall in?
            ah_with_zones = gpd.sjoin(
                ah_wgs,
                zones_wgs[["school", "geometry"]],
                how="left",
                predicate="within"
            )

            # Aggregate: total units per zone
            ah_by_zone = ah_with_zones.groupby("school").size().reset_index(name="ah_total_units")

            # Merge into zone_demographics
            zone_demographics = zone_demographics.merge(
                ah_by_zone, on="school", how="left"
            )
            zone_demographics["ah_total_units"] = zone_demographics["ah_total_units"].fillna(0).astype(int)
            _progress(f"Added affordable housing counts to {len(ah_by_zone)} zones")

        # Add MLS home sales aggregates per zone
        if mls_data is not None and len(mls_data) > 0:
            mls_wgs = mls_data.to_crs(CRS_WGS84)
            zones_wgs = zones.to_crs(CRS_WGS84)

            mls_with_zones = gpd.sjoin(
                mls_wgs,
                zones_wgs[["school", "geometry"]],
                how="left",
                predicate="within"
            )

            # Aggregate per zone: count, median price, median ppsf
            mls_zone_agg = mls_with_zones.dropna(subset=["school"]).groupby("school").agg(
                mls_total_sales=("close_price", "size"),
                mls_median_price=("close_price", "median"),
                mls_median_ppsf=("price_per_sqft", "median"),
            ).reset_index()

            zone_demographics = zone_demographics.merge(
                mls_zone_agg, on="school", how="left"
            )
            zone_demographics["mls_total_sales"] = zone_demographics["mls_total_sales"].fillna(0).astype(int)
            _progress(f"Added MLS sales data to {len(mls_zone_agg)} zones")

        # Add planned development aggregates per zone
        if planned_dev is not None and len(planned_dev) > 0:
            dev_wgs = planned_dev.to_crs(CRS_WGS84)
            zones_wgs = zones.to_crs(CRS_WGS84)

            dev_with_zones = gpd.sjoin(
                dev_wgs,
                zones_wgs[["school", "geometry"]],
                how="left",
                predicate="within"
            )

            dev_zone_agg = dev_with_zones.dropna(subset=["school"]).groupby("school").agg(
                dev_total_units=("expected_units", "sum"),
                dev_count=("expected_units", "size"),
            ).reset_index()

            zone_demographics = zone_demographics.merge(
                dev_zone_agg, on="school", how="left"
            )
            zone_demographics["dev_total_units"] = zone_demographics["dev_total_units"].fillna(0).astype(int)
            zone_demographics["dev_count"] = zone_demographics["dev_count"].fillna(0).astype(int)
            _progress(f"Added planned development data to {len(dev_zone_agg)} zones")

        # Add SAPFOTAC planned development aggregates per zone
        if sapfotac_dev is not None and len(sapfotac_dev) > 0:
            sap_wgs = sapfotac_dev.to_crs(CRS_WGS84)
            zones_wgs = zones.to_crs(CRS_WGS84)

            sap_with_zones = gpd.sjoin(
                sap_wgs,
                zones_wgs[["school", "geometry"]],
                how="left",
                predicate="within"
            )

            sap_zone_agg = sap_with_zones.dropna(subset=["school"]).groupby("school").agg(
                sapfotac_total_units=("total_units_remaining", "sum"),
                sapfotac_count=("total_units_remaining", "size"),
                sapfotac_elem_students=("students_elementary", "sum"),
            ).reset_index()

            zone_demographics = zone_demographics.merge(
                sap_zone_agg, on="school", how="left"
            )
            zone_demographics["sapfotac_total_units"] = zone_demographics["sapfotac_total_units"].fillna(0).astype(int)
            zone_demographics["sapfotac_count"] = zone_demographics["sapfotac_count"].fillna(0).astype(int)
            zone_demographics["sapfotac_elem_students"] = zone_demographics["sapfotac_elem_students"].fillna(0).astype(int)
            _progress(f"Added SAPFOTAC development data to {len(sap_zone_agg)} zones")

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

        # Add block-level affordable housing counts
        if affordable_housing is not None and len(affordable_housing) > 0:
            ah_wgs = affordable_housing.to_crs(CRS_WGS84)
            blocks_wgs = enriched_blocks.to_crs(CRS_WGS84)

            # Spatial join: count AH units per block
            ah_with_blocks = gpd.sjoin(
                ah_wgs[["geometry"]],
                blocks_wgs[["GEOID20", "geometry"]],
                how="left",
                predicate="within"
            )
            ah_by_block = ah_with_blocks.groupby("GEOID20").size().reset_index(name="ah_units")

            # Merge into enriched_blocks
            enriched_blocks = enriched_blocks.merge(ah_by_block, on="GEOID20", how="left")
            enriched_blocks["ah_units"] = enriched_blocks["ah_units"].fillna(0).astype(int)
            _progress(f"  Added AH counts to {(enriched_blocks['ah_units'] > 0).sum()} blocks")

        # Add block-level MLS aggregates
        if mls_data is not None and len(mls_data) > 0:
            mls_wgs = mls_data.to_crs(CRS_WGS84)
            blocks_wgs = enriched_blocks.to_crs(CRS_WGS84)

            mls_with_blocks = gpd.sjoin(
                mls_wgs[["close_price", "price_per_sqft", "geometry"]],
                blocks_wgs[["GEOID20", "geometry"]],
                how="left",
                predicate="within"
            )

            mls_block_agg = mls_with_blocks.dropna(subset=["GEOID20"]).groupby("GEOID20").agg(
                mls_sales_count=("close_price", "size"),
                mls_median_price=("close_price", "median"),
                mls_median_ppsf=("price_per_sqft", "median"),
            ).reset_index()

            enriched_blocks = enriched_blocks.merge(mls_block_agg, on="GEOID20", how="left")
            enriched_blocks["mls_sales_count"] = enriched_blocks["mls_sales_count"].fillna(0).astype(int)
            # Leave mls_median_price and mls_median_ppsf as NaN for blocks with no sales
            _progress(f"  Added MLS data to {(enriched_blocks['mls_sales_count'] > 0).sum()} blocks")

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
            mls_data=mls_data,
            planned_dev=planned_dev,
            sapfotac_dev=sapfotac_dev,
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
