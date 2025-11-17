#!/usr/bin/env python3
"""
Runway Detection API
FastAPI server providing runway configuration information
"""

from fastapi import FastAPI, HTTPException, Query, BackgroundTasks
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import HTMLResponse
from pydantic import BaseModel
from typing import List, Optional, Dict, Any
from datetime import datetime, timedelta
import psycopg2
from psycopg2.extras import RealDictCursor
import os
import logging
import json

from runway_parser import RunwayParser, RunwayConfiguration

# Configure logging
logging.basicConfig(level=logging.INFO)
logger = logging.getLogger(__name__)

# Database configuration
DB_CONFIG = {
    'host': os.getenv('DB_HOST', 'localhost'),
    'database': os.getenv('DB_NAME', 'runway_detection'),
    'user': os.getenv('DB_USER', 'postgres'),
    'password': os.getenv('DB_PASSWORD', 'postgres'),
    'port': os.getenv('DB_PORT', '5432')
}

# Initialize FastAPI app
app = FastAPI(
    title="Runway Direction API",
    description="API for real-time airport runway configuration information",
    version="1.0.0"
)

# Add CORS middleware
app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],  # Configure appropriately for production
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)

# Initialize runway parser
parser = RunwayParser()

# Response models
class RunwayResponse(BaseModel):
    airport: str
    timestamp: str
    information_letter: Optional[str]
    arriving_runways: List[str]
    departing_runways: List[str]
    traffic_flow: str
    configuration_name: Optional[str]
    confidence: float
    last_updated: str
    
class RunwayHistoryItem(BaseModel):
    timestamp: str
    information_letter: Optional[str]
    arriving_runways: List[str]
    departing_runways: List[str]
    traffic_flow: str
    configuration_name: Optional[str]
    duration_minutes: Optional[int]

class AtisReport(BaseModel):
    timestamp: str
    information_letter: Optional[str]
    datis_text: str
    arriving_runways: List[str]
    departing_runways: List[str]
    traffic_flow: str
    confidence: float

class AirportSummary(BaseModel):
    airport: str
    name: str
    current_config: Optional[RunwayResponse]
    status: str  # "active", "no_data", "stale"

class SystemStatus(BaseModel):
    status: str
    airports_monitored: int
    airports_active: int
    last_collection: Optional[str]
    database_status: str

class DashboardStats(BaseModel):
    current_time: str
    total_airports: int
    active_airports: int
    stale_airports: List[Dict]  # Airports with no updates in 3+ hours
    parsing_stats: Dict  # Success/failure rates
    confidence_stats: Dict  # Average confidence by airport
    activity_stats: Dict  # Updates by time period (hour, day, week, month)
    recent_changes: List[Dict]  # Recent runway config changes

class AirportStatus(BaseModel):
    airport_code: str
    arriving: List[str]
    departing: List[str]
    flow: str
    last_change: str
    recent_changes: List[Any]  # 4 most recent changes

class ReviewItem(BaseModel):
    id: int
    atis_id: int
    airport_code: str
    atis_text: str
    original_arriving: List[str]
    original_departing: List[str]
    confidence: float
    collected_at: str
    issue_type: str  # 'low_confidence', 'has_none', 'parse_failed', 'complete'
    merged_from_pair: bool = False
    component_confidence: Optional[Dict[str, float]] = None  # {"arrivals": 1.0, "departures": 1.0}
    has_reciprocal_runways: bool = False  # True if reciprocal runways detected (probably wrong data)
    is_incomplete_pair: bool = False  # True if split ATIS but missing DEP or ARR pair
    warnings: List[str] = []  # Human-readable warnings

class ReviewSubmission(BaseModel):
    review_id: int
    corrected_arriving: List[str]
    corrected_departing: List[str]
    notes: Optional[str] = None
    reviewed_by: str = "human_reviewer"

class ReviewStats(BaseModel):
    pending_count: int
    reviewed_count: int
    low_confidence_count: int
    has_none_count: int
    failed_parse_count: int


# Database connection helper
def get_db_connection():
    """Create database connection"""
    try:
        return psycopg2.connect(**DB_CONFIG, cursor_factory=RealDictCursor)
    except Exception as e:
        logger.error(f"Database connection failed: {e}")
        raise HTTPException(status_code=500, detail="Database connection failed")

# Helper functions for review queue
def detect_reciprocal_runways(runways: List[str]) -> bool:
    """
    Detect if list contains reciprocal runways (opposite ends of same runway)
    Reciprocals differ by 18 (180 degrees)
    Examples: 09/27, 18/36, 16/34
    """
    if not runways or len(runways) < 2:
        return False

    # Extract runway numbers (without L/C/R suffix)
    runway_numbers = []
    for rwy in runways:
        import re
        match = re.match(r'([0-9]{1,2})', rwy)
        if match:
            runway_numbers.append(int(match.group(1)))

    # Check all pairs for reciprocals
    for i in range(len(runway_numbers)):
        for j in range(i + 1, len(runway_numbers)):
            diff = abs(runway_numbers[i] - runway_numbers[j])
            if diff == 18:  # Reciprocal runways
                return True

    return False

def get_latest_configs_per_airport(conn):
    """
    Get the most current runway config for each airport with real-time split ATIS pairing.

    For split ATIS airports (DEP INFO / ARR INFO):
      - Find latest ARR INFO config
      - Find latest DEP INFO config
      - If both within 15 minutes: merge them
      - If only one: return it with incomplete pair warning

    For normal ATIS airports:
      - Return latest config

    Returns: List of dicts with config data + warnings
    """
    cursor = conn.cursor()

    # Get all unreviewed configs from last 6 hours, grouped by airport
    cursor.execute("""
        WITH unreviewed_configs AS (
            SELECT
                rc.id,
                rc.airport_code,
                rc.atis_id,
                rc.arriving_runways,
                rc.departing_runways,
                rc.confidence_score,
                rc.merged_from_pair,
                rc.component_confidence,
                rc.created_at,
                ad.datis_text,
                ad.collected_at,
                ad.datis_text LIKE '%DEP INFO%' as is_dep_info,
                ad.datis_text LIKE '%ARR INFO%' as is_arr_info
            FROM runway_configs rc
            JOIN atis_data ad ON rc.atis_id = ad.id
            LEFT JOIN human_reviews hr ON rc.id = hr.runway_config_id
            WHERE hr.id IS NULL
              AND (rc.confidence_score < 1.0
                   OR rc.arriving_runways::text = '[]'
                   OR rc.departing_runways::text = '[]')
              AND ad.collected_at > NOW() - INTERVAL '6 hours'
        )
        SELECT * FROM unreviewed_configs
        ORDER BY airport_code, created_at DESC
    """)

    all_configs = cursor.fetchall()

    # Group by airport
    airports = {}
    for config in all_configs:
        airport = config['airport_code']
        if airport not in airports:
            airports[airport] = []
        airports[airport].append(config)

    # Process each airport
    result_configs = []

    for airport_code, configs in airports.items():
        # Check if this is a split ATIS airport
        has_dep_info = any(c['is_dep_info'] for c in configs)
        has_arr_info = any(c['is_arr_info'] for c in configs)
        is_split_atis = has_dep_info or has_arr_info

        if is_split_atis:
            # Find latest ARR and DEP configs
            arr_configs = [c for c in configs if c['is_arr_info']]
            dep_configs = [c for c in configs if c['is_dep_info']]

            latest_arr = arr_configs[0] if arr_configs else None
            latest_dep = dep_configs[0] if dep_configs else None

            # Try to pair them if both exist and within 15 minutes
            if latest_arr and latest_dep:
                time_diff = abs((latest_arr['collected_at'] - latest_dep['collected_at']).total_seconds() / 60)

                if time_diff <= 15:
                    # Merge them - use latest as base
                    if latest_arr['created_at'] >= latest_dep['created_at']:
                        merged = dict(latest_arr)
                        merged['departing_runways'] = latest_dep['departing_runways']
                        merged['atis_text'] = f"ARR: {latest_arr['datis_text'][:100]}... | DEP: {latest_dep['datis_text'][:100]}..."
                        merged['merged_from_pair'] = True
                        merged['is_incomplete_pair'] = False
                    else:
                        merged = dict(latest_dep)
                        merged['arriving_runways'] = latest_arr['arriving_runways']
                        merged['atis_text'] = f"ARR: {latest_arr['datis_text'][:100]}... | DEP: {latest_dep['datis_text'][:100]}..."
                        merged['merged_from_pair'] = True
                        merged['is_incomplete_pair'] = False

                    result_configs.append(merged)
                else:
                    # Too far apart - show the latest one with warning
                    latest = latest_arr if latest_arr['created_at'] >= latest_dep['created_at'] else latest_dep
                    latest = dict(latest)
                    latest['is_incomplete_pair'] = True
                    result_configs.append(latest)
            elif latest_arr:
                # Only ARR INFO available
                latest_arr = dict(latest_arr)
                latest_arr['is_incomplete_pair'] = True
                result_configs.append(latest_arr)
            elif latest_dep:
                # Only DEP INFO available
                latest_dep = dict(latest_dep)
                latest_dep['is_incomplete_pair'] = True
                result_configs.append(latest_dep)
        else:
            # Normal ATIS - just get latest
            latest = dict(configs[0])
            latest['is_incomplete_pair'] = False
            result_configs.append(latest)

    return result_configs

