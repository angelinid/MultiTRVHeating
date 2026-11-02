"""Per-zone settings changed from the HA UI, kept in the config entry options.

The config entry data (set when the integration is added) is never rewritten. Options are
stored apart from it, so they survive restarts and reloads, and win over the data.
"""

from typing import Any, Mapping

try:
    from .const import CONF_ENTITY_ID, CONF_EXT_TEMP_ENTITY_ID
except ImportError:  # imported without Home Assistant (tests)
    from const import CONF_ENTITY_ID, CONF_EXT_TEMP_ENTITY_ID

OPT_EXT_TEMP = "ext_temp"  # {climate entity_id: sensor entity_id, or "" for none}


def zones_with_options(zones: list[dict[str, Any]], options: Mapping[str, Any]) -> list[dict[str, Any]]:
    """Zone configs with the external temperature sensors from the options applied."""
    overrides = options.get(OPT_EXT_TEMP) or {}
    result = []
    for zone in zones:
        zone = dict(zone)
        entity_id = zone.get(CONF_ENTITY_ID)
        if entity_id in overrides:
            zone[CONF_EXT_TEMP_ENTITY_ID] = overrides[entity_id] or None
        result.append(zone)
    return result
