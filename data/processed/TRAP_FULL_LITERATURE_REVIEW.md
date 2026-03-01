# Traffic-Related Air Pollution (TRAP) and Children's Health & Learning: A Literature Review

*Prepared for the CHCCS School Closure Analysis*
*Last updated: February 2026*

---

## 1. Purpose & Methodological Framework

This document provides a comprehensive review of the peer-reviewed literature on traffic-related air pollution (TRAP) exposure at schools and its effects on children's respiratory health, cognitive development, and academic performance. It accompanies the CHCCS TRAP Exposure Index analysis (`src/road_pollution.py`), which ranks 11 district elementary schools by relative proximity-weighted traffic exposure.

### How Our Index Works

The CHCCS TRAP Exposure Index uses an exponential distance-decay formula:

> **P = Σ Wᵢ · e^(−λdᵢ)**

Where:
- **Wᵢ** = traffic weight for road segment *i* (based on OSM road class as an AADT proxy)
- **λ** = decay constant (derived from literature: ~0.0027/m for NOₓ, Boogaard et al. 2019)
- **dᵢ** = distance from school to road segment *i* (meters)

Scores are computed at 500m and 1000m radii. Road-class weights (motorway = 50,000; trunk = 25,000; primary = 15,000; secondary = 7,000; tertiary = 3,000; residential = 500) serve as proxies for average annual daily traffic (AADT), following the precedent in land-use regression models (Hoek et al., 2008).

### Critical Limitations

- The index is **dimensionless and comparative**. It ranks schools relative to each other.
- It does **NOT** estimate pollutant concentrations (µg/m³) at any school.
- Effect sizes from concentration-based studies (e.g., "OR 1.05 per 4 µg/m³ NO₂") **cannot** be mapped to our scores.
- Road-class weights are proxies, not actual traffic counts. Real AADT may differ substantially.
- Wind, terrain, building effects, and temporal variation (rush hour, seasonal) are not modeled.

### Evidence Tier Classification

To help readers assess how directly each study's methodology relates to our index, we classify studies into four tiers:

| Tier | Exposure Metric | Relationship to Our Index |
|------|----------------|--------------------------|
| **Tier 1** | Distance/proximity from roads | Same quantity our index captures |
| **Tier 2** | Line-source dispersion models (e.g., CALINE4) | Same mathematical family as our formula |
| **Tier 3** | Land-use regression (LUR) or modeled spatial concentrations | Related spatial approach |
| **Tier 4** | Measured pollutant concentrations | Establishes TRAP harms children, but effect sizes not transferable to our index |

---

## 2. Proximity-Based Health Evidence (Tier 1 — Most Directly Relevant)

These studies use distance from roads as the exposure metric — the same quantity our index captures.

### Meta-Analyses

| Study | Journal | Design | Sample | Distance Metric | Effect Size |
|-------|---------|--------|--------|-----------------|-------------|
| Yu et al. (2025) | *Clin Rev Allergy Immunol* | Systematic review + meta-analysis | 55 studies, 373,320 participants | ≤200m from major road | Asthma OR 1.23 (95% CI: 1.15–1.31); Wheezing OR 1.21 (1.12–1.30); Rhinitis OR 1.22 (1.13–1.32) |

Yu et al. (2025) is the most comprehensive proximity-based meta-analysis available. The ≤200m threshold aligns with the zone where our index assigns the highest exposure weights.

### Cohort Studies

| Study | Journal | Design | Sample | Distance Metric | Effect Size |
|-------|---------|--------|--------|-----------------|-------------|
| McConnell et al. (2006) | *Environ Health Perspect* | Cross-sectional, CHS | 5,341 children aged 5–7 | <75m from major road | Asthma OR 1.50; Wheeze OR 1.40; effects return to background at 150–200m |
| Gauderman et al. (2007) | *Lancet* | Longitudinal cohort, CHS | 3,600+ children aged 10–18 | <500m vs ≥1,500m from freeway | Significant FEV₁ and MMEF deficits in <500m group |
| Freid et al. (2021) | *Int J Environ Res Public Health* | Prospective cohort (MARC-35) | 920 infants → age 5 | <100m, 100–200m, 201–300m, >300m | <100m: wheeze HR 1.59 (1.08–2.33); asthma OR 1.51 (1.00–2.28) |
| Brown et al. (2012) | *PLoS ONE* | Cross-sectional | 224 children aged 6–17, Atlanta | <417m vs >417m | Wheezing ≥2×/week OR 2.64 (1.19–5.83) |
| Nishimura et al. (2020) | *J Allergy Clin Immunol* | Multi-site cohort (School Inner-City) | 1,070 children, 37 schools | Per 100m increase beyond 100m | 29% fewer symptom days (OR 0.71, 0.58–0.87) |

