#!/usr/bin/env python3
"""
Deduplicate Review Queue
Removes duplicate runway configs from the review queue, keeping only the earliest occurrence
Groups by: (airport_code, arriving_runways, departing_runways, confidence_score)
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

def deduplicate_review_queue():
    """Remove duplicate runway configs from review queue"""

    conn = psycopg2.connect(**DB_CONFIG, cursor_factory=RealDictCursor)
    cursor = conn.cursor()

    print("Analyzing review queue for duplicates...\n")

    # First, get statistics before deduplication
    cursor.execute("""
        WITH review_queue AS (
            SELECT rc.id
            FROM runway_configs rc
            LEFT JOIN human_reviews hr ON rc.id = hr.runway_config_id
            WHERE hr.id IS NULL  -- Not reviewed
              AND (rc.confidence_score < 1.0 OR rc.arriving_runways = '[]' OR rc.departing_runways = '[]')
        )
        SELECT COUNT(*) as total_in_queue
        FROM review_queue
    """)

    before_count = cursor.fetchone()['total_in_queue']
    print(f"Items in review queue before deduplication: {before_count}")

    # Find all duplicate groups
    cursor.execute("""
        WITH review_queue AS (
            SELECT
                rc.id,
                rc.airport_code,
                rc.arriving_runways,
                rc.departing_runways,
                rc.confidence_score,
                rc.created_at
            FROM runway_configs rc
            LEFT JOIN human_reviews hr ON rc.id = hr.runway_config_id
            WHERE hr.id IS NULL  -- Not reviewed
              AND (rc.confidence_score < 1.0 OR rc.arriving_runways = '[]' OR rc.departing_runways = '[]')
        ),
        duplicate_groups AS (
            SELECT
                airport_code,
                arriving_runways,
                departing_runways,
                confidence_score,
                COUNT(*) as group_count,
                MIN(id) as keep_id,
                ARRAY_AGG(id ORDER BY created_at) as all_ids
            FROM review_queue
            GROUP BY airport_code, arriving_runways, departing_runways, confidence_score
            HAVING COUNT(*) > 1
        )
        SELECT
            airport_code,
            arriving_runways,
            departing_runways,
            confidence_score,
            group_count,
            keep_id,
            all_ids
        FROM duplicate_groups
        ORDER BY group_count DESC
    """)

    duplicate_groups = cursor.fetchall()

    if not duplicate_groups:
        print("No duplicates found!")
        cursor.close()
        conn.close()
        return

    print(f"Found {len(duplicate_groups)} duplicate groups\n")

    # Show top 10 examples
    print("Top 10 duplicate groups:")
    for i, group in enumerate(duplicate_groups[:10], 1):
        arr = group['arriving_runways'] if group['arriving_runways'] else '[]'
        dep = group['departing_runways'] if group['departing_runways'] else '[]'
        print(f"  {i}. {group['airport_code']}: Arr={arr}, Dep={dep}, "
              f"Conf={group['confidence_score']:.1f} - {group['group_count']} duplicates")

    # Calculate total removals
    total_to_remove = sum(group['group_count'] - 1 for group in duplicate_groups)
    print(f"\nTotal duplicates to remove: {total_to_remove}")
    print(f"Unique configs to keep: {len(duplicate_groups)}")

    # Collect all IDs to delete
    ids_to_delete = []
    for group in duplicate_groups:
        # Keep the first ID, delete the rest
        all_ids = group['all_ids']
        keep_id = all_ids[0]  # First one (earliest by created_at)
        delete_ids = all_ids[1:]  # Rest are duplicates
        ids_to_delete.extend(delete_ids)

    print(f"\nDeleting {len(ids_to_delete)} duplicate runway_configs...")

    # Delete in batches to avoid issues with large DELETE statements
    batch_size = 1000
    deleted_count = 0

    for i in range(0, len(ids_to_delete), batch_size):
        batch = ids_to_delete[i:i + batch_size]
        cursor.execute("""
            DELETE FROM runway_configs
            WHERE id = ANY(%s)
        """, (batch,))
        deleted_count += cursor.rowcount
        print(f"  Deleted batch {i//batch_size + 1}: {cursor.rowcount} records")

    conn.commit()

    # Verify results
    cursor.execute("""
        WITH review_queue AS (
            SELECT rc.id
            FROM runway_configs rc
            LEFT JOIN human_reviews hr ON rc.id = hr.runway_config_id
            WHERE hr.id IS NULL
              AND (rc.confidence_score < 1.0 OR rc.arriving_runways = '[]' OR rc.departing_runways = '[]')
        )
        SELECT COUNT(*) as total_in_queue
        FROM review_queue
    """)

    after_count = cursor.fetchone()['total_in_queue']

    print(f"\n=== Summary ===")
    print(f"Before deduplication: {before_count} items")
    print(f"Duplicates removed: {deleted_count}")
    print(f"After deduplication: {after_count} items")
    print(f"Reduction: {before_count - after_count} items ({(before_count - after_count) / before_count * 100:.1f}%)")

    cursor.close()
    conn.close()

if __name__ == "__main__":
    print("Review Queue Deduplication Script\n")
    print("This will remove duplicate runway configs from the review queue,")
    print("keeping only the earliest occurrence of each unique configuration.\n")

    deduplicate_review_queue()
    print("\nDone!")
