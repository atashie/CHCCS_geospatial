"""
CHCCS District Map Visualizations

Generates interactive maps showing:
- School locations with walkability zones
- School location comparison
- Childcare facility proximity
"""

import folium
from folium import plugins
import pandas as pd
import csv
from pathlib import Path

# Project paths
PROJECT_ROOT = Path(__file__).parent.parent
DATA_CACHE = PROJECT_ROOT / "data" / "cache"
DATA_PROCESSED = PROJECT_ROOT / "data" / "processed"
DATA_RAW = PROJECT_ROOT / "data" / "raw"
ASSETS_MAPS = PROJECT_ROOT / "assets" / "maps"

# Chapel Hill center coordinates
CHAPEL_HILL_CENTER = [35.9132, -79.0558]

# Uniform school color
SCHOOL_COLOR = "#2E86AB"
CHILDCARE_COLOR = "#9B59B6"


def ensure_directories():
    """Create output directories if they don't exist."""
    ASSETS_MAPS.mkdir(parents=True, exist_ok=True)


def load_schools():
    """Load school locations from NCES cache CSV."""
    schools = []
    school_file = DATA_CACHE / "nces_school_locations.csv"

    if not school_file.exists():
        print(f"Error: School locations file not found: {school_file}")
        print("Run road_pollution.py first to download from NCES.")
        return schools

    with open(school_file, 'r', encoding='utf-8') as f:
        reader = csv.DictReader(f)
        for row in reader:
            schools.append({
                'name': row['school'],
                'lat': float(row['lat']),
                'lon': float(row['lon']),
                'address': row.get('address', '')
            })

    return schools



def create_walkability_map():
    """
    Create map showing 0.5-mile walkability zones around all schools.
    """
    m = folium.Map(location=CHAPEL_HILL_CENTER, zoom_start=13, tiles="cartodbpositron")

    schools = load_schools()
    if not schools:
        print("Warning: No school data available. Skipping walkability map.")
        return m

    # Add legend
    legend_html = """
    <div style="position: fixed; bottom: 50px; left: 50px; z-index: 1000;
                background-color: white; padding: 10px; border-radius: 5px;
                box-shadow: 2px 2px 5px rgba(0,0,0,0.3);">
        <h4 style="margin: 0 0 10px 0;">Legend</h4>
        <div><span style="background-color: #2E86AB; width: 15px; height: 15px; display: inline-block; margin-right: 5px;"></span> Elementary Schools</div>
        <div style="font-size: 11px; color: #666; margin-top: 5px;">Circles show 0.5-mile radius</div>
    </div>
    """
    m.get_root().html.add_child(folium.Element(legend_html))

    for school in schools:
        # Add 0.5-mile radius circle (approximately 805 meters)
        folium.Circle(
            location=[school["lat"], school["lon"]],
            radius=805,
            color=SCHOOL_COLOR,
            fill=True,
            fillColor=SCHOOL_COLOR,
            fillOpacity=0.2,
            weight=2,
            popup=f"<b>{school['name']}</b><br>0.5-mile walkability zone"
        ).add_to(m)

        # Add school marker
        popup_html = f"""
        <b>{school['name']}</b><br>
        <small>{school['address']}</small>
        """

        folium.Marker(
            location=[school["lat"], school["lon"]],
            popup=folium.Popup(popup_html, max_width=250),
            icon=folium.Icon(color="blue", icon="graduation-cap", prefix="fa"),
        ).add_to(m)

    # Add title
    title_html = """
    <div style="position: fixed; top: 10px; left: 50%; transform: translateX(-50%);
                z-index: 1000; background-color: white; padding: 10px 20px;
                border-radius: 5px; box-shadow: 2px 2px 5px rgba(0,0,0,0.3);">
        <h3 style="margin: 0;">CHCCS Elementary Schools: Walkability Zones</h3>
        <p style="margin: 5px 0 0 0; font-size: 12px; color: #666;">
            0.5-mile radius around each school
        </p>
    </div>
    """
    m.get_root().html.add_child(folium.Element(title_html))

    # Save map
    map_path = ASSETS_MAPS / "walkability_map.html"
    m.save(str(map_path))
    print(f"Created: {map_path}")

    return m



