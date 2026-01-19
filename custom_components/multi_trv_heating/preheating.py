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

if TYPE_CHECKING:
    from master_controller import MasterController

_LOGGER = logging.getLogger("don_controller")

# =========================================================
# Pre-heating Control Constants
# =========================================================

# Flow temperature = 5°C is boiler OFF signal (minimum safe temperature)
MIN_FLOW_TEMP = 5.0

# Flow temperature = 80°C is maximum boiler output (safety limit)
MAX_FLOW_TEMP = 80.0

# Pre-heating tuning constant: scales thermal load to flow temperature override
# thermal_load = max(temp_error * floor_area) for all high-priority zones
# time_pressure = 1.0 / time_remaining_seconds
# flow_override = MIN_FLOW_TEMP + (thermal_load * time_pressure * PREHEATING_TUNING_CONSTANT)
# Tune this based on system response (larger = more aggressive preheating)
PREHEATING_TUNING_CONSTANT = 1.0  # Can be tuned empirically


class PreheatingController:
    """
    Manages pre-heating mode for the multi-zone heating system.
    
    Pre-heating allows the boiler to be activated at a higher flow temperature
    before zones actually need heat, warming the system up proactively.
    
    Usage:
    1. Set preheating_end_time to a future datetime to activate
    2. PreheatingController calculates aggressive flow temperature override
    3. When time expires or is cleared, falls back to normal demand-based control
    """
    
    def __init__(self, master_controller: "MasterController") -> None:
        """
        Initialize pre-heating controller.
        
        Args:
            master_controller: Reference to MasterController for zone access
        """
        self.master_controller = master_controller
        
        # Current pre-heating end time (None = not preheating)
        self.preheating_end_time: Optional[datetime] = None

        # Initialise it as disabled
        self.is_enabled: bool = False
    
    def is_active(self) -> bool:
        """
        Check if pre-heating mode is currently active.
        
        Returns:
            bool: True if preheating_end_time is set and in the future
        """
        if self.preheating_end_time is None or self.is_enabled is False:
            return False
        
        # Check if end time is in the future
        now = datetime.now()
        is_active = self.preheating_end_time > now
        
        if is_active:
            remaining_seconds = (self.preheating_end_time - now).total_seconds()
            _LOGGER.debug("Pre-heating is ACTIVE: %.0f seconds remaining", remaining_seconds)
        
        return is_active
    
    def _get_max_high_priority_thermal_load(self) -> float:
        """
        Calculate the maximum thermal load among high-priority zones.
        
        Thermal load = temperature_error * floor_area
        Returns the maximum value across all high-priority zones.
        Only considers zones that need heating (error > 0).
        
        Returns:
            float: Maximum thermal load (°C·m²), or 0 if no high-priority zones need heat
        """
        max_load = 0.0
        
        for zone in self.master_controller.zones.values():
            if zone.is_high_priority and zone.current_error > 0:
                thermal_load = zone.current_error * zone.floor_area_m2
                max_load = max(max_load, thermal_load)
                _LOGGER.debug(
                    "High-priority zone '%s': error=%.1f°C, area=%.1f m², load=%.1f",
                    zone.name, zone.current_error, zone.floor_area_m2, thermal_load
                )
        
        _LOGGER.debug("Max high-priority thermal load: %.1f °C·m²", max_load)
        return max_load
    
    def calculate_flow_temp_override(self) -> float:
        """
        Calculate flow temperature override during pre-heating mode.
        
        Pre-heating formula:
        1. Get max thermal load from high-priority zones: thermal_load = max(error * area)
        2. Calculate time pressure: time_pressure = 1.0 / time_remaining_seconds
        3. Calculate override: flow_temp = MIN_FLOW_TEMP + (thermal_load * time_pressure * TUNING_CONSTANT)
        4. Clamp to valid range [MIN_FLOW_TEMP, MAX_FLOW_TEMP]
        
        The override is aggressive: smaller time windows and larger thermal loads
        result in higher flow temperatures. As time runs out, flow_temp increases
        non-linearly (approaching MAX_FLOW_TEMP when time_remaining → 0).
        
        Returns:
            float: Calculated flow temperature override for pre-heating (°C)
        """
        if not self.is_active():
            return 0.0  # Not preheating
        
        now = datetime.now()
        time_remaining_seconds = (self.preheating_end_time - now).total_seconds()
        
        # Failsafe: if time is already past, return 0 to fall back to normal logic
        if time_remaining_seconds <= 0:
            _LOGGER.warning("Pre-heating time has expired, falling back to normal control")
            self.preheating_end_time = None  # Deactivate pre-heating
            return 0.0
        
        # Get max thermal load from high-priority zones
        max_thermal_load = self._get_max_high_priority_thermal_load()
        
        # Calculate time pressure (increases as time runs out)
        time_pressure = 1.0 / time_remaining_seconds
        
        # Calculate override using parametric formula
        # flow_override = thermal_load * time_pressure * TUNING_CONSTANT
        flow_override = max_thermal_load * time_pressure * PREHEATING_TUNING_CONSTANT
        
        # Calculate final flow temperature
        preheating_flow_temp = MIN_FLOW_TEMP + flow_override
        
        _LOGGER.debug(
            "Pre-heating calculation: thermal_load=%.1f, time_remaining=%.0f s, "
            "time_pressure=%.6f, override=%.1f°C, final_flow_temp=%.1f°C",
            max_thermal_load, time_remaining_seconds, time_pressure,
            flow_override, preheating_flow_temp
        )
        
        # Clamp to valid range
        return max(MIN_FLOW_TEMP, min(MAX_FLOW_TEMP, preheating_flow_temp))
