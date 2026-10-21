"""Hands-free wire contract and readiness using synthetic data only."""
import importlib.util
from pathlib import Path
from types import SimpleNamespace
import unittest
from unittest.mock import AsyncMock

spec = importlib.util.spec_from_file_location("hands_free_api", Path(__file__).resolve().parents[1] /
    "custom_components/cisco_phone_control/hands_free_api.py")
api = importlib.util.module_from_spec(spec)
spec.loader.exec_module(api)


def status(**changes):
    return {"schema_version": 1, "phone": "201", "line": "202", "enabled": True,
            "state": "running", "session_id": "a" * 32, "session_seconds": 300,
            "consecutive_failures": 0, "retry_after_seconds": 0, "error": None, **changes}


def finite(**changes):
    return {"session_id": "a" * 32, "state": "running", "active": True, "error": None,
            "stop_requested": False, "stop_confirmed": False,
            "remaining_seconds": 120,
            "phase": "armed", "phase_reason": "wake_ready", **changes}


class ContractTests(unittest.IsolatedAsyncioTestCase):
    def setUp(self):
        self.client = SimpleNamespace(hands_free_status=AsyncMock(return_value={"status": "ok", "hands_free": status()}),
                                      action=AsyncMock())
        self.coordinator = SimpleNamespace(client=self.client, _closing=False,
            phone=lambda p: {"id": "201", "lines": ["201", "202"]} if p == "201" else None)
        self.flags = {"request_id": "enable-test-001", "enable_microphone": True, "exclusive_media": True}

    async def test_status_keeps_selected_alias_without_inventory_or_action(self):
        value = await api.hands_free(self.coordinator, "201", "201", "status")
        self.assertEqual(value["hands_free"]["line"], "202")
        self.client.hands_free_status.assert_awaited_once_with("201")
        self.client.action.assert_not_awaited()

    async def test_enable_requires_both_literal_flags_and_no_unloading(self):
        for name in ("enable_microphone", "exclusive_media"):
            for value in (False, 1, "true", None):
                with self.subTest(name=name, value=value), self.assertRaises(api.HandsFreeError):
                    await api.hands_free(self.coordinator, "201", "202", "enable", **{**self.flags, name: value})
        self.coordinator._closing = True
        with self.assertRaises(api.HandsFreeError):
            await api.hands_free(self.coordinator, "201", "202", "enable", **self.flags)
        self.client.action.assert_not_awaited()

    async def test_enable_sends_only_explicit_persistent_flags_once(self):
        self.client.action.return_value = {"status": "accepted", "action_id": self.flags["request_id"], "hands_free": status()}
        await api.hands_free(self.coordinator, "201", "202", "enable", **self.flags)
        self.client.action.assert_awaited_once_with("202", "voice_hands_free_enable",
            {"enable_microphone": True, "exclusive_media": True}, "enable-test-001")

    async def test_disable_needs_no_session_even_between_sessions_or_unloading(self):
        self.coordinator._closing = True
        self.client.action.return_value = {"status": "accepted", "action_id": "disable-test-001",
            "hands_free": status(enabled=False, state="stopping")}
        result = await api.hands_free(self.coordinator, "201", "201", "disable", request_id="disable-test-001")
        self.assertFalse(result["hands_free"]["enabled"])
        self.assertEqual(result["hands_free"]["state"], "stopping")
        self.client.action.assert_awaited_once_with("201", "voice_hands_free_disable", {}, "disable-test-001")

    async def test_invalid_request_fields_and_ownership_never_dispatch(self):
        for primary, line, operation, options in (
            ("203", "201", "status", {}), ("201", "203", "status", {}),
            ("201", "201", "disable", {"request_id": "disable-test-001", "session_id": "a"*32}),
            ("201", "201", "enable", {**self.flags, "attended": True}),
            ("201", "201", "status", {"request_id": "status-test-001"}),
            ("201", "201", "enable", {**self.flags, "dry_run": 1}),
            ("201", "201", "disable", {"request_id": "short"})):
            with self.subTest(operation=operation, options=options), self.assertRaises(api.HandsFreeError):
                await api.hands_free(self.coordinator, primary, line, operation, **options)
        self.client.action.assert_not_awaited()
        self.client.hands_free_status.assert_not_awaited()

    async def test_unknown_mutation_outcome_is_not_retried_or_leaked(self):
        self.client.action.side_effect = RuntimeError("private-server-secret")
        with self.assertRaises(api.HandsFreeError) as error:
            await api.hands_free(self.coordinator, "201", "201", "enable", **self.flags)
        self.assertNotIn("private-server-secret", str(error.exception))
        self.client.action.assert_awaited_once()
        self.client.hands_free_status.assert_not_awaited()

    def test_fixed_response_shape_and_numeric_bounds(self):
        changes = ({"schema_version": True}, {"phone": "203"}, {"line": "204"},
            {"enabled": 1}, {"state": "ready"}, {"session_id": "bad"}, {"session_id": None},
            {"session_seconds": True}, {"session_seconds": 0}, {"session_seconds": 301},
            {"session_seconds": float("inf")}, {"consecutive_failures": True},
            {"consecutive_failures": -1}, {"consecutive_failures": 4},
            {"retry_after_seconds": float("nan")}, {"retry_after_seconds": 11},
            {"retry_after_seconds": 1}, {"error": "private-server-secret"},
            {"transcript": "private"}, {"state": "disabled"})
        for change in changes:
            with self.subTest(change=change), self.assertRaises(api.HandsFreeError):
                api.validate_response({"status": "ok", "hands_free": status(**change)}, "201", ["201", "202"], "status")
        for duration in (.1, 30, 300):
            value = status(state="disabled", enabled=False, session_id=None, session_seconds=duration)
            self.assertEqual(api.validate_response({"status": "ok", "hands_free": value}, "201", ["201", "202"], "status")["hands_free"], value)
        api.validate_response({"status": "ok", "hands_free": status(state="backoff", retry_after_seconds=10)}, "201", ["201", "202"], "status")

    def test_dry_run_cannot_be_confused_with_enabled_or_normal_status(self):
        dry = {"schema_version": 1, "phone": "201", "line": "201", "valid": True,
               "network_actions": 0, "microphone_started": False, "duration_seconds": 300}
        envelope = {"status": "accepted", "action_id": "enable-test-001", "hands_free": dry}
        api.validate_response(envelope, "201", ["201", "202"], "enable", "enable-test-001", True)
        for change in ({"duration_seconds": 29}, {"network_actions": False}, {"microphone_started": True}, {"enabled": False}):
            with self.subTest(change=change), self.assertRaises(api.HandsFreeError):
                api.validate_response({**envelope, "hands_free": {**dry, **change}}, "201", ["201", "202"], "enable", "enable-test-001", True)
        with self.assertRaises(api.HandsFreeError):
            api.validate_response(envelope, "201", ["201", "202"], "enable", "different-request", True)
        with self.assertRaises(api.HandsFreeError):
            api.validate_response({"status": "ok", "hands_free": dry}, "201", ["201", "202"], "status")

    def test_ready_requires_exact_nonnull_running_session_without_stop_or_error(self):
        self.assertEqual(api.actual_status(status(), finite()), "ready")
        for change in ({"session_id": "b"*32}, {"session_id": None}, {"state": "starting"},
            {"active": False}, {"stop_requested": True}, {"stop_confirmed": True},
            {"error": "voice_stop_unconfirmed"}, {"phase_reason": None}, {"phase": None},
            {"phase": "preparing"}):
            with self.subTest(change=change):
                self.assertNotEqual(api.actual_status(status(), finite(**change)), "ready")
        self.assertIsNone(api.actual_status(status(), None))
        self.assertIsNone(api.actual_status(status(session_id=None), finite()))

    def test_desired_intent_is_independent_of_actual_state(self):
        self.assertEqual(api.actual_status(status(state="waiting_idle", session_id=None)), "waiting_for_idle")
        self.assertEqual(api.actual_status(status(state="stopping", enabled=False), finite()), "stopping")
        self.assertEqual(api.actual_status(status(state="faulted", error="hands_free_retry_exhausted")), "failed")
        self.assertEqual(api.actual_status(status(state="faulted", error="hands_free_recovery_required")), "recovery_required")
        for phase in ("listening", "thinking", "speaking"):
            self.assertEqual(api.actual_status(status(), finite(phase=phase)), phase)
        self.assertEqual(api.actual_status(status(state="disabled", enabled=False)), "off")

    def test_unknown_child_during_start_or_stop_is_unavailable(self):
        for state in ("starting", "running", "stopping"):
            with self.subTest(state=state):
                self.assertIsNone(api.actual_status(status(state=state), None))
                self.assertIsNone(api.actual_status(status(state=state), finite(session_id="b"*32)))

    def test_backoff_retains_transient_error_without_becoming_terminal_failure(self):
        value = status(state="backoff", session_id=None, consecutive_failures=1,
                       retry_after_seconds=5, error="hands_free_start_failed")
        validated = api.validate_response({"status": "ok", "hands_free": value}, "201", ["201", "202"], "status")
        self.assertEqual(api.actual_status(validated["hands_free"]), "retry_wait")
        self.assertEqual(validated["hands_free"]["error"], "hands_free_start_failed")
        value.update(state="faulted", retry_after_seconds=0, consecutive_failures=3,
                     error="hands_free_retry_exhausted")
        self.assertEqual(api.actual_status(value), "failed")

    def test_exhausted_finite_lease_never_reports_ready(self):
        self.assertEqual(api.actual_status(status(), finite(remaining_seconds=0)), "running_not_ready")
        self.assertEqual(api.actual_status(status(), finite(remaining_seconds=.1)), "ready")

    def test_huge_integer_seconds_are_rejected_without_overflow(self):
        for field in ("session_seconds", "retry_after_seconds"):
            for value in (10**10000, -(10**10000)):
                with self.subTest(field=field), self.assertRaises(api.HandsFreeError):
                    api.validate_response({"status": "ok", "hands_free": status(**{field: value})},
                                          "201", ["201", "202"], "status")


if __name__ == "__main__":
    unittest.main()
