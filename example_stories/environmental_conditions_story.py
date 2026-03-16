"""Generate an editorial scrollytelling page on environmental conditions.

This module creates an interactive scrollytelling HTML page presenting
environmental and geographic data (flood risk, traffic air pollution,
urban heat) for all 11 CHCCS elementary schools, making the case that
environmental factors should inform closure decisions.

First in an Ephesus-focused editorial series; precedes the demographics
story (chccs_demographics.html).

Siloed in example_stories/ to keep editorial content separate from neutral
methodology pages in src/.

Architecture mirrors chccs_demographics_story.py: two-column layout (45%
narrative / 55% Leaflet map) with Scrollama-driven step transitions.

Usage:
    python example_stories/environmental_conditions_story.py
    python example_stories/environmental_conditions_story.py --cache-only

Output:
    example_stories/environmental_conditions.html
"""

from __future__ import annotations

import argparse
import base64
import io
import json
import sys
import warnings
from pathlib import Path

import geopandas as gpd
import matplotlib.colors as mcolors
import matplotlib.pyplot as plt
import numpy as np
import pandas as pd
from PIL import Image
from shapely.geometry import Point, mapping
from shapely.ops import unary_union

# ---------------------------------------------------------------------------
# Path setup — import from src/
# ---------------------------------------------------------------------------
PROJECT_ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(PROJECT_ROOT / "src"))

warnings.filterwarnings("ignore", category=FutureWarning)

# ---------------------------------------------------------------------------
# Path constants
# ---------------------------------------------------------------------------
DATA_RAW = PROJECT_ROOT / "data" / "raw"
DATA_PROCESSED = PROJECT_ROOT / "data" / "processed"
DATA_CACHE = PROJECT_ROOT / "data" / "cache"
OUTPUT_DIR = Path(__file__).resolve().parent
OUTPUT_HTML = OUTPUT_DIR / "environmental_conditions.html"

SCHOOL_CSV = DATA_CACHE / "nces_school_locations.csv"
DISTRICT_CACHE = DATA_CACHE / "chccs_district_boundary.gpkg"
FLOOD_CACHE = DATA_CACHE / "fema_flood_zones.gpkg"
PARCEL_POLYS = DATA_RAW / "properties" / "combined_data_polys.gpkg"
TRAP_SCORES_CSV = DATA_PROCESSED / "road_pollution_scores.csv"
UHI_SCORES_CSV = DATA_PROCESSED / "uhi_proxy_scores.csv"
TRAP_GRIDS_CACHE = DATA_CACHE / "trap_grids.npz"
UHI_GRID_CACHE = DATA_CACHE / "uhi_grid.npz"

# ---------------------------------------------------------------------------
# Domain constants
# ---------------------------------------------------------------------------
CRS_WGS84 = "EPSG:4326"
CRS_UTM17N = "EPSG:32617"
CHAPEL_HILL_CENTER = [35.9132, -79.0558]

# Color scheme
SCHOOL_COLORS = {
    "Ephesus Elementary": "#C62828",
    "Glenwood Elementary": "#2E7D32",
    "Seawell Elementary": "#1565C0",
}
DEFAULT_COLOR = "#888888"

# Flood zone colors
FLOOD_100YR = "#6baed6"
FLOOD_500YR = "#bdd7e7"
SCHOOL_FILL = "#d4edda"
SCHOOL_EDGE = "#155724"
OVERLAP_COLOR = "#e6031b"


# ---------------------------------------------------------------------------
# Utility functions
# ---------------------------------------------------------------------------
def _progress(msg: str):
    print(f"  ... {msg}")


def _round_coords(geom_dict: dict, precision: int = 4) -> dict:
    """Round coordinates in a GeoJSON geometry dict to reduce file size."""
    def _round(coords):
        if isinstance(coords[0], (list, tuple)):
            return [_round(c) for c in coords]
        return [round(c, precision) for c in coords]

    result = dict(geom_dict)
    if "coordinates" in result:
        result["coordinates"] = _round(result["coordinates"])
    return result


def gdf_to_geojson_str(
    gdf: gpd.GeoDataFrame,
    properties: list = None,
    simplify_m: float = None,
) -> str:
    """Convert GeoDataFrame to compact GeoJSON string."""
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
        props = {}
        if properties:
            for p in properties:
                val = row.get(p)
                if pd.notna(val):
                    props[p] = (
                        float(val)
                        if isinstance(val, (np.integer, np.floating))
                        else val
                    )
        features.append({
            "type": "Feature",
            "geometry": _round_coords(mapping(row.geometry)),
            "properties": props,
        })
    fc = {"type": "FeatureCollection", "features": features}
    return json.dumps(fc, separators=(",", ":"))


def grid_to_base64_png(grid: np.ndarray, colormap: str = "YlOrRd",
                       vmin: float = None, vmax: float = None) -> str:
    """Convert 2D numpy grid to base64 PNG data URL."""
    nonzero = grid[grid > 0]
    if vmin is None:
        vmin = np.percentile(nonzero, 5) if len(nonzero) > 0 else 0
    if vmax is None:
        vmax = np.percentile(nonzero, 95) if len(nonzero) > 0 else 1

    norm = mcolors.Normalize(vmin=vmin, vmax=vmax, clip=True)
    cmap = plt.get_cmap(colormap)
    normalized = norm(grid)
    mapped = cmap(normalized)  # (ny, nx, 4) float

    ny, nx = grid.shape
    rgba = np.zeros((ny, nx, 4), dtype=np.uint8)
    rgba[..., :3] = (mapped[..., :3] * 255).astype(np.uint8)
    # Alpha: transparent for near-zero, else scaled
    active = grid > 0.001
    alpha_vals = np.where(
        active,
        np.clip(120 + 80 * normalized, 0, 255).astype(np.uint8),
        0,
    )
    rgba[..., 3] = alpha_vals

    img = Image.fromarray(rgba, "RGBA")
    buf = io.BytesIO()
    img.save(buf, format="PNG")
    buf.seek(0)
    b64 = base64.b64encode(buf.read()).decode()
    return f"data:image/png;base64,{b64}"


# ---------------------------------------------------------------------------
# Data loading functions
# ---------------------------------------------------------------------------
def load_schools() -> pd.DataFrame:
    if not SCHOOL_CSV.exists():
        raise FileNotFoundError(
            f"School locations not found: {SCHOOL_CSV}\n"
            "Run: python src/road_pollution.py  (to download NCES data)"
        )
    return pd.read_csv(SCHOOL_CSV)


