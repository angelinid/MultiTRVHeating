"""
Home Assistant sensor entities for MultiTRVHeating.

Exports controller and zone state as Home Assistant sensor entities for
visualization and monitoring in the UI.
"""

import logging
from typing import Optional, Any

try:
    from homeassistant.components.sensor import SensorEntity, SensorStateClass
    from homeassistant.config_entries import ConfigEntry
    from homeassistant.const import UnitOfTemperature, PERCENTAGE
    from homeassistant.core import HomeAssistant, callback
    from homeassistant.helpers.entity_platform import AddEntitiesCallback
    from homeassistant.helpers.typing import ConfigType
    # Convert enum to string value
    _UNIT_TEMP = UnitOfTemperature.CELSIUS
    _UNIT_PERCENT = PERCENTAGE
except ImportError:
    # For testing without Home Assistant
    SensorEntity = object
    SensorStateClass = None
    _UNIT_TEMP = "°C"
    _UNIT_PERCENT = "%"
    HomeAssistant = None
    AddEntitiesCallback = None
    ConfigType = None
    ConfigEntry = None

_LOGGER = logging.getLogger("don_controller")


class MultiTRVHeatingSensor(SensorEntity if SensorEntity != object else object):
    """
    Base sensor class for MultiTRVHeating entities.
    
    Represents a single monitored value (temperature, demand, opening %, etc.)
    for either the controller as a whole or a specific zone.
    """
    
    def __init__(self, name: str, unique_id: str, unit_of_measurement: Optional[str] = None,
                 state_class: Optional[str] = None, icon: Optional[str] = None) -> None:
        """
        Initialize a MultiTRVHeating sensor.
        
        Args:
            name: Human-readable sensor name (e.g., "Living Room Temperature")
            unique_id: Unique identifier for HA entity registry (e.g., "multitrv_living_room_temperature")
            unit_of_measurement: Unit for sensor values (e.g., "°C", "%")
            state_class: HA state class for the sensor (e.g., "measurement", "total")
            icon: Icon name (e.g., "mdi:thermometer")
        """
        self._attr_name = name
        self._attr_unique_id = unique_id
        self._attr_native_unit_of_measurement = unit_of_measurement
        self._attr_state_class = state_class
        self._attr_icon = icon
        self._attr_native_value = None
    
    @property
    def state(self) -> Any:
        """Return the current state of the sensor."""
        return self._attr_native_value


class ControllerSensor(MultiTRVHeatingSensor):
    """
    Sensor for controller-level metrics (boiler demand, flow temperature, etc.).
    """
    
    def __init__(self, name: str, unique_id: str, metric_key: str,
                 unit: Optional[str] = None, state_class: Optional[str] = None,
                 icon: Optional[str] = None, entry_id: Optional[str] = None) -> None:
        """
        Initialize a controller sensor.
        
        Args:
            name: Human-readable name
            unique_id: Unique identifier (will be prefixed with entry_id)
            metric_key: Key to extract from get_controller_state() dict
            unit: Unit of measurement
            state_class: State class for HA
            icon: Icon name
            entry_id: Config entry ID for prefixing unique IDs
        """
        # Prefix unique_id with entry_id for better organization
        if entry_id:
            prefixed_id = f"{entry_id}_{unique_id}"
        else:
            prefixed_id = unique_id
        
        super().__init__(name, prefixed_id, unit, state_class, icon)
        self.metric_key = metric_key
        self.controller = None  # Will be set when attached to controller
    
    @property
    def state(self) -> Any:
        """Return current controller metric value."""
        if self.controller is None:
            return None
        
        state = self.controller.get_controller_state()
        if state and self.metric_key in state:
            return state[self.metric_key]
        return None


class ZoneSensor(MultiTRVHeatingSensor):
    """
    Sensor for zone-level metrics (temperature, demand, opening %, offset, etc.).
    """
    
    def __init__(self, zone_name: str, metric_name: str, metric_key: str,
                 unit: Optional[str] = None, state_class: Optional[str] = None,
                 icon: Optional[str] = None, entry_id: Optional[str] = None) -> None:
        """
        Initialize a zone sensor.
        
        Args:
            zone_name: Name of the zone (e.g., "Living Room")
            metric_name: Name of the metric (e.g., "Temperature")
            metric_key: Key to extract from zone.export_zone_state() dict
            unit: Unit of measurement
            state_class: State class for HA
            icon: Icon name
            entry_id: Config entry ID for prefixing unique IDs
        """
        name = f"{zone_name} {metric_name}"
        zone_name_lower = zone_name.lower().replace(" ", "_")
        unique_id = f"{zone_name_lower}_{metric_key}"
        
        # Prefix unique_id with entry_id for better organization
        if entry_id:
            prefixed_id = f"{entry_id}_{unique_id}"
        else:
            prefixed_id = unique_id
        
        super().__init__(name, prefixed_id, unit, state_class, icon)
        self.zone_name = zone_name
        self.metric_key = metric_key
        self.zone = None  # Will be set when attached to zone
    
    @property
    def state(self) -> Any:
        """Return current zone metric value."""
        if self.zone is None:
            return None
        
        state = self.zone.export_zone_state()
        if state and self.metric_key in state:
            return state[self.metric_key]
        return None


