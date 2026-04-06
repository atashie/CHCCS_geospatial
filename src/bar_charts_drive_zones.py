"""
Drive-Zone Bar Charts — per-school horizontal bar charts.

Generates all bars_*_drive.png images in assets/charts/:
  * bars_households_poverty_drive.png  (below_185_pov count)
  * bars_minority_residents_drive.png  (race_total − white_nh count)
  * bars_affordable_housing_drive.png  (spatial join → ah units)
  * bars_mls_sales_drive.png           (spatial join → MLS closed sales)
  * bars_no_vehicle_drive.png          (vehicles_zero count)
  * bars_children_5_9_drive.png        (male_5_9 + female_5_9 count)
  * bars_children_0_4_drive.png        (male_under_5 + female_under_5 count)
  * bars_sapfotac_students_drive.png   (spatial join → SAPFOTAC elem students)

Style: horizontal bars sorted descending, full-width light-gray tracks,
highlighted bars for Ephesus (red), Seawell (blue), and Glenwood (green).

Data sources:
  * census_dot_zone_demographics.csv (zone_type 'Nearest Drive')
  * school_desert_grid.csv → drive zone polygons (for spatial joins)
  * affordable_housing.gpkg, mls_home_sales.gpkg (spatial join targets)
  * SAPFOTAC_2025_future_residential.csv (projected elementary students)

Usage:
    python src/bar_charts_drive_zones.py
    python src/bar_charts_drive_zones.py --cache-only   # skip spatial joins
"""

from __future__ import annotations

import argparse
from pathlib import Path

import geopandas as gpd
import matplotlib.pyplot as plt
import pandas as pd
from shapely.geometry import box

PROJECT_ROOT = Path(__file__).resolve().parent.parent
CSV_IN = PROJECT_ROOT / "data" / "processed" / "census_dot_zone_demographics.csv"
GRID_CSV = PROJECT_ROOT / "data" / "processed" / "school_desert_grid.csv"
DISTRICT_CACHE = PROJECT_ROOT / "data" / "cache" / "chccs_district_boundary.gpkg"
AH_CACHE = PROJECT_ROOT / "data" / "cache" / "affordable_housing.gpkg"
MLS_CACHE = PROJECT_ROOT / "data" / "cache" / "mls_home_sales.gpkg"
SAPFOTAC_CSV = (PROJECT_ROOT / "data" / "raw" / "properties" / "planned"
                / "SAPFOTAC_2025_future_residential.csv")
OUT_DIR = PROJECT_ROOT / "assets" / "charts"

CRS_WGS84 = "EPSG:4326"
CRS_UTM17N = "EPSG:32617"

EPHESUS_COLOR = "#C6282B"
SEAWELL_COLOR = "#1585CD"
GLENWOOD_COLOR = "#2E7D32"
NEUTRAL_COLOR = "#CCCCCC"
TRACK_COLOR = "#EEEEEE"

FOOTER = ("Nearest Drive Zones (Dijkstra)  \u00b7  "
          "Highlights: Ephesus (#C6282B), Seawell (#1585CD), "
          "Glenwood (#2E7D32)")

plt.rcParams["font.family"] = "sans-serif"
plt.rcParams["font.sans-serif"] = ["Segoe UI", "Tahoma", "DejaVu Sans"]


def _progress(msg: str) -> None:
    print(f"  {msg}")


# ─────────────────────────────────────────────────────────────────────────────
# Data loading
# ─────────────────────────────────────────────────────────────────────────────

def _load_demo(zone_type: str = "Nearest Drive") -> pd.DataFrame:
    """Load demographics from the census CSV for the given zone_type."""
    if not CSV_IN.exists():
        raise FileNotFoundError(f"Missing {CSV_IN} — run the socioeconomic "
                                f"pipeline first.")
    df = pd.read_csv(CSV_IN)
    df = df[df["zone_type"] == zone_type].copy()
    if df.empty:
        raise RuntimeError(f"No '{zone_type}' rows in census CSV")
    # Derived columns
    df["children_5_9"] = df["male_5_9"].fillna(0) + df["female_5_9"].fillna(0)
    df["children_0_4"] = df["male_under_5"].fillna(0) + df["female_under_5"].fillna(0)
    df["minority_count"] = df["race_total"].fillna(0) - df["white_nh"].fillna(0)
    return df.reset_index(drop=True)


