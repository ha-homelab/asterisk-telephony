"""Bounded, non-retrying authenticated gateway client."""
import asyncio
import json
import uuid

import aiohttp

from .model import gateway_url, inventory

REFUSAL_DETAILS = {
    "ringer_volume_requires_idle_phone": "Ringer volume requires an idle phone; finish its call or alert first",
    "call_volume_context_changed": "Select the line with the single connected call, resume it if held, then refresh",
    "player_phone_busy": "The phone has another media owner or pending cleanup; physical volume was not sent",
    "bad_text": "Check Message title and Message text: both must be nonempty and contain no unsupported control characters",
    "phone_busy": "The phone has an active call on one of its lines; finish the call before sending",
    "voice_phone_busy": "The voice assistant owns the phone or requires recovery; check its status before sending",
    "hands_free_enabled": "Hands-free voice is enabled on this phone; this action cannot interrupt it. Check the voice controls before changing physical volume",
    "too_many_pending_messages": "The gateway has too many pending messages; wait for them to expire before sending another",
    "invalid_player_source": "Use an allowed HTTP(S) audio source or advertised station with bounded metadata",
    "player_session_changed": "The media session changed; refresh the player before controlling it",
    "player_line_changed": "The active media line changed; refresh before controlling the session",
    "player_call_not_observed": "The media call is still starting; wait for its observed state before controlling it",
    "player_dialplan_unavailable": "Install and activate the matching managed-media PBX configuration",
    "player_call_limit": "The gateway has reached its configured simultaneous media-session limit",
    "player_control_not_supported": "This media source or current session does not support the requested control",
    "player_not_active": "No managed media session is active on this phone",
}


async def refusal_detail(response):
    """Read a small error envelope, returning static text only."""
    try:
        chunks, length = [], 0
        async for chunk in response.content.iter_chunked(1024):
            length += len(chunk)
            if length > 4096:
                return None
            chunks.append(chunk)
        value = json.loads(b"".join(chunks))
        if (isinstance(value, dict) and value.get("status") == "refused"
                and isinstance(value.get("reason"), str)):
            return REFUSAL_DETAILS.get(value["reason"])
    except (aiohttp.ClientError, asyncio.TimeoutError, ValueError, UnicodeError, RecursionError):
        pass
    return None


class GatewayError(Exception):
    """Only sanitized status is safe to display; never include server response bodies."""
    def __init__(self, status, action_id=None, *, detail=None):
        self.status = status
        self.action_id = action_id
        super().__init__(f"Gateway {status}" + (f": {detail}" if detail else "")
                         + (f"; action {action_id}; do not retry blindly" if action_id else ""))


class GatewayClient:
    def __init__(self, session, url, token):
        self.session = session
        self.url = gateway_url(url)
        self._token = token

    async def _request(self, method, path, payload=None, action_id=None):
        try:
            async with self.session.request(
                method, self.url + path, json=payload,
                headers={"Authorization": f"Bearer {self._token}"},
                timeout=aiohttp.ClientTimeout(total=15), allow_redirects=False,
            ) as response:
                if response.status != 200:
                    status = ("authentication rejected" if response.status in (401, 403)
                              else "refused" if response.status in (400, 409, 413, 429)
                              else "outcome unknown" if method == "POST" else "unavailable")
                    detail = await refusal_detail(response) if status == "refused" else None
                    raise GatewayError(status, action_id, detail=detail)
                chunks = []
                length = 0
                async for chunk in response.content.iter_chunked(16384):
                    length += len(chunk)
                    if length > 256 * 1024:
                        raise GatewayError("invalid response", action_id)
                    chunks.append(chunk)
                raw = b"".join(chunks)
                result = json.loads(raw)
                if not isinstance(result, dict):
                    raise ValueError("Expected object")
                return result
        except GatewayError:
            raise
        except (aiohttp.ClientError, asyncio.TimeoutError, ValueError, UnicodeError):
            raise GatewayError("outcome unknown" if method == "POST" else "unavailable", action_id) from None

    async def snapshot(self):
        try:
            return inventory(await self._request("GET", "/v1/phones"))
        except ValueError:
            raise GatewayError("invalid inventory") from None

    async def voice_status(self, line):
        return await self._request("GET", f"/v1/phones/{line}/voice_status")

    async def hands_free_status(self, line):
        return await self._request("GET", f"/v1/phones/{line}/voice_hands_free_status")

    async def action(self, primary, action, values=None, action_id=None):
        key = action_id or uuid.uuid4().hex
        body = {**(values or {}), "idempotency_key": key}
        result = await self._request("POST", f"/v1/phones/{primary}/{action}", body, key)
        if result.get("status") != "accepted" or result.get("action_id") != key:
            raise GatewayError("outcome unknown", key)
        return result
