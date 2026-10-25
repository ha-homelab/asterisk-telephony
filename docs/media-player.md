# Music and radio on Cisco phones

The existing **Cisco phone controls** integration provides one media player per
physical handset, including offline handsets. Both SIP lines share the same
speaker and media player. Upgrading preserves the device and entity IDs; an
existing entity named **Radio** keeps that name and gains the supported controls.

![Home Assistant audio sources, managed playback and observed handset state](diagrams/phone-media-player.svg)

## Install both sides

Update `custom_components/cisco_phone_control` using the
[installation instructions](phone-controls.md#install-and-connect), then restart
Home Assistant. Install the matching managed-media gateway and its generated
Asterisk configuration from
[cisco-home-telephony](https://github.com/4alvit/cisco-home-telephony).
The gateway must advertise the versioned `phone.player` contract described below.
The HA component alone cannot add an audio transport to an older gateway.

Keep the existing integration entry, private gateway credentials and entity
registry. Backups belong outside `custom_components`. Verify the gateway's
inventory and loaded PBX route before testing playback. If Asterisk configuration
is mounted from an immutable Kubernetes Secret, replace that Secret and update
the workload reference; a dialplan reload cannot mount new files.

An older radio-capable gateway remains supported: station selection, browsing,
observed playback and exact-call Stop continue to work. Additional controls only
appear when the gateway advertises them. A gateway without either media contract
leaves the player unavailable.

## Choose audio

In **Settings → Devices & services → Entities**, find the phone's existing
`media_player` entity. All entity IDs and hostnames below are synthetic examples.
Add that entity to a dashboard media-control card to browse media, choose a radio
source, play, pause, resume and stop.

The media browser combines the gateway's configured **Radio stations** catalog
with Home Assistant's audio media sources. The station picker uses the exact
`number — label` values in `source_list`. An advertised station can also be played
directly:

```yaml
action: media_player.play_media
target:
  entity_id: media_player.example_phone_radio
data:
  media_content_id: "708"
  media_content_type: music
```

For an MP3, internet radio stream or another FFmpeg-decodable HTTP(S) audio source:

```yaml
action: media_player.play_media
target:
  entity_id: media_player.example_phone_radio
data:
  media_content_id: https://media.example.org/music.mp3
  media_content_type: audio/mpeg
  enqueue: replace
  extra:
    metadata:
      title: Example track
      artist: Example artist
      album: Example album
```

For an audio file in Home Assistant's local media library:

```yaml
action: media_player.play_media
target:
  entity_id: media_player.example_phone_radio
data:
  media_content_id: media-source://media_source/local/music.mp3
  media_content_type: music
```

HA resolves `media-source://` references and local HA media URLs before sending
the playback URL privately to the gateway. The **gateway**, not the handset,
must be able to reach the resolved HTTP(S) server. Use HA's reachable internal
URL when HA and the gateway run on the same network. A browser-accessible URL
may still be unreachable from a Kubernetes pod. Expired signed links need to be
resolved again; a queued signed URL must remain valid until the item starts.
Embedded `user:password@host` URLs, non-HTTP protocols and video requests are
rejected. This is audio playback, not video rendering.

Before resolving a media-source reference, HA captures the current session and
line. Changing the **Line** selector while resolution is pending does not retarget
that request. If another session starts before dispatch, the pending request is
refused without controlling the new session. New idle volume and mute commands
continue to use the currently selected line.

## Music Assistant

Use Music Assistant's **Home Assistant player provider** and enable the existing
Cisco media-player entity. Its HTTP audio stream reaches the same managed audio
path as `media_player.play_media`; no additional phone registration is needed.
Allow network access from the gateway to Music Assistant's stream address and
port. Keep Music Assistant's advertised stream address reachable from the PBX
environment, rather than using a loopback address.

Music Assistant owns its queue when streaming through its player provider. Its
`extra.metadata` title, artist, album and duration are accepted by the HA bridge.
The phone's HA queue controls operate the gateway queue, not Music Assistant's
separate queue. Prefer one queue owner for a listening session. This integration
does not advertise synchronized groups, crossfade, announcement interruption or
native Music Assistant queue browsing.

## Queue and transport controls

`play_media` accepts HA's `enqueue` values: `replace`, `add`, `next` and `play`.
Use `add` to append a track, or `next` to insert it after the current item.
`replace` starts a new queue with the requested item. `play` adds the item and
selects it immediately. Queue operations remain subject to the gateway's bounds.

The standard HA actions target the same entity:

- `media_player.media_pause` pauses managed playback.
- `media_player.media_play` resumes the current managed session.
- `media_player.media_stop` stops only the identified media session.
- `media_player.media_next_track` and `media_player.media_previous_track` move
  within the gateway queue.
- `media_player.clear_playlist` clears queued items according to the gateway's
  current-session policy.
- `media_player.media_seek` accepts `seek_position` in seconds. HA only shows
  seeking when the current source is reported as seekable; live radio streams
  usually cannot seek.
- `media_player.volume_set` accepts `volume_level` from `0.0` to `1.0`.
- `media_player.volume_mute` accepts the boolean `is_volume_muted`.

Pause keeps the decoder and HTTP stream attached, stops consuming decoded audio,
and sends silence on the SIP media leg. Bounded backpressure preserves playback
position rather than deliberately jumping to the live edge. A remote streaming
server may time out a long pause; that produces an explicit playback error and
requires restarting the source. The gateway bounds a session to two hours and
finite downloaded media to 64 MiB.

For example:

```yaml
action: media_player.volume_set
target:
  entity_id: media_player.example_phone_radio
data:
  volume_level: 0.35
```

Volume and mute adjust the managed audio stream in the gateway. They do not
change the handset's physical volume setting or an unrelated phone call.
Setting them while idle stores a preference without starting a call. A handset
with a very low physical speaker volume remains quiet even with high stream gain.

The player reports title, artist, album, duration, position, queue length and
queue index from the gateway. Metadata can come from the request or stream; an
unlabeled broadcast cannot reliably identify a song by itself. Query strings,
URL credentials and fragments are removed from the `media_content_id` exposed
to the HA frontend and recorder. The original playback URL is passed privately
to the gateway and must not be copied into public logs or documentation.

## Understand state and call ownership

The player changes state only after a fresh gateway observation. A successful
Play request means accepted, not that sound was heard. **Buffering**, **Playing**
and **Paused** describe the managed transport; verify audible sound separately
when commissioning a handset. `media_status` and `blocked_reason` explain
blocked or failed playback. `active_line` identifies the media session's SIP
line, independent of later changes to the handset's **Line** selector.

An ordinary call, an alert or another owner can block playback. Media controls
cannot hang up an unrelated conversation. Each command carries the observed
session identity; if that identity changes before dispatch, HA refuses the
command instead of controlling a replacement call. Refresh after an unknown
outcome rather than repeatedly clicking a transport button.

Manual calls to the numeric radio extensions keep their existing Asterisk
radio path. Their station title and exact-call Stop remain available in HA.
Until that legacy radio call ends, it does not gain the managed queue, pause,
seek or software-volume controls. Stop the old radio session and start a station
from the HA media player to use the managed transport.

Offline phones stay registered as entities and show **Unavailable**. Reconnection
restores availability through inventory polling, without automatically replaying
audio. Separate phones can play separately; this does not imply synchronized
multiroom audio.

## Troubleshooting and acceptance

1. **Only station selection and Stop appear:** inspect the gateway inventory for
   `phone.player.v: 1`. Upgrade the gateway/PBX as well as the HA component. A
   manually dialed legacy radio call intentionally exposes only legacy controls.
2. **Player unavailable:** check the phone registration and gateway availability.
   A stale or invalid inventory does not establish call ownership.
3. **URL accepted but no sound:** check gateway reachability, TLS trust, source
   authorization/expiry and decoder status. Do not infer playback from acceptance.
4. **Cannot seek:** inspect `seekable`; many live streams have no fixed position.
5. **Wrong or missing title:** supply `extra.metadata`, or verify that the stream
   actually supplies track metadata. Duration can be unknown for live audio.
6. **Session changed error:** refresh before deciding which current session to
   control. The guard prevents a stale click from stopping another call.
7. **Music Assistant buffering:** check its advertised stream URL from the gateway
   environment and confirm that the Home Assistant player provider selected the
   existing Cisco entity.

Commission with one deliberate audio request, observed Playing, audible output,
pause/resume, supported seek, volume/mute, next/previous and exact Stop. Check a
media-source file and a Music Assistant stream separately, then verify an unrelated
call survives refused media controls. Record software observations separately
from human listening confirmation; one handset test does not prove every phone.

## Versioned gateway contract

The optional `phone.player` object sits beside the unchanged legacy `phone.media`.
Version `v: 1` includes `capabilities`, `state`, `reason`, `session_id`, `line`,
`source_id`, `title`, `artist`, `album`, `position`, `position_updated_at` (Unix
seconds), `duration`, `seekable`, `volume_level`, `is_volume_muted`, `queue_index`
and `queue_length`. Position and duration use seconds; unknown duration is null.

Advertised capabilities map dynamically to HA features: `play`, `url`, `pause`,
`resume`, `seek`, `volume`, `mute`, `queue`, `next` and `previous`. Seek additionally
requires `seekable: true`. All version-1 players support exact-session Stop.

The actions are `player_play`, `player_pause`, `player_resume`, `player_stop`,
`player_next`, `player_previous`, `player_clear_queue`, `player_seek`,
`player_volume` and `player_mute`. Each uses the existing authenticated,
idempotent gateway action client. Transport mutations retain the observed
session ID and its line; idle volume/mute use a null session. `player_play`
includes `source`, bounded `metadata` and `enqueue`. No action is blindly retried.
