#!/usr/bin/env python3
"""
Fix KDEN configs - Denver publishes separate arrival and departure ATIS
DEP INFO messages are expected to have empty arrivals - this is VALID
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

def fix_kden_departure_configs():
    """Update KDEN DEP INFO configs to have high confidence"""

    conn = psycopg2.connect(**DB_CONFIG, cursor_factory=RealDictCursor)
    cursor = conn.cursor()

    # Find KDEN configs with "DEP INFO" that have departures but no arrivals
    cursor.execute("""
        SELECT rc.id, rc.airport_code, rc.arriving_runways, rc.departing_runways,
               rc.confidence_score, ad.datis_text
        FROM runway_configs rc
        JOIN atis_data ad ON rc.atis_id = ad.id
        WHERE rc.airport_code = 'KDEN'
          AND ad.datis_text LIKE '%DEP INFO%'
          AND rc.arriving_runways = '[]'
    """)

    configs = cursor.fetchall()
    print(f"Found {len(configs)} KDEN DEP INFO configs with empty arrivals")

    fixed_count = 0
    for config in configs:
        departing = config['departing_runways'] or []

        # If we have departures, this is valid - set confidence to 1.0
        if departing and len(departing) > 0:
            cursor.execute("""
                UPDATE runway_configs
                SET confidence_score = 1.0
                WHERE id = %s
            """, (config['id'],))
            fixed_count += 1
            print(f"Fixed KDEN config {config['id']}: Departing {departing}, Confidence → 1.0")

    conn.commit()
    print(f"\n=== Summary ===")
    print(f"Total KDEN DEP INFO configs fixed: {fixed_count}")
    print(f"These are now marked as valid (confidence = 1.0)")

    cursor.close()
    conn.close()

if __name__ == "__main__":
    print("Fixing KDEN departure-only configs...")
    fix_kden_departure_configs()
    print("\nDone!")
