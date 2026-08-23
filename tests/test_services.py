"""Tests for the `genwave.announce` service (STORY-362 AC2/AC4)."""

from __future__ import annotations

import logging
from pathlib import Path

import aiohttp
import pytest
import voluptuous as vol
import yaml
from homeassistant.config_entries import SOURCE_REAUTH
from homeassistant.const import CONF_API_TOKEN, CONF_URL
from homeassistant.core import HomeAssistant
from homeassistant.exceptions import HomeAssistantError
from pytest_homeassistant_custom_component.common import MockConfigEntry
from pytest_homeassistant_custom_component.test_util.aiohttp import AiohttpClientMocker

from custom_components.genwave.const import (
    ATTR_MESSAGE,
    ATTR_TTL_SECONDS,
    ATTR_VERBATIM,
    ATTR_VOICE,
    DOMAIN,
    MAX_TTL_SECONDS,
    MIN_TTL_SECONDS,
    SERVICE_ANNOUNCE,
)

from . import ANNOUNCEMENTS_URL, TOKEN, async_setup_loaded_entry

SERVICES_YAML_PATH = Path(__file__).parent.parent / "custom_components" / "genwave" / "services.yaml"


async def test_announce_posts_the_exact_1to1_body(
    hass: HomeAssistant, aioclient_mock: AiohttpClientMocker
) -> None:
    """AC2: message/verbatim/ttl_seconds/voice map onto the wire body unchanged (snake->camel only)."""
    await async_setup_loaded_entry(hass, aioclient_mock)
    aioclient_mock.post(ANNOUNCEMENTS_URL, json={"id": 42})

    await hass.services.async_call(
        DOMAIN,
        SERVICE_ANNOUNCE,
        {
            ATTR_MESSAGE: "Dinner's ready",
            ATTR_VERBATIM: True,
            ATTR_TTL_SECONDS: 600,
            ATTR_VOICE: "af_heart",
        },
        blocking=True,
    )

    assert len(aioclient_mock.mock_calls) == 1
    method, url, data, headers = aioclient_mock.mock_calls[0]
    assert method == "POST"
    assert str(url) == ANNOUNCEMENTS_URL
    assert data == {
        "message": "Dinner's ready",
        "verbatim": True,
        "ttlSeconds": 600,
        "voice": "af_heart",
    }
    assert headers["Authorization"] == f"Bearer {TOKEN}"


async def test_announce_omits_optional_fields_when_not_given(
    hass: HomeAssistant, aioclient_mock: AiohttpClientMocker
) -> None:
    """Omitted `ttl_seconds`/`voice` never become a client-invented default on the wire body."""
    await async_setup_loaded_entry(hass, aioclient_mock)
    aioclient_mock.post(ANNOUNCEMENTS_URL, json={"id": 1})

    await hass.services.async_call(
        DOMAIN, SERVICE_ANNOUNCE, {ATTR_MESSAGE: "Just the message"}, blocking=True
    )

    _, _, data, _ = aioclient_mock.mock_calls[0]
    assert data == {"message": "Just the message", "verbatim": False}
    assert "ttlSeconds" not in data
    assert "voice" not in data


async def test_out_of_range_ttl_is_rejected_before_any_call(
    hass: HomeAssistant, aioclient_mock: AiohttpClientMocker
) -> None:
    """The service schema mirrors SPEC F143.1's fixed 60-3600 bound — a bad value never reaches the wire."""
    await async_setup_loaded_entry(hass, aioclient_mock)

    with pytest.raises(vol.Invalid):
        await hass.services.async_call(
            DOMAIN, SERVICE_ANNOUNCE, {ATTR_MESSAGE: "x", ATTR_TTL_SECONDS: 1}, blocking=True
        )

    assert len(aioclient_mock.mock_calls) == 0


async def test_a_dead_token_raises_and_starts_reauth(
    hass: HomeAssistant, aioclient_mock: AiohttpClientMocker
) -> None:
    """AC4: a dead token surfaces as a reauth prompt AND a loud call failure — never a silent one.

    The message ("The announce token was refused...", capital-opener) is resolved through this
    integration's own `exceptions` translation catalog (`translation_domain`/`translation_key`),
    not a hardcoded string on the raise site - pinning that the wiring actually resolves, not just
    that *some* `HomeAssistantError` came out.
    """
    entry = await async_setup_loaded_entry(hass, aioclient_mock)
    aioclient_mock.post(ANNOUNCEMENTS_URL, status=401)

    with pytest.raises(HomeAssistantError, match="The announce token was refused") as excinfo:
        await hass.services.async_call(DOMAIN, SERVICE_ANNOUNCE, {ATTR_MESSAGE: "x"}, blocking=True)

    assert excinfo.value.translation_key == "announce_token_refused"

    await hass.async_block_till_done()
    flows = hass.config_entries.flow.async_progress_by_handler(
        DOMAIN, match_context={"source": SOURCE_REAUTH, "entry_id": entry.entry_id}
    )
    assert flows


