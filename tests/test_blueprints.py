"""Tests for the starter blueprint set (STORY-363 AC1/AC2, SPEC F147.4).

Blueprints can't run through pytest-homeassistant's usual `hass`-driven setup trivially. Instead:

- every blueprint file is schema-checked directly through `Blueprint` (the same class Home
  Assistant itself loads a blueprint through) - a malformed `blueprint:` block, a bad selector, or
  a `!input` used without being declared reds here;
- each `ttl_seconds` selector's bounds are pinned to `const.py`'s, the F10 lesson already applied
  to `services.yaml` in `test_services.py`;
- every shipped blueprint is fully instantiated: blueprint inputs -> a real automation -> a fired
  trigger -> the `genwave.announce` service -> the wire POST, proving AC1's "import and fill in
  entities produces a working automation" end to end for all three, not just the simplest one;
- the dinner-bell trigger is additionally probed for the three ways a plain `state` trigger
  misfires (P0-2): an attribute-only change, an unavailable-to-available reconnect, and (via the
  instantiation test above) that a real press still fires.
"""

from __future__ import annotations

from collections.abc import Callable, Coroutine
from dataclasses import dataclass
from datetime import datetime, timedelta
from pathlib import Path
from typing import Any

import pytest
from homeassistant.components.automation.config import AUTOMATION_BLUEPRINT_SCHEMA
from homeassistant.components.blueprint.models import Blueprint, BlueprintInputs
from homeassistant.components.blueprint.schemas import BLUEPRINT_SCHEMA
from homeassistant.const import STATE_UNAVAILABLE
from homeassistant.core import HomeAssistant
from homeassistant.setup import async_setup_component
from homeassistant.util import dt as dt_util
from homeassistant.util import yaml as yaml_util
from pytest_homeassistant_custom_component.common import async_fire_time_changed
from pytest_homeassistant_custom_component.test_util.aiohttp import AiohttpClientMocker

from custom_components.genwave.const import MAX_TTL_SECONDS, MIN_TTL_SECONDS

from . import ANNOUNCEMENTS_URL, async_setup_loaded_entry

BLUEPRINTS_DIR = Path(__file__).parent.parent / "blueprints" / "automation" / "genwave"
BLUEPRINT_PATHS = sorted(BLUEPRINTS_DIR.glob("*.yaml"))

# A blueprint set that only has laundry/dinner/morning recipes in it should never need this many
# files - if this fires, a stray file landed in the folder and the rest of this module's
# hardcoded "all three" assumptions (AC2's copy sweep, the TTL pin) may be silently skipping it.
assert len(BLUEPRINT_PATHS) == 3, f"expected exactly 3 blueprints, found {len(BLUEPRINT_PATHS)}"


@pytest.mark.parametrize("blueprint_path", BLUEPRINT_PATHS, ids=lambda p: p.stem)
def test_blueprint_schema_is_valid(blueprint_path: Path) -> None:
    """AC1: every shipped blueprint is a well-formed automation blueprint."""
    data = yaml_util.load_yaml_dict(str(blueprint_path))

    Blueprint(data, expected_domain="automation", schema=BLUEPRINT_SCHEMA)


@pytest.mark.parametrize("blueprint_path", BLUEPRINT_PATHS, ids=lambda p: p.stem)
def test_blueprint_ttl_selector_bounds_match_const(blueprint_path: Path) -> None:
    """F10: every blueprint's `ttl_seconds` selector bound is pinned to const.py's - the same
    lesson `test_services_yaml_ttl_bounds_match_const` already applies to services.yaml."""
    data = yaml_util.load_yaml_dict(str(blueprint_path))
    ttl_bounds = data["blueprint"]["input"]["ttl_seconds"]["selector"]["number"]

    assert ttl_bounds["min"] == MIN_TTL_SECONDS
    assert ttl_bounds["max"] == MAX_TTL_SECONDS


def test_no_blueprint_implies_seconds_scale_delivery() -> None:
    """AC2: nothing in the shipped set implies sub-minute delivery. A blueprint's own copy is free
    to *name* doorbell-class delivery only to honestly disclaim it (as every description here
    does) - the words this sweeps for are the ones that would actually promise it."""
    banned_words = ("instant", "immediate", "at once", "as soon as", "urgent")

    for blueprint_path in BLUEPRINT_PATHS:
        data = yaml_util.load_yaml_dict(str(blueprint_path))
        copy = f"{data['blueprint']['name']} {data['blueprint']['description']}".lower()
        for banned in banned_words:
            assert banned not in copy, f"{blueprint_path.name} implies urgency via {banned!r}"


