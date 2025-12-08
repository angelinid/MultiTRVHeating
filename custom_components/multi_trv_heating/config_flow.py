import logging
import voluptuous as vol

from homeassistant import config_entries
from homeassistant.core import callback
from homeassistant.helpers import selector

_LOGGER = logging.getLogger(__name__)

# Component domain - must match DOMAIN in __init__.py
DOMAIN = "multi_trv_heating"

# Configuration keys
CONF_ENTITY_ID = "entity_id"                              # Climate entity to control
CONF_NAME = "name"                                        # Zone name
CONF_AREA = "area"                                        # Zone floor area in m²
CONF_PRIORITY = "is_high_priority"                        # Zone priority (boolean: True=high, False=low)
CONF_TRV_POSITION_ENTITY_ID = "trv_position_entity_id"    # TRV position sensor entity
CONF_TEMP_CALIBRATION_ENTITY_ID = "temp_calib_entity_id"  # Temperature calibration (offset) number entity
CONF_EXT_TEMP_ENTITY_ID = "ext_temp_entity_id"            # Optional external temperature entity

# Define the data schema for the zone configuration
# This defines the structure of data for each zone
DATA_SCHEMA = vol.Schema({
    # Use EntitySelector for searchable dropdown (filter to climate entities)
    vol.Required(CONF_ENTITY_ID): selector.EntitySelector(
        selector.EntitySelectorConfig(domain="climate")
    ),
    
    vol.Optional(CONF_NAME): str,
    vol.Optional(CONF_AREA, default=0.0): vol.Coerce(float),
    
    # Priority: boolean True=high priority (triggers at any opening), False=low priority (needs 100%)
    vol.Optional(CONF_PRIORITY, default=True): bool,
    
    # TRV valve position sensor (e.g., sensor.radiator_1_bedroom_position)
    # Reports 0-100% valve opening
    vol.Required(CONF_TRV_POSITION_ENTITY_ID): selector.EntitySelector(
        selector.EntitySelectorConfig(domain="sensor")
    ),
    
    # Temperature calibration (offset) number entity (e.g., number.radiator_1_bedroom_local_temperature_calibration)
    # Allows adjusting TRV's temperature reading to make it open/close more
    vol.Required(CONF_TEMP_CALIBRATION_ENTITY_ID): selector.EntitySelector(
        selector.EntitySelectorConfig(domain="number")
    ),
    
    # Optional external temperature entity for better temperature readings
    # Useful when TRV is mounted near radiator and gives inaccurate readings
    vol.Optional(CONF_EXT_TEMP_ENTITY_ID): selector.EntitySelector(
        selector.EntitySelectorConfig(domain="sensor")
    ),
})


class OpenThermConfigFlow(config_entries.ConfigFlow, domain=DOMAIN):
    """
    Handle config flow for the Multi-TRV Heating MasterController.
    
    This configuration flow guides users through:
    1. Adding zones (climate entities with TRV valves)
    2. Setting zone properties:
       - Zone name (optional, defaults to entity name)
       - Floor area in m² (optional, for demand calculation)
       - Priority level: 0.0-1.0 (optional, defaults to 1.0)
         * > 0.5: High priority (triggers at 25% opening)
         * <= 0.5: Low priority (needs 100% or aggregates)
       - External temperature sensor (optional, for better readings)
    3. Adding multiple zones iteratively
    4. Finalizing configuration
    """

    VERSION = 1
    CONNECTION_CLASS = config_entries.CONN_CLASS_LOCAL_PUSH

    # Internal list to store zone configurations as the user adds them
    # This is cleared after config entry creation
    _zones_config = [] 

    async def async_step_user(self, user_input=None):
        """
        Handle the initial step when user adds the component.
        
        Presents form to configure a zone:
        - Select a climate entity (required) - the TRV thermostat
        - Enter zone name (optional, defaults to entity name)
        - Enter floor area in m² (optional)
        - Set priority (required boolean) - True=high priority (any opening), False=low priority (needs 100%)
        - Select TRV position sensor (required) - e.g., sensor.radiator_1_bedroom_position
        - Select temperature calibration entity (required) - e.g., number.radiator_1_bedroom_local_temperature_calibration
        - Select optional external temperature sensor - for more accurate room readings
        
        Args:
            user_input: Dictionary of user input from form
        """
        errors = {}

        if user_input is not None:
            # Validate and store the zone configuration
            self._zones_config.append(user_input)
            
            # Ask user if they want to add another zone
            return await self.async_step_add_another()
            
        # Initial form shown to the user
        return self.async_show_form(
            step_id="user",
            data_schema=DATA_SCHEMA,
            errors=errors,
            description_placeholders={"count": len(self._zones_config)}
        )

    async def async_step_add_another(self, user_input=None):
        """
        Ask the user if they want to add another zone.
        
        After successful zone configuration, offers the user a choice to:
        - Add another zone (returns to async_step_user)
        - Finish configuration (creates config entry with all zones)
        
        Args:
            user_input: Dictionary with 'add_another' boolean
        """
        if user_input is not None:
            if user_input.get("add_another"):
                # If yes, go back to the user step to collect data for the next zone
                return await self.async_step_user()
            
            # If no, finalize the configuration with all collected zones
            # Reset the zones list for next time this config flow runs
            zones = self._zones_config
            self._zones_config = []
            
            return self.async_create_entry(
                title="Multi-TRV Heating Controller",
                data={"zones": zones},  # Save the final list of zones
            )

        # Form to ask if more zones are needed
        return self.async_show_form(
            step_id="add_another",
            data_schema=vol.Schema({
                vol.Required("add_another", default=True): bool
            }),
            description_placeholders={"current_count": len(self._zones_config)}
        )
