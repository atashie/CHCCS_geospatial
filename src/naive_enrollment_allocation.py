"""
Calibrated Enrollment Allocation Model.

Fits a softmax (logit) choice model against observed 2025-26 per-school ADM
to estimate racial preferences at the four CHCCS magnets and the white-vs-
non-white homeschool / charter opt-out ratio. The only hard facts are the
actual ADM (4,294 total across 11 schools) and that ~10% of elementary-age
residents opt out to homeschool or charter schools. Everything else — the
intercepts, race bonuses, and opt-out bias — is calibrated by numerical
optimization against the per-school residuals.

Two independent calibrations are run, one minimizing MAE and one minimizing
RMSE across the 11 schools. Results are reported side by side. If the two
fits produce meaningfully different parameter vectors at similar school-level
errors, that is evidence of weak identification, not two competing answers.

Forward model (per elementary-age kid in zone z, race r):
    U[stay]      = 0
    U[FPG]       = intercept_FPG      + bonus_FPG_hispanic * I(r==hispanic)
    U[Carrboro]  = intercept_Carrboro + bonus_Carrboro_white * I(r==white)
                                      + bonus_Carrboro_asian * I(r==asian)
                                      — only if z is NOT Carrboro's zone
    U[Glenwood]  = intercept_Glenwood + bonus_Glenwood_asian * I(r==asian)
                                      — only if z is NOT Glenwood's zone
    U[Seawell]   = intercept_Seawell  + bonus_Seawell_white * I(r==white)
                                      + bonus_Seawell_asian * I(r==asian)
                                      — only if z is NOT Seawell's zone
    prob[z, dest, r] = softmax(U) over eligible destinations

The softmax is feasible by construction (probabilities always in the simplex),
so no infeasibility penalty is needed.

Parameters (11 total, see PARAM_NAMES/PARAM_BOUNDS below):
  * 4 magnet intercepts (log-odds relative to staying at home-zone school)
  * 6 race bonus terms (>=0 — direction constrained by known district facts)
  * 1 white vs non-white opt-out ratio

Constraints respected by construction:
  * District total after rescaling = 4,294 (implicitly sets the age scalar)
  * District-wide opt-out = 10% exactly (via per-race retention derived from
    w_white_optout and the kids-weighted district white share)

Usage:
    python src/naive_enrollment_allocation.py

Outputs:
    data/processed/naive_enrollment_allocation.csv
    data/processed/naive_enrollment_by_race.csv
    data/processed/naive_enrollment_flows.csv
    data/processed/NAIVE_ENROLLMENT_ALLOCATION.md
    assets/charts/naive_vs_actual_enrollment.png
    assets/charts/naive_calibrated_vs_actual.png
    assets/charts/redistribution_flows_mae_fit.png
    assets/charts/redistribution_flows_rmse_fit.png
    assets/charts/magnet_racial_composition_comparison.png
"""

from __future__ import annotations

import subprocess
import warnings
from dataclasses import dataclass
from datetime import datetime
from pathlib import Path
from typing import Literal

import geopandas as gpd
import matplotlib.pyplot as plt
import numpy as np
import pandas as pd
from scipy.optimize import differential_evolution, minimize

warnings.filterwarnings("ignore", category=FutureWarning)

# ---------------------------------------------------------------------------
# Paths
# ---------------------------------------------------------------------------
PROJECT_ROOT = Path(__file__).resolve().parent.parent
DATA_PROCESSED = PROJECT_ROOT / "data" / "processed"
DATA_CACHE = PROJECT_ROOT / "data" / "cache"
DATA_RAW = PROJECT_ROOT / "data" / "raw"
ASSETS_CHARTS = PROJECT_ROOT / "assets" / "charts"

# Block-group demographics from ACS (121 block groups, raw counts + income + geometry).
# This is the finest granularity available in the repo for race × age × income.
ACS_BG_GPKG = DATA_CACHE / "census_acs_blockgroups.gpkg"
# Residential parcels for dasymetric splitting of block groups across attendance zones.
PARCELS_GPKG = DATA_RAW / "properties" / "combined_data_polys.gpkg"
ADM_CSV = DATA_PROCESSED / "adm_forecast_2025_to_2035.csv"

OUT_ALLOC_CSV = DATA_PROCESSED / "naive_enrollment_allocation.csv"
OUT_BY_RACE_CSV = DATA_PROCESSED / "naive_enrollment_by_race.csv"
OUT_FLOWS_CSV = DATA_PROCESSED / "naive_enrollment_flows.csv"
OUT_DOC = DATA_PROCESSED / "NAIVE_ENROLLMENT_ALLOCATION.md"

CHART_NAIVE_VS_ACTUAL = ASSETS_CHARTS / "naive_vs_actual_enrollment.png"
CHART_CALIB_COMPARISON = ASSETS_CHARTS / "naive_calibrated_vs_actual.png"
CHART_FLOWS_MAE = ASSETS_CHARTS / "redistribution_flows_mae_fit.png"
CHART_FLOWS_RMSE = ASSETS_CHARTS / "redistribution_flows_rmse_fit.png"
CHART_RACE_COMP = ASSETS_CHARTS / "magnet_racial_composition_comparison.png"

# ---------------------------------------------------------------------------
# Constants — district, schools, and name crosswalk
# ---------------------------------------------------------------------------
ADM_YEAR = "2025-26"
DISTRICT_ADM = 4294
HOMESCHOOL_CHARTER_FRAC = 0.10  # exact, user-confirmed
# Post-opt-out target that the combined 5-9 + 0-4 pool must equal before
# the 10% retention step. Derived from 4,294 / 0.9.
TARGET_AFTER_OPTOUT = DISTRICT_ADM / (1.0 - HOMESCHOOL_CHARTER_FRAC)

# Canonical school-name crosswalk between the ADM CSV and the
# attendance-zone / socioeconomic pipeline.
ADM_TO_CANONICAL = {
    "Carrboro Elementary": "Carrboro Elementary",
    "Ephesus Elementary": "Ephesus Elementary",
    "Estes Hills Elementary": "Estes Hills Elementary",
    "FPG Elementary": "FPG Elementary",
    "Glenwood Elementary": "Glenwood Elementary",
    "McDougle Elementary": "McDougle Elementary",
    "Morris Grove Elementary": "Morris Grove Elementary",
    "Northside Elementary": "Northside Elementary",
    "Rashkis Elementary": "Rashkis Elementary",
    "Scroggs Elementary": "Scroggs Elementary",
    "Seawell Elementary": "Seawell Elementary",
}
ZONE_TO_CANONICAL = {
    "Carrboro Elementary": "Carrboro Elementary",
    "Ephesus Elementary": "Ephesus Elementary",
    "Estes Hills Elementary": "Estes Hills Elementary",
    "Frank Porter Graham Bilingue": "FPG Elementary",
    "Glenwood Elementary": "Glenwood Elementary",
    "McDougle Elementary": "McDougle Elementary",
    "Morris Grove Elementary": "Morris Grove Elementary",
    "Northside Elementary": "Northside Elementary",
    "Rashkis Elementary": "Rashkis Elementary",
    "Scroggs Elementary": "Scroggs Elementary",
    "Seawell Elementary": "Seawell Elementary",
}

ALL_SCHOOLS = [
    "Carrboro Elementary",
    "Ephesus Elementary",
    "Estes Hills Elementary",
    "FPG Elementary",
    "Glenwood Elementary",
    "McDougle Elementary",
    "Morris Grove Elementary",
    "Northside Elementary",
    "Rashkis Elementary",
    "Scroggs Elementary",
    "Seawell Elementary",
]

# SOURCE_ZONES are the 10 residential attendance zones from CHCCS.shp
# (FPG has no traditional zone). Order matters — used for vectorized
# forward-model indexing.
SOURCE_ZONES = [s for s in ALL_SCHOOLS if s != "FPG Elementary"]

MAGNETS = [
    "FPG Elementary",
    "Carrboro Elementary",
    "Glenwood Elementary",
    "Seawell Elementary",
]
NON_MAGNETS = [s for s in ALL_SCHOOLS if s not in MAGNETS]

# Race categories (matching census_school_demographics.csv column names)
RACE_COLS = [
    "white_nh",
    "black_nh",
    "asian_nh",
    "hispanic",
    "aian_nh",
    "nhpi_nh",
    "other_nh",
    "two_plus_nh",
]
RACE_IDX = {r: i for i, r in enumerate(RACE_COLS)}
MAGNET_IDX = {m: i for i, m in enumerate(MAGNETS)}

