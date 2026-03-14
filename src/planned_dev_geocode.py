"""
Planned Developments Geocoding

Geocodes addresses from Chapel Hill planned development data and caches as GeoPackage.
Primary geocoder: Census Bureau batch API (no API key needed).
Fallback: Nominatim via geopy for unmatched addresses.

Usage:
    python src/planned_dev_geocode.py                # Geocode and cache
    python src/planned_dev_geocode.py --cache-only   # Load cached data only

Input:
    data/raw/properties/planned/CH_Development-3_26.csv

Output:
    data/cache/planned_developments.gpkg
"""

from __future__ import annotations

import argparse
import re
import sys
from pathlib import Path

import geopandas as gpd
import pandas as pd
import requests
from shapely.geometry import Point

PROJECT_ROOT = Path(__file__).resolve().parent.parent
DATA_RAW = PROJECT_ROOT / "data" / "raw" / "properties" / "planned"
DATA_CACHE = PROJECT_ROOT / "data" / "cache"
DISTRICT_CACHE = DATA_CACHE / "chccs_district_boundary.gpkg"

DEV_RAW = DATA_RAW / "CH_Development-3_26.csv"
DEV_CACHE = DATA_CACHE / "planned_developments.gpkg"

CRS_WGS84 = "EPSG:4326"

CENSUS_BATCH_URL = (
    "https://geocoding.geo.census.gov/geocoder/geographies/addressbatch"
)


def _progress(msg: str):
    print(f"  {msg}")


# Address typo corrections
_ADDRESS_FIXES = {
    "Erwin Toad": "Erwin Road",
    "Weaver Diary": "Weaver Dairy",
    "Martin Luther Kind": "Martin Luther King",
}


def _fix_address(addr: str) -> str:
    """Fix known typos and simplify range/multi addresses."""
    addr = addr.strip()

    # Fix known typos
    for wrong, right in _ADDRESS_FIXES.items():
        addr = addr.replace(wrong, right)

    # Simplify "207 and 209 Meadowmont Lane" -> "207 Meadowmont Lane"
    addr = re.sub(r"^(\d+)\s+and\s+\d+\s+", r"\1 ", addr)

    # Simplify "1708 - 1712 Legion Road" -> "1708 Legion Road"
    addr = re.sub(r"^(\d+)\s*-\s*\d+\s+", r"\1 ", addr)

    # Simplify "101-110 Erwin Road" -> "101 Erwin Road"
    addr = re.sub(r"^(\d+)-\d+\s+", r"\1 ", addr)

    # Simplify "607-617 Martin Luther King Jr. Blvd" (already handled above)

    return addr.strip()


def load_dev_raw() -> pd.DataFrame:
    """Load raw planned development CSV."""
    if not DEV_RAW.exists():
        print(f"Error: Planned development file not found: {DEV_RAW}")
        sys.exit(1)

    df = pd.read_csv(DEV_RAW)
    # Strip column name whitespace
    df.columns = [c.strip() for c in df.columns]
    _progress(f"Loaded {len(df)} raw development records")

    # Rename columns
    df = df.rename(columns={
        "Name": "name",
        "Address": "address",
        "Number_of_Expected_Units": "expected_units",
    })

    # Parse expected_units
    df["expected_units"] = pd.to_numeric(df["expected_units"], errors="coerce")

    # Drop rows with no address or name
    df = df.dropna(subset=["address"])
    df = df[df["address"].str.strip() != ""]
    _progress(f"  {len(df)} records with valid address")

    # Clean names and addresses
    df["name"] = df["name"].str.strip()
    df["address"] = df["address"].apply(_fix_address)

    return df.reset_index(drop=True)


