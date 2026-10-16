"""Host cargo path resolution for Docker-backed Bay containers."""

import io
from types import SimpleNamespace
from unittest.mock import AsyncMock, Mock

import pytest

from app.drivers.docker import docker as docker_module
from app.drivers.docker.docker import DockerDriver


@pytest.fixture
def driver(monkeypatch):
    monkeypatch.setenv("HOSTNAME", "bay-container-id")
    monkeypatch.delenv("BAY_CONTAINER_NAME", raising=False)
    monkeypatch.setattr(
        docker_module,
        "get_settings",
        lambda: SimpleNamespace(
            cargo=SimpleNamespace(root_path="/var/lib/bay/cargos", host_root_path=None)
        ),
    )
    original_open = open

    def open_mountinfo(path, *args, **kwargs):
        if path == "/proc/self/mountinfo":
            return io.StringIO(
                "2187 2179 0:112 /cargos /var/lib/bay/cargos rw,relatime - tmpfs tmpfs rw\n"
            )
        return original_open(path, *args, **kwargs)

    monkeypatch.setattr("builtins.open", open_mountinfo)
    instance = DockerDriver.__new__(DockerDriver)
    instance._resolved_host_root = docker_module._UNSET
    instance._log = Mock()
    return instance


def inspect_mounts(driver, mounts):
    container = SimpleNamespace(show=AsyncMock(return_value={"Mounts": mounts}))
    client = SimpleNamespace(containers=SimpleNamespace(get=AsyncMock(return_value=container)))
    driver._get_client = AsyncMock(return_value=client)
    return client, container


@pytest.mark.asyncio
@pytest.mark.parametrize("source", ["/home/Docker/AstrBot/data/cargos", "/srv/bay-cargos"])
async def test_resolves_bind_source_from_docker_inspect(driver, source):
    client, container = inspect_mounts(
        driver,
        [
            {"Type": "bind", "Destination": "/app/config.yaml", "Source": "/host/config.yaml"},
            {"Type": "bind", "Destination": "/var/lib/bay/cargos/", "Source": source},
        ],
    )

    assert await driver._resolve_host_root() == source
    assert await driver._resolve_host_root() == source
    client.containers.get.assert_awaited_once_with("bay-container-id")
    container.show.assert_awaited_once()


@pytest.mark.asyncio
async def test_configured_container_name_works_with_custom_hostname(driver, monkeypatch):
    monkeypatch.setenv("HOSTNAME", "custom-internal-hostname")
    monkeypatch.setenv("BAY_CONTAINER_NAME", "bay")
    client, _ = inspect_mounts(
        driver,
        [
            {
                "Type": "bind",
                "Destination": "/var/lib/bay/cargos",
                "Source": "/home/Docker/AstrBot/data/cargos",
            }
        ],
    )

    assert await driver._resolve_host_root() == "/home/Docker/AstrBot/data/cargos"
    client.containers.get.assert_awaited_once_with("bay")


@pytest.mark.asyncio
async def test_configured_container_name_works_without_hostname(driver, monkeypatch):
    monkeypatch.delenv("HOSTNAME")
    monkeypatch.setenv("BAY_CONTAINER_NAME", "bay")
    client, _ = inspect_mounts(
        driver,
        [
            {
                "Type": "bind",
                "Destination": "/var/lib/bay/cargos",
                "Source": "/home/Docker/AstrBot/data/cargos",
            }
        ],
    )

    assert await driver._resolve_host_root() == "/home/Docker/AstrBot/data/cargos"
    client.containers.get.assert_awaited_once_with("bay")


@pytest.mark.asyncio
async def test_host_without_cargo_mount_uses_named_volumes(driver, monkeypatch):
    original_open = open

    def open_mountinfo(path, *args, **kwargs):
        if path == "/proc/self/mountinfo":
            return io.StringIO("12 11 0:1 / / rw,relatime - overlay overlay rw\n")
        return original_open(path, *args, **kwargs)

    monkeypatch.setattr("builtins.open", open_mountinfo)
    driver._get_client = AsyncMock()

    assert await driver._resolve_host_root() is None
    driver._get_client.assert_not_awaited()