# ---------------------------------------------------------------------------
# Parameter spec
# ---------------------------------------------------------------------------
PARAM_NAMES: list[str] = [
    "intercept_FPG",
    "intercept_Carrboro",
    "intercept_Glenwood",
    "intercept_Seawell",
    "bonus_FPG_hispanic",
    "bonus_Glenwood_asian",
    "bonus_Carrboro_white",
    "bonus_Carrboro_asian",
    "bonus_Seawell_white",
    "bonus_Seawell_asian",
    "w_white_optout",
    # mu_0_4: fraction of the effective elementary pool drawn from the ACS
    # 0-4 bucket instead of the 5-9 bucket. Reflects how much of the pre-
    # school cohort has aged into elementary since the ACS snapshot.
    # mu_0_4 = 0 means use only the 5-9 bucket (rescaled by the pinned
    # scale factor). mu_0_4 = 1 means use only the 0-4 bucket.
    # The district-total constraint is enforced by construction: the
    # forward model always computes alpha and beta so that
    # alpha * sum(kids_5_9) + beta * sum(kids_0_4) = 4294 / 0.9.
    "mu_0_4",
    # w_income_magnet: softmax utility bonus added to Carrboro and Seawell
    # for every standardized unit of zone median household income.
    # Direction: wealthier zones more likely to send kids to these two
    # partial magnets (>= 0).
    "w_income_magnet",
    # w_income_optout: per-unit-of-standardized-income multiplier applied
    # inside the exponent of the homeschool/charter opt-out rate.
    # Positive values mean wealthier zones opt out more (original
    # hypothesis); negative values mean wealthier zones opt out LESS
    # (which the 2025-26 residual pattern actually supports). The sign
    # is not direction-constrained — the optimizer picks it. The base
    # opt-out rate is solved for on every forward call so the district-
    # wide opt-out equals 10% exactly regardless of w_income_optout.
    "w_income_optout",
]
PARAM_BOUNDS: list[tuple[float, float]] = [
    (-5.0, 5.0),  # intercept_FPG
    (-5.0, 5.0),  # intercept_Carrboro
    (-5.0, 5.0),  # intercept_Glenwood
    (-5.0, 5.0),  # intercept_Seawell
    (0.0, 5.0),   # bonus_FPG_hispanic
    (0.0, 5.0),   # bonus_Glenwood_asian
    (0.0, 5.0),   # bonus_Carrboro_white
    (0.0, 5.0),   # bonus_Carrboro_asian
    (0.0, 5.0),   # bonus_Seawell_white
    (0.0, 5.0),   # bonus_Seawell_asian
    (1.0, 5.0),   # w_white_optout
    (0.0, 1.0),   # mu_0_4 — convex mix weight on 0-4 bucket
    (0.0, 5.0),   # w_income_magnet — zone-income boost for Carrboro/Seawell
    (-0.5, 0.5),  # w_income_optout — log-linear income effect on opt-out rate
                  # Sign is NOT constrained — negative values mean wealthier
                  # zones opt out LESS (which the data for this district
                  # actually supports, contrary to the stated hypothesis).
]
N_PARAMS = len(PARAM_NAMES)
PARAM_IDX = {n: i for i, n in enumerate(PARAM_NAMES)}

# Mapping from (magnet, race) to the parameter name that supplies the bonus,
# if any. Any (m, r) not in this dict uses a zero bonus.
PARAM_RACE_BONUS: dict[tuple[str, str], str] = {
    ("FPG Elementary", "hispanic"): "bonus_FPG_hispanic",
    ("Glenwood Elementary", "asian_nh"): "bonus_Glenwood_asian",
    ("Carrboro Elementary", "white_nh"): "bonus_Carrboro_white",
    ("Carrboro Elementary", "asian_nh"): "bonus_Carrboro_asian",
    ("Seawell Elementary", "white_nh"): "bonus_Seawell_white",
    ("Seawell Elementary", "asian_nh"): "bonus_Seawell_asian",
}

# DE + refinement settings
DE_SEEDS = (42, 1337, 2024)
DE_KWARGS = dict(
    tol=1e-9,
    maxiter=500,
    popsize=25,
    workers=1,
    polish=False,
    init="sobol",
    mutation=(0.5, 1.5),
    recombination=0.7,
)
LBFGS_KWARGS = dict(method="L-BFGS-B", options={"ftol": 1e-10, "maxiter": 1000})


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _progress(msg: str) -> None:
    print(f"  {msg}")


def _git_sha() -> str:
    try:
        out = subprocess.check_output(
            ["git", "rev-parse", "--short", "HEAD"],
            cwd=PROJECT_ROOT,
            stderr=subprocess.DEVNULL,
        )
        return out.decode().strip()
    except Exception:
        return "unknown"


# ---------------------------------------------------------------------------
# Data loading
# ---------------------------------------------------------------------------

def load_inputs() -> tuple[pd.DataFrame, dict]:
    """Return (fragments_df, adm_dict).

    `fragments_df` has one row per (block group × attendance zone) fragment
    — the dasymetric split of ACS block-group demographics onto the CHCCS
    attendance zones, weighted by residential parcel area. Each row carries
    the parent BG's race counts, age-bucket counts, and median household
    income, plus the `school` column naming the zone that contains it and
    a `weight` column giving the fraction of the BG's population in this
    fragment. Fragments with negligible weight are dropped.

    `adm` maps canonical school name to 2025-26 ADM (int).
    """
    # Lazy imports so the heavy geopandas / school_socioeconomic_analysis
    # stack doesn't run on every module import. When running as a script,
    # the sibling module is importable directly by name.
    import sys
    sys.path.insert(0, str(Path(__file__).resolve().parent))
    from school_socioeconomic_analysis import (  # type: ignore
        load_attendance_zones,
        intersect_zones_with_blockgroups,
    )

    _progress(f"Loading attendance zones from CHCCS shapefile")
    zones = load_attendance_zones()
    if zones is None:
        raise FileNotFoundError("Failed to load CHCCS attendance zones")
    # Normalize zone school names to the ADM-canonical form
    # (load_attendance_zones returns "Frank Porter Graham Bilingue" etc.)
    zones["school"] = zones["school"].map(ZONE_TO_CANONICAL)
    zones = zones[zones["school"].notna()].reset_index(drop=True)

    _progress(f"Loading ACS block groups from {ACS_BG_GPKG.name}")
    bg = gpd.read_file(ACS_BG_GPKG)

    _progress(f"Loading residential parcels from {PARCELS_GPKG.name}")
    parcels = gpd.read_file(PARCELS_GPKG)

    _progress("Running dasymetric intersection (block groups × zones)")
    fragments = intersect_zones_with_blockgroups(zones, bg, parcels)
    _progress(f"  Created {len(fragments)} raw fragments")

    # Drop fragments with negligible weight (these produce zero kids and
    # just add noise to the forward model).
    fragments = fragments[fragments["weight"] > 1e-6].reset_index(drop=True)
    _progress(f"  Kept {len(fragments)} fragments after weight filter")

    _progress(f"Loading {ADM_CSV.name}")
    adm_raw = pd.read_csv(ADM_CSV)
    adm_raw = adm_raw[adm_raw["School"] != "Elementary Total"].copy()
    adm_raw["school"] = adm_raw["School"].map(ADM_TO_CANONICAL)
    if adm_raw["school"].isna().any():
        raise ValueError(
            "Unmapped school in ADM CSV: "
            f"{adm_raw.loc[adm_raw['school'].isna(), 'School'].tolist()}"
        )
    adm = dict(zip(adm_raw["school"], adm_raw[ADM_YEAR].astype(int)))

    adm_set = set(adm.keys())
    frag_schools = set(fragments["school"])
    expected_all = set(ALL_SCHOOLS)
    expected_frag = set(SOURCE_ZONES)

    if adm_set != expected_all:
        raise ValueError(
            f"ADM crosswalk mismatch — expected {expected_all}, got {adm_set}"
        )
    if frag_schools != expected_frag:
        raise ValueError(
            f"Fragment home-school crosswalk mismatch — expected {expected_frag}, got {frag_schools}"
        )

    total_adm = sum(adm.values())
    if total_adm != DISTRICT_ADM:
        raise ValueError(
            f"ADM row sum {total_adm} != constant DISTRICT_ADM {DISTRICT_ADM}; "
            "one of the two is stale."
        )

    _progress(
        f"Loaded {len(fragments)} fragments across {fragments['school'].nunique()} "
        f"residential zones and {len(adm)} ADM rows (total ADM {total_adm:,})"
    )
    return fragments, adm


# ---------------------------------------------------------------------------
# Precomputation of the static parts of the forward model
# ---------------------------------------------------------------------------

@dataclass
class StaticModel:
    """Everything that's constant across forward-model evaluations.

    This version operates at the (block-group × zone) fragment level. Each
    fragment row carries the parent block group's race/age/income attributes
    scaled by the fragment's dasymetric weight (= fraction of the BG's
    residential parcel area lying within the fragment's zone). A single
    block group that spans multiple attendance zones becomes multiple
    fragments — each with its own home-zone school but the same racial
    composition and the same median household income.
    """
    raw_by_race_5_9: np.ndarray        # (n_rows, n_races)
    raw_by_race_0_4: np.ndarray        # (n_rows, n_races)
    district_kids_5_9_total: float
    district_kids_0_4_total: float
    eligibility: np.ndarray            # (n_rows, n_magnets) bool
    white_idx: int
    # Per-fragment home school indices
    fragment_home_school_idx: np.ndarray   # (n_rows,) index into ALL_SCHOOLS
    fragment_source_zone_idx: np.ndarray   # (n_rows,) index into SOURCE_ZONES
    # Income (standardized over the unique block groups using a valid-only filter
    # so the -666666666 Census sentinel doesn't contaminate mean/std).
    median_income_per_fragment: np.ndarray  # (n_rows,) raw, with imputed values for invalid BGs
    z_income: np.ndarray                    # (n_rows,) standardized, 0 for imputed rows
    income_mean: float
    income_std: float
    income_imputed_mask: np.ndarray         # (n_rows,) bool — True if BG had sentinel income
    # Indicator mask for magnets that receive the income bonus
    # (w_income_magnet applies only to Carrboro and Seawell)
    income_magnet_mask: np.ndarray          # (n_magnets,) bool
    # Per-school aggregates for output tables (computed once, not used in the
    # forward model).
    raw_kids_5_9_per_school: np.ndarray     # (n_schools,) — sum of weighted BG counts
    raw_kids_0_4_per_school: np.ndarray     # (n_schools,)


