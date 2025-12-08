# Multi-TRV Heating Controller - Testing Guide

## Overview

The test suite provides comprehensive validation of the Multi-TRV Heating Controller component without requiring a full Home Assistant installation. Tests are organized into 16 detailed test cases covering basic functionality, multi-zone scenarios, and edge cases.

## Quick Start

### Run All Tests with Info Logging (Recommended)

```bash
cd tests/
python test_suite.py info
```

### Run Tests with Debug Logging (Detailed Output)

```bash
cd tests/
python test_suite.py debug
```

### Run Tests with Warning Logging (Errors Only)

```bash
cd tests/
python test_suite.py warning
```

### Using the Shell Script

```bash
cd tests/
bash run_tests.sh info    # or debug, or warning
```

## Test Log Files

Tests automatically create timestamped log files in `tests/logs/`:

```
tests/logs/20251208_214055_multi_trv_heating_results.txt
```

Each log file contains:
- Test case descriptions
- Detailed step-by-step execution
- Pass/fail verification results
- Test summary with pass rate
- All calculated values and state changes

## Test Suite Architecture

### Components

1. **test_logger.py** - Logging infrastructure
   - Configurable log levels (WARNING, INFO, DEBUG)
   - Console and file output
   - Color-coded console display
   - Formatted, timestamped entries

2. **mock_ha.py** - Home Assistant mocks
   - MockHass: Simulates HA core functionality
   - MockState: Climate entity state
   - MockEvent: State change events
   - MockClimateEntity: TRV valve simulation
   - MockSensorEntity: Temperature sensor
   - MockNumberEntity: Flow temperature control

3. **test_suite.py** - Test cases
   - 16 comprehensive test cases
   - Single-zone basic tests
   - Multi-zone scenarios
   - Edge cases and corner conditions
   - Realistic house simulation

### Test Categories

#### 1. Basic Single-Zone Tests (4 tests)

**test_single_zone_low_demand()**
- Verifies basic zone creation and state update
- Tests demand metric calculation for low demand
- Validates high-priority zone trigger at 25% opening

**test_single_zone_high_demand()**
- Tests high demand scenario (8°C below target, 100% open)
- Verifies demand metric ~0.8

**test_single_high_priority_zone_trigger()**
- Tests 25% threshold (should trigger)
- Tests 24% threshold (should not trigger)
- Tests 100% threshold (should trigger)

**test_single_low_priority_zone_no_trigger()**
- Tests 50% opening (should not trigger)
- Tests 99% opening (should not trigger)
- Tests 100% opening (should trigger)

#### 2. Multi-Zone Tests (3 tests)

**test_multiple_high_priority_zones()**
- Two high-priority zones with different demands
- Verifies both demand heat
- Confirms highest demand is used for boiler intensity

**test_multiple_low_priority_aggregation()**
- Three low-priority zones
- 30% + 30% + 50% = 110% aggregate
- None trigger alone, aggregate triggers boiler

**test_mixed_priority_zones()**
- One high-priority (30% opening)
- One low-priority (40% opening)
- Verifies high-priority can trigger alone

#### 3. Boiler Control Tests (2 tests)

**test_boiler_intensity_calculation()**
- Tests flow temperature calculation
- 0.0 demand → 5°C (OFF)
- 0.5 demand → 42.5°C (medium)
- 1.0 demand → 80°C (maximum)

**test_boiler_off_when_no_demand()**
- All zones at 0% opening
- No zones demanding
- Boiler OFF (5°C flow temperature)

#### 4. Temperature Offset Tests (2 tests)

**test_temperature_offset_adjustment()**
- Set offset from 0 to -2°C
- Test minimum clamp (-5°C)
- Test maximum clamp (+5°C)

**test_temperature_offset_reset()**
- Set offset to -3°C
- Update zone to target temperature
- Reset offset to 0

#### 5. Edge Cases (3 tests)

