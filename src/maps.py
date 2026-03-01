# TODO: This module is Ephesus-specific and needs complete restructuring to be
# school-agnostic. Housing data is centered on Ephesus, walkability labels reference
# "99 students", childcare framing is "near Ephesus". Full restructuring is needed
# to support any school or all schools equally.
"""
CHCCS District Map Visualizations (needs restructuring)

Generates maps showing:
- School locations with walkability zones
- Affordable housing developments
- Attendance zone overlays
"""

import folium
from folium import plugins
import pandas as pd
import csv
from pathlib import Path
import webbrowser

# Project paths
PROJECT_ROOT = Path(__file__).parent.parent
DATA_PROCESSED = PROJECT_ROOT / "data" / "processed"
DATA_RAW = PROJECT_ROOT / "data" / "raw"
ASSETS_MAPS = PROJECT_ROOT / "assets" / "maps"

# Chapel Hill center coordinates
CHAPEL_HILL_CENTER = [35.9132, -79.0558]

# School colors
EPHESUS_COLOR = "#2E86AB"
CLOSURE_COLOR = "#E74C3C"  # Red for schools slated for closure
BOND_COLOR = "#F39C12"      # Orange for bond schools
OTHER_COLOR = "#95A5A6"     # Gray for others
CHILDCARE_COLOR = "#9B59B6"  # Purple for childcare centers
CHILDCARE_EPHESUS_COLOR = "#27AE60"  # Green for centers near Ephesus


def ensure_directories():
    """Create output directories if they don't exist."""
    ASSETS_MAPS.mkdir(parents=True, exist_ok=True)


def get_school_data():
    """Get school location and status data."""
    schools = [
        {
            "name": "Ephesus Elementary",
            "lat": 35.9372,
            "lon": -79.0178,
            "status": "highlight",
            "walkable_students": 99,
            "description": "Highest walkability in district"
        },
        {
            "name": "Glenwood Elementary",
            "lat": 35.9128,
            "lon": -79.0589,
            "status": "closure",
            "walkable_students": 65,
            "description": "Slated for closure"
        },
        {
            "name": "Seawell Elementary",
            "lat": 35.9033,
            "lon": -79.0817,
            "status": "closure",
            "walkable_students": 45,
            "description": "Slated for closure"
        },
        {
            "name": "Carrboro Elementary",
            "lat": 35.9103,
            "lon": -79.0753,
            "status": "bond",
            "walkable_students": 58,
            "description": "Bond funding for replacement (built 1959)"
        },
        {
            "name": "Estes Hills Elementary",
            "lat": 35.9442,
            "lon": -79.0467,
            "status": "bond",
            "walkable_students": 52,
            "description": "Bond funding for replacement (built 1958)"
        },
        {
            "name": "Frank Porter Graham",
            "lat": 35.9285,
            "lon": -79.0392,
            "status": "bond",
            "walkable_students": 48,
            "description": "Bond funding for replacement (built 1962)"
        },
        {
            "name": "Northside Elementary",
            "lat": 35.9225,
            "lon": -79.0567,
            "status": "other",
            "walkable_students": 72,
            "description": ""
        },
        {
            "name": "McDougle Elementary",
            "lat": 35.8983,
            "lon": -79.0453,
            "status": "other",
            "walkable_students": 38,
            "description": ""
        },
        {
            "name": "Morris Grove Elementary",
            "lat": 35.8775,
            "lon": -79.0308,
            "status": "other",
            "walkable_students": 32,
            "description": ""
        },
        {
            "name": "Rashkis Elementary",
            "lat": 35.8817,
            "lon": -79.0692,
            "status": "other",
            "walkable_students": 28,
            "description": ""
        },
        {
            "name": "Scroggs Elementary",
            "lat": 35.8650,
            "lon": -79.0433,
            "status": "other",
            "walkable_students": 25,
            "description": ""
        },
    ]
    return schools