# API Endpoints
@app.get("/", response_model=Dict)
async def root():
    """API root endpoint with basic information"""
    return {
        "name": "Runway Direction API",
        "version": "1.0.0",
        "endpoints": {
            "/dashboard": "Real-time monitoring dashboard",
            "/review": "Human review dashboard for corrections",
            "/api/runway/{airport_code}": "Get current runway configuration",
            "/api/runways/all": "Get all airports' runway configurations",
            "/api/runway/{airport_code}/history": "Get runway configuration history",
            "/api/airports": "List all monitored airports",
            "/api/status": "System status",
            "/api/dashboard/stats": "Dashboard statistics (JSON)",
            "/api/review/pending": "Get items needing review",
            "/api/review/stats": "Review statistics",
            "/docs": "Interactive API documentation"
        }
    }

@app.get("/api/runway/{airport_code}", response_model=RunwayResponse)
async def get_runway_status(airport_code: str):
    """Get current runway configuration for an airport"""
    
    airport_code = airport_code.upper()
    if not airport_code.startswith('K'):
        airport_code = 'K' + airport_code
    
    conn = get_db_connection()
    try:
        cursor = conn.cursor()
        
        # Get latest ATIS data
        cursor.execute("""
            SELECT airport_code, collected_at, information_letter, datis_text
            FROM atis_data
            WHERE airport_code = %s
            ORDER BY collected_at DESC
            LIMIT 1
        """, (airport_code,))
        
        result = cursor.fetchone()
        
        if not result:
            raise HTTPException(status_code=404, detail=f"No data available for {airport_code}")
        
        # Check if data is stale (>30 minutes old)
        age_minutes = (datetime.utcnow() - result['collected_at']).total_seconds() / 60
        if age_minutes > 30:
            logger.warning(f"Data for {airport_code} is {age_minutes:.1f} minutes old")
        
        # Parse runway configuration
        config = parser.parse(
            airport_code,
            result['datis_text'],
            result['information_letter']
        )
        
        # Store parsed configuration
        cursor.execute("""
            INSERT INTO runway_configs 
            (airport_code, atis_id, arriving_runways, departing_runways, 
             traffic_flow, configuration_name, confidence_score)
            SELECT %s, 
                   (SELECT id FROM atis_data WHERE airport_code = %s ORDER BY collected_at DESC LIMIT 1),
                   %s, %s, %s, %s, %s
            ON CONFLICT (airport_code, atis_id) DO NOTHING
        """, (
            airport_code,
            airport_code,
            json.dumps(config.arriving_runways),
            json.dumps(config.departing_runways),
            config.traffic_flow,
            config.configuration_name,
            config.confidence_score
        ))
        conn.commit()
        
        return RunwayResponse(
            airport=config.airport_code,
            timestamp=config.timestamp.isoformat(),
            information_letter=config.information_letter,
            arriving_runways=config.arriving_runways,
            departing_runways=config.departing_runways,
            traffic_flow=config.traffic_flow,
            configuration_name=config.configuration_name,
            confidence=config.confidence_score,
            last_updated=result['collected_at'].isoformat()
        )
        
    finally:
        conn.close()

@app.get("/api/runways/all", response_model=List[RunwayResponse])
async def get_all_runways():
    """Get runway configurations for all monitored airports"""
    
    conn = get_db_connection()
    try:
        cursor = conn.cursor()
        
        # Get latest ATIS for each airport
        cursor.execute("""
            SELECT DISTINCT ON (airport_code) 
                   airport_code, collected_at, information_letter, datis_text
            FROM atis_data
            WHERE collected_at > NOW() - INTERVAL '1 hour'
            ORDER BY airport_code, collected_at DESC
        """)
        
        results = cursor.fetchall()
        runway_configs = []
        
        for result in results:
            try:
                config = parser.parse(
                    result['airport_code'],
                    result['datis_text'],
                    result['information_letter']
                )
                
                runway_configs.append(RunwayResponse(
                    airport=config.airport_code,
                    timestamp=config.timestamp.isoformat(),
                    information_letter=config.information_letter,
                    arriving_runways=config.arriving_runways,
                    departing_runways=config.departing_runways,
                    traffic_flow=config.traffic_flow,
                    configuration_name=config.configuration_name,
                    confidence=config.confidence_score,
                    last_updated=result['collected_at'].isoformat()
                ))
            except Exception as e:
                logger.error(f"Error parsing {result['airport_code']}: {e}")
                continue
        
        return runway_configs
        
    finally:
        conn.close()

@app.get("/api/runway/{airport_code}/history", response_model=List[RunwayHistoryItem])
async def get_runway_history(
    airport_code: str,
    hours: int = Query(default=24, ge=1, le=168)  # Max 1 week
):
    """Get runway configuration changes over time"""
    
    airport_code = airport_code.upper()
    if not airport_code.startswith('K'):
        airport_code = 'K' + airport_code
    
    conn = get_db_connection()
    try:
        cursor = conn.cursor()
        
        # Get ATIS changes (only records where content changed)
        cursor.execute("""
            SELECT collected_at, information_letter, datis_text
            FROM atis_data
            WHERE airport_code = %s
              AND collected_at > NOW() - INTERVAL '%s hours'
              AND is_changed = true
            ORDER BY collected_at DESC
        """, (airport_code, hours))
        
        results = cursor.fetchall()
        
        if not results:
            return []
        
        history = []
        prev_timestamp = None
        
        for i, result in enumerate(results):
            config = parser.parse(
                airport_code,
                result['datis_text'],
                result['information_letter']
            )
            
            # Calculate duration if we have a previous timestamp
            duration = None
            if prev_timestamp:
                duration = int((prev_timestamp - result['collected_at']).total_seconds() / 60)
            
            history.append(RunwayHistoryItem(
                timestamp=result['collected_at'].isoformat(),
                information_letter=config.information_letter,
                arriving_runways=config.arriving_runways,
                departing_runways=config.departing_runways,
                traffic_flow=config.traffic_flow,
                configuration_name=config.configuration_name,
                duration_minutes=duration
            ))
            
            prev_timestamp = result['collected_at']
        
        return history

    finally:
        conn.close()

