#!/usr/bin/env python3
"""
Runway Detection API
FastAPI server providing runway configuration information
"""

from fastapi import FastAPI, HTTPException, Query, BackgroundTasks
from fastapi.middleware.cors import CORSMiddleware
from pydantic import BaseModel
from typing import List, Optional, Dict
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

# Database connection helper
def get_db_connection():
    """Create database connection"""
    try:
        return psycopg2.connect(**DB_CONFIG, cursor_factory=RealDictCursor)
    except Exception as e:
        logger.error(f"Database connection failed: {e}")
        raise HTTPException(status_code=500, detail="Database connection failed")

# API Endpoints
@app.get("/", response_model=Dict)
async def root():
    """API root endpoint with basic information"""
    return {
        "name": "Runway Direction API",
        "version": "1.0.0",
        "endpoints": {
            "/api/runway/{airport_code}": "Get current runway configuration",
            "/api/runways/all": "Get all airports' runway configurations",
            "/api/runway/{airport_code}/history": "Get runway configuration history",
            "/api/airports": "List all monitored airports",
            "/api/status": "System status",
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

# Health check endpoint
@app.get("/health")
async def health_check():
    """Simple health check endpoint"""
    return {"status": "healthy"}

if __name__ == "__main__":
    import uvicorn
    uvicorn.run(app, host="0.0.0.0", port=8000)
