# Affordable Housing Data — Source Documentation & Quality Assessment

**Download date:** 2026-03-01
**Records:** 1168 housing units
**Columns:** 44

---

## Data Source

**Provider:** Town of Chapel Hill
**Dashboard:** [Chapel Hill Affordable Housing](https://www.chapelhillaffordablehousing.org/tracking-our-progress)
**Service type:** ArcGIS Feature Service (public, no authentication)
**Endpoint:**
```
https://services2.arcgis.com/7KRXAKALbBGlCW77/arcgis/rest/services/
  Affordable_Housing_Data_2025/FeatureServer/6/query
```

Each record represents one housing **unit** (not a development/project).
Native CRS is NC State Plane (WKID 102719); downloaded with `outSR=4326` for WGS84.

## Download Methodology

1. Query the ArcGIS REST API with `where=1=1`, `outFields=*`, `outSR=4326`
2. Paginate via `resultOffset`/`resultRecordCount` (page size = 1000)
3. Convert point geometry features (x/y) to Shapely `Point` objects
4. Build a GeoDataFrame with EPSG:4326 CRS
5. Cache to `data/cache/affordable_housing.gpkg`

## Field Inventory

| Field | Type | Populated | Null | % Complete |
|-------|------|-----------|------|------------|
| AHDR | int64 | 1168 | 0 | 100.0% |
| AMIServed | str | 1167 | 1 | 99.9% |
| Address | str | 1168 | 0 | 100.0% |
| Affordability_End_Date | float64 | 240 | 928 | 20.5% |
| Bedrooms | int64 | 1168 | 0 | 100.0% |
| Bond | int64 | 1168 | 0 | 100.0% |
| CDBG | int64 | 1168 | 0 | 100.0% |
| Carrboro_AHR | int64 | 1168 | 0 | 100.0% |
| City | str | 1168 | 0 | 100.0% |
| DV | str | 1161 | 7 | 99.4% |
| DV1 | str | 1131 | 37 | 96.8% |
| Disability | str | 1162 | 6 | 99.5% |
| Disability1 | str | 1161 | 7 | 99.4% |
| EstSquareFeet | float64 | 631 | 537 | 54.0% |
| HOME | int64 | 1168 | 0 | 100.0% |
| HUD_Money | int64 | 1168 | 0 | 100.0% |
| Homeless | str | 1162 | 6 | 99.5% |
| Homeless1 | str | 1161 | 7 | 99.4% |
| Jurisdiction | str | 1168 | 0 | 100.0% |
| MonthlyRent | str | 784 | 384 | 67.1% |
| Name2 | str | 1163 | 5 | 99.6% |
| OBJECTID | int64 | 1168 | 0 | 100.0% |
| Other | int64 | 1168 | 0 | 100.0% |
| PIN | str | 1168 | 0 | 100.0% |
| ProjectName | str | 1168 | 0 | 100.0% |
| ProjectType | str | 1168 | 0 | 100.0% |
| Provider | str | 1168 | 0 | 100.0% |
| RentalOwnership | str | 1168 | 0 | 100.0% |
| Restricted | str | 216 | 952 | 18.5% |
| Restricted2 | str | 623 | 545 | 53.3% |
| Seniors | str | 1160 | 8 | 99.3% |
| Seniors1 | str | 1161 | 7 | 99.4% |
| TOCH_AHR | int64 | 1168 | 0 | 100.0% |
| Tenant_Subsidy_Accepted | str | 782 | 386 | 67.0% |
| Total | int64 | 1168 | 0 | 100.0% |
| UnitStructure | str | 863 | 305 | 73.9% |
| UnitType | str | 1167 | 1 | 99.9% |
| Veteran | str | 1161 | 7 | 99.4% |
| Veteran1 | str | 1161 | 7 | 99.4% |
| X | float64 | 1168 | 0 | 100.0% |
| Y | float64 | 1168 | 0 | 100.0% |
| YearBuilt | str | 1143 | 25 | 97.9% |
| Zip | str | 1168 | 0 | 100.0% |

## Geographic Coverage

- **Longitude range:** [-79.0825, -79.0089]
- **Latitude range:** [35.8651, 35.9770]
- **Unique locations:** 1140
- **Records sharing coordinates:** 33
  - This is expected: multiple units in the same building/development share a single point.
- All points fall within the expected Chapel Hill / Orange County bounding box

## Categorical Field Distributions

### AMIServed

5 unique values:

| Value | Count | % |
|-------|-------|---|
| 30-60% | 506 | 43.3% |
| 0-30% | 446 | 38.2% |
| 60-80% | 179 | 15.3% |
| 80%+ | 36 | 3.1% |
| *(null)* | 1 | 0.1% |

### RentalOwnership

2 unique values:

| Value | Count | % |
|-------|-------|---|
| Rental | 827 | 70.8% |
| Ownership | 341 | 29.2% |

### Provider

15 unique values:

| Value | Count | % |
|-------|-------|---|
| Town of Chapel Hill | 334 | 28.6% |
| Community Home Trust | 237 | 20.3% |
| DHIC, Inc. | 149 | 12.8% |
| Habitat for Humanity of Orange County | 99 | 8.5% |
| Inter Church Council Housing Corporation (INCHUCO) | 79 | 6.8% |
| Dobbins Hill Apartments Limited Partnership | 55 | 4.7% |
| EmPOWERment Inc. | 43 | 3.7% |
| First Baptist Church | 41 | 3.5% |
| CASA | 38 | 3.3% |
| Dobbins Hill II LLC | 32 | 2.7% |
| Bell Partners | 25 | 2.1% |
| Community Housing Alternatives | 24 | 2.1% |
| Pee Wee Homes | 7 | 0.6% |
| Shared Visions Foundation | 4 | 0.3% |
| Self-Help Ventures Fund | 1 | 0.1% |

### ProjectType

11 unique values:

| Value | Count | % |
|-------|-------|---|
| Public Housing | 299 | 25.6% |
| LIHTC 9% | 260 | 22.3% |
| Inclusionary | 222 | 19.0% |
| New Development | 129 | 11.0% |
| Section 236 | 79 | 6.8% |
| Section 202 | 65 | 5.6% |
| Purchase/Rehab | 57 | 4.9% |
| Other | 23 | 2.0% |
| Transitional Housing | 17 | 1.5% |
| Northside Initiatives | 10 | 0.9% |
| Master Leasing | 7 | 0.6% |

### UnitType

5 unique values:

| Value | Count | % |
|-------|-------|---|
| Multi-family | 771 | 66.0% |
| Single Family | 186 | 15.9% |
| Townhome | 108 | 9.2% |
| Condo | 102 | 8.7% |
| *(null)* | 1 | 0.1% |

### City

2 unique values:

| Value | Count | % |
|-------|-------|---|
| Chapel Hill | 1138 | 97.4% |
| Carrboro | 30 | 2.6% |

### Jurisdiction

3 unique values:

| Value | Count | % |
|-------|-------|---|
| Chapel Hill | 1136 | 97.3% |
| Carrboro | 30 | 2.6% |
| Chapel Hill  | 2 | 0.2% |

## Numeric Field Ranges

| Field | Count | Min | Max | Mean | Median |
|-------|-------|-----|-----|------|--------|
| Bedrooms | 1168 | 0 | 5 | 2.2 | 2.0 |
| EstSquareFeet | 631 | 320 | 13101 | 992.4 | 990.0 |

## Date Fields

### YearBuilt

- **Populated:** 1143 / 1168
- **Range:** 1921 to 2024 (year)

### Affordability_End_Date

- **Populated:** 240 / 1168
- **Range:** 2022-04-30 to 2049-05-17
- **Format:** Epoch milliseconds (converted to dates)

## Project Summary

- **Project field:** `ProjectName`
- **Unique projects:** 82
- **Total units:** 1168

## Known Limitations & Caveats

1. **Unit-level, not project-level:** Each record is a single unit. Multiple units in the same building share identical coordinates.
2. **Point geometry only:** Locations are single points, not building footprints. Spatial precision is at the development level, not the individual unit.
3. **Snapshot in time:** This dataset reflects the state at download date. The Town updates the dashboard periodically; records may change.
4. **NC State Plane origin:** Native coordinates are in NC State Plane (WKID 102719). Conversion to WGS84 is handled server-side via `outSR=4326`.
5. **No independent verification:** Field values (AMI levels, unit types, dates) are as reported by the Town. No cross-referencing with HUD or LIHTC data was performed.

## Reproduction

```bash
# Download and assess (writes cache + this report)
python src/affordable_housing.py

# Assess cached data only (no network requests)
python src/affordable_housing.py --cache-only
```
