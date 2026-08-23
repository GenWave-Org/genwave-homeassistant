"""Tests for `async_setup_entry`/`async_unload_entry` (STORY-362 AC1, F3)."""

from __future__ import annotations

from datetime import timedelta

import aiohttp
import pytest
from homeassistant.config_entries import ConfigEntryState
from homeassistant.const import CONF_API_TOKEN, CONF_URL
from homeassistant.core import HomeAssistant
from homeassistant.exceptions import HomeAssistantError
from homeassistant.util import dt as dt_util
from pytest_homeassistant_custom_component.common import MockConfigEntry, async_fire_time_changed
from pytest_homeassistant_custom_component.test_util.aiohttp import AiohttpClientMocker

from custom_components.genwave import _resolve_single_entry
from custom_components.genwave.const import DOMAIN

from . import (
    BASE_URL,
    NOW_PLAYING_URL,
    STANDBY_NOW_PLAYING_JSON,
    TOKEN,
    async_setup_loaded_entry,
)


@pytest.fixture
def not_loaded_entry(hass: HomeAssistant) -> MockConfigEntry:
    """A config entry added to hass but not yet set up, ready for `async_setup` to drive."""
    entry = MockConfigEntry(domain=DOMAIN, data={CONF_URL: BASE_URL, CONF_API_TOKEN: TOKEN})
    entry.add_to_hass(hass)
    return entry


async def test_404_at_setup_yields_retry_not_setup_error(
    hass: HomeAssistant, aioclient_mock: AiohttpClientMocker, not_loaded_entry: MockConfigEntry
) -> None:
    """F3: a 404 (Admin:Enabled off) is retry-worthy, not a hard setup failure — the entry stays
    recoverable (SETUP_RETRY) instead of landing in SETUP_ERROR, which a user would have to fix
    by hand."""
    aioclient_mock.get(NOW_PLAYING_URL, status=404, json={"detail": "Admin:Enabled is false"})

    assert not await hass.config_entries.async_setup(not_loaded_entry.entry_id)
    await hass.async_block_till_done()

    assert not_loaded_entry.state is ConfigEntryState.SETUP_RETRY


async def test_429_at_setup_yields_retry_not_setup_error(
    hass: HomeAssistant, aioclient_mock: AiohttpClientMocker, not_loaded_entry: MockConfigEntry
) -> None:
    """F3: 429 (the accept-rate door window) is the same retry-worthy, non-auth state as 404/502/503."""
    aioclient_mock.get(NOW_PLAYING_URL, status=429, json={"detail": "Too many requests"})

    assert not await hass.config_entries.async_setup(not_loaded_entry.entry_id)
    await hass.async_block_till_done()

    assert not_loaded_entry.state is ConfigEntryState.SETUP_RETRY


async def test_setup_retries_and_a_later_success_brings_the_entry_up(
    hass: HomeAssistant, aioclient_mock: AiohttpClientMocker, not_loaded_entry: MockConfigEntry
) -> None:
    """F3: the entry isn't stuck once the station recovers — the scheduled retry picks up a
    success on its own, no reload requested by the user."""
    aioclient_mock.get(NOW_PLAYING_URL, status=404, json={"detail": "Admin:Enabled is false"})

    assert not await hass.config_entries.async_setup(not_loaded_entry.entry_id)
    await hass.async_block_till_done()
    assert not_loaded_entry.state is ConfigEntryState.SETUP_RETRY

    aioclient_mock.clear_requests()
    aioclient_mock.get(NOW_PLAYING_URL, json=STANDBY_NOW_PLAYING_JSON)

    async_fire_time_changed(hass, dt_util.utcnow() + timedelta(seconds=10))
    await hass.async_block_till_done(wait_background_tasks=True)

    assert not_loaded_entry.state is ConfigEntryState.LOADED


