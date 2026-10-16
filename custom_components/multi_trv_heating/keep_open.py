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
from typing import TYPE_CHECKING, Optional

try:
    from .const import LOGGER_NAME
    from .zone_wrapper import HOLD_AUTO, HOLD_TEMP_OFFSET, ZoneWrapper
except ImportError:
    from const import LOGGER_NAME
    from zone_wrapper import HOLD_AUTO, HOLD_TEMP_OFFSET, ZoneWrapper

if TYPE_CHECKING:
    from .master_controller import MasterController

_LOGGER = logging.getLogger(LOGGER_NAME)

# The boiler pump keeps circulating for about this long after the boiler is told to stop.
BOILER_PUMP_OVERRUN = 300.0

# A held valve counts as confirmed once its calibration is on the device and it is open more than this
CONFIRM_OPENING = 50.0
# Held valve still not confirmed after this long (stuck, flat battery, offline, setpoint too low)
CONFIRM_TIMEOUT = 180.0


class KeepOpenController:
    """
    Never leave every valve closed: the selected "discharge" valve is held open permanently
    (via the TRV calibration offset), whatever the other valves or the boiler are doing. A heat
    request elsewhere never releases it, so a valve is always open for the pump, including the
    BOILER_PUMP_OVERRUN after the boiler stops. The room of the held valve is allowed to get warmer
    than its target while the boiler runs.

    The hold only moves when the selected valve changes (or a fallback is needed). It is a
    handover: the new valve is held first and the old one stays held until the new one is
    *confirmed* - calibration offset applied on the device and the valve open more than
    CONFIRM_OPENING. If the new valve is not confirmed within CONFIRM_TIMEOUT the old hold is kept
    and a warning is logged.

    If no held valve is confirmed after CONFIRM_TIMEOUT (stuck, flat battery, offline), the next
    usable valve is held as well. All decisions use *measured* values.
    """

    def __init__(self, controller: "MasterController",
                 discharge_trv_entity_id: Optional[str] = None,
                 discharge_trv_name: Optional[str] = None) -> None:
        self.controller = controller
        self.discharge_trv_entity_id = discharge_trv_entity_id
        self.discharge_trv_name = discharge_trv_name or "Unknown"

        self.engaged_at: Optional[float] = None       # controller clock, first auto hold of this episode
        self.last_escalation_at: Optional[float] = None
        self.handover_since: Optional[float] = None    # selected valve held but not yet confirmed
        self._warned_handover = False                   # "not confirmed" already logged for this handover
        self._warned_unholdable = False                 # "no valve can be held" already logged this episode
        self.last_reason = "idle"
        _LOGGER.debug(
            "Keep-open valve: %s (%s)", discharge_trv_entity_id or "not set", self.discharge_trv_name
        )

    def update_config(self, discharge_trv_entity_id: Optional[str],
                      discharge_trv_name: Optional[str]) -> None:
        """Change which TRV is held open (None disables the feature)."""
        self.discharge_trv_entity_id = discharge_trv_entity_id
        self.discharge_trv_name = discharge_trv_name or "Unknown"
        _LOGGER.debug(
            "Keep-open valve: %s (%s)", discharge_trv_entity_id or "not set", self.discharge_trv_name
        )

    @property
    def is_active(self) -> bool:
        return any(z.has_hold(HOLD_AUTO) for z in self.controller.zones.values())

    # ------------------------------------------------------------------
    # Candidates
    # ------------------------------------------------------------------

    def _primary(self) -> Optional[ZoneWrapper]:
        if self.discharge_trv_entity_id is None:
            return None
        return self.controller.zones.get(self.discharge_trv_entity_id)

    def _usable(self, zone: ZoneWrapper) -> bool:
        """A zone can be held if we can write its calibration and the entity is not offline."""
        return bool(zone.temp_calib_entity_id) and not self.controller.calibration_unavailable(zone)

    def _candidates(self) -> list[ZoneWrapper]:
        """Selected valve first, then the other zones (alphabetical) as fallbacks."""
        primary = self._primary()
        if primary is None:
            return []
        others = sorted((z for z in self.controller.zones.values() if z is not primary), key=lambda z: z.name)
        return [primary] + others

    # ------------------------------------------------------------------
    # Decision
    # ------------------------------------------------------------------

    def confirmed(self, zone: ZoneWrapper) -> bool:
        """The hold really works on this valve: offset on the device and the valve open."""
        return (
            zone.has_hold(HOLD_AUTO)
            and zone.position_available
            and zone.device_offset == HOLD_TEMP_OFFSET
            and not zone.offset_in_flight
            and zone.measured_opening > CONFIRM_OPENING
        )

    def update(self, now: float) -> bool:
        """Engage / hand over holds. Returns True if a hold changed."""
        ctl = self.controller
        zones = list(ctl.zones.values())
        auto = [z for z in zones if z.has_hold(HOLD_AUTO)]
        primary = self._primary()
        changed = False

        if primary is None:
            # Feature off (no valve selected): drop any hold we own
            for zone in auto:
                changed |= self._release(zone, now, "feature disabled")
            self.engaged_at = None
            self.handover_since = None
            self.last_reason = "disabled"
            return changed

        if not auto:
            self.engaged_at = None
            self.last_escalation_at = None
            self.handover_since = None
            return self._engage_first_usable(now, "no valve held")

        # --- holds exist ---
        # The selected valve was changed (or came back): hold it too; the old hold stays until it is confirmed
        if not primary.has_hold(HOLD_AUTO) and self._usable(primary) and primary.position_available:
            changed |= self._engage(primary, now, "selected valve changed")
            self.handover_since = now
            self._warned_handover = False

        held_zones = [z for z in zones if z.has_hold(HOLD_AUTO)]
        viable = [z for z in held_zones if self._usable(z) and z.position_available]
        any_confirmed = any(self.confirmed(z) for z in viable)

        if primary.has_hold(HOLD_AUTO) and self.confirmed(primary):
            self.handover_since = None
            self.last_reason = "holding"
            for zone in auto:
                if zone is not primary:
                    changed |= self._release(zone, now, "selected valve is confirmed open")
            return changed

        self.last_reason = "holding"

        if self.handover_since is None and primary.has_hold(HOLD_AUTO) and len(held_zones) > 1:
            self.handover_since = now

        # The selected valve is held but not confirmed: say why once the timeout passed
        if (primary.has_hold(HOLD_AUTO) and self.handover_since is not None and not self._warned_handover
                and now - self.handover_since >= CONFIRM_TIMEOUT):
            self._warned_handover = True
            _LOGGER.warning("Keep-open: '%s' is not confirmed open after %.0f s (%s), keeping the other hold",
                            primary.name, CONFIRM_TIMEOUT, self._why_not_open(primary))

        # A held valve that cannot be driven (offline, calibration entity unavailable) is no use:
        # hold another one at once. One that is merely slow gets CONFIRM_TIMEOUT to open.
        if not viable:
            changed |= self._escalate(now, "held valve unavailable")
        elif not any_confirmed and self.engaged_at is not None:
            reference = max(self.engaged_at, self.last_escalation_at or 0.0)
            if now - reference >= CONFIRM_TIMEOUT:
                changed |= self._escalate(now, f"held valve has not opened after {CONFIRM_TIMEOUT:.0f} s ({self._why_not_open(viable[0])})")
        return changed

    @staticmethod
    def _why_not_open(zone: ZoneWrapper) -> str:
        """Best explanation for a held valve that does not open."""
        if zone.device_offset != HOLD_TEMP_OFFSET or zone.offset_in_flight:
            return "calibration offset not applied on the device yet"
        if zone.trv_temp >= zone.target_temp:
            return (f"TRV setpoint {zone.target_temp:.1f}°C is not above its reading "
                    f"{zone.trv_temp:.1f}°C even with the {HOLD_TEMP_OFFSET:.0f}°C offset - raise the setpoint")
        return "offset applied but the valve is not opening"

    def _engage(self, zone: ZoneWrapper, now: float, why: str) -> bool:
        self._warned_unholdable = False
        changed = self.controller.apply_hold(zone, HOLD_AUTO, True, now)
        if self.engaged_at is None:
            self.engaged_at = now
        _LOGGER.info("Keep-open: holding '%s' open (%s)", zone.name, why)
        self.last_reason = why
        return changed

    def _release(self, zone: ZoneWrapper, now: float, why: str) -> bool:
        changed = self.controller.apply_hold(zone, HOLD_AUTO, False, now)
        _LOGGER.info("Keep-open: released '%s' (%s)", zone.name, why)
        return changed

    def _engage_first_usable(self, now: float, why: str) -> bool:
        for zone in self._candidates():
            if self._usable(zone):
                return self._engage(zone, now, why)
        # Logged once per episode: every state change reruns this, e.g. dozens of times while HA boots
        if not self._warned_unholdable:
            _LOGGER.warning("Keep-open: needed but no valve can be held (no usable calibration entity)")
            self._warned_unholdable = True
        self.last_reason = "no usable valve"
        return False

    def _escalate(self, now: float, why: str) -> bool:
        for zone in self._candidates():
            if not zone.has_hold(HOLD_AUTO) and self._usable(zone) and zone.position_available:
                self.last_escalation_at = now
                _LOGGER.warning("Keep-open: %s, also holding '%s'", why, zone.name)
                return self._engage(zone, now, why)
        if self.last_escalation_at is None or now - self.last_escalation_at >= CONFIRM_TIMEOUT:
            _LOGGER.warning("Keep-open: %s and no further valve can be held", why)
        self.last_escalation_at = now
        return False

    def get_state(self) -> dict:
        """Snapshot of the keep-open state for sensors."""
        now = self.controller.now()
        held = [z.name for z in self.controller.zones.values() if z.has_hold(HOLD_AUTO)]
        return {
            "discharge_trv_entity_id": self.discharge_trv_entity_id,
            "discharge_trv_name": self.discharge_trv_name,
            "is_holding": bool(held),
            "held_zones": held,
            "held_seconds": round(now - self.engaged_at, 1) if self.engaged_at is not None else 0.0,
            "pump_overrun_active": self.controller.pump_overrun_active(now),
            "reason": self.last_reason,
        }
