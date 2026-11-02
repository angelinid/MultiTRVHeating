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

"""Multi-TRV Heating integration entry point."""

import json
import logging
from pathlib import Path

from homeassistant.config_entries import ConfigEntry
from homeassistant.core import HomeAssistant

from .const import CONF_KEEP_OPEN_VALVE, CONF_ZONES, DOMAIN, LOGGER_NAME, PLATFORMS
from .master_controller import MasterController
from .storage import STORAGE_VERSION, StateStorage, set_storage
from .zones_config import apply_selected_sensors, describe_groups, group_zone_configs, legacy_keep_open

_LOGGER = logging.getLogger(LOGGER_NAME)

VERSION = json.loads((Path(__file__).parent / "manifest.json").read_text())["version"]


async def async_setup_entry(hass: HomeAssistant, entry: ConfigEntry) -> bool:
    """Set up the controller from a config entry and forward to the entity platforms."""
    # Warning level on purpose: visible with HA's default log config, so deploys can be verified
    _LOGGER.warning("Multi-TRV Heating v%s starting", VERSION)
    storage = StateStorage(hass)
    await storage.async_load()
    set_storage(storage)

    controller = MasterController(hass, entry.data.get(CONF_ZONES, []), entry.data.get(CONF_KEEP_OPEN_VALVE))
    hass.data.setdefault(DOMAIN, {})[entry.entry_id] = controller

    await controller.async_start_listening()

    # Entities group themselves into devices via matching DeviceInfo identifiers.
    await hass.config_entries.async_forward_entry_setups(entry, PLATFORMS)
    return True


async def async_unload_entry(hass: HomeAssistant, entry: ConfigEntry) -> bool:
    """Unload the entity platforms and drop the controller."""
    unload_ok = await hass.config_entries.async_unload_platforms(entry, PLATFORMS)
    controller = hass.data.get(DOMAIN, {}).pop(entry.entry_id, None)
    if controller is not None:
        await controller.async_stop_listening()
    return unload_ok


async def async_migrate_entry(hass: HomeAssistant, entry: ConfigEntry) -> bool:
    """Version 1 listed one zone per valve; group the valves that share an external sensor."""
    if entry.version >= 2:
        return True

    flat = entry.data.get(CONF_ZONES, [])
    # The sensors chosen in the UI (kept in storage) are the ones in use: group by those
    from homeassistant.helpers.storage import Store
    stored = await Store(hass, STORAGE_VERSION, f"{DOMAIN}_state_storage").async_load() or {}
    zones = group_zone_configs(apply_selected_sensors(flat, stored, entry.entry_id))
    for line in describe_groups(zones) or ["no valves share a sensor, nothing is merged"]:
        _LOGGER.warning("Config migration 1 -> 2: %s", line)

    data = {CONF_ZONES: zones}
    keep_open_valve, _name = legacy_keep_open(flat)
    if keep_open_valve:
        data[CONF_KEEP_OPEN_VALVE] = keep_open_valve

    # The zone entities of a valve that joined another zone are gone: remove their device
    merged_away = [v["entity_id"] for z in zones for v in z["valves"][1:]]
    _remove_zone_devices(hass, entry, merged_away)

    hass.config_entries.async_update_entry(entry, data=data, version=2)
    return True


def _remove_zone_devices(hass: HomeAssistant, entry: ConfigEntry, climate_entity_ids: list[str]) -> None:
    """Remove the zone devices (and so their entities) of valves that are no longer zones."""
    try:
        from homeassistant.helpers import device_registry as dr
        registry = dr.async_get(hass)
        for entity_id in climate_entity_ids:
            device = registry.async_get_device(
                identifiers={(DOMAIN, f"{entry.entry_id}_{entity_id.replace('.', '_')}")}
            )
            if device:
                _LOGGER.warning("Config migration 1 -> 2: removing the device of '%s'", entity_id)
                registry.async_remove_device(device.id)
    except Exception as err:  # never block the migration on a cleanup
        _LOGGER.warning("Config migration 1 -> 2: could not remove the merged zones' devices: %s", err)
