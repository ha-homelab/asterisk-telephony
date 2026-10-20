"""Event-controlled unload/reload races with real HA classes and fake I/O."""
import asyncio
import copy
import importlib.util
import tempfile
from types import MappingProxyType, SimpleNamespace
import unittest
from unittest.mock import AsyncMock, Mock, patch

from test_phone_controls import snapshot


READY = importlib.util.find_spec("homeassistant") is not None


class Gate:
    """Stop an awaited operation at a known boundary without wall-clock sleeps."""

    def __init__(self):
        self.entered = asyncio.Event()
        self.release = asyncio.Event()

    async def wait(self):
        self.entered.set()
        await self.release.wait()


class SharedStore:
    """Separate Store instances share disk state, never mutable payload aliases."""

    def __init__(self, phones):
        self.value = {"phones": copy.deepcopy(phones)}
        self.writes = []
        self.loads = []

    def instance(self, owner):
        backend = self

        class Store:
            def __init__(self):
                self.gates = {}
                self.failures = {}
                self.started = 0
                self.cancelled = 0

            async def async_load(self):
                backend.loads.append(owner)
                return copy.deepcopy(backend.value)

            async def async_save(self, value):
                self.started += 1
                number = self.started
                payload = copy.deepcopy(value)
                try:
                    if number in self.gates:
                        await self.gates[number].wait()
                except asyncio.CancelledError:
                    self.cancelled += 1
                    raise
                if number in self.failures:
                    raise self.failures[number]
                backend.value = payload
                backend.writes.append((owner, copy.deepcopy(payload)))

        return Store()