def _build_drive_zones() -> gpd.GeoDataFrame | None:
    """Build nearest-drive zone polygons from school_desert_grid.csv."""
    if not GRID_CSV.exists():
        _progress(f"Grid CSV not found: {GRID_CSV}")
        return None
    if not DISTRICT_CACHE.exists():
        _progress(f"District boundary not found: {DISTRICT_CACHE}")
        return None

    _progress("Building drive zone polygons from grid CSV ...")
    df = pd.read_csv(GRID_CSV, usecols=[
        "lat", "lon", "mode", "scenario", "nearest_school",
    ])
    df = df[(df["scenario"] == "baseline") & (df["mode"] == "drive")]
    df = df.dropna(subset=["nearest_school"])
    if df.empty:
        return None

    pts = gpd.GeoDataFrame(
        df, geometry=gpd.points_from_xy(df["lon"], df["lat"]), crs=CRS_WGS84,
    ).to_crs(CRS_UTM17N)
    half = 55
    pts["geometry"] = [
        box(g.x - half, g.y - half, g.x + half, g.y + half)
        for g in pts.geometry
    ]
    dissolved = pts.dissolve(by="nearest_school").reset_index()
    dissolved = dissolved.rename(columns={"nearest_school": "school"})

    district = gpd.read_file(DISTRICT_CACHE).to_crs(CRS_UTM17N)
    dissolved = gpd.clip(dissolved, district)
    mask = dissolved.geometry.geom_type.isin(["Polygon", "MultiPolygon"])
    dissolved = dissolved[mask][["school", "geometry"]].to_crs(CRS_WGS84)
    _progress(f"  Built {len(dissolved)} drive zones")
    return dissolved.reset_index(drop=True)


def _spatial_join_count(
    points_gpkg: Path, zones: gpd.GeoDataFrame, col_name: str,
) -> pd.DataFrame:
    """Spatial-join a point GeoPackage to drive zones; return counts."""
    if not points_gpkg.exists():
        _progress(f"  {points_gpkg.name} not found — {col_name} will be 0")
        return pd.DataFrame({"school": zones["school"], col_name: 0})

    pts = gpd.read_file(points_gpkg).to_crs(CRS_WGS84)
    joined = gpd.sjoin(pts, zones[["school", "geometry"]],
                       how="inner", predicate="within")
    counts = (joined.groupby("school").size()
              .reset_index(name=col_name))
    result = zones[["school"]].merge(counts, on="school", how="left")
    result[col_name] = result[col_name].fillna(0).astype(int)
    _progress(f"  {col_name}: {result[col_name].sum():,} total across "
              f"{(result[col_name] > 0).sum()} zones")
    return result


def _spatial_join_sum(
    csv_path: Path, zones: gpd.GeoDataFrame, value_col: str,
    out_col: str,
) -> pd.DataFrame:
    """Spatial-join a lat/lon CSV to drive zones; return sum of value_col."""
    if not csv_path.exists():
        _progress(f"  {csv_path.name} not found — {out_col} will be 0")
        return pd.DataFrame({"school": zones["school"], out_col: 0})

    raw = pd.read_csv(csv_path)
    raw = raw.dropna(subset=["lat", "lon", value_col])
    raw[value_col] = pd.to_numeric(raw[value_col], errors="coerce").fillna(0)
    pts = gpd.GeoDataFrame(
        raw, geometry=gpd.points_from_xy(raw["lon"], raw["lat"]),
        crs=CRS_WGS84,
    )
    joined = gpd.sjoin(pts, zones[["school", "geometry"]],
                       how="inner", predicate="within")
    sums = (joined.groupby("school")[value_col].sum()
            .reset_index().rename(columns={value_col: out_col}))
    result = zones[["school"]].merge(sums, on="school", how="left")
    result[out_col] = result[out_col].fillna(0).astype(int)
    _progress(f"  {out_col}: {result[out_col].sum():,} total across "
              f"{(result[out_col] > 0).sum()} zones")
    return result