class MultiTRVHeatingEntityManager:
    """
    Manages creation and updates of all MultiTRVHeating sensor entities.
    
    Creates sensors for:
    - Controller-level metrics (zone count, OpenTherm flow temperature, etc.)
    - Zone-level metrics (temperature, demand, opening, offset, etc.)
    """
    
    # Controller sensors: (name, unique_id, metric_key, unit, state_class, icon)
    CONTROLLER_SENSORS = [
        ("Zone Count", "zone_count", "zone_count", None, None, "mdi:counter"),
        ("OpenTherm Flow Temperature", "opentherm_flow_temp", "current_flow_temp", _UNIT_TEMP, "measurement", "mdi:thermometer-high"),
    ]
    
    # Zone sensors: (metric_name, metric_key, unit, state_class, icon)
    ZONE_SENSORS = [
        ("Current Temperature", "current_temperature", _UNIT_TEMP, "measurement", "mdi:thermometer"),
        ("Target Temperature", "target_temperature", _UNIT_TEMP, "measurement", "mdi:target-temperature"),
        ("Temperature Error", "temperature_error", _UNIT_TEMP, "measurement", "mdi:delta"),
        ("TRV Opening", "trv_opening_percent", _UNIT_PERCENT, "measurement", "mdi:percent"),
        ("Demand Metric", "demand_metric", None, "measurement", "mdi:gauge"),
        ("Is Demanding Heat", "is_demanding_heat", None, None, "mdi:fire"),
        ("Temperature Offset", "temperature_offset", _UNIT_TEMP, "measurement", "mdi:delta"),
        ("Floor Area", "floor_area_m2", "m²", None, "mdi:ruler"),
        ("Has External Sensor", "has_external_sensor", None, None, "mdi:thermometer-plus"),
        ("External Sensor Temperature", "external_sensor_temperature", _UNIT_TEMP, "measurement", "mdi:thermometer"),
    ]
    
    def __init__(self, controller, entry_id: Optional[str] = None) -> None:
        """
        Initialize the entity manager.
        
        Args:
            controller: MasterController instance
            entry_id: Config entry ID for prefixing sensor unique IDs
        """
        self.controller = controller
        self.entry_id = entry_id
        self.controller_sensors = []
        self.zone_sensors = {}  # { zone_entity_id: [sensor1, sensor2, ...] }
        self._create_sensors()
    
    def _create_sensors(self) -> None:
        """Create all sensor entities."""
        # Create controller sensors
        for name, unique_id, metric_key, unit, state_class, icon in self.CONTROLLER_SENSORS:
            sensor = ControllerSensor(name, unique_id, metric_key, unit, state_class, icon, self.entry_id)
            sensor.controller = self.controller
            self.controller_sensors.append(sensor)
            _LOGGER.debug("Created controller sensor: %s", name)
        
        # Create zone sensors
        for zone_entity_id, zone in self.controller.zones.items():
            zone_sensors = []
            for metric_name, metric_key, unit, state_class, icon in self.ZONE_SENSORS:
                sensor = ZoneSensor(zone.name, metric_name, metric_key, unit, state_class, icon, self.entry_id)
                sensor.zone = zone
                zone_sensors.append(sensor)
                _LOGGER.debug("Created zone sensor: %s - %s", zone.name, metric_name)
            
            self.zone_sensors[zone_entity_id] = zone_sensors
    
    def get_all_sensors(self) -> list:
        """
        Get all sensor entities (for HA integration).
        
        Returns:
            List of all MultiTRVHeatingSensor entities
        """
        all_sensors = self.controller_sensors.copy()
        for sensors in self.zone_sensors.values():
            all_sensors.extend(sensors)
        return all_sensors
    
    def get_controller_sensors(self) -> list:
        """Get controller-level sensors."""
        return self.controller_sensors
    
    def get_zone_sensors(self, zone_entity_id: str) -> list:
        """
        Get sensors for a specific zone.
        
        Args:
            zone_entity_id: Climate entity ID of the zone
            
        Returns:
            List of sensors for the zone, or empty list if zone not found
        """
        return self.zone_sensors.get(zone_entity_id, [])


async def async_setup_entry(
    hass: "HomeAssistant",
    entry: "ConfigEntry",
    async_add_entities: "AddEntitiesCallback",
) -> None:
    """
    Set up MultiTRVHeating sensors from config entry.
    
    This is called by Home Assistant when the integration is loaded.
    It creates all sensor entities and adds them to the UI.
    
    Args:
        hass: Home Assistant instance
        entry: Config entry for this integration
        async_add_entities: Callback to add entities
    """
    from . import DOMAIN
    
    # Get the controller instance from the entry data
    if entry.entry_id not in hass.data.get(DOMAIN, {}):
        _LOGGER.error("No controller found for entry %s", entry.entry_id)
        return
    
    controller = hass.data[DOMAIN][entry.entry_id]
    
    manager = MultiTRVHeatingEntityManager(controller, entry.entry_id)
    sensors = manager.get_all_sensors()
    
    async_add_entities(sensors, update_before_add=True)
    _LOGGER.info("Set up %d MultiTRVHeating sensors for entry %s", len(sensors), entry.entry_id)
