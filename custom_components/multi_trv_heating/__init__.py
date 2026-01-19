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

# __init__.py (Modern Component Entry Point)

from homeassistant.config_entries import ConfigEntry
from homeassistant.core import HomeAssistant
from .master_controller import MasterController

DOMAIN = "multi_trv_heating"

async def async_setup_entry(hass: HomeAssistant, entry: ConfigEntry) -> bool:
    """Set up MasterController from a config entry (UI setup)."""
    
    # 1. Load configuration from the UI entry
    zone_configs = entry.data.get("zones", [])
    
    # 2. Instantiate and store the controller
    controller = MasterController(hass, zone_configs)
    hass.data.setdefault(DOMAIN, {})[entry.entry_id] = controller
    
    # 3. Start listening to events
    await controller.async_start_listening()
    
    # 4. Set up sensor, switch, number, and select platforms to expose controller and zone state
    # Home Assistant will automatically discover and call async_setup_entry in sensor.py, switch.py, number.py, select.py
    # Entities will create their own DeviceInfo and group automatically by matching identifiers
    await hass.config_entries.async_forward_entry_setups(entry, ["sensor", "switch", "number", "select"])

    return True

async def async_unload_entry(hass: HomeAssistant, entry: ConfigEntry) -> bool:
    """Handle removal of a config entry."""
    # Unload sensor, switch, number, and select platforms
    unload_ok = await hass.config_entries.async_unload_platforms(entry, ["sensor", "switch", "number", "select"])
    
    # Clean up controller and listeners
    if DOMAIN in hass.data and entry.entry_id in hass.data[DOMAIN]:
        del hass.data[DOMAIN][entry.entry_id]
    
    return unload_ok