def get_housing_data():
    """Get affordable housing development data."""
    housing = [
        {
            "name": "Greenfield Place (Phase 1)",
            "lat": 35.9382,
            "lon": -79.0208,
            "units": 80,
            "type": "Working Families",
            "status": "Completed"
        },
        {
            "name": "Greenfield Commons (Phase 2)",
            "lat": 35.9378,
            "lon": -79.0215,
            "units": 69,
            "type": "Seniors",
            "status": "Completed"
        },
        {
            "name": "Park Apartments",
            "lat": 35.9405,
            "lon": -79.0180,
            "units": 414,
            "type": "Mixed Income",
            "status": "Under Construction"
        },
    ]
    return housing


def get_color_for_status(status):
    """Return color based on school status."""
    colors = {
        "highlight": EPHESUS_COLOR,
        "closure": CLOSURE_COLOR,
        "bond": BOND_COLOR,
        "other": OTHER_COLOR,
    }
    return colors.get(status, OTHER_COLOR)


def create_walkability_map():
    """
    Create map showing 0.5-mile walkability zones around schools.
    """
    m = folium.Map(location=CHAPEL_HILL_CENTER, zoom_start=13, tiles="cartodbpositron")

    schools = get_school_data()

    # Add legend
    legend_html = """
    <div style="position: fixed; bottom: 50px; left: 50px; z-index: 1000;
                background-color: white; padding: 10px; border-radius: 5px;
                box-shadow: 2px 2px 5px rgba(0,0,0,0.3);">
        <h4 style="margin: 0 0 10px 0;">Legend</h4>
        <div><span style="background-color: #2E86AB; width: 15px; height: 15px; display: inline-block; margin-right: 5px;"></span> Ephesus (99 students)</div>
        <div><span style="background-color: #E74C3C; width: 15px; height: 15px; display: inline-block; margin-right: 5px;"></span> Slated for Closure</div>
        <div><span style="background-color: #F39C12; width: 15px; height: 15px; display: inline-block; margin-right: 5px;"></span> Bond Funded</div>
        <div><span style="background-color: #95A5A6; width: 15px; height: 15px; display: inline-block; margin-right: 5px;"></span> Other Schools</div>
    </div>
    """
    m.get_root().html.add_child(folium.Element(legend_html))

    for school in schools:
        color = get_color_for_status(school["status"])

        # Add 0.5-mile radius circle (approximately 805 meters)
        folium.Circle(
            location=[school["lat"], school["lon"]],
            radius=805,
            color=color,
            fill=True,
            fillColor=color,
            fillOpacity=0.2,
            weight=2,
            popup=f"<b>{school['name']}</b><br>0.5-mile walkability zone"
        ).add_to(m)

        # Add school marker
        icon_color = "blue" if school["status"] == "highlight" else (
            "red" if school["status"] == "closure" else (
                "orange" if school["status"] == "bond" else "gray"
            )
        )

        popup_html = f"""
        <b>{school['name']}</b><br>
        Students within 0.5 mi: <b>{school['walkable_students']}</b><br>
        {school['description']}
        """

        folium.Marker(
            location=[school["lat"], school["lon"]],
            popup=folium.Popup(popup_html, max_width=250),
            icon=folium.Icon(color=icon_color, icon="graduation-cap", prefix="fa"),
        ).add_to(m)

    # Add title
    title_html = """
    <div style="position: fixed; top: 10px; left: 50%; transform: translateX(-50%);
                z-index: 1000; background-color: white; padding: 10px 20px;
                border-radius: 5px; box-shadow: 2px 2px 5px rgba(0,0,0,0.3);">
        <h3 style="margin: 0;">CHCCS Elementary Schools: Walkability Zones</h3>
        <p style="margin: 5px 0 0 0; font-size: 12px; color: #666;">
            Ephesus has the highest walkability with 99 students within 0.5 miles
        </p>
    </div>
    """
    m.get_root().html.add_child(folium.Element(title_html))

    # Save map
    map_path = ASSETS_MAPS / "walkability_map.html"
    m.save(str(map_path))
    print(f"Created: {map_path}")

    return m


