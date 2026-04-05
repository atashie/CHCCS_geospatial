"""Generate a standalone HTML map of the CHCCS drive road network,
color-coded by road type / speed limit, with toggleable layers for
intersection controls (stop signs, signals) and the 100 m analysis grid."""

import json
from pathlib import Path

import geopandas as gpd
import numpy as np
import osmnx as ox
import pandas as pd
from shapely.geometry import LineString, Point, box as shapely_box, mapping

# ---------------------------------------------------------------------------
# Paths
# ---------------------------------------------------------------------------
PROJECT_ROOT = Path(__file__).resolve().parent.parent
DATA_CACHE = PROJECT_ROOT / "data" / "cache"
ASSETS_MAPS = PROJECT_ROOT / "assets" / "maps"
OUTPUT_HTML = ASSETS_MAPS / "road_network.html"

DISTRICT_CACHE = DATA_CACHE / "chccs_district_boundary.gpkg"
NETWORK_GRAPHML = DATA_CACHE / "network_drive.graphml"
INTERSECTION_TAGS = DATA_CACHE / "intersection_control_tags.json"
PIXEL_GRID_CSV = DATA_CACHE / "closure_analysis" / "pixel_grid.csv"
SCHOOL_CSV = DATA_CACHE / "nces_school_locations.csv"

CRS_WGS84 = "EPSG:4326"
CRS_UTM17N = "EPSG:32617"

# Posted speeds for legend display
DRIVE_POSTED_SPEEDS_MPH = {
    "motorway": 65, "motorway_link": 55,
    "trunk": 55, "trunk_link": 45,
    "primary": 45, "primary_link": 35,
    "secondary": 35, "secondary_link": 30,
    "tertiary": 30, "tertiary_link": 25,
    "residential": 25, "living_street": 15,
    "service": 15, "unclassified": 25,
}

# Color scheme: warm = fast, cool = slow
ROAD_COLORS = {
    "motorway": "#d73027", "motorway_link": "#d73027",
    "trunk": "#f46d43", "trunk_link": "#f46d43",
    "primary": "#fdae61", "primary_link": "#fdae61",
    "secondary": "#fee08b", "secondary_link": "#fee08b",
    "tertiary": "#d9ef8b", "tertiary_link": "#d9ef8b",
    "residential": "#91bfdb", "living_street": "#4575b4",
    "service": "#cccccc", "unclassified": "#91bfdb",
}

ROAD_WEIGHTS = {
    "motorway": 4, "motorway_link": 3,
    "trunk": 3.5, "trunk_link": 2.5,
    "primary": 3, "primary_link": 2,
    "secondary": 2.5, "secondary_link": 1.8,
    "tertiary": 2, "tertiary_link": 1.5,
    "residential": 1.2, "living_street": 1,
    "service": 0.8, "unclassified": 1.2,
}

# Legend groupings (combine _link into parent for display)
ROAD_LEGEND = [
    ("motorway", "Motorway / Freeway", 65),
    ("trunk", "Trunk / US Highway", 55),
    ("primary", "Primary / State Hwy", 45),
    ("secondary", "Secondary", 35),
    ("tertiary", "Tertiary", 30),
    ("residential", "Residential", 25),
    ("living_street", "Living Street", 15),
    ("service", "Service / Alley", 15),
]


def _round_coords(geom_dict, precision=5):
    def _r(coords):
        if isinstance(coords[0], (list, tuple)):
            return [_r(c) for c in coords]
        return [round(c, precision) for c in coords]
    result = dict(geom_dict)
    if "coordinates" in result:
        result["coordinates"] = _r(result["coordinates"])
    return result


# ---------------------------------------------------------------------------
# 1. Load road network edges
# ---------------------------------------------------------------------------
print("[1/5] Loading drive network ...")
G = ox.load_graphml(NETWORK_GRAPHML)

edges = []
seen = set()
for u, v, key, data in G.edges(keys=True, data=True):
    canon = (min(u, v), max(u, v), key)
    if canon in seen:
        continue
    seen.add(canon)

    if "geometry" in data:
        geom = data["geometry"]
    else:
        geom = LineString([
            (G.nodes[u]["x"], G.nodes[u]["y"]),
            (G.nodes[v]["x"], G.nodes[v]["y"]),
        ])

    highway = data.get("highway", "residential")
    if isinstance(highway, list):
        highway = highway[0]

    length_m = data.get("length", 0)
    if length_m < 15:
        continue

    edges.append({
        "geometry": geom,
        "highway": highway,
        "length_m": length_m,
    })