@app.get("/api/runway/{airport_code}/reports", response_model=List[AtisReport])
async def get_atis_reports(
    airport_code: str,
    limit: int = Query(default=4, ge=1, le=20)
):
    """Get recent ATIS reports with full text for an airport"""

    airport_code = airport_code.upper()
    if not airport_code.startswith('K'):
        airport_code = 'K' + airport_code

    conn = get_db_connection()
    try:
        cursor = conn.cursor()

        # Get recent ATIS data with runway configs
        cursor.execute("""
            SELECT
                ad.collected_at,
                ad.information_letter,
                ad.datis_text,
                rc.arriving_runways,
                rc.departing_runways,
                rc.traffic_flow,
                rc.confidence_score
            FROM atis_data ad
            LEFT JOIN runway_configs rc ON ad.id = rc.atis_id
            WHERE ad.airport_code = %s
            ORDER BY ad.collected_at DESC
            LIMIT %s
        """, (airport_code, limit))

        results = cursor.fetchall()

        reports = []
        for result in results:
            reports.append(AtisReport(
                timestamp=result['collected_at'].isoformat(),
                information_letter=result['information_letter'],
                datis_text=result['datis_text'],
                arriving_runways=result['arriving_runways'] or [],
                departing_runways=result['departing_runways'] or [],
                traffic_flow=result['traffic_flow'] or 'UNKNOWN',
                confidence=result['confidence_score'] or 0.0
            ))

        return reports

    finally:
        conn.close()

@app.get("/api/airports", response_model=List[AirportSummary])
async def get_airports():
    """List all monitored airports with current status"""
    
    conn = get_db_connection()
    try:
        cursor = conn.cursor()
        
        # Get list of all airports and their latest data
        cursor.execute("""
            SELECT DISTINCT ON (airport_code)
                   airport_code,
                   collected_at,
                   information_letter,
                   datis_text
            FROM atis_data
            ORDER BY airport_code, collected_at DESC
        """)
        
        results = cursor.fetchall()
        airports = []
        
        # Airport names (expand as needed)
        airport_names = {
            'KSEA': 'Seattle-Tacoma International',
            'KSFO': 'San Francisco International',
            'KLAX': 'Los Angeles International',
            'KORD': "Chicago O'Hare International",
            'KATL': 'Hartsfield-Jackson Atlanta International',
            'KDFW': 'Dallas/Fort Worth International',
            'KDEN': 'Denver International',
            'KJFK': 'John F. Kennedy International',
            'KLAS': 'Las Vegas McCarran International',
            'KPHX': 'Phoenix Sky Harbor International'
        }
        
        for result in results:
            airport_code = result['airport_code']
            age_minutes = (datetime.utcnow() - result['collected_at']).total_seconds() / 60
            
            # Determine status
            if age_minutes > 60:
                status = "stale"
            elif age_minutes > 30:
                status = "aging"
            else:
                status = "active"
            
            # Parse current configuration
            current_config = None
            if status in ["active", "aging"]:
                try:
                    config = parser.parse(
                        airport_code,
                        result['datis_text'],
                        result['information_letter']
                    )
                    current_config = RunwayResponse(
                        airport=config.airport_code,
                        timestamp=config.timestamp.isoformat(),
                        information_letter=config.information_letter,
                        arriving_runways=config.arriving_runways,
                        departing_runways=config.departing_runways,
                        traffic_flow=config.traffic_flow,
                        configuration_name=config.configuration_name,
                        confidence=config.confidence_score,
                        last_updated=result['collected_at'].isoformat()
                    )
                except Exception as e:
                    logger.error(f"Error parsing {airport_code}: {e}")
            
            airports.append(AirportSummary(
                airport=airport_code,
                name=airport_names.get(airport_code, airport_code),
                current_config=current_config,
                status=status
            ))
        
        return airports
        
    finally:
        conn.close()

@app.get("/api/status", response_model=SystemStatus)
async def get_system_status():
    """Get system health and statistics"""
    
    conn = get_db_connection()
    try:
        cursor = conn.cursor()
        
        # Get statistics
        cursor.execute("""
            SELECT 
                COUNT(DISTINCT airport_code) as total_airports,
                COUNT(DISTINCT CASE 
                    WHEN collected_at > NOW() - INTERVAL '30 minutes' 
                    THEN airport_code 
                END) as active_airports,
                MAX(collected_at) as last_collection
            FROM atis_data
        """)
        
        stats = cursor.fetchone()
        
        return SystemStatus(
            status="operational",
            airports_monitored=stats['total_airports'] or 0,
            airports_active=stats['active_airports'] or 0,
            last_collection=stats['last_collection'].isoformat() if stats['last_collection'] else None,
            database_status="connected"
        )
        
    except Exception as e:
        logger.error(f"Status check failed: {e}")
        return SystemStatus(
            status="degraded",
            airports_monitored=0,
            airports_active=0,
            last_collection=None,
            database_status="error"
        )
    finally:
        if conn:
            conn.close()

