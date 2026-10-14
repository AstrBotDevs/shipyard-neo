"""Docker driver container removal: graceful stop before force deletion.

Regression tests for #32: warm pool rotation removes a running container, and
without a graceful stop the removal surfaces in Docker events (and in external
monitoring) as ``container kill`` / ``die ... exitCode=137``.
"""

from __future__ import annotations

import asyncio
from types import SimpleNamespace
from unittest.mock import AsyncMock, Mock

import pytest
from aiodocker.exceptions import DockerError

from app.drivers.docker.docker import DockerDriver


class FakeContainer:
    """Records stop/delete calls and can fail either one."""

    def __init__(
        self,
        *,
        stop_error: Exception | None = None,
        delete_error: Exception | None = None,
    ) -> None:
        self.calls: list[str] = []
        self.stop_kwargs: dict | None = None
        self.delete_kwargs: dict | None = None
        self._stop_error = stop_error
        self._delete_error = delete_error

    async def stop(self, **kwargs) -> None:
        self.calls.append("stop")
        self.stop_kwargs = kwargs
        if self._stop_error is not None:
            raise self._stop_error

    async def delete(self, **kwargs) -> None:
        self.calls.append("delete")
        self.delete_kwargs = kwargs
        if self._delete_error is not None:
            raise self._delete_error


@pytest.fixture
def driver() -> DockerDriver:
    instance = DockerDriver.__new__(DockerDriver)
    instance._log = Mock()
    return instance


def attach_client(driver: DockerDriver, container: FakeContainer | DockerError) -> None:
    if isinstance(container, DockerError):
        containers = SimpleNamespace(container=Mock(side_effect=container))
    else:
        containers = SimpleNamespace(container=Mock(return_value=container))
    driver._get_client = AsyncMock(return_value=SimpleNamespace(containers=containers))


def logged(driver: DockerDriver, event: str) -> bool:
    return any(call.args[0] == event for call in driver._log.warning.call_args_list)


@pytest.mark.asyncio
async def test_destroy_stops_container_before_force_removal(driver):
    """A running container is stopped gracefully, then removed."""
    container = FakeContainer()
    attach_client(driver, container)

    await driver.destroy("bay-session-sess-abc")

    assert container.calls == ["stop", "delete"]
    assert container.stop_kwargs == {"t": 10, "timeout": 30}
    assert container.delete_kwargs == {"force": True}


@pytest.mark.asyncio
async def test_destroy_removes_already_stopped_container(driver):
    """A container that is already stopped (304) is still removed."""
    container = FakeContainer(stop_error=DockerError(304, "container already stopped"))
    attach_client(driver, container)

    await driver.destroy("bay-session-sess-abc")

    assert container.calls == ["stop", "delete"]
    assert container.delete_kwargs == {"force": True}


@pytest.mark.asyncio
async def test_destroy_removes_container_when_stop_fails(driver):
    """A failing graceful stop does not block force removal."""
    container = FakeContainer(stop_error=DockerError(500, "stop failed"))
    attach_client(driver, container)

    await driver.destroy("bay-session-sess-abc")

    assert container.calls == ["stop", "delete"]
    assert container.delete_kwargs == {"force": True}
    assert logged(driver, "docker.destroy.stop_failed")


@pytest.mark.parametrize("error", [RuntimeError("connection reset"), asyncio.TimeoutError()])
@pytest.mark.asyncio
async def test_destroy_removes_container_when_stop_raises_transport_error(driver, error):
    """Transport or timeout errors from stop must not block force removal."""
    container = FakeContainer(stop_error=error)
    attach_client(driver, container)

    await driver.destroy("bay-session-sess-abc")

    assert container.calls == ["stop", "delete"]
    assert container.delete_kwargs == {"force": True}
    assert logged(driver, "docker.destroy.stop_failed")


@pytest.mark.asyncio
async def test_destroy_tolerates_removal_race(driver):
    """A container that disappears between stop and delete is not an error."""
    container = FakeContainer(delete_error=DockerError(404, "no such container"))
    attach_client(driver, container)

    await driver.destroy("bay-session-sess-abc")

    assert container.calls == ["stop", "delete"]
    assert logged(driver, "docker.destroy.not_found")


@pytest.mark.asyncio
async def test_destroy_missing_container_is_quiet(driver):
    """A container that is already gone is logged, not raised."""
    attach_client(driver, DockerError(404, "no such container"))

    await driver.destroy("bay-session-sess-gone")

    assert logged(driver, "docker.destroy.not_found")
