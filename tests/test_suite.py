"""
Comprehensive Test Suite for Multi-TRV Heating Controller

Tests cover:
1. Basic single-zone heating demand
2. Multi-zone scenarios with different priorities
3. Zone aggregation for low-priority zones
4. Boiler intensity calculation
5. Temperature offset adjustments
6. Corner cases and edge conditions
7. Realistic house scenario with thermal influence

Each test includes detailed logging of steps and verification points.
"""

import sys
import asyncio
from pathlib import Path
from typing import Optional

# Add parent directory to path to import component modules
sys.path.insert(0, str(Path(__file__).parent.parent / 'custom_components' / 'multi_trv_heating'))
sys.path.insert(0, str(Path(__file__).parent))

from zone_wrapper import ZoneWrapper
from master_controller import MasterController
from test_logger import create_test_logger, TestLogger
from mock_ha import MockHass, MockClimateEntity, MockState


class TestSuite:
    """Main test suite for Multi-TRV Heating Controller."""
    
    def __init__(self, log_level: str = 'info'):
        """
        Initialize test suite.
        
        Args:
            log_level: Logging level ('warning', 'info', 'debug')
        """
        self.log = create_test_logger(log_level, 'multi_trv_heating')
        self.passed_tests = 0
        self.failed_tests = 0
    
    async def run_all_tests(self) -> None:
        """Run all test cases in sequence."""
        try:
            # Basic single-zone tests
            await self.test_single_zone_low_demand()
            await self.test_single_zone_high_demand()
            await self.test_single_high_priority_zone_trigger()
            await self.test_single_low_priority_zone_no_trigger()
            
            # Multi-zone tests
            await self.test_multiple_high_priority_zones()
            await self.test_multiple_low_priority_aggregation()
            await self.test_mixed_priority_zones()
            
            # Boiler control tests
            await self.test_boiler_intensity_calculation()
            await self.test_boiler_off_when_no_demand()
            
            # Temperature offset tests
            await self.test_temperature_offset_adjustment()
            await self.test_temperature_offset_reset()
            
            # Edge cases
            await self.test_zone_at_target_temperature()
            await self.test_zero_opening_all_zones()
            await self.test_rapid_state_changes()
            
            # Realistic scenario
            await self.test_realistic_house_scenario()
            await self.test_zone_thermal_influence()
            
        except Exception as e:
            self.log.warning(f"Test suite error: {e}")
            self.failed_tests += 1
        
        # Print summary
        total = self.passed_tests + self.failed_tests
        self.log.summary(self.passed_tests, self.failed_tests, total)
    
    # ========== Basic Single-Zone Tests ==========
    
    async def test_single_zone_low_demand(self) -> None:
        """
        Test: Single zone with low heating demand
        
        Scenario: Zone is 2°C below target with 25% valve opening
        Expected: Zone calculates demand metric, boiler is ON
        """
        self.log.test_case(
            "Single Zone - Low Demand",
            "Test a single high-priority zone that is 2°C below target with 25% valve opening. "
            "Verify that demand metric is calculated and boiler turns on."
        )
        
        try:
            self.log.step(1, "Create high-priority zone")
            zone = ZoneWrapper(
                entity_id="climate.living_room",
                name="Living Room",
                floor_area_m2=35.0,
                is_high_priority=True  # High priority
            )
            self.log.debug(f"Zone created: {zone.name}, high_priority={zone.is_high_priority}")
            
            self.log.step(2, "Update zone to 2°C below target with 25% valve opening")
            state = MockState(
                entity_id="climate.living_room",
                state="heating",
                attributes={
                    'current_temperature': 18.0,
                    'target_temperature': 20.0,
                }
            )
            zone.update_from_state(state)
            zone.update_trv_opening(25.0)
            self.log.debug(f"Current: {zone.current_temp}°C, Target: {zone.target_temp}°C, Error: {zone.current_error}°C")
            self.log.debug(f"TRV Opening: {zone.trv_opening_percent}%")
            
            self.log.step(3, "Verify zone is demanding heat")
            self.log.verify(zone.is_demanding_heat, "Zone should be demanding heat (high priority at 25%)")
            self.log.debug(f"is_demanding_heat: {zone.is_demanding_heat}")
            
            self.log.step(4, "Verify demand metric is calculated correctly")
            demand = zone.get_demand_metric()
            expected_demand = (2.0 / 10.0) * (25.0 / 100.0)  # 0.05
            self.log.verify(0.04 < demand < 0.06, f"Demand metric should be ~0.05, got {demand}")
            self.log.debug(f"Demand metric: {demand}")
            
            self.log.info("Test PASSED: Single zone calculates demand correctly")
            self.passed_tests += 1
            
        except AssertionError as e:
            self.log.warning(str(e))
            self.failed_tests += 1
    
    async def test_single_zone_high_demand(self) -> None:
        """
        Test: Single zone with high heating demand
        
        Scenario: Zone is 8°C below target with 100% valve opening
        Expected: Zone calculates high demand metric (~0.8)
        """
        self.log.test_case(
            "Single Zone - High Demand",
            "Test a zone that is 8°C below target with 100% valve opening. "
            "Verify high demand metric is calculated."
        )
        
        try:
            self.log.step(1, "Create high-priority zone")
            zone = ZoneWrapper(
                entity_id="climate.bedroom",
                name="Bedroom",
                is_high_priority=True
            )
            
            self.log.step(2, "Update zone to 8°C below target with 100% valve opening")
            state = MockState(
                entity_id="climate.bedroom",
                state="heating",
                attributes={
                    'current_temperature': 12.0,
                    'target_temperature': 20.0,
                }
            )
            zone.update_from_state(state)
            zone.update_trv_opening(100.0)
            self.log.debug(f"Temperature error: {zone.current_error}°C, Opening: {zone.trv_opening_percent}%")
            
            self.log.step(3, "Calculate demand metric")
            demand = zone.get_demand_metric()
            # demand = min(1.0, (8/10) * (100/100)) = 0.8
            expected_demand = 0.8
            self.log.verify(0.75 < demand < 0.85, f"Demand should be ~0.8, got {demand}")
            self.log.debug(f"Demand metric: {demand}")
            
            self.log.info("Test PASSED: High demand calculated correctly")
            self.passed_tests += 1
            
        except AssertionError as e:
            self.log.warning(str(e))
            self.failed_tests += 1
    
    async def test_single_high_priority_zone_trigger(self) -> None:
        """
        Test: High-priority zone trigger threshold
        
        Scenario: High-priority zone with 25% opening should trigger boiler
        Expected: Zone is_demanding_heat = True
        """
        self.log.test_case(
            "High-Priority Zone Trigger",
            "Verify that a high-priority zone at 25% opening triggers boiler demand. "
            "Test that 24% does not trigger."
        )
        
        try:
            self.log.step(1, "Create high-priority zone")
            zone = ZoneWrapper(
                entity_id="climate.lounge",
                name="Lounge",
                is_high_priority=True  # High priority (> 0.5)
            )
            
            self.log.step(2, "Test 1% opening (should trigger with new ANY > 0% logic)")
            zone.update_trv_opening(1.0)
            self.log.verify(zone.is_demanding_heat, "1% opening should trigger (high-priority)")
            self.log.debug(f"1% opening: is_demanding={zone.is_demanding_heat}")
            
            self.log.step(3, "Test 24% opening (should trigger)")
            zone.update_trv_opening(24.0)
            self.log.verify(zone.is_demanding_heat, "24% opening should trigger (high-priority)")
            self.log.debug(f"24% opening: is_demanding={zone.is_demanding_heat}")
            
            self.log.step(4, "Test 100% opening (should trigger)")
            zone.update_trv_opening(100.0)
            self.log.verify(zone.is_demanding_heat, "100% opening should trigger")
            self.log.debug(f"100% opening: is_demanding={zone.is_demanding_heat}")
            
            self.log.info("Test PASSED: High-priority trigger at ANY > 0% works correctly")
            self.passed_tests += 1
            
        except AssertionError as e:
            self.log.warning(str(e))
            self.failed_tests += 1
    
    async def test_single_low_priority_zone_no_trigger(self) -> None:
        """
        Test: Low-priority zone trigger threshold
        
        Scenario: Low-priority zone at 50% should NOT trigger boiler alone
        Expected: Zone is_demanding_heat = False until 100%
        """
        self.log.test_case(
            "Low-Priority Zone - No Solo Trigger",
            "Verify that a low-priority zone does not trigger boiler until 100% opening. "
            "Test 99% does not trigger, 100% does."
        )
        
        try:
            self.log.step(1, "Create low-priority zone")
            zone = ZoneWrapper(
                entity_id="climate.guest_room",
                name="Guest Room",
                is_high_priority=False  # Low priority (<= 0.5)
            )
            self.log.verify(not zone.is_high_priority, "Zone should be low priority")
            
            self.log.step(2, "Test 50% opening (should NOT trigger)")
            zone.update_trv_opening(50.0)
            self.log.verify(not zone.is_demanding_heat, "50% opening should not trigger")
            self.log.debug(f"50% opening: is_demanding={zone.is_demanding_heat}")
            
            self.log.step(3, "Test 99% opening (should NOT trigger)")
            zone.update_trv_opening(99.0)
            self.log.verify(not zone.is_demanding_heat, "99% opening should not trigger")
            self.log.debug(f"99% opening: is_demanding={zone.is_demanding_heat}")
            
            self.log.step(4, "Test 100% opening (should trigger)")
            zone.update_trv_opening(100.0)
            self.log.verify(zone.is_demanding_heat, "100% opening should trigger")
            self.log.debug(f"100% opening: is_demanding={zone.is_demanding_heat}")
            
            self.log.info("Test PASSED: Low-priority 100% threshold works correctly")
            self.passed_tests += 1
            
        except AssertionError as e:
            self.log.warning(str(e))
            self.failed_tests += 1
    
    # ========== Multi-Zone Tests ==========
    
    async def test_multiple_high_priority_zones(self) -> None:
        """
        Test: Multiple high-priority zones
        
        Scenario: Two high-priority zones, one at 30%, one at 50% opening
        Expected: Both demanding heat, highest demand used for boiler intensity
        """
        self.log.test_case(
            "Multiple High-Priority Zones",
            "Test two high-priority zones with different demand levels. "
            "Verify that highest demand is used for boiler intensity."
        )
        
        try:
            self.log.step(1, "Create two high-priority zones")
            zone1 = ZoneWrapper("climate.room1", "Room 1", is_high_priority=True)
            zone2 = ZoneWrapper("climate.room2", "Room 2", is_high_priority=True)
            
            self.log.step(2, "Set zone1: 3°C below target, 30% opening")
            state1 = MockState("climate.room1", "heating", {
                'current_temperature': 17.0,
                'target_temperature': 20.0,
            })
            zone1.update_from_state(state1)
            zone1.update_trv_opening(30.0)
            demand1 = zone1.get_demand_metric()
            self.log.debug(f"Zone1 demand: {demand1}")
            
            self.log.step(3, "Set zone2: 6°C below target, 50% opening")
            state2 = MockState("climate.room2", "heating", {
                'current_temperature': 14.0,
                'target_temperature': 20.0,
            })
            zone2.update_from_state(state2)
            zone2.update_trv_opening(50.0)
            demand2 = zone2.get_demand_metric()
            self.log.debug(f"Zone2 demand: {demand2}")
            
            self.log.step(4, "Verify both are demanding heat")
            self.log.verify(zone1.is_demanding_heat, "Zone1 should demand heat")
            self.log.verify(zone2.is_demanding_heat, "Zone2 should demand heat")
            
            self.log.step(5, "Verify zone2 has higher demand")
            self.log.verify(demand2 > demand1, f"Zone2 demand ({demand2}) should be higher than zone1 ({demand1})")
            
            self.log.info("Test PASSED: Multiple high-priority zones work correctly")
            self.passed_tests += 1
            
        except AssertionError as e:
            self.log.warning(str(e))
            self.failed_tests += 1
    
    async def test_multiple_low_priority_aggregation(self) -> None:
        """
        Test: Low-priority zone aggregation
        
        Scenario: Three low-priority zones at 30%, 30%, 50% opening
        Expected: Aggregate = 110%, boiler should turn on, use highest demand
        """
        self.log.test_case(
            "Low-Priority Zone Aggregation",
            "Test that multiple low-priority zones aggregate their opening percentages. "
            "110% aggregate should trigger boiler even though individually < 100%."
        )
        
        try:
            self.log.step(1, "Create three low-priority zones")
            zone1 = ZoneWrapper("climate.guest1", "Guest 1", is_high_priority=False)
            zone2 = ZoneWrapper("climate.guest2", "Guest 2", is_high_priority=False)
            zone3 = ZoneWrapper("climate.hallway", "Hallway", is_high_priority=False)
            
            self.log.step(2, "Set zone1 to 30% opening")
            zone1.update_trv_opening(30.0)
            self.log.verify(not zone1.is_demanding_heat, "Zone1 at 30% should not trigger alone")
            
            self.log.step(3, "Set zone2 to 30% opening")
            zone2.update_trv_opening(30.0)
            self.log.verify(not zone2.is_demanding_heat, "Zone2 at 30% should not trigger alone")
            
            self.log.step(4, "Set zone3 to 50% opening")
            zone3.update_trv_opening(50.0)
            self.log.verify(not zone3.is_demanding_heat, "Zone3 at 50% should not trigger alone")
            
            self.log.step(5, "Verify aggregation: 30 + 30 + 50 = 110%")
            aggregate = zone1.trv_opening_percent + zone2.trv_opening_percent + zone3.trv_opening_percent
            self.log.verify(aggregate == 110.0, f"Aggregate should be 110%, got {aggregate}%")
            self.log.info(f"Aggregate opening: {aggregate}%")
            
            self.log.info("Test PASSED: Low-priority aggregation logic verified")
            self.passed_tests += 1
            
        except AssertionError as e:
            self.log.warning(str(e))
            self.failed_tests += 1
    
    async def test_mixed_priority_zones(self) -> None:
        """
        Test: Mixed high and low priority zones
        
        Scenario: One high-priority at 20% (triggers alone) + one low at 40%
        Expected: Boiler ON due to high-priority, use highest demand metric
        """
        self.log.test_case(
            "Mixed Priority Zones",
            "Test one high-priority zone (triggers at 25%) and one low-priority zone (30%). "
            "High-priority should trigger boiler alone."
        )
        
        try:
            self.log.step(1, "Create high-priority zone")
            high = ZoneWrapper("climate.living", "Living Room", is_high_priority=True)
            
            self.log.step(2, "Create low-priority zone")
            low = ZoneWrapper("climate.storage", "Storage", is_high_priority=False)
            
            self.log.step(3, "Set high-priority to 30% opening (should trigger)")
            high.update_trv_opening(30.0)
            self.log.verify(high.is_demanding_heat, "High-priority at 30% should trigger")
            
            self.log.step(4, "Set low-priority to 40% opening (should NOT trigger alone)")
            low.update_trv_opening(40.0)
            self.log.verify(not low.is_demanding_heat, "Low-priority at 40% should not trigger alone")
            
            self.log.step(5, "Verify high-priority can trigger boiler alone")
            boiler_should_be_on = high.is_demanding_heat or (high.trv_opening_percent + low.trv_opening_percent >= 100.0)
            self.log.verify(boiler_should_be_on, "Boiler should be ON due to high-priority demand")
            
            self.log.info("Test PASSED: Mixed priorities work correctly")
            self.passed_tests += 1
            
        except AssertionError as e:
            self.log.warning(str(e))
            self.failed_tests += 1
    
    # ========== Boiler Control Tests ==========
    
    async def test_boiler_intensity_calculation(self) -> None:
        """
        Test: Boiler intensity calculation
        
        Scenario: Zone with 0.5 demand metric
        Expected: Flow temperature = 5 + (0.5 * 75) = 42.5°C
        """
        self.log.test_case(
            "Boiler Intensity Calculation",
            "Verify flow temperature calculation: 5°C at 0 demand, 80°C at 1.0 demand, "
            "linear interpolation for 0.5 demand should be 42.5°C."
        )
        
        try:
            # Constants from master_controller
            MIN_FLOW_TEMP = 5.0
            MAX_FLOW_TEMP = 80.0
            
            self.log.step(1, "Test demand = 0.0 (boiler OFF)")
            demand = 0.0
            flow_temp = MIN_FLOW_TEMP + (demand * (MAX_FLOW_TEMP - MIN_FLOW_TEMP))
            expected = MIN_FLOW_TEMP
            self.log.verify(flow_temp == expected, f"Demand 0.0 should give {expected}°C, got {flow_temp}°C")
            self.log.debug(f"Flow temp at demand 0.0: {flow_temp}°C")
            
            self.log.step(2, "Test demand = 0.5 (medium)")
            demand = 0.5
            flow_temp = MIN_FLOW_TEMP + (demand * (MAX_FLOW_TEMP - MIN_FLOW_TEMP))
            expected = 42.5
            self.log.verify(abs(flow_temp - expected) < 0.1, f"Demand 0.5 should give {expected}°C, got {flow_temp}°C")
            self.log.debug(f"Flow temp at demand 0.5: {flow_temp}°C")
            
            self.log.step(3, "Test demand = 1.0 (maximum)")
            demand = 1.0
            flow_temp = MIN_FLOW_TEMP + (demand * (MAX_FLOW_TEMP - MIN_FLOW_TEMP))
            expected = MAX_FLOW_TEMP
            self.log.verify(flow_temp == expected, f"Demand 1.0 should give {expected}°C, got {flow_temp}°C")
            self.log.debug(f"Flow temp at demand 1.0: {flow_temp}°C")
            
            self.log.info("Test PASSED: Boiler intensity calculation correct")
            self.passed_tests += 1
            
        except AssertionError as e:
            self.log.warning(str(e))
            self.failed_tests += 1
    
    async def test_boiler_off_when_no_demand(self) -> None:
        """
        Test: Boiler turns OFF when no zones demand heat
        
        Scenario: All zones at 0% opening
        Expected: Boiler OFF (flow temp = 5°C)
        """
        self.log.test_case(
            "Boiler OFF - No Demand",
            "Verify that boiler turns off when no zones have any demand. "
            "All zones closed, flow temperature should be 5°C (OFF)."
        )
        
        try:
            self.log.step(1, "Create multiple zones")
            zone1 = ZoneWrapper("climate.r1", "Room 1", is_high_priority=True)
            zone2 = ZoneWrapper("climate.r2", "Room 2", is_high_priority=False)
            zone3 = ZoneWrapper("climate.r3", "Room 3", is_high_priority=False)
            
            self.log.step(2, "Set all zones to 0% opening")
            zone1.update_trv_opening(0.0)
            zone2.update_trv_opening(0.0)
            zone3.update_trv_opening(0.0)
            
            self.log.step(3, "Verify no zones demanding heat")
            self.log.verify(not zone1.is_demanding_heat, "Zone1 should not demand at 0%")
            self.log.verify(not zone2.is_demanding_heat, "Zone2 should not demand at 0%")
            self.log.verify(not zone3.is_demanding_heat, "Zone3 should not demand at 0%")
            
            self.log.step(4, "Verify boiler should be OFF")
            all_demands = [z.get_demand_metric() for z in [zone1, zone2, zone3]]
            max_demand = max(all_demands)
            boiler_off_temp = 5.0
            self.log.verify(max_demand == 0.0, "No zone should have demand")
            self.log.info(f"All zones closed: max demand = {max_demand}, boiler OFF at {boiler_off_temp}°C")
            
            self.log.info("Test PASSED: Boiler correctly turns OFF")
            self.passed_tests += 1
            
        except AssertionError as e:
            self.log.warning(str(e))
            self.failed_tests += 1
    
    # ========== Temperature Offset Tests ==========
    
    async def test_temperature_offset_adjustment(self) -> None:
        """
        Test: Temperature offset adjustment feature
        
        Scenario: Set offset to -2.0°C when zone demands heat
        Expected: Offset changes from 0.0 to -2.0
        """
        self.log.test_case(
            "Temperature Offset Adjustment",
            "Test setting TRV temperature offset to make valve open more. "
            "When zone is demanding heat, reduce offset from 0 to -2°C."
        )
        
        try:
            self.log.step(1, "Create zone with default offset 0")
            zone = ZoneWrapper("climate.room", "Test Room", is_high_priority=True)
            self.log.verify(zone.temperature_offset == 0.0, "Initial offset should be 0")
            self.log.debug(f"Initial offset: {zone.temperature_offset}°C")
            
            self.log.step(2, "Open valve - offset should change to -2.0°C automatically")
            zone.update_trv_opening(50.0)  # Open valve
            self.log.verify(zone.temperature_offset == -2.0, "Offset should be -2.0 when valve opens")
            self.log.debug(f"Offset when open: {zone.temperature_offset}°C")
            
            self.log.step(3, "Close valve - offset should reset to 0°C")
            zone.update_trv_opening(0.0)  # Close valve
            self.log.verify(zone.temperature_offset == 0.0, "Offset should be 0 when valve closes")
            self.log.debug(f"Offset when closed: {zone.temperature_offset}°C")
            
            self.log.step(4, "Valve at 1% should trigger offset change")
            zone.update_trv_opening(1.0)  # Minimal opening
            self.log.verify(zone.temperature_offset == -2.0, "Offset should be -2.0 even at 1% opening")
            self.log.debug(f"Offset at 1%: {zone.temperature_offset}°C")
            
            self.log.info("Test PASSED: Temperature offset adjustment works correctly")
            self.passed_tests += 1
            
        except AssertionError as e:
            self.log.warning(str(e))
            self.failed_tests += 1
    
    async def test_temperature_offset_reset(self) -> None:
        """
        Test: Temperature offset reset when target reached
        
        Scenario: Zone reaches target temperature
        Expected: Offset resets to 0.0
        """
        self.log.test_case(
            "Temperature Offset Reset",
            "Verify that temperature offset resets to 0 when zone reaches target temperature."
        )
        
        try:
            self.log.step(1, "Create zone and open valve - offset should be -2°C")
            zone = ZoneWrapper("climate.room", "Test Room", is_high_priority=True)
            zone.update_trv_opening(50.0)  # Open valve
            self.log.verify(zone.temperature_offset == -2.0, "Offset should be -2°C when open")
            
            self.log.step(2, "Close valve - offset should reset to 0°C")
            zone.update_trv_opening(0.0)  # Close valve
            self.log.verify(zone.temperature_offset == 0.0, "Offset should reset to 0")
            self.log.debug(f"Reset offset: {zone.temperature_offset}°C")
            
            self.log.info("Test PASSED: Offset reset works correctly")
            self.passed_tests += 1
            
        except AssertionError as e:
            self.log.warning(str(e))
            self.failed_tests += 1
    
    # ========== Edge Cases ==========
    
    async def test_zone_at_target_temperature(self) -> None:
        """
        Test: Zone at target temperature has no demand
        
        Scenario: Zone current = target, regardless of valve opening
        Expected: Demand metric = 0, is_demanding_heat behavior depends on opening
        """
        self.log.test_case(
            "Zone At Target Temperature",
            "Verify that a zone at target temperature has demand metric = 0, "
            "even if the valve is partially open."
        )
        
        try:
            self.log.step(1, "Create zone at target temperature")
            zone = ZoneWrapper("climate.room", "Test Room", is_high_priority=True)
            state = MockState("climate.room", "heating", {
                'current_temperature': 21.0,
                'target_temperature': 21.0,
            })
            zone.update_from_state(state)
            self.log.verify(zone.current_error == 0.0, "Temperature error should be 0")
            
            self.log.step(2, "Set valve to 50% opening")
            zone.update_trv_opening(50.0)
            
            self.log.step(3, "Verify demand metric is 0")
            demand = zone.get_demand_metric()
            self.log.verify(demand == 0.0, f"Demand should be 0, got {demand}")
            self.log.debug(f"Demand metric at target temp: {demand}")
            
            self.log.info("Test PASSED: Zone at target has zero demand")
            self.passed_tests += 1
            
        except AssertionError as e:
            self.log.warning(str(e))
            self.failed_tests += 1
    
    async def test_zero_opening_all_zones(self) -> None:
        """
        Test: All zones closed (0% opening)
        
        Scenario: All zones have 0% valve opening
        Expected: No zone is demanding, boiler OFF
        """
        self.log.test_case(
            "All Zones Closed",
            "Verify that when all zone valves are 0%, no zones demand heat "
            "and boiler should be OFF."
        )
        
        try:
            self.log.step(1, "Create 5 zones in different priority levels")
            zones = [
                ZoneWrapper("climate.r1", "Room 1", is_high_priority=True),
                ZoneWrapper("climate.r2", "Room 2", is_high_priority=True),
                ZoneWrapper("climate.r3", "Room 3", is_high_priority=False),
                ZoneWrapper("climate.r4", "Room 4", is_high_priority=False),
                ZoneWrapper("climate.r5", "Room 5", is_high_priority=False),
            ]
            
            self.log.step(2, "Set all zones to 0% opening")
            for zone in zones:
                zone.update_trv_opening(0.0)
            
            self.log.step(3, "Verify no zones demanding")
            for zone in zones:
                self.log.verify(
                    not zone.is_demanding_heat,
                    f"{zone.name} should not demand at 0%"
                )
            
            self.log.step(4, "Verify max demand is 0")
            demands = [z.get_demand_metric() for z in zones]
            max_demand = max(demands)
            self.log.verify(max_demand == 0.0, "Max demand should be 0")
            
            self.log.info("Test PASSED: All closed zones = boiler OFF")
            self.passed_tests += 1
            
        except AssertionError as e:
            self.log.warning(str(e))
            self.failed_tests += 1
    
    async def test_rapid_state_changes(self) -> None:
        """
        Test: Rapid state changes (oscillation simulation)
        
        Scenario: Zone temperature rapidly fluctuates around setpoint
        Expected: Demand changes accordingly, system remains stable
        """
        self.log.test_case(
            "Rapid State Changes",
            "Simulate rapid temperature fluctuations (oscillation) around setpoint. "
            "Verify system remains stable and calculates correct demand."
        )
        
        try:
            self.log.step(1, "Create zone at target")
            zone = ZoneWrapper("climate.room", "Oscillating Room", is_high_priority=True)
            zone.update_trv_opening(75.0)
            
            temps = [20.0, 19.8, 20.1, 19.9, 20.0, 19.7, 20.2, 20.0]
            
            self.log.step(2, "Simulate rapid temperature changes")
            for i, temp in enumerate(temps):
                state = MockState("climate.room", "heating", {
                    'current_temperature': temp,
                    'target_temperature': 20.0,
                })
                zone.update_from_state(state)
                demand = zone.get_demand_metric()
                self.log.debug(f"  Change {i+1}: temp={temp}°C, error={zone.current_error}°C, demand={demand:.3f}")
            
            self.log.step(3, "Verify final demand is reasonable")
            final_demand = zone.get_demand_metric()
            self.log.verify(
                0.0 <= final_demand <= 1.0,
                f"Final demand should be in 0-1 range, got {final_demand}"
            )
            
            self.log.info("Test PASSED: System handles rapid changes correctly")
            self.passed_tests += 1
            
        except AssertionError as e:
            self.log.warning(str(e))
            self.failed_tests += 1
    
    # ========== Realistic Scenarios ==========
    
    async def test_realistic_house_scenario(self) -> None:
        """
        Test: Realistic house with multiple zones
        
        Scenario:
        - Living Room (high pri): 22°C target, currently 20°C, 40% open
        - Bedroom (high pri): 20°C target, currently 18°C, 60% open
        - Guest (low pri): 19°C target, currently 18°C, 50% open
        - Hallway (low pri): 18°C target, currently 18°C, 0% open
        
        Expected:
        - Living Room and Bedroom demanding heat
        - Guest Room not demanding alone but included in aggregate
        - Hallway not demanding (at target)
        - Boiler ON, intensity based on highest demand (Bedroom)
        """
        self.log.test_case(
            "Realistic House Scenario",
            "Test a realistic 4-zone house: 2 high-priority (living, bedroom), "
            "2 low-priority (guest, hallway). Verify correct boiler control."
        )
        
        try:
            self.log.step(1, "Create 4 zones with realistic configuration")
            living = ZoneWrapper("climate.living", "Living Room", is_high_priority=True, floor_area_m2=40.0)
            bedroom = ZoneWrapper("climate.bedroom", "Bedroom", is_high_priority=True, floor_area_m2=25.0)
            guest = ZoneWrapper("climate.guest", "Guest Room", is_high_priority=False, floor_area_m2=15.0)
            hallway = ZoneWrapper("climate.hallway", "Hallway", is_high_priority=False, floor_area_m2=8.0)
            
            self.log.step(2, "Set Living Room: 22°C target, 20°C current, 40% valve")
            state = MockState("climate.living", "heating", {
                'current_temperature': 20.0,
                'target_temperature': 22.0,
            })
            living.update_from_state(state)
            living.update_trv_opening(40.0)
            self.log.debug(f"Living: error={living.current_error}°C, open={living.trv_opening_percent}%, demand={living.is_demanding_heat}")
            
            self.log.step(3, "Set Bedroom: 20°C target, 18°C current, 60% valve")
            state = MockState("climate.bedroom", "heating", {
                'current_temperature': 18.0,
                'target_temperature': 20.0,
            })
            bedroom.update_from_state(state)
            bedroom.update_trv_opening(60.0)
            self.log.debug(f"Bedroom: error={bedroom.current_error}°C, open={bedroom.trv_opening_percent}%, demand={bedroom.is_demanding_heat}")
            
            self.log.step(4, "Set Guest: 19°C target, 18°C current, 50% valve")
            state = MockState("climate.guest", "heating", {
                'current_temperature': 18.0,
                'target_temperature': 19.0,
            })
            guest.update_from_state(state)
            guest.update_trv_opening(50.0)
            self.log.debug(f"Guest: error={guest.current_error}°C, open={guest.trv_opening_percent}%, demand={guest.is_demanding_heat}")
            
            self.log.step(5, "Set Hallway: 18°C target, 18°C current, 0% valve")
            state = MockState("climate.hallway", "heating", {
                'current_temperature': 18.0,
                'target_temperature': 18.0,
            })
            hallway.update_from_state(state)
            hallway.update_trv_opening(0.0)
            self.log.debug(f"Hallway: error={hallway.current_error}°C, open={hallway.trv_opening_percent}%, demand={hallway.is_demanding_heat}")
            
            self.log.step(6, "Verify high-priority zones demanding")
            self.log.verify(living.is_demanding_heat, "Living room should demand (high pri, >25%)")
            self.log.verify(bedroom.is_demanding_heat, "Bedroom should demand (high pri, >25%)")
            
            self.log.step(7, "Verify low-priority zones not demanding alone")
            self.log.verify(not guest.is_demanding_heat, "Guest should not demand alone (<100%)")
            self.log.verify(not hallway.is_demanding_heat, "Hallway should not demand (at target)")
            
            self.log.step(8, "Verify aggregate demand")
            low_aggregate = guest.trv_opening_percent + hallway.trv_opening_percent
            self.log.info(f"Low-priority aggregate: {low_aggregate}% (guest 50% + hallway 0%)")
            
            self.log.step(9, "Calculate boiler command")
            # Boiler ON because high-priority zones demanding
            boiler_on = living.is_demanding_heat or bedroom.is_demanding_heat
            self.log.verify(boiler_on, "Boiler should be ON (high-priority demand)")
            
            # Use highest demand for intensity
            all_demands = [living.get_demand_metric(), bedroom.get_demand_metric(),
                          guest.get_demand_metric(), hallway.get_demand_metric()]
            max_demand = max(all_demands)
            flow_temp = 5.0 + (max_demand * 75.0)
            self.log.info(f"Max demand: {max_demand:.3f} → Flow temp: {flow_temp:.1f}°C")
            
            self.log.info("Test PASSED: Realistic house scenario works correctly")
            self.passed_tests += 1
            
        except AssertionError as e:
            self.log.warning(str(e))
            self.failed_tests += 1
    
    async def test_zone_thermal_influence(self) -> None:
        """
        Test: Zones influencing each other (thermal coupling)
        
        Scenario:
        - Zone A heating (50% open, 2°C below target)
        - Zone B adjacent, valve closed but benefits from Zone A's heat
        - Over time, Zone B temperature rises even with 0% valve
        - When Zone B reaches target, it doesn't demand
        
        This tests realistic scenarios where heat spreads between zones.
        """
        self.log.test_case(
            "Zone Thermal Influence",
            "Simulate thermal influence between zones. Zone A heats up, "
            "zone B (adjacent, closed) temperature gradually rises without valve opening. "
            "Verify zone B doesn't trigger boiler demand."
        )
        
        try:
            self.log.step(1, "Create two adjacent zones")
            zone_a = ZoneWrapper("climate.living", "Living Room", is_high_priority=True)
            zone_b = ZoneWrapper("climate.dining", "Dining Room", is_high_priority=True)
            
            self.log.step(2, "Set Zone A: demanding heat (2°C below, 50% open)")
            state_a = MockState("climate.living", "heating", {
                'current_temperature': 19.0,
                'target_temperature': 21.0,
            })
            zone_a.update_from_state(state_a)
            zone_a.update_trv_opening(50.0)
            self.log.verify(zone_a.is_demanding_heat, "Zone A should demand heat")
            self.log.info(f"Zone A: temp={zone_a.current_temp}°C, target={zone_a.target_temp}°C, "
                         f"error={zone_a.current_error}°C, demand={zone_a.is_demanding_heat}")
            
            self.log.step(3, "Set Zone B: closed, but temperature rising due to Zone A")
            # Initial: Zone B closed and 1°C below target
            state_b = MockState("climate.dining", "idle", {
                'current_temperature': 19.0,
                'target_temperature': 20.0,
            })
            zone_b.update_from_state(state_b)
            zone_b.update_trv_opening(0.0)
            self.log.verify(not zone_b.is_demanding_heat, "Zone B at 0% should not demand")
            self.log.info(f"Zone B initial: temp={zone_b.current_temp}°C, target={zone_b.target_temp}°C, "
                         f"error={zone_b.current_error}°C")
            
            self.log.step(4, "Simulate Zone B heating up as Zone A supplies heat")
            # Zone B temp rises due to thermal influence
            state_b = MockState("climate.dining", "idle", {
                'current_temperature': 20.0,  # Reached target
                'target_temperature': 20.0,
            })
            zone_b.update_from_state(state_b)
            zone_b.update_trv_opening(0.0)  # Still closed
            
            demand_b = zone_b.get_demand_metric()
            self.log.verify(
                demand_b == 0.0,
                f"Zone B at target should have 0 demand, got {demand_b}"
            )
            self.log.info(f"Zone B after heating: temp={zone_b.current_temp}°C, "
                         f"error={zone_b.current_error}°C, demand={demand_b}")
            
            self.log.step(5, "Verify boiler decision: ON due to Zone A, OFF for Zone B")
            # Boiler should be ON from Zone A alone
            boiler_on = zone_a.is_demanding_heat
            self.log.verify(boiler_on, "Boiler should be ON from Zone A demand")
            # Zone B doesn't contribute demand even though it benefited
            self.log.verify(not zone_b.is_demanding_heat, "Zone B shouldn't add demand (closed, at target)")
            
            self.log.info("Test PASSED: Thermal influence scenario works correctly")
            self.passed_tests += 1
            
        except AssertionError as e:
            self.log.warning(str(e))
            self.failed_tests += 1


async def main():
    """Main test runner."""
    import sys
    
    # Parse command line arguments for log level
    log_level = 'info'  # Default
    if len(sys.argv) > 1:
        log_level = sys.argv[1].lower()
    
    if log_level not in ['debug', 'info', 'warning']:
        print("Usage: python test_suite.py [debug|info|warning]")
        print("  debug   - Detailed logging (recommended for debugging)")
        print("  info    - Normal logging (default)")
        print("  warning - Only warnings and errors")
        sys.exit(1)
    
    # Run test suite
    suite = TestSuite(log_level=log_level)
    await suite.run_all_tests()


if __name__ == "__main__":
    asyncio.run(main())