@app.get("/review", response_class=HTMLResponse)
async def review_dashboard():
    """Serve the human review dashboard"""
    html_content = """
    <!DOCTYPE html>
    <html lang="en">
    <head>
        <meta charset="UTF-8">
        <meta name="viewport" content="width=device-width, initial-scale=1.0">
        <title>Human Review Dashboard</title>
        <style>
            * { margin: 0; padding: 0; box-sizing: border-box; }
            body {
                font-family: -apple-system, BlinkMacSystemFont, 'Segoe UI', Roboto, sans-serif;
                background: #0f1419;
                color: #e8eaed;
                padding: 20px;
            }
            .header {
                background: linear-gradient(135deg, #f59e0b 0%, #ef4444 100%);
                padding: 30px;
                border-radius: 12px;
                margin-bottom: 25px;
            }
            h1 { font-size: 32px; margin-bottom: 8px; }
            .subtitle { opacity: 0.9; font-size: 14px; }
            .stats-row {
                display: grid;
                grid-template-columns: repeat(auto-fit, minmax(150px, 1fr));
                gap: 15px;
                margin-bottom: 25px;
            }
            .stat-box {
                background: #1a1f2e;
                padding: 15px;
                border-radius: 8px;
                border: 1px solid #2d3748;
                text-align: center;
            }
            .stat-value { font-size: 28px; font-weight: bold; color: #f59e0b; }
            .stat-label { font-size: 12px; color: #a0aec0; margin-top: 5px; }
            .review-container {
                background: #1a1f2e;
                padding: 30px;
                border-radius: 12px;
                border: 1px solid #2d3748;
                margin-bottom: 20px;
            }
            .atis-text {
                background: #0f1419;
                padding: 20px;
                border-radius: 8px;
                border-left: 4px solid #f59e0b;
                margin: 20px 0;
                font-family: monospace;
                white-space: pre-wrap;
                line-height: 1.6;
            }
            .current-parse {
                background: #7f1d1d;
                border: 1px solid #991b1b;
                padding: 15px;
                border-radius: 8px;
                margin: 15px 0;
            }
            .form-group {
                margin: 20px 0;
            }
            label {
                display: block;
                margin-bottom: 8px;
                color: #a0aec0;
                font-weight: 500;
            }
            input[type="text"] {
                width: 100%;
                padding: 12px;
                background: #0f1419;
                border: 1px solid #2d3748;
                border-radius: 8px;
                color: #e8eaed;
                font-size: 14px;
            }
            input[type="text"]:focus {
                outline: none;
                border-color: #f59e0b;
            }
            textarea {
                width: 100%;
                padding: 12px;
                background: #0f1419;
                border: 1px solid #2d3748;
                border-radius: 8px;
                color: #e8eaed;
                font-size: 14px;
                min-height: 80px;
                resize: vertical;
            }
            .button-group {
                display: flex;
                gap: 15px;
                margin-top: 20px;
            }
            button {
                flex: 1;
                padding: 12px 24px;
                border: none;
                border-radius: 8px;
                font-size: 14px;
                font-weight: 600;
                cursor: pointer;
                transition: all 0.2s;
            }
            .btn-submit {
                background: #10b981;
                color: white;
            }
            .btn-submit:hover {
                background: #059669;
            }
            .btn-skip {
                background: #3b82f6;
                color: white;
            }
            .btn-skip:hover {
                background: #2563eb;
            }
            .nav-buttons {
                display: flex;
                gap: 15px;
                margin-bottom: 20px;
            }
            .btn-nav {
                flex: 1;
                padding: 10px 20px;
                border: 1px solid #2d3748;
                border-radius: 8px;
                font-size: 14px;
                font-weight: 600;
                cursor: pointer;
                background: #1a1f2e;
                color: #e8eaed;
                transition: all 0.2s;
            }
            .btn-nav:hover:not(:disabled) {
                background: #2d3748;
                border-color: #f59e0b;
            }
            .btn-nav:disabled {
                opacity: 0.4;
                cursor: not-allowed;
            }
            .badge {
                display: inline-block;
                padding: 4px 12px;
                border-radius: 12px;
                font-size: 11px;
                font-weight: 600;
                margin-left: 10px;
            }
            .badge-warning { background: #f59e0b; color: white; }
            .badge-danger { background: #ef4444; color: white; }
            .badge-info { background: #3b82f6; color: white; }
            .empty-state {
                text-align: center;
                padding: 60px 20px;
                color: #718096;
            }
            .empty-state h2 {
                font-size: 24px;
                margin-bottom: 10px;
                color: #10b981;
            }
            .help-text {
                font-size: 12px;
                color: #718096;
                margin-top: 5px;
            }
            .loading {
                text-align: center;
                padding: 40px;
            }
            .spinner {
                border: 3px solid #2d3748;
                border-top: 3px solid #f59e0b;
                border-radius: 50%;
                width: 40px;
                height: 40px;
                animation: spin 1s linear infinite;
                margin: 0 auto;
            }
            @keyframes spin {
                0% { transform: rotate(0deg); }
                100% { transform: rotate(360deg); }
            }
            .airport-list { margin-top: 15px; }
            .airport-item {
                display: flex;
                align-items: center;
                padding: 12px;
                background: #0f1419;
                border-radius: 8px;
                margin-bottom: 8px;
                border: 1px solid #2d3748;
                cursor: pointer;
                transition: background 0.2s;
            }
            .airport-item:hover { background: #1a1f2e; }
            .airport-item.pinned { background: #1e2a3a; border-color: #667eea; }
            .pin-icon {
                font-size: 16px;
                margin-right: 12px;
                cursor: pointer;
                user-select: none;
                opacity: 0.5;
                transition: opacity 0.2s;
            }
            .pin-icon:hover, .airport-item.pinned .pin-icon { opacity: 1; }
            .airport-code { font-weight: bold; margin-right: 12px; min-width: 50px; }
            .airport-runways { flex: 1; font-size: 14px; color: #a0aec0; }
            .airport-time { font-size: 12px; color: #718096; margin-right: 12px; }
            .chevron {
                font-size: 12px;
                transition: transform 0.3s;
                user-select: none;
            }
            .chevron.open { transform: rotate(180deg); }
            .airport-drawer {
                max-height: 0;
                overflow: hidden;
                transition: max-height 0.3s ease-out;
                background: #0a0e14;
                border-radius: 0 0 8px 8px;
                margin-top: -8px;
                margin-bottom: 8px;
            }
            .airport-drawer.open { max-height: 600px; padding: 15px; }
            .drawer-change {
                padding: 10px;
                background: #1a1f2e;
                border-radius: 6px;
                margin-bottom: 8px;
                font-size: 13px;
            }
            .drawer-change-time { color: #667eea; font-weight: bold; margin-bottom: 5px; }
        </style>
    </head>
    <body>
        <div class="header">
            <h1>👤 Human Review Dashboard</h1>
            <div class="subtitle">Review and correct parsing results to improve accuracy</div>
        </div>

        <div class="stats-row" id="stats">
            <div class="stat-box">
                <div class="stat-value" id="pendingCount">-</div>
                <div class="stat-label">Pending Review</div>
            </div>
            <div class="stat-box">
                <div class="stat-value" id="reviewedCount">-</div>
                <div class="stat-label">Reviewed</div>
            </div>
            <div class="stat-box">
                <div class="stat-value" id="lowConfCount">-</div>
                <div class="stat-label">Low Confidence</div>
            </div>
            <div class="stat-box">
                <div class="stat-value" id="noneCount">-</div>
                <div class="stat-label">Has "None"</div>
            </div>
        </div>

        <div id="reviewQueue"></div>

        <script>
            let currentItem = null;

            async function loadStats() {
                try {
                    const response = await fetch('/api/review/stats');
                    const stats = await response.json();
                    document.getElementById('pendingCount').textContent = stats.pending_count;
                    document.getElementById('reviewedCount').textContent = stats.reviewed_count;
                    document.getElementById('lowConfCount').textContent = stats.low_confidence_count;
                    document.getElementById('noneCount').textContent = stats.has_none_count;
                } catch (error) {
                    console.error('Failed to load stats:', error);
                }
            }

            function getConfigIdFromUrl() {
                const params = new URLSearchParams(window.location.search);
                return params.get('config_id');
            }

            async function loadReviewItem(configId = null) {
                const container = document.getElementById('reviewQueue');
                container.innerHTML = '<div class="loading"><div class="spinner"></div></div>';

                try {
                    let item;
                    if (configId) {
                        // Load specific item by ID
                        const response = await fetch(`/api/review/item/${configId}`);
                        if (!response.ok) throw new Error('Item not found');
                        item = await response.json();
                    } else {
                        // Load first pending item
                        const response = await fetch('/api/review/pending?limit=1');
                        const queue = await response.json();

                        if (queue.length === 0) {
                            container.innerHTML = `
                                <div class="empty-state">
                                    <h2>✅ All Clear!</h2>
                                    <p>No items need review at this time.</p>
                                </div>
                            `;
                            return;
                        }

                        item = queue[0];
                        // Update URL with the first item's ID
                        window.history.replaceState({}, '', `/review?config_id=${item.id}`);
                    }

                    currentItem = item;
                    showCurrentItem();
                } catch (error) {
                    console.error('Failed to load review item:', error);
                    container.innerHTML = '<div class="empty-state"><p>Error loading review item</p></div>';
                }
            }

            function showCurrentItem() {
                const item = currentItem;
                const container = document.getElementById('reviewQueue');

                const issueLabel = {
                    'low_confidence': 'Low Confidence',
                    'has_none': 'Has "None"',
                    'parse_failed': 'Parse Failed',
                    'complete': 'Complete'
                }[item.issue_type] || item.issue_type;

                const badgeClass = {
                    'low_confidence': 'badge-warning',
                    'has_none': 'badge-danger',
                    'parse_failed': 'badge-danger',
                    'complete': 'badge-success'
                }[item.issue_type] || 'badge-info';

                container.innerHTML = `
                    <div class="nav-buttons">
                        <button id="prevBtn" class="btn-nav" onclick="navigateItem('prev')">← Previous</button>
                        <button id="nextBtn" class="btn-nav" onclick="navigateItem('next')">Next →</button>
                    </div>

                    <div class="review-container">
                        <h2>
                            ${item.airport_code}
                            <span class="badge ${badgeClass}">${issueLabel}</span>
                            <span style="float: right; font-size: 14px; color: #718096;">
                                Config ID: ${item.id}
                            </span>
                        </h2>

                        <div class="current-parse">
                            <strong>Current Parse (Confidence: ${(item.confidence * 100).toFixed(0)}%):</strong><br>
                            ${item.merged_from_pair && item.component_confidence ? `
                                Arriving: ${item.original_arriving.join(', ') || 'None'}
                                <span style="color: #48bb78;">(${(item.component_confidence.arrivals * 100).toFixed(0)}%)</span><br>
                                Departing: ${item.original_departing.join(', ') || 'None'}
                                <span style="color: #48bb78;">(${(item.component_confidence.departures * 100).toFixed(0)}%)</span>
                            ` : `
                                Arriving: ${item.original_arriving.join(', ') || 'None'}<br>
                                Departing: ${item.original_departing.join(', ') || 'None'}
                            `}
                        </div>

                        ${item.warnings && item.warnings.length > 0 ? `
                            <div style="margin: 15px 0;">
                                ${item.warnings.map(warning => {
                                    // Red border for reciprocal runways, blue for other warnings
                                    const isReciprocal = warning.includes('RECIPROCAL');
                                    const borderColor = isReciprocal ? '#E53E3E' : '#4299E1';
                                    const bgColor = isReciprocal ? '#FFF5F5' : '#EDF2F7';
                                    const textColor = isReciprocal ? '#C53030' : '#2C5282';

                                    return `
                                        <div style="background-color: ${bgColor}; border-left: 4px solid ${borderColor}; padding: 12px; margin-bottom: 10px; border-radius: 4px;">
                                            <strong style="color: ${textColor};">${warning.split(' - ')[0]}</strong>
                                            ${warning.includes(' - ') ? `<br><span style="font-size: 14px; color: #4A5568;">${warning.split(' - ')[1]}</span>` : ''}
                                        </div>
                                    `;
                                }).join('')}
                            </div>
                        ` : ''}

                        <div class="atis-text">${item.atis_text}</div>

                        <form id="reviewForm">
                            <div class="form-group">
                                <label>Corrected Arriving Runways</label>
                                <input type="text" id="arrivingInput"
                                       placeholder="e.g., 16L, 16C, 16R (or leave empty for none)"
                                       value="${item.original_arriving.join(', ')}">
                                <div class="help-text">Separate multiple runways with commas</div>
                            </div>

                            <div class="form-group">
                                <label>Corrected Departing Runways</label>
                                <input type="text" id="departingInput"
                                       placeholder="e.g., 34L, 34C, 34R (or leave empty for none)"
                                       value="${item.original_departing.join(', ')}">
                                <div class="help-text">Separate multiple runways with commas</div>
                            </div>

                            <div class="form-group">
                                <label>Notes (Optional)</label>
                                <textarea id="notesInput" placeholder="Add any notes about this correction..."></textarea>
                            </div>

                            <div class="button-group">
                                <button type="button" class="btn-skip" onclick="skipItem()">
                                    ✓ Mark as Correct
                                </button>
                                <button type="submit" class="btn-submit">
                                    💾 Submit Correction
                                </button>
                            </div>
                        </form>
                    </div>
                `;

                document.getElementById('reviewForm').addEventListener('submit', submitReview);
            }

            async function navigateItem(direction) {
                try {
                    const response = await fetch(`/api/review/navigate/${currentItem.id}/${direction}`);
                    const data = await response.json();

                    if (data.next_id) {
                        window.location.href = `/review?config_id=${data.next_id}`;
                    } else {
                        alert(data.message || 'No more items in this direction');
                    }
                } catch (error) {
                    console.error('Navigation error:', error);
                    alert('Failed to navigate');
                }
            }

            function detectReciprocalRunways(runways) {
                // Extract runway numbers (without L/C/R suffix)
                const runwayNumbers = runways.map(rwy => {
                    const match = rwy.match(/^(\d{1,2})/);
                    return match ? parseInt(match[1]) : null;
                }).filter(n => n !== null);

                // Check all pairs for reciprocals (differ by 18)
                for (let i = 0; i < runwayNumbers.length; i++) {
                    for (let j = i + 1; j < runwayNumbers.length; j++) {
                        const diff = Math.abs(runwayNumbers[i] - runwayNumbers[j]);
                        if (diff === 18) {
                            return true;  // Found reciprocal runways
                        }
                    }
                }
                return false;
            }

            async function submitReview(event) {
                event.preventDefault();

                const arrivingText = document.getElementById('arrivingInput').value.trim();
                const departingText = document.getElementById('departingInput').value.trim();
                const notes = document.getElementById('notesInput').value.trim();

                const correctedArriving = arrivingText ? arrivingText.split(',').map(r => r.trim()).filter(r => r) : [];
                const correctedDeparting = departingText ? departingText.split(',').map(r => r.trim()).filter(r => r) : [];

                // Check for reciprocal runways in the correction
                const allRunways = [...correctedArriving, ...correctedDeparting];
                if (detectReciprocalRunways(allRunways)) {
                    const confirmed = confirm(
                        '⚠️ WARNING: Reciprocal Runways Detected!\\n\\n' +
                        'Your correction contains opposite ends of the same runway (e.g., 16/34, 09/27).\\n' +
                        'Aircraft cannot use opposite runway ends simultaneously.\\n\\n' +
                        'This is probably WRONG data.\\n\\n' +
                        'Click OK to submit anyway, or Cancel to fix it.'
                    );
                    if (!confirmed) {
                        return;  // User canceled, don't submit
                    }
                }

                try {
                    const response = await fetch('/api/review/submit', {
                        method: 'POST',
                        headers: { 'Content-Type': 'application/json' },
                        body: JSON.stringify({
                            review_id: currentItem.id,
                            corrected_arriving: correctedArriving,
                            corrected_departing: correctedDeparting,
                            notes: notes || null,
                            reviewed_by: 'human_reviewer'
                        })
                    });

                    if (response.ok) {
                        loadStats();
                        // Navigate to next item after submitting
                        navigateItem('next');
                    } else {
                        const error = await response.json();
                        alert(`Failed to submit review: ${error.detail || 'Unknown error'}`);
                    }
                } catch (error) {
                    console.error('Submit error:', error);
                    alert('Failed to submit review');
                }
            }

            async function skipItem() {
                try {
                    const response = await fetch(`/api/review/skip/${currentItem.id}`, {
                        method: 'POST'
                    });

                    if (response.ok) {
                        loadStats();
                        // Navigate to next item after skipping
                        navigateItem('next');
                    } else {
                        alert('Failed to skip item');
                    }
                } catch (error) {
                    console.error('Skip error:', error);
                    alert('Failed to skip item');
                }
            }

            // Initial load
            loadStats();
            const configId = getConfigIdFromUrl();
            loadReviewItem(configId);

            // Refresh stats every 30 seconds
            setInterval(loadStats, 30000);
        </script>
    </body>
    </html>
    """
    return HTMLResponse(content=html_content)

