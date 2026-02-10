"""
Pytest configuration and shared fixtures for Ship tests.
"""

import os
import sys
from pathlib import Path
from typing import Generator

import pytest


# Add the package root to Python path so 'app' module can be imported
_pkg_root = Path(__file__).parent.parent
if str(_pkg_root) not in sys.path:
    sys.path.insert(0, str(_pkg_root))

# Base URL for e2e tests - can be overridden via environment variable
DEFAULT_BASE_URL = "http://127.0.0.1:18123"


def pytest_configure(config: pytest.Config) -> None:
    """Configure pytest with custom markers."""
    # Markers are already defined in pyproject.toml, but we can add
    # additional runtime configuration here if needed
    pass


@pytest.fixture(scope="session")
def base_url() -> str:
    """
    Get the base URL for e2e tests.

    Uses SHIP_BASE_URL environment variable if set,
    otherwise defaults to http://127.0.0.1:18123
    """
    return os.environ.get("SHIP_BASE_URL", DEFAULT_BASE_URL)


@pytest.fixture(scope="session")
def ws_base_url(base_url: str) -> str:
    """
    Get the WebSocket base URL for e2e tests.

    Converts http:// to ws:// and https:// to wss://
    """
    if base_url.startswith("https://"):
        return base_url.replace("https://", "wss://", 1)
    return base_url.replace("http://", "ws://", 1)


@pytest.fixture
def test_file_content() -> str:
    """Sample file content for filesystem tests."""
    return "Hello, Ship!\nThis is a test file.\n"


@pytest.fixture
def test_python_code() -> str:
    """Sample Python code for IPython kernel tests."""
    return "result = 1 + 1\nprint(f'Result: {result}')"


@pytest.fixture
def test_shell_command() -> str:
    """Sample shell command for shell exec tests."""
    return "echo 'Hello from Ship shell'"
