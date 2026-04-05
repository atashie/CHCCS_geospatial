"""Standalone Travel-Time-only closure map with population masking.

Reproduces the "Travel Time" tab of assets/maps/school_closure_analysis.html
(grid-based canvas heatmap of minutes-to-nearest-open-school by mode, with
multi-select school closures, absolute-vs-delta views, zone-boundary and
road-network overlays) but additionally masks grid pixels that do not overlap
with any populated residential parcel (i.e., pixels with no population dots in
assets/maps/school_socioeconomic_map.html / assets/maps/population_dots.html).

Approach:
  1. Extract embedded data (grid meta, per-school grids, schools, zones, network,
     colormap LUTs) from assets/maps/school_closure_analysis.html.
  2. Build a population-presence mask by rasterizing the intersection of
     populated decennial census blocks with improved residential parcels onto
     the closure-analysis grid.
  3. Apply the mask to every per-school travel-time grid (set unpopulated pixels
     to NaN), then emit a standalone HTML with only the Travel Time controls.

Output:
  assets/maps/closure_travel_time_populated.html
"""
from __future__ import annotations

import base64
import json
import re
from pathlib import Path

import geopandas as gpd
import numpy as np
import pandas as pd
from rasterio.features import rasterize
from rasterio.transform import from_bounds

PROJECT_ROOT = Path(__file__).resolve().parent.parent
DATA_RAW = PROJECT_ROOT / "data" / "raw"
DATA_CACHE = PROJECT_ROOT / "data" / "cache"
ASSETS_MAPS = PROJECT_ROOT / "assets" / "maps"

SRC_HTML = ASSETS_MAPS / "school_closure_analysis.html"
OUT_HTML = ASSETS_MAPS / "closure_travel_time_populated.html"

PARCEL_POLYS = DATA_RAW / "properties" / "combined_data_polys.gpkg"
DISTRICT_CACHE = DATA_CACHE / "chccs_district_boundary.gpkg"
DECENNIAL_CACHE = DATA_CACHE / "census_decennial_blocks.gpkg"
SCHOOL_CSV = DATA_CACHE / "nces_school_locations.csv"

CRS_WGS84 = "EPSG:4326"
CRS_UTM17N = "EPSG:32617"


# ---------------------------------------------------------------------------
# Step 1: Extract data from the existing closure-analysis HTML
# ---------------------------------------------------------------------------
def extract_var(line: str, name: str) -> str:
    """Return the JSON string assigned to `var name = ... ;` on this line."""
    pattern = rf"var\s+{re.escape(name)}\s*=\s*(.*);\s*$"
    m = re.match(pattern, line.strip())
    if not m:
        raise ValueError(f"Could not parse var {name} from line: {line[:120]!r}...")
    return m.group(1)


def extract_embedded_data(html_path: Path) -> dict:
    """Pull the variables we need out of the closure-analysis HTML."""
    print(f"[1/4] Extracting embedded data from {html_path.name}")
    targets = {
        "MODE_LABELS", "MODE_RANGES", "SCHOOLS", "SCHOOL_NAMES", "GRID_META",
        "PER_SCHOOL_GRIDS_B64", "ZONE_POLYGONS", "NETWORK_GEOJSON",
        "CMAP_YLORD_B64", "CMAP_ORANGES_B64",
    }
    found = {}
    with open(html_path, "r", encoding="utf-8") as f:
        for line in f:
            stripped = line.strip()
            for t in targets - set(found.keys()):
                if stripped.startswith(f"var {t} ") or stripped.startswith(f"var {t}="):
                    found[t] = extract_var(line, t)
                    break
            if len(found) == len(targets):
                break
    missing = targets - set(found.keys())
    if missing:
        raise RuntimeError(f"Missing variables in source HTML: {missing}")
    for k, v in found.items():
        print(f"  extracted {k}  ({len(v):>10,} chars)")
    return found


