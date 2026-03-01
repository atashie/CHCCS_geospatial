"""
Merge parcel shapefile with FOIA tax assessment Excel, classify residential
parcels, and produce two GeoPackage outputs for downstream analysis.

Inputs:
- data/raw/properties/parcels/parview.shp  (59,307 parcels, Orange County)
- data/raw/properties/Data Request 1-27-2026.xlsx  (247K sale/assessment rows)

Outputs:
- data/raw/properties/combined_data_polys.gpkg      — all CHCCS parcels (polygons)
- data/raw/properties/combined_data_centroids.gpkg   — residential-only centroids (slim)

Run once to produce the cached files; school_desert.py reads the centroids file.
"""

import sys
from pathlib import Path

import geopandas as gpd
import pandas as pd

PROJECT_ROOT = Path(__file__).parent.parent
PROPERTIES_DIR = PROJECT_ROOT / "data" / "raw" / "properties"

SHAPEFILE = PROPERTIES_DIR / "parcels" / "parview.shp"
EXCEL_FILE = PROPERTIES_DIR / "Data Request 1-27-2026.xlsx"

OUT_POLYS = PROPERTIES_DIR / "combined_data_polys.gpkg"
OUT_CENTROIDS = PROPERTIES_DIR / "combined_data_centroids.gpkg"

# Land-use code prefixes that count as residential
RESIDENTIAL_LUC_PREFIXES = ("100", "110", "120", "630", "EXHA", "EXED")

# Slim columns to keep for the centroids file
CENTROID_COLUMNS = [
    "PIN", "is_residential", "primary_luc", "imp_vac",
    "RATECODE", "CALC_ACRES", "SQFT", "YEARBUILT", "BLDGCNT",
    "VALUATION", "CONDONAME", "SUBDIVISIO",
    "appraised_value", "assessed_value", "sale_date", "sale_price",
    "years_since_sale", "geometry",
]


def load_and_dedup_excel() -> pd.DataFrame:
    """Load the FOIA Excel and de-duplicate to one row per parcel.

    Strategy: keep rows where Latest Sale == 1.  If ties remain, keep the
    row with the most recent Sale Date.  If still tied, keep the first row.
    """
    print("  Reading Excel file ...")
    df = pd.read_excel(EXCEL_FILE, sheet_name="Sheet")
    print(f"  Raw Excel rows: {len(df):,}")

    # Coerce Sale Date to datetime (Excel may parse some cells as time-only)
    df["Sale Date"] = pd.to_datetime(df["Sale Date"], errors="coerce")

    # Prefer rows marked as latest sale
    df = df.sort_values(
        ["Latest Sale", "Sale Date"],
        ascending=[False, False],
        na_position="last",
    )
    df = df.drop_duplicates(subset="Parcel ID", keep="first")
    print(f"  After de-dup (one per parcel): {len(df):,}")
    return df


def merge_and_classify(gdf: gpd.GeoDataFrame, excel: pd.DataFrame) -> gpd.GeoDataFrame:
    """Left-join Excel columns onto shapefile and add derived fields."""

    # Rename Excel columns to short names for the merge
    rename = {
        "Parcel ID": "PIN_excel",
        "Total Appraised Value": "appraised_value",
        "Total Assessed Value": "assessed_value",
        "Sale Date": "sale_date",
        "Sale Price": "sale_price",
        "Sold As Vacant": "sold_as_vacant",
        "Imp/Vac/YI": "imp_vac",
        "Primary LUC": "primary_luc",
    }
    excel_slim = excel.rename(columns=rename)[list(rename.values())].copy()

    print("  Merging shapefile with Excel ...")
    merged = gdf.merge(excel_slim, left_on="PIN", right_on="PIN_excel", how="left")
    merged.drop(columns=["PIN_excel"], inplace=True)

    matched = merged["primary_luc"].notna().sum()
    print(f"  Matched: {matched:,} / {len(merged):,} ({100*matched/len(merged):.1f}%)")

    # is_residential: True if primary_luc starts with a residential prefix
    merged["is_residential"] = merged["primary_luc"].fillna("").str.startswith(
        RESIDENTIAL_LUC_PREFIXES
    )

    # years_since_sale
    merged["sale_date"] = pd.to_datetime(merged["sale_date"], errors="coerce")
    merged["years_since_sale"] = merged["sale_date"].apply(
        lambda d: 2026 - d.year if pd.notna(d) else None
    )

    return merged


def main():
    print("=" * 60)
    print("Property Data Processing")
    print("=" * 60)

    # 1. Load shapefile
    print("\n[1/5] Loading parcel shapefile ...")
    gdf = gpd.read_file(SHAPEFILE)
    print(f"  Total parcels: {len(gdf):,}  CRS: {gdf.crs}")

    # 2. Load & de-dup Excel
    print("\n[2/5] Loading Excel assessment data ...")
    excel = load_and_dedup_excel()

    # 3. Merge & classify
    print("\n[3/5] Merging and classifying ...")
    merged = merge_and_classify(gdf, excel)

    residential_count = merged["is_residential"].sum()
    print(f"  Residential parcels (all Orange County): {residential_count:,}")

    # 4. Filter to CHCCS, reproject, save polygon file
    print("\n[4/5] Filtering to CHCCS and saving polygon GeoPackage ...")
    chccs = merged[merged["SCHOOL_SYS"] == "Chapel Hill/Carrboro Schools"].copy()
    print(f"  CHCCS parcels: {len(chccs):,}")

    chccs = chccs.to_crs(epsg=4326)
    chccs.to_file(OUT_POLYS, driver="GPKG")
    print(f"  Saved: {OUT_POLYS}")

    # 5. Residential centroids (slim)
    #    Compute centroids in the original projected CRS (NAD83 StatePlane),
    #    then reproject the point geometries to WGS84.
    print("\n[5/5] Creating residential centroids GeoPackage ...")
    res = merged[
        (merged["SCHOOL_SYS"] == "Chapel Hill/Carrboro Schools")
        & merged["is_residential"]
    ].copy()
    print(f"  CHCCS residential parcels: {len(res):,}")

    res["geometry"] = res.geometry.centroid  # accurate in projected CRS
    res = res.to_crs(epsg=4326)

    # Keep only slim columns (drop any that are missing gracefully)
    keep = [c for c in CENTROID_COLUMNS if c in res.columns]
    res = res[keep]

    res.to_file(OUT_CENTROIDS, driver="GPKG")
    print(f"  Saved: {OUT_CENTROIDS}")

    # Summary
    print("\n" + "=" * 60)
    print("Done!")
    print(f"  Polygons:  {OUT_POLYS}  ({len(chccs):,} rows)")
    print(f"  Centroids: {OUT_CENTROIDS}  ({len(res):,} rows)")
    print("=" * 60)


if __name__ == "__main__":
    main()
