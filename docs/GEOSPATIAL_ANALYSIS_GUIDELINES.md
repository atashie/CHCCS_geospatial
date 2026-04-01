# Geospatial Analysis Guidelines — CHCCS Project

Practical reference for CRS discipline, spatial operations, and map aesthetics.
All geospatial code in this repository must follow these conventions.

**Reference implementation:** `src/school_socioeconomic_analysis.py`

---

## 1. CRS / Projection Rules

| Constant | EPSG | Purpose |
|----------|------|---------|
| `CRS_WGS84` | `EPSG:4326` | Storage, display, Folium overlays |
| `CRS_UTM17N` | `EPSG:32617` | ALL spatial calculations (area, buffer, distance, overlay) |

### Core Principle

```
Load WGS84 → Reproject UTM → Compute → Reproject back WGS84
```

### Rules

- **NEVER** compute `.area` on WGS84 geometries. Degree-squared areas are meaningless.
- **NEVER** use fixed-latitude conversion factors (`111,320² × cos(lat)`) when UTM is available.
- **ALWAYS** set `always_xy=True` on `pyproj.Transformer`.
- **ALWAYS** use UTM for `gpd.overlay()`, `gpd.sjoin()`, buffer operations, and distance calculations.
- WGS84 is acceptable for grid cell centers (display) but distance queries from those cells must transform to UTM first.

---

## 2. Data Loading & Inspection Checklist

After loading any GeoDataFrame:

1. **Assert CRS** — `assert gdf.crs is not None, "Missing CRS"`
2. **Log metadata** — CRS, feature count, `total_bounds`
3. **Validate geometry types** — filter out unexpected types (e.g., points from a polygon layer)
4. **Repair external data** — call `make_valid()` on data from external APIs (FEMA, Census, etc.)
5. **Use NCES for school locations** — never hardcode coordinates

```python
# Example
gdf = gpd.read_file(path)
assert gdf.crs is not None, f"Missing CRS in {path}"
print(f"  Loaded {len(gdf)} features, CRS={gdf.crs}, bounds={gdf.total_bounds}")
gdf["geometry"] = gdf.geometry.make_valid()
```

---

## 3. Reprojection Protocol

### Step-by-step Template

```python
# 1. Ensure source CRS is known
assert src_gdf.crs is not None

# 2. Reproject to UTM for computation
src_utm = src_gdf.to_crs(CRS_UTM17N)

# 3. Perform spatial operation in UTM
result_utm = gpd.overlay(src_utm, other_utm, how="intersection")

# 4. Compute area in meters
result_utm["area_m2"] = result_utm.geometry.area

# 5. Reproject result back to WGS84 for storage/display
result_wgs = result_utm.to_crs(CRS_WGS84)
```

### Anti-patterns

- Mixing CRS in a single spatial operation (e.g., `gpd.sjoin(wgs_gdf, utm_gdf)`)
- Forgetting to verify CRS match before overlay/sjoin — always assert `gdf1.crs == gdf2.crs`
- Computing `.area` or `.length` before reprojecting to a projected CRS

---

## 4. Spatial Operations

### Clip to District Boundary

Reference: `src/school_socioeconomic_analysis.py:clip_to_district()` (line 728)

```python
def clip_to_district(gdf, district):
    """Clip a GeoDataFrame to the district boundary.
    Filters out non-polygon geometries that can result from edge clipping.
    """
    clipped = gpd.clip(gdf, district.to_crs(gdf.crs))
    mask = clipped.geometry.geom_type.isin(["Polygon", "MultiPolygon"])
    return clipped[mask].copy()
```

Key points:
- Use `gpd.clip()`, not manual `intersects()` + bounding box
- Always filter geometry types after clipping (edge clipping can produce points/lines)
- Match CRS before clipping: `district.to_crs(gdf.crs)`

### Overlay Operations

- Always perform in UTM: `gpd.overlay(a.to_crs(CRS_UTM17N), b.to_crs(CRS_UTM17N), how="intersection")`
- Conservation checks: verify total area or population is preserved (within tolerance) after areal interpolation

---

## 5. Raster/Grid Operations

### Grid Construction

- Grids are built **directly in WGS84** using latitude-corrected degree spacing: `dlat = resolution_m / 111320`, `dlon = resolution_m / (111320 × cos(center_lat))`
- This avoids the convergence-angle rotation that occurs when creating grids in UTM and reprojecting to WGS84
- All analysis modules (`road_pollution.py`, `environmental_map.py`, `school_desert.py`, `school_closure_analysis.py`) use the same WGS84-native grid approach
- Cell **queries** (KD-tree, raster reads) transform cell centers to UTM on-the-fly when metric distances are needed

### District Boundary Clipping

Raster overlays MUST be masked to the district boundary at render time:

1. Build a boolean mask of grid cells inside the district polygon (with ~200 m buffer for edge smoothing)
2. Zero out cells outside the mask before rendering to RGBA
3. Use vectorized shapely operations (`prepared.prep()` + `contains()`) for performance

This prevents data from extending beyond the district boundary on the map.

### Grid Dimension Asymmetry

The WGS84 grid has equal angular spacing in x and y, but the cells are not square in meters (longitude degrees are ~18% shorter than latitude degrees at 35.9°N). This is acceptable for screening-level analysis because:
- Distance queries use UTM coordinates, not grid indices
- The visual distortion is minimal at the district scale

