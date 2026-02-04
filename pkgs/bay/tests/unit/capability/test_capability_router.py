"""Unit tests for CapabilityRouter._require_capability().

Tests capability validation using runtime /meta response.
Includes edge cases: adapter get_meta errors, sandbox not found, etc.
"""

from __future__ import annotations

from unittest.mock import AsyncMock, MagicMock

import pytest

from app.adapters.base import BaseAdapter, RuntimeMeta
from app.errors import CapabilityNotSupportedError
from app.router.capability import CapabilityRouter


class FakeAdapter(BaseAdapter):
    """Fake adapter for testing capability validation."""

    def __init__(self, capabilities: dict | None = None) -> None:
        self._meta = RuntimeMeta(
            name="fake",
            version="1.0.0",
            api_version="v1",
            mount_path="/workspace",
            capabilities=capabilities or {},
        )

    async def get_meta(self) -> RuntimeMeta:
        return self._meta

    async def health(self) -> bool:
        return True

    def supported_capabilities(self) -> list[str]:
        return list(self._meta.capabilities.keys())


class RaisingAdapter(BaseAdapter):
    """Adapter that raises exception on get_meta."""

    def __init__(self, error: Exception) -> None:
        self._error = error

    async def get_meta(self) -> RuntimeMeta:
        raise self._error

    async def health(self) -> bool:
        return True

    def supported_capabilities(self) -> list[str]:
        return []


class TestRequireCapability:
    """Test CapabilityRouter._require_capability() method."""

    @pytest.fixture
    def mock_sandbox_mgr(self):
        """Create mock sandbox manager."""
        return AsyncMock()

    async def test_require_capability_passes_when_present(self, mock_sandbox_mgr):
        """_require_capability should pass silently when capability exists."""
        adapter = FakeAdapter(
            capabilities={
                "python": {"operations": ["exec"]},
                "shell": {"operations": ["exec"]},
            }
        )
        router = CapabilityRouter(mock_sandbox_mgr)

        # Should not raise
        await router._require_capability(adapter, "python")
        await router._require_capability(adapter, "shell")

    async def test_require_capability_raises_when_missing(self, mock_sandbox_mgr):
        """_require_capability should raise CapabilityNotSupportedError when missing."""
        adapter = FakeAdapter(
            capabilities={
                "shell": {"operations": ["exec"]},
            }
        )
        router = CapabilityRouter(mock_sandbox_mgr)

        with pytest.raises(CapabilityNotSupportedError) as exc_info:
            await router._require_capability(adapter, "python")

        error = exc_info.value
        assert error.details["capability"] == "python"
        assert "shell" in error.details["available"]
        assert "python" not in error.details["available"]

    async def test_require_capability_error_message(self, mock_sandbox_mgr):
        """Error should contain meaningful message."""
        adapter = FakeAdapter(
            capabilities={
                "filesystem": {"operations": ["read", "write"]},
            }
        )
        router = CapabilityRouter(mock_sandbox_mgr)

        with pytest.raises(CapabilityNotSupportedError) as exc_info:
            await router._require_capability(adapter, "terminal")

        error = exc_info.value
        assert "terminal" in str(error)
        assert error.message == "Runtime does not support capability: terminal"

    async def test_require_capability_with_empty_capabilities(self, mock_sandbox_mgr):
        """Should raise when runtime reports no capabilities."""
        adapter = FakeAdapter(capabilities={})
        router = CapabilityRouter(mock_sandbox_mgr)

        with pytest.raises(CapabilityNotSupportedError) as exc_info:
            await router._require_capability(adapter, "python")

        error = exc_info.value
        assert error.details["available"] == []

    async def test_require_filesystem_capability(self, mock_sandbox_mgr):
        """Test filesystem capability validation."""
        adapter = FakeAdapter(
            capabilities={
                "filesystem": {
                    "operations": ["create", "read", "write", "delete", "list"],
                },
            }
        )
        router = CapabilityRouter(mock_sandbox_mgr)

        # Should pass
        await router._require_capability(adapter, "filesystem")

        # python should fail
        with pytest.raises(CapabilityNotSupportedError):
            await router._require_capability(adapter, "python")

    async def test_require_capability_meta_error_propagates(self, mock_sandbox_mgr):
        """_require_capability should propagate get_meta exceptions.

        When adapter.get_meta() raises an exception (e.g., network error),
        _require_capability should let it propagate to the caller.
        """
        adapter = RaisingAdapter(RuntimeError("Network error"))
        router = CapabilityRouter(mock_sandbox_mgr)

        with pytest.raises(RuntimeError) as exc_info:
            await router._require_capability(adapter, "python")

        assert "Network error" in str(exc_info.value)

    async def test_require_capability_meta_connection_error(self, mock_sandbox_mgr):
        """_require_capability should propagate connection errors."""
        adapter = RaisingAdapter(ConnectionError("Connection refused"))
        router = CapabilityRouter(mock_sandbox_mgr)

        with pytest.raises(ConnectionError) as exc_info:
            await router._require_capability(adapter, "shell")

        assert "Connection refused" in str(exc_info.value)


