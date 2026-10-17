"""Optional real HA tests with a fake local gateway; no physical calls."""
import asyncio
import copy
from datetime import datetime, timedelta, timezone
import importlib.util
import json
from pathlib import Path
import shutil
import tempfile
from types import MappingProxyType, SimpleNamespace
import unittest
from unittest.mock import AsyncMock, patch

from test_phone_controls import snapshot

READY = importlib.util.find_spec("homeassistant") is not None
ROOT = Path(__file__).resolve().parents[1]


@unittest.skipUnless(READY, "requires optional pinned Home Assistant runtime")
class PhoneRuntimeTests(unittest.IsolatedAsyncioTestCase):
    async def asyncSetUp(self):
        from homeassistant.core import HomeAssistant
        from homeassistant.config_entries import ConfigEntries, ConfigEntry
        from custom_components.cisco_phone_control.coordinator import PhoneCoordinator
        self.tmp = tempfile.TemporaryDirectory()
        self.hass = HomeAssistant(self.tmp.name)
        self.hass.config_entries = ConfigEntries(self.hass, {})
        from homeassistant import loader
        loader.async_setup(self.hass)
        self.entry = ConfigEntry(domain="cisco_phone_control", title="Synthetic gateway", unique_id="http://127.0.0.1:18084",
                                 version=1, minor_version=1, source="user", options={},
                                 discovery_keys=MappingProxyType({}), subentries_data=[],
                                 data={"url": "http://127.0.0.1:18084", "token": "synthetic-never-log"})
        self.data = snapshot()
        self.client = SimpleNamespace(snapshot=AsyncMock(side_effect=lambda: copy.deepcopy(self.data)), action=AsyncMock(return_value={"status": "accepted"}))
        self.c = PhoneCoordinator(self.hass, self.entry, self.client)
        self.c.store = SimpleNamespace(async_load=AsyncMock(return_value=None), async_save=AsyncMock())
        self.c.prepare_inventory(self.data)
        self.c.async_set_updated_data(copy.deepcopy(self.data))
        self.entry.runtime_data = self.c

    async def asyncTearDown(self):
        await self.hass.async_stop()
        self.tmp.cleanup()

    async def test_actions_use_fresh_exact_identity_not_stale_ha(self):
        from homeassistant.exceptions import HomeAssistantError
        self.data["phones"][0]["channels"] = [{"name": "PJSIP/201-00000002", "uniqueid": "12.3", "line": "201", "state": "Ringing"}]
        await self.c.perform("201", "answer")
        self.client.action.assert_awaited_once_with("201", "answer", {"line": "201", "channel": "PJSIP/201-00000002", "uniqueid": "12.3"}, None)
        self.client.action.reset_mock()
        self.data["phones"][0]["channels"][0]["state"] = "Ring"
        with self.assertRaises(HomeAssistantError):
            await self.c.perform("201", "answer")
        self.client.action.assert_not_awaited()

    async def test_atomic_secondary_timer_replay_and_sibling_ownership(self):
        from custom_components.cisco_phone_control.scheduling_api import schedule
        from homeassistant.exceptions import HomeAssistantError
        result = await schedule(self.c, "201", "202", "timer_start", minutes=5, request_id="timer-request-001")
        self.assertEqual(result["timer"]["line"], "202")
        self.assertEqual(self.c.settings["201"]["line"], "201")
        deadline = result["timer"]["deadline"]
        from custom_components.cisco_phone_control.schedule import validate_settings
        self.c.settings = validate_settings(copy.deepcopy(self.c.store.async_save.await_args.args[0]["phones"]))
        repeated = await schedule(self.c, "201", "202", "timer_start", minutes=5, request_id="timer-request-001")
        self.assertEqual(repeated["operation_result"], "replayed")
        self.assertEqual(repeated["timer"]["deadline"], deadline)
        with self.assertRaises(HomeAssistantError):
            await schedule(self.c, "201", "202", "timer_start", minutes=5, request_id="same-line-request-002")
        for operation, options in (("timer_start", {"minutes": 1}), ("timer_cancel", {})):
            with self.assertRaises(HomeAssistantError):
                await schedule(self.c, "201", "201", operation, request_id="other-request-001", **options)
        with self.assertRaises(HomeAssistantError):
            await schedule(self.c, "201", "202", "timer_start", minutes=7, request_id="timer-request-001")
        self.client.action.assert_not_awaited()

    async def test_atomic_alarm_has_own_line_and_exact_enable_disable(self):
        from custom_components.cisco_phone_control.scheduling_api import schedule
        from homeassistant.exceptions import HomeAssistantError
        await schedule(self.c, "201", "202", "alarm_set", time="09:45", weekdays=[4, 0], enabled=True, request_id="alarm-request-001")
        self.assertEqual(self.c.settings["201"]["line"], "201")
        await self.c.configure("201", "line", "201")
        result = await schedule(self.c, "201", "202", "status")
        self.assertEqual(result["alarm"], {"line": "202", "time": "09:45", "weekdays": [0, 4], "enabled": True, "configured": True})
        with self.assertRaises(HomeAssistantError):
            await schedule(self.c, "201", "201", "alarm_disable", request_id="alarm-request-002")
        await schedule(self.c, "201", "202", "alarm_disable", request_id="alarm-request-003")
        await schedule(self.c, "201", "202", "alarm_enable", request_id="alarm-request-004")
        self.assertEqual(self.c.settings["201"]["alarm_line"], "202")
        await self.c.configure("201", "alarm_enabled", False)
        await self.c.configure("201", "alarm_enabled", True)
        self.assertEqual(self.c.settings["201"]["alarm_line"], "202")
        with self.assertRaises(HomeAssistantError):
            await schedule(self.c, "201", "201", "alarm_set", time="07:30", weekdays=[1], request_id="alarm-request-005")
        await schedule(self.c, "201", "201", "alarm_set", time="07:30", weekdays=[1], replace=True, request_id="alarm-request-006")
        self.assertEqual(self.c.settings["201"]["alarm_line"], "201")

    async def test_atomic_stop_refuses_sibling_and_changed_alert(self):
        from custom_components.cisco_phone_control.scheduling_api import schedule
        from homeassistant.exceptions import HomeAssistantError
        self.data["phones"][0]["alert"] = {"id": "a" * 32, "kind": "timer", "line": "202", "state": "ringing", "reason": None}
        with self.assertRaises(HomeAssistantError):
            await schedule(self.c, "201", "201", "alert_stop", request_id="stop-request-001")
        original = copy.deepcopy(self.data)
        changed = copy.deepcopy(self.data)
        changed["phones"][0]["alert"]["id"] = "b" * 32
        self.client.snapshot.side_effect = [original, changed]
        result = await schedule(self.c, "201", "202", "alert_stop", request_id="stop-request-002")
        self.assertEqual(result["operation_result"], "outcome_unknown")
        self.client.action.assert_not_awaited()
        self.client.snapshot.side_effect = lambda: copy.deepcopy(changed)
        repeated = await schedule(self.c, "201", "202", "alert_stop", request_id="stop-request-002")
        self.assertEqual(repeated["operation_result"], "outcome_unknown")
        self.client.action.assert_not_awaited()

    async def test_atomic_cancel_persists_offline_and_never_rearms(self):
        from custom_components.cisco_phone_control.scheduling_api import schedule
        from custom_components.cisco_phone_control.api import GatewayError
        self.c.settings["201"].update(timer_line="202", timer_deadline=9999999999)
        self.client.snapshot.side_effect = GatewayError("offline")
        result = await schedule(self.c, "201", "202", "timer_cancel", request_id="cancel-request-001")
        self.assertEqual(result["operation_result"], "outcome_unknown")
        self.assertIsNone(self.c.settings["201"]["timer_deadline"])
        self.client.action.assert_not_awaited()

    async def test_atomic_schedule_save_failure_rolls_back_without_action(self):
        from custom_components.cisco_phone_control.scheduling_api import schedule
        from homeassistant.exceptions import HomeAssistantError
        old = copy.deepcopy(self.c.settings)
        self.c.store.async_save.side_effect = OSError("disk full")
        with self.assertRaises(HomeAssistantError):
            await schedule(self.c, "201", "202", "timer_start", minutes=1, request_id="failed-request-001")
        self.assertEqual(old, self.c.settings)
        self.client.action.assert_not_awaited()

    async def test_atomic_parallel_timer_starts_cannot_replace_sibling(self):
        from custom_components.cisco_phone_control.scheduling_api import schedule
        from homeassistant.exceptions import HomeAssistantError
        results = await asyncio.gather(
            schedule(self.c, "201", "201", "timer_start", minutes=1, request_id="parallel-request-001"),
            schedule(self.c, "201", "202", "timer_start", minutes=1, request_id="parallel-request-002"),
            return_exceptions=True)
        self.assertEqual(sum(isinstance(result, HomeAssistantError) for result in results), 1)
        self.assertEqual(self.c.settings["201"]["timer_line"], "201")

    async def test_text_privacy_gate_and_volatile_state(self):
        from homeassistant.helpers.recorder import DATA_INSTANCE
        from homeassistant.exceptions import HomeAssistantError
        from custom_components.cisco_phone_control.text import MessageInput
        field = MessageInput(self.c, "201", "message", "Message text")
        field.hass = self.hass
        field.entity_id = "text.synthetic_message"
        field._no_platform_reported = True
        with self.assertRaises(HomeAssistantError):
            await field.async_set_value("private sample")
        self.hass.data[DATA_INSTANCE] = SimpleNamespace(entity_filter=lambda entity_id: False)
        await field.async_set_value("private sample")
        self.assertEqual(field.native_value, "private sample")
        self.c.store.async_save.assert_not_awaited()
        del self.hass.data[DATA_INSTANCE]

    async def test_scheduler_persists_claim_before_call_and_never_retries(self):
        from custom_components.cisco_phone_control.api import GatewayError
        from custom_components.cisco_phone_control.schedule import start_timer
        now = datetime(2026, 10, 19, 10, 0, tzinfo=timezone.utc)
        self.c._started_at = now.timestamp() - 1000
        self.c.settings["201"]["timer_minutes"] = 1
        start_timer(self.c.settings["201"], now - timedelta(minutes=1))
        order = []
        async def save(data):
            order.append("save")
            self.assertIsNone(data["phones"]["201"]["timer_deadline"])
        async def refused(*args):
            order.append("call")
            raise GatewayError("outcome unknown", "synthetic-key")
        self.c.store.async_save.side_effect = save
        self.client.action.side_effect = refused
        with patch("custom_components.cisco_phone_control.coordinator.dt_util.now", return_value=now):
            await self.c._tick(now)
            await self.c._tick(now)
        self.assertEqual(order, ["save", "call", "save"])
        self.assertIn("no retry", self.c.settings["201"]["status"])

    async def test_unload_during_action_does_not_overwrite_new_instance_storage(self):
        from custom_components.cisco_phone_control.schedule import start_timer
        now = datetime(2026, 10, 19, 10, 0, tzinfo=timezone.utc)
        self.c._started_at = now.timestamp() - 1000
        self.c.settings["201"]["timer_minutes"] = 1
        start_timer(self.c.settings["201"], now - timedelta(minutes=1))
        async def close_during_action(*args):
            self.c.close()
        self.c.perform = close_during_action
        with patch("custom_components.cisco_phone_control.coordinator.dt_util.now", return_value=now):
            await self.c._tick(now)
        # The durable claim is written, but an unloading instance must not write
        # again after a freshly loaded instance could have changed the settings.
        self.c.store.async_save.assert_awaited_once()

    async def test_storage_failure_suppresses_scheduled_call(self):
        now = datetime(2026, 10, 19, 7, 0, 1, tzinfo=timezone.utc)
        self.c._started_at = now.timestamp() - 1000
        self.c.settings["201"]["alarm_enabled"] = True
        self.c.store.async_save.side_effect = OSError("synthetic storage failure")
        with patch("custom_components.cisco_phone_control.coordinator.dt_util.now", return_value=now):
            await self.c._tick(now)
        self.client.action.assert_not_awaited()

    async def test_due_timer_requests_persistent_alert_without_audio(self):
        from custom_components.cisco_phone_control.schedule import start_timer
        now = datetime(2026, 10, 19, 10, 0, tzinfo=timezone.utc)
        self.c._started_at = now.timestamp() - 1000
        self.c.settings["201"].update(timer_minutes=1, timer_destination="701")
        start_timer(self.c.settings["201"], now - timedelta(minutes=1))
        with patch("custom_components.cisco_phone_control.coordinator.dt_util.now", return_value=now):
            await self.c._tick(now)
        args = self.client.action.await_args.args
        self.assertEqual(args[:3], ("201", "alert", {"kind": "timer", "line": "201"}))
        self.assertTrue(args[3].startswith("ha-schedule-"))

    async def test_stop_alert_uses_fresh_id_even_after_selected_line_changes(self):
        self.c.settings["201"]["line"] = "202"
        self.data["phones"][0]["alert"] = {"id": "b" * 32, "kind": "timer", "line": "201", "state": "unknown", "reason": None}
        await self.c.stop_alert("201")
        self.client.action.assert_awaited_once_with("201", "alert_stop", {"alert_id": "b" * 32}, None)

    async def test_cancel_timer_and_disable_alarm_stop_only_matching_kind(self):
        self.data["phones"][0]["alert"] = {"id": "a" * 32, "kind": "alarm", "line": "201", "state": "ringing", "reason": None}
        self.c.settings["201"]["timer_deadline"] = 9999999999
        await self.c.timer("201", False)
        self.assertIsNone(self.c.settings["201"]["timer_deadline"])
        self.client.action.assert_not_awaited()
        await self.c.configure("201", "alarm_enabled", False)
        self.client.action.assert_awaited_once_with("201", "alert_stop", {"alert_id": "a" * 32}, None)
        self.client.action.reset_mock()
        self.data["phones"][0]["alert"]["kind"] = "timer"
        await self.c.configure("201", "alarm_enabled", False)
        self.client.action.assert_not_awaited()
        await self.c.timer("201", False)
        self.client.action.assert_awaited_once_with("201", "alert_stop", {"alert_id": "a" * 32}, None)

    async def test_unknown_or_active_alert_blocks_new_alert_without_retry(self):
        from homeassistant.exceptions import HomeAssistantError
        for state in ("starting", "ringing", "stopping", "unknown"):
            self.data["phones"][0]["alert"] = {"id": "a" * 32, "kind": "alarm", "line": "201", "state": state, "reason": None}
            with self.assertRaises(HomeAssistantError):
                await self.c.perform("201", "alert", {"kind": "timer", "line": "201"})
        self.client.action.assert_not_awaited()

    async def test_cancel_persists_pending_removal_if_gateway_is_unavailable(self):
        from custom_components.cisco_phone_control.api import GatewayError
        from homeassistant.exceptions import HomeAssistantError
        self.c.settings["201"]["timer_deadline"] = 9999999999
        self.client.snapshot.side_effect = GatewayError("unavailable")
        with self.assertRaises(HomeAssistantError): await self.c.timer("201", False)
        self.assertIsNone(self.c.store.async_save.await_args.args[0]["phones"]["201"]["timer_deadline"])
        self.client.action.assert_not_awaited()

    async def test_stop_and_local_cancellation_controls_remain_available_offline(self):
        from custom_components.cisco_phone_control.button import PhoneButton
        from custom_components.cisco_phone_control.switch import PhoneSwitch
        self.c.data["phones"][0]["online"] = False
        stop = PhoneButton(self.c, "201", "alert_stop", "Stop alert")
        cancel = PhoneButton(self.c, "201", "timer_cancel", "Cancel timer")
        enabled = PhoneSwitch(self.c, "201", "alarm_enabled", "Alarm enabled")
        self.assertTrue(stop.available)
        self.assertTrue(cancel.available)
        self.assertTrue(enabled.available)
        self.c.last_update_success = False
        self.assertFalse(stop.available)
        self.assertTrue(cancel.available)
        self.assertTrue(enabled.available)

    async def test_schedule_status_tracks_live_alert_and_unknown_gateway(self):
        from custom_components.cisco_phone_control.sensor import PhoneSensor
        sensor = PhoneSensor(self.c, "201", "schedule_status", "Schedule status")
        self.c.data["phones"][0]["alert"] = {"id": "a" * 32, "kind": "alarm", "line": "201", "state": "ringing", "reason": None}
        self.assertEqual(sensor.native_value, "Alarm ringing")
        self.c.data["phones"][0]["alert"].update(state="ended", reason="call_ended")
        self.assertEqual(sensor.native_value, "Alarm ended: call ended")
        self.c.settings["201"].update(timer_deadline=9999999999, status="Timer running")
        self.assertEqual(sensor.native_value, "Timer running")
        self.c.last_update_success = False
        self.assertEqual(sensor.native_value, "Gateway unavailable; alert status unknown")

    async def test_all_platforms_real_ha_state_and_dynamic_addition(self):
        from custom_components.cisco_phone_control import button, switch, select, text, time, sensor, number
        collected = []
        for platform in (button, switch, select, text, time, sensor, number):
            await platform.async_setup_entry(self.hass, self.entry, collected.extend)
        self.assertEqual(len(collected), 29)
        self.assertEqual(len({e.unique_id for e in collected}), len(collected))
        for index, entity in enumerate(collected):
            entity.hass = self.hass
            domain = entity.__module__.rsplit(".", 1)[-1]
            entity.entity_id = f"{domain}.synthetic_{index}"
            entity._no_platform_reported = True
            entity.async_write_ha_state()
            self.assertIsNotNone(self.hass.states.get(entity.entity_id))
        extra = copy.deepcopy(self.data["phones"][0])
        extra.update(id="203", lines=["203", "204"])
        self.data["phones"].append(extra)
        self.c.prepare_inventory(self.data)
        self.c.async_set_updated_data(copy.deepcopy(self.data))
        self.assertEqual(len(collected), 58)
        self.assertEqual(len({e.unique_id for e in collected}), 58)

    async def test_config_flow_validates_gateway_and_hides_token(self):
        from custom_components.cisco_phone_control.config_flow import CiscoPhoneConfigFlow
        flow = CiscoPhoneConfigFlow()
        flow.hass = self.hass
        flow.context = {"source": "user"}
        with patch("custom_components.cisco_phone_control.config_flow.GatewayClient", return_value=self.client), patch("custom_components.cisco_phone_control.config_flow.async_get_clientsession", return_value=None):
            result = await flow.async_step_user({"url": "http://127.0.0.1:18084", "token": "synthetic-token"})
        self.assertEqual(result["type"], "create_entry")
        self.assertNotIn("synthetic-token", result["title"])
        self.assertEqual(result["data"]["token"], "synthetic-token")

    async def test_full_component_setup_and_unload(self):
        from homeassistant.setup import async_setup_component
        from custom_components.cisco_phone_control import DOMAIN
        # The real integration loader reads manifest/translations from the HA config directory.
        target = Path(self.tmp.name) / "custom_components/cisco_phone_control"
        target.parent.mkdir()
        shutil.copytree(ROOT / "custom_components/cisco_phone_control", target, ignore=shutil.ignore_patterns("__pycache__"))
        from homeassistant.helpers import entity_registry, device_registry, area_registry, floor_registry, label_registry, issue_registry
        device_registry.async_setup(self.hass)
        for registry in (floor_registry, label_registry, area_registry, device_registry, entity_registry, issue_registry):
            await registry.async_load(self.hass, load_empty=True)
        self.hass.config_entries._entries[self.entry.entry_id] = self.entry
        registry = entity_registry.async_get(self.hass)
        obsolete = [registry.async_get_or_create("select", DOMAIN, f"{self.entry.entry_id}:201:{key}", config_entry=self.entry).entity_id
                    for key in ("alarm_destination", "timer_destination")]
        unrelated = registry.async_get_or_create("select", "unrelated", "keep-this-selector").entity_id
        unrelated_owned_key = registry.async_get_or_create("select", DOMAIN, f"{self.entry.entry_id}:999:alarm_destination", config_entry=self.entry).entity_id
        with patch("custom_components.cisco_phone_control.GatewayClient", return_value=self.client), patch("custom_components.cisco_phone_control.async_get_clientsession", return_value=None):
            self.assertTrue(await async_setup_component(self.hass, DOMAIN, {}))
            from homeassistant.config_entries import ConfigEntryState
            self.assertEqual(self.entry.state, ConfigEntryState.LOADED)
            await self.hass.async_block_till_done()
        self.assertTrue(all(registry.async_get(entity) is None for entity in obsolete))
        self.assertIsNotNone(registry.async_get(unrelated))
        self.assertIsNotNone(registry.async_get(unrelated_owned_key))
        owned = [s for s in self.hass.states.async_all() if s.entity_id.startswith(("button.cisco_201", "switch.cisco_201", "select.cisco_201", "text.cisco_201", "time.cisco_201", "number.cisco_201", "sensor.cisco_201"))]
        self.assertEqual(len(owned), 29)
        # Exercise actual HA service dispatch and response handling, not only the coordinator.
        response = await self.hass.services.async_call(DOMAIN, "schedule", {
            "entry_id": self.entry.entry_id, "phone": "201", "line": "202",
            "operation": "timer_start", "minutes": 5, "request_id": "service-request-001",
        }, blocking=True, return_response=True)
        self.assertEqual(response["schema_version"], 1)
        self.assertEqual(response["timer"]["line"], "202")
        self.assertEqual(response["operation_result"], "applied")
        # Exact line resolution also works without a separate gateway query.
        response = await self.hass.services.async_call(DOMAIN, "schedule", {
            "entry_id": self.entry.entry_id, "line": "202", "operation": "status",
        }, blocking=True, return_response=True)
        self.assertEqual((response["phone"], response["lines"]), ("201", ["201", "202"]))
        from custom_components.cisco_phone_control.api import GatewayError
        self.client.snapshot.side_effect = GatewayError("synthetic offline")
        response = await self.hass.services.async_call(DOMAIN, "schedule", {
            "entry_id": self.entry.entry_id, "line": "202", "operation": "timer_cancel",
            "request_id": "offline-request-001",
        }, blocking=True, return_response=True)
        self.assertEqual(response["operation_result"], "outcome_unknown")
        self.assertFalse(response["timer"]["active"])
        self.assertFalse(response["gateway_fresh"])
        self.assertTrue(await self.hass.config_entries.async_unload(self.entry.entry_id))


