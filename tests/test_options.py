"""Zone options set from the HA UI (external temperature sensor) win over the setup data."""

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent.parent / 'custom_components' / 'multi_trv_heating'))

from options import OPT_EXT_TEMP, zones_with_options

ZONES = [
    {'entity_id': 'climate.a', 'name': 'A', 'ext_temp_entity_id': 'sensor.old'},
    {'entity_id': 'climate.b', 'name': 'B'},
    {'entity_id': 'climate.c', 'name': 'C', 'ext_temp_entity_id': 'sensor.keep'},
]

failed = 0


def check(ok, text):
    global failed
    print(f"  {'PASS' if ok else 'FAIL'}: {text}")
    failed += not ok


no_options = zones_with_options(ZONES, {})
check(no_options == ZONES, "no options: the setup data is used as it is")

changed = zones_with_options(ZONES, {OPT_EXT_TEMP: {
    'climate.a': 'sensor.new', 'climate.b': 'sensor.added', 'climate.c': '',
}})
check(changed[0]['ext_temp_entity_id'] == 'sensor.new', "a sensor set at setup can be changed")
check(changed[1]['ext_temp_entity_id'] == 'sensor.added', "a zone without a sensor can get one")
check(changed[2]['ext_temp_entity_id'] is None, "an emptied field removes the setup sensor")

partial = zones_with_options(ZONES, {OPT_EXT_TEMP: {'climate.b': 'sensor.x'}})
check(partial[0]['ext_temp_entity_id'] == 'sensor.old' and partial[2]['ext_temp_entity_id'] == 'sensor.keep',
      "zones missing from the options keep the setup value")
check(ZONES[0]['ext_temp_entity_id'] == 'sensor.old', "the setup data itself is never modified")

sys.exit(1 if failed else 0)
