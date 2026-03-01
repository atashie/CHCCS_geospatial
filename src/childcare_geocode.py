"""
Childcare Facility Geocoding and Distance Calculation

Geocodes childcare facility addresses and calculates distances to CHCCS schools.
Supports multiple radius values and facility types (centers, family homes, or all).

Usage:
    python src/childcare_geocode.py                    # Run analysis for all types
    python src/childcare_geocode.py --type centers     # Centers only
    python src/childcare_geocode.py --type family      # Family homes only
    python src/childcare_geocode.py --type all         # Combined analysis
    python src/childcare_geocode.py --geocode          # Force re-geocoding

Input:
    data/raw/childcare/childcare_centers_raw.csv
    data/raw/childcare/childcare_family_homes_raw.csv
    data/raw/childcare/childcare_all_raw.csv
    data/processed/school_locations.csv

Output (in subdirectories by type):
    data/processed/centers/childcare_by_school_*.csv
    data/processed/family_homes/childcare_by_school_*.csv
    data/processed/all_types/childcare_by_school_*.csv
    data/processed/childcare_master_comparison.csv
"""

import csv
import math
import time
from pathlib import Path
from typing import Optional, Tuple

try:
    from geopy.geocoders import Nominatim
    from geopy.extra.rate_limiter import RateLimiter
    GEOPY_AVAILABLE = True
except ImportError:
    GEOPY_AVAILABLE = False
    print("Warning: geopy not installed. Run: pip install geopy>=2.4")

# Project paths
PROJECT_ROOT = Path(__file__).parent.parent
DATA_RAW_CHILDCARE = PROJECT_ROOT / "data" / "raw" / "childcare"
DATA_PROCESSED = PROJECT_ROOT / "data" / "processed"

# Distance thresholds in miles for multi-radius analysis
RADIUS_VALUES = [0.25, 0.5, 1.0, 2.0]
DEFAULT_RADIUS = 0.5

# Earth's radius in miles (for haversine calculation)
EARTH_RADIUS_MILES = 3959

# Facility type configurations
FACILITY_TYPES = {
    'centers': {
        'name': 'Child Care Centers',
        'raw_file': 'childcare_centers_raw.csv',
        'output_dir': 'centers',
        'filter_value': 'Child Care Center'
    },
    'family': {
        'name': 'Family Child Care Homes',
        'raw_file': 'childcare_family_homes_raw.csv',
        'output_dir': 'family_homes',
        'filter_value': 'Family Child Care Home'
    },
    'all': {
        'name': 'All Childcare Facilities',
        'raw_file': 'childcare_all_raw.csv',
        'output_dir': 'all_types',
        'filter_value': None  # No filtering
    }
}


def ensure_directories():
    """Create output directories if they don't exist."""
    DATA_PROCESSED.mkdir(parents=True, exist_ok=True)


def haversine_distance(lat1: float, lon1: float, lat2: float, lon2: float) -> float:
    """
    Calculate haversine distance between two points in miles.

    Args:
        lat1, lon1: Coordinates of first point
        lat2, lon2: Coordinates of second point

    Returns:
        Distance in miles
    """
    # Convert to radians
    lat1_rad = math.radians(lat1)
    lat2_rad = math.radians(lat2)
    dlat = math.radians(lat2 - lat1)
    dlon = math.radians(lon2 - lon1)

    # Haversine formula
    a = math.sin(dlat / 2) ** 2 + math.cos(lat1_rad) * math.cos(lat2_rad) * math.sin(dlon / 2) ** 2
    c = 2 * math.asin(math.sqrt(a))

    return EARTH_RADIUS_MILES * c


def load_schools() -> list:
    """Load school locations from NCES cache CSV."""
    schools = []
    school_file = PROJECT_ROOT / "data" / "cache" / "nces_school_locations.csv"

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


