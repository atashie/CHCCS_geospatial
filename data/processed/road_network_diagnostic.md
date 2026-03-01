# Road Network Diagnostic Report

**Generated:** 2026-02-28 22:21
**Method:** Compare `drive_service` (analysis network) vs all `highway` tags

This diagnostic downloads an unrestricted road network (all OSM `highway` tags)
within 1200m of each school and compares it against the `drive_service` network
used in the TRAP analysis. The goal is to quantify what road types are excluded
and whether they could meaningfully affect pollution scores.

---

## Drive-Service Network Summary

| Highway Type | Segments |
|-------------|----------|
| residential | 22,914 |
| service | 22,829 |
| tertiary | 6,567 |
| secondary | 2,288 |
| unclassified | 1,174 |
| primary | 1,040 |
| trunk | 814 |
| motorway_link | 110 |
| trunk_link | 52 |
| motorway | 50 |
| living_street | 27 |
| tertiary_link | 13 |
| primary_link | 12 |
| secondary_link | 8 |

**Total segments in analysis:** 57,898

---

## Per-School Comparison

### Carrboro Elementary

**500m radius:**
- Drive-service segments: 434
- All-highway segments: 1072
- Excluded types: ['cycleway', 'footway', 'path']
- Excluded segments: 351

  | Excluded Type | Segments | Avg Length (m) |
  |--------------|----------|----------------|
  | cycleway | 14 | 74 |
  | footway | 334 | 47 |
  | path | 3 | 91 |
- **Estimated max impact if excluded roads were residential-weighted:** 1.2866

**1000m radius:**
- Drive-service segments: 1422
- All-highway segments: 3768
- Excluded types: ['corridor', 'cycleway', 'footway', 'path', 'steps', 'track']
- Excluded segments: 1295

  | Excluded Type | Segments | Avg Length (m) |
  |--------------|----------|----------------|
  | corridor | 14 | 25 |
  | cycleway | 47 | 62 |
  | footway | 1205 | 46 |
  | path | 17 | 72 |
  | steps | 10 | 17 |
  | track | 2 | 57 |
- **Estimated max impact if excluded roads were residential-weighted:** 2.3014


### Seawell Elementary

**500m radius:**
- Drive-service segments: 24
- All-highway segments: 308
- Excluded types: ['cycleway', 'footway', 'path', 'steps', 'track']
- Excluded segments: 234

  | Excluded Type | Segments | Avg Length (m) |
  |--------------|----------|----------------|
  | cycleway | 14 | 80 |
  | footway | 46 | 97 |
  | path | 112 | 204 |
  | steps | 4 | 36 |
  | track | 58 | 148 |
- **Estimated max impact if excluded roads were residential-weighted:** 0.8481

**1000m radius:**
- Drive-service segments: 156
- All-highway segments: 1292
- Excluded types: ['cycleway', 'footway', 'path', 'steps', 'track']
- Excluded segments: 806

  | Excluded Type | Segments | Avg Length (m) |
  |--------------|----------|----------------|
  | cycleway | 66 | 84 |
  | footway | 292 | 70 |
  | path | 334 | 198 |
  | steps | 4 | 36 |
  | track | 110 | 152 |
- **Estimated max impact if excluded roads were residential-weighted:** 1.3320


### Ephesus Elementary

**500m radius:**
- Drive-service segments: 205
- All-highway segments: 588
- Excluded types: ['cycleway', 'footway']
- Excluded segments: 271

  | Excluded Type | Segments | Avg Length (m) |
  |--------------|----------|----------------|
  | cycleway | 47 | 28 |
  | footway | 224 | 45 |
- **Estimated max impact if excluded roads were residential-weighted:** 0.9170

**1000m radius:**
- Drive-service segments: 801
- All-highway segments: 2830
- Excluded types: ['cycleway', 'footway', 'path', 'steps']
- Excluded segments: 1046

  | Excluded Type | Segments | Avg Length (m) |
  |--------------|----------|----------------|
  | cycleway | 226 | 42 |
  | footway | 806 | 48 |
  | path | 6 | 84 |
  | steps | 8 | 52 |
- **Estimated max impact if excluded roads were residential-weighted:** 1.6840


### Estes Hills Elementary

**500m radius:**
- Drive-service segments: 140
- All-highway segments: 439
- Excluded types: ['cycleway', 'footway', 'path']
- Excluded segments: 146

  | Excluded Type | Segments | Avg Length (m) |
  |--------------|----------|----------------|
  | cycleway | 46 | 65 |
  | footway | 68 | 78 |
  | path | 32 | 83 |
- **Estimated max impact if excluded roads were residential-weighted:** 0.7554

