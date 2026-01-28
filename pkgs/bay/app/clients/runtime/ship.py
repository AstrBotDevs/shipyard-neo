"""Ship runtime client.

Pure HTTP client for communicating with Ship containers.
See: plans/bay-design.md section 8
"""

from __future__ import annotations

from typing import Any

import httpx
import structlog

from app.clients.runtime.base import ExecutionResult, RuntimeClient, RuntimeMeta
from app.errors import ShipError, TimeoutError

logger = structlog.get_logger()


class ShipClient(RuntimeClient):
    """HTTP client for Ship runtime."""

    def __init__(
        self,
        base_url: str,
        *,
        timeout: float = 30.0,
    ) -> None:
        self._base_url = base_url.rstrip("/")
        self._timeout = timeout
        self._log = logger.bind(client="ship", base_url=base_url)

    async def _request(
        self,
        method: str,
        path: str,
        *,
        json: dict[str, Any] | None = None,
        timeout: float | None = None,
    ) -> dict[str, Any]:
        """Make HTTP request to Ship."""
        url = f"{self._base_url}{path}"
        request_timeout = timeout or self._timeout

        try:
            async with httpx.AsyncClient() as client:
                response = await client.request(
                    method,
                    url,
                    json=json,
                    timeout=request_timeout,
                )

                if response.status_code >= 400:
                    self._log.error(
                        "ship.request_failed",
                        path=path,
                        status=response.status_code,
                        body=response.text,
                    )
                    raise ShipError(f"Ship request failed: {response.status_code}")

                return response.json()

        except httpx.TimeoutException:
            self._log.error("ship.timeout", path=path, timeout=request_timeout)
            raise TimeoutError(f"Ship request timed out: {path}")
        except httpx.RequestError as e:
            self._log.error("ship.request_error", path=path, error=str(e))
            raise ShipError(f"Ship request error: {e}")

    async def _get(self, path: str, **kwargs) -> dict[str, Any]:
        """GET request."""
        return await self._request("GET", path, **kwargs)

    async def _post(self, path: str, json: dict[str, Any] | None = None, **kwargs) -> dict[str, Any]:
        """POST request."""
        return await self._request("POST", path, json=json, **kwargs)

    # RuntimeClient implementation

    async def get_meta(self) -> RuntimeMeta:
        """Get runtime metadata for handshake validation."""
        data = await self._get("/meta")

        runtime = data.get("runtime", {})
        workspace = data.get("workspace", {})
        capabilities = data.get("capabilities", {})

        return RuntimeMeta(
            name=runtime.get("name", "ship"),
            version=runtime.get("version", "unknown"),
            api_version=runtime.get("api_version", "v1"),
            mount_path=workspace.get("mount_path", "/workspace"),
            capabilities=capabilities,
        )

    async def health(self) -> dict[str, Any]:
        """Check runtime health."""
        return await self._get("/health")

    # Filesystem operations

    async def read_file(self, path: str) -> str:
        """Read file content."""
        result = await self._post("/fs/read_file", {"path": path})
        return result.get("content", "")

    async def write_file(self, path: str, content: str) -> None:
        """Write file content."""
        await self._post("/fs/write_file", {"path": path, "content": content})

    async def list_files(self, path: str) -> list[dict[str, Any]]:
        """List directory contents."""
        result = await self._post("/fs/list", {"path": path})
        return result.get("entries", [])

    async def delete_file(self, path: str) -> None:
        """Delete file or directory."""
        await self._post("/fs/delete", {"path": path})

    # Execution operations

    async def exec_shell(
        self,
        command: str,
        *,
        timeout: int = 30,
        cwd: str | None = None,
    ) -> ExecutionResult:
        """Execute shell command."""
        payload = {
            "command": command,
            "timeout": timeout,
        }
        if cwd:
            payload["cwd"] = cwd

        result = await self._post("/shell/exec", payload, timeout=timeout + 5)

        return ExecutionResult(
            success=result.get("exit_code", -1) == 0,
            output=result.get("output", ""),
            error=result.get("error"),
            exit_code=result.get("exit_code"),
        )

    async def exec_python(
        self,
        code: str,
        *,
        timeout: int = 30,
    ) -> ExecutionResult:
        """Execute Python code."""
        result = await self._post(
            "/ipython/exec",
            {"code": code, "timeout": timeout},
            timeout=timeout + 5,
        )

        return ExecutionResult(
            success=result.get("success", False),
            output=result.get("output", ""),
            error=result.get("error"),
            data=result.get("data"),
        )
