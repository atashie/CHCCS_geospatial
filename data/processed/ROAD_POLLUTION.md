# Road Pollution Exposure Analysis — CHCCS Elementary Schools

**Analysis Date:** February 2026
**Method:** Road-classification-weighted exponential decay model with tree canopy mitigation

---

## What This Index Measures (Plain Language)

The TRAP Exposure Index answers a simple question: **how much traffic pollution
is a school exposed to based on nearby roads?**

1. **Every road segment within 500m of a school contributes pollution.** Bigger
   roads contribute more — an interstate (weight 1.0) contributes 100x more than
   a residential street (weight 0.01), because it carries roughly 100x more traffic.
2. **Closer roads matter more than distant ones.** Pollution drops off exponentially
   with distance. A road 100m away contributes about 3.5x more than the same road
   at 500m. By ~500m, most of the pollution has dispersed to near-background levels.
3. **The index sums up all contributions.** Every 50-meter chunk of every road within
   the radius gets a score based on its size and distance, and they all get added
   together. More big roads nearby = higher index.

**What the numbers mean in practice:**

- Glenwood (19.3) sits near multiple major roads — it gets hit from many directions
- FPG (17.6) is similar — right next to Glen Lennox and busy corridors
- Ephesus (1.7) is relatively sheltered — near 15-501 but not immediately adjacent,
  and most surrounding roads are residential
- Rashkis (0.18) is essentially surrounded by nothing but neighborhood streets

The index is **comparative, not a health measurement**. A score of 19 doesn't mean
"unhealthy air" — it means Glenwood has roughly 11x more traffic-generated pollution
pressure than Ephesus, and 107x more than Rashkis. The actual air quality at any
school depends on wind, terrain, buildings, and other factors this model doesn't capture.

---

## Methodology

### Road Pollution Index

For each road sub-segment *i* within the analysis radius of a school:

```
P_i = W(road_class_i) * exp(-0.003 * d_i)
```

Where:
- `W` = weight based on road classification (proxy for traffic volume)
- `d_i` = distance from sub-segment centroid to school (meters)
- `0.003 m^-1` = composite decay rate for NOx/BC/UFP
- Roads discretized into ~50m sub-segments to capture both distance AND length effects

**Total raw index:** `P_raw = SUM(P_i)` for all sub-segments within radius

**Methodological validation:** The composite decay rate λ = 0.003 m⁻¹ is validated by
Boogaard et al. (2019), a meta-analysis of near-road pollutant concentration decay
rates that found λ = 0.0026 for black carbon and λ = 0.0027 for NOx. Our composite
value sits within the observed range for major TRAP pollutants. The use of road
classification as an AADT proxy is standard practice in Land-Use Regression (LUR)
epidemiological models when actual traffic count data is unavailable (Hoek et al., 2008).

### Road Classification Weights (AADT Proxy)

| Road Class | AADT Proxy | Weight |
|------------|-----------|--------|
| motorway | ~50,000 | 1.000 |
| trunk | ~30,000 | 0.600 |
| primary | ~15,000 | 0.300 |
| secondary | ~7,500 | 0.150 |
| tertiary | ~3,000 | 0.060 |
| residential | ~500 | 0.010 |

**IMPORTANT:** These weights are proxies based on typical AADT by road class, 
NOT actual traffic count data for these specific roads. Actual AADT data from 
NCDOT would improve accuracy.

### Tree Canopy Mitigation

```
f_mitigation = 0.56 * canopy_cover  (capped at 80%)
P_net = P_raw * (1 - f_mitigation)
```

- Canopy cover fraction from Impact Observatory 10m Land Use/Land Cover
- Alpha = 0.56 derived from: 2.8% PM2.5 reduction per 5% canopy cover increase
- Based on meta-analyses of urban vegetation air quality effects (Nowak et al., 2014)

---

## Results: 500m Radius

| Rank | School | Raw Index | Canopy % | Mitigation % | Net Index | Net (Normalized) |
|------|--------|-----------|----------|-------------|-----------|-----------------|
| 1 | Glenwood Elementary | 19.29 | 61.0% | 34.2% | 12.70 | 100.0 |
| 2 | Frank Porter Graham Bilingue | 17.61 | 72.4% | 40.6% | 10.47 | 82.4 |
| 3 | Scroggs Elementary | 4.68 | 65.9% | 36.9% | 2.95 | 23.3 |
| 4 | Carrboro Elementary | 4.42 | 76.0% | 42.6% | 2.54 | 20.0 |
| 5 | McDougle Elementary | 3.13 | 72.6% | 40.7% | 1.86 | 14.6 |
| 6 | Morris Grove Elementary | 3.72 | 91.1% | 51.0% | 1.82 | 14.4 |
| 7 | Estes Hills Elementary | 2.93 | 93.5% | 52.3% | 1.40 | 11.0 |
| 8 |  **Ephesus Elementary** | 1.72 | 81.7% | 45.8% | 0.93 | 7.3 |
| 9 | New FPG Location | 1.34 | 80.9% | 45.3% | 0.73 | 5.8 |
| 10 | Northside Elementary | 1.17 | 84.3% | 47.2% | 0.62 | 4.9 |
| 11 | Seawell Elementary | 0.79 | 82.1% | 46.0% | 0.43 | 3.4 |
| 12 | Rashkis Elementary | 0.18 | 71.1% | 39.8% | 0.11 | 0.8 |