@app.get("/dashboard", response_class=HTMLResponse)
async def dashboard():
    """Serve the runway status dashboard"""
    try:
        with open('/app/dashboard.html', 'r') as f:
            html_content = f.read()
        return HTMLResponse(content=html_content)
    except FileNotFoundError:
        return HTMLResponse(content="<html><body><h1>Error: Dashboard file not found</h1></body></html>", status_code=500)


@app.get("/api/dashboard/stats", response_model=DashboardStats)
async def get_dashboard_stats():
    """Get comprehensive dashboard statistics"""

    conn = get_db_connection()
    try:
        cursor = conn.cursor()

        # Get stale airports (no updates in 3+ hours)
        cursor.execute("""
            SELECT DISTINCT ON (airport_code)
                   airport_code,
                   collected_at,
                   EXTRACT(EPOCH FROM (NOW() - collected_at))/3600 as hours_since_update
            FROM atis_data
            ORDER BY airport_code, collected_at DESC
        """)
        all_airports = cursor.fetchall()

        stale_airports = []
        active_count = 0
        for apt in all_airports:
            hours_old = apt['hours_since_update']
            if hours_old >= 3:
                stale_airports.append({
                    'airport': apt['airport_code'],
                    'hours_since_update': round(hours_old, 1),
                    'last_update': apt['collected_at'].isoformat()
                })
            elif hours_old < 1:
                active_count += 1

        # Get activity stats by time period
        cursor.execute("""
            SELECT
                COUNT(CASE WHEN collected_at > NOW() - INTERVAL '1 hour' THEN 1 END) as hour,
                COUNT(CASE WHEN collected_at > NOW() - INTERVAL '1 day' THEN 1 END) as day,
                COUNT(CASE WHEN collected_at > NOW() - INTERVAL '7 days' THEN 1 END) as week,
                COUNT(CASE WHEN collected_at > NOW() - INTERVAL '30 days' THEN 1 END) as month
            FROM atis_data
        """)
        activity = cursor.fetchone()

        # Get parsing success stats
        cursor.execute("""
            WITH recent_atis AS (
                SELECT airport_code, datis_text, information_letter
                FROM atis_data
                WHERE collected_at > NOW() - INTERVAL '24 hours'
                  AND is_changed = true
            )
            SELECT COUNT(*) as total_records
            FROM recent_atis
        """)
        parsing_total = cursor.fetchone()['total_records']

        # Parse recent data to get success/failure counts
        cursor.execute("""
            SELECT airport_code, datis_text, information_letter
            FROM atis_data
            WHERE collected_at > NOW() - INTERVAL '24 hours'
              AND is_changed = true
            LIMIT 200
        """)
        recent_records = cursor.fetchall()

        success_count = 0
        failure_count = 0
        low_confidence = 0

        for record in recent_records:
            try:
                config = parser.parse(
                    record['airport_code'],
                    record['datis_text'],
                    record['information_letter']
                )
                if config.confidence_score >= 0.5:
                    success_count += 1
                    if config.confidence_score < 0.8:
                        low_confidence += 1
                else:
                    failure_count += 1
            except Exception:
                failure_count += 1

        # Get confidence stats by airport
        cursor.execute("""
            SELECT
                rc.airport_code,
                AVG(rc.confidence_score) as avg_confidence,
                COUNT(*) as config_count
            FROM runway_configs rc
            JOIN atis_data ad ON rc.atis_id = ad.id
            WHERE ad.collected_at > NOW() - INTERVAL '7 days'
            GROUP BY rc.airport_code
            HAVING COUNT(*) >= 1
            ORDER BY avg_confidence DESC
            LIMIT 20
        """)
        confidence_by_airport = cursor.fetchall()

        confidence_stats = {
            'by_airport': [
                {
                    'airport': row['airport_code'],
                    'avg_confidence': round(row['avg_confidence'], 2),
                    'sample_size': row['config_count']
                }
                for row in confidence_by_airport
            ],
            'overall_avg': round(sum(r['avg_confidence'] for r in confidence_by_airport) / len(confidence_by_airport), 2) if confidence_by_airport else 0
        }

        # Get recent runway changes
        cursor.execute("""
            SELECT
                airport_code,
                change_time,
                from_config,
                to_config,
                duration_minutes
            FROM runway_changes
            WHERE change_time > NOW() - INTERVAL '24 hours'
            ORDER BY change_time DESC
            LIMIT 50
        """)
        changes = cursor.fetchall()

        recent_changes = [
            {
                'airport': change['airport_code'],
                'time': change['change_time'].isoformat(),
                'from': change['from_config'],
                'to': change['to_config'],
                'duration_minutes': change['duration_minutes']
            }
            for change in changes
        ]

        return DashboardStats(
            current_time=datetime.utcnow().isoformat(),
            total_airports=len(all_airports),
            active_airports=active_count,
            stale_airports=stale_airports,
            parsing_stats={
                'total_parsed': success_count + failure_count,
                'successful': success_count,
                'failed': failure_count,
                'low_confidence': low_confidence,
                'success_rate': round(success_count / (success_count + failure_count) * 100, 1) if (success_count + failure_count) > 0 else 0
            },
            confidence_stats=confidence_stats,
            activity_stats={
                'last_hour': activity['hour'],
                'last_day': activity['day'],
                'last_week': activity['week'],
                'last_month': activity['month']
            },
            recent_changes=recent_changes
        )

    finally:
        conn.close()

