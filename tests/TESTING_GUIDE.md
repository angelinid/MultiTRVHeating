# Testing Guide - Multi-TRV Heating Controller

## Overview

The Multi-TRV Heating Controller uses a **rationalized, modular test architecture** with organized test suites grouped by functionality:

- **Core Tests** (16 tests) - Essential heating logic ✓ 100% passing
- **Feature Tests** (22 tests) - Advanced features (pump discharge, pre-heating)
- **Integration Tests** (39 tests) - Home Assistant sensor integration ✓ 100% passing

**Total: 77 tests across 3 suites**

---

## Architecture

### Test Organization

```
tests/
├── test_runner.py                 # Unified test orchestrator
├── run_tests.sh                   # Shell script wrapper
│
├── test_suite.py                  # CORE: Heating logic (16 tests)
│   ├── Single-zone heating demand
│   ├── Multi-zone scenarios
│   ├── Demand aggregation
│   ├── Boiler control
│   ├── Temperature offset management
│   ├── Edge cases
│   └── Realistic scenarios
│
├── test_pump_discharge.py         # FEATURES: Pump discharge (8 tests)
│   ├── TRV exclusion from calculations
│   ├── Discharge activation/deactivation
│   ├── Timeout management
│   └── State reporting
│
├── test_preheating.py             # FEATURES: Pre-heating mode (14 tests)
│   ├── Activation/deactivation
│   ├── Thermal load calculation
│   ├── Flow temperature override
│   └── Edge cases
│
└── test_sensors.py                # INTEGRATION: Sensor entities (39 tests)
    ├── Entity creation
    ├── State computation
    ├── Units and icons
    └── Manager functionality
```

### Test Hierarchy

```
All Tests (77 total)
│
├─ Core (16/16 passing) ✓
│  └─ Essential heating logic - must not break
│
├─ Features (8/22 passing)
│  ├─ Pump Discharge (8 tests)
│  └─ Pre-heating (14 tests)
│
└─ Integration (39/39 passing) ✓
   └─ Home Assistant sensor integration
```

---

## Running Tests

### Quick Start

Run **all tests**:
```bash
./run_tests.sh
# or
python test_runner.py
```

### Specific Test Suites

Run **only core tests** (fastest, most critical):
```bash
./run_tests.sh info core
python test_runner.py info core
```

Run **feature tests** only:
```bash
./run_tests.sh info features
python test_runner.py info features
```

Run **integration tests** only:
```bash
./run_tests.sh info integration
python test_runner.py info integration
```

### Logging Levels

Control verbosity with log level (default: info):

```bash
./run_tests.sh debug              # Full debug output
./run_tests.sh info               # Normal logging (default)
./run_tests.sh warning            # Warnings only (errors)
```

### Full Examples

```bash
# Run all tests with debug logging
./run_tests.sh debug all

# Run only core tests with warning level
./run_tests.sh warning core

# Run features with debug output to diagnose failures
python test_runner.py debug features
```

---

## Test Status Summary

### ✓ PASSING (55/77 = 71.4%)

#### Core Tests: 16/16 (100%)
Essential heating control logic - **CRITICAL PATH**

| Test | Purpose |
|------|---------|
| Single Zone - Low Demand | Basic zone heating |
| Single Zone - High Demand | Maximum demand calculation |
| High-Priority Zone Trigger | Boiler activation at 25%+ opening |
| Low-Priority Zone - No Solo Trigger | 100% opening required |
| Multiple High-Priority Zones | Highest demand wins |
| Low-Priority Zone Aggregation | Multiple zones sum to 100% |
| Mixed Priority Zones | High priority overrides low |
| Boiler Intensity Calculation | Linear demand → flow temp |
| Boiler OFF - No Demand | Proper shutdown |
| Temperature Offset Adjustment | Offset at 75%+ opening |
| Temperature Offset Reset | Reset on valve close |
| Zone At Target Temperature | Zero demand when satisfied |
| All Zones Closed | Boiler stays OFF |
| Rapid State Changes | Stability under oscillation |
| Realistic House Scenario | 4-zone mixed priorities |
| Zone Thermal Influence | Adjacent zone heating |

#### Integration Tests: 39/39 (100%)
Home Assistant sensor entity management - **COMPLETE**

| Test Group | Status |
|-----------|--------|
| Sensor creation | ✓ 7/7 |
| State computation | ✓ 8/8 |
| Unit reporting | ✓ 7/7 |
| Icon configuration | ✓ 1/1 |
| Manager functionality | ✓ 16/16 |

### ✗ FAILING (22/77 = 28.6%)

#### Feature Tests: 8/22 (36.4%)

**Pump Discharge: 0/8** (needs implementation alignment)
- TRV exclusion from calculation
- Discharge activation/deactivation
- Timeout management
- State reporting

