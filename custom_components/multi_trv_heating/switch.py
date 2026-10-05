"""
Home Assistant switch entities for MultiTRVHeating.

- Per zone: priority (ON = high, OFF = low)
- Controller: pre-heating enable, component enable
"""

import logging
from typing import Any, Optional

try:
    from homeassistant.components.switch import SwitchEntity
    from homeassistant.config_entries import ConfigEntry
    from homeassistant.core import HomeAssistant
    from homeassistant.helpers.entity_platform import AddEntitiesCallback
except ImportError:
    # For testing without Home Assistant
    SwitchEntity = object
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


class MultiTRVHeatingSwitch(PersistentEntityMixin, SwitchEntity):
    """
    Base switch: turning on/off calls _apply(value); if it returns True the new
    state is written to HA and persisted.
    """

    def __init__(self, name: str, unique_id: str, icon: Optional[str] = None,
                 device_info: Optional[Any] = None) -> None:
        self._attr_name = name
        self._attr_unique_id = unique_id
        self._attr_icon = icon
        self._attr_device_info = device_info
        self._is_on = False

    def _apply(self, value: bool) -> bool:
        """Apply the new value to the controller/zone. Return False if not applicable."""
        raise NotImplementedError

    async def _async_set(self, value: bool) -> None:
        if not self._apply(value):
            return
        self.async_write_ha_state()
        await self._async_persist(value)

    async def async_turn_on(self, **kwargs) -> None:
        await self._async_set(True)

    async def async_turn_off(self, **kwargs) -> None:
        await self._async_set(False)


class ZonePrioritySwitch(MultiTRVHeatingSwitch):
    """Zone priority: ON = high priority, OFF = low priority (needs 100% opening or aggregation)."""

    STORAGE_PREFIX = "zone_priority"

    def __init__(self, zone_name: str, zone_entity_id: str, zone,
                 entry_id: Optional[str] = None, device_info: Optional[Any] = None,
                 hass: Optional[Any] = None) -> None:
        super().__init__(
            f"{zone_name} Priority (High)",
            prefixed_unique_id(entry_id, f"multi_trv_{zone_slug(zone_name)}_priority_switch"),
            "mdi:priority-high",
            device_info,
        )
        self.zone = zone
        self.zone_entity_id = zone_entity_id
        self.hass = hass

        stored_value = self._restore_stored()
        if stored_value is not None:
            self._attr_is_on = stored_value
            if self.zone:
                self.zone.is_high_priority = stored_value
            _LOGGER.info("Restored zone '%s' priority from storage: %s", zone_name, stored_value)
        else:
            self._attr_is_on = zone.is_high_priority if zone else True

    @property
    def is_on(self) -> bool:
        return self.zone.is_high_priority if self.zone else False

    def _apply(self, value: bool) -> bool:
        if not self.zone:
            return False
        self.zone.is_high_priority = value
        self._attr_is_on = value
        _LOGGER.info("Zone '%s' set to %s priority", self.zone.name, "high" if value else "low")
        return True


class PreheatingEnableSwitch(MultiTRVHeatingSwitch):
    """Enable/disable pre-heating. State always mirrors controller.preheating.is_enabled."""

    STORAGE_PREFIX = "preheating_enabled"

    def __init__(self, controller, entry_id: Optional[str] = None,
                 controller_device: Optional[Any] = None,
                 hass: Optional[Any] = None) -> None:
        super().__init__(
            name="Preheating",
            unique_id=f"{entry_id}_preheating_enable" if entry_id else "multi_trv_preheating_enable",
            icon="mdi:fire",
            device_info=controller_device,
        )
        self.controller = controller
        self.hass = hass
        self._attr_has_entity_name = True

        stored_value = self._restore_stored()
        if stored_value is not None:
            self._is_on = stored_value
            if self._preheating:
                self._preheating.is_enabled = stored_value
            _LOGGER.info("Restored pre-heating enabled from storage: %s", stored_value)
        else:
            self._is_on = self._preheating.is_enabled if self._preheating else False

    @property
    def _preheating(self):
        return self.controller.preheating if self.controller else None

    @property
    def is_on(self) -> bool:
        if self._preheating:
            return self._preheating.is_enabled
        return self._is_on

    def _apply(self, value: bool) -> bool:
        if not self._preheating:
            return False
        self._preheating.is_enabled = value
        self._is_on = value
        _LOGGER.info("Pre-heating %s via switch", "enabled" if value else "disabled")
        return True


class ComponentEnableSwitch(MultiTRVHeatingSwitch):
    """Master switch: when OFF the controller ignores state changes and stops commanding the boiler."""

    STORAGE_PREFIX = "component_enabled"

    def __init__(self, controller, entry_id: Optional[str] = None,
                 controller_device: Optional[Any] = None,
                 hass: Optional[Any] = None) -> None:
        super().__init__(
            name="Component Enable",
            unique_id=f"{entry_id}_component_enable" if entry_id else "multi_trv_component_enable",
            icon="mdi:power",
            device_info=controller_device,
        )
        self.controller = controller
        self.hass = hass
        self._attr_has_entity_name = True

        stored_value = self._restore_stored()
        if stored_value is not None:
            _LOGGER.info("Restored component enabled from storage: %s", stored_value)
        else:
            stored_value = True  # Default: enabled
        self._is_on = stored_value
        self.controller.component_enabled = stored_value

    @property
    def is_on(self) -> bool:
        return self._is_on

    def _apply(self, value: bool) -> bool:
        self.controller.component_enabled = value
        self._is_on = value
        _LOGGER.info("MultiTRVHeating component %s", "enabled" if value else "disabled")
        return True


async def async_setup_entry(
    hass: "HomeAssistant",
    entry: "ConfigEntry",
    async_add_entities: "AddEntitiesCallback",
) -> None:
    """Create zone priority switches plus the controller pre-heating/component switches."""
    controller = get_controller(hass, entry)
    if controller is None:
        return

    switches = [
        ZonePrioritySwitch(
            zone.name, zone_entity_id, zone, entry.entry_id,
            zone_device_info(entry.entry_id, zone_entity_id, zone.name), hass,
        )
        for zone_entity_id, zone in controller.zones.items()
    ]

    # NOTE: differs from the controller device name used by the other platforms; kept as-is.
    controller_device = controller_device_info(
        entry.entry_id,
        name="MultiTRVHeating Controller",
        manufacturer="Custom",
        model="Multi-Zone TRV Heating",
    )
    switches.append(PreheatingEnableSwitch(controller, entry.entry_id, controller_device, hass))
    switches.append(ComponentEnableSwitch(controller, entry.entry_id, controller_device, hass))

    async_add_entities(switches, update_before_add=True)
    _LOGGER.debug("Set up %d switches for entry %s", len(switches), entry.entry_id)
