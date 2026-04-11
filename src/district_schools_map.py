"""
District Schools Map — simple static PNG.

Renders the CHCCS district boundary with all 11 elementary schools marked
as plain blue circles. Also writes a second version that overlays each
school's nearest-drive zone boundary and color-codes the school markers
to match their zone border color.

Aesthetics match the project's other static maps (CartoDB Positron basemap,
dashed district outline).

Usage:
    python src/district_schools_map.py
"""

from __future__ import annotations

import sys
from pathlib import Path

import contextily as cx
import geopandas as gpd
import matplotlib.pyplot as plt
import pandas as pd
from shapely.geometry import Point

PROJECT_ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(PROJECT_ROOT / "src"))
from school_socioeconomic_analysis import _build_nearest_zones  # noqa: E402

DISTRICT_CACHE = PROJECT_ROOT / "data" / "cache" / "chccs_district_boundary.gpkg"
NCES_CSV = PROJECT_ROOT / "data" / "cache" / "nces_school_locations.csv"
GRID_CSV = PROJECT_ROOT / "data" / "processed" / "school_desert_grid.csv"
OUTPUT_PNG = PROJECT_ROOT / "assets" / "maps" / "district_schools.png"
OUTPUT_PNG_ZONES = PROJECT_ROOT / "assets" / "maps" / "district_schools_drive_zones.png"

CRS_WGS84 = "EPSG:4326"
CRS_UTM17N = "EPSG:32617"
CRS_WEB_MERCATOR = "EPSG:3857"

# Per-school color mapping. Ephesus/Glenwood/Seawell are pinned to
# red/green/blue by request; the remaining eight slots use the rest of the
# categorical palette shared with alternative_schools_map.py.
_SCHOOL_COLORS: dict[str, str] = {
    "Ephesus Elementary": "#e41a1c",            # red (pinned)
    "Glenwood Elementary": "#4daf4a",           # green (pinned)
    "Seawell Elementary": "#377eb8",            # blue (pinned)
    "Carrboro Elementary": "#ff7f00",           # orange
    "Estes Hills Elementary": "#8da0cb",        # light blue
    "Frank Porter Graham Bilingue": "#984ea3",  # purple
    "McDougle Elementary": "#a65628",           # brown
    "Morris Grove Elementary": "#f781bf",       # pink
    "Northside Elementary": "#999999",          # grey
    "Rashkis Elementary": "#66c2a5",            # teal
    "Scroggs Elementary": "#fc8d62",            # salmon
}


def load_inputs() -> tuple[gpd.GeoDataFrame, gpd.GeoDataFrame]:
    """Load district boundary + CHCCS school points (both in WebMercator)."""
    if not DISTRICT_CACHE.exists():
        print(f"Error: district boundary missing: {DISTRICT_CACHE}")
        sys.exit(1)
    if not NCES_CSV.exists():
        print(f"Error: NCES schools CSV missing: {NCES_CSV}")
        sys.exit(1)

    district = gpd.read_file(DISTRICT_CACHE).to_crs(CRS_WEB_MERCATOR)
    schools_df = pd.read_csv(NCES_CSV)
    schools = gpd.GeoDataFrame(
        schools_df,
        geometry=[Point(lon, lat)
                  for lon, lat in zip(schools_df["lon"], schools_df["lat"])],
        crs=CRS_WGS84,
    ).to_crs(CRS_WEB_MERCATOR)
    return district, schools


def build_drive_zones(
    district_merc: gpd.GeoDataFrame,
) -> gpd.GeoDataFrame | None:
    """Build nearest-drive zones via the shared Voronoi partition helper.

    Returns a GeoDataFrame in WebMercator for static map rendering.
    """
    if not GRID_CSV.exists():
        print(f"  Grid CSV not found: {GRID_CSV}")
        return None
    zones = _build_nearest_zones(GRID_CSV, "drive", district_merc)
    if zones is None or len(zones) == 0:
        return None
    return zones.to_crs(CRS_WEB_MERCATOR).reset_index(drop=True)


