# Refactor notes (branch `refactor/cleanup`)

Behaviour-preserving cleanup. Domain, entity names, unique IDs, DeviceInfo,
storage keys, config entry schema and the `don_controller` logger name are all
unchanged. Python in `custom_components/multi_trv_heating`: 3267 -> ~2260 lines.

## Verification

- `tests/run_tests.sh` / `test_runner.py`: 85/87 before and after. The same 2
  core tests fail on `main` (see "Suspected bugs" #1).
- A throwaway harness (not committed) stubbed `homeassistant`, ran the old and
  new code side by side, and diffed the results:
  - every platform entity: attributes, unique IDs, device info, restore from
    storage (empty, populated and junk), setters/toggles, persisted keys
  - 400 random climate/position/external-temp/pre-heating events through
    `MasterController`: flow temps, offsets, demand, discharge, service calls.
  Old and new matched exactly.

## Module layout

| Module | Role |
|---|---|
| `const.py` (new) | `DOMAIN`, `LOGGER_NAME`, `PLATFORMS`, `MIN/MAX_FLOW_TEMP`, `CONF_*` keys. These used to be copied across 5 files. |
| `entity.py` (new) | Shared platform plumbing: `prefixed_unique_id`, `zone_slug`, `controller_device_info`, `zone_device_info`, `get_controller`, `PersistentEntityMixin` (storage key `<STORAGE_PREFIX>_<unique_id>`). |
| `master_controller.py` | `_evaluate_boiler_demand()` split out of `_calculate_and_command()`. Shared helpers `_zone_for_sensor`, `_read_float_state` and `_async_push_zone_offset`. `demand_to_flow_temp()`, `VALVE_OPEN_DELAY`. |
| `zone_wrapper.py` | Unused constants removed (`MIN/MAX_TEMP_OFFSET`, `HIGH_PRIORITY_MIN_OPENING`). `update_trv_opening` annotated `-> bool` (it already returned a bool). |
| `preheating.py` | Magic numbers named (`TUNING_CONSTANT_MIN/MAX`, `TIME_PRESSURE_SCALE`, `MIN_RUN_MINUTES`, `TUNING_ADJ_*`). Still re-exports `MIN/MAX_FLOW_TEMP` for the tests. |
| `pump_discharge.py` | Enable and disable now share `_async_set_boost()` / `_boost_switch_id()`. |
| `number.py` | Hour and minute entities now share `_PreheatingEndTimeNumber`. Area and tuning limits are constants. |
| `switch.py` | The base class does turn_on/off, writes state and persists. Each subclass only implements `_apply()`. |
| `select.py`, `sensor.py` | Use the shared helpers. Dead code and unused imports removed. |
| `__init__.py`, `storage.py`, `config_flow.py` | Use `const`. Unused imports and logger removed. |

Modules imported directly by the tests (`sensor`, `entity`, `storage`, core
modules) keep the `try: from .x import ... except ImportError: from x import ...`
pattern.

## Logging policy (logger `don_controller`)

- **debug**: per-cycle detail. This covers zone updates, boiler decision and
  reason, flow temp, offsets pushed, pre-heating calculation, skipped
  unknown/unavailable sensors, and platform setup counts.
- **info**: meaningful state changes. These are boiler ON/OFF transitions
  (with reason and flow temp), pre-heating started/stopped/complete, tuning
  constant updates, pump discharge started/stopped, user changes made through
  entities, values restored from storage, zone registration and listener
  start-up.
- **warning/error**: unparseable sensor values, invalid states, missing
  controller, and service call failures.

Removed noise at info level: "Component disabled" and "Boiler command" on every
event, external temp on every update, the pre-heating override on every cycle,
and duplicate pump discharge lines. The tuning-constant log labels had old/new
swapped; they are fixed.

## Suspected bugs / oddities (left alone)

1. **2 failing core tests on `main`**: "1% opening should trigger
   (high-priority)" and "High-priority at 30% should trigger". High-priority
   demand is `current_error > 0 or heating_status == 'cooling'`, so it does not
   depend on valve opening. Either the tests or the logic are stale.
2. `heating_status` naming looks inverted. A closing valve sets `'heating'` and
   an opening valve (>75%) sets `'cooling'`, and `'cooling'` counts as demand.
   The original code had a comment that the author doesn't remember why.
3. When pre-heating ends inside `calculate_flow_temp_override()` (expired or
   complete), it returns `0.0`. That cycle then commands flow temp 0 (boiler
   OFF) even if zones are demanding heat. Normal control resumes on the next
   event.
4. The pre-heating "expired" branch (tuning factor 2.0) is effectively
   unreachable. `is_active()` already returns False once the end time passes,
   so expiry never triggers learning.
5. `_preheating_disabled()` sets `is_enabled = False` but does not update the
   persisted `preheating_enabled_*` value. After a restart, pre-heating comes
   back enabled.
6. `is_discharge_valve()` is only true *while discharging*. Outside a
   discharge, the discharge TRV takes part in boiler demand. The docstring
   used to say otherwise.
7. Pump discharge timeout is only checked on the next control cycle. If no
   events arrive, the boost switch can stay on indefinitely.
8. `MasterController.current_flow_temp` starts at `MIN_FLOW_TEMP` (25). Flow
   temp is never sent to any HA entity: `set_opentherm_flow_temp` only records
   it. The 20 s valve delay `asyncio.sleep` blocks the event handler.
9. State-change listeners are never unsubscribed in `async_unload_entry`, so
   they leak across reloads.
10. `StateStorage` is a module-level global. Multiple config entries would
    share and overwrite it.
11. The controller DeviceInfo in `switch.py` uses a different name,
    manufacturer and model ("MultiTRVHeating Controller" / "Custom") from
    sensor, number and select, but the same identifier. The final device name
    depends on platform setup order. Kept as-is and marked with a NOTE.
12. `OpenThermConfigFlow._zones_config` is a class-level mutable list, so it is
    shared between concurrent or abandoned flows.
13. Unique IDs are derived from zone *names*, so renaming a zone orphans its
    entities and stored values. The DeviceInfo uses the entity_id instead.
14. Restored switch values are not type-checked. A corrupt storage value such
    as `"x"` becomes `zone.is_high_priority = "x"`.
15. Sensors override `state` instead of `native_value`, which bypasses HA unit
    handling. Area and tuning setters don't trigger a recalculation.
16. README mentions a `multi_trv_heating.get_zone_state` service, but no
    services are registered.
17. Offsets are pushed even when `temp_calib_entity_id` is None, which makes
    a service call with `entity_id: None`. While the boiler is OFF, every
    control cycle re-sends `0` to every zone's calibration entity. That is a
    lot of redundant service calls.
