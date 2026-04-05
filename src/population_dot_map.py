"""Generate a standalone population dot map (race/ethnicity) for the CHCCS district."""

import json
from pathlib import Path

import geopandas as gpd
import numpy as np
import pandas as pd
from pyproj import Transformer
from shapely.geometry import mapping

# ---------------------------------------------------------------------------
# Paths
# ---------------------------------------------------------------------------
PROJECT_ROOT = Path(__file__).resolve().parent.parent
DATA_RAW = PROJECT_ROOT / "data" / "raw"
DATA_CACHE = PROJECT_ROOT / "data" / "cache"
ASSETS_MAPS = PROJECT_ROOT / "assets" / "maps"
OUTPUT_HTML = ASSETS_MAPS / "population_dots.html"

PARCEL_POLYS = DATA_RAW / "properties" / "combined_data_polys.gpkg"
DISTRICT_CACHE = DATA_CACHE / "chccs_district_boundary.gpkg"
DECENNIAL_CACHE = DATA_CACHE / "census_decennial_blocks.gpkg"
SCHOOL_CSV = DATA_CACHE / "nces_school_locations.csv"

CRS_WGS84 = "EPSG:4326"
CRS_UTM17N = "EPSG:32617"

# Race categories matching socioeconomic_story.py (censusdots.com scheme)
RACE_CATEGORIES = {
    "white_alone": ("#3b5fc0", "White"),
    "black_alone": ("#41ae76", "Black"),
    "hispanic_total": ("#f2c94c", "Hispanic/Latino"),
    "asian_alone": ("#e74c3c", "Asian"),
    "two_plus": ("#9b59b6", "Multiracial"),
    "other_race": ("#a0522d", "Native American/Other"),
}


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------
def _round_coords(geom_dict: dict, precision: int = 5) -> dict:
    def _round(coords):
        if isinstance(coords[0], (list, tuple)):
            return [_round(c) for c in coords]
        return [round(c, precision) for c in coords]
    result = dict(geom_dict)
    if "coordinates" in result:
        result["coordinates"] = _round(result["coordinates"])
    return result


def gdf_to_geojson_str(gdf, simplify_m=None):
    if len(gdf) == 0:
        return '{"type":"FeatureCollection","features":[]}'
    gdf = gdf.to_crs(CRS_WGS84)
    if simplify_m:
        gdf = gdf.copy()
        gdf_utm = gdf.to_crs(CRS_UTM17N)
        gdf_utm["geometry"] = gdf_utm.geometry.simplify(
            simplify_m, preserve_topology=True
        )
        gdf = gdf_utm.to_crs(CRS_WGS84)
    features = []
    for _, row in gdf.iterrows():
        if row.geometry is None or row.geometry.is_empty:
            continue
        features.append({
            "type": "Feature",
            "geometry": _round_coords(mapping(row.geometry)),
            "properties": {},
        })
    return json.dumps({"type": "FeatureCollection", "features": features},
                       separators=(",", ":"))


def _random_points_fallback(geom, n, rng):
    from shapely.geometry import Point
    points = []
    bounds = geom.bounds
    max_attempts = n * 20
    attempts = 0
    while len(points) < n and attempts < max_attempts:
        x = rng.uniform(bounds[0], bounds[2])
        y = rng.uniform(bounds[1], bounds[3])
        pt = Point(x, y)
        if geom.contains(pt):
            points.append(pt)
        attempts += 1
    return points