def build_static_model(fragments: pd.DataFrame) -> StaticModel:
    """Precompute the parts of the forward model that don't depend on params.

    `fragments` is the dataframe returned by load_inputs() — each row is a
    (block group × attendance zone) fragment with raw BG counts and a
    `weight` column. We multiply every count by the weight and treat each
    fragment as an independent "row" in the forward model.
    """
    n_rows = len(fragments)

    weight = fragments["weight"].to_numpy(dtype=float)
    raw_5_9_bg = (fragments["male_5_9"] + fragments["female_5_9"]).to_numpy(dtype=float)
    raw_0_4_bg = (fragments["male_under_5"] + fragments["female_under_5"]).to_numpy(dtype=float)
    race_total = fragments["race_total"].to_numpy(dtype=float)
    race_counts = fragments[RACE_COLS].to_numpy(dtype=float)

    # Weighted kids per fragment (dasymetric)
    raw_5_9 = raw_5_9_bg * weight
    raw_0_4 = raw_0_4_bg * weight

    # race_share is the parent BG's racial composition (same across all
    # fragments of that BG). Use total-pop race share as the age-race proxy.
    with np.errstate(invalid="ignore", divide="ignore"):
        race_share = np.where(
            race_total[:, None] > 0, race_counts / race_total[:, None], 0.0
        )
    raw_by_race_5_9 = raw_5_9[:, None] * race_share
    raw_by_race_0_4 = raw_0_4[:, None] * race_share

    district_kids_5_9_total = float(raw_by_race_5_9.sum())
    district_kids_0_4_total = float(raw_by_race_0_4.sum())
    white_idx = RACE_IDX["white_nh"]

    # Per-fragment home school indices
    all_school_idx = {s: i for i, s in enumerate(ALL_SCHOOLS)}
    source_zone_idx = {s: i for i, s in enumerate(SOURCE_ZONES)}
    fragment_home_school_idx = fragments["school"].map(all_school_idx).to_numpy(dtype=int)
    fragment_source_zone_idx = fragments["school"].map(source_zone_idx).to_numpy(dtype=int)

    # Income: standardize over the unique block groups (not fragments),
    # using a valid-only filter to drop the -666666666 Census sentinel.
    raw_income = fragments["median_hh_income"].to_numpy(dtype=float)
    valid_mask = raw_income > 0  # -666666666 sentinel AND zero-household BGs
    # Unique BGs with valid income (use GEOID for uniqueness)
    bg_income = fragments[["GEOID", "median_hh_income"]].drop_duplicates("GEOID")
    valid_bg_income = bg_income.loc[bg_income["median_hh_income"] > 0, "median_hh_income"]
    if len(valid_bg_income) == 0:
        raise ValueError("No valid median household income values found")
    income_mean = float(valid_bg_income.mean())
    income_std = float(valid_bg_income.std(ddof=0))
    if income_std <= 0:
        income_std = 1.0

    # Impute invalid fragment incomes with the mean (z_income = 0)
    imputed_income = np.where(valid_mask, raw_income, income_mean)
    z_income = (imputed_income - income_mean) / income_std
    z_income[~valid_mask] = 0.0  # explicit zero for imputed rows

    # Eligibility: magnet m is eligible from fragment f unless m == fragment's
    # home school. Glenwood-zone fragments cannot be pulled back into Glenwood,
    # etc.
    eligibility = np.ones((n_rows, len(MAGNETS)), dtype=bool)
    for f_idx in range(n_rows):
        home = fragments.iloc[f_idx]["school"]
        if home in MAGNET_IDX:
            eligibility[f_idx, MAGNET_IDX[home]] = False

    # Income bonus applies only to Carrboro and Seawell
    income_magnet_mask = np.zeros(len(MAGNETS), dtype=bool)
    income_magnet_mask[MAGNET_IDX["Carrboro Elementary"]] = True
    income_magnet_mask[MAGNET_IDX["Seawell Elementary"]] = True

    # Per-school aggregates (for display, not used by the forward model)
    raw_kids_5_9_per_school = np.zeros(len(ALL_SCHOOLS))
    raw_kids_0_4_per_school = np.zeros(len(ALL_SCHOOLS))
    for f_idx in range(n_rows):
        s_idx = fragment_home_school_idx[f_idx]
        raw_kids_5_9_per_school[s_idx] += raw_5_9[f_idx]
        raw_kids_0_4_per_school[s_idx] += raw_0_4[f_idx]

    _progress(
        f"Fragment-level raw district kids: 5-9 = {district_kids_5_9_total:,.1f}, "
        f"0-4 = {district_kids_0_4_total:,.1f}; "
        f"target after opt-out = {TARGET_AFTER_OPTOUT:,.1f}"
    )
    valid_count = int(valid_mask.sum())
    _progress(
        f"Income (unique BGs): mean ${income_mean:,.0f}, std ${income_std:,.0f}, "
        f"range ${valid_bg_income.min():,.0f} to ${valid_bg_income.max():,.0f}; "
        f"{n_rows - valid_count} of {n_rows} fragments had sentinel income (imputed)"
    )

    return StaticModel(
        raw_by_race_5_9=raw_by_race_5_9,
        raw_by_race_0_4=raw_by_race_0_4,
        district_kids_5_9_total=district_kids_5_9_total,
        district_kids_0_4_total=district_kids_0_4_total,
        eligibility=eligibility,
        white_idx=white_idx,
        fragment_home_school_idx=fragment_home_school_idx,
        fragment_source_zone_idx=fragment_source_zone_idx,
        median_income_per_fragment=imputed_income,
        z_income=z_income,
        income_mean=income_mean,
        income_std=income_std,
        income_imputed_mask=~valid_mask,
        income_magnet_mask=income_magnet_mask,
        raw_kids_5_9_per_school=raw_kids_5_9_per_school,
        raw_kids_0_4_per_school=raw_kids_0_4_per_school,
    )


# ---------------------------------------------------------------------------
# Forward model
# ---------------------------------------------------------------------------

def _retention_from_optout_ratio(
    w_white_optout: float, f_white_kids: float
) -> np.ndarray:
    """Return per-race retention vector (1 - per-race opt-out rate).

    Legacy helper kept for the naive baseline, which uses uniform income
    (no per-zone income adjustment). Solves:
        w * f_white + n * (1 - f_white) = HOMESCHOOL_CHARTER_FRAC
        w = w_white_optout * n
    so n = HOMESCHOOL_CHARTER_FRAC / (w_white_optout * f_white + (1 - f_white)).
    """
    denom = w_white_optout * f_white_kids + (1.0 - f_white_kids)
    n = HOMESCHOOL_CHARTER_FRAC / denom
    w = w_white_optout * n
    retention = np.full(len(RACE_COLS), 1.0 - n)
    retention[RACE_IDX["white_nh"]] = 1.0 - w
    return retention


def _retention_with_income(
    combined: np.ndarray,
    w_white_optout: float,
    w_income_optout: float,
    z_income: np.ndarray,
    white_idx: int,
) -> tuple[np.ndarray, bool]:
    """Return per-(zone, race) retention matrix AND a feasibility flag.

    Opt-out rate form:
        opt_out_rate[z, r] = base * race_mult[r] * income_mult[z]
        race_mult[white]   = w_white_optout
        race_mult[other]   = 1
        income_mult[z]     = exp(w_income_optout * z_income[z])

    `base` is solved so that the weighted district opt-out rate equals
    HOMESCHOOL_CHARTER_FRAC exactly:

        sum_{z, r} combined[z, r] * opt_out_rate[z, r] = 0.10 * sum(combined)

    Returns:
        retention: (n_zones, n_races) with values in (0, 1)
        feasible: True if every opt_out_rate is in [0, 1). If False, the
            caller should treat this parameter vector as infeasible.
    """
    n_races = combined.shape[1]
    total = float(combined.sum())
    race_mult = np.ones(n_races)
    race_mult[white_idx] = w_white_optout
    income_mult = np.exp(w_income_optout * z_income)  # (n_zones,)

    # base = 0.10 * total / sum_{z, r} combined[z, r] * race_mult[r] * income_mult[z]
    weighted_kids = combined * race_mult[None, :] * income_mult[:, None]
    denom = float(weighted_kids.sum())
    if denom <= 0:
        return np.ones_like(combined), False
    base = HOMESCHOOL_CHARTER_FRAC * total / denom

    opt_out_rate = base * race_mult[None, :] * income_mult[:, None]  # (n_zones, n_races)
    feasible = bool(np.all(opt_out_rate < 1.0) and np.all(opt_out_rate >= 0.0))
    retention = 1.0 - opt_out_rate
    return retention, feasible


