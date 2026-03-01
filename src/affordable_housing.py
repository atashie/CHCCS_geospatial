"""
Affordable Housing Data — Download & Quality Assessment.

Downloads unit-level affordable housing records from the Town of Chapel Hill's
public ArcGIS Feature Service and produces a data quality assessment.

Data source:
    Town of Chapel Hill Affordable Housing Dashboard (2025)
    ArcGIS Feature Service — 1,168 unit-level records, publicly accessible.
    https://services2.arcgis.com/7KRXAKALbBGlCW77/arcgis/rest/services/
      Affordable_Housing_Data_2025/FeatureServer/6

Usage:
    python src/affordable_housing.py               # download + assess
    python src/affordable_housing.py --cache-only  # assess cached data only

Output:
    data/cache/affordable_housing.gpkg
    data/processed/AFFORDABLE_HOUSING_DATA.md
"""

from __future__ import annotations

import argparse
from datetime import datetime
from pathlib import Path

import geopandas as gpd
import pandas as pd
import requests
from shapely.geometry import Point

# ---------------------------------------------------------------------------
# Paths
# ---------------------------------------------------------------------------
PROJECT_ROOT = Path(__file__).resolve().parent.parent
DATA_CACHE = PROJECT_ROOT / "data" / "cache"
DATA_PROCESSED = PROJECT_ROOT / "data" / "processed"
HOUSING_CACHE = DATA_CACHE / "affordable_housing.gpkg"
REPORT_PATH = DATA_PROCESSED / "AFFORDABLE_HOUSING_DATA.md"

# ---------------------------------------------------------------------------
# ArcGIS Feature Service endpoint
# ---------------------------------------------------------------------------
ARCGIS_URL = (
    "https://services2.arcgis.com/7KRXAKALbBGlCW77/arcgis/rest/services/"
    "Affordable_Housing_Data_2025/FeatureServer/6/query"
)
PAGE_SIZE = 1000

# ---------------------------------------------------------------------------
# Expected bounding box (Chapel Hill / Orange County area, WGS84)
# ---------------------------------------------------------------------------
EXPECTED_BBOX = {
    "min_lon": -79.25,
    "max_lon": -78.90,
    "min_lat": 35.85,
    "max_lat": 36.10,
}

CRS_WGS84 = "EPSG:4326"


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------
def _progress(msg: str):
    """Print a progress message."""
    print(f"  ... {msg}")


# ---------------------------------------------------------------------------
# Step 1: Download from ArcGIS Feature Service
# ---------------------------------------------------------------------------
def download_affordable_housing(cache_only: bool = False) -> gpd.GeoDataFrame:
    """Download all affordable housing records via ArcGIS REST API.

    Paginates through the feature service and converts to a GeoDataFrame
    with WGS84 (EPSG:4326) point geometries.
    """
    if HOUSING_CACHE.exists():
        _progress(f"Loading cached data from {HOUSING_CACHE}")
        return gpd.read_file(HOUSING_CACHE)

    if cache_only:
        raise FileNotFoundError(
            f"Housing cache not found at {HOUSING_CACHE}. "
            "Run without --cache-only to download."
        )

    _progress("Downloading affordable housing data from ArcGIS Feature Service ...")

    all_features: list[dict] = []
    offset = 0

    while True:
        params = {
            "where": "1=1",
            "outFields": "*",
            "outSR": "4326",
            "returnGeometry": "true",
            "f": "json",
            "resultRecordCount": PAGE_SIZE,
            "resultOffset": offset,
        }
        r = requests.get(ARCGIS_URL, params=params, timeout=60)
        r.raise_for_status()
        data = r.json()

        if "error" in data:
            raise RuntimeError(f"ArcGIS API error: {data['error']}")

        features = data.get("features", [])
        if not features:
            break

        all_features.extend(features)
        _progress(f"  Fetched {len(all_features)} records so far ...")

        if len(features) < PAGE_SIZE:
            break
        offset += PAGE_SIZE

    _progress(f"Downloaded {len(all_features)} total features")

    # Convert to GeoDataFrame
    rows = []
    skipped = 0
    for feat in all_features:
        attrs = feat.get("attributes", {})
        geom = feat.get("geometry")
        if geom and geom.get("x") is not None and geom.get("y") is not None:
            attrs["geometry"] = Point(geom["x"], geom["y"])
        else:
            skipped += 1
            attrs["geometry"] = None
        rows.append(attrs)

    if skipped:
        _progress(f"WARNING: {skipped} features had no geometry")

    gdf = gpd.GeoDataFrame(rows, crs=CRS_WGS84)

    # Drop rows with null geometry
    null_geom = gdf.geometry.isna().sum()
    if null_geom:
        _progress(f"Dropping {null_geom} records with null geometry")
        gdf = gdf.dropna(subset=["geometry"])

    # Cache
    DATA_CACHE.mkdir(parents=True, exist_ok=True)
    gdf.to_file(HOUSING_CACHE, driver="GPKG")
    _progress(f"Cached {len(gdf)} records to {HOUSING_CACHE}")

    return gdf


