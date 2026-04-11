# Calibrated Enrollment Allocation — Methodology

_Auto-generated 2026-04-10 18:40 · git 83901c8_

## Purpose

**What this model is.** A **constrained fragment-level reallocation scenario generator** that fits a softmax (logit) choice model against observed 2025-26 per-school ADM. It can reproduce the 11 per-school totals reasonably well by tuning 14 free parameters (magnet intercepts, race bonuses, opt-out ratio, 5-9/0-4 mix, and income effects) against the single ground-truth signal we have: actual per-school enrollment.

**What this model is NOT.** It is **not** a credible estimator of the true magnitudes of racial preferences, income effects, or opt-out bias at CHCCS magnets. With 14 parameters against only 11 data points, and with max pairwise L2 ≈ 0.94 across DE seeds, the coefficient space is weakly identified and sometimes corner-driven: different seeds find quite different parameter vectors that reach similar school-total error. **Do not interpret fitted coefficient values as point estimates of real-world effects.** Treat them as one of many configurations that reproduce the observed totals.

Two independent calibrations are reported — one minimizing MAE across the 11 schools, one minimizing RMSE. Their results are shown side by side. If the two fits produce meaningfully different parameter vectors at similar school-level errors, that is **evidence of weak identification, not two competing answers about district demographics.**

## Hard facts (inputs the calibration respects exactly)

- Actual 2025-26 elementary ADM per school (`adm_forecast_2025_to_2035.csv`), total 4,294
- District-wide homeschool / charter opt-out rate = 10% exactly
- Attendance zones from `data/raw/properties/CHCCS/CHCCS.shp` (10 residential zones; FPG has no traditional zone and draws district-wide)

## Population base: mix of ACS 5-9 and 0-4 buckets

Because the ACS 5-year estimates are multiple years old, the 5-9 bucket has partially aged out of elementary and the 0-4 bucket has partially aged in. The forward model treats the effective elementary-age pool as a mix of the two buckets:

```
alpha = (1 - mu_0_4) * (DISTRICT_ADM / 0.9) / sum(kids_5_9)
beta  =       mu_0_4  * (DISTRICT_ADM / 0.9) / sum(kids_0_4)
combined[f, r] = alpha * raw_5_9[f, r] + beta * raw_0_4[f, r]
```

By construction, `sum(combined) = 4294 / 0.9 = 4771.11` for every value of `mu_0_4 ∈ [0, 1]`. After the 10% per-race opt-out, the retained pool is exactly 4,294 — no explicit rescaling step is required. `mu_0_4 = 0` means the combined pool is purely the 5-9 bucket scaled to 4,771; `mu_0_4 = 1` means the combined pool is purely the 0-4 bucket scaled to 4,771. `mu_0_4` is one of the 14 calibrated parameters, so the optimizer chooses the mix that best fits per-school ADM.

## Forward model

For each kid in block-group fragment `f` (with home school `h(f)`) of race `r`, the destination is drawn from a softmax over:

```
U[stay]      = 0                                                       (always eligible)
U[FPG]       = intercept_FPG      + bonus_FPG_hispanic   * I(r=hispanic)
U[Carrboro]  = intercept_Carrboro + bonus_Carrboro_white * I(r=white)
                                  + bonus_Carrboro_asian * I(r=asian)
                                  + w_income_magnet      * z_income[f]  (only if h(f) != Carrboro)
U[Glenwood]  = intercept_Glenwood + bonus_Glenwood_asian * I(r=asian)   (only if h(f) != Glenwood)
U[Seawell]   = intercept_Seawell  + bonus_Seawell_white  * I(r=white)
                                  + bonus_Seawell_asian  * I(r=asian)
                                  + w_income_magnet      * z_income[f]  (only if h(f) != Seawell)
prob[f, s, r] = exp(U[s]) / sum_{s' eligible} exp(U[s'])
```

The softmax parameterization is **feasible by construction** — every probability is in [0, 1] and each (fragment, race) row sums to 1 exactly. There is no infeasibility penalty. `z_income[f]` is the parent block group's median household income, standardized across unique block groups with the Census -666,666,666 sentinel imputed to the district mean. The per-race opt-out rate per fragment also depends multiplicatively on `exp(w_income_optout * z_income[f])`, with the base rate solved so the district-wide opt-out is exactly 10% regardless of the parameter vector.