def create_comparison_map():
    """
    Create map showing all elementary school locations.
    """
    m = folium.Map(location=CHAPEL_HILL_CENTER, zoom_start=12, tiles="cartodbpositron")

    schools = load_schools()
    if not schools:
        print("Warning: No school data available. Skipping comparison map.")
        return m

    schools_group = folium.FeatureGroup(name="Elementary Schools")

    for school in schools:
        popup_html = f"""
        <b>{school['name']}</b><br>
        <small>{school['address']}</small>
        """

        marker = folium.Marker(
            location=[school["lat"], school["lon"]],
            popup=folium.Popup(popup_html, max_width=250),
            icon=folium.DivIcon(
                html=f"""
                <div style="background-color: {SCHOOL_COLOR}; color: white; padding: 5px 10px;
                            border-radius: 15px; font-weight: bold; white-space: nowrap;
                            box-shadow: 2px 2px 5px rgba(0,0,0,0.3); font-size: 11px;">
                    {school['name'].replace(' Elementary', '')}
                </div>
                """,
                icon_size=(120, 30),
                icon_anchor=(60, 15)
            )
        )
        marker.add_to(schools_group)

    schools_group.add_to(m)
    folium.LayerControl().add_to(m)

    # Add title
    title_html = """
    <div style="position: fixed; top: 10px; left: 50%; transform: translateX(-50%);
                z-index: 1000; background-color: white; padding: 10px 20px;
                border-radius: 5px; box-shadow: 2px 2px 5px rgba(0,0,0,0.3);">
        <h3 style="margin: 0;">CHCCS Elementary Schools</h3>
        <p style="margin: 5px 0 0 0; font-size: 12px; color: #666;">
            All 11 district elementary schools
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
    - All schools with multiple radius circles (0.25, 0.5, 1.0, 2.0 mi)
    - Childcare centers and family homes as markers
    - Layer controls to toggle radius views
    - Popups with facility details
    """
    m = folium.Map(location=CHAPEL_HILL_CENTER, zoom_start=12, tiles="cartodbpositron")

    schools = load_schools()
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
        for r in radii:
            folium.Circle(
                location=[school["lat"], school["lon"]],
                radius=r['meters'],
                color=SCHOOL_COLOR,
                fill=True,
                fillColor=SCHOOL_COLOR,
                fillOpacity=r['color_mult'],
                weight=2,
                popup=f"<b>{school['name']}</b><br>{r['miles']}-mile radius"
            ).add_to(radius_groups[r['miles']])

    # Add all radius groups to map
    for r in radii:
        radius_groups[r['miles']].add_to(m)

    # Load summaries for all radii (for popups)
    summaries = {}
    for r in radii:
        summaries[r['miles']] = load_childcare_summary('all_types', r['miles'])

    # Add school markers (always visible)
    schools_group = folium.FeatureGroup(name="Schools", show=True)
    for school in schools:
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
        <small>{school['address']}</small><br>
        <hr style="margin: 5px 0;">
        <b>Childcare by radius:</b><br>
        {childcare_info}
        """

        folium.Marker(
            location=[school["lat"], school["lon"]],
            popup=folium.Popup(popup_html, max_width=300),
            icon=folium.Icon(color="blue", icon="graduation-cap", prefix="fa"),
        ).add_to(schools_group)

    schools_group.add_to(m)

    # Add childcare facilities
    facilities_group = folium.FeatureGroup(name="Childcare Facilities", show=True)
    for facility in facilities:
        if facility.get('lat') is None:
            continue

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
            icon=folium.Icon(color="purple", icon="child", prefix="fa"),
        ).add_to(facilities_group)

    facilities_group.add_to(m)

    # Add layer control
    folium.LayerControl(collapsed=False).add_to(m)

    # Add legend
    legend_html = f"""
    <div style="position: fixed; bottom: 50px; left: 50px; z-index: 1000;
                background-color: white; padding: 10px; border-radius: 5px;
                box-shadow: 2px 2px 5px rgba(0,0,0,0.3); max-width: 280px;">
        <h4 style="margin: 0 0 10px 0;">Childcare Near Schools</h4>
        <div style="margin-bottom: 5px;">
            <span style="background-color: #2E86AB; width: 12px; height: 12px; display: inline-block; margin-right: 5px; border-radius: 50%;"></span>
            Elementary Schools
        </div>
        <div style="margin-bottom: 5px;">
            <span style="background-color: #9B59B6; width: 12px; height: 12px; display: inline-block; margin-right: 5px; border-radius: 50%;"></span>
            Childcare Facilities
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
    create_comparison_map()
    create_childcare_map()

    print("\n" + "=" * 60)
    print("All maps created!")
    print(f"Maps saved to: {ASSETS_MAPS}")
    print("\nNote: Maps are interactive HTML files.")
    print("=" * 60)


if __name__ == "__main__":
    main()
