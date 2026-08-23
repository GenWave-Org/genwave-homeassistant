"""Tests for the GenWave config flow (STORY-362 AC1/AC4)."""

from __future__ import annotations

import aiohttp
import pytest
from homeassistant import config_entries
from homeassistant.const import CONF_API_TOKEN, CONF_URL
from homeassistant.core import HomeAssistant
from homeassistant.data_entry_flow import FlowResultType
from pytest_homeassistant_custom_component.common import MockConfigEntry
from pytest_homeassistant_custom_component.test_util.aiohttp import AiohttpClientMocker

from custom_components.genwave.const import DOMAIN

from . import BASE_URL, NOW_PLAYING_URL, STANDBY_NOW_PLAYING_JSON, TOKEN


async def test_user_flow_validates_live_then_creates_entry(
    hass: HomeAssistant, aioclient_mock: AiohttpClientMocker
) -> None:
    """AC1: a good URL/token pair is validated live via the now-playing read, then an entry is created."""
    aioclient_mock.get(NOW_PLAYING_URL, json=STANDBY_NOW_PLAYING_JSON)

    result = await hass.config_entries.flow.async_init(DOMAIN, context={"source": config_entries.SOURCE_USER})
    assert result["type"] is FlowResultType.FORM
    assert result["step_id"] == "user"

    result = await hass.config_entries.flow.async_configure(
        result["flow_id"], {CONF_URL: BASE_URL, CONF_API_TOKEN: TOKEN}
    )

    assert result["type"] is FlowResultType.CREATE_ENTRY
    assert result["data"] == {CONF_URL: BASE_URL, CONF_API_TOKEN: TOKEN}
    # One live read in the flow itself (this AC's own contract) + one more when
    # `async_setup_entry` re-validates before the entry comes up (this integration's own
    # ConfigEntryNotReady/ConfigEntryAuthFailed contract) — both against the same now-playing read.
    assert len(aioclient_mock.mock_calls) == 2
    assert all(str(url) == NOW_PLAYING_URL for _, url, _, _ in aioclient_mock.mock_calls)


async def test_unreachable_url_is_a_form_error_not_a_broken_entry(
    hass: HomeAssistant, aioclient_mock: AiohttpClientMocker
) -> None:
    """AC1: a well-formed URL the station doesn't answer never creates an entry — it comes back as
    `cannot_connect` (distinct from `invalid_url`, which means the shape itself was bad)."""
    aioclient_mock.get(NOW_PLAYING_URL, exc=aiohttp.ClientConnectionError("refused"))

    result = await hass.config_entries.flow.async_init(DOMAIN, context={"source": config_entries.SOURCE_USER})
    result = await hass.config_entries.flow.async_configure(
        result["flow_id"], {CONF_URL: BASE_URL, CONF_API_TOKEN: TOKEN}
    )

    assert result["type"] is FlowResultType.FORM
    assert result["errors"] == {"base": "cannot_connect"}
    assert not hass.config_entries.async_entries(DOMAIN)


async def test_malformed_url_is_invalid_url_not_cannot_connect(
    hass: HomeAssistant, aioclient_mock: AiohttpClientMocker
) -> None:
    """F6: a URL with no scheme is rejected as `invalid_url` before any connection is attempted —
    distinct from `cannot_connect`, which means the shape was fine but the station wasn't
    reachable."""
    result = await hass.config_entries.flow.async_init(DOMAIN, context={"source": config_entries.SOURCE_USER})
    result = await hass.config_entries.flow.async_configure(
        result["flow_id"], {CONF_URL: "not-a-url", CONF_API_TOKEN: TOKEN}
    )

    assert result["type"] is FlowResultType.FORM
    assert result["errors"] == {"base": "invalid_url"}
    assert not hass.config_entries.async_entries(DOMAIN)
    assert len(aioclient_mock.mock_calls) == 0


async def test_bad_token_is_a_form_error_not_a_broken_entry(
    hass: HomeAssistant, aioclient_mock: AiohttpClientMocker
) -> None:
    """AC1: a bad/revoked token never creates an entry — it comes back as a form error."""
    aioclient_mock.get(NOW_PLAYING_URL, status=401)

    result = await hass.config_entries.flow.async_init(DOMAIN, context={"source": config_entries.SOURCE_USER})
    result = await hass.config_entries.flow.async_configure(
        result["flow_id"], {CONF_URL: BASE_URL, CONF_API_TOKEN: TOKEN}
    )

    assert result["type"] is FlowResultType.FORM
    assert result["errors"] == {"base": "invalid_auth"}
    assert not hass.config_entries.async_entries(DOMAIN)