def load_childcare_centers(facility_type_key: str = 'all') -> list:
    """
    Load childcare facilities from raw CSV.

    Args:
        facility_type_key: One of 'centers', 'family', or 'all'

    Returns:
        List of facility dictionaries
    """
    centers = []

    type_config = FACILITY_TYPES.get(facility_type_key, FACILITY_TYPES['all'])
    centers_file = DATA_RAW_CHILDCARE / type_config['raw_file']

    # Fall back to legacy file if type-specific file doesn't exist
    if not centers_file.exists():
        legacy_file = DATA_RAW_CHILDCARE / "childcare_raw.csv"
        if legacy_file.exists():
            print(f"Note: Using legacy file {legacy_file}")
            centers_file = legacy_file
        else:
            print(f"Error: Childcare file not found: {centers_file}")
            print("Run childcare_scraper.py first to generate this file.")
            return centers

    with open(centers_file, 'r', encoding='utf-8') as f:
        reader = csv.DictReader(f)
        for row in reader:
            # Skip entries without addresses (can't geocode)
            address = row.get('address', '').strip()
            if not address:
                continue

            centers.append({
                'name': row['name'],
                'license_number': row.get('license_number', ''),
                'address': address,
                'city': row.get('city', ''),
                'state': row.get('state', 'NC'),
                'zip_code': row.get('zip_code', ''),
                'phone': row.get('phone', ''),
                'star_rating': row.get('star_rating') or None,
                'capacity': row.get('capacity') or None,
                'facility_type': row.get('facility_type', 'Child Care Center'),
                'source_zip': row.get('source_zip', '')
            })

    return centers


def geocode_address(geocoder, full_address: str) -> Optional[Tuple[float, float]]:
    """
    Geocode a full address to lat/lon coordinates.

    Args:
        geocoder: Geopy geocoder instance
        full_address: Full address string (e.g., "123 Main St, Chapel Hill, NC 27514")

    Returns:
        Tuple of (latitude, longitude) or None if geocoding fails
    """
    if not full_address or not full_address.strip():
        return None

    try:
        location = geocoder(full_address)
        if location:
            return (location.latitude, location.longitude)
    except Exception as e:
        print(f"  Geocoding error for '{full_address[:50]}...': {e}")

    # Try with just city, state if full address fails
    try:
        # Extract city and state from address
        import re
        match = re.search(r',\s*([A-Za-z\s]+),\s*NC', full_address)
        if match:
            fallback = f"{match.group(1)}, NC"
            location = geocoder(fallback)
            if location:
                return (location.latitude, location.longitude)
    except Exception:
        pass

    return None


def load_geocoded_centers(facility_type_key: str = 'all') -> list:
    """
    Load previously geocoded centers from CSV.

    Args:
        facility_type_key: One of 'centers', 'family', or 'all'

    Returns:
        List of center dictionaries with lat/lon, or empty list if file not found
    """
    type_config = FACILITY_TYPES.get(facility_type_key, FACILITY_TYPES['all'])
    output_dir = DATA_PROCESSED / type_config['output_dir']
    geocoded_file = output_dir / "childcare_geocoded.csv"

    # Fall back to legacy location
    if not geocoded_file.exists():
        legacy_file = DATA_PROCESSED / "childcare_geocoded.csv"
        if legacy_file.exists():
            geocoded_file = legacy_file
        else:
            return []

    centers = []
    with open(geocoded_file, 'r', encoding='utf-8') as f:
        reader = csv.DictReader(f)
        for row in reader:
            center = dict(row)
            # Convert lat/lon to floats
            if center.get('lat') and center['lat'] != '':
                center['lat'] = float(center['lat'])
            else:
                center['lat'] = None
            if center.get('lon') and center['lon'] != '':
                center['lon'] = float(center['lon'])
            else:
                center['lon'] = None
            # Convert geocoded to boolean
            center['geocoded'] = center.get('geocoded', '').lower() == 'true'
            centers.append(center)

    return centers


