"""Real HA managed-player API behavior against a modeled gateway."""
from pathlib import Path
import shutil
from types import SimpleNamespace
import unittest
from unittest.mock import AsyncMock, patch

from test_phone_media import full_media_snapshot, playing
import test_phone_media_runtime as legacy_runtime


@unittest.skipUnless(legacy_runtime.READY, "requires optional pinned Home Assistant runtime")
class FullMediaRuntimeTests(unittest.IsolatedAsyncioTestCase):
    async def asyncSetUp(self):
        await legacy_runtime.PhoneMediaRuntimeTests.asyncSetUp(self)
        self.data = full_media_snapshot()
        self.player.hass = self.hass
        self.player.entity_id = "media_player.example_phone_radio"
        self.publish()

    asyncTearDown = legacy_runtime.PhoneMediaRuntimeTests.asyncTearDown
    publish = legacy_runtime.PhoneMediaRuntimeTests.publish

    def activate(self):
        self.data = full_media_snapshot(active=True)
        self.publish()

    async def test_features_and_seek_follow_fresh_capabilities(self):
        from homeassistant.components.media_player import MediaPlayerEntityFeature as F
        features = self.player.supported_features
        for feature in (F.PAUSE, F.PLAY, F.VOLUME_SET, F.VOLUME_MUTE, F.NEXT_TRACK,
                        F.PREVIOUS_TRACK, F.MEDIA_ENQUEUE, F.CLEAR_PLAYLIST):
            self.assertTrue(features & feature)
        self.assertFalse(features & F.SEEK)
        self.activate()
        self.assertTrue(self.player.supported_features & F.SEEK)
        self.data["phones"][0]["player"]["capabilities"] = ["play"]
        self.publish()
        self.assertFalse(self.player.supported_features & F.PAUSE)
        self.assertFalse(self.player.supported_features & F.SEEK)

    async def test_http_audio_music_assistant_metadata_and_enqueue(self):
        url = "https://media.example.test/stream.mp3?token=synthetic"
        with patch("custom_components.cisco_phone_control.media_player.async_process_play_media_url", side_effect=lambda hass, value: value):
            await self.player.async_play_media("music", url, enqueue="add", extra={
                "metadata": {"title": "Track", "artist": "Artist", "album": "Album", "duration": 45,
                             "images": [{"url": "https://media.example.test/image.png"}]}})
        self.client.action.assert_awaited_once_with("201", "player_play", {
            "source": url, "line": "201", "session_id": None, "enqueue": "add",
            "metadata": {"title": "Track", "artist": "Artist", "album": "Album", "duration": 45}}, None)
        self.assertEqual(self.player.state, "idle")

    async def test_home_assistant_media_source_is_resolved_before_gateway(self):
        url = "http://192.0.2.10:8123/media/file.mp3?authSig=synthetic"
        with patch("custom_components.cisco_phone_control.media_player.media_source.async_resolve_media",
                   new=AsyncMock(return_value=SimpleNamespace(url=url, mime_type="audio/mpeg"))) as resolve, \
             patch("custom_components.cisco_phone_control.media_player.async_process_play_media_url", side_effect=lambda hass, value: value):
            await self.player.async_play_media("music", "media-source://media_source/local/file.mp3")
        resolve.assert_awaited_once_with(self.hass, "media-source://media_source/local/file.mp3", self.player.entity_id)
        self.assertEqual(self.client.action.await_args.args[2]["source"], url)

    async def test_metadata_position_and_url_tokens_do_not_leak_into_state(self):
        self.activate()
        observed = self.data["phones"][0]["player"]
        observed.update(source_id="https://media.example.test/stream?token=private#secret", artist="Artist", album="Album")
        self.publish()
        self.assertEqual(self.player.media_content_id, "https://media.example.test/stream")
        self.assertEqual(self.player.media_title, "A song")
        self.assertEqual(self.player.media_artist, "Artist")
        self.assertEqual(self.player.media_album_name, "Album")
        self.assertEqual(self.player.media_duration, 120)
        self.assertEqual(self.player.media_position, 12.5)
        self.assertIsNotNone(self.player.media_position_updated_at.tzinfo)
        self.assertEqual(self.player.extra_state_attributes["queue_length"], 1)
        self.assertNotIn("session_id", self.player.extra_state_attributes)

    async def test_all_transport_methods_keep_observed_session_line(self):
        self.activate()
        session = self.data["phones"][0]["player"]["session_id"]
        operations = (
            (self.player.async_media_pause, (), "pause", {}),
            (self.player.async_media_play, (), "resume", {}),
            (self.player.async_media_stop, (), "stop", {}),
            (self.player.async_media_seek, (30,), "seek", {"position": 30}),
            (self.player.async_set_volume_level, (.7,), "volume", {"volume_level": .7}),
            (self.player.async_mute_volume, (True,), "mute", {"is_volume_muted": True}),
            (self.player.async_media_next_track, (), "next", {}),
            (self.player.async_media_previous_track, (), "previous", {}),
            (self.player.async_clear_playlist, (), "clear_queue", {}),
        )
        self.c.settings["201"]["line"] = "201"
        for method, args, operation, values in operations:
            with self.subTest(operation=operation):
                self.client.action.reset_mock()
                await method(*args)
                self.client.action.assert_awaited_once_with("201", "player_" + operation,
                    {"session_id": session, "line": "202", **values}, None)

    async def test_replacement_session_refuses_mutation_without_retry(self):
        from homeassistant.exceptions import HomeAssistantError
        self.activate()
        self.data["phones"][0]["player"]["session_id"] = "87654321-1234-4234-8234-123456789abc"
        with self.assertRaisesRegex(HomeAssistantError, "session changed"):
            await self.player.async_media_stop()
        self.client.action.assert_not_awaited()

    async def test_manual_radio_stays_visible_and_uses_legacy_exact_stop(self):
        identity = playing(self.data)
        self.publish()
        self.assertFalse(self.player.managed)
        self.assertEqual(self.player.media_title, "Toddler music")
        await self.player.async_media_stop()
        self.client.action.assert_awaited_once_with("201", "media_stop", {
            key: identity[key] for key in ("source", "line", "channel", "uniqueid")}, None)

    async def test_idle_volume_and_mute_do_not_start_audio(self):
        await self.player.async_set_volume_level(.3)
        self.client.action.assert_awaited_with("201", "player_volume", {
            "session_id": None, "line": "201", "volume_level": .3}, None)
        await self.player.async_mute_volume(True)
        self.client.action.assert_awaited_with("201", "player_mute", {
            "session_id": None, "line": "201", "is_volume_muted": True}, None)
        self.assertEqual(self.player.state, "idle")

    async def test_invalid_audio_and_unsupported_features_never_dispatch(self):
        from homeassistant.exceptions import HomeAssistantError
        with patch("custom_components.cisco_phone_control.media_player.async_process_play_media_url", side_effect=lambda hass, value: value):
            for media_type, url, options in (("video/mp4", "https://example.test/a", {}),
                                            ("music", "file:///etc/passwd", {}),
                                            ("music", "708", {"announce": True})):
                with self.subTest(media_type=media_type, url=url), self.assertRaises(HomeAssistantError):
                    await self.player.async_play_media(media_type, url, **options)
        self.client.action.assert_not_awaited()

    async def test_browse_combines_radio_and_home_assistant_media(self):
        from homeassistant.components.media_player import BrowseMedia, MediaClass, MediaType
        root = BrowseMedia(media_class=MediaClass.DIRECTORY, media_content_type=MediaType.CHANNELS,
                           media_content_id="", title="Media", can_play=False, can_expand=True, children=[])
        with patch("custom_components.cisco_phone_control.media_player.media_source.async_browse_media", new=AsyncMock(return_value=root)):
            result = await self.player.async_browse_media()
        self.assertEqual(result.children[0].media_content_id, "cisco-radio")
        self.assertEqual(result.children[0].children[0].media_content_id, "708")

    async def test_real_ha_registry_and_standard_service_dispatch(self):
        from homeassistant.setup import async_setup_component
        from homeassistant.helpers import entity_registry, device_registry, area_registry, floor_registry, label_registry, issue_registry
        from homeassistant.auth import auth_manager_from_config
        target = Path(self.tmp.name) / "custom_components/cisco_phone_control"
        target.parent.mkdir()
        shutil.copytree(legacy_runtime.ROOT / "custom_components/cisco_phone_control", target,
                        ignore=shutil.ignore_patterns("__pycache__"))
        device_registry.async_setup(self.hass)
        for registry in (floor_registry, label_registry, area_registry, device_registry, entity_registry, issue_registry):
            await registry.async_load(self.hass, load_empty=True)
        self.hass.auth = await auth_manager_from_config(self.hass, [], [])
        self.hass.config_entries._entries[self.entry.entry_id] = self.entry
        with patch("homeassistant.components.http.HomeAssistantHTTP.async_bind", new_callable=AsyncMock), \
             patch("custom_components.cisco_phone_control.GatewayClient", return_value=self.client), \
             patch("custom_components.cisco_phone_control.async_get_clientsession", return_value=None):
            self.assertTrue(await async_setup_component(self.hass, "cisco_phone_control", {}))
        await self.hass.async_block_till_done()
        registry = entity_registry.async_get(self.hass)
        entity_id = registry.async_get_entity_id("media_player", "cisco_phone_control", f"{self.entry.entry_id}:201:media_player")
        button_id = registry.async_get_entity_id("button", "cisco_phone_control", f"{self.entry.entry_id}:201:dial")
        self.assertEqual(registry.async_get(entity_id).device_id, registry.async_get(button_id).device_id)
        await self.hass.services.async_call("media_player", "volume_set", {
            "entity_id": entity_id, "volume_level": .25}, blocking=True)
        self.client.action.assert_awaited_with("201", "player_volume", {
            "session_id": None, "line": "201", "volume_level": .25}, None)
        await self.hass.services.async_call("media_player", "play_media", {
            "entity_id": entity_id, "media_content_type": "music", "media_content_id": "708",
            "enqueue": "replace", "extra": {"metadata": {"title": "A station"}}}, blocking=True)
        self.client.action.assert_awaited_with("201", "player_play", {
            "session_id": None, "line": "201", "source": "708", "enqueue": "replace",
            "metadata": {"title": "A station"}}, None)
        self.assertEqual(self.hass.states.get(entity_id).state, "idle")
        await self.hass.config_entries.async_unload(self.entry.entry_id)


if __name__ == "__main__":
    unittest.main()