# ─────────────────────────────────────────────────────────────────────────────
# Chart styling
# ─────────────────────────────────────────────────────────────────────────────

def _bar_color(school: str) -> str:
    if "Ephesus" in school:
        return EPHESUS_COLOR
    if "Seawell" in school:
        return SEAWELL_COLOR
    if "Glenwood" in school:
        return GLENWOOD_COLOR
    return NEUTRAL_COLOR


def _label_color(school: str) -> str:
    if "Ephesus" in school:
        return EPHESUS_COLOR
    if "Seawell" in school:
        return SEAWELL_COLOR
    if "Glenwood" in school:
        return GLENWOOD_COLOR
    return "#333333"


def _label_weight(school: str) -> str:
    if any(s in school for s in ("Ephesus", "Seawell", "Glenwood")):
        return "bold"
    return "normal"


def _short_name(school: str) -> str:
    return school.replace(" Elementary", "").replace(" Bilingue", "")


# ─────────────────────────────────────────────────────────────────────────────
# Chart drawing
# ─────────────────────────────────────────────────────────────────────────────

def draw_bar_chart(
    df: pd.DataFrame, metric_col: str, title: str, subtitle: str,
    value_fmt: str, out_path: Path,
) -> None:
    """Render a horizontal bar chart of metric_col per school."""
    data = df[["school", metric_col]].copy()
    data = data.sort_values(metric_col, ascending=True).reset_index(drop=True)

    fig, ax = plt.subplots(figsize=(6, 4.5), dpi=200)
    fig.patch.set_facecolor("white")
    ax.set_facecolor("white")

    y_pos = list(range(len(data)))
    colors = [_bar_color(s) for s in data["school"]]
    max_val = data[metric_col].max() if len(data) else 1.0

    # Background tracks (full width) for each row
    ax.barh(y_pos, [max_val] * len(data), color=TRACK_COLOR,
            edgecolor="none", height=0.9)
    # Value bars on top
    ax.barh(y_pos, data[metric_col], color=colors, edgecolor="none",
            height=0.9)

    # Hide axes / spines / ticks
    for spine in ax.spines.values():
        spine.set_visible(False)
    ax.set_xticks([])
    ax.set_yticks([])
    ax.grid(False)

    label_pad = max_val * 0.015
    value_pad = max_val * 0.015

    for i, row in data.iterrows():
        school = row["school"]
        val = row[metric_col]
        ax.text(-label_pad, i, _short_name(school),
                ha="right", va="center", fontsize=10,
                color=_label_color(school),
                fontweight=_label_weight(school))
        ax.text(val + value_pad, i, value_fmt.format(val),
                ha="left", va="center", fontsize=10,
                color=_label_color(school), fontweight="bold")

    ax.set_xlim(-max_val * 0.42, max_val * 1.18)
    ax.set_ylim(-0.6, len(data) - 0.4)

    fig.text(0.5, 0.955, title, ha="center", va="top",
             fontsize=14, fontweight="bold", color="#111111")
    fig.text(0.5, 0.905, subtitle, ha="center", va="top",
             fontsize=9.5, style="italic", color="#666666")
    fig.text(0.5, 0.02, FOOTER, ha="center", va="bottom",
             fontsize=8, color="#888888")

    plt.subplots_adjust(left=0.02, right=0.98, top=0.86, bottom=0.07)
    out_path.parent.mkdir(parents=True, exist_ok=True)
    fig.savefig(out_path, dpi=200, facecolor="white",
                bbox_inches=None, pad_inches=0.05)
    plt.close(fig)
    _progress(f"Saved {out_path.relative_to(PROJECT_ROOT)}")


# ─────────────────────────────────────────────────────────────────────────────
# Entry point
# ─────────────────────────────────────────────────────────────────────────────