def test_no_blueprint_uses_a_doorbell_or_presence_shaped_trigger() -> None:
    """AC2: every trigger platform used across the shipped set is drawn from a latency-tolerant
    allowlist. This only proves the *platform* is one of {"numeric_state", "state", "time"} - it
    says nothing about what feeds it (a `state` trigger wired to a doorbell's `binary_sensor`
    would still pass this alone); see the entity-selector sweep below for the check that actually
    catches that shape."""
    latency_tolerant_trigger_platforms = {"numeric_state", "state", "time"}

    for blueprint_path in BLUEPRINT_PATHS:
        data = yaml_util.load_yaml_dict(str(blueprint_path))
        for trigger in data["trigger"]:
            assert trigger["trigger"] in latency_tolerant_trigger_platforms, (
                f"{blueprint_path.name} uses trigger platform {trigger['trigger']!r}"
            )


def test_no_blueprint_entity_selector_offers_a_doorbell_shaped_domain() -> None:
    """AC2, the check that actually bites: no blueprint's entity selector offers a domain whose
    entities are themselves fast/urgent-shaped by nature - a doorbell or motion `binary_sensor`, a
    `person`, a `device_tracker` - the input that would let someone wire this latency-tolerant
    trigger set to a seconds-scale event no matter which trigger platform carries it."""
    doorbell_shaped_domains = {"binary_sensor", "person", "device_tracker"}

    for blueprint_path in BLUEPRINT_PATHS:
        data = yaml_util.load_yaml_dict(str(blueprint_path))
        for input_name, input_def in data["blueprint"]["input"].items():
            entity_selector = input_def.get("selector", {}).get("entity")
            if not entity_selector:
                continue
            domain = entity_selector.get("domain", [])
            domains = {domain} if isinstance(domain, str) else set(domain)
            offending = doorbell_shaped_domains & domains
            assert not offending, (
                f"{blueprint_path.name} input {input_name!r} offers doorbell-shaped domain(s) "
                f"{offending}"
            )


def _instantiate(stem: str, filled_inputs: dict[str, str]) -> dict[str, Any]:
    """Load one shipped blueprint and substitute its inputs into a real automation config - the
    same path Home Assistant's own blueprint importer takes."""
    data = yaml_util.load_yaml_dict(str(BLUEPRINTS_DIR / f"{stem}.yaml"))
    blueprint = Blueprint(data, expected_domain="automation", schema=AUTOMATION_BLUEPRINT_SCHEMA)
    blueprint_inputs = BlueprintInputs(
        blueprint,
        {"use_blueprint": {"path": f"genwave/{stem}.yaml", "input": filled_inputs}},
    )
    blueprint_inputs.validate()
    return blueprint_inputs.async_substitute()


def _next_local_time_utc(hour: int, minute: int, second: int) -> datetime:
    """The next UTC instant at which the given wall-clock time next occurs in Home Assistant's own
    configured local time zone - today if it hasn't passed yet, tomorrow otherwise."""
    local_now = dt_util.now()
    candidate = local_now.replace(hour=hour, minute=minute, second=second, microsecond=0)
    if candidate <= local_now:
        candidate += timedelta(days=1)
    return dt_util.as_utc(candidate)


def _setup_dinner_bell(hass: HomeAssistant) -> dict[str, str]:
    hass.states.async_set("input_button.dinner_bell", "2020-01-01T00:00:00+00:00")
    return {"bell": "input_button.dinner_bell"}


async def _fire_dinner_bell(hass: HomeAssistant) -> None:
    hass.states.async_set("input_button.dinner_bell", "2020-01-01T00:01:00+00:00")


def _setup_laundry_done(hass: HomeAssistant) -> dict[str, str]:
    hass.states.async_set("sensor.washer_power", "50")
    return {"power_sensor": "sensor.washer_power"}


async def _fire_laundry_done(hass: HomeAssistant) -> None:
    hass.states.async_set("sensor.washer_power", "2")
    await hass.async_block_till_done()
    # The default settle time (3 minutes) - `async_fire_time_changed` runs any scheduled callback
    # due by this mocked instant immediately, no real waiting involved.
    async_fire_time_changed(hass, dt_util.utcnow() + timedelta(minutes=3, seconds=5))


def _setup_morning_ramp(hass: HomeAssistant) -> dict[str, str]:
    return {}


async def _fire_morning_ramp(hass: HomeAssistant) -> None:
    async_fire_time_changed(hass, _next_local_time_utc(7, 0, 0))


@dataclass(frozen=True)
class _BlueprintCase:
    """One shipped blueprint's instantiation recipe: how to seed the entity(ies) it needs before
    the automation attaches, how to fire its trigger afterward, and the default message
    `genwave.announce` should receive back untouched."""

    stem: str
    setup_entities: Callable[[HomeAssistant], dict[str, str]]
    fire_trigger: Callable[[HomeAssistant], Coroutine[Any, Any, None]]
    default_message: str