def load_district_boundary() -> gpd.GeoDataFrame:
    if not DISTRICT_CACHE.exists():
        raise FileNotFoundError(
            f"District boundary not found: {DISTRICT_CACHE}\n"
            "Run: python src/school_desert.py  (to download boundary)"
        )
    return gpd.read_file(DISTRICT_CACHE)


def load_flood_zones() -> gpd.GeoDataFrame:
    if not FLOOD_CACHE.exists():
        raise FileNotFoundError(
            f"Flood zone cache not found: {FLOOD_CACHE}\n"
            "Run: python src/flood_map.py  (to download FEMA data)"
        )
    return gpd.read_file(FLOOD_CACHE)


def classify_flood_zones(
    flood: gpd.GeoDataFrame,
) -> tuple[gpd.GeoDataFrame, gpd.GeoDataFrame]:
    """Split into 100-year and 500-year flood zones."""
    flood_100 = flood[flood["FLD_ZONE"].isin(["A", "AE", "AO", "AH"])].copy()
    flood_500 = flood[
        flood["ZONE_SUBTY"].fillna("").str.contains("0.2 PCT", case=False)
    ].copy()
    _progress(
        f"Classified {len(flood_100)} 100-yr, {len(flood_500)} 500-yr features"
    )
    return flood_100, flood_500


def load_school_properties(schools: pd.DataFrame) -> gpd.GeoDataFrame:
    """For each school, find the parcel containing the NCES point."""
    if not PARCEL_POLYS.exists():
        raise FileNotFoundError(
            f"Parcel data not found: {PARCEL_POLYS}\n"
            "Run: python src/property_data.py"
        )
    parcels = gpd.read_file(PARCEL_POLYS)
    rows = []
    for _, sch in schools.iterrows():
        pt = Point(sch["lon"], sch["lat"])
        containing = parcels[parcels.geometry.contains(pt)]
        if len(containing) == 0:
            dists = parcels.geometry.centroid.distance(pt)
            containing = parcels.loc[[dists.idxmin()]]
        row = containing.iloc[0].copy()
        row["school_name"] = sch["school"]
        row["school_lat"] = sch["lat"]
        row["school_lon"] = sch["lon"]
        rows.append(row)
    gdf = gpd.GeoDataFrame(rows, crs=CRS_WGS84)
    _progress(f"Matched {len(gdf)} school property parcels")
    return gdf


def compute_overlaps(
    school_props: gpd.GeoDataFrame,
    flood_100: gpd.GeoDataFrame,
    flood_500: gpd.GeoDataFrame,
) -> gpd.GeoDataFrame:
    """Compute school-property x flood-zone intersection polygons."""
    overlaps = []

    if len(flood_100):
        flood_100 = flood_100.copy()
        flood_100["geometry"] = flood_100.geometry.make_valid()
    if len(flood_500):
        flood_500 = flood_500.copy()
        flood_500["geometry"] = flood_500.geometry.make_valid()

    school_props_utm = school_props.to_crs(CRS_UTM17N)
    flood_100_utm = flood_100.to_crs(CRS_UTM17N) if len(flood_100) else flood_100
    flood_500_utm = flood_500.to_crs(CRS_UTM17N) if len(flood_500) else flood_500

    union_100 = unary_union(flood_100_utm.geometry) if len(flood_100_utm) else None
    union_500 = unary_union(flood_500_utm.geometry) if len(flood_500_utm) else None

    for _, row in school_props_utm.iterrows():
        geom = row.geometry
        for label, union_geom in [("100-year", union_100), ("500-year", union_500)]:
            if union_geom is None:
                continue
            ix = geom.intersection(union_geom)
            if ix.is_empty:
                continue
            acres = ix.area / 4046.86
            calc_acres = row.get("CALC_ACRES", 0) or 0
            pct = acres / calc_acres * 100 if calc_acres else 0
            ix_wgs = (
                gpd.GeoSeries([ix], crs=CRS_UTM17N)
                .to_crs(CRS_WGS84)
                .iloc[0]
            )
            overlaps.append({
                "geometry": ix_wgs,
                "school_name": row["school_name"],
                "flood_type": label,
                "overlap_acres": round(acres, 2),
                "overlap_pct": round(pct, 1),
            })
            _progress(
                f"  {row['school_name']}: {label} overlap = "
                f"{acres:.2f} ac ({pct:.0f}%)"
            )

    if not overlaps:
        return gpd.GeoDataFrame(
            columns=["geometry", "school_name", "flood_type",
                     "overlap_acres", "overlap_pct"]
        )
    return gpd.GeoDataFrame(overlaps, crs=CRS_WGS84)


def load_trap_scores() -> pd.DataFrame:
    if not TRAP_SCORES_CSV.exists():
        raise FileNotFoundError(
            f"TRAP scores not found: {TRAP_SCORES_CSV}\n"
            "Run: python src/road_pollution.py"
        )
    return pd.read_csv(TRAP_SCORES_CSV)


def load_uhi_scores() -> pd.DataFrame:
    if not UHI_SCORES_CSV.exists():
        raise FileNotFoundError(
            f"UHI scores not found: {UHI_SCORES_CSV}\n"
            "Run: python src/environmental_map.py"
        )
    return pd.read_csv(UHI_SCORES_CSV)


def load_trap_grids() -> dict:
    """Load TRAP grid arrays. Returns dict with keys and bounds."""
    if not TRAP_GRIDS_CACHE.exists():
        raise FileNotFoundError(
            f"TRAP grids not found: {TRAP_GRIDS_CACHE}\n"
            "Run: python src/environmental_map.py"
        )
    data = np.load(TRAP_GRIDS_CACHE)
    return {
        "raw": data["raw_grid"],
        "net": data["net_grid"],
        "bounds": tuple(data["bounds"]),  # (west, south, east, north)
    }


def load_uhi_grid() -> dict:
    """Load UHI grid array. Returns dict with grid and bounds."""
    if not UHI_GRID_CACHE.exists():
        raise FileNotFoundError(
            f"UHI grid not found: {UHI_GRID_CACHE}\n"
            "Run: python src/environmental_map.py"
        )
    data = np.load(UHI_GRID_CACHE)
    return {
        "grid": data["uhi_grid"],
        "bounds": tuple(data["bounds"]),
    }


