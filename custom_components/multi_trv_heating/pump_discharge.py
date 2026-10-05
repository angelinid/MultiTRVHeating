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
    from homeassistant.core import HomeAssistant

_LOGGER = logging.getLogger(LOGGER_NAME)

PUMP_DISCHARGE_TIMEOUT = 300  # seconds
BOOST_HEATING_SWITCH_SUFFIX = "_boost_heating"  # climate.<x> -> switch.<x>_boost_heating


class PumpDischargeController:
    """
    Keep one TRV open for a while after the boiler turns off so the pump can
    dump residual heat instead of trapping hot water in the pipes.

    On a boiler ON -> OFF transition the discharge TRV's boost switch is turned
    on; it is turned off again after PUMP_DISCHARGE_TIMEOUT (checked on the next
    control cycle) or as soon as the boiler is needed again. While discharging,
    the discharge TRV is excluded from boiler demand calculations.
    """

    def __init__(self, hass: Optional["HomeAssistant"] = None,
                 discharge_trv_entity_id: Optional[str] = None,
                 discharge_trv_name: Optional[str] = None) -> None:
        self.hass = hass
        self.discharge_trv_entity_id = discharge_trv_entity_id
        self.discharge_trv_name = discharge_trv_name or "Unknown"

        self.is_discharging = False      # Boost switch currently ON
        self.discharge_start_time = 0.0  # time.time() when discharge started
        self.boiler_was_on = False       # Previous boiler state, for transition detection

        _LOGGER.debug(
            "Pump discharge valve: %s (%s)", discharge_trv_entity_id or "not set", self.discharge_trv_name
        )

    def update_config(self, discharge_trv_entity_id: Optional[str],
                      discharge_trv_name: Optional[str]) -> None:
        """Change which TRV is used for discharge (None disables the feature)."""
        self.discharge_trv_entity_id = discharge_trv_entity_id
        self.discharge_trv_name = discharge_trv_name or "Unknown"
        _LOGGER.debug(
            "Pump discharge valve: %s (%s)", discharge_trv_entity_id or "not set", self.discharge_trv_name
        )

    def is_discharge_valve(self, entity_id: str) -> bool:
        """True if entity_id is the discharge TRV *and* a discharge is in progress."""
        return (
            self.is_discharge_active()
            and entity_id == self.discharge_trv_entity_id
            and self.discharge_trv_entity_id is not None
        )

    def is_discharge_active(self) -> bool:
        return self.is_discharging

    async def evaluate_and_update(self, boiler_should_be_on: bool) -> None:
        """Start/stop discharge based on the boiler decision; called every control cycle."""
        if self.discharge_trv_entity_id is None:
            self.boiler_was_on = boiler_should_be_on
            return

        if boiler_should_be_on:
            if self.is_discharging:
                await self._disable_discharge()
                _LOGGER.info("Pump discharge stopped: boiler needed again")
            self.boiler_was_on = True
            return

        boiler_just_turned_off = self.boiler_was_on
        self.boiler_was_on = False

        if boiler_just_turned_off and not self.is_discharging:
            await self._enable_discharge()
            _LOGGER.info(
                "Pump discharge started via '%s' (timeout %.0f s)",
                self.discharge_trv_name, PUMP_DISCHARGE_TIMEOUT,
            )
        elif self.is_discharging:
            elapsed = time.time() - self.discharge_start_time
            if elapsed > PUMP_DISCHARGE_TIMEOUT:
                await self._disable_discharge()
                _LOGGER.info("Pump discharge stopped: timeout (%.0f s)", elapsed)

    def _boost_switch_id(self) -> Optional[str]:
        """switch.<device>_boost_heating for the discharge climate entity, or None if malformed."""
        parts = self.discharge_trv_entity_id.split(".")
        if len(parts) < 2:
            _LOGGER.error("Invalid discharge TRV entity ID: %s", self.discharge_trv_entity_id)
            return None
        return f"switch.{parts[1]}{BOOST_HEATING_SWITCH_SUFFIX}"

    async def _async_set_boost(self, on: bool) -> bool:
        """Turn the discharge TRV's boost switch on/off. Returns True if the service was called."""
        action = "enable" if on else "disable"
        if not self.hass or not self.discharge_trv_entity_id:
            _LOGGER.warning("Pump discharge: cannot %s - hass or entity_id not set", action)
            return False

        try:
            boost_switch_id = self._boost_switch_id()
            if boost_switch_id is None:
                return False
            await self.hass.services.async_call(
                "switch",
                "turn_on" if on else "turn_off",
                {"entity_id": boost_switch_id},
                blocking=False,
            )
            _LOGGER.debug("Pump discharge: %s turned %s", boost_switch_id, "on" if on else "off")
            return True
        except Exception as e:
            _LOGGER.error("Pump discharge: error trying to %s: %s", action, e)
            return False

    async def _enable_discharge(self) -> None:
        if await self._async_set_boost(True):
            self.is_discharging = True
            self.discharge_start_time = time.time()

    async def _disable_discharge(self) -> None:
        if await self._async_set_boost(False):
            self.is_discharging = False

    def get_discharge_state(self) -> dict:
        """Snapshot of discharge state for sensors."""
        elapsed = time.time() - self.discharge_start_time if self.is_discharging else 0.0
        return {
            "discharge_trv_entity_id": self.discharge_trv_entity_id,
            "discharge_trv_name": self.discharge_trv_name,
            "is_discharging": self.is_discharging,
            "elapsed_seconds": round(elapsed, 1),
            "timeout_seconds": PUMP_DISCHARGE_TIMEOUT,
        }
