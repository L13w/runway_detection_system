#!/usr/bin/env python3
"""
Test SFO pattern parsing: Named visual approaches (FMS BRIDGE, TIPP TOE)
"""

from runway_parser import RunwayParser

def test_sfo_patterns():
    parser = RunwayParser()

    # Test 1: SFO named visual approaches with DEPG
    test1 = "SFO ATIS INFO Z 0356Z. SIMUL CHARTED VISUAL FMS BRIDGE RY 28R AND TIPP TOE RY 28L APP IN USE. DEPG RWYS 1L, 1R"
    result1 = parser.parse("KSFO", test1, "Z")

    print("Test 1: SFO Named Visual Approaches")
    print(f"  Input: {test1}")
    print(f"  Arriving: {result1.arriving_runways}")
    print(f"  Departing: {result1.departing_runways}")
    print(f"  Traffic Flow: {result1.traffic_flow}")
    print(f"  Confidence: {result1.confidence_score}")
    print(f"  Expected: Arriving=['28L', '28R'], Departing=['1L', '1R'], Flow=MIXED")
    print()

    if set(result1.arriving_runways) == {'28L', '28R'} and set(result1.departing_runways) == {'1L', '1R'}:
        print("  ✅ PASS")
    else:
        print("  ❌ FAIL")
        print(f"  Missing arrivals: {set(['28L', '28R']) - set(result1.arriving_runways)}")
        print(f"  Missing departures: {set(['1L', '1R']) - set(result1.departing_runways)}")
    print()

    # Test 2: Simpler SFO pattern without named approaches
    test2 = "SIMUL APPS RWY 28L AND 28R. DEPG RWYS 1L, 1R"
    result2 = parser.parse("KSFO", test2, "A")

    print("Test 2: SFO Simultaneous Approaches (simpler)")
    print(f"  Input: {test2}")
    print(f"  Arriving: {result2.arriving_runways}")
    print(f"  Departing: {result2.departing_runways}")
    print(f"  Expected: Arriving=['28L', '28R'], Departing=['1L', '1R']")
    print()

    if set(result2.arriving_runways) == {'28L', '28R'} and set(result2.departing_runways) == {'1L', '1R'}:
        print("  ✅ PASS")
    else:
        print("  ❌ FAIL")
    print()

if __name__ == "__main__":
    print("Testing SFO pattern recognition...\n")
    test_sfo_patterns()
    print("\nDone!")
