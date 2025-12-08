# Multi-TRV Heating Controller for Home Assistant

## Overview

The **Multi-TRV Heating Controller** is a Home Assistant custom component designed to intelligently manage heating systems in multi-zone homes using Zigbee TRV (Thermostatic Radiator Valve) units. It aggregates heating demands from multiple zones and proportionally controls a central boiler to optimize energy efficiency while maintaining comfort in all zones.

## Purpose & Scope

Traditional home heating systems either:
- **Zone systems**: Control each room's radiator valve independently but lack centralized boiler control
- **Whole-house systems**: Control only a single boiler temperature for the entire house, ignoring individual zone needs

This component bridges the gap by:
1. **Monitoring** multiple TRV valves and their opening percentages
2. **Aggregating** heating requests from all zones based on priority levels
3. **Controlling** a central boiler to match the aggregate demand
4. **Optimizing** energy usage by setting boiler intensity proportionally to actual heating need

## Key Features

### 1. Multi-Zone Support with Priority Levels
- Configure any number of heating zones (rooms/areas)
- Each zone has a **priority level** (high or low):
  - **High Priority** (>0.5): Can trigger boiler independently at 25% TRV opening
  - **Low Priority** (≤0.5): Requires either 100% opening OR aggregation with other low-priority zones
  
### 2. Intelligent Boiler Control
- **Demand Metric**: Each zone calculates a heat demand (0.0 to 1.0) based on:
  - How far below target temperature the zone currently is
  - Current TRV valve opening percentage
  
- **Boiler Intensity**: Set proportionally to the **highest demand** among all zones
  - 0 demand → Boiler OFF (5°C flow temperature)
  - 0.5 demand → Medium heat (42.5°C flow temperature)
  - 1.0 demand → Maximum heat (80°C flow temperature)

### 3. Zone Aggregation for Low Priority Areas
- Multiple low-priority zones can trigger the boiler together
- Example: Two zones at 50% opening each = 100% aggregate demand = boiler ON
- This allows cost-effective heating of less-important areas

### 4. Temperature Offset Feature
Each TRV valve can report its opening percentage but may have inaccurate temperature readings if mounted near a radiator. The component includes optional **temperature offset** adjustment:
- Reduce the TRV's internal temperature offset (-5°C to +5°C) when heating is needed
- This tricks the valve into opening more while the boiler provides heat
- Reset offset to 0°C when zone reaches target temperature

### 5. External Temperature Sensors
For zones with unreliable TRV temperature readings, optionally connect external temperature sensors:
- Mounted away from radiators for accurate ambient readings
- Readings exported alongside zone state for monitoring
- Helps with demand calculation accuracy

### 6. Extensive Logging & Monitoring
- Export complete zone state including:
  - Current vs target temperature
  - Temperature error and demand metric
  - TRV opening percentage
  - Priority level and heating status
  - External sensor temperature (if available)

## How It Works

### Component Architecture

```
┌─────────────────────────────────────────────────────────────┐
│  Home Assistant Integration Layer                            │
│  - Config Flow (UI-based zone setup)                        │
│  - State change event listeners                             │
└────────────────┬────────────────────────────────────────────┘
                 │
         ┌───────┴──────────────────────────────────┐
         │   MasterController                       │
         │  - Aggregates zone demands              │
         │  - Applies priority logic               │
         │  - Calculates boiler intensity          │
         │  - Commands flow temperature            │
         └────────────────────────┬────────────────┘
                                  │
        ┌─────────────┬───────────┼───────────┬──────────────┐
        │             │           │           │              │
    ┌───▼──┐     ┌────▼──┐  ┌────▼──┐   ┌────▼──┐      ┌────▼──┐
    │Zone1 │     │Zone2  │  │Zone3  │   │Zone4  │      │ZoneN  │
    │      │     │       │  │       │   │       │      │       │
    │High  │     │Low    │  │High   │   │Low    │      │...    │
    │Pri   │     │Pri    │  │Pri    │   │Pri    │      │       │
    └──────┘     └───────┘  └───────┘   └───────┘      └───────┘
         │             │           │           │              │
         └─────────────┴───────────┴───────────┴──────────────┘
                       │
         ┌─────────────▼───────────────┐
         │  Boiler Control System      │
         │  (OpenTherm/ESPHome)        │
         │  - Receives flow temp       │
         │  - Sets boiler intensity    │
         └─────────────────────────────┘
```

