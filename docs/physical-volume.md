# Physical phone volume

Version **0.8.0b2** adds four buttons to each Cisco device: **Ringer volume up**,
**Ringer volume down**, **Call volume up**, and **Call volume down**. They require
a gateway advertising `hardware_volume` and remain unavailable with an older
gateway or an offline phone. Update both sides using the
[installation guide](phone-controls.md); existing entity IDs and settings remain.

Each press sends one physical volume-key event. Ringer buttons require an idle
phone. Call buttons use the saved **Line** selection and require exactly one
connected call on that line across the physical phone. If the call is on the
sibling line, select that line first. A fresh gateway inventory supplies the
channel and its unique ID; a changed or ambiguous call is refused.

Resume a locally held call before changing call volume. PBX state cannot prove
that local Hold is off or that an idle phone is physically on-hook. The phone
chooses whether its volume key affects the current handset, headset, speaker or
ringer. There is no independent output selector or readable absolute hardware
volume. Acknowledgement means the key events were accepted, not that the phone
measured a new level or verified that physical audio path. At its limit, another
key press may have no audible effect.

For call volume to persist into future calls, the phone may require its
currently displayed **Save** softkey. This integration does not guess or press
a softkey position. See the [illustrated CLI and protocol guide](https://github.com/4alvit/cisco-home-telephony/blob/main/docs/hardware-volume.md)
for the diagram, command examples and official Cisco references.

The existing media-player volume slider remains **digital stream gain**. It does
not move the phone's physical volume setting. A quiet physical speaker can still
limit loudness with digital gain at maximum. Neither control adjusts an unrelated
call automatically. Active voice-assistant ownership, alerts or pending media
cleanup can refuse the physical controls; this action never stops them to gain
access to the phone.

## Automation action

The administrator-only `cisco_phone_control.hardware_volume` action accepts an
explicit line, context (`ringer` or `call`), direction (`up` or `down`) and integer
`steps` from 1 through 5, defaulting to 1. IDs below are synthetic examples.

```yaml
action: cisco_phone_control.hardware_volume
data:
  entry_id: YOUR_CISCO_CONFIG_ENTRY_ID
  phone: "201"
  line: "202"
  context: call
  direction: up
  steps: 2
response_variable: phone_volume_result
```

The response reports `context`, `direction`, `requested_steps` and
`acknowledged_steps`. A complete response must acknowledge every requested key.
`level_known` and `physical_context_verified` remain `false`, `volume_level` is
`null`, and `audio_output` is `contextual`. Supplying a line in the action does
not change the device's saved Line selection.

If the result is refused before sending, follow its explanation: select the
connected line, wait for the phone to become idle, or resolve voice/media
ownership. If a request times out or only some key presses may have applied,
the result is **outcome unknown**. Do not retry it blindly: inspect the phone's
actual loudness before deliberately sending another step. The integration sends
one request and never retries a physical key operation automatically.

From **0.8.0b3**, duplicate JSON members or decoder failures cannot be mistaken
for a successful gateway reply. An incomplete physical-volume acknowledgement
also retains the action ID in its error. Use that ID to correlate the existing
request with gateway logs; it is not an instruction to repeat the key operation.
