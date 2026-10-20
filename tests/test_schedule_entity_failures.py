"""Real HA coordinator tests for surfaced Store failures; no physical calls.

The fake Store models a save that raises or returns normally, not filesystem
durability or Home Assistant's internally suppressed storage errors.
"""
import asyncio
import copy
from datetime import datetime, time, timedelta, timezone
import importlib.util
import tempfile
from types import MappingProxyType, SimpleNamespace
import unittest
from unittest.mock import AsyncMock, Mock, patch

from test_phone_controls import snapshot

READY = importlib.util.find_spec("homeassistant") is not None
CLOCK = "custom_components.cisco_phone_control.coordinator.dt_util"


@unittest.skipUnless(READY, "requires optional pinned Home Assistant runtime")
class ScheduleEntityFailureTests(unittest.IsolatedAsyncioTestCase):
    async def asyncSetUp(self):
        from homeassistant import loader
        from homeassistant.config_entries import ConfigEntries, ConfigEntry
        from homeassistant.core import HomeAssistant
        from custom_components.cisco_phone_control.coordinator import PhoneCoordinator

        self.tmp = tempfile.TemporaryDirectory()
        self.hass = HomeAssistant(self.tmp.name)
        self.hass.config_entries = ConfigEntries(self.hass, {})
        loader.async_setup(self.hass)
        entry = ConfigEntry(
            domain="cisco_phone_control", title="Synthetic gateway",
            unique_id="http://127.0.0.1:18084", version=1, minor_version=1,
            source="user", options={}, discovery_keys=MappingProxyType({}),
            subentries_data=[],
            data={"url": "http://127.0.0.1:18084", "token": "synthetic-token"},
        )
        self.data = snapshot()
        self.client = SimpleNamespace(
            snapshot=AsyncMock(side_effect=lambda: copy.deepcopy(self.data)),
            action=AsyncMock(return_value={"status": "accepted"}),
        )
        self.c = PhoneCoordinator(self.hass, entry, self.client)
        self.c.prepare_inventory(self.data)
        self.c.async_set_updated_data(copy.deepcopy(self.data))
        entry.runtime_data = self.c
        self.c.store = SimpleNamespace(
            async_save=AsyncMock(side_effect=self.save),
            async_load=AsyncMock(return_value=None),
        )
        self.listeners = Mock()
        self.c.async_update_listeners = self.listeners
        self.c.async_request_refresh = AsyncMock()
        self.now = datetime(2026, 10, 19, 7, 0, 1, tzinfo=timezone.utc)
        self.c._started_at = self.now.timestamp() - 3600
        self.settings = self.c.settings
        self.row = self.settings["201"]
        self.saved = {"phones": copy.deepcopy(self.settings)}

    async def asyncTearDown(self):
        await self.c.async_close()
        await self.hass.async_stop()
        self.tmp.cleanup()

    async def save(self, payload):
        self.saved = copy.deepcopy(payload)

    def baseline(self, **values):
        self.row.update(values)
        self.saved = {"phones": copy.deepcopy(self.settings)}
        return copy.deepcopy(self.row)

    def assert_unchanged(self, before):
        self.assertIs(self.c.settings, self.settings)
        self.assertIs(self.c.settings["201"], self.row)
        self.assertEqual(self.row, before)
        self.assertEqual(self.saved["phones"]["201"], before)
        self.listeners.assert_not_called()
        self.client.snapshot.assert_not_awaited()
        self.client.action.assert_not_awaited()

    async def tick(self, when):
        with patch(CLOCK + ".now", return_value=when):
            await self.c._tick(when)

    async def test_failed_timer_start_does_not_fire_after_storage_recovers(self):
        before = self.baseline(line="202", timer_minutes=1)
        error = OSError("synthetic save failure")
        self.c.store.async_save.side_effect = error
        with patch(CLOCK + ".utcnow", return_value=self.now):
            with self.assertRaises(OSError) as raised:
                await self.c.timer("201", True)
        self.assertIs(raised.exception, error)
        self.assert_unchanged(before)
        attempted = self.c.store.async_save.await_args.args[0]["phones"]["201"]
        self.assertEqual(attempted["timer_deadline"], self.now.timestamp() + 60)
        self.assertEqual(attempted["timer_line"], "202")
        self.assertIsNotNone(attempted["timer_id"])
        self.assertIsNot(attempted, self.row)
        attempted["weekdays"].append(6)
        self.assertEqual(self.row, before)
        self.c.store.async_save.side_effect = self.save
        await self.tick(self.now + timedelta(minutes=1))
        self.client.action.assert_not_awaited()
        self.assertIsNone(self.row["timer_deadline"])
        self.assertIsNone(self.row["timer_id"])

    async def test_failed_alarm_enable_does_not_capture_owner_or_fire(self):
        from custom_components.cisco_phone_control.switch import PhoneSwitch

        before = self.baseline(line="202")
        self.c.store.async_save.side_effect = OSError("synthetic save failure")
        switch = PhoneSwitch(self.c, "201", "alarm_enabled", "Alarm enabled")
        with self.assertRaises(OSError):
            await switch.async_turn_on()
        self.assert_unchanged(before)
        attempted = self.c.store.async_save.await_args.args[0]["phones"]["201"]
        self.assertTrue(attempted["alarm_enabled"])
        self.assertTrue(attempted["alarm_configured"])
        self.assertEqual(attempted["alarm_line"], "202")
        self.assertIsNot(attempted, self.row)
        self.c.store.async_save.side_effect = self.save
        await self.tick(self.now)
        self.client.action.assert_not_awaited()
        self.assertFalse(self.row["alarm_configured"])

    async def test_failed_alarm_time_edit_does_not_fire_at_requested_time(self):
        from custom_components.cisco_phone_control.time import AlarmTime

        before = self.baseline(
            alarm_enabled=True, alarm_configured=True,
            alarm_line="202", alarm_time="08:00", weekdays=[0],
        )
        self.c.store.async_save.side_effect = OSError("synthetic save failure")
        field = AlarmTime(self.c, "201", "alarm_time", "Alarm time")
        with self.assertRaises(OSError):
            await field.async_set_value(time(7, 0))
        self.assert_unchanged(before)
        self.c.store.async_save.side_effect = self.save
        await self.tick(self.now)
        self.client.action.assert_not_awaited()
        await self.tick(self.now + timedelta(hours=1))
        self.client.action.assert_awaited_once()
        self.assertEqual(self.client.action.await_args.args[:3], (
            "201", "alert", {"kind": "alarm", "line": "202"},
        ))

    async def test_failed_weekday_edit_does_not_fire_on_requested_day(self):
        from custom_components.cisco_phone_control.switch import PhoneSwitch

        before = self.baseline(
            alarm_enabled=True, alarm_configured=True,
            alarm_line="202", weekdays=[1],
        )
        self.c.store.async_save.side_effect = OSError("synthetic save failure")
        switch = PhoneSwitch(self.c, "201", "weekday_0", "Alarm Monday")
        with self.assertRaises(OSError):
            await switch.async_turn_on()
        self.assert_unchanged(before)
        self.c.store.async_save.side_effect = self.save
        await self.tick(self.now)
        self.client.action.assert_not_awaited()
        await self.tick(self.now + timedelta(days=1))
        self.client.action.assert_awaited_once()
        self.assertEqual(self.client.action.await_args.args[:3], (
            "201", "alert", {"kind": "alarm", "line": "202"},
        ))

    async def test_failed_first_time_or_weekday_edit_keeps_alarm_unconfigured(self):
        before = self.baseline(line="202")
        self.c.store.async_save.side_effect = OSError("synthetic save failure")
        for key, value in (("alarm_time", "09:15"), ("weekdays", [6])):
            with self.subTest(key=key):
                with self.assertRaises(OSError):
                    await self.c.configure("201", key, value)
                self.assert_unchanged(before)

    async def assert_stop_retry(self, kind):
        values = (
            {"alarm_enabled": True, "alarm_configured": True}
            if kind == "alarm" else
            {"timer_deadline": self.now.timestamp() + 60,
             "timer_id": "b" * 32, "timer_line": "202", "status": "Timer running"}
        )
        before = self.baseline(**values)
        self.data["phones"][0]["alert"] = {
            "id": "a" * 32, "kind": kind, "line": "202",
            "state": "ringing", "reason": None,
        }

        async def cancel():
            if kind == "alarm":
                await self.c.configure("201", "alarm_enabled", False)
            else:
                await self.c.timer("201", False)

        self.c.store.async_save.side_effect = OSError("synthetic save failure")
        with self.assertRaises(OSError):
            await cancel()
        self.assert_unchanged(before)
        self.client.snapshot.assert_not_awaited()

        async def stop_after_save(*args):
            key = "alarm_enabled" if kind == "alarm" else "timer_deadline"
            expected = False if kind == "alarm" else None
            self.assertIs(self.saved["phones"]["201"][key], expected)
            self.assertIs(self.row[key], expected)
            self.assertEqual(args, ("201", "alert_stop", {"alert_id": "a" * 32}, None))
            return {"status": "accepted"}

        self.c.store.async_save.side_effect = self.save
        self.client.action.side_effect = stop_after_save
        await cancel()
        self.client.action.assert_awaited_once()
        self.assertIs(self.c.settings, self.settings)
        self.assertIs(self.c.settings["201"], self.row)
        await self.tick(self.now if kind == "alarm" else self.now + timedelta(minutes=1))
        self.client.action.assert_awaited_once()

    async def test_failed_alarm_disable_keeps_enabled_then_retry_saves_before_stop(self):
        await self.assert_stop_retry("alarm")

    async def test_failed_timer_cancel_keeps_deadline_then_retry_saves_before_stop(self):
        await self.assert_stop_retry("timer")

    async def test_caller_cancellation_waits_for_write_then_publishes_saved_state(self):
        initial = copy.deepcopy(self.row)
        for operation in ("enable", "disable", "start", "cancel"):
            with self.subTest(operation=operation):
                self.row.clear()
                self.row.update(copy.deepcopy(initial))
                before = self.baseline(
                    alarm_enabled=operation == "disable",
                    timer_deadline=self.now.timestamp() + 60 if operation == "cancel" else None,
                    timer_id="b" * 32 if operation == "cancel" else None,
                )
                entered, release = asyncio.Event(), asyncio.Event()
                self.listeners.reset_mock()
                self.client.action.reset_mock()

                async def blocked_save(payload):
                    entered.set()
                    await release.wait()
                    await self.save(payload)

                self.c.store.async_save.side_effect = blocked_save
                change = (
                    self.c.configure("201", "alarm_enabled", operation == "enable")
                    if operation in ("enable", "disable") else
                    self.c.timer("201", operation == "start")
                )
                task = asyncio.create_task(change)
                try:
                    await asyncio.wait_for(entered.wait(), 1)
                    self.assert_unchanged(before)
                    task.cancel()
                    await asyncio.sleep(0)
                    task.cancel()
                    await asyncio.sleep(0)
                    self.assertFalse(task.done())
                    self.assertTrue(self.c._settings_lock.locked())
                    self.assert_unchanged(before)
                    release.set()
                    with self.assertRaises(asyncio.CancelledError):
                        await asyncio.wait_for(task, 1)
                finally:
                    release.set()
                    await asyncio.gather(task, return_exceptions=True)
                self.assertNotEqual(self.row, before)
                self.assertEqual(self.row, self.saved["phones"]["201"])
                self.assertIs(self.c.settings, self.settings)
                self.assertIs(self.c.settings["201"], self.row)
                self.listeners.assert_called_once_with()
                self.client.snapshot.assert_not_awaited()
                self.client.action.assert_not_awaited()
                self.assertFalse(self.c._settings_lock.locked())
                self.c.store.async_save.side_effect = self.save
                await asyncio.wait_for(self.c.configure("201", "dial_number", "600"), 1)
                self.assertEqual(self.saved["phones"]["201"]["dial_number"], "600")

    async def test_write_cancellation_preserves_original_state(self):
        before = self.baseline(line="202")
        self.c.store.async_save.side_effect = asyncio.CancelledError
        for operation in (
            lambda: self.c.configure("201", "alarm_enabled", True),
            lambda: self.c.timer("201", True),
        ):
            with self.assertRaises(asyncio.CancelledError):
                await operation()
            self.assert_unchanged(before)
            self.assertFalse(self.c._settings_lock.locked())

    async def test_cancelled_queued_changes_never_start_a_write(self):
        before = self.baseline()
        await self.c._settings_lock.acquire()
        tasks = [
            asyncio.create_task(self.c.configure("201", "alarm_enabled", True)),
            asyncio.create_task(self.c.timer("201", True)),
        ]
        try:
            await asyncio.sleep(0)
            for task in tasks:
                task.cancel()
            results = await asyncio.gather(*tasks, return_exceptions=True)
            self.assertTrue(all(isinstance(result, asyncio.CancelledError) for result in results))
            self.c.store.async_save.assert_not_awaited()
            self.assert_unchanged(before)
        finally:
            self.c._settings_lock.release()
            for task in tasks:
                task.cancel()
            await asyncio.gather(*tasks, return_exceptions=True)

    async def test_close_fences_queued_configure_and_timer_before_any_write(self):
        from homeassistant.exceptions import HomeAssistantError

        before = self.baseline()
        await self.c._settings_lock.acquire()
        tasks = [
            asyncio.create_task(self.c.configure("201", "alarm_enabled", True)),
            asyncio.create_task(self.c.timer("201", True)),
        ]
        close = None
        try:
            await asyncio.sleep(0)
            close = asyncio.create_task(self.c.async_close())
            await asyncio.sleep(0)
            self.assertTrue(self.c._closing)
            self.assertFalse(close.done())
        finally:
            self.c._settings_lock.release()
        results = await asyncio.wait_for(asyncio.gather(*tasks, return_exceptions=True), 1)
        await asyncio.wait_for(close, 1)
        self.assertTrue(all(isinstance(result, HomeAssistantError) for result in results))
        self.c.store.async_save.assert_not_awaited()
        self.assert_unchanged(before)

    async def test_close_during_save_publishes_but_suppresses_listeners_and_stop(self):
        from homeassistant.exceptions import HomeAssistantError

        initial = copy.deepcopy(self.row)
        for kind, cancel_caller in (("alarm", False), ("timer", False), ("alarm", True)):
            with self.subTest(kind=kind, cancel_caller=cancel_caller):
                self.c._closing = False
                self.row.clear()
                self.row.update(copy.deepcopy(initial))
                before = self.baseline(
                    alarm_enabled=True, timer_deadline=self.now.timestamp() + 60,
                    timer_id="b" * 32,
                )
                self.listeners.reset_mock()
                entered, release = asyncio.Event(), asyncio.Event()

                async def blocked_save(payload):
                    entered.set()
                    await release.wait()
                    await self.save(payload)

                self.c.store.async_save.side_effect = blocked_save
                operation = (self.c.configure("201", "alarm_enabled", False)
                             if kind == "alarm" else self.c.timer("201", False))
                task = asyncio.create_task(operation)
                close = None
                try:
                    await asyncio.wait_for(entered.wait(), 1)
                    close = asyncio.create_task(self.c.async_close())
                    await asyncio.sleep(0)
                    self.assertTrue(self.c._closing)
                    self.assertFalse(close.done())
                    self.assert_unchanged(before)
                    if cancel_caller:
                        task.cancel()
                        await asyncio.sleep(0)
                    release.set()
                    error = asyncio.CancelledError if cancel_caller else HomeAssistantError
                    with self.assertRaises(error):
                        await asyncio.wait_for(task, 1)
                    await asyncio.wait_for(close, 1)
                finally:
                    release.set()
                    await asyncio.gather(*(t for t in (task, close) if t is not None),
                                         return_exceptions=True)
                self.assertNotEqual(self.row, before)
                self.assertEqual(self.row, self.saved["phones"]["201"])
                self.assertIs(self.c.settings, self.settings)
                self.assertIs(self.c.settings["201"], self.row)
                self.listeners.assert_not_called()
                self.client.snapshot.assert_not_awaited()
                self.client.action.assert_not_awaited()

    async def test_stop_failure_does_not_undo_successful_disable_or_cancel_save(self):
        from homeassistant.exceptions import HomeAssistantError

        for kind in ("alarm", "timer"):
            with self.subTest(kind=kind):
                self.baseline(alarm_enabled=True, timer_deadline=self.now.timestamp() + 60,
                              timer_id="b" * 32)
                with patch.object(self.c, "stop_alert", new=AsyncMock(
                    side_effect=HomeAssistantError("synthetic stop failure")
                )):
                    with self.assertRaises(HomeAssistantError):
                        if kind == "alarm":
                            await self.c.configure("201", "alarm_enabled", False)
                        else:
                            await self.c.timer("201", False)
                key = "alarm_enabled" if kind == "alarm" else "timer_deadline"
                expected = False if kind == "alarm" else None
                self.assertIs(self.row[key], expected)
                self.assertIs(self.saved["phones"]["201"][key], expected)

    async def test_queued_edit_survives_previous_save_failure(self):
        before = self.baseline(line="202")
        entered, release = asyncio.Event(), asyncio.Event()
        attempts = 0

        async def save_after_failure(payload):
            nonlocal attempts
            attempts += 1
            if attempts == 1:
                entered.set()
                await release.wait()
                raise OSError("synthetic save failure")
            await self.save(payload)

        self.c.store.async_save.side_effect = save_after_failure
        first = asyncio.create_task(self.c.configure("201", "alarm_enabled", True))
        second = None
        try:
            await asyncio.wait_for(entered.wait(), 1)
            second = asyncio.create_task(self.c.configure("201", "dial_number", "600"))
            await asyncio.sleep(0)
            self.assertFalse(second.done())
            self.assertEqual(attempts, 1)
            self.assert_unchanged(before)
            await self.tick(self.now)
            self.client.action.assert_not_awaited()
            release.set()
            with self.assertRaises(OSError):
                await asyncio.wait_for(first, 1)
            await asyncio.wait_for(second, 1)
        finally:
            release.set()
            for task in (first, second):
                if task is not None:
                    task.cancel()
            await asyncio.gather(*(t for t in (first, second) if t is not None),
                                 return_exceptions=True)
        expected = {**before, "dial_number": "600"}
        self.assertEqual(self.row, expected)
        self.assertEqual(self.saved["phones"]["201"], expected)
        self.assertIs(self.c.settings, self.settings)
        self.assertIs(self.c.settings["201"], self.row)
        await self.tick(self.now)
        self.client.action.assert_not_awaited()

    async def test_queued_successful_edits_preserve_both_changes_and_owner(self):
        self.baseline(line="202")
        entered, release = asyncio.Event(), asyncio.Event()

        async def blocked_save(payload):
            if not entered.is_set():
                entered.set()
                await release.wait()
            await self.save(payload)

        self.c.store.async_save.side_effect = blocked_save
        first = asyncio.create_task(self.c.configure("201", "alarm_time", "09:15"))
        second = None
        try:
            await asyncio.wait_for(entered.wait(), 1)
            second = asyncio.create_task(self.c.configure("201", "weekdays", [0, 2]))
            await asyncio.sleep(0)
            self.assertFalse(second.done())
            release.set()
            await asyncio.wait_for(asyncio.gather(first, second), 1)
        finally:
            release.set()
            for task in (first, second):
                if task is not None:
                    task.cancel()
            await asyncio.gather(*(t for t in (first, second) if t is not None),
                                 return_exceptions=True)
        self.assertEqual(self.row["alarm_time"], "09:15")
        self.assertEqual(self.row["weekdays"], [0, 2])
        self.assertEqual(self.row["alarm_line"], "202")
        self.assertTrue(self.row["alarm_configured"])
        self.assertEqual(self.saved["phones"]["201"], self.row)
        self.assertIs(self.c.settings, self.settings)
        self.assertIs(self.c.settings["201"], self.row)

    async def test_inventory_discovered_during_save_survives_failure_and_success(self):
        for fails, primary in ((True, "203"), (False, "205")):
            with self.subTest(fails=fails):
                entered, release = asyncio.Event(), asyncio.Event()
                before = copy.deepcopy(self.row)

                async def blocked_save(payload):
                    entered.set()
                    await release.wait()
                    if fails:
                        raise OSError("synthetic save failure")
                    await self.save(payload)

                self.c.store.async_save.side_effect = blocked_save
                task = asyncio.create_task(self.c.configure("201", "alarm_time", "09:15"))
                try:
                    await asyncio.wait_for(entered.wait(), 1)
                    discovered = copy.deepcopy(self.data)
                    phone = copy.deepcopy(discovered["phones"][0])
                    phone.update(id=primary, lines=[primary, str(int(primary) + 1)])
                    discovered["phones"].append(phone)
                    self.c.prepare_inventory(discovered)
                    new_row = self.c.settings[primary]
                    release.set()
                    if fails:
                        with self.assertRaises(OSError):
                            await asyncio.wait_for(task, 1)
                        self.assertEqual(self.row, before)
                    else:
                        await asyncio.wait_for(task, 1)
                        self.assertEqual(self.row["alarm_time"], "09:15")
                finally:
                    release.set()
                    task.cancel()
                    await asyncio.gather(task, return_exceptions=True)
                self.assertIs(self.c.settings, self.settings)
                self.assertIs(self.c.settings["201"], self.row)
                self.assertIs(self.c.settings[primary], new_row)
                self.c.store.async_save.side_effect = self.save
                await self.c.configure(primary, "dial_number", "600")
                self.assertEqual(self.saved["phones"][primary]["dial_number"], "600")
                self.assertEqual(self.saved["phones"]["201"], self.row)

    async def test_success_does_not_alias_caller_store_or_other_live_rows(self):
        self.c.prepare_inventory({"phones": [{"id": "203"}]})
        other = self.c.settings["203"]
        days = [0, 2]
        entered, release = asyncio.Event(), asyncio.Event()
        payloads = []

        async def blocked_save(payload):
            payloads.append(payload)
            entered.set()
            await release.wait()
            await self.save(payload)

        self.c.store.async_save.side_effect = blocked_save
        task = asyncio.create_task(self.c.configure("201", "weekdays", days))
        try:
            await asyncio.wait_for(entered.wait(), 1)
            days.append(4)
            self.assertEqual(payloads[0]["phones"]["201"]["weekdays"], [0, 2])
            release.set()
            await asyncio.wait_for(task, 1)
        finally:
            release.set()
            task.cancel()
            await asyncio.gather(task, return_exceptions=True)
        payloads[0]["phones"]["201"]["weekdays"].append(6)
        payloads[0]["phones"]["203"]["weekdays"].clear()
        self.assertEqual(self.row["weekdays"], [0, 2])
        self.assertEqual(other["weekdays"], [0, 1, 2, 3, 4])
        self.row["weekdays"].append(1)
        self.assertEqual(payloads[0]["phones"]["201"]["weekdays"], [0, 2, 6])
        self.assertIs(self.c.settings, self.settings)
        self.assertIs(self.c.settings["201"], self.row)
        self.assertIs(self.c.settings["203"], other)


if __name__ == "__main__":
    unittest.main()
