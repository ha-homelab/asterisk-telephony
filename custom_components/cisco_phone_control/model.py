"""Pure validation helpers. No credentials, channels, or message bodies are logged."""
import math
import os
from pathlib import Path
import re
import stat
from urllib.parse import urlsplit
from uuid import UUID

MAX_IMAGE = 2 * 1024 * 1024
NUMBER = re.compile(r"[0-9]{1,12}\Z")
MEDIA_REASONS = frozenset({"offline", "dnd", "alert", "call", "voice", "unavailable", "ambiguous", "unmarked"})
MEDIA_IDENTITY = ("source", "line", "channel", "uniqueid")
PLAYER_CAPABILITIES = frozenset({"play", "url", "pause", "resume", "seek", "volume", "mute", "queue", "next", "previous"})


def finite_number(value, low=0, high=None):
    return (type(value) in (int, float) and math.isfinite(value)
            and value >= low and (high is None or value <= high))


def player_source(value, sources):
    """Accept advertised stations or HTTP audio without logging private URLs."""
    if not isinstance(value, str) or not value or len(value) > 8192:
        raise ValueError("Choose an advertised station or an HTTP(S) audio URL")
    if value in sources:
        return value
    try:
        parts = urlsplit(value)
        valid = (parts.scheme in ("http", "https") and parts.hostname
                 and not parts.username and not parts.password and not parts.fragment
                 and not any(ord(char) < 33 for char in value))
        parts.port
    except ValueError:
        valid = False
    if not valid:
        raise ValueError("Choose an advertised station or an HTTP(S) audio URL without embedded credentials")
    return value


def player_metadata(value):
    """Bound only supported metadata; artwork URLs are not sent to the phone."""
    if not isinstance(value, dict):
        raise ValueError("Invalid media metadata")
    result = {}
    for key in ("title", "artist", "album"):
        item = value.get(key)
        if item is None:
            continue
        if not isinstance(item, str) or len(item) > 512 or any(ord(char) < 32 for char in item):
            raise ValueError("Media metadata must be short text without control characters")
        result[key] = item
    if value.get("duration") is not None:
        if not finite_number(value["duration"]):
            raise ValueError("Media duration must be a finite nonnegative number")
        result["duration"] = value["duration"]
    return result


def player_observation(phone):
    """Validate the versioned managed player independently of legacy radio."""
    if "player" not in phone:
        return None
    player = phone["player"]
    if (not isinstance(player, dict) or type(player.get("v")) is not int or player["v"] != 1
            or player.get("state") not in ("idle", "buffering", "playing", "paused", "error", "blocked")
            or not isinstance(player.get("capabilities"), list)
            or any(not isinstance(cap, str) for cap in player["capabilities"])):
        raise ValueError("Invalid managed player observation")
    session = player.get("session_id")
    if session is not None:
        try:
            UUID(session)
        except (ValueError, TypeError, AttributeError):
            raise ValueError("Invalid managed player session") from None
        if player.get("line") not in phone["lines"]:
            raise ValueError("Managed player line does not belong to this handset")
    elif player.get("line") is not None or player["state"] in ("buffering", "playing", "paused"):
        raise ValueError("Active managed player requires a session and line")
    if player.get("source_id") is not None and not isinstance(player["source_id"], str):
        raise ValueError("Invalid managed player source")
    player_metadata(player)
    if (not finite_number(player.get("position"))
            or not finite_number(player.get("volume_level"), high=1)
            or type(player.get("is_volume_muted")) is not bool
            or type(player.get("seekable")) is not bool
            or not finite_number(player.get("position_updated_at"))):
        raise ValueError("Invalid managed player transport values")
    length, index = player.get("queue_length"), player.get("queue_index")
    if (type(length) is not int or not 0 <= length <= 1000
            or (length == 0 and index not in (None, -1, 0))
            or (length > 0 and (type(index) is not int or not 0 <= index < length))):
        raise ValueError("Invalid managed player queue")
    reason = player.get("reason")
    if reason is not None and (not isinstance(reason, str) or len(reason) > 128):
        raise ValueError("Invalid managed player reason")
    return player


