"""
Alternative Schools Map — charter + private schools near CHCCS.

Parses data/raw/charter_schools.txt, geocodes every address (Census batch
API with Nominatim fallback), filters to schools within 10 miles of the
CHCCS district boundary, and renders a standalone Leaflet HTML map with:

    * CHCCS district boundary
    * Per-school attendance zones (toggleable, from CHCCS.shp)
    * Per-school nearest-drive zones (toggleable, from school_desert_grid)
    * CHCCS elementary schools (11 markers)
    * Charter + private alternative schools (markers)

Usage:
    python src/alternative_schools_map.py                # Geocode + build
    python src/alternative_schools_map.py --cache-only   # Use cached gpkg
"""

from __future__ import annotations

import argparse
import re
import sys
from pathlib import Path

import folium
import geopandas as gpd
import pandas as pd
from shapely.geometry import Point

PROJECT_ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(PROJECT_ROOT / "src"))
from school_socioeconomic_analysis import _build_nearest_zones  # noqa: E402

RAW_TXT = PROJECT_ROOT / "data" / "raw" / "charter_schools.txt"
CACHE_GPKG = PROJECT_ROOT / "data" / "cache" / "charter_private_schools.gpkg"
MAP_OUT = PROJECT_ROOT / "assets" / "maps" / "alternative_schools_map.html"

DISTRICT_CACHE = PROJECT_ROOT / "data" / "cache" / "chccs_district_boundary.gpkg"
CHCCS_SHP = PROJECT_ROOT / "data" / "raw" / "properties" / "CHCCS" / "CHCCS.shp"
GRID_CSV = PROJECT_ROOT / "data" / "processed" / "school_desert_grid.csv"
NCES_CSV = PROJECT_ROOT / "data" / "cache" / "nces_school_locations.csv"

CRS_WGS84 = "EPSG:4326"
CRS_UTM17N = "EPSG:32617"
CHAPEL_HILL_CENTER = [35.9132, -79.0558]

# Schools more than this distance (miles) from the CHCCS district boundary
# are excluded from the map.
DISTANCE_LIMIT_MI = 10.0
MI_TO_M = 1609.344

CENSUS_BATCH_URL = (
    "https://geocoding.geo.census.gov/geocoder/geographies/addressbatch"
)

# ENAME → project school name mapping (copied from school_socioeconomic_analysis)
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


def _progress(msg: str) -> None:
    print(f"  {msg}")


# ─────────────────────────────────────────────────────────────────────────────
# Text parsing
# ─────────────────────────────────────────────────────────────────────────────

_COUNTY_RE = re.compile(r"^(ORANGE|DURHAM|CHATHAM)\s+COUNTY$", re.IGNORECASE)
_ICON_RE = re.compile(r"[\u2020\u271d\u2721\u2719\U0001f3eb]")  # ✝  and  🏫


def parse_charter_schools_txt(path: Path) -> pd.DataFrame:
    """Parse the raw text file into a DataFrame of school records."""
    if not path.exists():
        print(f"Error: Input file not found: {path}")
        sys.exit(1)

    rows = []
    current_county: str | None = None
    current_type: str | None = None

    for raw_line in path.read_text(encoding="utf-8").splitlines():
        line = raw_line.strip()
        if not line:
            continue

        if _COUNTY_RE.match(line):
            current_county = f"{line.title()}"
            continue
        if line in ("Private", "Charter"):
            current_type = line
            continue
        if " \u2013 " not in line and " - " not in line:
            # Not a school record
            continue

        # Split on en-dash (preferred) or hyphen-with-spaces
        sep = " \u2013 " if " \u2013 " in line else " - "
        name_part, addr_part = line.split(sep, 1)

        # Strip religious/school icons from the name
        name = _ICON_RE.sub("", name_part).strip()

        # Strip trailing parenthetical note from address, if any
        note = ""
        m = re.search(r"\(([^)]*)\)\s*$", addr_part)
        if m:
            note = m.group(1).strip()
            addr_part = addr_part[: m.start()].strip()

        # Parse "street, city, NC ZIP"
        parts = [p.strip() for p in addr_part.split(",")]
        if len(parts) < 3:
            _progress(f"  skipping (unparseable address): {line}")
            continue
        street = parts[0]
        city = parts[1]
        state_zip = parts[2].split()
        state = state_zip[0] if state_zip else ""
        zipc = state_zip[1] if len(state_zip) > 1 else ""

        rows.append({
            "name": name,
            "type": current_type or "",
            "county": current_county or "",
            "street": street,
            "city": city,
            "state": state,
            "zip": zipc,
            "note": note,
        })

    df = pd.DataFrame(rows)
    _progress(f"Parsed {len(df)} school records "
              f"({(df['type'] == 'Charter').sum()} charter, "
              f"{(df['type'] == 'Private').sum()} private)")
    return df