# ---------------------------------------------------------------------------
# Step 2: Compute population-presence mask on the closure grid
# ---------------------------------------------------------------------------
def compute_population_mask(grid_meta: dict) -> np.ndarray:
    """Return a (nRows, nCols) bool array marking pixels that overlap with
    populated residential parcels (matching the dot-placement logic in
    population_dot_map.py and socioeconomic_map).

    A pixel is True iff its 100m footprint intersects the union of
    (block_geom ∩ improved_residential_parcels) for any block with
    total_population > 0. Blocks with no parcel overlap fall back to the
    block geometry itself.
    """
    print("[2/4] Building population-presence mask")

    # --- Load residential parcels (improved residential only, matching
    #     population_dot_map.py)
    parcels = gpd.read_file(PARCEL_POLYS).to_crs(CRS_WGS84)
    mask = parcels.get("is_residential", pd.Series(False, index=parcels.index))
    if "imp_vac" in parcels.columns:
        mask = mask & parcels["imp_vac"].str.contains("Improved", case=False, na=False)
    parcels = parcels[mask].copy()
    print(f"  {len(parcels):,} improved residential parcels")

    # --- Load decennial census blocks
    blocks = gpd.read_file(DECENNIAL_CACHE).to_crs(CRS_WGS84)

    # --- Clip blocks to district boundary
    district = gpd.read_file(DISTRICT_CACHE).to_crs(CRS_WGS84)
    blocks = gpd.overlay(blocks, district[["geometry"]], how="intersection")
    print(f"  {len(blocks):,} decennial blocks within district")

    # --- Compute total population per block (matches RACE_CATEGORIES cols)
    race_cols = ["white_alone", "black_alone", "hispanic_total", "asian_alone",
                 "two_plus"]
    # Add an "other" column as the sum of aian/nhpi/other_alone (mirrors
    # population_dot_map.py "other_race" fallback)
    other_cols = [c for c in ["aian_alone", "nhpi_alone", "other_alone"]
                  if c in blocks.columns]
    present_race_cols = [c for c in race_cols if c in blocks.columns]
    for c in present_race_cols:
        blocks[c] = pd.to_numeric(blocks[c], errors="coerce").fillna(0).clip(lower=0)
    if other_cols:
        blocks["_other_sum"] = (
            blocks[other_cols].apply(pd.to_numeric, errors="coerce")
            .fillna(0).clip(lower=0).sum(axis=1)
        )
    else:
        blocks["_other_sum"] = 0
    blocks["_total_pop"] = blocks[present_race_cols].sum(axis=1) + blocks["_other_sum"]

    populated = blocks[blocks["_total_pop"] > 0].copy()
    print(f"  {len(populated):,} populated blocks  (total {int(populated['_total_pop'].sum()):,} residents)")

    # --- For each populated block, compute placement geom = block ∩ parcels
    #     (falling back to block geom if no parcels intersect; same as
    #     population_dot_map.py)
    parcels_utm = parcels.to_crs(CRS_UTM17N)
    parcels_sindex = parcels_utm.sindex
    pop_utm = populated.to_crs(CRS_UTM17N)
    placement_polys = []
    for _, block in pop_utm.iterrows():
        geom = block.geometry
        if geom is None or geom.is_empty:
            continue
        candidates = list(parcels_sindex.intersection(geom.bounds))
        placed = None
        if candidates:
            try:
                pu = parcels_utm.iloc[candidates].union_all()
            except AttributeError:
                pu = parcels_utm.iloc[candidates].unary_union
            inter = geom.intersection(pu)
            if not inter.is_empty and inter.area > 10:
                placed = inter
        if placed is None:
            placed = geom  # fallback mirrors population_dot_map.py
        if placed is not None and not placed.is_empty:
            placement_polys.append(placed)

    if not placement_polys:
        raise RuntimeError("No placement polygons generated")

    # --- Rasterize onto the closure grid
    n_rows = int(grid_meta["nRows"])
    n_cols = int(grid_meta["nCols"])
    lon_min = float(grid_meta["lonMin"])
    lat_min = float(grid_meta["latMin"])
    lon_max = float(grid_meta["lonMax"])
    lat_max = float(grid_meta["latMax"])
    # rasterio transform: (west, south, east, north, width, height)
    # Note: we pass shapes in lon/lat (WGS84) and transform in WGS84.
    transform = from_bounds(lon_min, lat_min, lon_max, lat_max, n_cols, n_rows)

    # Convert placement polys back to WGS84 for rasterization
    placement_gdf_utm = gpd.GeoDataFrame(geometry=placement_polys, crs=CRS_UTM17N)
    placement_wgs = placement_gdf_utm.to_crs(CRS_WGS84)

    shapes = [(g, 1) for g in placement_wgs.geometry if g is not None and not g.is_empty]
    pop_mask = rasterize(
        shapes=shapes,
        out_shape=(n_rows, n_cols),
        transform=transform,
        fill=0,
        all_touched=True,  # mark a pixel populated if ANY part of it is on a populated parcel
        dtype=np.uint8,
    ).astype(bool)

    n_populated = int(pop_mask.sum())
    print(f"  populated pixels: {n_populated:,} / {n_rows * n_cols:,} "
          f"({100.0 * n_populated / (n_rows * n_cols):.1f}% of grid)")
    return pop_mask


