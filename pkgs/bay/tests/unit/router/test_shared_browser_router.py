"""Tests for CapabilityRouter with shared Gull (items 8-12)."""

from __future__ import annotations

from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from app.adapters.base import BaseAdapter, ExecutionResult
from app.models.sandbox import Sandbox
from app.models.session import Session
from app.router.capability import CapabilityRouter


class _FakeSandboxMgr:
    """Minimal fake that counts ensure_running calls."""

    def __init__(self):
        self.ensure_calls = 0
        self._get_calls = 0

    async def ensure_running(self, sandbox):
        self.ensure_calls += 1
        return Session(
            id="sess-fake",
            sandbox_id=sandbox.id,
            profile_id="test",
            runtime_type="ship",
        )


class _SpyAdapter(BaseAdapter):
    """Adapter that records method invocations."""

    def __init__(self, *, return_result=None, raise_on=None):
        self.exec_calls: list[dict] = []
        self._return_result = return_result or ExecutionResult(
            success=True, output="ok", error="", exit_code=0,
        )
        self._raise_on = raise_on or {}

    async def exec_browser(self, cmd, sandbox_id=None, timeout=30):
        self.exec_calls.append({"cmd": cmd, "sandbox_id": sandbox_id, "timeout": timeout})
        if "exec_browser" in self._raise_on:
            raise self._raise_on["exec_browser"]
        return self._return_result

    async def exec_browser_batch(self, commands, sandbox_id=None, timeout=60, stop_on_error=True):
        self.exec_calls.append(
            {"batch": commands, "sandbox_id": sandbox_id, "timeout": timeout}
        )
        return [self._return_result for _ in commands]

    async def get_meta(self):
        return {}

    async def health(self):
        return True

    def supported_capabilities(self):
        return ["browser"]


@pytest.fixture
def sandbox() -> Sandbox:
    return Sandbox(
        id="sandbox-test",
        owner="test",
        profile_id="browser-shared",
    )


class TestSharedGullRouting:
    """When shared_gull is set, browser commands bypass ensure_session."""

    @pytest.fixture
    def fake_mgr(self):
        return _FakeSandboxMgr()

    @pytest.fixture
    def shared_gull(self):
        return _SpyAdapter()

    @pytest.fixture
    def router(self, fake_mgr, shared_gull):
        return CapabilityRouter(fake_mgr, shared_gull=shared_gull)

    async def test_exec_browser_skips_ensure_session(self, router, sandbox, fake_mgr, shared_gull):
        result = await router.exec_browser(sandbox, "goto https://example.com", timeout=15)

        # Should NOT have called ensure_running (no container needed)
        assert fake_mgr.ensure_calls == 0
        # Should have forwarded to shared gull
        assert len(shared_gull.exec_calls) == 1
        assert shared_gull.exec_calls[0]["cmd"] == "goto https://example.com"
        assert shared_gull.exec_calls[0]["sandbox_id"] == "sandbox-test"
        assert result.success is True

    async def test_exec_browser_batch_skips_ensure_session(self, router, sandbox, fake_mgr, shared_gull):
        await router.exec_browser_batch(
            sandbox, ["goto A", "snapshot"], timeout=30,
        )
        assert fake_mgr.ensure_calls == 0
        assert len(shared_gull.exec_calls) == 1  # batch is one call

    async def test_without_shared_gull_goes_through_ensure_session(self, fake_mgr, sandbox):
        """When shared_gull is not set, legacy path is used."""
        router = CapabilityRouter(fake_mgr, shared_gull=None)

        # This will fail because legacy path requires a real session/adapter.
        # We just verify it tried to call ensure_running.
        with pytest.raises(Exception):
            await router.exec_browser(sandbox, "goto X")
        assert fake_mgr.ensure_calls > 0


class TestAutoDetectFromConfig:
    """CapabilityRouter auto-creates SharedGullAdapter from config."""

    @pytest.fixture
    def fake_mgr(self):
        return _FakeSandboxMgr()

    def test_disabled_by_default(self, fake_mgr):
        """Without browser_service config, shared_gull is None."""
        router = CapabilityRouter(fake_mgr)
        assert router._shared_gull is None

    def test_enabled_creates_adapter(self, fake_mgr):
        """When browser_service.enabled=True, adapter is auto-created."""
        fake_settings = MagicMock()
        fake_settings.browser_service.enabled = True
        fake_settings.browser_service.endpoint = "http://test-gull:8115"

        with (
            patch("app.config.get_settings", return_value=fake_settings),
            patch("app.adapters.shared_gull.SharedGullAdapter") as mock_cls,
        ):
            router = CapabilityRouter(fake_mgr)
            assert router._shared_gull is not None
            mock_cls.assert_called_once_with("http://test-gull:8115")

    def test_config_import_error_graceful(self, fake_mgr):
        """If config module breaks, router still works (shared_gull = None)."""
        with patch(
            "app.config.get_settings",
            side_effect=ImportError("config not available"),
        ):
            router = CapabilityRouter(fake_mgr)
            assert router._shared_gull is None
