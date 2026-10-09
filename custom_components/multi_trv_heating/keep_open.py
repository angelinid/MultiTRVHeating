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
    from .zone_wrapper import HOLD_AUTO, ZoneWrapper
except ImportError:
    from const import LOGGER_NAME
    from zone_wrapper import HOLD_AUTO, ZoneWrapper

if TYPE_CHECKING:
    from .master_controller import MasterController

_LOGGER = logging.getLogger(LOGGER_NAME)

# Valves move in 25 % steps. The boiler turns on when the openings add up to 100 %.
ENGAGE_MAX_OPENING = 25.0      # Engage when no other valve is open more than this ...
ENGAGE_MAX_SUM = 100.0         # ... and the other openings add up to less than this
RELEASE_OPENING = 50.0         # Release when another valve is at least this open (or the sum is 100 %)
CLOSING_ENGAGE_OPENING = 25.0  # A valve closing at or below this counts as closed

# The boiler pump keeps circulating for about this long after the boiler is told to stop.
BOILER_PUMP_OVERRUN = 300.0

# Actuators are slow and finish a travel before taking the next command, so do not flap:
MIN_HOLD_SECONDS = 30.0        # a hold lasts at least this long
RELEASE_SETTLE = 150.0         # a just-released valve is still closing: do not count it as open
CONFIRM_TIMEOUT = 180.0        # held valve still shut after this long: hold another one as well
RELEASE_STABLE_SECONDS = 30.0  # other valves must keep providing flow this long before a release


class KeepOpenController:
    """
    Never leave every valve closed: hold one valve open (via the TRV calibration offset).

    Whenever the other valves are about to close - none open more than ENGAGE_MAX_OPENING (a valve
    already closing at CLOSING_ENGAGE_OPENING or less counts as closed) and their openings adding up to less than
    ENGAGE_MAX_SUM - the selected "discharge" valve is held
    open. This is independent of the boiler state, so a valve is already open when the boiler
    stops and the pump (which overruns for BOILER_PUMP_OVERRUN) always has somewhere to push water.

    The hold is released when other valves provide the flow (one at least RELEASE_OPENING open, or
    the openings adding up to ENGAGE_MAX_SUM) continuously for RELEASE_STABLE_SECONDS, but never
    earlier than MIN_HOLD_SECONDS after it started and never within BOILER_PUMP_OVERRUN of a boiler
    stop (a heat request that turns the boiler on again ends that window). A neighbour valve that
    keeps flapping faster than RELEASE_STABLE_SECONDS therefore does not flap the hold.

    If the held valve has not opened after CONFIRM_TIMEOUT (stuck, flat battery, offline), the next
    usable valve is held as well. All decisions use *measured* openings.
    """

    def __init__(self, controller: "MasterController",
                 discharge_trv_entity_id: Optional[str] = None,
                 discharge_trv_name: Optional[str] = None) -> None:
        self.controller = controller
        self.discharge_trv_entity_id = discharge_trv_entity_id
        self.discharge_trv_name = discharge_trv_name or "Unknown"

        self.engaged_at: Optional[float] = None       # controller clock, first auto hold of this episode
        self.last_escalation_at: Optional[float] = None
        self.release_ok_since: Optional[float] = None   # other valves have been providing flow since
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

    def update(self, now: float) -> bool:
        """Engage / release holds from the measured openings. Returns True if a hold changed."""
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
            self.last_reason = "disabled"
            return changed

        # Valves providing flow independently of us: not auto-held (or switch-held), not still closing
        anchors = [
            z for z in zones
            if (not z.has_hold(HOLD_AUTO) or z.held and z.hold_since.keys() - {HOLD_AUTO})
            and not z.recently_released(now, RELEASE_SETTLE)
        ]
        # Actuators need time to open: a valve already closing at or below CLOSING_ENGAGE_OPENING
        # counts as closed, so the hold starts well before the last valve is shut.
        anchor_max = max((z.flow_opening(CLOSING_ENGAGE_OPENING) for z in anchors), default=0.0)
        anchor_sum = sum(z.flow_opening(CLOSING_ENGAGE_OPENING) for z in anchors)
        need = anchor_max <= ENGAGE_MAX_OPENING and anchor_sum < ENGAGE_MAX_SUM
        can_release = anchor_max >= RELEASE_OPENING or anchor_sum >= ENGAGE_MAX_SUM
        overrun = ctl.pump_overrun_active(now)

        if can_release:
            if self.release_ok_since is None:
                self.release_ok_since = now
        else:
            self.release_ok_since = None
        stable = self.release_ok_since is not None and now - self.release_ok_since >= RELEASE_STABLE_SECONDS

        if not auto:
            self.engaged_at = None
            self.last_escalation_at = None
            if need:
                changed |= self._engage_first_usable(now, f"other valves max {anchor_max:.0f}%, sum {anchor_sum:.0f}%")
            else:
                self._warned_unholdable = False
                self.last_reason = "other valves open"
            return changed

        # --- holds exist ---
        if can_release and stable and not overrun:
            for zone in auto:
                if zone.hold_age(HOLD_AUTO, now) >= MIN_HOLD_SECONDS:
                    changed |= self._release(zone, now, f"other valves open (max {anchor_max:.0f}%, sum {anchor_sum:.0f}%)")
            if not any(z.has_hold(HOLD_AUTO) for z in zones):
                self.engaged_at = None
                self.last_escalation_at = None
            return changed

        self.last_reason = "pump overrun" if (can_release and overrun) else "holding"

        # The selected valve was changed while holding: hold the new one too; the old one is
        # released below once the new one is measured open
        if not primary.has_hold(HOLD_AUTO) and self._usable(primary) and primary.position_available:
            changed |= self._engage(primary, now, "selected valve changed")

        # A held valve that cannot be driven (offline, calibration entity unavailable) is no use:
        # hold another one at once. One that is merely slow gets CONFIRM_TIMEOUT to open.
        held_zones = [z for z in zones if z.has_hold(HOLD_AUTO)]
        viable = [z for z in held_zones if self._usable(z) and z.position_available]
        held_open = any(z.measured_opening > 0 for z in viable)
        if need and not viable:
            changed |= self._escalate(now, "held valve unavailable")
        elif need and not held_open and self.engaged_at is not None:
            reference = max(self.engaged_at, self.last_escalation_at or 0.0)
            if now - reference >= CONFIRM_TIMEOUT:
                changed |= self._escalate(now, f"held valve has not opened after {CONFIRM_TIMEOUT:.0f} s")

        # The selected valve is open now: fallbacks are no longer needed
        if primary.has_hold(HOLD_AUTO) and primary.measured_opening > 0:
            for zone in auto:
                if zone is not primary and zone.hold_age(HOLD_AUTO, now) >= MIN_HOLD_SECONDS:
                    changed |= self._release(zone, now, "selected valve is open")
        return changed

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