edges_gdf = gpd.GeoDataFrame(edges, crs=CRS_WGS84)
print(f"  {len(edges_gdf)} edges")

# Simplify geometries to reduce file size
edges_utm = edges_gdf.to_crs(CRS_UTM17N)
edges_utm["geometry"] = edges_utm.geometry.simplify(3, preserve_topology=True)
edges_gdf = edges_utm.to_crs(CRS_WGS84)

# Convert to GeoJSON with highway attribute
edge_features = []
for _, row in edges_gdf.iterrows():
    if row.geometry is None or row.geometry.is_empty:
        continue
    edge_features.append({
        "type": "Feature",
        "geometry": _round_coords(mapping(row.geometry)),
        "properties": {"h": row["highway"]},
    })
edges_json = json.dumps(
    {"type": "FeatureCollection", "features": edge_features},
    separators=(",", ":"),
)
print(f"  GeoJSON: {len(edges_json):,} bytes")

# ---------------------------------------------------------------------------
# 2. Load intersection control tags + node positions
# ---------------------------------------------------------------------------
print("[2/5] Loading intersection controls ...")
with open(INTERSECTION_TAGS) as f:
    tags_raw = json.load(f)

# Map OSM node IDs to graph node lat/lon
controls = []  # [lat, lon, type_index]
# type_index: 0=signal, 1=stop
type_map = {"traffic_signals": 0, "stop": 1}

for nid_str, tag in tags_raw.items():
    nid = int(nid_str)
    if nid not in G.nodes:
        continue
    tidx = type_map.get(tag)
    if tidx is None:
        continue
    nd = G.nodes[nid]
    controls.append([round(nd["y"], 5), round(nd["x"], 5), tidx])

controls_json = json.dumps(controls, separators=(",", ":"))
print(f"  {sum(1 for c in controls if c[2]==0)} signals, "
      f"{sum(1 for c in controls if c[2]==1)} stop signs")

# ---------------------------------------------------------------------------
# 3. Load 100 m grid
# ---------------------------------------------------------------------------
print("[3/5] Loading 100 m pixel grid ...")
grid = pd.read_csv(PIXEL_GRID_CSV)
# Transmit as compact [[lat, lon], ...] array
grid_points = [[round(r["lat"], 5), round(r["lon"], 5)] for _, r in grid.iterrows()]
grid_json = json.dumps(grid_points, separators=(",", ":"))
# Compute cell half-dimensions in degrees (matching school_desert.py create_grid)
_dist_tmp = gpd.read_file(DISTRICT_CACHE).to_crs(CRS_WGS84)
_center_lat = (_dist_tmp.total_bounds[1] + _dist_tmp.total_bounds[3]) / 2
grid_dlat = 100.0 / 111_320.0
grid_dlon = 100.0 / (111_320.0 * np.cos(np.radians(_center_lat)))
print(f"  {len(grid_points)} grid points, dlat={grid_dlat:.6f}, dlon={grid_dlon:.6f}")

# ---------------------------------------------------------------------------
# 4. District boundary + schools
# ---------------------------------------------------------------------------
print("[4/5] Loading district boundary and schools ...")
district = gpd.read_file(DISTRICT_CACHE).to_crs(CRS_WGS84)
dist_features = []
for _, row in district.iterrows():
    if row.geometry is None:
        continue
    dist_features.append({
        "type": "Feature",
        "geometry": _round_coords(mapping(row.geometry)),
        "properties": {},
    })
district_json = json.dumps(
    {"type": "FeatureCollection", "features": dist_features},
    separators=(",", ":"),
)

schools = pd.read_csv(SCHOOL_CSV)
schools_json = json.dumps(
    [{"name": r["school"], "lat": r["lat"], "lon": r["lon"]}
     for _, r in schools.iterrows()],
    separators=(",", ":"),
)

bounds = district.total_bounds
center_lat = (bounds[1] + bounds[3]) / 2
center_lon = (bounds[0] + bounds[2]) / 2

# Road color/weight lookup for JS
road_colors_js = json.dumps(ROAD_COLORS, separators=(",", ":"))
road_weights_js = json.dumps(ROAD_WEIGHTS, separators=(",", ":"))

# ---------------------------------------------------------------------------
# 5. Build HTML
# ---------------------------------------------------------------------------
print("[5/5] Building HTML ...")

