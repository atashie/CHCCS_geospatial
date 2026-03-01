# Raw Data Sources

## Orange County Parcel Data (`properties/`)

**Source:** Orange County GIS Data Downloads
**URL:** https://www.orangecountync.gov/2090/Download-GIS-Data
**Files:**
- `combined_data_polys.gpkg` — Parcel polygon geometries
- `combined_data_centroids.gpkg` — Parcel centroid points with assessed values, sale dates, land use codes

**How to re-obtain:**
1. Visit the Orange County GIS download page
2. Download the parcel data GeoPackage files
3. Place in `data/raw/properties/`

## Childcare Facility Data (`childcare/`)

**Source:** NC Division of Child Development and Early Education (DCDEE) facility search
**URL:** https://ncchildcare.ncdhhs.gov/
**Files:** CSV exports of licensed childcare centers and family homes in Orange County

**How to re-obtain:**
1. Search DCDEE for Orange County facilities
2. Export results as CSV
3. Place in `data/raw/childcare/`

## CHCCS Attendance Zone Shapefiles

**Source:** Chapel Hill-Carrboro City Schools GIS
**Note:** These are downloaded automatically by the analysis modules and cached in `data/cache/`

## NCES School Locations

**Source:** NCES EDGE Public School Locations 2023-24 (LEAID 3700720)
**Note:** Downloaded automatically by `src/road_pollution.py` and cached at `data/cache/nces_school_locations.csv`
