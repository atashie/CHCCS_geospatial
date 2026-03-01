"""
Flood Plain & School Property Map (Static PNG).

Overlays FEMA 100-year and 500-year flood zones on elementary school
property parcels.  FPG is highlighted as the school with the largest
flood-plain intersection.

Usage:
    python src/flood_map.py

Output:
    assets/maps/flood_school_properties.png
"""

from __future__ import annotations

import math
from pathlib import Path

import contextily as cx
import geopandas as gpd
import matplotlib.patches as mpatches
import matplotlib.pyplot as plt
import pandas as pd
import requests
from shapely.geometry import Point
from shapely.ops import unary_union

# ── paths ──────────────────────────────────────────────────────────────
PROJECT_ROOT = Path(__file__).resolve().parent.parent
DATA_CACHE = PROJECT_ROOT / "data" / "cache"
POLYS_GPKG = PROJECT_ROOT / "data" / "raw" / "properties" / "combined_data_polys.gpkg"
SCHOOL_CSV = DATA_CACHE / "nces_school_locations.csv"
FLOOD_CACHE = DATA_CACHE / "fema_flood_zones.gpkg"
OUTPUT_PNG = PROJECT_ROOT / "assets" / "maps" / "flood_school_properties.png"

CRS_WGS84 = "EPSG:4326"
CRS_WEB_MERCATOR = "EPSG:3857"

# ── colours ────────────────────────────────────────────────────────────
SCHOOL_FILL = "#d4edda"
SCHOOL_EDGE = "#155724"
FLOOD_100YR = "#6baed6"
FLOOD_500YR = "#bdd7e7"
OVERLAP_COLOR = "#e6031b"

# ── FEMA NFHL REST endpoint (layer 28 = S_FLD_HAZ_AR) ─────────────────
FEMA_URL = (
    "https://hazards.fema.gov/arcgis/rest/services/public/NFHL/MapServer/28/query"
)
FEMA_PAGE_SIZE = 1000


# ━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
# Step 1: download & cache FEMA flood data
# ━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━


def _esri_rings_to_polygon(rings: list[list]) -> "shapely.geometry.base.BaseGeometry":
    """Convert Esri JSON rings to a Shapely Polygon/MultiPolygon."""
    from shapely.geometry import Polygon, MultiPolygon

    if len(rings) == 1:
        return Polygon(rings[0])
    # first ring = exterior, subsequent = holes (if orientation differs)
    return Polygon(rings[0], rings[1:])


def _fetch_fema_tile(bbox_str: str) -> list[dict]:
    """Fetch all flood features for a single bbox tile, with pagination."""
    records: list[dict] = []
    offset = 0
    while True:
        params = {
            "where": "1=1",
            "geometry": bbox_str,
            "geometryType": "esriGeometryEnvelope",
            "inSR": "4326",
            "outSR": "4326",
            "spatialRel": "esriSpatialRelIntersects",
            "outFields": "FLD_ZONE,ZONE_SUBTY,SFHA_TF",
            "returnGeometry": "true",
            "f": "json",
            "resultRecordCount": FEMA_PAGE_SIZE,
            "resultOffset": offset,
        }
        r = requests.get(FEMA_URL, params=params, timeout=60)
        r.raise_for_status()
        data = r.json()
        if "error" in data:
            raise RuntimeError(f"FEMA API error: {data['error']}")
        features = data.get("features", [])
        if not features:
            break
        records.extend(features)
        if len(features) < FEMA_PAGE_SIZE:
            break
        offset += FEMA_PAGE_SIZE
    return records


