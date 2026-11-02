"""One climate entity per zone: the room temperature and the one place to set its target."""

import logging
from typing import Any, Optional

try:
    from homeassistant.components.climate import (
        ClimateEntity,
        ClimateEntityFeature,
        HVACAction,
        HVACMode,
    )
    from homeassistant.config_entries import ConfigEntry
    from homeassistant.const import ATTR_TEMPERATURE, UnitOfTemperature
    from homeassistant.core import HomeAssistant
    from homeassistant.helpers.entity_platform import AddEntitiesCallback
    from homeassistant.helpers.event import async_track_state_change_event
except ImportError:  # imported without Home Assistant (tests)
    ClimateEntity = object
    ClimateEntityFeature = None
    HVACAction = HVACMode = ConfigEntry = HomeAssistant = AddEntitiesCallback = None
    ATTR_TEMPERATURE = "temperature"
    UnitOfTemperature = None
    async_track_state_change_event = None

from .const import LOGGER_NAME
from .entity import PersistentEntityMixin, get_controller, prefixed_unique_id, zone_device_info

_LOGGER = logging.getLogger(LOGGER_NAME)

PRESET_MANUAL = "manual"
PRESET_BOOST = "boost"


class ZoneClimate(PersistentEntityMixin, ClimateEntity):
    """
    The zone as one thermostat. Its target and mode are written to every valve; the temperature
    is the zone temperature (external sensor, else the average of the valves).

    Presets: "boost" (also shown while any valve boosts) starts the TRV boost on all valves.
    "manual" does nothing: scenes store it, and restoring a scene must not cancel a boost.
    """

    STORAGE_PREFIX = "zone_climate_target"
    _attr_has_entity_name = True
    _attr_name = "Thermostat"
    _attr_should_poll = False
    _attr_min_temp = 5.0
    _attr_max_temp = 35.0
    _attr_target_temperature_step = 0.5
    _attr_preset_modes = [PRESET_MANUAL, PRESET_BOOST]

    def __init__(self, zone, controller, entry_id: Optional[str], device_info: Optional[Any]) -> None:
        self.zone = zone
        self.controller = controller
        self._attr_unique_id = prefixed_unique_id(entry_id, f"{zone.zone_id.replace('.', '_')}_zone_climate")
        self._attr_device_info = device_info
        if UnitOfTemperature is not None:
            self._attr_temperature_unit = UnitOfTemperature.CELSIUS
            self._attr_hvac_modes = [HVACMode.HEAT, HVACMode.OFF]
            self._attr_supported_features = (
                ClimateEntityFeature.TARGET_TEMPERATURE
                | ClimateEntityFeature.PRESET_MODE
                | ClimateEntityFeature.TURN_ON
                | ClimateEntityFeature.TURN_OFF
            )
        # Shown until the valves report; they win from then on (a change made elsewhere is adopted)
        stored = self._restore_stored()
        if stored is not None and zone.desired_target is None:
            zone.desired_target = float(stored)

    async def async_added_to_hass(self) -> None:
        watched = [v.entity_id for v in self.zone.valves]
        if self.zone.ext_temp_entity_id:
            watched.append(self.zone.ext_temp_entity_id)
        if async_track_state_change_event is not None:
            self.async_on_remove(async_track_state_change_event(self.hass, watched, self._async_changed))

    async def _async_changed(self, _event) -> None:
        self.async_write_ha_state()

    @property
    def current_temperature(self) -> Optional[float]:
        return round(self.zone.current_temp, 1)

    @property
    def target_temperature(self) -> Optional[float]:
        return self.zone.target_temp

    @property
    def hvac_mode(self):
        return HVACMode.OFF if self.zone.hvac_mode == "off" else HVACMode.HEAT

    @property
    def hvac_action(self):
        if self.zone.hvac_mode == "off":
            return HVACAction.OFF
        heating = self.zone.measured_opening > 0 and self.zone.current_error > 0
        return HVACAction.HEATING if heating else HVACAction.IDLE

    @property
    def preset_mode(self) -> str:
        return PRESET_BOOST if self.zone.boosted else PRESET_MANUAL

    @property
    def extra_state_attributes(self) -> dict[str, Any]:
        state = self.zone.export_zone_state()
        valves = self.zone.valves
        return {
            "temperature_source": state["temperature_source"],
            "valves": [v.entity_id for v in valves],
            "valve_temperatures": {v.name: round(v.trv_temp, 1) for v in valves},
            "valve_positions": {v.name: round(v.measured_opening, 1) for v in valves},
            "applied_offsets": {v.name: round(v.applied_offset(), 1) for v in valves},
            "unavailable_valves": [v.name for v in valves if not v.available],
        }

    async def async_set_temperature(self, **kwargs: Any) -> None:
        if (value := kwargs.get(ATTR_TEMPERATURE)) is None:
            return
        await self.controller.async_set_zone_target(self.zone, float(value))
        await self._async_persist(float(value))
        self.async_write_ha_state()

    async def async_set_hvac_mode(self, hvac_mode) -> None:
        await self.controller.async_set_zone_hvac_mode(self.zone, "off" if hvac_mode == HVACMode.OFF else "heat")
        self.async_write_ha_state()

    async def async_turn_on(self) -> None:
        await self.async_set_hvac_mode(HVACMode.HEAT)

    async def async_turn_off(self) -> None:
        await self.async_set_hvac_mode(HVACMode.OFF)

    async def async_set_preset_mode(self, preset_mode: str) -> None:
        if preset_mode == PRESET_BOOST:
            await self.controller.async_boost_zone(self.zone)
        self.async_write_ha_state()


async def async_setup_entry(
    hass: "HomeAssistant",
    entry: "ConfigEntry",
    async_add_entities: "AddEntitiesCallback",
) -> None:
    """One thermostat per zone."""
    controller = get_controller(hass, entry)
    if controller is None:
        return
    entities = [
        ZoneClimate(zone, controller, entry.entry_id, zone_device_info(entry.entry_id, zone_id, zone.name))
        for zone_id, zone in controller.rooms.items()
    ]
    async_add_entities(entities)
