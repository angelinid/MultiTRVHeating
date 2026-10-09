"""
MIT License

Copyright (c) 2025

Permission is hereby granted, free of charge, to any person obtaining a copy
of this software and associated documentation files (the "Software"), to deal
in the Software without restriction, including without limitation the rights
to use, copy, modify, merge, publish, distribute, sublicense, and/or sell
copies of the Software, and to permit persons to whom the Software is
furnished to do so, subject to the following conditions:

The above copyright notice and this permission notice shall be included in all
copies or substantial portions of the Software.

THE SOFTWARE IS PROVIDED "AS IS", WITHOUT WARRANTY OF ANY KIND, EXPRESS OR
IMPLIED, INCLUDING BUT NOT LIMITED TO THE WARRANTIES OF MERCHANTABILITY,
FITNESS FOR A PARTICULAR PURPOSE AND NONINFRINGEMENT. IN NO EVENT SHALL THE
AUTHORS OR COPYRIGHT HOLDERS BE LIABLE FOR ANY CLAIM, DAMAGES OR OTHER
LIABILITY, WHETHER IN AN ACTION OF CONTRACT, TORT OR OTHERWISE, ARISING FROM,
OUT OF OR IN CONNECTION WITH THE SOFTWARE OR THE USE OR OTHER DEALINGS IN THE
SOFTWARE.
"""

import logging
import time
from typing import TYPE_CHECKING, Optional

try:
    from .const import LOGGER_NAME
    from .trv_model import opening_for_error
except ImportError:
    from const import LOGGER_NAME
    from trv_model import opening_for_error

if TYPE_CHECKING:
    from .master_controller import MasterController

_LOGGER = logging.getLogger(LOGGER_NAME)

# TRV temperature offset (calibration) values, °C
DEFAULT_TEMP_OFFSET = 0.0      # Neutral
HEATING_TEMP_OFFSET = -2.0     # Applied while heating so the TRV reads colder and opens further
HEATING_TEMP_OFFSET_THRESHOLD = 75.0  # Valve opening (%) above which the heating offset applies
HOLD_TEMP_OFFSET = -9.0        # Held-open valve: the TRV reads 9 °C colder, so it opens fully

# Low-priority zones only demand heat on their own at full opening
LOW_PRIORITY_MIN_OPENING = 100.0

# An external temperature reading older than this (s) is ignored in favour of the TRV's own
EXT_TEMP_MAX_AGE = 1800.0

# The TRV adds its calibration offset to the temperature it reports, immediately (confirmed on the
# live install: -1 °C of calibration drops the reading by 1 °C at once).
TRV_REPORTS_CALIBRATED_TEMP = True

# Hold reasons: why a zone's valve is being forced open
HOLD_SWITCH = "switch"   # User/schedule asked for it (e.g. a child's room overnight)
HOLD_AUTO = "auto"       # Keep-open controller: never leave every valve closed