@app.get("/api/dashboard/current-airports", response_model=List[AirportStatus])
async def get_current_airports():
    """Get current status for all airports"""
    conn = get_db_connection()
    try:
        cursor = conn.cursor()

        # Get latest config for each airport
        cursor.execute("""
            WITH latest_configs AS (
                SELECT DISTINCT ON (rc.airport_code)
                    rc.airport_code,
                    rc.arriving_runways,
                    rc.departing_runways,
                    rc.traffic_flow,
                    rc.created_at
                FROM runway_configs rc
                JOIN atis_data ad ON rc.atis_id = ad.id
                WHERE ad.collected_at > NOW() - INTERVAL '6 hours'
                ORDER BY rc.airport_code, rc.created_at DESC
            )
            SELECT * FROM latest_configs
            ORDER BY airport_code
        """)

        latest_configs = cursor.fetchall()

        result = []
        for config in latest_configs:
            airport = config['airport_code']

            # Get 4 most recent changes for this airport
            cursor.execute("""
                SELECT
                    from_config,
                    to_config,
                    change_time,
                    duration_minutes
                FROM runway_changes
                WHERE airport_code = %s
                ORDER BY change_time DESC
                LIMIT 4
            """, (airport,))

            changes = cursor.fetchall()
            recent_changes = []
            for change in changes:
                from_cfg = change['from_config'] or {}
                to_cfg = change['to_config'] or {}
                recent_changes.append({
                    'time': change['change_time'].isoformat(),
                    'from': {
                        'arriving': from_cfg.get('arriving', []),
                        'departing': from_cfg.get('departing', [])
                    },
                    'to': {
                        'arriving': to_cfg.get('arriving', []),
                        'departing': to_cfg.get('departing', [])
                    },
                    'duration_minutes': change['duration_minutes']
                })

            result.append(AirportStatus(
                airport_code=airport,
                arriving=config['arriving_runways'] or [],
                departing=config['departing_runways'] or [],
                flow=config['traffic_flow'] or 'UNKNOWN',
                last_change=config['created_at'].isoformat(),
                recent_changes=recent_changes
            ))

        return result

    finally:
        conn.close()

