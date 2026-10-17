# PBX contract

## AMI

This setup targets Asterisk/PJSIP and one AMI integration per HA instance.
The upstream `asterisk.send_action` action is global and has no config-entry
selector. Do not add several PBXs expecting to select a server per action.

The companion PBX generator uses this local configuration structure:

```json
{
  "home_assistant": {
    "enabled": true,
    "ami_client_ipv4": "192.0.2.20",
    "allow_originate": false,
    "assist": {
      "enabled": false,
      "host_ipv4": "192.0.2.20",
      "sip_port": 5060
    }
  }
}
```

`192.0.2.20` is a documentation address, **not a working home address**. Replace
it locally. `ami_client_ipv4` must match the source address the PBX observes
for HA. AMI listens on the PBX LAN IP at TCP5038; the firewall and AMI ACL allow
only the selected HA address as `/32`. For remote HA, establish a trusted VPN
route first. The selected integration version uses ordinary AMI TCP and has
no TLS settings.

The username `homeassistant` and a random secret are stored privately in
`.local/ha-ami-secret.json` on the PBX side. Enter them through the HA UI,
without placing them in a package or dashboard. The PBX must not reuse the
phones' SIP passwords for AMI.

Verified permissions are `read=call,dtmf`, `write=system`. Add `originate` only
when `allow_originate:true` is enabled.
**This is not a read-only account:** upstream discovery needs `system`, but
that permission also allows powerful AMI actions, including Hangup and
ModuleLoad. Dashboard buttons do not reduce the account's permissions. Allow
only a trusted HA instance, limit its administrators, and do not expose AMI to
the Internet. Strictly read-only monitoring requires a different architecture
or an upstream modification with a separately verified contract.

For the `201 → 202` button, both PJSIP endpoints and context `home` must exist.
AMI Originate first calls `PJSIP/201`; after answer, Asterisk executes extension
`202` in `home`. It does not ring both phones simultaneously. The general
example does not configure a SIP trunk or external numbers.

## Assist: a separate path through extension 630

After verifying Voice over IP and the audio path, set `assist.enabled:true`
and provide HA's actual IP and port. The PBX must route extension `630` to
HA over SIP/UDP while preserving the original internal extension in From.
The phones can keep using SIP/TCP; these transport settings are independent.

For the HA 2026.10 version covered here, native VoIP uses **Opus**. The Cisco
7945G profile uses G.711. Asterisk needs a **working bidirectional G.711↔Opus
transcoder**. Seeing `opus` in a format list or passing it through in SDP does
not prove transcoding. Check the modules, translation paths, and a live call
with audio. The PBX project verified Wazo codec_opus on Asterisk 22.11.0 locally
on ARM64 and in
[GitHub Actions on amd64](https://github.com/4alvit/cisco-home-telephony/actions/runs/39005213169).
The module loads and both translation paths are available; AMI actions,
extension 630 routing, and TFTP checks also passed. This is not acceptance
of a real call. Repeat module, translation, and live audio checks on the home node.

Add Voice over IP through Devices & services, configure the Assist pipeline,
make a test call to 630, and allow calls for the discovered phone device using
the settings in your HA version. Compare the device number with From, then
select the desired pipeline. The renderer does not assume any actual VoIP entity IDs.

SIP uses the selected HA UDP port, usually 5060. RTP needs media traffic between
the PBX and HA on the ports negotiated in SDP. Opening only the SIP port does
not enable audio. Check the port ranges of the HA and PBX versions actually
running. RTP packets must not go through an HTTP Ingress.

Neither AMI nor this repository requires an HTTP/WebSocket bridge. Cloud
conversation, STT, or TTS providers may receive audio or text. For local
processing, choose local providers and verify the routes.