class ZoneWrapper:
    """
    State of one heating zone (a room with a TRV).

    Tracks current/target temperature from the climate entity, valve opening
    from the TRV position sensor, an optional external temperature sensor and
    the TRV temperature offset we push to its calibration entity.

    Temperature used for control ("effective", stored in current_temp):
    - the external sensor while its reading is fresh (younger than EXT_TEMP_MAX_AGE), else
    - the TRV's reported temperature minus the offset actually applied to it, because the TRV
      adds its calibration to what it reports and our own offsets must not look like a cold room.

    Valve opening used for control ("effective", effective_opening):
    - the measured opening for a normal zone,
    - a virtual opening derived from the room temperature error (trv_model) for a held zone,
      whose valve is forced open and therefore carries no information about demand.

    Demand:
    - High priority (or held by switch): demanding when below target, or when heating_status is
      'cooling' (not while held)
    - Low priority: demanding only at LOW_PRIORITY_MIN_OPENING (or via aggregation in MasterController)
    - Demand metric (0..1) = effective opening, or 0 when at/above target

    Offset policy (desired_offset): HOLD_TEMP_OFFSET while held, otherwise HEATING_TEMP_OFFSET
    once the valve opened past the threshold, reset to DEFAULT_TEMP_OFFSET when it fully closes.
    temperature_offset is the offset the zone wants on its TRV; the controller writes it to the
    device (pushed_offset / device_offset track what the device actually has).
    """

    def __init__(self, entity_id: str, name: str, floor_area_m2: float = 0.0,
                 is_high_priority: bool = True, trv_position_entity_id: Optional[str] = None,
                 temp_calib_entity_id: Optional[str] = None, ext_temp_entity_id: Optional[str] = None,
                 my_master_controller: Optional['MasterController'] = None) -> None:
        self.entity_id = entity_id
        self.name = name
        self.floor_area_m2 = floor_area_m2
        self.is_high_priority = is_high_priority

        # Temperatures (°C); current_temp is the effective temperature (see class docstring),
        # trv_temp what the TRV reported (includes its calibration offset).
        # current_error = target - current (positive = needs heat)
        self.trv_temp = 20.0
        self.current_temp = 20.0
        self.target_temp = 20.0
        self.current_error = 0.0

        # Optional external temperature sensor
        self.ext_temp_entity_id = ext_temp_entity_id
        self.ext_current_temp: Optional[float] = None   # None until the sensor reports
        self.ext_last_update = 0.0

        # TRV valve (measured opening, 0-100 %)
        self.trv_position_entity_id = trv_position_entity_id
        self.trv_opening_percent = 0.0
        self.position_available = True    # False while the position sensor is unavailable/unknown
        self.opening_falling = False      # last position report was lower than the one before
        self.is_demanding_heat = False

        # 'heating' / 'cooling' / 'idle' - updated only from valve movement, see update_trv_opening()
        self.heating_status = 'idle'

        # TRV temperature calibration (offset): wanted, last written, and as reported by the device
        self.temp_calib_entity_id = temp_calib_entity_id
        self.temperature_offset = DEFAULT_TEMP_OFFSET      # what this zone wants on its TRV
        self.pushed_offset: Optional[float] = None         # last value we wrote (None = never)
        self.device_offset: Optional[float] = None         # last value the device reported
        self.offset_in_flight = False                      # written, device has not confirmed it yet
        self.last_offset_write = -1e9                      # controller clock time of the last write
        self.drift_retries = 0                             # rewrites of a calibration the device lost
        self._policy_offset = DEFAULT_TEMP_OFFSET          # opening-based offset (no hold)

        # Held-open valve: reason -> controller clock time it started
        self.hold_since: dict[str, float] = {}
        self.hold_released_at: Optional[float] = None

        self.last_update_time = time.time()
        self.master_controller = my_master_controller

    # ------------------------------------------------------------------
    # Hold (valve forced open)
    # ------------------------------------------------------------------

    @property
    def held(self) -> bool:
        return bool(self.hold_since)

    def has_hold(self, reason: str) -> bool:
        return reason in self.hold_since

    @property
    def counts_as_high_priority(self) -> bool:
        """A zone held by switch asks for heat on its own, whatever its configured priority."""
        return self.is_high_priority or HOLD_SWITCH in self.hold_since

    def set_hold(self, reason: str, active: bool, now: float) -> bool:
        """
        Add or remove a hold reason. Returns True if the wanted offset changed.

        `now` is the controller clock (MasterController.now()).
        """
        was_held = self.held
        if active:
            self.hold_since.setdefault(reason, now)
        else:
            self.hold_since.pop(reason, None)

        if self.held and not was_held:
            self.heating_status = 'idle'   # the forced opening is not a demand signal
        if was_held and not self.held:
            self.hold_released_at = now
            self.heating_status = 'idle'

        changed = self._apply_desired_offset()
        self._refresh_temperature()
        return changed

    def hold_age(self, reason: str, now: float) -> float:
        """Seconds the given hold reason has been active (0 if it is not)."""
        started = self.hold_since.get(reason)
        return 0.0 if started is None else max(0.0, now - started)

    def recently_released(self, now: float, window: float) -> bool:
        """True if a hold ended less than `window` seconds ago (valve still closing)."""
        return (
            not self.held
            and self.hold_released_at is not None
            and now - self.hold_released_at < window
        )

    # ------------------------------------------------------------------
    # Offset policy
    # ------------------------------------------------------------------

    def desired_offset(self) -> float:
        """The one place that decides which offset this zone's TRV should have."""
        return HOLD_TEMP_OFFSET if self.held else self._policy_offset

    def _apply_desired_offset(self) -> bool:
        """Set temperature_offset to the desired value; True if it changed."""
        desired = self.desired_offset()
        if self.temperature_offset == desired:
            return False
        self.temperature_offset = desired
        return True

    def offset_to_write(self, hold_writes_enabled: bool = True) -> float:
        """Offset to put on the TRV: the desired one, but no hold offset while hold writes are off."""
        if self.held and not hold_writes_enabled:
            return self._policy_offset
        return self.desired_offset()

    def reset_policy_offset(self) -> bool:
        """Boiler off: drop the opening-based (heating) offset. A hold is untouched."""
        self._policy_offset = DEFAULT_TEMP_OFFSET
        return self._apply_desired_offset()

    def applied_offset(self) -> float:
        """
        The offset the TRV is believed to have right now. A write takes effect on the TRV's reading
        at once, before the device reports it back, so a write in flight counts as applied.
        """
        if self.offset_in_flight and self.pushed_offset is not None:
            return self.pushed_offset
        if self.device_offset is not None:
            return self.device_offset
        if self.pushed_offset is not None:
            return self.pushed_offset
        return self.temperature_offset

    # ------------------------------------------------------------------
    # Inputs
    # ------------------------------------------------------------------

    def update_from_state(self, new_state) -> None:
        """Update current/target temperature from a climate entity state."""
        if not new_state or not new_state.attributes:
            _LOGGER.warning("Received invalid state for zone '%s'", self.name)
            return

        try:
            current = new_state.attributes.get("current_temperature")
            # Target: 'temperature', then 'target_temp', then the state value itself
            target = new_state.attributes.get("temperature")
            if target is None:
                target = new_state.attributes.get("target_temp")
            if target is None:
                target = new_state.state

            if current is not None:
                self.trv_temp = float(current)
            if target is not None:
                self.target_temp = float(target)

            self.last_update_time = time.time()
            self._refresh_temperature()

            _LOGGER.debug(
                "Zone '%s': current=%.1f°C, target=%.1f°C, error=%.1f°C, opening=%.0f%%, demanding=%s",
                self.name, self.current_temp, self.target_temp, self.current_error,
                self.trv_opening_percent, self.is_demanding_heat,
            )
        except (ValueError, TypeError, AttributeError) as e:
            if new_state.state in ("unknown", "unavailable"):
                # Expected for a few seconds after HA boots, before entities report
                _LOGGER.debug("Zone '%s' not ready (state %s)", self.name, new_state.state)
            else:
                _LOGGER.warning("Error parsing state for zone '%s': %s", self.name, e)

    def update_trv_opening(self, opening_percent: float) -> bool:
        """
        Update the measured valve opening (0-100%) and the heating status / offset policy.

        - Valve closing           -> heating_status = 'heating'
        - Valve opening past threshold -> heating_status = 'cooling', policy offset = HEATING_TEMP_OFFSET
        - Valve fully closed      -> policy offset reset to DEFAULT_TEMP_OFFSET

        While the zone is held the status and policy offset are left alone: the opening is
        forced and says nothing about the room.

        Returns True if the wanted temperature offset changed and must be pushed to the TRV.
        """
        opening_percent = max(0.0, min(100.0, opening_percent))

        if not self.held:
            if self.trv_opening_percent > opening_percent:
                self.heating_status = 'heating'
            elif self.trv_opening_percent < opening_percent and opening_percent > HEATING_TEMP_OFFSET_THRESHOLD:
                # Original note: unclear why the threshold is needed here; it interacts with
                # the 'cooling' clause in _update_demand_metric().
                self.heating_status = 'cooling'
                self._policy_offset = HEATING_TEMP_OFFSET

        if opening_percent < self.trv_opening_percent:
            self.opening_falling = True
        elif opening_percent > self.trv_opening_percent:
            self.opening_falling = False
        self.trv_opening_percent = opening_percent
        self.position_available = True

        if self.trv_opening_percent == 0.0:
            self._policy_offset = DEFAULT_TEMP_OFFSET

        offset_changed = self._apply_desired_offset()
        self._update_demand_metric()

        _LOGGER.debug(
            "Zone '%s': opening=%.0f%%, status=%s, offset=%.1f°C, demanding=%s (current=%.1f°C, target=%.1f°C)",
            self.name, self.trv_opening_percent, self.heating_status, self.temperature_offset,
            self.is_demanding_heat, self.current_temp, self.target_temp,
        )
        return offset_changed

    def update_external_temperature(self, external_temp: float) -> None:
        """Store an external temperature sensor reading and refresh the effective temperature."""
        self.ext_current_temp = float(external_temp)
        self.ext_last_update = time.time()
        _LOGGER.debug("Zone '%s': external temp=%.1f°C", self.name, self.ext_current_temp)
        self._refresh_temperature()

    def update_device_offset(self, offset: Optional[float]) -> None:
        """The TRV's calibration entity reported a value (None = unavailable)."""
        self.device_offset = offset
        if offset is not None and self.pushed_offset is not None and abs(offset - self.pushed_offset) <= 0.01:
            self.offset_in_flight = False
        self._refresh_temperature()

    # ------------------------------------------------------------------
    # Derived values
    # ------------------------------------------------------------------

    @property
    def measured_opening(self) -> float:
        """Valve opening as measured; an unavailable position sensor counts as closed (safe side)."""
        return self.trv_opening_percent if self.position_available else 0.0

    def flow_opening(self, closing_threshold: float) -> float:
        """
        Measured opening as a source of flow. A valve that is closing and already at or below
        `closing_threshold` counts as closed: it will be shut before a newly held valve can open.
        """
        opening = self.measured_opening
        if self.opening_falling and opening <= closing_threshold:
            return 0.0
        return opening

    def mark_position_unavailable(self) -> None:
        """The position sensor went unavailable: treat the valve as closed until it reports again."""
        self.position_available = False
        self._update_demand_metric()

    @property
    def effective_opening(self) -> float:
        """Opening used for decisions: measured, or virtual (from the error) while held."""
        if self.held:
            return opening_for_error(self.current_error)
        return self.measured_opening

    def has_fresh_external_temperature(self) -> bool:
        """True if an external sensor is configured and has reported recently enough."""
        return (
            self.ext_temp_entity_id is not None
            and self.ext_current_temp is not None
            and time.time() - self.ext_last_update <= EXT_TEMP_MAX_AGE
        )

    def _refresh_temperature(self) -> None:
        """Recompute effective temperature, error and demand from the latest readings."""
        if self.has_fresh_external_temperature():
            self.current_temp = self.ext_current_temp
        elif TRV_REPORTS_CALIBRATED_TEMP:
            self.current_temp = self.trv_temp - self.applied_offset()
        else:
            self.current_temp = self.trv_temp
        self.current_error = self.target_temp - self.current_temp
        self._update_demand_metric()

    def _update_demand_metric(self) -> None:
        """Recalculate is_demanding_heat from priority, temperature error and valve state."""
        if self.counts_as_high_priority:
            cooling = self.heating_status == 'cooling' and not self.held
            self.is_demanding_heat = self.current_error > 0 or cooling
        else:
            self.is_demanding_heat = self.effective_opening >= LOW_PRIORITY_MIN_OPENING

    def get_demand_metric(self) -> float:
        """Demand 0.0-1.0 = effective opening fraction, or 0.0 if the zone is at/above target."""
        if self.current_error <= 0.0:
            return 0.0
        return max(0.0, min(1.0, self.effective_opening / 100.0))

    def export_zone_state(self) -> dict:
        """Snapshot of the zone state for sensors."""
        return {
            "current_temperature": round(self.current_temp, 2),
            "trv_temperature": round(self.trv_temp, 2),
            "temperature_source": "external" if self.has_fresh_external_temperature() else "trv",
            "target_temperature": round(self.target_temp, 2),
            "temperature_error": round(self.current_error, 2),
            "name": self.name,
            "floor_area_m2": round(self.floor_area_m2, 2),
            "is_high_priority": self.is_high_priority,
            "is_demanding_heat": self.is_demanding_heat,
            "trv_opening_percent": round(self.measured_opening, 1),
            "effective_opening_percent": round(self.effective_opening, 1),
            "held": self.held,
            "hold_reasons": sorted(self.hold_since),
            "temperature_offset": round(self.temperature_offset, 1),
            "applied_offset": round(self.applied_offset(), 1),
            "temp_calib_entity_id": self.temp_calib_entity_id,
            "has_external_sensor": self.ext_temp_entity_id is not None,
            "external_sensor_temperature": (
                round(self.ext_current_temp, 2) if self.ext_current_temp is not None else None
            ),
        }
