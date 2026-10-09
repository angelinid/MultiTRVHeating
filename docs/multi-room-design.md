# Multi-room support: design

Status: draft, not started. Written 2026-10-09 while brainstorming the "keep one valve open"
feature (see "Relationship to other work"). Nothing here is implemented.

## Goal

A **zone is a room**, and a room can have **one or more TRV valves**. Each zone gets a single
wrapper `climate` entity that is the one place to read and set the room's temperature.

## Why

- Today a zone has exactly one TRV (`entity_id`, `trv_position_entity_id`,
  `temp_calib_entity_id` in `const.py`). A room with two radiators has to be entered as two
  zones. On the live install, "Bedroom 1" and "Bedroom 2 Heating" are exactly that: two zones,
  35 m² each, both pointing at `sensor.tuya_temperature_sensor_temperature`.
- The dashboard shows the TRV's own reported temperature, which includes the calibration
  offsets the controller pushes (-2 °C while heating, more for a held-open valve). Users read
  the wrong room temperature.
- Setpoints are set per TRV (the schedule scenes write each `climate.radiator_*`), so two
  radiators in one room can drift apart.

## Requirements

1. A zone holds 1..n valves; valves of a zone share one external sensor, priority, area, name.
2. One wrapper climate entity per zone: the room temperature, the room target, hvac mode and
   action. Setting the target writes to all valves of the zone.
3. The room target is owned by the integration (persisted), not read back from a TRV.
4. Boiler decisions work on zones, not valves: no double counting of two radiators.
5. Existing installs migrate without manual steps and keep their entity IDs where possible.
6. The effective temperature never includes our own calibration offsets.

Non-goals: new heating algorithms, changing how the boiler flow temperature is computed,
pre-heating changes (it must keep working on zones).

## Data model

```
Zone                                  Valve
  zone_id (stable, generated)           climate_entity_id
  name, area_m2, is_high_priority       trv_position_entity_id
  ext_temp_entity_id (optional)         temp_calib_entity_id
  valves: [Valve]                       applied_offset (what we last pushed)
  target_temp (owned, persisted)        trv_temp (as reported)
  hvac_mode                             opening_percent
```

`ZoneWrapper` (zone_wrapper.py) becomes the zone, holding a list of valve objects. The
per-valve parts of today's `ZoneWrapper` (position, calibration entity, applied offset,
`update_trv_opening`, heating status) move to the valve class.

### Zone values derived from the valves