# ---------------------------------------------------------------------------
# Step 2: Data quality assessment
# ---------------------------------------------------------------------------
def assess_data_quality(gdf: gpd.GeoDataFrame) -> dict:
    """Run comprehensive data quality checks and return findings dict."""
    findings = {}

    # --- Basic stats ---
    findings["record_count"] = len(gdf)
    findings["column_count"] = len(gdf.columns)
    findings["columns"] = list(gdf.columns)

    # --- Field completeness ---
    completeness = {}
    for col in gdf.columns:
        if col == "geometry":
            continue
        total = len(gdf)
        null_count = gdf[col].isna().sum()
        # Also count empty strings for object columns
        empty_count = 0
        if gdf[col].dtype == "object":
            empty_count = (gdf[col].fillna("").astype(str).str.strip() == "").sum() - null_count
            empty_count = max(0, empty_count)
        completeness[col] = {
            "null": int(null_count),
            "empty": int(empty_count),
            "populated": int(total - null_count - empty_count),
            "pct_populated": round((total - null_count - empty_count) / total * 100, 1),
        }
    findings["completeness"] = completeness

    # --- Coordinate validity ---
    coords = {}
    lons = gdf.geometry.x
    lats = gdf.geometry.y
    coords["lon_range"] = (float(lons.min()), float(lons.max()))
    coords["lat_range"] = (float(lats.min()), float(lats.max()))

    out_of_bounds = gdf[
        (lons < EXPECTED_BBOX["min_lon"])
        | (lons > EXPECTED_BBOX["max_lon"])
        | (lats < EXPECTED_BBOX["min_lat"])
        | (lats > EXPECTED_BBOX["max_lat"])
    ]
    coords["out_of_bounds"] = len(out_of_bounds)

    # Duplicate coordinates
    coord_pairs = pd.DataFrame({"lon": lons, "lat": lats})
    dup_coords = coord_pairs.duplicated(keep=False)
    coords["duplicate_coordinate_count"] = int(dup_coords.sum())
    coords["unique_locations"] = int((~coord_pairs.duplicated(keep="first")).sum())
    findings["coordinates"] = coords

    # --- Categorical distributions ---
    categorical_fields = [
        "AMIServed", "RentalOwnership", "Provider", "ProjectType",
        "UnitType", "City", "Jurisdiction",
    ]
    cat_distributions = {}
    for field in categorical_fields:
        if field in gdf.columns:
            vc = gdf[field].value_counts(dropna=False)
            cat_distributions[field] = {
                "unique_values": int(gdf[field].nunique(dropna=False)),
                "distribution": {str(k): int(v) for k, v in vc.items()},
            }
    findings["categorical"] = cat_distributions

    # --- Numeric fields ---
    numeric_fields = ["Bedrooms", "EstSquareFeet"]
    # Also look for funding columns dynamically
    funding_cols = [c for c in gdf.columns if any(
        kw in c.lower() for kw in ["fund", "amount", "cost", "invest", "subsid"]
    )]
    numeric_fields.extend(funding_cols)
    # Deduplicate while preserving order
    numeric_fields = list(dict.fromkeys(numeric_fields))

    numeric_stats = {}
    for field in numeric_fields:
        if field not in gdf.columns:
            continue
        col = pd.to_numeric(gdf[field], errors="coerce")
        if col.notna().sum() == 0:
            continue
        numeric_stats[field] = {
            "count": int(col.notna().sum()),
            "min": float(col.min()),
            "max": float(col.max()),
            "mean": round(float(col.mean()), 1),
            "median": round(float(col.median()), 1),
        }
    findings["numeric"] = numeric_stats

    # --- Date fields ---
    date_fields = ["YearBuilt", "Affordability_End_Date"]
    # Also check for other date-like columns
    date_like = [c for c in gdf.columns if any(
        kw in c.lower() for kw in ["date", "year"]
    )]
    date_fields.extend(date_like)
    date_fields = list(dict.fromkeys(date_fields))

    date_stats = {}
    for field in date_fields:
        if field not in gdf.columns:
            continue
        col = gdf[field]
        non_null = col.dropna()
        if len(non_null) == 0:
            date_stats[field] = {"populated": 0}
            continue
        # Try to interpret as numeric year or date string
        stats = {"populated": int(len(non_null)), "null": int(col.isna().sum())}
        numeric_vals = pd.to_numeric(non_null, errors="coerce").dropna()
        if len(numeric_vals) > 0:
            # Epoch milliseconds (ArcGIS date fields)
            if numeric_vals.min() > 1e10:
                try:
                    dates = pd.to_datetime(numeric_vals, unit="ms")
                    stats["min_date"] = str(dates.min().date())
                    stats["max_date"] = str(dates.max().date())
                    stats["interpretation"] = "epoch_ms"
                except Exception:
                    stats["min_raw"] = float(numeric_vals.min())
                    stats["max_raw"] = float(numeric_vals.max())
            else:
                stats["min"] = float(numeric_vals.min())
                stats["max"] = float(numeric_vals.max())
                if 1900 <= numeric_vals.min() <= 2100:
                    stats["interpretation"] = "year"
        date_stats[field] = stats
    findings["dates"] = date_stats

    # --- Unique projects ---
    project_fields = ["ProjectName", "Project_Name", "Project", "Development"]
    project_col = None
    for pf in project_fields:
        if pf in gdf.columns:
            project_col = pf
            break
    if project_col:
        findings["unique_projects"] = int(gdf[project_col].nunique(dropna=True))
        findings["project_column"] = project_col
    else:
        # Check for any column with "project" in the name
        proj_cols = [c for c in gdf.columns if "project" in c.lower()]
        if proj_cols:
            findings["project_column"] = proj_cols[0]
            findings["unique_projects"] = int(gdf[proj_cols[0]].nunique(dropna=True))

    # --- Address completeness ---
    addr_fields = [c for c in gdf.columns if any(
        kw in c.lower() for kw in ["address", "addr", "street"]
    )]
    findings["address_fields"] = addr_fields

    return findings