# ─────────────────────────────────────────────────────────────────────────────
# Geocoding (Census batch → Nominatim fallback)
# ─────────────────────────────────────────────────────────────────────────────

def _census_batch(df: pd.DataFrame) -> dict[int, tuple[float, float]]:
    """Submit df to the Census batch geocoder. Returns {index: (lat, lon)}."""
    import requests

    lines = []
    for idx, row in df.iterrows():
        street = str(row["street"]).replace('"', "").strip()
        city = str(row["city"]).replace('"', "").strip()
        state = str(row["state"]).replace('"', "").strip()
        zipc = str(row["zip"]).replace('"', "").strip()
        lines.append(f'{idx},"{street}","{city}","{state}","{zipc}"')
    csv_content = "\n".join(lines)

    results: dict[int, tuple[float, float]] = {}
    try:
        resp = requests.post(
            CENSUS_BATCH_URL,
            files={"addressFile": ("addresses.csv", csv_content, "text/csv")},
            data={"benchmark": "Public_AR_Current",
                  "vintage": "Current_Current"},
            timeout=180,
        )
        resp.raise_for_status()
    except Exception as e:
        _progress(f"  Census batch error: {e}")
        return results

    for line in resp.text.strip().split("\n"):
        if not line.strip():
            continue
        parts = [p.strip('"') for p in line.split('","')]
        if len(parts) < 6:
            continue
        try:
            rec_id = int(parts[0].strip('"'))
        except ValueError:
            continue
        if parts[2] not in ("Match", "Exact"):
            continue
        lon_lat = parts[5]
        if "," not in lon_lat:
            continue
        lon_str, lat_str = lon_lat.split(",")
        try:
            results[rec_id] = (float(lat_str), float(lon_str))
        except ValueError:
            continue
    return results


def _nominatim_fallback(df: pd.DataFrame) -> None:
    """Geocode remaining rows in-place via Nominatim."""
    try:
        from geopy.extra.rate_limiter import RateLimiter
        from geopy.geocoders import Nominatim
    except ImportError:
        _progress("  geopy not installed — skipping Nominatim fallback")
        return

    unmatched_idx = df.index[df["lat"].isna()].tolist()
    if not unmatched_idx:
        return

    _progress(f"  Nominatim fallback for {len(unmatched_idx)} addresses ...")
    geolocator = Nominatim(user_agent="chccs_geospatial_alt_schools")
    geocode = RateLimiter(geolocator.geocode, min_delay_seconds=1.0)

    matched = 0
    for idx in unmatched_idx:
        row = df.loc[idx]
        full = (f"{row['street']}, {row['city']}, "
                f"{row['state']} {row['zip']}").strip()
        try:
            loc = geocode(full)
        except Exception:
            loc = None
        if loc is None:
            # Retry without ZIP
            try:
                loc = geocode(f"{row['street']}, {row['city']}, {row['state']}")
            except Exception:
                loc = None
        if loc is not None:
            df.at[idx, "lat"] = loc.latitude
            df.at[idx, "lon"] = loc.longitude
            matched += 1
    _progress(f"  Nominatim matched {matched}/{len(unmatched_idx)}")


