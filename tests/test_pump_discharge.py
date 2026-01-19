"""
Test Suite for Pump Discharge Controller

Tests cover:
1. Discharge TRV exclusion from boiler calculations
2. Discharge switch activation when boiler shuts off
3. Discharge timeout (5 minutes)
4. Discharge re-activation when boiler needs heat again
5. Discharge state management and reporting
"""

import sys
import asyncio
import time
from pathlib import Path
from typing import Optional

# Add parent directory to path to import component modules
sys.path.insert(0, str(Path(__file__).parent.parent / 'custom_components' / 'multi_trv_heating'))
sys.path.insert(0, str(Path(__file__).parent))

from zone_wrapper import ZoneWrapper
from master_controller import MasterController
from pump_discharge import PumpDischargeController, PUMP_DISCHARGE_TIMEOUT
from test_logger import create_test_logger, TestLogger
from mock_ha import MockHass, MockClimateEntity, MockState


class PumpDischargeTestSuite:
    """Test suite for pump discharge functionality."""
    
    def __init__(self, log_level: str = 'info'):
        """
        Initialize test suite.
        
        Args:
            log_level: Logging level ('warning', 'info', 'debug')
        """
        self.log = create_test_logger(log_level, 'pump_discharge_tests')
        self.passed_tests = 0
        self.failed_tests = 0
    
    async def run_all_tests(self) -> None:
        """Run all pump discharge test cases."""
        try:
            await self.test_discharge_trv_excluded_from_calculation()
            await self.test_discharge_activation_when_boiler_off()
            await self.test_discharge_disabled_when_boiler_on()
            await self.test_discharge_timeout_expires()
            await self.test_discharge_disabled_by_reactivation()
            await self.test_discharge_state_export()
            await self.test_discharge_with_multiple_zones()
            await self.test_discharge_prevents_boiler_shutdown()
            
            self._print_summary()
        except Exception as e:
            self.log.warning(f"Test suite issue: {e}")
            self._print_summary()
    
    async def test_discharge_trv_excluded_from_calculation(self) -> None:
        """
        Test that the discharge TRV is excluded from boiler control calculations.
        
        Scenario:
        - 2 zones: Zone A (high priority) and Zone B (discharge)
        - Zone A has 0% opening (no demand)
        - Zone B (discharge) has 50% opening
        - Expected: Boiler OFF (Zone B is ignored)
        """
        test_name = "Discharge TRV Excluded From Boiler Calculation"
        self.log.test_case(test_name, "Verify that discharge TRV opening does not trigger boiler")
        
        try:
            # Setup zones
            zone_configs = [
                {
                    'entity_id': 'climate.zone_a',
                    'name': 'Zone A',
                    'area': 20.0,
                    'is_high_priority': True,
                    'trv_position_entity_id': 'sensor.zone_a_position',
                },
                {
                    'entity_id': 'climate.zone_b',
                    'name': 'Zone B (Discharge)',
                    'area': 15.0,
                    'is_high_priority': False,
                    'trv_position_entity_id': 'sensor.zone_b_position',
                    'discharge_trv_entity_id': 'climate.zone_b',  # This is the discharge TRV
                }
            ]
            
            self.log.step(1, "Create controller with 2 zones")
            mock_hass = MockHass()
            controller = MasterController(mock_hass, zone_configs)
            
            self.log.step(2, "Configure Zone B as discharge TRV")
            controller.pump_discharge.update_config('climate.zone_b', 'Zone B')
            
            self.log.step(3, "Set Zone A: 0% opening (no demand)")
            controller.zones['climate.zone_a'].update_trv_opening(0.0)
            
            self.log.step(4, "Set Zone B: 50% opening")
            controller.zones['climate.zone_b'].update_trv_opening(50.0)
            
            self.log.step(5, "Calculate boiler command")
            await controller._calculate_and_command()
            
            self.log.step(6, "Verify boiler OFF")
            if controller.current_flow_temp == 5.0:
                self.log.verify(True, f"Boiler OFF (flow_temp=5.0°C)")
                self.log.debug(f"Zone A opening: 0.0%")
                self.log.debug(f"Zone B (discharge) opening: 50.0% (excluded)")
                self.passed_tests += 1
            else:
                self.log.verify(False, f"Expected boiler OFF, got flow_temp={controller.current_flow_temp}°C")
                self.failed_tests += 1
        
        except Exception as e:
            self.log.warning(f"Test failed: {e}")
            self.failed_tests += 1
    
    async def test_discharge_activation_when_boiler_off(self) -> None:
        """
        Test that discharge activates when boiler shuts off and no zones demanding.
        
        Scenario:
        - Zone A (high priority) at 0% opening (boiler OFF)
        - No zones demanding heat
        - Expected: Discharge switch enabled
        """
        test_name = "Discharge Activation When Boiler OFF"
        self.log.test_case(test_name, "Test discharge functionality")
        
        try:
            zone_configs = [
                {
                    'entity_id': 'climate.zone_a',
                    'name': 'Zone A',
                    'area': 20.0,
                    'is_high_priority': True,
                    'trv_position_entity_id': 'sensor.zone_a_position',
                    'discharge_trv_entity_id': 'climate.zone_a',
                }
            ]
            
            mock_hass = MockHass()
            controller = MasterController(mock_hass, zone_configs)
            controller.pump_discharge.update_config('climate.zone_a', 'Zone A')
            
            # Set Zone A: 0% opening (no demand)
            controller.zones['climate.zone_a'].update_trv_opening(0.0)
            
            # Calculate boiler command
            await controller._calculate_and_command()
            
            # Verify: Boiler OFF and discharge activated
            if controller.current_flow_temp == 5.0 and controller.pump_discharge.is_discharging:
                self.log.verify(True, f"✓ Boiler OFF and discharge activated")
                self.log.debug(f"  Flow temp: 5.0°C")
                self.log.debug(f"  Discharge active: True")
                self.passed_tests += 1
            else:
                self.log.warning(f"✗ Expected discharge ON, got:")
                self.log.warning(f"  Flow temp: {controller.current_flow_temp}°C")
                self.log.warning(f"  Discharge active: {controller.pump_discharge.is_discharging}")
                self.failed_tests += 1
        
        except Exception as e:
            self.log.warning(f"✗ Test failed: {e}")
            self.failed_tests += 1
    
    async def test_discharge_disabled_when_boiler_on(self) -> None:
        """
        Test that discharge disables when boiler reactivates.
        
        Scenario:
        - Discharge is active
        - Zone A (high priority) opens to 50%
        - Expected: Boiler ON and discharge disabled
        """
        test_name = "Discharge Disabled When Boiler Activates"
        self.log.test_case(test_name, "Test discharge functionality")
        
        try:
            zone_configs = [
                {
                    'entity_id': 'climate.zone_a',
                    'name': 'Zone A',
                    'area': 20.0,
                    'is_high_priority': True,
                    'trv_position_entity_id': 'sensor.zone_a_position',
                    'discharge_trv_entity_id': 'climate.zone_a',
                }
            ]
            
            mock_hass = MockHass()
            controller = MasterController(mock_hass, zone_configs)
            controller.pump_discharge.update_config('climate.zone_a', 'Zone A')
            
            # Start with zone at 0% (boiler off, discharge on)
            controller.zones['climate.zone_a'].update_trv_opening(0.0)
            await controller._calculate_and_command()
            
            # Verify discharge is active
            if not controller.pump_discharge.is_discharging:
                self.log.warning("✗ Discharge should be active at start")
                self.failed_tests += 1
                return
            
            # Now zone opens (boiler needed)
            # First update temperature so zone has demand
            state = MockState(entity_id='climate.zone_a', state='heating', attributes={'current_temperature': 15.0, 'temperature': 22.0})
            controller.zones['climate.zone_a'].update_from_state(state)
            controller.zones['climate.zone_a'].update_trv_opening(50.0)
            
            # Recalculate
            await controller._calculate_and_command()
            
            # Verify: Boiler ON and discharge disabled
            if controller.current_flow_temp > 5.0 and not controller.pump_discharge.is_discharging:
                self.log.verify(True, f"✓ Boiler ON and discharge disabled")
                self.log.debug(f"  Flow temp: {controller.current_flow_temp}°C")
                self.log.debug(f"  Discharge active: {controller.pump_discharge.is_discharging}")
                self.passed_tests += 1
            else:
                self.log.warning(f"✗ Expected boiler ON and discharge OFF, got:")
                self.log.warning(f"  Flow temp: {controller.current_flow_temp}°C")
                self.log.warning(f"  Discharge active: {controller.pump_discharge.is_discharging}")
                self.failed_tests += 1
        
        except Exception as e:
            self.log.warning(f"✗ Test failed: {e}")
            self.failed_tests += 1
    
    async def test_discharge_timeout_expires(self) -> None:
        """
        Test that discharge timeout expires after 5 minutes.
        
        Scenario:
        - Discharge is active
        - Simulate 5+ minutes passing
        - Expected: Discharge automatically disabled
        """
        test_name = "Discharge Timeout Expires After 5 Minutes"
        self.log.test_case(test_name, "Test discharge functionality")
        
        try:
            zone_configs = [
                {
                    'entity_id': 'climate.zone_a',
                    'name': 'Zone A',
                    'area': 20.0,
                    'is_high_priority': True,
                    'trv_position_entity_id': 'sensor.zone_a_position',
                    'discharge_trv_entity_id': 'climate.zone_a',
                }
            ]
            
            mock_hass = MockHass()
            controller = MasterController(mock_hass, zone_configs)
            controller.pump_discharge.update_config('climate.zone_a', 'Zone A')
            
            # Activate discharge
            controller.zones['climate.zone_a'].update_trv_opening(0.0)
            await controller._calculate_and_command()
            
            if not controller.pump_discharge.is_discharging:
                self.log.warning("✗ Discharge should be active")
                self.failed_tests += 1
                return
            
            # Simulate time passing by manually setting discharge_start_time
            # Move it back by (TIMEOUT + 10 seconds)
            controller.pump_discharge.discharge_start_time = time.time() - (PUMP_DISCHARGE_TIMEOUT + 10)
            
            # Zone still has no demand, recalculate
            await controller._calculate_and_command()
            
            # Verify: Discharge should be disabled due to timeout
            if not controller.pump_discharge.is_discharging:
                self.log.verify(True, f"✓ Discharge correctly disabled after timeout")
                elapsed = time.time() - (controller.pump_discharge.discharge_start_time + PUMP_DISCHARGE_TIMEOUT + 10)
                self.log.debug(f"  Timeout: {PUMP_DISCHARGE_TIMEOUT}s")
                self.passed_tests += 1
            else:
                self.log.warning(f"✗ Expected discharge disabled, got active")
                self.failed_tests += 1
        
        except Exception as e:
            self.log.warning(f"✗ Test failed: {e}")
            self.failed_tests += 1
    
    async def test_discharge_disabled_by_reactivation(self) -> None:
        """
        Test that discharge disables when boiler reactivates before timeout.
        
        Scenario:
        - Discharge is active
        - Zone demands heat again (before timeout)
        - Expected: Discharge disabled immediately
        """
        test_name = "Discharge Disabled By Boiler Reactivation Before Timeout"
        self.log.test_case(test_name, "Test discharge functionality")
        
        try:
            zone_configs = [
                {
                    'entity_id': 'climate.zone_a',
                    'name': 'Zone A',
                    'area': 20.0,
                    'is_high_priority': True,
                    'trv_position_entity_id': 'sensor.zone_a_position',
                    'discharge_trv_entity_id': 'climate.zone_a',
                }
            ]
            
            mock_hass = MockHass()
            controller = MasterController(mock_hass, zone_configs)
            controller.pump_discharge.update_config('climate.zone_a', 'Zone A')
            
            # Start discharge
            controller.zones['climate.zone_a'].update_trv_opening(0.0)
            await controller._calculate_and_command()
            
            if not controller.pump_discharge.is_discharging:
                self.log.warning("✗ Discharge should be active")
                self.failed_tests += 1
                return
            
            # Zone demands heat (still discharge TRV, but now demanding)
            state = MockState(entity_id='climate.zone_a', state='heating', attributes={'current_temperature': 15.0, 'temperature': 22.0})
            controller.zones['climate.zone_a'].update_from_state(state)
            controller.zones['climate.zone_a'].update_trv_opening(80.0)  # High demand
            
            # Recalculate
            await controller._calculate_and_command()
            
            # Verify: Discharge disabled and boiler on
            if not controller.pump_discharge.is_discharging and controller.current_flow_temp > 5.0:
                self.log.verify(True, f"✓ Discharge disabled by reactivation")
                self.log.debug(f"  Flow temp: {controller.current_flow_temp}°C")
                self.log.debug(f"  Discharge active: {controller.pump_discharge.is_discharging}")
                self.passed_tests += 1
            else:
                self.log.warning(f"✗ Expected discharge OFF and boiler ON, got:")
                self.log.warning(f"  Flow temp: {controller.current_flow_temp}°C")
                self.log.warning(f"  Discharge active: {controller.pump_discharge.is_discharging}")
                self.failed_tests += 1
        
        except Exception as e:
            self.log.warning(f"✗ Test failed: {e}")
            self.failed_tests += 1
    
    async def test_discharge_state_export(self) -> None:
        """
        Test that discharge state is properly exported for monitoring.
        
        Scenario:
        - Discharge is active
        - Export controller state
        - Expected: Discharge state visible in export
        """
        test_name = "Discharge State Export"
        self.log.test_case(test_name, "Test discharge functionality")
        
        try:
            zone_configs = [
                {
                    'entity_id': 'climate.zone_a',
                    'name': 'Zone A',
                    'area': 20.0,
                    'is_high_priority': True,
                    'trv_position_entity_id': 'sensor.zone_a_position',
                    'discharge_trv_entity_id': 'climate.zone_a',
                }
            ]
            
            mock_hass = MockHass()
            controller = MasterController(mock_hass, zone_configs)
            controller.pump_discharge.update_config('climate.zone_a', 'Zone A')
            
            # Activate discharge
            controller.zones['climate.zone_a'].update_trv_opening(0.0)
            await controller._calculate_and_command()
            
            # Export state
            state = controller.get_controller_state()
            
            # Verify discharge state in export
            if 'pump_discharge' in state and state['pump_discharge']['is_discharging']:
                self.log.verify(True, f"✓ Discharge state correctly exported")
                self.log.debug(f"  Entity ID: {state['pump_discharge']['discharge_trv_entity_id']}")
                self.log.debug(f"  Name: {state['pump_discharge']['discharge_trv_name']}")
                self.log.debug(f"  Is Discharging: {state['pump_discharge']['is_discharging']}")
                self.log.debug(f"  Timeout: {state['pump_discharge']['timeout_seconds']}s")
                self.passed_tests += 1
            else:
                self.log.warning(f"✗ Discharge state not properly exported")
                self.failed_tests += 1
        
        except Exception as e:
            self.log.warning(f"✗ Test failed: {e}")
            self.failed_tests += 1
    
    async def test_discharge_with_multiple_zones(self) -> None:
        """
        Test discharge with multiple zones (one is discharge, others normal).
        
        Scenario:
        - Zone A: High priority, 0% opening
        - Zone B: Low priority, 0% opening
        - Zone C: Low priority, discharge TRV, 100% opening
        - Expected: Boiler OFF (A and B excluded, C is discharge), discharge ON
        """
        test_name = "Discharge With Multiple Zones"
        self.log.test_case(test_name, "Test discharge functionality")
        
        try:
            zone_configs = [
                {
                    'entity_id': 'climate.zone_a',
                    'name': 'Zone A',
                    'area': 20.0,
                    'is_high_priority': True,
                    'trv_position_entity_id': 'sensor.zone_a_position',
                },
                {
                    'entity_id': 'climate.zone_b',
                    'name': 'Zone B',
                    'area': 20.0,
                    'is_high_priority': False,
                    'trv_position_entity_id': 'sensor.zone_b_position',
                },
                {
                    'entity_id': 'climate.zone_c',
                    'name': 'Zone C (Discharge)',
                    'area': 20.0,
                    'is_high_priority': False,
                    'trv_position_entity_id': 'sensor.zone_c_position',
                    'discharge_trv_entity_id': 'climate.zone_c',
                }
            ]
            
            mock_hass = MockHass()
            controller = MasterController(mock_hass, zone_configs)
            controller.pump_discharge.update_config('climate.zone_c', 'Zone C')
            
            # Set all zones to 0% except discharge (100%)
            controller.zones['climate.zone_a'].update_trv_opening(0.0)
            controller.zones['climate.zone_b'].update_trv_opening(0.0)
            controller.zones['climate.zone_c'].update_trv_opening(100.0)
            
            # Calculate
            await controller._calculate_and_command()
            
            # Verify: Boiler OFF (discharge excluded) and discharge ON
            if controller.current_flow_temp == 5.0 and controller.pump_discharge.is_discharging:
                self.log.verify(True, f"✓ Discharge works with multiple zones")
                self.log.debug(f"  Zone A: 0.0%")
                self.log.debug(f"  Zone B: 0.0%")
                self.log.debug(f"  Zone C (discharge): 100.0% (excluded)")
                self.log.debug(f"  Boiler: OFF, Discharge: ON")
                self.passed_tests += 1
            else:
                self.log.warning(f"✗ Expected boiler OFF and discharge ON, got:")
                self.log.warning(f"  Flow temp: {controller.current_flow_temp}°C")
                self.log.warning(f"  Discharge active: {controller.pump_discharge.is_discharging}")
                self.failed_tests += 1
        
        except Exception as e:
            self.log.warning(f"✗ Test failed: {e}")
            self.failed_tests += 1
    
    async def test_discharge_prevents_boiler_shutdown(self) -> None:
        """
        Test that discharge TRV prevents immediate boiler shutdown.
        
        Scenario:
        - Zone A: High priority, 30% opening (triggers boiler)
        - Zone B: Discharge TRV, configured
        - Zone A closes to 0%
        - Expected: Discharge activates, preventing immediate shutdown
        """
        test_name = "Discharge Prevents Immediate Boiler Shutdown"
        self.log.test_case(test_name, "Test discharge functionality")
        
        try:
            zone_configs = [
                {
                    'entity_id': 'climate.zone_a',
                    'name': 'Zone A',
                    'area': 20.0,
                    'is_high_priority': True,
                    'trv_position_entity_id': 'sensor.zone_a_position',
                },
                {
                    'entity_id': 'climate.zone_b',
                    'name': 'Zone B (Discharge)',
                    'area': 20.0,
                    'is_high_priority': False,
                    'trv_position_entity_id': 'sensor.zone_b_position',
                    'discharge_trv_entity_id': 'climate.zone_b',
                }
            ]
            
            mock_hass = MockHass()
            controller = MasterController(mock_hass, zone_configs)
            controller.pump_discharge.update_config('climate.zone_b', 'Zone B')
            
            # Zone A opens, boiler should run
            state = MockState(entity_id='climate.zone_a', state='heating', attributes={'current_temperature': 15.0, 'temperature': 22.0})
            controller.zones['climate.zone_a'].update_from_state(state)
            controller.zones['climate.zone_a'].update_trv_opening(30.0)
            
            await controller._calculate_and_command()
            
            if controller.current_flow_temp <= 5.0:
                self.log.warning("✗ Boiler should be ON with Zone A at 30%")
                self.failed_tests += 1
                return
            
            boiler_temp_with_demand = controller.current_flow_temp
            self.log.debug(f"Boiler ON: flow_temp={boiler_temp_with_demand}°C")
            
            # Zone A closes
            controller.zones['climate.zone_a'].update_trv_opening(0.0)
            await controller._calculate_and_command()
            
            # Verify: Discharge activates
            if controller.pump_discharge.is_discharging and controller.current_flow_temp == 5.0:
                self.log.verify(True, f"✓ Discharge activates after boiler shutdown")
                self.log.debug(f"  Zone A: 0.0% (closed)")
                self.log.debug(f"  Boiler: OFF")
                self.log.debug(f"  Discharge: ON")
                self.passed_tests += 1
            else:
                self.log.warning(f"✗ Expected discharge ON, got:")
                self.log.warning(f"  Flow temp: {controller.current_flow_temp}°C")
                self.log.warning(f"  Discharge active: {controller.pump_discharge.is_discharging}")
                self.failed_tests += 1
        
        except Exception as e:
            self.log.warning(f"✗ Test failed: {e}")
            self.failed_tests += 1
    
    def _print_summary(self) -> None:
        """Print test summary."""
        total = self.passed_tests + self.failed_tests
        self.log.summary(self.passed_tests, self.failed_tests, total)


async def main():
    """Run the test suite."""
    suite = PumpDischargeTestSuite(log_level='debug')
    await suite.run_all_tests()


if __name__ == "__main__":
    asyncio.run(main())
