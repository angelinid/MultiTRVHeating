"""
Unified Test Runner for Multi-TRV Heating Controller

This module orchestrates all test suites across the project:
- Core Tests: Zone wrapping, demand calculations, boiler control
- Feature Tests: Pump discharge, pre-heating, temperature offsets
- Integration Tests: Multi-zone scenarios, realistic house models
- Sensor Tests: Entity creation and state management

Usage:
    python test_runner.py [log_level] [test_suite]
    
    log_level: debug|info|warning (default: info)
    test_suite: core|features|integration|sensors|all (default: all)

Architecture:
    - Core tests (test_suite.py): 16 tests - Essential heating logic
    - Feature tests (test_pump_discharge.py): 8 tests - Pump discharge
    - Feature tests (test_preheating.py): 10 tests - Pre-heating mode
    - Integration tests (test_sensors.py): Sensor entity management
"""

import sys
import asyncio
import os
from pathlib import Path
from enum import Enum

# Add parent directory to path
sys.path.insert(0, str(Path(__file__).parent.parent / 'custom_components' / 'multi_trv_heating'))
sys.path.insert(0, str(Path(__file__).parent))

from test_logger import create_test_logger


class TestSuite(Enum):
    """Available test suites."""
    CORE = "core"
    FEATURES = "features"
    INTEGRATION = "integration"
    ALL = "all"


