"""
Shipyard Python SDK - Session ship implementation
"""

import os
from typing import Dict, Any, TYPE_CHECKING
from .types import ShipInfo
from .filesystem import FileSystemComponent
from .shell import ShellComponent
from .python import PythonComponent

if TYPE_CHECKING:
    from .client import ShipyardClient


class SessionShip(ShipInfo):
    """Represents a ship session with file system, shell, and Python components"""

    def __init__(
        self, client: "ShipyardClient", ship_data: Dict[str, Any], session_id: str
    ):
        super().__init__(ship_data)
        self._client = client
        self._session_id = session_id

        # Initialize components
        self.fs = FileSystemComponent(client, self.id, session_id)
        self.shell = ShellComponent(client, self.id, session_id)
        self.python = PythonComponent(client, self.id, session_id)

    async def extend_ttl(self, ttl: int) -> Dict[str, Any]:
        """Extend the ship's TTL"""
        return await self._client.extend_ship_ttl(self.id, ttl)

    async def get_logs(self) -> str:
        """Get ship container logs"""
        return await self._client.get_ship_logs(self.id)

    async def upload_file(
        self, file_path: str, remote_file_path: str | None = None
    ) -> Dict[str, Any]:
        """Upload file to this ship session

        Args:
            file_path: Path to the local file to upload
            remote_file_path: Path where the file should be saved in the ship workspace.

        Returns:
            Dictionary with upload result information
        """
        if not remote_file_path:
            remote_file_path = os.path.basename(file_path)

        return await self._client.upload_file(
            self.id, file_path, self._session_id, remote_file_path
        )

    async def download_file(
        self, remote_file_path: str, local_file_path: str | None = None
    ) -> None:
        """Download file from this ship session

        Args:
            remote_file_path: Path to the file in the ship workspace to download
            local_file_path: Path where the file should be saved locally.
                           If not provided, uses the basename of remote_file_path.
        """
        if not local_file_path:
            local_file_path = os.path.basename(remote_file_path)

        await self._client.download_file(
            self.id, remote_file_path, self._session_id, local_file_path
        )
