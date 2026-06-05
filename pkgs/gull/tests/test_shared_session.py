"""Tests for shared browser session module (items 19-23).

Tests execute_browser, check_chromium_health, destroy_session with
mocked subprocess to avoid needing actual agent-browser or Chromium.

These run in the Gull test suite (pkgs/gull).
"""

from __future__ import annotations

import asyncio
from unittest.mock import AsyncMock, MagicMock, patch

import pytest


class _SubprocessMock:
    """Simulate asyncio.create_subprocess_exec result."""

    def __init__(self, stdout=b"", stderr=b"", returncode=0, delay=0):
        self._stdout = stdout
        self._stderr = stderr
        self._returncode = returncode
        self._delay = delay
        self._killed = False
        self.communicate_called = False

    @property
    def returncode(self):
        return self._returncode

    async def communicate(self):
        self.communicate_called = True
        if self._delay > 0:
            await asyncio.sleep(self._delay)
        return self._stdout, self._stderr

    def kill(self):
        self._killed = True

    async def wait(self):
        pass


@pytest.fixture
def mock_subprocess(monkeypatch):
    """Patch asyncio.create_subprocess_exec with a controllable mock."""
    factory = {"proc": _SubprocessMock()}

    async def fake_subprocess(*args, **kwargs):
        proc = factory["proc"]
        factory["last_args"] = args
        return proc

    monkeypatch.setattr(asyncio, "create_subprocess_exec", fake_subprocess)
    return factory


class TestExecuteBrowser:
    """execute_browser calls agent-browser with --cdp and --session."""

    async def test_command_includes_cdp_and_session(self, mock_subprocess):
        from app.session import execute_browser

        mock_subprocess["proc"] = _SubprocessMock(
            stdout=b"ok", stderr=b"", returncode=0,
        )

        stdout, stderr, code = await execute_browser(
            "sandbox-abc", "goto https://example.com",
        )

        args = mock_subprocess["last_args"]
        args_str = " ".join(str(a) for a in args)

        assert "--cdp" in args_str
        assert "--session" in args_str
        assert "sandbox-abc" in args_str
        assert "goto https://example.com" in args_str
        assert "agent-browser" in args[0]
        assert stdout == "ok"
        assert code == 0

    async def test_success_maps_return_code(self, mock_subprocess):
        from app.session import execute_browser

        mock_subprocess["proc"] = _SubprocessMock(
            stdout=b"done", returncode=0,
        )
        _, _, code = await execute_browser("s", "snapshot")
        assert code == 0

    async def test_failure_maps_return_code(self, mock_subprocess):
        from app.session import execute_browser

        mock_subprocess["proc"] = _SubprocessMock(
            stdout=b"", stderr=b"error", returncode=1,
        )
        _, stderr, code = await execute_browser("s", "bad_cmd")
        assert code == 1
        assert stderr == "error"


class TestExecuteBrowserTimeout:
    """Timeout handling: kill process + return error code."""

    async def test_timeout_kills_process(self, mock_subprocess):
        from app.session import execute_browser

        mock_subprocess["proc"] = _SubprocessMock(delay=5)

        stdout, stderr, code = await execute_browser(
            "s", "slow_cmd", timeout=0.1,
        )

        assert code == 124
        assert stderr == "Command timed out"
        assert mock_subprocess["proc"]._killed is True


class TestExecuteBrowserFileNotFound:
    """agent-browser binary not found."""

    async def test_binary_not_found(self, monkeypatch):
        from app.session import execute_browser

        async def raise_file_not_found(*args, **kwargs):
            raise FileNotFoundError("agent-browser not found")

        monkeypatch.setattr(asyncio, "create_subprocess_exec", raise_file_not_found)

        stdout, stderr, code = await execute_browser("s", "cmd")
        assert code == 127
        assert "not found" in stderr


class TestCheckChromiumHealth:
    """check_chromium_health probes CDP port."""

    def test_false_when_chromium_not_started(self):
        import app.session as sess

        sess._chromium = None
        result = asyncio.run(sess.check_chromium_health())
        assert result is False

    def test_false_when_chromium_exited(self):
        import app.session as sess

        fake = MagicMock()
        fake.returncode = 1
        sess._chromium = fake
        result = asyncio.run(sess.check_chromium_health())
        assert result is False


class TestDestroySession:
    """destroy_session is best-effort — never raises."""

    async def test_destroy_does_not_raise_on_error(self, monkeypatch):
        from app.session import destroy_session

        async def raise_error(*args, **kwargs):
            raise RuntimeError("boom")

        monkeypatch.setattr(asyncio, "create_subprocess_exec", raise_error)

        # Must not raise
        await destroy_session("sandbox-abc")
