# Compatibility verification with Home Assistant 2026.10.0 and 2026.10.2

On October 16, 2026, the unchanged upstream TECH7Fox `v1.0.5`, commit
`65ea9d637a58d6edb3b2bc9e8a6f90de896a9b3e`, was tested in a disposable Python
3.14.7 virtualenv with the actual packages `homeassistant==2026.10.2`,
`asterisk-ami==0.1.7`, and `voluptuous==0.15.2`. Source files came from the
archive verified against [upstream.lock.json](../upstream.lock.json).
All four tests in [test_upstream_runtime.py](../tests/test_upstream_runtime.py) passed.

On October 17, the same suite passed with `homeassistant==2026.10.0` before
installation on that HA version. CI checks both versions in separate isolated
environments. To reproduce the 2026.10.0 run, substitute
`requirements-runtime-2026.10.0.txt` for `requirements-runtime.txt` below.

The same suite also passed on Linux amd64 in
[GitHub Actions](https://github.com/ha-homelab/asterisk-telephony/actions/runs/39005310862):
26 generator and installer checks plus four separate HA runtime checks.
The [Asterisk container checks](https://github.com/4alvit/cisco-home-telephony/actions/runs/39005213169)
also passed: 20 unit tests, image builds, and an isolated smoke test with real
AMI, G.711/Opus translation, Assist routing, and TFTP.

The following API boundaries were verified:

- The upstream configuration form serializes with current HA/Probatio and
  contains host, port, username, and password. Synthetic AMI login creates
  a config entry.
- Setup correctly handles the absence of chan_sip and discovers PJSIP endpoints.
  The real HA service registry calls `asterisk.send_action` and passes
  Originate/Hangup to the upstream integration with the expected parameters.
- Six actual sensor classes initialize, request CoreSettings, and handle
  unavailable/ringing states and repeated DTMF 1/1/#. An event for another
  ConnectedLineNum does not update the selected DTMF sensor.
- Generated YAML passes the actual HA schemas for scripts, automations, and
  state triggers. Action sequences pass HA's asynchronous validation.

A controlled fake replaces the AMI client. Platform forwarding is intercepted;
service registration, the config flow form, and sensor classes are real.
The result therefore verifies these APIs' compatibility, not a full Home
Assistant startup, AMI network delivery, live HA configuration, or a successful
Cisco phone call.

## Reproducing the tests outside the running HA instance

Python 3.14.2 or newer is required. From this repository's root, create a fresh
staging directory and a separate environment. Do not install test dependencies
into your running Home Assistant instance.

```bash
python3 scripts/stage_integration.py --stage .local/runtime-upstream
uv venv --python 3.14 .local/runtime-venv
uv pip install --python .local/runtime-venv/bin/python -r requirements-runtime.txt
ASTERISK_UPSTREAM_ROOT="$PWD/.local/runtime-upstream" \
  .local/runtime-venv/bin/python -m unittest discover -s tests -p test_upstream_runtime.py -v
```

The installer refuses to overwrite an existing staging directory. For a later
run, reuse the verified directory or select a new path. An ordinary
`python3 -m unittest discover -s tests -v` without this environment skips the
four runtime tests and runs the main standard-library test suite.

## What still needs acceptance testing at home

The server stage was completed on October 17, 2026, with HA 2026.10.0: Asterisk
and VoIP loaded, AMI was verified from the HA container, and a status dashboard
was added. This is a separate
[live system verification](https://github.com/4alvit/cisco-home-telephony/blob/main/docs/server-validation.md),
not a result of the mock tests described here. Additional current checks for
the AMI probe and both HA versions passed in
[CI for commit 7c04416](https://github.com/ha-homelab/asterisk-telephony/actions/runs/39029938171).

After installation, test AMI reconnection, state updates when a phone registers
or disconnects, incoming and outgoing calls, hold/hangup, and actual DTMF
directions. Upstream uses `ConnectedLineNum` to select a sensor, so verify its
relationship to the handset sending the digit with a call between two phones.

For Assist, separately test Cisco G.711 → Asterisk Opus → HA VoIP, Allow calls,
the STT/TTS pipeline, audio in both directions, and channel release after hangup.
The API tests in this document do not replace that acceptance work.

Primary sources:
[TECH7Fox integration](https://github.com/TECH7Fox/asterisk-hass-integration/tree/65ea9d637a58d6edb3b2bc9e8a6f90de896a9b3e),
[HA 2026.10.2](https://github.com/home-assistant/core/tree/2026.10.2),
[official VoIP integration](https://www.home-assistant.io/integrations/voip/).