def geocode_census_batch(df: pd.DataFrame) -> pd.DataFrame:
    """Geocode addresses via Census Bureau batch API.

    Returns DataFrame with lat/lon columns added (NaN for failures).
    """
    _progress("Geocoding via Census Bureau batch API ...")

    # Build CSV for batch submission — try Chapel Hill first
    lines = []
    for idx, row in df.iterrows():
        addr = str(row["address"]).strip()
        lines.append(f'{idx},"{addr}",Chapel Hill,NC,')

    csv_content = "\n".join(lines)

    all_results = {}
    try:
        resp = requests.post(
            CENSUS_BATCH_URL,
            files={"addressFile": ("addresses.csv", csv_content, "text/csv")},
            data={"benchmark": "Public_AR_Current", "vintage": "Current_Current"},
            timeout=120,
        )
        resp.raise_for_status()

        for line in resp.text.strip().split("\n"):
            if not line.strip():
                continue
            parts = line.split('","')
            parts = [p.strip('"') for p in parts]
            if len(parts) >= 6:
                rec_id = parts[0].strip('"')
                match_status = parts[2]
                if match_status in ("Match", "Exact"):
                    lon_lat = parts[5]
                    if "," in lon_lat:
                        lon_str, lat_str = lon_lat.split(",")
                        try:
                            all_results[int(rec_id)] = (
                                float(lat_str), float(lon_str)
                            )
                        except ValueError:
                            pass
    except Exception as e:
        _progress(f"  Census batch error: {e}")

    # Apply results
    df["lat"] = pd.NA
    df["lon"] = pd.NA
    matched = 0
    for idx, (lat, lon) in all_results.items():
        if idx in df.index:
            df.at[idx, "lat"] = lat
            df.at[idx, "lon"] = lon
            matched += 1

    _progress(f"  Census batch: {matched}/{len(df)} matched "
              f"({matched / len(df) * 100:.1f}%)")

    # Retry unmatched with Carrboro
    unmatched_mask = df["lat"].isna()
    unmatched_count = unmatched_mask.sum()
    if unmatched_count > 0:
        _progress(f"  Retrying {unmatched_count} unmatched with Carrboro ...")
        retry_lines = []
        for idx in df[unmatched_mask].index:
            addr = str(df.at[idx, "address"]).strip()
            retry_lines.append(f'{idx},"{addr}",Carrboro,NC,')

        retry_csv = "\n".join(retry_lines)
        try:
            resp = requests.post(
                CENSUS_BATCH_URL,
                files={"addressFile": ("addresses.csv", retry_csv, "text/csv")},
                data={"benchmark": "Public_AR_Current", "vintage": "Current_Current"},
                timeout=120,
            )
            resp.raise_for_status()
            for line in resp.text.strip().split("\n"):
                if not line.strip():
                    continue
                parts = line.split('","')
                parts = [p.strip('"') for p in parts]
                if len(parts) >= 6:
                    rec_id = parts[0].strip('"')
                    match_status = parts[2]
                    if match_status in ("Match", "Exact"):
                        lon_lat = parts[5]
                        if "," in lon_lat:
                            lon_str, lat_str = lon_lat.split(",")
                            try:
                                rid = int(rec_id)
                                df.at[rid, "lat"] = float(lat_str)
                                df.at[rid, "lon"] = float(lon_str)
                                matched += 1
                            except ValueError:
                                pass
        except Exception as e:
            _progress(f"  Carrboro retry error: {e}")

        new_unmatched = df["lat"].isna().sum()
        carrboro_matched = unmatched_count - new_unmatched
        if carrboro_matched > 0:
            _progress(f"  Carrboro retry matched {carrboro_matched} more")

    return df