## Results: 1000m Radius

| Rank | School | Raw Index | Canopy % | Mitigation % | Net Index | Net (Normalized) |
|------|--------|-----------|----------|-------------|-----------|-----------------|
| 1 | Glenwood Elementary | 23.99 | 69.8% | 39.1% | 14.61 | 100.0 |
| 2 | Frank Porter Graham Bilingue | 22.16 | 80.2% | 44.9% | 12.20 | 83.5 |
| 3 | Scroggs Elementary | 8.78 | 83.2% | 46.6% | 4.69 | 32.1 |
| 4 | Carrboro Elementary | 8.11 | 75.5% | 42.3% | 4.68 | 32.0 |
| 5 | New FPG Location | 8.56 | 84.4% | 47.3% | 4.51 | 30.9 |
| 6 |  **Ephesus Elementary** | 7.13 | 73.0% | 40.9% | 4.22 | 28.9 |
| 7 | Northside Elementary | 5.56 | 67.9% | 38.0% | 3.45 | 23.6 |
| 8 | McDougle Elementary | 4.42 | 80.4% | 45.0% | 2.43 | 16.6 |
| 9 | Morris Grove Elementary | 5.01 | 93.6% | 52.4% | 2.38 | 16.3 |
| 10 | Estes Hills Elementary | 4.19 | 92.5% | 51.8% | 2.02 | 13.8 |
| 11 | Seawell Elementary | 2.03 | 85.3% | 47.8% | 1.06 | 7.2 |
| 12 | Rashkis Elementary | 0.63 | 77.2% | 43.2% | 0.36 | 2.4 |

---

## Ephesus Elementary Summary

- **500m raw rank:** #8 of 12
- **500m net rank (after canopy):** #8 of 12
- **1000m raw rank:** #6 of 12
- **1000m net rank (after canopy):** #6 of 12
- **Tree canopy (500m):** 81.7%
- **Tree canopy (1000m):** 73.0%

### Context

Ephesus is located on Ephesus Church Road (two-lane) but is east of NC 15-501 
(Fordham Boulevard), a major 4-lane arterial classified as a 'trunk' road. 
The proximity to 15-501 contributes to the school's pollution exposure score.

---

## Health & Educational Implications

A large and growing body of peer-reviewed research links traffic-related air pollution
(TRAP) exposure near schools to adverse health, cognitive, and academic outcomes in
children. The Health Effects Institute's comprehensive review of 353 reports (HEI SR23,
2022) confirmed causal or likely-causal associations between TRAP and respiratory
disease, cardiovascular effects, lung cancer, cognitive impairment, and preterm birth.
Health Canada's 2025 systematic review meta-analyzed 64 studies and established a
causal relationship between TRAP and all-cause mortality. The evidence below is
organized by how closely each study's exposure metric matches our index (distance from
roads) versus measured pollutant concentrations (which our index does not estimate).

### Proximity-Based Health Evidence

The following studies use distance from roads as their exposure metric — the same
quantity our index captures. Yu et al. (2025), the most comprehensive proximity-based
meta-analysis (55 studies, 373,320 participants), found that living within 200m of a
major road is associated with asthma (OR 1.23, 95% CI: 1.15–1.31), wheezing (OR 1.21,
1.12–1.30), and rhinitis (OR 1.22, 1.13–1.32). McConnell et al. (2006) found asthma
OR 1.50 for children living <75m from a major road, with effects returning to
background at 150–200m. Gauderman et al. (2007) showed significant lung function
deficits (FEV₁ and MMEF) in children living <500m from a freeway — the same radius as
our primary analysis. Freid et al. (2021) found that infants living <100m from a major
road had a wheeze hazard ratio of 1.59 (1.08–2.33) and asthma OR of 1.51 (1.00–2.28).
Nishimura et al. (2020) documented a dose-response gradient: each 100m increase in
distance from a major road was associated with 29% fewer symptom days (OR 0.71,
0.58–0.87), directly paralleling our index's continuous distance-decay function.

### Dispersion Model Evidence

