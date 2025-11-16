#!/usr/bin/env python3
"""
Re-parse SFO configs with the updated parser
Fixes named visual approach patterns (FMS BRIDGE, TIPP TOE)
and DEPG RWYS comma-separated format
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

def reparse_sfo_configs():
    """Re-parse SFO configs with updated parser"""

    conn = psycopg2.connect(**DB_CONFIG, cursor_factory=RealDictCursor)
    cursor = conn.cursor()

    parser = RunwayParser()

    # Find all SFO configs (especially those with APP IN USE or DEPG RWYS patterns)
    cursor.execute("""
        SELECT rc.id, rc.airport_code, ad.information_letter,
               rc.arriving_runways, rc.departing_runways,
               rc.confidence_score, ad.datis_text
        FROM runway_configs rc
        JOIN atis_data ad ON rc.atis_id = ad.id
        WHERE rc.airport_code = 'KSFO'
          AND (ad.datis_text ILIKE '%%APP IN USE%%'
               OR ad.datis_text ILIKE '%%DEPG RWYS%%'
               OR ad.datis_text ILIKE '%%FMS BRIDGE%%'
               OR ad.datis_text ILIKE '%%TIPP TOE%%')
        ORDER BY rc.created_at DESC
    """)

    configs = cursor.fetchall()
    print(f"Found {len(configs)} SFO configs to re-parse")

    fixed_count = 0
    improved_confidence = 0

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

            # Check if confidence improved
            if result.confidence_score > config['confidence_score']:
                improved_confidence += 1

            print(f"Re-parsed SFO config {config['id']}:")
            print(f"  ATIS: {config['datis_text'][:100]}...")
            print(f"  OLD: Arr: {old_arriving}, Dep: {old_departing}, Conf: {config['confidence_score']}")
            print(f"  NEW: Arr: {new_arriving}, Dep: {new_departing}, Conf: {result.confidence_score}")
            print()

    conn.commit()

    print(f"\n=== Summary ===")
    print(f"Total SFO configs checked: {len(configs)}")
    print(f"Total configs updated: {fixed_count}")
    print(f"Configs with improved confidence: {improved_confidence}")

    cursor.close()
    conn.close()

if __name__ == "__main__":
    print("Re-parsing SFO configs with updated parser...\n")
    reparse_sfo_configs()
    print("\nDone!")
