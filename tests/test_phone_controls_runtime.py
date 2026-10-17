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

    async def test_all_platforms_real_ha_state_and_dynamic_addition(self):
        from custom_components.cisco_phone_control import button, switch, select, text, time, sensor, number
        collected = []
        for platform in (button, switch, select, text, time, sensor, number):
            await platform.async_setup_entry(self.hass, self.entry, collected.extend)
        self.assertEqual(len(collected), 30)
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
        self.assertEqual(len(collected), 60)
        self.assertEqual(len({e.unique_id for e in collected}), 60)

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
        with patch("custom_components.cisco_phone_control.GatewayClient", return_value=self.client), patch("custom_components.cisco_phone_control.async_get_clientsession", return_value=None):
            self.assertTrue(await async_setup_component(self.hass, DOMAIN, {}))
            from homeassistant.config_entries import ConfigEntryState
            self.assertEqual(self.entry.state, ConfigEntryState.LOADED)
            await self.hass.async_block_till_done()
        owned = [s for s in self.hass.states.async_all() if s.entity_id.startswith(("button.cisco_201", "switch.cisco_201", "select.cisco_201", "text.cisco_201", "time.cisco_201", "number.cisco_201", "sensor.cisco_201"))]
        self.assertEqual(len(owned), 30)
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