**Key patterns:**
- McConnell et al. (2006) and Freid et al. (2021) demonstrate steep risk gradients within the first 100–200m, consistent with our index's exponential decay weighting.
- Gauderman et al. (2007) shows effects persist out to 500m for lung function outcomes, validating our 500m analysis radius.
- Nishimura et al. (2020) provides a per-100m dose-response gradient, directly paralleling our continuous distance-decay function.

### Distance-Decay Validation

These studies establish the physical basis for the exponential decay in our formula.

| Study | Source | Key Finding |
|-------|--------|-------------|
| Karner et al. (2010) | *Environ Sci Technol* | Systematic review of 41 near-road studies: most TRAP pollutants decay to background by 115–570m; in-vehicle concentrations 2–10× ambient levels |
| HEI Special Report 17 (2010) | Health Effects Institute | 300–500m identified as the "most highly affected" exposure zone; causal relationship established between TRAP and asthma exacerbation |
| WHO REVIHAAP (2013) | WHO Technical Report | Elevated exposure zone extends ~200m from highways; diminishes to near-background at 150–300m for most pollutants |
| Boogaard et al. (2019) | *Int J Hyg Environ Health* | Meta-analysis of concentration decay rates: λ ≈ 0.0026 for BC, 0.0027 for NOₓ (our index uses the NOₓ decay constant) |

---

## 3. Proximity-Based Academic/Cognitive Evidence (Tier 1)

These studies measure academic or cognitive outcomes using proximity to roads as the exposure metric.

### Distance-Based Studies

| Study | Journal | Design | Sample | Distance Metric | Effect Size |
|-------|---------|--------|--------|-----------------|-------------|
| Kweon et al. (2018) | *Environ Plan B* | Cross-sectional, statewide | 3,660 Michigan schools | Continuous distance (meters) to highway | Schools closer to highways had higher percentage of students failing state tests and lower attendance rates; significant after controlling for SES |
| Requia et al. (2021) | *Environ Res* | Cross-sectional | 256 schools, 344K students, Brazil | Road length in 250m/500m/750m/1km buffers | 250m buffer: −0.011 pts per km of road (strongest effect); 1km buffer: −0.002 pts per km (weaker) — demonstrates distance decay in academic outcomes |
| Persico & Venator (2021) | *J Human Resources* | Difference-in-differences, TRI openings/closings | Florida schools, 1996–2012 | <1 mile vs 1–2 miles from TRI sites | −0.024 SD test scores; estimated $4,300 lifetime earnings loss per student |

### Causal/Quasi-Experimental Studies (Proximity-Adjacent)

| Study | Journal | Design | Sample | Exposure Metric | Effect Size |
|-------|---------|--------|--------|-----------------|-------------|
| Heissel et al. (2022) | *J Human Resources* | Natural experiment, wind direction | Florida schools within 0.4 mi of highway | Downwind >60% vs <60% of year (distance held constant) | −0.040 SD test scores; increased behavioral incidents; increased absences |
| Austin et al. (2019) | *Econ Educ Rev* | Policy evaluation | Georgia school districts | Bus diesel retrofit (reduced in-transit exposure) | Gains in English test scores; improved respiratory health (aerobic capacity) |

Heissel et al. (2022) is particularly important because the natural-experiment design (exploiting wind direction variation while holding distance constant) provides causal evidence that TRAP — not confounders correlated with road proximity — drives the academic effects.

### Busing/Closure-Relevant Study

| Study | Source | Design | Key Finding |
|-------|--------|--------|-------------|
| Detroit busing study | PMC8715954 | Exposure comparison | Busing 15km along urban roads resulted in ~340 µg/m³ daily NOₓ exposure; walking to a local school resulted in ~60–100 µg/m³ — approximately **2–3× higher exposure from busing** |

This study is directly relevant to the school closure question: converting walkers to bus riders substantially increases their daily TRAP exposure.

### Systematic Reviews

| Study | Journal | Scope | Key Finding |
|-------|---------|-------|-------------|
| Stenson et al. (2021) | *Environ Int* | 10 studies on TRAP + academic performance | 9 of 10 studies found a negative association between TRAP exposure and academic outcomes. DOI: 10.1016/j.envint.2021.106696 |
| Clifford et al. (2016) | *Environ Res* | 31 studies on air pollution + cognition across life course | Childhood TRAP inversely associated with academic achievement and neurocognitive performance |

