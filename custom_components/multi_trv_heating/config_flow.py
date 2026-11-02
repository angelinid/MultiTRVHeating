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

import voluptuous as vol

from homeassistant import config_entries
from homeassistant.helpers import selector

from .const import (
    CONF_AREA,
    CONF_ENTITY_ID,
    CONF_EXT_TEMP_ENTITY_ID,
    CONF_KEEP_OPEN_VALVE,
    CONF_NAME,
    CONF_PRIORITY,
    CONF_TEMP_CALIBRATION_ENTITY_ID,
    CONF_TRV_POSITION_ENTITY_ID,
    CONF_VALVES,
    CONF_ZONES,
    DOMAIN,
)

# A zone (room): what its valves share
ZONE_SCHEMA = vol.Schema({
    vol.Required(CONF_NAME): str,
    vol.Optional(CONF_AREA, default=0.0): vol.Coerce(float),
    # Priority: True=high priority (triggers at any opening), False=low priority (needs 100%)
    vol.Optional(CONF_PRIORITY, default=True): bool,
    # Optional room temperature sensor; without it the zone uses the average of its valves' readings
    vol.Optional(CONF_EXT_TEMP_ENTITY_ID): selector.EntitySelector(
        selector.EntitySelectorConfig(domain="sensor", device_class="temperature")
    ),
})

# One valve (TRV) of the zone
VALVE_SCHEMA = vol.Schema({
    vol.Required(CONF_ENTITY_ID): selector.EntitySelector(
        selector.EntitySelectorConfig(domain="climate")
    ),
    vol.Optional(CONF_NAME): str,
    # Valve position sensor (0-100 % opening)
    vol.Required(CONF_TRV_POSITION_ENTITY_ID): selector.EntitySelector(
        selector.EntitySelectorConfig(domain="sensor")
    ),
    # Temperature calibration (offset) number entity of the TRV
    vol.Required(CONF_TEMP_CALIBRATION_ENTITY_ID): selector.EntitySelector(
        selector.EntitySelectorConfig(domain="number")
    ),
})


class OpenThermConfigFlow(config_entries.ConfigFlow, domain=DOMAIN):
    """
    Config flow for the Multi-TRV Heating controller.

    1. zone: name, area, priority, optional room temperature sensor
    2. valve: climate entity, position sensor and calibration number of one TRV
    3. another valve in the same zone? (back to 2)
    4. another zone? (back to 1)
    5. keep-open valve: the valve held open so the pump never runs against closed valves (optional)
    """

    VERSION = 2
    CONNECTION_CLASS = config_entries.CONN_CLASS_LOCAL_PUSH

    def __init__(self) -> None:
        self._zones: list[dict] = []
        self._zone: dict = {}

    async def async_step_user(self, user_input=None):
        """Start a zone."""
        if user_input is not None:
            self._zone = {**user_input, CONF_VALVES: []}
            return await self.async_step_valve()
        return self.async_show_form(
            step_id="user",
            data_schema=ZONE_SCHEMA,
            description_placeholders={"count": len(self._zones)},
        )

    async def async_step_valve(self, user_input=None):
        """Add a valve (TRV) to the zone."""
        if user_input is not None:
            valve = dict(user_input)
            valve.setdefault(CONF_NAME, valve[CONF_ENTITY_ID])
            self._zone[CONF_VALVES].append(valve)
            return await self.async_step_add_valve()
        return self.async_show_form(
            step_id="valve",
            data_schema=VALVE_SCHEMA,
            description_placeholders={"zone": self._zone.get(CONF_NAME, ""), "count": len(self._zone[CONF_VALVES])},
        )

    async def async_step_add_valve(self, user_input=None):
        """Ask whether the zone has another valve."""
        if user_input is not None:
            if user_input.get("add_another"):
                return await self.async_step_valve()
            zone = self._zone
            zone["zone_id"] = zone[CONF_VALVES][0][CONF_ENTITY_ID]
            self._zones.append(zone)
            self._zone = {}
            return await self.async_step_add_zone()
        return self.async_show_form(
            step_id="add_valve",
            data_schema=vol.Schema({vol.Required("add_another", default=False): bool}),
        )

    async def async_step_add_zone(self, user_input=None):
        """Ask whether to add another zone."""
        if user_input is not None:
            if user_input.get("add_another"):
                return await self.async_step_user()
            return await self.async_step_keep_open()
        return self.async_show_form(
            step_id="add_zone",
            data_schema=vol.Schema({vol.Required("add_another", default=True): bool}),
            description_placeholders={"current_count": len(self._zones)},
        )

    async def async_step_keep_open(self, user_input=None):
        """Choose the valve that is held open (optional)."""
        if user_input is not None:
            data = {CONF_ZONES: self._zones}
            if user_input.get(CONF_KEEP_OPEN_VALVE):
                data[CONF_KEEP_OPEN_VALVE] = user_input[CONF_KEEP_OPEN_VALVE]
            self._zones = []
            return self.async_create_entry(title="Multi-TRV Heating Controller", data=data)

        valves = {
            valve[CONF_ENTITY_ID]: valve.get(CONF_NAME, valve[CONF_ENTITY_ID])
            for zone in self._zones for valve in zone[CONF_VALVES]
        }
        return self.async_show_form(
            step_id="keep_open",
            data_schema=vol.Schema({vol.Optional(CONF_KEEP_OPEN_VALVE): vol.In(valves)}),
        )