## Constraints (enforced inside the forward model)

- **10% opt-out.** Per-race retention is derived from `w_white_optout` and the kids-weighted district white share of the COMBINED pool (which depends on `mu_0_4`) so the weighted opt-out rate is exactly 10% for every parameter vector. White residents opt out at `w_white_optout ×` the rate of non-white residents.
- **Combined-pool total is pinned.** After applying the mu-dependent alpha/beta weights, the combined pool sums to exactly 4,771.1 before opt-out, and to exactly 4,294 after. No additional rescaling step is required.

## Parameters fit

### MAE-fit

Final objective (MAE): **37.9112**

| Parameter | Bound low | Bound high | Fitted | At rail? |
| --- | ---:|---:|---:|:---:|
| `intercept_FPG` | -5.00 | +5.00 | -3.559 |  |
| `intercept_Carrboro` | -5.00 | +5.00 | -4.858 |  |
| `intercept_Glenwood` | -5.00 | +5.00 | -2.186 |  |
| `intercept_Seawell` | -5.00 | +5.00 | -4.918 |  |
| `bonus_FPG_hispanic` | +0.00 | +5.00 | +4.957 |  |
| `bonus_Glenwood_asian` | +0.00 | +5.00 | +0.042 |  |
| `bonus_Carrboro_white` | +0.00 | +5.00 | +2.208 |  |
| `bonus_Carrboro_asian` | +0.00 | +5.00 | +0.342 |  |
| `bonus_Seawell_white` | +0.00 | +5.00 | +2.456 |  |
| `bonus_Seawell_asian` | +0.00 | +5.00 | +0.379 |  |
| `w_white_optout` | +1.00 | +5.00 | +3.295 |  |
| `mu_0_4` | +0.00 | +1.00 | +0.800 |  |
| `w_income_magnet` | +0.00 | +5.00 | +0.696 |  |
| `w_income_optout` | -0.50 | +0.50 | -0.476 |  |

Per-seed DE objectives before L-BFGS-B refinement:

| Seed | Objective |
| ---:| ---:|
| 42 | 38.2178 |
| 1337 | 38.3176 |
| 2024 | 38.6717 |

Max pairwise L2 distance between DE seed parameter vectors (normalized to [0, 1] per dimension): **0.9429**. Objective spread across seeds: **0.4539**. Weakly identified? **YES**.

Hessian condition number at optimum: **9.60e+01**. Smallest absolute eigenvalue: **9.53e+03**.

### RMSE-fit

Final objective (RMSE): **52.1004**

| Parameter | Bound low | Bound high | Fitted | At rail? |
| --- | ---:|---:|---:|:---:|
| `intercept_FPG` | -5.00 | +5.00 | -3.530 |  |
| `intercept_Carrboro` | -5.00 | +5.00 | -4.859 |  |
| `intercept_Glenwood` | -5.00 | +5.00 | -2.152 |  |
| `intercept_Seawell` | -5.00 | +5.00 | -5.000 | LOW |
| `bonus_FPG_hispanic` | +0.00 | +5.00 | +5.000 | HIGH |
| `bonus_Glenwood_asian` | +0.00 | +5.00 | +0.000 | LOW |
| `bonus_Carrboro_white` | +0.00 | +5.00 | +2.761 |  |
| `bonus_Carrboro_asian` | +0.00 | +5.00 | +0.000 | LOW |
| `bonus_Seawell_white` | +0.00 | +5.00 | +3.045 |  |
| `bonus_Seawell_asian` | +0.00 | +5.00 | +0.000 | LOW |
| `w_white_optout` | +1.00 | +5.00 | +1.816 |  |
| `mu_0_4` | +0.00 | +1.00 | +1.000 | HIGH |
| `w_income_magnet` | +0.00 | +5.00 | +0.000 | LOW |
| `w_income_optout` | -0.50 | +0.50 | -0.500 | LOW |

Per-seed DE objectives before L-BFGS-B refinement:

| Seed | Objective |
| ---:| ---:|
| 42 | 52.2195 |
| 1337 | 52.2345 |
| 2024 | 52.1974 |

Max pairwise L2 distance between DE seed parameter vectors: **0.5730**. Objective spread across seeds: **0.0371**. Weakly identified? **YES**.

Hessian condition number at optimum: **1.27e+05**. Smallest absolute eigenvalue: **1.99e-03**.

## Per-school results

| School | ADM | Naive | MAE-fit | MAE residual | RMSE-fit | RMSE residual |
| --- | ---:|---:|---:|---:|---:|---:|
| Carrboro Elementary | 462 | 352 | 462 | -0 | 471 | +9 |
| Ephesus Elementary | 343 | 435 | 382 | +39 | 399 | +56 |
| Estes Hills Elementary | 324 | 307 | 249 | -75 | 264 | -60 |
| FPG Elementary | 499 | 0 | 499 | +0 | 509 | +10 |
| Glenwood Elementary | 394 | 37 | 394 | -0 | 401 | +7 |
| McDougle Elementary | 469 | 602 | 471 | +2 | 494 | +25 |
| Morris Grove Elementary | 371 | 549 | 343 | -28 | 326 | -45 |
| Northside Elementary | 335 | 940 | 503 | +168 | 447 | +112 |
| Rashkis Elementary | 367 | 442 | 312 | -55 | 291 | -76 |
| Scroggs Elementary | 366 | 398 | 316 | -50 | 320 | -46 |
| Seawell Elementary | 364 | 232 | 364 | -0 | 372 | +8 |

## Limitations

1. **Degrees of freedom.** 14 parameters against 11 school totals is under-determined at the school-total level — the model has far more knobs than observations. This is what makes identifiability weak. The fragment-level input (143 fragments × 8 races = 1,144 internal rows) gives the optimizer hundreds of rows of data, but the loss function aggregates back to the 11 per-school totals before evaluation.
2. **Identifiability is expected to be weak.** Intercepts, race bonuses, income coefficients, and the 5-9/0-4 mix all partially trade off against each other within the softmax. The multi-seed DE stability diagnostic and Hessian condition number surface this. If the MAE-fit and RMSE-fit parameter vectors differ meaningfully at similar school-level errors, that is weak identification, not two answers about district demographics.
3. **Race × age approximation.** Kids 5-9 and kids 0-4 racial composition is proxied by each fragment's parent block-group's total-population racial composition. A proper per-age-bucket race breakdown would require ACS tables B01001A–I and is a follow-up.
4. **Income imputation.** ~10 block groups carry the Census `-666666666` sentinel for `median_hh_income`. These are imputed with the district mean (z_income = 0). Any fragment inside one of these BGs contributes zero to the income signal.
5. **No out-of-sample validation.** The single data point is the 2025-26 per-school ADM vector.
6. **Model structural limits.** Even a perfect fit cannot capture things the model cannot express: cross-district transfers, private school enrollment (not included in the 10% opt-out), charter-specific draws, capacity constraints, etc.
7. **Expected-value flows.** The flows CSV reports `rescaled[f, r] * prob[f, m, r]`, which is an **expected count** — a real-valued assignment weight, not a count of individual students moved. Do not interpret a flow of 12.4 as '12.4 real students.'
8. **Northside residual.** The fragment-level calibrated softmax has 14 free parameters and still cannot fully compress Northside's residual. This is a statement about model expressiveness — the covariates we have (race, age, income at block-group level) do not explain Northside's anomaly. Candidate omitted factors: cross-district transfers, UNC-adjacent population turnover, private school enrollment, lingering 2020-vintage ACS staleness.

## Outputs

- `data/processed/naive_enrollment_allocation.csv` — per-school summary
- `data/processed/naive_enrollment_by_race.csv` — long school × race × scheme
- `data/processed/naive_enrollment_flows.csv` — long expected-value flows
- `assets/charts/naive_vs_actual_enrollment.png`
- `assets/charts/naive_calibrated_vs_actual.png`
- `assets/charts/redistribution_flows_mae_fit.png`
- `assets/charts/redistribution_flows_rmse_fit.png`
- `assets/charts/magnet_racial_composition_comparison.png`
