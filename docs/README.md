# Documentation

An Asterisk/PJSIP telephony stack with a Home Assistant bridge, covering the PBX
contract, the Cisco phone integrations and the pinned installation.

## Install and configure

- [Pinned installation](installation.md) — `upstream.lock.json` and the pinned source ZIP.
- [PBX contract](pbx-contract.md) — the Asterisk/PJSIP target and the one-AMI-per-instance rule.
- [Package, dashboard and DTMF](configuration.md) — the upstream entities this setup relies on.
- [Installation through HACS](hacs.md) — the optional Cisco Phone Control integration.
- [Home Assistant Container in k3s](ha-container-k3s.md) — the existing container this assumes and how the pieces fit.

## Phone features

- [Cisco phone controls, timers and alarms](phone-controls.md) — the optional custom integration and the entities it adds.
- [Toggle a saved alarm from a handset](dialled-alarm.md) — the administrator-only `toggle_saved_alarm` operation.
- [Physical phone volume](physical-volume.md) — the four ringer and speaker buttons added per device.
- [Music and radio on Cisco phones](media-player.md) — one media player per phone from the existing integration.

## Verification

- [AMI verification with safe password handling](ami-verification.md) — what `verify_ami.py` checks and how it handles the password.
- [Acceptance testing](acceptance.md) — what CI covers with synthetic data and what it cannot.
- [Home Assistant compatibility verification](runtime-verification.md) — the current CI target version and the named pins.
- [Operations and troubleshooting](operations.md) — what to record before reporting a problem.

## Records

- [Release notes](release-notes.md) — the maintenance beta and its unchanged runtime behaviour.
- [Sources and verification limits](sources.md) — source review date and what presence in code does not prove.
- [Privacy and access](privacy.md) — the data boundaries of the setup.
