"""Real HA service/entity lifecycle with synthetic gateway responses only."""
import asyncio
import copy
import importlib.util
from pathlib import Path
import shutil
from types import SimpleNamespace
import unittest
from unittest.mock import AsyncMock, patch

from test_hands_free import status
from test_phone_voice import extended_status
import test_phone_controls_runtime as runtime

READY = importlib.util.find_spec("homeassistant") is not None


@unittest.skipUnless(READY, "requires optional pinned Home Assistant runtime")
class HandsFreeRuntimeTests(unittest.IsolatedAsyncioTestCase):
    async def asyncSetUp(self):
        await runtime.PhoneRuntimeTests.asyncSetUp(self)
        from custom_components.cisco_phone_control.hands_free_coordinator import HandsFreeCoordinator
        self.client.hands_free_status = AsyncMock(return_value={"status": "ok", "hands_free": status()})
        self.client.voice_status = AsyncMock(return_value={"status": "ok", "voice": extended_status(
            phase="armed", phase_reason="wake_ready", mode="wake", session_seconds=300)})
        self.hf = HandsFreeCoordinator(self.c)
        self.c.hands_free = self.hf

    async def asyncTearDown(self):
        await self.hf.async_close()
        current = getattr(getattr(self.entry, "runtime_data", None), "hands_free", None)
        if current is not None and current is not self.hf:
            await current.async_close()
        await runtime.PhoneRuntimeTests.asyncTearDown(self)

    async def test_polling_and_entities_ignore_ami_failure_and_call_action_lock(self):
        from custom_components.cisco_phone_control.binary_sensor import HandsFreeEnabled
        from custom_components.cisco_phone_control.sensor import HandsFreeStatus
        self.c.last_update_success = False
        self.client.snapshot.side_effect = RuntimeError("AMI unavailable")
        async with self.c._action_lock:
            await asyncio.wait_for(self.hf.async_refresh(), 1)
        enabled, actual = HandsFreeEnabled(self.hf, "201"), HandsFreeStatus(self.hf, "201")
        self.assertTrue(enabled.available)
        self.assertTrue(enabled.is_on)
        self.assertTrue(actual.available)
        self.assertEqual(actual.native_value, "ready")
        self.assertNotIn("session_id", actual.extra_state_attributes)
        self.client.snapshot.assert_not_awaited()
        self.client.action.assert_not_awaited()
        self.client.voice_status.assert_awaited_once_with("202")

    async def test_mismatched_or_failed_finite_status_never_shows_ready(self):
        from custom_components.cisco_phone_control.sensor import HandsFreeStatus
        for result in ({"status": "ok", "voice": extended_status(session_id="b"*32)},
                       {"status": "ok", "voice": {"raw": "private"}}):
            self.client.voice_status.return_value = result
            await self.hf.async_refresh()
            actual = HandsFreeStatus(self.hf, "201")
            self.assertFalse(actual.available)
            self.assertIsNone(actual.native_value)
        self.client.voice_status.side_effect = RuntimeError("private")
        await self.hf.async_refresh()
        self.assertFalse(HandsFreeStatus(self.hf, "201").available)

    async def test_missing_stale_and_failed_gateway_never_looks_disabled(self):
        from custom_components.cisco_phone_control.binary_sensor import HandsFreeEnabled
        from custom_components.cisco_phone_control.sensor import HandsFreeStatus
        enabled, actual = HandsFreeEnabled(self.hf, "201"), HandsFreeStatus(self.hf, "201")
        self.assertFalse(enabled.available)
        self.assertIsNone(enabled.is_on)
        await self.hf.async_refresh()
        self.hf.data["201"]["observed_at"] -= 26
        self.assertFalse(enabled.available)
        self.assertFalse(actual.available)
        self.client.hands_free_status.side_effect = RuntimeError("secret")
        await self.hf.async_refresh()
        self.assertIsNone(enabled.is_on)
        self.assertIsNone(actual.native_value)

    async def test_disabled_waiting_and_stopping_are_distinct_without_ha_mutation(self):
        from custom_components.cisco_phone_control.sensor import HandsFreeStatus
        for changes, expected in (({"enabled": False, "state": "disabled", "session_id": None}, "off"),
                                  ({"enabled": True, "state": "waiting_idle", "session_id": None}, "waiting_for_idle"),
                                  ({"enabled": False, "state": "stopping"}, "stopping")):
            self.client.hands_free_status.return_value = {"status": "ok", "hands_free": status(**changes)}
            await self.hf.async_refresh()
            self.assertEqual(HandsFreeStatus(self.hf, "201").native_value, expected)
        self.client.action.assert_not_awaited()

    async def test_discovery_only_after_supported_response_and_no_duplicate_alias_entity(self):
        from custom_components.cisco_phone_control import binary_sensor
        from custom_components.cisco_phone_control.sensor import HandsFreeStatus
        from custom_components.cisco_phone_control.hands_free_entity import setup_hands_free_entities
        entities = []
        await binary_sensor.async_setup_entry(self.hass, self.entry, entities.extend)
        await setup_hands_free_entities(self.entry, entities.extend, HandsFreeStatus)
        self.assertEqual(entities, [])
        await self.hf.async_refresh()
        self.assertEqual(len(entities), 2)
        self.assertEqual(len({item.unique_id for item in entities}), 2)
        await self.hf.async_refresh()
        self.assertEqual(len(entities), 2)
        self.assertEqual({item.unique_id.rsplit(":", 1)[1] for item in entities},
                         {"hands_free_enabled", "voice_status"})
        self.client.action.assert_not_awaited()

    async def test_unload_cancels_and_drains_owned_reads_without_disabling_gateway(self):
        entered, ended = asyncio.Event(), asyncio.Event()
        async def pending(_line):
            entered.set()
            try:
                await asyncio.Event().wait()
            finally:
                ended.set()
        self.client.hands_free_status.side_effect = pending
        self.hf.request_refresh()
        await asyncio.wait_for(entered.wait(), 1)
        self.hf.request_refresh()  # One poll owner, no duplicate request.
        await asyncio.wait_for(self.hf.async_close(), 1)
        self.assertTrue(ended.is_set())
        self.hf.request_refresh()
        self.assertIsNone(self.hf._refresh_task)
        self.client.hands_free_status.assert_awaited_once()
        self.client.action.assert_not_awaited()

    async def test_poll_total_deadline_keeps_other_phone_results_and_cancels_stall(self):
        extra = copy.deepcopy(self.data["phones"][0]); extra.update(id="203", lines=["203", "204"])
        self.c.data["phones"].append(extra)
        ended = asyncio.Event()
        async def response(line):
            if line == "201":
                return {"status": "ok", "hands_free": status(state="disabled", enabled=False, session_id=None)}
            try:
                await asyncio.Event().wait()
            finally:
                ended.set()
        self.client.hands_free_status.side_effect = response
        with patch("custom_components.cisco_phone_control.hands_free_coordinator.POLL_TIMEOUT", .03):
            await self.hf.async_refresh()
        self.assertEqual(self.hf.data["201"]["actual"], "off")
        self.assertIsNone(self.hf.data["203"])
        self.assertTrue(ended.is_set())

    async def _load_entry(self):
        from homeassistant.setup import async_setup_component
        from homeassistant.helpers import entity_registry, device_registry, area_registry, floor_registry, label_registry, issue_registry
        target = Path(self.tmp.name) / "custom_components/cisco_phone_control"
        target.parent.mkdir()
        shutil.copytree(runtime.ROOT / "custom_components/cisco_phone_control", target, ignore=shutil.ignore_patterns("__pycache__"))
        device_registry.async_setup(self.hass)
        for registry in (floor_registry, label_registry, area_registry, device_registry, entity_registry, issue_registry):
            await registry.async_load(self.hass, load_empty=True)
        self.hass.config_entries._entries[self.entry.entry_id] = self.entry
        with patch("custom_components.cisco_phone_control.GatewayClient", return_value=self.client), patch("custom_components.cisco_phone_control.async_get_clientsession", return_value=None):
            self.assertTrue(await async_setup_component(self.hass, "cisco_phone_control", {}))
        await self.hass.async_block_till_done()

    async def test_real_admin_service_and_read_only_entities_preserve_authorization(self):
        from homeassistant.core import Context
        from homeassistant.exceptions import Unauthorized
        from custom_components.cisco_phone_control import vol
        await self._load_entry()
        self.client.action.assert_not_awaited()  # Setup only observes gateway state.
        domain = "cisco_phone_control"
        base = {"entry_id": self.entry.entry_id, "phone": "201", "line": "201"}
        enable = {**base, "operation": "enable", "request_id": "enable-service-001",
                  "enable_microphone": True, "exclusive_media": True}
        self.client.action.reset_mock()
        self.entry.runtime_data.last_update_success = False
        self.client.snapshot.side_effect = RuntimeError("AMI unavailable")
        self.client.snapshot.reset_mock()
        self.hass.auth = SimpleNamespace(async_get_user=AsyncMock(return_value=SimpleNamespace(is_admin=False)))
        with self.assertRaises(Unauthorized):
            await self.hass.services.async_call(domain, "hands_free", enable, blocking=True, return_response=True,
                                               context=Context(user_id="non-admin"))
        for flag in ("enable_microphone", "exclusive_media"):
            with self.assertRaises(vol.Invalid):
                await self.hass.services.async_call(domain, "hands_free", {**enable, flag: 1}, blocking=True, return_response=True)
        self.client.action.assert_not_awaited()
        self.client.action.return_value = {"status": "accepted", "action_id": "enable-service-001", "hands_free": status()}
        result = await self.hass.services.async_call(domain, "hands_free", enable, blocking=True, return_response=True)
        self.assertTrue(result["hands_free"]["enabled"])
        self.client.action.assert_awaited_once_with("201", "voice_hands_free_enable",
            {"enable_microphone": True, "exclusive_media": True}, "enable-service-001")
        self.client.action.reset_mock()
        self.client.action.return_value = {"status": "accepted", "action_id": "disable-service-001",
            "hands_free": status(enabled=False, state="stopping")}
        result = await self.hass.services.async_call(domain, "hands_free", {
            **base, "operation": "disable", "request_id": "disable-service-001"}, blocking=True, return_response=True)
        self.assertEqual(result["hands_free"]["state"], "stopping")
        self.client.action.assert_awaited_once_with("201", "voice_hands_free_disable", {}, "disable-service-001")
        self.client.snapshot.assert_not_awaited()
        states = self.hass.states.async_all()
        self.assertTrue(any(s.entity_id.startswith("binary_sensor.") and "hands_free" in s.entity_id for s in states))
        self.assertFalse(any(s.entity_id.startswith("switch.") and "hands_free" in s.entity_id for s in states))
        current = self.entry.runtime_data.hands_free
        self.assertTrue(await self.hass.config_entries.async_unload(self.entry.entry_id))
        self.assertTrue(current._closing)


if __name__ == "__main__":
    unittest.main()
