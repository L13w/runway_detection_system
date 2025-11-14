#!/usr/bin/env python3
"""
Runway Detection API
FastAPI server providing runway configuration information
"""

from fastapi import FastAPI, HTTPException, Query, BackgroundTasks
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import HTMLResponse
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

class DashboardStats(BaseModel):
    current_time: str
    total_airports: int
    active_airports: int
    stale_airports: List[Dict]  # Airports with no updates in 3+ hours
    parsing_stats: Dict  # Success/failure rates
    confidence_stats: Dict  # Average confidence by airport
    activity_stats: Dict  # Updates by time period (hour, day, week, month)
    recent_changes: List[Dict]  # Recent runway config changes

class ReviewItem(BaseModel):
    id: int
    atis_id: int
    airport_code: str
    atis_text: str
    original_arriving: List[str]
    original_departing: List[str]
    confidence: float
    collected_at: str
    issue_type: str  # 'low_confidence', 'has_none', 'parse_failed'

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
            let currentQueue = [];
            let currentIndex = 0;

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

            async function loadQueue() {
                const container = document.getElementById('reviewQueue');
                container.innerHTML = '<div class="loading"><div class="spinner"></div></div>';

                try {
                    const response = await fetch('/api/review/pending?limit=20');
                    currentQueue = await response.json();
                    currentIndex = 0;

                    if (currentQueue.length === 0) {
                        container.innerHTML = `
                            <div class="empty-state">
                                <h2>✅ All Clear!</h2>
                                <p>No items need review at this time.</p>
                            </div>
                        `;
                        return;
                    }

                    showCurrentItem();
                } catch (error) {
                    console.error('Failed to load queue:', error);
                    container.innerHTML = '<div class="empty-state"><p>Error loading review queue</p></div>';
                }
            }

            function showCurrentItem() {
                if (currentIndex >= currentQueue.length) {
                    loadQueue(); // Reload if we've gone through all items
                    return;
                }

                const item = currentQueue[currentIndex];
                const container = document.getElementById('reviewQueue');

                const issueLabel = {
                    'low_confidence': 'Low Confidence',
                    'has_none': 'Has "None"',
                    'parse_failed': 'Parse Failed'
                }[item.issue_type] || item.issue_type;

                const badgeClass = {
                    'low_confidence': 'badge-warning',
                    'has_none': 'badge-danger',
                    'parse_failed': 'badge-danger'
                }[item.issue_type] || 'badge-info';

                container.innerHTML = `
                    <div class="review-container">
                        <h2>
                            ${item.airport_code}
                            <span class="badge ${badgeClass}">${issueLabel}</span>
                            <span style="float: right; font-size: 14px; color: #718096;">
                                ${currentIndex + 1} of ${currentQueue.length}
                            </span>
                        </h2>

                        <div class="current-parse">
                            <strong>Current Parse (Confidence: ${(item.confidence * 100).toFixed(0)}%):</strong><br>
                            Arriving: ${item.original_arriving.join(', ') || 'None'}<br>
                            Departing: ${item.original_departing.join(', ') || 'None'}
                        </div>

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

            async function submitReview(event) {
                event.preventDefault();

                const item = currentQueue[currentIndex];
                const arrivingText = document.getElementById('arrivingInput').value.trim();
                const departingText = document.getElementById('departingInput').value.trim();
                const notes = document.getElementById('notesInput').value.trim();

                const correctedArriving = arrivingText ? arrivingText.split(',').map(r => r.trim()).filter(r => r) : [];
                const correctedDeparting = departingText ? departingText.split(',').map(r => r.trim()).filter(r => r) : [];

                try {
                    const response = await fetch('/api/review/submit', {
                        method: 'POST',
                        headers: { 'Content-Type': 'application/json' },
                        body: JSON.stringify({
                            review_id: item.id,
                            corrected_arriving: correctedArriving,
                            corrected_departing: correctedDeparting,
                            notes: notes || null,
                            reviewed_by: 'human_reviewer'
                        })
                    });

                    if (response.ok) {
                        currentIndex++;
                        loadStats();
                        showCurrentItem();
                    } else {
                        alert('Failed to submit review');
                    }
                } catch (error) {
                    console.error('Submit error:', error);
                    alert('Failed to submit review');
                }
            }

            async function skipItem() {
                const item = currentQueue[currentIndex];

                try {
                    const response = await fetch(`/api/review/skip/${item.id}`, {
                        method: 'POST'
                    });

                    if (response.ok) {
                        currentIndex++;
                        loadStats();
                        showCurrentItem();
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
            loadQueue();

            // Refresh stats every 30 seconds
            setInterval(loadStats, 30000);
        </script>
    </body>
    </html>
    """
    return HTMLResponse(content=html_content)

@app.get("/dashboard", response_class=HTMLResponse)
async def dashboard():
    """Serve the dashboard HTML page"""
    html_content = """
    <!DOCTYPE html>
    <html lang="en">
    <head>
        <meta charset="UTF-8">
        <meta name="viewport" content="width=device-width, initial-scale=1.0">
        <title>Runway Direction Dashboard</title>
        <style>
            * { margin: 0; padding: 0; box-sizing: border-box; }
            body {
                font-family: -apple-system, BlinkMacSystemFont, 'Segoe UI', Roboto, Oxygen, Ubuntu, Cantarell, sans-serif;
                background: #0f1419;
                color: #e8eaed;
                padding: 20px;
            }
            .header {
                background: linear-gradient(135deg, #667eea 0%, #764ba2 100%);
                padding: 30px;
                border-radius: 12px;
                margin-bottom: 25px;
                box-shadow: 0 4px 6px rgba(0,0,0,0.3);
            }
            h1 { font-size: 32px; margin-bottom: 8px; }
            .subtitle { opacity: 0.9; font-size: 14px; }
            .stats-grid {
                display: grid;
                grid-template-columns: repeat(auto-fit, minmax(250px, 1fr));
                gap: 20px;
                margin-bottom: 25px;
            }
            .stat-card {
                background: #1a1f2e;
                padding: 24px;
                border-radius: 12px;
                border: 1px solid #2d3748;
                transition: transform 0.2s, box-shadow 0.2s;
            }
            .stat-card:hover {
                transform: translateY(-2px);
                box-shadow: 0 8px 16px rgba(0,0,0,0.4);
            }
            .stat-value {
                font-size: 36px;
                font-weight: bold;
                margin: 10px 0;
                color: #667eea;
            }
            .stat-label {
                font-size: 13px;
                color: #a0aec0;
                text-transform: uppercase;
                letter-spacing: 0.5px;
            }
            .stat-sublabel {
                font-size: 12px;
                color: #718096;
                margin-top: 5px;
            }
            .section {
                background: #1a1f2e;
                padding: 25px;
                border-radius: 12px;
                margin-bottom: 25px;
                border: 1px solid #2d3748;
            }
            .section-title {
                font-size: 20px;
                margin-bottom: 20px;
                color: #667eea;
                display: flex;
                align-items: center;
                gap: 10px;
            }
            .badge {
                display: inline-block;
                padding: 4px 12px;
                border-radius: 12px;
                font-size: 11px;
                font-weight: 600;
                text-transform: uppercase;
            }
            .badge-success { background: #10b981; color: white; }
            .badge-warning { background: #f59e0b; color: white; }
            .badge-danger { background: #ef4444; color: white; }
            .badge-info { background: #3b82f6; color: white; }
            .confidence-list {
                display: grid;
                gap: 10px;
            }
            .confidence-item {
                display: flex;
                justify-content: space-between;
                align-items: center;
                padding: 12px;
                background: #0f1419;
                border-radius: 8px;
                border: 1px solid #2d3748;
            }
            .confidence-bar {
                flex: 1;
                height: 8px;
                background: #2d3748;
                border-radius: 4px;
                margin: 0 15px;
                overflow: hidden;
            }
            .confidence-fill {
                height: 100%;
                background: linear-gradient(90deg, #10b981 0%, #667eea 100%);
                transition: width 0.3s;
            }
            .changes-list {
                max-height: 400px;
                overflow-y: auto;
            }
            .change-item {
                padding: 15px;
                background: #0f1419;
                border-radius: 8px;
                margin-bottom: 10px;
                border-left: 3px solid #667eea;
            }
            .change-header {
                display: flex;
                justify-content: space-between;
                margin-bottom: 8px;
            }
            .change-time {
                font-size: 12px;
                color: #718096;
            }
            .runway-change {
                display: flex;
                align-items: center;
                gap: 10px;
                font-size: 14px;
                margin-top: 5px;
            }
            .arrow { color: #667eea; font-size: 18px; }
            .stale-alert {
                background: #7f1d1d;
                border: 1px solid #991b1b;
                padding: 12px;
                border-radius: 8px;
                margin-bottom: 10px;
                display: flex;
                justify-content: space-between;
                align-items: center;
            }
            .loading {
                text-align: center;
                padding: 40px;
                color: #718096;
            }
            .spinner {
                border: 3px solid #2d3748;
                border-top: 3px solid #667eea;
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
            .last-updated {
                text-align: center;
                color: #718096;
                font-size: 12px;
                margin-top: 20px;
            }
            ::-webkit-scrollbar { width: 8px; }
            ::-webkit-scrollbar-track { background: #1a1f2e; }
            ::-webkit-scrollbar-thumb { background: #2d3748; border-radius: 4px; }
            ::-webkit-scrollbar-thumb:hover { background: #4a5568; }
        </style>
    </head>
    <body>
        <div class="header">
            <h1>🛬 Runway Direction Dashboard</h1>
            <div class="subtitle">Real-time monitoring of airport runway configurations</div>
            <div class="subtitle" style="color: #e74c3c; font-weight: bold; margin-top: 10px;">⚠️ IMPORTANT: This system is under development. Accuracy is neither expected nor guaranteed.</div>
        </div>

        <div id="dashboard">
            <div class="loading">
                <div class="spinner"></div>
                <p style="margin-top: 15px;">Loading dashboard data...</p>
            </div>
        </div>

        <div class="last-updated" id="lastUpdated"></div>

        <script>
            let refreshInterval;

            async function fetchDashboardData() {
                try {
                    const response = await fetch('/api/dashboard/stats');
                    const data = await response.json();
                    renderDashboard(data);
                    document.getElementById('lastUpdated').textContent =
                        `Last updated: ${new Date().toLocaleTimeString()}`;
                } catch (error) {
                    console.error('Error fetching dashboard data:', error);
                    document.getElementById('dashboard').innerHTML =
                        '<div class="loading"><p>Error loading dashboard data</p></div>';
                }
            }

            function renderDashboard(data) {
                const dashboard = document.getElementById('dashboard');

                const html = `
                    <div class="stats-grid">
                        <div class="stat-card">
                            <div class="stat-label">Total Airports</div>
                            <div class="stat-value">${data.total_airports}</div>
                            <div class="stat-sublabel">${data.active_airports} currently active</div>
                        </div>
                        <div class="stat-card">
                            <div class="stat-label">Updates (24h)</div>
                            <div class="stat-value">${data.activity_stats.last_day.toLocaleString()}</div>
                            <div class="stat-sublabel">${data.activity_stats.last_hour} in last hour</div>
                        </div>
                        <div class="stat-card">
                            <div class="stat-label">Parsing Success</div>
                            <div class="stat-value">${data.parsing_stats.success_rate}%</div>
                            <div class="stat-sublabel">
                                ${data.parsing_stats.successful} / ${data.parsing_stats.total_parsed} successful
                            </div>
                        </div>
                        <div class="stat-card">
                            <div class="stat-label">Avg Confidence</div>
                            <div class="stat-value">${(data.confidence_stats.overall_avg * 100).toFixed(0)}%</div>
                            <div class="stat-sublabel">
                                ${data.parsing_stats.low_confidence} with low confidence
                            </div>
                        </div>
                    </div>

                    ${data.stale_airports.length > 0 ? `
                    <div class="section">
                        <div class="section-title">
                            ⚠️ Stale Airports (No Updates 3+ Hours)
                        </div>
                        ${data.stale_airports.map(apt => `
                            <div class="stale-alert">
                                <div>
                                    <strong>${apt.airport}</strong>
                                    <span style="margin-left: 10px; color: #718096;">
                                        ${apt.hours_since_update.toFixed(1)} hours ago
                                    </span>
                                </div>
                                <span class="badge badge-danger">Stale</span>
                            </div>
                        `).join('')}
                    </div>
                    ` : ''}

                    <div class="section">
                        <div class="section-title">
                            🔄 Recent Runway Configuration Changes (24h)
                        </div>
                        <div class="changes-list">
                            ${data.recent_changes.length > 0 ? data.recent_changes.map(change => `
                                <div class="change-item">
                                    <div class="change-header">
                                        <strong>${change.airport}</strong>
                                        <span class="change-time">
                                            ${new Date(change.time).toLocaleString()}
                                        </span>
                                    </div>
                                    <div class="runway-change">
                                        <span>
                                            ↓ ${(change.from.arriving || []).join(', ') || 'None'}
                                            ↑ ${(change.from.departing || []).join(', ') || 'None'}
                                        </span>
                                        <span class="arrow">→</span>
                                        <span>
                                            ↓ ${(change.to.arriving || []).join(', ') || 'None'}
                                            ↑ ${(change.to.departing || []).join(', ') || 'None'}
                                        </span>
                                    </div>
                                    ${change.duration_minutes ? `
                                        <div class="stat-sublabel" style="margin-top: 5px;">
                                            Previous config lasted ${change.duration_minutes} minutes
                                        </div>
                                    ` : ''}
                                </div>
                            `).join('') : '<p style="color: #718096;">No changes in the last 24 hours</p>'}
                        </div>
                    </div>

                    ${data.confidence_stats.by_airport.filter(apt => apt.avg_confidence < 1.0).length > 0 ? `
                    <div class="section">
                        <div class="section-title">
                            📊 Low Confidence Airports (< 100%)
                        </div>
                        <div class="confidence-list">
                            ${data.confidence_stats.by_airport.filter(apt => apt.avg_confidence < 1.0).slice(0, 10).map(apt => `
                                <div class="confidence-item">
                                    <strong style="min-width: 60px;">${apt.airport}</strong>
                                    <div class="confidence-bar">
                                        <div class="confidence-fill" style="width: ${apt.avg_confidence * 100}%"></div>
                                    </div>
                                    <span style="min-width: 50px; text-align: right;">
                                        ${(apt.avg_confidence * 100).toFixed(0)}%
                                    </span>
                                    <span class="badge ${apt.avg_confidence >= 0.8 ? 'badge-success' : apt.avg_confidence >= 0.5 ? 'badge-warning' : 'badge-danger'}">
                                        ${apt.sample_size} samples
                                    </span>
                                </div>
                            `).join('')}
                        </div>
                    </div>
                    ` : ''}

                    <div class="stats-grid">
                        <div class="stat-card">
                            <div class="stat-label">Last Week</div>
                            <div class="stat-value">${data.activity_stats.last_week.toLocaleString()}</div>
                            <div class="stat-sublabel">updates collected</div>
                        </div>
                        <div class="stat-card">
                            <div class="stat-label">Last Month</div>
                            <div class="stat-value">${data.activity_stats.last_month.toLocaleString()}</div>
                            <div class="stat-sublabel">updates collected</div>
                        </div>
                        <div class="stat-card">
                            <div class="stat-label">Failed Parses</div>
                            <div class="stat-value">${data.parsing_stats.failed}</div>
                            <div class="stat-sublabel">in last 24 hours</div>
                        </div>
                        <div class="stat-card">
                            <div class="stat-label">Runway Changes</div>
                            <div class="stat-value">${data.recent_changes.length}</div>
                            <div class="stat-sublabel">in last 24 hours</div>
                        </div>
                    </div>
                `;

                dashboard.innerHTML = html;
            }

            // Initial load
            fetchDashboardData();

            // Auto-refresh every 30 seconds
            refreshInterval = setInterval(fetchDashboardData, 30000);

            // Cleanup on page unload
            window.addEventListener('beforeunload', () => {
                if (refreshInterval) clearInterval(refreshInterval);
            });
        </script>
    </body>
    </html>
    """
    return HTMLResponse(content=html_content)

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

@app.get("/api/review/pending", response_model=List[ReviewItem])
async def get_pending_reviews(limit: int = Query(default=20, le=100)):
    """Get items needing human review"""

    conn = get_db_connection()
    try:
        cursor = conn.cursor()

        # Get runway configs with issues that need review
        cursor.execute("""
            WITH issue_configs AS (
                SELECT DISTINCT ON (rc.id)
                    rc.id as config_id,
                    rc.atis_id,
                    rc.airport_code,
                    rc.arriving_runways,
                    rc.departing_runways,
                    rc.confidence_score,
                    ad.datis_text,
                    ad.collected_at,
                    CASE
                        WHEN rc.confidence_score < 1.0 THEN 'low_confidence'
                        WHEN rc.arriving_runways::text = '[]' OR rc.departing_runways::text = '[]' THEN 'has_none'
                        ELSE 'parse_failed'
                    END as issue_type
                FROM runway_configs rc
                JOIN atis_data ad ON rc.atis_id = ad.id
                LEFT JOIN human_reviews hr ON rc.id = hr.runway_config_id AND hr.review_status = 'corrected'
                WHERE hr.id IS NULL  -- Not already reviewed
                  AND (rc.confidence_score < 1.0
                       OR rc.arriving_runways::text = '[]'
                       OR rc.departing_runways::text = '[]')
                  AND ad.collected_at > NOW() - INTERVAL '7 days'
                ORDER BY rc.id, rc.created_at DESC
            )
            SELECT
                config_id,
                atis_id,
                airport_code,
                datis_text,
                arriving_runways,
                departing_runways,
                confidence_score,
                collected_at,
                issue_type
            FROM issue_configs
            ORDER BY confidence_score ASC, collected_at DESC
            LIMIT %s
        """, (limit,))

        results = cursor.fetchall()

        review_items = []
        for row in results:
            review_items.append(ReviewItem(
                id=row['config_id'],
                atis_id=row['atis_id'],
                airport_code=row['airport_code'],
                atis_text=row['datis_text'],
                original_arriving=json.loads(row['arriving_runways'] or '[]'),
                original_departing=json.loads(row['departing_runways'] or '[]'),
                confidence=row['confidence_score'],
                collected_at=row['collected_at'].isoformat(),
                issue_type=row['issue_type']
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
            config['arriving_runways'],
            config['departing_runways'],
            config['confidence_score'],
            json.dumps(submission.corrected_arriving),
            json.dumps(submission.corrected_departing),
            'corrected',
            submission.reviewed_by,
            submission.notes
        ))

        review_id = cursor.fetchone()[0]

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
            config['arriving_runways'],
            config['departing_runways'],
            config['confidence_score'],
            config['arriving_runways'],  # Same as original
            config['departing_runways'],  # Same as original
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

# Health check endpoint
@app.get("/health")
async def health_check():
    """Simple health check endpoint"""
    return {"status": "healthy"}

if __name__ == "__main__":
    import uvicorn
    uvicorn.run(app, host="0.0.0.0", port=8000)
