"""
Home Assistant number entities for MultiTRVHeating per-zone control.

Provides number entities for configuring zone-specific settings like floor area.
"""

import logging
from datetime import datetime, timedelta
from typing import Optional, Any

try:
    from homeassistant.components.number import NumberEntity, NumberMode
    from homeassistant.config_entries import ConfigEntry
    from homeassistant.const import UnitOfArea
    from homeassistant.core import HomeAssistant, callback
    from homeassistant.helpers.entity_platform import AddEntitiesCallback
    from homeassistant.helpers.typing import ConfigType
    from homeassistant.helpers.device_registry import DeviceInfo
except ImportError:
    # For testing without Home Assistant
    NumberEntity = object
    NumberMode = None
    UnitOfArea = "m²"
    HomeAssistant = None
    AddEntitiesCallback = None
    ConfigType = None
    ConfigEntry = None
    DeviceInfo = None

from .storage import get_storage

_LOGGER = logging.getLogger("don_controller")


class MultiTRVHeatingNumber(NumberEntity if NumberEntity != object else object):
    """Base number class for MultiTRVHeating control entities."""
    
    def __init__(self, name: str, unique_id: str, icon: Optional[str] = None,
                 unit: Optional[str] = None, min_val: float = 0.0,
                 max_val: float = 1000.0, step: float = 0.1,
                 device_info: Optional[Any] = None) -> None:
        """
        Initialize a MultiTRVHeating number entity.
        
        Args:
            name: Human-readable number name
            unique_id: Unique identifier for HA entity registry
            icon: Icon name
            unit: Unit of measurement
            min_val: Minimum value
            max_val: Maximum value
            step: Step size for increments
            device_info: Device info dict for grouping entities
        """
        self._attr_name = name
        self._attr_unique_id = unique_id
        self._attr_icon = icon
        self._attr_native_unit_of_measurement = unit
        self._attr_native_min_value = min_val
        self._attr_native_max_value = max_val
        self._attr_native_step = step
        self._attr_device_info = device_info
        self._attr_native_value = 0.0


class ZoneAreaNumber(MultiTRVHeatingNumber):
    """
    Number entity for controlling zone floor area.
    
    Allows users to adjust floor area (m²) which affects demand calculation.
    """
    
    def __init__(self, zone_name: str, zone_entity_id: str, zone,
                 entry_id: Optional[str] = None, device_info: Optional[Any] = None,
                 hass: Optional[Any] = None) -> None:
        """
        Initialize zone area number entity.
        
        Args:
            zone_name: Human-readable zone name
            zone_entity_id: Climate entity ID of the zone
            zone: ZoneWrapper instance
            entry_id: Config entry ID for prefixing unique IDs
            device_info: Device info dict for grouping
            hass: Home Assistant instance for state restoration
        """
        name = f"{zone_name} Floor Area"
        zone_name_lower = zone_name.lower().replace(" ", "_")
        unique_id = f"multi_trv_{zone_name_lower}_area_m2"
        
        if entry_id:
            prefixed_id = f"{entry_id}_{unique_id}"
        else:
            prefixed_id = unique_id
        
        # Floor area range: 0-500 m², step 0.1
        # Convert UnitOfArea enum to string if needed
        unit_str = UnitOfArea.value if hasattr(UnitOfArea, 'value') else str(UnitOfArea) if UnitOfArea else "m²"
        super().__init__(
            name,
            prefixed_id,
            icon="mdi:ruler-square",
            unit=unit_str,
            min_val=0.0,
            max_val=500.0,
            step=0.1,
            device_info=device_info
        )
        self.zone = zone
        self.zone_entity_id = zone_entity_id
        self.hass = hass
        
        # Restore state from storage if available
        storage = get_storage()
        if storage:
            stored_value = storage.get(f"zone_floor_area_{prefixed_id}")
            if stored_value is not None:
                try:
                    self.zone.floor_area_m2 = max(0.0, min(500.0, float(stored_value)))
                    self._attr_native_value = self.zone.floor_area_m2
                    _LOGGER.info("Restored zone %s floor area from storage: %.2f m²", zone_name, self.zone.floor_area_m2)
                except (ValueError, TypeError):
                    self._attr_native_value = zone.floor_area_m2 if zone else 0.0
            else:
                self._attr_native_value = zone.floor_area_m2 if zone else 0.0
        else:
            self._attr_native_value = zone.floor_area_m2 if zone else 0.0
    
    @property
    def native_value(self) -> Optional[float]:
        """Return current floor area value."""
        return self.zone.floor_area_m2 if self.zone else None
    
    async def async_set_native_value(self, value: float) -> None:
        """Set floor area value."""
        if self.zone:
            self.zone.floor_area_m2 = max(0.0, min(500.0, value))
            self._attr_native_value = self.zone.floor_area_m2
            self.async_write_ha_state()
            # Persist state to storage
            storage = get_storage()
            if storage:
                await storage.async_set_and_save(f"zone_floor_area_{self._attr_unique_id}", self.zone.floor_area_m2)
            _LOGGER.info("Set zone %s floor area to %.2f m²", self.zone.name, self.zone.floor_area_m2)