def geocode_centers(centers: list) -> list:
    """
    Geocode all childcare centers.

    Args:
        centers: List of center dictionaries

    Returns:
        List of centers with lat/lon added
    """
    if not GEOPY_AVAILABLE:
        print("Error: geopy not available for geocoding")
        return centers

    # Initialize geocoder with rate limiting (1 request per second)
    geolocator = Nominatim(user_agent="chccs_geospatial_analysis")
    geocoder = RateLimiter(geolocator.geocode, min_delay_seconds=1.0)

    print(f"\nGeocoding {len(centers)} childcare centers...")
    print("(This may take a few minutes due to rate limiting)")

    geocoded = []
    success_count = 0
    fail_count = 0

    for i, center in enumerate(centers, 1):
        print(f"  [{i}/{len(centers)}] {center['name'][:40]}...", end=" ")

        coords = geocode_address(geocoder, center['address'])

        if coords:
            center['lat'] = coords[0]
            center['lon'] = coords[1]
            center['geocoded'] = True
            success_count += 1
            print(f"OK ({coords[0]:.4f}, {coords[1]:.4f})")
        else:
            center['lat'] = None
            center['lon'] = None
            center['geocoded'] = False
            fail_count += 1
            print("FAILED")

        geocoded.append(center)

    print(f"\nGeocoding complete: {success_count} succeeded, {fail_count} failed")

    return geocoded


def calculate_distances(centers: list, schools: list) -> list:
    """
    Calculate distance from each center to all schools.
    Add nearest school and distance to each center.

    Args:
        centers: List of geocoded centers
        schools: List of school locations

    Returns:
        List of centers with distance calculations added
    """
    for center in centers:
        if center.get('lat') is None or center.get('lon') is None:
            center['nearest_school'] = None
            center['distance_miles'] = None
            center['schools_within_range'] = []
            continue

        min_distance = float('inf')
        nearest_school = None
        schools_within_range = []

        for school in schools:
            distance = haversine_distance(
                center['lat'], center['lon'],
                school['lat'], school['lon']
            )

            if distance < min_distance:
                min_distance = distance
                nearest_school = school['name']

            if distance <= DEFAULT_RADIUS:
                schools_within_range.append({
                    'school': school['name'],
                    'distance': round(distance, 3)
                })

        center['nearest_school'] = nearest_school
        center['distance_miles'] = round(min_distance, 3)
        center['schools_within_range'] = schools_within_range

    return centers


def generate_school_summary(centers: list, schools: list, radius_miles: float = DEFAULT_RADIUS) -> list:
    """
    Generate summary of childcare centers by school within a given radius.

    Args:
        centers: List of centers with distance calculations
        schools: List of schools
        radius_miles: Distance threshold in miles (default 0.5)

    Returns:
        List of school summaries
    """
    summary = []

    for school in schools:
        nearby_centers = []
        total_capacity = 0

        for center in centers:
            if center.get('lat') is None:
                continue

            distance = haversine_distance(
                center['lat'], center['lon'],
                school['lat'], school['lon']
            )

            if distance <= radius_miles:
                nearby_centers.append(center)
                capacity = center.get('capacity')
                if capacity:
                    try:
                        total_capacity += int(capacity)
                    except (ValueError, TypeError):
                        pass

        summary.append({
            'school': school['name'],
            'lat': school['lat'],
            'lon': school['lon'],
            'center_count': len(nearby_centers),
            'total_capacity': total_capacity
        })

    # Sort by center count descending
    summary.sort(key=lambda x: x['center_count'], reverse=True)

    return summary


def generate_comparison_table(centers: list, schools: list, radii: list = RADIUS_VALUES) -> list:
    """
    Generate comparison table showing center counts at each radius for all schools.

    Args:
        centers: List of geocoded centers
        schools: List of schools
        radii: List of radius values in miles

    Returns:
        List of comparison rows (one per school)
    """
    comparison = []

    for school in schools:
        row = {'school': school['name']}

        for radius in radii:
            center_count = 0
            for center in centers:
                if center.get('lat') is None:
                    continue

                distance = haversine_distance(
                    center['lat'], center['lon'],
                    school['lat'], school['lon']
                )

                if distance <= radius:
                    center_count += 1

            # Format column name like "centers_0.25mi"
            col_name = f"centers_{radius}mi"
            row[col_name] = center_count

        comparison.append(row)

    # Sort by school name for consistency
    comparison.sort(key=lambda x: x['school'])

    return comparison


