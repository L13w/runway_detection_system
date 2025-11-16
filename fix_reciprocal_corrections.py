#!/usr/bin/env python3
"""
Find and fix corrections with reciprocal runways
Reciprocal runways are opposite ends of the same runway (differ by 18)
Examples: 09/27, 10/28, 16/34, 18/36
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

def detect_reciprocal_runways(runways):
    """
    Detect if list contains reciprocal runways
    Returns (has_reciprocals, reciprocal_pairs)
    """
    if not runways or len(runways) < 2:
        return False, []

    # Extract runway numbers (without L/C/R suffix)
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

def find_reciprocal_corrections():
    """Find all corrections with reciprocal runways"""

    conn = psycopg2.connect(**DB_CONFIG, cursor_factory=RealDictCursor)
    cursor = conn.cursor()

    print("Searching for corrections with reciprocal runways...\n")

    # Get all corrections
    cursor.execute("""
        SELECT
            hr.id,
            hr.airport_code,
            hr.runway_config_id,
            hr.corrected_arriving_runways,
            hr.corrected_departing_runways,
            hr.reviewed_at,
            hr.reviewed_by
        FROM human_reviews hr
        ORDER BY hr.reviewed_at DESC
    """)

    all_corrections = cursor.fetchall()
    print(f"Checking {len(all_corrections)} total corrections...")

    bad_corrections = []

    for correction in all_corrections:
        arriving = correction['corrected_arriving_runways'] or []
        departing = correction['corrected_departing_runways'] or []
        all_runways = arriving + departing

        has_reciprocals, pairs = detect_reciprocal_runways(all_runways)

        if has_reciprocals:
            bad_corrections.append({
                'id': correction['id'],
                'airport_code': correction['airport_code'],
                'config_id': correction['runway_config_id'],
                'arriving': arriving,
                'departing': departing,
                'reciprocal_pairs': pairs,
                'reviewed_at': correction['reviewed_at'],
                'reviewed_by': correction['reviewed_by']
            })

    print(f"\nFound {len(bad_corrections)} corrections with reciprocal runways:\n")

    for i, bad in enumerate(bad_corrections, 1):
        print(f"{i}. {bad['airport_code']} (Review ID: {bad['id']}, Config ID: {bad['config_id']})")
        print(f"   Arrivals: {bad['arriving']}")
        print(f"   Departures: {bad['departing']}")
        print(f"   Reciprocals: {', '.join(bad['reciprocal_pairs'])}")
        print(f"   Reviewed: {bad['reviewed_at']} by {bad['reviewed_by']}")
        print()

    cursor.close()
    conn.close()

    return bad_corrections

def delete_bad_corrections(bad_corrections):
    """Delete corrections with reciprocal runways"""

    if not bad_corrections:
        print("No bad corrections to delete.")
        return

    conn = psycopg2.connect(**DB_CONFIG, cursor_factory=RealDictCursor)
    cursor = conn.cursor()

    print(f"\nDeleting {len(bad_corrections)} bad corrections...")

    review_ids = [correction['id'] for correction in bad_corrections]

    cursor.execute("""
        DELETE FROM human_reviews
        WHERE id = ANY(%s)
    """, (review_ids,))

    deleted_count = cursor.rowcount
    conn.commit()

    print(f"✓ Deleted {deleted_count} corrections with reciprocal runways")

    cursor.close()
    conn.close()

if __name__ == "__main__":
    print("=" * 70)
    print("Fix Reciprocal Runway Corrections")
    print("=" * 70)
    print()

    # Find bad corrections
    bad_corrections = find_reciprocal_corrections()

    if bad_corrections:
        print("=" * 70)
        print("These corrections will be DELETED (they contain reciprocal runways)")
        print("=" * 70)

        # Delete them
        delete_bad_corrections(bad_corrections)

        print("\n✓ Done! Bad corrections removed from database.")
        print("These configs will return to the review queue for proper correction.")
    else:
        print("✓ No corrections with reciprocal runways found!")
