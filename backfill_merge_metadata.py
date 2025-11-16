#!/usr/bin/env python3
"""
Backfill merge metadata for existing split ATIS configs
Sets merged_from_pair=TRUE and component_confidence for configs created from matching pairs
"""

import psycopg2
from psycopg2.extras import RealDictCursor
import os
import json

DB_CONFIG = {
    'host': os.getenv('DB_HOST', 'localhost'),
    'database': os.getenv('DB_NAME', 'runway_detection'),
    'user': os.getenv('DB_USER', 'postgres'),
    'password': os.getenv('DB_PASSWORD', 'postgres'),
    'port': os.getenv('DB_PORT', '5432')
}

def backfill_merge_metadata():
    """Backfill merge metadata for existing configs"""

    conn = psycopg2.connect(**DB_CONFIG, cursor_factory=RealDictCursor)
    cursor = conn.cursor()

    print("Finding split ATIS configs with both arrivals and departures...")

    # Find all configs that appear to be merged (split ATIS with both arrays populated)
    cursor.execute("""
        SELECT
            rc.id,
            rc.airport_code,
            rc.confidence_score,
            ad.datis_text,
            CASE
                WHEN ad.datis_text ILIKE '%%ARR INFO%%' THEN 'ARR'
                WHEN ad.datis_text ILIKE '%%DEP INFO%%' THEN 'DEP'
                ELSE 'OTHER'
            END as info_type
        FROM runway_configs rc
        JOIN atis_data ad ON rc.atis_id = ad.id
        WHERE (ad.datis_text ILIKE '%%ARR INFO%%' OR ad.datis_text ILIKE '%%DEP INFO%%')
          AND rc.arriving_runways::text != '[]'
          AND rc.departing_runways::text != '[]'
          AND rc.merged_from_pair IS NOT TRUE  -- Not already marked
        ORDER BY rc.airport_code, rc.created_at DESC
    """)

    configs = cursor.fetchall()
    print(f"Found {len(configs)} configs to backfill\n")

    updated_count = 0

    for config in configs:
        # For merged configs, set conservative component confidence
        # We don't know the exact original confidence, so use current confidence
        # Assume both components had same confidence since we merged them
        conf = config['confidence_score'] or 0.9

        cursor.execute("""
            UPDATE runway_configs
            SET merged_from_pair = TRUE,
                component_confidence = %s
            WHERE id = %s
        """, (
            json.dumps({"arrivals": conf, "departures": conf}),
            config['id']
        ))

        updated_count += 1

        if updated_count <= 10:  # Show first 10 examples
            print(f"  Config {config['id']} ({config['airport_code']} {config['info_type']} INFO): "
                  f"marked as merged, conf: {conf}")

    conn.commit()

    print(f"\n=== Summary ===")
    print(f"Total configs backfilled: {updated_count}")
    print(f"All existing merged configs now have merge metadata ✓")

    cursor.close()
    conn.close()

if __name__ == "__main__":
    print("Backfilling merge metadata for existing split ATIS configs...\n")
    backfill_merge_metadata()
    print("\nDone!")
