"""Config flow for the GenWave integration (SPEC F147.2, STORY-362 AC1/AC4)."""

from __future__ import annotations

import logging
from collections.abc import Mapping
from typing import Any

import voluptuous as vol
from homeassistant.config_entries import ConfigFlow, ConfigFlowResult
from homeassistant.const import CONF_API_TOKEN, CONF_URL
from homeassistant.helpers import config_validation as cv
from homeassistant.helpers import selector
from homeassistant.helpers.aiohttp_client import async_get_clientsession

from .api import GenWaveApiClient, GenWaveApiError, GenWaveCannotConnect, GenWaveInvalidAuth, redact_url
from .const import DOMAIN

_LOGGER = logging.getLogger(__name__)

STEP_USER_DATA_SCHEMA = vol.Schema(
    {
        vol.Required(CONF_URL): selector.TextSelector(
            selector.TextSelectorConfig(type=selector.TextSelectorType.URL)
        ),
        vol.Required(CONF_API_TOKEN): str,
    }
)

STEP_REAUTH_DATA_SCHEMA = vol.Schema({vol.Required(CONF_API_TOKEN): str})


class GenWaveConfigFlow(ConfigFlow, domain=DOMAIN):
    """Handle a config flow for GenWave.

    Exactly one live call happens anywhere in this flow: the now-playing read
    (`GenWaveApiClient.async_get_now_playing`) — the same "validated live, before the entry is
    created" contract SPEC F147.2 states. A bad URL or a bad/revoked token surfaces as a form
    error on this step; no entry is ever created from unvalidated input.
    """

    VERSION = 1

    async def async_step_user(self, user_input: dict[str, Any] | None = None) -> ConfigFlowResult:
        """The (and only) setup step: URL + token, validated live."""
        errors: dict[str, str] = {}

        if user_input is not None:
            error_key = await self._async_try_connect(user_input[CONF_URL], user_input[CONF_API_TOKEN])
            if error_key is not None:
                errors["base"] = error_key
            else:
                self._async_abort_entries_match({CONF_URL: user_input[CONF_URL]})
                return self.async_create_entry(title=_entry_title(user_input[CONF_URL]), data=user_input)

        return self.async_show_form(step_id="user", data_schema=STEP_USER_DATA_SCHEMA, errors=errors)

    async def async_step_reauth(self, entry_data: Mapping[str, Any]) -> ConfigFlowResult:
        """A revoked/dead token landed here (SPEC F147.2's reauth prompt, AC4) — never a silent failure."""
        return await self.async_step_reauth_confirm()

    async def async_step_reauth_confirm(self, user_input: dict[str, Any] | None = None) -> ConfigFlowResult:
        """Collect a replacement token and re-validate live before swapping it in."""
        errors: dict[str, str] = {}
        reauth_entry = self._get_reauth_entry()

        if user_input is not None:
            new_token = user_input[CONF_API_TOKEN]
            error_key = await self._async_try_connect(reauth_entry.data[CONF_URL], new_token)
            if error_key is not None:
                errors["base"] = error_key
            else:
                return self.async_update_reload_and_abort(
                    reauth_entry,
                    data={**reauth_entry.data, CONF_API_TOKEN: new_token},
                )

        return self.async_show_form(
            step_id="reauth_confirm",
            data_schema=STEP_REAUTH_DATA_SCHEMA,
            errors=errors,
        )

    async def _async_try_connect(self, url: str, token: str) -> str | None:
        """Attempt the now-playing read; return an error key for the form, or None on success.

        The URL's shape is checked first, before any network attempt — a URL with no scheme (or
        no host) is `invalid_url`, distinct from `cannot_connect` (a well-formed URL the station
        just didn't answer).
        """
        try:
            cv.url(url)
        except vol.Invalid:
            return "invalid_url"

        session = async_get_clientsession(self.hass)
        client = GenWaveApiClient(session, url, token)

        try:
            await client.async_get_now_playing()
        except GenWaveInvalidAuth:
            return "invalid_auth"
        except GenWaveCannotConnect:
            return "cannot_connect"
        except GenWaveApiError:
            _LOGGER.exception("Unexpected error validating the GenWave connection")
            return "unknown"

        return None


def _entry_title(url: str) -> str:
    """A stable, human-readable title derived from the station URL alone — never its credentials."""
    return f"GenWave ({redact_url(url)})"
