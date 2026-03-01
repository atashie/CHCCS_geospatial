# Property Data Guide

Three datasets in this directory, all sourced from Orange County, NC (Tax Assessor / GIS). Together they form a complete spatial property database for the CHCCS district area.

---

## 1. `CHCCS/CHCCS.shp` — School Attendance Zone Boundaries

| Field | Value |
|-------|-------|
| Type | Shapefile (polygon) |
| Features | 161 zones |
| CRS | NAD 1983 StatePlane NC (feet) |
| Coverage | Full CHCCS district |

Each polygon represents a named neighborhood or street segment assigned to specific elementary, middle, and high schools.

**Key columns:**

| Column | Description |
|--------|-------------|
| `ENAME` | Elementary school name (e.g., "Ephesus Elementary") |
| `ELEM` | Elementary school code (e.g., "EP") |
| `MNAME` / `HNAME` | Middle / high school name |
| `SHORTNAME` | Neighborhood or street segment name |
| `WALKZONE` | Walking zone flag ("Y") |
| `ESWALK` / `MSWALK` / `HSWALK` | Walk flags by school level |

**Zone counts by elementary school:** Northside (33), Estes Hills (24), McDougle (20), Ephesus (17), Rashkis (15), Carrboro (14), Scroggs (12), Seawell (11), Morris Grove (10), Glenwood (5).

---

## 2. `parcels/parview.shp` — Tax Parcel Boundaries

| Field | Value |
|-------|-------|
| Type | Shapefile (polygon) |
| Features | 59,307 parcels |
| CRS | NAD 1983 StatePlane NC (feet) — same as CHCCS |
| Coverage | All of Orange County |

Full cadaster with parcel geometry, ownership, valuation, and regulatory overlays. Filter to CHCCS district using `SCHOOL_SYS == "Chapel Hill/Carrboro Schools"` (28,090 parcels).

**Key columns:**

| Column | Description |
|--------|-------------|
| `PIN` | Parcel ID — **join key** to the Excel file |
| `SCHOOL_SYS` | School system ("Chapel Hill/Carrboro Schools" or "Orange County Schools") |
| `RATECODE` | Tax rate classification (see decoding notes below) |
| `CALC_ACRES` | Lot size in acres |
| `SQFT` | Building square footage |
| `BLDGCNT` | Number of buildings on parcel |
| `YEARBUILT` | Year structure built |
| `VALUATION` | Total assessed value |
| `LANDVALUE` / `BLDGVALUE` | Land and building values |
| `CONDONAME` | Condo/complex name (non-null for 4,303 parcels) |
| `OWNER1` | Owner name |
| `ADDRESS1`, `CITY`, `STATE`, `ZIPCODE` | Mailing address |
| `Zonings` | Zoning code(s) — mostly NaN for CHCCS parcels |
| `Floodzone1` / `Floodzone5` | 100-yr / 500-yr floodplain (Y/N) |
| `SUBDIVISIO` | Subdivision name |
| `DATESOLD` | Last sale date |

**No explicit property-class field** (residential vs. commercial, single-family vs. apartment). Best proxies:

- **`RATECODE`** — tax rate code. For CHCCS parcels:
  - Codes 21, 22, 32 → small lots (0.1–0.7 ac), high condo association → likely multi-family / condo / townhome
  - Codes 02, 04, 07, 11, 14, 17, 19 → larger lots (2–6 ac), rare condos → likely single-family
- **`CONDONAME`** — non-null identifies condo units specifically
- **`Primary LUC`** in the Excel file (see below) provides labeled land-use classes; join via `PIN` ↔ `Parcel ID`

---

## 3. `Data Request 1-27-2026.xlsx` — Property Tax Assessment Records

| Field | Value |
|-------|-------|
| Type | Excel workbook |
| Rows | 247,808 |
| Sheets | 2 ("Sheet" = main data; "Sheet2" = sparse summary/pivot) |
| Source | FOIA response from Orange County Tax Assessor, dated Jan 27, 2026 |
| Coverage | All of Orange County |

**Key columns (Sheet 1):**

| Column | Description |
|--------|-------------|
| `Parcel ID` | **Join key** to shapefile `PIN` |
| `Primary Neighborhood` | Area code (e.g., "1003 - 1MINCEYSC") |
| `Total Appraised Value` | Appraised value ($) |
| `Total Assessed Value` | Assessed value ($) |
| `Sale Date` | Last sale date (Excel serial format) |
| `Sale Price` | Last sale price ($) |
| `Sold As Vacant` | 0/1 flag |
| `Imp/Vac/YI` | "Improved" or "Vacant" — development status |
| `Primary LUC` | **Land Use Classification** — best property-type field |

**`Primary LUC` values** (most useful for property classification):

| Code | Meaning |
|------|---------|
| `RES-I` | Residential — Improved |
| `RES-U` | Residential — Unimproved (vacant lot) |
| `RES-U-AG` | Residential — Unimproved, Agricultural use |
| (others) | Commercial, institutional, etc. |

**Note:** Requires `openpyxl` to read with pandas (`pip install openpyxl`).

---

## Joining the datasets

```
parcels/parview.shp  ←──PIN = Parcel ID──→  Data Request 1-27-2026.xlsx
        │
        │ (spatial join)
        ▼
CHCCS/CHCCS.shp  →  assigns each parcel to an elementary school zone
```

1. Join Excel → shapefile on `Parcel ID` = `PIN` to add `Primary LUC` and valuation detail to parcel geometries.
2. Spatial-join parcels to CHCCS attendance zones to assign each parcel to a school.
3. Filter to CHCCS district: `SCHOOL_SYS == "Chapel Hill/Carrboro Schools"` (28,090 parcels).