class UnifiedTestRunner:
    """Orchestrates all test suites."""
    
    def __init__(self, log_level: str = 'info'):
        """Initialize test runner."""
        self.log = create_test_logger(log_level, 'test_runner')
        self.results = {
            'core': {'passed': 0, 'failed': 0, 'total': 0},
            'features': {'passed': 0, 'failed': 0, 'total': 0},
            'integration': {'passed': 0, 'failed': 0, 'total': 0},
        }
        self.log_level = log_level
    
    async def run_all_tests(self, suite_type: str = 'all') -> None:
        """
        Run all test suites.
        
        Args:
            suite_type: Which suites to run: 'core', 'features', 'integration', or 'all'
        """
        print("\n" + "="*80)
        print("MULTI-TRV HEATING CONTROLLER - UNIFIED TEST RUNNER")
        print("="*80)
        print(f"Log Level: {self.log_level.upper()}")
        print(f"Test Suite: {suite_type.upper()}")
        print("="*80 + "\n")
        
        if suite_type in ['core', 'all']:
            await self._run_core_tests()
        
        if suite_type in ['features', 'all']:
            await self._run_feature_tests()
        
        if suite_type in ['integration', 'all']:
            await self._run_integration_tests()
        
        self._print_summary()
    
    async def _run_core_tests(self) -> None:
        """Run core heating logic tests."""
        print("\n" + "-"*80)
        print("CORE TESTS: Zone Heating Logic")
        print("-"*80)
        print("Testing: Single/multi-zone heating, demand calculation, boiler control")
        print()
        
        try:
            from test_suite import TestSuite as CoreTestSuite
            suite = CoreTestSuite(log_level=self.log_level)
            await suite.run_all_tests()
            
            self.results['core']['passed'] = suite.passed_tests
            self.results['core']['failed'] = suite.failed_tests
            self.results['core']['total'] = suite.passed_tests + suite.failed_tests
            
        except Exception as e:
            self.log.warning(f"Core tests failed to run: {e}")
            self.results['core']['failed'] = 1
            self.results['core']['total'] = 1
    
    async def _run_feature_tests(self) -> None:
        """Run feature tests (pump discharge, preheating)."""
        print("\n" + "-"*80)
        print("FEATURE TESTS: Advanced Heating Features")
        print("-"*80)
        print("Testing: Pump discharge, pre-heating, temperature offsets")
        print()
        
        # Pump discharge tests
        await self._run_pump_discharge_tests()
        
        # Pre-heating tests
        await self._run_preheating_tests()
    
    async def _run_pump_discharge_tests(self) -> None:
        """Run pump discharge tests."""
        print("\n  Pump Discharge Tests:")
        print("  " + "-"*76)
        
        try:
            from test_pump_discharge import PumpDischargeTestSuite
            suite = PumpDischargeTestSuite(log_level=self.log_level)
            await suite.run_all_tests()
            
            self.results['features']['passed'] += suite.passed_tests
            self.results['features']['failed'] += suite.failed_tests
            self.results['features']['total'] += suite.passed_tests + suite.failed_tests
            
        except Exception as e:
            self.log.warning(f"Pump discharge tests failed to run: {e}")
            self.results['features']['failed'] += 1
            self.results['features']['total'] += 1
    
    async def _run_preheating_tests(self) -> None:
        """Run pre-heating tests."""
        print("\n  Pre-heating Tests:")
        print("  " + "-"*76)
        
        try:
            from test_preheating import TestPreheating
            suite = TestPreheating()
            suite.run_all_tests()
            
            self.results['features']['passed'] += suite.passed
            self.results['features']['failed'] += suite.failed
            self.results['features']['total'] += suite.passed + suite.failed
            
        except Exception as e:
            self.log.warning(f"Pre-heating tests failed to run: {e}")
            self.results['features']['failed'] += 1
            self.results['features']['total'] += 1
    
    async def _run_integration_tests(self) -> None:
        """Run integration tests (sensors)."""
        print("\n" + "-"*80)
        print("INTEGRATION TESTS: Home Assistant Integration")
        print("-"*80)
        print("Testing: Sensor entity creation and state management")
        print()
        
        try:
            from test_sensors import TestSensors
            suite = TestSensors()
            suite.run_all_tests()
            
            self.results['integration']['passed'] = suite.passed
            self.results['integration']['failed'] = suite.failed
            self.results['integration']['total'] = suite.passed + suite.failed
            
        except Exception as e:
            self.log.warning(f"Integration tests failed to run: {e}")
            self.results['integration']['failed'] = 1
            self.results['integration']['total'] = 1
    
    def _print_summary(self) -> None:
        """Print test summary."""
        print("\n" + "="*80)
        print("TEST SUMMARY")
        print("="*80)
        
        total_passed = 0
        total_failed = 0
        total_tests = 0
        
        for suite_name, results in self.results.items():
            if results['total'] > 0:
                passed = results['passed']
                failed = results['failed']
                total = results['total']
                percent = (passed / total * 100) if total > 0 else 0
                
                status = "✓ PASS" if failed == 0 else "✗ FAIL"
                print(f"\n{suite_name.upper():15} {status:10} {passed:3}/{total:3} ({percent:5.1f}%)")
                
                total_passed += passed
                total_failed += failed
                total_tests += total
        
        print("\n" + "-"*80)
        overall_percent = (total_passed / total_tests * 100) if total_tests > 0 else 0
        print(f"{'OVERALL':15} {'TOTAL':10} {total_passed:3}/{total_tests:3} ({overall_percent:5.1f}%)")
        print("="*80)
        
        # Exit code
        exit_code = 0 if total_failed == 0 else 1
        
        if total_failed == 0:
            print("\n✓ ALL TESTS PASSED!")
        else:
            print(f"\n✗ {total_failed} TEST(S) FAILED")
        
        print()
        sys.exit(exit_code)


def _run_preheating_sync(runner):
    """Wrapper to run preheating tests synchronously."""
    from test_preheating import TestPreheating
    suite = TestPreheating()
    suite.run_all_tests()
    return suite


async def main():
    """Main entry point."""
    # Parse arguments
    log_level = 'info'
    suite_type = 'all'
    
    if len(sys.argv) > 1:
        log_level = sys.argv[1].lower()
        if log_level not in ['debug', 'info', 'warning']:
            print("Usage: python test_runner.py [log_level] [suite]")
            print("  log_level: debug|info|warning (default: info)")
            print("  suite: core|features|integration|all (default: all)")
            sys.exit(1)
    
    if len(sys.argv) > 2:
        suite_type = sys.argv[2].lower()
        if suite_type not in ['core', 'features', 'integration', 'all']:
            print("Usage: python test_runner.py [log_level] [suite]")
            print("  log_level: debug|info|warning (default: info)")
            print("  suite: core|features|integration|all (default: all)")
            sys.exit(1)
    
    # Run tests
    runner = UnifiedTestRunner(log_level=log_level)
    await runner.run_all_tests(suite_type=suite_type)


if __name__ == "__main__":
    asyncio.run(main())