---

## 6. Map Visualization Standards

All interactive maps use Folium with a consistent visual language.

### Base Map

| Element | Value |
|---------|-------|
| Tileset | `cartodbpositron` |
| Center | `[35.9132, -79.0558]` |
| Zoom | 12 |

### School Markers

Reference: `src/school_socioeconomic_analysis.py` line 1761

```python
folium.CircleMarker(
    location=[lat, lon],
    radius=6,
    color="#333333",
    weight=2,
    fillColor="#2196F3",
    fillOpacity=1.0,
)
```

All maps must include a "Schools" FeatureGroup with fixed-blue markers (always visible). Metric-colored markers may be added as separate toggleable layers.

### District Boundary

Reference: `src/school_socioeconomic_analysis.py` line 1492

```python
folium.GeoJson(
    district.to_crs(CRS_WGS84).__geo_interface__,
    name="District Boundary",
    style_function=lambda x: {
        "fillColor": "transparent",
        "color": "#333333",
        "weight": 2,
        "dashArray": "5,5",
    },
)
```

### Choropleth Normalization

Reference: `src/school_socioeconomic_analysis.py` line 1526

```python
import matplotlib.colors as mcolors
import matplotlib.pyplot as plt

vals = gdf[col].dropna()
vmin = vals.quantile(0.05)
vmax = vals.quantile(0.95)
norm = mcolors.Normalize(vmin=vmin, vmax=vmax)
cmap = plt.get_cmap(cmap_name)
fill_color = mcolors.rgb2hex(cmap(norm(value)))
```

- Use 5th–95th percentile normalization to handle outliers
- NaN values: fill with `#cccccc`

### Colormaps

Use matplotlib perceptually-uniform colormaps. **Never hand-code RGB color ramps.**

| Data Type | Colormap | Rationale |
|-----------|----------|-----------|
| Pollution / risk (sequential) | `YlOrRd` | Yellow-orange-red, perceptually uniform |
| Diverging (cool → hot) | `RdYlBu_r` | Blue-yellow-red, symmetric diverging |
| Income / positive | `YlGn` | Yellow-green |
| Percentage | `OrRd` | Orange-red |
| Delta / increase (sequential) | `Oranges` | White-to-orange, transparent where no change |
| Traffic difference (diverging) | `RdBu_r` | Red-white-blue, symmetric diverging |

### Client-Side Canvas Rendering

For scenarios requiring many overlay variants (e.g., arbitrary multi-school closure combinations × 3 modes × 2 views), use **client-side canvas rendering** instead of pre-rendered PNG overlays:

1. **Per-entity float32 grids**: Rasterize each entity (e.g., school) separately as base64-encoded float32 arrays. Embed in HTML as JS variables.
2. **Colormap LUTs**: Use `_generate_cmap_lut(cmap_name)` to extract 256-entry RGBA arrays from matplotlib. Client-side JS indexes into the LUT for coloring.
3. **Client-side computation**: JS computes derived values (e.g., `min(open_schools)` or `closure - baseline`) from the raw grids in real-time.
4. **Custom Leaflet layer**: `CanvasHeatmapLayer` renders a `<canvas>` element positioned over the map, updated on zoom/pan.

This pattern is used by `school_closure_analysis.py`, which embeds per-school float32 grids + colormap LUTs for travel time rendering, and predecessor maps + edge lookups for client-side traffic computation. The resulting HTML is ~16 MB — supporting all 2^11 closure combinations with no pre-computation.

Reference: `src/school_closure_analysis.py`, `_generate_cmap_lut()` and `renderHeatmapCanvas` / `computeTraffic` in `_build_control_html()`.

### Summary Table

| Element | Standard | Reference |
|---------|----------|-----------|
| Base tileset | `cartodbpositron` | All maps |
| School markers | `CircleMarker(radius=6, color="#333333", fillColor="#2196F3", fillOpacity=1.0)` | socioeconomic line 1761 |
| District boundary | `GeoJson, color="#333333", weight=2, dashArray="5,5", fillColor="transparent"` | socioeconomic line 1492 |
| Choropleth normalization | 5th–95th percentile with `matplotlib.colors.Normalize` | socioeconomic line 1526 |
| Colormaps | matplotlib perceptually-uniform (YlOrRd, RdYlBu_r, etc.) | socioeconomic line 1504 |
| NaN fill | `#cccccc` | socioeconomic line 1537 |
| Canvas rendering | Per-entity float32 grids + colormap LUTs + client-side computation | closure analysis |

---

## 7. Data Validation Checklist

Before every spatial join or overlay:

- [ ] Assert CRS on both inputs: `assert a.crs == b.crs`
- [ ] Verify geometry types are compatible (polygons for overlay, points for sjoin)

After areal interpolation:

- [ ] Conservation check: `abs(sum_before - sum_after) / sum_before < 0.01`

After loading school data:

- [ ] Validate school names against the canonical 11-school set
- [ ] Verify count: `assert len(schools) == 11`

After clipping:

- [ ] Filter geometry types: keep only `Polygon` / `MultiPolygon` for polygon layers
- [ ] Log feature count before and after clip

---

*This document is maintained alongside all geospatial analysis code. Update it when conventions change.*