**test_zone_at_target_temperature()**
- Zone current = target
- Demand metric = 0 regardless of valve opening

**test_zero_opening_all_zones()**
- 5 zones with 0% opening
- All priorities (1.0 down to 0.0)
- No zones demanding, boiler OFF

**test_rapid_state_changes()**
- Simulate temperature oscillation around setpoint
- 8 rapid temperature changes
- Verify system stability and correct calculations

#### 6. Realistic Scenarios (2 tests)

**test_realistic_house_scenario()**
- Living Room (high pri): 22°C target, 20°C current, 40% open
- Bedroom (high pri): 20°C target, 18°C current, 60% open
- Guest (low pri): 19°C target, 18°C current, 50% open
- Hallway (low pri): 18°C target, 18°C current, 0% open

Expected Results:
- Living and Bedroom demand heat (high priority)
- Guest not demanding alone but included in aggregate
- Hallway not demanding (at target)
- Boiler ON, intensity based on highest demand

**test_zone_thermal_influence()**
- Zone A heating (50% open, 2°C below target)
- Zone B adjacent, closed initially
- Zone B temperature rises due to thermal influence
- When Zone B reaches target, it doesn't add demand
- Tests realistic multi-zone thermal coupling

## Understanding Test Output

### Console Output

Tests produce color-coded console output:

```
[INFO    ] 21:40:55 - test_suite - test_case() - 
[INFO    ] 21:40:55 - test_suite - test_case() - ┌──────────────────────────────────────────────────────────────────────────────┐
[INFO    ] 21:40:55 - test_suite - test_case() - │ TEST CASE: Single Zone - Low Demand                                           │
[INFO    ] 21:40:55 - test_suite - test_case() - │ Description of what the test does...                                          │
[INFO    ] 21:40:55 - test_suite - test_case() - └──────────────────────────────────────────────────────────────────────────────┘

[INFO    ] 21:40:55 - test_suite - step() - 
  Step 1: Create high-priority zone

[INFO    ] 21:40:55 - test_suite - verify() -     ✓ PASS: Zone should be demanding heat

[INFO    ] 21:40:55 - test_suite - debug() -   → Detailed internal state information

[INFO    ] 21:40:55 - test_suite - info() -   • Important information or result
```

### Log File Format

Log files contain the same information plus:
- Exact timestamps for each operation
- Full details of state changes
- Calculation intermediate values
- Summary statistics

Example log entry:

```
[INFO    ] 21:40:55 - test_suite - step() - 
  Step 2: Update zone to 2°C below target with 25% valve opening

[DEBUG   ] 21:40:55 - test_suite - debug() -   → Current: 18.0°C, Target: 20.0°C, Error: 2.0°C

[DEBUG   ] 21:40:55 - test_suite - debug() -   → TRV Opening: 25.0%

[INFO    ] 21:40:55 - test_suite - verify() -     ✓ PASS: Zone should be demanding heat (high priority at 25%)
```

## Test Coverage

### Functionality Tested

✓ Zone initialization and configuration
✓ State updates (temperature, target, opening %)
✓ Demand metric calculation
✓ High-priority zone logic (>0.5 priority)
✓ Low-priority zone logic (≤0.5 priority)
✓ Zone aggregation for low-priority zones
✓ Boiler on/off decisions
✓ Boiler intensity calculation (flow temperature)
✓ Temperature offset adjustment
✓ Temperature offset reset at target
✓ Edge cases (zero demand, rapid changes, etc.)
✓ Realistic multi-zone scenarios
✓ Thermal influence between zones

### Scenarios Covered

- Single zones with varying demands
- Multiple high-priority zones competing for boiler
- Multiple low-priority zones aggregating
- Mixed priority scenarios
- Zones at target temperature (no demand)
- Rapid temperature fluctuations
- Realistic 4-zone house setup
- Thermal coupling between adjacent zones

## Test Results Interpretation

### Perfect Run (All 16 Tests Pass)

