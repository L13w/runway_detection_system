#!/usr/bin/env python3
"""
ATIS Data Collector
Fetches D-ATIS data from clowd.io API and stores in database
Run every 5 minutes via cron or scheduler
"""

import requests
import json
import hashlib
import psycopg2
from psycopg2.extras import Json
from datetime import datetime
import logging
import os
from typing import Dict, List, Optional

# Configure logging
logging.basicConfig(
    level=logging.INFO,
    format='%(asctime)s - %(name)s - %(levelname)s - %(message)s'
)
logger = logging.getLogger(__name__)

# Configuration
DATIS_API_URL = "https://datis.clowd.io/api/all"
DB_CONFIG = {
    'host': os.getenv('DB_HOST', 'localhost'),
    'database': os.getenv('DB_NAME', 'runway_detection'),
    'user': os.getenv('DB_USER', 'postgres'),
    'password': os.getenv('DB_PASSWORD', 'postgres'),
    'port': os.getenv('DB_PORT', '5432')
}

class ATISCollector:
    def __init__(self):
        self.conn = None
        self.connect_db()
        
    def connect_db(self):
        """Establish database connection"""
        try:
            self.conn = psycopg2.connect(**DB_CONFIG)
            logger.info("Database connection established")
        except Exception as e:
            logger.error(f"Failed to connect to database: {e}")
            raise
    
    def fetch_atis_data(self) -> Optional[List[Dict]]:
        """Fetch current ATIS data from API"""
        try:
            response = requests.get(DATIS_API_URL, timeout=30)
            response.raise_for_status()
            data = response.json()
            logger.info(f"Fetched ATIS data for {len(data)} airports")
            return data
        except requests.RequestException as e:
            logger.error(f"Failed to fetch ATIS data: {e}")
            return None
        except json.JSONDecodeError as e:
            logger.error(f"Failed to parse ATIS JSON: {e}")
            return None
    
    def calculate_hash(self, text: str) -> str:
        """Calculate MD5 hash of ATIS text for change detection"""
        return hashlib.md5(text.encode('utf-8')).hexdigest()
    
    def store_atis_snapshot(self, airports_data: List[Dict]):
        """Store ATIS data in database"""
        cursor = self.conn.cursor()
        collected_at = datetime.utcnow()
        
        new_records = 0
        changed_records = 0
        unchanged_records = 0
        
        for airport in airports_data:
            try:
                airport_code = airport.get('airport')
                datis_text = airport.get('datis', '')
                
                if not airport_code or not datis_text:
                    continue
                
                # Extract information letter (usually first letter after airport code)
                info_letter = self.extract_info_letter(datis_text)
                content_hash = self.calculate_hash(datis_text)
                
                # Check if this is a new/changed ATIS
                cursor.execute("""
                    SELECT content_hash 
                    FROM atis_data 
                    WHERE airport_code = %s 
                    ORDER BY collected_at DESC 
                    LIMIT 1
                """, (airport_code,))
                
                last_hash = cursor.fetchone()
                
                if not last_hash:
                    # First record for this airport
                    new_records += 1
                    is_changed = True
                elif last_hash[0] != content_hash:
                    # ATIS has changed
                    changed_records += 1
                    is_changed = True
                else:
                    # No change
                    unchanged_records += 1
                    is_changed = False
                
                # Store the snapshot (always store for historical record)
                cursor.execute("""
                    INSERT INTO atis_data 
                    (airport_code, collected_at, information_letter, datis_text, content_hash, is_changed)
                    VALUES (%s, %s, %s, %s, %s, %s)
                """, (airport_code, collected_at, info_letter, datis_text, content_hash, is_changed))
                
            except Exception as e:
                logger.error(f"Error storing ATIS for {airport_code}: {e}")
                continue
        
        self.conn.commit()
        logger.info(f"Stored ATIS data: {new_records} new, {changed_records} changed, {unchanged_records} unchanged")
    
    def extract_info_letter(self, datis_text: str) -> Optional[str]:
        """Extract ATIS information letter from text"""
        import re
        
        # Common patterns for info letter
        patterns = [
            r'ATIS\s+(?:INFO|INFORMATION)\s+([A-Z])',
            r'INFORMATION\s+([A-Z])\s',
            r'ATIS\s+([A-Z])\s+\d{4}',
            r'^[A-Z]{3,4}\s+ATIS\s+([A-Z])\s',
        ]
        
        text_upper = datis_text.upper()
        for pattern in patterns:
            match = re.search(pattern, text_upper)
            if match:
                return match.group(1)
        
        return None
    
    def cleanup_old_data(self, days_to_keep: int = 90):
        """Remove old ATIS data to manage storage"""
        cursor = self.conn.cursor()
        cursor.execute("""
            DELETE FROM atis_data 
            WHERE collected_at < NOW() - INTERVAL '%s days'
        """, (days_to_keep,))
        deleted = cursor.rowcount
        self.conn.commit()
        
        if deleted > 0:
            logger.info(f"Cleaned up {deleted} old ATIS records")
    
    def run(self):
        """Main execution method"""
        try:
            # Fetch current ATIS data
            airports_data = self.fetch_atis_data()
            
            if airports_data:
                # Store in database
                self.store_atis_snapshot(airports_data)
                
                # Cleanup old data (run occasionally)
                from random import random
                if random() < 0.01:  # 1% chance each run
                    self.cleanup_old_data()
            
        except Exception as e:
            logger.error(f"Collector run failed: {e}")
            raise
        finally:
            if self.conn:
                self.conn.close()

def main():
    """Entry point for script"""
    collector = ATISCollector()
    collector.run()

if __name__ == "__main__":
    main()
