"""Catalog radio players sharing the existing physical handset devices."""
from homeassistant.components.media_player import (
    BrowseError,
    BrowseMedia,
    MediaClass,
    MediaPlayerDeviceClass,
    MediaPlayerEntity,
    MediaPlayerEntityFeature,
    MediaPlayerState,
    MediaType,
)
from homeassistant.exceptions import HomeAssistantError

from .entity import PhoneEntity, setup_entities
from .model import MEDIA_IDENTITY, radio_sources, supports_media

CATALOG_ID = "cisco-radio"


async def async_setup_entry(hass, entry, async_add_entities):
    await setup_entities(entry, async_add_entities, lambda c, p: [PhoneMediaPlayer(c, p)])


class PhoneMediaPlayer(PhoneEntity, MediaPlayerEntity):
    """A radio path, not a generic call or an optimistic playback request."""

    _attr_device_class = MediaPlayerDeviceClass.SPEAKER
    _attr_supported_features = (
        MediaPlayerEntityFeature.PLAY_MEDIA
        | MediaPlayerEntityFeature.SELECT_SOURCE
        | MediaPlayerEntityFeature.BROWSE_MEDIA
        | MediaPlayerEntityFeature.STOP
    )

    def __init__(self, coordinator, primary):
        super().__init__(coordinator, primary, "media_player", "Radio")

    @property
    def available(self):
        return (super().available and self.phone["online"]
                and supports_media(self.phone)
                and isinstance(self.phone.get("media"), dict))

    @property
    def observation(self):
        return (self.phone or {}).get("media", {})

    @property
    def state(self):
        if not self.available:
            return None
        return {
            "idle": MediaPlayerState.IDLE,
            "blocked": MediaPlayerState.IDLE,
            "buffering": MediaPlayerState.BUFFERING,
            "playing": MediaPlayerState.PLAYING,
        }.get(self.observation.get("state"))

    @property
    def sources(self):
        return radio_sources(self.coordinator.data or {})

    @property
    def source_list(self):
        return [f"{number} — {label}" for number, label in self.sources.items()]

    @property
    def source(self):
        number = self.observation.get("source")
        label = self.sources.get(number)
        return f"{number} — {label}" if label is not None else None

    @property
    def media_title(self):
        return self.sources.get(self.observation.get("source"))

    @property
    def media_content_id(self):
        return self.observation.get("source")

    @property
    def media_content_type(self):
        return MediaType.CHANNEL if self.media_content_id is not None else None

    @property
    def extra_state_attributes(self):
        return {"active_line": self.observation.get("line"),
                "media_status": self.observation.get("state"),
                "blocked_reason": self.observation.get("reason")}

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
        if (media_type not in (MediaType.CHANNEL, MediaType.MUSIC)
                or not isinstance(media_id, str) or media_id not in self.sources):
            raise HomeAssistantError("Only advertised radio station numbers are supported")
        if kwargs.get("announce") or kwargs.get("enqueue") not in (None, "replace"):
            raise HomeAssistantError("Phone radio does not support announcements or queues")
        await self.coordinator.perform(self.primary, "media_play", {"source": media_id})

    async def async_media_stop(self):
        self._require_available()
        media = self.observation
        if media.get("state") == "idle":
            return
        if media.get("state") not in ("playing", "buffering"):
            raise HomeAssistantError("No confirmed radio session is available to stop")
        identity = {key: media[key] for key in MEDIA_IDENTITY}
        await self.coordinator.perform(self.primary, "media_stop", identity)

    async def async_browse_media(self, media_content_type=None, media_content_id=None):
        self._require_available()
        if media_content_id not in (None, "", CATALOG_ID):
            raise BrowseError("Only the phone radio catalog can be browsed")
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