### Decision Flow

For each zone state change (temperature update or valve opening change):

```
1. Update affected zone(s) with new state
   ├─ Update temperature reading
   ├─ Update TRV opening percentage
   └─ Update external sensor reading (if available)

2. Recalculate zone demands
   ├─ HIGH PRIORITY zones: demanding if opening >= 25%
   └─ LOW PRIORITY zones: demanding if opening >= 100%

3. Determine boiler state
   ├─ IF any high-priority zone is demanding
   │   └─ BOILER ON
   ├─ ELSE IF low-priority aggregate >= 100%
   │   └─ BOILER ON
   └─ ELSE
       └─ BOILER OFF

4. Calculate boiler intensity (flow temperature)
   ├─ Find the highest demand metric among all zones
   ├─ Convert to flow temperature (5°C to 80°C)
   └─ Send command to boiler

5. Optional: Adjust temperature offsets
   ├─ For zones demanding heat: Reduce offset (make valve open more)
   └─ For zones at target: Reset offset to 0
```

## Configuration

### Setup via UI

1. Go to **Settings → Devices & Services → Create Automation**
2. Select **Multi-TRV Heating Controller**
3. For each zone, configure:
   - **Climate Entity**: Select your TRV valve (required)
   - **Zone Name**: User-friendly name (optional)
   - **Floor Area**: Area in m² (optional, for logging)
   - **Priority**: 0.0-1.0 (optional)
     - > 0.5: High priority
     - ≤ 0.5: Low priority
   - **External Temp Sensor**: Select external temperature sensor (optional)
4. Add more zones or finish configuration

### Example Configuration

```yaml
# Example zone setup (UI form):

Zone 1: Living Room
  - Climate Entity: climate.living_room_trv
  - Priority: 1.0 (high priority - living room should always be warm)
  - Area: 35 m²
  - External Sensor: sensor.living_room_temperature

Zone 2: Bedroom 1
  - Climate Entity: climate.bedroom_1_trv
  - Priority: 0.8 (high priority - bedroom needs heat)
  - Area: 20 m²

Zone 3: Guest Room
  - Climate Entity: climate.guest_room_trv
  - Priority: 0.3 (low priority - only heat if others demand it too)
  - Area: 15 m²

Zone 4: Hallway
  - Climate Entity: climate.hallway_trv
  - Priority: 0.2 (low priority - minimal heating needed)
  - Area: 10 m²
```

## Requirements

### Hardware
- **Home Assistant** instance (2024.2.0 or later)
- **Zigbee TRV valves** that:
  - Report as climate entities in Home Assistant
  - Expose their valve opening percentage (0-100%)
  - Ideally support temperature offset adjustment (-5 to +5°C)
- **Boiler control** via OpenTherm protocol with ESPHome integration
  - Typically: ESPHome device running OpenTherm protocol
  - Exposes a `number` entity for flow temperature setting
  - Default entity: `number.opentherm_flow_temp` (configurable)

### TRV Valve Requirements
The component relies on TRVs having these attributes:
- **Opening Percentage**: Must report current valve opening (0-100%)
- **Temperature Reading**: Current room temperature
- **Target Temperature**: User-set target temperature
- **(Optional) Temperature Offset**: Adjustable offset (-5 to +5°C) for fine-tuning

Example supported TRVs:
- TUYA Zigbee TRV with opening percentage
- Aqara E1 with opening percentage
- Schneider Electric Wiser TRV
- Other Zigbee TRVs that expose opening percentage

### Boiler Control
The component commands a boiler via:
- **OpenTherm Integration** (ESPHome)
- Sends flow temperature setpoint (5°C to 80°C)
- Default entity: `number.opentherm_flow_temp`
- Can be customized in component code

## Zone Priority Logic (In Detail)

### High Priority Zones (priority > 0.5)

These zones represent critical areas (living rooms, main bedrooms) that should always be comfortable.

**Trigger Threshold**: 25% TRV opening

- A single high-priority zone at 25% opening can trigger the boiler independently
- **Example**: Living room TRV opens to 30% (needs heat) → boiler turns ON
- **Example**: Hallway TRV opens to 50% but is low priority → insufficient alone to turn boiler on

**Demand Calculation**:
```
demand = (temperature_error / 10) × (opening / 100)
- Temperature error: How far below target (0-10°C normalized to 0-1)
- Opening percentage: How much valve is open (0-100)
```

