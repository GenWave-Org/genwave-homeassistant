"""Tests for the GenWave `notify` platform (STORY-362 AC2, SPEC F147.3)."""

from __future__ import annotations

from homeassistant.components.notify import (
    ATTR_MESSAGE,
    ATTR_TITLE,
    SERVICE_SEND_MESSAGE,
)
from homeassistant.components.notify import (
    DOMAIN as NOTIFY_DOMAIN,
)
from homeassistant.const import ATTR_ENTITY_ID
from homeassistant.core import HomeAssistant
from pytest_homeassistant_custom_component.test_util.aiohttp import AiohttpClientMocker

from . import ANNOUNCEMENTS_URL, async_setup_loaded_entry


async def test_notify_send_message_delivers_through_the_same_client(
    hass: HomeAssistant, aioclient_mock: AiohttpClientMocker
) -> None:
    """notify.send_message reaches the exact same POST /api/announcements — no second write path."""
    await async_setup_loaded_entry(hass, aioclient_mock)
    aioclient_mock.post(ANNOUNCEMENTS_URL, json={"id": 7})

    entity_ids = hass.states.async_entity_ids(NOTIFY_DOMAIN)
    assert len(entity_ids) == 1

    await hass.services.async_call(
        NOTIFY_DOMAIN,
        SERVICE_SEND_MESSAGE,
        {ATTR_ENTITY_ID: entity_ids[0], ATTR_MESSAGE: "The dryer just finished"},
        blocking=True,
    )

    assert len(aioclient_mock.mock_calls) == 1
    _, url, data, _ = aioclient_mock.mock_calls[0]
    assert str(url) == ANNOUNCEMENTS_URL
    assert data == {"message": "The dryer just finished", "verbatim": False}


async def test_notify_folds_title_into_the_message(
    hass: HomeAssistant, aioclient_mock: AiohttpClientMocker
) -> None:
    """AnnouncementRequest carries no title field — a caller-supplied title is folded in, not dropped."""
    await async_setup_loaded_entry(hass, aioclient_mock)
    aioclient_mock.post(ANNOUNCEMENTS_URL, json={"id": 8})
    entity_ids = hass.states.async_entity_ids(NOTIFY_DOMAIN)

    await hass.services.async_call(
        NOTIFY_DOMAIN,
        SERVICE_SEND_MESSAGE,
        {ATTR_ENTITY_ID: entity_ids[0], ATTR_MESSAGE: "just finished", ATTR_TITLE: "Laundry"},
        blocking=True,
    )

    _, _, data, _ = aioclient_mock.mock_calls[0]
    assert data["message"] == "Laundry: just finished"