# ---------------------------------------------------------------------------
# HTML builder
# ---------------------------------------------------------------------------
def build_html(data: dict) -> str:
    """Build the 14-step environmental conditions scrollytelling HTML."""

    trap_data_js = data["trap_chart_data"]
    uhi_data_js = data["uhi_chart_data"]

    html = f"""<!DOCTYPE html>
<html lang="en">
<head>
<meta charset="utf-8">
<meta name="viewport" content="width=device-width, initial-scale=1">
<title>Environmental Conditions &mdash; CHCCS District Analysis</title>
<link rel="stylesheet" href="https://unpkg.com/leaflet@1.9.3/dist/leaflet.css" />
<style>
* {{ margin: 0; padding: 0; box-sizing: border-box; }}
body {{ font-family: 'Segoe UI', Tahoma, Geneva, Verdana, sans-serif;
       background: #f5f5f5; overflow-x: hidden; }}

.scroll-container {{
  width: 45%;
  padding: 0 30px;
  position: relative;
  z-index: 10;
}}

#map-container {{
  position: fixed;
  top: 0; right: 0;
  width: 55%;
  height: 100vh;
  z-index: 5;
}}

#map {{ width: 100%; height: 100%; }}

#map-dim {{
  position: absolute;
  top: 0; left: 0;
  width: 100%; height: 100%;
  background: rgba(255,255,255,0.4);
  z-index: 1000;
  pointer-events: none;
  display: none;
}}

.step {{
  min-height: 80vh;
  padding: 30px;
  margin: 20px 0;
  background: white;
  border-radius: 8px;
  box-shadow: 0 2px 8px rgba(0,0,0,0.1);
  border-left: 4px solid #666;
  opacity: 0.3;
  transition: opacity 0.4s ease, border-color 0.3s ease;
}}

.step:first-child {{ margin-top: 40vh; }}
.step:last-child {{ margin-bottom: 60vh; }}
.step.is-active {{ opacity: 1; border-color: #333; }}

.step-number {{
  display: inline-block;
  width: 28px; height: 28px;
  background: #555;
  color: white;
  border-radius: 50%;
  text-align: center;
  line-height: 28px;
  font-weight: bold;
  font-size: 14px;
  margin-bottom: 10px;
}}

h2 {{ color: #333; margin: 10px 0 15px; font-size: 1.3em; }}
h3 {{ color: #333; margin: 15px 0 8px; font-size: 1.1em; }}
p {{ line-height: 1.6; margin: 10px 0; color: #333; }}

.source {{
  background: #e3f2fd;
  padding: 12px 15px;
  border-radius: 6px;
  margin: 12px 0;
  font-size: 0.9em;
}}

.limitation {{
  background: #fff8e1;
  padding: 12px 15px;
  border-radius: 6px;
  margin: 12px 0;
  border-left: 3px solid #ffc107;
  font-size: 0.9em;
}}

.insight {{
  background: #e8f5e9;
  padding: 12px 15px;
  border-radius: 6px;
  margin: 12px 0;
  border-left: 3px solid #4caf50;
  font-size: 0.9em;
}}

.metric-box {{
  display: flex;
  gap: 12px;
  flex-wrap: wrap;
  margin: 12px 0;
}}

.metric {{
  flex: 1;
  min-width: 120px;
  padding: 12px;
  background: #f5f5f5;
  border-radius: 6px;
  text-align: center;
}}

.metric-value {{
  font-size: 1.4em;
  font-weight: bold;
  color: #333;
}}

.metric-label {{
  font-size: 0.8em;
  color: #666;
  margin-top: 4px;
}}

details {{
  margin: 10px 0;
  padding: 8px 12px;
  background: #fafafa;
  border-radius: 4px;
  border: 1px solid #e0e0e0;
}}

summary {{
  cursor: pointer;
  font-weight: bold;
  color: #555;
  padding: 4px 0;
}}

details[open] summary {{ margin-bottom: 8px; }}

a {{ color: #1565C0; }}

.ephesus-label {{ color: #C62828; font-weight: bold; }}
.glenwood-label {{ color: #2E7D32; font-weight: bold; }}
.seawell-label {{ color: #1565C0; font-weight: bold; }}
.fpg-label {{ color: #FF8F00; font-weight: bold; }}

@media (max-width: 900px) {{
  .scroll-container {{ width: 100%; }}
  #map-container {{
    position: relative;
    width: 100%;
    height: 40vh;
  }}
}}
</style>
</head>
<body>
<div class="scroll-container">

  <!-- Step 0: District Overview -->
  <div class="step" data-step="0">
    <div class="step-number">1</div>
    <h2>Geography and Environmental Conditions Across CHCCS Schools</h2>
    <p>CHCCS faces the difficult decision of closing schools. The Board has
    identified criteria including facilities condition, enrollment trends,
    community impact, and <strong>geographic conditions</strong>. But there
    are other environmental factors that should also inform this decision:
    conditions that affect student health and long-term infrastructure
    viability.</p>
    <p>Only three schools are being considered for closure, which creates an
    uncomfortable comparison. Though this analysis is Ephesus-focused, we
    want to clearly advocate for all school communities to be preserved as
    much as possible.
    <span class="ephesus-label">Ephesus</span>,
    <span class="glenwood-label">Glenwood</span>, and
    <span class="seawell-label">Seawell</span> are all amazing schools.</p>
    <p>Data can provide insight into whether the <strong>lands</strong>
    themselves should be reviewed for closure consideration &mdash;
    independent of the programs and communities they currently serve.</p>
  </div>

  <!-- Step 1: Environmental Overview -->
  <div class="step" data-step="1">
    <div class="step-number">2</div>
    <h2>What We Analyze</h2>
    <p>We examine three environmental factors across all 11 elementary schools:</p>
    <ul style="margin:8px 0 8px 20px;line-height:1.8;">
      <li><strong>Flood risk</strong> &mdash; FEMA flood zone overlap with school properties</li>
      <li><strong>Traffic air pollution (TRAP)</strong> &mdash; modeled exposure from nearby roads</li>
      <li><strong>Urban heat island (UHI)</strong> &mdash; heat vulnerability from surrounding land cover</li>
    </ul>
    <p>These factors affect student health, infrastructure costs, and long-term
    viability of school sites.</p>
    <div class="source">
      <strong>Full interactive map:</strong>
      <a href="https://atashie.github.io/CHCCS_geospatial/assets/maps/chccs_environmental_analysis.html">CHCCS Environmental Analysis</a>
      &bull;
      <strong>Methodology:</strong>
      <a href="https://atashie.github.io/CHCCS_geospatial/assets/maps/environmental_methodology.html">Environmental Methodology</a>
    </div>
  </div>

  <!-- ========== FLOOD ZONES (Steps 2-4) ========== -->

  <!-- Step 2: What Are Flood Zones? -->
  <div class="step" data-step="2">
    <div class="step-number">3</div>
    <h2>What Are Flood Zones?</h2>
    <p>FEMA classifies areas by flood risk. A <strong>100-year flood zone</strong>
    has a 1% annual chance of flooding; a <strong>500-year flood zone</strong>
    has a 0.2% annual chance.</p>
    <p>These sound rare &mdash; but they are not. A property in a 100-year
    flood zone has a <strong>26% chance of flooding over a 30-year mortgage</strong>.
    A 500-year zone still carries meaningful cumulative risk.</p>
    <p>The map shows FEMA flood zones across the CHCCS district.</p>
    <div class="source">
      <strong>Data:</strong> FEMA National Flood Hazard Layer (NFHL)
    </div>
  </div>

  <!-- Step 3: Chapel Hill's Flood History -->
  <div class="step" data-step="3">
    <div class="step-number">4</div>
    <h2>Chapel Hill&rsquo;s Flood History</h2>
    <p>Hurricane Chantal (2025) produced a <strong>1,000-year rainfall event</strong>
    with extensive flooding throughout Chapel Hill, causing an estimated
    <strong>$54&nbsp;million in total damage</strong> to homes, businesses, and
    public property &mdash; including $20.7&nbsp;million in commercial damage and
    $22.6&nbsp;million in public damage. <strong>Most significant damage
    occurred in areas of known flood risk.</strong></p>
    <p>With earlier severe events (Hurricane Fran 1996, Hurricane Floyd 1999),
    that is at least three severe flood events in roughly 30 years &mdash;
    approximately <strong>one per decade</strong>. Climate change projections
    suggest this trend will intensify.</p>
    <p>A &ldquo;500-year flood&rdquo; is not a once-in-a-lifetime event in
    Chapel Hill.</p>
  </div>

  <!-- Step 4: Schools at Risk (merged) -->
  <div class="step" data-step="4">
    <div class="step-number">5</div>
    <h2>Schools at Risk: FPG &amp; Rashkis</h2>
    <p>Only <strong>2 of 11</strong> schools have FEMA flood zone overlap on
    their property. Percentages below represent the portion of each
    school&rsquo;s property that falls within the referenced flood plain:</p>
    <div class="metric-box">
      <div class="metric" style="border:2px solid #FF8F00;">
        <div class="metric-value" style="color:#FF8F00;">26.4%</div>
        <div class="metric-label">of FPG property in<br>100-year flood zone<br>(2.59 acres)</div>
      </div>
      <div class="metric">
        <div class="metric-value">7.1%</div>
        <div class="metric-label">of Rashkis property in<br>100-year flood zone<br>(1.22 acres)</div>
      </div>
      <div class="metric">
        <div class="metric-value">4.8%</div>
        <div class="metric-label">of Rashkis property in<br>500-year flood zone<br>(0.81 acres)</div>
      </div>
    </div>
    <p>Neither is being considered for closure, but both represent assets at
    ongoing flood risk.</p>
    <p><span class="fpg-label">FPG</span> is planned to rebuild on a new site.
    Once relocated, the current property &mdash; with over a quarter of its
    acreage in a 100-year flood zone &mdash; becomes a liability.
    If FPG is to be relocated, is there any good rationale for maintaining
    any school property that is at documented risk of flooding?</p>
    <p>All three schools under consideration for closure &mdash;
    <span class="ephesus-label">Ephesus</span>,
    <span class="glenwood-label">Glenwood</span>, and
    <span class="seawell-label">Seawell</span> &mdash; have
    <strong>zero flood zone overlap</strong>.</p>
  </div>

  <!-- ========== TRAFFIC AIR POLLUTION (Steps 5-8) ========== -->

  <!-- Step 5: TRAP & Children's Health -->
  <div class="step" data-step="5">
    <div class="step-number">6</div>
    <h2>Traffic Air Pollution &amp; Children&rsquo;s Health</h2>
    <p>Traffic-related air pollution (TRAP) &mdash; nitrogen dioxide, particulate
    matter, ultrafine particles &mdash; is directly linked to adverse health
    outcomes in children including asthma, reduced lung function, and impaired
    cognitive development.</p>
    <p>Studies show children at schools near major roads have higher rates of
    respiratory illness and lower academic performance.</p>
    <div class="source">
      <strong>References:</strong> EPA Integrated Science Assessment for NO&#8322;
      (2016) &bull; HEI Traffic-Related Air Pollution (2010) &bull; Sunyer et al.,
      <em>PLOS Medicine</em> (2015) &bull; California Air Resources Board school
      proximity studies
    </div>
  </div>

  <!-- Step 6: Policies -->
  <div class="step" data-step="6">
    <div class="step-number">7</div>
    <h2>Policies Protecting Children from TRAP</h2>
    <p>A growing number of jurisdictions prohibit siting schools near major
    roads:</p>
    <ul style="text-align:left; margin:0.5em auto; max-width:90%; font-size:0.95em;">
      <li><strong>California (SB&nbsp;352, 2025):</strong> Prohibits school site
      approval within 500&nbsp;feet of a freeway or busy traffic corridor unless the
      district proves air-quality mitigation will protect students.</li>
      <li><strong>New York (The SIGH Act, 2024):</strong> Prevents new schools
      within 500&nbsp;feet of a major highway.</li>
      <li><strong>Federal (EPA guidance):</strong> Recommends 500&ndash;1,000 foot
      buffers from major highways and distribution centers.</li>
    </ul>
    <p>The trend is toward <strong>more restrictive</strong> siting rules.
    Reopening or expanding schools on sites with high traffic exposure may
    face increasing regulatory and liability scrutiny in the years ahead.</p>
    <p>Chapel Hill has no such policy, but the data is clear: <strong>proximity
    to traffic matters for children&rsquo;s health and academic outcomes</strong>.</p>
  </div>

  <!-- Step 7: Net TRAP Exposure (consolidated) -->
  <div class="step" data-step="7">
    <div class="step-number">8</div>
    <h2>Net Traffic Air Pollution Exposure</h2>
    <p>Traffic pollution drops roughly 50% within 230&nbsp;meters of a road,
    and tree canopy can reduce what remains by up to 56%. We modeled both
    effects across the district using OpenStreetMap road data, NCDOT traffic
    counts, and ESA WorldCover land classification.</p>
    <p>The map shows <strong>net pollution</strong> &mdash; the exposure that
    remains after tree canopy mitigation. Hotter colors indicate higher
    cumulative exposure.</p>
    <div class="source">
      <strong>Full methodology:</strong>
      <a href="https://atashie.github.io/CHCCS_geospatial/assets/maps/environmental_methodology.html">Environmental Methodology Walkthrough</a>
    </div>
  </div>

  <!-- Step 8: TRAP Bar Chart -->
  <div class="step" data-step="8">
    <div class="step-number">9</div>
    <h2>Traffic Pollution: All Schools Compared</h2>
    <p>Net TRAP exposure (500m radius, normalized 0&ndash;100) for all schools,
    including FPG&rsquo;s planned new location:</p>
    <p><span class="glenwood-label">Glenwood</span> has the highest net TRAP
    exposure &mdash; bordered by Estes Drive and near US 15-501.
    <span class="fpg-label">FPG</span>&rsquo;s current site ranks #2, but its
    <strong>new planned location drops to #9</strong> &mdash; a dramatic
    improvement.</p>
    <p><span class="ephesus-label">Ephesus</span> (12.8) and
    <span class="seawell-label">Seawell</span> (3.9) have relatively low
    exposure.</p>
    <div class="limitation">
      <strong>Note:</strong> FPG&rsquo;s current site is not ideal for students.
      The planned relocation would significantly reduce their pollution exposure.
    </div>
  </div>

  <!-- ========== URBAN HEAT ISLAND (Steps 9-11) ========== -->

  <!-- Step 9: What Is Urban Heat? -->
  <div class="step" data-step="9">
    <div class="step-number">10</div>
    <h2>What Is Urban Heat?</h2>
    <p>Urban heat islands form where pavement and buildings absorb and
    re-radiate heat, while tree canopy provides cooling.</p>
    <p>We estimated heat vulnerability using ESA WorldCover land classification
    &mdash; a proxy based on surrounding land cover, not direct temperature
    measurement. Higher scores indicate more built-up, less vegetated
    surroundings.</p>
    <p><strong>Why this matters for elementary schools:</strong> Extreme heat
    reduces outdoor recess and physical activity time, and research links
    elevated temperatures to decreased concentration and academic performance.
    Schools in high-UHI areas also face higher cooling costs and greater risk
    of heat-related illness during arrival, dismissal, and outdoor
    activities.</p>
  </div>

  <!-- Step 10: UHI Heatmap -->
  <div class="step" data-step="10">
    <div class="step-number">11</div>
    <h2>Urban Heat Vulnerability Map</h2>
    <p>The UHI proxy reveals a clear pattern: downtown and commercial corridors
    are hotter; forested areas along stream buffers are cooler.</p>
    <p>Blue indicates cooler (more vegetation), red indicates hotter (more
    impervious surface).</p>
  </div>

  <!-- Step 11: UHI Bar Chart -->
  <div class="step" data-step="11">
    <div class="step-number">12</div>
    <h2>Urban Heat: All Schools Compared</h2>
    <p>UHI proxy scores (500m radius) for all schools:</p>
    <p><span class="glenwood-label">Glenwood</span> again ranks near the top
    (#2) for heat vulnerability. <span class="fpg-label">FPG</span>&rsquo;s
    current site is #3.</p>
    <p>Among the closure candidates, <span class="ephesus-label">Ephesus</span>
    (14.7) is moderate, while <span class="seawell-label">Seawell</span>
    (11.5) benefits from more surrounding vegetation.</p>
  </div>

  <!-- ========== SUMMARY (Steps 12-13) ========== -->

  <!-- Step 12: Environmental Summary -->
  <div class="step" data-step="12">
    <div class="step-number">13</div>
    <h2>Geographic and Environmental Factors Summary</h2>
    <p>Geographic and environmental conditions are interconnected with built
    and natural environment factors that have significant impact on health
    outcomes, academic performance, and financial savings.</p>
    <h3>Flood Risk</h3>
    <p>Only FPG and Rashkis have flood zone overlap. All 3 closure candidates
    have <strong>zero flood exposure</strong>. FPG&rsquo;s current site is a
    flood liability that should not be maintained post-relocation.</p>
    <h3>Traffic Pollution</h3>
    <p><span class="glenwood-label">Glenwood</span> and
    <span class="fpg-label">FPG (current)</span> are far above all other schools.
    FPG&rsquo;s new site would dramatically improve.
    <span class="ephesus-label">Ephesus</span> and
    <span class="seawell-label">Seawell</span> have low exposure.</p>
    <h3>Urban Heat</h3>
    <p><span class="glenwood-label">Glenwood</span> and
    <span class="fpg-label">FPG</span> again rank highest.
    <span class="ephesus-label">Ephesus</span> is moderate;
    <span class="seawell-label">Seawell</span> is low.</p>
    <div class="insight">
      <strong>Bottom line:</strong> Programs can move, buildings can be rebuilt,
      but the lands are here to stay. We need to consider selling property that
      are not only toxic to our children but also pose as ongoing financial
      toxic investments.
    </div>
  </div>

  <!-- Step 13: Transition -->
  <div class="step" data-step="13">
    <div class="step-number">14</div>
    <h2>What&rsquo;s Next</h2>
    <p>Environmental and geographic conditions are just one lens. Next, we
    examine <strong>Chapel Hill&rsquo;s demographics</strong> &mdash; the
    people, communities, and socioeconomic patterns that define each
    school&rsquo;s role in the district.</p>
    <p style="margin-top:20px;">
      <a href="https://atashie.github.io/CHCCS_geospatial/example_stories/chccs_demographics.html" style="font-size:1.1em;font-weight:bold;">
        Continue to Demographics Analysis &rarr;
      </a>
    </p>
    <div class="source">
      <strong>Data sources:</strong> FEMA NFHL &bull; OpenStreetMap road network
      &bull; NCDOT AADT traffic counts &bull; ESA WorldCover 2021 &bull;
      NCES EDGE 2023-24 &bull; Orange County parcel data
    </div>
  </div>

</div> <!-- end scroll-container -->
<div id="map-container">
  <div id="map"></div>
  <div id="map-dim"></div>
  <div id="chart-panel" style="position:absolute;top:0;left:0;width:100%;height:100%;
    z-index:500;background:#fff;display:none;overflow-y:auto;padding:24px 20px;">
    <div id="chart-title" style="text-align:center;margin:0 0 16px;">
      <h3 style="margin:0 0 4px;font-size:1.15em;color:#333;"></h3>
      <p style="margin:0;font-size:0.82em;color:#777;"></p>
    </div>
    <div id="chart-bars"></div>
    <p id="chart-footer" style="text-align:center;margin:16px 0 0;font-size:0.8em;color:#999;line-height:1.4;"></p>
  </div>
</div>

<script src="https://unpkg.com/leaflet@1.9.3/dist/leaflet.js"></script>
<script src="https://unpkg.com/scrollama@3.2.0/build/scrollama.min.js"></script>
<script>
// === Embedded data ===
var SCHOOLS = {data["schools_json"]};
var DISTRICT = {data["district_json"]};
var FLOOD_100 = {data["flood_100_json"]};
var FLOOD_500 = {data["flood_500_json"]};
var SCHOOL_PROPS = {data["school_props_json"]};
var FLOOD_OVERLAPS = {data["flood_overlaps_json"]};
var TRAP_RAW_URL = "{data["trap_raw_png"]}";
var TRAP_NET_URL = "{data["trap_net_png"]}";
var TRAP_BOUNDS = {json.dumps(data["trap_bounds"])};
var UHI_URL = "{data["uhi_png"]}";
var UHI_BOUNDS = {json.dumps(data["uhi_bounds"])};
var TRAP_DATA = {trap_data_js};
var UHI_DATA = {uhi_data_js};

var SCHOOL_COLORS = {{
  "Ephesus Elementary": "#C62828",
  "Glenwood Elementary": "#2E7D32",
  "Seawell Elementary": "#1565C0"
}};
var DEFAULT_COLOR = "#888888";

// === Bar chart builder ===
function renderBars(containerId, data, metric, options) {{
  options = options || {{}};
  var maxVal = 0;
  data.forEach(function(d) {{ if (d[metric] > maxVal) maxVal = d[metric]; }});
  if (maxVal === 0) maxVal = 1;
  var html = "";
  data.forEach(function(d) {{
    var val = d[metric] || 0;
    var width = (val / maxVal * 100).toFixed(1);
    var label = d.school.replace(" Elementary", "").replace(" Bilingue", "");
    var barColor = SCHOOL_COLORS[d.school] || DEFAULT_COLOR;
    var isHighlight = !!SCHOOL_COLORS[d.school];
    var fontWeight = isHighlight ? "bold" : "normal";
    var fontColor = isHighlight ? barColor : "#555";
    var borderStyle = "";
    if (d.school === "New FPG Location") {{
      borderStyle = "border:2px dashed #FF8F00;background:rgba(255,143,0,0.08);";
    }}
    var valText = val.toFixed(1);
    html += '<div style="display:flex;align-items:center;margin:4px 0;font-size:0.82em;' + borderStyle + 'padding:2px 4px;border-radius:4px;">'
      + '<div style="width:120px;text-align:right;padding-right:8px;color:' + fontColor + ';font-weight:' + fontWeight + ';white-space:nowrap;overflow:hidden;text-overflow:ellipsis;">'
      + label + '</div>'
      + '<div style="flex:1;background:#eee;border-radius:3px;height:18px;position:relative;">'
      + '<div style="width:' + width + '%;height:100%;background:' + barColor + ';border-radius:3px;opacity:0.85;"></div>'
      + '</div>'
      + '<div style="width:50px;text-align:right;padding-left:6px;color:#555;font-size:0.95em;">'
      + valText + '</div></div>';
  }});
  document.getElementById(containerId).innerHTML = html;
}}

function showChart(title, subtitle, footer, data, metric) {{
  document.getElementById("chart-panel").style.display = "block";
  var titleEl = document.querySelector("#chart-title h3");
  var subtitleEl = document.querySelector("#chart-title p");
  titleEl.textContent = title;
  subtitleEl.textContent = subtitle;
  document.getElementById("chart-footer").textContent = footer;
  renderBars("chart-bars", data, metric, {{}});
}}

function showTrapChart() {{
  showChart(
    "Net TRAP Exposure by School",
    "500m radius, normalized 0\u2013100 (after tree canopy mitigation)",
    "Higher values = greater traffic pollution exposure. FPG new site shown with dashed border.",
    TRAP_DATA, "value"
  );
}}

function showUhiChart() {{
  showChart(
    "Urban Heat Vulnerability by School",
    "500m radius UHI proxy score (ESA WorldCover land cover)",
    "Higher values = more built-up surroundings, less vegetation.",
    UHI_DATA, "value"
  );
}}

// === Map setup ===
var map = L.map("map", {{
  center: [{CHAPEL_HILL_CENTER[0]}, {CHAPEL_HILL_CENTER[1]}],
  zoom: 12,
  scrollWheelZoom: false,
  zoomControl: true,
}});

L.tileLayer("https://{{s}}.basemaps.cartocdn.com/light_all/{{z}}/{{x}}/{{y}}@2x.png", {{
  attribution: '&copy; <a href="https://carto.com/">CARTO</a> &copy; <a href="https://www.openstreetmap.org/copyright">OSM</a>',
  maxZoom: 19,
}}).addTo(map);

var dimOverlay = document.getElementById("map-dim");
var districtBounds = L.geoJSON(DISTRICT).getBounds();

function districtView() {{
  map.fitBounds(districtBounds.pad(0.05));
}}

// === Layer factories ===
var layers = {{}};

// District boundary
layers.district = L.geoJSON(DISTRICT, {{
  style: {{ color: "#333", weight: 2, dashArray: "6 4", fillOpacity: 0 }}
}});

// Schools (colored by role)
layers.schools = L.geoJSON(SCHOOLS, {{
  pointToLayer: function(f, ll) {{
    var name = f.properties.school || "";
    var color = SCHOOL_COLORS[name] || "#888";
    var radius = SCHOOL_COLORS[name] ? 8 : 5;
    return L.circleMarker(ll, {{
      radius: radius,
      fillColor: color,
      color: "#fff",
      weight: 2,
      fillOpacity: 0.9,
    }});
  }},
  onEachFeature: function(f, layer) {{
    layer.bindTooltip(f.properties.school, {{permanent: false}});
  }}
}});

// Schools with permanent labels
layers.schoolsLabeled = L.geoJSON(SCHOOLS, {{
  pointToLayer: function(f, ll) {{
    var name = f.properties.school || "";
    var color = SCHOOL_COLORS[name] || "#888";
    var radius = SCHOOL_COLORS[name] ? 7 : 4;
    return L.circleMarker(ll, {{
      radius: radius,
      fillColor: color,
      color: "#fff",
      weight: 2,
      fillOpacity: 0.9,
    }});
  }},
  onEachFeature: function(f, layer) {{
    var label = f.properties.school.replace(" Elementary", "").replace(" Bilingue", "");
    layer.bindTooltip(label, {{
      permanent: true,
      direction: "right",
      offset: [10, 0],
      className: "school-label-tip"
    }});
  }}
}});

// Flood zones
layers.flood100 = L.geoJSON(FLOOD_100, {{
  style: {{ fillColor: "#6baed6", fillOpacity: 0.4, color: "#6baed6", weight: 0.5 }}
}});
layers.flood500 = L.geoJSON(FLOOD_500, {{
  style: {{ fillColor: "#bdd7e7", fillOpacity: 0.3, color: "#bdd7e7", weight: 0.5 }}
}});

// School properties
layers.schoolProps = L.geoJSON(SCHOOL_PROPS, {{
  style: {{ fillColor: "#d4edda", fillOpacity: 0.6, color: "#155724", weight: 1.5 }},
  onEachFeature: function(f, layer) {{
    var name = (f.properties.school_name || "").replace(" Elementary", "").replace(" Bilingue", "");
    layer.bindTooltip(name);
  }}
}});

// Flood overlaps
layers.floodOverlaps = L.geoJSON(FLOOD_OVERLAPS, {{
  style: {{ fillColor: "#e6031b", fillOpacity: 0.6, color: "#e6031b", weight: 1 }},
  onEachFeature: function(f, layer) {{
    var p = f.properties;
    layer.bindTooltip(
      (p.school_name || "") + "<br>" +
      (p.flood_type || "") + ": " + (p.overlap_acres || 0) + " ac (" + (p.overlap_pct || 0) + "%)"
    );
  }}
}});

// TRAP image overlays
var trapBoundsLL = L.latLngBounds(
  [TRAP_BOUNDS[1], TRAP_BOUNDS[0]],
  [TRAP_BOUNDS[3], TRAP_BOUNDS[2]]
);
layers.trapRaw = L.imageOverlay(TRAP_RAW_URL, trapBoundsLL, {{ opacity: 0.7 }});
layers.trapNet = L.imageOverlay(TRAP_NET_URL, trapBoundsLL, {{ opacity: 0.7 }});

// UHI image overlay
var uhiBoundsLL = L.latLngBounds(
  [UHI_BOUNDS[1], UHI_BOUNDS[0]],
  [UHI_BOUNDS[3], UHI_BOUNDS[2]]
);
layers.uhi = L.imageOverlay(UHI_URL, uhiBoundsLL, {{ opacity: 0.7 }});

// === Zoom helpers ===
function zoomToFpgRashkis() {{
  // Find FPG and Rashkis bounds from school props
  var fpg = null, rash = null;
  SCHOOL_PROPS.features.forEach(function(f) {{
    var n = f.properties.school_name || "";
    if (n.indexOf("Frank Porter Graham") >= 0) fpg = f;
    if (n.indexOf("Rashkis") >= 0) rash = f;
  }});
  var group = L.geoJSON({{ type: "FeatureCollection", features: [fpg, rash].filter(Boolean) }});
  if (group.getLayers().length) {{
    map.fitBounds(group.getBounds().pad(0.3));
  }}
}}

// === Step handler ===
var currentStep = -1;

function clearAllLayers() {{
  Object.keys(layers).forEach(function(k) {{
    if (map.hasLayer(layers[k])) map.removeLayer(layers[k]);
  }});
  dimOverlay.style.display = "none";
  document.getElementById("chart-panel").style.display = "none";
}}

function handleStep(idx) {{
  if (idx === currentStep) return;
  currentStep = idx;
  clearAllLayers();

  switch(idx) {{
    case 0: // Geography intro
      layers.district.addTo(map);
      layers.schools.addTo(map);
      districtView();
      break;

    case 1: // What We Analyze
      layers.district.addTo(map);
      layers.schools.addTo(map);
      districtView();
      break;

    case 2: // What Are Flood Zones?
      layers.district.addTo(map);
      layers.flood500.addTo(map);
      layers.flood100.addTo(map);
      layers.schools.addTo(map);
      districtView();
      break;

    case 3: // Flood History
      layers.district.addTo(map);
      layers.flood500.addTo(map);
      layers.flood100.addTo(map);
      layers.schools.addTo(map);
      districtView();
      break;

    case 4: // Schools at Risk (merged)
      layers.flood500.addTo(map);
      layers.flood100.addTo(map);
      layers.schoolProps.addTo(map);
      layers.floodOverlaps.addTo(map);
      layers.schools.addTo(map);
      zoomToFpgRashkis();
      break;

    case 5: // TRAP & Health
      layers.district.addTo(map);
      layers.schools.addTo(map);
      districtView();
      break;

    case 6: // Policies
      layers.district.addTo(map);
      layers.schools.addTo(map);
      dimOverlay.style.display = "block";
      districtView();
      break;

    case 7: // Net TRAP Exposure
      layers.district.addTo(map);
      layers.trapNet.addTo(map);
      layers.schoolsLabeled.addTo(map);
      districtView();
      break;

    case 8: // TRAP Bar Chart
      showTrapChart();
      break;

    case 9: // What Is Urban Heat?
      layers.district.addTo(map);
      layers.schools.addTo(map);
      districtView();
      break;

    case 10: // UHI Map
      layers.district.addTo(map);
      layers.uhi.addTo(map);
      layers.schoolsLabeled.addTo(map);
      districtView();
      break;

    case 11: // UHI Bar Chart
      showUhiChart();
      break;

    case 12: // Env Factors Summary
      layers.district.addTo(map);
      layers.trapNet.addTo(map);
      layers.flood500.addTo(map);
      layers.flood100.addTo(map);
      layers.schools.addTo(map);
      districtView();
      break;

    case 13: // What's Next
      layers.district.addTo(map);
      layers.schools.addTo(map);
      dimOverlay.style.display = "block";
      districtView();
      break;
  }}
}}

// === Scrollama ===
var scroller = scrollama();
scroller.setup({{
  step: ".step",
  offset: 0.5,
  progress: false,
}}).onStepEnter(function(response) {{
  document.querySelectorAll(".step").forEach(function(el) {{
    el.classList.remove("is-active");
  }});
  response.element.classList.add("is-active");
  handleStep(parseInt(response.element.dataset.step));
}});
window.addEventListener("resize", scroller.resize);
setTimeout(function() {{ handleStep(0); }}, 100);
</script>
<style>
.school-label-tip {{
  background: rgba(255,255,255,0.85);
  border: none;
  box-shadow: 0 1px 3px rgba(0,0,0,0.2);
  font-size: 11px;
  font-weight: bold;
  padding: 2px 6px;
}}
</style>
</body>
</html>"""

    return html


