# Release notes

## 0.8.0b4

Captures the media session and selected line before resolving a Home Assistant
media-source reference. A line change cannot redirect that pending request, and
a request resolved after another session starts is refused instead of replacing
that session. Idle volume and mute still use the current selected line.

The runtime correction was reviewed in #13. This candidate changes only the
component version and release documentation. Existing entity IDs, configuration,
gateway protocol and action acknowledgement handling are unchanged. Run the
contract suite and both supported Home Assistant runtime jobs on the final
release commit before packaging. Physical phone and audio acceptance remain
separate; publishing this package does not install or activate it.

## 0.8.0b3

Rejects duplicate JSON response members instead of trusting the last value,
including nested and escaped duplicate names. JSON decoder recursion failures
become sanitized gateway errors. Incomplete physical-volume acknowledgements
retain the original action ID and report an unknown outcome without retrying.
Entity IDs, saved settings, media controls and the physical-volume service schema
are unchanged. Validation uses local fixtures; this release does not establish
new physical-phone or audio acceptance.

## 0.8.0b2

Adds capability-gated physical ringer/call volume up/down buttons and the
administrator `hardware_volume` action for 1–5 key presses. Fresh call identity
and complete acknowledgements are required; unknown outcomes are never retried.
The phone selects the physical audio path and does not expose an absolute level.
The existing media slider remains digital gain. See [physical volume](physical-volume.md)
for selected-line behavior, local Hold limitations and automation examples.
This source release does not itself install a gateway or verify physical sound.

## 0.8.0b1

The existing handset media players gain HTTP(S) audio, Home Assistant media-source
resolution and Music Assistant stream compatibility through a versioned managed
gateway transport. Capabilities advertised by that gateway enable pause/resume,
seek for seekable sources, stream volume/mute, queue changes and current-track
metadata. Devices and entity unique IDs stay unchanged. Legacy radio gateways
and manually dialed radio calls retain their previous exact-call Stop path.

Both the HA component and the matching gateway/PBX must be upgraded to use these
controls. The component never turns an accepted request into optimistic Playing
state, and transport actions retain the observed session and line. See the
[media guide](media-player.md) for installation, Music Assistant configuration,
service examples, limits and troubleshooting. Automated tests use modeled local
gateways; physical sound and deployment are separate acceptance steps.

## 0.7.0

The stable release retains the implementation accepted in 0.7.0rc1. Only the
component version and release documentation change. The RC passed the contract
suite and both supported Home Assistant runtime jobs; its published component
archive and all accompanying verification files were downloaded and verified.

The stable component is packaged from its own reviewed commit after the same
checks pass again. Its provenance and checksums identify these new metadata
bytes; it is not presented as a byte-for-byte copy of the RC archive. Installation
and rollback follow the guidance below. No live phone test or deployment is
implied by this source release.

## 0.7.0rc1

This candidate changes only release metadata and documentation from 0.7.0b2.
It retains early validation for empty message fields, consistent Unicode and
control-character checks, bounded explanations for known gateway refusals, and
clearable in-memory message fields. It introduces no new retry, call, gateway
update or firmware behavior.

The component has no additional Python requirements. Qualify the exact release
commit with the existing contract tests and both supported Home Assistant
runtime test jobs before publishing. The beta's recorded physical acceptance
remains historical evidence for that tested installation; publication does not
repeat live phone tests or establish compatibility with every installation.

The downloadable component archive contains only tracked files under
`custom_components/cisco_phone_control`. Its accompanying `files.json`,
`provenance.json` and `SHA256SUMS` bind each payload to the reviewed source commit.
The release includes the frozen `build_package.py` used to reconstruct those
bytes. Verify the checksums before installing, and keep the previous component
archive plus a private configuration backup for rollback.

Keep the existing integration entry, entity registry, preferences and schedules
when updating through the normal installation procedure. Publishing a release
does not install it, restart Home Assistant, place a call or change the gateway.
