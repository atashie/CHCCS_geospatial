"""
MLS Home Sales Geocoding

Geocodes addresses from MLS home sales data (2023-2025) and caches as GeoPackage.
Primary geocoder: Census Bureau batch API (no API key needed).
Fallback: Nominatim via geopy for unmatched addresses.

Usage:
    python src/mls_geocode.py                # Geocode and cache
    python src/mls_geocode.py --cache-only   # Load cached data only

Input:
    data/raw/MLS/2023-2025 CHCCS Home Sales.csv

Output:
    data/cache/mls_home_sales.gpkg
"""

from __future__ import annotations

import argparse
import io
import re
import sys
import time
from pathlib import Path

import geopandas as gpd
import pandas as pd
import requests
from shapely.geometry import Point

PROJECT_ROOT = Path(__file__).resolve().parent.parent
DATA_RAW = PROJECT_ROOT / "data" / "raw" / "MLS"
DATA_CACHE = PROJECT_ROOT / "data" / "cache"
DISTRICT_CACHE = DATA_CACHE / "chccs_district_boundary.gpkg"

MLS_RAW = DATA_RAW / "2023-2025 CHCCS Home Sales.csv"
MLS_CACHE = DATA_CACHE / "mls_home_sales.gpkg"

CRS_WGS84 = "EPSG:4326"

CENSUS_BATCH_URL = (
    "https://geocoding.geo.census.gov/geocoder/geographies/addressbatch"
)


def _progress(msg: str):
    print(f"  {msg}")


def load_mls_raw() -> pd.DataFrame:
    """Load raw MLS CSV and parse price/date columns."""
    if not MLS_RAW.exists():
        print(f"Error: MLS file not found: {MLS_RAW}")
        sys.exit(1)

    df = pd.read_csv(MLS_RAW)
    _progress(f"Loaded {len(df)} raw MLS records")

    # Parse Close Price: strip $ and commas
    df["close_price"] = (
        df["Close Price"]
        .astype(str)
        .str.replace("$", "", regex=False)
        .str.replace(",", "", regex=False)
        .apply(pd.to_numeric, errors="coerce")
    )

    # Parse Price Per SQFT
    df["price_per_sqft"] = (
        df["Price Per SQFT"]
        .astype(str)
        .str.replace("$", "", regex=False)
        .str.replace(",", "", regex=False)
        .apply(pd.to_numeric, errors="coerce")
    )

    # Parse Close Date
    df["close_date"] = pd.to_datetime(df["Close Date"], errors="coerce")

    # Keep relevant columns
    df = df.rename(columns={"Address": "address"})
    df = df[["address", "close_price", "price_per_sqft", "close_date",
             "Living Area", "Year Built", "Property Sub Type",
             "Subdivision-Free Text", "Bedrooms Total"]].copy()
    df = df.rename(columns={
        "Living Area": "living_area",
        "Year Built": "year_built",
        "Property Sub Type": "property_type",
        "Subdivision-Free Text": "subdivision",
        "Bedrooms Total": "bedrooms",
    })
    df["bedrooms"] = pd.to_numeric(df["bedrooms"], errors="coerce")

    # Drop rows with no address or no price
    df = df.dropna(subset=["address", "close_price"])
    _progress(f"  {len(df)} records with valid address and price")

    return df.reset_index(drop=True)