# ---------------------------------------------------------------------------
# Step 3: Apply the mask to every per-school travel-time grid
# ---------------------------------------------------------------------------
def apply_mask_to_grids(per_school_grids_json: str, mask: np.ndarray,
                        grid_meta: dict) -> tuple[str, int, int]:
    """Decode each base64 float32 grid, set unpopulated pixels to NaN,
    re-encode. Returns (new_json_str, n_pixels_before_any_school, n_pixels_after)."""
    print("[3/4] Applying mask to per-school grids")
    per_school = json.loads(per_school_grids_json)
    n_rows = int(grid_meta["nRows"])
    n_cols = int(grid_meta["nCols"])
    n_px = n_rows * n_cols
    flat_mask = mask.reshape(-1)  # True where populated

    # Count non-NaN pixels in the original baseline for reporting
    before = 0
    after = 0
    first_grid = next(iter(next(iter(per_school.values())).values()))
    baseline_flat = np.frombuffer(base64.b64decode(first_grid), dtype=np.float32)
    before = int(np.isfinite(baseline_flat).sum())

    out = {}
    for mode, grids in per_school.items():
        out[mode] = {}
        for school, b64 in grids.items():
            arr = np.frombuffer(base64.b64decode(b64), dtype=np.float32).copy()
            if arr.size != n_px:
                raise ValueError(
                    f"Grid size mismatch for {mode}/{school}: {arr.size} vs {n_px}"
                )
            # Set unpopulated pixels to NaN
            arr[~flat_mask] = np.nan
            out[mode][school] = base64.b64encode(
                arr.astype(np.float32).tobytes()
            ).decode("ascii")
    # Count non-NaN pixels in the masked baseline
    post = np.frombuffer(
        base64.b64decode(next(iter(next(iter(out.values())).values()))),
        dtype=np.float32,
    )
    after = int(np.isfinite(post).sum())
    print(f"  valid pixels: {before:,} -> {after:,}  (kept {100.0 * after / max(before, 1):.1f}%)")
    return json.dumps(out, separators=(",", ":")), before, after


