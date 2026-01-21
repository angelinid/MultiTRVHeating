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

if TYPE_CHECKING:
    from homeassistant.core import HomeAssistant

_LOGGER = logging.getLogger("don_controller")

# Pump discharge configuration constants
PUMP_DISCHARGE_TIMEOUT = 300  # 5 minutes in seconds
BOOST_HEATING_SWITCH_SUFFIX = "_boost_heating"  # Switch naming convention


class PumpDischargeController:
    """
    Controller for keeping the boiler pump running after all zones shut off.
    
    When the last TRV closes and all zones stop demanding heat, the boiler stops.
    However, this causes the pump to stop immediately, trapping hot water in the pipes.
    
    This controller:
    1. Keeps one designated TRV open by enabling its boost_heating switch
    2. Allows boiler pump to continue circulating for 5 minutes
    3. Excludes the discharge TRV from boiler control calculations (ignores its demand)
    4. Disables the switch after timeout OR when boiler needs to run again
    
    Configuration:
    - discharge_trv_entity_id: Which climate entity is the pump discharge valve
    - discharge_trv_name: User-friendly name of the discharge TRV device
    """
    
    def __init__(self, hass: Optional["HomeAssistant"] = None, 
                 discharge_trv_entity_id: Optional[str] = None,
                 discharge_trv_name: Optional[str] = None) -> None:
        """
        Initialize pump discharge controller.
        
        Args:
            hass: Home Assistant instance for calling services
            discharge_trv_entity_id: Climate entity ID of the pump discharge TRV
            discharge_trv_name: User-friendly name of the discharge TRV (e.g., "Hallway")
        """
        self.hass = hass
        self.discharge_trv_entity_id = discharge_trv_entity_id
        self.discharge_trv_name = discharge_trv_name or "Unknown"
        
        # State tracking
        self.is_discharging = False  # Is boost switch currently ON?
        self.discharge_start_time = 0.0  # Timestamp when discharge started
        self.boiler_was_on = False  # Track previous boiler state for transition detection
        
        _LOGGER.info(
            "PumpDischargeController initialized: discharge_trv=%s (%s)",
            discharge_trv_entity_id or "not set", self.discharge_trv_name
        )
    
    def update_config(self, discharge_trv_entity_id: Optional[str],
                     discharge_trv_name: Optional[str]) -> None:
        """
        Update the discharge TRV configuration.
        
        Args:
            discharge_trv_entity_id: Climate entity ID of the pump discharge TRV
            discharge_trv_name: User-friendly name of the discharge TRV
        """
        self.discharge_trv_entity_id = discharge_trv_entity_id
        self.discharge_trv_name = discharge_trv_name or "Unknown"
        _LOGGER.debug(
            "PumpDischargeController config updated: discharge_trv=%s (%s)",
            discharge_trv_entity_id or "not set", self.discharge_trv_name
        )
    
    def is_discharge_valve(self, entity_id: str) -> bool:
        """
        Check if a given entity ID is the configured discharge valve.
        
        Args:
            entity_id: Climate entity ID to check
            
        Returns:
            bool: True if this is the discharge TRV
        """
        return self.is_discharge_active() and entity_id == self.discharge_trv_entity_id and self.discharge_trv_entity_id is not None
    
    def is_discharge_active(self) -> bool:
        """
        Check if discharge process is currently active.
        
        Returns:
            bool: True if boost switch is ON and discharging
        """
        return self.is_discharging
    
    async def evaluate_and_update(self, boiler_should_be_on: bool) -> None:
        """
        Evaluate discharge state and update boost switch accordingly.
        
        Called from _calculate_and_command() after boiler decision is made.
        
        Logic:
        1. If boiler should be ON → disable discharge (zones need heat)
        2. If boiler transitions from ON to OFF → enable discharge (keep pump circulating)
        3. If discharge running and timeout elapsed → disable discharge
        4. If boiler reactivates while discharging → disable discharge

        Args:
            boiler_should_be_on: Whether boiler should be running (from master controller)
        """
        if self.discharge_trv_entity_id is None:
            self.boiler_was_on = boiler_should_be_on
            return
        
        # Case 1: Boiler should be ON → disable discharge (zones need heat)
        if boiler_should_be_on:
            if self.is_discharging:
                await self._disable_discharge()
                _LOGGER.info(
                    "PumpDischarge: Disabling discharge (boiler activated, zones need heat)"
                )
            self.boiler_was_on = True
            return
        
        # Case 2: Boiler OFF - manage discharge state
        # Only activate discharge on transition FROM ON to OFF (not continuously when OFF)
        boiler_just_turned_off = self.boiler_was_on and not boiler_should_be_on
        self.boiler_was_on = boiler_should_be_on
        
        if boiler_just_turned_off and not self.is_discharging:
            # Boiler just turned off - start discharge to keep pump running
            await self._enable_discharge()
            _LOGGER.info(
                "PumpDischarge: Boiler OFF - Starting discharge for valve '%s' (timeout=%.0fs)",
                self.discharge_trv_name, PUMP_DISCHARGE_TIMEOUT
            )
        elif self.is_discharging:
            # Already discharging, check timeout
            elapsed = time.time() - self.discharge_start_time
            if elapsed > PUMP_DISCHARGE_TIMEOUT:
                await self._disable_discharge()
                _LOGGER.info(
                    "PumpDischarge: Timeout elapsed (%.0fs > %.0fs), disabling discharge",
                    elapsed, PUMP_DISCHARGE_TIMEOUT
                )
    
    async def _enable_discharge(self) -> None:
        """
        Enable the discharge TRV by turning on its boost_heating switch.
        
        Converts entity_id format:
        - climate.hallway_trv → switch.hallway_trv_boost_heating
        """
        if not self.hass or not self.discharge_trv_entity_id:
            _LOGGER.warning("PumpDischarge: Cannot enable - hass or entity_id not set")
            return
        
        try:
            # Extract device name from climate entity ID
            # e.g., climate.hallway_trv → hallway_trv
            climate_id_parts = self.discharge_trv_entity_id.split(".")
            if len(climate_id_parts) < 2:
                _LOGGER.error("Invalid entity ID format: %s", self.discharge_trv_entity_id)
                return
            
            device_name = climate_id_parts[1]
            boost_switch_id = f"switch.{device_name}{BOOST_HEATING_SWITCH_SUFFIX}"
            
            _LOGGER.debug("PumpDischarge: Enabling switch %s", boost_switch_id)
            
            # Call Home Assistant switch service to turn ON
            await self.hass.services.async_call(
                "switch",
                "turn_on",
                {"entity_id": boost_switch_id},
                blocking=False,
            )
            
            self.is_discharging = True
            self.discharge_start_time = time.time()
            
            _LOGGER.info("PumpDischarge: Switch %s enabled (discharge started)", boost_switch_id)
            
        except Exception as e:
            _LOGGER.error("PumpDischarge: Error enabling discharge: %s", e)
    
    async def _disable_discharge(self) -> None:
        """
        Disable the discharge TRV by turning off its boost_heating switch.
        """
        if not self.hass or not self.discharge_trv_entity_id:
            _LOGGER.warning("PumpDischarge: Cannot disable - hass or entity_id not set")
            return
        
        try:
            # Extract device name from climate entity ID
            climate_id_parts = self.discharge_trv_entity_id.split(".")
            if len(climate_id_parts) < 2:
                _LOGGER.error("Invalid entity ID format: %s", self.discharge_trv_entity_id)
                return
            
            device_name = climate_id_parts[1]
            boost_switch_id = f"switch.{device_name}{BOOST_HEATING_SWITCH_SUFFIX}"
            
            _LOGGER.debug("PumpDischarge: Disabling switch %s", boost_switch_id)
            
            # Call Home Assistant switch service to turn OFF
            await self.hass.services.async_call(
                "switch",
                "turn_off",
                {"entity_id": boost_switch_id},
                blocking=False,
            )
            
            self.is_discharging = False
            
            _LOGGER.info("PumpDischarge: Switch %s disabled (discharge stopped)", boost_switch_id)
            
        except Exception as e:
            _LOGGER.error("PumpDischarge: Error disabling discharge: %s", e)
    
    def get_discharge_state(self) -> dict:
        """
        Export pump discharge controller state for monitoring.
        
        Returns:
            dict: Current discharge state and configuration
        """
        elapsed = 0.0
        if self.is_discharging:
            elapsed = time.time() - self.discharge_start_time
        
        return {
            "discharge_trv_entity_id": self.discharge_trv_entity_id,
            "discharge_trv_name": self.discharge_trv_name,
            "is_discharging": self.is_discharging,
            "elapsed_seconds": round(elapsed, 1),
            "timeout_seconds": PUMP_DISCHARGE_TIMEOUT,
        }