**1000m radius:**
- Drive-service segments: 443
- All-highway segments: 1369
- Excluded types: ['cycleway', 'footway', 'path', 'steps']
- Excluded segments: 482

  | Excluded Type | Segments | Avg Length (m) |
  |--------------|----------|----------------|
  | cycleway | 108 | 52 |
  | footway | 228 | 83 |
  | path | 144 | 114 |
  | steps | 2 | 5 |
- **Estimated max impact if excluded roads were residential-weighted:** 1.0870


### Frank Porter Graham Bilingue

**500m radius:**
- Drive-service segments: 234
- All-highway segments: 609
- Excluded types: ['cycleway', 'footway', 'path']
- Excluded segments: 212

  | Excluded Type | Segments | Avg Length (m) |
  |--------------|----------|----------------|
  | cycleway | 11 | 38 |
  | footway | 157 | 58 |
  | path | 44 | 163 |
- **Estimated max impact if excluded roads were residential-weighted:** 0.7695

**1000m radius:**
- Drive-service segments: 792
- All-highway segments: 2012
- Excluded types: ['cycleway', 'footway', 'path', 'steps', 'track']
- Excluded segments: 694

  | Excluded Type | Segments | Avg Length (m) |
  |--------------|----------|----------------|
  | cycleway | 25 | 88 |
  | footway | 547 | 65 |
  | path | 94 | 125 |
  | steps | 2 | 9 |
  | track | 26 | 60 |
- **Estimated max impact if excluded roads were residential-weighted:** 1.2901


### Glenwood Elementary

**500m radius:**
- Drive-service segments: 197
- All-highway segments: 603
- Excluded types: ['cycleway', 'footway', 'path']
- Excluded segments: 256

  | Excluded Type | Segments | Avg Length (m) |
  |--------------|----------|----------------|
  | cycleway | 8 | 102 |
  | footway | 208 | 61 |
  | path | 40 | 259 |
- **Estimated max impact if excluded roads were residential-weighted:** 0.9083

**1000m radius:**
- Drive-service segments: 576
- All-highway segments: 2121
- Excluded types: ['corridor', 'cycleway', 'footway', 'path', 'steps']
- Excluded segments: 1025

  | Excluded Type | Segments | Avg Length (m) |
  |--------------|----------|----------------|
  | corridor | 2 | 21 |
  | cycleway | 69 | 57 |
  | footway | 786 | 57 |
  | path | 162 | 171 |
  | steps | 6 | 87 |
- **Estimated max impact if excluded roads were residential-weighted:** 1.6247


### McDougle Elementary

**500m radius:**
- Drive-service segments: 158
- All-highway segments: 553
- Excluded types: ['footway', 'path', 'steps']
- Excluded segments: 272

  | Excluded Type | Segments | Avg Length (m) |
  |--------------|----------|----------------|
  | footway | 264 | 50 |
  | path | 6 | 110 |
  | steps | 2 | 16 |
- **Estimated max impact if excluded roads were residential-weighted:** 1.5065

**1000m radius:**
- Drive-service segments: 411
- All-highway segments: 1390
- Excluded types: ['cycleway', 'footway', 'path', 'steps', 'track']
- Excluded segments: 594

  | Excluded Type | Segments | Avg Length (m) |
  |--------------|----------|----------------|
  | cycleway | 2 | 146 |
  | footway | 554 | 63 |
  | path | 20 | 170 |
  | steps | 2 | 16 |
  | track | 16 | 73 |
- **Estimated max impact if excluded roads were residential-weighted:** 1.7883


### Scroggs Elementary

**500m radius:**
- Drive-service segments: 360
- All-highway segments: 1405
- Excluded types: ['cycleway', 'footway', 'path', 'steps']
- Excluded segments: 768

  | Excluded Type | Segments | Avg Length (m) |
  |--------------|----------|----------------|
  | cycleway | 47 | 75 |
  | footway | 711 | 37 |
  | path | 8 | 156 |
  | steps | 2 | 55 |
- **Estimated max impact if excluded roads were residential-weighted:** 3.0793

**1000m radius:**
- Drive-service segments: 813
- All-highway segments: 2862
- Excluded types: ['cycleway', 'footway', 'path', 'steps']
- Excluded segments: 1464

  | Excluded Type | Segments | Avg Length (m) |
  |--------------|----------|----------------|
  | cycleway | 90 | 69 |
  | footway | 1336 | 42 |
  | path | 32 | 197 |
  | steps | 6 | 128 |
- **Estimated max impact if excluded roads were residential-weighted:** 3.8740


### Rashkis Elementary