def main() -> None:
    parser = argparse.ArgumentParser(
        description="Generate drive-zone bar charts for CHCCS schools"
    )
    parser.add_argument("--cache-only", action="store_true",
                        help="Skip spatial joins (AH/MLS charts omitted)")
    args = parser.parse_args()

    print("=" * 60)
    print("Drive-Zone Bar Charts")
    print("=" * 60)

    df = _load_demo("Nearest Drive")
    _progress(f"Loaded {len(df)} drive-zone rows from census CSV")

    # Charts from census CSV (always available)
    draw_bar_chart(
        df, "below_185_pov",
        "Households below 185% Poverty",
        "Count of residents below the Free/Reduced-price Lunch threshold "
        "(ACS 5-Year 2020\u20132024)",
        "{:,.0f}", OUT_DIR / "bars_households_poverty_drive.png",
    )
    draw_bar_chart(
        df, "minority_count",
        "Minority Residents (non-White)",
        "Count of non-white residents in each drive zone "
        "(2020 Decennial Census, dot placed)",
        "{:,.0f}", OUT_DIR / "bars_minority_residents_drive.png",
    )
    draw_bar_chart(
        df, "vehicles_zero",
        "Households with No Vehicle",
        "Count of zero-vehicle households in each drive zone "
        "(ACS 5-Year 2020\u20132024)",
        "{:,.0f}", OUT_DIR / "bars_no_vehicle_drive.png",
    )
    draw_bar_chart(
        df, "children_5_9",
        "Population Aged 5\u20139",
        "Count of residents aged 5 to 9 in each drive zone "
        "(2020 Decennial Census, dot-placed)",
        "{:,.0f}", OUT_DIR / "bars_children_5_9_drive.png",
    )
    draw_bar_chart(
        df, "children_0_4",
        "Population Aged 0\u20134",
        "Count of residents aged 0 to 4 in each drive zone "
        "(2020 Decennial Census, dot-placed)",
        "{:,.0f}", OUT_DIR / "bars_children_0_4_drive.png",
    )

    # Charts requiring spatial joins (AH + MLS + SAPFOTAC → drive zones)
    if args.cache_only:
        _progress("--cache-only: skipping affordable housing & MLS charts")
    else:
        zones = _build_drive_zones()
        if zones is not None:
            ah_counts = _spatial_join_count(AH_CACHE, zones, "ah_units")
            df = df.merge(ah_counts, on="school", how="left")
            df["ah_units"] = df["ah_units"].fillna(0).astype(int)
            draw_bar_chart(
                df, "ah_units",
                "Affordable Housing Units",
                "Income-restricted units within each drive zone "
                "(Town of Chapel Hill inventory, 2025)",
                "{:,.0f}", OUT_DIR / "bars_affordable_housing_drive.png",
            )

            mls_counts = _spatial_join_count(MLS_CACHE, zones, "mls_sales")
            df = df.merge(mls_counts, on="school", how="left")
            df["mls_sales"] = df["mls_sales"].fillna(0).astype(int)
            draw_bar_chart(
                df, "mls_sales",
                "Housing Market (2023\u20132025)",
                "MLS homes sold in each drive zone "
                "(Triangle MLS closed sales, 2023\u20132025)",
                "{:,.0f}", OUT_DIR / "bars_mls_sales_drive.png",
            )

            sap_counts = _spatial_join_sum(
                SAPFOTAC_CSV, zones, "students_elementary",
                "sapfotac_elem",
            )
            df = df.merge(sap_counts, on="school", how="left")
            df["sapfotac_elem"] = df["sapfotac_elem"].fillna(0).astype(int)
            draw_bar_chart(
                df, "sapfotac_elem",
                "Projected New Elementary Students (SAPFOTAC)",
                "Estimated students from planned developments "
                "(SAPFOTAC 2025 Annual Report, certified 2025-06-03)",
                "{:,.0f}", OUT_DIR / "bars_sapfotac_students_drive.png",
            )
        else:
            _progress("WARNING: Could not build drive zones — "
                      "AH/MLS/SAPFOTAC charts skipped")

    # ── Attendance (School) Zone charts ──────────────────────────────────
    sz = _load_demo("School Zones")
    _progress(f"Loaded {len(sz)} school-zone rows from census CSV")
    draw_bar_chart(
        sz, "children_0_4",
        "Population Aged 0\u20134 (Attendance Zones)",
        "Count of residents aged 0 to 4 in each attendance zone "
        "(2020 Decennial Census, dot-placed)",
        "{:,.0f}", OUT_DIR / "bars_children_0_4_school_zones.png",
    )

    print("=" * 60)


if __name__ == "__main__":
    main()
