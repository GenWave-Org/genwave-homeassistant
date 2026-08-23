"""The `notify` platform for GenWave (SPEC F147.3) — a `NotifyEntity` over the same
`async_announce_or_raise` helper `genwave.announce` uses (`__init__.py`); this platform adds no
second write path, and no second failure-translation path either.
"""

from __future__ import annotations

from homeassistant.components.notify import NotifyEntity
from homeassistant.core import HomeAssistant
from homeassistant.helpers.entity_platform import AddConfigEntryEntitiesCallback

from . import GenWaveConfigEntry, async_announce_or_raise


async def async_setup_entry(
    hass: HomeAssistant,
    entry: GenWaveConfigEntry,
    async_add_entities: AddConfigEntryEntitiesCallback,
) -> None:
    """Set up the single GenWave notify entity for this config entry."""
    async_add_entities([GenWaveNotifyEntity(entry)])


class GenWaveNotifyEntity(NotifyEntity):
    """`notify.send_message` delivered flavored (verbatim=False) through GenWave's own DJ.

    Deliberately the plainest possible mapping onto `POST /api/announcements`: `AnnouncementRequest`
    carries no `title` field, so a caller-supplied `title` is folded into the message text rather
    than silently dropped — a caller who set it clearly meant it heard.
    """

    def __init__(self, entry: GenWaveConfigEntry) -> None:
        """Initialize the entity against the entry's own shared API client."""
        self._entry = entry
        self._attr_name = entry.title
        self._attr_unique_id = entry.entry_id

    async def async_send_message(self, message: str, title: str | None = None) -> None:
        """Send `message` (with `title` folded in when given) through GenWave's DJ."""
        full_message = f"{title}: {message}" if title else message
        await async_announce_or_raise(self.hass, self._entry, full_message)
