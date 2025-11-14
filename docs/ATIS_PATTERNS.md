# ATIS Phraseology Patterns Reference

This document catalogs ATIS phraseology patterns discovered from real-world data. Use this as a reference when improving the parser or reviewing unusual cases.

## Document Purpose
- **Reference**: Quick lookup for common ATIS phrases
- **Training**: Understand what patterns the parser should recognize
- **Debugging**: Compare actual ATIS text against known patterns
- **Learning**: Add new patterns discovered through human review

---

## Arrival Runway Patterns

### Standard Patterns
| Pattern | Example | Notes |
|---------|---------|-------|
| `RUNWAY XX APPROACH` | "RUNWAY 16L APPROACH" | Most common arrival pattern |
| `LANDING RUNWAY XX` | "LANDING RUNWAY 34R" | Direct and unambiguous |
| `EXPECT ILS RUNWAY XX APPROACH` | "EXPECT ILS RUNWAY 28L APPROACH" | Instrument approach |
| `VISUAL APPROACHES RUNWAY XX` | "VISUAL APPROACHES RUNWAY 19L" | VFR conditions |
| `ARRIVALS RUNWAY XX` | "ARRIVALS RUNWAY 16C" | Concise format |
| `LANDING AND DEPARTING RUNWAY XX` | "LANDING AND DEPARTING RUNWAY 16C" | Single runway ops |

### Multiple Runways
```
"RUNWAYS 16L 16C 16R IN USE FOR ARRIVALS"
"EXPECT RUNWAY 28L OR 28R APPROACH"
"LANDING RUNWAYS 16L AND 16R"
"SIMULTANEOUS APPROACHES RUNWAYS 28L 28R"
```

### Less Common Formats
```
"ILS APPROACHES TO RUNWAYS 16L 16C 16R"
"RUNWAY ONE SIX CENTER FOR ARRIVAL"
"APPROACH RUNWAY THREE FOUR RIGHT"
"ARRIVING AIRCRAFT USE RUNWAY 25L"
```

---

## Departure Runway Patterns

### Standard Patterns
| Pattern | Example | Notes |
|---------|---------|-------|
| `DEPARTING RUNWAY XX` | "DEPARTING RUNWAY 34R" | Most common departure pattern |
| `DEPARTURE RUNWAY XX` | "DEPARTURE RUNWAY 16L" | Slightly more formal |
| `TAKEOFF RUNWAY XX` | "TAKEOFF RUNWAY 28R" | Less common but valid |
| `DEPARTURES RUNWAY XX` | "DEPARTURES RUNWAY 25L" | Plural form |

### Multiple Runways
```
"DEPARTING RUNWAYS 16L 16C 16R"
"DEPARTURES USE RUNWAY 28L OR 28R"
"TAKEOFF RUNWAYS 07L 07R"
```

### Abbreviated Forms
```
"DEP RWY 16L"
"DEPG RWY 34R"
"DEP RUNWAY TWO EIGHT LEFT"
```

---

## Combined Operations Patterns

### Same Runways for Both
```
"RUNWAYS IN USE 28L 28R"
"LANDING AND DEPARTING RUNWAY 16C"
"RUNWAYS 16L 16C 16R IN USE"
"RUNWAY ONE SIX CENTER FOR ARRIVAL AND DEPARTURE"
```

### Separate Arrivals and Departures
```
"RUNWAY 16L APPROACH, DEPARTING RUNWAY 16R"
"LANDING RUNWAY 28L, DEPARTURE RUNWAY 28R"
"ARRIVALS RUNWAY 34L, DEPARTURES RUNWAY 34R"
"EXPECT ILS RUNWAY 16C APPROACH, DEPARTING RUNWAY 16L"
```

### Complex Multi-Runway Operations
```
"SIMULTANEOUS ILS APPROACHES RUNWAYS 28L 28R, DEPARTURES RUNWAYS 28L 28R"
"LANDING RUNWAYS 16L 16C, DEPARTING RUNWAYS 16C 16R"
"RUNWAYS IN USE: ARRIVALS 34L 34R, DEPARTURES 35L 35R"
```

---

## Tricky Cases and Edge Cases

