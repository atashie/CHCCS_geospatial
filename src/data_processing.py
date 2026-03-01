"""
CHCCS District Geospatial Data Processing

This module cleans and transforms raw data for visualization and analysis.
"""

import pandas as pd
from pathlib import Path

# Project paths
PROJECT_ROOT = Path(__file__).parent.parent
DATA_RAW = PROJECT_ROOT / "data" / "raw"
DATA_PROCESSED = PROJECT_ROOT / "data" / "processed"


def load_enrollment_data():
    """Load and validate enrollment data."""
    filepath = DATA_PROCESSED / "enrollment.csv"
    if not filepath.exists():
        raise FileNotFoundError(f"Run data_collection.py first: {filepath}")

    df = pd.read_csv(filepath)
    return df


def load_academic_data():
    """Load and validate academic growth data."""
    filepath = DATA_PROCESSED / "academic_growth.csv"
    if not filepath.exists():
        raise FileNotFoundError(f"Run data_collection.py first: {filepath}")

    df = pd.read_csv(filepath)
    return df


def load_costs_data():
    """Load and validate cost data."""
    filepath = DATA_PROCESSED / "costs.csv"
    if not filepath.exists():
        raise FileNotFoundError(f"Run data_collection.py first: {filepath}")

    df = pd.read_csv(filepath)
    return df


def load_demographics_data():
    """Load and validate demographics data."""
    filepath = DATA_PROCESSED / "demographics.csv"
    if not filepath.exists():
        raise FileNotFoundError(f"Run data_collection.py first: {filepath}")

    df = pd.read_csv(filepath)
    return df


def load_housing_data():
    """Load affordable housing data."""
    filepath = DATA_RAW / "housing_data" / "affordable_housing.csv"
    if not filepath.exists():
        raise FileNotFoundError(f"Run data_collection.py first: {filepath}")

    df = pd.read_csv(filepath)
    return df


def load_school_locations():
    """Load school location data (NCES EDGE 2023-24, cached by road_pollution.py)."""
    filepath = PROJECT_ROOT / "data" / "cache" / "nces_school_locations.csv"
    if not filepath.exists():
        raise FileNotFoundError(
            f"School locations not found at {filepath}. "
            "Run road_pollution.py first to download from NCES."
        )

    df = pd.read_csv(filepath)
    return df


def calculate_walkability_comparison():
    """
    Calculate walkability metrics for comparison.
    Returns DataFrame sorted by students within walking distance.
    """
    enrollment = load_enrollment_data()

    # Sort by walkability (students within 0.5 miles)
    walkability = enrollment[["school", "students_within_half_mile", "walk_bike_pct"]].copy()
    walkability = walkability.sort_values("students_within_half_mile", ascending=False)

    return walkability


def calculate_cost_comparison():
    """
    Calculate 10-year cost comparison for each school scenario.
    """
    costs = load_costs_data()

    # Assumptions for 10-year projection
    ANNUAL_OPERATING_COST = 1_700_000  # Estimated per school
    ANNUAL_BUS_COST_INCREASE = 200_000  # If school closes

    scenarios = []

    for _, school in costs.iterrows():
        school_name = school["school"]
        bond_funding = school.get("bond_funding_2024", False)

        # Scenario: Keep Open
        keep_cost = ANNUAL_OPERATING_COST * 10

        # Scenario: Close
        close_savings = ANNUAL_OPERATING_COST * 10
        close_transport_cost = ANNUAL_BUS_COST_INCREASE * 10
        close_net = close_savings - close_transport_cost

        scenarios.append({
            "school": school_name,
            "keep_open_10yr": keep_cost,
            "close_savings_10yr": close_net,
            "bond_funding": bond_funding,
            "renovation_cost": school.get("renovation_cost_estimate", None),
        })

    return pd.DataFrame(scenarios)


def calculate_equity_metrics():
    """
    Calculate equity and diversity metrics.
    """
    enrollment = load_enrollment_data()
    demographics = load_demographics_data()

    # Merge data
    equity = enrollment.merge(demographics, on="school", how="left")

    # Calculate composite equity score
    equity["equity_score"] = (
        equity["free_reduced_lunch_pct"].fillna(0) * 0.4 +
        equity["minority_pct"].fillna(0) * 0.3 +
        equity["title_i"].fillna(False).astype(int) * 30
    )

    return equity[["school", "free_reduced_lunch_pct", "minority_pct",
                   "title_i", "equity_score"]].sort_values("equity_score", ascending=False)


def prepare_visualization_data():
    """
    Prepare all data needed for visualizations.
    Saves processed data to DATA_PROCESSED directory.
    """
    print("Preparing visualization data...")

    # Walkability comparison
    walkability = calculate_walkability_comparison()
    walkability.to_csv(DATA_PROCESSED / "viz_walkability.csv", index=False)
    print(f"  - Created: viz_walkability.csv")

    # Cost comparison
    costs = calculate_cost_comparison()
    costs.to_csv(DATA_PROCESSED / "viz_costs.csv", index=False)
    print(f"  - Created: viz_costs.csv")

    # Equity metrics
    equity = calculate_equity_metrics()
    equity.to_csv(DATA_PROCESSED / "viz_equity.csv", index=False)
    print(f"  - Created: viz_equity.csv")

    print("Visualization data prepared!")


def generate_summary_stats():
    """Generate summary statistics for the district."""
    enrollment = load_enrollment_data()

    summary = {
        "total_schools": len(enrollment),
        "total_enrollment": int(enrollment.get("enrollment", pd.Series()).sum()) if "enrollment" in enrollment.columns else 0,
    }

    return summary


def main():
    """Run all data processing tasks."""
    print("=" * 60)
    print("CHCCS Geospatial - Data Processing")
    print("=" * 60)

    try:
        prepare_visualization_data()

        print("\nSummary Statistics:")
        stats = generate_summary_stats()
        for key, value in stats.items():
            print(f"  {key}: {value}")

    except FileNotFoundError as e:
        print(f"\nError: {e}")
        print("Please run data_collection.py first.")
        return

    print("\n" + "=" * 60)
    print("Data processing complete!")
    print("Next step: Run visualizations.py to generate charts")
    print("=" * 60)


if __name__ == "__main__":
    main()
