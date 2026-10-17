# Package, dashboard, and DTMF

## Actual entity IDs

Upstream creates Registered, State, Connected Line, DTMF Sent, and DTMF Received
entities for discovered SIP/PJSIP endpoints. Names may depend on the entity
registry, renaming, and conflicts. Do not derive entity IDs from a phone number
using a naming pattern.

Open Devices & services → Asterisk → the phone device → Entities and copy the
actual IDs. Registered uses the `binary_sensor` domain; the others use `sensor`.
Fill in all five fields for each real phone in `.local/site.json`. The generator
accepts 1–50 phones. Start with the single-phone pilot below, or use
`examples/site.example.json` for two phones; add other real phones in the same way.

Upstream derives Registered from device state, not a complete audit of SIP
ContactStatus. State describes the endpoint state. Connected Line represents
the last event for one channel, not a complete list of simultaneous calls.
Also check registration and the full channel list directly on Asterisk.

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
Then run a config check and a controlled restart or reload. These scripts use
the `asterisk_` prefix; check for duplicate keys in other packages.

## Call control

When `call_to` names a second mapped phone, `script.asterisk_call_pair` uses `asterisk.send_action` with
`action: Originate` and `parameters: {Channel, Context, Exten, Priority, CallerID,
Timeout, Async}`. The PBX setting `allow_originate:true` is required.
Similarly, `script.asterisk_call_assist` rings the selected phone first, then
connects it to 630. Do not use this button until Assist has passed acceptance tests.

`script.asterisk_hangup_<number>` reads the **current** `Channel` from the chosen
Connected Line sensor every time. Its guard requires a `PJSIP/<number>-…` channel,
state `Up`, and a nonempty Connected Line. It does not send an idle, stale, or
another endpoint's channel. The button is for connected calls, not every ringing
stage. If upstream reports the opposite party's channel, the guard rejects the
action: inspect the attributes and select the correct sensor. Never store a
channel suffix such as `-0000001a` as a permanent setting.

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