def generate_dots(blocks, parcels):
    """Generate dot-density data: [[lat, lon, raceIdx], ...] at 1:1 ratio."""
    if blocks.empty:
        return "[]"

    blocks_utm = blocks.to_crs(CRS_UTM17N)

    use_parcels = len(parcels) > 0
    if use_parcels:
        parcels_utm = parcels.to_crs(CRS_UTM17N)
        parcel_sindex = parcels_utm.sindex

    race_keys = list(RACE_CATEGORIES.keys())
    rng = np.random.default_rng(42)

    if "other_race" not in blocks_utm.columns:
        other_cols = [c for c in ["aian_alone", "nhpi_alone", "other_alone"]
                      if c in blocks_utm.columns]
        if other_cols:
            blocks_utm["other_race"] = blocks_utm[other_cols].sum(axis=1).clip(lower=0)
        else:
            blocks_utm["other_race"] = 0

    raw_dots = []

    for idx, block in blocks_utm.iterrows():
        block_geom = block.geometry
        if block_geom is None or block_geom.is_empty:
            continue

        placement_geom = block_geom
        if use_parcels:
            candidates = list(parcel_sindex.intersection(block_geom.bounds))
            if candidates:
                try:
                    parcel_union = parcels_utm.iloc[candidates].union_all()
                except AttributeError:
                    parcel_union = parcels_utm.iloc[candidates].unary_union
                intersection = block_geom.intersection(parcel_union)
                if not intersection.is_empty and intersection.area > 10:
                    placement_geom = intersection

        if placement_geom.area < 10:
            continue

        for race_idx, race_col in enumerate(race_keys):
            count = int(block.get(race_col, 0))
            if count <= 0:
                continue
            try:
                from shapely import random_points as _shp_random_points
                pts = _shp_random_points(placement_geom, count, rng=rng)
                if hasattr(pts, "geoms"):
                    pt_list = list(pts.geoms)
                else:
                    pt_list = [pts] if not hasattr(pts, "__len__") else list(pts)
            except (ImportError, TypeError):
                pt_list = _random_points_fallback(placement_geom, count, rng)

            for pt in pt_list:
                raw_dots.append((pt.x, pt.y, race_idx))

    if not raw_dots:
        return "[]"

    transformer = Transformer.from_crs(CRS_UTM17N, CRS_WGS84, always_xy=True)
    xs = [d[0] for d in raw_dots]
    ys = [d[1] for d in raw_dots]
    race_idxs = [d[2] for d in raw_dots]
    lons, lats = transformer.transform(xs, ys)

    dots_wgs = []
    for i in range(len(raw_dots)):
        dots_wgs.append([round(lats[i], 5), round(lons[i], 5), race_idxs[i]])

    return json.dumps(dots_wgs, separators=(",", ":"))


# ---------------------------------------------------------------------------
# Load data
# ---------------------------------------------------------------------------
print("[1/4] Loading residential parcels ...")
parcels = gpd.read_file(PARCEL_POLYS).to_crs(CRS_WGS84)
mask = parcels.get("is_residential", pd.Series(False, index=parcels.index))
if "imp_vac" in parcels.columns:
    mask = mask & parcels["imp_vac"].str.contains("Improved", case=False, na=False)
parcels = parcels[mask].copy()
print(f"  {len(parcels)} improved residential parcels")

print("[2/4] Loading decennial blocks ...")
blocks = gpd.read_file(DECENNIAL_CACHE).to_crs(CRS_WGS84)

# Clip blocks to district boundary
print("[3/4] Clipping blocks to district and loading boundary ...")
district = gpd.read_file(DISTRICT_CACHE).to_crs(CRS_WGS84)
blocks = gpd.overlay(blocks, district[["geometry"]], how="intersection")
print(f"  {len(blocks)} blocks within district")

print("[4/4] Generating dots (1 dot = 1 person) ...")
dots_json = generate_dots(blocks, parcels)
n_dots = dots_json.count("[") - 1  # rough count
print(f"  ~{n_dots:,} dots generated")

# District boundary GeoJSON
district_json = gdf_to_geojson_str(district, simplify_m=20)

# Schools
schools = pd.read_csv(SCHOOL_CSV)
schools_json = json.dumps(
    [{"name": r["school"], "lat": r["lat"], "lon": r["lon"]}
     for _, r in schools.iterrows()],
    separators=(",", ":"),
)

# Race metadata for JS
race_colors_js = json.dumps(
    [v[0] for v in RACE_CATEGORIES.values()], separators=(",", ":")
)
race_labels_js = json.dumps(
    [v[1] for v in RACE_CATEGORIES.values()], separators=(",", ":")
)

bounds = district.total_bounds
center_lat = (bounds[1] + bounds[3]) / 2
center_lon = (bounds[0] + bounds[2]) / 2