def print_quality_report(gdf: gpd.GeoDataFrame, findings: dict) -> None:
    """Print a formatted quality assessment report to console."""
    print("\n" + "=" * 60)
    print("AFFORDABLE HOUSING DATA — QUALITY ASSESSMENT")
    print("=" * 60)

    # --- Overview ---
    print(f"\nRecords: {findings['record_count']}")
    print(f"Columns: {findings['column_count']}")
    if "unique_projects" in findings:
        print(f"Unique projects ({findings.get('project_column', '?')}): {findings['unique_projects']}")
    coords = findings["coordinates"]
    print(f"Unique locations: {coords['unique_locations']}")
    print(f"Duplicate coordinates: {coords['duplicate_coordinate_count']} records share a location with another")
    print(f"Lon range: [{coords['lon_range'][0]:.4f}, {coords['lon_range'][1]:.4f}]")
    print(f"Lat range: [{coords['lat_range'][0]:.4f}, {coords['lat_range'][1]:.4f}]")
    if coords["out_of_bounds"] > 0:
        print(f"WARNING: {coords['out_of_bounds']} points outside expected bounding box")
    else:
        print("All points within expected bounding box")

    # --- Field completeness ---
    print("\n--- Field Completeness ---")
    print(f"{'Field':<35} {'Populated':>10} {'Null':>6} {'Empty':>6} {'%Pop':>6}")
    print("-" * 67)
    for col, stats in sorted(findings["completeness"].items()):
        print(
            f"{col:<35} {stats['populated']:>10} {stats['null']:>6} "
            f"{stats['empty']:>6} {stats['pct_populated']:>5.1f}%"
        )

    # --- Categorical distributions ---
    print("\n--- Categorical Distributions ---")
    for field, info in findings.get("categorical", {}).items():
        print(f"\n  {field} ({info['unique_values']} unique values):")
        for val, count in sorted(info["distribution"].items(), key=lambda x: -x[1]):
            pct = count / findings["record_count"] * 100
            label = val if val != "nan" else "(null)"
            print(f"    {label:<40} {count:>5}  ({pct:>5.1f}%)")

    # --- Numeric fields ---
    if findings.get("numeric"):
        print("\n--- Numeric Fields ---")
        for field, stats in findings["numeric"].items():
            print(
                f"  {field}: n={stats['count']}, "
                f"range=[{stats['min']:.0f}, {stats['max']:.0f}], "
                f"mean={stats['mean']:.1f}, median={stats['median']:.1f}"
            )

    # --- Date fields ---
    if findings.get("dates"):
        print("\n--- Date Fields ---")
        for field, stats in findings["dates"].items():
            parts = [f"  {field}: populated={stats.get('populated', 0)}"]
            if "min_date" in stats:
                parts.append(f"range=[{stats['min_date']}, {stats['max_date']}]")
            elif "min" in stats:
                interp = stats.get("interpretation", "numeric")
                parts.append(f"range=[{stats['min']:.0f}, {stats['max']:.0f}] ({interp})")
            print(", ".join(parts))

    print("\n" + "=" * 60)