def save_comparison_table(comparison: list, filepath: Path, radii: list = RADIUS_VALUES):
    """Save comparison table to CSV."""
    fieldnames = ['school'] + [f"centers_{r}mi" for r in radii]

    with open(filepath, 'w', newline='', encoding='utf-8') as f:
        writer = csv.DictWriter(f, fieldnames=fieldnames)
        writer.writeheader()
        writer.writerows(comparison)

    print(f"Saved comparison table to: {filepath}")


def save_geocoded_centers(centers: list, filepath: Path):
    """Save geocoded centers to CSV."""
    fieldnames = ['name', 'license_number', 'address', 'city', 'state', 'zip_code',
                  'lat', 'lon', 'phone', 'star_rating', 'capacity', 'facility_type',
                  'geocoded']

    with open(filepath, 'w', newline='', encoding='utf-8') as f:
        writer = csv.DictWriter(f, fieldnames=fieldnames, extrasaction='ignore')
        writer.writeheader()
        writer.writerows(centers)

    print(f"Saved geocoded centers to: {filepath}")


def save_centers_detail(centers: list, filepath: Path):
    """Save detailed center data with school matches."""
    fieldnames = ['center_name', 'license_number', 'address', 'center_lat', 'center_lon',
                  'phone', 'capacity', 'star_rating', 'nearest_school', 'distance_miles']

    rows = []
    for center in centers:
        if center.get('lat') is not None:
            rows.append({
                'center_name': center['name'],
                'license_number': center.get('license_number', ''),
                'address': center['address'],
                'center_lat': center['lat'],
                'center_lon': center['lon'],
                'phone': center.get('phone', ''),
                'capacity': center.get('capacity', 'N/A'),
                'star_rating': center.get('star_rating', 'N/A'),
                'nearest_school': center.get('nearest_school', 'N/A'),
                'distance_miles': center.get('distance_miles', 'N/A')
            })

    # Sort by distance
    rows.sort(key=lambda x: x['distance_miles'] if isinstance(x['distance_miles'], (int, float)) else 999)

    with open(filepath, 'w', newline='', encoding='utf-8') as f:
        writer = csv.DictWriter(f, fieldnames=fieldnames)
        writer.writeheader()
        writer.writerows(rows)

    print(f"Saved center details to: {filepath}")


def save_school_summary(summary: list, filepath: Path):
    """Save school summary to CSV."""
    fieldnames = ['school', 'lat', 'lon', 'center_count', 'total_capacity']

    with open(filepath, 'w', newline='', encoding='utf-8') as f:
        writer = csv.DictWriter(f, fieldnames=fieldnames)
        writer.writeheader()
        writer.writerows(summary)

    print(f"Saved school summary to: {filepath}")


def print_summary(summary: list, radius_miles: float = DEFAULT_RADIUS):
    """Print summary table to console."""
    print("\n" + "=" * 70)
    print(f"CHILDCARE CENTERS WITHIN {radius_miles} MILES OF EACH SCHOOL")
    print("=" * 70)
    print(f"{'School':<35} {'Centers':>10} {'Capacity':>12}")
    print("-" * 70)

    for row in summary:
        capacity_str = str(row['total_capacity']) if row['total_capacity'] else "N/A"
        print(f"{row['school']:<35} {row['center_count']:>10} {capacity_str:>12}")

    print("-" * 70)



def print_comparison_summary(comparison: list, radii: list = RADIUS_VALUES):
    """Print comparison table to console."""
    print("\n" + "=" * 80)
    print("CHILDCARE CENTER COMPARISON BY RADIUS")
    print("=" * 80)

    # Header
    header = f"{'School':<35}"
    for r in radii:
        header += f" {r}mi".rjust(8)
    print(header)
    print("-" * 80)

    # Data rows
    for row in comparison:
        line = f"{row['school']:<35}"
        for r in radii:
            col = f"centers_{r}mi"
            line += f" {row[col]}".rjust(8)
        print(line)

    print("-" * 80)



