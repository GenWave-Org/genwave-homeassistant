"""Polls the now-playing read on a fixed interval for the `sensor` platform (STORY-362 AC3,
SPEC F147.3) — the same `GenWaveApiClient.async_get_now_playing()` the config flow and
`async_setup_entry` already validate with (`api.py`'s own header rule: no parallel read path).
This module adds only a schedule and an error mapping onto the coordinator's own vocabulary.
"""

from __future__ import annotations

import logging
from datetime import timedelta
from typing import TYPE_CHECKING

from homeassistant.core import HomeAssistant
from homeassistant.exceptions import ConfigEntryAuthFailed
from homeassistant.helpers.update_coordinator import DataUpdateCoordinator, UpdateFailed

from .api import GenWaveApiClient, GenWaveApiProblem, GenWaveCannotConnect, GenWaveInvalidAuth, NowPlaying
from .const import DOMAIN, NOW_PLAYING_POLL_INTERVAL_SECONDS

if TYPE_CHECKING:
    from . import GenWaveConfigEntry

_LOGGER = logging.getLogger(__name__)

_UPDATE_INTERVAL = timedelta(seconds=NOW_PLAYING_POLL_INTERVAL_SECONDS)


class GenWaveNowPlayingCoordinator(DataUpdateCoordinator[NowPlaying]):
    """Polls `GET /api/announcements/now-playing` for the `sensor` platform.

    Error mapping is deliberate and narrow, and distinct from `async_setup_entry`'s own one-time
    validation read:

    - `GenWaveApiProblem`/`GenWaveCannotConnect` (both transient, retry-worthy states) become
      `UpdateFailed` — the entity goes unavailable (the standard `CoordinatorEntity` contract) and
      the coordinator's own schedule retries on the next tick. Never `ConfigEntryNotReady` — that
      arm belongs to `async_setup_entry`'s setup-time read alone, not the poll path.
    - `GenWaveInvalidAuth` becomes `ConfigEntryAuthFailed`, which the coordinator machinery turns
      into the same reauth flow `config_flow.async_step_reauth` already serves — AC4's third door,
      alongside the config-flow validation read and `genwave.announce`.
    """

    config_entry: GenWaveConfigEntry

    def __init__(
        self, hass: HomeAssistant, config_entry: GenWaveConfigEntry, client: GenWaveApiClient
    ) -> None:
        """Initialize the coordinator against `config_entry`'s own shared API client."""
        super().__init__(
            hass,
            _LOGGER,
            config_entry=config_entry,
            name=f"{DOMAIN} now playing",
            update_interval=_UPDATE_INTERVAL,
        )
        self.client = client

    async def _async_update_data(self) -> NowPlaying:
        """Fetch the current now-playing read, translated into the coordinator's own vocabulary."""
        try:
            return await self.client.async_get_now_playing()
        except GenWaveInvalidAuth as err:
            raise ConfigEntryAuthFailed("the announce token was refused") from err
        except GenWaveApiProblem as err:
            raise UpdateFailed(err.detail) from err
        except GenWaveCannotConnect as err:
            raise UpdateFailed(str(err)) from err
