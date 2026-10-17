import importlib.util
import io
import json
from pathlib import Path
import socket
import threading
import unittest
from unittest.mock import patch


ROOT = Path(__file__).resolve().parents[1]
SPEC = importlib.util.spec_from_file_location("verify_ami", ROOT / "scripts/verify_ami.py")
probe = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(probe)

SECRET = "synthetic-fixture-secret-never-print"
USERNAME = "synthetic-fixture-user-never-print"
HOST = "pbx.example.invalid"


class FakeAMI(threading.Thread):
    """An in-process Unix socket pair; no TCP listener or external connection."""

    def __init__(self, sock, scenario):
        super().__init__(daemon=True)
        self.sock = sock
        self.scenario = scenario
        self.actions = []
        self.errors = []

    def send(self, **headers):
        payload = ("".join(f"{key}: {value}\r\n" for key, value in headers.items())
                   + "\r\n").encode()
        # Split a header across writes to exercise incremental protocol framing.
        self.sock.sendall(payload[:5])
        self.sock.sendall(payload[5:])

    def run(self):
        try:
            with self.sock, self.sock.makefile("rb") as stream:
                self.sock.settimeout(2)
                self.sock.sendall(b"Asterisk Call Manager/11.0.0\r\n")
                while True:
                    headers = {}
                    while True:
                        line = stream.readline(65537)
                        if not line:
                            return
                        if line == b"\r\n":
                            break
                        key, value = line.decode().rstrip("\r\n").split(":", 1)
                        headers[key] = value.lstrip()
                    self.actions.append(headers)
                    action = headers["Action"]
                    action_id = headers["ActionID"]
                    if action == "Login":
                        if self.scenario == "denied_login":
                            self.send(Response="Error", ActionID=action_id, Message=SECRET)
                            return
                        self.send(Response="Success", ActionID=action_id)
                    elif action == "CoreSettings":
                        # A response belonging to another ActionID must be ignored.
                        self.send(Response="Error", ActionID="unrelated", Message=SECRET)
                        self.send(Response="Success", ActionID=action_id, AsteriskVersion="22.11.0")
                    elif action == "PJSIPShowEndpoints":
                        self.send(Response="Success", ActionID=action_id, EventList="start")
                        self.send(Event="EndpointListComplete", ActionID="unrelated",
                                  EventList="Complete", ListItems="0")
                        endpoint = "other-endpoint" if self.scenario == "missing_endpoint" else "ha-assist"
                        self.send(Event="EndpointList", ActionID=action_id,
                                  ObjectType="endpoint", ObjectName=endpoint)
                        self.send(Event="EndpointListComplete", ActionID=action_id,
                                  EventList="Complete", ListItems="1")
                    elif action == "Command":
                        if self.scenario == "cli_allowed":
                            self.send(Response="Success", ActionID=action_id, Output=SECRET)
                        else:
                            self.send(Response="Error", ActionID=action_id, Message="Permission denied")
                    elif action == "Logoff":
                        self.send(Response="Goodbye", ActionID=action_id)
                        return
                    else:
                        raise AssertionError("Unexpected AMI action")
        except Exception as error:
            self.errors.append(error)


class AMIProbeTests(unittest.TestCase):
    def run_scenario(self, scenario):
        client, server = socket.socketpair()
        fixture = FakeAMI(server, scenario)
        fixture.start()
        stdin = io.TextIOWrapper(io.BytesIO(json.dumps({
            "host": HOST, "username": USERNAME, "secret": SECRET,
        }).encode()))
        stdout = io.StringIO()
        try:
            with patch.object(probe.socket, "create_connection", return_value=client) as connect, \
                    patch.object(probe.sys, "stdin", stdin), \
                    patch.object(probe.sys, "stdout", stdout), \
                    patch.object(probe, "IO_TIMEOUT", 1.0), \
                    patch.object(probe, "TOTAL_TIMEOUT", 4.0):
                exit_code = probe.main()
                connect.assert_called_once_with((HOST, 5038), timeout=1.0)
        finally:
            client.close()
            fixture.join(timeout=3)
            server.close()
            stdin.close()
        self.assertFalse(fixture.is_alive(), "Mock protocol exceeded its bounded deadline")
        self.assertEqual(fixture.errors, [])
        output = stdout.getvalue()
        for sensitive in (SECRET, USERNAME, HOST):
            self.assertNotIn(sensitive, output)
        self.assertEqual(output.count("\n"), 1)
        return exit_code, json.loads(output), fixture.actions

    def test_success_consumes_complete_list_and_logs_off(self):
        exit_code, result, actions = self.run_scenario("success")
        self.assertEqual(exit_code, 0)
        self.assertTrue(result["ok"])
        self.assertTrue(all(result["checks"].values()))
        self.assertEqual(result["endpoint_count"], 1)
        self.assertEqual(result["errors"], [])
        self.assertEqual([action["Action"] for action in actions],
                         ["Login", "CoreSettings", "PJSIPShowEndpoints", "Command", "Logoff"])
        self.assertEqual(actions[0]["Events"], "off")
        self.assertEqual(actions[3]["Command"], "core show version")

    def test_denied_login_stops_before_any_privileged_action(self):
        exit_code, result, actions = self.run_scenario("denied_login")
        self.assertEqual(exit_code, 1)
        self.assertFalse(result["ok"])
        self.assertEqual(result["errors"], [{"stage": "login", "code": "login_rejected"}])
        self.assertEqual([action["Action"] for action in actions], ["Login"])

    def test_missing_endpoint_fails_after_complete_list_and_logs_off(self):
        exit_code, result, actions = self.run_scenario("missing_endpoint")
        self.assertEqual(exit_code, 1)
        self.assertFalse(result["ok"])
        self.assertTrue(result["checks"]["endpoint_list_complete"])
        self.assertFalse(result["checks"]["ha_assist_endpoint_present"])
        self.assertIn({"stage": "endpoints", "code": "expected_endpoint_missing"}, result["errors"])
        self.assertTrue(result["checks"]["cli_command_permission_denied"])
        self.assertEqual(actions[-1]["Action"], "Logoff")

    def test_unexpected_cli_access_fails_without_echoing_command_output(self):
        exit_code, result, actions = self.run_scenario("cli_allowed")
        self.assertEqual(exit_code, 1)
        self.assertFalse(result["ok"])
        self.assertFalse(result["checks"]["cli_command_permission_denied"])
        self.assertIn({"stage": "cli_permission", "code": "cli_permission_not_denied"}, result["errors"])
        self.assertTrue(result["checks"]["logoff"])
        self.assertEqual(actions[-1]["Action"], "Logoff")

    def test_header_injection_rejected_before_connect(self):
        stdin = io.TextIOWrapper(io.BytesIO(json.dumps({
            "host": HOST, "username": USERNAME, "secret": SECRET + "\r\nAction: Originate",
        }).encode()))
        stdout = io.StringIO()
        with stdin, patch.object(probe.socket, "create_connection") as connect, \
                patch.object(probe.sys, "stdin", stdin), patch.object(probe.sys, "stdout", stdout):
            self.assertEqual(probe.main(), 1)
            connect.assert_not_called()
        result = json.loads(stdout.getvalue())
        self.assertEqual(result["errors"], [{"stage": "input", "code": "invalid_input"}])
        self.assertNotIn(SECRET, stdout.getvalue())


if __name__ == "__main__":
    unittest.main()