# ---------------------------------------------------------------------------
# Main orchestrator
# ---------------------------------------------------------------------------
def main():
    parser = argparse.ArgumentParser(
        description="Generate environmental conditions editorial scrollytelling page"
    )
    parser.add_argument("--cache-only", action="store_true",
                        help="Only use cached data (default behavior)")
    parser.parse_args()

    print("=" * 60)
    print("Environmental Conditions: Editorial Story Generator")
    print("=" * 60)

    # [1/9] Load schools
    print("\n[1/9] Loading school locations ...")
    schools = load_schools()
    schools_gdf = gpd.GeoDataFrame(
        schools,
        geometry=gpd.points_from_xy(schools.lon, schools.lat),
        crs=CRS_WGS84,
    )
    schools_json = gdf_to_geojson_str(schools_gdf, properties=["school"])
    _progress(f"Loaded {len(schools)} schools")

    # [2/9] District boundary
    print("[2/9] Loading district boundary ...")
    district = load_district_boundary()
    district_json = gdf_to_geojson_str(district, simplify_m=50)

    # [3/9] Flood zones
    print("[3/9] Loading flood zones ...")
    flood = load_flood_zones()
    flood_100, flood_500 = classify_flood_zones(flood)

    # Clip flood zones to district + buffer for smaller GeoJSON
    district_buf = district.to_crs(CRS_UTM17N).buffer(500).to_crs(CRS_WGS84)
    dist_union = district_buf.union_all() if hasattr(district_buf, "union_all") else district_buf.unary_union
    if len(flood_100) > 0:
        f100 = flood_100.copy()
        f100["geometry"] = f100.geometry.make_valid()
        flood_100_clip = gpd.clip(f100, dist_union)
    else:
        flood_100_clip = flood_100
    if len(flood_500) > 0:
        f500 = flood_500.copy()
        f500["geometry"] = f500.geometry.make_valid()
        flood_500_clip = gpd.clip(f500, dist_union)
    else:
        flood_500_clip = flood_500

    flood_100_json = gdf_to_geojson_str(flood_100_clip, simplify_m=10)
    flood_500_json = gdf_to_geojson_str(flood_500_clip, simplify_m=10)
    _progress(f"Clipped flood zones: {len(flood_100_clip)} 100-yr, {len(flood_500_clip)} 500-yr")

    # [4/9] School properties + flood overlaps
    print("[4/9] Loading school properties & computing flood overlaps ...")
    school_props = load_school_properties(schools)
    overlaps = compute_overlaps(school_props, flood_100, flood_500)

    school_props_json = gdf_to_geojson_str(
        school_props,
        properties=["school_name", "CALC_ACRES"],
        simplify_m=5,
    )
    flood_overlaps_json = gdf_to_geojson_str(
        overlaps,
        properties=["school_name", "flood_type", "overlap_acres", "overlap_pct"],
        simplify_m=5,
    )
    _progress(f"Found {len(overlaps)} flood overlap polygons")

    # [5/9] TRAP scores + chart data
    print("[5/9] Loading TRAP scores ...")
    trap_scores = load_trap_scores()
    # net_norm_500m is already normalized 0-100
    trap_col = "net_norm_500m" if "net_norm_500m" in trap_scores.columns else "net_500m"
    trap_chart = trap_scores[["school", trap_col]].copy()
    trap_chart.columns = ["school", "value"]
    if trap_col == "net_500m":
        # Normalize to 0-100 if using raw values
        max_trap = trap_chart["value"].max()
        if max_trap > 0:
            trap_chart["value"] = (trap_chart["value"] / max_trap * 100).round(1)
    trap_chart = trap_chart.sort_values("value", ascending=False)
    trap_chart_records = trap_chart.to_dict("records")
    for rec in trap_chart_records:
        if isinstance(rec["value"], (np.integer, np.floating)):
            rec["value"] = float(rec["value"])
    trap_data_js = json.dumps(trap_chart_records, separators=(",", ":"))
    _progress(f"Loaded TRAP scores for {len(trap_scores)} schools")

    # [6/9] UHI scores + chart data
    print("[6/9] Loading UHI scores ...")
    uhi_scores = load_uhi_scores()
    uhi_col = "uhi_500m" if "uhi_500m" in uhi_scores.columns else uhi_scores.columns[1]
    uhi_chart = uhi_scores[["school", uhi_col]].copy()
    uhi_chart.columns = ["school", "value"]
    uhi_chart["value"] = uhi_chart["value"].round(1)
    uhi_chart = uhi_chart.sort_values("value", ascending=False)
    uhi_chart_records = uhi_chart.to_dict("records")
    for rec in uhi_chart_records:
        if isinstance(rec["value"], (np.integer, np.floating)):
            rec["value"] = float(rec["value"])
    uhi_data_js = json.dumps(uhi_chart_records, separators=(",", ":"))
    _progress(f"Loaded UHI scores for {len(uhi_scores)} schools")

    # [7/9] TRAP grids → base64 PNGs
    print("[7/9] Converting TRAP grids to PNG ...")
    trap_grids = load_trap_grids()
    trap_raw_png = grid_to_base64_png(trap_grids["raw"], colormap="YlOrRd")
    trap_net_png = grid_to_base64_png(trap_grids["net"], colormap="YlOrRd")
    trap_bounds = list(trap_grids["bounds"])
    _progress(f"TRAP grids: {trap_grids['raw'].shape}")

    # [8/9] UHI grid → base64 PNG
    print("[8/9] Converting UHI grid to PNG ...")
    uhi_grid_data = load_uhi_grid()
    uhi_png = grid_to_base64_png(uhi_grid_data["grid"], colormap="RdYlBu_r")
    uhi_bounds = list(uhi_grid_data["bounds"])
    _progress(f"UHI grid: {uhi_grid_data['grid'].shape}")

    # [9/9] Build HTML
    print("[9/9] Building HTML ...")
    data = {
        "schools_json": schools_json,
        "district_json": district_json,
        "flood_100_json": flood_100_json,
        "flood_500_json": flood_500_json,
        "school_props_json": school_props_json,
        "flood_overlaps_json": flood_overlaps_json,
        "trap_raw_png": trap_raw_png,
        "trap_net_png": trap_net_png,
        "trap_bounds": trap_bounds,
        "uhi_png": uhi_png,
        "uhi_bounds": uhi_bounds,
        "trap_chart_data": trap_data_js,
        "uhi_chart_data": uhi_data_js,
    }
    html = build_html(data)

    OUTPUT_HTML.parent.mkdir(parents=True, exist_ok=True)
    OUTPUT_HTML.write_text(html, encoding="utf-8")
    size_mb = OUTPUT_HTML.stat().st_size / (1024 * 1024)
    print(f"\nSaved -> {OUTPUT_HTML}  ({size_mb:.1f} MB)")
    print("Done!")


if __name__ == "__main__":
    main()
