# Acceptance testing

This repository's CI tests the generator and installer with synthetic data.
A separate job tests the [real HA runtime with modeled AMI](runtime-verification.md).
These tests do not confirm the state of your network, HA, PBX, or phones.
Perform live acceptance testing separately, without secret DTMF sequences
or external phone numbers.

## 1. Package and installation

- Hash, size, and manifest checks pass; staging contains only the Asterisk component.
- Without `--apply`, the HA configuration remains unchanged.
- Backup and rollback are available, the HA config check passes, and the selected HA Pod is ready.
- The Asterisk integration connects to exactly one PBX using the specified account.

## 2. Discovery and state

- Registered, State, Connected Line, DTMF Sent, and DTMF Received entities appear
  for two existing PJSIP endpoints. All IDs have been checked manually.
- Taking a physical phone offline and online is compared with PBX contacts and HA state.
- Starting, connecting, and ending a call changes the observed states.
- After a temporary AMI disconnection, the connection recovers. A real event
  confirms fresh sensor data; merely loading the UI is not enough.

## 3. Call control

- `asterisk_call_pair` rings source phone 201. After answer, it rings 202.
- Canceling the dashboard confirmation does not start a call.
- A PBX with `allow_originate:false` rejects Originate. This does not make the
  account completely read-only: Hangup and ModuleLoad are included in `system`.
- During a connected call, Channel belongs to the intended endpoint.
- Hangup ends only the selected live channel. Idle, None, and another endpoint's
  Channel fail the guard. For simultaneous calls, Connected Line may be
  insufficient: check the full channel list on the PBX.

## 4. DTMF

- During a 201↔202 call, press the selected test digit on each handset in turn.
- Record which actual Sent/Received sensor changes its timestamp and Digit.
- Correct the mapping, enable the notification automation, and repeat the test.
- Only a local notification appears; no home controls are operated.
- Repeating the same digit produces a new timestamp. Restarting HA alone must
  not replay an old DTMF event as a new action.
- Disable the automation again after learning or pilot testing if appropriate.

## 5. Assist 630

- The pipeline responds in the HA UI before a phone is connected.
- Both G.711↔Opus transcoding directions on the PBX are verified with actual
  audio samples or a live call. An `opus` format without a working codec and
  translation path is not a pass.
- The phone calls 630, and the VoIP device receives the correct From extension.
- After calls are allowed, the request is recognized and the reply is audible
  on the phone.
- Both RTP directions, repeat calls, hangup, and channel cleanup are verified.
- If the setup is described as local, STT, the agent, and TTS do not send requests
  to cloud services.

## Recording results

Keep results locally: date, HA/PBX/upstream versions, codec image and digest,
verified items, and defects. Do not attach CallerID, real IP addresses, AMI
secrets, residents' audio, or DTMF sequences to a public issue. Use a redacted
example with the minimum number of events needed to explain the result.
