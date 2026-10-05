"""
Home Assistant number entities for MultiTRVHeating.

- Per zone: floor area (m²)
- Controller: pre-heating end hour / minute, pre-heating tuning constant
"""

import logging
from datetime import datetime, timedelta
from typing import Any, Optional

try:
    from homeassistant.components.number import NumberEntity
    from homeassistant.config_entries import ConfigEntry
    from homeassistant.const import UnitOfArea
    from homeassistant.core import HomeAssistant
    from homeassistant.helpers.entity_platform import AddEntitiesCallback
except ImportError:
    # For testing without Home Assistant
    NumberEntity = object
    UnitOfArea = "m²"
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
from .preheating import TUNING_CONSTANT_MAX, TUNING_CONSTANT_MIN

_LOGGER = logging.getLogger(LOGGER_NAME)

MAX_FLOOR_AREA = 500.0  # m²


class MultiTRVHeatingNumber(PersistentEntityMixin, NumberEntity):
    """Base number entity for MultiTRVHeating settings."""

    def __init__(self, name: str, unique_id: str, icon: Optional[str] = None,
                 unit: Optional[str] = None, min_val: float = 0.0,
                 max_val: float = 1000.0, step: float = 0.1,
                 device_info: Optional[Any] = None) -> None:
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
    """Zone floor area (m²), used by the pre-heating thermal load calculation."""

    STORAGE_PREFIX = "zone_floor_area"

    def __init__(self, zone_name: str, zone_entity_id: str, zone,
                 entry_id: Optional[str] = None, device_info: Optional[Any] = None,
                 hass: Optional[Any] = None) -> None:
        # UnitOfArea may be an enum (HA) or a plain string (tests)
        unit_str = UnitOfArea.value if hasattr(UnitOfArea, 'value') else str(UnitOfArea) if UnitOfArea else "m²"
        super().__init__(
            f"{zone_name} Floor Area",
            prefixed_unique_id(entry_id, f"multi_trv_{zone_slug(zone_name)}_area_m2"),
            icon="mdi:ruler-square",
            unit=unit_str,
            min_val=0.0,
            max_val=MAX_FLOOR_AREA,
            step=0.1,
            device_info=device_info,
        )
        self.zone = zone
        self.zone_entity_id = zone_entity_id
        self.hass = hass

        self._attr_native_value = zone.floor_area_m2 if zone else 0.0
        stored_value = self._restore_stored()
        if stored_value is not None:
            try:
                self.zone.floor_area_m2 = max(0.0, min(MAX_FLOOR_AREA, float(stored_value)))
                self._attr_native_value = self.zone.floor_area_m2
                _LOGGER.info("Restored zone '%s' floor area from storage: %.2f m²", zone_name, self.zone.floor_area_m2)
            except (ValueError, TypeError):
                pass

    @property
    def native_value(self) -> Optional[float]:
        return self.zone.floor_area_m2 if self.zone else None

    async def async_set_native_value(self, value: float) -> None:
        if not self.zone:
            return
        self.zone.floor_area_m2 = max(0.0, min(MAX_FLOOR_AREA, value))
        self._attr_native_value = self.zone.floor_area_m2
        self.async_write_ha_state()
        await self._async_persist(self.zone.floor_area_m2)
        _LOGGER.info("Zone '%s' floor area set to %.2f m²", self.zone.name, self.zone.floor_area_m2)


def _next_occurrence(hour: int, minute: int) -> datetime:
    """The next datetime (today or tomorrow) at hour:minute."""
    now = datetime.now()
    end_time = now.replace(hour=hour, minute=minute, second=0, microsecond=0)
    if end_time <= now:
        end_time += timedelta(days=1)
    return end_time


class _PreheatingEndTimeNumber(MultiTRVHeatingNumber):
    """
    One component (FIELD = 'hour' or 'minute') of the pre-heating end time.

    Setting it rebuilds preheating_end_time as the next occurrence of hour:minute,
    keeping the other component from the current end time (or from now).
    """

    FIELD: str
    OTHER_FIELD: str
    # Fallback for OTHER_FIELD when restoring with no end time set (None = take it from now).
    RESTORE_OTHER_DEFAULT: Optional[int] = None

    def __init__(self, controller, name: str, unique_id: str, unit: str, max_val: float,
                 entry_id: Optional[str], controller_device_info: Optional[Any],
                 hass: Optional[Any]) -> None:
        super().__init__(
            name,
            prefixed_unique_id(entry_id, unique_id),
            icon="mdi:clock-outline",
            unit=unit,
            min_val=0.0,
            max_val=max_val,
            step=1.0,
            device_info=controller_device_info,
        )
        self.controller = controller
        self.hass = hass

        now = datetime.now()
        self._attr_native_value = float(getattr(now, self.FIELD))
        stored_value = self._restore_stored()
        if stored_value is not None:
            try:
                value = int(float(stored_value))
                other_default = self.RESTORE_OTHER_DEFAULT
                if other_default is None:
                    other_default = getattr(datetime.now(), self.OTHER_FIELD)
                self._set_end_time(value, other_default)
                self._attr_native_value = float(value)
                _LOGGER.info("Restored pre-heating end %s from storage: %d", self.FIELD, value)
            except (ValueError, AttributeError):
                pass

    @property
    def _end_time(self) -> Optional[datetime]:
        return self.controller.preheating.preheating_end_time

    def _set_end_time(self, value: int, other_default: int) -> datetime:
        """Combine `value` with the other component (from end time, else other_default)."""
        other = getattr(self._end_time, self.OTHER_FIELD) if self._end_time else other_default
        parts = {self.FIELD: value, self.OTHER_FIELD: other}
        end_time = _next_occurrence(parts["hour"], parts["minute"])
        self.controller.preheating.preheating_end_time = end_time
        return end_time

    @property
    def native_value(self) -> Optional[float]:
        if self._end_time:
            return float(getattr(self._end_time, self.FIELD))
        return float(getattr(datetime.now(), self.FIELD))

    async def async_set_native_value(self, value: float) -> None:
        value = int(value)
        end_time = self._set_end_time(value, getattr(datetime.now(), self.OTHER_FIELD))
        self._attr_native_value = float(value)
        self.async_write_ha_state()
        await self._async_persist(value)
        _LOGGER.info("Pre-heating end time set to %02d:%02d", end_time.hour, end_time.minute)


