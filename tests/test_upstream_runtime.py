"""Optional real-HA API tests; upstream code stays outside this repository.

Stage the locked archive with scripts/stage_integration.py, install
requirements-runtime.txt in a Python 3.14 venv, then run this suite with
ASTERISK_UPSTREAM_ROOT=/absolute/path/to/stage. The ordinary stdlib suite skips
these tests. AMI is a deterministic in-process fake; no PBX/HA network is used.
"""
import asyncio
import copy
import importlib.util
import json
import os
from pathlib import Path
import sys
import tempfile
from types import MappingProxyType, SimpleNamespace
import unittest
from unittest.mock import AsyncMock, patch

UPSTREAM = os.environ.get("ASTERISK_UPSTREAM_ROOT")
RUNTIME_READY = bool(
    UPSTREAM
    and (Path(UPSTREAM) / "custom_components/asterisk/__init__.py").is_file()
    and importlib.util.find_spec("homeassistant")
)


@unittest.skipUnless(RUNTIME_READY, "requires optional HA runtime and ASTERISK_UPSTREAM_ROOT")
class UpstreamRuntimeTests(unittest.IsolatedAsyncioTestCase):
    async def asyncSetUp(self):
        sys.path.insert(0, str(Path(UPSTREAM).resolve()))
        from homeassistant.core import HomeAssistant
        from homeassistant.config_entries import ConfigEntries, ConfigEntry
        from custom_components.asterisk import binary_sensor, config_flow, sensor
        import custom_components.asterisk as integration
        from asterisk.ami import Event

        self.integration = integration
        self.config_flow = config_flow
        self.binary_sensor = binary_sensor
        self.sensor = sensor
        self.temp = tempfile.TemporaryDirectory()
        self.hass = HomeAssistant(self.temp.name)
        self.hass.config_entries = ConfigEntries(self.hass, {})
        self.entry = ConfigEntry(
            domain="asterisk", title="Synthetic AMI", unique_id="127.0.0.1:5038",
            version=1, minor_version=1, source="user", options={},
            discovery_keys=MappingProxyType({}), subentries_data=[],
            data={"host": "127.0.0.1", "port": 5038,
                  "username": "synthetic", "password": "synthetic-only"},
        )

        class FakeAMI:
            """Models responses and event filters used by the unmodified component."""
            instances = []

            def __init__(self, *args, **kwargs):
                self.listeners = []
                self.actions = []
                self.instances.append(self)

            def login(self, **kwargs):
                return self.response()

            @staticmethod
            def response(error=False):
                return SimpleNamespace(response=SimpleNamespace(
                    is_error=lambda: error,
                    keys={"AsteriskVersion": "22.11.0", "Message": "synthetic"},
                ))

            def add_event_listener(self, callback, white_list, **filters):
                self.listeners.append((callback, white_list, filters))

            def emit(self, name, **fields):
                for callback, names, filters in list(self.listeners):
                    if name in names and all(fields.get(k) == v for k, v in filters.items()):
                        callback(Event(name, fields))

            def send_action(self, action):
                self.actions.append(action)
                if action.name == "SIPpeers":
                    return self.response(error=True)  # Asterisk 22 has no chan_sip.
                if action.name == "PJSIPShowEndpoints":
                    self.emit("EndpointList", ObjectName="201", DeviceState="Not in use")
                    self.emit("EndpointListComplete")
                return self.response()

            def logoff(self):
                pass

            def disconnect(self):
                pass

        self.fake = FakeAMI
        self.patches = [
            patch.object(integration, "AMIClient", FakeAMI),
            patch.object(config_flow, "AMIClient", FakeAMI),
            patch.object(integration, "AutoReconnect", lambda *a, **k: SimpleNamespace()),
            patch.object(self.hass.config_entries, "async_forward_entry_setups", new=AsyncMock()),
        ]
        for item in self.patches:
            item.start()

    async def asyncTearDown(self):
        await self.hass.async_stop()
        for item in reversed(self.patches):
            item.stop()
        self.temp.cleanup()
        sys.path.remove(str(Path(UPSTREAM).resolve()))

    async def test_config_flow_form_serializes_with_current_ha_and_auth_creates_entry(self):
        import probatio
        from homeassistant.helpers import config_validation as cv
        flow = self.config_flow.AsteriskConfigFlow()
        flow.hass = self.hass
        flow.context = {"source": "user"}  # Normally set by HA's FlowManager.
        result = await flow.async_step_user()
        fields = probatio.to_field_list(result["data_schema"], custom_serializer=cv.custom_serializer)
        self.assertEqual({f["name"] for f in fields}, {"host", "port", "username", "password"})
        result = await flow.async_step_user(dict(self.entry.data))
        self.assertEqual(result["type"], "create_entry")
        self.assertEqual(result["data"]["username"], "synthetic")

    async def test_pjsip_only_setup_and_real_ha_service_registry(self):
        self.assertTrue(await self.integration.async_setup_entry(self.hass, self.entry))
        await asyncio.sleep(0)
        await asyncio.sleep(0)
        self.hass.config_entries.async_forward_entry_setups.assert_awaited_once_with(
            self.entry, ["binary_sensor", "sensor"]
        )
        client = self.hass.data["asterisk"][self.entry.entry_id]["client"]
        self.assertEqual([a.name for a in client.actions], ["SIPpeers", "PJSIPShowEndpoints"])
        self.assertTrue(self.hass.services.has_service("asterisk", "send_action"))
        for name, parameters in [
            ("Originate", {"Channel": "PJSIP/201", "Context": "home", "Exten": "630",
                           "Priority": "1", "CallerID": "HA test <201>", "Async": "true"}),
            ("Hangup", {"Channel": "PJSIP/201-00000001"}),
        ]:
            await self.hass.services.async_call(
                "asterisk", "send_action", {"action": name, "parameters": parameters}, blocking=True
            )
            self.assertEqual(client.actions[-1].name, name)
            self.assertEqual(client.actions[-1].keys, parameters)

    async def test_actual_sensor_classes_process_filtered_ami_events(self):
        await self.integration.async_setup_entry(self.hass, self.entry)
        await asyncio.sleep(0)
        await asyncio.sleep(0)
        client = self.hass.data["asterisk"][self.entry.entry_id]["client"]
        entities = []
        await self.binary_sensor.async_setup_entry(self.hass, self.entry, lambda added, *a: entities.extend(added))
        await self.sensor.async_setup_entry(self.hass, self.entry, lambda added, *a: entities.extend(added))
        self.assertEqual(len(entities), 6)
        self.assertEqual(client.actions[-1].name, "CoreSettings")
        for entity in entities:
            entity.schedule_update_ha_state = lambda *a, **k: None  # No entity platform is bootstrapped.
        registered = next(e for e in entities if isinstance(e, self.binary_sensor.RegisteredSensor))
        state = next(e for e in entities if isinstance(e, self.sensor.DeviceStateSensor))
        dtmf = next(e for e in entities if isinstance(e, self.sensor.DTMFReceivedSensor))
        client.emit("DeviceStateChange", Device="PJSIP/201", State="UNAVAILABLE")
        self.assertFalse(registered.is_on)
        self.assertEqual(state.state, "Unavailable")
        client.emit("DeviceStateChange", Device="PJSIP/201", State="RINGING")
        self.assertTrue(registered.is_on)
        self.assertEqual(state.state, "Ringing")
        for digit in ("1", "1", "#"):
            old_timestamp = dtmf.state
            client.emit("DTMFBegin", ConnectedLineNum="201", Direction="Received",
                        Channel="PJSIP/202-00000002", Digit=digit,
                        ConnectedLineName="Synthetic", Context="home")
            self.assertEqual(dtmf.extra_state_attributes["Digit"], digit)
            self.assertNotEqual(dtmf.state, old_timestamp)
        last_timestamp = dtmf.state
        client.emit("DTMFBegin", ConnectedLineNum="202", Direction="Received")
        self.assertEqual(dtmf.state, last_timestamp)

    async def test_generated_yaml_passes_actual_ha_script_and_automation_schemas(self):
        await self._assert_generated_yaml_schemas('site.example.json')

    async def test_answered_call_hangup_uses_device_state_when_connected_line_stays_ring(self):
        from homeassistant.helpers.template import Template
        from test_render import config, render

        await self.integration.async_setup_entry(self.hass, self.entry)
        await asyncio.sleep(0)
        await asyncio.sleep(0)
        client = self.hass.data['asterisk'][self.entry.entry_id]['client']
        entities = []
        await self.sensor.async_setup_entry(self.hass, self.entry, lambda added, *a: entities.extend(added))
        for entity in entities:
            entity.schedule_update_ha_state = lambda *a, **k: None
        connected = next(e for e in entities if isinstance(e, self.sensor.ConnectedLineSensor))
        state = next(e for e in entities if isinstance(e, self.sensor.DeviceStateSensor))
        channel = 'PJSIP/201-00000005'
        client.emit('NewConnectedLine', CallerIDNum='201', ConnectedLineNum='630',
                    CallerIDName='Synthetic phone', ConnectedLineName='Synthetic Assist',
                    Channel=channel, ChannelState='4', ChannelStateDesc='Ring', Exten='630', Context='home')
        client.emit('Newstate', CallerIDNum='201', Channel=channel,
                    ChannelState='6', ChannelStateDesc='Up')
        client.emit('DeviceStateChange', Device='PJSIP/201', State='INUSE')
        self.assertEqual(connected.extra_state_attributes['ChannelStateDesc'], 'Ring')
        self.assertEqual(state.state, 'In use')
        self.hass.states.async_set('sensor.example_201_connected_line', connected.state, connected.extra_state_attributes)
        self.hass.states.async_set('sensor.example_201_state', state.state)
        sequence = render.build(config('site.single-phone.example.json'))['asterisk.yaml']['script']['asterisk_hangup_201']['sequence']
        guard = Template(sequence[1]['value_template'], self.hass)
        self.assertTrue(guard.async_render({'channel': channel}))
        client.emit('DeviceStateChange', Device='PJSIP/201', State='NOT_INUSE')
        self.hass.states.async_set('sensor.example_201_state', state.state)
        self.assertFalse(guard.async_render({'channel': channel}))

    async def test_single_phone_yaml_passes_actual_ha_script_and_automation_schemas(self):
        await self._assert_generated_yaml_schemas('site.single-phone.example.json')

    async def test_per_phone_assist_origins_execute_for_the_selected_endpoint(self):
        from homeassistant.core import Context
        from homeassistant.components.script.config import async_validate_config_item
        from homeassistant.helpers.script import Script
        from test_render import config, render

        await self.integration.async_setup_entry(self.hass, self.entry)
        await asyncio.sleep(0)
        await asyncio.sleep(0)
        client = self.hass.data['asterisk'][self.entry.entry_id]['client']
        value = config('site.single-phone.example.json')
        for extension in ('203', '205'):
            phone = copy.deepcopy(value['phones'][0])
            phone.update(extension=extension, name='Synthetic ' + extension, assist_origin=True)
            phone['entities'] = {key: entity.replace('201', extension) for key, entity in phone['entities'].items()}
            value['phones'].append(phone)
        generated = render.build(value)['asterisk.yaml']['script']
        for extension, name in (('201', 'asterisk_call_assist'), ('203', 'asterisk_call_assist_203'),
                                ('205', 'asterisk_call_assist_205')):
            with self.subTest(extension=extension):
                checked = await async_validate_config_item(self.hass, name, generated[name])
                before = len(client.actions)
                await Script(self.hass, checked['sequence'], name, 'script').async_run(context=Context())
                self.assertEqual(len(client.actions), before + 1)
                self.assertEqual(client.actions[-1].name, 'Originate')
                self.assertEqual(client.actions[-1].keys, {
                    'Channel': 'PJSIP/' + extension, 'Context': 'home', 'Exten': '630', 'Priority': '1',
                    'CallerID': 'Home Assistant <' + extension + '>', 'Timeout': '20000', 'Async': 'true'})

    async def test_echo_none_connected_line_hangup_runs_only_for_active_own_channel(self):
        from homeassistant.core import Context
        from homeassistant.components.script.config import async_validate_config_item
        from homeassistant.helpers.script import Script
        from test_render import config, render

        await self.integration.async_setup_entry(self.hass, self.entry)
        await asyncio.sleep(0)
        await asyncio.sleep(0)
        client = self.hass.data['asterisk'][self.entry.entry_id]['client']
        device = {'extension': '202', 'tech': 'PJSIP', 'status': 'Not in use'}
        connected = self.sensor.ConnectedLineSensor(self.hass, self.entry, device)
        state = self.sensor.DeviceStateSensor(self.hass, self.entry, device)
        for entity in (connected, state):
            entity.schedule_update_ha_state = lambda *a, **k: None
        channel = 'PJSIP/202-00000000'
        client.emit('Newchannel', CallerIDNum='202', ConnectedLineNum='<unknown>',
                    CallerIDName='Synthetic phone', ConnectedLineName='<unknown>',
                    Channel=channel, ChannelState='4', ChannelStateDesc='Ring',
                    Exten='600', Context='home')
        client.emit('Newstate', CallerIDNum='202', Channel=channel,
                    ChannelState='6', ChannelStateDesc='Up')
        client.emit('DeviceStateChange', Device='PJSIP/202', State='INUSE')
        self.assertEqual(connected.state, 'None')
        self.assertEqual(connected.extra_state_attributes['ConnectedLineNum'], '<unknown>')
        self.assertEqual(connected.extra_state_attributes['Channel'], channel)
        self.assertEqual(state.state, 'In use')
        self.hass.states.async_set('sensor.example_202_connected_line', connected.state,
                                   connected.extra_state_attributes)
        self.hass.states.async_set('sensor.example_202_state', state.state)
        generated = render.build(config())['asterisk.yaml']['script']['asterisk_hangup_202']
        checked = await async_validate_config_item(self.hass, 'asterisk_hangup_202', generated)
        script = Script(self.hass, checked['sequence'], 'Synthetic echo hangup', 'script')
        await script.async_run(context=Context())
        self.assertEqual(client.actions[-1].name, 'Hangup')
        self.assertEqual(client.actions[-1].keys, {'Channel': channel})
        count = len(client.actions)
        for line, device_state, current_channel in (
                ('None', 'Not in use', channel),
                ('None', 'Ringing', channel),
                ('None', 'In use', 'PJSIP/201-00000000'),
                ('None', 'In use', ''),
                ('unknown', 'In use', channel),
                ('unavailable', 'In use', channel),
                ('', 'In use', channel)):
            with self.subTest(line=line, device_state=device_state, channel=current_channel):
                self.hass.states.async_set('sensor.example_202_connected_line', line,
                                           {'Channel': current_channel})
                self.hass.states.async_set('sensor.example_202_state', device_state)
                await script.async_run(context=Context())
                self.assertEqual(len(client.actions), count)

    async def _assert_generated_yaml_schemas(self, example):
        import yaml
        from homeassistant.components.automation.config import PLATFORM_SCHEMA
        from homeassistant.components.homeassistant.triggers.state import TRIGGER_STATE_SCHEMA
        from homeassistant.components.script.config import async_validate_config_item
        from homeassistant.helpers.script import async_validate_actions_config

        root = Path(__file__).resolve().parents[1]
        spec = importlib.util.spec_from_file_location("runtime_renderer", root / "scripts/render.py")
        renderer = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(renderer)
        configuration = json.loads((root / "examples" / example).read_text())
        for phone in configuration["phones"]:
            for key in phone["entities"]:
                domain = "binary_sensor" if key == "registered" else "sensor"
                phone["entities"][key] = f"{domain}.synthetic_{phone['extension']}_{key}"
        documents = renderer.build(configuration)
        package = yaml.safe_load(renderer.yaml_text(documents["asterisk.yaml"]))
        dtmf_config = configuration["dtmf"]
        dtmf_phone = next(p for p in configuration["phones"] if p["extension"] == dtmf_config["phone_extension"])
        expected_dtmf = dtmf_phone["entities"]["dtmf_" + dtmf_config["sensor"]]
        for name, script in package["script"].items():
            with self.subTest(script=name):
                checked = await async_validate_config_item(self.hass, name, script)
                self.assertIsNotNone(checked)
                self.assertIn("sequence", checked)
        for automation in package["automation"]:
            checked = PLATFORM_SCHEMA(automation)
            for trigger in checked["triggers"]:
                self.assertEqual(TRIGGER_STATE_SCHEMA(trigger)["entity_id"], [expected_dtmf])
            actions = await async_validate_actions_config(self.hass, checked["actions"])
            self.assertEqual(len(actions), 1)


if __name__ == "__main__":
    unittest.main()
