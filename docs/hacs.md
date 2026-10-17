# Installation through HACS

This project now contains the optional **Cisco Phone Control** integration.
Add `https://github.com/ha-homelab/asterisk-telephony` to HACS as an
**Integration** to install it, then follow the [gateway, configuration and
privacy steps](phone-controls.md). It provides handset controls, timers,
weekday alarms and screen notifications.

The independent **Asterisk** integration still comes from TECH7Fox. HACS and
the pinned installer are alternative ways to install that same upstream
component; choose one. Installing Cisco Phone Control does not install or
replace Asterisk or native VoIP/Assist.

## Install the upstream Asterisk integration

1. Install and configure HACS using its [official instructions](https://www.hacs.xyz/docs/use/).
2. In HACS, open the three-dot menu → Custom repositories.
3. Add `https://github.com/TECH7Fox/asterisk-hass-integration` with type
   **Integration**. This is the [supported HACS custom repository procedure](https://www.hacs.xyz/docs/faq/custom_repositories/).
4. Download Asterisk. If the dialog offers a version selector, choose the
   tested `1.0.5`; check the installed `custom_components/asterisk/manifest.json`.
5. Restart HA, then add Asterisk through Devices & services and configure
   AMI according to the [PBX contract](pbx-contract.md).
6. Once the entities appear, fill in the local mapping and install the
   [package and dashboard](configuration.md).

HACS does not check this repository's `upstream.lock.json` during an ordinary
download. Use the installer for an exact match to the pinned commit and SHA-256.
Automatic updates must not bypass acceptance testing: upstream changes can
affect sensor behavior, actions, or HA compatibility. Back up, stage, and test first.
