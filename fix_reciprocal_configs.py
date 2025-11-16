#!/usr/bin/env python3
"""
Find and delete runway_configs with reciprocal runways
These are bad parses that should be flagged for review
"""

import psycopg2
from psycopg2.extras import RealDictCursor
import os

DB_CONFIG = {
    'host': os.getenv('DB_HOST', 'localhost'),
    'database': os.getenv('DB_NAME', 'runway_detection'),
    'user': os.getenv('DB_USER', 'postgres'),
    'password': os.getenv('DB_PASSWORD', 'postgres'),
    'port': os.getenv('DB_PORT', '5432')
}

def detect_reciprocal_runways(runways):
    """Detect if list contains reciprocal runways"""
    if not runways or len(runways) < 2:
        return False, []

    # Extract runway numbers
    runway_data = []
    for rwy in runways:
        import re
        match = re.match(r'([0-9]{1,2})', rwy)
        if match:
            runway_data.append({'full': rwy, 'number': int(match.group(1))})

    # Check all pairs for reciprocals
    reciprocal_pairs = []
    for i in range(len(runway_data)):
        for j in range(i + 1, len(runway_data)):
            diff = abs(runway_data[i]['number'] - runway_data[j]['number'])
            if diff == 18:
                reciprocal_pairs.append(f"{runway_data[i]['full']} ↔ {runway_data[j]['full']}")

    return len(reciprocal_pairs) > 0, reciprocal_pairs

def find_and_delete_reciprocal_configs():
    """Find and delete configs with reciprocal runways"""

    conn = psycopg2.connect(**DB_CONFIG, cursor_factory=RealDictCursor)
    cursor = conn.cursor()

    print("Searching for configs with reciprocal runways...\n")

    # Get all unreviewed configs from recent collections
    cursor.execute("""
        SELECT
            rc.id,
            rc.airport_code,
            rc.arriving_runways,
            rc.departing_runways,
            rc.confidence_score,
            rc.created_at,
            ad.collected_at
        FROM runway_configs rc
        JOIN atis_data ad ON rc.atis_id = ad.id
        LEFT JOIN human_reviews hr ON rc.id = hr.runway_config_id
        WHERE hr.id IS NULL  -- Not reviewed
          AND rc.created_at > NOW() - INTERVAL '7 days'
        ORDER BY rc.airport_code, rc.created_at DESC
    """)

    all_configs = cursor.fetchall()
    print(f"Checking {len(all_configs)} unreviewed configs...")

    bad_configs = []

    for config in all_configs:
        arriving = config['arriving_runways'] or []
        departing = config['departing_runways'] or []
        all_runways = arriving + departing

        has_reciprocals, pairs = detect_reciprocal_runways(all_runways)

        if has_reciprocals:
            bad_configs.append({
                'id': config['id'],
                'airport_code': config['airport_code'],
                'arriving': arriving,
                'departing': departing,
                'reciprocal_pairs': pairs,
                'confidence': config['confidence_score'],
                'collected_at': config['collected_at']
            })

    # Group by airport for summary
    by_airport = {}
    for config in bad_configs:
        airport = config['airport_code']
        if airport not in by_airport:
            by_airport[airport] = []
        by_airport[airport].append(config)

    print(f"\nFound {len(bad_configs)} configs with reciprocal runways across {len(by_airport)} airports:\n")

    for airport, configs in sorted(by_airport.items()):
        print(f"{airport}: {len(configs)} configs")
        # Show first example
        example = configs[0]
        print(f"  Example: Arr={example['arriving']}, Dep={example['departing']}")
        print(f"  Reciprocals: {', '.join(example['reciprocal_pairs'])}")
        print()

    if bad_configs:
        print(f"\nDeleting {len(bad_configs)} configs with reciprocal runways...")

        config_ids = [config['id'] for config in bad_configs]

        cursor.execute("""
            DELETE FROM runway_configs
            WHERE id = ANY(%s)
        """, (config_ids,))

        deleted_count = cursor.rowcount
        conn.commit()

        print(f"✓ Deleted {deleted_count} bad configs")
        print("\nThese airports will show improved data in the review queue.")

    cursor.close()
    conn.close()

    return bad_configs

if __name__ == "__main__":
    print("=" * 70)
    print("Fix Reciprocal Runway Configs")
    print("=" * 70)
    print()

    bad_configs = find_and_delete_reciprocal_configs()

    if not bad_configs:
        print("✓ No configs with reciprocal runways found!")
    else:
        print("\n✓ Done! Parser will need to be improved to prevent these in future.")
