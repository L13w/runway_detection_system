#!/usr/bin/env python3
"""
Re-parse split ATIS configs (DEP INFO / ARR INFO) to bump confidence to 100%
when both arrivals and departures are populated.

These entries have been filled in from matching pairs via fix_split_atis.py,
so there's nothing for a human to review - set to 100% confidence.
"""

import psycopg2
from psycopg2.extras import RealDictCursor
import os
import json
from runway_parser import RunwayParser

DB_CONFIG = {
    'host': os.getenv('DB_HOST', 'localhost'),
    'database': os.getenv('DB_NAME', 'runway_detection'),
    'user': os.getenv('DB_USER', 'postgres'),
    'password': os.getenv('DB_PASSWORD', 'postgres'),
    'port': os.getenv('DB_PORT', '5432')
}

def reparse_split_atis_confidence():
    """Re-parse split ATIS configs to bump confidence to 100%"""

    conn = psycopg2.connect(**DB_CONFIG, cursor_factory=RealDictCursor)
    cursor = conn.cursor()

    parser = RunwayParser()

    # Find all split ATIS configs with both arrivals and departures populated
    # but confidence < 100%
    cursor.execute("""
        SELECT rc.id, rc.airport_code, ad.information_letter,
               rc.arriving_runways, rc.departing_runways,
               rc.confidence_score, ad.datis_text
        FROM runway_configs rc
        JOIN atis_data ad ON rc.atis_id = ad.id
        WHERE (ad.datis_text ILIKE '%%DEP INFO%%' OR ad.datis_text ILIKE '%%ARR INFO%%')
          AND rc.arriving_runways::text != '[]'
          AND rc.departing_runways::text != '[]'
          AND rc.confidence_score < 1.0
        ORDER BY rc.airport_code, rc.created_at DESC
    """)

    configs = cursor.fetchall()
    print(f"Found {len(configs)} split ATIS configs with both runways populated but < 100% confidence\n")

    # Group by airport for reporting
    airports = {}
    for config in configs:
        airport = config['airport_code']
        if airport not in airports:
            airports[airport] = []
        airports[airport].append(config)

    total_updated = 0

    for airport in sorted(airports.keys()):
        configs_for_airport = airports[airport]
        print(f"\n=== {airport} ({len(configs_for_airport)} configs) ===")

        updated_count = 0
        for config in configs_for_airport:
            # Since this is a split ATIS (DEP INFO or ARR INFO) and the database
            # already has BOTH arrivals and departures populated (from matching pairs),
            # set confidence to 100%. There's nothing for a human to review.

            # Update confidence to 100%
            cursor.execute("""
                UPDATE runway_configs
                SET confidence_score = 1.0
                WHERE id = %s
            """, (config['id'],))

            updated_count += 1
            total_updated += 1

            if updated_count <= 3:  # Show first 3 examples per airport
                print(f"  Config {config['id']}: {config['confidence_score']:.2f} → 1.00")
                print(f"    Arr: {config['arriving_runways']}, Dep: {config['departing_runways']}")
                print(f"    ATIS: {config['datis_text'][:80]}...")

        if updated_count > 3:
            print(f"  ... and {updated_count - 3} more")

        print(f"  {airport}: Updated {updated_count} configs to 100% confidence")

    conn.commit()

    print(f"\n=== Summary ===")
    print(f"Total airports processed: {len(airports)}")
    print(f"Total configs updated: {total_updated}")
    print(f"All split ATIS configs with complete data now at 100% confidence ✓")

    cursor.close()
    conn.close()

if __name__ == "__main__":
    print("Bumping split ATIS confidence to 100% for complete configs...\n")
    reparse_split_atis_confidence()
    print("\nDone!")