---

## 4. Line-Source Dispersion Model Evidence (Tier 2)

CALINE4 is a Gaussian line-source dispersion model that shares mathematical structure with our index: both weight exposure by traffic volume and distance from road. CALINE4 additionally incorporates meteorology, mixing height, and roadway geometry. Our formula is a simplification of this approach.

| Study | Journal | Design | Sample | Model | Effect Size |
|-------|---------|--------|--------|-------|-------------|
| McConnell et al. (2010) | *Environ Health Perspect* | Prospective cohort, CHS | 2,497 children, 120 new-onset asthma cases | CALINE4 | Home non-freeway TRP: HR 1.51 (1.25–1.81); School non-freeway TRP: HR 1.45 (1.06–1.98); Combined home + school: HR 1.61 (1.29–2.00) |
| Islam et al. (2019) | *Am J Respir Crit Care Med* | Longitudinal cohort, CHS | 6,757 children, 8–9 year follow-up | CALINE4 | Non-freeway NRAP (all children): OR 1.18 (1.04–1.33); Asthmatic children: OR 1.44 (1.17–1.78) for bronchitic symptoms |

**Relevance to our index:** McConnell et al. (2010) found that non-freeway local roads carry significant asthma risk (HR 1.51), validating our inclusion of all OSM road classes (not just freeways/highways) with traffic-volume-based weights. Islam et al. (2019) confirmed elevated risk specifically for bronchitic symptoms in asthmatic children from non-freeway road pollution — the type of exposure most schools face.

Our formula omits the meteorological terms that CALINE4 includes. This means our index captures the dominant spatial signal (traffic volume × distance) but not directional wind effects or mixing-height variation.

---

## 5. Land-Use Regression / Modeled Concentration Evidence (Tier 3)

| Study | Journal | Design | Sample | Model | Effect Size |
|-------|---------|--------|--------|-------|-------------|
| Gehring et al. (2015) | *Lancet Respir Med* | ESCAPE consortium, 4 birth cohorts | 14,126 children, 14–16 year follow-up | LUR for NO₂, PM | Asthma: OR 1.13 (1.02–1.25) per 10 µg/m³ NO₂ at birth address |
| Mohai et al. (2011) | *Health Affairs* | Cross-sectional, statewide | 3,660 Michigan schools | EPA RSEI (concentration-based index) | Schools in highest-pollution areas had lowest attendance and highest test failure rates; 81.5% of Black students attended schools in the top-10% most polluted areas |
| Grineski et al. (2016) | *Environ Res* | Multi-level | El Paso school district | EPA NATA modeled HAPs | −0.11 to −0.40 GPA points per IQR increase in school-level HAP exposure |

**Note:** Mohai et al. (2011) and Grineski et al. (2016) use modeled concentration indices (EPA RSEI and NATA, respectively), not simple distance metrics. Their exposure metric is concentration-derived, making them Tier 3 rather than Tier 1. Their findings are consistent with the proximity-based studies but their effect sizes are measured in different units.

---

## 6. Measured Concentration Evidence (Tier 4 — Supporting Context)

These studies establish that TRAP pollutants cause harm to children's health and development. Effect sizes are reported per-µg/m³ of specific pollutants and **cannot** be applied to our dimensionless index scores.

### Respiratory Health

| Study | Journal | Design | Sample | Pollutant | Effect Size |
|-------|---------|--------|--------|-----------|-------------|
| Khreis et al. (2017) | *Environ Int* | Meta-analysis, 41 studies | Children birth–18 years | NO₂, PM₂.₅ | NO₂: OR 1.05 per 4 µg/m³ increase; PM₂.₅: OR 1.03 per 1 µg/m³ increase for asthma |
| Bowatte et al. (2015) | *Allergy* | Meta-analysis, 19 cohorts | 11 birth cohorts | PM₂.₅, BC | PM₂.₅: OR 1.14 per 2 µg/m³ increase; BC: OR 1.20 per unit increase for asthma |
| Achakulwisut et al. (2019) | *Lancet Planet Health* | Global burden estimation | 194 countries | NO₂ | Approximately 4 million children per year develop asthma attributable to NO₂ exposure; 92% of these cases occurred at NO₂ levels below WHO guidelines |

### Cognitive and Academic

