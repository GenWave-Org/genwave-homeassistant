"""Tests for the GenWave integration."""

from __future__ import annotations

from typing import Any

from homeassistant.const import CONF_API_TOKEN, CONF_URL
from homeassistant.core import HomeAssistant
from pytest_homeassistant_custom_component.common import MockConfigEntry
from pytest_homeassistant_custom_component.test_util.aiohttp import AiohttpClientMocker

BASE_URL = "http://genwave.example.com"
TOKEN = "test-announce-token-should-never-appear-in-any-log-line"

NOW_PLAYING_URL = f"{BASE_URL}/api/announcements/now-playing"
ANNOUNCEMENTS_URL = f"{BASE_URL}/api/announcements"

STANDBY_NOW_PLAYING_JSON = {"title": None, "artist": None, "djName": None}
ON_AIR_NOW_PLAYING_JSON = {"title": "A Song", "artist": "An Artist", "djName": "Flip"}


async def async_setup_loaded_entry(
    hass: HomeAssistant,
    aioclient_mock: AiohttpClientMocker,
    token: str = TOKEN,
    now_playing_json: dict[str, Any] = STANDBY_NOW_PLAYING_JSON,
) -> MockConfigEntry:
    """Queue the setup-time validation read and bring a GenWave config entry fully up.

    Shared by every test that needs a live `genwave.announce`/`notify`/sensor surface rather than
    the config flow itself — mirrors `async_setup_entry`'s own "re-validate live" contract. The
    same queued response also answers the `sensor` platform's own coordinator refresh (T347) —
    `now_playing_json` lets sensor tests bring the entry up already on-air rather than idle.
    """
    aioclient_mock.get(NOW_PLAYING_URL, json=now_playing_json)

    entry = MockConfigEntry(domain="genwave", data={CONF_URL: BASE_URL, CONF_API_TOKEN: token})
    entry.add_to_hass(hass)

    assert await hass.config_entries.async_setup(entry.entry_id)
    await hass.async_block_till_done()

    aioclient_mock.clear_requests()
    return entry