def _draw_district(ax, district: gpd.GeoDataFrame) -> None:
    district.plot(
        ax=ax,
        facecolor="#333333",
        edgecolor="none",
        alpha=0.04,
    )
    district.boundary.plot(
        ax=ax,
        edgecolor="#222222",
        linewidth=2.0,
        linestyle="--",
    )


def _set_extent(ax, district: gpd.GeoDataFrame) -> None:
    minx, miny, maxx, maxy = district.total_bounds
    dx = (maxx - minx) * 0.06
    dy = (maxy - miny) * 0.06
    ax.set_xlim(minx - dx, maxx + dx)
    ax.set_ylim(miny - dy, maxy + dy)


def render_plain(
    district: gpd.GeoDataFrame, schools: gpd.GeoDataFrame,
) -> None:
    """Original plain map: all schools in the same blue."""
    fig, ax = plt.subplots(figsize=(10, 10))
    _draw_district(ax, district)
    schools.plot(
        ax=ax,
        color="#1f77b4",
        edgecolor="#000000",
        linewidth=0.8,
        markersize=90,
        zorder=5,
    )
    _set_extent(ax, district)
    cx.add_basemap(ax, source=cx.providers.CartoDB.Positron, zoom=12)
    ax.set_axis_off()

    fig.suptitle("CHCCS Elementary Schools", fontsize=14,
                 fontweight="bold", y=0.95)
    fig.text(
        0.5, 0.04,
        "Source: NCES EDGE 2023-24; Census TIGER/Line district boundary",
        ha="center", fontsize=8, color="#666666",
    )
    plt.tight_layout(rect=[0, 0.05, 1, 0.93])
    OUTPUT_PNG.parent.mkdir(parents=True, exist_ok=True)
    fig.savefig(OUTPUT_PNG, dpi=200, bbox_inches="tight", facecolor="white")
    plt.close(fig)
    print(f"Saved -> {OUTPUT_PNG}")


def render_with_zones(
    district: gpd.GeoDataFrame,
    schools: gpd.GeoDataFrame,
    drive_zones: gpd.GeoDataFrame,
) -> None:
    """Color-coded version: drive zone borders + markers share a color."""
    color_map = _SCHOOL_COLORS

    fig, ax = plt.subplots(figsize=(10, 10))
    _draw_district(ax, district)

    # Drive zone borders (one color per school, no fill)
    for _, row in drive_zones.iterrows():
        c = color_map.get(row["school"], "#888888")
        gpd.GeoSeries([row.geometry], crs=drive_zones.crs).boundary.plot(
            ax=ax,
            edgecolor=c,
            linewidth=2.0,
            zorder=3,
        )

    # School markers — match each school's zone color, thin black border
    school_colors = [color_map.get(s, "#888888")
                     for s in schools["school"]]
    schools.plot(
        ax=ax,
        color=school_colors,
        edgecolor="#000000",
        linewidth=0.8,
        markersize=110,
        zorder=5,
    )

    _set_extent(ax, district)
    cx.add_basemap(ax, source=cx.providers.CartoDB.Positron, zoom=12)
    ax.set_axis_off()

    fig.suptitle(
        "CHCCS Elementary Schools & Nearest-Drive Zones",
        fontsize=14, fontweight="bold", y=0.95,
    )
    fig.text(
        0.5, 0.04,
        "Source: NCES EDGE 2023-24; Census TIGER/Line; "
        "nearest-drive zones from school_desert_grid (baseline, drive mode)",
        ha="center", fontsize=8, color="#666666",
    )
    plt.tight_layout(rect=[0, 0.05, 1, 0.93])
    OUTPUT_PNG_ZONES.parent.mkdir(parents=True, exist_ok=True)
    fig.savefig(OUTPUT_PNG_ZONES, dpi=200, bbox_inches="tight",
                facecolor="white")
    plt.close(fig)
    print(f"Saved -> {OUTPUT_PNG_ZONES}")


def main() -> None:
    district, schools = load_inputs()
    print(f"Loaded district boundary and {len(schools)} schools")

    render_plain(district, schools)

    drive_zones = build_drive_zones(district)
    if drive_zones is None or drive_zones.empty:
        print("Skipping drive-zone version (no drive zone data)")
        return
    render_with_zones(district, schools, drive_zones)


if __name__ == "__main__":
    main()
