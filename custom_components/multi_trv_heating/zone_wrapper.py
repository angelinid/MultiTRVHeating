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
except ImportError:
    from const import LOGGER_NAME

if TYPE_CHECKING:
    from .master_controller import MasterController

_LOGGER = logging.getLogger(LOGGER_NAME)

# TRV temperature offset (calibration) values, °C
DEFAULT_TEMP_OFFSET = 0.0      # Neutral
HEATING_TEMP_OFFSET = -2.0     # Applied while heating so the TRV reads colder and opens further
HEATING_TEMP_OFFSET_THRESHOLD = 75.0  # Valve opening (%) above which the heating offset applies

# Low-priority zones only demand heat on their own at full opening
LOW_PRIORITY_MIN_OPENING = 100.0


class ZoneWrapper:
    """
    State of one heating zone (a room with a TRV).

    Tracks current/target temperature from the climate entity, valve opening
    from the TRV position sensor, an optional external temperature sensor and
    the TRV temperature offset we push to its calibration entity.

    Demand:
    - High priority: demanding when below target, or when heating_status is 'cooling'
    - Low priority: demanding only at LOW_PRIORITY_MIN_OPENING (or via aggregation in MasterController)
    - Demand metric (0..1) = valve opening, or 0 when at/above target

    Offset: set to HEATING_TEMP_OFFSET when the valve opens past the threshold,
    reset to DEFAULT_TEMP_OFFSET when the valve fully closes.
    """

    def __init__(self, entity_id: str, name: str, floor_area_m2: float = 0.0,
                 is_high_priority: bool = True, trv_position_entity_id: Optional[str] = None,
                 temp_calib_entity_id: Optional[str] = None, ext_temp_entity_id: Optional[str] = None,
                 my_master_controller: Optional['MasterController'] = None) -> None:
        self.entity_id = entity_id
        self.name = name
        self.floor_area_m2 = floor_area_m2
        self.is_high_priority = is_high_priority

        # Temperatures (°C); current_error = target - current (positive = needs heat)
        self.current_temp = 20.0
        self.target_temp = 20.0
        self.current_error = 0.0

        # Optional external temperature sensor
        self.ext_temp_entity_id = ext_temp_entity_id
        self.ext_current_temp = 20.0

        # TRV valve
        self.trv_position_entity_id = trv_position_entity_id
        self.trv_opening_percent = 0.0
        self.is_demanding_heat = False

        # 'heating' / 'cooling' / 'idle' - updated only from valve movement, see update_trv_opening()
        self.heating_status = 'idle'

        # TRV temperature calibration (offset) entity and current value
        self.temp_calib_entity_id = temp_calib_entity_id
        self.temperature_offset = DEFAULT_TEMP_OFFSET

        self.last_update_time = time.time()
        self.master_controller = my_master_controller

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
                self.current_temp = float(current)
            if target is not None:
                self.target_temp = float(target)

            self.current_error = self.target_temp - self.current_temp
            self.last_update_time = time.time()
            self._update_demand_metric()

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
        Update the valve opening (0-100%) and the heating status / temperature offset.

        - Valve closing           -> heating_status = 'heating'
        - Valve opening past threshold -> heating_status = 'cooling', offset = HEATING_TEMP_OFFSET
        - Valve fully closed      -> offset reset to DEFAULT_TEMP_OFFSET

        Returns True if the temperature offset changed and must be pushed to the TRV.
        """
        opening_percent = max(0.0, min(100.0, opening_percent))
        offset_changed = False

        if self.trv_opening_percent > opening_percent:
            self.heating_status = 'heating'
        elif self.trv_opening_percent < opening_percent and opening_percent > HEATING_TEMP_OFFSET_THRESHOLD:
            # Original note: unclear why the threshold is needed here; it interacts with
            # the 'cooling' clause in _update_demand_metric().
            self.heating_status = 'cooling'
            if self.temperature_offset != HEATING_TEMP_OFFSET:
                self.temperature_offset = HEATING_TEMP_OFFSET
                offset_changed = True

        self.trv_opening_percent = opening_percent

        if self.trv_opening_percent == 0.0 and self.temperature_offset != DEFAULT_TEMP_OFFSET:
            self.temperature_offset = DEFAULT_TEMP_OFFSET
            offset_changed = True

        self._update_demand_metric()

        _LOGGER.debug(
            "Zone '%s': opening=%.0f%%, status=%s, offset=%.1f°C, demanding=%s (current=%.1f°C, target=%.1f°C)",
            self.name, self.trv_opening_percent, self.heating_status, self.temperature_offset,
            self.is_demanding_heat, self.current_temp, self.target_temp,
        )
        return offset_changed

    def update_external_temperature(self, external_temp: float) -> None:
        """Store an external temperature sensor reading (does not trigger recalculation)."""
        self.ext_current_temp = float(external_temp)
        _LOGGER.debug("Zone '%s': external temp=%.1f°C", self.name, self.ext_current_temp)

    def _update_demand_metric(self) -> None:
        """Recalculate is_demanding_heat from priority, temperature error and valve state."""
        if self.is_high_priority:
            self.is_demanding_heat = self.current_error > 0 or self.heating_status == 'cooling'
        else:
            self.is_demanding_heat = self.trv_opening_percent >= LOW_PRIORITY_MIN_OPENING

    def get_demand_metric(self) -> float:
        """Demand 0.0-1.0 = valve opening fraction, or 0.0 if the zone is at/above target."""
        if self.current_error <= 0.0:
            return 0.0
        return max(0.0, min(1.0, self.trv_opening_percent / 100.0))

    def export_zone_state(self) -> dict:
        """Snapshot of the zone state for sensors."""
        return {
            "current_temperature": round(self.current_temp, 2),
            "target_temperature": round(self.target_temp, 2),
            "temperature_error": round(self.current_error, 2),
            "name": self.name,
            "floor_area_m2": round(self.floor_area_m2, 2),
            "is_high_priority": self.is_high_priority,
            "is_demanding_heat": self.is_demanding_heat,
            "trv_opening_percent": round(self.trv_opening_percent, 1),
            "temperature_offset": round(self.temperature_offset, 1),
            "temp_calib_entity_id": self.temp_calib_entity_id,
            "has_external_sensor": self.ext_temp_entity_id is not None,
            "external_sensor_temperature": round(self.ext_current_temp, 2) if self.ext_temp_entity_id else None,
        }
