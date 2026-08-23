"""Tests for the GenWave `sensor` platform and its polling coordinator (STORY-362 AC3, SPEC F147.3)."""

from __future__ import annotations

from datetime import timedelta

from homeassistant.config_entries import SOURCE_REAUTH, ConfigEntryState
from homeassistant.const import STATE_UNAVAILABLE
from homeassistant.core import HomeAssistant
from homeassistant.helpers import entity_registry as er
from homeassistant.util import dt as dt_util
from pytest_homeassistant_custom_component.common import async_fire_time_changed
from pytest_homeassistant_custom_component.test_util.aiohttp import AiohttpClientMocker

from custom_components.genwave.const import DOMAIN, NOW_PLAYING_POLL_INTERVAL_SECONDS

from . import NOW_PLAYING_URL, ON_AIR_NOW_PLAYING_JSON, STANDBY_NOW_PLAYING_JSON, async_setup_loaded_entry

SENSOR_ENTITY_ID = "sensor.now_playing"

NEXT_POLL = timedelta(seconds=NOW_PLAYING_POLL_INTERVAL_SECONDS)


async def test_sensor_state_and_attributes_reflect_the_now_playing_read(
    hass: HomeAssistant, aioclient_mock: AiohttpClientMocker
) -> None:
    """AC3: state is the track title; artist/dj_name ride along as attributes."""
    await async_setup_loaded_entry(hass, aioclient_mock, now_playing_json=ON_AIR_NOW_PLAYING_JSON)

    state = hass.states.get(SENSOR_ENTITY_ID)

    assert state is not None
    assert state.state == "A Song"
    assert state.attributes["artist"] == "An Artist"
    assert state.attributes["dj_name"] == "Flip"


async def test_sensor_state_is_idle_when_nothing_is_airing(
    hass: HomeAssistant, aioclient_mock: AiohttpClientMocker
) -> None:
    """A standby read (no title) surfaces as `idle`, not a blank or `None` state."""
    await async_setup_loaded_entry(hass, aioclient_mock, now_playing_json=STANDBY_NOW_PLAYING_JSON)

    state = hass.states.get(SENSOR_ENTITY_ID)

    assert state.state == "idle"
    assert state.attributes["artist"] is None
    assert state.attributes["dj_name"] is None


async def test_sensor_unique_id_is_stable_off_the_entry(
    hass: HomeAssistant, aioclient_mock: AiohttpClientMocker, entity_registry: er.EntityRegistry
) -> None:
    """The sensor's unique_id is derived from the entry_id, not the (mutable) station URL — the
    same stability contract `redact_url`'s callers rely on elsewhere in this integration."""
    entry = await async_setup_loaded_entry(hass, aioclient_mock)

    registered = entity_registry.async_get(SENSOR_ENTITY_ID)

    assert registered is not None
    assert registered.unique_id == f"{entry.entry_id}_now_playing"
    assert registered.config_entry_id == entry.entry_id


async def test_sensor_polls_again_after_the_interval_and_follows_the_new_read(
    hass: HomeAssistant, aioclient_mock: AiohttpClientMocker
) -> None:
    """gh-#558's volume lesson: the coordinator IS the poll (`iot_class: local_polling`) —
    advancing past the interval fetches again, and the sensor's state follows the new read rather
    than a static snapshot from setup."""
    await async_setup_loaded_entry(hass, aioclient_mock, now_playing_json=STANDBY_NOW_PLAYING_JSON)
    aioclient_mock.get(
        NOW_PLAYING_URL, json={"title": "A New Song", "artist": "A New Artist", "djName": "Mike"}
    )

    async_fire_time_changed(hass, dt_util.utcnow() + NEXT_POLL)
    await hass.async_block_till_done(wait_background_tasks=True)

    state = hass.states.get(SENSOR_ENTITY_ID)
    assert state.state == "A New Song"
    assert state.attributes["artist"] == "A New Artist"
    assert len(aioclient_mock.mock_calls) == 1


async def test_a_poll_time_401_marks_the_sensor_unavailable_and_starts_reauth(
    hass: HomeAssistant, aioclient_mock: AiohttpClientMocker
) -> None:
    """AC4's third door: a dead token discovered mid-poll (not just at setup or on
    `genwave.announce`) starts reauth too, and the sensor goes unavailable rather than serving a
    stale reading silently.

    RED PROOF: mapping `GenWaveInvalidAuth` to anything other than `ConfigEntryAuthFailed` in
    `GenWaveNowPlayingCoordinator._async_update_data` reds this — no reauth flow gets started.
    """
    entry = await async_setup_loaded_entry(hass, aioclient_mock)
    aioclient_mock.get(NOW_PLAYING_URL, status=401)

    async_fire_time_changed(hass, dt_util.utcnow() + NEXT_POLL)
    await hass.async_block_till_done(wait_background_tasks=True)

    state = hass.states.get(SENSOR_ENTITY_ID)
    assert state.state == STATE_UNAVAILABLE

    flows = hass.config_entries.flow.async_progress_by_handler(
        DOMAIN, match_context={"source": SOURCE_REAUTH, "entry_id": entry.entry_id}
    )
    assert flows


async def test_a_poll_time_problem_marks_the_sensor_unavailable_without_reauth_or_notready(
    hass: HomeAssistant, aioclient_mock: AiohttpClientMocker
) -> None:
    """A transient server refusal (e.g. 404 Admin:Enabled off) goes unavailable and retries on the
    coordinator's own schedule — never `ConfigEntryNotReady` (that arm is setup-only, never reused
    for poll failures) and never a reauth prompt (the token itself wasn't the problem)."""
    entry = await async_setup_loaded_entry(hass, aioclient_mock)
    aioclient_mock.get(NOW_PLAYING_URL, status=404, json={"detail": "Admin:Enabled is false"})

    async_fire_time_changed(hass, dt_util.utcnow() + NEXT_POLL)
    await hass.async_block_till_done(wait_background_tasks=True)

    state = hass.states.get(SENSOR_ENTITY_ID)
    assert state.state == STATE_UNAVAILABLE
    assert entry.state is ConfigEntryState.LOADED

    flows = hass.config_entries.flow.async_progress_by_handler(
        DOMAIN, match_context={"source": SOURCE_REAUTH, "entry_id": entry.entry_id}
    )
    assert not flows
