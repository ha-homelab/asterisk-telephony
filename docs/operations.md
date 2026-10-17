# Operations and troubleshooting

## Before updating

Save the HA version, the Asterisk integration's `manifest.json`, the locked
commit and hash, a backup of HA configuration including `.storage`, the previous
packages and dashboard, and the private entity mapping. Install changes in a
test HA instance first. Then repeat [acceptance testing](acceptance.md), especially
DTMF mapping and the current Channel used for Hangup. Automatic upstream updates
must not silently override the pinned installation.

A new upstream version requires a diff review, a new commit and ZIP SHA-256,
an updated `upstream.lock.json`, and a staging test. Do not change the checksum
just because a download stopped validating. GitHub-generated ZIP files can
change when regenerated; confirm the source tree and provenance.

## Updating an existing installation

1. Save the current owned packages, base configuration, entity mappings,
   dashboard, and hashes before rendering. Record existing script/automation
   entity IDs and automation enabled states, Recorder exclusions, and native
   VoIP settings. Keep this material private.
2. Render into a fresh directory and review the diff. Immediately before
   replacing a file, require its current hash to match the saved baseline.
   Stop and reconcile any concurrent change. Replace only the owned files;
   preserve existing endpoint mappings, call controls, and unrelated keys.
   Read a storage-mode dashboard again before saving, require it to match the
   baseline, and preserve its cards in the replacement.
3. Wait for active calls to finish and run HA's configuration check. Redirect
   raw stdout/stderr to a private file with `umask 077`: diagnostics can expose
   credentials from unrelated
   configuration. Report only the exit status and sanitized diagnostics, never
   paste the full log into a public issue or commit. A change to Recorder
   exclusions needs a controlled HA restart. For a script-only
   correction, replace only `asterisk.yaml` and call `script.reload`; it reloads
   package scripts without a restart and cannot apply Recorder changes.
4. After a restart, wait for HA to reach `RUNNING` and the relevant config
   entries to load. A ready container or responding HTTP endpoint alone is
   insufficient. Verify all prior script/automation entities remain, automation
   enabled states are unchanged, and Recorder has the exact previous exclusions
   plus the intended additions. Confirm native VoIP settings, actual endpoint
   IDs, and dashboard API readback against the reviewed result.
5. Check the generated scripts are idle, the DTMF example remains disabled,
   and no pair-call script exists when `call_to` is `null`. Record before/after
   hashes and verification results, then test the changed call control with a
   fresh live channel. Keep the previous owned files available for rollback.

## Symptoms

**The integration does not connect.** Check the address and TCP5038 from the
HA host, the source IP after SNAT, the exact `/32` ACL, the dedicated AMI secret,
and Asterisk logs. A TCP connection check without sending the password is
enough to test basic network reachability. Do not expose the secret in command
arguments, shell history, `kubectl describe`, or shared logs. To verify login,
version, PJSIP discovery, and denied CLI access from the HA container, use the
[AMI probe with its password supplied through stdin](ami-verification.md).

**HA connects but has no entities.** Check `PJSIPShowEndpoints`, discovery
permissions, and that the PJSIP endpoints existed before the integration loaded.
The pinned TECH7Fox 1.0.5 enumerates devices during setup. If a new endpoint is
registered on the PBX but absent from HA, wait for calls to finish, then use
Devices & services → Asterisk → the existing config entry's menu → Reload.
This scoped reload was required to discover line 202; adding another config
entry or restarting all of HA is unnecessary for endpoint discovery. Wait for
the entry to load, confirm the original endpoint IDs remain, then copy the new
endpoint's five actual entity IDs. A `SIPpeers` error is
expected on a PJSIP-only PBX if subsequent PJSIP discovery succeeds. Do not use
`write=all` as a general fix.

**Registered differs from `pjsip show contacts`.** It is derived from DeviceState.
Use the PBX as the registration source of truth and compare AMI events. This is
not a SIP REGISTER audit system. After losing AMI, verify fresh data rather
than relying on the last saved state shown by a green icon.

**Originate does not work.** The PBX needs `allow_originate:true`. Check the
context, the destination extension, and source phone availability. Answer the
source phone first; only then does Asterisk connect the destination.

**Hangup does nothing.** The mapped Device State must be `In use`, Connected Line
must not be `unknown`, `unavailable`, or empty, and its current Channel must still
equal the captured channel for this endpoint. The literal state `None` is valid
for an active Echo call: upstream can retain it from `Newchannel` when no
connected-party number is supplied. Idle, ringing, unavailable, changed, or foreign channels are
rejected. Connected Line's `ChannelStateDesc` can remain `Ring` after answer
because upstream does not update it on `Newstate`. Compare the channel with
`core show channels concise` on the PBX. After Hangup, the old Channel attribute
can remain even though Device State is `Not in use`; never treat that retained
attribute as proof of an active call. Lost AMI events can leave stale states
that the guard cannot detect; confirm fresh data after a disconnect. Do not
replace the guard with an action that sends a saved Channel value.

**DTMF produces no notification.** The automation is disabled by default. During
a test call, check the actual DTMF sensor, direction, timestamp change, Digit
attribute, and Context. Do not expect a DTMF event on the HA event bus from this
upstream integration.

**Assist rings but cannot hear or reply.** Separately check the HA pipeline,
the VoIP device's call permission, SIP/SDP, RTP between nodes, and both G.711↔Opus
translation paths on the PBX. SIP 200 OK or a `codec_opus` file in a directory
does not verify audio. Do not switch the Cisco phone's transport to UDP merely
because the Asterisk→HA leg uses a different transport.

## Recovery

Restore compatible versions of the component and HA configuration, followed by
the entity mapping. Preserving the registry usually preserves IDs; verify them
before installing the package anyway. When rotating the AMI secret, update the
PBX and the HA config entry together, check reconnection, and do not publish
either value in a diff. Backups must be encrypted and tested by restoring them
in a separate environment.
