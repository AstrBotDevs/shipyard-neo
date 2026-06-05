"""Tests for SharedGullAdapter HTTP forwarding (items 13-18)."""

from __future__ import annotations

import json

import httpx
import pytest

import app.adapters.shared_gull as sg_mod
from app.adapters.shared_gull import SharedGullAdapter
from app.errors import RequestTimeoutError


@pytest.fixture
async def mock_client(monkeypatch: pytest.MonkeyPatch):
    """Provide a shared httpx.AsyncClient with mock transport."""

    calls: list[dict] = []

    async def handler(request: httpx.Request) -> httpx.Response:
        calls.append({
            "method": request.method,
            "path": request.url.path,
            "body": json.loads(request.content.decode()) if request.content else None,
        })

        if request.url.path == "/exec":
            body = json.loads(request.content.decode()) if request.content else {}
            cmd = body.get("cmd", "")
            sandbox = body.get("sandbox_id", "")

            # Simulate agent-browser responses
            if "bad_cmd" in cmd:
                return httpx.Response(200, json={
                    "stdout": "", "stderr": "command failed", "exit_code": 1,
                })
            if "slow" in cmd:
                # Simulate timeout (let httpx handle it)
                raise httpx.TimeoutException("timed out")
            return httpx.Response(200, json={
                "stdout": f"OK: {cmd} in {sandbox}\n",
                "stderr": "",
                "exit_code": 0,
            })

        if request.url.path.startswith("/sessions/"):
            return httpx.Response(200, json={"destroyed": True})

        if request.url.path == "/health":
            return httpx.Response(200, json={"status": "healthy"})

        return httpx.Response(404, json={"error": "not found"})

    transport = httpx.MockTransport(handler)
    client = httpx.AsyncClient(transport=transport, base_url="http://shared-gull")

    monkeypatch.setattr(sg_mod, "_get_client", lambda: client)

    yield client, calls
    await client.aclose()


@pytest.fixture
def adapter():
    return SharedGullAdapter("http://shared-gull")


class TestExecBrowser:
    """exec_browser forwards to /exec with sandbox_id."""

    async def test_forward_cmd_and_sandbox_id(self, adapter, mock_client):
        client, calls = mock_client
        result = await adapter.exec_browser(
            "goto https://example.com",
            sandbox_id="sandbox-abc",
        )
        assert calls[-1]["path"] == "/exec"
        assert calls[-1]["body"]["cmd"] == "goto https://example.com"
        assert calls[-1]["body"]["sandbox_id"] == "sandbox-abc"
        assert result.success is True
        assert "sandbox-abc" in result.output

    async def test_failure_maps_to_success_false(self, adapter, mock_client):
        result = await adapter.exec_browser("bad_cmd", sandbox_id="s")
        assert result.success is False
        assert result.exit_code == 1
        assert result.error == "command failed"

    async def test_http_timeout_raises(self, adapter, mock_client):
        with pytest.raises(RequestTimeoutError):
            await adapter.exec_browser("slow_cmd", sandbox_id="s", timeout=0.5)

    async def test_non_200_status_returns_error(self, adapter, mock_client, monkeypatch):
        async def broken_handler(request):
            return httpx.Response(502, json={"error": "bad gateway"})

        transport = httpx.MockTransport(broken_handler)
        bad_client = httpx.AsyncClient(transport=transport)
        monkeypatch.setattr(sg_mod, "_get_client", lambda: bad_client)

        result = await adapter.exec_browser("goto X", sandbox_id="s")
        assert result.success is False


class TestDestroySession:
    """destroy_session forwards DELETE to /sessions/{sandbox_id}."""

    async def test_destroy_forwards_delete(self, adapter, mock_client):
        client, calls = mock_client
        await adapter.destroy_session("sandbox-abc")
        session_calls = [c for c in calls if c["path"].startswith("/sessions/")]
        assert len(session_calls) == 1
        assert session_calls[0]["method"] == "DELETE"
        assert session_calls[0]["path"] == "/sessions/sandbox-abc"

    async def test_destroy_error_does_not_raise(self, adapter, mock_client, monkeypatch):
        async def error_handler(request):
            raise RuntimeError("connection refused")

        transport = httpx.MockTransport(error_handler)
        bad_client = httpx.AsyncClient(transport=transport)
        monkeypatch.setattr(sg_mod, "_get_client", lambda: bad_client)

        # Must not raise
        await adapter.destroy_session("sandbox-abc")


class TestHealthAndMeta:
    """Health and meta endpoints."""

    async def test_health_returns_true_when_200(self, adapter, mock_client):
        healthy = await adapter.health()
        assert healthy is True

    async def test_health_returns_false_when_error(self, adapter, mock_client, monkeypatch):
        async def err_handler(request):
            return httpx.Response(503)

        transport = httpx.MockTransport(err_handler)
        bad_client = httpx.AsyncClient(transport=transport)
        monkeypatch.setattr(sg_mod, "_get_client", lambda: bad_client)

        healthy = await adapter.health()
        assert healthy is False
