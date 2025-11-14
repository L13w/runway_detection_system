# Runway Direction Detection System - Implementation Plan

## Executive Summary
Build a system to parse D-ATIS data from https://datis.clowd.io/api/all to determine active runway directions at US airports, filling a gap in available aviation APIs.

## Why Runway Direction Isn't Available via API (Analysis)
The reason runway direction information isn't readily available via API while other weather data is:

1. **Dynamic Nature**: Runway configurations change based on wind, traffic flow, and operational needs - more complex than static weather readings
2. **Safety Critical**: This is operational ATC data that requires interpretation, not just measurement
3. **No Standardized Format**: Each airport's ATIS has slightly different phrasing for runway information
4. **Liability Concerns**: Providing incorrect runway information could have safety implications
5. **Source Complexity**: This data comes from ATC operations, not automated weather stations (ASOS/AWOS)

## Phase 1: Data Collection Strategy

### Collection Schedule
- **Primary Schedule**: Every 5 minutes (aligns with :03/:33 update pattern)
- **Rationale**: Captures regular updates plus any emergency changes
- **Storage Estimate**: ~100KB per pull × 288 pulls/day = ~29MB/day raw data

### Data Collection Script
```python
# Runs every 5 minutes via cron/scheduler
import requests
import json
from datetime import datetime
import hashlib

def collect_atis_data():
    response = requests.get('https://datis.clowd.io/api/all')
    data = response.json()
    
    # Add timestamp
    data['collected_at'] = datetime.utcnow().isoformat()
    
    # Create hash to detect changes
    for airport in data:
        if airport.get('datis'):
            airport['content_hash'] = hashlib.md5(
                airport['datis'].encode()
            ).hexdigest()
    
    # Store in database
    store_atis_snapshot(data)
    
    # Check for changes and flag them
    detect_changes(data)
```

### Training Data Requirements
- **Minimum Viable Dataset**: 2-3 weeks (4,000+ samples)
- **Robust Dataset**: 1-2 months (8,000-17,000 samples)
- **Seasonal Coverage**: 3+ months (captures seasonal wind patterns)
- **Diminishing Returns**: After ~3 months for most airports

## Phase 2: Data Processing & Feature Extraction

### Runway Information Representation
```python
@dataclass
class RunwayConfiguration:
    """Standardized runway configuration"""
    airport_code: str
    timestamp: datetime
    information_letter: str  # ATIS letter (A, B, C, etc.)
    
    # Active runways by operation type
    arriving_runways: List[str]  # e.g., ["16L", "16C", "16R"]
    departing_runways: List[str]  # e.g., ["16L", "16C", "16R"]
    
    # Simplified direction indicator
    traffic_flow: str  # "NORTH", "SOUTH", "EAST", "WEST", "MIXED"
    
    # Configuration pattern (for common configs)
    configuration_name: Optional[str]  # e.g., "South Flow", "West Plan"
    
    # Metadata
    wind_direction: Optional[int]
    wind_speed: Optional[int]
    visibility: Optional[float]
    confidence_score: float  # Model confidence in extraction
```

### Pattern Extraction Pipeline
```python
class ATISParser:
    def __init__(self):
        self.runway_patterns = [
            # Approach patterns
            r'(?:APCH|APPROACH|APCHS|APPROACHES?)\s+(?:IN USE\s+)?(?:RWY?S?\s+)?([0-9]{1,2}[LCR]?(?:\s+AND\s+[0-9]{1,2}[LCR]?)*)',
            r'(?:LANDING|LDG)\s+(?:RWY?S?\s+)?([0-9]{1,2}[LCR]?(?:\s+AND\s+[0-9]{1,2}[LCR]?)*)',
            r'(?:VISUAL|ILS|RNAV)\s+(?:APCH|APPROACH)\s+(?:RWY?S?\s+)?([0-9]{1,2}[LCR]?)',
            
            # Departure patterns
            r'(?:DEP|DEPARTURE|DEPARTING|DEPS)\s+(?:RWY?S?\s+)?([0-9]{1,2}[LCR]?(?:\s+AND\s+[0-9]{1,2}[LCR]?)*)',
            r'(?:TAKEOFF|TKOF)\s+(?:RWY?S?\s+)?([0-9]{1,2}[LCR]?)',
            
            # Combined patterns
            r'RWY?S?\s+([0-9]{1,2}[LCR]?(?:\s+AND\s+[0-9]{1,2}[LCR]?)*)\s+IN\s+USE',
        ]
```

## Phase 3: Model Selection & Training

### Approach 1: Rule-Based System (Start Here)
**Pros**: Interpretable, no training needed, quick to deploy
**Cons**: Requires manual pattern updates

```python
class RuleBasedExtractor:
    def extract_runways(self, atis_text):
        # Normalize text
        text = atis_text.upper()
        
        # Extract using regex patterns
        arrivals = self.extract_arrivals(text)
        departures = self.extract_departures(text)
        
        # Determine flow direction
        flow = self.determine_flow(arrivals, departures)
        
        return RunwayConfiguration(...)
```

### Approach 2: NLP with spaCy (Medium-term)
**Pros**: Handles variations better, can learn context
**Cons**: Requires labeled training data

```python
import spacy
from spacy.tokens import DocBin

# Custom NER for runway entities
nlp = spacy.blank("en")
ner = nlp.add_pipe("ner")

# Add custom labels
ner.add_label("ARRIVAL_RUNWAY")
ner.add_label("DEPARTURE_RUNWAY")
ner.add_label("RUNWAY_NUMBER")
```

