# CD Forecast Reconstruction: Next Session Initialization

## Context

You are continuing iterative work on `experiments/cd_reconstruction/reconstruct.py` (~2800 lines), a self-contained tool that reverse-engineers Carolina Demography's (CD) 10-year enrollment forecast for CHCCS elementary schools.

**Critical principle:** We are trying to reproduce CD's work using ONLY their methods and reasonable inferences from their published report. We must NOT incorporate any of our own project's geospatial analysis (drive zones, dot-weighted demographics, etc.). CD's work is 100% independent of ours. EXCEPTION: Census "School Zones" data = CHCCS attendance zones, which CD also had.

---

## Current State (as of 2026-05-07)

### What works:
- **Track B** (inverse model): Recovers implied baselines and composite GPRs from published targets.
- **Track A** (forward sweep): Tests 91,692 configurations across 7 parameter dimensions (12 B2K methods).
- **Coordinate descent**: Per-school-grade optimization, converges in 2-5 rounds.
- **Published base year** (`--use-published-base`): Uses TARGETS[:,0] as known 2025-26 base.
- **Student-body geography** (`bk_studentbody_trend`): Models program schools drawing K from across district.
- **OSBM birth extrapolation** (`--birth-extrap osbm`, default): Uses OSBM age-0 growth rate instead of linear extrapolation. Births plateau ~1,180 instead of increasing to 1,280+.

### Current RMSE scores (with `--use-published-base --coordinate-descent --birth-extrap osbm`):
| Metric | Score |
|--------|-------|
| Global best (single config) | 50.6 |
| Per-school optimized | 3.6 |
| **Coordinate descent** | **3.2** |

### Per-school breakdown (coordinate descent, OSBM):
| School | RMSE | B2K Method | Notes |
|--------|------|-----------|-------|
| Carrboro | 2.7 | bk_studentbody_trend | Excellent |
| Ephesus | 2.3 | bk_studentbody_trend | Excellent |
| Estes Hills | 2.6 | bk_A | Excellent |
| FPG | 2.8 | bk_B | Excellent |
| Glenwood | 2.7 | bk_B | Excellent (was 5.9 with linear births) |
| McDougle | 3.4 | bk_studentbody | Good |
| Morris Grove | 2.4 | bk_A | Excellent |
| Northside | 1.7 | bk_A_trend | Near-perfect |
| Rashkis | 3.0 | bk_studentbody_trend | Good (was 79.8 at start) |
| Scroggs | 2.4 | bk_A_trend | Excellent |
| **Seawell** | **6.4** | bk_census_b2k | **Last remaining problem school** |

### RMSE History
| Date | Per-School | Coord Descent | Key Change |
|------|-----------|---------------|------------|
| 2026-05-05 (start) | 32.9 | — | Baseline |
| 2026-05-05 (births) | 27.9 | 24.4 | +asymptotic GPR, +B2K trend |
| 2026-05-06 (census) | 27.6 | — | +census proxy methods (marginal) |
| 2026-05-06 (studentbody) | 9.5 | 6.4 | +studentbody geography (breakthrough) |
| 2026-05-07 (Codex fixes) | 6.5 | 4.2 | Fixed program detection + normalization |
| 2026-05-07 (OSBM) | **3.6** | **3.2** | +OSBM birth extrapolation |

---

## What Carolina Demography Actually Used (from their report)

### Data Sources:
| Dataset | Source | Years | Status |
|---------|--------|-------|--------|
| Average Daily Membership Month-2 | CHCCS, NCDPI | 2015-16 through 2025-26 | Using CCD proxy (2015-2025) |
| Attendance Zone Boundaries | CHCCS | 2025-26 | Using census "School Zones" |
| Births to resident mothers | NC DPH | 2011 through 2022 | Using full Orange County |
| Population estimates/forecasts by age | NC OSBM | 2010-2060 | **IMPLEMENTED** (age-0 growth rate) |
| Developments (42 projects) | Municipalities | March 2026 | Implemented (3 schedules) |
| Student addresses (implied) | CHCCS | 2025-26 | Approximated via student-body geography |

