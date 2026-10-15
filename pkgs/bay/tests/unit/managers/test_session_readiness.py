"""Readiness compatibility, concurrency, and cancellation during startup."""

from __future__ import annotations

import asyncio
from collections import Counter
from contextlib import suppress
from types import SimpleNamespace
from unittest.mock import AsyncMock

import httpx
import pytest

import app.managers.session.session as session_module
from app.drivers.base import MultiContainerInfo
from app.errors import SessionNotReadyError
from app.managers.session import SessionManager
from tests.fakes import FakeDriver


def container(name: str, runtime_type: str = "ship") -> MultiContainerInfo:
    return MultiContainerInfo(
        name=name,
        container_id=f"container-{name}",
        endpoint=f"http://{name}:8123/",
        runtime_type=runtime_type,
    )


@pytest.fixture
def manager() -> SessionManager:
    return SessionManager(FakeDriver(), AsyncMock())


@pytest.mark.parametrize("multi", [False, True])
@pytest.mark.parametrize(
    ("runtime_type", "status", "body", "ready"),
    [
        ("ship", 200, b'{"status":"healthy"}', True),
        ("ship", 200, b'{"browser_ready":false}', True),
        ("gull", 200, b'{"browser_ready":false}', True),
        ("browser", 200, b'{"browser_ready":true}', True),
        ("browser", 200, b'{"browser_ready":false}', False),
        ("browser", 200, b'{"browser_ready":null}', False),
        ("browser", 200, b"{}", True),
        ("browser", 200, b"not json", True),
        ("browser", 200, b"[]", True),
        ("ship", 503, b"{}", False),
        ("browser", 503, b'{"browser_ready":true}', False),
    ],
)
async def test_readiness_preserves_runtime_response_contract(
    manager, monkeypatch, multi, runtime_type, status, body, ready
):
    """Keep existing readiness rules, including legacy runtime responses."""
    requests = []

    def health(request):
        requests.append(request)
        return httpx.Response(status, content=body)

    async with httpx.AsyncClient(transport=httpx.MockTransport(health)) as client:
        monkeypatch.setattr(session_module, "http_client_manager", SimpleNamespace(client=client))
        ci = container("runtime", runtime_type)
        if multi:
            wait = manager._wait_for_multi_ready(
                [ci], session_id="session", sandbox_id="sandbox", max_wait_seconds=0
            )
        else:
            wait = manager._wait_for_ready(
                ci.endpoint,
                runtime_type=runtime_type,
                session_id="session",
                sandbox_id="sandbox",
                max_wait_seconds=0,
            )
        if ready:
            await wait
        else:
            with pytest.raises(SessionNotReadyError) as caught:
                await wait
            assert caught.value.status_code == 503
            assert caught.value.details == {"sandbox_id": "sandbox", "retry_after_ms": 1000}

    assert len(requests) == 1
    assert str(requests[0].url) == "http://runtime:8123/health"
    assert set(requests[0].extensions["timeout"].values()) == {2.0}


async def test_multi_readiness_overlaps_probes_without_returning_early(manager, monkeypatch):
    entered = set()
    both_started = asyncio.Event()
    release = asyncio.Event()

    async def health(request):
        entered.add(request.url.host)
        if len(entered) == 2:
            both_started.set()
        await release.wait()
        return httpx.Response(200)

    async with httpx.AsyncClient(transport=httpx.MockTransport(health)) as client:
        monkeypatch.setattr(session_module, "http_client_manager", SimpleNamespace(client=client))
        task = asyncio.create_task(
            manager._wait_for_multi_ready(
                [container("first"), container("second")],
                session_id="session",
                sandbox_id="sandbox",
            )
        )
        try:
            # A serial implementation cannot start the second probe while the
            # first is still waiting. Avoid wall-clock performance assertions.
            await asyncio.wait_for(both_started.wait(), 1)
            assert not task.done()
            release.set()
            await task
        finally:
            task.cancel()
            with suppress(asyncio.CancelledError):
                await task


