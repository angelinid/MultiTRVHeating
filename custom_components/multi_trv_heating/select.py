"""
Home Assistant select entities for MultiTRVHeating.

Provides selection entities for configuration options like choosing the discharge TRV.
"""

import logging
from typing import Optional, Any

try:
    from homeassistant.components.select import SelectEntity
    from homeassistant.config_entries import ConfigEntry
    from homeassistant.core import HomeAssistant, callback
    from homeassistant.helpers.entity_platform import AddEntitiesCallback
    from homeassistant.helpers.device_registry import DeviceInfo
except ImportError:
    # For testing without Home Assistant
    SelectEntity = object
    DeviceInfo = None
    HomeAssistant = None
    AddEntitiesCallback = None
    ConfigEntry = None

_LOGGER = logging.getLogger("don_controller")


class MultiTRVHeatingSelect(SelectEntity if SelectEntity != object else object):
    """
    Base select entity class for MultiTRVHeating configuration options.
    
    Represents a configurable choice (e.g., which TRV to use as discharge valve).
    """
    
    def __init__(self, name: str, unique_id: str, icon: Optional[str] = None,
                 device_info: Optional[Any] = None) -> None:
        """
        Initialize a MultiTRVHeating select entity.
        
        Args:
            name: Human-readable entity name
            unique_id: Unique identifier for HA entity registry
            icon: Icon name (e.g., "mdi:water-pump")
            device_info: Device info dict for grouping entities into a device
        """
        self._attr_name = name
        self._attr_unique_id = unique_id
        self._attr_icon = icon
        self._attr_current_option = None
        self._attr_options = []
        self._attr_device_info = device_info
    
    @property
    def current_option(self) -> Optional[str]:
        """Return the currently selected option."""
        return self._attr_current_option
    
    @property
    def options(self) -> list:
        """Return the list of available options."""
        return self._attr_options


class DischargeTRVSelect(MultiTRVHeatingSelect):
    """
    Select entity for choosing which TRV acts as the pump discharge valve.
    
    Allows users to select from all configured zones which one should be used
    to keep the boiler pump running after all zones close.
    """
    
    def __init__(self, controller, entry_id: Optional[str] = None,
                 device_info: Optional[Any] = None) -> None:
        """
        Initialize the discharge TRV selector.
        
        Args:
            controller: MasterController instance
            entry_id: Config entry ID for prefixing unique IDs
            device_info: Device info dict for controller device
        """
        # Create unique ID
        if entry_id:
            unique_id = f"{entry_id}_discharge_trv_select"
        else:
            unique_id = "multi_trv_discharge_trv_select"
        
        super().__init__(
            name="Discharge TRV",
            unique_id=unique_id,
            icon="mdi:water-pump",
            device_info=device_info
        )
        
        self.controller = controller
        self._attr_has_entity_name = True
        self._update_options()
        
        # Set current option based on controller's pump discharge config
        self._update_current_option()
    
    def _update_options(self) -> None:
        """
        Update available options based on configured zones.
        
        Options are the names of all configured zones plus an "Off" option.
        """
        options = ["Off"]  # Always include "Off" to disable discharge
        
        if self.controller and self.controller.zones:
            for zone in self.controller.zones.values():
                options.append(zone.name)
        
        self._attr_options = options
        _LOGGER.debug("Discharge TRV options updated: %s", options)
    
    def _update_current_option(self) -> None:
        """Update the current selected option based on controller state."""
        if not self.controller or not self.controller.pump_discharge:
            self._attr_current_option = "Off"
            return
        
        discharge_trv_name = self.controller.pump_discharge.discharge_trv_name
        
        if discharge_trv_name and discharge_trv_name != "Unknown":
            if discharge_trv_name in self._attr_options:
                self._attr_current_option = discharge_trv_name
            else:
                # Configured TRV not in options, set to Off
                self._attr_current_option = "Off"
        else:
            self._attr_current_option = "Off"
    
    async def async_select_option(self, option: str) -> None:
        """
        Handle selection of a discharge TRV option.
        
        Updates the controller's pump discharge configuration with the selected zone.
        
        Args:
            option: The selected zone name or "Off"
        """
        if not self.controller or not self.controller.pump_discharge:
            _LOGGER.warning("DischargeTRVSelect: Cannot select - controller not available")
            return
        
        if option == "Off":
            # Disable discharge
            self.controller.pump_discharge.update_config(None, None)
            self._attr_current_option = "Off"
            _LOGGER.info("Discharge TRV disabled (selection: Off)")
        else:
            # Find the zone with matching name and update config
            found = False
            for zone in self.controller.zones.values():
                if zone.name == option:
                    self.controller.pump_discharge.update_config(zone.entity_id, zone.name)
                    self._attr_current_option = option
                    _LOGGER.info("Discharge TRV updated to: %s (entity_id: %s)", option, zone.entity_id)
                    found = True
                    break
            
            if not found:
                _LOGGER.warning("DischargeTRVSelect: Could not find zone with name '%s'", option)
                self._attr_current_option = "Off"
        
        # Trigger state change notification
        self.async_write_ha_state()


class MultiTRVHeatingSelectManager:
    """
    Manages creation and updates of all MultiTRVHeating select entities.
    
    Currently creates:
    - Discharge TRV selector (choose which TRV is pump discharge valve)
    """
    
    def __init__(self, controller, entry_id: Optional[str] = None,
                 controller_device: Optional[Any] = None) -> None:
        """
        Initialize the select entity manager.
        
        Args:
            controller: MasterController instance
            entry_id: Config entry ID for prefixing unique IDs
            controller_device: DeviceInfo dict for controller device
        """
        self.controller = controller
        self.entry_id = entry_id
        self.controller_device = controller_device
        self.select_entities = []
        self._create_entities()
    
    def _create_entities(self) -> None:
        """Create all select entities."""
        # Create discharge TRV selector
        discharge_selector = DischargeTRVSelect(
            self.controller,
            self.entry_id,
            self.controller_device
        )
        self.select_entities.append(discharge_selector)
        _LOGGER.debug("Created discharge TRV selector entity")
    
    def get_all_entities(self) -> list:
        """
        Get all select entities.
        
        Returns:
            List of all MultiTRVHeatingSelect entities
        """
        return self.select_entities


async def async_setup_entry(
    hass: "HomeAssistant",
    entry: "ConfigEntry",
    async_add_entities: "AddEntitiesCallback",
) -> None:
    """
    Set up MultiTRVHeating select entities from config entry.
    
    This is called by Home Assistant when the integration is loaded.
    It creates all select entities and adds them to the UI.
    
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
    
    # Create controller device info for select entities (same as sensor.py)
    controller_device_info = None
    if DeviceInfo is not None:  # Only if running with Home Assistant
        controller_device_info = DeviceInfo(
            identifiers={("multi_trv_heating", f"{entry.entry_id}_controller")},
            name="Multi-TRV Heating Controller",
            manufacturer="Multi-TRV Heating",
            model="System Controller",
        )
    
    manager = MultiTRVHeatingSelectManager(controller, entry.entry_id, controller_device_info)
    entities = manager.get_all_entities()
    
    async_add_entities(entities, update_before_add=True)
    _LOGGER.info("Set up %d MultiTRVHeating select entities for entry %s", len(entities), entry.entry_id)
