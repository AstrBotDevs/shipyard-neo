from __future__ import annotations

import tempfile
import unittest
from pathlib import Path
from unittest.mock import AsyncMock, Mock

from app.drivers.docker.docker import DockerDriver


class TestDockerBindCargoPaths(unittest.IsolatedAsyncioTestCase):
    def setUp(self) -> None:
        self.temp_dir = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp_dir.cleanup)

        self.local_root = Path(self.temp_dir.name) / "container-cargos"
        self.driver = DockerDriver.__new__(DockerDriver)
        self.driver._cargo_root_path = self.local_root
        self.driver._log = Mock()
        self.driver._resolve_host_root = AsyncMock(
            return_value="/daemon-only/shipyard-cargos"
        )

    async def test_create_volume_uses_local_mount_and_returns_host_bind_path(
        self,
    ) -> None:
        volume = await self.driver.create_volume("bay-cargo-test")

        self.assertEqual(
            volume,
            "/daemon-only/shipyard-cargos/bay-cargo-test",
        )
        self.assertTrue((self.local_root / "bay-cargo-test").is_dir())

    async def test_volume_exists_checks_local_mount(self) -> None:
        (self.local_root / "bay-cargo-test").mkdir(parents=True)

        exists = await self.driver.volume_exists(
            "/daemon-only/shipyard-cargos/bay-cargo-test"
        )

        self.assertTrue(exists)

    async def test_delete_volume_removes_directory_from_local_mount(self) -> None:
        local_path = self.local_root / "bay-cargo-test"
        local_path.mkdir(parents=True)
        (local_path / "artifact.txt").write_text("test")

        await self.driver.delete_volume(
            "/daemon-only/shipyard-cargos/bay-cargo-test"
        )

        self.assertFalse(local_path.exists())

    async def test_delete_volume_rejects_cargo_root(self) -> None:
        sentinel = self.local_root / "keep.txt"
        self.local_root.mkdir(parents=True)
        sentinel.write_text("keep")

        with self.assertRaises(ValueError):
            await self.driver.delete_volume("/")

        self.assertTrue(sentinel.is_file())

    async def test_delete_volume_rejects_path_outside_configured_host_root(
        self,
    ) -> None:
        local_path = self.local_root / "bay-cargo-test"
        local_path.mkdir(parents=True)

        with self.assertRaises(ValueError):
            await self.driver.delete_volume("/other-root/bay-cargo-test")

        self.assertTrue(local_path.is_dir())


if __name__ == "__main__":
    unittest.main()