def forward_model(
    params: np.ndarray,
    static: StaticModel,
) -> tuple[np.ndarray, np.ndarray, np.ndarray, bool]:
    """Vectorized softmax forward model.

    Returns:
        post_by_school_race: (n_schools, n_races) predicted counts
        flows: (n_magnets, n_zones, n_races) expected-value inflows from
            each (zone, race) cell to each magnet
        rescaled: (n_zones, n_races) post-opt-out pool (== post-redistribution
            input, useful for the naive baseline and racial diagnostics).
        feasible: False if any opt-out rate exceeded 1 (parameter
            combination is implausible). Callers should penalize.
    """
    # Unpack params
    intercepts = params[:4]  # order: FPG, Carrboro, Glenwood, Seawell
    w_white_optout = params[PARAM_IDX["w_white_optout"]]
    mu = float(params[PARAM_IDX["mu_0_4"]])
    w_income_magnet = float(params[PARAM_IDX["w_income_magnet"]])
    w_income_optout = float(params[PARAM_IDX["w_income_optout"]])

    bonuses = np.zeros((len(MAGNETS), len(RACE_COLS)))
    for (m, r), pname in PARAM_RACE_BONUS.items():
        bonuses[MAGNET_IDX[m], RACE_IDX[r]] = params[PARAM_IDX[pname]]

    # 1. Combine 5-9 and 0-4 buckets so the pre-opt-out district total
    #    equals TARGET_AFTER_OPTOUT exactly. alpha and beta are derived
    #    from mu_0_4 and the static totals:
    #
    #        alpha * K_5_9 = (1 - mu) * TARGET
    #        beta  * K_0_4 = mu       * TARGET
    #
    #    so alpha * raw_5_9 + beta * raw_0_4 sums to TARGET regardless of mu.
    alpha = (1.0 - mu) * TARGET_AFTER_OPTOUT / static.district_kids_5_9_total
    beta = mu * TARGET_AFTER_OPTOUT / static.district_kids_0_4_total
    combined = alpha * static.raw_by_race_5_9 + beta * static.raw_by_race_0_4
    # Note: combined.sum() == TARGET_AFTER_OPTOUT by construction

    # 2. Per-(zone, race) retention, parameterized by w_white_optout and
    #    w_income_optout. Per-race bias + per-zone income bias. The base
    #    rate is solved so the district-wide opt-out equals exactly 10%
    #    regardless of the other parameters.
    retention, feasible = _retention_with_income(
        combined=combined,
        w_white_optout=w_white_optout,
        w_income_optout=w_income_optout,
        z_income=static.z_income,
        white_idx=static.white_idx,
    )
    # If any opt-out rate exceeds 1, mark the call infeasible by flagging
    # it — we still compute something rather than crashing, but the
    # objective function adds a penalty when feasible is False.
    rescaled = combined * retention  # (n_zones, n_races)

    # 4. Utilities per (row, race, dest) where dest ∈ {stay, FPG, Carr, Glen, Sea}.
    # "Row" is now a (block-group × zone) fragment, not a single zone.
    n_rows = combined.shape[0]
    n_races = len(RACE_COLS)
    n_dest = 1 + len(MAGNETS)
    U = np.zeros((n_rows, n_races, n_dest))
    # U[:, :, 0] = 0 (stay)
    income_bonus_per_zone = w_income_magnet * static.z_income  # (n_zones,)
    for m_idx in range(len(MAGNETS)):
        # U[z, r, 1+m_idx] = intercept[m] + bonus[m, r]
        U[:, :, 1 + m_idx] = intercepts[m_idx] + bonuses[m_idx, :][None, :]
        # Add per-zone income bonus for Carrboro and Seawell only
        if static.income_magnet_mask[m_idx]:
            U[:, :, 1 + m_idx] += income_bonus_per_zone[:, None]
        # Mask out ineligible zones (own-zone magnet) with -inf
        ineligible_zones = ~static.eligibility[:, m_idx]
        U[ineligible_zones, :, 1 + m_idx] = -np.inf

    # 5. Softmax over destinations (numerically stable)
    U_max = U.max(axis=-1, keepdims=True)
    # U_max can be -inf only if every destination is -inf, which can't happen
    # (stay is always eligible with U=0).
    exp_U = np.exp(U - U_max)
    exp_U_sum = exp_U.sum(axis=-1, keepdims=True)
    probs = exp_U / exp_U_sum  # (n_zones, n_races, n_dest)

    # 6. Aggregate to per-school totals
    # post_by_school_race[s, r] — s ranges over ALL_SCHOOLS
    post = np.zeros((len(ALL_SCHOOLS), n_races))
    school_idx = {s: i for i, s in enumerate(ALL_SCHOOLS)}

    # Stay contribution: rescaled * probs[:, :, 0] goes to each fragment's
    # home school. np.add.at accumulates into the correct row of `post`
    # for each fragment.
    stay_contrib = rescaled * probs[:, :, 0]  # (n_rows, n_races)
    np.add.at(post, static.fragment_home_school_idx, stay_contrib)

    # Magnet contributions: rescaled * probs[:, :, 1+m_idx] -> magnet m.
    # Also aggregate per-fragment flows back to source-zone level (10 source
    # zones, same shape as the old output).
    n_source_zones = len(SOURCE_ZONES)
    flows = np.zeros((len(MAGNETS), n_source_zones, n_races))
    for m_idx, m in enumerate(MAGNETS):
        contrib = rescaled * probs[:, :, 1 + m_idx]  # (n_rows, n_races)
        # Aggregate to (n_source_zones, n_races) by summing fragments that
        # share a source zone.
        np.add.at(flows[m_idx], static.fragment_source_zone_idx, contrib)
        post[school_idx[m]] += contrib.sum(axis=0)  # total magnet pull

    return post, flows, rescaled, feasible


def forward_totals(
    params: np.ndarray, static: StaticModel
) -> tuple[np.ndarray, bool]:
    """Return predicted per-school totals as a 1-D array + feasibility flag."""
    post, _, _, feasible = forward_model(params, static)
    return post.sum(axis=1), feasible


# ---------------------------------------------------------------------------
# Objective + calibration
# ---------------------------------------------------------------------------

Metric = Literal["mae", "rmse"]


INFEASIBLE_PENALTY = 1e5


def compute_objective(
    params: np.ndarray,
    static: StaticModel,
    actual_adm_vec: np.ndarray,
    metric: Metric,
) -> float:
    """Per-school MAE or RMSE between forward-model totals and actual ADM.

    If the opt-out computation is infeasible (rates would exceed 1 in some
    (zone, race) cell given the parameter combination), returns a large
    penalty so the optimizer avoids that region.
    """
    totals, feasible = forward_totals(params, static)
    err = totals - actual_adm_vec
    if metric == "mae":
        base = float(np.mean(np.abs(err)))
    else:
        base = float(np.sqrt(np.mean(err ** 2)))
    if not feasible:
        return INFEASIBLE_PENALTY + base
    return base


@dataclass
class CalibResult:
    params: np.ndarray
    objective: float
    seed_results: list[tuple[int, np.ndarray, float]]  # (seed, params, obj) per DE seed
    lbfgs_success: bool
    stability: dict                     # seed-stability diagnostics
    hessian_info: dict                  # Hessian condition + correlation matrix
    metric: Metric


def calibrate(
    static: StaticModel,
    actual_adm_vec: np.ndarray,
    metric: Metric,
) -> CalibResult:
    """Multi-seed DE + L-BFGS-B refinement for a single objective metric."""
    _progress(f"Calibrating {metric.upper()}-fit — {len(DE_SEEDS)}-seed DE + L-BFGS-B")

    def obj(x: np.ndarray) -> float:
        return compute_objective(x, static, actual_adm_vec, metric)

    seed_results: list[tuple[int, np.ndarray, float]] = []
    for seed in DE_SEEDS:
        de = differential_evolution(
            obj,
            bounds=PARAM_BOUNDS,
            seed=seed,
            **DE_KWARGS,
        )
        seed_results.append((seed, de.x.copy(), float(de.fun)))
        _progress(
            f"  seed {seed}: objective = {de.fun:.4f}, nit = {de.nit}, "
            f"success = {de.success}"
        )

    # Pick the best DE result
    best_seed, best_x, best_obj = min(seed_results, key=lambda t: t[2])
    _progress(f"  best DE seed = {best_seed}, objective = {best_obj:.4f}")

    # Local refinement
    lbfgs = minimize(obj, x0=best_x, bounds=PARAM_BOUNDS, **LBFGS_KWARGS)
    final_x = lbfgs.x.copy()
    final_obj = float(lbfgs.fun)
    _progress(
        f"  L-BFGS-B refinement: objective {best_obj:.4f} -> {final_obj:.4f} "
        f"(success = {lbfgs.success}, nit = {lbfgs.nit})"
    )

    # Stability diagnostics (L2 distances across DE seeds, in normalized bounds)
    bounds_arr = np.array(PARAM_BOUNDS)
    bound_range = bounds_arr[:, 1] - bounds_arr[:, 0]
    normalized_seed_x = np.stack(
        [(x - bounds_arr[:, 0]) / bound_range for _, x, _ in seed_results]
    )
    pairwise_l2 = []
    for i in range(len(seed_results)):
        for j in range(i + 1, len(seed_results)):
            d = float(np.linalg.norm(normalized_seed_x[i] - normalized_seed_x[j]))
            pairwise_l2.append(d)
    objective_spread = max(r[2] for r in seed_results) - min(r[2] for r in seed_results)

    stability = {
        "pairwise_l2_normalized": pairwise_l2,
        "max_pairwise_l2": max(pairwise_l2) if pairwise_l2 else 0.0,
        "objective_spread": objective_spread,
        "weakly_identified": (
            (max(pairwise_l2) if pairwise_l2 else 0.0) > 0.15
            or objective_spread > max(0.01 * final_obj, 1e-6)
        ),
    }

    # Hessian diagnostics at the optimum (finite differences)
    hessian_info = compute_hessian_diagnostics(obj, final_x)

    return CalibResult(
        params=final_x,
        objective=final_obj,
        seed_results=seed_results,
        lbfgs_success=bool(lbfgs.success),
        stability=stability,
        hessian_info=hessian_info,
        metric=metric,
    )


