# Sources and verification limits

Source review date: October 16, 2026. A feature being present in source code
does not mean it has been tested on a particular home installation.

## Upstream Asterisk integration

- [Repository](https://github.com/TECH7Fox/asterisk-hass-integration).
- [Pinned commit](https://github.com/TECH7Fox/asterisk-hass-integration/tree/65ea9d637a58d6edb3b2bc9e8a6f90de896a9b3e), manifest version 1.0.5.
- [Action schema](https://github.com/TECH7Fox/asterisk-hass-integration/blob/65ea9d637a58d6edb3b2bc9e8a6f90de896a9b3e/custom_components/asterisk/services.yaml): `asterisk.send_action`, with `action` and `parameters` fields.
- [Setup/service implementation](https://github.com/TECH7Fox/asterisk-hass-integration/blob/65ea9d637a58d6edb3b2bc9e8a6f90de896a9b3e/custom_components/asterisk/__init__.py): discovery during setup, a global action, and AMI TCP.
- [Sensors](https://github.com/TECH7Fox/asterisk-hass-integration/blob/65ea9d637a58d6edb3b2bc9e8a6f90de896a9b3e/custom_components/asterisk/sensor.py): Channel attributes and DTMF timestamp sensors.
- [Binary sensor](https://github.com/TECH7Fox/asterisk-hass-integration/blob/65ea9d637a58d6edb3b2bc9e8a6f90de896a9b3e/custom_components/asterisk/binary_sensor.py): Registered is derived from device state.
- [Lock file](../upstream.lock.json): commit, URL, archive size, and SHA-256.

No explicit upstream license allowing its source to be copied into this
repository was found. This repository contains its own scripts, documentation,
and links; the installer obtains the user's selected upstream directly from
its repository. A link or publicly visible source code must not be interpreted
as relicensing it.

## Home Assistant and HACS

- [Packages](https://www.home-assistant.io/docs/configuration/packages/): YAML inclusion and merging rules.
- [Dashboard actions](https://www.home-assistant.io/dashboards/actions/): `perform-action`, `perform_action`, and confirmation.
- [Recorder](https://www.home-assistant.io/integrations/recorder/): entity exclusion and history.
- [Voice over IP](https://www.home-assistant.io/integrations/voip/): the separate integration and SIP port configuration.
- [HA 2026.10.2 VoIP source](https://github.com/home-assistant/core/tree/2026.10.2/homeassistant/components/voip): the version used to verify the media contract.
- [HACS custom repositories](https://www.hacs.xyz/docs/faq/custom_repositories/): installation of external integrations.

## Asterisk

- [AMI Originate](https://docs.asterisk.org/Latest_API/API_Documentation/AMI_Actions/Originate/).
- [AMI Hangup](https://docs.asterisk.org/Latest_API/API_Documentation/AMI_Actions/Hangup/).
- [AMI configuration](https://docs.asterisk.org/Configuration/Interfaces/Asterisk-Manager-Interface-AMI/).

Also verify AMI permissions against your actual Asterisk version. Discovery
in the selected upstream requires `system`; that account must not be described
as read-only.
