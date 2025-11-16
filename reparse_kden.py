#!/usr/bin/env python3
"""
Re-parse KDEN configs with the updated parser
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

def reparse_kden_configs():
    """Re-parse KDEN configs with updated parser"""

    conn = psycopg2.connect(**DB_CONFIG, cursor_factory=RealDictCursor)
    cursor = conn.cursor()

    parser = RunwayParser()

    # Find all KDEN configs with empty arrivals or departures
    cursor.execute("""
        SELECT rc.id, rc.airport_code, ad.information_letter,
               rc.arriving_runways, rc.departing_runways,
               rc.confidence_score, ad.datis_text
        FROM runway_configs rc
        JOIN atis_data ad ON rc.atis_id = ad.id
        WHERE rc.airport_code = 'KDEN'
          AND (rc.arriving_runways = '[]' OR rc.departing_runways = '[]')
        ORDER BY rc.created_at DESC
    """)

    configs = cursor.fetchall()
    print(f"Found {len(configs)} KDEN configs to re-parse")

    fixed_count = 0
    for config in configs:
        # Re-parse with updated parser
        result = parser.parse(
            config['airport_code'],
            config['datis_text'],
            config['information_letter']
        )

        old_arriving = config['arriving_runways'] or []
        old_departing = config['departing_runways'] or []
        new_arriving = result.arriving_runways
        new_departing = result.departing_runways

        # Update if different
        if (new_arriving != old_arriving or
            new_departing != old_departing or
            result.confidence_score != config['confidence_score']):

            cursor.execute("""
                UPDATE runway_configs
                SET arriving_runways = %s,
                    departing_runways = %s,
                    traffic_flow = %s,
                    confidence_score = %s
                WHERE id = %s
            """, (
                json.dumps(new_arriving),
                json.dumps(new_departing),
                result.traffic_flow,
                result.confidence_score,
                config['id']
            ))

            fixed_count += 1
            print(f"Re-parsed KDEN config {config['id']}:")
            print(f"  OLD: Arriving: {old_arriving}, Departing: {old_departing}, Confidence: {config['confidence_score']}")
            print(f"  NEW: Arriving: {new_arriving}, Departing: {new_departing}, Confidence: {result.confidence_score}")
            print()

    conn.commit()

    print(f"\n=== Summary ===")
    print(f"Total KDEN configs re-parsed: {fixed_count}")

    cursor.close()
    conn.close()

if __name__ == "__main__":
    print("Re-parsing KDEN configs with updated parser...")
    reparse_kden_configs()
    print("\nDone!")
