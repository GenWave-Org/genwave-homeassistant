"""Direct unit tests for `GenWaveNowPlayingCoordinator`'s error mapping (STORY-362 AC3, SPEC F147.3).

`tests/test_sensor.py` proves the same mapping end to end (poll -> entity state/reauth flow);
these tests isolate `_async_update_data` itself so a broken mapping fails fast and specifically.
"""

from __future__ import annotations

import aiohttp
import pytest
from homeassistant.const import CONF_API_TOKEN, CONF_URL
from homeassistant.core import HomeAssistant
from homeassistant.exceptions import ConfigEntryAuthFailed
from homeassistant.helpers.aiohttp_client import async_get_clientsession
from homeassistant.helpers.update_coordinator import UpdateFailed
from pytest_homeassistant_custom_component.common import MockConfigEntry
from pytest_homeassistant_custom_component.test_util.aiohttp import AiohttpClientMocker

from custom_components.genwave.api import GenWaveApiClient
from custom_components.genwave.const import DOMAIN, NOW_PLAYING_POLL_INTERVAL_SECONDS
from custom_components.genwave.coordinator import GenWaveNowPlayingCoordinator

from . import BASE_URL, NOW_PLAYING_URL, TOKEN


def _coordinator(hass: HomeAssistant) -> GenWaveNowPlayingCoordinator:
    entry = MockConfigEntry(domain=DOMAIN, data={CONF_URL: BASE_URL, CONF_API_TOKEN: TOKEN})
    entry.add_to_hass(hass)
    client = GenWaveApiClient(async_get_clientsession(hass), BASE_URL, TOKEN)
    return GenWaveNowPlayingCoordinator(hass, entry, client)


def test_poll_interval_is_the_30s_floor() -> None:
    """SPEC F147.3/gh-#558: 30s is the floor this integration polls at, not a faster default —
    2 of the 60 requests/min door budget, per `NOW_PLAYING_POLL_INTERVAL_SECONDS`'s own comment."""
    assert NOW_PLAYING_POLL_INTERVAL_SECONDS == 30


async def test_a_dead_token_maps_to_config_entry_auth_failed(
    hass: HomeAssistant, aioclient_mock: AiohttpClientMocker
) -> None:
    """RED PROOF: `GenWaveInvalidAuth` (401) must become `ConfigEntryAuthFailed`, not `UpdateFailed`
    — the mapping that lets a poll-discovered dead token start reauth (AC4's third door). Swap the
    `except GenWaveInvalidAuth` branch in `coordinator.py` for a plain `UpdateFailed` and this
    test reds."""
    aioclient_mock.get(NOW_PLAYING_URL, status=401)
    coordinator = _coordinator(hass)

    with pytest.raises(ConfigEntryAuthFailed):
        await coordinator._async_update_data()


async def test_a_server_problem_maps_to_update_failed_with_the_servers_own_detail(
    hass: HomeAssistant, aioclient_mock: AiohttpClientMocker
) -> None:
    """A 403/429/etc. server refusal becomes `UpdateFailed`, carrying the server's own detail —
    never `ConfigEntryNotReady` (that arm is setup-only, not reused for poll failures)."""
    aioclient_mock.get(NOW_PLAYING_URL, status=403, json={"detail": "The station is public"})
    coordinator = _coordinator(hass)

    with pytest.raises(UpdateFailed, match="The station is public"):
        await coordinator._async_update_data()


async def test_a_connection_failure_maps_to_update_failed(
    hass: HomeAssistant, aioclient_mock: AiohttpClientMocker
) -> None:
    """An unreachable station also becomes `UpdateFailed` — the entity goes unavailable and the
    coordinator's own schedule retries, same as any other transient poll failure."""
    aioclient_mock.get(NOW_PLAYING_URL, exc=aiohttp.ClientConnectionError("refused"))
    coordinator = _coordinator(hass)

    with pytest.raises(UpdateFailed):
        await coordinator._async_update_data()