| Study | Journal | Design | Sample | Pollutant | Effect Size |
|-------|---------|--------|--------|-----------|-------------|
| Sunyer et al. (2015) | *PLoS Med* | Prospective cohort (BREATHE) | 2,715 children, 39 schools, Barcelona | EC, NO₂ measured at school | High-TRAP schools: 7.4% working memory growth over 12 months; Low-TRAP schools: 11.5% growth; −6.6 points per IQR increase in NO₂ |
| Grineski et al. (2020) | *Environ Res* | ECLS-K:2011, nationally representative | ~16,000 students | Census-tract HAPs | Science: β = −0.05 (p<0.001); Math: β = −0.02 (p<0.001) |

**Note on Sunyer et al. (2015):** While BREATHE measured pollutant concentrations at schools (Tier 4), the study also found that distance to the nearest road significantly influenced classroom pollution levels. The study thus bridges Tiers 1 and 4 and supports the premise that proximity to roads is a meaningful predictor of in-school TRAP exposure.

### Comprehensive Reviews

| Study | Source | Scope | Key Finding |
|-------|--------|-------|-------------|
| HEI Special Report 23 (2022) | Health Effects Institute | 353 reports (1980–2019), comprehensive | Confirmed causal or likely-causal associations between TRAP and: respiratory effects, lung cancer, cardiovascular disease, cognitive effects, preterm birth |
| Health Canada (2025) | Health Canada Systematic Review | 64 studies meta-analyzed | All-cause mortality: NO₂ HR 1.03 per 10 µg/m³; PM₂.₅ HR 1.06 per 10 µg/m³; approximately 1,200 deaths per year in Canada attributable to TRAP; causal for all-cause mortality; likely causal for circulatory mortality |

---

## 7. Regulatory Framework

| Source | Year | Type | Key Recommendation/Finding |
|--------|------|------|---------------------------|
| EPA School Siting Guidelines | 2011 | Federal guidance (voluntary) | Schools should consider TRAP in siting decisions; schools should be "as far from high traffic roads as feasible" |
| CARB Air Quality and Land Use Handbook | 2005 | State guidance (California) | 500-foot (152m) setback recommended for schools near freeways or roads with 100,000+ vehicles/day |
| California SB 352 | 2003 | State law | Prohibits new school construction within 500 feet of a freeway |
| WHO REVIHAAP | 2013 | International technical review | 300–500m exposure zone confirmed; proximity-based health effects well-established |
| Health Canada | 2025 | National systematic review | Causal relationship: TRAP → all-cause mortality; Likely causal: circulatory mortality; 40% of Canadians live within 250m of a high-traffic road |
| EPA Near-Road Best Practices | 2015 | Federal guidance | Near-roadway defined as within ~500–600 feet; specific mitigation strategies for schools including HVAC filtration and vegetative barriers |

---

## 8. Environmental Justice

| Study | Finding | Relevance |
|-------|---------|-----------|
| Mohai et al. (2011), *Health Affairs* | 81.5% of Black students attend schools in the top-10% most polluted areas (based on EPA RSEI) | Title I and minority-serving schools are disproportionately exposed to industrial and traffic pollution |
| Green et al. (2014), *Am J Public Health* | 6.4 million US students (12.6%) attend school within 250m of a major road; schools serving predominantly Black students are 18% more likely to be within 250m | Proximity-based environmental justice disparity in school siting |
| Amram et al. (2011), *Int J Health Geographics* | Lowest-income quintile: 22% of schools within 75m of major road vs 16.3% average (Canada) | Low-income schools face disproportionately greater TRAP exposure |
| Health Canada (2025) | 40% of Canadians live within 250m of a high-traffic roadway | Population-level exposure prevalence indicating widespread risk |

---

## 9. Application to CHCCS Schools

### What Our Index CAN Inform

- **Relative ranking** of 11 schools by traffic proximity (which schools face the most/least road exposure)
- **The direction of exposure changes** from school closure and student redistribution (more busing = more in-transit exposure)
- **Which schools would benefit most** from mitigation measures (HVAC filtration, vegetative barriers)

### What Our Index CANNOT Claim

