"""
Unit tests for pre-heating functionality in MasterController.

Pre-heating allows the boiler to heat more aggressively when a scene activates
with target temperatures that must be reached by a specific time. Instead of
using demand-based flow temperature calculation, pre-heating uses a parametric
formula based on thermal load and time remaining.
"""

import asyncio
from datetime import datetime, timedelta
import sys
import os

# Add parent directory to path for imports
sys.path.insert(0, os.path.join(os.path.dirname(__file__), '..', 'custom_components', 'multi_trv_heating'))

from zone_wrapper import ZoneWrapper
from master_controller import MasterController
from preheating import PreheatingController, PREHEATING_TUNING_CONSTANT, MIN_FLOW_TEMP, MAX_FLOW_TEMP


class MockHomeAssistant:
    """Mock Home Assistant instance for testing."""
    
    def __init__(self):
        self.services = MockServices()
        self.helpers = MockHelpers()


class MockServices:
    """Mock Home Assistant services."""
    
    async def async_call(self, *args, **kwargs):
        pass


class MockHelpers:
    """Mock Home Assistant helpers."""
    
    def __init__(self):
        self.event = MockEvent()


class MockEvent:
    """Mock event tracking."""
    
    def async_track_state_change_event(self, entities, callback):
        pass


class TestPreheating:
    """Test suite for pre-heating functionality."""
    
    def __init__(self):
        self.hass = MockHomeAssistant()
        self.controller = None
        self.passed = 0
        self.failed = 0
    
    def setup_controller(self, zone_configs=None):
        """Set up a master controller with test zones."""
        if zone_configs is None:
            zone_configs = [
                {
                    'entity_id': 'climate.living_room',
                    'name': 'Living Room',
                    'area': 40.0,
                    'is_high_priority': True,
                    'trv_position_entity_id': 'sensor.living_room_position',
                    'temp_calib_entity_id': 'number.living_room_calib'
                },
                {
                    'entity_id': 'climate.bedroom',
                    'name': 'Bedroom',
                    'area': 25.0,
                    'is_high_priority': True,
                    'trv_position_entity_id': 'sensor.bedroom_position',
                    'temp_calib_entity_id': 'number.bedroom_calib'
                },
                {
                    'entity_id': 'climate.guest',
                    'name': 'Guest Room',
                    'area': 15.0,
                    'is_high_priority': False,
                    'trv_position_entity_id': 'sensor.guest_position',
                    'temp_calib_entity_id': None
                }
            ]
        
        self.controller = MasterController(self.hass, zone_configs)
    
    def verify(self, condition: bool, message: str) -> None:
        """Verify a test condition."""
        if condition:
            print(f"    ✓ PASS: {message}")
            self.passed += 1
        else:
            print(f"    ✗ FAIL: {message}")
            self.failed += 1
    
    def test_1_preheating_not_active_by_default(self):
        """Test that pre-heating is not active by default."""
        print("\nTest 1: Pre-heating not active by default")
        self.setup_controller()
        
        is_active = self.controller.preheating.is_active()
        self.verify(not is_active, "Pre-heating should not be active by default")
        
        end_time = self.controller.preheating.preheating_end_time
        self.verify(end_time is None, "End time should be None initially")
    
    def test_2_preheating_activation(self):
        """Test activating pre-heating with a future end time."""
        print("\nTest 2: Pre-heating activation")
        self.setup_controller()
        
        # Set end time 30 minutes in future
        future_time = datetime.now() + timedelta(minutes=30)
        self.controller.preheating.preheating_end_time = future_time
        self.controller.preheating.is_enabled = True
        
        is_active = self.controller.preheating.is_active()
        self.verify(is_active, "Pre-heating should be active when end time is in future")
        
        remaining = (future_time - datetime.now()).total_seconds()
        self.verify(1700 < remaining < 1810, f"30 min window should be ~1800s, got {remaining}s")
    
    def test_3_preheating_deactivation_past_time(self):
        """Test that pre-heating deactivates when time is in the past."""
        print("\nTest 3: Pre-heating deactivation (past time)")
        self.setup_controller()
        
        # Set end time 5 minutes in past
        past_time = datetime.now() - timedelta(minutes=5)
        self.controller.preheating.preheating_end_time = past_time
        
        is_active = self.controller.preheating.is_active()
        self.verify(not is_active, "Pre-heating should be inactive when end time is past")
    
    def test_4_max_thermal_load_single_zone(self):
        """Test thermal load calculation for single high-priority zone."""
        print("\nTest 4: Max thermal load - single zone")
        self.setup_controller()
        
        # Set up living room: 4°C below target, 40 m² area
        living = self.controller.zones['climate.living_room']
        living.target_temp = 22.0
        living.current_temp = 18.0
        living.current_error = 4.0
        
        # Bedroom at target (should be ignored)
        bedroom = self.controller.zones['climate.bedroom']
        bedroom.target_temp = 20.0
        bedroom.current_temp = 20.0
        bedroom.current_error = 0.0
        
        max_load = self.controller.preheating._get_max_high_priority_thermal_load()
        expected_load = 4.0 * 40.0  # error * area = 160
        
        self.verify(
            abs(max_load - expected_load) < 0.1,
            f"Max thermal load should be {expected_load}, got {max_load}"
        )
    
    def test_5_max_thermal_load_multiple_zones(self):
        """Test thermal load with multiple high-priority zones."""
        print("\nTest 5: Max thermal load - multiple zones")
        self.setup_controller()
        
        # Living room: 2°C error, 40 m² → load = 80
        living = self.controller.zones['climate.living_room']
        living.target_temp = 22.0
        living.current_temp = 20.0
        living.current_error = 2.0
        
        # Bedroom: 4°C error, 25 m² → load = 100 (MAX)
        bedroom = self.controller.zones['climate.bedroom']
        bedroom.target_temp = 20.0
        bedroom.current_temp = 16.0
        bedroom.current_error = 4.0
        
        max_load = self.controller.preheating._get_max_high_priority_thermal_load()
        expected_max = 100.0  # bedroom's load
        
        self.verify(
            abs(max_load - expected_max) < 0.1,
            f"Should use max load (100), got {max_load}"
        )
    
    def test_6_max_thermal_load_ignores_low_priority(self):
        """Test that low-priority zones are ignored in thermal load."""
        print("\nTest 6: Max thermal load - ignore low-priority")
        self.setup_controller()
        
        # Only low-priority zone has error (should be ignored)
        guest = self.controller.zones['climate.guest']
        guest.target_temp = 19.0
        guest.current_temp = 15.0  # 4°C error
        guest.current_error = 4.0
        
        # High-priority zones at target
        for zone in [self.controller.zones['climate.living_room'],
                     self.controller.zones['climate.bedroom']]:
            zone.target_temp = 22.0
            zone.current_temp = 22.0
            zone.current_error = 0.0
        
        max_load = self.controller.preheating._get_max_high_priority_thermal_load()
        self.verify(max_load == 0.0, "Low-priority zones should not contribute to thermal load")
    
    def test_7_preheating_flow_temp_calculation_30min(self):
        """Test pre-heating flow temp calculation with 30 min window."""
        print("\nTest 7: Pre-heating flow temp - 30 min window")
        self.setup_controller()
        
        # Set up scenario: Living room 2°C below, 40 m²
        living = self.controller.zones['climate.living_room']
        living.target_temp = 22.0
        living.current_temp = 20.0
        living.current_error = 2.0
        
        # Activate pre-heating: 30 minutes from now
        end_time = datetime.now() + timedelta(minutes=30)
        self.controller.preheating.preheating_end_time = end_time
        self.controller.preheating.is_enabled = True
        
        flow_temp = self.controller.preheating.calculate_flow_temp_override()
        
        # thermal_load = 2.0 * 40.0 = 80
        # time_pressure = 1.0 / 1800 = 0.000556
        # override = 80 * 0.000556 * 1.0 = 0.0444
        # flow_temp = 5 + 0.0444 ≈ 5.04
        
        self.verify(
            MIN_FLOW_TEMP <= flow_temp <= MAX_FLOW_TEMP,
            f"Flow temp should be in range [{MIN_FLOW_TEMP}, {MAX_FLOW_TEMP}], got {flow_temp}"
        )
        self.verify(
            flow_temp > MIN_FLOW_TEMP,
            "Pre-heating should increase flow temp above MIN"
        )
    
    def test_8_preheating_flow_temp_aggressive_with_less_time(self):
        """Test that pre-heating becomes more aggressive as time runs out."""
        print("\nTest 8: Pre-heating - more aggressive with less time")
        self.setup_controller()
        
        # Set up same scenario
        living = self.controller.zones['climate.living_room']
        living.target_temp = 22.0
        living.current_temp = 20.0
        living.current_error = 2.0
        
        # Flow temp with 30 minutes remaining
        end_time_30min = datetime.now() + timedelta(minutes=30)
        self.controller.preheating.preheating_end_time = end_time_30min
        self.controller.preheating.is_enabled = True
        flow_temp_30min = self.controller.preheating.calculate_flow_temp_override()
        
        # Flow temp with 5 minutes remaining (6x time pressure)
        end_time_5min = datetime.now() + timedelta(minutes=5)
        self.controller.preheating.preheating_end_time = end_time_5min
        self.controller.preheating.is_enabled = True
        flow_temp_5min = self.controller.preheating.calculate_flow_temp_override()
        
        self.verify(
            flow_temp_5min > flow_temp_30min,
            f"5-min should be more aggressive ({flow_temp_5min}) than 30-min ({flow_temp_30min})"
        )
    
    def test_9_preheating_maxes_out_with_large_thermal_load(self):
        """Test that pre-heating respects MAX_FLOW_TEMP."""
        print("\nTest 9: Pre-heating - clamps to MAX_FLOW_TEMP")
        self.setup_controller()
        
        # Extreme scenario: 10°C error, 100 m² area, only 1 second remaining
        living = self.controller.zones['climate.living_room']
        living.target_temp = 22.0
        living.current_temp = 12.0
        living.current_error = 10.0
        living.floor_area_m2 = 100.0
        
        # End time in 1 second
        end_time = datetime.now() + timedelta(seconds=1)
        self.controller.preheating.preheating_end_time = end_time
        self.controller.preheating.is_enabled = True
        
        flow_temp = self.controller.preheating.calculate_flow_temp_override()
        
        self.verify(
            flow_temp <= MAX_FLOW_TEMP,
            f"Flow temp should not exceed MAX ({MAX_FLOW_TEMP}), got {flow_temp}"
        )
        self.verify(
            flow_temp > 50.0,  # Should be quite aggressive
            f"Should be aggressive with extreme conditions, got {flow_temp}"
        )
    
    def test_10_preheating_ignores_low_priority_in_override(self):
        """Test that low-priority zones don't affect pre-heating override."""
        print("\nTest 10: Pre-heating - ignores low-priority zones")
        self.setup_controller()
        
        # High-priority zone minimal error
        living = self.controller.zones['climate.living_room']
        living.target_temp = 22.0
        living.current_temp = 21.5
        living.current_error = 0.5
        
        # Low-priority zone has huge error (should be ignored)
        guest = self.controller.zones['climate.guest']
        guest.target_temp = 19.0
        guest.current_temp = 10.0  # 9°C error!
        guest.current_error = 9.0
        guest.floor_area_m2 = 100.0
        
        # 30 minutes remaining
        end_time = datetime.now() + timedelta(minutes=30)
        self.controller.preheating.preheating_end_time = end_time
        self.controller.preheating.is_enabled = True
        
        flow_temp = self.controller.preheating.calculate_flow_temp_override()
        
        # Should use living room's load (0.5 * 40 = 20), not guest's (9 * 100 = 900)
        expected_override = 20 * (1.0 / 1800.0) * PREHEATING_TUNING_CONSTANT
        expected_flow = MIN_FLOW_TEMP + expected_override
        
        self.verify(
            abs(flow_temp - expected_flow) < 0.5,
            f"Should use high-pri load only, expected ~{expected_flow}°C, got {flow_temp}°C"
        )
    
    def run_all_tests(self):
        """Run all pre-heating tests."""
        print("\n" + "="*80)
        print("PRE-HEATING TEST SUITE")
        print("="*80)
        
        self.test_1_preheating_not_active_by_default()
        self.test_2_preheating_activation()
        self.test_3_preheating_deactivation_past_time()
        self.test_4_max_thermal_load_single_zone()
        self.test_5_max_thermal_load_multiple_zones()
        self.test_6_max_thermal_load_ignores_low_priority()
        self.test_7_preheating_flow_temp_calculation_30min()
        self.test_8_preheating_flow_temp_aggressive_with_less_time()
        self.test_9_preheating_maxes_out_with_large_thermal_load()
        self.test_10_preheating_ignores_low_priority_in_override()
        
        print("\n" + "="*80)
        print(f"RESULTS: {self.passed} passed, {self.failed} failed")
        print("="*80)
        
        return self.failed == 0


if __name__ == "__main__":
    suite = TestPreheating()
    success = suite.run_all_tests()
    sys.exit(0 if success else 1)