@app.get("/api/review/pending", response_model=List[ReviewItem])
async def get_pending_reviews(limit: int = Query(default=100, le=100)):
    """Get items needing human review - shows latest config per airport with real-time pairing"""

    conn = get_db_connection()
    try:
        # Get latest configs per airport with real-time pairing
        configs = get_latest_configs_per_airport(conn)

        # Sort by confidence (lowest first) and limit
        configs.sort(key=lambda x: (x['confidence_score'], x['collected_at']), reverse=False)
        configs = configs[:limit]

        # Build review items with warnings
        review_items = []
        for config in configs:
            # Determine issue type
            if config['confidence_score'] < 1.0:
                issue_type = 'low_confidence'
            elif not config['arriving_runways'] or not config['departing_runways']:
                issue_type = 'has_none'
            elif config['confidence_score'] == 1.0 and config['arriving_runways'] and config['departing_runways']:
                issue_type = 'complete'
            else:
                issue_type = 'parse_failed'

            # Detect reciprocal runways
            all_runways = list(config['arriving_runways']) + list(config['departing_runways'])
            has_reciprocals = detect_reciprocal_runways(all_runways)

            # Build warnings
            warnings = []
            if has_reciprocals:
                warnings.append("⚠️ RECIPROCAL RUNWAYS DETECTED - Data probably wrong (opposite ends of same runway in use)")
            if config.get('is_incomplete_pair', False):
                warnings.append("⚠️ Incomplete split ATIS pair - Missing recent DEP or ARR INFO broadcast")
            if config.get('merged_from_pair', False):
                warnings.append("ℹ️ Merged from separate ARR/DEP INFO broadcasts")

            review_items.append(ReviewItem(
                id=config['id'],
                atis_id=config['atis_id'],
                airport_code=config['airport_code'],
                atis_text=config['datis_text'],
                original_arriving=config['arriving_runways'] or [],
                original_departing=config['departing_runways'] or [],
                confidence=config['confidence_score'],
                collected_at=config['collected_at'].isoformat(),
                issue_type=issue_type,
                merged_from_pair=config.get('merged_from_pair', False),
                component_confidence=config.get('component_confidence'),
                has_reciprocal_runways=has_reciprocals,
                is_incomplete_pair=config.get('is_incomplete_pair', False),
                warnings=warnings
            ))

        return review_items

    finally:
        conn.close()

@app.post("/api/review/submit")
async def submit_review(submission: ReviewSubmission):
    """Submit a human correction"""

    conn = get_db_connection()
    try:
        cursor = conn.cursor()

        # Get the original config details
        cursor.execute("""
            SELECT rc.atis_id, rc.airport_code, rc.arriving_runways, rc.departing_runways,
                   rc.confidence_score, ad.datis_text
            FROM runway_configs rc
            JOIN atis_data ad ON rc.atis_id = ad.id
            WHERE rc.id = %s
        """, (submission.review_id,))

        config = cursor.fetchone()
        if not config:
            raise HTTPException(status_code=404, detail="Configuration not found")

        # Validate correction: Check for reciprocal runways
        all_corrected_runways = submission.corrected_arriving + submission.corrected_departing
        if detect_reciprocal_runways(all_corrected_runways):
            raise HTTPException(
                status_code=400,
                detail="Correction contains reciprocal runways (opposite ends of same runway). "
                       "Please verify the data - aircraft cannot use opposite runway ends simultaneously."
            )

        # Store the human review
        cursor.execute("""
            INSERT INTO human_reviews
            (atis_id, airport_code, runway_config_id, original_atis_text,
             original_arriving_runways, original_departing_runways, original_confidence,
             corrected_arriving_runways, corrected_departing_runways,
             review_status, reviewed_by, reviewed_at, notes)
            VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, NOW(), %s)
            RETURNING id
        """, (
            config['atis_id'],
            config['airport_code'],
            submission.review_id,
            config['datis_text'],
            json.dumps(config['arriving_runways'] or []),
            json.dumps(config['departing_runways'] or []),
            config['confidence_score'],
            json.dumps(submission.corrected_arriving),
            json.dumps(submission.corrected_departing),
            'corrected',
            submission.reviewed_by,
            submission.notes
        ))

        review_id = cursor.fetchone()['id']

        # Extract patterns from the correction for future learning
        # Store simple pattern: if ATIS contains these keywords -> use these runways
        atis_text = config['datis_text'].upper()

        # Look for runway mentions in ATIS text
        import re
        runway_pattern = r'\b\d{1,2}[LCR]?\b'
        mentioned_runways = set(re.findall(runway_pattern, atis_text))

        if mentioned_runways:
            cursor.execute("""
                INSERT INTO parsing_corrections
                (airport_code, pattern_text, arriving_runways, departing_runways)
                VALUES (%s, %s, %s, %s)
            """, (
                config['airport_code'],
                config['datis_text'][:200],  # Store snippet
                json.dumps(submission.corrected_arriving),
                json.dumps(submission.corrected_departing)
            ))

        conn.commit()

        return {
            "status": "success",
            "review_id": review_id,
            "message": "Review submitted successfully"
        }

    except Exception as e:
        conn.rollback()
        logger.error(f"Failed to submit review: {e}")
        raise HTTPException(status_code=500, detail=f"Failed to submit review: {str(e)}")
    finally:
        conn.close()

@app.post("/api/review/skip/{config_id}")
async def skip_review(config_id: int, notes: Optional[str] = None):
    """Mark an item as correctly parsed (skip review)"""

    conn = get_db_connection()
    try:
        cursor = conn.cursor()

        # Get the config details
        cursor.execute("""
            SELECT rc.atis_id, rc.airport_code, rc.arriving_runways, rc.departing_runways,
                   rc.confidence_score, ad.datis_text
            FROM runway_configs rc
            JOIN atis_data ad ON rc.atis_id = ad.id
            WHERE rc.id = %s
        """, (config_id,))

        config = cursor.fetchone()
        if not config:
            raise HTTPException(status_code=404, detail="Configuration not found")

        # Store as approved (skipped)
        cursor.execute("""
            INSERT INTO human_reviews
            (atis_id, airport_code, runway_config_id, original_atis_text,
             original_arriving_runways, original_departing_runways, original_confidence,
             corrected_arriving_runways, corrected_departing_runways,
             review_status, reviewed_by, reviewed_at, notes)
            VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, NOW(), %s)
        """, (
            config['atis_id'],
            config['airport_code'],
            config_id,
            config['datis_text'],
            json.dumps(config['arriving_runways'] or []),
            json.dumps(config['departing_runways'] or []),
            config['confidence_score'],
            json.dumps(config['arriving_runways'] or []),  # Same as original
            json.dumps(config['departing_runways'] or []),  # Same as original
            'approved',
            'human_reviewer',
            notes or 'Marked as correct'
        ))

        conn.commit()

        return {"status": "success", "message": "Item marked as correct"}

    except Exception as e:
        conn.rollback()
        raise HTTPException(status_code=500, detail=str(e))
    finally:
        conn.close()