### Key methodological facts:
1. **GPR model**: "10 years of historical ADM by grade by school. Kindergarten forecasted via birth-to-kindergarten ratios using births 5 years prior."
2. **B2K ratio**: Was 1.18 in 2015-16, "now declining toward/below 1.0"
3. **Birth data**: "Births to resident mothers" — likely CH+Carrboro municipal (we use full county, B2K self-calibrates)
4. **Zone definition for program schools**: "Zone" = student-body geography for FPG/Glenwood/Carrboro/Seawell
5. **Base year**: 2025-26 is their KNOWN base year.
6. **Capture rate**: Declining from 94% (2020) to 91% (2024). Not yet modeled.
7. **OSBM projections**: Used for birth/population trajectory. We now use OSBM age-0 growth rate.

---

## Remaining Problem: Seawell (RMSE = 6.4)

Seawell is the last school above 3.5 RMSE. Analysis:
- Baseline trajectory has a non-monotonic "dip-then-recovery" pattern
- Years 1-3: declining ~11/year (GPR ~0.968)
- Year 4: sharp +6 reversal (GPR 1.019)
- Years 5-10: oscillates, then gentle decline
- Development yields are small (12 at 5yr, 18 at 10yr) — can't explain the reversal
- Likely caused by a specific historical cohort size progressing through the school

### Possible approaches:
1. **Capture rate modeling**: CD documents 94%→91% decline. If Seawell is disproportionately affected (e.g., charter proximity), this could help.
2. **Cohort-specific adjustments**: If Seawell has an unusually small cohort in grades 3-4 now, those grades "falling off" while larger K cohorts enter could create the dip-then-rise.
3. **Program strand growth**: Seawell has a program strand. If CD projected increasing program draw, Seawell's K share would grow over time.

---

## Suggested Next Steps

### A. Capture rate modeling (CD-documented, not yet implemented)
94%→91% over 4 years. Could be applied as declining multiplicative factor. May be geographically concentrated.

### B. Trending program draw
If magnet participation is increasing, program schools' external draw grows over time. Currently bk_studentbody uses static shares. A trending version could further improve Rashkis and help Seawell.

### C. Expand birth data range
Our load_births() caps at 2022 (CD's stated range), but we have 2023 DPH data (1163). CD's report was published April 2026 — they likely had 2023 births too. Adding 2023 would give one more known birth year.

### D. Municipal-level birth data
NC SCHS may publish Chapel Hill + Carrboro municipal births separately from full county.

---

## File Locations

| File | Purpose |
|------|---------|
| `experiments/cd_reconstruction/reconstruct.py` | Main reconstruction code (~2800 lines) |
| `experiments/cd_reconstruction/data/births_chapel_hill.csv` | NC DPH Orange County births 2010-2023 |
| `experiments/cd_reconstruction/data/osbm_orange_age0.csv` | OSBM age-0 projections (2010-2040) |
| `experiments/cd_reconstruction/data/adm_history_680.csv` | CCD enrollment 2015-16 to 2024-25 |
| `experiments/cd_reconstruction/output/methodology.md` | Auto-generated findings document |
| `data/processed/census_dot_zone_demographics.csv` | Census age data by school zone |
| `docs/CAROLINA_DEMOGRAPHY_ENROLLMENT_FORECAST_2026.md` | Our extraction of CD's report |
| `docs/CAROLINA-DEMOGRAPHY_METHODS-AND-QUESTIONS.md` | Detailed methods analysis |

## Commands

```bash
# Best results: OSBM birth extrapolation + published base + coord descent (~20 min)
python experiments/cd_reconstruction/reconstruct.py --use-published-base --coordinate-descent --birth-extrap osbm

# Linear birth extrapolation (for comparison)
python experiments/cd_reconstruction/reconstruct.py --use-published-base --coordinate-descent --birth-extrap linear

# Track B only (fast, no sweep)
python experiments/cd_reconstruction/reconstruct.py --track-b-only

# Quick test (no coord descent, ~12 min)
python experiments/cd_reconstruction/reconstruct.py --use-published-base --birth-extrap osbm
```

---

## Ground Rules

1. **Census "School Zones" = CHCCS attendance zones** — CD had these, so they're fair game.
2. **Only use data that CD had access to**: NCDPI/CCD enrollment, NC DPH births, OSBM projections, CHCCS attendance zones, student addresses (implied), development data.
3. **Inferences must be grounded in CD's report**: If we hypothesize CD did X, cite evidence.
4. **The goal is reproduction, not improvement**: We want to understand HOW CD produced their numbers.
5. **Per-school parameterization is expected**: CD made school-specific modeling decisions.
6. **Program schools use student-body geography**: "Zone" for FPG/Glenwood/Carrboro/Seawell means where students live, not attendance boundary.
