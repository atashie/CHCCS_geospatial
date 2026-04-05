"""Generate a standalone HTML map of all residential parcels in the CHCCS district."""

import json
from pathlib import Path

import geopandas as gpd
import numpy as np
import pandas as pd
from shapely.geometry import mapping

# ---------------------------------------------------------------------------
# Paths
# ---------------------------------------------------------------------------
PROJECT_ROOT = Path(__file__).resolve().parent.parent
DATA_RAW = PROJECT_ROOT / "data" / "raw"
DATA_CACHE = PROJECT_ROOT / "data" / "cache"
ASSETS_MAPS = PROJECT_ROOT / "assets" / "maps"
OUTPUT_HTML = ASSETS_MAPS / "residential_parcels.html"

PARCEL_POLYS = DATA_RAW / "properties" / "combined_data_polys.gpkg"
DISTRICT_CACHE = DATA_CACHE / "chccs_district_boundary.gpkg"
SCHOOL_CSV = DATA_CACHE / "nces_school_locations.csv"

CRS_WGS84 = "EPSG:4326"
CRS_UTM17N = "EPSG:32617"

RESIDENTIAL_LUC_PREFIXES = ("100", "110", "120", "630", "EXH")


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


def gdf_to_geojson_str(gdf, simplify_m=None, precision=5):
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
            "geometry": _round_coords(mapping(row.geometry), precision),
            "properties": {},
        })
    fc = {"type": "FeatureCollection", "features": features}
    return json.dumps(fc, separators=(",", ":"))


# ---------------------------------------------------------------------------
# Load data
# ---------------------------------------------------------------------------
print("Loading residential parcels (full district) ...")
parcels = gpd.read_file(PARCEL_POLYS).to_crs(CRS_WGS84)
mask = parcels.get("is_residential", pd.Series(False, index=parcels.index))
if "imp_vac" in parcels.columns:
    mask = mask & parcels["imp_vac"].str.contains("Improved", case=False, na=False)
parcels = parcels[mask].copy()
print(f"  {len(parcels)} improved residential parcels")

print("Converting to GeoJSON (simplify 5 m) ...")
parcels_json = gdf_to_geojson_str(parcels, simplify_m=5)

print("Loading district boundary ...")
district = gpd.read_file(DISTRICT_CACHE).to_crs(CRS_WGS84)
district_json = gdf_to_geojson_str(district, simplify_m=20)

print("Loading school locations ...")
schools = pd.read_csv(SCHOOL_CSV)
schools_json = json.dumps(
    [{"name": r["school"], "lat": r["lat"], "lon": r["lon"]}
     for _, r in schools.iterrows()],
    separators=(",", ":"),
)

# Compute district center for initial view
bounds = district.total_bounds  # minx, miny, maxx, maxy
center_lat = (bounds[1] + bounds[3]) / 2
center_lon = (bounds[0] + bounds[2]) / 2

# ---------------------------------------------------------------------------
# HTML template
# ---------------------------------------------------------------------------
html = f"""<!DOCTYPE html>
<html lang="en">
<head>
<meta charset="UTF-8">
<meta name="viewport" content="width=device-width, initial-scale=1.0">
<title>CHCCS Residential Parcels</title>
<link rel="stylesheet" href="https://unpkg.com/leaflet@1.9.4/dist/leaflet.css"/>
<script src="https://unpkg.com/leaflet@1.9.4/dist/leaflet.js"></script>
<style>
  * {{ margin: 0; padding: 0; box-sizing: border-box; }}
  html, body {{ height: 100%; }}
  #map {{ height: 100%; width: 100%; }}
  .info-box {{
    background: white; padding: 10px 14px; border-radius: 6px;
    box-shadow: 0 2px 8px rgba(0,0,0,0.25); font: 13px/1.4 system-ui, sans-serif;
    max-width: 260px;
  }}
  .info-box h3 {{ margin: 0 0 4px; font-size: 15px; }}
  .legend-row {{ display: flex; align-items: center; gap: 6px; margin: 3px 0; }}
  .legend-swatch {{
    width: 16px; height: 16px; border-radius: 3px; flex-shrink: 0;
  }}
</style>
</head>
<body>
<div id="map"></div>
<script>
var PARCELS = {parcels_json};
var DISTRICT = {district_json};
var SCHOOLS = {schools_json};

var map = L.map('map', {{
  center: [{center_lat}, {center_lon}],
  zoom: 12,
  zoomControl: true,
  renderer: L.canvas(),
}});

L.tileLayer('https://{{s}}.basemaps.cartocdn.com/light_all/{{z}}/{{x}}/{{y}}{{r}}.png', {{
  attribution: '&copy; OpenStreetMap contributors &copy; CARTO',
  maxZoom: 19,
}}).addTo(map);

// District boundary
var districtLayer = L.geoJSON(DISTRICT, {{
  style: {{ color: '#2c3e50', weight: 2.5, fillOpacity: 0, dashArray: '6,4' }}
}}).addTo(map);

// Residential parcels
var parcelsLayer = L.geoJSON(PARCELS, {{
  style: {{ color: '#27ae60', weight: 0.5, fillColor: '#a8e6a0', fillOpacity: 1.0 }}
}}).addTo(map);

// School markers
SCHOOLS.forEach(function(s) {{
  L.circleMarker([s.lat, s.lon], {{
    radius: 6, color: '#fff', weight: 2,
    fillColor: '#e74c3c', fillOpacity: 0.9,
  }}).bindTooltip(s.name, {{ direction: 'top', offset: [0, -8] }}).addTo(map);
}});

// Fit to district bounds
map.fitBounds(districtLayer.getBounds().pad(0.05));

// Legend
var legend = L.control({{ position: 'bottomright' }});
legend.onAdd = function() {{
  var div = L.DomUtil.create('div', 'info-box');
  div.innerHTML = '<h3>Legend</h3>'
    + '<div class="legend-row"><span class="legend-swatch" style="background:#27ae60;opacity:0.6"></span> Residential parcel</div>'
    + '<div class="legend-row"><span class="legend-swatch" style="border:2px dashed #2c3e50;background:transparent"></span> District boundary</div>'
    + '<div class="legend-row"><span class="legend-swatch" style="background:#e74c3c;border-radius:50%"></span> Elementary school</div>';
  return div;
}};
legend.addTo(map);
</script>
</body>
</html>"""

# ---------------------------------------------------------------------------
# Write output
# ---------------------------------------------------------------------------
ASSETS_MAPS.mkdir(parents=True, exist_ok=True)
OUTPUT_HTML.write_text(html, encoding="utf-8")
print(f"\nWrote {OUTPUT_HTML}  ({len(html):,} bytes)")
