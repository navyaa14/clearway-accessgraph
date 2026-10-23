"""Ring Partner API client (server-side only).

Implements only documented endpoints (developer.amazon.com/docs/ring/api-documentation.html):
  POST https://oauth.ring.com/oauth/token                         (refresh_token grant)
  GET  /v1/users/me
  GET  /v1/devices?include=status,capabilities
  GET  /v1/devices/{device_id}/capabilities
  GET  /v1/devices/{device_id}/status
  POST /v1/devices/{device_id}/media/image/download              (JPEG/PNG snapshot)
  POST /v1/devices/{device_id}/media/streaming/whep/sessions      (see whep.py)
  DELETE <Location of WHEP session>
Required headers: Authorization: Bearer, User-Agent: App/version, Hardware_Id: UUID.
"""
from __future__ import annotations

import asyncio
import time
import uuid
from dataclasses import dataclass

import httpx

from ..config import Settings


class RingError(Exception):
    def __init__(self, status: int, message: str, retry_after: float | None = None, kind: str = "http"):
        super().__init__(f"Ring API {status}: {message}")
        self.status, self.message, self.retry_after, self.kind = status, message, retry_after, kind

    @property
    def user_message(self) -> str:
        return {
            401: "Ring authorisation failed or expired. Re-link the Ring account.",
            403: "This Ring account has not granted the required permission.",
            404: "The Ring device was not found for this account.",
            406: "Request blocked by Ring's firewall (missing headers or unregistered egress IP).",
            429: "Ring rate limit reached; retrying later.",
            503: "The Ring device is offline or unavailable.",
        }.get(self.status, self.message)


@dataclass
class TokenState:
    access_token: str | None
    refresh_token: str | None
    expires_at: float | None = None


class RingClient:
    def __init__(self, settings: Settings, transport: httpx.AsyncBaseTransport | None = None, token_sink=None):
        self.s = settings
        self.tokens = TokenState(settings.ring_access_token, settings.ring_refresh_token)
        self.hardware_id = settings.ring_hardware_id or str(uuid.uuid5(uuid.NAMESPACE_URL, f"clearway:{settings.ring_client_id}:{settings.ring_device_id}"))
        self._transport = transport
        self._token_sink = token_sink  # persists rotated tokens (encrypted) when provided
        self._lock = asyncio.Lock()

    def headers(self, extra: dict | None = None) -> dict:
        h = {"User-Agent": self.s.ring_user_agent, "Hardware_Id": self.hardware_id, "Accept": "application/vnd.api+json, application/json"}
        if self.tokens.access_token:
            h["Authorization"] = f"Bearer {self.tokens.access_token}"
        if extra:
            h.update(extra)
        return h

    def http(self) -> httpx.AsyncClient:
        return httpx.AsyncClient(base_url=self.s.ring_api_base, timeout=20.0, transport=self._transport)

    async def refresh(self) -> bool:
        if not (self.tokens.refresh_token and self.s.ring_client_id and self.s.ring_client_secret):
            return False
        async with self._lock:
            async with httpx.AsyncClient(timeout=20.0, transport=self._transport) as c:
                r = await c.post(self.s.ring_oauth_url, data={
                    "grant_type": "refresh_token", "refresh_token": self.tokens.refresh_token,
                    "client_id": self.s.ring_client_id, "client_secret": self.s.ring_client_secret,
                }, headers={"Content-Type": "application/x-www-form-urlencoded", "User-Agent": self.s.ring_user_agent})
            if r.status_code != 200:
                raise RingError(r.status_code, "token refresh failed")
            body = r.json()
            # each refresh returns a NEW refresh token that invalidates the old one
            self.tokens = TokenState(body["access_token"], body.get("refresh_token", self.tokens.refresh_token),
                                     time.time() + float(body.get("expires_in", 14400)))
            if self._token_sink:
                self._token_sink(self.tokens)
            return True

    async def request(self, method: str, path: str, *, retries: int = 3, **kw) -> httpx.Response:
        if not self.tokens.access_token and self.tokens.refresh_token:
            await self.refresh()
        if not self.tokens.access_token:
            raise RingError(401, "no Ring access token configured", kind="not_configured")
        # Per-request headers (e.g. Content-Type: application/sdp for WHEP) are captured ONCE and re-applied on
        # every attempt; headers() is rebuilt each time so a refreshed bearer token is picked up after a 401.
        extra_headers = dict(kw.pop("headers", None) or {})
        refreshed = False
        for attempt in range(retries + 1):
            async with self.http() as c:
                r = await c.request(method, path, headers=self.headers(extra_headers), **kw)
            if r.status_code == 401 and not refreshed and self.tokens.refresh_token:
                refreshed = True
                await self.refresh()
                continue
            if r.status_code == 429 and attempt < retries:
                ra = float(r.headers.get("Retry-After", 2 ** attempt))
                await asyncio.sleep(min(ra, 10))
                continue
            if r.status_code >= 400:
                raise RingError(r.status_code, _err_detail(r), float(r.headers["Retry-After"]) if "Retry-After" in r.headers else None)
            return r
        raise RingError(429, "rate limited after retries")

    async def me(self) -> dict:
        return (await self.request("GET", "/v1/users/me")).json()

    async def devices(self) -> list[dict]:
        r = await self.request("GET", "/v1/devices", params={"include": "status,capabilities"})
        body = r.json()
        return body.get("data", body if isinstance(body, list) else [])

    async def capabilities(self, device_id: str) -> dict:
        return (await self.request("GET", f"/v1/devices/{device_id}/capabilities")).json()

    async def status(self, device_id: str) -> dict:
        return (await self.request("GET", f"/v1/devices/{device_id}/status")).json()

    async def unlink(self) -> int:
        """Partner-side unlink of the app integration (documented: DELETE /v1/accounts/me/app-integrations).
        Returns the HTTP status. Ring does not document a separate OAuth token-revocation endpoint."""
        r = await self.request("DELETE", "/v1/accounts/me/app-integrations", retries=1)
        return r.status_code

    async def snapshot(self, device_id: str, component_id: str | None = None) -> tuple[bytes, str]:
        body = {"components": [int(component_id)]} if component_id else {}
        r = await self.request("POST", f"/v1/devices/{device_id}/media/image/download", json=body)
        ctype = r.headers.get("Content-Type", "")
        if not ctype.startswith("image/"):
            raise RingError(r.status_code, f"unexpected snapshot content type '{ctype}' (media may be encrypted)", kind="unreadable")
        return r.content, ctype


def _err_detail(r: httpx.Response) -> str:
    try:
        j = r.json()
        errs = j.get("errors") if isinstance(j, dict) else None
        if errs:
            e = errs[0]
            return str(e.get("detail") or e.get("title") or e)
        return str(j)[:200]
    except Exception:
        return r.text[:200] or r.reason_phrase