@pytest.mark.asyncio
async def test_docker_volume_mount_uses_named_volumes(driver):
    inspect_mounts(
        driver,
        [{"Type": "volume", "Destination": "/var/lib/bay/cargos", "Source": "/docker/volumes/bay"}],
    )

    assert await driver._resolve_host_root() is None


@pytest.mark.asyncio
async def test_explicit_host_root_does_not_inspect_docker(driver, monkeypatch):
    monkeypatch.setattr(
        docker_module,
        "get_settings",
        lambda: SimpleNamespace(
            cargo=SimpleNamespace(root_path="/var/lib/bay/cargos", host_root_path="/custom/cargos")
        ),
    )
    driver._get_client = AsyncMock()

    assert await driver._resolve_host_root() == "/custom/cargos"
    driver._get_client.assert_not_awaited()


@pytest.mark.asyncio
async def test_inspect_failure_does_not_cache_named_volume_fallback(driver):
    client = SimpleNamespace(
        containers=SimpleNamespace(get=AsyncMock(side_effect=OSError("offline")))
    )
    driver._get_client = AsyncMock(return_value=client)

    with pytest.raises(RuntimeError, match="docker.sock"):
        await driver._resolve_host_root()
    client.containers.get.side_effect = None
    container = SimpleNamespace(
        show=AsyncMock(
            return_value={
                "Mounts": [
                    {
                        "Type": "bind",
                        "Destination": "/var/lib/bay/cargos",
                        "Source": "/home/Docker/AstrBot/data/cargos",
                    }
                ]
            }
        )
    )
    client.containers.get.return_value = container

    assert await driver._resolve_host_root() == "/home/Docker/AstrBot/data/cargos"


@pytest.mark.asyncio
async def test_missing_hostname_reports_configuration_error(driver, monkeypatch):
    monkeypatch.delenv("HOSTNAME")
    driver._get_client = AsyncMock()

    with pytest.raises(RuntimeError, match="BAY_CONTAINER_NAME or HOSTNAME"):
        await driver._resolve_host_root()
    driver._get_client.assert_not_awaited()


@pytest.mark.asyncio
async def test_mount_missing_from_docker_inspect_fails_closed(driver):
    inspect_mounts(driver, [])

    with pytest.raises(RuntimeError, match="mounted cargo path"):
        await driver._resolve_host_root()


@pytest.mark.asyncio
async def test_create_bind_cargo_on_local_mount(driver, tmp_path, monkeypatch):
    local_root = tmp_path / "bay-mount"
    local_root.mkdir()
    host_root = "/daemon-only/cargos"
    monkeypatch.setattr(
        docker_module,
        "get_settings",
        lambda: SimpleNamespace(
            cargo=SimpleNamespace(root_path=str(local_root), host_root_path=host_root)
        ),
    )
    name = "bay-cargo-ws-123456789abc"

    assert await driver.create_volume(name) == f"{host_root}/{name}"
    assert (local_root / name).is_dir()
    assert not (tmp_path / "daemon-only").exists()


@pytest.mark.asyncio
async def test_named_volume_reference_remains_named_after_bind_resolution(driver):
    driver._resolved_host_root = "/home/Docker/AstrBot/data/cargos"
    volume = SimpleNamespace(delete=AsyncMock())
    client = SimpleNamespace(volumes=SimpleNamespace(get=AsyncMock(return_value=volume)))
    driver._get_client = AsyncMock(return_value=client)

    assert await driver.volume_exists("bay-cargo-old")
    await driver.delete_volume("bay-cargo-old")

    assert client.volumes.get.await_count == 2
    volume.delete.assert_awaited_once()