def create_housing_map():
    """
    Create map showing affordable housing developments near Ephesus.
    """
    # Center on Ephesus
    ephesus_coords = [35.9372, -79.0178]

    m = folium.Map(location=ephesus_coords, zoom_start=15, tiles="cartodbpositron")

    housing = get_housing_data()

    # Add Ephesus school marker
    folium.Marker(
        location=ephesus_coords,
        popup="<b>Ephesus Elementary School</b>",
        icon=folium.Icon(color="blue", icon="graduation-cap", prefix="fa"),
    ).add_to(m)

    # Add 0.5-mile walkability zone
    folium.Circle(
        location=ephesus_coords,
        radius=805,
        color=EPHESUS_COLOR,
        fill=True,
        fillColor=EPHESUS_COLOR,
        fillOpacity=0.1,
        weight=2,
        popup="0.5-mile walkability zone"
    ).add_to(m)

    # Add housing developments
    total_units = 0
    for project in housing:
        total_units += project["units"]

        icon_color = "green" if project["status"] == "Completed" else "orange"

        popup_html = f"""
        <b>{project['name']}</b><br>
        Units: <b>{project['units']}</b><br>
        Type: {project['type']}<br>
        Status: {project['status']}
        """

        folium.Marker(
            location=[project["lat"], project["lon"]],
            popup=folium.Popup(popup_html, max_width=250),
            icon=folium.Icon(color=icon_color, icon="home", prefix="fa"),
        ).add_to(m)

    # Add legend
    legend_html = f"""
    <div style="position: fixed; bottom: 50px; left: 50px; z-index: 1000;
                background-color: white; padding: 10px; border-radius: 5px;
                box-shadow: 2px 2px 5px rgba(0,0,0,0.3);">
        <h4 style="margin: 0 0 10px 0;">Housing Near Ephesus</h4>
        <div><span style="background-color: #2E86AB; width: 15px; height: 15px; display: inline-block; margin-right: 5px;"></span> Ephesus Elementary</div>
        <div><span style="background-color: #27AE60; width: 15px; height: 15px; display: inline-block; margin-right: 5px;"></span> Completed Housing</div>
        <div><span style="background-color: #F39C12; width: 15px; height: 15px; display: inline-block; margin-right: 5px;"></span> Under Construction</div>
        <hr style="margin: 10px 0;">
        <div><b>Total: {total_units} new units</b></div>
    </div>
    """
    m.get_root().html.add_child(folium.Element(legend_html))

    # Add title
    title_html = f"""
    <div style="position: fixed; top: 10px; left: 50%; transform: translateX(-50%);
                z-index: 1000; background-color: white; padding: 10px 20px;
                border-radius: 5px; box-shadow: 2px 2px 5px rgba(0,0,0,0.3);">
        <h3 style="margin: 0;">Affordable Housing Development Near Ephesus</h3>
        <p style="margin: 5px 0 0 0; font-size: 12px; color: #666;">
            563+ new housing units in the Ephesus-Fordham District
        </p>
    </div>
    """
    m.get_root().html.add_child(folium.Element(title_html))

    # Save map
    map_path = ASSETS_MAPS / "housing_map.html"
    m.save(str(map_path))
    print(f"Created: {map_path}")

    return m