def player_action(phone, action, values, sources, selected_line):
    """Fence transport commands to the observed session, including sibling lines."""
    player = player_observation(phone)
    if player is None or not phone["online"]:
        raise ValueError("The managed phone media player is unavailable")
    operation = action.removeprefix("player_")
    capability = "queue" if operation == "clear_queue" else operation
    if operation != "stop" and capability not in player["capabilities"]:
        raise ValueError("The gateway does not support this media operation")
    if values.get("session_id") != player.get("session_id"):
        raise ValueError("The media session changed; refresh the player before controlling it")
    session = player.get("session_id")
    line = player.get("line") if session else selected_line
    if line not in phone["lines"] or values.get("line", line) != line:
        raise ValueError("The media session line changed; no other call was controlled")
    result = {"session_id": session, "line": line}
    if operation == "play":
        if player["state"] == "blocked" and player.get("reason") != "voice":
            raise ValueError("The phone is busy with a call, alert or voice session")
        source = player_source(values.get("source"), sources)
        if source not in sources and "url" not in player["capabilities"]:
            raise ValueError("The gateway does not support audio URLs")
        enqueue = values.get("enqueue", "replace")
        if enqueue not in ("replace", "add", "next", "play"):
            raise ValueError("Unsupported media queue operation")
        if enqueue != "replace" and "queue" not in player["capabilities"]:
            raise ValueError("The gateway does not support a media queue")
        result.update(source=source, metadata=player_metadata(values.get("metadata", {})), enqueue=enqueue)
    elif operation == "volume":
        if not finite_number(values.get("volume_level"), high=1):
            raise ValueError("Media volume must be between zero and one")
        result["volume_level"] = values["volume_level"]
    elif operation == "mute":
        if type(values.get("is_volume_muted")) is not bool:
            raise ValueError("Media mute must be a boolean")
        result["is_volume_muted"] = values["is_volume_muted"]
    else:
        if session is None:
            raise ValueError("No managed media session is active")
        if operation == "seek":
            position = values.get("position")
            if not player["seekable"] or not finite_number(position, high=player.get("duration")):
                raise ValueError("This stream cannot seek to that position")
            result["position"] = position
    return result


def notification_text(value, *, title=False):
    """Match the gateway's text contract without changing private content."""
    field, limit = ("Message title", 48) if title else ("Message text", 2000)
    if not isinstance(value, str) or not value:
        detail = (" Message fields are cleared when Home Assistant restarts or "
                  "the integration reloads." if not title else "")
        raise ValueError(f"Enter {field} and apply the edit before sending.{detail}")
    if len(value) > limit:
        raise ValueError(f"{field} must contain at most {limit} characters")
    if any(ord(char) < 32 and char not in "\n\t" for char in value):
        raise ValueError(f"{field} contains unsupported control characters; only newline and tab are allowed")
    return value


def supports_media(phone):
    capabilities = phone.get("capabilities")
    return isinstance(capabilities, list) and "media" in capabilities


def radio_sources(value):
    """Return only unambiguous, advertised radio destinations with safe labels."""
    services = value.get("services", [])
    allowed = value.get("dial_allowlist", [])
    if not isinstance(services, list) or not isinstance(allowed, list):
        return {}
    local = {line for phone in value.get("phones", []) if isinstance(phone, dict)
             for line in phone.get("lines", []) if isinstance(line, str)}
    clients = value.get("softphones", [])
    clients = clients if isinstance(clients, list) else []
    local.update(client.get("extension") for client in clients
                 if isinstance(client, dict) and isinstance(client.get("extension"), str))
    sources, duplicates = {}, set()
    for item in services:
        if not isinstance(item, dict) or item.get("kind") != "radio":
            continue
        number, label = item.get("number"), item.get("label")
        if (not isinstance(number, str) or not NUMBER.fullmatch(number)
                or number not in allowed or number in local
                or not isinstance(label, str) or label != label.strip()
                or not re.fullmatch(r"[A-Za-z0-9][ -~]{0,79}", label)):
            continue
        if number in sources:
            duplicates.add(number)
        sources[number] = label
    return {number: label for number, label in sources.items() if number not in duplicates}


