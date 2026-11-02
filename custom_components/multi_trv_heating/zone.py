"""A zone (room) with one or more valves (TRVs)."""

import logging
from typing import Any, Optional

try:
    from .const import LOGGER_NAME
    from .zone_wrapper import HOLD_SWITCH, ZoneWrapper
except ImportError:  # imported without Home Assistant (tests)
    from const import LOGGER_NAME
    from zone_wrapper import HOLD_SWITCH, ZoneWrapper

_LOGGER = logging.getLogger(LOGGER_NAME)


class Zone:
    """
    A room: shared name, area, priority, external sensor, target and mode, over its valves.

    The valves are `ZoneWrapper` objects and keep all per-valve logic (offsets, holds, boost,
    opening). Every valve computes its error against the zone temperature: the external sensor
    while it is fresh, else the average of the available valves' own readings.

    A valve whose climate entity is unavailable is ignored as long as another valve of the zone is
    available (`active_valves`). A zone with no available valve keeps its last values.
    """

    def __init__(self, zone_id: str, name: str, valves: list[ZoneWrapper], floor_area_m2: float = 0.0,
                 is_high_priority: bool = True, ext_temp_entity_id: Optional[str] = None) -> None:
        self.zone_id = zone_id
        self.name = name
        self.valves = valves
        self.floor_area_m2 = floor_area_m2
        self.is_high_priority = is_high_priority
        self.ext_temp_entity_id = ext_temp_entity_id
        self.desired_target: Optional[float] = None   # the zone's own target (shared by its valves)
        self.pending_targets: dict[str, tuple[float, float]] = {}   # valve id -> (value, written at)
        for valve in valves:
            valve.zone = self

    @property
    def entity_id(self) -> str:
        """Same name the single-valve code used for the key of a zone."""
        return self.zone_id

    @property
    def active_valves(self) -> list[ZoneWrapper]:
        available = [v for v in self.valves if v.available]
        return available or list(self.valves)

    # --- temperatures ----------------------------------------------------

    def valve_average(self) -> Optional[float]:
        """Average of the active valves' own readings (our calibration removed), None if none reported."""
        readings = [v.compensated_reading() for v in self.active_valves if v.has_reading]
        return sum(readings) / len(readings) if readings else None

    def refresh_others(self, source: ZoneWrapper) -> None:
        """A valve's reading changed: the other valves' zone temperature and error follow."""
        for valve in self.valves:
            if valve is not source:
                valve._refresh_temperature(cascade=False)

    def set_external_sensor(self, entity_id: Optional[str]) -> None:
        self.ext_temp_entity_id = entity_id
        for valve in self.valves:
            valve.reset_external_reading()

    @property
    def uses_external_sensor(self) -> bool:
        return any(v.has_fresh_external_temperature() for v in self.active_valves)

    @property
    def current_temp(self) -> float:
        return sum(v.current_temp for v in self.active_valves) / len(self.active_valves)

    @property
    def target_temp(self) -> float:
        """The zone's target: its own once set, else what the valves report."""
        if self.desired_target is not None:
            return self.desired_target
        return self.active_valves[0].target_temp

    @property
    def hvac_mode(self) -> str:
        """'off' only if every active valve is off."""
        return "off" if all(v.hvac_state == "off" for v in self.active_valves) else "heat"

    @property
    def current_error(self) -> float:
        return max(v.current_error for v in self.active_valves)

    # --- aggregated state -----------------------------------------------

    @property
    def is_demanding_heat(self) -> bool:
        return any(v.is_demanding_heat for v in self.active_valves)

    @property
    def measured_opening(self) -> float:
        return max(v.measured_opening for v in self.active_valves)

    @property
    def effective_opening(self) -> float:
        return max(v.effective_opening for v in self.active_valves)

    @property
    def boosted(self) -> bool:
        return any(v.boosted for v in self.valves)

    @property
    def held(self) -> bool:
        return any(v.held for v in self.valves)

    def has_hold(self, reason: str) -> bool:
        return any(v.has_hold(reason) for v in self.valves)

    def export_zone_state(self) -> dict[str, Any]:
        """Snapshot for the zone sensors."""
        valves = self.active_valves
        first = valves[0].export_zone_state()
        state = dict(first)
        state.update({
            "name": self.name,
            "floor_area_m2": round(self.floor_area_m2, 2),
            "is_high_priority": self.is_high_priority,
            "valve_count": len(self.valves),
            "has_external_sensor": self.ext_temp_entity_id is not None,
        })
        if len(self.valves) > 1:
            exports = [v.export_zone_state() for v in valves]
            state.update({
                "current_temperature": round(self.current_temp, 2),
                "trv_temperature": round(sum(e["trv_temperature"] for e in exports) / len(exports), 2),
                "temperature_error": round(self.current_error, 2),
                "is_demanding_heat": self.is_demanding_heat,
                "trv_opening_percent": round(self.measured_opening, 1),
                "effective_opening_percent": round(self.effective_opening, 1),
                "held": self.held,
                "boosted": self.boosted,
                "hold_reasons": sorted({r for e in exports for r in e["hold_reasons"]}),
                "temperature_offset": min(e["temperature_offset"] for e in exports),
                "applied_offset": min(e["applied_offset"] for e in exports),
                "valves": {v.name: e for v, e in zip(valves, exports)},
            })
        return state