def create_comparison_map():
    """
    Create map comparing all school statuses with annotations.
    """
    m = folium.Map(location=CHAPEL_HILL_CENTER, zoom_start=12, tiles="cartodbpositron")

    schools = get_school_data()

    # Create feature groups for filtering
    ephesus_group = folium.FeatureGroup(name="Ephesus (Highest Walkability)")
    closure_group = folium.FeatureGroup(name="Slated for Closure")
    bond_group = folium.FeatureGroup(name="Bond Funded (Built 1958-1962)")
    other_group = folium.FeatureGroup(name="Other Schools")

    for school in schools:
        color = get_color_for_status(school["status"])

        popup_html = f"""
        <b>{school['name']}</b><br>
        Walkable Students: <b>{school['walkable_students']}</b><br>
        {school['description']}
        """

        marker = folium.Marker(
            location=[school["lat"], school["lon"]],
            popup=folium.Popup(popup_html, max_width=250),
            icon=folium.DivIcon(
                html=f"""
                <div style="background-color: {color}; color: white; padding: 5px 10px;
                            border-radius: 15px; font-weight: bold; white-space: nowrap;
                            box-shadow: 2px 2px 5px rgba(0,0,0,0.3);">
                    {school['walkable_students']}
                </div>
                """,
                icon_size=(50, 30),
                icon_anchor=(25, 15)
            )
        )

        if school["status"] == "highlight":
            marker.add_to(ephesus_group)
        elif school["status"] == "closure":
            marker.add_to(closure_group)
        elif school["status"] == "bond":
            marker.add_to(bond_group)
        else:
            marker.add_to(other_group)

    ephesus_group.add_to(m)
    closure_group.add_to(m)
    bond_group.add_to(m)
    other_group.add_to(m)

    folium.LayerControl().add_to(m)

    # Add title
    title_html = """
    <div style="position: fixed; top: 10px; left: 50%; transform: translateX(-50%);
                z-index: 1000; background-color: white; padding: 10px 20px;
                border-radius: 5px; box-shadow: 2px 2px 5px rgba(0,0,0,0.3);">
        <h3 style="margin: 0;">CHCCS Elementary Schools: Status Comparison</h3>
        <p style="margin: 5px 0 0 0; font-size: 12px; color: #666;">
            Numbers show students within 0.5-mile walking distance
        </p>
    </div>
    """
    m.get_root().html.add_child(folium.Element(title_html))

    # Save map
    map_path = ASSETS_MAPS / "comparison_map.html"
    m.save(str(map_path))
    print(f"Created: {map_path}")

    return m


def load_childcare_data(facility_type='all_types'):
    """
    Load childcare facility data from processed CSV.

    Args:
        facility_type: One of 'centers', 'family_homes', or 'all_types'
    """
    facilities = []

    # Try new directory structure first
    detail_file = DATA_PROCESSED / facility_type / "childcare_detail.csv"

    # Fall back to legacy location
    if not detail_file.exists():
        detail_file = DATA_PROCESSED / "childcare_centers_detail.csv"

    if not detail_file.exists():
        print(f"Warning: Childcare file not found: {detail_file}")
        print("Run childcare_geocode.py first to generate this file.")
        return facilities

    with open(detail_file, 'r', encoding='utf-8') as f:
        reader = csv.DictReader(f)
        for row in reader:
            try:
                facilities.append({
                    'name': row['center_name'],
                    'license_number': row.get('license_number', ''),
                    'address': row['address'],
                    'lat': float(row['center_lat']),
                    'lon': float(row['center_lon']),
                    'phone': row.get('phone', ''),
                    'capacity': row.get('capacity', 'N/A'),
                    'star_rating': row.get('star_rating', 'N/A'),
                    'nearest_school': row.get('nearest_school', 'Unknown'),
                    'distance_miles': float(row['distance_miles']) if row.get('distance_miles') else None
                })
            except (ValueError, KeyError) as e:
                print(f"Warning: Skipping facility due to data error: {e}")
                continue

    return facilities


def load_childcare_summary(facility_type='all_types', radius=0.5):
    """
    Load childcare summary by school.

    Args:
        facility_type: One of 'centers', 'family_homes', or 'all_types'
        radius: Radius in miles (0.25, 0.5, 1.0, or 2.0)
    """
    summary = []

    # Try new directory structure first
    summary_file = DATA_PROCESSED / facility_type / f"childcare_by_school_{radius}mi.csv"

    # Fall back to legacy location
    if not summary_file.exists():
        summary_file = DATA_PROCESSED / "childcare_by_school.csv"

    if not summary_file.exists():
        return summary

    with open(summary_file, 'r', encoding='utf-8') as f:
        reader = csv.DictReader(f)
        for row in reader:
            summary.append({
                'school': row['school'],
                'center_count': int(row['center_count']),
                'total_capacity': int(row['total_capacity']) if row.get('total_capacity') else 0
            })

    return summary


