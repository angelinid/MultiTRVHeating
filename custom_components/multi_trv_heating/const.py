"""Shared constants for the Multi-TRV Heating integration."""

DOMAIN = "multi_trv_heating"

# All modules log to this logger so it can be tuned with a single HA `logger:` entry.
LOGGER_NAME = "don_controller"

PLATFORMS = ["sensor", "switch", "number", "select"]

# OpenTherm flow temperature limits while the boiler is ON (°C).
MIN_FLOW_TEMP = 25.0
MAX_FLOW_TEMP = 60.0

# Config entry keys (per zone, stored in entry.data["zones"]).
CONF_ENTITY_ID = "entity_id"                              # Climate entity of the zone
CONF_NAME = "name"                                        # Zone name
CONF_AREA = "area"                                        # Zone floor area in m²
CONF_PRIORITY = "is_high_priority"                        # True=high, False=low priority
CONF_TRV_POSITION_ENTITY_ID = "trv_position_entity_id"    # TRV valve position sensor
CONF_TEMP_CALIBRATION_ENTITY_ID = "temp_calib_entity_id"  # TRV temperature calibration number
CONF_EXT_TEMP_ENTITY_ID = "ext_temp_entity_id"            # Optional external temperature sensor
# Pump discharge settings, stored on the first zone's config.
CONF_DISCHARGE_TRV_ENTITY_ID = "discharge_trv_entity_id"
CONF_DISCHARGE_TRV_NAME = "discharge_trv_name"
