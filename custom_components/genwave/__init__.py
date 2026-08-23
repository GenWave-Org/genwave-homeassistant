"""The GenWave integration (SPEC F147.1/.2/.3, STORY-362 AC1/AC2/AC3/AC4).

Sets up the config entry's shared `GenWaveApiClient`, forwards to the `notify` and `sensor`
platforms, and registers the `genwave.announce` service (AC2: message/verbatim/ttl_seconds/voice
map 1:1 onto `POST /api/announcements` — this module adds no semantics of its own beyond the wire
mapping).
"""

from __future__ import annotations

import voluptuous as vol
from homeassistant.config_entries import ConfigEntry
from homeassistant.const import CONF_API_TOKEN, CONF_URL, Platform
from homeassistant.core import HomeAssistant, ServiceCall
from homeassistant.exceptions import ConfigEntryAuthFailed, ConfigEntryNotReady, HomeAssistantError
from homeassistant.helpers import config_validation as cv
from homeassistant.helpers.aiohttp_client import async_get_clientsession

from .api import (
    GenWaveApiClient,
    GenWaveApiProblem,
    GenWaveCannotConnect,
    GenWaveInvalidAuth,
    redact_url,
)
from .const import (
    ATTR_MESSAGE,
    ATTR_TTL_SECONDS,
    ATTR_VERBATIM,
    ATTR_VOICE,
    DEFAULT_VERBATIM,
    DOMAIN,
    MAX_TTL_SECONDS,
    MIN_TTL_SECONDS,
    SERVICE_ANNOUNCE,
)

PLATFORMS: list[Platform] = [Platform.NOTIFY, Platform.SENSOR]

type GenWaveConfigEntry = ConfigEntry[GenWaveApiClient]

SERVICE_ANNOUNCE_SCHEMA = vol.Schema(
    {
        vol.Required(ATTR_MESSAGE): cv.string,
        vol.Optional(ATTR_VERBATIM, default=DEFAULT_VERBATIM): cv.boolean,
        vol.Optional(ATTR_TTL_SECONDS): vol.All(
            vol.Coerce(int), vol.Range(min=MIN_TTL_SECONDS, max=MAX_TTL_SECONDS)
        ),
        vol.Optional(ATTR_VOICE): cv.string,
    }
)


async def async_setup_entry(hass: HomeAssistant, entry: GenWaveConfigEntry) -> bool:
    """Set up GenWave from a config entry.

    Re-runs the same now-playing read the config flow already validated with — a station that
    went unreachable or whose token died between setup runs surfaces here the same honest way
    (ConfigEntryNotReady retries; ConfigEntryAuthFailed starts reauth), rather than the notify
    platform silently coming up against a client that was never actually proven live.
    """
    session = async_get_clientsession(hass)
    client = GenWaveApiClient(session, entry.data[CONF_URL], entry.data[CONF_API_TOKEN])

    try:
        await client.async_get_now_playing()
    except GenWaveInvalidAuth as err:
        raise ConfigEntryAuthFailed("the announce token was refused") from err
    except GenWaveCannotConnect as err:
        raise ConfigEntryNotReady(f"could not reach {redact_url(entry.data[CONF_URL])}") from err
    except GenWaveApiProblem as err:
        # Every other honest server refusal (404 Admin:Enabled off, 429 the door window, 502/503 a
        # proxy mid-deploy) is a transient, non-auth state — retry-worthy, not a hard setup error.
        # GenWaveInvalidAuth (401) is the one status that means "stop retrying, get a new token",
        # and it's already handled above.
        raise ConfigEntryNotReady(
            f"GenWave at {redact_url(entry.data[CONF_URL])} returned {err.status}: {err.detail}"
        ) from err

    entry.runtime_data = client

    await hass.config_entries.async_forward_entry_setups(entry, PLATFORMS)

    _async_register_announce_service(hass)

    return True


async def async_unload_entry(hass: HomeAssistant, entry: GenWaveConfigEntry) -> bool:
    """Unload a config entry, dropping the shared service once no other entry still needs it.

    By the time this runs, `entry`'s own state has already left `LOADED` (the config entries
    machinery flips it to `UNLOAD_IN_PROGRESS` before calling here), so `async_loaded_entries`
    already excludes it — an empty result means this really was the last one, with no `<= 1`
    off-by-one against the entry currently unloading itself.
    """
    unloaded = await hass.config_entries.async_unload_platforms(entry, PLATFORMS)
    if unloaded and not hass.config_entries.async_loaded_entries(DOMAIN):
        hass.services.async_remove(DOMAIN, SERVICE_ANNOUNCE)
    return unloaded


async def async_announce_or_raise(
    hass: HomeAssistant,
    entry: GenWaveConfigEntry,
    message: str,
    *,
    verbatim: bool = False,
    ttl_seconds: int | None = None,
    voice: str | None = None,
) -> int:
    """Speak `message` through `entry`'s GenWave client, translating every failure into the one
    honest `HomeAssistantError` shape both `genwave.announce` and the `notify` platform surface.

    A dead token (401) starts a reauth flow AND still raises — the caller sees the call failed,
    never a silent no-op (AC4). Every other server-side refusal (`GenWaveApiProblem`) surfaces the
    server's own `detail` verbatim, never re-worded (this module's own header rule).
    """
    client = entry.runtime_data

    try:
        return await client.async_announce(
            message, verbatim=verbatim, ttl_seconds=ttl_seconds, voice=voice
        )
    except GenWaveInvalidAuth as err:
        entry.async_start_reauth(hass)
        raise HomeAssistantError(
            "the GenWave announce token was refused — reauthenticate the integration"
        ) from err
    except GenWaveApiProblem as err:
        raise HomeAssistantError(err.detail) from err
    except GenWaveCannotConnect as err:
        raise HomeAssistantError(f"could not reach GenWave: {err}") from err


def _async_register_announce_service(hass: HomeAssistant) -> None:
    """Register `genwave.announce` once — idempotent across however many entries load."""
    if hass.services.has_service(DOMAIN, SERVICE_ANNOUNCE):
        return

    async def _async_handle_announce(call: ServiceCall) -> None:
        entry = _resolve_single_entry(hass)
        await async_announce_or_raise(
            hass,
            entry,
            call.data[ATTR_MESSAGE],
            verbatim=call.data[ATTR_VERBATIM],
            ttl_seconds=call.data.get(ATTR_TTL_SECONDS),
            voice=call.data.get(ATTR_VOICE),
        )

    hass.services.async_register(
        DOMAIN, SERVICE_ANNOUNCE, _async_handle_announce, schema=SERVICE_ANNOUNCE_SCHEMA
    )


def _resolve_single_entry(hass: HomeAssistant) -> GenWaveConfigEntry:
    """The one GenWave station `genwave.announce` speaks through.

    manifest.json's `single_config_entry: true` is what actually guarantees there is ever at most
    one — the config flow aborts a second attempt (`single_instance_allowed`) before any input is
    even asked for. The `len(entries) > 1` branch below is therefore structurally unreachable
    through the config flow; it stays only as defense-in-depth against a future manifest edit or
    an entry created some other way, never as this integration's real guardrail.
    """
    entries: list[GenWaveConfigEntry] = hass.config_entries.async_loaded_entries(DOMAIN)
    if not entries:
        raise HomeAssistantError("no GenWave integration is configured")
    if len(entries) > 1:
        raise HomeAssistantError(
            "more than one GenWave station is configured — genwave.announce cannot pick one"
        )
    return entries[0]
