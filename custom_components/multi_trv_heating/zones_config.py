"""Zone configuration: grouping valves into zones.

Config entry version 1 listed one entry per valve (a "zone" with one TRV). Version 2 lists zones,
each with its valves. `normalize_zone_configs` accepts both, so the controller does not depend on
the config entry migration having run.
"""

import logging
from typing import Any, Optional

try:
    from .const import LOGGER_NAME
except ImportError:  # imported without Home Assistant (tests)
    from const import LOGGER_NAME

_LOGGER = logging.getLogger(LOGGER_NAME)

VALVE_KEYS = ("entity_id", "name", "trv_position_entity_id", "temp_calib_entity_id", "boost_entity_id")


def _valve_config(flat: dict[str, Any]) -> dict[str, Any]:
    valve = {key: flat[key] for key in VALVE_KEYS if flat.get(key)}
    valve.setdefault("name", flat["entity_id"])
    return valve


def group_zone_configs(flat: list[dict[str, Any]]) -> list[dict[str, Any]]:
    """
    Version 1 list (one entry per valve) -> list of zones. Valves that share an external
    temperature sensor are one room; every other valve is a zone of its own. The zone takes the
    first valve's name and id, the largest area, and is high priority if any valve is.
    """
    zones: list[dict[str, Any]] = []
    by_sensor: dict[str, dict[str, Any]] = {}
    for entry in flat:
        sensor = entry.get("ext_temp_entity_id") or None
        zone = by_sensor.get(sensor) if sensor else None
        if zone is None:
            zone = {
                "zone_id": entry["entity_id"],
                "name": entry.get("name", entry["entity_id"]),
                "area": float(entry.get("area", 0.0) or 0.0),
                "is_high_priority": bool(entry.get("is_high_priority", True)),
                "ext_temp_entity_id": sensor,
                "valves": [],
            }
            zones.append(zone)
            if sensor:
                by_sensor[sensor] = zone
        else:
            zone["area"] = max(zone["area"], float(entry.get("area", 0.0) or 0.0))
            zone["is_high_priority"] = zone["is_high_priority"] or bool(entry.get("is_high_priority", True))
        zone["valves"].append(_valve_config(entry))
    return zones


def normalize_zone_configs(configs: list[dict[str, Any]]) -> list[dict[str, Any]]:
    """Zones with valves, from either config format."""
    if configs and all("valves" in c for c in configs):
        return [dict(c, zone_id=c.get("zone_id") or c["valves"][0]["entity_id"]) for c in configs]
    return group_zone_configs(configs)


def legacy_keep_open(configs: list[dict[str, Any]]) -> tuple[Optional[str], Optional[str]]:
    """The keep-open valve stored on the first entry of a version 1 config: (entity id, name)."""
    first = configs[0] if configs else {}
    return first.get("discharge_trv_entity_id"), first.get("discharge_trv_name")


def describe_groups(zones: list[dict[str, Any]]) -> list[str]:
    """One line per zone with more than one valve, for the migration log."""
    return [
        f"zone '{z['name']}' = " + " + ".join(v["entity_id"] for v in z["valves"])
        for z in zones if len(z["valves"]) > 1
    ]


def apply_selected_sensors(flat: list[dict[str, Any]], stored: dict[str, Any], entry_id: str) -> list[dict[str, Any]]:
    """
    Version 1 flat config with the external sensors chosen in the UI applied.

    Since v1.2.8 a zone's sensor is chosen with a select entity and kept in the integration's
    storage (key `zone_ext_temp_select_<entry>_<zone name slug>_ext_temp_select`, "" = none). That
    choice, not the sensor from the setup form, is the one in use, so the grouping must use it.
    """
    result = []
    for entry in flat:
        slug = entry.get("name", entry["entity_id"]).lower().replace(" ", "_")
        key = f"zone_ext_temp_select_{entry_id}_{slug}_ext_temp_select"
        entry = dict(entry)
        if key in stored:
            entry["ext_temp_entity_id"] = stored[key] or None
        result.append(entry)
    return result