# ---------------------------------------------------------------------------
# Step 4: Emit the standalone Travel-Time-only HTML
# ---------------------------------------------------------------------------
HTML_TEMPLATE = r"""<!DOCTYPE html>
<html lang="en">
<head>
<meta charset="UTF-8">
<meta name="viewport" content="width=device-width, initial-scale=1.0">
<title>CHCCS Travel Time to Nearest Open School (Populated Areas Only)</title>
<link rel="stylesheet" href="https://unpkg.com/leaflet@1.9.4/dist/leaflet.css"/>
<script src="https://unpkg.com/leaflet@1.9.4/dist/leaflet.js"></script>
<style>
* { margin: 0; padding: 0; box-sizing: border-box; }
html, body { height: 100vh; overflow: hidden; font-family: 'Segoe UI', Tahoma, sans-serif; }
body { display: flex; flex-direction: row; }
.leaflet-image-layer { image-rendering: pixelated; image-rendering: -moz-crisp-edges; image-rendering: crisp-edges; }
#main-column { flex: 1; display: flex; flex-direction: column; min-width: 0; height: 100vh; }
#banner { background: white; padding: 10px 20px; border-bottom: 1px solid #dee2e6; text-align: center; flex-shrink: 0; }
#banner h1 { margin: 0; font-size: 18px; font-weight: 600; color: #333; }
#banner .subtitle { margin: 2px 0 0 0; font-size: 12px; color: #666; }
#map { flex: 1; width: 100%; }
#controls {
  flex: 0 0 320px; width: 320px; height: 100vh; overflow-y: auto;
  background: white; border-left: 1px solid #dee2e6;
  box-shadow: -2px 0 8px rgba(0,0,0,0.1);
  font-size: 13px; padding: 12px 15px;
}
#controls label { display: block; margin: 2px 0; cursor: pointer; padding: 2px 4px; border-radius: 3px; font-size: 12px; }
#controls label:hover { background: #f0f0f0; }
.section-title { font-weight: bold; margin: 10px 0 5px 0; color: #555; font-size: 11px; text-transform: uppercase; }
.section-subtitle { font-size: 10px; font-style: italic; color: #888; margin: -3px 0 5px 0; }
.subsection { margin-left: 8px; padding-left: 8px; border-left: 2px solid #eee; }
.scenario-list { max-height: 220px; overflow-y: auto; border: 1px solid #eee; border-radius: 4px; padding: 2px; margin-top: 4px; }
.scenario-list label { font-size: 11px !important; padding: 3px 6px !important; }
.scenario-list label.selected { background: #e8f0fe; border-radius: 3px; }
.legend-box { margin-top: 10px; padding-top: 8px; border-top: 1px solid #ddd; }
.gradient-bar { height: 12px; border-radius: 3px; margin: 4px 0; }
.range-labels { display: flex; justify-content: space-between; font-size: 11px; color: #666; }
.school-marker-info { font-size: 12px; margin-top: 8px; padding-top: 8px; border-top: 1px solid #ddd; color: #666; }
.school-marker-info .closed { color: #dc3545; font-weight: bold; }
.mask-note { margin-top: 10px; padding: 6px 8px; background: #fff3cd; border: 1px solid #ffeaa7; border-radius: 4px; font-size: 11px; color: #664d03; line-height: 1.4; }
#hover-tooltip {
  position: fixed; z-index: 2000;
  background: rgba(0,0,0,0.85); color: #fff;
  padding: 5px 10px; border-radius: 4px;
  font-size: 12px; font-family: 'Segoe UI', Tahoma, sans-serif;
  pointer-events: none; display: none;
  white-space: nowrap; max-width: 350px;
}
</style>
</head>
<body>
<div id="hover-tooltip"></div>
<div id="main-column">
  <div id="banner">
    <h1>CHCCS Travel Time to Nearest Open School</h1>
    <p class="subtitle">Heatmap masked to parcels with residential population (2020 Decennial Census + Orange County residential parcels)</p>
  </div>
  <div id="map"></div>
</div>
<div id="controls">
  <div class="section-title">Schools to Close</div>
  <div class="section-subtitle">Check one or more schools (none = baseline)</div>
  <div class="scenario-list" id="school-list"></div>

  <div class="section-title">Travel Mode</div>
  <div class="section-subtitle">Time to nearest open school</div>
  <div id="mode-options"></div>

  <div class="section-title">View</div>
  <div class="subsection">
    <label><input type="radio" name="view" value="abs" checked onchange="updateMap()"> Absolute travel time</label>
    <label><input type="radio" name="view" value="delta" onchange="updateMap()"> Increase vs. baseline</label>
  </div>

  <div class="section-title">Layers</div>
  <div class="subsection">
    <label><input type="checkbox" id="show-zones" checked onchange="updateMap()"> Zone boundaries</label>
    <label><input type="checkbox" id="show-network" onchange="updateMap()"> Road network</label>
  </div>

  <div class="legend-box">
    <div class="section-title">Legend</div>
    <div id="legend-label"></div>
    <div class="gradient-bar" id="legend-bar"></div>
    <div class="range-labels">
      <span id="legend-min"></span>
      <span id="legend-max"></span>
    </div>
  </div>

  <div class="school-marker-info" id="school-info"></div>

  <div class="mask-note">
    <b>Population mask:</b> pixels with no residential parcels occupied by 2020 Census
    population are rendered transparent. __MASK_STATS__
  </div>
</div>

<script>
var MODE_LABELS = __MODE_LABELS__;
var MODE_RANGES = __MODE_RANGES__;
var SCHOOLS = __SCHOOLS__;
var SCHOOL_NAMES = __SCHOOL_NAMES__;
var GRID_META = __GRID_META__;
var PER_SCHOOL_GRIDS_B64 = __PER_SCHOOL_GRIDS_B64__;
var ZONE_POLYGONS = __ZONE_POLYGONS__;
var NETWORK_GEOJSON = __NETWORK_GEOJSON__;
var DISTRICT = __DISTRICT__;
var CMAP_YLORD_B64 = "__CMAP_YLORD_B64__";
var CMAP_ORANGES_B64 = "__CMAP_ORANGES_B64__";

var map = L.map('map', { zoomControl: true, preferCanvas: true });
L.tileLayer('https://{s}.basemaps.cartocdn.com/light_all/{z}/{x}/{y}{r}.png', {
  attribution: '&copy; OpenStreetMap &copy; CARTO', maxZoom: 19,
}).addTo(map);
map.createPane('heatmapPane');
map.getPane('heatmapPane').style.zIndex = 250;
map.getPane('heatmapPane').style.pointerEvents = 'none';

// District boundary
var districtLayer = L.geoJSON(DISTRICT, {
  style: { color: '#333', weight: 2, dashArray: '5,5', fillOpacity: 0 }
}).addTo(map);
map.fitBounds(districtLayer.getBounds().pad(0.02));

// --- Decode helpers ---
function b64ToFloat32(b64) {
  var raw = atob(b64);
  var buf = new ArrayBuffer(raw.length);
  var u8 = new Uint8Array(buf);
  for (var i = 0; i < raw.length; i++) u8[i] = raw.charCodeAt(i);
  return new Float32Array(buf);
}
function b64ToUint8(b64) {
  var raw = atob(b64);
  var arr = new Uint8Array(raw.length);
  for (var i = 0; i < raw.length; i++) arr[i] = raw.charCodeAt(i);
  return arr;
}
var decodedGrids = {};
function getSchoolGrid(mode, school) {
  var key = mode + '|' + school;
  if (!decodedGrids[key]) {
    var g = PER_SCHOOL_GRIDS_B64[mode];
    if (!g || !g[school]) return null;
    decodedGrids[key] = b64ToFloat32(g[school]);
  }
  return decodedGrids[key];
}
var decodedCmaps = {};
function getCmapLUT(name) {
  if (!decodedCmaps[name]) {
    decodedCmaps[name] = b64ToUint8(name === 'YlOrRd' ? CMAP_YLORD_B64 : CMAP_ORANGES_B64);
  }
  return decodedCmaps[name];
}

// --- Nearest-school grid (client-side min over open schools) ---
function computeNearestSchoolGrid(mode, closed) {
  var nPx = GRID_META.nRows * GRID_META.nCols;
  var result = new Float32Array(nPx);
  var names = new Array(nPx);
  result.fill(Infinity);
  var open = SCHOOL_NAMES.filter(function(s) { return closed.indexOf(s) === -1; });
  for (var si = 0; si < open.length; si++) {
    var g = getSchoolGrid(mode, open[si]);
    if (!g) continue;
    for (var j = 0; j < nPx; j++) {
      if (!isNaN(g[j]) && g[j] < result[j]) { result[j] = g[j]; names[j] = open[si]; }
    }
  }
  for (var j = 0; j < nPx; j++) {
    if (result[j] === Infinity) { result[j] = NaN; names[j] = null; }
  }
  return { values: result, names: names };
}

// --- Canvas heatmap rendering ---
var heatmapOverlay = null, heatmapCanvas = null;
function renderHeatmapCanvas(values, vmin, vmax, cmapName) {
  var nRows = GRID_META.nRows, nCols = GRID_META.nCols;
  if (!heatmapCanvas) { heatmapCanvas = document.createElement('canvas'); heatmapCanvas.width = nCols; heatmapCanvas.height = nRows; }
  var ctx = heatmapCanvas.getContext('2d');
  var img = ctx.createImageData(nCols, nRows);
  var lut = getCmapLUT(cmapName);
  var range = vmax - vmin; if (range < 0.001) range = 1;
  for (var i = 0; i < nRows * nCols; i++) {
    var v = values[i], o = i * 4;
    if (isNaN(v) || v === Infinity) {
      img.data[o] = 0; img.data[o+1] = 0; img.data[o+2] = 0; img.data[o+3] = 0;
    } else {
      var t = Math.max(0, Math.min(1, (v - vmin) / range));
      var li = Math.min(255, Math.floor(t * 255)) * 4;
      img.data[o] = lut[li]; img.data[o+1] = lut[li+1]; img.data[o+2] = lut[li+2]; img.data[o+3] = 210;
    }
  }
  ctx.putImageData(img, 0, 0);
  return heatmapCanvas.toDataURL();
}
function showHeatmap(dataUrl) {
  var bounds = [[GRID_META.latMin, GRID_META.lonMin], [GRID_META.latMax, GRID_META.lonMax]];
  if (!heatmapOverlay) {
    heatmapOverlay = L.imageOverlay(dataUrl, bounds, { opacity: 1, interactive: false, pane: 'heatmapPane' }).addTo(map);
  } else {
    heatmapOverlay.setUrl(dataUrl); heatmapOverlay.setBounds(bounds); heatmapOverlay.setOpacity(1);
  }
}

// --- Hover tooltip: read current grid value at cursor ---
var currentGridResult = null;
var tooltipEl = document.getElementById('hover-tooltip');
map.on('mousemove', function(e) {
  if (!currentGridResult || !GRID_META) return;
  var lat = e.latlng.lat, lon = e.latlng.lng;
  var fracX = (lon - GRID_META.lonMin) / (GRID_META.lonMax - GRID_META.lonMin);
  var fracY = (lat - GRID_META.latMin) / (GRID_META.latMax - GRID_META.latMin);
  var col = Math.floor(fracX * GRID_META.nCols);
  var row = Math.floor((1 - fracY) * GRID_META.nRows);
  if (row < 0 || row >= GRID_META.nRows || col < 0 || col >= GRID_META.nCols) {
    tooltipEl.style.display = 'none'; return;
  }
  var idx = row * GRID_META.nCols + col;
  var val = currentGridResult.values[idx];
  if (isNaN(val) || val === null) { tooltipEl.style.display = 'none'; return; }
  var name = currentGridResult.names ? currentGridResult.names[idx] : null;
  var lines = [];
  if (name) lines.push('Nearest open: ' + name);
  if (currentGridResult.view === 'delta') lines.push('+' + val.toFixed(1) + ' min vs. baseline');
  else lines.push(val.toFixed(1) + ' min');
  tooltipEl.innerHTML = lines.join('<br>');
  tooltipEl.style.left = (e.originalEvent.pageX + 15) + 'px';
  tooltipEl.style.top = (e.originalEvent.pageY - 10) + 'px';
  tooltipEl.style.display = 'block';
});
map.on('mouseout', function() { tooltipEl.style.display = 'none'; });

// --- Overlays ---
var networkLayer = null, zoneLayer = null;
var schoolMarkers = [];
function updateSchoolMarkers(closed) {
  schoolMarkers.forEach(function(m) { map.removeLayer(m); });
  schoolMarkers = [];
  SCHOOLS.forEach(function(s) {
    var isClosed = closed.indexOf(s.name) !== -1;
    var mk = L.circleMarker([s.lat, s.lon], {
      radius: isClosed ? 8 : 7,
      fillColor: isClosed ? '#dc3545' : '#0d6efd',
      color: isClosed ? '#dc3545' : '#0a58ca', weight: 2, opacity: 1,
      fillOpacity: isClosed ? 0.3 : 0.8, dashArray: isClosed ? '4,4' : null,
    });
    var status = isClosed ? '<span style="color:#dc3545;font-weight:bold">CLOSED</span>' : '<span style="color:#198754">Open</span>';
    mk.bindPopup('<b>' + s.name + '</b><br>' + (s.address || '') + '<br>' + status);
    if (isClosed) {
      var xIcon = L.divIcon({ html: '<span style="color:#dc3545;font-size:18px;font-weight:bold;">&times;</span>',
                              className: 'closed-school-x', iconSize: [20,20], iconAnchor: [10,10] });
      var xm = L.marker([s.lat, s.lon], { icon: xIcon }).addTo(map);
      schoolMarkers.push(xm);
    }
    mk.addTo(map);
    schoolMarkers.push(mk);
  });
}
function updateZonePolygons(mode, show) {
  if (zoneLayer) { map.removeLayer(zoneLayer); zoneLayer = null; }
  if (!show) return;
  var geo = ZONE_POLYGONS['baseline|' + mode];
  if (!geo || !geo.features) return;
  var colors = ['#1f77b4','#ff7f0e','#2ca02c','#d62728','#9467bd','#8c564b','#e377c2','#7f7f7f','#bcbd22','#17becf','#aec7e8'];
  var schoolColors = {}, ci = 0;
  geo.features.forEach(function(f) {
    var s = f.properties.school;
    if (!schoolColors[s]) { schoolColors[s] = colors[ci % colors.length]; ci++; }
  });
  zoneLayer = L.geoJSON(geo, {
    style: function(f) { return { fillColor: 'transparent', color: schoolColors[f.properties.school] || '#ccc', weight: 2.5, fillOpacity: 0, opacity: 0.8 }; },
    onEachFeature: function(f, layer) { layer.bindPopup('<b>Zone:</b> ' + f.properties.school); }
  }).addTo(map);
}
function updateNetwork(mode, show) {
  if (networkLayer) { map.removeLayer(networkLayer); networkLayer = null; }
  if (show && NETWORK_GEOJSON[mode]) {
    networkLayer = L.geoJSON(NETWORK_GEOJSON[mode], {
      style: { color: '#333', weight: 1, opacity: 0.4 }, interactive: false,
    }).addTo(map);
  }
}

// --- School checkbox list + mode radios ---
function getClosedSchools() {
  var closed = [];
  document.querySelectorAll('#school-list input[type="checkbox"]').forEach(function(cb) {
    if (cb.checked) closed.push(cb.value);
  });
  return closed;
}
function getSelectedRadio(name) {
  var radios = document.querySelectorAll('input[name="' + name + '"]');
  for (var i = 0; i < radios.length; i++) if (radios[i].checked) return radios[i].value;
  return null;
}
(function populate() {
  var list = document.getElementById('school-list');
  SCHOOL_NAMES.forEach(function(name) {
    var label = document.createElement('label');
    var cb = document.createElement('input');
    cb.type = 'checkbox'; cb.value = name;
    cb.onchange = function() { this.parentElement.classList.toggle('selected', this.checked); updateMap(); };
    label.appendChild(cb);
    label.appendChild(document.createTextNode(' ' + name.replace(' Elementary', '').replace(' Bilingue', '')));
    list.appendChild(label);
  });
  var modeDiv = document.getElementById('mode-options');
  var first = true;
  Object.keys(MODE_LABELS).forEach(function(key) {
    var label = document.createElement('label');
    var radio = document.createElement('input');
    radio.type = 'radio'; radio.name = 'mode'; radio.value = key;
    radio.onchange = updateMap;
    if (first) { radio.checked = true; first = false; }
    label.appendChild(radio);
    label.appendChild(document.createTextNode(' ' + MODE_LABELS[key]));
    modeDiv.appendChild(label);
  });
})();

// --- Main update ---
function updateMap() {
  var closed = getClosedSchools();
  var mode = getSelectedRadio('mode');
  var view = getSelectedRadio('view');
  var showZones = document.getElementById('show-zones').checked;
  var showNetwork = document.getElementById('show-network').checked;
  if (!mode) return;
  var isBaseline = closed.length === 0;

  var deltaRadio = document.querySelector('input[name="view"][value="delta"]');
  if (isBaseline) {
    if (view === 'delta') { document.querySelector('input[name="view"][value="abs"]').checked = true; view = 'abs'; }
    deltaRadio.disabled = true; deltaRadio.parentElement.style.opacity = '0.4';
  } else {
    deltaRadio.disabled = false; deltaRadio.parentElement.style.opacity = '1';
  }

  var result = computeNearestSchoolGrid(mode, closed);
  var ranges = MODE_RANGES[mode];
  if (view === 'delta') {
    var base = computeNearestSchoolGrid(mode, []);
    var delta = new Float32Array(result.values.length);
    for (var i = 0; i < result.values.length; i++) {
      var cv = result.values[i], bv = base.values[i];
      if (isNaN(cv) || isNaN(bv)) delta[i] = NaN;
      else { var d = cv - bv; delta[i] = d > 0.01 ? d : NaN; }
    }
    showHeatmap(renderHeatmapCanvas(delta, ranges.delta[0], ranges.delta[1], 'Oranges'));
    document.getElementById('legend-label').textContent = 'Added minutes (vs baseline)';
    document.getElementById('legend-bar').style.background = 'linear-gradient(to right, #fff5eb, #fdbe85, #fd8d3c, #e6550d, #a63603)';
    document.getElementById('legend-min').textContent = ranges.delta[0] + ' min';
    document.getElementById('legend-max').textContent = ranges.delta[1] + ' min';
    currentGridResult = { values: delta, names: result.names, view: 'delta' };
  } else {
    showHeatmap(renderHeatmapCanvas(result.values, ranges.abs[0], ranges.abs[1], 'YlOrRd'));
    document.getElementById('legend-label').textContent = 'Minutes to nearest open school';
    document.getElementById('legend-bar').style.background = 'linear-gradient(to right, #ffffcc, #feb24c, #fd8d3c, #fc4e2a, #bd0026)';
    document.getElementById('legend-min').textContent = ranges.abs[0] + ' min';
    document.getElementById('legend-max').textContent = ranges.abs[1] + ' min';
    currentGridResult = { values: result.values, names: result.names, view: 'abs' };
  }

  updateZonePolygons(mode, showZones);
  updateNetwork(mode, showNetwork);
  updateSchoolMarkers(closed);

  var info = document.getElementById('school-info');
  info.innerHTML = isBaseline ? 'All 11 schools open'
    : '<span class="closed">Closed (' + closed.length + '):</span> ' + closed.join(', ');
}

updateMap();
</script>
</body>
</html>
"""