```
================================================================================
TEST SUMMARY
================================================================================
Total Tests:    16
Passed:         16 (100.0%)
Failed:         0
Test Log File:  /home/don/projects/homeassistant/MultiTRVHeating/tests/logs/20251208_214055_multi_trv_heating_results.txt
================================================================================
```

### Failed Test

If a test fails, the log will show:

```
[ERROR  ] ... - verify() -     ✗ FAIL: Zone should be demanding heat (high priority at 25%)
```

Then check:
1. The calculation details logged before the failure
2. The expected vs actual values
3. The component code for the failing condition

## Adding New Tests

To add a new test case:

1. Add a new method to the `TestSuite` class:

```python
async def test_new_scenario(self) -> None:
    """
    Test: Brief description
    
    Scenario: What this tests
    Expected: What should happen
    """
    self.log.test_case(
        "Test Name",
        "Detailed description of what the test validates."
    )
    
    try:
        self.log.step(1, "First step description")
        # ... test code ...
        
        self.log.verify(condition, "Verification message")
        
        self.log.info("Test PASSED: Description")
        self.passed_tests += 1
        
    except AssertionError as e:
        self.log.warning(str(e))
        self.failed_tests += 1
```

2. Add it to `run_all_tests()`:

```python
async def run_all_tests(self) -> None:
    """Run all test cases in sequence."""
    try:
        # ... existing tests ...
        await self.test_new_scenario()  # Add your test here
    except Exception as e:
        # ...
```

## Debugging Tips

### Enable Debug Logging

```bash
python test_suite.py debug
```

Debug output includes:
- Intermediate calculation values
- State transitions
- All zone attributes before/after updates

### Check Specific Test

Edit `test_suite.py` and modify `run_all_tests()` to run only one test:

```python
async def run_all_tests(self) -> None:
    try:
        await self.test_single_zone_low_demand()  # Only this one
    except Exception as e:
        # ...
```

### View Recent Log Files

```bash
ls -lt tests/logs/ | head -5
tail -f tests/logs/20251208_214055_multi_trv_heating_results.txt
```

### Analyze Test Failures

1. Look at the log file for the failing test
2. Find the step where it fails
3. Check the calculated values in debug output
4. Compare with expected values in the test case
5. Review the component code for the relevant logic

## Performance Notes

- Full test suite runs in ~0.5 seconds
- 16 comprehensive tests with detailed logging
- No dependencies on Home Assistant or hardware
- All mocked interactions
- Suitable for CI/CD pipelines

## Future Test Enhancements

Potential additions to the test suite:

1. **Thermal Model Tests**: Simulate realistic heat transfer between zones
2. **Time-Series Tests**: Multi-step scenarios tracking temperature over time
3. **Stress Tests**: High number of zones (50+)
4. **Performance Tests**: Measurement of decision calculation time
5. **Integration Tests**: Mock full component lifecycle (init → updates → shutdown)
6. **Property-Based Tests**: Using hypothesis library for generated test cases

## Troubleshooting

### Tests Won't Run

```bash
cd tests/
python -m py_compile test_suite.py mock_ha.py test_logger.py
```

Check for syntax errors in component files:

```bash
python -m py_compile ../custom_components/multi_trv_heating/*.py
```

### Import Errors

Verify path setup in test_suite.py:

```python
sys.path.insert(0, str(Path(__file__).parent.parent / 'custom_components' / 'multi_trv_heating'))
sys.path.insert(0, str(Path(__file__).parent))
```

### Log Files Not Created

Verify `logs/` directory exists:

```bash
mkdir -p tests/logs/
```

### Tests Fail with Different Results

Check if component code has changed. Re-run tests with debug logging:

```bash
python test_suite.py debug
```

Compare output with previous successful runs.

## Support

For issues or enhancements:
1. Check test output and log files
2. Enable debug logging for detailed trace
3. Compare with recent log files to see changes
4. Review component code changes
5. Add new tests for new functionality