def download_flood_zones(bbox: tuple[float, float, float, float]) -> gpd.GeoDataFrame:
    """Query FEMA NFHL REST API for flood hazard polygons.

    The FEMA server errors on large bboxes, so we tile the request into
    a 3×3 grid of smaller sub-bboxes.
    """
    if FLOOD_CACHE.exists():
        print(f"  Loading cached flood zones from {FLOOD_CACHE}")
        return gpd.read_file(FLOOD_CACHE)

    minx, miny, maxx, maxy = bbox
    n_tiles = 3
    dx = (maxx - minx) / n_tiles
    dy = (maxy - miny) / n_tiles

    all_records: list[dict] = []
    for ix in range(n_tiles):
        for iy in range(n_tiles):
            tile_bbox = (
                f"{minx + ix * dx},{miny + iy * dy},"
                f"{minx + (ix + 1) * dx},{miny + (iy + 1) * dy}"
            )
            print(f"  Fetching FEMA tile ({ix},{iy}) ...")
            records = _fetch_fema_tile(tile_bbox)
            all_records.extend(records)

    print(f"  Downloaded {len(all_records)} flood zone features (before dedup)")

    # Convert Esri JSON to GeoDataFrame
    rows = []
    for feat in all_records:
        attrs = feat["attributes"]
        rings = feat.get("geometry", {}).get("rings")
        if not rings:
            continue
        geom = _esri_rings_to_polygon(rings)
        rows.append({**attrs, "geometry": geom})

    gdf = gpd.GeoDataFrame(rows, crs=CRS_WGS84)
    # Deduplicate features that span tile boundaries
    gdf = gdf.drop_duplicates(subset=["geometry"])
    print(f"  Unique features after dedup: {len(gdf)}")

    DATA_CACHE.mkdir(parents=True, exist_ok=True)
    gdf.to_file(FLOOD_CACHE, driver="GPKG")
    print(f"  Cached to {FLOOD_CACHE}")
    return gdf


# ━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
# Step 2: extract school property polygons
# ━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━


def load_school_properties() -> gpd.GeoDataFrame:
    """For each elementary school, find the parcel containing the NCES point."""
    schools = pd.read_csv(SCHOOL_CSV)
    parcels = gpd.read_file(POLYS_GPKG)

    rows = []
    for _, sch in schools.iterrows():
        pt = Point(sch["lon"], sch["lat"])
        containing = parcels[parcels.geometry.contains(pt)]
        if len(containing) == 0:
            # fallback: nearest parcel centroid
            dists = parcels.geometry.centroid.distance(pt)
            containing = parcels.loc[[dists.idxmin()]]
        row = containing.iloc[0].copy()
        row["school_name"] = sch["school"]
        row["school_lat"] = sch["lat"]
        row["school_lon"] = sch["lon"]
        rows.append(row)

    gdf = gpd.GeoDataFrame(rows, crs=CRS_WGS84)
    print(f"  Matched {len(gdf)} school property parcels")
    return gdf


# ━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
# Step 3: classify flood zones and compute intersections
# ━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━


def classify_flood_zones(
    flood: gpd.GeoDataFrame,
) -> tuple[gpd.GeoDataFrame, gpd.GeoDataFrame]:
    """Split into 100-year (AE/A/AO/AH + floodway) and 500-year (X 0.2 PCT)."""
    flood_100 = flood[flood["FLD_ZONE"].isin(["A", "AE", "AO", "AH"])].copy()
    flood_500 = flood[
        flood["ZONE_SUBTY"].fillna("").str.contains("0.2 PCT", case=False)
    ].copy()
    print(f"  100-year features: {len(flood_100)},  500-year features: {len(flood_500)}")
    return flood_100, flood_500


def compute_overlaps(
    school_props: gpd.GeoDataFrame,
    flood_100: gpd.GeoDataFrame,
    flood_500: gpd.GeoDataFrame,
) -> gpd.GeoDataFrame:
    """Compute school-property × flood-zone intersection polygons."""
    overlaps = []

    # Fix invalid geometries before union
    if len(flood_100):
        flood_100 = flood_100.copy()
        flood_100["geometry"] = flood_100.geometry.make_valid()
    if len(flood_500):
        flood_500 = flood_500.copy()
        flood_500["geometry"] = flood_500.geometry.make_valid()

    union_100 = unary_union(flood_100.geometry) if len(flood_100) else None
    union_500 = unary_union(flood_500.geometry) if len(flood_500) else None

    # area conversion factor: deg² → m² → acres (at ~35.9°N)
    lat_rad = math.radians(35.9)
    m2_per_deg2 = 111_320 * 111_320 * math.cos(lat_rad)
    acres_per_m2 = 1 / 4046.86

    for _, row in school_props.iterrows():
        geom = row.geometry
        for label, union_geom in [("100-year", union_100), ("500-year", union_500)]:
            if union_geom is None:
                continue
            ix = geom.intersection(union_geom)
            if ix.is_empty:
                continue
            acres = ix.area * m2_per_deg2 * acres_per_m2
            pct = acres / row["CALC_ACRES"] * 100 if row["CALC_ACRES"] else 0
            overlaps.append(
                {
                    "geometry": ix,
                    "school_name": row["school_name"],
                    "flood_type": label,
                    "overlap_acres": round(acres, 2),
                    "overlap_pct": round(pct, 1),
                }
            )
            print(
                f"    {row['school_name']}: {label} overlap = "
                f"{acres:.2f} ac ({pct:.0f}% of {row['CALC_ACRES']} ac)"
            )

    if not overlaps:
        return gpd.GeoDataFrame(columns=["geometry", "school_name", "flood_type"])
    return gpd.GeoDataFrame(overlaps, crs=CRS_WGS84)