# ---------------------------------------------------------------------------
# Step 3: Generate markdown report
# ---------------------------------------------------------------------------
def generate_report_markdown(gdf: gpd.GeoDataFrame, findings: dict) -> None:
    """Write comprehensive data quality report to markdown file."""
    today = datetime.now().strftime("%Y-%m-%d")
    lines = []

    lines.append("# Affordable Housing Data — Source Documentation & Quality Assessment")
    lines.append("")
    lines.append(f"**Download date:** {today}")
    lines.append(f"**Records:** {findings['record_count']} housing units")
    lines.append(f"**Columns:** {findings['column_count']}")
    lines.append("")
    lines.append("---")
    lines.append("")

    # --- Data source ---
    lines.append("## Data Source")
    lines.append("")
    lines.append("**Provider:** Town of Chapel Hill")
    lines.append("**Dashboard:** [Chapel Hill Affordable Housing](https://www.chapelhillaffordablehousing.org/tracking-our-progress)")
    lines.append("**Service type:** ArcGIS Feature Service (public, no authentication)")
    lines.append("**Endpoint:**")
    lines.append("```")
    lines.append("https://services2.arcgis.com/7KRXAKALbBGlCW77/arcgis/rest/services/")
    lines.append("  Affordable_Housing_Data_2025/FeatureServer/6/query")
    lines.append("```")
    lines.append("")
    lines.append("Each record represents one housing **unit** (not a development/project).")
    lines.append("Native CRS is NC State Plane (WKID 102719); downloaded with `outSR=4326` for WGS84.")
    lines.append("")

    # --- Download methodology ---
    lines.append("## Download Methodology")
    lines.append("")
    lines.append("1. Query the ArcGIS REST API with `where=1=1`, `outFields=*`, `outSR=4326`")
    lines.append(f"2. Paginate via `resultOffset`/`resultRecordCount` (page size = {PAGE_SIZE})")
    lines.append("3. Convert point geometry features (x/y) to Shapely `Point` objects")
    lines.append("4. Build a GeoDataFrame with EPSG:4326 CRS")
    lines.append(f"5. Cache to `{HOUSING_CACHE.relative_to(PROJECT_ROOT).as_posix()}`")
    lines.append("")

    # --- Field inventory ---
    lines.append("## Field Inventory")
    lines.append("")
    lines.append("| Field | Type | Populated | Null | % Complete |")
    lines.append("|-------|------|-----------|------|------------|")
    for col in sorted(findings["completeness"].keys()):
        stats = findings["completeness"][col]
        dtype = str(gdf[col].dtype) if col in gdf.columns else "?"
        lines.append(
            f"| {col} | {dtype} | {stats['populated']} | {stats['null']} | "
            f"{stats['pct_populated']:.1f}% |"
        )
    lines.append("")

    # --- Geographic coverage ---
    lines.append("## Geographic Coverage")
    lines.append("")
    coords = findings["coordinates"]
    lines.append(f"- **Longitude range:** [{coords['lon_range'][0]:.4f}, {coords['lon_range'][1]:.4f}]")
    lines.append(f"- **Latitude range:** [{coords['lat_range'][0]:.4f}, {coords['lat_range'][1]:.4f}]")
    lines.append(f"- **Unique locations:** {coords['unique_locations']}")
    lines.append(f"- **Records sharing coordinates:** {coords['duplicate_coordinate_count']}")
    lines.append(f"  - This is expected: multiple units in the same building/development share a single point.")
    if coords["out_of_bounds"] > 0:
        lines.append(f"- **WARNING:** {coords['out_of_bounds']} points fall outside the expected Chapel Hill bounding box")
    else:
        lines.append("- All points fall within the expected Chapel Hill / Orange County bounding box")
    lines.append("")

    # --- Categorical distributions ---
    if findings.get("categorical"):
        lines.append("## Categorical Field Distributions")
        lines.append("")
        for field, info in findings["categorical"].items():
            lines.append(f"### {field}")
            lines.append("")
            lines.append(f"{info['unique_values']} unique values:")
            lines.append("")
            lines.append("| Value | Count | % |")
            lines.append("|-------|-------|---|")
            for val, count in sorted(info["distribution"].items(), key=lambda x: -x[1]):
                pct = count / findings["record_count"] * 100
                label = val if val != "nan" else "*(null)*"
                lines.append(f"| {label} | {count} | {pct:.1f}% |")
            lines.append("")

    # --- Numeric fields ---
    if findings.get("numeric"):
        lines.append("## Numeric Field Ranges")
        lines.append("")
        lines.append("| Field | Count | Min | Max | Mean | Median |")
        lines.append("|-------|-------|-----|-----|------|--------|")
        for field, stats in findings["numeric"].items():
            lines.append(
                f"| {field} | {stats['count']} | {stats['min']:.0f} | "
                f"{stats['max']:.0f} | {stats['mean']:.1f} | {stats['median']:.1f} |"
            )
        lines.append("")

    # --- Date fields ---
    if findings.get("dates"):
        lines.append("## Date Fields")
        lines.append("")
        for field, stats in findings["dates"].items():
            lines.append(f"### {field}")
            lines.append("")
            lines.append(f"- **Populated:** {stats.get('populated', 0)} / {findings['record_count']}")
            if "min_date" in stats:
                lines.append(f"- **Range:** {stats['min_date']} to {stats['max_date']}")
                lines.append(f"- **Format:** Epoch milliseconds (converted to dates)")
            elif "min" in stats:
                interp = stats.get("interpretation", "numeric")
                lines.append(f"- **Range:** {stats['min']:.0f} to {stats['max']:.0f} ({interp})")
            lines.append("")

    # --- Unique projects ---
    if "unique_projects" in findings:
        lines.append("## Project Summary")
        lines.append("")
        lines.append(f"- **Project field:** `{findings.get('project_column', '?')}`")
        lines.append(f"- **Unique projects:** {findings['unique_projects']}")
        lines.append(f"- **Total units:** {findings['record_count']}")
        lines.append("")

    # --- Known limitations ---
    lines.append("## Known Limitations & Caveats")
    lines.append("")
    lines.append("1. **Unit-level, not project-level:** Each record is a single unit. "
                 "Multiple units in the same building share identical coordinates.")
    lines.append("2. **Point geometry only:** Locations are single points, not building footprints. "
                 "Spatial precision is at the development level, not the individual unit.")
    lines.append("3. **Snapshot in time:** This dataset reflects the state at download date. "
                 "The Town updates the dashboard periodically; records may change.")
    lines.append("4. **NC State Plane origin:** Native coordinates are in NC State Plane (WKID 102719). "
                 "Conversion to WGS84 is handled server-side via `outSR=4326`.")
    lines.append("5. **No independent verification:** Field values (AMI levels, unit types, dates) "
                 "are as reported by the Town. No cross-referencing with HUD or LIHTC data was performed.")
    lines.append("")

    # --- Reproduction ---
    lines.append("## Reproduction")
    lines.append("")
    lines.append("```bash")
    lines.append("# Download and assess (writes cache + this report)")
    lines.append("python src/affordable_housing.py")
    lines.append("")
    lines.append("# Assess cached data only (no network requests)")
    lines.append("python src/affordable_housing.py --cache-only")
    lines.append("```")
    lines.append("")

    DATA_PROCESSED.mkdir(parents=True, exist_ok=True)
    REPORT_PATH.write_text("\n".join(lines), encoding="utf-8")
    _progress(f"Wrote report to {REPORT_PATH}")


# ---------------------------------------------------------------------------
# Main
# ---------------------------------------------------------------------------
def main():
    parser = argparse.ArgumentParser(
        description="Download & assess Chapel Hill affordable housing data"
    )
    parser.add_argument(
        "--cache-only",
        action="store_true",
        help="Use only cached data; do not download from ArcGIS",
    )
    args = parser.parse_args()

    print("=" * 60)
    print("Affordable Housing Data — Download & Quality Assessment")
    print("Chapel Hill, NC")
    print("=" * 60)

    # 1. Download / load
    print("\n[1/3] Loading affordable housing data ...")
    gdf = download_affordable_housing(cache_only=args.cache_only)
    print(f"  Loaded {len(gdf)} records, {len(gdf.columns)} columns")

    # 2. Assess
    print("\n[2/3] Assessing data quality ...")
    findings = assess_data_quality(gdf)
    print_quality_report(gdf, findings)

    # 3. Write report
    print("\n[3/3] Generating documentation ...")
    generate_report_markdown(gdf, findings)

    print("\nDone!")


if __name__ == "__main__":
    main()
