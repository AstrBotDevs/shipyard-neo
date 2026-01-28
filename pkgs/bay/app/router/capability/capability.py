"""CapabilityRouter - routes capability requests to runtime.

Responsibilities:
- Resolve sandbox_id -> session endpoint
- Ensure session is running (ensure_running)
- Apply policies: timeout, retry, circuit-breaker, audit
- Route to appropriate RuntimeClient

See: plans/bay-design.md section 3.2.4
"""

from __future__ import annotations

from typing import Any

import structlog

from app.clients.runtime.base import ExecutionResult, RuntimeClient
from app.clients.runtime.ship import ShipClient
from app.errors import SessionNotReadyError
from app.managers.sandbox import SandboxManager
from app.models.sandbox import Sandbox
from app.models.session import Session

logger = structlog.get_logger()


class CapabilityRouter:
    """Routes capability requests to the appropriate runtime."""

    def __init__(self, sandbox_mgr: SandboxManager) -> None:
        self._sandbox_mgr = sandbox_mgr
        self._log = logger.bind(component="capability_router")
        # Cache of RuntimeClients by endpoint
        self._clients: dict[str, RuntimeClient] = {}

    async def ensure_session(self, sandbox: Sandbox) -> Session:
        """Ensure sandbox has a running session.
        
        Args:
            sandbox: Sandbox to ensure is running
            
        Returns:
            Running session
            
        Raises:
            SessionNotReadyError: If session is starting
        """
        return await self._sandbox_mgr.ensure_running(sandbox)

    def _get_client(self, session: Session) -> RuntimeClient:
        """Get or create RuntimeClient for session.
        
        Caches clients by endpoint to avoid creating new connections.
        """
        if session.endpoint is None:
            raise SessionNotReadyError(
                message="Session has no endpoint",
                sandbox_id=session.sandbox_id,
            )

        if session.endpoint not in self._clients:
            # Create client based on runtime type
            if session.runtime_type == "ship":
                self._clients[session.endpoint] = ShipClient(session.endpoint)
            else:
                raise ValueError(f"Unknown runtime type: {session.runtime_type}")

        return self._clients[session.endpoint]

    async def exec_python(
        self,
        sandbox: Sandbox,
        code: str,
        *,
        timeout: int = 30,
    ) -> ExecutionResult:
        """Execute Python code in sandbox.
        
        Args:
            sandbox: Target sandbox
            code: Python code to execute
            timeout: Execution timeout in seconds
            
        Returns:
            Execution result
        """
        session = await self.ensure_session(sandbox)
        client = self._get_client(session)

        self._log.info(
            "capability.python.exec",
            sandbox_id=sandbox.id,
            session_id=session.id,
            code_len=len(code),
        )

        return await client.exec_python(code, timeout=timeout)

    async def exec_shell(
        self,
        sandbox: Sandbox,
        command: str,
        *,
        timeout: int = 30,
        cwd: str | None = None,
    ) -> ExecutionResult:
        """Execute shell command in sandbox.
        
        Args:
            sandbox: Target sandbox
            command: Shell command to execute
            timeout: Execution timeout in seconds
            cwd: Working directory (relative to /workspace)
            
        Returns:
            Execution result
        """
        session = await self.ensure_session(sandbox)
        client = self._get_client(session)

        self._log.info(
            "capability.shell.exec",
            sandbox_id=sandbox.id,
            session_id=session.id,
            command=command[:100],
        )

        return await client.exec_shell(command, timeout=timeout, cwd=cwd)

    async def read_file(
        self,
        sandbox: Sandbox,
        path: str,
    ) -> str:
        """Read file content from sandbox.
        
        Args:
            sandbox: Target sandbox
            path: File path (relative to /workspace)
            
        Returns:
            File content
        """
        session = await self.ensure_session(sandbox)
        client = self._get_client(session)

        self._log.info(
            "capability.files.read",
            sandbox_id=sandbox.id,
            path=path,
        )

        return await client.read_file(path)

    async def write_file(
        self,
        sandbox: Sandbox,
        path: str,
        content: str,
    ) -> None:
        """Write file content to sandbox.
        
        Args:
            sandbox: Target sandbox
            path: File path (relative to /workspace)
            content: File content
        """
        session = await self.ensure_session(sandbox)
        client = self._get_client(session)

        self._log.info(
            "capability.files.write",
            sandbox_id=sandbox.id,
            path=path,
            content_len=len(content),
        )

        await client.write_file(path, content)

    async def list_files(
        self,
        sandbox: Sandbox,
        path: str,
    ) -> list[dict[str, Any]]:
        """List directory contents in sandbox.
        
        Args:
            sandbox: Target sandbox
            path: Directory path (relative to /workspace)
            
        Returns:
            List of file entries
        """
        session = await self.ensure_session(sandbox)
        client = self._get_client(session)

        self._log.info(
            "capability.files.list",
            sandbox_id=sandbox.id,
            path=path,
        )

        return await client.list_files(path)

    async def delete_file(
        self,
        sandbox: Sandbox,
        path: str,
    ) -> None:
        """Delete file or directory from sandbox.
        
        Args:
            sandbox: Target sandbox
            path: File/directory path (relative to /workspace)
        """
        session = await self.ensure_session(sandbox)
        client = self._get_client(session)

        self._log.info(
            "capability.files.delete",
            sandbox_id=sandbox.id,
            path=path,
        )

        await client.delete_file(path)
