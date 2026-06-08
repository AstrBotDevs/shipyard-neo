"""Shared Gull adapter.

Routes browser commands to the global Gull Service instead of
per-sandbox Gull containers.  Used when profile has browser:shared.

Unlike GullAdapter (per-sandbox), this adapter talks to a single
global Gull Service that manages multiple sandbox sessions internally
via agent-browser --cdp --session.
"""

from __future__ import annotations

from typing import Any

import httpx
import structlog

from app.adapters.base import BaseAdapter, ExecutionResult
from app.errors import RequestTimeoutError, ShipError
from app.services.http import http_client_manager

logger = structlog.get_logger()


def _get_client() -> httpx.AsyncClient | None:
    """Get shared HTTP client if available."""
    try:
        return http_client_manager.client
    except RuntimeError:
        return None


class SharedGullAdapter(BaseAdapter):
    """HTTP adapter for the shared Gull Service.

    Unlike the per-sandbox GullAdapter, this adapter does NOT manage
    container lifecycle — it just forwards browser commands to the
    global Gull Service endpoint.
    """

    SUPPORTED_CAPABILITIES = ["browser"]

    # Sentinel for get_meta — shared adapter has no meaningful endpoint
    # of its own, so we return a static meta.
    _STATIC_META: dict[str, Any] | None = None

    def __init__(
        self,
        endpoint: str,
        *,
        timeout: float = 30.0,
    ) -> None:
        self._endpoint = endpoint.rstrip("/")
        self._timeout = timeout
        self._log = logger.bind(adapter="shared_browser", endpoint=endpoint)

    async def get_meta(self) -> dict[str, Any]:
        """Return metadata for the shared Gull service."""
        if self._STATIC_META is not None:
            return self._STATIC_META

        client = _get_client()
        try:
            if client is not None:
                resp = await client.get(
                    f"{self._endpoint}/health",
                    timeout=5,
                )
                resp.raise_for_status()
                self._STATIC_META = resp.json()
            else:
                async with httpx.AsyncClient() as tmp:
                    resp = await tmp.get(f"{self._endpoint}/health", timeout=5)
                    resp.raise_for_status()
                    self._STATIC_META = resp.json()
        except Exception as exc:
            self._log.warning("shared_gull.meta_failed", error=str(exc))
            self._STATIC_META = {
                "status": "degraded",
                "version": "unknown",
                "session": "shared",
            }
        return self._STATIC_META

    async def health(self) -> bool:
        """Check if the shared Gull Service is reachable."""
        client = _get_client()
        try:
            if client is not None:
                resp = await client.get(f"{self._endpoint}/health", timeout=5)
            else:
                async with httpx.AsyncClient() as tmp:
                    resp = await tmp.get(f"{self._endpoint}/health", timeout=5)
            return resp.status_code == 200
        except Exception:
            return False

    def supported_capabilities(self) -> list[str]:
        return self.SUPPORTED_CAPABILITIES

    async def exec_browser(
        self,
        cmd: str,
        *,
        sandbox_id: str,
        cargo_id: str | None = None,
        timeout: float | None = None,
    ) -> ExecutionResult:
        """Execute a browser command via the shared Gull Service.

        Args:
            cmd: agent-browser command (without 'agent-browser' prefix).
            sandbox_id: Target sandbox for session isolation.
            cargo_id: Cargo volume ID for path translation (shared mode).
            timeout: Per-command timeout in seconds.
        """
        t = timeout or self._timeout
        client = _get_client()

        body: dict[str, Any] = {
            "cmd": cmd,
            "sandbox_id": sandbox_id,
            "timeout": int(t),
        }
        if cargo_id:
            body["cargo_id"] = cargo_id

        try:
            if client is not None:
                resp = await client.post(
                    f"{self._endpoint}/exec",
                    json=body,
                    timeout=t + 10,
                )
            else:
                async with httpx.AsyncClient() as tmp:
                    resp = await tmp.post(
                        f"{self._endpoint}/exec",
                        json=body,
                        timeout=t + 10,
                    )

            if resp.status_code != 200:
                return ExecutionResult(
                    success=False,
                    output="",
                    error=f"Gull returned HTTP {resp.status_code}",
                    exit_code=resp.status_code,
                )

            data = resp.json()
            return ExecutionResult(
                success=(data.get("exit_code", 1) == 0),
                output=data.get("stdout", ""),
                error=data.get("stderr", ""),
                exit_code=data.get("exit_code", 1),
            )
        except httpx.TimeoutException:
            raise RequestTimeoutError(
                f"Browser command timed out after {t:.0f}s"
            )
        except httpx.HTTPStatusError as exc:
            raise ShipError(
                f"Gull service returned HTTP {exc.response.status_code}"
            )
        except Exception as exc:
            raise ShipError(f"Browser execution failed: {exc}")

    async def exec_browser_batch(
        self,
        commands: list[str],
        *,
        sandbox_id: str,
        cargo_id: str | None = None,
        timeout: float | None = None,
        stop_on_error: bool = True,
    ) -> list[ExecutionResult]:
        """Execute a batch of browser commands."""
        results: list[ExecutionResult] = []
        for cmd in commands:
            result = await self.exec_browser(
                cmd, sandbox_id=sandbox_id, cargo_id=cargo_id,
                timeout=timeout,
            )
            results.append(result)
            if stop_on_error and not result.success:
                break
        return results

    async def destroy_session(self, sandbox_id: str) -> None:
        """Notify the shared Gull to close a sandbox's browser session.

        Best-effort — errors are logged but not raised.
        """
        client = _get_client()
        try:
            if client is not None:
                await client.delete(
                    f"{self._endpoint}/sessions/{sandbox_id}",
                    timeout=5,
                )
            else:
                async with httpx.AsyncClient() as tmp:
                    await tmp.delete(
                        f"{self._endpoint}/sessions/{sandbox_id}",
                        timeout=5,
                    )
        except Exception:
            self._log.debug(
                "shared_gull.destroy_failed",
                sandbox_id=sandbox_id,
                exc_info=True,
            )
