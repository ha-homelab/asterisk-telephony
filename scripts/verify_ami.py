#!/usr/bin/env python3
"""Bounded AMI health probe; credentials are JSON on stdin only.

Input: {"host": "PBX address", "username": "AMI account", "secret": "..."}
Only Login, CoreSettings, PJSIPShowEndpoints, read-only Command(core show
version), and Logoff are sent. The Command action MUST be permission-denied.
No calls, configuration changes, raw AMI replies, or credentials are printed.

Run with the script already copied into the HA container, then stream the
credential JSON through `kubectl exec -i ... -- python3 /tmp/verify_ami.py`.
Do not put credentials in command arguments or environment diagnostics.
"""

import json
import re
import socket
import sys
import time


EXPECTED_VERSION = "22.11.0"
EXPECTED_ENDPOINT = "ha-assist"
PORT = 5038
IO_TIMEOUT = 8.0
TOTAL_TIMEOUT = 45.0
MAX_INPUT = 65536
MAX_LINE = 65536
MAX_RECEIVED = 2 * 1024 * 1024
MAX_FRAMES = 4096


class ProbeError(Exception):
    """A static, safe-to-print error code, never a remote message."""


class AMI:
    def __init__(self, sock, deadline):
        self.sock = sock
        self.deadline = deadline
        self.buffer = bytearray()
        self.received = 0
        self.frames = 0

    def remaining(self):
        remaining = self.deadline - time.monotonic()
        if remaining <= 0:
            raise ProbeError("overall_timeout")
        return min(IO_TIMEOUT, remaining)

    def line(self):
        while True:
            index = self.buffer.find(b"\n")
            if index >= 0:
                if index > MAX_LINE:
                    raise ProbeError("response_line_too_large")
                line = bytes(self.buffer[:index]).rstrip(b"\r")
                del self.buffer[: index + 1]
                return line.decode("utf-8", errors="replace")
            if len(self.buffer) > MAX_LINE:
                raise ProbeError("response_line_too_large")
            self.sock.settimeout(self.remaining())
            chunk = self.sock.recv(8192)
            if not chunk:
                raise ProbeError("connection_closed")
            self.received += len(chunk)
            if self.received > MAX_RECEIVED:
                raise ProbeError("response_total_too_large")
            self.buffer.extend(chunk)

    def frame(self):
        headers = {}
        while True:
            line = self.line()
            if not line:
                if not headers:
                    continue
                self.frames += 1
                if self.frames > MAX_FRAMES:
                    raise ProbeError("too_many_response_frames")
                return headers
            key, separator, value = line.partition(":")
            if not separator:
                raise ProbeError("invalid_response_frame")
            # Output headers may repeat; only the protocol fields are used.
            headers.setdefault(key.strip().lower(), value.lstrip())

    def send(self, action, action_id, **headers):
        fields = {"Action": action, "ActionID": action_id, **headers}
        if any("\r" in value or "\n" in value or "\x00" in value
               for value in fields.values()):
            raise ProbeError("invalid_input")
        payload = "".join(f"{key}: {value}\r\n" for key, value in fields.items())
        self.sock.settimeout(self.remaining())
        self.sock.sendall((payload + "\r\n").encode("utf-8"))

    def response(self, action_id):
        while True:
            frame = self.frame()
            if frame.get("actionid") == action_id and "response" in frame:
                return frame

    def action(self, action, action_id, **headers):
        self.send(action, action_id, **headers)
        return self.response(action_id)


def credentials():
    raw = sys.stdin.buffer.read(MAX_INPUT + 1)
    if len(raw) > MAX_INPUT:
        raise ProbeError("invalid_input")
    try:
        values = json.loads(raw)
    except (ValueError, UnicodeError):
        raise ProbeError("invalid_input") from None
    if not isinstance(values, dict) or set(values) != {"host", "username", "secret"}:
        raise ProbeError("invalid_input")
    for name in ("host", "username", "secret"):
        value = values[name]
        if (not isinstance(value, str) or not value or len(value) > 4096
                or any(character in value for character in "\r\n\x00")):
            raise ProbeError("invalid_input")
    if not re.fullmatch(r"[A-Za-z0-9_.:%-]{1,253}", values["host"]):
        raise ProbeError("invalid_input")
    return values


def error_code(error):
    if isinstance(error, ProbeError):
        return error.args[0]
    if isinstance(error, (socket.timeout, TimeoutError)):
        return "network_timeout"
    if isinstance(error, OSError):
        return "network_error"
    return "probe_error"