def run_multi_radius_analysis(centers: list, schools: list, output_dir: Path = None, type_label: str = "facilities"):
    """
    Run analysis for multiple radius values and generate comparison outputs.

    Args:
        centers: List of geocoded centers
        schools: List of schools
        output_dir: Directory to save outputs (defaults to DATA_PROCESSED)
        type_label: Label for the facility type (for display)

    Returns:
        Comparison table data
    """
    if output_dir is None:
        output_dir = DATA_PROCESSED

    # Ensure output directory exists
    output_dir.mkdir(parents=True, exist_ok=True)

    print("\n" + "=" * 60)
    print(f"MULTI-RADIUS ANALYSIS: {type_label.upper()}")
    print("=" * 60)
    print(f"Analyzing distances at: {', '.join(f'{r}mi' for r in RADIUS_VALUES)}")

    # Generate summary for each radius
    for radius in RADIUS_VALUES:
        print(f"\nProcessing {radius}-mile radius...")
        summary = generate_school_summary(centers, schools, radius_miles=radius)

        # Save to radius-specific file
        filename = f"childcare_by_school_{radius}mi.csv"
        filepath = output_dir / filename
        save_school_summary(summary, filepath)

        # Print summary for this radius
        print_summary(summary, radius_miles=radius)

    # Generate and save comparison table
    print("\nGenerating comparison table...")
    comparison = generate_comparison_table(centers, schools, RADIUS_VALUES)
    comparison_path = output_dir / "comparison.csv"
    save_comparison_table(comparison, comparison_path, RADIUS_VALUES)

    # Print comparison summary
    print_comparison_summary(comparison, RADIUS_VALUES)

    return comparison


def generate_master_comparison(schools: list, radii: list = RADIUS_VALUES) -> list:
    """
    Generate master comparison table showing all facility types side-by-side.

    Args:
        schools: List of schools
        radii: List of radius values

    Returns:
        List of comparison rows with columns for each type and radius
    """
    comparison = []

    for school in schools:
        row = {'school': school['name']}

        # Load data for each facility type
        for type_key, type_config in FACILITY_TYPES.items():
            output_dir = DATA_PROCESSED / type_config['output_dir']

            for radius in radii:
                # Try to load the summary file for this type and radius
                summary_file = output_dir / f"childcare_by_school_{radius}mi.csv"
                count = 0

                if summary_file.exists():
                    with open(summary_file, 'r', encoding='utf-8') as f:
                        reader = csv.DictReader(f)
                        for summary_row in reader:
                            if summary_row['school'] == school['name']:
                                count = int(summary_row.get('center_count', 0))
                                break

                # Column name like "centers_0.5mi" or "family_0.5mi"
                col_name = f"{type_key}_{radius}mi"
                row[col_name] = count

        comparison.append(row)

    # Sort by school name
    comparison.sort(key=lambda x: x['school'])

    return comparison


def save_master_comparison(comparison: list, filepath: Path, radii: list = RADIUS_VALUES):
    """Save master comparison table to CSV."""
    # Build fieldnames: school, then for each radius: centers, family, all
    fieldnames = ['school']
    for radius in radii:
        for type_key in ['centers', 'family', 'all']:
            fieldnames.append(f"{type_key}_{radius}mi")

    with open(filepath, 'w', newline='', encoding='utf-8') as f:
        writer = csv.DictWriter(f, fieldnames=fieldnames)
        writer.writeheader()
        writer.writerows(comparison)

    print(f"Saved master comparison to: {filepath}")