def compute_hessian_diagnostics(obj_fn, x: np.ndarray, eps: float = 1e-4) -> dict:
    """Finite-difference Hessian at x; return condition number and correlation."""
    n = len(x)
    H = np.zeros((n, n))
    f0 = obj_fn(x)
    for i in range(n):
        xi_plus = x.copy()
        xi_plus[i] += eps
        xi_minus = x.copy()
        xi_minus[i] -= eps
        H[i, i] = (obj_fn(xi_plus) - 2 * f0 + obj_fn(xi_minus)) / (eps ** 2)
    for i in range(n):
        for j in range(i + 1, n):
            xpp = x.copy()
            xpp[i] += eps
            xpp[j] += eps
            xpm = x.copy()
            xpm[i] += eps
            xpm[j] -= eps
            xmp = x.copy()
            xmp[i] -= eps
            xmp[j] += eps
            xmm = x.copy()
            xmm[i] -= eps
            xmm[j] -= eps
            val = (obj_fn(xpp) - obj_fn(xpm) - obj_fn(xmp) + obj_fn(xmm)) / (4 * eps ** 2)
            H[i, j] = val
            H[j, i] = val

    # Symmetrize (in case of FD noise)
    H_sym = 0.5 * (H + H.T)
    try:
        eigenvalues = np.linalg.eigvalsh(H_sym)
    except np.linalg.LinAlgError:
        eigenvalues = np.full(n, np.nan)

    abs_eig = np.abs(eigenvalues)
    condition = float(abs_eig.max() / abs_eig.min()) if abs_eig.min() > 0 else float("inf")

    # Covariance approximation = H^{-1} (positive definite required for
    # meaningful correlation matrix)
    try:
        cov = np.linalg.pinv(H_sym)
        std = np.sqrt(np.abs(np.diag(cov)))
        # Correlation matrix
        with np.errstate(invalid="ignore", divide="ignore"):
            corr = cov / np.where(std[:, None] * std[None, :] > 0,
                                   std[:, None] * std[None, :],
                                   np.nan)
        corr = np.clip(np.nan_to_num(corr, nan=0.0), -1.0, 1.0)
    except np.linalg.LinAlgError:
        corr = np.zeros((n, n))

    return {
        "hessian": H_sym,
        "eigenvalues": eigenvalues,
        "condition_number": condition,
        "correlation_matrix": corr,
        "smallest_abs_eigenvalue": float(abs_eig.min()),
        "largest_abs_eigenvalue": float(abs_eig.max()),
    }


# ---------------------------------------------------------------------------
# Output assembly
# ---------------------------------------------------------------------------

def naive_baseline(static: StaticModel) -> tuple[np.ndarray, np.ndarray]:
    """Return per-school naive enrollment (strict residency) + per-(row, race)
    retained pool.

    The naive baseline uses race-neutral opt-out (w_white_optout = 1.0), no
    income effect (w_income_optout = 0), no 0-4 mixing (mu_0_4 = 0 → kids-5-9
    only rescaled to district total), and assigns every kid to the zone
    school that contains its fragment. FPG has no residential zone so FPG's
    naive enrollment is zero by construction.
    """
    alpha = TARGET_AFTER_OPTOUT / static.district_kids_5_9_total
    combined = alpha * static.raw_by_race_5_9

    retention, feasible = _retention_with_income(
        combined=combined,
        w_white_optout=1.0,
        w_income_optout=0.0,
        z_income=static.z_income,
        white_idx=static.white_idx,
    )
    assert feasible, "naive baseline must be feasible"
    rescaled = combined * retention

    totals = np.zeros(len(ALL_SCHOOLS))
    row_totals = rescaled.sum(axis=1)  # (n_rows,)
    np.add.at(totals, static.fragment_home_school_idx, row_totals)
    # totals[FPG] stays at 0
    return totals, rescaled


def assemble_school_df(
    actual_adm_vec: np.ndarray,
    naive_totals: np.ndarray,
    mae_totals: np.ndarray,
    rmse_totals: np.ndarray,
    raw_kids_5_9_per_school: np.ndarray,
    raw_kids_0_4_per_school: np.ndarray,
    kept_per_school: np.ndarray,
) -> pd.DataFrame:
    """Per-school summary row for the output CSV.

    All arrays are already indexed by ALL_SCHOOLS position — built from
    the static model's per-school aggregates.
    """
    rows = []
    for i, s in enumerate(ALL_SCHOOLS):
        rows.append({
            "school": s,
            "actual_adm_2025_26": int(actual_adm_vec[i]),
            "raw_kids_5_9": float(raw_kids_5_9_per_school[i]),
            "raw_kids_0_4": float(raw_kids_0_4_per_school[i]),
            "naive_kept_after_opt_out": float(kept_per_school[i]),
            "naive_enrollment": float(naive_totals[i]),
            "mae_fit_final": float(mae_totals[i]),
            "rmse_fit_final": float(rmse_totals[i]),
            "mae_residual": float(mae_totals[i] - actual_adm_vec[i]),
            "rmse_residual": float(rmse_totals[i] - actual_adm_vec[i]),
        })
    return pd.DataFrame(rows)


def assemble_race_df(
    static: StaticModel,
    naive_rescaled: np.ndarray,
    mae_post: np.ndarray,
    rmse_post: np.ndarray,
) -> pd.DataFrame:
    """Long-format school × race × scheme counts.

    For the naive baseline, each fragment's per-race pool accumulates into
    its home school's row. FPG has no fragments and stays at zero.
    """
    rows = []
    # Naive per-school-per-race: sum fragment rescaled counts into home school
    naive_post = np.zeros_like(mae_post)
    np.add.at(naive_post, static.fragment_home_school_idx, naive_rescaled)

    for i, s in enumerate(ALL_SCHOOLS):
        for r_idx, r in enumerate(RACE_COLS):
            rows.append({
                "school": s,
                "race": r,
                "naive": float(naive_post[i, r_idx]),
                "mae_fit": float(mae_post[i, r_idx]),
                "rmse_fit": float(rmse_post[i, r_idx]),
            })
    return pd.DataFrame(rows)


def assemble_flows_df(
    mae_flows: np.ndarray,
    rmse_flows: np.ndarray,
) -> pd.DataFrame:
    """Long-format expected-value flows for both fitted models."""
    rows = []
    for scheme_name, flows in [("mae_fit", mae_flows), ("rmse_fit", rmse_flows)]:
        for m_idx, m in enumerate(MAGNETS):
            for z_idx, z in enumerate(SOURCE_ZONES):
                for r_idx, r in enumerate(RACE_COLS):
                    val = float(flows[m_idx, z_idx, r_idx])
                    if val < 1e-9:
                        continue
                    rows.append({
                        "scheme": scheme_name,
                        "source_zone": z,
                        "destination_magnet": m,
                        "race": r,
                        "count": val,
                    })
    return pd.DataFrame(rows)


# ---------------------------------------------------------------------------
# Charts
# ---------------------------------------------------------------------------

BAR_COLOR_ACTUAL = "#2f4858"
BAR_COLOR_NAIVE = "#b07a3a"
BAR_COLOR_MAE = "#4a8aa8"
BAR_COLOR_RMSE = "#8ab04a"
NEUTRAL = "#666666"
GRID_COLOR = "#dddddd"

RACE_LABELS = {
    "white_nh": "White (NH)",
    "black_nh": "Black (NH)",
    "asian_nh": "Asian (NH)",
    "hispanic": "Hispanic",
    "aian_nh": "AIAN (NH)",
    "nhpi_nh": "NHPI (NH)",
    "other_nh": "Other (NH)",
    "two_plus_nh": "Two+ (NH)",
}
RACE_COLORS = {
    "white_nh": "#8c8c8c",
    "black_nh": "#2b5d8b",
    "asian_nh": "#5ba6a0",
    "hispanic": "#d98b44",
    "aian_nh": "#a07ab0",
    "nhpi_nh": "#b0a060",
    "other_nh": "#70706a",
    "two_plus_nh": "#c4a8c4",
}


def _school_label(school: str) -> str:
    if school == "FPG Elementary":
        return f"{school}\n[full magnet]"
    if school in ("Carrboro Elementary", "Glenwood Elementary", "Seawell Elementary"):
        return f"{school}\n[partial magnet]"
    return school


def _style_axes(ax):
    ax.spines["top"].set_visible(False)
    ax.spines["right"].set_visible(False)
    ax.grid(axis="y", color=GRID_COLOR, linewidth=0.8)
    ax.set_axisbelow(True)