### Opposite Direction Operations
```
"OPPOSITE DIRECTION OPERATIONS IN EFFECT"
"RUNWAY 16L FOR ARRIVAL, RUNWAY 34R FOR DEPARTURE"
```
**Challenge**: Requires extracting runway numbers from context, not standard pattern.

### Converging Runway Operations
```
"CONVERGING RUNWAY OPERATIONS IN EFFECT, RUNWAYS 28R AND 33L"
"SIMULTANEOUS CONVERGING APPROACHES RUNWAYS 28L 33R"
```
**Challenge**: Multiple flow directions active simultaneously.

### Runway Closures (Future Enhancement)
```
"RUNWAY 16L CLOSED"
"RUNWAY 34R CLOSED TO DEPARTURES"
"RUNWAY 28L AVAILABLE FOR ARRIVALS ONLY"
```
**Challenge**: Need to track restrictions, not just active runways.

### Intersection Departures
```
"DEPARTURES RUNWAY 16C AT INTERSECTION NOVEMBER"
"TAKEOFF RUNWAY 34L FULL LENGTH OR INTERSECTION TANGO"
```
**Challenge**: Additional detail that doesn't affect runway assignment.

### Taxiway Operations
```
"TAXIWAY CHARLIE USED AS RUNWAY"
"DEPARTURES TAXIWAY ECHO EAST"
```
**Challenge**: Taxiways acting as runways (usually at smaller airports).

---

## Numeric vs. Spelled-Out Formats

### Numeric Format (Easier to Parse)
```
"RUNWAY 16L APPROACH"
"DEPARTING RUNWAY 34R"
"RUNWAYS 28L 28R IN USE"
```

### Spelled-Out Format (Harder to Parse)
```
"RUNWAY ONE SIX LEFT APPROACH"
"DEPARTING RUNWAY THREE FOUR RIGHT"
"RUNWAYS TWO EIGHT LEFT TWO EIGHT RIGHT IN USE"
```

**Parser Note**: Need to handle both formats. Spelled-out numbers can be:
- ONE, TWO, THREE, FOUR, FIVE, SIX, SEVEN, EIGHT, NINER (or NINE), ZERO
- LEFT, CENTER, RIGHT (sometimes abbreviated L, C, R)

---

## Airport-Specific Patterns

### Seattle-Tacoma (KSEA)
**South Flow** (most common):
```
"RUNWAY 16L APPROACH, DEPARTING RUNWAYS 16L 16C 16R"
"SIMULTANEOUS ILS APPROACHES RUNWAYS 16L 16C 16R"
```

**North Flow** (strong south winds):
```
"RUNWAY 34L APPROACH, DEPARTING RUNWAYS 34L 34C 34R"
```

### San Francisco (KSFO)
**West Flow** (typical):
```
"SIMULTANEOUS ILS APPROACHES RUNWAYS 28L 28R"
"DEPARTURES RUNWAYS 28L 28R"
```

**Crossing Runway Ops** (special):
```
"LANDING RUNWAYS 28L 28R, DEPARTURES RUNWAY 01L 01R"
```

### Los Angeles (KLAX)
**West Flow** (prevailing):
```
"RUNWAYS 24L 24R 25L 25R IN USE"
"OVER OCEAN OPERATIONS IN EFFECT"
```

**East Flow** (rare, Santa Ana winds):
```
"RUNWAYS 06L 06R 07L 07R IN USE"
```

### Denver (KDEN)
**Unique patterns observed**:
```
"DEPG RWY 17L, RWY 25" → Departing runways: 17L, 25
```
**Note**: "DEPG" abbreviation for departing (non-standard).

---

## Pattern Extraction Rules

### Current Parser Logic
1. **Split by keywords**: APPROACH, LANDING, DEPARTING, etc.
2. **Extract runway numbers**: Regex `\d{2}[LCR]?` or spelled-out numbers
3. **Assign to category**: Based on keyword context (arrival vs departure)
4. **Calculate confidence**: Based on pattern clarity and matches

### Confidence Scoring Factors
- **High confidence (1.0)**: Unambiguous keywords, clear runway assignments
- **Medium confidence (0.5-0.9)**: Some ambiguity, multiple interpretations possible
- **Low confidence (< 0.5)**: Unclear phrasing, unusual patterns
- **Zero confidence (0.0)**: No patterns matched, parsing failed