class TestCapabilityRouterGetAdapter:
    """Test CapabilityRouter._get_adapter() method."""

    @pytest.fixture
    def mock_sandbox_mgr(self):
        """Create mock sandbox manager."""
        return AsyncMock()

    @pytest.fixture
    def adapter_pool(self):
        from app.router.capability.adapter_pool import AdapterPool

        return AdapterPool(max_size=16, ttl_seconds=60.0)

    def test_get_adapter_no_endpoint_raises(self, mock_sandbox_mgr, adapter_pool):
        """_get_adapter should raise when session has no endpoint."""
        from app.errors import SessionNotReadyError
        from app.models.session import Session

        router = CapabilityRouter(mock_sandbox_mgr, adapter_pool=adapter_pool)

        session = MagicMock(spec=Session)
        session.endpoint = None
        session.sandbox_id = "sandbox-123"

        with pytest.raises(SessionNotReadyError) as exc_info:
            router._get_adapter(session)

        assert "no endpoint" in exc_info.value.message.lower()

    def test_get_adapter_unknown_runtime_type_raises(self, mock_sandbox_mgr, adapter_pool):
        """_get_adapter should raise for unknown runtime types."""
        from app.models.session import Session

        router = CapabilityRouter(mock_sandbox_mgr, adapter_pool=adapter_pool)

        session = MagicMock(spec=Session)
        session.endpoint = "http://localhost:8123"
        session.runtime_type = "unknown_runtime"

        with pytest.raises(ValueError) as exc_info:
            router._get_adapter(session)

        assert "Unknown runtime type" in str(exc_info.value)

    def test_get_adapter_caches_by_endpoint(self, mock_sandbox_mgr, adapter_pool):
        """_get_adapter should cache adapters by endpoint (pool hit)."""
        from app.models.session import Session

        router = CapabilityRouter(mock_sandbox_mgr, adapter_pool=adapter_pool)

        session = MagicMock(spec=Session)
        session.endpoint = "http://localhost:8123"
        session.runtime_type = "ship"

        adapter1 = router._get_adapter(session)
        adapter2 = router._get_adapter(session)

        assert adapter1 is adapter2

    def test_get_adapter_reuses_across_router_instances(self, mock_sandbox_mgr, adapter_pool):
        """Adapters should be reused across CapabilityRouter instances."""
        from app.models.session import Session

        router1 = CapabilityRouter(mock_sandbox_mgr, adapter_pool=adapter_pool)
        router2 = CapabilityRouter(mock_sandbox_mgr, adapter_pool=adapter_pool)

        session = MagicMock(spec=Session)
        session.endpoint = "http://localhost:8123"
        session.runtime_type = "ship"

        adapter1 = router1._get_adapter(session)
        adapter2 = router2._get_adapter(session)

        assert adapter1 is adapter2

    def test_get_adapter_pool_eviction_by_capacity(self, mock_sandbox_mgr):
        """Pool should evict least-recently-used adapters when over capacity."""
        from app.models.session import Session
        from app.router.capability.adapter_pool import AdapterPool

        pool = AdapterPool(max_size=1, ttl_seconds=60.0)
        router = CapabilityRouter(mock_sandbox_mgr, adapter_pool=pool)

        session1 = MagicMock(spec=Session)
        session1.endpoint = "http://localhost:8123"
        session1.runtime_type = "ship"

        session2 = MagicMock(spec=Session)
        session2.endpoint = "http://localhost:9000"
        session2.runtime_type = "ship"

        adapter1 = router._get_adapter(session1)
        _adapter2 = router._get_adapter(session2)

        adapter1_new = router._get_adapter(session1)
        assert adapter1_new is not adapter1

    def test_get_adapter_pool_eviction_by_ttl(self, mock_sandbox_mgr):
        """Pool should recreate adapters after TTL expiry."""
        from app.models.session import Session
        from app.router.capability.adapter_pool import AdapterPool

        now = {"t": 0.0}
        pool = AdapterPool(max_size=8, ttl_seconds=1.0, now=lambda: now["t"])
        router = CapabilityRouter(mock_sandbox_mgr, adapter_pool=pool)

        session = MagicMock(spec=Session)
        session.endpoint = "http://localhost:8123"
        session.runtime_type = "ship"

        adapter1 = router._get_adapter(session)
        now["t"] = 2.0
        adapter2 = router._get_adapter(session)

        assert adapter2 is not adapter1
