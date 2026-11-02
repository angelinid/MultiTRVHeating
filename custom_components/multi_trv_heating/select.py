"""
Home Assistant select entities for MultiTRVHeating.

- Controller: which zone's TRV is held open so a valve is never left shut ("Off" disables)
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
from .entity import (
    PersistentEntityMixin,
    controller_device_info,
    get_controller,
    prefixed_unique_id,
    zone_device_info,
    zone_slug,
)

_LOGGER = logging.getLogger(LOGGER_NAME)

OPTION_OFF = "Off"
OPTION_NO_SENSOR = "None"


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
    """Choose the zone whose TRV is held open when the other valves are about to close."""

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
        """Current option according to the controller's keep-open config."""
        if not self.controller or not self.controller.keep_open:
            return OPTION_OFF
        name = self.controller.keep_open.discharge_trv_name
        if name and name != "Unknown" and name in self._attr_options:
            return name
        return OPTION_OFF

    def _apply_option(self, option: str) -> bool:
        """Point the keep-open valve at the zone named `option` (or disable). False if not found."""
        if option == OPTION_OFF:
            self.controller.keep_open.update_config(None, None)
            return True
        for zone in self.controller.zones.values():
            if zone.name == option:
                self.controller.keep_open.update_config(zone.entity_id, zone.name)
                return True
        return False

    async def async_select_option(self, option: str) -> None:
        if not self.controller or not self.controller.keep_open:
            _LOGGER.warning("Discharge TRV select: controller not available")
            return

        if self._apply_option(option):
            self._attr_current_option = option
            _LOGGER.info("Discharge TRV set to: %s", option)
        else:
            _LOGGER.warning("Discharge TRV select: no zone named '%s'", option)
            self._attr_current_option = OPTION_OFF

        # Move the hold now instead of waiting for the next control cycle
        await self.controller._calculate_and_command()

        self.async_write_ha_state()
        await self._async_persist(self._attr_current_option)


class ZoneExternalTempSelect(MultiTRVHeatingSelect):
    """Choose the room temperature sensor of one zone (instead of the TRV's own reading)."""

    STORAGE_PREFIX = "zone_ext_temp_select"

    def __init__(self, zone, controller, entry_id: Optional[str] = None,
                 device_info: Optional[Any] = None, hass: Optional[Any] = None) -> None:
        super().__init__(
            name="External temperature sensor",
            unique_id=prefixed_unique_id(entry_id, f"{zone_slug(zone.name)}_ext_temp_select"),
            icon="mdi:thermometer",
            device_info=device_info,
        )
        self.zone = zone
        self.controller = controller
        self.hass = hass
        self._attr_has_entity_name = True

        # A stored choice (including "none") wins over the sensor given when the zone was set up.
        stored = self._restore_stored()
        if stored is not None:
            controller.set_external_sensor(zone, stored or None)

    @staticmethod
    def _label(entity_id: str, name: Optional[str]) -> str:
        return f"{name} ({entity_id})" if name and name != entity_id else entity_id

    def _temperature_sensors(self) -> dict:
        """{label: entity_id} of the temperature sensors that exist now, plus the one in use."""
        found = {}
        states = self.hass.states.async_all("sensor") if self.hass else []
        for state in states:
            if state.attributes.get("device_class") == "temperature":
                found[state.entity_id] = state.attributes.get("friendly_name")
        current = self.zone.ext_temp_entity_id
        if current and current not in found:
            found[current] = None  # keep the selection valid even if the sensor is missing right now
        return {self._label(eid, name): eid for eid, name in sorted(found.items())}

    @property
    def options(self) -> list:
        return [OPTION_NO_SENSOR] + sorted(self._temperature_sensors())

    @property
    def current_option(self) -> Optional[str]:
        current = self.zone.ext_temp_entity_id
        if not current:
            return OPTION_NO_SENSOR
        for label, entity_id in self._temperature_sensors().items():
            if entity_id == current:
                return label
        return current

    async def async_select_option(self, option: str) -> None:
        entity_id = None if option == OPTION_NO_SENSOR else self._temperature_sensors().get(option)
        if option != OPTION_NO_SENSOR and entity_id is None:
            _LOGGER.warning("Zone '%s': unknown external temperature sensor '%s'", self.zone.name, option)
            return
        self.controller.set_external_sensor(self.zone, entity_id)
        await self._async_persist(entity_id or "")
        self.async_write_ha_state()
        await self.controller._calculate_and_command()


class MultiTRVHeatingSelectManager:
    """Creates all MultiTRVHeating select entities (the discharge TRV and one external temperature sensor per zone)."""

    def __init__(self, controller, entry_id: Optional[str] = None,
                 controller_device: Optional[Any] = None,
                 hass: Optional[Any] = None) -> None:
        self.controller = controller
        self.entry_id = entry_id
        self.controller_device = controller_device
        self.hass = hass
        self.select_entities = [DischargeTRVSelect(controller, entry_id, controller_device, hass)]
        self.select_entities += [
            ZoneExternalTempSelect(
                zone, controller, entry_id,
                zone_device_info(entry_id, zone_entity_id, zone.name) if entry_id else None, hass,
            )
            for zone_entity_id, zone in controller.zones.items()
        ]

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
