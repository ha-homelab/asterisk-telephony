#!/usr/bin/env python3
"""Render local Home Assistant packages from explicitly mapped entity IDs."""
import argparse
import json
import os
from pathlib import Path
import re

ENTITY_KEYS = {'registered', 'state', 'connected_line', 'dtmf_sent', 'dtmf_received'}
ROOT_KEYS = {'schema_version', 'dialplan_context', 'call_from', 'call_to', 'assist_extension', 'dtmf', 'phones'}

def require(condition, message):
    if not condition:
        raise ValueError(message)

def validate(config):
    require(isinstance(config, dict) and set(config) == ROOT_KEYS, 'Input keys do not match examples/site.example.json')
    require(type(config['schema_version']) is int and config['schema_version'] == 1, 'schema_version must be 1')
    require(isinstance(config['dialplan_context'], str) and re.fullmatch(r'[A-Za-z0-9_-]{1,64}', config['dialplan_context']), 'Invalid dialplan_context')
    for k in ('call_from', 'call_to', 'assist_extension'):
        require(isinstance(config[k], str) and re.fullmatch(r'[2-9][0-9]{2,5}', config[k]), 'Extensions must be strings of 3–6 digits, starting 2–9')
    require(len({config['call_from'], config['call_to'], config['assist_extension']}) == 3, 'Call endpoints and Assist number must be different')
    phones = config['phones']
    require(isinstance(phones, list) and 2 <= len(phones) <= 50, 'Provide 2–50 phones')
    extensions, entities = set(), set()
    for phone in phones:
        require(isinstance(phone, dict) and set(phone) == {'extension', 'name', 'entities'}, 'Invalid phone keys')
        ext = phone['extension']
        require(isinstance(ext, str) and re.fullmatch(r'[2-9][0-9]{2,5}', ext) and ext not in extensions, 'Invalid or duplicate phone extension')
        extensions.add(ext)
        name = phone['name']
        require(isinstance(name, str) and 1 <= len(name) <= 60 and all(ord(c) >= 32 for c in name), 'Invalid phone name')
        mapping = phone['entities']
        require(isinstance(mapping, dict) and set(mapping) == ENTITY_KEYS, 'Map all five actual entities for each phone')
        for key, entity in mapping.items():
            domain = 'binary_sensor' if key == 'registered' else 'sensor'
            require(isinstance(entity, str) and re.fullmatch(domain + r'\.[a-z0-9_]+', entity), 'Replace every entity ID from the actual HA entity registry')
            require('replace' not in entity.lower() and entity not in entities, 'Placeholder or duplicate entity ID')
            entities.add(entity)
    require(config['call_from'] in extensions and config['call_to'] in extensions, 'Call endpoints must have phone mappings')
    require(config['assist_extension'] not in extensions, 'Assist number collides with a phone')
    dtmf = config['dtmf']
    require(isinstance(dtmf, dict) and set(dtmf) == {'phone_extension', 'sensor', 'digit', 'enabled'}, 'Invalid dtmf keys')
    require(isinstance(dtmf['phone_extension'], str) and dtmf['phone_extension'] in extensions and dtmf['sensor'] in ('sent', 'received'), 'Invalid DTMF source')
    require(isinstance(dtmf['digit'], str) and dtmf['digit'] in '0123456789*#ABCD' and len(dtmf['digit']) == 1, 'Invalid DTMF digit')
    require(type(dtmf['enabled']) is bool, 'dtmf.enabled must be boolean')
    return config

def originate(config, source, target):
    return {'action': 'asterisk.send_action', 'data': {'action': 'Originate', 'parameters': {
        'Channel': f'PJSIP/{source}', 'Context': config['dialplan_context'], 'Exten': target,
        'Priority': '1', 'CallerID': f'Home Assistant <{source}>', 'Timeout': '20000', 'Async': 'true'}}}