### Approach 3: Transformer-Based (Long-term)
**Pros**: Best accuracy, handles complex patterns
**Cons**: Requires significant training data, compute resources

```python
from transformers import AutoTokenizer, AutoModelForTokenClassification

# Fine-tune BERT for runway extraction
model = AutoModelForTokenClassification.from_pretrained(
    "bert-base-uncased",
    num_labels=5  # O, B-ARR, I-ARR, B-DEP, I-DEP
)
```

## Phase 4: Training Pipeline

### Data Labeling Strategy
1. **Semi-Automated Labeling**:
   - Use rule-based system to pre-label
   - Manual review and correction
   - Create ground truth dataset

2. **Active Learning**:
   - Start with high-confidence samples
   - Manually label edge cases
   - Retrain iteratively

### Training Schedule
```python
# Weekly retraining pipeline
def weekly_training_pipeline():
    # 1. Collect week's data
    data = fetch_weekly_atis_data()
    
    # 2. Apply current model
    predictions = model.predict(data)
    
    # 3. Identify low-confidence samples
    uncertain = get_low_confidence_samples(predictions)
    
    # 4. Manual review
    labeled = manual_review(uncertain)
    
    # 5. Retrain model
    model.retrain(labeled)
    
    # 6. Evaluate performance
    metrics = evaluate_model()
    
    return metrics
```

## Phase 5: API Development

### FastAPI Implementation
```python
from fastapi import FastAPI, HTTPException
from pydantic import BaseModel
from typing import List, Optional
import uvicorn

app = FastAPI(title="Runway Direction API")

class RunwayResponse(BaseModel):
    airport: str
    timestamp: str
    arriving_runways: List[str]
    departing_runways: List[str]
    traffic_flow: str
    confidence: float

@app.get("/api/runway/{airport_code}")
async def get_runway_status(airport_code: str):
    """Get current runway configuration for an airport"""
    
    # Fetch latest ATIS
    atis_data = fetch_latest_atis(airport_code)
    
    if not atis_data:
        raise HTTPException(404, f"No data for {airport_code}")
    
    # Process through model
    runway_config = model.extract_runways(atis_data)
    
    return RunwayResponse(**runway_config)

@app.get("/api/runways/all")
async def get_all_runways():
    """Get runway configurations for all monitored airports"""
    return process_all_airports()

@app.get("/api/runway/{airport_code}/history")
async def get_runway_history(
    airport_code: str,
    hours: int = 24
):
    """Get runway configuration changes over time"""
    return fetch_runway_history(airport_code, hours)
```

### Database Schema
```sql
-- ATIS snapshots
CREATE TABLE atis_data (
    id SERIAL PRIMARY KEY,
    airport_code VARCHAR(4),
    collected_at TIMESTAMP,
    information_letter CHAR(1),
    datis_text TEXT,
    content_hash VARCHAR(32),
    created_at TIMESTAMP DEFAULT NOW()
);

-- Extracted runway configurations
CREATE TABLE runway_configs (
    id SERIAL PRIMARY KEY,
    airport_code VARCHAR(4),
    atis_id INTEGER REFERENCES atis_data(id),
    arriving_runways JSON,
    departing_runways JSON,
    traffic_flow VARCHAR(20),
    confidence_score FLOAT,
    created_at TIMESTAMP DEFAULT NOW()
);

-- Create indexes
CREATE INDEX idx_airport_time ON atis_data(airport_code, collected_at);
CREATE INDEX idx_runway_airport ON runway_configs(airport_code, created_at);
```

## Phase 6: Deployment Architecture

```yaml
# docker-compose.yml
version: '3.8'
services:
  collector:
    build: ./collector
    environment:
      - CRON_SCHEDULE=*/5 * * * *
    depends_on:
      - postgres
  
  api:
    build: ./api
    ports:
      - "8000:8000"
    depends_on:
      - postgres
      - redis
  
  postgres:
    image: postgres:14
    volumes:
      - postgres_data:/var/lib/postgresql/data
  
  redis:
    image: redis:7
    # For caching recent queries
```

## Implementation Timeline

### Week 1-2: Data Collection
- Set up data collection pipeline
- Database schema implementation
- Begin collecting ATIS data

### Week 3-4: Rule-Based Parser
- Implement regex patterns
- Test on collected data
- Deploy basic API

### Week 5-8: Model Development
- Label training data
- Train NLP model
- A/B test against rules

### Week 9-12: Production Deployment
- API optimization
- Monitoring setup
- Documentation

## Success Metrics

1. **Extraction Accuracy**: >90% correct runway identification
2. **API Uptime**: 99.9% availability
3. **Response Time**: <100ms for single airport
4. **Data Freshness**: <5 minutes from ATIS update

## Special Considerations

### Airport-Specific Patterns
Some airports have unique configurations:
- **SFO**: Often uses crossing runways (28s and 1s)
- **LAX**: Complex quad-parallel operations
- **DFW**: Multiple simultaneous configurations

### Edge Cases to Handle
- Runway changes mid-ATIS
- Closed runways
- Special operations (e.g., "opposite direction ops")
- Emergencies affecting normal flow

## Next Steps

1. Start data collection immediately
2. Implement rule-based parser for initial validation
3. Begin labeling data for ML approach
4. Deploy MVP API with basic functionality
5. Iterate based on accuracy metrics

This system will fill a critical gap in aviation APIs by providing real-time runway direction information that's currently unavailable elsewhere.