**Pre-heating: 6/14** (missing implementation)
- Activation/deactivation: ✓ 3/3
- Thermal load: ✓ 3/3  
- Flow temp calculation: ✗ 0/4
- Edge cases: ✗ 0/4

---

## Test Development Guidelines

### Adding New Tests

1. **Determine the category**: Core, Feature, or Integration
2. **Add to appropriate file**:
   - `test_suite.py` - Heating logic
   - `test_pump_discharge.py` - Pump discharge feature
   - `test_preheating.py` - Pre-heating feature
   - `test_sensors.py` - Sensor entities

3. **Follow the pattern**:
```python
async def test_my_feature(self) -> None:
    """Test: My feature description"""
    self.log.test_case("My Feature", "Detailed description")
    
    try:
        self.log.step(1, "Setup")
        # ... test code ...
        
        self.log.verify(condition, "Message")
        self.passed_tests += 1
    except AssertionError as e:
        self.log.warning(str(e))
        self.failed_tests += 1
```

4. **Run only your test suite during development**:
```bash
python test_suite.py debug  # For test_suite.py changes
python test_runner.py debug features  # For feature tests
```

---

## Diagnostic Tips

### Core Tests Failing?
- Check [test_suite.py](test_suite.py) - tests the heating algorithm
- Likely issue: Changes to `zone_wrapper.py` or `master_controller.py`
- **Action**: Review zone demand logic and boiler control

### Integration Tests Failing?
- Check [test_sensors.py](test_sensors.py) - tests sensor entity creation
- Likely issue: Changes to `sensor.py` or entity definitions
- **Action**: Review sensor entity creation and state computation

### Feature Tests Failing?
- Check [test_pump_discharge.py](test_pump_discharge.py) or [test_preheating.py](test_preheating.py)
- These are advanced features - likely not critical for core heating
- **Action**: Either fix implementation or update tests to match new behavior

---

## Continuous Integration

The unified test runner is designed for CI/CD integration:

```bash
# Example for GitHub Actions / GitLab CI
python test_runner.py warning all
exit_code=$?
if [ $exit_code -ne 0 ]; then
    echo "Tests failed!"
    exit 1
fi
```

### Exit Codes
- `0` - All tests passed
- `1` - One or more tests failed

---

## Performance

Test execution times (approximate):

| Suite | Time | Count |
|-------|------|-------|
| Core | ~2 sec | 16 |
| Features | ~3 sec | 22 |
| Integration | ~1 sec | 39 |
| **Total** | **~6 sec** | **77** |

Run `time ./run_tests.sh` to measure on your system.

---

## Test Logs

Test logs are automatically saved to `logs/` directory:

```
logs/
├── 20260120_215714_multi_trv_heating_results.txt
├── 20260120_215728_preheating_tests_results.txt
├── 20260120_215741_pump_discharge_tests_results.txt
└── 20260120_215742_sensor_tests_results.txt
```

Each log contains:
- Full test output
- Detailed steps and verifications
- Timestamps
- Log level-specific filtering

---

## Troubleshooting

### Tests Won't Run
```bash
# Check Python path
python -c "import sys; print(sys.path)"

# Verify imports
python -c "from test_logger import create_test_logger"

# Run with full traceback
python test_runner.py warning all
```

### Permission Denied on run_tests.sh
```bash
chmod +x run_tests.sh
./run_tests.sh
```

### Mixed Test Results Expected?
- Core tests (16) should ALWAYS pass - these are critical
- Integration tests (39) should ALWAYS pass - sensors are complete
- Feature tests (22) may fail if features are not fully implemented
- Update feature test expectations if implementation changes

---

## Architecture Rationale

This test architecture provides:

1. **Clear Separation of Concerns**
   - Core logic isolated from features
   - Integration tests independent of implementation details

2. **Scalability**
   - Easy to add new tests to any suite
   - Unified runner scales with new suites

3. **Meaningful Feedback**
   - Know exactly which component is broken
   - Core tests are safety net for regressions

4. **CI/CD Integration**
   - Single exit code for build systems
   - Flexible suite selection for different pipelines
   - Log retention for debugging

5. **Development Workflow**
   - Run only relevant tests during development
   - Full regression testing before commit
   - Fast feedback loop with individual test files

---

## Next Steps

### To Fix Feature Tests
1. **Pump Discharge**: Align tests with actual discharge behavior
2. **Pre-heating**: Implement missing flow temperature calculations

### To Extend Testing
1. Add new core tests as heating logic evolves
2. Add feature tests for new components
3. Add integration tests for new sensor entities

### Future Improvements
- Parametrized tests for multiple scenarios
- Performance benchmarking
- Coverage reporting
- Mutation testing for algorithm validation