@unittest.skipUnless(READY, "requires optional pinned Home Assistant runtime")
class ScheduleUnloadTests(unittest.IsolatedAsyncioTestCase):
    async def asyncSetUp(self):
        from homeassistant import loader
        from homeassistant.config_entries import ConfigEntries, ConfigEntry
        from homeassistant.core import HomeAssistant

        self.tmp = tempfile.TemporaryDirectory()
        self.hass = HomeAssistant(self.tmp.name)
        self.hass.config_entries = ConfigEntries(self.hass, {})
        loader.async_setup(self.hass)
        self.entry = ConfigEntry(
            domain="cisco_phone_control", title="Synthetic gateway",
            unique_id="synthetic-schedule-store", version=1, minor_version=1,
            source="user", options={}, discovery_keys=MappingProxyType({}),
            subentries_data=[], data={"url": "http://127.0.0.1:18084",
                                      "token": "synthetic-never-log"})
        self.data = snapshot()
        self.tasks = []
        self.gates = []
        self.coordinators = []
        self.c = self.make_coordinator()
        self.disk = SharedStore(self.c.settings)
        self.c.store = self.disk.instance("old")
        self.entry.runtime_data = self.c

    def make_coordinator(self):
        from custom_components.cisco_phone_control.coordinator import PhoneCoordinator

        client = SimpleNamespace(
            snapshot=AsyncMock(side_effect=lambda: copy.deepcopy(self.data)),
            action=AsyncMock(return_value={"status": "accepted"}))
        coordinator = PhoneCoordinator(self.hass, self.entry, client)
        coordinator.prepare_inventory(self.data)
        coordinator.async_set_updated_data(copy.deepcopy(self.data))
        coordinator.async_update_listeners = Mock()
        self.coordinators.append(coordinator)
        return coordinator

    def gate(self):
        gate = Gate()
        self.gates.append(gate)
        return gate

    def spawn(self, awaitable):
        task = asyncio.create_task(awaitable)
        self.tasks.append(task)
        return task

    async def checkpoint(self):
        # Let already-scheduled tasks run; ordering comes from Events, not delay.
        ready = asyncio.Event()
        asyncio.get_running_loop().call_soon(ready.set)
        await ready.wait()

    async def entered(self, gate):
        await asyncio.wait_for(gate.entered.wait(), 2)

    async def finish(self, task):
        return await asyncio.wait_for(asyncio.shield(task), 2)

    async def begin_close(self):
        task = self.spawn(self.c.async_close())
        await self.checkpoint()
        self.assertTrue(self.c._closing)
        return task

    async def replacement(self):
        from homeassistant.config_entries import ConfigEntryState

        replacement = self.make_coordinator()
        replacement.store = self.disk.instance("replacement")
        # Exercise the production load path, suppressing only periodic timers.
        self.entry._async_set_state(
            self.hass, ConfigEntryState.SETUP_IN_PROGRESS, None)
        with patch("custom_components.cisco_phone_control.coordinator."
                   "async_track_time_interval", return_value=lambda: None):
            await replacement.async_initialize()
        self.entry.runtime_data = replacement
        return replacement

    async def request(self, operation="timer_start", request_id="unload-request-001",
                      coordinator=None, **options):
        from custom_components.cisco_phone_control.scheduling_api import schedule

        if operation == "timer_start":
            options.setdefault("minutes", 5)
        if operation != "status":
            options["request_id"] = request_id
        return await schedule(coordinator or self.c, "201", "201", operation,
                              **options)

    async def asyncTearDown(self):
        for gate in self.gates:
            gate.release.set()
        if self.tasks:
            await asyncio.wait_for(
                asyncio.gather(*self.tasks, return_exceptions=True), 3)
        for coordinator in self.coordinators:
            await coordinator.async_close()
        await self.hass.async_stop()
        self.tmp.cleanup()

    async def test_unload_during_snapshot_refuses_before_mutation(self):
        from homeassistant.exceptions import HomeAssistantError

        gate = self.gate()

        async def blocked_snapshot():
            await gate.wait()
            return copy.deepcopy(self.data)

        self.c.client.snapshot.side_effect = blocked_snapshot
        before = copy.deepcopy(self.c.settings)
        pending = self.spawn(self.request())
        await self.entered(gate)
        closing = await self.begin_close()
        self.assertFalse(closing.done())
        gate.release.set()
        with self.assertRaisesRegex(HomeAssistantError, "unloading"):
            await self.finish(pending)
        await self.finish(closing)
        self.assertEqual(self.c.settings, before)
        self.assertEqual(self.disk.writes, [])
        self.c.client.action.assert_not_awaited()
        self.c.async_update_listeners.assert_not_called()

    async def test_snapshot_failure_after_close_cannot_persist_offline_cancel(self):
        from custom_components.cisco_phone_control.api import GatewayError
        from homeassistant.exceptions import HomeAssistantError

        gate = self.gate()

        async def failed_snapshot():
            await gate.wait()
            raise GatewayError("synthetic unavailable")

        self.c.client.snapshot.side_effect = failed_snapshot
        before = copy.deepcopy(self.c.settings)
        pending = self.spawn(self.request("timer_cancel"))
        await self.entered(gate)
        closing = await self.begin_close()
        gate.release.set()
        with self.assertRaisesRegex(HomeAssistantError, "unloading"):
            await self.finish(pending)
        await self.finish(closing)
        self.assertEqual(self.c.settings, before)
        self.assertEqual(self.disk.writes, [])
        self.c.async_update_listeners.assert_not_called()

    async def test_unload_during_external_stop_keeps_claim_without_late_write(self):
        gate = self.gate()
        self.data["phones"][0]["alert"] = {
            "id": "a" * 32, "line": "201", "kind": "timer",
            "state": "ringing", "reason": None}

        async def external_stop(*_args):
            await gate.wait()
            return {"status": "accepted"}

        # Only the external operation is faked; schedule and persistence are real.
        self.c.perform = AsyncMock(side_effect=external_stop)
        pending = self.spawn(self.request("alert_stop"))
        await self.entered(gate)
        self.assertEqual(len(self.disk.writes), 1)
        self.c.async_update_listeners.reset_mock()
        closing = await self.begin_close()
        self.assertFalse(closing.done())
        gate.release.set()
        result = await self.finish(pending)
        await self.finish(closing)
        self.assertEqual(result["operation_result"], "outcome_unknown")
        self.assertFalse(result["gateway_fresh"])
        self.assertIsNone(result["alert"])
        self.assertEqual(len(self.disk.writes), 1)
        self.c.async_update_listeners.assert_not_called()
        self.c.perform.assert_awaited_once()

        replacement = await self.replacement()
        replacement.perform = AsyncMock()
        replay = await self.request("alert_stop", coordinator=replacement)
        self.assertEqual(replay["operation_result"], "outcome_unknown")
        replacement.perform.assert_not_awaited()
        self.assertEqual(len(self.disk.writes), 1)
        from homeassistant.exceptions import HomeAssistantError
        with self.assertRaisesRegex(HomeAssistantError, "different schedule inputs"):
            await self.request("timer_cancel", coordinator=replacement)

    async def save_boundary(self, number):
        gate = self.gate()
        self.c.store.gates[number] = gate
        pending = self.spawn(self.request())
        await self.entered(gate)
        self.c.async_update_listeners.reset_mock()
        closing = await self.begin_close()
        self.assertFalse(closing.done())
        self.assertTrue(self.c._settings_lock.locked())
        self.assertEqual(len(self.disk.writes), number - 1)

        async def reload_after_unload():
            await closing
            return await self.replacement()

        reloading = self.spawn(reload_after_unload())
        await self.checkpoint()
        self.assertNotIn("replacement", self.disk.loads)
        gate.release.set()
        result = await self.finish(pending)
        replacement = await self.finish(reloading)
        self.assertEqual(result["operation_result"], "outcome_unknown")
        self.assertFalse(result["gateway_fresh"])
        expected = "claimed" if number == 1 else "complete"
        ledger = replacement.settings["201"]["schedule_requests"]
        self.assertEqual(ledger["unload-request-001"]["state"], expected)
        self.assertEqual(len(self.disk.writes), number)
        self.c.async_update_listeners.assert_not_called()
        self.c.client.action.assert_not_awaited()

        replay = await self.request(coordinator=replacement)
        self.assertEqual(replay["operation_result"],
                         "outcome_unknown" if number == 1 else "replayed")
        self.assertEqual(len(self.disk.writes), number)
        await replacement.configure("201", "line", "202")
        self.assertEqual(self.disk.value["phones"]["201"]["line"], "202")
        self.assertEqual([owner for owner, _ in self.disk.writes],
                         ["old"] * number + ["replacement"])
        await self.checkpoint()
        self.assertEqual(self.disk.value["phones"]["201"]["line"], "202")

    async def test_initial_save_drains_before_same_store_replacement(self):
        await self.save_boundary(1)

    async def test_completion_save_drains_and_preserves_committed_complete(self):
        await self.save_boundary(2)

    async def cancellation_boundary(self, number):
        gate = self.gate()
        self.c.store.gates[number] = gate
        pending = self.spawn(self.request())
        await self.entered(gate)
        self.c.async_update_listeners.reset_mock()
        closing = await self.begin_close()
        for _ in range(2):
            pending.cancel()
            closing.cancel()
            await self.checkpoint()
            self.assertFalse(pending.done())
            self.assertFalse(closing.done())
            self.assertTrue(self.c._settings_lock.locked())
            self.assertEqual(self.c.store.cancelled, 0)
        gate.release.set()
        with self.assertRaises(asyncio.CancelledError):
            await self.finish(pending)
        with self.assertRaises(asyncio.CancelledError):
            await self.finish(closing)
        self.assertFalse(self.c._settings_lock.locked())
        self.assertEqual(self.c.store.cancelled, 0)
        self.assertEqual(len(self.disk.writes), number)
        self.assertEqual(self.c.settings, self.disk.value["phones"])
        self.c.async_update_listeners.assert_not_called()
        self.c.client.action.assert_not_awaited()
        replacement = await self.replacement()
        state = replacement.settings["201"]["schedule_requests"]["unload-request-001"]["state"]
        self.assertEqual(state, "claimed" if number == 1 else "complete")
        await replacement.configure("201", "line", "202")
        self.assertEqual(self.disk.writes[-1][0], "replacement")
        self.assertEqual(self.disk.value["phones"]["201"]["line"], "202")

    async def test_repeated_cancellation_does_not_release_initial_save_owner(self):
        await self.cancellation_boundary(1)

    async def test_repeated_cancellation_does_not_release_completion_save_owner(self):
        await self.cancellation_boundary(2)

    async def internal_cancellation_boundary(self, number):
        gate = self.gate()
        self.c.store.gates[number] = gate
        self.c.store.failures[number] = asyncio.CancelledError()
        pending = self.spawn(self.request())
        await self.entered(gate)
        # The candidate is still private until this particular write succeeds.
        before = copy.deepcopy(self.c.settings)
        disk_before = copy.deepcopy(self.disk.value)
        self.assertEqual(before, disk_before["phones"])
        self.c.async_update_listeners.reset_mock()
        gate.release.set()
        with self.assertRaises(asyncio.CancelledError):
            await self.finish(pending)
        self.assertFalse(self.c._settings_lock.locked())
        self.assertFalse(self.c._closing)
        self.assertEqual(self.c.settings, before)
        self.assertEqual(self.disk.value, disk_before)
        self.assertEqual(len(self.disk.writes), number - 1)
        self.c.async_update_listeners.assert_not_called()
        self.c.client.action.assert_not_awaited()
        if number == 1:
            self.assertIsNone(self.c.settings["201"]["timer_deadline"])
            self.assertEqual(self.c.settings["201"]["schedule_requests"], {})
        else:
            ledger = self.c.settings["201"]["schedule_requests"]
            self.assertEqual(ledger["unload-request-001"]["state"], "claimed")
            replay = await self.request()
            self.assertEqual(replay["operation_result"], "outcome_unknown")
            self.assertEqual(len(self.disk.writes), 1)

    async def test_internal_initial_save_cancellation_leaves_timer_unarmed(self):
        await self.internal_cancellation_boundary(1)

    async def test_internal_completion_save_cancellation_keeps_claim(self):
        await self.internal_cancellation_boundary(2)

    async def test_actual_entry_unload_waits_for_inflight_store_save(self):
        from custom_components.cisco_phone_control import async_unload_entry

        gate = self.gate()
        self.c.store.gates[1] = gate
        pending = self.spawn(self.request())
        await self.entered(gate)
        with patch.object(self.hass.config_entries, "async_unload_platforms",
                          new=AsyncMock(return_value=True)) as platforms:
            unloading = self.spawn(async_unload_entry(self.hass, self.entry))
            await self.checkpoint()
            self.assertTrue(self.c._closing)
            self.assertFalse(unloading.done())
            gate.release.set()
            self.assertTrue(await self.finish(unloading))
            self.assertEqual((await self.finish(pending))["operation_result"],
                             "outcome_unknown")
            platforms.assert_awaited_once()
        replacement = await self.replacement()
        self.assertEqual(replacement.settings["201"]["schedule_requests"]
                         ["unload-request-001"]["state"], "claimed")

    async def test_failed_platform_unload_leaves_coordinator_usable(self):
        from custom_components.cisco_phone_control import async_unload_entry

        with patch.object(self.hass.config_entries, "async_unload_platforms",
                          new=AsyncMock(return_value=False)):
            self.assertFalse(await async_unload_entry(self.hass, self.entry))
        self.assertFalse(self.c._closing)
        result = await self.request()
        self.assertEqual(result["operation_result"], "applied")
        self.assertEqual(len(self.disk.writes), 2)

    async def test_failed_initial_save_rolls_back_and_sends_no_external_stop(self):
        from homeassistant.exceptions import HomeAssistantError

        self.data["phones"][0]["alert"] = {
            "id": "a" * 32, "line": "201", "kind": "alarm",
            "state": "ringing", "reason": None}
        self.c.perform = AsyncMock()
        self.c.store.failures[1] = OSError("synthetic store failure")
        before = copy.deepcopy(self.c.settings)
        with self.assertRaisesRegex(HomeAssistantError, "no operation was sent"):
            await self.request("alert_stop")
        self.assertEqual(self.c.settings, before)
        self.assertEqual(self.disk.value["phones"], before)
        self.assertEqual(self.disk.writes, [])
        self.c.perform.assert_not_awaited()


if __name__ == "__main__":
    unittest.main()