def chart_naive_vs_actual(school_df: pd.DataFrame) -> None:
    _progress(f"Writing {CHART_NAIVE_VS_ACTUAL.name}")
    df = school_df.sort_values("actual_adm_2025_26", ascending=False).reset_index(drop=True)
    x = np.arange(len(df))
    width = 0.38

    fig, ax = plt.subplots(figsize=(13, 6))
    ax.bar(x - width / 2, df["actual_adm_2025_26"], width,
           color=BAR_COLOR_ACTUAL, label=f"Actual ADM ({ADM_YEAR})")
    ax.bar(x + width / 2, df["naive_enrollment"], width,
           color=BAR_COLOR_NAIVE, label="Naive (strict residency)")

    for i, (_, row) in enumerate(df.iterrows()):
        delta = row["naive_enrollment"] - row["actual_adm_2025_26"]
        sign = "+" if delta >= 0 else ""
        y = max(row["actual_adm_2025_26"], row["naive_enrollment"]) + 15
        ax.text(i, y, f"{sign}{delta:.0f}", ha="center", va="bottom",
                fontsize=9, color="#333333")

    ax.set_xticks(x)
    ax.set_xticklabels(
        [_school_label(s).replace("\n", " ") for s in df["school"]],
        rotation=35, ha="right", fontsize=8,
    )
    ax.set_ylabel("Students")
    ax.set_title(
        "Actual 2025-26 ADM vs Naive Residency Enrollment\n"
        "Naive = ACS kids 5-9, 10% opt-out removed, assigned strictly by attendance zone",
        fontsize=11,
    )
    ax.legend(loc="upper right", frameon=False)
    _style_axes(ax)
    ax.set_ylim(
        0, max(df["actual_adm_2025_26"].max(), df["naive_enrollment"].max()) * 1.2
    )
    fig.tight_layout()
    fig.savefig(CHART_NAIVE_VS_ACTUAL, dpi=200, facecolor="white")
    plt.close(fig)


def chart_calibrated_comparison(
    school_df: pd.DataFrame,
    mae_obj: float,
    rmse_obj: float,
) -> None:
    _progress(f"Writing {CHART_CALIB_COMPARISON.name}")
    df = school_df.sort_values("actual_adm_2025_26", ascending=False).reset_index(drop=True)
    x = np.arange(len(df))
    width = 0.2

    fig, ax = plt.subplots(figsize=(15, 6.5))
    series = [
        (f"Actual ADM ({ADM_YEAR})",  "actual_adm_2025_26", BAR_COLOR_ACTUAL),
        ("Naive (strict residency)", "naive_enrollment",   BAR_COLOR_NAIVE),
        ("MAE-fit",                  "mae_fit_final",      BAR_COLOR_MAE),
        ("RMSE-fit",                 "rmse_fit_final",     BAR_COLOR_RMSE),
    ]
    offsets = (np.arange(len(series)) - (len(series) - 1) / 2.0) * width
    for off, (label, col, color) in zip(offsets, series):
        ax.bar(x + off, df[col], width, color=color, label=label)

    title = (
        "Actual 2025-26 ADM vs calibrated model fits\n"
        f"MAE-fit: MAE = {mae_obj:.2f}    RMSE-fit: RMSE = {rmse_obj:.2f}    "
        "(14-parameter softmax model, block-group-fragment granularity, "
        "calibrated against per-school ADM)"
    )
    ax.set_title(title, fontsize=11)
    ax.set_ylabel("Students")
    ax.set_xticks(x)
    ax.set_xticklabels(
        [_school_label(s).replace("\n", " ") for s in df["school"]],
        rotation=35, ha="right", fontsize=8,
    )
    _style_axes(ax)
    ax.legend(loc="upper right", frameon=False, fontsize=9, ncol=2)
    ax.set_ylim(
        0,
        max(
            df["actual_adm_2025_26"].max(),
            df["naive_enrollment"].max(),
            df["mae_fit_final"].max(),
            df["rmse_fit_final"].max(),
        ) * 1.15,
    )
    fig.tight_layout()
    fig.savefig(CHART_CALIB_COMPARISON, dpi=200, facecolor="white")
    plt.close(fig)


def chart_flows(
    flows: np.ndarray,
    title: str,
    out_path: Path,
) -> None:
    """Stacked horizontal bars per magnet, colored by race."""
    _progress(f"Writing {out_path.name}")
    # matrix[m, r] = total expected flow across source zones
    matrix = flows.sum(axis=1)  # (n_magnets, n_races)

    fig, ax = plt.subplots(figsize=(12, 5))
    y = np.arange(len(MAGNETS))
    left = np.zeros(len(MAGNETS))
    for r_idx, r in enumerate(RACE_COLS):
        vals = matrix[:, r_idx]
        ax.barh(
            y,
            vals,
            left=left,
            color=RACE_COLORS[r],
            label=RACE_LABELS[r],
            edgecolor="white",
            linewidth=0.4,
        )
        left += vals

    for i in range(len(MAGNETS)):
        ax.text(left[i] + 3, i, f"{int(round(matrix[i].sum()))}",
                va="center", fontsize=9, color="#333333")

    ax.set_yticks(y)
    ax.set_yticklabels([_school_label(m).replace("\n", " ") for m in MAGNETS], fontsize=9)
    ax.invert_yaxis()
    ax.set_xlabel("Students drawn in (expected value)")
    ax.set_title(title, fontsize=11)
    _style_axes(ax)
    ax.legend(
        title="Race category",
        loc="center left",
        bbox_to_anchor=(1.02, 0.5),
        fontsize=8,
        frameon=False,
    )
    fig.tight_layout()
    fig.savefig(out_path, dpi=200, facecolor="white", bbox_inches="tight")
    plt.close(fig)


def chart_race_composition(race_df: pd.DataFrame) -> None:
    _progress(f"Writing {CHART_RACE_COMP.name}")
    fig, axes = plt.subplots(1, len(MAGNETS), figsize=(15, 5.5), sharey=True)
    schemes = ["naive", "mae_fit", "rmse_fit"]
    scheme_labels = ["Naive\n(residency)", "MAE-fit\n(calibrated)", "RMSE-fit\n(calibrated)"]

    for ax, m in zip(axes, MAGNETS):
        rows = race_df[race_df["school"] == m].set_index("race")
        bottom = np.zeros(len(schemes))
        for r in RACE_COLS:
            vals = np.array([rows.loc[r, s] for s in schemes])
            ax.bar(
                scheme_labels,
                vals,
                bottom=bottom,
                color=RACE_COLORS[r],
                label=RACE_LABELS[r],
                edgecolor="white",
                linewidth=0.4,
            )
            bottom += vals
        ax.set_title(_school_label(m), fontsize=10)
        _style_axes(ax)
        ax.tick_params(axis="x", labelsize=8)
    axes[0].set_ylabel("Students")

    handles, labels = axes[0].get_legend_handles_labels()
    fig.legend(
        handles,
        labels,
        loc="lower center",
        ncol=len(RACE_COLS),
        frameon=False,
        fontsize=8,
        bbox_to_anchor=(0.5, -0.02),
    )
    fig.suptitle(
        "Magnet racial composition under the calibrated models\n"
        "Softmax choice model with per-magnet intercepts and race bonuses "
        "(direction constrained, magnitudes fit to actual per-school ADM).",
        fontsize=11,
    )
    fig.tight_layout(rect=(0, 0.05, 1, 0.95))
    fig.savefig(CHART_RACE_COMP, dpi=200, facecolor="white", bbox_inches="tight")
    plt.close(fig)


# ---------------------------------------------------------------------------
# Methodology doc writer
# ---------------------------------------------------------------------------

