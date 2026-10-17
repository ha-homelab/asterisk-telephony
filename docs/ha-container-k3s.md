# Home Assistant Container in k3s

These instructions assume an **existing** Home Assistant Container with
persistent `/config` storage. Installing the integration does not require HA OS,
Supervisor, or a new cluster. Asterisk is a custom integration running inside
HA. Official VoIP is a separate built-in integration, not an add-on.

## Preparation

Record the namespace, Deployment, container name, PVC, UID/GID, and the IP
address from which HA actually reaches the PBX in private local notes. Do not
commit these values. Check that the Pod does not store `/config` in `emptyDir`
and that backups include `.storage`. Use one replica per HA configuration;
avoid two processes writing to the same configuration during an update.
`Recreate` is suitable for an existing Deployment, but check it against your
runbook first.

AMI requires an outbound TCP connection from HA to PBX:5038. Do not publish
this port through Ingress, NodePort, or the router. With ordinary pod networking,
the source IP may depend on SNAT: the PBX's /32 ACL must match the **observed**
client address.

For VoIP, `hostNetwork: true` on a fixed LAN node with
`dnsPolicy: ClusterFirstWithHostNet` gives SIP and RTP predictable LAN addresses.
This is the design used here, not a requirement for every HA installation.
`NetworkPolicy` does not replace a host firewall for hostNetwork workloads.

Check whether the SIP port is already in use. If Asterisk and HA share a node,
two processes cannot bind the same IP/UDP port. Use different LAN addresses
or nodes, or change the VoIP port in its settings and update `assist.sip_port`
on the PBX to match. HA [documents how to change the VoIP port](https://www.home-assistant.io/integrations/voip/).

## Delivering the files

The simplest approach is to run the installer on a host where the config PVC
or volume is accessible as a directory, under HA's owner account. If you can
only access it through the Pod, prepare staging locally first, make a backup,
and use your usual method to copy files into the PVC.

Example for a selected running Pod, after verifying the variables locally:

```bash
set -eu
# Set variables locally. Do not paste a production kubeconfig here.
: "${HA_KUBE_CONTEXT:?set context}" "${HA_NAMESPACE:?set namespace}" "${HA_POD:?set pod}" "${HA_CONTAINER:?set container}"
kubectl --context "$HA_KUBE_CONTEXT" -n "$HA_NAMESPACE" exec "$HA_POD" -c "$HA_CONTAINER" -- \
  test -f /config/configuration.yaml
# These checks must pass. Stop if the component directory exists or a path is a symlink.
kubectl --context "$HA_KUBE_CONTEXT" -n "$HA_NAMESPACE" exec "$HA_POD" -c "$HA_CONTAINER" -- \
  test ! -L /config/custom_components
kubectl --context "$HA_KUBE_CONTEXT" -n "$HA_NAMESPACE" exec "$HA_POD" -c "$HA_CONTAINER" -- \
  test ! -e /config/custom_components/asterisk
kubectl --context "$HA_KUBE_CONTEXT" -n "$HA_NAMESPACE" exec "$HA_POD" -c "$HA_CONTAINER" -- \
  test ! -L /config/custom_components/asterisk
kubectl --context "$HA_KUBE_CONTEXT" -n "$HA_NAMESPACE" exec "$HA_POD" -c "$HA_CONTAINER" -- \
  mkdir -p /config/custom_components
kubectl --context "$HA_KUBE_CONTEXT" cp .local/upstream-stage/custom_components/asterisk \
  "$HA_NAMESPACE/$HA_POD:/config/custom_components/asterisk" -c "$HA_CONTAINER"
```

`kubectl cp` requires `tar` inside the container. If it is absent, use a temporary
helper Pod with the same PVC under your access policy, rather than changing
the permanent HA image just to copy files. When replacing an existing component,
stop HA first and work through the PVC or a maintenance Pod to avoid mixing versions.

Check that there is only one directory level:
`/config/custom_components/asterisk/manifest.json`, not a nested
`asterisk/asterisk/manifest.json`. Check UID/GID and file readability from inside
the container. Install the YAML using [configuration.md](configuration.md), then run:

```bash
: "${HA_KUBE_CONTEXT:?set context}" "${HA_NAMESPACE:?set namespace}" "${HA_POD:?set pod}" "${HA_CONTAINER:?set container}"
kubectl --context "$HA_KUBE_CONTEXT" -n "$HA_NAMESPACE" exec "$HA_POD" -c "$HA_CONTAINER" -- \
  python -m homeassistant --script check_config --config /config
```

After a successful check, restart **only your HA Deployment** using its normal
procedure and wait for Ready. HA readiness does not confirm phone registration
or Assist audio: perform [acceptance testing](acceptance.md).

## Assist in Container

Add **Voice over IP** and the speech providers you need. For a fully local
pipeline, use separately deployed Wyoming STT/TTS services if they already
exist at home. This repository does not create them or subscribe you to a
cloud service. Select the language, STT, conversation agent, and TTS in the
Assist settings. Test the pipeline in the HA UI first, then over SIP.
