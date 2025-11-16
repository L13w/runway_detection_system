#!/usr/bin/env python3
"""
Test Parser Fixes
Verify parser correctly handles:
1. NOTAM filtering ("RWY 17 RIGHT INNER MARKER OTS")
2. "AND RIGHT" expansion ("35L AND RIGHT" -> "35L", "35R")
3. Arrival/departure assignment (only arrivals from "LNDG RWYS")
"""

from runway_parser import RunwayParser

def test_smf_example():
    """Test the SMF ATIS example that was showing incorrect parsing"""
    parser = RunwayParser()

    atis_text = """SMF ATIS INFO F 0353Z. 33005KT 10SM BKN055 13/12 A3000 (THREE ZERO ZERO ZERO).
    SIMUL VISUAL APCHS IN USE, LNDG RWYS 35L AND RIGHT. NOTAMS... TWY A5 CLSD.
    RWY 17 RIGHT INNER MARKER OTS. METERING IN EFFECT FOR SFO, LAX, SEA, LAS.
    BIRD ACTIVITY VICINITY ARPT. HAZD WX INFO FOR SMF AREA SIGMET QUEBEC 7
    CALIFORNIA, NEVADA, OREGON AVBL FM FSS. CD IS ON 121.7. ...ADVS YOU HAVE INFO F."""

    result = parser.parse("KSMF", atis_text, "F")

    print("=" * 60)
    print("SMF ATIS Test")
    print("=" * 60)
    print(f"ATIS Text (excerpt): LNDG RWYS 35L AND RIGHT... RWY 17 RIGHT INNER MARKER OTS")
    print()
    print("Expected:")
    print("  Arrivals: ['35L', '35R']")
    print("  Departures: []")
    print()
    print("Actual:")
    print(f"  Arrivals: {result.arriving_runways}")
    print(f"  Departures: {result.departing_runways}")
    print(f"  Confidence: {result.confidence_score}")
    print()

    # Check results
    expected_arr = ['35L', '35R']
    expected_dep = []

    success = (
        sorted(result.arriving_runways) == sorted(expected_arr) and
        sorted(result.departing_runways) == sorted(expected_dep)
    )

    if success:
        print("✓ TEST PASSED")
    else:
        print("✗ TEST FAILED")
        print(f"  Expected arrivals: {expected_arr}, got: {result.arriving_runways}")
        print(f"  Expected departures: {expected_dep}, got: {result.departing_runways}")

    return success

def test_and_right_pattern():
    """Test 'AND RIGHT' pattern expansion"""
    parser = RunwayParser()

    test_cases = [
        ("LNDG RWYS 35L AND RIGHT", ["35L", "35R"], []),
        ("APCH RWY 16C AND LEFT", ["16C", "16L"], []),
        ("LANDING RUNWAY 28R AND LEFT", ["28L", "28R"], []),
    ]

    print("\n" + "=" * 60)
    print("AND RIGHT/LEFT Pattern Tests")
    print("=" * 60)

    all_passed = True
    for atis_text, expected_arr, expected_dep in test_cases:
        result = parser.parse("KTEST", atis_text, "A")

        passed = (
            sorted(result.arriving_runways) == sorted(expected_arr) and
            sorted(result.departing_runways) == sorted(expected_dep)
        )

        status = "✓" if passed else "✗"
        print(f"{status} '{atis_text}'")
        print(f"   Expected: arr={expected_arr}, dep={expected_dep}")
        print(f"   Got:      arr={result.arriving_runways}, dep={result.departing_runways}")

        if not passed:
            all_passed = False

    return all_passed

def test_notam_filtering():
    """Test NOTAM filtering (OTS, CLSD, etc.)"""
    parser = RunwayParser()

    test_cases = [
        ("RWY 17 RIGHT INNER MARKER OTS. LANDING RWY 35L", ["35L"], []),
        ("RWY 09 CLSD. APCH RWY 27", ["27"], []),
        ("RWY 16L ILS OTS. VISUAL APCH RWY 16R", ["16R"], []),
    ]

    print("\n" + "=" * 60)
    print("NOTAM Filtering Tests")
    print("=" * 60)

    all_passed = True
    for atis_text, expected_arr, expected_dep in test_cases:
        result = parser.parse("KTEST", atis_text, "A")

        # Check that the OTS/CLSD runway is NOT in the results
        passed = (
            sorted(result.arriving_runways) == sorted(expected_arr) and
            sorted(result.departing_runways) == sorted(expected_dep)
        )

        status = "✓" if passed else "✗"
        print(f"{status} '{atis_text}'")
        print(f"   Expected: arr={expected_arr}, dep={expected_dep}")
        print(f"   Got:      arr={result.arriving_runways}, dep={result.departing_runways}")

        if not passed:
            all_passed = False

    return all_passed

if __name__ == "__main__":
    print("Testing Parser Fixes\n")

    test1 = test_smf_example()
    test2 = test_and_right_pattern()
    test3 = test_notam_filtering()

    print("\n" + "=" * 60)
    print("OVERALL RESULTS")
    print("=" * 60)
    print(f"SMF Example Test: {'PASSED' if test1 else 'FAILED'}")
    print(f"AND RIGHT/LEFT Tests: {'PASSED' if test2 else 'FAILED'}")
    print(f"NOTAM Filtering Tests: {'PASSED' if test3 else 'FAILED'}")

    if test1 and test2 and test3:
        print("\n✓ ALL TESTS PASSED")
    else:
        print("\n✗ SOME TESTS FAILED")