class PreheatingEndTimeHour(MultiTRVHeatingNumber):
    """
    Number entity for controlling preheating end time hour.
    
    Represents the hour (0-23) of when preheating should stop.
    """
    
    def __init__(self, controller, entry_id: Optional[str] = None,
                 controller_device_info: Optional[Any] = None,
                 hass: Optional[Any] = None) -> None:
        """
        Initialize preheating hour number entity.
        
        Args:
            controller: MasterController instance
            entry_id: Config entry ID for prefixing unique IDs
            controller_device_info: Device info dict for grouping with controller metrics
            hass: Home Assistant instance for state restoration
        """
        name = "Preheating End Hour"
        unique_id = "multi_trv_preheating_end_hour"
        
        if entry_id:
            prefixed_id = f"{entry_id}_{unique_id}"
        else:
            prefixed_id = unique_id
        
        # Hour range: 0-23, step 1
        super().__init__(
            name,
            prefixed_id,
            icon="mdi:clock-outline",
            unit="h",
            min_val=0.0,
            max_val=23.0,
            step=1.0,
            device_info=controller_device_info
        )
        self.controller = controller
        self.hass = hass
        
        # Initialize with current hour, but restore from storage if available
        now = datetime.now()
        storage = get_storage()
        if storage:
            stored_hour = storage.get(f"preheating_end_hour_{prefixed_id}")
            if stored_hour is not None:
                try:
                    hour = int(float(stored_hour))
                    # Restore the preheating end time with this hour
                    minute = self.controller.preheating.preheating_end_time.minute if self.controller.preheating.preheating_end_time else 0
                    new_end_time = now.replace(hour=hour, minute=minute, second=0, microsecond=0)
                    if new_end_time <= now:
                        new_end_time += timedelta(days=1)
                    self.controller.preheating.preheating_end_time = new_end_time
                    self._attr_native_value = float(hour)
                    _LOGGER.info("Restored preheating end hour from storage: %d", hour)
                except (ValueError, AttributeError):
                    self._attr_native_value = float(now.hour)
            else:
                self._attr_native_value = float(now.hour)
        else:
            self._attr_native_value = float(now.hour)
    
    @property
    def native_value(self) -> Optional[float]:
        """Return current preheating hour."""
        if self.controller.preheating.preheating_end_time:
            return float(self.controller.preheating.preheating_end_time.hour)
        return float(datetime.now().hour)
    
    async def async_set_native_value(self, value: float) -> None:
        """Set preheating end hour."""
        hour = int(value)
        
        # Get current minute from preheating_end_time or from now
        if self.controller.preheating.preheating_end_time:
            minute = self.controller.preheating.preheating_end_time.minute
        else:
            minute = datetime.now().minute
        
        # Create new end time with updated hour
        now = datetime.now()
        new_end_time = now.replace(hour=hour, minute=minute, second=0, microsecond=0)
        
        # If the time is in the past, move to next day
        if new_end_time <= now:
            new_end_time += timedelta(days=1)
        
        self.controller.preheating.preheating_end_time = new_end_time
        self._attr_native_value = float(hour)
        self.async_write_ha_state()
        # Persist state to storage
        storage = get_storage()
        if storage:
            await storage.async_set_and_save(f"preheating_end_hour_{self._attr_unique_id}", hour)
        _LOGGER.info("Set preheating end time to %02d:%02d", hour, minute)