| Value | Rule |
|---|---|
| Opening | max over valves |
| Effective temperature | fresh external sensor reading, else min over valves of (`trv_temp` - `applied_offset`) |
| Error / demand | from the effective temperature and the zone target, as today |
| Heating status / offset logic | per valve (each valve's own opening) |

Staleness of the external sensor: 30 min (`EXT_TEMP_MAX_AGE`), already implemented for
single-valve zones.

Rationale for "lowest valve reading": heat until the coldest valve is satisfied. Valves
regulate themselves, so a satisfied valve closes while another keeps heating. The zone only
reports the minimum for demand and display.

## Grouping and migration

Existing config entries list one zone per valve. The migration (config entry version bump,
`async_migrate_entry`) groups them:

- valves that share an `ext_temp_entity_id` become one zone (on the live install only the
  two bedrooms share one: `sensor.tuya_temperature_sensor_temperature`);
- every other valve becomes a one-valve zone;
- zone name: the first valve's name, area: the largest value among the grouped valves,
  priority: high if any is high.

Sharing a sensor is only a migration heuristic. After migration the grouping is explicit in
the stored config, so pointing two rooms at one sensor later does not merge them.

The migration should be previewable: log (and show in the options flow) the groups it built
before writing, so the user can correct them.

## Target storage and sync

- `storage.py` persists `target_temp` and `hvac_mode` per zone.
- Wrapper `set_temperature` / `set_hvac_mode` store the value, then call
  `climate.set_temperature` / `climate.set_hvac_mode` on every valve.
- If a valve's setpoint changes outside the wrapper (dial, Z2M, old card), the last writer
  wins: that value becomes the zone target and the other valves are re-synced. To avoid a
  feedback loop, ignore valve state changes that match what the wrapper just wrote.
- Valve `unavailable`: the wrapper stays available while at least one valve is available;
  sync skips unavailable valves and re-applies when they return.

## Wrapper climate entity

New `climate.py` platform, one entity per zone, `unique_id` from the stable `zone_id`.

- `current_temperature`: the zone's effective temperature.
- `target_temperature`, `hvac_mode` (heat/off), `hvac_action` (heating when any valve opening
  is above 0 and the zone is below target, else idle).
- Attributes: `valve_temperatures`, `applied_offsets`, `valve_positions`, `temperature_source`
  (external / trv).
- Must support `scene.turn_on` restores (the schedule scenes): `set_temperature` and
  `set_hvac_mode` must both work with the attributes a scene stores.

## Entities and IDs

- Zone sensors, numbers and switches are currently keyed by zone name. Move to `zone_id`
  based unique IDs; keep the existing entity IDs for zones that do not change.
- A merged zone (the bedrooms) keeps the first valve's entities. The second zone's entities
  are removed from the entity registry; their history ends. Document this in the release
  notes.

## Config flow

- Add zone: name, area, priority, optional external sensor, then one or more valves (climate
  entity, position sensor, calibration number). "Add another valve" loop.
- Edit zone: add/remove valves (options flow).
- The pump discharge valve selector currently lists zones and is stored on the first zone's
  config. Replace by a valve selector and a top-level setting.
- Fix the shared class-level zone list in the config flow (known issue, REFACTOR_NOTES.md).

## Controller changes

- `MasterController` listens to every valve's climate/position entity plus each zone's
  external sensor; maps events to (zone, valve).
- `_evaluate_boiler_demand` iterates zones. Low-priority sums use the zone opening.
- `_reset_all_zone_offsets` and offset pushes iterate valves; make offset writes change-only
  (today they are written on every event while the boiler is off).
- Pre-heating thermal load uses the zone area once per zone (the area is no longer entered
  twice for a two-radiator room).

## Dashboard and scenes (user-side changes)

- The 11 thermostat cards in the Heating dashboard (`lovelace.lovelace_heating`) point at
  wrapper entities. Bedroom becomes one card.
- `scenes.yaml`: the `scene.multitrv_heat_*` scenes set each `climate.radiator_*`. Rewrite them
  to set the wrapper entities. The 7 `TRV Heat ...` automations only call the scenes and do
  not change.
- Draft the rewritten scenes as a file in the repo, review it, then apply it by hand.

## Tests

- Update the existing suites to the zone/valve model.
- Migration: single-valve entries, shared-sensor grouping, idempotence, unknown fields.
- Zone values: opening = max, lowest reading with and without external sensor, offsets
  removed per valve.
- Wrapper: set target fans out to all valves, external change adopted and re-synced without a
  loop, one valve unavailable, scene restore.
- Config flow: add/remove valves.
- A live smoke test on the installed instance with the debug logger before enabling writes.

## Rollout

1. Branch from the keep-open work (the effective temperature code is per valve and moves
   into the valve class). Own PR, own version bump (minor).
2. Deploy to the live install with the Nest override automation still disabled; verify the
   migration log and that the zone list is as expected.
3. Switch the dashboard cards to the wrappers.
4. Rewrite the scenes, enable, watch one full schedule day.
5. Re-enable the Nest override only after that.

Back-out: restore the config entry from the HA backup, redeploy the previous version.

## Risks

- Config entry migration is one-way. Mitigation: HA backup before deploying, previewable
  grouping, idempotent migration.
- Entity registry cleanup of the removed bedroom entities could take dashboards or history
  cards with it.
- Setpoint feedback loop between wrapper and valves.
- Scene restore silently doing nothing if the wrapper's service handling is wrong.
- Behaviour change for merged rooms: one demand instead of two, zone area counted once.

## Open questions

- Behaviour when a valve is unavailable for a long time: ignore it for demand, or fall back?
- Should the zone offer per-valve exclusion from the "lowest reading" (a valve behind a
  curtain)? Not planned; revisit if it matters.
- Whether the effective-temperature reading includes the calibration offset was inferred, not
  verified on the live system (all calibrations were -2.0 and the TRVs read 2-4 °C below the
  room sensors). Verify before relying on it.
- Multiple external sensors per zone (average)? Not planned.

## Relationship to other work

- **Keep-one-valve-open / hold mode** (separate design): works on valves and the boiler
  decision and does not need grouping. It ships first. Its dashboard trade-off (the held
  zone's thermostat card reads low) is fixed by the wrapper in this work.
- Pump discharge (boost-based) is removed once the hold mode is proven.
