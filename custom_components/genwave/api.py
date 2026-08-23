"""Thin async client for the GenWave announce-token door.

Wraps exactly two GenWave endpoints — `GET /api/announcements/now-playing` (the config-flow
validation read, and later T347's sensor poll) and `POST /api/announcements` (`genwave.announce`)
— and adds no semantics of its own: field names, defaults, and caps are the server's to own. See
`AnnouncementsController`/`AnnouncementRequest` in the GenWave app repo (SPEC F143.1/F145.3) for
the wire contract this mirrors.

The token is a bearer credential handed to this client's constructor by the caller (the config
entry's own data) — this module never reads it from anywhere else, never logs it, and never
includes it in an exception message.
"""

from __future__ import annotations

import asyncio
from dataclasses import dataclass
from typing import Any, Final

import aiohttp
import yarl

from .const import API_TIMEOUT_SECONDS

NOW_PLAYING_PATH: Final = "/api/announcements/now-playing"
ANNOUNCEMENTS_PATH: Final = "/api/announcements"


def redact_url(url: str) -> str:
    """Strip embedded userinfo before a URL is ever echoed into a log line, an exception message,
    or a config entry title — a URL is caller-supplied and may carry a `user:pass@` or bare
    `token@` prefix that this module must never repeat back verbatim.
    """
    return str(yarl.URL(url).with_user(None).with_password(None))


class GenWaveApiError(Exception):
    """Base error for every GenWave API failure."""


class GenWaveCannotConnect(GenWaveApiError):
    """The GenWave host could not be reached — DNS, refused connection, or a timeout."""


class GenWaveInvalidAuth(GenWaveApiError):
    """The announce token was rejected (401) — dead, revoked, or never configured."""


class GenWaveApiProblem(GenWaveApiError):
    """The server refused the request with an honest reason.

    Carries the server's own ProblemDetails `detail` string verbatim (403 SpectatorMode, 429 caps,
    400 validation) — the caller surfaces `detail` to the user rather than inventing its own
    wording, honoring "the integration adds no semantics of its own".
    """

    def __init__(self, status: int, detail: str) -> None:
        """Initialize with the HTTP status and the server's own detail string."""
        super().__init__(detail)
        self.status = status
        self.detail = detail


@dataclass(slots=True, frozen=True)
class NowPlaying:
    """The minimal now-playing read — mirrors `AnnouncementNowPlayingDto` field for field."""

    title: str | None
    artist: str | None
    dj_name: str | None


class GenWaveApiClient:
    """Bearer-authed async client for the GenWave announcements family."""

    def __init__(self, session: aiohttp.ClientSession, base_url: str, token: str) -> None:
        """Initialize the client with an HA-owned session, the station URL, and the announce token."""
        self._session = session
        self._base_url = base_url.rstrip("/")
        self._token = token

    async def async_get_now_playing(self) -> NowPlaying:
        """GET /api/announcements/now-playing — the config-flow validation read."""
        payload = await self._async_request("GET", NOW_PLAYING_PATH)
        return NowPlaying(
            title=payload.get("title"),
            artist=payload.get("artist"),
            dj_name=payload.get("djName"),
        )

    async def async_announce(
        self,
        message: str,
        *,
        verbatim: bool = False,
        ttl_seconds: int | None = None,
        voice: str | None = None,
    ) -> int:
        """POST /api/announcements — the 1:1 wire body `genwave.announce` sends.

        Field mapping is snake_case-in/camelCase-on-the-wire only: `ttl_seconds` -> `ttlSeconds`.
        `ttl_seconds`/`voice` are omitted from the body entirely when not supplied, matching the
        server's own "omitted means its own default" contract (`AnnouncementRequest`'s remarks) —
        this client never invents a default the server doesn't already have.

        Returns the accepted row's id (`AnnouncementAcceptedDto.Id`).
        """
        body: dict[str, Any] = {"message": message, "verbatim": verbatim}
        if ttl_seconds is not None:
            body["ttlSeconds"] = ttl_seconds
        if voice is not None:
            body["voice"] = voice

        payload = await self._async_request("POST", ANNOUNCEMENTS_PATH, json_body=body)
        return int(payload["id"])

    async def _async_request(
        self, method: str, path: str, *, json_body: dict[str, Any] | None = None
    ) -> dict[str, Any]:
        url = f"{self._base_url}{path}"
        headers = {"Authorization": f"Bearer {self._token}"}

        try:
            async with asyncio.timeout(API_TIMEOUT_SECONDS):
                async with self._session.request(method, url, headers=headers, json=json_body) as response:
                    if response.status == 401:
                        raise GenWaveInvalidAuth("the announce token was refused")
                    if response.status >= 400:
                        detail = await self._async_problem_detail(response)
                        raise GenWaveApiProblem(response.status, detail)
                    return await response.json()
        except TimeoutError as err:
            raise GenWaveCannotConnect(f"timed out reaching {redact_url(self._base_url)}") from err
        except aiohttp.ClientError as err:
            raise GenWaveCannotConnect(str(err)) from err

    @staticmethod
    async def _async_problem_detail(response: aiohttp.ClientResponse) -> str:
        """The server's own ProblemDetails `detail`, or an honest fallback if the body isn't one."""
        try:
            body = await response.json(content_type=None)
        except (aiohttp.ContentTypeError, ValueError):
            return getattr(response, "reason", None) or f"HTTP {response.status}"

        if isinstance(body, dict):
            detail = body.get("detail")
            if isinstance(detail, str) and detail:
                return detail

        return getattr(response, "reason", None) or f"HTTP {response.status}"