class PreheatingEndTimeMinute(MultiTRVHeatingNumber):
    """
    Number entity for controlling preheating end time minute.
    
    Represents the minute (0-59) of when preheating should stop.
    """
    
    def __init__(self, controller, entry_id: Optional[str] = None,
                 controller_device_info: Optional[Any] = None,
                 hass: Optional[Any] = None) -> None:
        """
        Initialize preheating minute number entity.
        
        Args:
            controller: MasterController instance
            entry_id: Config entry ID for prefixing unique IDs
            controller_device_info: Device info dict for grouping with controller metrics
            hass: Home Assistant instance for state restoration
        """
        name = "Preheating End Minute"
        unique_id = "multi_trv_preheating_end_minute"
        
        if entry_id:
            prefixed_id = f"{entry_id}_{unique_id}"
        else:
            prefixed_id = unique_id
        
        # Minute range: 0-59, step 1
        super().__init__(
            name,
            prefixed_id,
            icon="mdi:clock-outline",
            unit="min",
            min_val=0.0,
            max_val=59.0,
            step=1.0,
            device_info=controller_device_info
        )
        self.controller = controller
        self.hass = hass
        
        # Initialize with current minute, but restore from storage if available
        now = datetime.now()
        storage = get_storage()
        if storage:
            stored_minute = storage.get(f"preheating_end_minute_{prefixed_id}")
            if stored_minute is not None:
                try:
                    minute = int(float(stored_minute))
                    # Restore the preheating end time with this minute
                    hour = self.controller.preheating.preheating_end_time.hour if self.controller.preheating.preheating_end_time else datetime.now().hour
                    new_end_time = now.replace(hour=hour, minute=minute, second=0, microsecond=0)
                    if new_end_time <= now:
                        new_end_time += timedelta(days=1)
                    self.controller.preheating.preheating_end_time = new_end_time
                    self._attr_native_value = float(minute)
                    _LOGGER.info("Restored preheating end minute from storage: %d", minute)
                except (ValueError, AttributeError):
                    self._attr_native_value = float(now.minute)
            else:
                self._attr_native_value = float(now.minute)
        else:
            self._attr_native_value = float(now.minute)
    
    @property
    def native_value(self) -> Optional[float]:
        """Return current preheating minute."""
        if self.controller.preheating.preheating_end_time:
            return float(self.controller.preheating.preheating_end_time.minute)
        return float(datetime.now().minute)
    
    async def async_set_native_value(self, value: float) -> None:
        """Set preheating end minute."""
        minute = int(value)
        
        # Get current hour from preheating_end_time or from now
        if self.controller.preheating.preheating_end_time:
            hour = self.controller.preheating.preheating_end_time.hour
        else:
            hour = datetime.now().hour
        
        # Create new end time with updated minute
        now = datetime.now()
        new_end_time = now.replace(hour=hour, minute=minute, second=0, microsecond=0)
        
        # If the time is in the past, move to next day
        if new_end_time <= now:
            new_end_time += timedelta(days=1)
        
        self.controller.preheating.preheating_end_time = new_end_time
        self._attr_native_value = float(minute)
        self.async_write_ha_state()
        # Persist state to storage
        storage = get_storage()
        if storage:
            await storage.async_set_and_save(f"preheating_end_minute_{self._attr_unique_id}", minute)
        _LOGGER.info("Set preheating end time to %02d:%02d", hour, minute)


async def async_setup_entry(
    hass: "HomeAssistant",
    entry: "ConfigEntry",
    async_add_entities: "AddEntitiesCallback",
) -> None:
    """
    Set up MultiTRVHeating number entities from config entry.
    
    Args:
        hass: Home Assistant instance
        entry: Config entry for this integration
        async_add_entities: Callback to add entities
    """
    from . import DOMAIN
    
    # Get the controller instance
    if entry.entry_id not in hass.data.get(DOMAIN, {}):
        _LOGGER.error("No controller found for entry %s", entry.entry_id)
        return
    
    controller = hass.data[DOMAIN][entry.entry_id]
    
    numbers = []
    
    # Create controller device for controller-level metrics (preheating, flow temp, zone count)
    controller_device_info = None
    if DeviceInfo is not None:  # Only if running with Home Assistant
        controller_device_info = DeviceInfo(
            identifiers={("multi_trv_heating", f"{entry.entry_id}_controller")},
            name="Multi-TRV Heating Controller",
            manufacturer="Multi-TRV Heating",
            model="System Controller",
        )
    
    # Create preheating time control entities (not zone-specific, global controller)
    preheating_hour = PreheatingEndTimeHour(controller, entry.entry_id, controller_device_info, hass)
    preheating_minute = PreheatingEndTimeMinute(controller, entry.entry_id, controller_device_info, hass)
    numbers.append(preheating_hour)
    numbers.append(preheating_minute)
    _LOGGER.debug("Created preheating time control entities")
    
    # Create area number for each zone
    for zone_entity_id, zone in controller.zones.items():
        # Create device info for this zone
        device_id = f"{entry.entry_id}_{zone_entity_id.replace('.', '_')}"
        device_info = None
        if DeviceInfo is not None:
            device_info = DeviceInfo(
                identifiers={("multi_trv_heating", device_id)},
                name=zone.name,
                manufacturer="Multi-TRV Heating",
                model="Zone Controller",
            )
        
        number = ZoneAreaNumber(
            zone.name,
            zone_entity_id,
            zone,
            entry.entry_id,
            device_info,
            hass
        )
        numbers.append(number)
        _LOGGER.debug("Created area number for zone: %s", zone.name)
    
    if numbers:
        async_add_entities(numbers, update_before_add=True)
        _LOGGER.info("Set up %d MultiTRVHeating numbers for entry %s", len(numbers), entry.entry_id)
