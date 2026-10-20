"""Real HA Store failure and executor-cancellation tests; no live services."""

import asyncio
import copy
import importlib.util
import json
from pathlib import Path
import tempfile
import threading
import traceback
import unittest
from unittest.mock import Mock, patch


READY = importlib.util.find_spec("homeassistant") is not None


@unittest.skipUnless(READY, "requires the pinned Home Assistant runtime")
class ScheduleStorageTests(unittest.IsolatedAsyncioTestCase):
    async def asyncSetUp(self):
        from homeassistant.core import HomeAssistant
        from custom_components.cisco_phone_control.storage import ScheduleStore

        self.temporary = tempfile.TemporaryDirectory()
        self.hass = HomeAssistant(self.temporary.name)
        self.store = ScheduleStore(
            self.hass, 1, "cisco_phone_control.synthetic",
            private=True, atomic_writes=True)
        self.tasks = []
        self.releases = []

    async def asyncTearDown(self):
        for release in self.releases:
            release.set()
        if self.tasks:
            await asyncio.wait_for(
                asyncio.gather(*self.tasks, return_exceptions=True), timeout=5)
        await self.hass.async_stop()
        self.temporary.cleanup()

    def spawn(self, coroutine):
        task = asyncio.create_task(coroutine)
        self.tasks.append(task)
        return task

    def coordinator(self):
        from custom_components.cisco_phone_control.coordinator import PhoneCoordinator

        # These tests isolate the real persistence/lifecycle methods. Inventory,
        # entity platforms and gateway requests are exercised by runtime tests.
        coordinator = object.__new__(PhoneCoordinator)
        coordinator.store = self.store
        coordinator.settings = {"201": {"status": "Synthetic candidate"}}
        coordinator._settings_lock = asyncio.Lock()
        coordinator._closing = False
        coordinator._tick_remove = None
        return coordinator

    def block_writer(self, *, fail=False):
        """Pause the actual executor boundary, not the async_save coroutine."""
        from homeassistant.util.file import WriteError

        entered = threading.Event()
        release = threading.Event()
        self.releases.append(release)
        original = self.store._write_prepared_data

        def blocked(mode, serialized):
            entered.set()
            if not release.wait(10):
                raise WriteError("Synthetic writer did not receive release")
            if fail:
                raise WriteError("Synthetic filesystem failure")
            original(mode, serialized)

        self.store._write_prepared_data = blocked
        return entered, release

    async def wait_for_writer(self, entered):
        self.assertTrue(await asyncio.to_thread(entered.wait, 3))

    def saved_data(self):
        return json.loads(Path(self.store.path).read_text())["data"]

    async def test_file_envelope_round_trips_through_unmodified_ha_store(self):
        from homeassistant.helpers.storage import Store

        payload = {"phones": {"201": {"status": "Synthetic schedule"}}}
        await self.store.async_save(payload)
        envelope = json.loads(Path(self.store.path).read_text())
        self.assertEqual(envelope, {
            "version": 1, "minor_version": 1,
            "key": "cisco_phone_control.synthetic", "data": payload,
        })
        self.assertEqual(Path(self.store.path).stat().st_mode & 0o777, 0o600)
        self.assertTrue(self.store._atomic_writes)
        reader = Store(self.hass, 1, self.store.key)
        self.assertEqual(await reader.async_load(), payload)

    async def test_executor_serialization_keeps_ha_encoder_and_envelope(self):
        from custom_components.cisco_phone_control.storage import ScheduleStore

        marker = object()

        class SyntheticEncoder(json.JSONEncoder):
            def default(self, value):
                if value is marker:
                    return "Synthetic encoded value"
                return super().default(value)

        self.store = ScheduleStore(
            self.hass, 1, self.store.key, private=True, atomic_writes=True,
            encoder=SyntheticEncoder, serialize_in_event_loop=False)
        await self.store.async_save({"phones": {"201": {"status": marker}}})
        envelope = json.loads(Path(self.store.path).read_text())
        self.assertEqual(envelope, {
            "version": 1, "minor_version": 1,
            "key": "cisco_phone_control.synthetic",
            "data": {"phones": {"201": {"status": "Synthetic encoded value"}}},
        })

    async def test_real_store_does_not_swallow_filesystem_failure(self):
        from homeassistant.util.file import WriteError
        from custom_components.cisco_phone_control.storage import ScheduleStorageError

        original = {"phones": {"201": {"status": "Before"}}}
        await self.store.async_save(original)
        self.store._write_prepared_data = Mock(
            side_effect=WriteError("Synthetic filesystem failure"))
        with self.assertRaisesRegex(ScheduleStorageError, "storage write failed"):
            await self.store.async_save({"phones": {"201": {"status": "After"}}})
        self.assertEqual(self.saved_data(), original)

    async def test_real_mkdir_failures_report_bounded_error_without_private_path(self):
        from custom_components.cisco_phone_control.storage import ScheduleStorageError

        original = {"phones": {"201": {"status": "Before"}}}
        await self.store.async_save(original)
        private_directory = str(Path(self.store.path).parent)
        for failure in (
            PermissionError(13, "Synthetic permission failure", private_directory),
            OSError(5, "Synthetic filesystem failure", private_directory),
        ):
            with self.subTest(error=type(failure).__name__):
                with patch("homeassistant.helpers.storage.os.makedirs",
                           side_effect=failure) as mkdir:
                    with self.assertRaises(ScheduleStorageError) as caught:
                        await self.store.async_save({"phones": {}})
                mkdir.assert_called_once_with(private_directory, exist_ok=True)
                error = caught.exception
                self.assertEqual(str(error), "Schedule storage write failed")
                self.assertTrue(error.__suppress_context__)
                self.assertIsNone(error.__cause__)
                self.assertNotIn(private_directory, "".join(traceback.format_exception(error)))
                self.assertEqual(self.saved_data(), original)

    async def test_real_store_does_not_swallow_serialization_failure(self):
        from custom_components.cisco_phone_control.storage import ScheduleStorageError

        with self.assertRaisesRegex(ScheduleStorageError, "storage write failed"):
            await self.store.async_save({"phones": object()})
        self.assertFalse(Path(self.store.path).exists())

    async def test_stopping_refuses_instead_of_acknowledging_deferred_write(self):
        from homeassistant.core import CoreState
        from custom_components.cisco_phone_control.storage import ScheduleStorageError

        self.hass.state = CoreState.stopping
        writer = self.store._write_prepared_data = Mock()
        try:
            with self.assertRaisesRegex(ScheduleStorageError, "storage is stopping"):
                await self.store.async_save({"phones": {}})
            writer.assert_not_called()
            self.assertIsNone(self.store._data)
            self.assertIsNone(self.store._unsub_final_write_listener)
        finally:
            self.hass.state = CoreState.not_running

    async def test_read_only_store_cannot_acknowledge_a_discarded_write(self):
        from custom_components.cisco_phone_control.storage import ScheduleStorageError

        self.store.make_read_only()
        with self.assertRaisesRegex(ScheduleStorageError, "read-only"):
            await self.store.async_save({"phones": {}})
        self.assertFalse(Path(self.store.path).exists())

    async def test_direct_store_cancellation_retains_lock_until_executor_finishes(self):
        entered, release = self.block_writer()
        payload = {"phones": {"201": {"status": "Written before cancellation"}}}
        task = self.spawn(self.store.async_save(payload))
        await self.wait_for_writer(entered)
        task.cancel()
        await asyncio.sleep(0)
        task.cancel()
        await asyncio.sleep(0)
        self.assertFalse(task.done())
        self.assertTrue(self.store._write_lock.locked())
        release.set()
        with self.assertRaises(asyncio.CancelledError):
            await task
        self.assertFalse(self.store._write_lock.locked())
        self.assertEqual(self.saved_data(), payload)

    async def test_candidate_helper_defers_repeated_cancellation_and_freezes_payload(self):
        coordinator = self.coordinator()
        entered, release = self.block_writer()
        payload = {"phones": copy.deepcopy(coordinator.settings)}

        async def commit_candidate():
            async with coordinator._settings_lock:
                return await coordinator._persist_settings(payload)

        task = self.spawn(commit_candidate())
        await self.wait_for_writer(entered)
        expected = copy.deepcopy(payload)
        payload["phones"]["201"]["status"] = "Changed by caller"
        task.cancel()
        await asyncio.sleep(0)
        task.cancel()
        await asyncio.sleep(0)
        self.assertFalse(task.done())
        self.assertTrue(coordinator._settings_lock.locked())
        release.set()
        self.assertIs(await task, True)
        self.assertEqual(self.saved_data(), expected)

    async def test_save_wrapper_propagates_cancellation_only_after_write(self):
        coordinator = self.coordinator()
        entered, release = self.block_writer()

        async def save():
            async with coordinator._settings_lock:
                await coordinator._save()

        task = self.spawn(save())
        await self.wait_for_writer(entered)
        task.cancel()
        await asyncio.sleep(0)
        self.assertTrue(coordinator._settings_lock.locked())
        release.set()
        with self.assertRaises(asyncio.CancelledError):
            await task
        self.assertEqual(self.saved_data(), {"phones": coordinator.settings})
        self.assertFalse(coordinator._settings_lock.locked())

    async def test_internal_save_task_cancellation_is_not_success(self):
        coordinator = self.coordinator()
        entered, release = self.block_writer()
        internal = []
        original_save = self.store.async_save

        async def record_save(payload):
            internal.append(asyncio.current_task())
            await original_save(payload)

        self.store.async_save = record_save

        async def save():
            async with coordinator._settings_lock:
                return await coordinator._persist_settings({"phones": coordinator.settings})

        task = self.spawn(save())
        await self.wait_for_writer(entered)
        internal[0].cancel()
        await asyncio.sleep(0)
        self.assertFalse(task.done())
        self.assertTrue(coordinator._settings_lock.locked())
        release.set()
        with self.assertRaises(asyncio.CancelledError):
            await task
        self.assertEqual(self.saved_data(), {"phones": coordinator.settings})

    async def test_write_failure_after_cancellation_never_returns_success(self):
        from custom_components.cisco_phone_control.storage import ScheduleStorageError

        coordinator = self.coordinator()
        entered, release = self.block_writer(fail=True)

        async def save():
            async with coordinator._settings_lock:
                return await coordinator._persist_settings({"phones": coordinator.settings})

        task = self.spawn(save())
        await self.wait_for_writer(entered)
        task.cancel()
        await asyncio.sleep(0)
        release.set()
        with self.assertRaises(ScheduleStorageError):
            await task
        self.assertFalse(Path(self.store.path).exists())
        self.assertFalse(coordinator._settings_lock.locked())

    async def test_close_drains_cancelled_writer_before_same_key_reload(self):
        from custom_components.cisco_phone_control.storage import ScheduleStore

        coordinator = self.coordinator()
        entered, release = self.block_writer()
        before_reload = {"phones": copy.deepcopy(coordinator.settings)}
        after_reload = {"phones": {"201": {"status": "Replacement instance"}}}
        loaded = []

        async def save():
            async with coordinator._settings_lock:
                await coordinator._save()

        async def unload_then_reload():
            await coordinator.async_close()
            replacement = ScheduleStore(
                self.hass, 1, self.store.key, private=True, atomic_writes=True)
            loaded.append(await replacement.async_load())
            await replacement.async_save(after_reload)

        writer = self.spawn(save())
        await self.wait_for_writer(entered)
        writer.cancel()
        reloader = self.spawn(unload_then_reload())
        await asyncio.sleep(0)
        self.assertTrue(coordinator._closing)
        self.assertFalse(reloader.done())
        self.assertEqual(loaded, [])
        release.set()
        with self.assertRaises(asyncio.CancelledError):
            await writer
        await reloader
        self.assertEqual(loaded, [before_reload])
        self.assertEqual(self.saved_data(), after_reload)

    async def test_ha_background_cancellation_cannot_release_running_executor_write(self):
        from custom_components.cisco_phone_control.storage import ScheduleStore

        coordinator = self.coordinator()
        entered, release = self.block_writer()
        before_reload = {"phones": copy.deepcopy(coordinator.settings)}
        after_reload = {"phones": {"201": {"status": "After HA cancellation"}}}
        completions = []
        loaded = []
        submit = self.hass.loop.run_in_executor
        blocked_writer = self.store._write_prepared_data

        def record_completion(executor, function, *args):
            future = submit(executor, function, *args)
            if function is blocked_writer:
                completions.append(future)
            return future

        async def save():
            async with coordinator._settings_lock:
                await coordinator._save()

        async def unload_then_reload():
            await coordinator.async_close()
            replacement = ScheduleStore(
                self.hass, 1, self.store.key, private=True, atomic_writes=True)
            loaded.append(await replacement.async_load())
            await replacement.async_save(after_reload)

        with patch.object(self.hass.loop, "run_in_executor", side_effect=record_completion):
            writer = self.hass.async_create_background_task(save(), "synthetic-schedule-save")
            self.tasks.append(writer)
            await self.wait_for_writer(entered)
            self.assertEqual(len(completions), 1)
            completion = completions[0]
            self.assertNotIn(completion, self.hass._background_tasks)
            self.assertNotIn(completion, self.hass._tasks)
            self.assertIn(writer, self.hass._background_tasks)

            # Also cancel an actual HA-registered executor Future. Its thread
            # remains alive, illustrating why the schedule completion is private.
            other_entered = threading.Event()
            other_finished = threading.Event()

            def other_job():
                other_entered.set()
                release.wait(10)
                other_finished.set()

            other = self.hass.async_add_executor_job(other_job)
            await self.wait_for_writer(other_entered)
            self.assertIn(other, self.hass._background_tasks)
            for task in tuple(self.hass._background_tasks):
                task.cancel("Synthetic Home Assistant shutdown")
            await asyncio.sleep(0)
            self.assertTrue(other.cancelled())
            self.assertFalse(other_finished.is_set())
            self.assertFalse(completion.done())
            self.assertFalse(writer.done())
            self.assertTrue(self.store._write_lock.locked())
            self.assertTrue(coordinator._settings_lock.locked())

            reloader = self.spawn(unload_then_reload())
            await asyncio.sleep(0)
            self.assertFalse(reloader.done())
            self.assertEqual(loaded, [])
            release.set()
            with self.assertRaises(asyncio.CancelledError):
                await writer
            await reloader
            self.assertTrue(await asyncio.to_thread(other_finished.wait, 3))
        self.assertTrue(completion.done())
        self.assertFalse(completion.cancelled())
        self.assertEqual(loaded, [before_reload])
        self.assertEqual(self.saved_data(), after_reload)

    async def test_cancelled_close_waits_for_writer_then_propagates_cancellation(self):
        coordinator = self.coordinator()
        entered, release = self.block_writer()

        async def save():
            async with coordinator._settings_lock:
                await coordinator._save()

        writer = self.spawn(save())
        await self.wait_for_writer(entered)
        closer = self.spawn(coordinator.async_close())
        await asyncio.sleep(0)
        closer.cancel()
        await asyncio.sleep(0)
        closer.cancel()
        await asyncio.sleep(0)
        self.assertTrue(coordinator._closing)
        self.assertFalse(closer.done())
        self.assertTrue(coordinator._settings_lock.locked())
        self.assertFalse(writer.done())
        release.set()
        await writer
        with self.assertRaises(asyncio.CancelledError):
            await closer
        self.assertEqual(self.saved_data(), {"phones": coordinator.settings})
        self.assertFalse(coordinator._settings_lock.locked())
        await coordinator.async_close()

    async def test_closed_coordinator_refuses_any_new_save(self):
        from homeassistant.exceptions import HomeAssistantError

        coordinator = self.coordinator()
        await coordinator.async_close()
        writer = self.store._write_prepared_data = Mock()
        with self.assertRaisesRegex(HomeAssistantError, "unloading"):
            async with coordinator._settings_lock:
                await coordinator._persist_settings({"phones": {}})
        writer.assert_not_called()


if __name__ == "__main__":
    unittest.main()