def main():
    summary = {
        "ok": False,
        "checks": {
            "ami_greeting": False,
            "login": False,
            "core_settings": False,
            "asterisk_version_matches_22_11_0": False,
            "endpoint_list_complete": False,
            "ha_assist_endpoint_present": False,
            "cli_command_permission_denied": False,
            "logoff": False,
        },
        "endpoint_count": 0,
        "errors": [],
    }
    stage = "input"
    sock = None
    ami = None
    logged_in = False
    try:
        values = credentials()
        deadline = time.monotonic() + TOTAL_TIMEOUT
        stage = "connect"
        sock = socket.create_connection((values["host"], PORT), timeout=IO_TIMEOUT)
        ami = AMI(sock, deadline)
        stage = "greeting"
        if not ami.line().startswith("Asterisk Call Manager/"):
            raise ProbeError("invalid_ami_greeting")
        summary["checks"]["ami_greeting"] = True

        stage = "login"
        response = ami.action("Login", "probe-login", Username=values["username"],
                              Secret=values["secret"], Events="off")
        if response.get("response", "").lower() != "success":
            raise ProbeError("login_rejected")
        logged_in = True
        summary["checks"]["login"] = True

        stage = "core_settings"
        response = ami.action("CoreSettings", "probe-core")
        if response.get("response", "").lower() != "success":
            raise ProbeError("core_settings_rejected")
        summary["checks"]["core_settings"] = True
        summary["checks"]["asterisk_version_matches_22_11_0"] = (
            response.get("asteriskversion") == EXPECTED_VERSION
        )
        if not summary["checks"]["asterisk_version_matches_22_11_0"]:
            summary["errors"].append({"stage": stage, "code": "unexpected_version"})

        stage = "endpoints"
        response = ami.action("PJSIPShowEndpoints", "probe-endpoints")
        if (response.get("response", "").lower() != "success"
                or response.get("eventlist", "").lower() != "start"):
            raise ProbeError("endpoint_list_rejected")
        endpoint_names = set()
        endpoint_events = 0
        while True:
            frame = ami.frame()
            if frame.get("actionid") != "probe-endpoints":
                continue
            event = frame.get("event", "").lower()
            if event == "endpointlist":
                endpoint_events += 1
                endpoint_names.add(frame.get("objectname", ""))
            elif event == "endpointlistcomplete":
                if frame.get("eventlist", "").lower() != "complete":
                    raise ProbeError("invalid_endpoint_list_completion")
                count = frame.get("listitems", "")
                if not count.isdigit() or int(count) != endpoint_events:
                    raise ProbeError("endpoint_list_count_mismatch")
                summary["endpoint_count"] = endpoint_events
                summary["checks"]["endpoint_list_complete"] = True
                break
            elif frame.get("response", "").lower() == "error":
                raise ProbeError("endpoint_list_failed")
        summary["checks"]["ha_assist_endpoint_present"] = EXPECTED_ENDPOINT in endpoint_names
        if not summary["checks"]["ha_assist_endpoint_present"]:
            summary["errors"].append({"stage": stage, "code": "expected_endpoint_missing"})

        stage = "cli_permission"
        response = ami.action("Command", "probe-command", Command="core show version")
        summary["checks"]["cli_command_permission_denied"] = (
            response.get("response", "").lower() == "error"
            and response.get("message", "").strip().lower() == "permission denied"
        )
        if not summary["checks"]["cli_command_permission_denied"]:
            summary["errors"].append({"stage": stage, "code": "cli_permission_not_denied"})
    except Exception as error:
        summary["errors"].append({"stage": stage, "code": error_code(error)})
    finally:
        if ami is not None and logged_in:
            try:
                response = ami.action("Logoff", "probe-logoff")
                summary["checks"]["logoff"] = response.get("response", "").lower() == "goodbye"
                if not summary["checks"]["logoff"]:
                    summary["errors"].append({"stage": "logoff", "code": "logoff_not_confirmed"})
            except Exception as error:
                summary["errors"].append({"stage": "logoff", "code": error_code(error)})
        if sock is not None:
            sock.close()
    summary["ok"] = all(summary["checks"].values()) and not summary["errors"]
    print(json.dumps(summary, sort_keys=True))
    return 0 if summary["ok"] else 1


if __name__ == "__main__":
    sys.exit(main())
