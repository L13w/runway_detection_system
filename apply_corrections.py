#!/usr/bin/env python3
"""
Apply learned patterns to fix parsing errors automatically
Based on human corrections, identifies and fixes similar patterns
"""

import psycopg2
from psycopg2.extras import RealDictCursor
import re
import json
import os

DB_CONFIG = {
    'host': os.getenv('DB_HOST', 'localhost'),
    'database': os.getenv('DB_NAME', 'runway_detection'),
    'user': os.getenv('DB_USER', 'postgres'),
    'password': os.getenv('DB_PASSWORD', 'postgres'),
    'port': os.getenv('DB_PORT', '5432')
}

def extract_runways_from_pattern(text, pattern):
    """Extract runway numbers following a pattern"""
    runways = []

    # Find the pattern in the text
    match = re.search(pattern, text, re.IGNORECASE)
    if match:
        # Get text after the pattern (next 100 chars)
        remaining_text = text[match.end():match.end()+100]

        # Extract runway numbers (format: 01-36 with optional L/C/R)
        runway_matches = re.findall(r'\b([0-3]?[0-9][LCR]?)\b', remaining_text)

        # Filter to valid runway numbers (01-36)
        for rwy in runway_matches:
            # Extract numeric part
            num_part = re.match(r'(\d+)', rwy)
            if num_part:
                num = int(num_part.group(1))
                if 1 <= num <= 36:
                    runways.append(rwy)
                    # Stop after finding runways if we hit certain keywords
                    if re.search(r'\b(NOTAM|TWY|TAXIWAY|NOTICE)\b', remaining_text[:remaining_text.find(rwy)+10], re.IGNORECASE):
                        break

    return runways

def apply_pattern_corrections():
    """Apply pattern-based corrections to runway configs with empty arrays"""

    conn = psycopg2.connect(**DB_CONFIG, cursor_factory=RealDictCursor)
    cursor = conn.cursor()

    # Find configs with empty arriving or departing runways
    cursor.execute("""
        SELECT rc.id, rc.airport_code, rc.arriving_runways, rc.departing_runways,
               rc.confidence_score, ad.datis_text
        FROM runway_configs rc
        JOIN atis_data ad ON rc.atis_id = ad.id
        WHERE (rc.arriving_runways = '[]' OR rc.departing_runways = '[]')
          AND rc.id NOT IN (SELECT runway_config_id FROM human_reviews WHERE runway_config_id IS NOT NULL)
        ORDER BY rc.created_at DESC
    """)

    configs = cursor.fetchall()
    print(f"Found {len(configs)} configs with empty runway arrays")

    fixed_count = 0
    patterns_found = {
        'LDG': 0,
        'LAND': 0,
        'DEPG': 0,
        'DEPART': 0,
        'VISUAL_APCH': 0
    }

    for config in configs:
        atis_text = config['datis_text'].upper()
        # JSONB columns are already Python lists, not strings
        current_arriving = config['arriving_runways'] if config['arriving_runways'] else []
        current_departing = config['departing_runways'] if config['departing_runways'] else []

        new_arriving = list(current_arriving)
        new_departing = list(current_departing)
        changed = False

        # Pattern 1: LDG RY or LAND RY for arrivals
        if not new_arriving:
            ldg_runways = extract_runways_from_pattern(atis_text, r'LDG\s+RY?')
            if ldg_runways:
                new_arriving.extend(ldg_runways)
                patterns_found['LDG'] += 1
                changed = True
            else:
                land_runways = extract_runways_from_pattern(atis_text, r'LAND(?:ING)?\s+RY?')
                if land_runways:
                    new_arriving.extend(land_runways)
                    patterns_found['LAND'] += 1
                    changed = True

        # Pattern 2: DEPG RY for departures
        if not new_departing:
            depg_runways = extract_runways_from_pattern(atis_text, r'DEPG\s+RY?')
            if depg_runways:
                new_departing.extend(depg_runways)
                patterns_found['DEPG'] += 1
                changed = True
            else:
                depart_runways = extract_runways_from_pattern(atis_text, r'DEPART(?:URE|ING)?\s+RY?')
                if depart_runways:
                    new_departing.extend(depart_runways)
                    patterns_found['DEPART'] += 1
                    changed = True

        # Pattern 3: VISUAL APCH RY ... IN USE for arrivals
        if not new_arriving:
            visual_match = re.search(r'VISUAL\s+APCH\s+RY?\s+([\d\sLCR,]+)\s+IN\s+USE', atis_text)
            if visual_match:
                runway_text = visual_match.group(1)
                runways = re.findall(r'([0-3]?[0-9][LCR]?)', runway_text)
                valid_runways = [r for r in runways if 1 <= int(re.match(r'(\d+)', r).group(1)) <= 36]
                if valid_runways:
                    new_arriving.extend(valid_runways)
                    patterns_found['VISUAL_APCH'] += 1
                    changed = True

        # Remove duplicates while preserving order
        new_arriving = list(dict.fromkeys(new_arriving))
        new_departing = list(dict.fromkeys(new_departing))

        # Update if we found new runways
        if changed and (new_arriving or new_departing):
            # Calculate new confidence score
            new_confidence = 0.8  # Pattern-based correction gets 0.8 confidence

            cursor.execute("""
                UPDATE runway_configs
                SET arriving_runways = %s,
                    departing_runways = %s,
                    confidence_score = %s
                WHERE id = %s
            """, (
                json.dumps(new_arriving),
                json.dumps(new_departing),
                new_confidence,
                config['id']
            ))

            fixed_count += 1
            print(f"Fixed {config['airport_code']} (ID {config['id']}): "
                  f"Arriving: {new_arriving}, Departing: {new_departing}")

    conn.commit()

    print(f"\n=== Summary ===")
    print(f"Total configs fixed: {fixed_count}")
    print(f"\nPatterns applied:")
    for pattern, count in patterns_found.items():
        if count > 0:
            print(f"  {pattern}: {count} times")

    cursor.close()
    conn.close()

if __name__ == "__main__":
    print("Applying pattern-based corrections...")
    apply_pattern_corrections()
    print("\nDone! Check the review dashboard to see the reduced queue.")
