"""
Unit tests for MultiTRVHeating sensor entities.

Tests the creation and updating of Home Assistant sensor entities that
expose controller and zone state for monitoring and visualization.
"""

import sys
import os

# Add parent directory to path for imports
sys.path.insert(0, os.path.join(os.path.dirname(__file__), '..', 'custom_components', 'multi_trv_heating'))

from master_controller import MasterController
from sensor import (
    MultiTRVHeatingSensor, ControllerSensor, ZoneSensor,
    MultiTRVHeatingEntityManager, _UNIT_TEMP, _UNIT_PERCENT
)


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


class TestSensors:
    """Test suite for sensor entity creation and management."""
    
    def __init__(self):
        self.hass = MockHomeAssistant()
        self.controller = None
        self.passed = 0
        self.failed = 0
    
    def setup_controller(self):
        """Set up a master controller with test zones."""
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
    
    def test_1_base_sensor_creation(self):
        """Test creating a base MultiTRVHeatingSensor."""
        print("\nTest 1: Base sensor creation")
        
        sensor = MultiTRVHeatingSensor(
            name="Test Sensor",
            unique_id="test_sensor_1",
            unit_of_measurement="°C",
            state_class="measurement",
            icon="mdi:thermometer"
        )
        
        self.verify(sensor._attr_name == "Test Sensor", "Name should be set")
        self.verify(sensor._attr_unique_id == "test_sensor_1", "Unique ID should be set")
        self.verify(sensor._attr_native_unit_of_measurement == "°C", "Unit should be set")
        self.verify(sensor._attr_state_class == "measurement", "State class should be set")
        self.verify(sensor._attr_icon == "mdi:thermometer", "Icon should be set")
        self.verify(sensor.state is None, "Initial state should be None")
    
    def test_2_sensor_state_update(self):
        """Test updating sensor state via native value."""
        print("\nTest 2: Sensor state update")
        
        sensor = MultiTRVHeatingSensor("Test", "test_2")
        
        self.verify(sensor.state is None, "Initial state should be None")
        
        # Update native value directly
        sensor._attr_native_value = 25.5
        self.verify(sensor.state == 25.5, "State should be updated to 25.5")
        
        sensor._attr_native_value = "heating"
        self.verify(sensor.state == "heating", "State should be updated to 'heating'")
    
    def test_3_controller_sensor_creation(self):
        """Test creating a controller sensor."""
        print("\nTest 3: Controller sensor creation")
        self.setup_controller()
        
        sensor = ControllerSensor(
            name="Zone Count",
            unique_id="multitrv_zone_count",
            metric_key="zone_count"
        )
        sensor.controller = self.controller
        
        self.verify(sensor.controller == self.controller, "Controller should be set")
        self.verify(sensor.metric_key == "zone_count", "Metric key should be set")
        self.verify(sensor._attr_name == "Zone Count", "Name should be set")
    
    def test_4_controller_sensor_update(self):
        """Test controller sensor computes state from controller."""
        print("\nTest 4: Controller sensor update")
        self.setup_controller()
        
        sensor = ControllerSensor(
            name="Zone Count",
            unique_id="multitrv_zone_count",
            metric_key="zone_count"
        )
        sensor.controller = self.controller
        
        # Sensor state is computed on demand from controller.get_controller_state()
        self.verify(sensor.state == 3, "Zone count should be 3 (computed from controller)")
    
    def test_5_zone_sensor_creation(self):
        """Test creating a zone sensor."""
        print("\nTest 5: Zone sensor creation")
        self.setup_controller()
        
        sensor = ZoneSensor(
            zone_name="Living Room",
            metric_name="Temperature",
            metric_key="current_temperature",
            unit=_UNIT_TEMP,
            icon="mdi:thermometer"
        )
        
        self.verify("Living Room" in sensor._attr_name, "Zone name should be in sensor name")
        self.verify("living_room" in sensor._attr_unique_id, "Zone name should be in unique ID")
        self.verify(sensor.metric_key == "current_temperature", "Metric key should be set")
    
    def test_6_zone_sensor_update(self):
        """Test zone sensor computes state from zone."""
        print("\nTest 6: Zone sensor update")
        self.setup_controller()
        
        zone = self.controller.zones['climate.living_room']
        zone.current_temp = 21.5
        zone.target_temp = 22.0
        zone.current_error = 0.5
        
        sensor = ZoneSensor(
            zone_name="Living Room",
            metric_name="Current Temperature",
            metric_key="current_temperature"
        )
        sensor.zone = zone
        
        # Sensor state is computed on demand from zone.export_zone_state()
        self.verify(sensor.state == 21.5, f"Current temp should be 21.5, got {sensor.state}")
    
    def test_7_entity_manager_creation(self):
        """Test creating entity manager."""
        print("\nTest 7: Entity manager creation")
        self.setup_controller()
        
        manager = MultiTRVHeatingEntityManager(self.controller)
        
        self.verify(manager.controller == self.controller, "Controller should be set")
        self.verify(len(manager.controller_sensors) > 0, "Should have controller sensors")
        self.verify(len(manager.zone_sensors) == 3, f"Should have 3 zone sensor groups, got {len(manager.zone_sensors)}")
    
    def test_8_manager_creates_all_zone_sensors(self):
        """Test that manager creates sensors for all zones."""
        print("\nTest 8: Manager creates all zone sensors")
        self.setup_controller()
        
        manager = MultiTRVHeatingEntityManager(self.controller)
        
        # Check each zone has sensors
        for zone_entity_id, sensors in manager.zone_sensors.items():
            self.verify(
                len(sensors) > 0,
                f"Zone {zone_entity_id} should have sensors (got {len(sensors)})"
            )
            # Should have at least temperature, opening, demand sensors
            self.verify(
                len(sensors) >= 8,
                f"Zone {zone_entity_id} should have at least 8 sensors, got {len(sensors)}"
            )
    
    def test_9_manager_get_all_sensors(self):
        """Test getting all sensors from manager."""
        print("\nTest 9: Manager get all sensors")
        self.setup_controller()
        
        manager = MultiTRVHeatingEntityManager(self.controller)
        all_sensors = manager.get_all_sensors()
        
        # Should have controller sensors + zone sensors
        expected_count = len(manager.controller_sensors)
        for sensors in manager.zone_sensors.values():
            expected_count += len(sensors)
        
        self.verify(
            len(all_sensors) == expected_count,
            f"Should have {expected_count} total sensors, got {len(all_sensors)}"
        )
    
    def test_10_manager_update_all_sensors(self):
        """Test sensor states are computed from controller/zones."""
        print("\nTest 10: Sensor state computation")
        self.setup_controller()
        
        # Set some zone state
        living_room = self.controller.zones['climate.living_room']
        living_room.current_temp = 19.5
        living_room.target_temp = 22.0
        living_room.current_error = 2.5
        living_room.trv_opening_percent = 45.0
        
        bedroom = self.controller.zones['climate.bedroom']
        bedroom.current_temp = 18.0
        bedroom.target_temp = 20.0
        bedroom.current_error = 2.0
        bedroom.trv_opening_percent = 60.0
        
        manager = MultiTRVHeatingEntityManager(self.controller)
        
        # Sensors compute state on demand (no need to call update_all_sensors)
        
        # Check controller sensor
        zone_count_sensor = manager.controller_sensors[0]
        self.verify(zone_count_sensor.state == 3, "Zone count should be 3")
        
        # Check zone sensors
        living_room_sensors = manager.get_zone_sensors('climate.living_room')
        self.verify(len(living_room_sensors) > 0, "Should have living room sensors")
        
        # Find temperature sensor
        temp_sensor = None
        for sensor in living_room_sensors:
            if "Current Temperature" in sensor._attr_name:
                temp_sensor = sensor
                break
        
        self.verify(temp_sensor is not None, "Should find temperature sensor")
        if temp_sensor:
            self.verify(
                temp_sensor.state == 19.5,
                f"Temperature should be 19.5, got {temp_sensor.state}"
            )
    
    def test_11_sensor_units(self):
        """Test sensor units are set correctly."""
        print("\nTest 11: Sensor units")
        self.setup_controller()
        
        manager = MultiTRVHeatingEntityManager(self.controller)
        sensors = manager.get_all_sensors()
        
        # Check some sensors have correct units
        temp_sensors = [s for s in sensors if "Temperature" in s._attr_name and "External" not in s._attr_name]
        self.verify(len(temp_sensors) > 0, "Should have temperature sensors")
        
        for sensor in temp_sensors:
            if "Current" in sensor._attr_name or "Target" in sensor._attr_name:
                self.verify(
                    sensor._attr_native_unit_of_measurement == _UNIT_TEMP,
                    f"Temperature sensor should have unit {_UNIT_TEMP}, got {sensor._attr_native_unit_of_measurement}"
                )
    
    def test_12_sensor_icons(self):
        """Test sensor icons are set."""
        print("\nTest 12: Sensor icons")
        self.setup_controller()
        
        manager = MultiTRVHeatingEntityManager(self.controller)
        sensors = manager.get_all_sensors()
        
        # Check sensors have icons
        sensors_with_icons = [s for s in sensors if s._attr_icon is not None]
        self.verify(
            len(sensors_with_icons) > 0,
            f"Should have sensors with icons (got {len(sensors_with_icons)}/{len(sensors)})"
        )
    
    def run_all_tests(self):
        """Run all sensor tests."""
        print("\n" + "="*80)
        print("SENSOR ENTITY TEST SUITE")
        print("="*80)
        
        self.test_1_base_sensor_creation()
        self.test_2_sensor_state_update()
        self.test_3_controller_sensor_creation()
        self.test_4_controller_sensor_update()
        self.test_5_zone_sensor_creation()
        self.test_6_zone_sensor_update()
        self.test_7_entity_manager_creation()
        self.test_8_manager_creates_all_zone_sensors()
        self.test_9_manager_get_all_sensors()
        self.test_10_manager_update_all_sensors()
        self.test_11_sensor_units()
        self.test_12_sensor_icons()
        
        print("\n" + "="*80)
        print(f"RESULTS: {self.passed} passed, {self.failed} failed")
        print("="*80)
        
        return self.failed == 0


if __name__ == "__main__":
    suite = TestSensors()
    success = suite.run_all_tests()
    sys.exit(0 if success else 1)
