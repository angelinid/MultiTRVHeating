"""Temporary override of the boiler flow temperature (from the dashboard)."""

import logging
from typing import TYPE_CHECKING, Optional

try:
    from .const import LOGGER_NAME, MAX_FLOW_TEMP, MIN_FLOW_TEMP
except ImportError:  # imported without Home Assistant (tests)
    from const import LOGGER_NAME, MAX_FLOW_TEMP, MIN_FLOW_TEMP

if TYPE_CHECKING:
    from .master_controller import MasterController

_LOGGER = logging.getLogger(LOGGER_NAME)

OVERRIDE_DURATION = 900.0        # s: then the schedule takes over again
MAX_FLOW_ONE_VALVE = 45.0        # °C: cap while only one valve is measured open (it takes the whole flow)
NOTIFICATION_ID = "multi_trv_flow_override"


class OverrideError(Exception):
    """The override cannot be applied (the message is shown to the user)."""


class FlowOverride:
    """
    Forces the flow request to a fixed value for OVERRIDE_DURATION, whatever the zones ask for.

    - value >= MIN_FLOW_TEMP: flow temperature in °C; anything lower: force the boiler off.
    - A flow above 0 needs a valve measured open when it is set, else it is refused (OverrideError).
    - While only one valve is open the flow is capped at MAX_FLOW_ONE_VALVE.
    - If every valve closes during the override the interlock stops the boiler (the override and its
      timer carry on; `blocked` tells why there is no flow).
    - Setting it again restarts the 15 minutes; `cancel()` or the timer ends it. Not persisted: it
      ends with an HA restart.
    """

    def __init__(self, controller: "MasterController") -> None:
        self.controller = controller
        self.value: Optional[float] = None     # requested flow (0 = off); None = no override
        self.until: Optional[float] = None     # controller clock time it ends
        self.last_value = 35.0                 # what the slider shows when no override is active
        self.capped_to: Optional[float] = None
        self.blocked = False

    @property
    def active(self) -> bool:
        return self.value is not None

    def remaining(self, now: float) -> float:
        return max(0.0, self.until - now) if self.active and self.until is not None else 0.0

    def start(self, value: float, now: float) -> None:
        """Start (or restart) the override. Raises OverrideError (shown to the user) if it cannot be applied."""
        value = float(value)
        if value < 0 or value > MAX_FLOW_TEMP:
            raise OverrideError(f"The flow must be at most {MAX_FLOW_TEMP:.0f} °C")
        if value < MIN_FLOW_TEMP:
            value = 0.0          # anything below the boiler minimum means off
        if value > 0:
            if not self.controller.has_open_valve():
                raise OverrideError("No valve is open, so the boiler cannot be started. Nothing was changed.")
        self.value = value
        self.until = now + OVERRIDE_DURATION
        self.last_value = value if value > 0 else self.last_value
        self.capped_to = None
        self.blocked = False
        _LOGGER.info("Flow override: %s for %.0f minutes", "off" if value == 0 else f"{value:.0f} °C", OVERRIDE_DURATION / 60)

    def cancel(self, reason: str = "cancelled") -> None:
        if not self.active:
            return
        self.value = None
        self.until = None
        self.capped_to = None
        self.blocked = False
        _LOGGER.info("Flow override ended (%s)", reason)

    def _open_valves(self) -> int:
        return sum(1 for v in self.controller.zones.values() if v.measured_opening > 0)

    def apply(self, flow_temp: float, reason: str, now: float) -> tuple[float, str]:
        """Replace the scheduled flow request while the override is active (before the interlock)."""
        if not self.active:
            return flow_temp, reason
        if now >= (self.until or 0.0):
            self.cancel("time is up, back to the schedule")
            return flow_temp, reason
        value = self.value or 0.0
        self.capped_to = None
        if value > MAX_FLOW_ONE_VALVE and self._open_valves() <= 1:
            value = MAX_FLOW_ONE_VALVE
            self.capped_to = value
        self.blocked = value > 0 and not self.controller.has_open_valve()
        return value, f"flow override ({value:.0f} °C)" if value > 0 else "flow override (off)"

    def status(self, now: float) -> str:
        if not self.active:
            return "inactive"
        left = f"{self.remaining(now) / 60:.0f} min left"
        if self.blocked:
            return f"no flow: no valve is open ({left})"
        return f"active, {left}"