def build(config):
    validate(config)
    phones = {p['extension']: p for p in config['phones']}
    src, dst = config['call_from'], config['call_to']
    scripts = {
        'asterisk_call_pair': {'alias': f'Asterisk: call {src}, then {dst}', 'mode': 'single', 'sequence': [originate(config, src, dst)]},
        'asterisk_call_assist': {'alias': f'Asterisk: call {src}, then Assist {config["assist_extension"]}', 'mode': 'single', 'sequence': [originate(config, src, config['assist_extension'])]},
    }
    rows = []
    private_entities = []
    for ext, phone in phones.items():
        ent = phone['entities']; connected = ent['connected_line']
        scripts[f'asterisk_hangup_{ext}'] = {
            'alias': f'Asterisk: end connected call on {ext}', 'mode': 'single',
            'sequence': [
                {'variables': {'channel': "{{ state_attr('" + connected + "', 'Channel') | default('', true) }}"}},
                {'condition': 'template', 'value_template': "{{ channel is string and channel.startswith('PJSIP/" + ext + "-') and states('" + connected + "') not in ['None', 'unknown', 'unavailable', ''] and state_attr('" + connected + "', 'ChannelStateDesc') == 'Up' }}"},
                {'action': 'asterisk.send_action', 'data': {'action': 'Hangup', 'parameters': {'Channel': '{{ channel }}'}}},
            ]}
        rows.append({'type': 'entities', 'title': phone['name'], 'show_header_toggle': False, 'entities': [
            {'entity': ent['registered'], 'name': f'{ext}: registered (derived)'},
            {'entity': ent['state'], 'name': 'Device state'},
            {'entity': connected, 'name': 'Connected line'},
            {'entity': ent['dtmf_sent'], 'name': 'DTMF sent: last event'},
            {'entity': ent['dtmf_received'], 'name': 'DTMF received: last event'},
        ]})
        private_entities.extend(ent[k] for k in ('connected_line', 'dtmf_sent', 'dtmf_received'))
    dtmf = config['dtmf']; de = phones[dtmf['phone_extension']]['entities']['dtmf_' + dtmf['sensor']]
    condition = "{{ trigger.from_state is not none and trigger.to_state is not none and trigger.to_state.state not in ['unknown', 'unavailable', 'None', ''] and trigger.from_state.state != trigger.to_state.state and trigger.to_state.attributes.get('Digit') == '" + dtmf['digit'] + "' and trigger.to_state.attributes.get('Context') == '" + config['dialplan_context'] + "' }}"
    package = {'script': scripts, 'automation': [{
        'id': 'asterisk_dtmf_test_notification', 'alias': 'Asterisk: safe DTMF test notification',
        'initial_state': dtmf['enabled'], 'mode': 'single',
        'triggers': [{'trigger': 'state', 'entity_id': de}],
        'conditions': [{'condition': 'template', 'value_template': condition}],
        'actions': [{'action': 'persistent_notification.create', 'data': {
            'notification_id': 'asterisk_dtmf_test', 'title': 'Asterisk DTMF test',
            'message': 'The configured DTMF test was received. No device was controlled.'}}],
    }]}
    buttons = []
    for service, title in [('asterisk_call_pair', f'Call {src}, then {dst}'), ('asterisk_call_assist', f'Call {src}, then Assist')]:
        buttons.append({'type': 'button', 'name': title, 'icon': 'mdi:phone', 'tap_action': {
            'action': 'perform-action', 'perform_action': 'script.' + service, 'confirmation': {'text': title + '? This rings a phone.'}}})
    for ext in phones:
        buttons.append({'type': 'button', 'name': f'Hang up {ext}', 'icon': 'mdi:phone-hangup', 'tap_action': {
            'action': 'perform-action', 'perform_action': f'script.asterisk_hangup_{ext}', 'confirmation': {'text': f'End the connected call on {ext}?'}}})
    dashboard = {'title': 'Asterisk telephony', 'views': [{'title': 'Phones', 'path': 'phones', 'cards': [
        {'type': 'markdown', 'content': '## Local telephony\nRegistered is derived from device state. Connected Line shows the latest channel, not a complete call ledger. Assist requires a tested media path.'},
        *rows, {'type': 'grid', 'columns': 2, 'square': False, 'cards': buttons}]}]}
    privacy = {'recorder': {'exclude': {'entities': private_entities}}}
    return {'asterisk.yaml': package, 'asterisk_privacy.yaml': privacy, 'asterisk_dashboard.yaml': dashboard}

def yaml_text(value, level=0):
    """Small safe emitter for JSON types. Every string is JSON-quoted (valid YAML)."""
    prefix = ' ' * level
    if isinstance(value, dict):
        if not value:
            return prefix + '{}\n'
        out = ''
        for key, item in value.items():
            key_text = json.dumps(str(key), ensure_ascii=False)
            if isinstance(item, (dict, list)) and item:
                out += prefix + key_text + ':\n' + yaml_text(item, level + 2)
            else:
                out += prefix + key_text + ': ' + json.dumps(item, ensure_ascii=False) + '\n'
        return out
    if isinstance(value, list):
        if not value:
            return prefix + '[]\n'
        return ''.join(prefix + '-\n' + yaml_text(item, level + 2) if isinstance(item, (dict, list)) and item else prefix + '- ' + json.dumps(item, ensure_ascii=False) + '\n' for item in value)
    return prefix + json.dumps(value, ensure_ascii=False) + '\n'

def write_outputs(config, output):
    documents = build(config)  # Validate before creating anything.
    output = Path(output)
    require(not output.is_symlink(), 'Output directory cannot be a symlink')
    for parent in [output, *output.parents]:
        require(not parent.is_symlink(), 'Output path contains a symlink')
    output.mkdir(parents=True, exist_ok=True, mode=0o700)
    os.chmod(output, 0o700)
    for name in documents:
        require(not (output / name).exists() and not (output / name).is_symlink(), f'Output exists: {name}; use a fresh staging directory')
    for name, document in documents.items():
        with (output / name).open('x', encoding='utf-8') as f:
            os.chmod(f.name, 0o600)
            f.write('# Generated locally. Keep outside public Git. Review before installation.\n')
            f.write(yaml_text(document))
    return documents

def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--input', required=True, type=Path)
    parser.add_argument('--output', type=Path, default=Path('.local/rendered'))
    args = parser.parse_args()
    try:
        write_outputs(json.loads(args.input.read_text()), args.output)
    except (ValueError, OSError, json.JSONDecodeError) as exc:
        parser.error(str(exc))
    print('Rendered package, privacy package, and dashboard. Nothing installed or restarted.')

if __name__ == '__main__':
    main()