@pytest.mark.asyncio
async def test_bind_reference_remains_bind_after_named_volume_resolution(
    driver, tmp_path, monkeypatch
):
    local_root = tmp_path / "bay-mount"
    local_root.mkdir()
    host_root = "/daemon-only/cargos"
    monkeypatch.setattr(
        docker_module,
        "get_settings",
        lambda: SimpleNamespace(
            cargo=SimpleNamespace(root_path=str(local_root), host_root_path=host_root)
        ),
    )
    driver._resolved_host_root = docker_module._UNSET
    cargo_name = "bay-cargo-ws-123456789abc"
    cargo_path = local_root / cargo_name
    cargo_path.mkdir()
    driver._get_client = AsyncMock()

    assert await driver.volume_exists(f"{host_root}/{cargo_name}")
    await driver.delete_volume(f"{host_root}/{cargo_name}")

    assert not cargo_path.exists()
    driver._get_client.assert_not_awaited()


@pytest.mark.asyncio
async def test_rejects_unrelated_absolute_path(driver, tmp_path):
    driver._resolved_host_root = None
    unrelated = tmp_path / "unrelated"
    unrelated.mkdir()

    with pytest.raises(ValueError, match="Invalid cargo bind reference"):
        await driver.delete_volume(str(unrelated))

    assert unrelated.is_dir()


@pytest.mark.asyncio
async def test_rejects_symlinked_cargo_mount(driver, tmp_path, monkeypatch):
    real_root = tmp_path / "real"
    real_root.mkdir()
    symlink_root = tmp_path / "bay-mount"
    symlink_root.symlink_to(real_root, target_is_directory=True)
    monkeypatch.setattr(
        docker_module,
        "get_settings",
        lambda: SimpleNamespace(
            cargo=SimpleNamespace(root_path=str(symlink_root), host_root_path="/daemon-only/cargos")
        ),
    )
    cargo_name = "bay-cargo-ws-123456789abc"

    with pytest.raises(ValueError, match="Cargo mount is not a directory"):
        await driver.create_volume(cargo_name)
    with pytest.raises(ValueError, match="Cargo mount is not a directory"):
        await driver.delete_volume(f"/daemon-only/cargos/{cargo_name}")

    assert list(real_root.iterdir()) == []


@pytest.mark.asyncio
async def test_rejects_symlinked_cargo_directory(driver, tmp_path, monkeypatch):
    local_root = tmp_path / "bay-mount"
    local_root.mkdir()
    unrelated = tmp_path / "unrelated"
    unrelated.mkdir()
    cargo_name = "bay-cargo-ws-123456789abc"
    (local_root / cargo_name).symlink_to(unrelated, target_is_directory=True)
    monkeypatch.setattr(
        docker_module,
        "get_settings",
        lambda: SimpleNamespace(
            cargo=SimpleNamespace(root_path=str(local_root), host_root_path="/daemon-only/cargos")
        ),
    )

    with pytest.raises(ValueError, match="symbolic link"):
        await driver.delete_volume(f"/daemon-only/cargos/{cargo_name}")
    assert unrelated.is_dir()


@pytest.mark.asyncio
async def test_delete_failure_is_reported(driver, tmp_path, monkeypatch):
    local_root = tmp_path / "bay-mount"
    local_root.mkdir()
    cargo_name = "bay-cargo-ws-123456789abc"
    (local_root / cargo_name).mkdir()
    monkeypatch.setattr(
        docker_module,
        "get_settings",
        lambda: SimpleNamespace(
            cargo=SimpleNamespace(root_path=str(local_root), host_root_path="/daemon-only/cargos")
        ),
    )
    monkeypatch.setattr(
        docker_module.shutil,
        "rmtree",
        Mock(side_effect=OSError("permission denied")),
    )

    with pytest.raises(OSError, match="permission denied"):
        await driver.delete_volume(f"/daemon-only/cargos/{cargo_name}")
    assert (local_root / cargo_name).is_dir()