### Low Priority Zones (priority ≤ 0.5)

These zones represent less critical areas (guest rooms, storage, hallways) that can share boiler time.

**Trigger Threshold**: 100% TRV opening OR aggregate with other low-priority zones

- Cannot trigger boiler alone unless fully open (100%)
- **Example**: Hallway at 50% + Guest room at 50% = 100% aggregate → boiler ON
- **Example**: Single low-priority zone at 50% → boiler stays OFF
- Multiple low-priority zones aggregate their opening percentages

**Aggregation Logic**:
```
if SUM(low_priority_openings) >= 100%:
    boiler_on = True
    use_highest_demand_metric
```

## Boiler Intensity Calculation

The boiler intensity is determined by the **highest demand metric** among all demanding zones:

```
demand_metric = (temperature_error / 10) × (opening / 100)
            = (normalized_error) × (normalized_opening)

flow_temperature = 5°C + (demand_metric × 75°C)

Examples:
- demand=0.0  → flow_temp = 5°C   (OFF)
- demand=0.2  → flow_temp = 20°C  (Low heat)
- demand=0.5  → flow_temp = 42.5°C (Medium)
- demand=0.8  → flow_temp = 65°C  (High)
- demand=1.0  → flow_temp = 80°C  (Maximum)
```

This ensures:
- The boiler provides just enough heat for the neediest zone
- No wasted energy on oversizing
- All zones eventually reach comfort temperature

## Monitoring & Diagnostics

Each zone exports complete state information:

```python
zone_state = {
    "current_temperature": 18.5,
    "target_temperature": 21.0,
    "temperature_error": 2.5,
    "priority": 1.0,
    "is_high_priority": True,
    "is_demanding_heat": True,
    "demand_metric": 0.186,
    "trv_opening_percent": 75.0,
    "temperature_offset": -1.5,
    "external_sensor_temperature": 18.2,
}
```

Monitor via:
- **Home Assistant Developer Tools**: Call `multi_trv_heating.get_zone_state`
- **Component logs**: Set logging to DEBUG for detailed decision trace
- **Custom template entities**: Create templates for monitoring zones

## Logging Levels

The component supports three logging levels:

- **WARNING**: Only critical issues (errors, unsafe conditions)
- **INFO**: Normal operation, state changes, boiler commands (default)
- **DEBUG**: Detailed demand calculations, zone updates, decision logic

Configure logging in `configuration.yaml`:
```yaml
logger:
  logs:
    don_controller: debug
```

## Future Enhancements

Possible extensions for future versions:

1. **Outdoor Temperature Compensation**: Adjust target temperatures based on weather
2. **Time-Based Scheduling**: Lower priorities during off-peak hours
3. **Machine Learning**: Learn optimal flow temperatures for different demand patterns
4. **Zone Influence**: Model thermal influence between adjacent zones
5. **Radiator Balancing**: Adjust valve targets to balance flow between zones
6. **Predictive Control**: Anticipate demand changes based on time and patterns
7. **Cost Optimization**: Prefer cheaper fuel/sources when available

## Troubleshooting

### Boiler not turning on
- Check that at least one zone has demand >= 25% (high priority) or 100% (low priority)
- Verify flow temperature entity exists and is controllable
- Check logs for priority assignments

### Boiler cycling too often
- Increase hysteresis: Add deadbands to temperature targets
- Reduce logging to INFO level to avoid overhead
- Ensure TRV readings are stable (check for rapid fluctuations)

### Zones not reaching target temperature
- Check boiler flow temperature settings (may be capped)
- Verify radiator valves are not physically stuck
- Check for high thermal losses in insulation

### Incorrect zone state readings
- Verify climate entities are updating properly in Home Assistant
- Check TRV mounting location (near radiator can cause inaccurate readings)
- Use external temperature sensors for unreliable zones

## Development & Testing

See `/tests` directory for comprehensive unit test suite with:
- Basic single-zone demand tests
- Multi-zone aggregation tests
- Priority logic validation
- Boiler command verification
- Corner cases and edge conditions

Run tests with logging:
```bash
python -m pytest tests/ -v --log-cli-level=INFO
```

## License

MIT License - See LICENSE file for details

## Support

For issues, feature requests, or discussions:
- GitHub Issues: https://github.com/angelinid/MultiTRVHeating/issues
- GitHub Discussions: https://github.com/angelinid/MultiTRVHeating/discussions