### Common Parsing Challenges
1. **Ambiguous "in use"**: Doesn't specify arrivals vs departures
2. **Spelled-out numbers**: Harder to extract reliably
3. **Multiple operations**: Complex combinations of arrivals/departures
4. **Non-standard abbreviations**: Airport-specific shorthand
5. **Runway closures**: Negative statements (not X, only Y)

---

## Adding New Patterns

When you discover a new pattern through human review:

### 1. Document It Here
Add the pattern to the appropriate section with:
- Example ATIS text
- Airport code where found
- Expected parse result
- Any special notes or challenges

### 2. Test Parser Behavior
```python
# Example test
sample = "NEW PATTERN RUNWAY 16L SOMETHING"
config = parser.parse("KXXX", sample, "A")
# Did it parse correctly? What confidence?
```

### 3. Update Parser if Needed
If pattern is common and parser misses it:
- Add regex pattern to runway_parser.py
- Update confidence scoring
- Test against other samples
- Document the change

### 4. Add to Human Review Notes
If pattern appears in human_reviews table:
- Check parsing_corrections for learned pattern
- Verify success_rate is being tracked
- Consider applying automatically in future

---

## Regular Expression Patterns Used

### Current Regex Patterns (runway_parser.py)
```python
# Runway number extraction
r'\b(\d{2}[LCR]?)\b'  # Numeric: 16L, 34R, 09
r'(ONE|TWO|THREE|...).*?(LEFT|CENTER|RIGHT)?'  # Spelled-out

# Arrival keywords
r'APPROACH|LANDING|APCH RWY|ARRIVALS|ILS.*RUNWAY|VISUAL.*RUNWAY'

# Departure keywords
r'DEPARTURE|DEPARTING|TAKEOFF|DEP RWY|DEPG RWY'

# Combined operations
r'RUNWAYS? IN USE|LANDING AND DEPARTING|FOR ARRIVAL AND DEPARTURE'
```

### Pattern Testing
Test new patterns at: https://regex101.com/ (Python flavor)

---

## Human Review Integration

### When to Flag for Review
- Confidence score < 100%
- Empty arriving_runways or departing_runways arrays
- Unusual airport codes or ATIS formats
- Parser exceptions or errors
- New patterns not in this document

### Learning from Reviews
1. Human corrects parse in review dashboard
2. System stores in `human_reviews` table
3. Pattern extracted to `parsing_corrections` table
4. Success rate tracked over time
5. **Future**: Apply learned patterns automatically

### Review Dashboard Workflow
1. Access: http://localhost:8000/review
2. System shows ATIS text + current parse
3. Human corrects arriving/departing fields
4. Optional notes explain the correction
5. Submit → Pattern learned and stored

---

## Future Enhancements

### Short Term
- [ ] Add support for all spelled-out number variations
- [ ] Handle runway closure statements
- [ ] Detect opposite direction operations explicitly
- [ ] Improve confidence scoring algorithm

### Medium Term
- [ ] Use NLP (spaCy) to understand sentence structure
- [ ] Extract runway restrictions (arrival only, departure only)
- [ ] Detect special operations (converging, simultaneous, etc.)
- [ ] Apply learned patterns from human_reviews automatically

### Long Term
- [ ] Train ML model on corrected dataset
- [ ] Active learning: Ask human about most uncertain cases
- [ ] Multi-language support (ATIS in other countries)
- [ ] Audio ATIS transcription integration

---

## Contributing

Found a new pattern? Add it here!

1. Note the airport code and date/time
2. Copy the full ATIS text
3. Document what should be extracted
4. Note any parsing challenges
5. Update relevant sections of this document

**Example Entry**:
```
### New Pattern Name
**Found at**: KXXX on 2025-11-14
**ATIS Text**: "UNUSUAL PHRASING RUNWAY 16L..."
**Expected Parse**: Arriving: [16L], Departing: []
**Challenge**: Non-standard keyword usage
**Solution**: Added regex pattern `...`
```

---

**Last Updated**: 2025-11-14
**Patterns Documented**: 50+
**Airports Covered**: KSEA, KSFO, KLAX, KDEN, and growing
**Maintainer**: Human + Claude Code collaboration
