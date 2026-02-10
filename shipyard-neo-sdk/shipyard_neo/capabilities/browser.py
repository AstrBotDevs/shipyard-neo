"""Browser automation capability."""

from __future__ import annotations

from shipyard_neo.capabilities.base import BaseCapability
from shipyard_neo.types import BrowserBatchExecResult, BrowserExecResult


class BrowserCapability(BaseCapability):
    """Browser automation capability.

    Executes browser automation commands in the sandbox via the Gull runtime.
    """

    async def exec(
        self,
        cmd: str,
        *,
        timeout: int = 30,
    ) -> BrowserExecResult:
        """Execute a browser automation command in the sandbox.

        Args:
            cmd: Browser automation command to execute
            timeout: Execution timeout in seconds (1-300)

        Returns:
            BrowserExecResult with output, error, and exit code

        Raises:
            CapabilityNotSupportedError: If browser capability not in profile
            SessionNotReadyError: If session is still starting
            RequestTimeoutError: If execution times out
        """
        from shipyard_neo.types import _BrowserExecRequest

        body = _BrowserExecRequest(cmd=cmd, timeout=timeout).model_dump(exclude_none=True)

        response = await self._http.post(
            f"{self._base_path}/browser/exec",
            json=body,
            timeout=float(timeout) + 10,  # Add buffer for network overhead
        )

        return BrowserExecResult.model_validate(response)

    async def exec_batch(
        self,
        commands: list[str],
        *,
        timeout: int = 60,
        stop_on_error: bool = True,
    ) -> BrowserBatchExecResult:
        """Execute a batch of browser automation commands in the sandbox.

        Use this for deterministic sequences that don't need intermediate
        reasoning (e.g., open → fill → click → wait). For flows that need
        intermediate decisions, use individual exec() calls instead.

        Args:
            commands: List of browser commands (without 'agent-browser' prefix)
            timeout: Overall timeout in seconds for all commands (1-600)
            stop_on_error: Whether to stop on first failure

        Returns:
            BrowserBatchExecResult with per-step results and overall status

        Raises:
            CapabilityNotSupportedError: If browser capability not in profile
            SessionNotReadyError: If session is still starting
            RequestTimeoutError: If execution times out
        """
        from shipyard_neo.types import _BrowserBatchExecRequest

        body = _BrowserBatchExecRequest(
            commands=commands,
            timeout=timeout,
            stop_on_error=stop_on_error,
        ).model_dump(exclude_none=True)

        response = await self._http.post(
            f"{self._base_path}/browser/exec_batch",
            json=body,
            timeout=float(timeout) + 15,  # Add buffer for network overhead
        )

        return BrowserBatchExecResult.model_validate(response)
