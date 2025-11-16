#!/usr/bin/env python3
"""
Test BOS pattern parsing: "RNAV 27, DEP 33L"
"""

from runway_parser import RunwayParser

def test_bos_patterns():
    parser = RunwayParser()

    # Test 1: BOS pattern "RNAV 27, DEP 33L"
    test1 = "BOS ATIS INFO H 0254Z. RNAV 27, DEP 33L. RY 27 ILS OTS."
    result1 = parser.parse("KBOS", test1, "H")

    print("Test 1: BOS 'RNAV 27, DEP 33L'")
    print(f"  Input: {test1}")
    print(f"  Arriving: {result1.arriving_runways}")
    print(f"  Departing: {result1.departing_runways}")
    print(f"  Confidence: {result1.confidence_score}")
    print(f"  Expected: Arriving=['27'], Departing=['33L']")

    if result1.arriving_runways == ['27'] and result1.departing_runways == ['33L']:
        print("  ✅ PASS")
    else:
        print("  ❌ FAIL")
    print()

    # Test 2: RNAV Y approach
    test2 = "RNAV Y 16L, DEP 16R"
    result2 = parser.parse("TEST", test2, "A")

    print("Test 2: RNAV Y pattern")
    print(f"  Input: {test2}")
    print(f"  Arriving: {result2.arriving_runways}")
    print(f"  Departing: {result2.departing_runways}")
    print(f"  Expected: Arriving=['16L'], Departing=['16R']")

    if result2.arriving_runways == ['16L'] and result2.departing_runways == ['16R']:
        print("  ✅ PASS")
    else:
        print("  ❌ FAIL")
    print()

    # Test 3: Multiple RNAV approaches
    test3 = "RNAV 22L, RNAV 22R, DEP 27"
    result3 = parser.parse("TEST", test3, "B")

    print("Test 3: Multiple RNAV approaches")
    print(f"  Input: {test3}")
    print(f"  Arriving: {result3.arriving_runways}")
    print(f"  Departing: {result3.departing_runways}")
    print(f"  Expected: Arriving=['22L', '22R'], Departing=['27']")

    if set(result3.arriving_runways) == {'22L', '22R'} and result3.departing_runways == ['27']:
        print("  ✅ PASS")
    else:
        print("  ❌ FAIL")
    print()

if __name__ == "__main__":
    print("Testing BOS pattern recognition...\n")
    test_bos_patterns()
    print("\nDone!")
