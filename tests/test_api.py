"""Direct unit tests for `GenWaveApiClient` (the client the live wire-proof also drives)."""

from __future__ import annotations

import pytest
from homeassistant.core import HomeAssistant
from homeassistant.helpers.aiohttp_client import async_get_clientsession
from pytest_homeassistant_custom_component.test_util.aiohttp import AiohttpClientMocker

from custom_components.genwave.api import (
    NOW_PLAYING_PATH,
    GenWaveApiClient,
    GenWaveApiProblem,
    GenWaveCannotConnect,
    NowPlaying,
)

from . import ANNOUNCEMENTS_URL, BASE_URL, NOW_PLAYING_URL, ON_AIR_NOW_PLAYING_JSON, TOKEN


def _client(hass: HomeAssistant) -> GenWaveApiClient:
    return GenWaveApiClient(async_get_clientsession(hass), BASE_URL, TOKEN)


async def test_now_playing_maps_djname_to_dj_name(
    hass: HomeAssistant, aioclient_mock: AiohttpClientMocker
) -> None:
    """The wire's camelCase `djName` becomes the client's `dj_name` — everything else passes through."""
    aioclient_mock.get(NOW_PLAYING_URL, json=ON_AIR_NOW_PLAYING_JSON)

    result = await _client(hass).async_get_now_playing()

    assert result == NowPlaying(title="A Song", artist="An Artist", dj_name="Flip")


async def test_problem_detail_falls_back_when_the_body_is_not_json(
    hass: HomeAssistant, aioclient_mock: AiohttpClientMocker
) -> None:
    """A non-JSON error body (e.g. a proxy's own HTML page) still raises an honest, readable detail."""
    aioclient_mock.post(
        ANNOUNCEMENTS_URL,
        status=502,
        text="<html>Bad Gateway</html>",
        headers={"content-type": "text/html"},
    )

    with pytest.raises(GenWaveApiProblem) as excinfo:
        await _client(hass).async_announce("x")

    assert excinfo.value.status == 502
    assert excinfo.value.detail


async def test_timeout_wording_redacts_credentials_from_the_base_url(
    hass: HomeAssistant, aioclient_mock: AiohttpClientMocker
) -> None:
    """Pin the `GenWaveCannotConnect` timeout message: a credentialed base URL is redacted before
    it's echoed back, exactly like every other place this client repeats a caller-supplied URL
    (`redact_url`'s own header rule). RED PROOF: swap `redact_url(self._base_url)` for the raw
    `self._base_url` in `_async_request`'s `except TimeoutError` branch and this test reds.
    """
    credentialed_url = "http://a-secret-user:a-secret-pass@genwave.example.com"
    aioclient_mock.get(f"{credentialed_url}{NOW_PLAYING_PATH}", exc=TimeoutError())
    client = GenWaveApiClient(async_get_clientsession(hass), credentialed_url, TOKEN)

    with pytest.raises(GenWaveCannotConnect) as excinfo:
        await client.async_get_now_playing()

    assert "a-secret-user" not in str(excinfo.value)
    assert "a-secret-pass" not in str(excinfo.value)
    assert "genwave.example.com" in str(excinfo.value)