async def test_a_connection_failure_at_announce_time_raises_the_translated_message(
    hass: HomeAssistant, aioclient_mock: AiohttpClientMocker
) -> None:
    """The `GenWaveCannotConnect` branch also resolves through the translation catalog, with the
    underlying error folded in as a placeholder rather than baked into a hardcoded string."""
    await async_setup_loaded_entry(hass, aioclient_mock)
    aioclient_mock.post(ANNOUNCEMENTS_URL, exc=aiohttp.ClientConnectionError("refused"))

    with pytest.raises(HomeAssistantError, match="Could not reach GenWave") as excinfo:
        await hass.services.async_call(DOMAIN, SERVICE_ANNOUNCE, {ATTR_MESSAGE: "x"}, blocking=True)

    assert excinfo.value.translation_key == "cannot_reach_station"


async def test_server_problem_detail_surfaces_verbatim(
    hass: HomeAssistant, aioclient_mock: AiohttpClientMocker
) -> None:
    """403 SpectatorMode / 429 caps / 400 validation: the server's own words, never re-invented."""
    await async_setup_loaded_entry(hass, aioclient_mock)
    aioclient_mock.post(
        ANNOUNCEMENTS_URL,
        status=403,
        json={"detail": "The station is public (Station:SpectatorMode is on)."},
    )

    with pytest.raises(HomeAssistantError, match="The station is public"):
        await hass.services.async_call(DOMAIN, SERVICE_ANNOUNCE, {ATTR_MESSAGE: "x"}, blocking=True)


async def test_token_never_appears_in_any_log_line(
    hass: HomeAssistant, aioclient_mock: AiohttpClientMocker, caplog: pytest.LogCaptureFixture
) -> None:
    """The token crosses the wire in the Authorization header only — never through logging."""
    caplog.set_level(logging.DEBUG)
    await async_setup_loaded_entry(hass, aioclient_mock)
    aioclient_mock.post(ANNOUNCEMENTS_URL, json={"id": 1})

    await hass.services.async_call(DOMAIN, SERVICE_ANNOUNCE, {ATTR_MESSAGE: "x"}, blocking=True)

    assert TOKEN not in caplog.text


async def test_credentials_embedded_in_the_url_never_appear_unredacted_in_logs(
    hass: HomeAssistant, aioclient_mock: AiohttpClientMocker, caplog: pytest.LogCaptureFixture
) -> None:
    """F4: a URL with embedded userinfo is redacted before it's ever echoed into a log line — here,
    the `ConfigEntryNotReady` debug log the retry machinery writes on a failed setup attempt."""
    caplog.set_level(logging.DEBUG)
    credentialed_url = "http://a-secret-user:a-secret-pass@genwave.example.com"
    aioclient_mock.get(f"{credentialed_url}/api/announcements/now-playing", status=404)

    entry = MockConfigEntry(domain=DOMAIN, data={CONF_URL: credentialed_url, CONF_API_TOKEN: TOKEN})
    entry.add_to_hass(hass)

    assert not await hass.config_entries.async_setup(entry.entry_id)
    await hass.async_block_till_done()

    assert "a-secret-user" not in caplog.text
    assert "a-secret-pass" not in caplog.text


async def test_service_removed_after_the_last_entry_unloads_and_is_removed(
    hass: HomeAssistant, aioclient_mock: AiohttpClientMocker
) -> None:
    """F5: `genwave.announce` doesn't outlive the entry that backs it — a stale registration would
    let an automation call it and get a confusing `_resolve_single_entry` error instead of HA's
    own "unknown service" refusal."""
    entry = await async_setup_loaded_entry(hass, aioclient_mock)
    assert hass.services.has_service(DOMAIN, SERVICE_ANNOUNCE)

    await hass.config_entries.async_remove(entry.entry_id)
    await hass.async_block_till_done()

    assert not hass.services.has_service(DOMAIN, SERVICE_ANNOUNCE)


def test_services_yaml_ttl_bounds_match_const() -> None:
    """F10: services.yaml's number selector bounds are bound here to const.py's — the lightest
    honest way to keep the form the user sees from silently drifting off SPEC F143.1's fixed
    60-3600 bound if MIN_TTL_SECONDS/MAX_TTL_SECONDS ever change (a generator felt like overkill
    for two integers)."""
    services = yaml.safe_load(SERVICES_YAML_PATH.read_text())
    ttl_bounds = services["announce"]["fields"]["ttl_seconds"]["selector"]["number"]

    assert ttl_bounds["min"] == MIN_TTL_SECONDS
    assert ttl_bounds["max"] == MAX_TTL_SECONDS
