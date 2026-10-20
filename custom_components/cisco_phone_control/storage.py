"""Acknowledged schedule writes using Home Assistant's existing file format."""

import asyncio

from homeassistant.core import CoreState
from homeassistant.exceptions import HomeAssistantError
from homeassistant.helpers import json as json_helper
from homeassistant.helpers.storage import Store
from homeassistant.util.file import WriteError
from homeassistant.util.json import SerializationError


class ScheduleStorageError(HomeAssistantError):
    """A schedule write did not complete successfully."""


async def async_drain_task(task: asyncio.Future) -> bool:
    """Drain owned completion; return whether its caller requested cancellation."""
    cancelled = False
    while not task.done():
        try:
            # Unlike awaiting the task directly, wait() never propagates
            # caller cancellation into the task's executor work.
            await asyncio.wait((task,))
        except asyncio.CancelledError:
            cancelled = True
    # Retrieve failures, including internal cancellation, before acknowledging
    # completion. Internal cancellation is never a successful deferred request.
    task.result()
    return cancelled


class ScheduleStore(Store):
    """Preserve Store's envelope while making write failures observable.

    Store normally logs serialization and filesystem failures, and defers saves
    during HA shutdown. Neither behavior acknowledges a schedule claim. The
    coordinator also keeps each save task alive until its executor work settles.
    """

    async def async_save(self, data) -> None:
        if self.hass.state is CoreState.stopping:
            raise ScheduleStorageError("Schedule storage is stopping")
        if self._read_only:
            raise ScheduleStorageError("Schedule storage is read-only")
        await super().async_save(data)

    async def _async_write_data(self, data) -> None:
        try:
            # Match Store's serialization choices and existing file writer.
            # Its caller still owns Store's lock and cache invalidation.
            if self._serialize_in_event_loop:
                if "data_func" in data:
                    data["data"] = data.pop("data_func")()
                mode, serialized = json_helper.prepare_save_json(data, encoder=self._encoder)
                write = self.hass.loop.run_in_executor(
                    None, self._write_prepared_data, mode, serialized)
            else:
                write = self.hass.loop.run_in_executor(None, self._write_data, data)

            # HA cancels the Futures registered by async_add_executor_job at
            # shutdown, even when their threads are still running. Keep this
            # completion Future private and never propagate cancellation to it.
            cancelled = await async_drain_task(write)
        except (SerializationError, WriteError, OSError):
            # Store's caller logs and swallows WriteError/SerializationError.
            # A distinct exception keeps this failure observable.
            raise ScheduleStorageError("Schedule storage write failed") from None
        if cancelled:
            # Even direct cancellation of a Store save must retain its write
            # lock until the executor finishes. Its caller still sees failure.
            raise asyncio.CancelledError
