# Release notes

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