def geocode_schools(df: pd.DataFrame) -> pd.DataFrame:
    """Add lat/lon columns using Census batch + Nominatim fallback."""
    _progress("Geocoding via Census Bureau batch API ...")
    df = df.copy()
    df["lat"] = pd.NA
    df["lon"] = pd.NA

    results = _census_batch(df)
    for idx, (lat, lon) in results.items():
        if idx in df.index:
            df.at[idx, "lat"] = lat
            df.at[idx, "lon"] = lon
    n = df["lat"].notna().sum()
    _progress(f"  Census batch: {n}/{len(df)} matched "
              f"({n / len(df) * 100:.1f}%)")

    _nominatim_fallback(df)

    total = len(df)
    ok = df["lat"].notna().sum()
    _progress(f"  Final: {ok}/{total} geocoded ({ok / total * 100:.1f}%)")
    failed = df[df["lat"].isna()]
    if len(failed):
        _progress("  Failed addresses:")
        for _, r in failed.iterrows():
            _progress(f"    - {r['name']}: {r['street']}, {r['city']}, "
                      f"{r['state']} {r['zip']}")

    df = df.dropna(subset=["lat", "lon"]).copy()
    df["lat"] = df["lat"].astype(float)
    df["lon"] = df["lon"].astype(float)
    return df.reset_index(drop=True)


def load_or_geocode(cache_only: bool = False) -> gpd.GeoDataFrame:
    """Either read the cached GeoPackage or parse + geocode from scratch."""
    if cache_only:
        if not CACHE_GPKG.exists():
            print(f"Error: Cache not found at {CACHE_GPKG}. Run without "
                  f"--cache-only first.")
            sys.exit(1)
        gdf = gpd.read_file(CACHE_GPKG)
        _progress(f"Loaded {len(gdf)} cached schools from {CACHE_GPKG.name}")
        return gdf

    df = parse_charter_schools_txt(RAW_TXT)
    df = geocode_schools(df)
    geometry = [Point(lon, lat) for lat, lon in zip(df["lat"], df["lon"])]
    gdf = gpd.GeoDataFrame(df, geometry=geometry, crs=CRS_WGS84)

    CACHE_GPKG.parent.mkdir(parents=True, exist_ok=True)
    gdf.to_file(CACHE_GPKG, driver="GPKG")
    _progress(f"Saved cache to {CACHE_GPKG}")
    return gdf


# ─────────────────────────────────────────────────────────────────────────────
# Zone polygon loaders (reused from school_socioeconomic_analysis patterns)
# ─────────────────────────────────────────────────────────────────────────────

def load_attendance_zones() -> gpd.GeoDataFrame | None:
    """Load elementary attendance zones from CHCCS.shp, dissolved by ENAME."""
    if not CHCCS_SHP.exists():
        _progress(f"  Attendance shapefile not found: {CHCCS_SHP}")
        return None
    raw = gpd.read_file(CHCCS_SHP).to_crs(CRS_WGS84)
    zones = raw.dissolve(by="ENAME").reset_index()
    zones["school"] = zones["ENAME"].map(_ENAME_TO_SCHOOL)
    zones = zones[zones["school"].notna()][["school", "geometry"]].copy()
    _progress(f"  Loaded {len(zones)} attendance zones")
    return zones.reset_index(drop=True)


def build_drive_zones(district: gpd.GeoDataFrame) -> gpd.GeoDataFrame | None:
    """Build nearest-drive zones via the shared Voronoi partition helper."""
    if not GRID_CSV.exists():
        _progress(f"  Grid CSV not found: {GRID_CSV}")
        return None
    zones = _build_nearest_zones(GRID_CSV, "drive", district)
    if zones is None or len(zones) == 0:
        return None
    return zones.reset_index(drop=True)


