"""
Geocode SAPFOTAC 2025 CSVs (future residential + rezoning approved).

Uses Census Bureau batch API (primary) + Nominatim fallback.
Writes lat/lon columns back into the source CSVs.
"""

from __future__ import annotations

import sys
from pathlib import Path

import pandas as pd
import requests

PROJECT_ROOT = Path(__file__).resolve().parent.parent
DATA_RAW = PROJECT_ROOT / "data" / "raw" / "properties" / "planned"

FUTURE_CSV = DATA_RAW / "SAPFOTAC_2025_future_residential.csv"
REZONING_CSV = DATA_RAW / "SAPFOTAC_2025_rezoning_approved.csv"

CENSUS_BATCH_URL = (
    "https://geocoding.geo.census.gov/geocoder/geographies/addressbatch"
)

# Addresses where Census geocoder returns wrong match — force Nominatim
_CENSUS_SKIP = {
    "119 Bennett Way",  # Census matches to Bennett Woods, wrong street
}


def _progress(msg: str):
    print(f"  {msg}")


def geocode_census_batch(df: pd.DataFrame) -> pd.DataFrame:
    """Geocode addresses via Census Bureau batch API with Chapel Hill + Carrboro."""
    _progress("Geocoding via Census Bureau batch API ...")

    df["lat"] = pd.NA
    df["lon"] = pd.NA

    # Try Chapel Hill first
    lines = []
    for idx, row in df.iterrows():
        addr = str(row["address"]).strip()
        lines.append(f'{idx},"{addr}",Chapel Hill,NC,')

    csv_content = "\n".join(lines)
    matched = 0

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
                            rid = int(rec_id)
                            df.at[rid, "lat"] = float(lat_str)
                            df.at[rid, "lon"] = float(lon_str)
                            matched += 1
                        except ValueError:
                            pass
    except Exception as e:
        _progress(f"  Census batch error: {e}")

    _progress(f"  Chapel Hill: {matched}/{len(df)} matched")

    # Retry unmatched with Carrboro
    unmatched_mask = df["lat"].isna()
    unmatched_count = int(unmatched_mask.sum())
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

        new_unmatched = int(df["lat"].isna().sum())
        carrboro_matched = unmatched_count - new_unmatched
        if carrboro_matched > 0:
            _progress(f"  Carrboro: {carrboro_matched} more matched")

    return df


def geocode_nominatim_fallback(df: pd.DataFrame) -> pd.DataFrame:
    """Geocode remaining unmatched via Nominatim."""
    try:
        from geopy.geocoders import Nominatim
        from geopy.extra.rate_limiter import RateLimiter
    except ImportError:
        _progress("geopy not installed — skipping Nominatim fallback")
        return df

    unmatched = df[df["lat"].isna()]
    if len(unmatched) == 0:
        return df

    _progress(f"Nominatim fallback for {len(unmatched)} unmatched ...")
    geolocator = Nominatim(user_agent="chccs_sapfotac_geocode")
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


def process_csv(path: Path) -> None:
    """Load CSV, geocode, write lat/lon back."""
    print(f"\n{'=' * 60}")
    print(f"Processing: {path.name}")
    print(f"{'=' * 60}")

    df = pd.read_csv(path)
    _progress(f"Loaded {len(df)} rows")

    # Drop existing lat/lon if re-running
    for col in ["lat", "lon"]:
        if col in df.columns:
            df = df.drop(columns=[col])

    df = geocode_census_batch(df)
    df = geocode_nominatim_fallback(df)

    geocoded = int(df["lat"].notna().sum())
    total = len(df)
    _progress(f"Final: {geocoded}/{total} geocoded")

    if geocoded < total:
        failed = df[df["lat"].isna()]
        _progress("Failed addresses:")
        for _, row in failed.iterrows():
            _progress(f"  - {row['project']}: {row['address']}")

    # Round to 6 decimal places
    df["lat"] = df["lat"].astype(float).round(6)
    df["lon"] = df["lon"].astype(float).round(6)

    # Write to temp file first, then replace (handles locked files)
    tmp_path = path.with_suffix(".tmp.csv")
    df.to_csv(tmp_path, index=False)
    try:
        tmp_path.replace(path)
    except PermissionError:
        _progress(f"WARNING: {path.name} is locked — saved to {tmp_path.name} instead")
        _progress(f"  Close the file and rename {tmp_path.name} -> {path.name}")
        return
    _progress(f"Wrote {path.name}")


def main():
    for csv_path in [FUTURE_CSV, REZONING_CSV]:
        if not csv_path.exists():
            print(f"Error: {csv_path} not found")
            sys.exit(1)
        process_csv(csv_path)

    print(f"\n{'=' * 60}")
    print("Done. Both CSVs updated with lat/lon columns.")
    print(f"{'=' * 60}")


if __name__ == "__main__":
    main()
