"""Voice metadata and explicit controls with fake clients; no HA or devices needed."""
import asyncio
import importlib.util
import math
from pathlib import Path
from types import SimpleNamespace
import unittest
from unittest.mock import AsyncMock

spec = importlib.util.spec_from_file_location("voice_api", Path(__file__).resolve().parents[1] /
                                            "custom_components/cisco_phone_control/voice_api.py")
voice_api = importlib.util.module_from_spec(spec)
spec.loader.exec_module(voice_api)


def status(**values):
    return {"schema_version": 1, "phone": "201", "line": "202", "session_id": "a" * 32,
            "state": "running", "active": True, "remaining_seconds": 20,
            "stop_requested": False, "stop_confirmed": False, "error": None, **values}


def extended_status(**values):
    return status(remaining_seconds=values.pop("remaining_seconds", 299.5),
                  **{"phase": "armed", "phase_reason": "wake_ready", "turns_completed": 2,
                     "mode": "wake", "session_seconds": 300, **values})


class VoiceTests(unittest.IsolatedAsyncioTestCase):
    def setUp(self):
        self.client = SimpleNamespace(voice_status=AsyncMock(return_value={"status": "ok", "voice": status()}),
                                      action=AsyncMock(), snapshot=AsyncMock(side_effect=AssertionError("no AMI")))
        self.coordinator = SimpleNamespace(client=self.client, phone=lambda primary:
                                          {"id": "201", "lines": ["201", "202"]} if primary == "201" else None,
                                          perform=AsyncMock(side_effect=AssertionError("no action lock")))
        self.consent = {"request_id": "voice-test-001", "attended": True,
                        "exclusive_media": True, "enable_microphone": True}

    async def test_status_uses_saved_alias_ownership_without_inventory(self):
        result = await voice_api.voice(self.coordinator, "201", "201", "status")
        self.assertEqual(result["voice"]["line"], "202")  # original Start alias
        self.client.voice_status.assert_awaited_once_with("201")
        self.client.snapshot.assert_not_awaited()
        self.coordinator.perform.assert_not_awaited()

    async def test_start_requires_literal_and_complete_consent(self):
        for flag in ("attended", "exclusive_media", "enable_microphone"):
            for value in (None, False, 1, "true", "yes"):
                with self.subTest(flag=flag, value=value):
                    options = {**self.consent, flag: value}
                    with self.assertRaises(voice_api.VoiceError):
                        await voice_api.voice(self.coordinator, "201", "202", "start", **options)
        self.client.action.assert_not_awaited()
        self.client.action.return_value = {"status": "accepted", "action_id": "voice-test-001", "voice": status()}
        await voice_api.voice(self.coordinator, "201", "202", "start", **self.consent)
        self.client.action.assert_awaited_once_with("202", "voice_start",
                            {"attended": True, "exclusive_media": True, "enable_microphone": True}, "voice-test-001")

    async def test_stop_uses_exact_session_and_no_refresh(self):
        self.client.action.return_value = {"status": "accepted", "action_id": "stop-test-001",
                                          "voice": status(state="stopping", stop_requested=True)}
        result = await voice_api.voice(self.coordinator, "201", "201", "stop", request_id="stop-test-001", session_id="a" * 32)
        self.assertFalse(result["voice"]["stop_confirmed"])
        self.client.action.assert_awaited_once_with("201", "voice_stop", {"session_id": "a" * 32}, "stop-test-001")
        self.client.voice_status.assert_not_awaited()
        self.client.snapshot.assert_not_awaited()
        self.client.action.return_value["voice"]["session_id"] = "b" * 32
        with self.assertRaises(voice_api.VoiceError):
            await voice_api.voice(self.coordinator, "201", "201", "stop", request_id="stop-test-001", session_id="a" * 32)

    async def test_strict_fixed_metadata_and_identity(self):
        bad = [{"schema_version": True}, {"phone": "203"}, {"line": "204"}, {"state": []},
               {"session_id": "abc"}, {"active": 1}, {"stop_requested": "no"}, {"stop_confirmed": 0},
               {"remaining_seconds": True}, {"remaining_seconds": float("nan")},
               {"remaining_seconds": float("inf")}, {"remaining_seconds": 10 ** 1000},
               {"remaining_seconds": -1}, {"remaining_seconds": 301}, {"error": "private secret"},
               {"error": []}, {"state": "idle"}, {"active": False}, {"transcript": "private secret"}]
        for change in bad:
            with self.subTest(change=change):
                self.client.voice_status.return_value = {"status": "ok", "voice": status(**change)}
                with self.assertRaises(voice_api.VoiceError) as raised:
                    await voice_api.voice(self.coordinator, "201", "201", "status")
                self.assertNotIn("private secret", str(raised.exception))
        self.client.voice_status.return_value = {"status": "ok", "voice": status()}
        del self.client.voice_status.return_value["voice"]["error"]
        with self.assertRaises(voice_api.VoiceError):
            await voice_api.voice(self.coordinator, "201", "201", "status")

    async def test_extended_metadata_survives_status_start_and_exact_stop(self):
        cases = (
            ("status", {}, extended_status()),
            ("start", self.consent, extended_status(state="starting", phase="preparing",
                                                  phase_reason="model_warmup", turns_completed=0)),
            ("stop", {"request_id": "stop-test-001", "session_id": "a" * 32},
             extended_status(state="stopped", active=False, remaining_seconds=0,
                             stop_requested=True, stop_confirmed=True, phase="stopped",
                             phase_reason="explicit_stop")),
        )
        for operation, options, payload in cases:
            with self.subTest(operation=operation):
                self.client.voice_status.reset_mock()
                self.client.action.reset_mock()
                envelope = {"status": "ok" if operation == "status" else "accepted", "voice": payload}
                if operation != "status":
                    envelope["action_id"] = options["request_id"]
                self.client.voice_status.return_value = envelope
                self.client.action.return_value = envelope
                result = await voice_api.voice(self.coordinator, "201", "201", operation, **options)
                self.assertEqual(result, envelope)
                self.assertIsNot(result["voice"], payload)
                if operation == "status":
                    self.client.voice_status.assert_awaited_once_with("201")
                    self.client.action.assert_not_awaited()
                else:
                    values = {key: value for key, value in options.items() if key != "request_id"}
                    self.client.action.assert_awaited_once_with(
                        "201", "voice_" + operation, values, options["request_id"])
                    self.client.voice_status.assert_not_awaited()
        self.client.snapshot.assert_not_awaited()
        self.coordinator.perform.assert_not_awaited()

    async def test_added_fields_are_independently_optional_without_fabricated_metadata(self):
        additions = {"phase": None, "phase_reason": None, "turns_completed": 0,
                     "mode": "manual", "session_seconds": .5}
        for optional in ({}, *({key: value} for key, value in additions.items())):
            with self.subTest(optional=optional):
                payload = status(**optional)
                self.client.voice_status.return_value = {"status": "ok", "voice": payload}
                result = await voice_api.voice(self.coordinator, "201", "201", "status")
                self.assertEqual(result["voice"], payload)
                self.assertEqual(set(result["voice"]), set(payload))
        idle = extended_status(state="idle", session_id=None, active=False,
                               remaining_seconds=0, phase=None, phase_reason=None, turns_completed=0)
        self.client.voice_status.return_value = {"status": "ok", "voice": idle}
        self.assertEqual((await voice_api.voice(self.coordinator, "201", "201", "status"))["voice"], idle)

    async def test_extended_numeric_boundaries_and_all_documented_phases(self):
        changes = ([{"remaining_seconds": value} for value in (0, .5, 30, 180, 300)]
                   + [{"session_seconds": value} for value in (.001, 30, 180, 300)]
                   + [{"turns_completed": value} for value in (0, 1, 10000)]
                   + [{"phase": value} for value in
                      (None, "preparing", "armed", "listening", "thinking", "speaking", "stopped", "error")]
                   + [{"mode": value} for value in ("manual", "wake")])
        for change in changes:
            with self.subTest(change=change):
                payload = extended_status(**change)
                self.client.voice_status.return_value = {"status": "ok", "voice": payload}
                result = await voice_api.voice(self.coordinator, "201", "201", "status")
                self.assertEqual(result["voice"], payload)

    async def test_extended_fields_reject_malformed_types_and_out_of_range_values(self):
        invalid = {
            "phase": (True, 1, [], {}, "ready", "synthetic-private-marker"),
            "mode": (None, True, 1, [], {}, "onnx", "synthetic-private-marker"),
            "turns_completed": (None, True, 1.0, "1", [], {}, -1, 10001, 10 ** 400),
            "session_seconds": (None, True, "300", [], {}, 0, -1, math.nextafter(300, math.inf),
                                10 ** 400, float("nan"), float("inf"), -float("inf")),
            "remaining_seconds": (None, True, "300", [], {}, -.001, math.nextafter(300, math.inf),
                                  10 ** 400, float("nan"), float("inf"), -float("inf")),
        }
        for name, values in invalid.items():
            for value in values:
                with self.subTest(field=name, value=value):
                    self.client.voice_status.return_value = {"status": "ok", "voice": extended_status(**{name: value})}
                    with self.assertRaises(voice_api.VoiceError) as raised:
                        await voice_api.voice(self.coordinator, "201", "201", "status")
                    self.assertNotIn("synthetic-private-marker", str(raised.exception))

    async def test_phase_reason_uses_fixed_codes_and_sanitizes_without_mutating_input(self):
        reasons = ("model_warmup", "wake_ready", "new_session", "turn_complete", "user_stop",
                   "explicit_stop", "display_stop", "shutdown", "lease_expired", "session_finished",
                   "transport_stopped", "receive_idle_timeout", "voice_session_failed", "turn_timeout",
                   "turn_failed", "audio_backpressure", "wake_inference_failed", "playback_stop_failed",
                   "speech_start", "end_of_speech", "no_speech", "utterance_limit", "audio_not_received",
                   "turn_cancelled", "duplicate_activation", "call", "start_failed", "assistant_error")
        for value in (None, *reasons, "synthetic-private-marker", "token_shaped_unknown_code", True, 1,
                      ["synthetic-private-marker"], {"token": "synthetic-private-marker"}):
            with self.subTest(reason=value):
                payload = extended_status(phase_reason=value)
                self.client.voice_status.return_value = {"status": "ok", "voice": payload,
                                                       "private": "synthetic-private-marker"}
                result = await voice_api.voice(self.coordinator, "201", "201", "status")
                expected = value if value is None or type(value) is str and value in reasons else None
                self.assertEqual(result["voice"]["phase_reason"], expected)
                self.assertEqual(payload["phase_reason"], value)
                self.assertNotIn("synthetic-private-marker", repr(result))
                self.assertEqual(set(result), {"status", "voice"})

    async def test_extended_schema_still_requires_legacy_fields_and_rejects_extra_fields(self):
        for missing in status():
            with self.subTest(missing=missing):
                payload = extended_status()
                del payload[missing]
                self.client.voice_status.return_value = {"status": "ok", "voice": payload}
                with self.assertRaises(voice_api.VoiceError):
                    await voice_api.voice(self.coordinator, "201", "201", "status")
        for extra in ("transcript", "token", "model_path", "updated_at"):
            with self.subTest(extra=extra):
                self.client.voice_status.return_value = {
                    "status": "ok", "voice": extended_status(**{extra: "synthetic-private-marker"})}
                with self.assertRaises(voice_api.VoiceError) as raised:
                    await voice_api.voice(self.coordinator, "201", "201", "status")
                self.assertNotIn("synthetic-private-marker", str(raised.exception))

    async def test_extended_stop_still_rejects_a_different_session_without_retry(self):
        self.client.action.return_value = {"status": "accepted", "action_id": "stop-test-001",
                                          "voice": extended_status(session_id="b" * 32)}
        with self.assertRaises(voice_api.VoiceError):
            await voice_api.voice(self.coordinator, "201", "201", "stop",
                                  request_id="stop-test-001", session_id="a" * 32)
        self.client.action.assert_awaited_once_with(
            "201", "voice_stop", {"session_id": "a" * 32}, "stop-test-001")
        self.client.voice_status.assert_not_awaited()
        self.client.snapshot.assert_not_awaited()

    async def test_correlated_envelope_and_transport_failures(self):
        for envelope in ({"status": "ok", "action_id": "voice-test-001"},
                         {"status": "accepted", "action_id": "wrong-test-001"}):
            self.client.action.return_value = {**envelope, "voice": status()}
            with self.assertRaises(voice_api.VoiceError):
                await voice_api.voice(self.coordinator, "201", "202", "start", **self.consent)
        self.client.action.side_effect = RuntimeError("secret URL token")
        with self.assertRaises(voice_api.VoiceError) as raised:
            await voice_api.voice(self.coordinator, "201", "202", "start", **self.consent)
        self.assertNotIn("secret", str(raised.exception))
        self.assertEqual(self.client.action.await_count, 3)  # exactly once per explicit call
        self.client.action.side_effect = asyncio.CancelledError()
        for options in ({}, {"dry_run": True}):
            with self.subTest(options=options):
                self.client.action.reset_mock()
                with self.assertRaises(asyncio.CancelledError):
                    await voice_api.voice(self.coordinator, "201", "202", "start", **self.consent, **options)
                self.client.action.assert_awaited_once()

    async def test_dry_run_requires_exact_metadata(self):
        dry = {"schema_version": 1, "phone": "201", "line": "202", "valid": True,
               "network_actions": 0, "microphone_started": False, "duration_seconds": 30}
        self.client.action.return_value = {"status": "accepted", "action_id": "voice-test-001", "voice": dry}
        result = await voice_api.voice(self.coordinator, "201", "202", "start", **self.consent, dry_run=True)
        self.assertEqual(result, self.client.action.return_value)
        self.client.action.assert_awaited_once_with(
            "202", "voice_start",
            {"attended": True, "exclusive_media": True, "enable_microphone": True, "dry_run": True},
            "voice-test-001")
        self.client.voice_status.assert_not_awaited()
        self.client.snapshot.assert_not_awaited()
        for change in ({"valid": 1}, {"network_actions": False}, {"network_actions": 1},
                       {"microphone_started": 0}, {"microphone_started": True},
                       {"duration_seconds": 0}, {"duration_seconds": 301}, {"duration_seconds": float("nan")},
                       {"duration_seconds": True}, {"duration_seconds": 10 ** 400},
                       {"duration_seconds": math.nextafter(300, math.inf)},
                       {"private": "hidden"}, {"phase": "preparing"}):
            with self.subTest(change=change):
                self.client.action.return_value["voice"] = {**dry, **change}
                with self.assertRaises(voice_api.VoiceError):
                    await voice_api.voice(self.coordinator, "201", "202", "start", **self.consent, dry_run=True)
        for missing in dry:
            with self.subTest(missing=missing):
                self.client.action.return_value["voice"] = {key: value for key, value in dry.items() if key != missing}
                with self.assertRaises(voice_api.VoiceError):
                    await voice_api.voice(self.coordinator, "201", "202", "start", **self.consent, dry_run=True)

    async def test_dry_run_accepts_old_and_extended_duration_boundaries(self):
        for duration in (.001, 30, 180, 300):
            with self.subTest(duration=duration):
                self.client.action.reset_mock()
                dry = {"schema_version": 1, "phone": "201", "line": "202", "valid": True,
                       "network_actions": 0, "microphone_started": False, "duration_seconds": duration}
                self.client.action.return_value = {"status": "accepted", "action_id": "voice-test-001", "voice": dry}
                result = await voice_api.voice(self.coordinator, "201", "202", "start", **self.consent, dry_run=True)
                self.assertEqual(result["voice"], dry)
                self.client.action.assert_awaited_once_with(
                    "202", "voice_start",
                    {"attended": True, "exclusive_media": True, "enable_microphone": True, "dry_run": True},
                    "voice-test-001")
        self.client.voice_status.assert_not_awaited()
        self.client.snapshot.assert_not_awaited()

    async def test_dry_run_rejects_session_response_without_retry(self):
        for session in (status(), status(state="stopped", active=False, remaining_seconds=0, stop_confirmed=True)):
            with self.subTest(state=session["state"]):
                self.client.action.reset_mock()
                self.client.action.side_effect = [
                    {"status": "accepted", "action_id": "voice-test-001", "voice": session},
                    AssertionError("A dry-run response mismatch must not trigger another action"),
                ]
                with self.assertRaisesRegex(voice_api.VoiceError, "unconfirmed"):
                    await voice_api.voice(self.coordinator, "201", "202", "start", **self.consent, dry_run=True)
                self.client.action.assert_awaited_once()
                self.client.voice_status.assert_not_awaited()
                self.client.snapshot.assert_not_awaited()
                self.coordinator.perform.assert_not_awaited()

    async def test_dry_run_metadata_cannot_acknowledge_other_requests(self):
        dry = {"schema_version": 1, "phone": "201", "line": "202", "valid": True,
               "network_actions": 0, "microphone_started": False, "duration_seconds": 30}
        self.client.action.return_value = {"status": "accepted", "action_id": "voice-test-001", "voice": dry}
        self.client.voice_status.return_value = {"status": "ok", "voice": dry}
        for operation, options in (("start", self.consent), ("start", {**self.consent, "dry_run": False}),
                                   ("status", {}), ("stop", {"request_id": "voice-test-001", "session_id": "a" * 32})):
            with self.subTest(operation=operation, options=options):
                with self.assertRaises(voice_api.VoiceError):
                    await voice_api.voice(self.coordinator, "201", "202", operation, **options)

    async def test_unloading_blocks_start_but_allows_status_and_exact_stop(self):
        self.coordinator._closing = True
        with self.assertRaises(voice_api.VoiceError):
            await voice_api.voice(self.coordinator, "201", "202", "start", **self.consent)
        self.client.action.assert_not_awaited()
        await voice_api.voice(self.coordinator, "201", "202", "status")
        self.client.action.return_value = {"status": "accepted", "action_id": "stop-test-001", "voice": status()}
        await voice_api.voice(self.coordinator, "201", "202", "stop", request_id="stop-test-001", session_id="a" * 32)

    async def test_start_cannot_acknowledge_idle_without_a_session(self):
        self.client.action.return_value = {"status": "accepted", "action_id": "voice-test-001",
            "voice": status(state="idle", session_id=None, active=False, remaining_seconds=0)}
        with self.assertRaises(voice_api.VoiceError):
            await voice_api.voice(self.coordinator, "201", "202", "start", **self.consent)

    async def test_invalid_requests_never_reach_gateway(self):
        cases = [("203", "203", "status", {}), ("201", "203", "status", {}),
                 ("201", "201", "status", {"request_id": "unexpected"}),
                 ("201", "201", "stop", {"request_id": "stop-001", "session_id": "A" * 32}),
                 ("201", "201", "start", {**self.consent, "dry_run": "true"}),
                 ("201", "201", "start", {**self.consent, "request_id": "bad/id"}),
                 ("201", "201", "start", {**self.consent, "duration_seconds": 60})]
        for primary, line, operation, options in cases:
            with self.subTest(operation=operation, options=options):
                with self.assertRaises(voice_api.VoiceError):
                    await voice_api.voice(self.coordinator, primary, line, operation, **options)
        self.client.action.assert_not_awaited()
        self.client.voice_status.assert_not_awaited()


if __name__ == "__main__":
    unittest.main()