def geocode_nominatim_fallback(df: pd.DataFrame) -> pd.DataFrame:
    """Geocode remaining unmatched addresses via Nominatim."""
    try:
        from geopy.geocoders import Nominatim
        from geopy.extra.rate_limiter import RateLimiter
    except ImportError:
        _progress("geopy not installed — skipping Nominatim fallback")
        return df

    unmatched = df[df["lat"].isna()]
    if len(unmatched) == 0:
        return df

    _progress(f"Nominatim fallback for {len(unmatched)} unmatched addresses ...")
    geolocator = Nominatim(user_agent="chccs_geospatial_planned_dev")
    geocode = RateLimiter(geolocator.geocode, min_delay_seconds=1.0)

    matched = 0
    for idx, row in unmatched.iterrows():
        addr = str(row["address"]).strip()
        for city in ["Chapel Hill, NC", "Carrboro, NC"]:
            full_addr = f"{addr}, {city}"
            try:
                location = geocode(full_addr)
                if location:
                    df.at[idx, "lat"] = location.latitude
                    df.at[idx, "lon"] = location.longitude
                    matched += 1
                    break
            except Exception:
                continue

    _progress(f"  Nominatim matched {matched}/{len(unmatched)}")
    return df


def geocode_planned_dev(cache_only: bool = False) -> gpd.GeoDataFrame | None:
    """Main entry point: geocode planned developments and return GeoDataFrame.

    Args:
        cache_only: If True, only load from cache (no geocoding).

    Returns:
        GeoDataFrame with point geometry and development attributes, or None.
    """
    if cache_only:
        if DEV_CACHE.exists():
            gdf = gpd.read_file(DEV_CACHE)
            _progress(f"Loaded {len(gdf)} cached planned developments")
            return gdf
        else:
            _progress("Planned developments cache not found (run planned_dev_geocode.py to create)")
            return None

    # Load and geocode
    df = load_dev_raw()
    df = geocode_census_batch(df)
    df = geocode_nominatim_fallback(df)

    # Summary
    total = len(df)
    geocoded = df["lat"].notna().sum()
    failed = total - geocoded
    _progress(f"Final: {geocoded}/{total} geocoded ({geocoded / total * 100:.1f}%), "
              f"{failed} failed")

    if failed > 0:
        failed_rows = df[df["lat"].isna()]
        _progress("  Failed addresses:")
        for _, row in failed_rows.iterrows():
            _progress(f"    - {row['name']}: {row['address']}")

    # Drop ungeocoded rows
    df = df.dropna(subset=["lat", "lon"])

    # Convert to float
    df["lat"] = df["lat"].astype(float)
    df["lon"] = df["lon"].astype(float)

    # Create GeoDataFrame
    geometry = [Point(lon, lat) for lat, lon in zip(df["lat"], df["lon"])]
    gdf = gpd.GeoDataFrame(df, geometry=geometry, crs=CRS_WGS84)

    # Clip to district boundary
    if DISTRICT_CACHE.exists():
        district = gpd.read_file(DISTRICT_CACHE).to_crs(CRS_WGS84)
        before = len(gdf)
        gdf = gpd.clip(gdf, district)
        after = len(gdf)
        if before > after:
            _progress(f"  Clipped {before - after} records outside district boundary")
    else:
        _progress("  Warning: district boundary not found, skipping clip")

    # Save cache
    DATA_CACHE.mkdir(parents=True, exist_ok=True)
    gdf.to_file(DEV_CACHE, driver="GPKG")
    _progress(f"Saved {len(gdf)} geocoded developments to {DEV_CACHE}")

    return gdf


def main():
    parser = argparse.ArgumentParser(
        description="Geocode planned development data for CHCCS district"
    )
    parser.add_argument(
        "--cache-only", action="store_true",
        help="Only load cached data (no geocoding)",
    )
    args = parser.parse_args()

    print("=" * 60)
    print("Planned Developments Geocoding")
    print("=" * 60)

    gdf = geocode_planned_dev(cache_only=args.cache_only)

    if gdf is not None and len(gdf) > 0:
        print(f"\nSummary:")
        print(f"  Total geocoded developments: {len(gdf)}")
        print(f"  Total expected units: {gdf['expected_units'].sum():,.0f}")
        print(f"  Range: {gdf['expected_units'].min():.0f} – {gdf['expected_units'].max():.0f} units")
    else:
        print("\nNo data available.")

    print("=" * 60)


if __name__ == "__main__":
    main()