# ---------------------------------------------------------------------------
# HTML
# ---------------------------------------------------------------------------
html = f"""<!DOCTYPE html>
<html lang="en">
<head>
<meta charset="UTF-8">
<meta name="viewport" content="width=device-width, initial-scale=1.0">
<title>CHCCS Population Dot Map — Race / Ethnicity</title>
<link rel="stylesheet" href="https://unpkg.com/leaflet@1.9.4/dist/leaflet.css"/>
<script src="https://unpkg.com/leaflet@1.9.4/dist/leaflet.js"></script>
<style>
  * {{ margin: 0; padding: 0; box-sizing: border-box; }}
  html, body {{ height: 100%; }}
  #map {{ height: 100%; width: 100%; }}
  .info-box {{
    background: white; padding: 10px 14px; border-radius: 6px;
    box-shadow: 0 2px 8px rgba(0,0,0,0.25);
    font: 13px/1.5 system-ui, sans-serif;
    max-width: 220px;
  }}
  .info-box h3 {{ margin: 0 0 6px; font-size: 14px; }}
  .legend-row {{ display: flex; align-items: center; gap: 6px; margin: 2px 0; }}
  .legend-swatch {{
    width: 12px; height: 12px; border-radius: 50%; flex-shrink: 0;
  }}
  .legend-label {{ font-size: 12px; }}
  .info-note {{ font-size: 11px; color: #666; margin-top: 6px; }}
</style>
</head>
<body>
<div id="map"></div>
<script>
var DOT_DATA = {dots_json};
var DISTRICT = {district_json};
var SCHOOLS = {schools_json};
var RACE_COLORS = {race_colors_js};
var RACE_LABELS = {race_labels_js};

var map = L.map('map', {{
  center: [{center_lat}, {center_lon}],
  zoom: 12,
  zoomControl: true,
}});

L.tileLayer('https://{{s}}.basemaps.cartocdn.com/light_all/{{z}}/{{x}}/{{y}}{{r}}.png', {{
  attribution: '&copy; OpenStreetMap &copy; CARTO',
  maxZoom: 19,
}}).addTo(map);

// District boundary
var districtLayer = L.geoJSON(DISTRICT, {{
  style: {{ color: '#2c3e50', weight: 2.5, fillOpacity: 0, dashArray: '6,4' }}
}}).addTo(map);

// Dot-density layer (canvas rendered)
var dotCanvas = L.canvas({{ padding: 0.5 }});
var dotLayer = L.layerGroup();
for (var i = 0; i < DOT_DATA.length; i++) {{
  L.circleMarker([DOT_DATA[i][0], DOT_DATA[i][1]], {{
    radius: 1.5,
    fillColor: RACE_COLORS[DOT_DATA[i][2]],
    color: RACE_COLORS[DOT_DATA[i][2]],
    weight: 0,
    fillOpacity: 0.7,
    renderer: dotCanvas,
  }}).addTo(dotLayer);
}}
dotLayer.addTo(map);

// School markers
SCHOOLS.forEach(function(s) {{
  L.circleMarker([s.lat, s.lon], {{
    radius: 6, color: '#fff', weight: 2,
    fillColor: '#e74c3c', fillOpacity: 0.9,
  }}).bindTooltip(s.name, {{ direction: 'top', offset: [0, -8] }}).addTo(map);
}});

// Fit to district
map.fitBounds(districtLayer.getBounds().pad(0.05));

// Legend
var legend = L.control({{ position: 'bottomright' }});
legend.onAdd = function() {{
  var div = L.DomUtil.create('div', 'info-box');
  var html = '<h3>Race / Ethnicity</h3>';
  for (var i = 0; i < RACE_LABELS.length; i++) {{
    html += '<div class="legend-row">'
      + '<span class="legend-swatch" style="background:' + RACE_COLORS[i] + '"></span>'
      + '<span class="legend-label">' + RACE_LABELS[i] + '</span></div>';
  }}
  html += '<div class="info-note">1 dot = 1 person<br>Source: 2020 Decennial Census<br>Dots placed within residential parcels</div>';
  div.innerHTML = html;
  return div;
}};
legend.addTo(map);
</script>
</body>
</html>"""

ASSETS_MAPS.mkdir(parents=True, exist_ok=True)
OUTPUT_HTML.write_text(html, encoding="utf-8")
print(f"\nWrote {OUTPUT_HTML}  ({len(html):,} bytes)")