@unittest.skipUnless(READY, "requires optional pinned Home Assistant runtime")
class GatewayClientTests(unittest.IsolatedAsyncioTestCase):
    async def test_real_http_client_success_and_bounded_response(self):
        import aiohttp
        from aiohttp import web
        from custom_components.cisco_phone_control.api import GatewayClient, GatewayError
        requests = []
        async def handle(request):
            self.assertEqual(request.headers.get("Authorization"), "Bearer synthetic-token")
            requests.append(request.path)
            if request.method == "GET":
                return web.json_response(snapshot())
            body = await request.json()
            return web.json_response({"status": "accepted", "action_id": body["idempotency_key"]})
        async def too_large(request):
            return web.Response(body=b" " * (256 * 1024 + 1))
        app = web.Application()
        app.router.add_route("*", "/v1/phones", handle)
        app.router.add_route("POST", "/v1/phones/201/speaker", handle)
        app.router.add_route("POST", "/v1/phones/201/image", too_large)
        runner = web.AppRunner(app)
        await runner.setup()
        site = web.TCPSite(runner, "127.0.0.1", 0)
        await site.start()
        port = site._server.sockets[0].getsockname()[1]
        try:
            async with aiohttp.ClientSession() as session:
                client = GatewayClient(session, f"http://127.0.0.1:{port}", "synthetic-token")
                self.assertEqual((await client.snapshot())["phones"][0]["id"], "201")
                result = await client.action("201", "speaker", {"mode": "toggle"}, "synthetic-once")
                self.assertEqual(result["action_id"], "synthetic-once")
                with self.assertRaises(GatewayError):
                    await client.action("201", "image", {}, "synthetic-too-large")
            self.assertEqual(requests, ["/v1/phones", "/v1/phones/201/speaker"])
        finally:
            await runner.cleanup()

    async def test_unknown_http_outcome_is_not_retried_or_leaked(self):
        from custom_components.cisco_phone_control.api import GatewayClient, GatewayError
        response = SimpleNamespace(status=503)
        class Context:
            async def __aenter__(self):
                return response
            async def __aexit__(self, *args):
                pass
        calls = []
        def request(*args, **kwargs):
            calls.append((args, kwargs))
            return Context()
        client = GatewayClient(SimpleNamespace(request=request), "http://127.0.0.1:18084", "secret-test-token")
        with self.assertRaises(GatewayError) as caught:
            await client.action("201", "speaker", {"mode": "toggle"}, "one-key-only")
        self.assertEqual(len(calls), 1)
        self.assertFalse(calls[0][1]["allow_redirects"])
        self.assertIn("one-key-only", str(caught.exception))
        self.assertNotIn("secret-test-token", str(caught.exception))
