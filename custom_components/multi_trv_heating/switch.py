"""
Home Assistant switch entities for MultiTRVHeating per-zone control.

Provides switch entities for controlling zone-specific settings like priority level.
"""

import logging
from typing import Optional, Any

try:
    from homeassistant.components.switch import SwitchEntity
    from homeassistant.config_entries import ConfigEntry
    from homeassistant.core import HomeAssistant, callback
    from homeassistant.helpers.entity_platform import AddEntitiesCallback
    from homeassistant.helpers.typing import ConfigType
    from homeassistant.helpers.device_registry import DeviceInfo
except ImportError:
    # For testing without Home Assistant
    SwitchEntity = object
    HomeAssistant = None
    AddEntitiesCallback = None
    ConfigType = None
    ConfigEntry = None
    DeviceInfo = None

_LOGGER = logging.getLogger("don_controller")


class MultiTRVHeatingSwitch(SwitchEntity if SwitchEntity != object else object):
    """Base switch class for MultiTRVHeating control entities."""
    
    def __init__(self, name: str, unique_id: str, icon: Optional[str] = None,
                 device_info: Optional[Any] = None) -> None:
        """
        Initialize a MultiTRVHeating switch.
        
        Args:
            name: Human-readable switch name
            unique_id: Unique identifier for HA entity registry
            icon: Icon name (e.g., "mdi:toggle-switch")
            device_info: Device info dict for grouping entities
        """
        self._attr_name = name
        self._attr_unique_id = unique_id
        self._attr_icon = icon
        self._attr_device_info = device_info
        self._is_on = False


class ZonePrioritySwitch(MultiTRVHeatingSwitch):
    """
    Switch entity for controlling zone priority level.
    
    ON = High priority (triggers boiler at 25% opening)
    OFF = Low priority (requires 100% opening to trigger)
    """
    
    def __init__(self, zone_name: str, zone_entity_id: str, zone,
                 entry_id: Optional[str] = None, device_info: Optional[Any] = None) -> None:
        """
        Initialize zone priority switch.
        
        Args:
            zone_name: Human-readable zone name
            zone_entity_id: Climate entity ID of the zone
            zone: ZoneWrapper instance
            entry_id: Config entry ID for prefixing unique IDs
            device_info: Device info dict for grouping
        """
        name = f"{zone_name} Priority (High)"
        zone_name_lower = zone_name.lower().replace(" ", "_")
        unique_id = f"multi_trv_{zone_name_lower}_priority_switch"
        
        if entry_id:
            prefixed_id = f"{entry_id}_{unique_id}"
        else:
            prefixed_id = unique_id
        
        super().__init__(name, prefixed_id, "mdi:priority-high", device_info)
        self.zone = zone
        self.zone_entity_id = zone_entity_id
        self._attr_is_on = zone.is_high_priority if zone else True
    
    @property
    def is_on(self) -> bool:
        """Return True if zone is high priority."""
        return self.zone.is_high_priority if self.zone else False
    
    async def async_turn_on(self, **kwargs) -> None:
        """Turn on - set zone to high priority."""
        if self.zone:
            self.zone.is_high_priority = True
            self.async_write_ha_state()
            _LOGGER.info("Set zone %s to high priority", self.zone.name)
    
    async def async_turn_off(self, **kwargs) -> None:
        """Turn off - set zone to low priority."""
        if self.zone:
            self.zone.is_high_priority = False
            self.async_write_ha_state()
            _LOGGER.info("Set zone %s to low priority", self.zone.name)


async def async_setup_entry(
    hass: "HomeAssistant",
    entry: "ConfigEntry",
    async_add_entities: "AddEntitiesCallback",
) -> None:
    """
    Set up MultiTRVHeating switches from config entry.
    
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
    
    switches = []
    
    # Create priority switch for each zone
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
        
        switch = ZonePrioritySwitch(
            zone.name,
            zone_entity_id,
            zone,
            entry.entry_id,
            device_info
        )
        switches.append(switch)
        _LOGGER.debug("Created priority switch for zone: %s", zone.name)
    
    if switches:
        async_add_entities(switches, update_before_add=True)
        _LOGGER.info("Set up %d MultiTRVHeating switches for entry %s", len(switches), entry.entry_id)