def filter_by_distance(
    schools: gpd.GeoDataFrame, district: gpd.GeoDataFrame, limit_mi: float,
) -> gpd.GeoDataFrame:
    """Keep only schools whose distance to district boundary ≤ limit_mi.

    Schools inside the boundary have distance 0 and are always kept.
    """
    dist_utm = district.to_crs(CRS_UTM17N).union_all()
    schools_utm = schools.to_crs(CRS_UTM17N).copy()
    schools_utm["distance_m"] = schools_utm.geometry.distance(dist_utm)
    schools_utm["distance_mi"] = schools_utm["distance_m"] / MI_TO_M

    keep = schools_utm["distance_m"] <= limit_mi * MI_TO_M
    n_dropped = (~keep).sum()
    _progress(f"  Distance filter: kept {keep.sum()}/{len(schools)} "
              f"(dropped {n_dropped} beyond {limit_mi} mi)")

    out = schools_utm[keep].to_crs(CRS_WGS84).reset_index(drop=True)
    # Drop the intermediate metric column; keep miles for popups
    out["distance_mi"] = out["distance_mi"].round(2)
    return out.drop(columns=["distance_m"])


# ─────────────────────────────────────────────────────────────────────────────
# Map building
# ─────────────────────────────────────────────────────────────────────────────

_ZONE_COLORS = [
    "#e41a1c", "#377eb8", "#4daf4a", "#984ea3", "#ff7f00",
    "#a65628", "#f781bf", "#999999", "#66c2a5", "#fc8d62", "#8da0cb",
]


def _add_zone_layer(
    m: folium.Map, zones: gpd.GeoDataFrame, label: str,
    school_color_map: dict[str, str], show: bool,
) -> None:
    fg = folium.FeatureGroup(name=label, show=show)
    for _, row in zones.iterrows():
        sn = row["school"]
        c = school_color_map.get(sn, "#888888")
        folium.GeoJson(
            gpd.GeoDataFrame([row], crs=CRS_WGS84).__geo_interface__,
            style_function=lambda x, c=c: {
                "fillColor": c, "fillOpacity": 0.10,
                "color": c, "weight": 2.5,
            },
            tooltip=f"{label}: {sn}",
        ).add_to(fg)
    fg.add_to(m)


def build_map(
    alt_schools: gpd.GeoDataFrame,
    chccs_schools: pd.DataFrame,
    district: gpd.GeoDataFrame,
    attendance_zones: gpd.GeoDataFrame | None,
    drive_zones: gpd.GeoDataFrame | None,
) -> folium.Map:
    """Render the Leaflet HTML map."""
    m = folium.Map(
        location=CHAPEL_HILL_CENTER, zoom_start=11, tiles="cartodbpositron",
    )

    # District boundary (on by default, always)
    folium.GeoJson(
        district.__geo_interface__,
        name="CHCCS District Boundary",
        style_function=lambda x: {
            "fillColor": "#333333", "fillOpacity": 0.03,
            "color": "#222222", "weight": 3, "dashArray": "5,5",
        },
        tooltip="CHCCS District Boundary",
    ).add_to(m)

    # Consistent school→color mapping
    school_names = sorted(chccs_schools["school"].tolist())
    color_map = {s: _ZONE_COLORS[i % len(_ZONE_COLORS)]
                 for i, s in enumerate(school_names)}

    if attendance_zones is not None:
        _add_zone_layer(m, attendance_zones, "Attendance Zones",
                        color_map, show=False)
    if drive_zones is not None:
        _add_zone_layer(m, drive_zones, "Nearest-Drive Zones",
                        color_map, show=False)

    # CHCCS elementary schools
    chccs_fg = folium.FeatureGroup(name="CHCCS Elementary Schools", show=True)
    for _, row in chccs_schools.iterrows():
        popup = folium.Popup(
            f"<b>{row['school']}</b><br>"
            f"<small>{row['address']}, {row['city']}</small><br>"
            f"<i>CHCCS public elementary</i>",
            max_width=300,
        )
        folium.Marker(
            location=[row["lat"], row["lon"]],
            popup=popup,
            tooltip=row["school"],
            icon=folium.Icon(color="blue", icon="graduation-cap",
                             prefix="fa"),
        ).add_to(chccs_fg)
    chccs_fg.add_to(m)

    # Alternative schools: charter + private as separate toggles
    type_styles = {
        "Charter": {"color": "purple", "icon": "book"},
        "Private": {"color": "darkred", "icon": "school"},
    }
    for school_type, style in type_styles.items():
        subset = alt_schools[alt_schools["type"] == school_type]
        if len(subset) == 0:
            continue
        fg = folium.FeatureGroup(
            name=f"{school_type} Schools ({len(subset)})", show=True,
        )
        for _, row in subset.iterrows():
            note_html = (f"<br><i>{row['note']}</i>"
                         if row.get("note") else "")
            addr_html = (f"{row['street']}, {row['city']}, "
                         f"{row['state']} {row['zip']}")
            popup = folium.Popup(
                f"<b>{row['name']}</b><br>"
                f"<small>{addr_html}</small><br>"
                f"{row['type']} &middot; {row['county']}<br>"
                f"{row['distance_mi']:.1f} mi from district boundary"
                f"{note_html}",
                max_width=320,
            )
            folium.Marker(
                location=[row["geometry"].y, row["geometry"].x],
                popup=popup,
                tooltip=row["name"],
                icon=folium.Icon(color=style["color"], icon=style["icon"],
                                 prefix="fa"),
            ).add_to(fg)
        fg.add_to(m)

    folium.LayerControl(collapsed=False).add_to(m)

    # Title/legend overlay
    title_html = f"""
    <div style="position: fixed; top: 10px; left: 50px; z-index: 1000;
                background: rgba(255,255,255,0.92); padding: 10px 14px;
                border-radius: 6px; border: 1px solid #999;
                font-family: sans-serif; font-size: 13px;
                box-shadow: 0 2px 6px rgba(0,0,0,0.15); max-width: 420px;">
      <b style="font-size: 15px;">Alternative Schools near CHCCS</b><br>
      Charter + private schools within {DISTANCE_LIMIT_MI:.0f} mi of the
      CHCCS district boundary, shown alongside the 11 CHCCS public
      elementary schools. Use the layer control to toggle attendance
      zones and nearest-drive zones.
    </div>
    """
    m.get_root().html.add_child(folium.Element(title_html))

    return m