@app.get("/api/review/item/{config_id}", response_model=ReviewItem)
async def get_review_item(config_id: int):
    """Get a specific review item by config ID"""

    conn = get_db_connection()
    try:
        cursor = conn.cursor()

        cursor.execute("""
            SELECT
                rc.id as config_id,
                rc.atis_id,
                rc.airport_code,
                ad.datis_text,
                rc.arriving_runways,
                rc.departing_runways,
                rc.confidence_score,
                ad.collected_at,
                rc.merged_from_pair,
                rc.component_confidence,
                CASE
                    WHEN rc.confidence_score < 1.0 THEN 'low_confidence'
                    WHEN rc.arriving_runways::text = '[]' OR rc.departing_runways::text = '[]' THEN 'has_none'
                    WHEN rc.confidence_score = 1.0 AND rc.arriving_runways::text != '[]' AND rc.departing_runways::text != '[]' THEN 'complete'
                    ELSE 'parse_failed'
                END as issue_type
            FROM runway_configs rc
            JOIN atis_data ad ON rc.atis_id = ad.id
            WHERE rc.id = %s
        """, (config_id,))

        row = cursor.fetchone()

        if not row:
            raise HTTPException(status_code=404, detail="Review item not found")

        return ReviewItem(
            id=row['config_id'],
            atis_id=row['atis_id'],
            airport_code=row['airport_code'],
            atis_text=row['datis_text'],
            original_arriving=row['arriving_runways'] or [],
            original_departing=row['departing_runways'] or [],
            confidence=row['confidence_score'],
            collected_at=row['collected_at'].isoformat(),
            issue_type=row['issue_type'],
            merged_from_pair=row['merged_from_pair'] or False,
            component_confidence=row['component_confidence']
        )

    finally:
        conn.close()

@app.get("/api/review/navigate/{config_id}/{direction}")
async def navigate_review(config_id: int, direction: str):
    """Get next or previous review item (Option A: exclude already reviewed)"""

    if direction not in ['next', 'prev']:
        raise HTTPException(status_code=400, detail="Direction must be 'next' or 'prev'")

    conn = get_db_connection()
    try:
        cursor = conn.cursor()

        # Find next/previous unreviewed config
        if direction == 'next':
            cursor.execute("""
                SELECT rc.id
                FROM runway_configs rc
                LEFT JOIN human_reviews hr ON rc.id = hr.runway_config_id
                WHERE hr.id IS NULL
                  AND (rc.confidence_score < 1.0
                       OR rc.arriving_runways::text = '[]'
                       OR rc.departing_runways::text = '[]')
                  AND rc.created_at > NOW() - INTERVAL '6 hours'
                  AND rc.id > %s
                ORDER BY rc.id ASC
                LIMIT 1
            """, (config_id,))
        else:  # prev
            cursor.execute("""
                SELECT rc.id
                FROM runway_configs rc
                LEFT JOIN human_reviews hr ON rc.id = hr.runway_config_id
                WHERE hr.id IS NULL
                  AND (rc.confidence_score < 1.0
                       OR rc.arriving_runways::text = '[]'
                       OR rc.departing_runways::text = '[]')
                  AND rc.created_at > NOW() - INTERVAL '6 hours'
                  AND rc.id < %s
                ORDER BY rc.id DESC
                LIMIT 1
            """, (config_id,))

        result = cursor.fetchone()

        if not result:
            return {"next_id": None, "message": "No more items in this direction"}

        return {"next_id": result['id']}

    finally:
        conn.close()

@app.get("/api/review/stats", response_model=ReviewStats)
async def get_review_stats():
    """Get review statistics"""

    conn = get_db_connection()
    try:
        cursor = conn.cursor()

        # Count pending items
        cursor.execute("""
            SELECT COUNT(DISTINCT rc.id) as pending
            FROM runway_configs rc
            LEFT JOIN human_reviews hr ON rc.id = hr.runway_config_id
            WHERE hr.id IS NULL
              AND (rc.confidence_score < 1.0
                   OR rc.arriving_runways::text = '[]'
                   OR rc.departing_runways::text = '[]')
              AND rc.created_at > NOW() - INTERVAL '7 days'
        """)
        pending = cursor.fetchone()['pending']

        # Count reviewed items
        cursor.execute("""
            SELECT COUNT(*) as reviewed
            FROM human_reviews
            WHERE review_status IN ('corrected', 'approved')
        """)
        reviewed = cursor.fetchone()['reviewed']

        # Count by issue type
        cursor.execute("""
            SELECT
                COUNT(CASE WHEN confidence_score < 1.0 THEN 1 END) as low_conf,
                COUNT(CASE WHEN arriving_runways::text = '[]' OR departing_runways::text = '[]' THEN 1 END) as has_none
            FROM runway_configs rc
            LEFT JOIN human_reviews hr ON rc.id = hr.runway_config_id
            WHERE hr.id IS NULL
              AND rc.created_at > NOW() - INTERVAL '7 days'
        """)
        counts = cursor.fetchone()

        return ReviewStats(
            pending_count=pending,
            reviewed_count=reviewed,
            low_confidence_count=counts['low_conf'],
            has_none_count=counts['has_none'],
            failed_parse_count=0
        )

    finally:
        conn.close()

@app.get("/api/dashboard/current-airports", response_model=List[AirportStatus])
async def get_current_airports():
    """Get current status for all airports with their 4 most recent runway changes"""
    
    conn = get_db_connection()
    try:
        cursor = conn.cursor()
        
        # Get latest runway config for each airport
        cursor.execute("""
            WITH latest_configs AS (
                SELECT DISTINCT ON (airport_code)
                    airport_code,
                    arriving_runways,
                    departing_runways,
                    traffic_flow,
                    created_at
                FROM runway_configs
                ORDER BY airport_code, created_at DESC
            )
            SELECT * FROM latest_configs
            ORDER BY airport_code
        """)
        
        airports_data = cursor.fetchall()
        airport_statuses = []
        
        for airport_row in airports_data:
            airport_code = airport_row['airport_code']
            
            # Get 4 most recent runway changes for this airport
            cursor.execute("""
                SELECT
                    change_time,
                    from_config,
                    to_config,
                    duration_minutes
                FROM runway_changes
                WHERE airport_code = %s
                ORDER BY change_time DESC
                LIMIT 4
            """, (airport_code,))
            
            changes = cursor.fetchall()
            recent_changes = []
            
            for change in changes:
                recent_changes.append(RunwayChangeItem(
                    time=change['change_time'].isoformat(),
                    from_arriving=change['from_config'].get('arriving', []) if change['from_config'] else [],
                    from_departing=change['from_config'].get('departing', []) if change['from_config'] else [],
                    to_arriving=change['to_config'].get('arriving', []) if change['to_config'] else [],
                    to_departing=change['to_config'].get('departing', []) if change['to_config'] else [],
                    duration_minutes=change['duration_minutes']
                ))
            
            airport_statuses.append(AirportStatus(
                airport_code=airport_code,
                arriving=airport_row['arriving_runways'] or [],
                departing=airport_row['departing_runways'] or [],
                flow=airport_row['traffic_flow'] or 'UNKNOWN',
                last_change=airport_row['created_at'].isoformat() if airport_row['created_at'] else None,
                recent_changes=recent_changes
            ))
        
        return airport_statuses
        
    finally:
        conn.close()

# Health check endpoint
@app.get("/health")
async def health_check():
    """Simple health check endpoint"""
    return {"status": "healthy"}

if __name__ == "__main__":
    import uvicorn
    uvicorn.run(app, host="0.0.0.0", port=8000)
