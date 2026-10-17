"""Pure validation helpers. No credentials, channels, or message bodies are logged."""
import os
from pathlib import Path
import re
import stat
from urllib.parse import urlsplit

MAX_IMAGE = 2 * 1024 * 1024
NUMBER = re.compile(r"[0-9]{1,12}\Z")


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
    return value


def owned_channel(phone, line, answer=False):
    """A call must be isolated to this handset and the selected line."""
    channels = phone["channels"]
    if len(channels) != 1 or channels[0]["line"] != line:
        raise ValueError("Select the line with exactly one active handset call")
    channel = channels[0]
    if answer and channel["state"] != "Ringing":
        raise ValueError("The selected line is not ringing")
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
