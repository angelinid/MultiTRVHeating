# Multi-valve zones: design

Status: implemented on `feature/multi-trv-zones` (v2.0.0). Decisions taken with the user on 2026-11-02.

## Model

A **zone** is a room. It has 1..n **valves** (TRVs). A valve is what `ZoneWrapper` has always
been (climate entity, position sensor, calibration number, offsets, hold, boost); a new `Zone`
class (`zone.py`) groups valves and owns what is shared:

| Zone (shared) | Valve (own) |
|---|---|
| name, `zone_id`, area, priority | climate entity, position sensor, calibration number |
| external temperature sensor | offsets, hold, boost, heating status, opening |
| target temperature, hvac mode | name (used by the keep-open valve selector) |

`zone_id` is the first valve's climate entity id, so entity ids and device identifiers of
existing single-valve zones do not change.

## Values

- **Zone temperature**: the external sensor while its reading is fresh, else the **average** of
  the valves' own readings with our calibration offset removed (available valves only).
  Every valve's error is computed against the zone temperature.
- **Opening**: maximum over the valves. The low-priority "sum to 100 %" counts a zone once.
- **Demand**: maximum over the valves. A zone is high priority if its priority says so, or one of
  its valves is boosted / held by switch.
- **Area**: counted once per zone (pre-heating load).
- **Unavailable valve**: ignored (temperature, demand, opening) while at least one other valve of
  the zone is available. A zone with no available valve keeps its last values.

## Hold Open switch

One switch per **valve** (named after the valve, on its zone's device), like the keep-open choice.
For a one-valve zone nothing changes (same entity and storage key).

## Keep-open

Works on valves. The "Keep-open valve" select lists valves and replaces the Discharge TRV select.
The choice is stored (`keep_open_valve_select_<id>`); the old `discharge_trv_select_<id>` value is
read once as a fallback. Start-up settling waits for every valve.

## Boost

A zone counts as boosted when any valve's boost switch is on (whoever started it). Boosting the
zone (wrapper climate preset `boost`) switches the boost switch of **all** its valves on; nothing
else does that.

## Target, mode and the wrapper climate entity

`climate.py`: one entity per zone. It owns the target and hvac mode (stored), writes them to every
valve, and shows the zone temperature. A valve changed from outside (dial, Z2M, old scene) is
adopted by the zone (last writer wins) and the other valves are re-synced. Our own writes are
remembered until the valve reports them, so stale reports do not flip the target back. Presets:
`manual` (no boost) and `boost`, so a scene that stores `preset_mode: manual` restores cleanly.
Valves that are unavailable are skipped and re-synced when they come back.

## Config and migration

Config entry version 2: `{"zones": [{zone_id, name, area, is_high_priority, ext_temp_entity_id,
valves: [{entity_id, name, trv_position_entity_id, temp_calib_entity_id}]}], "keep_open_valve"}`.
Version 1 listed one zone per valve. `async_migrate_entry` groups valves that **share an external
sensor** into one zone (name of the first valve, largest area, high priority if any is) and logs
the groups before writing. The controller also accepts the version 1 list and groups it the same
way, so nothing depends on the migration having run. Merged zones keep the first valve's entities;
the others' zone entities are removed (their history ends). Take an HA backup before deploying.

## Rollout

1. Deploy with the Nest override automation off; check the migration log lines.
2. User rewrites dashboard cards, scenes and automations to the wrapper entities.
3. Watch one full schedule day, then re-enable the Nest override.

Back-out: restore the config entry from the HA backup, redeploy the previous version.

## Risks

- The migration is one-way (backup first).
- Setpoint feedback loop between wrapper and valves (pending-write memory).
- Scenes that set valve climates directly still work but are adopted by the zone, last writer wins.