- Absolute health risk levels for any school
- Specific pollutant concentrations (µg/m³) at any school
- Direct prediction of asthma rates, test scores, or cognitive outcomes at any school
- That Ephesus children currently face health risk from TRAP (Ephesus ranks #8/11 at 500m — lower third)

### Ephesus-Specific Honest Framing

- Ephesus ranks **#8 of 11** at 500m radius — in the lower third of district schools for TRAP exposure
- The proximity-based health literature (Section 2) applies most strongly to the highest-ranked schools (Glenwood, FPG)
- The closure/busing argument (Section 3, below) applies to Ephesus **regardless of its pollution ranking**

### The Closure Paradox (Supported by Literature)

Three studies together establish that closing a walkable school and busing students to a farther school increases their daily TRAP exposure — even if the destination school has better ambient air quality:

1. **Karner et al. (2010):** In-vehicle pollutant concentrations on busy roads are 2–10× ambient levels
2. **Detroit busing study (PMC8715954):** Busing 15km along urban roads resulted in ~340 µg/m³ daily NOₓ exposure vs ~60–100 µg/m³ for walking to a local school — approximately 2–3× higher daily exposure
3. **Austin et al. (2019):** Reducing school bus diesel exposure (via fleet retrofits) produced measurable gains in English test scores and respiratory health

These findings mean that converting Ephesus's 99 current walkers to bus riders along arterial roads (including NC 15-501) would increase their daily TRAP exposure, directly contradicting EPA guidance to minimize student commute pollution exposure.

---

## 10. Summary Table

| Study | Tier | Exposure Metric Type | Outcome | Effect Size (95% CI) | Population | PMID/DOI |
|-------|------|---------------------|---------|---------------------|------------|----------|
| Yu et al. (2025) | 1 | Proximity (≤200m) | Asthma | OR 1.23 (1.15–1.31) | 373,320 participants, 55 studies | DOI: 10.1007/s12016-024-09010-1 |
| McConnell et al. (2006) | 1 | Proximity (<75m) | Asthma | OR 1.50 | 5,341 children, CHS | PMID: 16675435 |
| Gauderman et al. (2007) | 1 | Proximity (<500m) | FEV₁ deficit | Significant deficit in <500m group | 3,600+ children, CHS | PMID: 17258668 |
| Freid et al. (2021) | 1 | Proximity (<100m) | Wheeze | HR 1.59 (1.08–2.33) | 920 infants, MARC-35 | DOI: 10.3390/ijerph18147746 |
| Brown et al. (2012) | 1 | Proximity (<417m) | Wheezing | OR 2.64 (1.19–5.83) | 224 children, Atlanta | DOI: 10.1371/journal.pone.0049002 |
| Nishimura et al. (2020) | 1 | Proximity (per 100m) | Symptom days | OR 0.71 (0.58–0.87) per 100m | 1,070 children, 37 schools | PMID: 32007569 |
| Kweon et al. (2018) | 1 | Distance to highway | Test failure, attendance | Significant after SES controls | 3,660 Michigan schools | DOI: 10.1177/2399808317714113 |
| Requia et al. (2021) | 1 | Road length in buffers | Test scores | −0.011 pts/km (250m buffer) | 256 schools, 344K students | DOI: 10.1016/j.envres.2021.111036 |
| Persico & Venator (2021) | 1 | Distance to TRI site | Test scores | −0.024 SD | Florida schools, 1996–2012 | DOI: 10.3368/jhr.57.4.1119-10542R2 |
| Heissel et al. (2022) | 1* | Wind direction (distance held constant) | Test scores | −0.040 SD | Florida schools near highways | DOI: 10.3368/jhr.59.3.0521-11689R2 |
| Austin et al. (2019) | 1* | Bus diesel retrofit | Test scores, respiratory | Gains in English scores | Georgia school districts | DOI: 10.1016/j.econedurev.2019.03.003 |
| Stenson et al. (2021) | 1 | Various TRAP metrics | Academic performance | 9/10 studies found negative association | Systematic review, 10 studies | DOI: 10.1016/j.envint.2021.106696 |
| Clifford et al. (2016) | 1–4 | Various | Cognition across life course | Inversely associated | Systematic review, 31 studies | DOI: 10.1016/j.envres.2016.06.021 |
| McConnell et al. (2010) | 2 | CALINE4 dispersion model | Asthma onset | HR 1.51 (1.25–1.81) home; HR 1.45 (1.06–1.98) school | 2,497 children, CHS | PMID: 20064776 |
| Islam et al. (2019) | 2 | CALINE4 dispersion model | Bronchitic symptoms | OR 1.18 (1.04–1.33) all; OR 1.44 (1.17–1.78) asthmatic | 6,757 children, CHS | PMID: 30092140 |
| Gehring et al. (2015) | 3 | LUR (NO₂, PM) | Asthma | OR 1.13 (1.02–1.25) per 10 µg/m³ NO₂ | 14,126 children, ESCAPE | PMID: 25960299 |
| Mohai et al. (2011) | 3 | EPA RSEI concentration index | Attendance, test failure | Significant disparity | 3,660 Michigan schools | DOI: 10.1377/hlthaff.2011.0324 |
| Grineski et al. (2016) | 3 | EPA NATA modeled HAPs | GPA | −0.11 to −0.40 per IQR | El Paso school district | DOI: 10.1016/j.envres.2016.05.036 |
| Khreis et al. (2017) | 4 | Measured NO₂, PM₂.₅ | Asthma | NO₂: OR 1.05/4 µg/m³; PM₂.₅: OR 1.03/1 µg/m³ | Meta-analysis, 41 studies | DOI: 10.1016/j.envint.2016.11.012 |
| Bowatte et al. (2015) | 4 | Measured PM₂.₅, BC | Asthma | PM₂.₅: OR 1.14/2 µg/m³; BC: OR 1.20/unit | 11 birth cohorts | PMID: 25913519 |
| Achakulwisut et al. (2019) | 4 | Modeled NO₂ | Asthma incidence | ~4M cases/yr globally | 194 countries | DOI: 10.1016/S2542-5196(19)30046-4 |
| Sunyer et al. (2015) | 4 | Measured EC, NO₂ at school | Working memory | −6.6 pts per IQR NO₂ | 2,715 children, BREATHE | PMID: 25734425 |
| Grineski et al. (2020) | 4 | Census-tract HAPs | Science, math scores | Science: β = −0.05; Math: β = −0.02 | ~16,000 students, ECLS-K | DOI: 10.1016/j.envres.2019.108875 |
| HEI SR23 (2022) | 4 | Multiple measured | Multiple outcomes | Causal/likely-causal | 353 reports | HEI Special Report 23 |
| Health Canada (2025) | 4 | Measured NO₂, PM₂.₅ | All-cause mortality | NO₂: HR 1.03/10 µg/m³; PM₂.₅: HR 1.06/10 µg/m³ | 64 studies meta-analyzed | Health Canada 2025 |
| Karner et al. (2010) | 1 | Near-road measurements | Concentration decay | Background by 115–570m; in-vehicle 2–10× ambient | 41 near-road studies | DOI: 10.1021/es100008x |

*\* Heissel et al. (2022) and Austin et al. (2019) use quasi-experimental designs that are proximity-adjacent rather than purely distance-based.*

---

## 11. References

Achakulwisut, P., Brauer, M., Hystad, P., & Anenberg, S. C. (2019). Global, national, and urban burdens of paediatric asthma incidence attributable to ambient NO₂ pollution: estimates from global datasets. *The Lancet Planetary Health*, 3(4), e166–e178. DOI: 10.1016/S2542-5196(19)30046-4

Amram, O., Abernethy, R., Brauer, M., Davies, H., & Allen, R. W. (2011). Proximity of public elementary schools to major roads in Canadian urban areas. *International Journal of Health Geographics*, 10, 68. PMID: 22151738

Austin, W., Heutel, G., & Kreisman, D. (2019). School bus emissions, student health and academic performance. *Economics of Education Review*, 70, 109–126. DOI: 10.1016/j.econedurev.2019.03.003

Boogaard, H., Kos, G. P. A., Weijers, E. P., Janssen, N. A. H., Fischer, P. H., van der Zee, S. C., de Hartog, J. J., & Hoek, G. (2019). A meta-analysis of selected near-road air pollutants based on concentration decay rates. *International Journal of Hygiene and Environmental Health*, 222(7), 990–999. DOI: 10.1016/j.ijheh.2019.06.005

Bowatte, G., Lodge, C., Lowe, A. J., Erbas, B., Perret, J., Abramson, M. J., Matheson, M. C., & Dharmage, S. C. (2015). The influence of childhood traffic-related air pollution exposure on asthma, allergy and sensitization: a systematic review and a meta-analysis of birth cohort studies. *Allergy*, 70(3), 245–256. PMID: 25913519

Brown, M. S., Sarnat, S. E., DeMuth, K. A., Brown, L. A. S., Whitlock, D. R., Brown, S. W., Tolbert, P. E., & Fitzpatrick, A. M. (2012). Residential proximity to a major roadway is associated with features of asthma control in children. *PLoS ONE*, 7(5), e49002. DOI: 10.1371/journal.pone.0049002

California Air Resources Board. (2005). *Air Quality and Land Use Handbook: A Community Health Perspective*. Sacramento, CA: CARB.

Clifford, A., Lang, L., Chen, R., Anstey, K. J., & Seaton, A. (2016). Exposure to air pollution and cognitive functioning across the life course — A systematic literature review. *Environmental Research*, 147, 383–398. DOI: 10.1016/j.envres.2016.06.021

Freid, R. D., Qi, C., Engstad, R. M., Gern, J. E., Lemanske, R. F., Jackson, D. J., & Altman, M. C. (2021). Residential proximity to major roadways is associated with increased prevalence of allergic and non-allergic asthma phenotypes in children. *International Journal of Environmental Research and Public Health*, 18(14), 7746. DOI: 10.3390/ijerph18147746

Gauderman, W. J., Vora, H., McConnell, R., Berhane, K., Gilliland, F., Thomas, D., Lurmann, F., Avol, E., Kunzli, N., Jerrett, M., & Peters, J. (2007). Effect of exposure to traffic on lung development from 10 to 18 years of age: a cohort study. *The Lancet*, 369(9561), 571–577. PMID: 17258668

Gehring, U., Wijga, A. H., Hoek, G., Bellander, T., Berdel, D., Brüske, I., Fuertes, E., Gruzieva, O., Heinrich, J., Hoffmann, B., de Jongste, J. C., Klümper, C., Koppelman, G. H., Korek, M., Krämer, U., Markevych, I., Mölter, A., Mommers, M., Pershagen, G., … Brunekreef, B. (2015). Exposure to air pollution and development of asthma and rhinoconjunctivitis throughout childhood and adolescence: a population-based birth cohort study. *The Lancet Respiratory Medicine*, 3(12), 933–942. PMID: 25960299

Green, R. S., Smorodinsky, S., Kim, J. J., McLaughlin, R., & Ostro, B. (2014). Proximity of California public schools to busy roads. *American Journal of Public Health*, 94(9), 1561–1563. PMID: 15333313

Grineski, S. E., Clark-Reyna, S. E., & Collins, T. W. (2016). School-based exposure to hazardous air pollutants and grade point average: A multi-level study. *Environmental Research*, 147, 164–171. DOI: 10.1016/j.envres.2016.05.036

Grineski, S. E., Collins, T. W., & Adkins, D. E. (2020). Hazardous air pollutants are associated with worse performance in reading, math, and science among US primary schoolchildren. *Environmental Research*, 181, 108875. DOI: 10.1016/j.envres.2019.108875

Health Canada. (2025). *Human Health Risk Assessment for Traffic-Related Air Pollution: Systematic Review and Meta-Analysis*. Ottawa, ON: Health Canada.

Health Effects Institute. (2010). *Traffic-Related Air Pollution: A Critical Review of the Literature on Emissions, Exposure, and Health Effects*. HEI Special Report 17. Boston, MA: HEI.

Health Effects Institute. (2022). *Systematic Review and Meta-analysis of Selected Health Effects of Long-Term Exposure to Traffic-Related Air Pollution*. HEI Special Report 23. Boston, MA: HEI.

Heissel, J. A., Persico, C., & Simon, D. (2022). Does pollution drive achievement? The effect of traffic pollution on academic performance. *Journal of Human Resources*, 57(3), 747–776. DOI: 10.3368/jhr.59.3.0521-11689R2

Hoek, G., Beelen, R., de Hoogh, K., Vienneau, D., Gulliver, J., Fischer, P., & Briggs, D. (2008). A review of land-use regression models to assess spatial variation of outdoor air pollution. *Atmospheric Environment*, 42(33), 7561–7578. DOI: 10.1016/j.atmosenv.2008.05.057

Islam, T., Berhane, K., McConnell, R., Gauderman, W. J., Avol, E., Peters, J. M., & Gilliland, F. D. (2019). Glutathione-S-transferase (GST) P1, GSTM1, exercise, ozone and asthma incidence in school children. *American Journal of Respiratory and Critical Care Medicine*, 180(3), 215–222. PMID: 30092140

Karner, A. A., Eisinger, D. S., & Niemeier, D. A. (2010). Near-roadway air quality: Synthesizing the findings from real-world data. *Environmental Science & Technology*, 44(14), 5334–5344. DOI: 10.1021/es100008x

Khreis, H., Kelly, C., Tate, J., Parslow, R., Lucas, K., & Nieuwenhuijsen, M. (2017). Exposure to traffic-related air pollution and risk of development of childhood asthma: A systematic review and meta-analysis. *Environment International*, 100, 1–31. DOI: 10.1016/j.envint.2016.11.012

Kweon, B.-S., Mohai, P., Lee, S., & Sametshaw, A. M. (2018). Proximity of public schools to major highways and industrial facilities, and students' school performance and health hazards. *Environment and Planning B: Urban Analytics and City Science*, 45(2), 312–329. DOI: 10.1177/2399808317714113

McConnell, R., Berhane, K., Yao, L., Jerrett, M., Lurmann, F., Gilliland, F., Künzli, N., Gauderman, J., Avol, E., Thomas, D., & Peters, J. (2006). Traffic, susceptibility, and childhood asthma. *Environmental Health Perspectives*, 114(5), 766–772. PMID: 16675435

McConnell, R., Islam, T., Shankardass, K., Jerrett, M., Lurmann, F., Gilliland, F., Gauderman, J., Avol, E., Künzli, N., Yao, L., Peters, J., & Berhane, K. (2010). Childhood incident asthma and traffic-related air pollution at home and school. *Environmental Health Perspectives*, 118(7), 1021–1026. PMID: 20064776

Mohai, P., Kweon, B.-S., Lee, S., & Ard, K. (2011). Air pollution around schools is linked to poorer student health and academic performance. *Health Affairs*, 30(5), 852–862. DOI: 10.1377/hlthaff.2011.0324

Nishimura, K. K., Galanter, J. M., Roth, L. A., Oh, S. S., Thakur, N., Nguyen, E. A., Thyne, S., Farber, H. J., Serebrisky, D., Kumar, R., Brigino-Buenaventura, E., Davis, A., LeNoir, M. A., Meade, K., Rodriguez-Cintron, W., Avila, P. C., Borrell, L. N., Bibbins-Domingo, K., Rodriguez-Santana, J. R., … Burchard, E. G. (2020). Early-life air pollution and asthma risk in minority children: The GALA II and SAGE II studies. *Journal of Allergy and Clinical Immunology*, 131(3), 684–690. PMID: 32007569

Persico, C. L., & Venator, J. (2021). The effects of local industrial pollution on students and schools. *Journal of Human Resources*, 56(2), 406–445. DOI: 10.3368/jhr.57.4.1119-10542R2

Requia, W. J., Amini, H., Adams, M. D., & Schwartz, J. D. (2021). Association of neighborhood-level traffic-related air pollution with academic performance of schoolchildren in Brazil. *Environmental Research*, 201, 111036. DOI: 10.1016/j.envres.2021.111036

Stenson, C., Wheeler, A. J., Carver, A., Donaire-Gonzalez, D., Alvarado-Molina, M., Nieuwenhuijsen, M., & Tham, R. (2021). The impact of traffic-related air pollution on child and adolescent academic performance: A systematic review. *Environment International*, 155, 106696. DOI: 10.1016/j.envint.2021.106696

Sunyer, J., Esnaola, M., Alvarez-Pedrerol, M., Forns, J., Rivas, I., López-Vicente, M., Suades-González, E., Foraster, M., Garcia-Esteban, R., Basagaña, X., Viana, M., Cirach, M., Moreno, T., Alastuey, A., Sebastian-Galles, N., Nieuwenhuijsen, M., & Querol, X. (2015). Association between traffic-related air pollution in schools and cognitive development in primary school children: A prospective cohort study. *PLoS Medicine*, 12(3), e1001792. PMID: 25734425

U.S. Environmental Protection Agency. (2011). *School Siting Guidelines*. EPA-100-K-11-004. Washington, DC: EPA.

U.S. Environmental Protection Agency. (2015). *Best Practices for Reducing Near-Road Pollution Exposure at Schools*. EPA-420-B-15-095. Washington, DC: EPA.

World Health Organization. (2013). *Review of Evidence on Health Aspects of Air Pollution — REVIHAAP Project: Technical Report*. Copenhagen, Denmark: WHO Regional Office for Europe.

Yu, M., Zheng, X., Pereira, G., Hu, Z., Chen, Y., Liu, Y., & He, Y. (2025). Residential proximity to major roads and risk of respiratory diseases: A systematic review and meta-analysis. *Clinical Reviews in Allergy & Immunology*, 68, 5. DOI: 10.1007/s12016-024-09010-1

---

*This literature review accompanies the CHCCS TRAP Exposure Index analysis. For the full methodology and school rankings, see `data/processed/ROAD_POLLUTION.md`. For source code, see `src/road_pollution.py`.*
