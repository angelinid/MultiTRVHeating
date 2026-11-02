"""Buttons: cancel the boiler flow override."""

import logging
from typing import Optional

try:
    from homeassistant.components.button import ButtonEntity
    from homeassistant.config_entries import ConfigEntry
    from homeassistant.core import HomeAssistant
    from homeassistant.helpers.entity_platform import AddEntitiesCallback
except ImportError:  # imported without Home Assistant (tests)
    ButtonEntity = object
    ConfigEntry = HomeAssistant = AddEntitiesCallback = None

from .const import LOGGER_NAME
from .entity import controller_device_info, get_controller, prefixed_unique_id

_LOGGER = logging.getLogger(LOGGER_NAME)


class CancelFlowOverrideButton(ButtonEntity):
    """End the flow override now and go back to the schedule."""

    _attr_has_entity_name = True
    _attr_name = "Cancel flow override"
    _attr_icon = "mdi:cancel"

    def __init__(self, controller, entry_id: Optional[str], device_info) -> None:
        self.controller = controller
        self._attr_unique_id = prefixed_unique_id(entry_id, "flow_override_cancel")
        self._attr_device_info = device_info

    async def async_press(self) -> None:
        self.controller.flow_override.cancel("cancelled by the user")
        await self.controller._calculate_and_command()


async def async_setup_entry(
    hass: "HomeAssistant",
    entry: "ConfigEntry",
    async_add_entities: "AddEntitiesCallback",
) -> None:
    controller = get_controller(hass, entry)
    if controller is None:
        return
    async_add_entities([CancelFlowOverrideButton(controller, entry.entry_id, controller_device_info(entry.entry_id))])
