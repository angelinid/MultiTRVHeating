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
from datetime import datetime
from typing import TYPE_CHECKING, Optional

try:
    from .const import LOGGER_NAME, MAX_FLOW_TEMP, MIN_FLOW_TEMP
except ImportError:
    from const import LOGGER_NAME, MAX_FLOW_TEMP, MIN_FLOW_TEMP

if TYPE_CHECKING:
    from .master_controller import MasterController

_LOGGER = logging.getLogger(LOGGER_NAME)

# flow_temp = MIN_FLOW_TEMP + thermal_load * (TIME_PRESSURE_SCALE / seconds_remaining) * tuning_constant
# where thermal_load = max(temp_error * floor_area) over high-priority zones.
DEFAULT_PREHEATING_TUNING_CONSTANT = 1.0
TUNING_CONSTANT_MIN = 0.1
TUNING_CONSTANT_MAX = 5.0
TIME_PRESSURE_SCALE = 1000.0

# Weight of a new value when learning the tuning constant (exponential low-pass filter).
LOW_PASS_FILTER_ALPHA = 0.2

# Pre-heating may only finish early (no thermal load) after running this long.
MIN_RUN_MINUTES = 5.0

# Tuning adjustment factors applied when a cycle ends.
TUNING_ADJ_NONE = 1.0       # Stopped without learning
TUNING_ADJ_EXPIRED = 2.0    # Ran out of time: be more aggressive next time
TUNING_ADJ_COMPLETED = 0.5  # Zones reached target early: be gentler next time


class PreheatingController:
    """
    Pre-heating: run the boiler hotter ahead of a target end time.

    Active while `is_enabled` and `preheating_end_time` is in the future. While
    active, MasterController uses calculate_flow_temp_override() instead of the
    demand-based flow temperature. Each completed cycle nudges the tuning constant.
    """

    def __init__(self, master_controller: "MasterController") -> None:
        self.master_controller = master_controller
        self.preheating_end_time: Optional[datetime] = None
        self.preheating_start_time: Optional[datetime] = None
        self.is_enabled: bool = False
        self.tuning_constant: float = DEFAULT_PREHEATING_TUNING_CONSTANT

    def _preheating_disabled(self, tuning_adj: float) -> None:
        """End the current cycle, disable pre-heating and learn from the outcome."""
        self.preheating_end_time = None
        self.preheating_start_time = None
        self.is_enabled = False
        _LOGGER.info("Pre-heating stopped (tuning adjustment factor %.1f)", tuning_adj)
        self._update_tuning_constant(self.tuning_constant * tuning_adj)

    def _update_tuning_constant(self, new_value: float) -> None:
        """Blend a (clamped) new value into the tuning constant with a low-pass filter."""
        clamped_value = max(TUNING_CONSTANT_MIN, min(TUNING_CONSTANT_MAX, new_value))
        old_value = self.tuning_constant
        self.tuning_constant = (1.0 - LOW_PASS_FILTER_ALPHA) * old_value + LOW_PASS_FILTER_ALPHA * clamped_value
        _LOGGER.info(
            "Pre-heating tuning constant updated: old=%.3f, target=%.3f, new=%.3f",
            old_value, clamped_value, self.tuning_constant,
        )

    def is_active(self) -> bool:
        """True if enabled and the end time is in the future."""
        if self.preheating_end_time is None or self.is_enabled is False:
            return False

        now = datetime.now()
        if self.preheating_end_time > now:
            _LOGGER.debug(
                "Pre-heating active: %.0f s remaining", (self.preheating_end_time - now).total_seconds()
            )
            return True
        return False

    def _get_max_high_priority_thermal_load(self) -> float:
        """Max of temp_error * floor_area over high-priority zones below target (°C·m²)."""
        max_load = 0.0
        for zone in self.master_controller.zones.values():
            if zone.is_high_priority and zone.current_error > 0:
                max_load = max(max_load, zone.current_error * zone.floor_area_m2)
        return max_load

    def calculate_flow_temp_override(self) -> float:
        """
        Flow temperature to use while pre-heating, clamped to MIN..MAX_FLOW_TEMP.

        Returns 0.0 (and ends the cycle) if pre-heating is disabled, the end time
        has passed, or - after MIN_RUN_MINUTES - no high-priority zone needs heat.
        """
        if self.preheating_end_time is None or self.is_enabled is False:
            _LOGGER.warning("Pre-heating flow temperature requested but pre-heating is disabled")
            self._preheating_disabled(TUNING_ADJ_NONE)
            return 0.0

        now = datetime.now()
        time_remaining_seconds = (self.preheating_end_time - now).total_seconds()
        if self.preheating_start_time is None:
            self.preheating_start_time = now
            _LOGGER.info(
                "Pre-heating started (end time %s, tuning constant %.3f)",
                self.preheating_end_time.strftime("%H:%M"), self.tuning_constant,
            )

        if time_remaining_seconds <= 0:
            _LOGGER.info("Pre-heating end time reached, falling back to normal control")
            self._preheating_disabled(TUNING_ADJ_EXPIRED)
            return 0.0

        max_thermal_load = self._get_max_high_priority_thermal_load()

        elapsed_minutes = (now - self.preheating_start_time).total_seconds() / 60.0
        if elapsed_minutes > MIN_RUN_MINUTES and max_thermal_load <= 0:
            _LOGGER.info("Pre-heating complete after %.1f min: no high-priority zone needs heat", elapsed_minutes)
            self._preheating_disabled(TUNING_ADJ_COMPLETED)
            return 0.0

        time_pressure = TIME_PRESSURE_SCALE / time_remaining_seconds
        flow_override = max_thermal_load * time_pressure * self.tuning_constant
        preheating_flow_temp = MIN_FLOW_TEMP + flow_override

        _LOGGER.debug(
            "Pre-heating: thermal_load=%.1f, remaining=%.0f s, time_pressure=%.6f, "
            "tuning_constant=%.3f, override=%.1f°C, flow_temp=%.1f°C",
            max_thermal_load, time_remaining_seconds, time_pressure,
            self.tuning_constant, flow_override, preheating_flow_temp,
        )
        return max(MIN_FLOW_TEMP, min(MAX_FLOW_TEMP, preheating_flow_temp))
