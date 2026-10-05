"""
Home Assistant select entities for MultiTRVHeating.

- Controller: which zone's TRV is used as the pump discharge valve ("Off" disables)
"""

import logging
from typing import Any, Optional

try:
    from homeassistant.components.select import SelectEntity
    from homeassistant.config_entries import ConfigEntry
    from homeassistant.core import HomeAssistant
    from homeassistant.helpers.entity_platform import AddEntitiesCallback
except ImportError:
    # For testing without Home Assistant
    SelectEntity = object
    HomeAssistant = None
    AddEntitiesCallback = None
    ConfigEntry = None

from .const import LOGGER_NAME
from .entity import PersistentEntityMixin, controller_device_info, get_controller

_LOGGER = logging.getLogger(LOGGER_NAME)

OPTION_OFF = "Off"


class MultiTRVHeatingSelect(PersistentEntityMixin, SelectEntity):
    """Base select entity for MultiTRVHeating configuration options."""

    def __init__(self, name: str, unique_id: str, icon: Optional[str] = None,
                 device_info: Optional[Any] = None) -> None:
        self._attr_name = name
        self._attr_unique_id = unique_id
        self._attr_icon = icon
        self._attr_current_option = None
        self._attr_options = []
        self._attr_device_info = device_info

    @property
    def current_option(self) -> Optional[str]:
        return self._attr_current_option

    @property
    def options(self) -> list:
        return self._attr_options


class DischargeTRVSelect(MultiTRVHeatingSelect):
    """Choose the zone whose TRV keeps the pump circulating after the boiler stops."""

    STORAGE_PREFIX = "discharge_trv_select"

    def __init__(self, controller, entry_id: Optional[str] = None,
                 device_info: Optional[Any] = None, hass: Optional[Any] = None) -> None:
        super().__init__(
            name="Discharge TRV",
            unique_id=f"{entry_id}_discharge_trv_select" if entry_id else "multi_trv_discharge_trv_select",
            icon="mdi:water-pump",
            device_info=device_info,
        )
        self.controller = controller
        self.hass = hass
        self._attr_has_entity_name = True
        self._attr_options = [OPTION_OFF] + [z.name for z in controller.zones.values()] if controller else [OPTION_OFF]

        stored_option = self._restore_stored()
        if stored_option and stored_option in self._attr_options:
            self._attr_current_option = stored_option
            self._apply_option(stored_option)
            _LOGGER.info("Restored discharge TRV selection from storage: %s", stored_option)
        else:
            self._attr_current_option = self._option_from_controller()

    def _option_from_controller(self) -> str:
        """Current option according to the controller's pump discharge config."""
        if not self.controller or not self.controller.pump_discharge:
            return OPTION_OFF
        name = self.controller.pump_discharge.discharge_trv_name
        if name and name != "Unknown" and name in self._attr_options:
            return name
        return OPTION_OFF

    def _apply_option(self, option: str) -> bool:
        """Point pump discharge at the zone named `option` (or disable). False if not found."""
        if option == OPTION_OFF:
            self.controller.pump_discharge.update_config(None, None)
            return True
        for zone in self.controller.zones.values():
            if zone.name == option:
                self.controller.pump_discharge.update_config(zone.entity_id, zone.name)
                return True
        return False

    async def async_select_option(self, option: str) -> None:
        if not self.controller or not self.controller.pump_discharge:
            _LOGGER.warning("Discharge TRV select: controller not available")
            return

        if self._apply_option(option):
            self._attr_current_option = option
            _LOGGER.info("Discharge TRV set to: %s", option)
        else:
            _LOGGER.warning("Discharge TRV select: no zone named '%s'", option)
            self._attr_current_option = OPTION_OFF

        self.async_write_ha_state()
        await self._async_persist(self._attr_current_option)


class MultiTRVHeatingSelectManager:
    """Creates all MultiTRVHeating select entities (currently just the discharge TRV)."""

    def __init__(self, controller, entry_id: Optional[str] = None,
                 controller_device: Optional[Any] = None,
                 hass: Optional[Any] = None) -> None:
        self.controller = controller
        self.entry_id = entry_id
        self.controller_device = controller_device
        self.hass = hass
        self.select_entities = [DischargeTRVSelect(controller, entry_id, controller_device, hass)]

    def get_all_entities(self) -> list:
        return self.select_entities


async def async_setup_entry(
    hass: "HomeAssistant",
    entry: "ConfigEntry",
    async_add_entities: "AddEntitiesCallback",
) -> None:
    """Create the select entities for a config entry."""
    controller = get_controller(hass, entry)
    if controller is None:
        return

    manager = MultiTRVHeatingSelectManager(
        controller, entry.entry_id, controller_device_info(entry.entry_id), hass
    )
    entities = manager.get_all_entities()
    async_add_entities(entities, update_before_add=True)
    _LOGGER.debug("Set up %d select entities for entry %s", len(entities), entry.entry_id)
