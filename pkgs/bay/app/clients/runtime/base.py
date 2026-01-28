"""Runtime client base class.

RuntimeClient is an abstraction for communicating with different runtimes.
- ShipClient: Ship runtime (Python/Shell execution)
- Future: BrowserClient, GPUClient, etc.

See: plans/bay-design.md section 3.2.4
"""

from __future__ import annotations

from abc import ABC, abstractmethod
from dataclasses import dataclass
from typing import Any


@dataclass
class RuntimeMeta:
    """Runtime metadata from GET /meta."""

    name: str  # e.g., "ship"
    version: str
    api_version: str
    mount_path: str  # e.g., "/workspace"
    capabilities: dict[str, Any]  # capability -> operations


@dataclass
class ExecutionResult:
    """Result of code/command execution."""

    success: bool
    output: str
    error: str | None = None
    exit_code: int | None = None
    data: dict[str, Any] | None = None


class RuntimeClient(ABC):
    """Abstract runtime client interface."""

    @abstractmethod
    async def get_meta(self) -> RuntimeMeta:
        """Get runtime metadata for handshake validation."""
        ...

    @abstractmethod
    async def health(self) -> dict[str, Any]:
        """Check runtime health."""
        ...

    # Filesystem operations

    @abstractmethod
    async def read_file(self, path: str) -> str:
        """Read file content."""
        ...

    @abstractmethod
    async def write_file(self, path: str, content: str) -> None:
        """Write file content."""
        ...

    @abstractmethod
    async def list_files(self, path: str) -> list[dict[str, Any]]:
        """List directory contents."""
        ...

    @abstractmethod
    async def delete_file(self, path: str) -> None:
        """Delete file or directory."""
        ...

    # Execution operations

    @abstractmethod
    async def exec_shell(
        self,
        command: str,
        *,
        timeout: int = 30,
        cwd: str | None = None,
    ) -> ExecutionResult:
        """Execute shell command."""
        ...

    @abstractmethod
    async def exec_python(
        self,
        code: str,
        *,
        timeout: int = 30,
    ) -> ExecutionResult:
        """Execute Python code."""
        ...