# ━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
# Step 4: render the map
# ━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━


def render_map(
    school_props: gpd.GeoDataFrame,
    flood_100: gpd.GeoDataFrame,
    flood_500: gpd.GeoDataFrame,
    overlaps: gpd.GeoDataFrame,
) -> None:
    """Two-panel figure: district overview (left) + FPG zoom (right)."""

    # reproject everything to Web Mercator for contextily
    sp = school_props.to_crs(CRS_WEB_MERCATOR)
    f100 = flood_100.to_crs(CRS_WEB_MERCATOR)
    f500 = flood_500.to_crs(CRS_WEB_MERCATOR)
    ov = overlaps.to_crs(CRS_WEB_MERCATOR) if len(overlaps) else overlaps

    fig, (ax_dist, ax_fpg) = plt.subplots(
        1, 2, figsize=(16, 9), gridspec_kw={"width_ratios": [1.1, 1]}
    )

    # ── left panel: district overview ──────────────────────────────────
    _draw_layers(ax_dist, sp, f100, f500, ov, label_schools=True)

    # set extent to school properties + padding
    total_bounds = sp.total_bounds  # minx, miny, maxx, maxy
    dx = (total_bounds[2] - total_bounds[0]) * 0.08
    dy = (total_bounds[3] - total_bounds[1]) * 0.08
    ax_dist.set_xlim(total_bounds[0] - dx, total_bounds[2] + dx)
    ax_dist.set_ylim(total_bounds[1] - dy, total_bounds[3] + dy)

    cx.add_basemap(ax_dist, source=cx.providers.CartoDB.Positron, zoom=13)
    ax_dist.set_title("CHCCS Elementary School Properties & FEMA Flood Zones", fontsize=12, fontweight="bold")
    ax_dist.set_axis_off()

    # ── right panel: FPG zoom ──────────────────────────────────────────
    fpg_row = sp[sp["school_name"].str.contains("Frank Porter Graham")]
    if len(fpg_row) == 0:
        fpg_row = sp[sp["school_name"].str.contains("FPG")]

    _draw_layers(ax_fpg, sp, f100, f500, ov, label_schools=False)

    if len(fpg_row) > 0:
        fpg_bounds = fpg_row.total_bounds
        buf = 400  # metres
        ax_fpg.set_xlim(fpg_bounds[0] - buf, fpg_bounds[2] + buf)
        ax_fpg.set_ylim(fpg_bounds[1] - buf, fpg_bounds[3] + buf)

        # label FPG
        cx_pt = (fpg_bounds[0] + fpg_bounds[2]) / 2
        cy_pt = fpg_bounds[3] + 50
        ax_fpg.annotate(
            "Frank Porter Graham",
            xy=(cx_pt, cy_pt),
            fontsize=10,
            fontweight="bold",
            ha="center",
            va="bottom",
            color=SCHOOL_EDGE,
        )

        # overlap acreage annotation
        fpg_ov = overlaps[overlaps["school_name"].str.contains("Frank Porter Graham")]
        if len(fpg_ov) > 0:
            ov_100 = fpg_ov[fpg_ov["flood_type"] == "100-year"]
            if len(ov_100) > 0:
                acres = ov_100.iloc[0]["overlap_acres"]
                pct = ov_100.iloc[0]["overlap_pct"]
                ax_fpg.annotate(
                    f"100-yr flood overlap:\n{acres} acres ({pct}%)",
                    xy=(cx_pt, fpg_bounds[1] - 60),
                    fontsize=9,
                    ha="center",
                    va="top",
                    color=OVERLAP_COLOR,
                    fontweight="bold",
                    bbox=dict(
                        boxstyle="round,pad=0.3",
                        facecolor="white",
                        edgecolor=OVERLAP_COLOR,
                        alpha=0.9,
                    ),
                )

    cx.add_basemap(ax_fpg, source=cx.providers.CartoDB.Positron, zoom=16)
    ax_fpg.set_title("FPG Flood Zone Detail", fontsize=12, fontweight="bold")
    ax_fpg.set_axis_off()

    # ── legend ─────────────────────────────────────────────────────────
    legend_elements = [
        mpatches.Patch(facecolor=SCHOOL_FILL, edgecolor=SCHOOL_EDGE, linewidth=1.5,
                       label="School property"),
        mpatches.Patch(facecolor=FLOOD_100YR, alpha=0.4, label="100-year flood zone"),
        mpatches.Patch(facecolor=FLOOD_500YR, alpha=0.4, label="500-year flood zone"),
        mpatches.Patch(facecolor=OVERLAP_COLOR, alpha=0.7,
                       label="Flood x school overlap"),
    ]
    fig.legend(
        handles=legend_elements,
        loc="upper center",
        bbox_to_anchor=(0.5, 0.06),
        ncol=4,
        fontsize=9,
        frameon=True,
        edgecolor="#cccccc",
        fancybox=True,
    )

    fig.suptitle(
        "FEMA Flood Plains & Elementary School Properties",
        fontsize=14,
        fontweight="bold",
        y=0.97,
    )
    fig.text(
        0.5,
        0.01,
        "Source: FEMA National Flood Hazard Layer (NFHL); Orange County parcel data",
        ha="center",
        fontsize=8,
        color="#666666",
    )

    plt.tight_layout(rect=[0, 0.08, 1, 0.94])
    OUTPUT_PNG.parent.mkdir(parents=True, exist_ok=True)
    fig.savefig(OUTPUT_PNG, dpi=200, bbox_inches="tight", facecolor="white")
    plt.close(fig)
    print(f"\nSaved -> {OUTPUT_PNG}")


