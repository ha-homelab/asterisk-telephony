"""Exercise the emitted YAML and Jinja guards, not only string snapshots."""
import unittest
from types import SimpleNamespace
from test_render import render, config
try:
    import yaml
    from jinja2 import Environment
except ImportError:
    yaml = None
    Environment = None

@unittest.skipIf(yaml is None or Environment is None, 'Install requirements-test.txt for YAML/Jinja validation')
class TemplateTests(unittest.TestCase):
    def test_all_output_yaml_roundtrips_without_type_changes(self):
        for example in ('site.example.json', 'site.single-phone.example.json'):
            for name, document in render.build(config(example)).items():
                with self.subTest(example=example, file=name):
                    self.assertEqual(yaml.safe_load(render.yaml_text(document)), document)
    def test_hangup_only_for_active_own_channel(self):
        seq = render.build(config())['asterisk.yaml']['script']['asterisk_hangup_201']['sequence']
        for channel, line, desc, expected in [
            ('PJSIP/201-000000aa', '202', 'Up', True),
            ('PJSIP/202-000000aa', '202', 'Up', False),
            ('PJSIP/201-000000aa', 'None', 'Up', False),
            ('PJSIP/201-000000aa', '202', 'Ringing', False),
            ('', '202', 'Up', False),
        ]:
            with self.subTest(channel=channel, line=line, desc=desc):
                env=Environment()
                env.globals['states']=lambda e: line
                env.globals['state_attr']=lambda e,k: channel if k=='Channel' else desc
                self.assertEqual(env.from_string(seq[1]['value_template']).render(channel=channel), str(expected))
    def test_dtmf_guard_rejects_startup_duplicates_other_digits_and_contexts(self):
        condition = render.build(config())['asterisk.yaml']['automation'][0]['conditions'][0]['value_template']
        state=lambda stamp,digit='5',context='home':SimpleNamespace(state=stamp,attributes={'Digit':digit,'Context':context})
        cases=[(state('t1'),state('t2'),True),(None,state('t2'),False),(state('t1'),state('t1'),False),
               (state('t1'),state('t2','6'),False),(state('t1'),state('t2','5','other'),False),
               (state('t1'),state('unavailable'),False)]
        env=Environment()
        for old,new,expected in cases:
            with self.subTest(old=old,new=new):
                result=env.from_string(condition).render(trigger=SimpleNamespace(from_state=old,to_state=new))
                self.assertEqual(result,str(expected))

if __name__=='__main__':unittest.main()