def process_facility_type(type_key: str, schools: list, force_geocode: bool = False):
    """
    Process a single facility type: geocode and run analysis.

    Args:
        type_key: One of 'centers', 'family', or 'all'
        schools: List of schools
        force_geocode: Whether to force re-geocoding

    Returns:
        List of geocoded facilities with valid coordinates
    """
    type_config = FACILITY_TYPES[type_key]
    output_dir = DATA_PROCESSED / type_config['output_dir']
    output_dir.mkdir(parents=True, exist_ok=True)

    print(f"\n{'='*60}")
    print(f"Processing: {type_config['name']}")
    print("=" * 60)

    # Check for existing geocoded data
    geocoded_centers = []
    if not force_geocode:
        geocoded_centers = load_geocoded_centers(type_key)
        if geocoded_centers:
            print(f"  Loaded {len(geocoded_centers)} previously geocoded facilities")
            print("  (Use --geocode flag to force re-geocoding)")

    # Geocode if needed
    geocoded_path = output_dir / "childcare_geocoded.csv"
    if not geocoded_centers or force_geocode:
        centers = load_childcare_centers(type_key)
        print(f"  Loaded {len(centers)} facilities from raw data")

        if not centers:
            print(f"Warning: No facilities found for {type_config['name']}")
            return []

        # Geocode centers
        geocoded_centers = geocode_centers(centers)

        # Save geocoded data
        save_geocoded_centers(geocoded_centers, geocoded_path)

    # Filter to only geocoded centers
    valid_centers = [c for c in geocoded_centers if c.get('lat') is not None]
    print(f"  {len(valid_centers)} facilities with valid coordinates")

    # Calculate distances (for detail output)
    print("\nCalculating distances to schools...")
    centers_with_distances = calculate_distances(geocoded_centers, schools)

    # Save detailed center data
    detail_path = output_dir / "childcare_detail.csv"
    save_centers_detail(centers_with_distances, detail_path)

    # Run multi-radius analysis
    run_multi_radius_analysis(valid_centers, schools, output_dir, type_config['name'])

    return valid_centers


def main():
    """Main function to geocode facilities and calculate distances."""
    import argparse

    parser = argparse.ArgumentParser(description='Geocode childcare facilities and calculate distances to schools')
    parser.add_argument('--geocode', action='store_true', help='Force re-geocoding of addresses')
    parser.add_argument('--type', choices=['centers', 'family', 'all'], default=None,
                        help='Facility type to process (default: process all types)')
    args = parser.parse_args()

    print("=" * 60)
    print("Childcare Facility Geocoding and Distance Analysis")
    print("=" * 60)

    ensure_directories()

    # Load school data
    print("\nLoading school data...")
    schools = load_schools()
    print(f"  Loaded {len(schools)} schools")

    if not schools:
        print("Error: Missing school locations file")
        return

    # Determine which types to process
    if args.type:
        types_to_process = [args.type]
    else:
        # Process all types
        types_to_process = ['centers', 'family', 'all']

    # Process each facility type
    results = {}
    for type_key in types_to_process:
        valid_centers = process_facility_type(type_key, schools, args.geocode)
        results[type_key] = valid_centers

    # Generate master comparison (only if we processed all types)
    if len(types_to_process) == 3:
        print("\n" + "=" * 60)
        print("GENERATING MASTER COMPARISON")
        print("=" * 60)
        master_comparison = generate_master_comparison(schools, RADIUS_VALUES)
        master_path = DATA_PROCESSED / "childcare_master_comparison.csv"
        save_master_comparison(master_comparison, master_path, RADIUS_VALUES)

        # Print master comparison summary
        print("\nMaster comparison by school (0.5mi radius):")
        print("-" * 70)
        print(f"{'School':<35} {'Centers':>10} {'Family':>10} {'Total':>10}")
        print("-" * 70)
        for row in master_comparison:
            print(f"{row['school']:<35} {row.get('centers_0.5mi', 0):>10} "
                  f"{row.get('family_0.5mi', 0):>10} {row.get('all_0.5mi', 0):>10}")

    # Output summary
    print("\n" + "=" * 60)
    print("Analysis complete!")
    print("=" * 60)
    print("\nOutput directories:")
    for type_key in types_to_process:
        type_config = FACILITY_TYPES[type_key]
        output_dir = DATA_PROCESSED / type_config['output_dir']
        print(f"  - {output_dir}/")

    if len(types_to_process) == 3:
        print(f"\nMaster comparison: {DATA_PROCESSED / 'childcare_master_comparison.csv'}")

    print("\nNext step: Run maps.py to create interactive childcare map")
    print("=" * 60)


if __name__ == "__main__":
    main()
