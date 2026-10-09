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

Uses *measured* openings of the other valves (a closing valve at <= 75 % counts as closed; an
unavailable sensor counts as closed):

- engage: none open more than 25 % (a valve closing at 25 % or less counts as closed) and the
  openings add up to less than 100 %;
- release: one at least 50 % open, or the sum >= 100 %, continuously for 30 s, hold older than
  30 s, and not within 5 min of a boiler stop (a heat request that turns the boiler on again
  ends that window);
- selected valve unavailable/stuck: the next usable valve is held (immediately if it cannot be
  driven, after 3 min if it is merely not opening).

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

- Actuator latency is physical: if the last open valve closes faster than the held valve can open
  there is a gap of seconds. In simulation (200 random houses per actuator speed) 5-8 houses see
  such a gap at `CLOSING_ENGAGE_OPENING` = 25 % (mostly 1-22 s, worst 39 s) against 0-1 at 75 %.
  50 % is the middle ground.
- With -9 the TRV only stays fully open while the room is below setpoint + 7 °C.
- A TRV that goes silent looks open until Z2M marks it unavailable (25 min for battery devices).
- A disabled component stops commanding: the boiler flow keeps its last value.
- The `heating_status == 'cooling'` clause is left as it was.

## Tunables

`trv_model.OPENING_CURVE`, `keep_open.py` constants (thresholds, hold and overrun times),
`zone_wrapper.HOLD_TEMP_OFFSET`, `master_controller` write throttling constants.