**500m radius:**
- Drive-service segments: 73
- All-highway segments: 462
- Excluded types: ['cycleway', 'footway', 'path', 'steps']
- Excluded segments: 260

  | Excluded Type | Segments | Avg Length (m) |
  |--------------|----------|----------------|
  | cycleway | 29 | 81 |
  | footway | 213 | 86 |
  | path | 16 | 470 |
  | steps | 2 | 108 |
- **Estimated max impact if excluded roads were residential-weighted:** 0.9944

**1000m radius:**
- Drive-service segments: 446
- All-highway segments: 2183
- Excluded types: ['cycleway', 'footway', 'path', 'steps', 'track']
- Excluded segments: 1174

  | Excluded Type | Segments | Avg Length (m) |
  |--------------|----------|----------------|
  | cycleway | 43 | 79 |
  | footway | 1069 | 58 |
  | path | 58 | 236 |
  | steps | 2 | 108 |
  | track | 2 | 98 |
- **Estimated max impact if excluded roads were residential-weighted:** 2.0004


### Morris Grove Elementary

**500m radius:**
- Drive-service segments: 51
- All-highway segments: 235
- Excluded types: ['cycleway', 'footway', 'path']
- Excluded segments: 138

  | Excluded Type | Segments | Avg Length (m) |
  |--------------|----------|----------------|
  | cycleway | 18 | 106 |
  | footway | 60 | 45 |
  | path | 60 | 116 |
- **Estimated max impact if excluded roads were residential-weighted:** 0.7235

**1000m radius:**
- Drive-service segments: 93
- All-highway segments: 461
- Excluded types: ['cycleway', 'footway', 'path', 'track']
- Excluded segments: 274

  | Excluded Type | Segments | Avg Length (m) |
  |--------------|----------|----------------|
  | cycleway | 24 | 116 |
  | footway | 60 | 45 |
  | path | 176 | 99 |
  | track | 14 | 145 |
- **Estimated max impact if excluded roads were residential-weighted:** 0.8973


### Northside Elementary

**500m radius:**
- Drive-service segments: 278
- All-highway segments: 659
- Excluded types: ['cycleway', 'footway', 'path', 'steps']
- Excluded segments: 248

  | Excluded Type | Segments | Avg Length (m) |
  |--------------|----------|----------------|
  | cycleway | 38 | 75 |
  | footway | 198 | 47 |
  | path | 6 | 152 |
  | steps | 6 | 62 |
- **Estimated max impact if excluded roads were residential-weighted:** 1.1381

**1000m radius:**
- Drive-service segments: 1375
- All-highway segments: 4473
- Excluded types: ['corridor', 'cycleway', 'footway', 'living_street', 'path', 'proposed', 'steps', 'track']
- Excluded segments: 1804

  | Excluded Type | Segments | Avg Length (m) |
  |--------------|----------|----------------|
  | corridor | 12 | 30 |
  | cycleway | 50 | 94 |
  | footway | 1630 | 36 |
  | living_street | 6 | 20 |
  | path | 32 | 68 |
  | proposed | 2 | 446 |
  | steps | 62 | 31 |
  | track | 10 | 143 |
- **Estimated max impact if excluded roads were residential-weighted:** 2.7205


### New FPG Location

**500m radius:**
- Drive-service segments: 251
- All-highway segments: 836
- Excluded types: ['cycleway', 'footway', 'steps']
- Excluded segments: 410

  | Excluded Type | Segments | Avg Length (m) |
  |--------------|----------|----------------|
  | cycleway | 13 | 195 |
  | footway | 393 | 64 |
  | steps | 4 | 119 |
- **Estimated max impact if excluded roads were residential-weighted:** 1.2927

**1000m radius:**
- Drive-service segments: 904
- All-highway segments: 2940
- Excluded types: ['cycleway', 'footway', 'path', 'steps']
- Excluded segments: 1456

  | Excluded Type | Segments | Avg Length (m) |
  |--------------|----------|----------------|
  | cycleway | 74 | 106 |
  | footway | 1276 | 53 |
  | path | 96 | 145 |
  | steps | 10 | 83 |
- **Estimated max impact if excluded roads were residential-weighted:** 2.3819


---

## Interpretation

Excluded road types (footway, cycleway, path, pedestrian, steps, track, etc.)
are non-motor-vehicle roads that do not generate significant traffic-related
air pollution. Their exclusion from the TRAP analysis is methodologically correct.

The 'estimated max impact' assumes all excluded roads carry residential-level
traffic (weight=0.01), which is a substantial overestimate for pedestrian paths
and cycleways. The actual impact of excluding these roads is effectively zero
for pollution modeling purposes.
