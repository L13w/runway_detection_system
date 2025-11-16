#!/usr/bin/env python3
"""
Fix airports with split DEP INFO / ARR INFO ATIS
Matches pairs of configs for same airport and fills in missing arrivals/departures
"""

import psycopg2
from psycopg2.extras import RealDictCursor
import os
import json
from datetime import timedelta

DB_CONFIG = {
    'host': os.getenv('DB_HOST', 'localhost'),
    'database': os.getenv('DB_NAME', 'runway_detection'),
    'user': os.getenv('DB_USER', 'postgres'),
    'password': os.getenv('DB_PASSWORD', 'postgres'),
    'port': os.getenv('DB_PORT', '5432')
}

def fix_split_atis_configs():
    """Match and merge split DEP/ARR INFO configs"""

    conn = psycopg2.connect(**DB_CONFIG, cursor_factory=RealDictCursor)
    cursor = conn.cursor()

    print("Finding airports with split DEP/ARR INFO pattern...")

    # Get all airports with DEP INFO or ARR INFO
    cursor.execute("""
        SELECT DISTINCT rc.airport_code
        FROM runway_configs rc
        JOIN atis_data ad ON rc.atis_id = ad.id
        WHERE ad.datis_text LIKE '%%DEP INFO%%'
           OR ad.datis_text LIKE '%%ARR INFO%%'
        ORDER BY rc.airport_code
    """)

    airports = [row['airport_code'] for row in cursor.fetchall()]
    print(f"Found {len(airports)} airports with split ATIS: {', '.join(airports)}\n")

    total_fixed = 0

    for airport in airports:
        print(f"\n=== Processing {airport} ===")

        # Get all configs with empty arrivals or departures
        cursor.execute("""
            SELECT
                rc.id,
                rc.airport_code,
                ad.information_letter,
                rc.arriving_runways,
                rc.departing_runways,
                rc.confidence_score,
                ad.datis_text,
                ad.collected_at,
                CASE
                    WHEN ad.datis_text LIKE '%%DEP INFO%%' THEN 'DEP'
                    WHEN ad.datis_text LIKE '%%ARR INFO%%' THEN 'ARR'
                    ELSE 'UNKNOWN'
                END as info_type
            FROM runway_configs rc
            JOIN atis_data ad ON rc.atis_id = ad.id
            WHERE rc.airport_code = %s
              AND (rc.arriving_runways::text = '[]' OR rc.departing_runways::text = '[]')
            ORDER BY ad.collected_at DESC
        """, (airport,))

        configs = cursor.fetchall()
        print(f"  Found {len(configs)} configs with empty fields")

        fixed_count = 0

        for config in configs:
            # Skip if both are empty (no match will help)
            if not config['arriving_runways'] and not config['departing_runways']:
                continue

            # Determine what we're looking for
            need_arrivals = not config['arriving_runways']
            need_departures = not config['departing_runways']

            # Look for matching config within ±10 minutes
            time_window_start = config['collected_at'] - timedelta(minutes=10)
            time_window_end = config['collected_at'] + timedelta(minutes=10)

            if need_arrivals:
                # Look for ARR INFO with arrivals
                cursor.execute("""
                    SELECT rc.id, rc.arriving_runways, ad.information_letter, ad.collected_at
                    FROM runway_configs rc
                    JOIN atis_data ad ON rc.atis_id = ad.id
                    WHERE rc.airport_code = %s
                      AND ad.collected_at BETWEEN %s AND %s
                      AND ad.datis_text LIKE '%%ARR INFO%%'
                      AND rc.arriving_runways::text != '[]'
                      AND rc.id != %s
                    ORDER BY ABS(EXTRACT(EPOCH FROM (ad.collected_at - %s::timestamp)))
                    LIMIT 1
                """, (airport, time_window_start, time_window_end, config['id'], config['collected_at']))

                match = cursor.fetchone()
                if match:
                    # Update with arrivals from matching ARR INFO
                    # Set merged_from_pair and component confidence
                    arr_conf = 0.9  # Arrivals from matched ARR INFO
                    dep_conf = config['confidence_score'] or 0.9  # Departures from this DEP INFO
                    overall_conf = min(arr_conf, dep_conf)

                    cursor.execute("""
                        UPDATE runway_configs
                        SET arriving_runways = %s,
                            confidence_score = %s,
                            merged_from_pair = TRUE,
                            component_confidence = %s
                        WHERE id = %s
                    """, (
                        json.dumps(match['arriving_runways']),
                        overall_conf,
                        json.dumps({"arrivals": arr_conf, "departures": dep_conf}),
                        config['id']
                    ))

                    fixed_count += 1
                    print(f"    ✓ Config {config['id']} ({config['info_type']} INFO {config['information_letter']}): "
                          f"Added arrivals {match['arriving_runways']} from ARR INFO {match['information_letter']} (conf: {overall_conf})")

            if need_departures:
                # Look for DEP INFO with departures
                cursor.execute("""
                    SELECT rc.id, rc.departing_runways, ad.information_letter, ad.collected_at
                    FROM runway_configs rc
                    JOIN atis_data ad ON rc.atis_id = ad.id
                    WHERE rc.airport_code = %s
                      AND ad.collected_at BETWEEN %s AND %s
                      AND ad.datis_text LIKE '%%DEP INFO%%'
                      AND rc.departing_runways::text != '[]'
                      AND rc.id != %s
                    ORDER BY ABS(EXTRACT(EPOCH FROM (ad.collected_at - %s::timestamp)))
                    LIMIT 1
                """, (airport, time_window_start, time_window_end, config['id'], config['collected_at']))

                match = cursor.fetchone()
                if match:
                    # Update with departures from matching DEP INFO
                    # Set merged_from_pair and component confidence
                    arr_conf = config['confidence_score'] or 0.9  # Arrivals from this ARR INFO
                    dep_conf = 0.9  # Departures from matched DEP INFO
                    overall_conf = min(arr_conf, dep_conf)

                    cursor.execute("""
                        UPDATE runway_configs
                        SET departing_runways = %s,
                            confidence_score = %s,
                            merged_from_pair = TRUE,
                            component_confidence = %s
                        WHERE id = %s
                    """, (
                        json.dumps(match['departing_runways']),
                        overall_conf,
                        json.dumps({"arrivals": arr_conf, "departures": dep_conf}),
                        config['id']
                    ))

                    fixed_count += 1
                    print(f"    ✓ Config {config['id']} ({config['info_type']} INFO {config['information_letter']}): "
                          f"Added departures {match['departing_runways']} from DEP INFO {match['information_letter']} (conf: {overall_conf})")

        conn.commit()
        print(f"  {airport}: Fixed {fixed_count} configs")
        total_fixed += fixed_count

    print(f"\n=== Summary ===")
    print(f"Total configs fixed: {total_fixed}")
    print(f"Airports processed: {len(airports)}")

    cursor.close()
    conn.close()

if __name__ == "__main__":
    print("Fixing split DEP/ARR INFO configs...\n")
    fix_split_atis_configs()
    print("\nDone!")