def media_observation(phone, sources):
    """Validate optional media evidence without upgrading a legacy gateway."""
    if "media" not in phone:
        return None
    media = phone["media"]
    if (not isinstance(media, dict)
            or set(media) != {"state", "reason", *MEDIA_IDENTITY}
            or media.get("state") not in ("idle", "buffering", "playing", "blocked", "unknown")):
        raise ValueError("Invalid media observation")
    state = media["state"]
    if state in ("idle", "blocked", "unknown"):
        if any(media[key] is not None for key in MEDIA_IDENTITY):
            raise ValueError("Inactive media cannot claim a call identity")
        if ((state == "idle" and media["reason"] is not None)
                or (state != "idle" and (not isinstance(media["reason"], str)
                                        or media["reason"] not in MEDIA_REASONS))):
            raise ValueError("Invalid media reason")
    else:
        if (media["reason"] is not None or not isinstance(media["source"], str)
                or media["source"] not in sources or media["line"] not in phone["lines"]
                or not isinstance(media["channel"], str)
                or not isinstance(media["uniqueid"], str)):
            raise ValueError("Invalid active radio identity")
        matches = [channel for channel in phone["channels"]
                   if (channel["line"], channel["name"], channel["uniqueid"])
                   == (media["line"], media["channel"], media["uniqueid"])]
        if len(matches) != 1 or len(phone["channels"]) != 1:
            raise ValueError("Radio identity does not match one isolated handset call")
        if state == "playing" and matches[0]["state"] != "Up":
            raise ValueError("Playing radio call is not connected")
    return media


def gateway_url(value):
    """Only an explicit HTTP(S) origin; never credentials, queries or redirects."""
    parts = urlsplit(value.strip())
    if (parts.scheme not in ("http", "https") or not parts.hostname or parts.username
            or parts.password or parts.query or parts.fragment or parts.path not in ("", "/")):
        raise ValueError("Use an HTTP(S) gateway origin without credentials or a path")
    try:
        parts.port
    except ValueError:
        raise ValueError("Invalid gateway port") from None
    return value.strip().rstrip("/")


def inventory(value):
    """Reject ambiguous identities, foreign channels and incomplete API responses."""
    if not isinstance(value, dict) or value.get("ami_fresh") is not True:
        raise ValueError("Gateway has no fresh AMI snapshot")
    phones = value.get("phones")
    if not isinstance(phones, list) or not 1 <= len(phones) <= 64:
        raise ValueError("Invalid handset inventory")
    seen, lines = set(), set()
    for phone in phones:
        if not isinstance(phone, dict):
            raise ValueError("Invalid handset")
        primary, assigned = phone.get("id"), phone.get("lines")
        if (not isinstance(primary, str) or not NUMBER.fullmatch(primary) or primary in seen
                or not isinstance(assigned, list) or not 1 <= len(assigned) <= 8
                or primary not in assigned
                or any(not isinstance(n, str) for n in assigned)
                or len(set(assigned)) != len(assigned)):
            raise ValueError("Invalid handset identity")
        if any(not isinstance(n, str) or not NUMBER.fullmatch(n) or n in lines for n in assigned):
            raise ValueError("Overlapping or invalid handset lines")
        seen.add(primary)
        lines.update(assigned)
        if any(type(phone.get(key)) is not bool for key in ("online", "dnd", "autoanswer", "dnd_pending")):
            raise ValueError("Invalid handset state")
        alert = phone.get("alert")
        if alert is not None:
            if (not isinstance(alert, dict) or not isinstance(alert.get("id"), str)
                    or not re.fullmatch(r"[0-9a-f]{32}", alert["id"])
                    or alert.get("kind") not in ("timer", "alarm") or alert.get("line") not in assigned
                    or alert.get("state") not in ("starting", "ringing", "stopping", "ended", "unknown")
                    or alert.get("reason") not in (None, "call_ended", "stopped", "not_observed")):
                raise ValueError("Invalid or foreign alert identity")
        channels = phone.get("channels")
        if not isinstance(channels, list) or len(channels) > 32:
            raise ValueError("Invalid channel list")
        channel_ids = set()
        for channel in channels:
            if not isinstance(channel, dict):
                raise ValueError("Invalid channel")
            line, name, uid = channel.get("line"), channel.get("name"), channel.get("uniqueid")
            if (line not in assigned or not isinstance(name, str)
                    or not re.fullmatch(r"PJSIP/" + re.escape(line) + r"-[0-9a-f]+", name)
                    or not isinstance(uid, str) or not re.fullmatch(r"[0-9]+\.[0-9]+", uid)
                    or not isinstance(channel.get("state"), str) or (name, uid) in channel_ids):
                raise ValueError("Invalid or foreign channel identity")
            channel_ids.add((name, uid))
    for key in ("dial_allowlist", "ring_allowlist"):
        allowed = value.get(key)
        if (not isinstance(allowed, list) or len(allowed) > 256
                or any(not isinstance(n, str) or not NUMBER.fullmatch(n) for n in allowed)
                or len(set(allowed)) != len(allowed)):
            raise ValueError("Invalid destination list")
    sources = radio_sources(value)
    for phone in phones:
        media_observation(phone, sources)
        player_observation(phone)
    return value