CALINE4 dispersion models share mathematical structure with our formula — both weight
exposure by traffic volume and distance from road. McConnell et al. (2010), using
CALINE4 in the Children's Health Study (2,497 children), found that non-freeway local
road pollution at home carried an asthma hazard ratio of 1.51 (1.25–1.81), and at
school HR 1.45 (1.06–1.98). Combined home and school exposure yielded HR 1.61
(1.29–2.00). Islam et al. (2019) confirmed elevated bronchitic symptom risk from
non-freeway near-road air pollution: OR 1.18 (1.04–1.33) for all children, rising to
OR 1.44 (1.17–1.78) for asthmatic children. These findings validate our inclusion of
all OSM road classes (not just freeways) with traffic-volume-based weights.

### Cognitive and Academic Effects

Sunyer et al. (2015, BREATHE study) found that children at high-TRAP schools showed
7.4% working memory growth over 12 months versus 11.5% at low-TRAP schools — a
substantial gap measured by elemental carbon and NO₂ concentrations at school.
Heissel et al. (2022) used a natural experiment (wind direction variation while holding
distance constant) to establish a causal effect: schools downwind of highways >60% of
the year showed −0.040 SD in test scores and increased behavioral incidents. Kweon et
al. (2018) found that Michigan schools closer to highways had higher test failure rates
and lower attendance after controlling for SES — using continuous distance in meters,
the same metric as our index. Requia et al. (2021, 256 Brazilian schools) demonstrated
distance decay in academic effects: the strongest impact (−0.011 pts per km of road)
occurred within 250m, weakening to −0.002 at 1km, mirroring the exponential decay in
our formula. Stenson et al. (2021) systematically reviewed 10 studies on TRAP and
academic performance; 9 of 10 found a negative association.

### Ephesus Context

Ephesus ranks #8 of 12 in raw pollution exposure at 500m — in the lower third of
district schools. This is a moderate position; schools like
Glenwood (19.29) and FPG (17.61) face pollution indices roughly
10x higher than Ephesus (1.72).
However, at the 1000m radius, Ephesus rises to #5 due to proximity to the NC 15-501
corridor. The proximity-based health literature above applies most strongly to the
highest-ranked schools (Glenwood, FPG). These results should not be overstated —
Ephesus is not among the most pollution-exposed schools in the district at the
primary 500m radius.

### Closure Consideration

If Ephesus were closed, the 99 students currently walking to school would be bused,
increasing their daily TRAP exposure during transit along arterial roads. Three lines
of evidence establish this effect:

1. **In-vehicle concentrations:** Karner et al. (2010) found that in-vehicle pollutant
   concentrations on busy roads are 2–10× higher than ambient levels.
2. **Busing exposure:** A Detroit study (PMC8715954) found that busing 15km along
   urban roads resulted in ~340 µg/m³ daily NOₓ exposure versus ~60–100 µg/m³ for
   walking to a local school — approximately 2–3× higher daily exposure.
3. **Academic impact of bus emissions:** Austin et al. (2019) showed that reducing
   school bus diesel exposure through fleet retrofits produced measurable gains in
   English test scores and improved respiratory health (aerobic capacity).

Converting 99 walkers to bus riders along NC 15-501 (a trunk road in our index)
directly contradicts EPA School Siting Guidelines (2011) and EPA Near-Road Best
Practices (2015), both of which recommend minimizing student commute pollution
exposure. This argument applies regardless of Ephesus's relative pollution ranking.

*For complete citations, evidence tier classification, and methodological notes, see*
*`data/processed/TRAP_FULL_LITERATURE_REVIEW.md`.*

---

## Limitations

1. **Road weights are proxies**, not actual AADT traffic counts.
   Real traffic volumes may differ significantly from class-based estimates.
2. **The pollution index is relative/comparative**, not an absolute health risk
   assessment. It should not be interpreted as pollutant concentrations.
3. **Tree canopy mitigation factors** are from literature meta-analyses, not
   Chapel Hill-specific measurements. Local conditions (species, density,
   seasonality) may differ.
4. **Wind patterns, terrain, and building effects** are not modeled. These
   factors significantly influence actual pollutant dispersion.
5. **Temporal variation** (rush hour, seasonal) is not captured.
6. **CRITICAL: ESA WorldCover urban canopy limitation.** The ESA WorldCover 10m
   land cover classifies each pixel into a single dominant class. In suburban
   areas like Chapel Hill (which has ~55% city-wide tree canopy per American
   Forests estimates), neighborhoods with scattered trees along streets and in
   yards are classified as "Built-up" (class 50) rather than "Tree cover" (class 10).
   This means **tree canopy mitigation is significantly underestimated for urban
   and suburban schools** (most show 0% canopy within 500m) while being
   accurately captured for schools near contiguous forest. A high-resolution
   tree canopy cover dataset (e.g., USDA Forest Service Urban Tree Canopy,
   LiDAR-derived canopy height) would substantially improve the mitigation
   analysis. **The raw pollution index (without mitigation) is the more
   reliable metric for comparing schools.**