def write_methodology_doc(
    static: StaticModel,
    mae_result: CalibResult,
    rmse_result: CalibResult,
    school_df: pd.DataFrame,
) -> None:
    _progress(f"Writing {OUT_DOC.name}")
    sha = _git_sha()
    timestamp = datetime.now().strftime("%Y-%m-%d %H:%M")

    def _param_table(result: CalibResult) -> list[str]:
        lines = [
            "| Parameter | Bound low | Bound high | Fitted | At rail? |",
            "| --- | ---:|---:|---:|:---:|",
        ]
        for name, (lo, hi), val in zip(PARAM_NAMES, PARAM_BOUNDS, result.params):
            at_rail = ""
            if abs(val - lo) < 1e-6:
                at_rail = "LOW"
            elif abs(val - hi) < 1e-6:
                at_rail = "HIGH"
            lines.append(
                f"| `{name}` | {lo:+.2f} | {hi:+.2f} | {val:+.3f} | {at_rail} |"
            )
        return lines

    def _seed_table(result: CalibResult) -> list[str]:
        lines = [
            "| Seed | Objective |",
            "| ---:| ---:|",
        ]
        for seed, _, obj in result.seed_results:
            lines.append(f"| {seed} | {obj:.4f} |")
        return lines

    lines = [
        "# Calibrated Enrollment Allocation — Methodology",
        "",
        f"_Auto-generated {timestamp} · git {sha}_",
        "",
        "## Purpose",
        "",
        "**What this model is.** A **constrained fragment-level reallocation "
        "scenario generator** that fits a softmax (logit) choice model against "
        "observed 2025-26 per-school ADM. It can reproduce the 11 per-school "
        "totals reasonably well by tuning 14 free parameters (magnet intercepts, "
        "race bonuses, opt-out ratio, 5-9/0-4 mix, and income effects) against "
        "the single ground-truth signal we have: actual per-school enrollment.",
        "",
        "**What this model is NOT.** It is **not** a credible estimator of the "
        "true magnitudes of racial preferences, income effects, or opt-out bias "
        "at CHCCS magnets. With 14 parameters against only 11 data points, and "
        "with max pairwise L2 ≈ 0.94 across DE seeds, the coefficient space is "
        "weakly identified and sometimes corner-driven: different seeds find "
        "quite different parameter vectors that reach similar school-total "
        "error. **Do not interpret fitted coefficient values as point estimates "
        "of real-world effects.** Treat them as one of many configurations that "
        "reproduce the observed totals.",
        "",
        "Two independent calibrations are reported — one minimizing MAE across "
        "the 11 schools, one minimizing RMSE. Their results are shown side by "
        "side. If the two fits produce meaningfully different parameter "
        "vectors at similar school-level errors, that is **evidence of weak "
        "identification, not two competing answers about district demographics.**",
        "",
        "## Hard facts (inputs the calibration respects exactly)",
        "",
        f"- Actual 2025-26 elementary ADM per school (`{ADM_CSV.name}`), total {DISTRICT_ADM:,}",
        f"- District-wide homeschool / charter opt-out rate = {HOMESCHOOL_CHARTER_FRAC*100:.0f}% exactly",
        "- Attendance zones from `data/raw/properties/CHCCS/CHCCS.shp` (10 residential zones; FPG has no traditional zone and draws district-wide)",
        "",
        "## Population base: mix of ACS 5-9 and 0-4 buckets",
        "",
        "Because the ACS 5-year estimates are multiple years old, the 5-9 bucket "
        "has partially aged out of elementary and the 0-4 bucket has partially "
        "aged in. The forward model treats the effective elementary-age pool as "
        "a mix of the two buckets:",
        "",
        "```",
        "alpha = (1 - mu_0_4) * (DISTRICT_ADM / 0.9) / sum(kids_5_9)",
        "beta  =       mu_0_4  * (DISTRICT_ADM / 0.9) / sum(kids_0_4)",
        "combined[f, r] = alpha * raw_5_9[f, r] + beta * raw_0_4[f, r]",
        "```",
        "",
        "By construction, `sum(combined) = 4294 / 0.9 = 4771.11` for every value "
        "of `mu_0_4 ∈ [0, 1]`. After the 10% per-race opt-out, the retained pool "
        "is exactly 4,294 — no explicit rescaling step is required. `mu_0_4 = 0` "
        "means the combined pool is purely the 5-9 bucket scaled to 4,771; "
        "`mu_0_4 = 1` means the combined pool is purely the 0-4 bucket scaled to "
        "4,771. `mu_0_4` is one of the 14 calibrated parameters, so the optimizer "
        "chooses the mix that best fits per-school ADM.",
        "",
        "## Forward model",
        "",
        "For each kid in block-group fragment `f` (with home school `h(f)`) "
        "of race `r`, the destination is drawn from a softmax over:",
        "",
        "```",
        "U[stay]      = 0                                                       (always eligible)",
        "U[FPG]       = intercept_FPG      + bonus_FPG_hispanic   * I(r=hispanic)",
        "U[Carrboro]  = intercept_Carrboro + bonus_Carrboro_white * I(r=white)",
        "                                  + bonus_Carrboro_asian * I(r=asian)",
        "                                  + w_income_magnet      * z_income[f]  (only if h(f) != Carrboro)",
        "U[Glenwood]  = intercept_Glenwood + bonus_Glenwood_asian * I(r=asian)   (only if h(f) != Glenwood)",
        "U[Seawell]   = intercept_Seawell  + bonus_Seawell_white  * I(r=white)",
        "                                  + bonus_Seawell_asian  * I(r=asian)",
        "                                  + w_income_magnet      * z_income[f]  (only if h(f) != Seawell)",
        "prob[f, s, r] = exp(U[s]) / sum_{s' eligible} exp(U[s'])",
        "```",
        "",
        "The softmax parameterization is **feasible by construction** — every "
        "probability is in [0, 1] and each (fragment, race) row sums to 1 "
        "exactly. There is no infeasibility penalty. `z_income[f]` is the "
        "parent block group's median household income, standardized across "
        "unique block groups with the Census -666,666,666 sentinel imputed to "
        "the district mean. The per-race opt-out rate per fragment also "
        "depends multiplicatively on `exp(w_income_optout * z_income[f])`, "
        "with the base rate solved so the district-wide opt-out is exactly "
        "10% regardless of the parameter vector.",
        "",
        "## Constraints (enforced inside the forward model)",
        "",
        "- **10% opt-out.** Per-race retention is derived from `w_white_optout` "
        "and the kids-weighted district white share of the COMBINED pool (which "
        "depends on `mu_0_4`) so the weighted opt-out rate is exactly 10% for "
        "every parameter vector. White residents opt out at `w_white_optout ×` "
        "the rate of non-white residents.",
        "- **Combined-pool total is pinned.** After applying the mu-dependent "
        f"alpha/beta weights, the combined pool sums to exactly {TARGET_AFTER_OPTOUT:,.1f} "
        f"before opt-out, and to exactly {DISTRICT_ADM:,} after. No additional "
        "rescaling step is required.",
        "",
        "## Parameters fit",
        "",
        "### MAE-fit",
        "",
        f"Final objective (MAE): **{mae_result.objective:.4f}**",
        "",
    ]
    lines += _param_table(mae_result)
    lines += [
        "",
        "Per-seed DE objectives before L-BFGS-B refinement:",
        "",
    ]
    lines += _seed_table(mae_result)
    lines += [
        "",
        f"Max pairwise L2 distance between DE seed parameter vectors "
        f"(normalized to [0, 1] per dimension): "
        f"**{mae_result.stability['max_pairwise_l2']:.4f}**. "
        f"Objective spread across seeds: **{mae_result.stability['objective_spread']:.4f}**. "
        f"Weakly identified? **"
        f"{'YES' if mae_result.stability['weakly_identified'] else 'no'}**.",
        "",
        f"Hessian condition number at optimum: "
        f"**{mae_result.hessian_info['condition_number']:.2e}**. "
        f"Smallest absolute eigenvalue: "
        f"**{mae_result.hessian_info['smallest_abs_eigenvalue']:.2e}**.",
        "",
        "### RMSE-fit",
        "",
        f"Final objective (RMSE): **{rmse_result.objective:.4f}**",
        "",
    ]
    lines += _param_table(rmse_result)
    lines += [
        "",
        "Per-seed DE objectives before L-BFGS-B refinement:",
        "",
    ]
    lines += _seed_table(rmse_result)
    lines += [
        "",
        f"Max pairwise L2 distance between DE seed parameter vectors: "
        f"**{rmse_result.stability['max_pairwise_l2']:.4f}**. "
        f"Objective spread across seeds: **{rmse_result.stability['objective_spread']:.4f}**. "
        f"Weakly identified? **"
        f"{'YES' if rmse_result.stability['weakly_identified'] else 'no'}**.",
        "",
        f"Hessian condition number at optimum: "
        f"**{rmse_result.hessian_info['condition_number']:.2e}**. "
        f"Smallest absolute eigenvalue: "
        f"**{rmse_result.hessian_info['smallest_abs_eigenvalue']:.2e}**.",
        "",
        "## Per-school results",
        "",
        "| School | ADM | Naive | MAE-fit | MAE residual | RMSE-fit | RMSE residual |",
        "| --- | ---:|---:|---:|---:|---:|---:|",
    ]
    for _, row in school_df.iterrows():
        lines.append(
            f"| {row['school']} | {row['actual_adm_2025_26']:,} | "
            f"{row['naive_enrollment']:.0f} | {row['mae_fit_final']:.0f} | "
            f"{row['mae_residual']:+.0f} | {row['rmse_fit_final']:.0f} | "
            f"{row['rmse_residual']:+.0f} |"
        )
    lines += [
        "",
        "## Limitations",
        "",
        f"1. **Degrees of freedom.** {N_PARAMS} parameters against 11 school totals is "
        "under-determined at the school-total level — the model has far more "
        "knobs than observations. This is what makes identifiability weak. "
        "The fragment-level input (143 fragments × 8 races = 1,144 internal rows) gives the "
        "optimizer hundreds of internal rows of data, but the loss function "
        "aggregates back to the 11 per-school totals before evaluation.",
        "2. **Identifiability is expected to be weak.** Intercepts, race "
        "bonuses, income coefficients, and the 5-9/0-4 mix all partially "
        "trade off against each other within the softmax. The multi-seed DE "
        "stability diagnostic and Hessian condition number surface this. If "
        "the MAE-fit and RMSE-fit parameter vectors differ meaningfully at "
        "similar school-level errors, that is weak identification, not two "
        "answers about district demographics.",
        "3. **Race × age approximation.** Kids 5-9 and kids 0-4 racial "
        "composition is proxied by each fragment's parent block-group's "
        "total-population racial composition. A proper per-age-bucket race "
        "breakdown would require ACS tables B01001A–I and is a follow-up.",
        "4. **Income imputation.** ~10 block groups carry the Census "
        "`-666666666` sentinel for `median_hh_income`. These are imputed "
        "with the district mean (z_income = 0). Any fragment inside one of "
        "these BGs contributes zero to the income signal.",
        "5. **No out-of-sample validation.** The single data point is the "
        "2025-26 per-school ADM vector.",
        "6. **Model structural limits.** Even a perfect fit cannot capture "
        "things the model cannot express: cross-district transfers, private "
        "school enrollment (not included in the 10% opt-out), charter-"
        "specific draws, capacity constraints, etc.",
        "7. **Expected-value flows.** The flows CSV reports "
        "`rescaled[f, r] * prob[f, m, r]`, which is an **expected count** — "
        "a real-valued assignment weight, not a count of individual students "
        "moved. Do not interpret a flow of 12.4 as '12.4 real students.'",
        "8. **Northside residual.** The fragment-level calibrated softmax has "
        f"{N_PARAMS} free parameters and still cannot fully compress "
        "Northside's residual. This is a statement about model expressiveness "
        "— the covariates we have (race, age, income at block-group level) "
        "do not explain Northside's anomaly. Candidate omitted factors: "
        "cross-district transfers, UNC-adjacent population turnover, private "
        "school enrollment, lingering 2020-vintage ACS staleness.",
        "",
        "## Outputs",
        "",
        "- `data/processed/naive_enrollment_allocation.csv` — per-school summary",
        "- `data/processed/naive_enrollment_by_race.csv` — long school × race × scheme",
        "- `data/processed/naive_enrollment_flows.csv` — long expected-value flows",
        "- `assets/charts/naive_vs_actual_enrollment.png`",
        "- `assets/charts/naive_calibrated_vs_actual.png`",
        "- `assets/charts/redistribution_flows_mae_fit.png`",
        "- `assets/charts/redistribution_flows_rmse_fit.png`",
        "- `assets/charts/magnet_racial_composition_comparison.png`",
        "",
    ]
    OUT_DOC.write_text("\n".join(lines), encoding="utf-8")