class PreheatingEndTimeHour(_PreheatingEndTimeNumber):
    """Hour (0-23) at which pre-heating should finish."""

    STORAGE_PREFIX = "preheating_end_hour"
    FIELD = "hour"
    OTHER_FIELD = "minute"
    RESTORE_OTHER_DEFAULT = 0

    def __init__(self, controller, entry_id: Optional[str] = None,
                 controller_device_info: Optional[Any] = None,
                 hass: Optional[Any] = None) -> None:
        super().__init__(controller, "Preheating End Hour", "multi_trv_preheating_end_hour",
                         "h", 23.0, entry_id, controller_device_info, hass)


class PreheatingEndTimeMinute(_PreheatingEndTimeNumber):
    """Minute (0-59) at which pre-heating should finish."""

    STORAGE_PREFIX = "preheating_end_minute"
    FIELD = "minute"
    OTHER_FIELD = "hour"

    def __init__(self, controller, entry_id: Optional[str] = None,
                 controller_device_info: Optional[Any] = None,
                 hass: Optional[Any] = None) -> None:
        super().__init__(controller, "Preheating End Minute", "multi_trv_preheating_end_minute",
                         "min", 59.0, entry_id, controller_device_info, hass)


class PreheatingTuningNumber(MultiTRVHeatingNumber):
    """
    Pre-heating tuning constant (higher = more aggressive). Set from the UI it
    applies directly; pre-heating cycles also refine it via a low-pass filter.
    """

    STORAGE_PREFIX = "preheating_tuning_constant"

    def __init__(self, controller, entry_id: Optional[str] = None,
                 controller_device_info: Optional[Any] = None,
                 hass: Optional[Any] = None) -> None:
        super().__init__(
            "Preheating Tuning Constant",
            prefixed_unique_id(entry_id, "multi_trv_preheating_tuning_constant"),
            icon="mdi:tune",
            unit=None,
            min_val=TUNING_CONSTANT_MIN,
            max_val=TUNING_CONSTANT_MAX,
            step=0.1,
            device_info=controller_device_info,
        )
        self.controller = controller
        self.hass = hass

        self._attr_native_value = self.controller.preheating.tuning_constant
        stored_value = self._restore_stored()
        if stored_value is not None:
            try:
                value = max(TUNING_CONSTANT_MIN, min(TUNING_CONSTANT_MAX, float(stored_value)))
                self.controller.preheating.tuning_constant = value
                self._attr_native_value = value
                _LOGGER.info("Restored pre-heating tuning constant from storage: %.3f", value)
            except (ValueError, TypeError):
                pass

    @property
    def native_value(self) -> Optional[float]:
        return round(self.controller.preheating.tuning_constant, 3)

    async def async_set_native_value(self, value: float) -> None:
        self.controller.preheating.tuning_constant = value
        self._attr_native_value = round(value, 3)
        self.async_write_ha_state()
        await self._async_persist(value)
        _LOGGER.info("Pre-heating tuning constant set to %.3f via UI", value)


async def async_setup_entry(
    hass: "HomeAssistant",
    entry: "ConfigEntry",
    async_add_entities: "AddEntitiesCallback",
) -> None:
    """Create the controller pre-heating numbers and a floor area number per zone."""
    controller = get_controller(hass, entry)
    if controller is None:
        return

    device_info = controller_device_info(entry.entry_id)
    numbers = [
        PreheatingEndTimeHour(controller, entry.entry_id, device_info, hass),
        PreheatingEndTimeMinute(controller, entry.entry_id, device_info, hass),
        PreheatingTuningNumber(controller, entry.entry_id, device_info, hass),
    ]
    numbers.extend(
        ZoneAreaNumber(
            zone.name, zone_entity_id, zone, entry.entry_id,
            zone_device_info(entry.entry_id, zone_entity_id, zone.name), hass,
        )
        for zone_entity_id, zone in controller.zones.items()
    )

    async_add_entities(numbers, update_before_add=True)
    _LOGGER.debug("Set up %d numbers for entry %s", len(numbers), entry.entry_id)
