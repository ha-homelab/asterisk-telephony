# Privacy and access

![Data boundaries](diagrams/privacy.svg)

Public Git contains generator source code, documentation, the upstream lock
file, and examples with synthetic IDs. Actual IP addresses, room and resident
names, entity maps, AMI credentials, `.storage`, backups, and logs stay in
`.local/` or your private storage. `.gitignore` reduces the chance of mistakes
but does not replace reviewing `git diff --cached` before publication.

## What Home Assistant can see

AMI carries numbers, CallerID names, channel states and identifiers, and DTMF
events may contain entered digits. Do not treat this as harmless telemetry.
The AMI password is stored in the HA config entry, so `.storage` backups also
contain secrets. Review diagnostic exports and debug logs before sharing them
with others. Do not leave upstream debug logging permanently enabled in a
shared logging system: actions and responses may contain call parameters.

## Recorder

The generated `asterisk_privacy.yaml` excludes each phone's Connected Line and
both DTMF sensors from Recorder. Registered and State remain available in
history. This does not remove old records, hide current attributes from HA
users, or change external log and backup systems. If these entities were
previously recorded, consider a separate history cleanup using the
[official Recorder documentation](https://www.home-assistant.io/integrations/recorder/).
Do not purge the entire database solely for this integration.

## Access to AMI and HA

The selected integration's ordinary AMI TCP connection is unencrypted. Use a
trusted LAN or VPN, an exact `/32` source ACL, and a host firewall. Do not
publish SIP, TFTP, or AMI ports externally. HA's AMI account has privileged
`write=system` access; hiding dangerous buttons does not restrict that access.

Dashboard confirmation prevents accidental clicks. Give HA administration,
script, developer-tool, and automation access only to people authorized to
control telephony. The DTMF automation is disabled by default and only displays
a local notification. Before adding home controls, separately address
authorization and incorrect or repeated events.

Assist may send audio and text to the configured speech or AI providers. For
a fully local setup, choose local STT, a local conversation agent, and local
TTS, and verify their settings. A phone connecting to a home IP address alone
does not make the configuration local.