# ---------------------------------------------------------------------------
# Main
# ---------------------------------------------------------------------------

def _print_fitted_params(result: CalibResult, label: str) -> None:
    print(f"\n{label} fitted parameters (objective = {result.objective:.4f}):")
    for name, (lo, hi), val in zip(PARAM_NAMES, PARAM_BOUNDS, result.params):
        rail = ""
        if abs(val - lo) < 1e-6:
            rail = "  <-- LOW RAIL"
        elif abs(val - hi) < 1e-6:
            rail = "  <-- HIGH RAIL"
        print(f"  {name:<26}  [{lo:+.2f}, {hi:+.2f}]  {val:+.4f}{rail}")
    print(f"  Stability — max pairwise L2 across DE seeds: "
          f"{result.stability['max_pairwise_l2']:.4f}, "
          f"objective spread: {result.stability['objective_spread']:.4f}, "
          f"weakly identified: {result.stability['weakly_identified']}")
    print(f"  Hessian — condition number: "
          f"{result.hessian_info['condition_number']:.2e}, "
          f"smallest |eig|: {result.hessian_info['smallest_abs_eigenvalue']:.2e}")


def _print_magnet_racial_shares(
    label: str, post: np.ndarray, static: StaticModel
) -> None:
    """Eyeball sanity: racial shares at each magnet vs district."""
    print(f"\n{label} — magnet racial composition (share of students):")
    district_total = post.sum(axis=0)
    district_share = district_total / district_total.sum()
    print(f"  {'':<24}  " + "  ".join(f"{RACE_LABELS[r][:6]:>6}" for r in RACE_COLS))
    print(f"  {'district':<24}  " + "  ".join(
        f"{district_share[RACE_IDX[r]]*100:>5.1f}%" for r in RACE_COLS
    ))
    school_idx = {s: i for i, s in enumerate(ALL_SCHOOLS)}
    for m in MAGNETS:
        row = post[school_idx[m]]
        share = row / row.sum() if row.sum() > 0 else row
        print(f"  {m:<24}  " + "  ".join(
            f"{share[RACE_IDX[r]]*100:>5.1f}%" for r in RACE_COLS
        ))


def main() -> None:
    ASSETS_CHARTS.mkdir(parents=True, exist_ok=True)
    DATA_PROCESSED.mkdir(parents=True, exist_ok=True)

    fragments, adm = load_inputs()
    static = build_static_model(fragments)
    actual_adm_vec = np.array([adm[s] for s in ALL_SCHOOLS], dtype=float)

    # Sanity: 10% constraint holds for any choice of w_white_optout, mu_0_4,
    # AND w_income_optout. Sweep a 3-D grid.
    for test_ratio in (1.0, 2.0, 3.5, 5.0):
        for test_mu in (0.0, 0.5, 1.0):
            for test_income in (-0.5, -0.25, 0.0, 0.25, 0.5):
                alpha = (1.0 - test_mu) * TARGET_AFTER_OPTOUT / static.district_kids_5_9_total
                beta = test_mu * TARGET_AFTER_OPTOUT / static.district_kids_0_4_total
                combined = alpha * static.raw_by_race_5_9 + beta * static.raw_by_race_0_4
                retention, feasible = _retention_with_income(
                    combined=combined,
                    w_white_optout=test_ratio,
                    w_income_optout=test_income,
                    z_income=static.z_income,
                    white_idx=static.white_idx,
                )
                if not feasible:
                    continue  # skip combinations that produce rate > 1
                kept_total = float((combined * retention).sum())
                assert abs(kept_total - DISTRICT_ADM) < 1e-6, (
                    f"10% constraint broken at w_white_optout={test_ratio}, "
                    f"mu={test_mu}, w_income_optout={test_income}: "
                    f"kept={kept_total:.4f}, expected={DISTRICT_ADM}"
                )
    _progress("10% opt-out constraint verified across feasible parameter grid")

    # Naive baseline
    naive_totals, naive_rescaled = naive_baseline(static)
    assert abs(naive_totals.sum() - DISTRICT_ADM) < 0.5
    # kids_5_9_after_opt_out per school (aggregated from fragments) for display
    kept_per_school = np.zeros(len(ALL_SCHOOLS))
    row_kept_totals = naive_rescaled.sum(axis=1)  # (n_rows,)
    np.add.at(kept_per_school, static.fragment_home_school_idx, row_kept_totals)

    # Calibrate both metrics independently
    mae_result = calibrate(static, actual_adm_vec, metric="mae")
    rmse_result = calibrate(static, actual_adm_vec, metric="rmse")

    mae_post, mae_flows, _, mae_feasible = forward_model(mae_result.params, static)
    rmse_post, rmse_flows, _, rmse_feasible = forward_model(rmse_result.params, static)
    if not mae_feasible:
        _progress("WARNING: MAE-fit converged to an infeasible corner — opt-out rate >= 1 for some (zone, race) cell")
    if not rmse_feasible:
        _progress("WARNING: RMSE-fit converged to an infeasible corner — opt-out rate >= 1 for some (zone, race) cell")
    mae_totals = mae_post.sum(axis=1)
    rmse_totals = rmse_post.sum(axis=1)

    # Sanity on conservation
    assert abs(mae_totals.sum() - DISTRICT_ADM) < 0.5
    assert abs(rmse_totals.sum() - DISTRICT_ADM) < 0.5
    assert (mae_totals >= -1e-6).all()
    assert (rmse_totals >= -1e-6).all()

    # Assemble output tables
    school_df = assemble_school_df(
        actual_adm_vec, naive_totals, mae_totals, rmse_totals,
        static.raw_kids_5_9_per_school,
        static.raw_kids_0_4_per_school,
        kept_per_school,
    )
    race_df = assemble_race_df(static, naive_rescaled, mae_post, rmse_post)
    flows_df = assemble_flows_df(mae_flows, rmse_flows)

    school_df.to_csv(OUT_ALLOC_CSV, index=False)
    _progress(f"Wrote {OUT_ALLOC_CSV.name}")
    race_df.to_csv(OUT_BY_RACE_CSV, index=False)
    _progress(f"Wrote {OUT_BY_RACE_CSV.name}")
    flows_df.to_csv(OUT_FLOWS_CSV, index=False)
    _progress(f"Wrote {OUT_FLOWS_CSV.name}")

    chart_naive_vs_actual(school_df)
    chart_calibrated_comparison(school_df, mae_result.objective, rmse_result.objective)
    chart_flows(
        mae_flows,
        "MAE-fit redistribution flows — stacked by race (expected values)",
        CHART_FLOWS_MAE,
    )
    chart_flows(
        rmse_flows,
        "RMSE-fit redistribution flows — stacked by race (expected values)",
        CHART_FLOWS_RMSE,
    )
    chart_race_composition(race_df)

    write_methodology_doc(static, mae_result, rmse_result, school_df)

    # --- Console summary ---
    print()
    print("Per-school results:")
    print(
        school_df.to_string(
            index=False,
            formatters={
                "raw_kids_5_9": "{:.0f}".format,
                "raw_kids_0_4": "{:.0f}".format,
                "naive_kept_after_opt_out": "{:.0f}".format,
                "naive_enrollment": "{:.0f}".format,
                "mae_fit_final": "{:.0f}".format,
                "rmse_fit_final": "{:.0f}".format,
                "mae_residual": "{:+.0f}".format,
                "rmse_residual": "{:+.0f}".format,
            },
        )
    )

    # Error stats
    print()
    print("Error stats vs actual 2025-26 ADM:")
    print(f"  {'model':<22}  {'MAE':>7}  {'RMSE':>7}  {'max|err|':>9}")
    for label, totals in [
        ("naive", naive_totals),
        ("MAE-fit", mae_totals),
        ("RMSE-fit", rmse_totals),
    ]:
        err = totals - actual_adm_vec
        mae_v = float(np.mean(np.abs(err)))
        rmse_v = float(np.sqrt(np.mean(err ** 2)))
        max_v = float(np.max(np.abs(err)))
        print(f"  {label:<22}  {mae_v:7.2f}  {rmse_v:7.2f}  {max_v:9.2f}")

    _print_fitted_params(mae_result, "MAE-fit")
    _print_fitted_params(rmse_result, "RMSE-fit")
    _print_magnet_racial_shares("MAE-fit", mae_post, static)
    _print_magnet_racial_shares("RMSE-fit", rmse_post, static)


if __name__ == "__main__":
    main()