def geocode_census_batch(df: pd.DataFrame) -> pd.DataFrame:
    """Geocode addresses via Census Bureau batch API.

    The batch API accepts CSV with columns: id, street, city, state, zip.
    We don't have zip codes, so leave blank.

    Returns DataFrame with lat/lon columns added (NaN for failures).
    """
    _progress("Geocoding via Census Bureau batch API ...")

    # Build CSV for batch submission
    # Try Chapel Hill first for all addresses
    lines = []
    for idx, row in df.iterrows():
        addr = str(row["address"]).strip()
        lines.append(f"{idx},\"{addr}\",Chapel Hill,NC,")

    csv_content = "\n".join(lines)

    # Submit batch (API accepts up to ~10,000 at once)
    # Process in chunks of 1000 to be safe
    chunk_size = 1000
    all_results = {}

    for start in range(0, len(lines), chunk_size):
        chunk = lines[start:start + chunk_size]
        chunk_csv = "\n".join(chunk)
        _progress(f"  Sending batch {start // chunk_size + 1} "
                  f"({len(chunk)} addresses) ...")

        try:
            resp = requests.post(
                CENSUS_BATCH_URL,
                files={"addressFile": ("addresses.csv", chunk_csv, "text/csv")},
                data={"benchmark": "Public_AR_Current", "vintage": "Current_Current"},
                timeout=120,
            )
            resp.raise_for_status()

            # Parse response CSV
            for line in resp.text.strip().split("\n"):
                if not line.strip():
                    continue
                # Census batch returns: id, input_address, match_status,
                # match_type, matched_address, lon_lat, tiger_id, side
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
        retry_indices = []
        for idx in df[unmatched_mask].index:
            addr = str(df.at[idx, "address"]).strip()
            retry_lines.append(f"{idx},\"{addr}\",Carrboro,NC,")
            retry_indices.append(idx)

        for start in range(0, len(retry_lines), chunk_size):
            chunk = retry_lines[start:start + chunk_size]
            chunk_csv = "\n".join(chunk)
            try:
                resp = requests.post(
                    CENSUS_BATCH_URL,
                    files={"addressFile": ("addresses.csv", chunk_csv, "text/csv")},
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
    geolocator = Nominatim(user_agent="chccs_geospatial_mls_analysis")
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


def geocode_mls(cache_only: bool = False) -> gpd.GeoDataFrame | None:
    """Main entry point: geocode MLS data and return GeoDataFrame.

    Args:
        cache_only: If True, only load from cache (no geocoding).

    Returns:
        GeoDataFrame with point geometry and sale attributes, or None.
    """
    if cache_only:
        if MLS_CACHE.exists():
            gdf = gpd.read_file(MLS_CACHE)
            _progress(f"Loaded {len(gdf)} cached MLS sales")
            return gdf
        else:
            _progress("MLS cache not found (run mls_geocode.py to create)")
            return None

    # Load and geocode
    df = load_mls_raw()
    df = geocode_census_batch(df)
    df = geocode_nominatim_fallback(df)

    # Summary
    total = len(df)
    geocoded = df["lat"].notna().sum()
    failed = total - geocoded
    _progress(f"Final: {geocoded}/{total} geocoded ({geocoded / total * 100:.1f}%), "
              f"{failed} failed")

    if failed > 0:
        failed_addrs = df[df["lat"].isna()]["address"].tolist()
        _progress(f"  Failed addresses (first 20):")
        for addr in failed_addrs[:20]:
            _progress(f"    - {addr}")

    # Drop ungeocoded rows
    df = df.dropna(subset=["lat", "lon"])

    # Convert to float (may be object after pd.NA assignments)
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
    gdf.to_file(MLS_CACHE, driver="GPKG")
    _progress(f"Saved {len(gdf)} geocoded sales to {MLS_CACHE}")

    return gdf


def main():
    parser = argparse.ArgumentParser(
        description="Geocode MLS home sales data for CHCCS district"
    )
    parser.add_argument(
        "--cache-only", action="store_true",
        help="Only load cached data (no geocoding)",
    )
    args = parser.parse_args()

    print("=" * 60)
    print("MLS Home Sales Geocoding")
    print("=" * 60)

    gdf = geocode_mls(cache_only=args.cache_only)

    if gdf is not None and len(gdf) > 0:
        print(f"\nSummary:")
        print(f"  Total geocoded sales: {len(gdf)}")
        print(f"  Median close price: ${gdf['close_price'].median():,.0f}")
        print(f"  Median price/sqft: ${gdf['price_per_sqft'].median():,.0f}")
        print(f"  Date range: {gdf['close_date'].min():%Y-%m-%d} to "
              f"{gdf['close_date'].max():%Y-%m-%d}")
    else:
        print("\nNo data available.")

    print("=" * 60)


if __name__ == "__main__":
    main()