def _draw_layers(
    ax,
    school_props: gpd.GeoDataFrame,
    flood_100: gpd.GeoDataFrame,
    flood_500: gpd.GeoDataFrame,
    overlaps: gpd.GeoDataFrame,
    *,
    label_schools: bool = False,
) -> None:
    """Draw all layers onto a matplotlib axes."""
    # 500-year flood (bottom)
    if len(flood_500) > 0:
        flood_500.plot(ax=ax, facecolor=FLOOD_500YR, edgecolor="none", alpha=0.25)
    # 100-year flood
    if len(flood_100) > 0:
        flood_100.plot(ax=ax, facecolor=FLOOD_100YR, edgecolor="none", alpha=0.4)
    # school properties
    school_props.plot(
        ax=ax,
        facecolor=SCHOOL_FILL,
        edgecolor=SCHOOL_EDGE,
        linewidth=1.5,
        alpha=0.7,
    )
    # overlap (top)
    if len(overlaps) > 0:
        ov_merc = overlaps.to_crs(CRS_WEB_MERCATOR) if overlaps.crs != CRS_WEB_MERCATOR else overlaps
        ov_merc.plot(ax=ax, facecolor=OVERLAP_COLOR, edgecolor=OVERLAP_COLOR, alpha=0.7, linewidth=0.5)

    if label_schools:
        for _, row in school_props.iterrows():
            name = row["school_name"].replace(" Elementary", "").replace(" Bilingue", "")
            bounds = row.geometry.bounds  # minx, miny, maxx, maxy
            top_center_x = (bounds[0] + bounds[2]) / 2
            top_y = bounds[3]  # top edge of polygon
            ax.annotate(
                name,
                xy=(top_center_x, top_y),
                xytext=(0, 4),
                textcoords="offset points",
                fontsize=6,
                ha="center",
                va="bottom",
                fontweight="bold",
                color="#333333",
                bbox=dict(
                    boxstyle="round,pad=0.15",
                    facecolor="white",
                    edgecolor="none",
                    alpha=0.7,
                ),
            )


# ━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
# Main
# ━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━


def main() -> None:
    print("=" * 60)
    print("Flood Plain & School Property Map")
    print("=" * 60)

    # 1. Load school properties
    print("\n[1/4] Loading school properties ...")
    school_props = load_school_properties()

    # 2. Download/load flood zones (bbox = school properties + buffer)
    print("\n[2/4] Loading FEMA flood zones ...")
    bounds = school_props.total_bounds  # minx, miny, maxx, maxy
    buf = 0.01  # ~1 km buffer in degrees
    bbox = (bounds[0] - buf, bounds[1] - buf, bounds[2] + buf, bounds[3] + buf)
    flood = download_flood_zones(bbox)

    # 3. Classify and compute overlaps
    print("\n[3/4] Computing flood x school overlaps ...")
    flood_100, flood_500 = classify_flood_zones(flood)
    overlaps = compute_overlaps(school_props, flood_100, flood_500)

    # 4. Render
    print("\n[4/4] Rendering map ...")
    render_map(school_props, flood_100, flood_500, overlaps)

    print("\nDone!")


if __name__ == "__main__":
    main()
