"""Voice metadata and explicit controls with fake clients; no HA or devices needed."""
import asyncio
import importlib.util
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
               {"remaining_seconds": -1}, {"remaining_seconds": 31}, {"error": "private secret"},
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
                       {"duration_seconds": 0}, {"duration_seconds": 31}, {"duration_seconds": float("nan")},
                       {"private": "hidden"}):
            with self.subTest(change=change):
                self.client.action.return_value["voice"] = {**dry, **change}
                with self.assertRaises(voice_api.VoiceError):
                    await voice_api.voice(self.coordinator, "201", "202", "start", **self.consent, dry_run=True)
        for missing in dry:
            with self.subTest(missing=missing):
                self.client.action.return_value["voice"] = {key: value for key, value in dry.items() if key != missing}
                with self.assertRaises(voice_api.VoiceError):
                    await voice_api.voice(self.coordinator, "201", "202", "start", **self.consent, dry_run=True)

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
