"""
Shipyard Python SDK - File system component
"""

from typing import Dict, Any, Optional, TYPE_CHECKING

if TYPE_CHECKING:
    from .client import ShipyardClient


class FileSystemComponent:
    """File system operations component"""

    def __init__(self, client: "ShipyardClient", ship_id: str, session_id: str):
        self._client = client
        self._ship_id = ship_id
        self._session_id = session_id

    async def create_file(
        self, path: str, content: str = "", mode: int = 0o644
    ) -> Dict[str, Any]:
        """Create a file with the specified content"""
        payload = {"path": path, "content": content, "mode": mode}
        return await self._client._exec_operation(
            self._ship_id, "fs/create_file", payload, self._session_id
        )

    async def read_file(
        self,
        path: str,
        encoding: str = "utf-8",
        offset: Optional[int] = None,
        limit: Optional[int] = None,
    ) -> Dict[str, Any]:
        """Read file content

        Args:
            path: File path to read
            encoding: File encoding (default: utf-8)
            offset: Starting line number (1-based), None to start from beginning
            limit: Maximum number of lines to read, None to read all lines

        Returns:
            Dictionary containing file content and metadata
        """
        payload: Dict[str, Any] = {"path": path, "encoding": encoding}
        if offset is not None:
            payload["offset"] = offset
        if limit is not None:
            payload["limit"] = limit

        return await self._client._exec_operation(
            self._ship_id, "fs/read_file", payload, self._session_id
        )

    async def write_file(
        self, path: str, content: str, mode: str = "w", encoding: str = "utf-8"
    ) -> Dict[str, Any]:
        """Write content to file"""
        payload = {"path": path, "content": content, "mode": mode, "encoding": encoding}
        return await self._client._exec_operation(
            self._ship_id, "fs/write_file", payload, self._session_id
        )

    async def edit_file(
        self,
        path: str,
        old_string: str,
        new_string: str,
        replace_all: bool = False,
        encoding: str = "utf-8",
    ) -> Dict[str, Any]:
        """Edit file content by replacing strings

        Args:
            path: File path to edit
            old_string: String to be replaced
            new_string: String to replace with
            replace_all: Whether to replace all occurrences (default: False)
            encoding: File encoding (default: utf-8)

        Returns:
            Dictionary containing edit result and metadata
        """
        payload: Dict[str, Any] = {
            "path": path,
            "old_string": old_string,
            "new_string": new_string,
            "replace_all": replace_all,
            "encoding": encoding,
        }
        return await self._client._exec_operation(
            self._ship_id, "fs/edit_file", payload, self._session_id
        )

    async def delete_file(self, path: str) -> Dict[str, Any]:
        """Delete file or directory"""
        payload = {"path": path}
        return await self._client._exec_operation(
            self._ship_id, "fs/delete_file", payload, self._session_id
        )

    async def list_dir(
        self, path: str = ".", show_hidden: bool = False
    ) -> Dict[str, Any]:
        """List directory contents"""
        payload = {"path": path, "show_hidden": show_hidden}
        return await self._client._exec_operation(
            self._ship_id, "fs/list_dir", payload, self._session_id
        )
