#!/usr/bin/env python3
"""
Runway Parser
Extracts runway configuration from ATIS text using pattern matching
"""

import re
import logging
from typing import List, Dict, Optional, Tuple, Set
from dataclasses import dataclass, asdict
from datetime import datetime
from enum import Enum

logger = logging.getLogger(__name__)

class TrafficFlow(Enum):
    NORTH = "NORTH"
    SOUTH = "SOUTH"
    EAST = "EAST"
    WEST = "WEST"
    NORTHEAST = "NORTHEAST"
    NORTHWEST = "NORTHWEST"
    SOUTHEAST = "SOUTHEAST"
    SOUTHWEST = "SOUTHWEST"
    MIXED = "MIXED"
    UNKNOWN = "UNKNOWN"

@dataclass
class RunwayConfiguration:
    """Standardized runway configuration"""
    airport_code: str
    timestamp: datetime
    information_letter: Optional[str]
    arriving_runways: List[str]
    departing_runways: List[str]
    traffic_flow: str
    configuration_name: Optional[str]
    raw_text: str
    confidence_score: float
    
    def to_dict(self):
        """Convert to dictionary for JSON serialization"""
        d = asdict(self)
        d['timestamp'] = self.timestamp.isoformat()
        return d

class RunwayParser:
    def __init__(self):
        # Compile regex patterns for efficiency
        self.approach_patterns = [
            re.compile(r'(?:EXPECT\s+)?(?:ILS|VISUAL|RNAV|VOR|GPS|LOC)\s+(?:OR\s+)?(?:ILS|VISUAL|RNAV|VOR|GPS|LOC)?\s*(?:APCH|APPROACH|APCHS|APPROACHES)\s+(?:RWY?S?\s+)?([0-9]{1,2}[LCR]?)(?:\s+(?:AND|OR)\s+(?:RWY?S?\s+)?([0-9]{1,2}[LCR]?))*', re.IGNORECASE),
            re.compile(r'(?:APCH|APPROACH|APCHS|APPROACHES)\s+(?:IN\s+USE\s+)?(?:RWY?S?\s+)?([0-9]{1,2}[LCR]?)(?:\s+(?:AND|OR)\s+([0-9]{1,2}[LCR]?))*', re.IGNORECASE),
            re.compile(r'(?:LANDING|LDG|LAND)\s+(?:AND\s+DEPARTING\s+)?(?:RWY?S?\s+)?([0-9]{1,2}[LCR]?)(?:\s+(?:AND|OR)\s+([0-9]{1,2}[LCR]?))*', re.IGNORECASE),
            re.compile(r'RWY?S?\s+([0-9]{1,2}[LCR]?)(?:\s+(?:AND|OR)\s+([0-9]{1,2}[LCR]?))*\s+(?:FOR\s+)?(?:APCH|APPROACH|LANDING|ARRIVAL)', re.IGNORECASE),
        ]
        
        self.departure_patterns = [
            re.compile(r'(?:DEP|DEPARTURE|DEPARTING|DEPS|DEPARTURES)\s+(?:RWY?S?\s+)?([0-9]{1,2}[LCR]?)(?:\s+(?:AND|OR)\s+([0-9]{1,2}[LCR]?))*', re.IGNORECASE),
            re.compile(r'(?:TAKEOFF|TKOF|TAKE\s+OFF)\s+(?:RWY?S?\s+)?([0-9]{1,2}[LCR]?)(?:\s+(?:AND|OR)\s+([0-9]{1,2}[LCR]?))*', re.IGNORECASE),
            re.compile(r'RWY?S?\s+([0-9]{1,2}[LCR]?)(?:\s+(?:AND|OR)\s+([0-9]{1,2}[LCR]?))*\s+(?:FOR\s+)?(?:DEP|DEPARTURE|TAKEOFF)', re.IGNORECASE),
        ]
        
        self.combined_patterns = [
            re.compile(r'RWY?S?\s+(?:IN\s+USE\s+)?([0-9]{1,2}[LCR]?)(?:\s+(?:AND|OR)\s+([0-9]{1,2}[LCR]?))*', re.IGNORECASE),
            re.compile(r'(?:SIMUL|SIMULTANEOUS)\s+(?:APCHS|APPROACHES)\s+(?:IN\s+USE\s+)?(?:TO\s+)?(?:RWY?S?\s+)?([0-9]{1,2}[LCR]?)(?:\s+(?:AND|OR)\s+([0-9]{1,2}[LCR]?))*', re.IGNORECASE),
        ]
        
        # Airport-specific configuration names
        self.airport_configs = {
            'KSEA': {
                'south': ['16L', '16C', '16R'],
                'north': ['34L', '34C', '34R']
            },
            'KSFO': {
                'west': ['28L', '28R'],
                'east': ['10L', '10R'],
                'southeast': ['19L', '19R'],
                'northwest': ['01L', '01R']
            },
            'KLAX': {
                'west': ['24L', '24R', '25L', '25R'],
                'east': ['06L', '06R', '07L', '07R']
            }
        }
    
    def parse(self, airport_code: str, atis_text: str, info_letter: Optional[str] = None) -> RunwayConfiguration:
        """Main parsing method"""
        timestamp = datetime.utcnow()
        
        # Clean and prepare text
        cleaned_text = self.clean_text(atis_text)
        
        # Extract runways
        arriving = self.extract_arriving_runways(cleaned_text)
        departing = self.extract_departing_runways(cleaned_text)
        
        # If we couldn't find specific arrival/departure, try combined patterns
        if not arriving and not departing:
            combined = self.extract_combined_runways(cleaned_text)
            arriving = combined
            departing = combined
        
        # Determine traffic flow
        flow = self.determine_traffic_flow(arriving, departing)
        
        # Get configuration name if available
        config_name = self.get_configuration_name(airport_code, arriving, departing)
        
        # Calculate confidence score
        confidence = self.calculate_confidence(arriving, departing, cleaned_text)
        
        return RunwayConfiguration(
            airport_code=airport_code,
            timestamp=timestamp,
            information_letter=info_letter,
            arriving_runways=sorted(list(arriving)),
            departing_runways=sorted(list(departing)),
            traffic_flow=flow.value,
            configuration_name=config_name,
            raw_text=atis_text,
            confidence_score=confidence
        )
    
    def clean_text(self, text: str) -> str:
        """Clean ATIS text for better pattern matching"""
        # Remove extra whitespace
        text = ' '.join(text.split())
        
        # Standardize runway notation
        text = re.sub(r'RUNWAY', 'RWY', text, flags=re.IGNORECASE)
        text = re.sub(r'RUNWAYS', 'RWYS', text, flags=re.IGNORECASE)
        
        # Remove periods that might interfere
        text = text.replace('.', ' ')
        
        return text
    
    def extract_arriving_runways(self, text: str) -> Set[str]:
        """Extract arrival runway information"""
        runways = set()
        
        for pattern in self.approach_patterns:
            matches = pattern.finditer(text)
            for match in matches:
                # Extract all groups that matched
                for group in match.groups():
                    if group and re.match(r'^[0-9]{1,2}[LCR]?$', group):
                        runways.add(self.normalize_runway(group))
        
        return runways
    
    def extract_departing_runways(self, text: str) -> Set[str]:
        """Extract departure runway information"""
        runways = set()
        
        for pattern in self.departure_patterns:
            matches = pattern.finditer(text)
            for match in matches:
                for group in match.groups():
                    if group and re.match(r'^[0-9]{1,2}[LCR]?$', group):
                        runways.add(self.normalize_runway(group))
        
        return runways
    
    def extract_combined_runways(self, text: str) -> Set[str]:
        """Extract runways when arrival/departure not specified"""
        runways = set()
        
        for pattern in self.combined_patterns:
            matches = pattern.finditer(text)
            for match in matches:
                for group in match.groups():
                    if group and re.match(r'^[0-9]{1,2}[LCR]?$', group):
                        runways.add(self.normalize_runway(group))
        
        return runways
    
    def normalize_runway(self, runway: str) -> str:
        """Normalize runway format (ensure 2 digits)"""
        # Extract number and suffix
        match = re.match(r'^([0-9]{1,2})([LCR])?$', runway)
        if match:
            number = match.group(1).zfill(2)
            suffix = match.group(2) or ''
            return f"{number}{suffix}"
        return runway
    
    def determine_traffic_flow(self, arriving: Set[str], departing: Set[str]) -> TrafficFlow:
        """Determine overall traffic flow direction"""
        all_runways = arriving.union(departing)
        
        if not all_runways:
            return TrafficFlow.UNKNOWN
        
        # Get runway headings
        headings = []
        for runway in all_runways:
            try:
                heading = int(runway[:2]) * 10
                headings.append(heading)
            except ValueError:
                continue
        
        if not headings:
            return TrafficFlow.UNKNOWN
        
        # Calculate average heading
        avg_heading = sum(headings) / len(headings)
        
        # Determine flow direction based on heading
        if 337.5 <= avg_heading or avg_heading < 22.5:
            return TrafficFlow.NORTH
        elif 22.5 <= avg_heading < 67.5:
            return TrafficFlow.NORTHEAST
        elif 67.5 <= avg_heading < 112.5:
            return TrafficFlow.EAST
        elif 112.5 <= avg_heading < 157.5:
            return TrafficFlow.SOUTHEAST
        elif 157.5 <= avg_heading < 202.5:
            return TrafficFlow.SOUTH
        elif 202.5 <= avg_heading < 247.5:
            return TrafficFlow.SOUTHWEST
        elif 247.5 <= avg_heading < 292.5:
            return TrafficFlow.WEST
        elif 292.5 <= avg_heading < 337.5:
            return TrafficFlow.NORTHWEST
        
        return TrafficFlow.UNKNOWN
    
    def get_configuration_name(self, airport_code: str, arriving: Set[str], departing: Set[str]) -> Optional[str]:
        """Get airport-specific configuration name"""
        if airport_code not in self.airport_configs:
            return None
        
        configs = self.airport_configs[airport_code]
        all_runways = arriving.union(departing)
        
        for config_name, config_runways in configs.items():
            if any(rwy in config_runways for rwy in all_runways):
                return f"{config_name.capitalize()} Flow"
        
        return None
    
    def calculate_confidence(self, arriving: Set[str], departing: Set[str], text: str) -> float:
        """Calculate confidence score for extraction"""
        score = 0.0
        
        # Base score if we found any runways
        if arriving or departing:
            score = 0.5
        
        # Higher score if we found both arrival and departure
        if arriving and departing:
            score += 0.3
        
        # Check for explicit keywords
        if any(word in text.upper() for word in ['APPROACH', 'DEPARTURE', 'LANDING', 'TAKEOFF']):
            score += 0.1
        
        # Check for runway format validity
        valid_format = all(re.match(r'^[0-9]{2}[LCR]?$', rwy) for rwy in arriving.union(departing))
        if valid_format and (arriving or departing):
            score += 0.1
        
        return min(score, 1.0)

# Example usage
if __name__ == "__main__":
    parser = RunwayParser()
    
    # Test with sample ATIS text
    sample_atis = """
    SEA ATIS INFO C 0053Z. 11010KT 10SM FEW015 BKN250 11/07 A3012 
    RMK AO2 SLP202 T01110072. ILS APPROACHES IN USE. LANDING RUNWAY 16L 16C AND 16R. 
    DEPARTING RUNWAY 16L 16C AND 16R. NOTAMS: RUNWAY 16L CLSD BTN 0600 AND 1400Z DAILY.
    """
    
    result = parser.parse("KSEA", sample_atis, "C")
    print(f"Airport: {result.airport_code}")
    print(f"Arriving: {result.arriving_runways}")
    print(f"Departing: {result.departing_runways}")
    print(f"Traffic Flow: {result.traffic_flow}")
    print(f"Confidence: {result.confidence_score:.2f}")