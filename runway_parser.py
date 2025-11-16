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
            re.compile(r'(?:EXPECT\s+)?(?:ILS|VISUAL|RNAV|VOR|GPS|LOC)\s+(?:OR\s+)?(?:ILS|VISUAL|RNAV|VOR|GPS|LOC)?\s*(?:APCH|APPROACH|APCHS|APPROACHES)\s+(?:RWYS?|RY)\s+([0-9]{1,2}[LCR]?)(?:(?:\s*,\s*|\s+(?:AND|OR)\s+)(?:RWYS?|RY)\s+([0-9]{1,2}[LCR]?))*', re.IGNORECASE),
            re.compile(r'(?:APCH|APPROACH|APCHS|APPROACHES)\s+(?:IN\s+USE\s+)?(?:RWYS?|RY)\s+([0-9]{1,2}[LCR]?)(?:(?:\s*,\s*|\s+(?:AND|OR)\s+)(?:RWYS?|RY)\s+([0-9]{1,2}[LCR]?))*', re.IGNORECASE),
            # LNDG/LANDING/LDG + RWYS: "LNDG RWYS 35L AND RIGHT" - captures landing runways
            re.compile(r'(?:LNDG|LANDING|LDG|LAND)\s+(?:AND\s+DEPARTING\s+)?(?:RWYS?|RY)\s+([0-9]{1,2}[LCR]?)(?:(?:\s*,\s*|\s+(?:AND|OR)\s+)(?:(?:RWYS?|RY)\s+)?([0-9]{1,2}[LCR]?))*', re.IGNORECASE),
            re.compile(r'(?:RWYS?|RY)\s+([0-9]{1,2}[LCR]?)(?:(?:\s*,\s*|\s+(?:AND|OR)\s+)(?:RWYS?|RY)\s+([0-9]{1,2}[LCR]?))*\s+(?:FOR\s+)?(?:APCH|APPROACH|LANDING|ARRIVAL)', re.IGNORECASE),
            # Shortened RNAV approach: "RNAV 27" or "RNAV Y 27" or "RNAV Z 27"
            re.compile(r'RNAV\s+(?:[YZ]\s+)?([0-9]{1,2}[LCR]?)(?:(?:\s*,\s*|\s+(?:AND|OR)\s+)(?:RNAV\s+)?(?:[YZ]\s+)?([0-9]{1,2}[LCR]?))*', re.IGNORECASE),
            # Named visual approaches: "FMS BRIDGE RY 28R AND TIPP TOE RY 28L APP IN USE"
            # Matches: [approach name] RY [runway] [AND [approach name] RY [runway]]* APP IN USE
            re.compile(r'(?:[A-Z]+(?:\s+[A-Z]+)*\s+)?RY\s+([0-9]{1,2}[LCR]?)(?:\s+AND\s+(?:[A-Z]+(?:\s+[A-Z]+)*\s+)?RY\s+([0-9]{1,2}[LCR]?))*\s+APP\s+IN\s+USE', re.IGNORECASE),
        ]

        self.departure_patterns = [
            # DEPG/DEP with RWYS - allow comma-separated without repeating RWYS: "DEPG RWYS 1L, 1R"
            re.compile(r'(?:DEPG|DEP|DEPARTURE|DEPARTING|DEPS|DEPARTURES)\s+(?:RWYS?|RY)\s+([0-9]{1,2}[LCR]?)(?:(?:\s*,\s*|\s+(?:AND|OR)\s+)(?:(?:RWYS?|RY)\s+)?([0-9]{1,2}[LCR]?))*', re.IGNORECASE),
            re.compile(r'(?:TAKEOFF|TKOF|TAKE\s+OFF)\s+(?:RWYS?|RY)\s+([0-9]{1,2}[LCR]?)(?:(?:\s*,\s*|\s+(?:AND|OR)\s+)(?:(?:RWYS?|RY)\s+)?([0-9]{1,2}[LCR]?))*', re.IGNORECASE),
            re.compile(r'(?:RWYS?|RY)\s+([0-9]{1,2}[LCR]?)(?:(?:\s*,\s*|\s+(?:AND|OR)\s+)([0-9]{1,2}[LCR]?))*\s+(?:FOR\s+)?(?:DEPG|DEP|DEPARTURE|TAKEOFF)', re.IGNORECASE),
            # Shortened departure: "DEP 33L" or "DEPG 16R" (without RWY keyword)
            re.compile(r'(?:DEPG|DEP)\s+([0-9]{1,2}[LCR]?)(?:(?:\s*,\s*|\s+(?:AND|OR)\s+)(?:DEPG|DEP\s+)?([0-9]{1,2}[LCR]?))*', re.IGNORECASE),
        ]
        
        self.combined_patterns = [
            re.compile(r'(?:RWYS?|RY)\s+(?:IN\s+USE\s+)?([0-9]{1,2}[LCR]?)(?:(?:\s*,\s*|\s+(?:AND|OR)\s+)(?:RWYS?|RY)\s+([0-9]{1,2}[LCR]?))*', re.IGNORECASE),
            re.compile(r'(?:SIMUL|SIMULTANEOUS)\s+(?:APCHS|APPROACHES)\s+(?:IN\s+USE\s*,?\s*)?(?:TO\s+)?(?:RWYS?|RY)\s+([0-9]{1,2}[LCR]?)(?:(?:\s*,\s*|\s+(?:AND|OR)\s+)(?:RWYS?|RY)\s+([0-9]{1,2}[LCR]?))*', re.IGNORECASE),
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

        # Airport-specific handling: KDEN publishes separate ARR INFO and DEP INFO
        text_upper = cleaned_text.upper()
        is_kden_dep = airport_code == 'KDEN' and 'DEP INFO' in text_upper
        is_kden_arr = airport_code == 'KDEN' and 'ARR INFO' in text_upper

        # For KDEN ARR INFO: if approach patterns didn't find anything, try combined patterns
        if is_kden_arr and not arriving:
            combined = self.extract_combined_runways(cleaned_text)
            if combined:
                arriving = combined

        # For KDEN DEP INFO: if departure patterns didn't find anything, don't use combined
        # For other airports: if neither arrival nor departure found, try combined patterns
        if not arriving and not departing and not (is_kden_dep or is_kden_arr):
            combined = self.extract_combined_runways(cleaned_text)
            arriving = combined
            departing = combined

        # Determine traffic flow
        flow = self.determine_traffic_flow(arriving, departing)
        
        # Get configuration name if available
        config_name = self.get_configuration_name(airport_code, arriving, departing)
        
        # Calculate confidence score
        confidence = self.calculate_confidence(arriving, departing, cleaned_text)

        # Split ATIS confidence boost: If this is a split DEP/ARR INFO entry and both
        # arrivals and departures are populated, set confidence to 100% since the data
        # was filled in from a matching pair and there's nothing for a human to review
        is_split_atis = ('DEP INFO' in text_upper or 'ARR INFO' in text_upper)
        if is_split_atis and arriving and departing:
            confidence = 1.0

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

        # Convert digit-by-digit runway callouts to standard format
        # "RUNWAY 3 4 LEFT" -> "RWY 34L", "RWY 1 6 RIGHT" -> "RWY 16R"
        def consolidate_runway(match):
            prefix = match.group(1) or 'RWY'
            digit1 = match.group(2)
            digit2 = match.group(3)
            suffix = match.group(4) if match.group(4) else ''
            # Convert suffix words to letters
            suffix_map = {'LEFT': 'L', 'RIGHT': 'R', 'CENTER': 'C'}
            suffix_letter = suffix_map.get(suffix.upper(), suffix)
            return f"{prefix} {digit1}{digit2}{suffix_letter}"

        # Pattern: RUNWAY 3 4 LEFT, RWY 1 6 RIGHT, etc.
        text = re.sub(
            r'(?:RUNWAY|RUNWAYS|RWY?S?|RY)\s+([0-9])\s+([0-9])\s*(LEFT|RIGHT|CENTER|L|R|C)?',
            consolidate_runway,
            text,
            flags=re.IGNORECASE
        )

        # Filter out NOTAMs with closures (including digit-by-digit format)
        # "RWY 1 6 LEFT 3 4 RIGHT CLOSED" or "RWY 16L CLOSED"
        closure_patterns = [
            r'RWY?\s+[0-9]{1,2}[LCR]?\s+(?:CLSD|CLOSED)',  # Standard: RWY 16L CLOSED
            r'RWY?\s+[0-9]\s+[0-9]\s+(?:LEFT|RIGHT|CENTER|L|R|C)?\s+(?:CLSD|CLOSED)',  # Digit-by-digit
        ]
        for pattern in closure_patterns:
            text = re.sub(pattern, '', text, flags=re.IGNORECASE)

        # Filter out other NOTAMs and equipment status - these are NOT runway operations
        notam_patterns = [
            r'RWY?\s+[0-9]{1,2}[LCR]?\s+(?:INNER|OUTER|MIDDLE)\s+MARKER\s+(?:OTS|OUT\s+OF\s+SERVICE|INOP|U\/S)',
            r'RWY?\s+[0-9]{1,2}[LCR]?\s+(?:REIL|ALS|PAPI|VASI|ILS|LOC|GS|GLIDESLOPE|ALSF|MALSR|MALS|SSALR|SSALS)\s+(?:OTS|OUT\s+OF\s+SERVICE|INOP|U\/S)',
            r'RWY?\s+[0-9]{1,2}[LCR]?\s+(?:OTS|OUT\s+OF\s+SERVICE|INOP|U\/S)',
        ]
        for pattern in notam_patterns:
            text = re.sub(pattern, '', text, flags=re.IGNORECASE)

        # Expand "AND RIGHT" / "AND LEFT" patterns before general processing
        # "35L AND RIGHT" -> "35L AND 35R"
        # "35R AND LEFT" -> "35R AND 35L"
        text = self.expand_and_right_left(text)

        # Standardize runway notation
        text = re.sub(r'RUNWAY', 'RWY', text, flags=re.IGNORECASE)
        text = re.sub(r'RUNWAYS', 'RWYS', text, flags=re.IGNORECASE)

        # Add space between RWY/RY and runway number (e.g., "RWY17L" -> "RWY 17L")
        text = re.sub(r'(RWY?S?|RY)([0-9]{1,2}[LCR]?)', r'\1 \2', text, flags=re.IGNORECASE)

        # Remove periods that might interfere
        text = text.replace('.', ' ')

        return text

    def expand_and_right_left(self, text: str) -> str:
        """Expand 'AND RIGHT' / 'AND LEFT' patterns to explicit runway numbers
        Examples:
          'RWY 35L AND RIGHT' -> 'RWY 35L AND RWY 35R'
          'RWY 35R AND LEFT' -> 'RWY 35R AND RWY 35L'
          'RWY 16C AND LEFT' -> 'RWY 16C AND RWY 16L'
        """
        # Pattern: (optional RWY/RWYS/RY) runway number followed by "AND RIGHT/LEFT"
        def expand_match(match):
            rwy_keyword = match.group(1) or ''  # "RWY", "RWYS", "RY", or empty
            runway = match.group(2)  # Full runway (e.g., "35L")
            direction = match.group(3).upper()  # "RIGHT" or "LEFT"

            # Extract base number and current suffix
            rwy_match = re.match(r'([0-9]{1,2})([LCR])?', runway)
            if not rwy_match:
                return match.group(0)  # Return unchanged if can't parse

            base_num = rwy_match.group(1)
            current_suffix = rwy_match.group(2) or ''

            # Determine new suffix based on direction
            if direction == 'RIGHT':
                new_suffix = 'R'
            elif direction == 'LEFT':
                new_suffix = 'L'
            else:
                return match.group(0)  # Return unchanged

            # Build new runway designation
            new_runway = f"{base_num}{new_suffix}"

            # Return expanded form with RWY keyword if it was present
            if rwy_keyword:
                return f"{rwy_keyword} {runway} AND {rwy_keyword} {new_runway}"
            else:
                return f"{runway} AND {new_runway}"

        # Match pattern: "RWY 35L AND RIGHT" or just "35L AND RIGHT"
        pattern = r'(?:(RWY?S?|RY)\s+)?([0-9]{1,2}[LCR]?)\s+AND\s+(RIGHT|LEFT)\b'
        text = re.sub(pattern, expand_match, text, flags=re.IGNORECASE)

        return text
    
    def extract_arriving_runways(self, text: str) -> Set[str]:
        """Extract arrival runway information"""
        runways = set()

        for pattern in self.approach_patterns:
            matches = pattern.finditer(text)
            for match in matches:
                # Extract all runway numbers from the matched text
                matched_text = match.group(0)
                runway_matches = re.findall(r'\b([0-9]{1,2}[LCR]?)\b', matched_text)
                for rwy in runway_matches:
                    if re.match(r'^[0-9]{1,2}[LCR]?$', rwy):
                        # Validate runway number range (01-36)
                        num_part = re.match(r'^([0-9]{1,2})', rwy)
                        if num_part and 1 <= int(num_part.group(1)) <= 36:
                            runways.add(self.normalize_runway(rwy))

        return runways

    def extract_departing_runways(self, text: str) -> Set[str]:
        """Extract departure runway information"""
        runways = set()

        for pattern in self.departure_patterns:
            matches = pattern.finditer(text)
            for match in matches:
                # Extract all runway numbers from the matched text
                matched_text = match.group(0)
                runway_matches = re.findall(r'\b([0-9]{1,2}[LCR]?)\b', matched_text)
                for rwy in runway_matches:
                    if re.match(r'^[0-9]{1,2}[LCR]?$', rwy):
                        # Validate runway number range (01-36)
                        num_part = re.match(r'^([0-9]{1,2})', rwy)
                        if num_part and 1 <= int(num_part.group(1)) <= 36:
                            runways.add(self.normalize_runway(rwy))

        return runways
    
    def extract_combined_runways(self, text: str) -> Set[str]:
        """Extract runways when arrival/departure not specified"""
        runways = set()

        for pattern in self.combined_patterns:
            matches = pattern.finditer(text)
            for match in matches:
                # Extract all runway numbers from the matched text
                matched_text = match.group(0)
                runway_matches = re.findall(r'\b([0-9]{1,2}[LCR]?)\b', matched_text)
                for rwy in runway_matches:
                    if re.match(r'^[0-9]{1,2}[LCR]?$', rwy):
                        # Validate runway number range (01-36)
                        num_part = re.match(r'^([0-9]{1,2})', rwy)
                        if num_part and 1 <= int(num_part.group(1)) <= 36:
                            runways.add(self.normalize_runway(rwy))

        return runways
    
    def normalize_runway(self, runway: str) -> str:
        """Normalize runway format (preserve original format from ATIS)"""
        # Extract number and suffix - preserve single vs double digit as it appears in ATIS
        match = re.match(r'^([0-9]{1,2})([LCR])?$', runway)
        if match:
            number = match.group(1)  # Don't pad with zeros - preserve original format
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

        # Airport-specific patterns: Some airports publish separate arrival/departure ATIS
        text_upper = text.upper()

        # KDEN publishes separate "DEP INFO" and "ARR INFO" - empty arrivals on DEP INFO is valid
        if 'KDEN' in text_upper or 'DEN DEP INFO' in text_upper or 'DEN ARR INFO' in text_upper:
            if 'DEP INFO' in text_upper and not arriving and departing:
                # Departure-only ATIS is expected and valid
                return 1.0
            elif 'ARR INFO' in text_upper and arriving and not departing:
                # Arrival-only ATIS is expected and valid
                return 1.0

        # Base score if we found any runways
        if arriving or departing:
            score = 0.5

        # Higher score if we found both arrival and departure
        if arriving and departing:
            score += 0.3

        # Check for explicit keywords (including abbreviated forms)
        if any(word in text_upper for word in ['APPROACH', 'DEPARTURE', 'DEPG', 'LANDING', 'LDG', 'LAND', 'TAKEOFF']):
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