BLUEPRINT_CASES = (
    _BlueprintCase("dinner-bell", _setup_dinner_bell, _fire_dinner_bell, "Dinner is ready."),
    _BlueprintCase(
        "laundry-done", _setup_laundry_done, _fire_laundry_done, "The laundry is done."
    ),
    _BlueprintCase("morning-ramp", _setup_morning_ramp, _fire_morning_ramp, "Good morning!"),
)


@pytest.mark.parametrize("case", BLUEPRINT_CASES, ids=lambda c: c.stem)
async def test_blueprint_produces_a_working_automation(
    hass: HomeAssistant, aioclient_mock: AiohttpClientMocker, case: _BlueprintCase
) -> None:
    """AC1, end to end, for every shipped blueprint: importing it with only its required entity
    inputs filled produces a real automation that, once its trigger fires, calls
    `genwave.announce` with that blueprint's own default message, verbatim, and ttl_seconds - not
    just a schema that happens to parse. A mutated `action:` in any one of the three (previously
    only dinner-bell was covered, so laundry/morning could ship a broken action silently) reds its
    own leg without touching the others.
    """
    await async_setup_loaded_entry(hass, aioclient_mock)
    aioclient_mock.post(ANNOUNCEMENTS_URL, json={"id": 1})

    filled_inputs = case.setup_entities(hass)
    automation_config = _instantiate(case.stem, filled_inputs)

    assert await async_setup_component(hass, "automation", {"automation": [automation_config]})
    await hass.async_block_till_done()

    await case.fire_trigger(hass)
    await hass.async_block_till_done()

    assert len(aioclient_mock.mock_calls) == 1
    _, _, sent, _ = aioclient_mock.mock_calls[0]
    assert sent["message"] == case.default_message
    assert sent["verbatim"] is False
    assert sent["ttlSeconds"] == 900


async def test_dinner_bell_ignores_an_attribute_only_change(
    hass: HomeAssistant, aioclient_mock: AiohttpClientMocker
) -> None:
    """P0-2 red proof: an attribute-only change on the bell entity (its `.state` string unchanged,
    only an attribute riding along) must not announce - one of the probe-proven misfires the
    trigger's `not_from`/`condition` pair now guards against.

    RED PROOF: drop the trigger's `not_from` list (and its paired `condition:` block) in a scratch
    copy of dinner-bell.yaml and this reds - a plain `state` trigger fires on any attribute change
    too, not just a `.state` transition.
    """
    await async_setup_loaded_entry(hass, aioclient_mock)
    aioclient_mock.post(ANNOUNCEMENTS_URL, json={"id": 1})

    hass.states.async_set("scene.dinner_bell", "2020-01-01T00:00:00+00:00")
    automation_config = _instantiate("dinner-bell", {"bell": "scene.dinner_bell"})
    assert await async_setup_component(hass, "automation", {"automation": [automation_config]})
    await hass.async_block_till_done()

    hass.states.async_set(
        "scene.dinner_bell", "2020-01-01T00:00:00+00:00", {"friendly_name": "Renamed"}
    )
    await hass.async_block_till_done()

    assert len(aioclient_mock.mock_calls) == 0


async def test_dinner_bell_ignores_an_unavailable_recovery(
    hass: HomeAssistant, aioclient_mock: AiohttpClientMocker
) -> None:
    """P0-2 red proof: the bell entity coming back from `unavailable` (an integration reload, an
    HA restart racing the entity's own platform) must not announce - only a genuine press or scene
    activation should.

    RED PROOF: drop the trigger's `not_from` list in a scratch copy of dinner-bell.yaml and this
    reds - a plain `state` trigger treats an unavailable-to-available reconnect the same as a real
    press.
    """
    await async_setup_loaded_entry(hass, aioclient_mock)
    aioclient_mock.post(ANNOUNCEMENTS_URL, json={"id": 1})

    hass.states.async_set("input_button.dinner_bell", "2020-01-01T00:00:00+00:00")
    automation_config = _instantiate("dinner-bell", {"bell": "input_button.dinner_bell"})
    assert await async_setup_component(hass, "automation", {"automation": [automation_config]})
    await hass.async_block_till_done()

    hass.states.async_set("input_button.dinner_bell", STATE_UNAVAILABLE)
    await hass.async_block_till_done()
    aioclient_mock.clear_requests()
    aioclient_mock.post(ANNOUNCEMENTS_URL, json={"id": 1})

    hass.states.async_set("input_button.dinner_bell", "2020-01-01T00:05:00+00:00")
    await hass.async_block_till_done()

    assert len(aioclient_mock.mock_calls) == 0
