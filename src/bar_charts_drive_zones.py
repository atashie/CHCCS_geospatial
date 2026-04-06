"""
Drive-Zone Bar Charts — per-school horizontal bar charts.

Matches the style of the existing bars_households_poverty_drive.png and
bars_minority_residents_drive.png images in assets/charts/: horizontal bars
sorted descending, Ephesus highlighted red, Seawell highlighted blue, all
other schools in neutral gray. Values shown to the right of each bar.

Reads from data/processed/census_dot_zone_demographics.csv (zone_type
'Nearest Drive'), the same source used for the dynamic dual-panel charts
in the demographics story.

Usage:
    python src/bar_charts_drive_zones.py
"""

from __future__ import annotations

from pathlib import Path

import matplotlib.pyplot as plt
import pandas as pd

PROJECT_ROOT = Path(__file__).resolve().parent.parent
CSV_IN = PROJECT_ROOT / "data" / "processed" / "census_dot_zone_demographics.csv"
OUT_DIR = PROJECT_ROOT / "assets" / "charts"

EPHESUS_COLOR = "#C6282B"
SEAWELL_COLOR = "#1585CD"
NEUTRAL_COLOR = "#CCCCCC"
TRACK_COLOR = "#EEEEEE"

FOOTER = ("Nearest Drive Zones (Dijkstra)  \u00b7  "
          "Highlights: Ephesus (#C6282B), Seawell (#1585CD)")

plt.rcParams["font.family"] = "sans-serif"
plt.rcParams["font.sans-serif"] = ["Segoe UI", "Tahoma", "DejaVu Sans"]


def _load_drive_zones() -> pd.DataFrame:
    if not CSV_IN.exists():
        raise FileNotFoundError(f"Missing {CSV_IN} — run the socioeconomic "
                                f"pipeline first.")
    df = pd.read_csv(CSV_IN)
    df = df[df["zone_type"] == "Nearest Drive"].copy()
    if df.empty:
        raise RuntimeError("No 'Nearest Drive' rows in census_dot_zone_demographics.csv")
    # Compute children 5-9 count (not already materialized)
    df["children_5_9"] = df["male_5_9"].fillna(0) + df["female_5_9"].fillna(0)
    return df.reset_index(drop=True)


def _short_name(school: str) -> str:
    """Strip the ' Elementary' / ' Bilingue' suffix for compact labels."""
    name = school.replace(" Elementary", "").replace(" Bilingue", "")
    return name


def _bar_color(school: str) -> str:
    if "Ephesus" in school:
        return EPHESUS_COLOR
    if "Seawell" in school:
        return SEAWELL_COLOR
    return NEUTRAL_COLOR


def _label_color(school: str) -> str:
    if "Ephesus" in school:
        return EPHESUS_COLOR
    if "Seawell" in school:
        return SEAWELL_COLOR
    return "#333333"


def _label_weight(school: str) -> str:
    if "Ephesus" in school or "Seawell" in school:
        return "bold"
    return "normal"


def draw_bar_chart(
    df: pd.DataFrame, metric_col: str, title: str, subtitle: str,
    value_fmt: str, out_path: Path,
) -> None:
    """Render a horizontal bar chart of metric_col per school."""
    data = df[["school", metric_col]].copy()
    data = data.sort_values(metric_col, ascending=True).reset_index(drop=True)

    fig, ax = plt.subplots(figsize=(6, 4.5), dpi=200)
    fig.patch.set_facecolor("white")
    ax.set_facecolor("white")

    y_pos = list(range(len(data)))
    colors = [_bar_color(s) for s in data["school"]]
    max_val = data[metric_col].max() if len(data) else 1.0
    # Background tracks (full width) for each row
    ax.barh(y_pos, [max_val] * len(data), color=TRACK_COLOR,
            edgecolor="none", height=0.9)
    # Value bars on top
    ax.barh(y_pos, data[metric_col], color=colors, edgecolor="none",
            height=0.9)

    # Hide axes / spines / ticks
    for spine in ax.spines.values():
        spine.set_visible(False)
    ax.set_xticks([])
    ax.set_yticks([])
    ax.grid(False)

    # Padding used to place labels
    label_pad = max_val * 0.015
    value_pad = max_val * 0.015

    # Per-bar school label (left) and value label (right)
    for i, row in data.iterrows():
        school = row["school"]
        val = row[metric_col]
        ax.text(
            -label_pad, i, _short_name(school),
            ha="right", va="center",
            fontsize=10, color=_label_color(school),
            fontweight=_label_weight(school),
        )
        ax.text(
            val + value_pad, i, value_fmt.format(val),
            ha="left", va="center",
            fontsize=10, color=_label_color(school),
            fontweight="bold",
        )

    # X-axis padding so labels + values fit
    ax.set_xlim(-max_val * 0.42, max_val * 1.18)
    ax.set_ylim(-0.6, len(data) - 0.4)

    # Title & subtitle
    fig.text(0.5, 0.955, title, ha="center", va="top",
             fontsize=14, fontweight="bold", color="#111111")
    fig.text(0.5, 0.905, subtitle, ha="center", va="top",
             fontsize=9.5, style="italic", color="#666666")

    # Footer
    fig.text(0.5, 0.02, FOOTER, ha="center", va="bottom",
             fontsize=8, color="#888888")

    plt.subplots_adjust(left=0.02, right=0.98, top=0.86, bottom=0.07)
    out_path.parent.mkdir(parents=True, exist_ok=True)
    fig.savefig(out_path, dpi=200, facecolor="white",
                bbox_inches=None, pad_inches=0.05)
    plt.close(fig)
    print(f"  Saved {out_path.relative_to(PROJECT_ROOT)}")


def main() -> None:
    print("=" * 60)
    print("Drive-Zone Bar Charts")
    print("=" * 60)
    df = _load_drive_zones()
    print(f"  Loaded {len(df)} drive-zone rows")

    # Chart 1: Households with No Vehicle (count)
    draw_bar_chart(
        df,
        metric_col="vehicles_zero",
        title="Households with No Vehicle",
        subtitle="Count of zero-vehicle households in each drive zone "
                 "(ACS 5-Year 2020-2024)",
        value_fmt="{:,.0f}",
        out_path=OUT_DIR / "bars_no_vehicle_drive.png",
    )

    # Chart 2: Population Aged 5-9 (count)
    draw_bar_chart(
        df,
        metric_col="children_5_9",
        title="Population Aged 5\u20139",
        subtitle="Count of residents aged 5 to 9 in each drive zone "
                 "(2020 Decennial Census, dot-placed)",
        value_fmt="{:,.0f}",
        out_path=OUT_DIR / "bars_children_5_9_drive.png",
    )

    print("=" * 60)


if __name__ == "__main__":
    main()
