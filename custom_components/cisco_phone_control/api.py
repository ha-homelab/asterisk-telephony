"""Bounded, non-retrying authenticated gateway client."""
import asyncio
import json
import uuid

import aiohttp

from .model import gateway_url, inventory


class GatewayError(Exception):
    """Only sanitized status is safe to display; never include server response bodies."""
    def __init__(self, status, action_id=None):
        self.status = status
        self.action_id = action_id
        super().__init__(f"Gateway {status}" + (f"; action {action_id}; do not retry blindly" if action_id else ""))


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
                    raise GatewayError(status, action_id)
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