def create_childcare_map():
    """
    Create map showing childcare facilities near all CHCCS elementary schools.

    Shows:
    - All 11 schools with multiple radius circles (0.25, 0.5, 1.0, 2.0 mi)
    - Childcare centers and family homes as markers
    - Color-coded by proximity to Ephesus vs other schools
    - Layer controls to toggle radius views
    - Popups with facility details
    """
    m = folium.Map(location=CHAPEL_HILL_CENTER, zoom_start=12, tiles="cartodbpositron")

    schools = get_school_data()
    facilities = load_childcare_data('all_types')

    # Radius configurations (miles to meters: 1 mile = 1609.34 meters)
    radii = [
        {'miles': 0.25, 'meters': 402, 'color_mult': 0.4},
        {'miles': 0.5, 'meters': 805, 'color_mult': 0.3},
        {'miles': 1.0, 'meters': 1609, 'color_mult': 0.2},
        {'miles': 2.0, 'meters': 3219, 'color_mult': 0.1},
    ]

    # Create feature groups for each radius
    radius_groups = {}
    for r in radii:
        radius_groups[r['miles']] = folium.FeatureGroup(name=f"{r['miles']} mile radius", show=(r['miles'] == 0.5))

    # Add radius circles for all schools
    for school in schools:
        color = get_color_for_status(school["status"])

        for r in radii:
            folium.Circle(
                location=[school["lat"], school["lon"]],
                radius=r['meters'],
                color=color,
                fill=True,
                fillColor=color,
                fillOpacity=r['color_mult'],
                weight=2,
                popup=f"<b>{school['name']}</b><br>{r['miles']}-mile radius"
            ).add_to(radius_groups[r['miles']])

    # Add all radius groups to map
    for r in radii:
        radius_groups[r['miles']].add_to(m)

    # Load summaries for all radii (for legend)
    summaries = {}
    for r in radii:
        summaries[r['miles']] = load_childcare_summary('all_types', r['miles'])

    # Add school markers (always visible)
    schools_group = folium.FeatureGroup(name="Schools", show=True)
    for school in schools:
        icon_color = "blue" if school["status"] == "highlight" else (
            "red" if school["status"] == "closure" else (
                "orange" if school["status"] == "bond" else "gray"
            )
        )

        # Build childcare info for all radii
        childcare_rows = []
        for r in radii:
            summary = summaries[r['miles']]
            school_summary = next((s for s in summary if s['school'] == school['name']), None)
            count = school_summary['center_count'] if school_summary else 0
            childcare_rows.append(f"{r['miles']} mi: <b>{count}</b> facilities")

        childcare_info = "<br>".join(childcare_rows)

        popup_html = f"""
        <b>{school['name']}</b><br>
        Walkable students: <b>{school['walkable_students']}</b><br>
        <hr style="margin: 5px 0;">
        <b>Childcare by radius:</b><br>
        {childcare_info}
        """

        folium.Marker(
            location=[school["lat"], school["lon"]],
            popup=folium.Popup(popup_html, max_width=300),
            icon=folium.Icon(color=icon_color, icon="graduation-cap", prefix="fa"),
        ).add_to(schools_group)

    schools_group.add_to(m)

    # Add childcare facilities
    facilities_group = folium.FeatureGroup(name="Childcare Facilities", show=True)
    for facility in facilities:
        if facility.get('lat') is None:
            continue

        # Color based on whether nearest school is Ephesus
        is_near_ephesus = 'Ephesus' in facility.get('nearest_school', '')
        marker_color = "green" if is_near_ephesus else "purple"

        phone = facility.get('phone', '')
        phone_html = f"<br>Phone: {phone}" if phone else ""

        popup_html = f"""
        <b>{facility['name']}</b><br>
        <small>{facility['address']}</small>{phone_html}<br>
        <hr style="margin: 5px 0;">
        Nearest school: {facility.get('nearest_school', 'Unknown')}<br>
        Distance: <b>{facility.get('distance_miles', 'N/A')} mi</b>
        """

        folium.Marker(
            location=[facility["lat"], facility["lon"]],
            popup=folium.Popup(popup_html, max_width=300),
            icon=folium.Icon(color=marker_color, icon="child", prefix="fa"),
        ).add_to(facilities_group)

    facilities_group.add_to(m)

    # Add layer control
    folium.LayerControl(collapsed=False).add_to(m)

    # Get Ephesus counts for all radii
    ephesus_counts = []
    for r in radii:
        summary = summaries[r['miles']]
        ephesus_summary = next((s for s in summary if 'Ephesus' in s['school']), None)
        count = ephesus_summary['center_count'] if ephesus_summary else 0
        ephesus_counts.append(f"{r['miles']} mi: {count}")

    ephesus_info = "<br>".join(ephesus_counts)

    # Add legend
    legend_html = f"""
    <div style="position: fixed; bottom: 50px; left: 50px; z-index: 1000;
                background-color: white; padding: 10px; border-radius: 5px;
                box-shadow: 2px 2px 5px rgba(0,0,0,0.3); max-width: 280px;">
        <h4 style="margin: 0 0 10px 0;">Childcare Near Schools</h4>
        <div style="margin-bottom: 5px;">
            <span style="background-color: #2E86AB; width: 12px; height: 12px; display: inline-block; margin-right: 5px; border-radius: 50%;"></span>
            Ephesus Elementary
        </div>
        <div style="margin-bottom: 5px;">
            <span style="background-color: #27AE60; width: 12px; height: 12px; display: inline-block; margin-right: 5px; border-radius: 50%;"></span>
            Childcare (near Ephesus)
        </div>
        <div style="margin-bottom: 5px;">
            <span style="background-color: #9B59B6; width: 12px; height: 12px; display: inline-block; margin-right: 5px; border-radius: 50%;"></span>
            Childcare (other areas)
        </div>
        <div style="margin-bottom: 5px;">
            <span style="background-color: #95A5A6; width: 12px; height: 12px; display: inline-block; margin-right: 5px; border-radius: 50%;"></span>
            Other Schools
        </div>
        <hr style="margin: 10px 0;">
        <div style="font-size: 11px;">
            <b>Ephesus Area Facilities:</b><br>
            {ephesus_info}
        </div>
        <div style="font-size: 10px; color: #666; margin-top: 5px;">
            Total: {len(facilities)} facilities<br>
            (Centers + Family Homes)
        </div>
    </div>
    """
    m.get_root().html.add_child(folium.Element(legend_html))

    # Add title
    title_html = """
    <div style="position: fixed; top: 10px; left: 50%; transform: translateX(-50%);
                z-index: 1000; background-color: white; padding: 10px 20px;
                border-radius: 5px; box-shadow: 2px 2px 5px rgba(0,0,0,0.3);">
        <h3 style="margin: 0;">Licensed Childcare Near CHCCS Elementary Schools</h3>
        <p style="margin: 5px 0 0 0; font-size: 12px; color: #666;">
            Use layer controls to toggle radius views (0.25, 0.5, 1.0, 2.0 miles)
        </p>
    </div>
    """
    m.get_root().html.add_child(folium.Element(title_html))

    # Save map
    map_path = ASSETS_MAPS / "childcare_map.html"
    m.save(str(map_path))
    print(f"Created: {map_path}")

    return m


def main():
    """Generate all maps."""
    print("=" * 60)
    print("CHCCS Geospatial - Generating Maps")
    print("=" * 60)

    ensure_directories()

    print("\nGenerating maps...")
    create_walkability_map()
    create_housing_map()
    create_comparison_map()
    create_childcare_map()

    print("\n" + "=" * 60)
    print("All maps created!")
    print(f"Maps saved to: {ASSETS_MAPS}")
    print("\nNote: Maps are interactive HTML files.")
    print("For the PDF report, take screenshots or use browser print-to-PDF.")
    print("\nNext step: Run report_generator.py to assemble the final PDF")
    print("=" * 60)


if __name__ == "__main__":
    main()