def gdf_to_geojson_obj(gdf: gpd.GeoDataFrame, simplify_m: float | None = None) -> dict:
    if simplify_m:
        utm = gdf.to_crs(CRS_UTM17N)
        utm["geometry"] = utm.geometry.simplify(simplify_m, preserve_topology=True)
        gdf = utm.to_crs(CRS_WGS84)
    return json.loads(gdf.to_crs(CRS_WGS84).to_json())


def build_html(data: dict, masked_grids_json: str, mask_stats: str) -> str:
    """Substitute all the data variables into the template."""
    district = gpd.read_file(DISTRICT_CACHE)
    district_geojson = gdf_to_geojson_obj(district, simplify_m=20)

    replacements = {
        "__MODE_LABELS__": data["MODE_LABELS"],
        "__MODE_RANGES__": data["MODE_RANGES"],
        "__SCHOOLS__": data["SCHOOLS"],
        "__SCHOOL_NAMES__": data["SCHOOL_NAMES"],
        "__GRID_META__": data["GRID_META"],
        "__PER_SCHOOL_GRIDS_B64__": masked_grids_json,
        "__ZONE_POLYGONS__": data["ZONE_POLYGONS"],
        "__NETWORK_GEOJSON__": data["NETWORK_GEOJSON"],
        "__DISTRICT__": json.dumps(district_geojson, separators=(",", ":")),
        "__CMAP_YLORD_B64__": data["CMAP_YLORD_B64"].strip('"'),
        "__CMAP_ORANGES_B64__": data["CMAP_ORANGES_B64"].strip('"'),
        "__MASK_STATS__": mask_stats,
    }
    html = HTML_TEMPLATE
    for k, v in replacements.items():
        html = html.replace(k, v)
    return html


def main():
    if not SRC_HTML.exists():
        raise FileNotFoundError(f"Source HTML not found: {SRC_HTML}")
    data = extract_embedded_data(SRC_HTML)
    grid_meta = json.loads(data["GRID_META"])
    mask = compute_population_mask(grid_meta)
    masked_grids_json, before, after = apply_mask_to_grids(
        data["PER_SCHOOL_GRIDS_B64"], mask, grid_meta,
    )
    n_total = grid_meta["nRows"] * grid_meta["nCols"]
    n_pop = int(mask.sum())
    mask_stats = (
        f"Retained {after:,} of {before:,} in-district grid pixels "
        f"({100.0 * after / max(before, 1):.1f}%). "
        f"Populated pixels: {n_pop:,}/{n_total:,} "
        f"({100.0 * n_pop / n_total:.1f}% of full grid)."
    )
    html = build_html(data, masked_grids_json, mask_stats)
    print(f"[4/4] Writing {OUT_HTML}")
    OUT_HTML.write_text(html, encoding="utf-8")
    size_mb = OUT_HTML.stat().st_size / (1024 * 1024)
    print(f"  wrote {size_mb:.1f} MB")


if __name__ == "__main__":
    main()