# ─────────────────────────────────────────────────────────────────────────────
# Entry point
# ─────────────────────────────────────────────────────────────────────────────

def main() -> None:
    parser = argparse.ArgumentParser(
        description="Build alternative schools map for CHCCS district"
    )
    parser.add_argument("--cache-only", action="store_true",
                        help="Use cached geocoded gpkg; skip geocoding")
    args = parser.parse_args()

    print("=" * 60)
    print("Alternative Schools Map")
    print("=" * 60)

    # Load district + CHCCS schools + zones
    if not DISTRICT_CACHE.exists():
        print(f"Error: district boundary missing: {DISTRICT_CACHE}")
        sys.exit(1)
    district = gpd.read_file(DISTRICT_CACHE).to_crs(CRS_WGS84)
    _progress(f"Loaded district boundary")

    if not NCES_CSV.exists():
        print(f"Error: NCES schools CSV missing: {NCES_CSV}")
        sys.exit(1)
    chccs_schools = pd.read_csv(NCES_CSV)
    _progress(f"Loaded {len(chccs_schools)} CHCCS elementary schools")

    attendance_zones = load_attendance_zones()
    drive_zones = build_drive_zones(district)

    # Geocode alternative schools (or load cache)
    alt = load_or_geocode(cache_only=args.cache_only)
    alt = filter_by_distance(alt, district, DISTANCE_LIMIT_MI)

    # Build map
    _progress("Building HTML map ...")
    m = build_map(alt, chccs_schools, district, attendance_zones, drive_zones)
    MAP_OUT.parent.mkdir(parents=True, exist_ok=True)
    m.save(str(MAP_OUT))
    _progress(f"Saved map to {MAP_OUT}")

    # Summary
    print()
    print(f"  Total alternative schools on map: {len(alt)}")
    for t in ("Charter", "Private"):
        n = (alt["type"] == t).sum()
        print(f"    {t}: {n}")
    print("=" * 60)


if __name__ == "__main__":
    main()
