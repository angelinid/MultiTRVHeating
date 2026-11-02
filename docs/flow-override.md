# Flow override (v2.1.0)

A temporary override of the boiler flow temperature, set from the dashboard.

- **Slider**: `number.multi_trv_heating_controller_flow_override`, 0-60 °C. Opened by tapping the flow
  gauge. It shows the flow the boiler is asked for now (0 = off). **Below 25 °C (the boiler minimum)
  switches the boiler off**; 25-60 requests that flow.
- **Setting it** starts the override for 15 minutes, then the schedule takes over again. Setting it
  again restarts the 15 minutes. Not persisted: an HA restart ends it.
- **Cancel**: `button.multi_trv_heating_controller_cancel_flow_override`. The dashboard shows it (a
  `conditional` card) only while `binary_sensor.multi_trv_heating_controller_flow_override_active` is on.
  The backend cannot tell that the slider dialog was reopened, so reopening cannot cancel.
- **Needs an open valve**: refused unless a valve is measured open when it is set. The refusal is
  raised as an error, which Home Assistant shows as a pop-up. If every valve closes later the
  interlock stops the boiler; the override and its timer carry on (attribute `override` says why).
- **One open valve**: the flow is limited to 45 °C (`MAX_FLOW_ONE_VALVE`) while only one valve is open.
- **No confirmations**: nothing is announced when it is set or ends; the slider shows the flow.
- **Precedence**: the override replaces the scheduled and pre-heating request; the interlock and the
  pump-overrun rules still apply after it. The Nest override automation follows the flow sensor.
