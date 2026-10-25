"""Observed media sessions sharing the existing physical handset devices."""
from datetime import datetime, timezone
from urllib.parse import urlsplit, urlunsplit

from homeassistant.components import media_source
from homeassistant.components.media_player import (
    BrowseError,
    BrowseMedia,
    MediaClass,
    MediaPlayerDeviceClass,
    MediaPlayerEntity,
    MediaPlayerEntityFeature,
    MediaPlayerState,
    MediaType,
    async_process_play_media_url,
)
from homeassistant.exceptions import HomeAssistantError

from .entity import PhoneEntity, setup_entities
from .model import MEDIA_IDENTITY, player_metadata, player_observation, radio_sources, supports_media

CATALOG_ID = "cisco-radio"


async def async_setup_entry(hass, entry, async_add_entities):
    await setup_entities(entry, async_add_entities, lambda c, p: [PhoneMediaPlayer(c, p)])


class PhoneMediaPlayer(PhoneEntity, MediaPlayerEntity):
    """A managed audio player, with a compatible observed-radio fallback."""

    _attr_device_class = MediaPlayerDeviceClass.SPEAKER

    def __init__(self, coordinator, primary):
        super().__init__(coordinator, primary, "media_player", "Radio")

    @property
    def available(self):
        return (super().available and self.phone["online"] and (
            self.player is not None or (supports_media(self.phone)
                                      and isinstance(self.phone.get("media"), dict))))

    @property
    def player(self):
        return player_observation(self.phone or {})

    @property
    def legacy_active(self):
        return (not (self.player or {}).get("session_id")
                and (self.phone or {}).get("media", {}).get("state") in ("playing", "buffering"))

    @property
    def managed(self):
        return self.player is not None and not self.legacy_active

    @property
    def supported_features(self):
        feature = MediaPlayerEntityFeature
        result = feature.PLAY_MEDIA | feature.SELECT_SOURCE | feature.BROWSE_MEDIA | feature.STOP
        if not self.managed:
            return result
        capabilities = self.player["capabilities"]
        if "play" not in capabilities:
            result &= ~(feature.PLAY_MEDIA | feature.SELECT_SOURCE)
        for capability, bit in (("pause", feature.PAUSE), ("resume", feature.PLAY),
                                ("volume", feature.VOLUME_SET), ("mute", feature.VOLUME_MUTE),
                                ("queue", feature.MEDIA_ENQUEUE), ("queue", feature.CLEAR_PLAYLIST),
                                ("next", feature.NEXT_TRACK), ("previous", feature.PREVIOUS_TRACK)):
            if capability in capabilities:
                result |= bit
        if "seek" in capabilities and self.player["seekable"]:
            result |= feature.SEEK
        return result

    @property
    def observation(self):
        return self.player if self.managed else (self.phone or {}).get("media", {})

    @property
    def state(self):
        if not self.available:
            return None
        return {
            "idle": MediaPlayerState.IDLE,
            "blocked": MediaPlayerState.IDLE,
            "buffering": MediaPlayerState.BUFFERING,
            "playing": MediaPlayerState.PLAYING,
            "paused": MediaPlayerState.PAUSED,
        }.get(self.observation.get("state"))

    @property
    def sources(self):
        return radio_sources(self.coordinator.data or {})

    @property
    def source_list(self):
        return [f"{number} — {label}" for number, label in self.sources.items()]

    @property
    def source(self):
        number = self.observation.get("source_id" if self.managed else "source")
        label = self.sources.get(number)
        return f"{number} — {label}" if label is not None else None

    @property
    def media_title(self):
        if self.managed:
            return self.observation.get("title") or self.sources.get(self.observation.get("source_id"))
        return self.sources.get(self.observation.get("source"))

    @property
    def media_artist(self):
        return self.observation.get("artist") if self.managed else None

    @property
    def media_album_name(self):
        return self.observation.get("album") if self.managed else None

    @property
    def media_content_id(self):
        value = self.observation.get("source_id" if self.managed else "source")
        if value and value.startswith(("https://", "http://")):
            # Signed media URLs may carry private tokens. HA's recorder and
            # frontend need an identifier, never those playback credentials.
            parts = urlsplit(value)
            return urlunsplit((parts.scheme, parts.netloc.rsplit("@", 1)[-1], parts.path, "", ""))
        return value

    @property
    def media_content_type(self):
        if self.media_content_id is None:
            return None
        return MediaType.MUSIC if self.managed else MediaType.CHANNEL

    @property
    def media_position(self):
        return self.observation.get("position") if self.managed and self.observation.get("session_id") else None

    @property
    def media_position_updated_at(self):
        value = self.observation.get("position_updated_at") if self.managed else None
        return datetime.fromtimestamp(value, timezone.utc) if value is not None else None

    @property
    def media_duration(self):
        return self.observation.get("duration") if self.managed else None

    @property
    def volume_level(self):
        return self.observation.get("volume_level") if self.managed else None

    @property
    def is_volume_muted(self):
        return self.observation.get("is_volume_muted") if self.managed else None

    @property
    def extra_state_attributes(self):
        result = {"active_line": self.observation.get("line"),
                "media_status": self.observation.get("state"),
                "blocked_reason": self.observation.get("reason")}
        if self.managed:
            result.update(queue_index=self.observation.get("queue_index"),
                          queue_length=self.observation.get("queue_length"),
                          media_transport="managed", seekable=self.observation.get("seekable"))
        return result

    def _require_available(self):
        if not self.available:
            raise HomeAssistantError("The phone media player is unavailable")

    async def async_select_source(self, source):
        self._require_available()
        choices = {f"{number} — {label}": number for number, label in self.sources.items()}
        if source not in choices:
            raise HomeAssistantError("Choose an advertised radio station")
        await self.async_play_media(MediaType.CHANNEL, choices[source])

    async def async_play_media(self, media_type, media_id, **kwargs):
        self._require_available()
        if self.managed:
            # Resolving a media-source URL may yield while another caller starts
            # playback or changes the line selector. Keep this request's owner.
            player = self.player
            identity = {"session_id": player.get("session_id"),
                        "line": player.get("line") or self.settings["line"]}
            if not isinstance(media_id, str) or not media_id:
                raise HomeAssistantError("Choose an audio source")
            if kwargs.get("announce"):
                raise HomeAssistantError("Phone media does not support announcement interruption")
            if media_source.is_media_source_id(media_id):
                resolved = await media_source.async_resolve_media(self.hass, media_id, self.entity_id)
                media_id, media_type = resolved.url, resolved.mime_type
            if media_id not in self.sources:
                if media_type not in (MediaType.MUSIC, MediaType.URL, "audio", "channel") and not str(media_type).startswith("audio/"):
                    raise HomeAssistantError("Choose an audio file or stream")
                media_id = async_process_play_media_url(self.hass, media_id)
            extra = kwargs.get("extra") or {}
            try:
                metadata = player_metadata(extra.get("metadata", {}))
            except (ValueError, AttributeError) as error:
                raise HomeAssistantError("Invalid audio metadata") from error
            await self._player_action("play", identity=identity, source=media_id, metadata=metadata,
                                      enqueue=kwargs.get("enqueue") or "replace")
            return
        if (media_type not in (MediaType.CHANNEL, MediaType.MUSIC)
                or not isinstance(media_id, str) or media_id not in self.sources):
            raise HomeAssistantError("Only advertised radio station numbers are supported")
        if kwargs.get("announce") or kwargs.get("enqueue") not in (None, "replace"):
            raise HomeAssistantError("Phone radio does not support announcements or queues")
        await self.coordinator.perform(self.primary, "media_play", {"source": media_id})

    async def async_media_stop(self):
        self._require_available()
        if self.managed:
            if self.player.get("session_id") is not None:
                await self._player_action("stop")
            return
        media = self.observation
        if media.get("state") == "idle":
            return
        if media.get("state") not in ("playing", "buffering"):
            raise HomeAssistantError("No confirmed radio session is available to stop")
        identity = {key: media[key] for key in MEDIA_IDENTITY}
        await self.coordinator.perform(self.primary, "media_stop", identity)

    async def _player_action(self, operation, *, identity=None, **values):
        self._require_available()
        if not self.managed:
            raise HomeAssistantError("This operation requires a managed media session")
        if identity is None:
            player = self.player
            identity = {"session_id": player.get("session_id"),
                        "line": player.get("line") or self.settings["line"]}
        values.update(identity)
        await self.coordinator.perform(self.primary, "player_" + operation, values)

    async def async_media_pause(self):
        await self._player_action("pause")

    async def async_media_play(self):
        await self._player_action("resume")

    async def async_media_seek(self, position):
        await self._player_action("seek", position=position)

    async def async_set_volume_level(self, volume):
        await self._player_action("volume", volume_level=volume)

    async def async_mute_volume(self, mute):
        await self._player_action("mute", is_volume_muted=mute)

    async def async_media_next_track(self):
        await self._player_action("next")

    async def async_media_previous_track(self):
        await self._player_action("previous")

    async def async_clear_playlist(self):
        await self._player_action("clear_queue")

    async def async_browse_media(self, media_content_type=None, media_content_id=None):
        self._require_available()
        if self.managed and "url" in self.player["capabilities"] and media_content_id != CATALOG_ID:
            browser = await media_source.async_browse_media(
                self.hass, media_content_id,
                content_filter=lambda item: item.media_content_type.startswith("audio/"))
            if media_content_id in (None, ""):
                browser.children = [self._radio_catalog(), *(browser.children or [])]
            return browser
        if media_content_id not in (None, "", CATALOG_ID):
            raise BrowseError("Only the phone radio catalog can be browsed")
        return self._radio_catalog()

    def _radio_catalog(self):
        return BrowseMedia(
            media_class=MediaClass.DIRECTORY,
            media_content_type=MediaType.CHANNELS,
            media_content_id=CATALOG_ID,
            title="Radio stations",
            can_play=False,
            can_expand=True,
            children_media_class=MediaClass.CHANNEL,
            children=[BrowseMedia(
                media_class=MediaClass.CHANNEL,
                media_content_type=MediaType.CHANNEL,
                media_content_id=number,
                title=f"{number} — {label}",
                can_play=True,
                can_expand=False,
            ) for number, label in self.sources.items()],
        )
