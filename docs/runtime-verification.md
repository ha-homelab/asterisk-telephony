# Compatibility verification with Home Assistant 2026.10.0 and 2026.10.2

On October 16, 2026, the unchanged upstream TECH7Fox `v1.0.5`, commit
`65ea9d637a58d6edb3b2bc9e8a6f90de896a9b3e`, was tested in a disposable Python
3.14.7 virtualenv with the actual packages `homeassistant==2026.10.2`,
`asterisk-ami==0.1.7`, and `voluptuous==0.15.2`. Source files came from the
archive verified against [upstream.lock.json](../upstream.lock.json).
The original four tests in [test_upstream_runtime.py](../tests/test_upstream_runtime.py) passed.

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

The later [run at commit `66320ad`](https://github.com/ha-homelab/asterisk-telephony/actions/runs/39053379153)
passed 37 generator/template checks and all six runtime tests on both HA
versions. The added runtime cases cover single-phone YAML and an answered
call whose Connected Line still reports `Ring`: Hangup uses the current
Device State and the captured channel instead.

A later local regression on HA 2026.10.0 covers an Echo call whose Connected
Line remains the literal string `None` after `Newchannel`, with
`ConnectedLineNum=<unknown>`. It runs the generated Hangup script through HA's
actual script engine and service registry: an active endpoint's own channel
receives Hangup, while idle, ringing, missing, foreign, and unavailable cases
send no action. This uses the pinned upstream sensor classes and modeled AMI;
it does not itself prove a successful live call or delivery after an AMI outage.

The optional per-phone Assist-origin test also runs generated scripts through
HA's actual script engine and the pinned integration. It verifies separate
Originate actions for 201, 203 and 205, each ringing its selected endpoint and
then connecting to 630. The existing 201 script keeps its original ID. These
are synthetic AMI actions; successful real calls still require phone acceptance.

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
eight runtime tests and runs the main standard-library test suite.

## What still needs acceptance testing at home

The server stage was completed on October 17, 2026, with HA 2026.10.0: Asterisk
and VoIP loaded, AMI was verified from the HA container, and a status dashboard
was added. This is a separate
[live system verification](https://github.com/4alvit/cisco-home-telephony/blob/main/docs/server-validation.md),
not a result of the mock tests described here. Additional current checks for
the AMI probe and both HA versions passed in
[CI for commit 7c04416](https://github.com/ha-homelab/asterisk-telephony/actions/runs/39029938171).

The first physical 7945G subsequently converted to SIP, registered as 201 and
passed the operator-confirmed handset echo test. The single-phone control
package and Recorder exclusions were installed; a full HA restart preserved
all 214 existing script/automation entities and the native VoIP settings.
The operator confirmed Russian Assist replies from a dialed 630 call and an
HA-initiated call. A separate live test invoked the generated Hangup script
and confirmed the intended channel ended, HA returned to idle and Asterisk
had no active channels. The dashboard configuration was read back through the
API; browser confirmation behavior remains a separate UI check.
The linked live record distinguishes these checks from the remaining physical
acceptance work.

Adding line 202 required reloading the existing Asterisk config entry before
its five real entity IDs appeared. The update retained the 201 mappings,
`call_from: "201"`, and `call_to: null`: these are two endpoints on one handset.
The guarded package update compared live files with saved baseline hashes.
The Recorder change required a full HA restart; verification preserved all 217
pre-update script/automation entities and automation enabled states, and
confirmed the exclusion union grew from 46 to 49 entries. Dashboard API
readback matched the reviewed update. The existing native VoIP identity and
Russian Assist settings for 201 remained intact; no native VoIP identity for
202 was created or tested.

A subsequent live **202 → 600** test used the second line on that same 7945G.
Asterisk reported 261 received and 261 transmitted G.711 μ-law RTP packets,
with zero reported loss during the approximately five-second sample. Connected
Line remained `None` while Device State was `In use`; the corrected generated
HA Hangup script ended the current 202 channel. HA returned to `Not in use`,
registration stayed on, and the PBX reported zero active channels. This verifies
RTP flow and call control for 202, not operator-confirmed audible echo on that
line. The prior human audio confirmation applies to 201.
The preceding Hangup correction replaced only the owned script package and
used `script.reload`, with no further HA restart or privacy/dashboard changes.
The ended call's Channel attribute remained visible after HA returned to idle,
confirming why Hangup must also require current Device State and channel ownership.

HA was then prepared for six lines across three handsets. A scoped Asterisk
integration reload discovered all 20 actual entities for new lines 203–206,
while the two additional phones still required removal of old ITL trust.
Four guarded Hangup scripts and their dashboard cards were installed without
changing the existing 201/202 controls. A full configuration check passed;
one controlled HA restart activated 12 additional Recorder exclusions, bringing
the exact total from 49 to 61. Verification preserved all 218 pre-update
script/automation entities and automation enabled states, and dashboard API
readback matched the reviewed changes. The PBX had zero active calls/channels
at the maintenance checks.

Afterward, 201 and 202 were registered and idle, and 201's native VoIP identity
and Russian Assist settings were unchanged. Lines 203–206 remained unregistered
and `Unavailable`; no native VoIP identities for 203 or 205 had been discovered.
Their conversion, registration, live calls, audio, Hangup and Assist acceptance
remain pending. This verifies the HA configuration, not those phone tests.
Existing offline entities can be configured using strict registry/device
ownership checks while physical acceptance is pending; this does not require
weakening the runtime Hangup guard or inventing entity IDs.

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