def owned_channel(phone, line, answer=False):
    """A call must be isolated to this handset and the selected line."""
    channels = phone["channels"]
    if len(channels) != 1 or channels[0]["line"] != line:
        raise ValueError("Select the line with exactly one active handset call")
    channel = channels[0]
    if answer and channel["state"] != "Ringing":
        raise ValueError("The selected line is not ringing")
    alert = phone.get("alert")
    if answer and alert and alert["state"] != "ended" and alert["line"] == line:
        raise ValueError("Answer an alert physically, or use Stop alert")
    return {"line": line, "channel": channel["name"], "uniqueid": channel["uniqueid"]}


def read_image(value, roots):
    """Read a bounded regular file through no-follow directory descriptors.

    O_NOFOLLOW on every path component also prevents a symlink swap during reads.
    Approved roots are configured by the integration, never supplied by a service.
    """
    if not value or "\x00" in value:
        raise ValueError("Choose an approved image file")
    roots = [Path(os.path.abspath(root)) for root in roots]
    requested = Path(value)
    if not requested.is_absolute():
        requested = roots[0] / requested
    if ".." in requested.parts:
        raise ValueError("Parent traversal is not allowed")
    path = Path(os.path.abspath(requested))
    if not any(path.is_relative_to(root) and path != root for root in roots):
        raise ValueError("Image must be inside an approved phone-images folder")
    if path.suffix.lower() not in (".png", ".jpg", ".jpeg"):
        raise ValueError("Choose a PNG or JPEG file")
    descriptor = os.open(path.anchor, os.O_RDONLY | os.O_DIRECTORY)
    try:
        for part in path.parts[1:-1]:
            child = os.open(part, os.O_RDONLY | os.O_DIRECTORY | os.O_NOFOLLOW, dir_fd=descriptor)
            os.close(descriptor)
            descriptor = child
        image_fd = os.open(path.name, os.O_RDONLY | os.O_NOFOLLOW | os.O_NONBLOCK, dir_fd=descriptor)
        with os.fdopen(image_fd, "rb") as source:
            info = os.fstat(source.fileno())
            if not stat.S_ISREG(info.st_mode) or info.st_size > MAX_IMAGE:
                raise ValueError("Image must be a regular file no larger than 2 MiB")
            data = source.read(MAX_IMAGE + 1)
        if len(data) > MAX_IMAGE or not (data.startswith(b"\x89PNG\r\n\x1a\n") or data.startswith(b"\xff\xd8\xff")):
            raise ValueError("Invalid or oversized PNG/JPEG")
        return data
    finally:
        os.close(descriptor)