async def test_retries_only_pending_containers_after_transport_error(manager, monkeypatch):
    calls = Counter()

    def health(request):
        name = request.url.host
        calls[name] += 1
        if name == "slow" and calls[name] == 1:
            raise httpx.ReadTimeout("not listening yet", request=request)
        if name == "slow" and calls[name] == 2:
            return httpx.Response(200, json={"browser_ready": False})
        return httpx.Response(200, json={"browser_ready": True})

    async with httpx.AsyncClient(transport=httpx.MockTransport(health)) as client:
        monkeypatch.setattr(session_module, "http_client_manager", SimpleNamespace(client=client))
        await manager._wait_for_multi_ready(
            [container("ready"), container("slow", "browser")],
            session_id="session",
            sandbox_id="sandbox",
            initial_interval=0,
            max_interval=0,
        )
    assert calls == {"ready": 1, "slow": 3}


async def test_timeout_preserves_pending_order_and_error_details(manager, monkeypatch):
    def health(request):
        return httpx.Response(200 if request.url.host == "ready" else 503)

    missing = container("missing")
    missing.endpoint = None
    async with httpx.AsyncClient(transport=httpx.MockTransport(health)) as client:
        monkeypatch.setattr(session_module, "http_client_manager", SimpleNamespace(client=client))
        with pytest.raises(SessionNotReadyError) as caught:
            await manager._wait_for_multi_ready(
                [container("second"), container("ready"), missing, container("first")],
                session_id="session",
                sandbox_id="sandbox",
                max_wait_seconds=0,
            )
    assert caught.value.to_dict() == {
        "error": {
            "code": "session_not_ready",
            "message": "Containers not ready: ['second', 'missing', 'first']",
            "details": {"sandbox_id": "sandbox", "retry_after_ms": 1000},
        }
    }


async def test_error_waits_for_other_probes_and_preserves_original_exception(manager, monkeypatch):
    second_started = asyncio.Event()
    release = asyncio.Event()
    finished = []
    original = ValueError("first failure")

    async def health(request):
        if request.url.host == "first":
            raise original
        second_started.set()
        await release.wait()
        finished.append("second")
        raise RuntimeError("second failure")

    async with httpx.AsyncClient(transport=httpx.MockTransport(health)) as client:
        monkeypatch.setattr(session_module, "http_client_manager", SimpleNamespace(client=client))
        task = asyncio.create_task(
            manager._wait_for_multi_ready(
                [container("first"), container("second")],
                session_id="session",
                sandbox_id="sandbox",
            )
        )
        try:
            await asyncio.wait_for(second_started.wait(), 1)
            assert not task.done()
            release.set()
            with pytest.raises(ValueError) as caught:
                await task
            assert caught.value is original
            assert finished == ["second"]
        finally:
            if not task.done():
                task.cancel()
                with suppress(asyncio.CancelledError):
                    await task


async def test_cancellation_leaves_no_background_probes(manager, monkeypatch):
    entered = set()
    finished = set()
    both_started = asyncio.Event()

    async def health(request):
        name = request.url.host
        entered.add(name)
        if len(entered) == 2:
            both_started.set()
        try:
            await asyncio.Event().wait()
        finally:
            finished.add(name)

    async with httpx.AsyncClient(transport=httpx.MockTransport(health)) as client:
        monkeypatch.setattr(session_module, "http_client_manager", SimpleNamespace(client=client))
        task = asyncio.create_task(
            manager._wait_for_multi_ready(
                [container("first"), container("second")],
                session_id="session",
                sandbox_id="sandbox",
            )
        )
        try:
            await asyncio.wait_for(both_started.wait(), 1)
        finally:
            task.cancel()
            with pytest.raises(asyncio.CancelledError):
                await task
        assert finished == {"first", "second"}


async def test_fallback_client_is_closed_without_shared_pool(manager, monkeypatch):
    client = AsyncMock()
    client.get.return_value = httpx.Response(200)
    client.__aenter__.return_value = client

    def factory(**kwargs):
        assert kwargs == {"trust_env": False}
        return client

    monkeypatch.setattr(session_module.httpx, "AsyncClient", factory)

    assert await manager._probe_runtime_ready(None, "http://runtime/", "ship")
    client.get.assert_awaited_once_with("http://runtime/health", timeout=2.0)
    client.__aexit__.assert_awaited_once()
