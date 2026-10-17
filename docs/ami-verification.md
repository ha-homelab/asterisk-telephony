# AMI verification with safe password handling

[verify_ami.py](../scripts/verify_ami.py) checks this project's specific contract:
Asterisk **22.11.0**, the **ha-assist** PJSIP endpoint, and an AMI account without
`command` permission. It requires only Python 3.10+; no third-party packages
are needed.

Checks use one TCP connection to port **5038**:

1. `Login` with `Events: off`.
2. `CoreSettings` and an exact Asterisk version match.
3. `PJSIPShowEndpoints`: consume every `EndpointList` through
   `EndpointListComplete`, verify the record count, and check for `ha-assist`.
4. `Command: core show version` must return `Error / Permission denied`.
   If the account was accidentally granted CLI access, only this read command
   runs, and the verification fails.
5. `Logoff`; after a failure following successful login, the script also tries
   to end the session.

The script does not make calls, change the PBX, restart services, or scan
addresses. `Events: off` disables the background event subscription; list
responses to the explicit `PJSIPShowEndpoints` request still arrive.

Success means exit code `0` and one JSON object with `ok: true`. A contract
mismatch or network error returns exit code `1` with safe error codes. Output
contains check flags and the endpoint count; it does not print the address,
username, password, AMI replies, or CLI output. Response sizes and waits are
bounded: up to 8 seconds per network operation and an overall 45-second deadline
after reading input. Address resolution depends on the system's DNS resolver.

## Input through stdin

The script accepts exactly three JSON fields and waits for EOF after the object:

```json
{"host":"pbx.example.invalid","username":"ha_monitor","secret":"REPLACE_LOCALLY"}
```

Prepare this JSON in a private `.local/ami-credentials.json` file with mode
`0600`, using a local editor or secret manager. Do not add it to Git. The
password must not appear in a shell command, process arguments, `set -x` output,
a chat message, or shared logs.

If the current machine is the actual source of HA's AMI connections:

```bash
python3 scripts/verify_ami.py < .local/ami-credentials.json
```

Running the probe from a laptop does not verify the running HA instance's ACL
or routing. For HA Container in Kubernetes, run it **inside the existing HA
container** to check its network source, including any SNAT.

```bash
# Replace these nonsecret names with the existing context/namespace/pod/container.
HA_CONTEXT=home-k3s
HA_NAMESPACE=homeassistant
HA_POD=home-assistant-0
HA_CONTAINER=home-assistant

# Only code is copied into the container; the password is not stored there as a file.
kubectl --context "$HA_CONTEXT" -n "$HA_NAMESPACE" exec -i "$HA_POD" \
  -c "$HA_CONTAINER" -- sh -c 'umask 077; cat > /tmp/verify_ami.py' \
  < scripts/verify_ami.py

kubectl --context "$HA_CONTEXT" -n "$HA_NAMESPACE" exec -i "$HA_POD" \
  -c "$HA_CONTAINER" -- python3 /tmp/verify_ami.py \
  < .local/ami-credentials.json

kubectl --context "$HA_CONTEXT" -n "$HA_NAMESPACE" exec "$HA_POD" \
  -c "$HA_CONTAINER" -- rm /tmp/verify_ami.py
```

Do not use `-t`: the secret travels through ordinary stdin, without an interactive
terminal. Do not paste JSON containing a real password into a command, heredoc,
or `echo` example. For automation, pass JSON to the child process's stdin,
not through `--secret` or a Python `-c` argument.

## What the result means

A successful check confirms AMI reachability from the execution location and
the specified permissions. It does not test physical phones' SIP registration,
calls, DTMF, RTP, Opus, the Assist pipeline, HA automations, or recovery after
a disconnection. The presence of `ha-assist` confirms endpoint configuration,
not a live SIP session.

Denying `Command` does not make the account entirely read-only: the required
`system` permission permits other administrative actions. Keep a separate user,
a `/32` source restriction, and the minimum permissions from the
[PBX contract](pbx-contract.md). Do not fix a denial by granting `all`.

## Tests without a PBX

```bash
python3 -m unittest discover -s tests -p test_verify_ami.py -v
```

Tests use an in-process `socketpair` and replace connection creation. They
start no TCP server and do not contact the home HA/PBX or the Internet.
They cover success, denied login, a missing endpoint, unexpected CLI access,
and rejection of CR/LF injection before connecting. Each mock has a bounded
lifetime. Unrelated `ActionID` values are ignored, and a secret returned by
the mock never reaches the output JSON.
