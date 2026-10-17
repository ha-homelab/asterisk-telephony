# Package, dashboard, and DTMF

## Actual entity IDs

Upstream creates Registered, State, Connected Line, DTMF Sent, and DTMF Received
entities for discovered SIP/PJSIP endpoints. Names may depend on the entity
registry, renaming, and conflicts. Do not derive entity IDs from a phone number
using a naming pattern.

Open Devices & services → Asterisk → the phone device → Entities and copy the
actual IDs. Registered uses the `binary_sensor` domain; the others use `sensor`.
Fill in all five fields for each real SIP endpoint in `.local/site.json`.
The `phones` array accepts 1–50 endpoint mappings, regardless of how many
physical handsets provide them. Start with the single-phone pilot below, or
use `examples/site.example.json` for pair calling between two physical phones.

Upstream derives Registered from device state, not a complete audit of SIP
ContactStatus. State describes the endpoint state. Connected Line represents
the last event for one channel, not a complete list of simultaneous calls.
Also check registration and the full channel list directly on Asterisk.

The pinned integration enumerates endpoints during setup. If a newly added
endpoint is registered on Asterisk but absent from HA, reload the existing
Asterisk config entry before copying its actual IDs; see the
[discovery procedure](operations.md#symptoms). Do not invent IDs or add a
duplicate integration.

```bash
python3 scripts/render.py --input .local/site.json --output .local/rendered
```

The generator does not print the mapping or AMI secret. It rejects unknown
fields, placeholders, unsuitable domains, duplicate IDs, and unsafe contexts
or extensions. Use a fresh output directory on each run: existing files are
not overwritten. These are syntax checks; the user must confirm that the
entities actually exist in HA.

## Single-phone pilot

Use the dedicated example when only one physical phone has been verified:

```bash
umask 077
mkdir -p .local
cp examples/site.single-phone.example.json .local/site.single-phone.json
# Replace every entity placeholder with the actual HA registry ID.
python3 scripts/render.py --input .local/site.single-phone.json --output .local/rendered-single-phone
```

Keep `call_to` explicitly `null` (JSON null, not the string `"null"`). This omits
`script.asterisk_call_pair` and its dashboard button. The package still contains
the Assist call script, guarded Hangup script, all five mapped phone entities,
privacy exclusions, and disabled DTMF notification example. It does not create
a second phone or loosen the requirement for actual entity IDs.

`call_from` must name a mapped phone, and `assist_extension` must be a distinct
number that does not belong to any mapped phone. Assist still requires a tested
SIP/media path; generating the button does not establish that it works.
Keep DTMF automation disabled until its real event direction is verified.

To add pair calling later, add the second phone's five actual entity IDs and
set `call_to` to its distinct extension string. Render into a fresh directory,
review the result, and replace the previous generated package and dashboard
after backing them up. Do not install both generated packages together.
Conversely, if pair calling is disabled after an earlier installation, replace
the old owned package rather than merging its obsolete call-pair script into
the new one. Existing two-phone configurations need no changes.

### Two lines on one handset

For lines 201 and 202 on one Cisco 7945G, keep `call_from: "201"` and
`call_to: null`. Retain the existing 201 mapping and add 202 only after its
five actual Asterisk entity IDs have been discovered and checked. Name the
rows, for example, `Cisco 7945G — line 201` and `Cisco 7945G — line 202`.
The generator retains Assist from 201 and Hangup 201, adds Hangup 202 and
three Recorder exclusions, and leaves pair calling absent. Keep the existing
disabled DTMF example unchanged. No renderer change is required.

Replace the owned generated package after reviewing the diff; do not install
a second copy. Preserve custom dashboard cards, including native VoIP controls.
Before using Hangup 202, compare its current `PJSIP/202-…` Channel and Device
State with the live PBX call, as described below. Two lines on one handset do
not satisfy acceptance tests that require two physical phones.

Asterisk endpoint discovery does not create a native HA VoIP identity. That
identity follows the extension presented on a call to 630. Adding line 202
therefore does not require a second VoIP device or a change to 201's Allow
calls, pipeline, or language settings. Discover and configure a separate 202
VoIP identity only if that line will also call Assist; preserve the working
201 identity and its controls.

## Installing the files

Copy `asterisk.yaml` and `asterisk_privacy.yaml` to `/config/packages/`.
Do not copy the dashboard into packages. Add packages to the **existing**
`homeassistant` section in `configuration.yaml`, without creating a duplicate key:

```yaml
homeassistant:
  packages: !include_dir_named packages
```

If packages are already included another way, adapt the path to your setup.
[Official package merging rules](https://www.home-assistant.io/docs/configuration/packages/).

Copy `asterisk_dashboard.yaml` to `/config/dashboards/`. For a YAML dashboard,
add the following to the existing `lovelace` section:

```yaml
lovelace:
  dashboards:
    asterisk-telephony:
      mode: yaml
      title: Telephony
      icon: mdi:phone
      show_in_sidebar: true
      filename: dashboards/asterisk_dashboard.yaml
```

Alternatively, create a separate dashboard in the UI and paste the file contents
into the Raw configuration editor. Do not replace the main dashboard wholesale.
Then run a config check and a controlled restart after active calls finish.
Recorder creates its exclusion filter at startup, so the new privacy package
requires a restart. For later script-only changes, `script.reload` includes
package scripts but does not activate Recorder changes. These scripts use the
`asterisk_` prefix; check for duplicate keys in other packages.
For an existing installation, use the [guarded update procedure](operations.md#updating-an-existing-installation)
to preserve unrelated configuration and verify the result.

## Call control

When `call_to` names a second mapped phone, `script.asterisk_call_pair` uses `asterisk.send_action` with
`action: Originate` and `parameters: {Channel, Context, Exten, Priority, CallerID,
Timeout, Async}`. The PBX setting `allow_originate:true` is required.
Similarly, `script.asterisk_call_assist` rings the selected phone first, then
connects it to 630. Do not use this button until Assist has passed acceptance tests.

`script.asterisk_hangup_<number>` reads the **current** `Channel` from the chosen
Connected Line sensor every time. Its guard requires a `PJSIP/<number>-…` channel,
the mapped Device State sensor to be `In use`, and a Connected Line state other
than `unknown`, `unavailable`, or an empty string.
It also rechecks that the current Channel still equals the captured value.
Upstream updates Connected Line on `NewConnectedLine`, `Newchannel`, and `Hangup`,
not `Newstate`; its `ChannelStateDesc` can remain `Ring` during an answered call.
Device State instead follows `DeviceStateChange`.

The literal Connected Line state `None` is allowed when those channel and
Device State checks pass. Upstream sets this string on `Newchannel`, including
an answered Echo call to 600 whose `ConnectedLineNum` remains `<unknown>`.
It describes the absence of a connected-party number, not proof that no call
exists. An idle endpoint or a missing/foreign Channel still fails the guard.
Upstream can retain the last Channel attribute after Hangup while Device State
has returned to `Not in use`; the presence of that attribute alone does not
identify a live call.

The guard rejects observed idle, unavailable, ringing, changed, or foreign
channels. It cannot prove freshness if AMI events have been lost; after an AMI
outage, verify fresh events and the live PBX channel list before using Hangup.
The button is for connected calls, not every ringing stage. If upstream reports
the opposite party's channel, the guard rejects the action: inspect the
attributes and select the correct sensor. Never store a channel suffix such as
`-0000001a` as a permanent setting.

The dashboard asks for confirmation before calling or hanging up. This prevents
accidental clicks; it does not restrict the account's AMI permissions.

## DTMF

Upstream does not emit a separate DTMF event on the HA event bus. A DTMF sensor
stores a timestamp in its state and `Digit`, `Channel`, `ConnectedLineNum`, and
`Context` in its attributes. Sent/Received directions are from Asterisk's
perspective and are filtered by ConnectedLineNum. The sensor name therefore
does not guarantee that a digit came from that physical handset's keypad.
Check both directions with a test call.

The example uses a state trigger, a changed timestamp, the selected `Digit`,
and the context. Defaults are `dtmf.enabled:false` and automation
`initial_state:false`. Select the actual sensor after testing, then deliberately
enable the automation. It calls `persistent_notification.create` only: no locks,
alarms, doors, external messages, or payments. Do not enter DTMF secrets during testing.

A separate privacy package excludes call and DTMF entities from Recorder.
The dashboard shows the time of the last DTMF event, not the entered digits.
For troubleshooting, inspect the `Digit` attribute in Developer tools → States.
