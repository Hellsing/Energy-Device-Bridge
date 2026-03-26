"""Shared pytest fixtures for Energy Device Bridge tests."""

from __future__ import annotations

import os

import pytest
import pytest_socket

pytest_plugins = "pytest_homeassistant_custom_component"

if os.name == "nt":
    # pytest-homeassistant-custom-component disables non-unix sockets per test.
    # On Windows, asyncio event loop internals require regular sockets at creation time.
    pytest_socket.disable_socket = lambda *args, **kwargs: None


@pytest.fixture(autouse=True)
def _auto_enable_custom_integrations(enable_custom_integrations: None) -> None:
    """Allow loading custom_components from this repository."""
