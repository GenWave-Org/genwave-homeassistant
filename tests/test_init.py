"""Tests for `async_setup_entry`/`async_unload_entry` (STORY-362 AC1, F3)."""

from __future__ import annotations

from datetime import timedelta

import pytest
from homeassistant.config_entries import ConfigEntryState
from homeassistant.const import CONF_API_TOKEN, CONF_URL
from homeassistant.core import HomeAssistant
from homeassistant.util import dt as dt_util
from pytest_homeassistant_custom_component.common import MockConfigEntry, async_fire_time_changed
from pytest_homeassistant_custom_component.test_util.aiohttp import AiohttpClientMocker

from custom_components.genwave.const import DOMAIN

from . import BASE_URL, NOW_PLAYING_URL, STANDBY_NOW_PLAYING_JSON, TOKEN


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