async def test_second_flow_aborts_single_instance_allowed(
    hass: HomeAssistant, aioclient_mock: AiohttpClientMocker
) -> None:
    """F2: manifest.json's `single_config_entry` blocks a second flow before any input is even
    asked for — genwave.announce carries no target selector (STORY-362), so a second station
    could never be addressed anyway. No now-playing call happens; the flow never reaches a form.

    `_async_abort_entries_match`'s raw-string dedupe in `async_step_user` is kept as a belt below
    this manifest-level guarantee, but is no longer independently reachable through any public
    entry point — every second-flow attempt, regardless of URL, aborts here first.
    """
    MockConfigEntry(domain=DOMAIN, data={CONF_URL: BASE_URL, CONF_API_TOKEN: TOKEN}).add_to_hass(hass)

    result = await hass.config_entries.flow.async_init(DOMAIN, context={"source": config_entries.SOURCE_USER})

    assert result["type"] is FlowResultType.ABORT
    assert result["reason"] == "single_instance_allowed"
    assert len(aioclient_mock.mock_calls) == 0


@pytest.fixture
def existing_entry(hass: HomeAssistant) -> MockConfigEntry:
    """A config entry already in place, ready for a reauth flow to target."""
    entry = MockConfigEntry(domain=DOMAIN, data={CONF_URL: BASE_URL, CONF_API_TOKEN: TOKEN})
    entry.add_to_hass(hass)
    return entry


async def test_reauth_with_a_working_token_updates_the_entry(
    hass: HomeAssistant, aioclient_mock: AiohttpClientMocker, existing_entry: MockConfigEntry
) -> None:
    """AC4: a dead token surfaces as reauth; a fresh, working token completes it and updates the entry."""
    aioclient_mock.get(NOW_PLAYING_URL, json=STANDBY_NOW_PLAYING_JSON)
    new_token = "a-fresh-token-after-regenerate"

    result = await existing_entry.start_reauth_flow(hass)
    assert result["step_id"] == "reauth_confirm"

    result = await hass.config_entries.flow.async_configure(result["flow_id"], {CONF_API_TOKEN: new_token})

    assert result["type"] is FlowResultType.ABORT
    assert result["reason"] == "reauth_successful"
    assert existing_entry.data[CONF_API_TOKEN] == new_token
    assert existing_entry.data[CONF_URL] == BASE_URL


async def test_reauth_with_a_still_dead_token_stays_on_the_form(
    hass: HomeAssistant, aioclient_mock: AiohttpClientMocker, existing_entry: MockConfigEntry
) -> None:
    """A reauth attempt with another bad token is refused, never silently accepted."""
    aioclient_mock.get(NOW_PLAYING_URL, status=401)

    result = await existing_entry.start_reauth_flow(hass)
    result = await hass.config_entries.flow.async_configure(
        result["flow_id"], {CONF_API_TOKEN: "still-bad"}
    )

    assert result["type"] is FlowResultType.FORM
    assert result["step_id"] == "reauth_confirm"
    assert result["errors"] == {"base": "invalid_auth"}
    assert existing_entry.data[CONF_API_TOKEN] == TOKEN


async def test_entry_title_never_includes_credentials_from_the_url(
    hass: HomeAssistant, aioclient_mock: AiohttpClientMocker
) -> None:
    """F4: a URL with embedded userinfo is redacted out of the entry title — the title is shown
    all over the UI (device list, notify entity name), never a place for a leaked credential."""
    credentialed_url = "http://a-secret-user:a-secret-pass@genwave.example.com"
    aioclient_mock.get(f"{credentialed_url}/api/announcements/now-playing", json=STANDBY_NOW_PLAYING_JSON)

    result = await hass.config_entries.flow.async_init(DOMAIN, context={"source": config_entries.SOURCE_USER})
    result = await hass.config_entries.flow.async_configure(
        result["flow_id"], {CONF_URL: credentialed_url, CONF_API_TOKEN: TOKEN}
    )

    assert result["type"] is FlowResultType.CREATE_ENTRY
    assert "a-secret-user" not in result["title"]
    assert "a-secret-pass" not in result["title"]
    assert result["title"] == "GenWave (http://genwave.example.com)"
