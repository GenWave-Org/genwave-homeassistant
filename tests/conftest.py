"""Shared fixtures for the GenWave integration's test suite."""

from __future__ import annotations

from collections.abc import Generator

import pytest

pytest_plugins = "pytest_homeassistant_custom_component"


@pytest.fixture(autouse=True)
def auto_enable_custom_integrations(enable_custom_integrations: None) -> Generator[None]:
    """Every test in this suite loads `custom_components/genwave` as a real integration."""
    yield
