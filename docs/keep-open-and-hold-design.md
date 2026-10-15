# Keep-open valve and hold mode

Implemented on `feature/min-open-valve` (manifest 1.2.0), not merged, not committed at the time
of writing.

## Problem

1. When every TRV is closed while the boiler pump is running (including the ~5 minutes it keeps
   circulating after it is told to stop), the pipework sees a pressure difference. At least one
   valve must always be physically open.
2. A room (e.g. a child's bedroom overnight) should keep its valve open for a whole period and
   let its own room sensor drive the boiler.

The TRVs have no minimum-opening setting, so a valve is held open by writing the lowest
calibration offset (-9 °C): the TRV believes the room is 9 °C colder and opens fully.

## Concepts

- **Effective temperature** (`ZoneWrapper.current_temp`): fresh external sensor, else the TRV
  reading minus the offset actually on the device (the TRV's reading moves with its calibration at
  once, so an offset just written counts as applied). Our own offsets never look like a cold room.
- **Effective opening** (`ZoneWrapper.effective_opening`): measured opening for a normal zone; for
  a held zone a *virtual* opening from the room error via `trv_model.OPENING_CURVE`
  (0.5/1/1.5/2 °C -> 25/50/75/100 %). Boiler demand, low-priority sums and flow temperature all
  use it, so a held zone behaves like any other valve.
- **One offset policy** (`ZoneWrapper.desired_offset`): hold offset while held, else the old
  opening-based -2 °C rule. **One writer** (`MasterController._sync_offsets`): change-only,
  throttled, retried when the device does not keep a value.
- **Hold reasons**: `switch` (per-zone `Hold Open` switch, restored on restart; the zone counts as
  high priority while held) and `auto` (keep-open controller, priority unchanged).

## Keep-open rule (`keep_open.py`)

The selected valve is held **permanently** (v1.2.3). Nothing else releases it: a heat request from
any other zone, a boost or the pump overrun do not matter, so one valve is always open. The
held room may run warmer than its target while the boiler runs; that is accepted.

The hold moves only when the selected valve changes, as a handover:

- the new valve is held first, the old one stays held;
- the new valve is *confirmed* when its calibration offset is on the device and it is measured
  open more than 50 %; only then is the old hold released;
- not confirmed within 3 min: the old hold is kept and a warning is logged with the reason
  (offset not applied, setpoint too low, valve not opening);
- selected valve unavailable/stuck and nothing confirmed: the next usable valve is held as well
  (immediately if it cannot be driven, after 3 min if it is merely not opening); the extra hold is
  released once the selected valve is confirmed;
- no valve selected: the hold is dropped.

The valve is the existing "Discharge TRV" select.

## Boiler safety

- Interlock: no flow is requested unless a valve is measured open (also for pre-heating), and it
  is checked again after the 20 s valve-open delay.
- The control loop also runs every 30 s, so timeouts work with no entity changes.
- The controller seeds from current entity states at startup.

## Rollout switch

`Hold Offset Writes` (controller switch, default OFF): while off, hold decisions are made, logged
and shown on the sensors but the hold offset is not written to the TRVs.

## Known limits

- With -9 the TRV only opens while its setpoint is above (room temperature - 9 °C). A held valve
  with a frost setpoint stays shut; the confirmation timeout logs this ("raise the setpoint").
- Because the hold is permanent there is no engage race: a valve is open before any other closes.
  The only gap is at boot or after a selection change, covered by the handover rule.
- A TRV that goes silent looks open until Z2M marks it unavailable (25 min for battery devices).
- A disabled component stops commanding: the boiler flow keeps its last value.
- The `heating_status == 'cooling'` clause is left as it was.

## Tunables

`trv_model.OPENING_CURVE`, `keep_open.py` constants (`CONFIRM_OPENING`, `CONFIRM_TIMEOUT`, `BOILER_PUMP_OVERRUN`),
`zone_wrapper.HOLD_TEMP_OFFSET`, `master_controller` write throttling constants.