html = f"""<!DOCTYPE html>
<html lang="en">
<head>
<meta charset="UTF-8">
<meta name="viewport" content="width=device-width, initial-scale=1.0">
<title>CHCCS Road Network</title>
<link rel="stylesheet" href="https://unpkg.com/leaflet@1.9.4/dist/leaflet.css"/>
<script src="https://unpkg.com/leaflet@1.9.4/dist/leaflet.js"></script>
<style>
  * {{ margin: 0; padding: 0; box-sizing: border-box; }}
  html, body {{ height: 100%; }}
  #map {{ height: 100%; width: 100%; }}
  .info-box {{
    background: white; padding: 10px 14px; border-radius: 6px;
    box-shadow: 0 2px 8px rgba(0,0,0,0.25);
    font: 12px/1.5 system-ui, sans-serif;
    max-width: 240px;
  }}
  .info-box h3 {{ margin: 0 0 6px; font-size: 14px; }}
  .legend-row {{ display: flex; align-items: center; gap: 6px; margin: 2px 0; }}
  .legend-swatch {{ flex-shrink: 0; height: 4px; width: 24px; border-radius: 2px; }}
  .legend-label {{ font-size: 11px; }}
  .legend-speed {{ font-size: 11px; color: #888; margin-left: auto; white-space: nowrap; }}
  .control-legend {{
    background: white; padding: 8px 12px; border-radius: 6px;
    box-shadow: 0 2px 8px rgba(0,0,0,0.25);
    font: 12px/1.5 system-ui, sans-serif;
  }}
  .control-legend label {{ display: flex; align-items: center; gap: 6px; cursor: pointer; margin: 2px 0; }}
  .control-legend input {{ margin: 0; }}
</style>
</head>
<body>
<div id="map"></div>
<script>
var EDGES = {edges_json};
var CONTROLS = {controls_json};
var GRID = {grid_json};
var GRID_DLAT = {grid_dlat};
var GRID_DLON = {grid_dlon};
var DISTRICT = {district_json};
var SCHOOLS = {schools_json};
var ROAD_COLORS = {road_colors_js};
var ROAD_WEIGHTS = {road_weights_js};

var map = L.map('map', {{
  center: [{center_lat}, {center_lon}],
  zoom: 12,
  zoomControl: true,
}});

// Custom pane for intersection controls so they render on top of everything
map.createPane('controlsPane');
map.getPane('controlsPane').style.zIndex = 650;

L.tileLayer('https://{{s}}.basemaps.cartocdn.com/light_all/{{z}}/{{x}}/{{y}}{{r}}.png', {{
  attribution: '&copy; OpenStreetMap &copy; CARTO',
  maxZoom: 19,
}}).addTo(map);

// District boundary
L.geoJSON(DISTRICT, {{
  style: {{ color: '#2c3e50', weight: 2.5, fillOpacity: 0, dashArray: '6,4' }}
}}).addTo(map);

// Road edges (always on)
L.geoJSON(EDGES, {{
  style: function(f) {{
    var h = f.properties.h;
    return {{
      color: ROAD_COLORS[h] || '#999',
      weight: ROAD_WEIGHTS[h] || 1,
      opacity: 0.9,
    }};
  }}
}}).addTo(map);

// School markers
SCHOOLS.forEach(function(s) {{
  L.circleMarker([s.lat, s.lon], {{
    radius: 6, color: '#fff', weight: 2,
    fillColor: '#e74c3c', fillOpacity: 0.9,
  }}).bindTooltip(s.name, {{ direction: 'top', offset: [0, -8] }}).addTo(map);
}});

// -- Toggleable layers --

// Intersection controls (signals + stops) — rendered in controlsPane (on top)
var controlCanvas = L.canvas({{ padding: 0.5, pane: 'controlsPane' }});
var signalLayer = L.layerGroup();
var stopLayer = L.layerGroup();
var CTRL_STYLES = [
  {{ radius: 6, fillColor: '#8e44ad', color: '#fff', weight: 1.5, fillOpacity: 0.9 }},   // signal — purple
  {{ radius: 4.2, fillColor: '#f39c12', color: '#fff', weight: 1, fillOpacity: 0.9 }},    // stop — orange
];
for (var i = 0; i < CONTROLS.length; i++) {{
  var c = CONTROLS[i];
  var opts = Object.assign({{ renderer: controlCanvas }}, CTRL_STYLES[c[2]]);
  var marker = L.circleMarker([c[0], c[1]], opts);
  if (c[2] === 0) signalLayer.addLayer(marker);
  else stopLayer.addLayer(marker);
}}

// 100 m grid — actual cell outlines (rectangles)
var gridLayer = L.layerGroup();
var gridLoaded = false;
function ensureGridLoaded() {{
  if (gridLoaded) return;
  gridLoaded = true;
  var hLat = GRID_DLAT / 2;
  var hLon = GRID_DLON / 2;
  for (var i = 0; i < GRID.length; i++) {{
    var lat = GRID[i][0], lon = GRID[i][1];
    L.rectangle(
      [[lat - hLat, lon - hLon], [lat + hLat, lon + hLon]],
      {{ color: '#666', weight: 0.5, fillOpacity: 0, opacity: 0.4 }}
    ).addTo(gridLayer);
  }}
}}

// Toggle control
var toggleCtrl = L.control({{ position: 'topright' }});
toggleCtrl.onAdd = function() {{
  var div = L.DomUtil.create('div', 'control-legend');
  div.innerHTML =
    '<label><input type="checkbox" id="cb-signals"> Traffic signals</label>' +
    '<label><input type="checkbox" id="cb-stops"> Stop signs</label>' +
    '<label><input type="checkbox" id="cb-grid"> 100 m analysis grid</label>';

  L.DomEvent.disableClickPropagation(div);

  div.querySelector('#cb-signals').addEventListener('change', function() {{
    if (this.checked) signalLayer.addTo(map); else map.removeLayer(signalLayer);
  }});
  div.querySelector('#cb-stops').addEventListener('change', function() {{
    if (this.checked) stopLayer.addTo(map); else map.removeLayer(stopLayer);
  }});
  div.querySelector('#cb-grid').addEventListener('change', function() {{
    if (this.checked) {{ ensureGridLoaded(); gridLayer.addTo(map); }}
    else map.removeLayer(gridLayer);
  }});

  return div;
}};
toggleCtrl.addTo(map);

// Legend
var legend = L.control({{ position: 'bottomright' }});
legend.onAdd = function() {{
  var div = L.DomUtil.create('div', 'info-box');
  var rows = [
    ['motorway', 'Motorway / Freeway', '65 mph'],
    ['trunk', 'Trunk / US Highway', '55 mph'],
    ['primary', 'Primary / State Hwy', '45 mph'],
    ['secondary', 'Secondary', '35 mph'],
    ['tertiary', 'Tertiary', '30 mph'],
    ['residential', 'Residential', '25 mph'],
    ['living_street', 'Living Street', '15 mph'],
    ['service', 'Service / Alley', '15 mph'],
  ];
  var html = '<h3>Road Type</h3>';
  for (var i = 0; i < rows.length; i++) {{
    html += '<div class="legend-row">'
      + '<span class="legend-swatch" style="background:' + ROAD_COLORS[rows[i][0]] + '"></span>'
      + '<span class="legend-label">' + rows[i][1] + '</span>'
      + '<span class="legend-speed">' + rows[i][2] + '</span>'
      + '</div>';
  }}
  html += '<div style="margin-top:8px;border-top:1px solid #eee;padding-top:6px">';
  html += '<div class="legend-row"><span style="display:inline-block;width:12px;height:12px;border-radius:50%;background:#8e44ad;border:1.5px solid #fff"></span><span class="legend-label">Traffic signal</span></div>';
  html += '<div class="legend-row"><span style="display:inline-block;width:8px;height:8px;border-radius:50%;background:#f39c12;border:1px solid #fff"></span><span class="legend-label">Stop sign</span></div>';
  html += '<div class="legend-row"><span style="display:inline-block;width:12px;height:12px;border:1px solid #666;background:transparent"></span><span class="legend-label">100 m grid cell</span></div>';
  html += '</div>';
  html += '<div style="font-size:10px;color:#888;margin-top:6px">Speed = posted limit<br>Source: OpenStreetMap</div>';
  div.innerHTML = html;
  return div;
}};
legend.addTo(map);

// Fit to district
map.fitBounds(L.geoJSON(DISTRICT).getBounds().pad(0.05));
</script>
</body>
</html>"""

ASSETS_MAPS.mkdir(parents=True, exist_ok=True)
OUTPUT_HTML.write_text(html, encoding="utf-8")
print(f"\nWrote {OUTPUT_HTML}  ({len(html):,} bytes)")