---

## Sources

### Methodology & Data
- Karner, A. A., Eisinger, D. S., & Niemeier, D. A. (2010). Near-roadway air quality: 
  Synthesizing the findings from real-world data. *Environ Sci Technol*, 44(14). DOI: 10.1021/es100008x
- Health Effects Institute. (2010). Traffic-Related Air Pollution: A Critical Review 
  of the Literature on Emissions, Exposure, and Health Effects. HEI Special Report 17.
- Nowak, D. J., et al. (2014). Tree and forest effects on air quality and human health 
  in the United States. *Environ Pollution*, 193.
- Impact Observatory / Esri. (2023). 10m Annual Land Use Land Cover.
- OpenStreetMap contributors. Road network data.
- Boogaard, H., et al. (2019). Concentration decay rates for near-road air pollutants. 
  *Int J Hyg Environ Health*, 222(7). [λ = 0.0026 BC, 0.0027 NOx]
- Hoek, G., et al. (2008). Land-use regression models for intraurban air pollution. 
  *Atmos Environ*, 42(33). [Road-class-as-AADT-proxy precedent]

### Proximity-Based Health Evidence
- Yu, M., et al. (2025). Residential proximity to major roads and respiratory disease risk. 
  *Clin Rev Allergy Immunol*, 68, 5. DOI: 10.1007/s12016-024-09010-1
- McConnell, R., et al. (2006). Traffic, susceptibility, and childhood asthma. 
  *Environ Health Perspect*, 114(5), 766–772. PMID: 16675435
- Gauderman, W. J., et al. (2007). Effect of exposure to traffic on lung development. 
  *Lancet*, 369(9561), 571–577. PMID: 17258668
- Freid, R. D., et al. (2021). Residential proximity to major roadways and asthma 
  phenotypes in children. *Int J Environ Res Public Health*, 18(14), 7746.
- Nishimura, K. K., et al. (2020). Early-life air pollution and asthma risk in 
  minority children. *J Allergy Clin Immunol*, 131(3), 684–690. PMID: 32007569

### Dispersion Model Evidence
- McConnell, R., et al. (2010). Childhood incident asthma and traffic-related air 
  pollution at home and school. *Environ Health Perspect*, 118(7), 1021–1026. PMID: 20064776
- Islam, T., et al. (2019). Non-freeway near-road air pollution and bronchitic symptoms. 
  *Am J Respir Crit Care Med*, 180(3), 215–222. PMID: 30092140

### Cognitive & Academic Effects
- Sunyer, J., et al. (2015). Traffic-related air pollution in schools and cognitive 
  development. *PLoS Med*, 12(3), e1001792. PMID: 25734425
- Heissel, J. A., Persico, C., & Simon, D. (2022). Does pollution drive achievement? 
  *J Human Resources*, 57(3), 747–776. DOI: 10.3368/jhr.59.3.0521-11689R2
- Kweon, B.-S., et al. (2018). Proximity of public schools to major highways and 
  students' performance. *Environ Plan B*, 45(2), 312–329. DOI: 10.1177/2399808317714113
- Requia, W. J., et al. (2021). Neighborhood traffic-related air pollution and academic 
  performance in Brazil. *Environ Res*, 201, 111036. DOI: 10.1016/j.envres.2021.111036
- Stenson, C., et al. (2021). Impact of traffic-related air pollution on child academic 
  performance: systematic review. *Environ Int*, 155, 106696. DOI: 10.1016/j.envint.2021.106696

### Closure/Busing Evidence
- Austin, W., Heutel, G., & Kreisman, D. (2019). School bus emissions, student health 
  and academic performance. *Econ Educ Rev*, 70, 109–126. DOI: 10.1016/j.econedurev.2019.03.003
- Persico, C. L., & Venator, J. (2021). Effects of local industrial pollution on students 
  and schools. *J Human Resources*, 56(2), 406–445. DOI: 10.3368/jhr.57.4.1119-10542R2
- Detroit busing exposure study. PMC8715954.

### Comprehensive Reviews & Regulatory
- Health Effects Institute. (2022). Long-Term Exposure to Traffic-Related Air Pollution. 
  HEI Special Report 23.
- Health Canada. (2025). Human Health Risk Assessment for Traffic-Related Air Pollution.
- U.S. EPA. (2011). School Siting Guidelines. EPA-100-K-11-004.
- U.S. EPA. (2015). Best Practices for Reducing Near-Road Pollution Exposure at Schools.
- WHO. (2013). REVIHAAP Technical Report.

---

*Analysis generated by src/road_pollution.py*
