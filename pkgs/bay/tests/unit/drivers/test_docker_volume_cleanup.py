"""Volume cleanup must distinguish absent resources from failed deletion."""

from types import SimpleNamespace
from unittest.mock import AsyncMock

import pytest
from aiodocker.exceptions import DockerError

from app.drivers.docker.docker import DockerDriver


@pytest.mark.parametrize("stage", ["lookup", "delete"])
@pytest.mark.parametrize("status", [403, 404, 409, 500])
async def test_named_volume_cleanup_only_ignores_not_found(monkeypatch, stage, status):
    driver = DockerDriver()
    volume = SimpleNamespace(delete=AsyncMock())
    client = SimpleNamespace(volumes=SimpleNamespace(get=AsyncMock(return_value=volume)))
    monkeypatch.setattr(driver, "_resolve_host_root", AsyncMock(return_value=None))
    monkeypatch.setattr(driver, "_get_client", AsyncMock(return_value=client))
    operation = client.volumes.get if stage == "lookup" else volume.delete
    operation.side_effect = DockerError(status, {"message": "injected volume error"})

    if status == 404:
        await driver.delete_volume("audit-volume")
    else:
        with pytest.raises(DockerError) as exc:
            await driver.delete_volume("audit-volume")
        assert exc.value.status == status


async def test_bind_volume_cleanup_permission_failure_is_retryable(tmp_path, monkeypatch):
    driver = DockerDriver()
    workspace = tmp_path / "cargo"
    workspace.mkdir()
    (workspace / "file.txt").write_text("still present")
    monkeypatch.setattr(driver, "_resolve_host_root", AsyncMock(return_value=str(tmp_path)))

    def cannot_remove(path, **kwargs):
        if not kwargs.get("ignore_errors", False):
            raise PermissionError("workspace is not writable")

    with monkeypatch.context() as patch:
        patch.setattr("app.drivers.docker.docker.shutil.rmtree", cannot_remove)
        with pytest.raises(PermissionError):
            await driver.delete_volume(str(workspace))
    assert (workspace / "file.txt").read_text() == "still present"
    await driver.delete_volume(str(workspace))
    assert not workspace.exists()
    # Retrying after the directory is already gone remains a successful no-op.
    await driver.delete_volume(str(workspace))
