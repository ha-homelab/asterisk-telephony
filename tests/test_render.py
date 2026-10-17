import importlib.util
import json
from pathlib import Path
import stat
import tempfile
import unittest

ROOT = Path(__file__).resolve().parents[1]
spec = importlib.util.spec_from_file_location('render', ROOT / 'scripts/render.py')
render = importlib.util.module_from_spec(spec); spec.loader.exec_module(render)

def config(example='site.example.json'):
    value = json.loads((ROOT / 'examples' / example).read_text())
    for phone in value['phones']:
        for key in phone['entities']:
            domain = 'binary_sensor' if key == 'registered' else 'sensor'
            phone['entities'][key] = f'{domain}.example_{phone["extension"]}_{key}'
    return value

class RendererTests(unittest.TestCase):
    def test_single_phone_keeps_assist_hangup_privacy_and_disabled_dtmf(self):
        output = render.build(config('site.single-phone.example.json'))
        package = output['asterisk.yaml']
        self.assertEqual(set(package['script']), {'asterisk_call_assist', 'asterisk_hangup_201'})
        params = package['script']['asterisk_call_assist']['sequence'][0]['data']['parameters']
        self.assertEqual(params['Channel'], 'PJSIP/201')
        self.assertEqual(params['Exten'], '630')
        self.assertEqual(params['Context'], 'home')
        dashboard = output['asterisk_dashboard.yaml']['views'][0]['cards']
        self.assertEqual(len(dashboard[1]['entities']), 5)
        self.assertEqual([card['tap_action']['perform_action'] for card in dashboard[-1]['cards']],
                         ['script.asterisk_call_assist', 'script.asterisk_hangup_201'])
        self.assertTrue(all(card['tap_action']['confirmation']['text'] for card in dashboard[-1]['cards']))
        self.assertFalse(package['automation'][0]['initial_state'])
        self.assertEqual(package['automation'][0]['triggers'][0]['entity_id'], 'sensor.example_201_dtmf_received')
        self.assertEqual(set(output['asterisk_privacy.yaml']['recorder']['exclude']['entities']),
                         {'sensor.example_201_connected_line', 'sensor.example_201_dtmf_sent', 'sensor.example_201_dtmf_received'})
        self.assertNotIn('202', json.dumps(output))
    def test_null_call_to_can_disable_pair_control_without_removing_mapped_phones(self):
        value = config(); value['call_to'] = None
        scripts = render.build(value)['asterisk.yaml']['script']
        self.assertEqual(set(scripts), {'asterisk_call_assist', 'asterisk_hangup_201', 'asterisk_hangup_202'})
    def test_single_phone_requires_explicit_null_instead_of_unmapped_destination(self):
        for target in ('202', '201', '', 'null', False, 0, [], {}):
            with self.subTest(target=target):
                value = config('site.single-phone.example.json'); value['call_to'] = target
                with self.assertRaises(ValueError): render.build(value)
        value = config('site.single-phone.example.json'); del value['call_to']
        with self.assertRaises(ValueError): render.build(value)
    def test_single_phone_still_requires_real_source_mapping_and_distinct_assist(self):
        for field, value in [('call_from', '202'), ('call_from', None), ('assist_extension', '201'), ('assist_extension', None)]:
            with self.subTest(field=field, value=value):
                data = config('site.single-phone.example.json'); data[field] = value
                with self.assertRaises(ValueError): render.build(data)
        data = config('site.single-phone.example.json'); data['phones'] = []
        with self.assertRaises(ValueError): render.build(data)
    def test_single_phone_requires_all_five_actual_entity_ids(self):
        for key in render.ENTITY_KEYS:
            with self.subTest(missing_key=key):
                value = config('site.single-phone.example.json'); del value['phones'][0]['entities'][key]
                with self.assertRaises(ValueError): render.build(value)
        with self.assertRaises(ValueError):
            render.build(json.loads((ROOT / 'examples/site.single-phone.example.json').read_text()))
    def test_pair_call_still_requires_two_distinct_mapped_endpoints(self):
        for field, value in [('call_to', '201'), ('call_to', '203'), ('assist_extension', '202')]:
            with self.subTest(field=field, value=value):
                data = config(); data[field] = value
                with self.assertRaises(ValueError): render.build(data)
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
