| Zone | Valves | Thermostat entity |
|---|---|---|
| Bedroom 1 | radiator_1_bedroom, radiator_2_bedroom | `climate.bedroom_1_thermostat` |
| Living Room Heating | radiator_1_living_room, radiator_tv_room | `climate.living_room_heating_thermostat` |
| Bathroom Heating | radiator_bathroom | `climate.bathroom_heating_thermostat` |
| Ensuite Heating | radiator_ensuite | `climate.ensuite_heating_thermostat` |
| Hall Heating | radiator_hall, radiator_kitchen | `climate.hall_heating_thermostat` |
| Leos Heat | radiator_leo | `climate.leos_heat_thermostat` |
| Study Heat | radiator_study | `climate.study_heat_thermostat` |
| Toilet Heat | radiator_toilet | `climate.toilet_heat_thermostat` |

- MultiTRV Heat day: switch.hall_heating_priority_high - merged valves disagree ['on', 'off']; used 'on' (high priority if any)
- MultiTRV Heat night: Hall Heating - Hall was 19, Kitchen 12; set to 12 by the user.
- Heat TRV enabled: switch.hall_heating_priority_high - merged valves disagree ['on', 'off']; used 'on' (high priority if any)

## Apply (after the v2.0.0 deploy and migration)

1. Check the thermostat entity ids above exist (Settings > Entities, search "Thermostat"); the ids here are predicted from the zone names.
2. `scenes.yaml`: replace the file, then reload scenes. Automations need no change: they only call `scene.multitrv_heat_*`.
3. Heating dashboard: paste `lovelace_heating.config.json` into the raw configuration editor (or edit `.storage/lovelace.lovelace_heating` with HA stopped).
4. Entity ids used by the dashboard that HA creates: `switch.<valve name>_hold_open`, `select.<zone name>_external_temperature_sensor`, `select.multi_trv_heating_controller_keep_open_valve`. Check them as in step 1.
5. Removed by the migration (delete from dashboards/scenes if you still have them): `switch.bedroom_2_heating_priority_high`, `switch.tv_room_heat_priority_high`, `switch.kitchen_heat_priority_high` and the old zone sensors of Bedroom 2, TV Room and Kitchen.
