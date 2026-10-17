import importlib.util
import json
from pathlib import Path
import stat
import tempfile
import unittest

ROOT = Path(__file__).resolve().parents[1]
spec = importlib.util.spec_from_file_location('render', ROOT / 'scripts/render.py')
render = importlib.util.module_from_spec(spec); spec.loader.exec_module(render)

def config():
    value = json.loads((ROOT / 'examples/site.example.json').read_text())
    for phone in value['phones']:
        for key in phone['entities']:
            domain = 'binary_sensor' if key == 'registered' else 'sensor'
            phone['entities'][key] = f'{domain}.example_{phone["extension"]}_{key}'
    return value

class RendererTests(unittest.TestCase):
    def test_originate_rings_source_then_dials_destination(self):
        output = render.build(config())
        action = output['asterisk.yaml']['script']['asterisk_call_pair']['sequence'][0]
        self.assertEqual(action['action'], 'asterisk.send_action')
        self.assertEqual(action['data']['action'], 'Originate')
        self.assertEqual(action['data']['parameters']['Channel'], 'PJSIP/201')
        self.assertEqual(action['data']['parameters']['Exten'], '202')
        self.assertEqual(action['data']['parameters']['Context'], 'home')
        self.assertEqual(action['data']['parameters']['CallerID'], 'Home Assistant <201>')
        self.assertEqual(action['data']['parameters']['Async'], 'true')
    def test_hangup_uses_live_channel_and_refuses_idle(self):
        script = render.build(config())['asterisk.yaml']['script']['asterisk_hangup_201']['sequence']
        self.assertIn("state_attr('sensor.example_201_connected_line', 'Channel')", script[0]['variables']['channel'])
        self.assertIn("channel.startswith('PJSIP/201-')", script[1]['value_template'])
        self.assertIn("['None', 'unknown', 'unavailable', '']", script[1]['value_template'])
        self.assertEqual(script[2]['data']['parameters']['Channel'], '{{ channel }}')
    def test_safe_dtmf_automation_default_disabled(self):
        automation = render.build(config())['asterisk.yaml']['automation'][0]
        self.assertFalse(automation['initial_state'])
        self.assertEqual(automation['triggers'][0]['trigger'], 'state')
        self.assertEqual(automation['actions'][0]['action'], 'persistent_notification.create')
        self.assertIn("get('Digit') == '5'", automation['conditions'][0]['value_template'])
    def test_dashboard_requires_confirmation_for_each_action(self):
        cards = render.build(config())['asterisk_dashboard.yaml']['views'][0]['cards'][-1]['cards']
        self.assertTrue(all(card['tap_action']['confirmation']['text'] for card in cards))
    def test_privacy_excludes_call_and_dtmf_history(self):
        excluded = render.build(config())['asterisk_privacy.yaml']['recorder']['exclude']['entities']
        self.assertEqual(len(excluded), 6)
        self.assertIn('sensor.example_201_connected_line', excluded)
    def test_placeholders_rejected(self):
        with self.assertRaises(ValueError):
            render.build(json.loads((ROOT / 'examples/site.example.json').read_text()))
    def test_duplicate_entity_rejected(self):
        value = config(); value['phones'][1]['entities']['state'] = value['phones'][0]['entities']['state']
        with self.assertRaises(ValueError): render.build(value)
    def test_duplicate_extension_rejected(self):
        value = config(); value['phones'][1]['extension'] = '201'
        with self.assertRaises(ValueError): render.build(value)
    def test_context_and_entity_injection_rejected(self):
        for change in ('context', 'entity'):
            value = config()
            if change == 'context': value['dialplan_context'] = "home\nwrite=all"
            else: value['phones'][0]['entities']['state'] = "sensor.foo'}}"
            with self.assertRaises(ValueError): render.build(value)
    def test_unknown_secret_field_rejected(self):
        value = config(); value['ami_password'] = 'must-never-serialize'
        with self.assertRaises(ValueError): render.build(value)
    def test_output_permissions_and_no_overwrite(self):
        with tempfile.TemporaryDirectory() as tmp:
            render.write_outputs(config(), Path(tmp).resolve()/'stage')
            self.assertEqual(stat.S_IMODE((Path(tmp)/'stage/asterisk.yaml').stat().st_mode), 0o600)
            with self.assertRaises(ValueError): render.write_outputs(config(), Path(tmp).resolve()/'stage')
    def test_symlink_output_rejected(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp).resolve(); (root/'real').mkdir(); (root/'link').symlink_to(root/'real', target_is_directory=True)
            with self.assertRaises(ValueError): render.write_outputs(config(), root/'link')
            self.assertFalse((root/'real/asterisk.yaml').exists())
    def test_quote_encoding(self):
        value = config(); value['phones'][0]['name'] = 'Phone: "Example"'
        text = render.yaml_text(render.build(value)['asterisk_dashboard.yaml'])
        self.assertIn('Phone: \\"Example\\"', text)

if __name__ == '__main__': unittest.main()
