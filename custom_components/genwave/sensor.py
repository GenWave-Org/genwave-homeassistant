"""The `sensor` platform for GenWave (STORY-362 AC3, SPEC F147.3) — a single now-playing sensor
backed by a `DataUpdateCoordinator` (`coordinator.py`) polling the same now-playing read the
config flow and `async_setup_entry` already validate with. Deliberately not a `media_player`:
GenWave has no transport controls to expose (play/pause/skip), only a read — out of scope here.
"""

from __future__ import annotations

from homeassistant.components.sensor import SensorEntity
from homeassistant.const import STATE_IDLE
from homeassistant.core import HomeAssistant
from homeassistant.helpers.entity_platform import AddConfigEntryEntitiesCallback
from homeassistant.helpers.update_coordinator import CoordinatorEntity

from . import GenWaveConfigEntry
from .coordinator import GenWaveNowPlayingCoordinator

ATTR_ARTIST = "artist"
ATTR_DJ_NAME = "dj_name"


async def async_setup_entry(
    hass: HomeAssistant,
    entry: GenWaveConfigEntry,
    async_add_entities: AddConfigEntryEntitiesCallback,
) -> None:
    """Set up the single GenWave now-playing sensor for this config entry.

    Deliberately `async_refresh()`, not `async_config_entry_first_refresh()`: the latter raises
    `ConfigEntryNotReady`/`ConfigEntryAuthFailed` on failure, which HA's own forwarded-platform
    setup explicitly rejects (a platform's `async_setup_entry` must never raise either — only the
    config entry's own `async_setup_entry`, which already ran its live validation read before
    forwarding here, may). `async_refresh()` never raises: a failed first read just starts the
    entity unavailable (the standard `CoordinatorEntity` contract) and, for a dead token, starts
    reauth directly — the poll path's own AC4 door, entirely independent of setup's.
    """
    coordinator = GenWaveNowPlayingCoordinator(hass, entry, entry.runtime_data)
    await coordinator.async_refresh()

    async_add_entities([GenWaveNowPlayingSensor(coordinator, entry)])


class GenWaveNowPlayingSensor(CoordinatorEntity[GenWaveNowPlayingCoordinator], SensorEntity):
    """What's airing right now: state is the track title, or `idle` when the station is quiet
    (standby/jingle/no track metadata); `artist`/`dj_name` ride along as attributes, mirroring how
    HA's own media-player-adjacent sensors shape a "what's playing" read as one entity rather than
    three. Goes unavailable on a coordinator error — the standard `CoordinatorEntity` contract,
    inherited unmodified.
    """

    _attr_has_entity_name = True
    _attr_translation_key = "now_playing"

    def __init__(self, coordinator: GenWaveNowPlayingCoordinator, entry: GenWaveConfigEntry) -> None:
        """Initialize the sensor with a unique_id stable off the entry, not the mutable station URL."""
        super().__init__(coordinator)
        self._attr_unique_id = f"{entry.entry_id}_now_playing"

    @property
    def native_value(self) -> str:
        """The track title, or `idle` while nothing is airing."""
        return self.coordinator.data.title or STATE_IDLE

    @property
    def extra_state_attributes(self) -> dict[str, str | None]:
        """`artist`/`dj_name` alongside the state — `None` for either while idle."""
        now_playing = self.coordinator.data
        return {ATTR_ARTIST: now_playing.artist, ATTR_DJ_NAME: now_playing.dj_name}