async def test_cannot_connect_at_setup_redacts_credentials_from_the_retry_reason(
    hass: HomeAssistant, aioclient_mock: AiohttpClientMocker
) -> None:
    """Pin `async_setup_entry`'s `cannot_connect` `ConfigEntryNotReady` message: a credentialed URL
    is redacted out of the retry reason the config entries machinery stores on the entry (surfaced
    in the UI's "why won't this load" repair flow) — the same rule `redact_url`'s other callers
    already honor. Distinct from the `GenWaveApiProblem` branch's own redaction test in
    `test_services.py`; `GenWaveCannotConnect` (a refused/timed-out connection, not an HTTP status)
    is a separate `except` arm with its own `redact_url` call to lose.

    RED PROOF: swap `redact_url(entry.data[CONF_URL])` for the raw URL in the `cannot_connect`
    branch of `async_setup_entry` and this test reds.
    """
    credentialed_url = "http://a-secret-user:a-secret-pass@genwave.example.com"
    aioclient_mock.get(
        f"{credentialed_url}/api/announcements/now-playing",
        exc=aiohttp.ClientConnectionError("refused"),
    )
    entry = MockConfigEntry(domain=DOMAIN, data={CONF_URL: credentialed_url, CONF_API_TOKEN: TOKEN})
    entry.add_to_hass(hass)

    assert not await hass.config_entries.async_setup(entry.entry_id)
    await hass.async_block_till_done()

    assert entry.state is ConfigEntryState.SETUP_RETRY
    assert entry.reason is not None
    assert "a-secret-user" not in entry.reason
    assert "a-secret-pass" not in entry.reason
    assert "genwave.example.com" in entry.reason


async def test_resolve_single_entry_raises_the_translated_message_when_none_are_loaded(
    hass: HomeAssistant, aioclient_mock: AiohttpClientMocker
) -> None:
    """`_resolve_single_entry`'s own translated message resolves when no GenWave entry is loaded -
    the message a stray `genwave.announce` call would surface if it somehow outlived its last
    entry (F5, `test_services.py`'s own service-removal test).

    Loads and then removes one entry first, rather than calling `_resolve_single_entry` against a
    `hass` that never loaded `genwave` at all: translations are cached once, on first component
    setup, and never unloaded (`_TranslationCache`'s own contract) - this mirrors the real path,
    where the resolver is only ever reachable through a service that itself only exists once
    `genwave` has loaded at least once.
    """
    entry = await async_setup_loaded_entry(hass, aioclient_mock)
    await hass.config_entries.async_remove(entry.entry_id)
    await hass.async_block_till_done()

    with pytest.raises(HomeAssistantError, match="No GenWave integration is configured") as excinfo:
        _resolve_single_entry(hass)

    assert excinfo.value.translation_key == "no_entry_configured"


async def test_resolve_single_entry_raises_the_translated_message_when_more_than_one_is_loaded(
    hass: HomeAssistant, aioclient_mock: AiohttpClientMocker
) -> None:
    """Defense-in-depth (this function's own docstring): two GenWave entries loaded some other way
    than the single-instance config flow makes `_resolve_single_entry` refuse to guess rather than
    silently picking one.

    The second entry is created and set up only *after* the first has finished loading - setting
    up the first while it's the only known entry is what triggers `genwave`'s own one-time
    component bootstrap (which loads every not-yet-loaded entry of the domain in one batch); doing
    both up front would load them together before this test ever gets to call the resolver.
    """
    first = await async_setup_loaded_entry(hass, aioclient_mock)

    aioclient_mock.get(
        "http://second.example.com/api/announcements/now-playing", json=STANDBY_NOW_PLAYING_JSON
    )
    second = MockConfigEntry(
        domain=DOMAIN, data={CONF_URL: "http://second.example.com", CONF_API_TOKEN: TOKEN}
    )
    second.add_to_hass(hass)
    assert await hass.config_entries.async_setup(second.entry_id)
    await hass.async_block_till_done()

    with pytest.raises(HomeAssistantError, match="More than one GenWave station") as excinfo:
        _resolve_single_entry(hass)

    assert excinfo.value.translation_key == "multiple_entries_configured"
    assert first.state is ConfigEntryState.LOADED
