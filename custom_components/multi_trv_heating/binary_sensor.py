"""Binary sensors: is the boiler flow override active (the dashboard shows its cancel button on it)."""

import logging
from typing import Any, Optional

try:
    from homeassistant.components.binary_sensor import BinarySensorEntity
    from homeassistant.config_entries import ConfigEntry
    from homeassistant.core import HomeAssistant
    from homeassistant.helpers.entity_platform import AddEntitiesCallback
except ImportError:  # imported without Home Assistant (tests)
    BinarySensorEntity = object
    ConfigEntry = HomeAssistant = AddEntitiesCallback = None

from .const import LOGGER_NAME
from .entity import controller_device_info, get_controller, prefixed_unique_id

_LOGGER = logging.getLogger(LOGGER_NAME)


class FlowOverrideActiveSensor(BinarySensorEntity):
    """On while a flow override is running. Updated after every control cycle."""

    _attr_has_entity_name = True
    _attr_name = "Flow override active"
    _attr_should_poll = False
    _attr_icon = "mdi:water-boiler-alert"

    def __init__(self, controller, entry_id: Optional[str], device_info) -> None:
        self.controller = controller
        self._attr_unique_id = prefixed_unique_id(entry_id, "flow_override_active")
        self._attr_device_info = device_info

    async def async_added_to_hass(self) -> None:
        self.async_on_remove(self.controller.add_listener(self._refresh))

    def _refresh(self) -> None:
        if self.hass is not None:
            self.async_write_ha_state()

    @property
    def is_on(self) -> bool:
        return self.controller.flow_override.active

    @property
    def extra_state_attributes(self) -> dict[str, Any]:
        override = self.controller.flow_override
        now = self.controller.now()
        return {"status": override.status(now), "remaining_minutes": round(override.remaining(now) / 60, 1)}


async def async_setup_entry(
    hass: "HomeAssistant",
    entry: "ConfigEntry",
    async_add_entities: "AddEntitiesCallback",
) -> None:
    controller = get_controller(hass, entry)
    if controller is None:
        return
    async_add_entities([FlowOverrideActiveSensor(controller, entry.entry_id, controller_device_info(entry.entry_id))])
