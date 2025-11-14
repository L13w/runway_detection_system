# Runway Direction Detection System

A real-time API system that parses D-ATIS data to determine active runway configurations at US airports, filling a critical gap in aviation data APIs.

## 🎯 Problem Statement

While weather data is readily available via API, runway direction information (which runways are active for arrivals/departures) is not available through any existing API. This system solves that by:
- Collecting D-ATIS data every 5 minutes from all major US airports
- Parsing runway information using pattern matching
- Providing a simple REST API for runway configuration queries

## 🚀 Quick Start

### Using Docker Compose (Recommended)

```bash
# Clone the repository
git clone <your-repo-url>
cd runway-detection

# Start all services
docker-compose up -d

# View logs
docker-compose logs -f

# Access the API
curl http://localhost:8000/api/runway/KSEA
```

### Manual Setup

1. **Install PostgreSQL**
```bash
# Ubuntu/Debian
sudo apt-get install postgresql postgresql-contrib

# Create database
sudo -u postgres psql
CREATE DATABASE runway_detection;
\q
```

2. **Setup Python Environment**
```bash
python3 -m venv venv
source venv/bin/activate  # On Windows: venv\Scripts\activate
pip install -r requirements.txt
```

3. **Initialize Database**
```bash
psql -U postgres -d runway_detection -f database_schema.sql
```

4. **Start Services**
```bash
# Terminal 1: Start collector (runs every 5 minutes)
python atis_collector.py

# Terminal 2: Start API server
uvicorn runway_api:app --reload
```

## 📡 API Endpoints

### Get Current Runway Configuration
```bash
GET /api/runway/{airport_code}

# Example
curl http://localhost:8000/api/runway/KSEA

# Response
{
  "airport": "KSEA",
  "timestamp": "2024-11-13T18:30:00Z",
  "information_letter": "C",
  "arriving_runways": ["16L", "16C", "16R"],
  "departing_runways": ["16L", "16C", "16R"],
  "traffic_flow": "SOUTH",
  "configuration_name": "South Flow",
  "confidence": 0.9,
  "last_updated": "2024-11-13T18:33:00Z"
}
```

### Get All Airports
```bash
GET /api/runways/all

# Returns runway configs for all monitored airports
```

### Get Runway History
```bash
GET /api/runway/{airport_code}/history?hours=24

# Shows configuration changes over time
```

### List Monitored Airports
```bash
GET /api/airports

# Returns all airports with current status
```

### System Status
```bash
GET /api/status

# System health and statistics
```

## 📊 Data Collection Schedule

The collector runs every 5 minutes, aligned with typical ATIS update patterns (:03 and :33 past the hour). This ensures:
- Captures regular updates
- Detects emergency configuration changes
- Minimizes API calls while maintaining data freshness

## 🧠 How It Works

### 1. Data Collection
- Fetches D-ATIS JSON from `https://datis.clowd.io/api/all`
- Stores raw ATIS text with timestamps
- Detects changes using content hashing

### 2. Runway Parsing
The parser uses regex patterns to identify:
- **Arrival runways**: "APPROACH", "LANDING", "APCH RWY"
- **Departure runways**: "DEPARTURE", "TAKEOFF", "DEP RWY"
- **Combined operations**: "RWYS IN USE"

### 3. Traffic Flow Detection
Calculates average runway heading to determine flow:
- North (340°-020°): Runways 34, 35, 36, 01, 02
- South (160°-200°): Runways 16, 17, 18, 19, 20
- East (070°-110°): Runways 07, 08, 09, 10, 11
- West (250°-290°): Runways 25, 26, 27, 28, 29

## 📈 Training & Improvement

### Data Requirements
- **Minimum**: 2-3 weeks (4,000+ samples)
- **Robust**: 1-2 months (17,000+ samples)
- **Seasonal**: 3+ months (captures wind patterns)

### Model Evolution Path
1. **Current**: Rule-based regex patterns (85-90% accuracy)
2. **Next**: NLP with spaCy (90-95% accuracy)
3. **Future**: Fine-tuned BERT (95%+ accuracy)

### Accuracy Monitoring
```sql
-- Check parser accuracy
SELECT 
    airport_code,
    AVG(confidence_score) as avg_confidence,
    COUNT(*) as samples
FROM runway_configs
WHERE created_at > NOW() - INTERVAL '7 days'
GROUP BY airport_code
ORDER BY avg_confidence DESC;
```

## 🏢 Airport-Specific Notes

### Seattle (KSEA)
- **South Flow**: 16L, 16C, 16R (most common)
- **North Flow**: 34L, 34C, 34R (strong south winds)

### San Francisco (KSFO)
- **West Flow**: 28L, 28R (typical)
- **Southeast Flow**: 19L, 19R (rare)
- **Special**: Often uses crossing runways (28s and 1s)

### Los Angeles (KLAX)
- **West Flow**: 24L, 24R, 25L, 25R
- **East Flow**: 06L, 06R, 07L, 07R
- **Complex**: Quad-parallel operations

## 🔧 Configuration

### Environment Variables
```bash
DB_HOST=localhost
DB_NAME=runway_detection
DB_USER=postgres
DB_PASSWORD=postgres
DB_PORT=5432
```

### Database Maintenance
```sql
-- Clean old data (>90 days)
DELETE FROM atis_data 
WHERE collected_at < NOW() - INTERVAL '90 days';

-- Analyze runway usage patterns
SELECT * FROM get_runway_usage_stats('KSEA', 30);
```

## 📊 Monitoring

### Key Metrics
- **Data Freshness**: <5 minutes from ATIS update
- **Parser Confidence**: Target >0.8 average
- **API Response Time**: <100ms p95
- **Collection Success Rate**: >99%

### Health Checks
```bash
# API health
curl http://localhost:8000/health

# System status
curl http://localhost:8000/api/status
```

## 🚧 Known Limitations

1. **Pattern Variations**: Some airports use non-standard ATIS phrasing
2. **Special Operations**: May miss "opposite direction ops" or emergency configs
3. **Closed Runways**: Currently doesn't track runway closures
4. **International**: Only supports US airports (ICAO codes starting with K)

## 🔮 Future Enhancements

- [ ] Machine learning model for improved accuracy
- [ ] WebSocket support for real-time updates
- [ ] Historical trend analysis
- [ ] Wind-based runway prediction
- [ ] Integration with ATC audio feeds
- [ ] Mobile app notifications
- [ ] GraphQL API option

## 📝 Contributing

1. Collect ATIS samples with unusual patterns
2. Add regex patterns for new phrases
3. Test with diverse airport configurations
4. Submit labeled training data

## 📄 License

MIT License - See LICENSE file

## 🙏 Acknowledgments

- D-ATIS data provided by clowd.io
- Inspired by the lack of runway direction APIs
- Aviation community for ATIS format documentation

## 📞 Support

For issues or questions:
- Open an issue on GitHub
- API documentation: http://localhost:8000/docs
- System status: http://localhost:8000/api/status

---

**Note**: This system is for informational purposes only. Always verify runway information through official aviation sources for operational use.
