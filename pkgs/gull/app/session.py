"""Shared browser session management.

In shared mode (GULL_MODE=shared), Gull runs a single headless Chromium
process and executes agent-browser commands for multiple sandboxes via
the --cdp (connect to external Chromium) and --session (per-sandbox
daemon isolation) flags.

agent-browser's built-in daemon-per-session model provides:
- Tab isolation between sandboxes (separate daemon = separate tab pool)
- Cookie / localStorage isolation
- Idle daemon auto-shutdown (AGENT_BROWSER_IDLE_TIMEOUT_MS)
- State auto-save/restore across daemon restarts

Gull's responsibility is limited to:
1. Keep Chromium alive (the shared browser process)
2. Route each sandbox's commands through agent-browser
3. Health-check that Chromium is responsive
"""

from __future__ import annotations

import asyncio
import logging
import os
import shlex
import shutil

logger = logging.getLogger(__name__)

# — Chromium ————————————————————————————————————————————————————————

CDP_PORT = int(os.environ.get("GULL_CDP_PORT", "9222"))


def _find_chromium() -> str:
    """Locate a headless Chromium binary.

    Priority: GULL_CHROMIUM_BIN env → system PATH → Playwright cache.
    """
    # 1. Explicit override
    explicit = os.environ.get("GULL_CHROMIUM_BIN")
    if explicit:
        return explicit

    # 2. System PATH
    for name in ("google-chrome", "chromium", "chromium-browser", "chrome"):
        path = shutil.which(name)
        if path:
            return path

    # 3. Playwright cache — find chrome binary in any platform dir
    for pw_root in (
        "/root/.cache/ms-playwright",
        os.path.expanduser("~/.cache/ms-playwright"),
    ):
        if not os.path.isdir(pw_root):
            continue
        for entry in sorted(os.listdir(pw_root), reverse=True):
            if not entry.startswith("chromium"):
                continue
            chrome_dir = os.path.join(pw_root, entry)
            for sub in os.listdir(chrome_dir):
                if sub.startswith("chrome-"):
                    binary = os.path.join(chrome_dir, sub, "chrome")
                    if os.path.isfile(binary):
                        return binary

    return "chromium"  # fallback (will fail with clear error)


CHROMIUM_BIN = _find_chromium()
CHROMIUM_ARGS = [
    "--headless=new",
    "--no-sandbox",
    "--disable-gpu",
    "--disable-dev-shm-usage",
    "--disable-extensions",
    "--disable-background-networking",
    "--disable-sync",
    "--no-first-run",
    f"--remote-debugging-port={CDP_PORT}",
]

CUSTOM_ARGS = os.environ.get("GULL_CHROMIUM_ARGS")
if CUSTOM_ARGS:
    try:
        custom_args = shlex.split(CUSTOM_ARGS)
        CHROMIUM_ARGS.extend(custom_args)
        logger.info("[gull] Applied Chromium args: %s", custom_args)
    except ValueError as e:
        logger.exception(
            "[gull] Invalid GULL_CHROMIUM_ARGS: '%s' — %s. Ignoring custom args.",
            CUSTOM_ARGS,
            e
        )

logger.info("[gull] Final Chromium arguments: %s", CHROMIUM_ARGS)

_chromium: asyncio.subprocess.Process | None = None
_chromium_lock: asyncio.Lock = asyncio.Lock()


async def start_shared_chromium() -> None:
    """Launch headless Chromium for all sandboxes to share."""
    global _chromium
    async with _chromium_lock:
        if _chromium is not None and _chromium.returncode is None:
            return

        logger.info(
            "[gull] Starting shared Chromium: %s --remote-debugging-port=%s",
            CHROMIUM_BIN,
            CDP_PORT,
        )
        try:
            _chromium = await asyncio.create_subprocess_exec(
                CHROMIUM_BIN,
                *CHROMIUM_ARGS,
                stdout=asyncio.subprocess.DEVNULL,
                stderr=asyncio.subprocess.PIPE,
            )
            await asyncio.sleep(1)
            if _chromium.returncode is not None:
                stderr_data = await _chromium.stderr.read()
                raise RuntimeError(
                    f"Chromium exited immediately (code={_chromium.returncode}): "
                    f"{stderr_data.decode(errors='replace')}"
                )
            logger.info("[gull] Shared Chromium started (pid=%s)", _chromium.pid)
        except Exception:
            logger.exception("[gull] Failed to start shared Chromium")
            _chromium = None
            raise


async def stop_shared_chromium() -> None:
    """Gracefully shut down shared Chromium."""
    global _chromium
    async with _chromium_lock:
        if _chromium is None:
            return
        logger.info("[gull] Stopping shared Chromium (pid=%s)", _chromium.pid)
        try:
            _chromium.terminate()
            try:
                await asyncio.wait_for(_chromium.wait(), timeout=5)
            except asyncio.TimeoutError:
                _chromium.kill()
                await _chromium.wait()
        except Exception:
            logger.exception("[gull] Error stopping Chromium")
        finally:
            _chromium = None


async def check_chromium_health() -> bool:
    """Return True if Chromium is running and CDP is reachable."""
    if _chromium is None or _chromium.returncode is not None:
        return False
    try:
        reader, writer = await asyncio.wait_for(
            asyncio.open_connection("127.0.0.1", CDP_PORT),
            timeout=2,
        )
        request = (
            "GET /json/version HTTP/1.1\r\nHost: localhost\r\nConnection: close\r\n\r\n"
        )
        writer.write(request.encode())
        await writer.drain()
        response = await asyncio.wait_for(reader.read(4096), timeout=2)
        writer.close()
        return b"200 OK" in response and b"Browser" in response
    except Exception:
        return False


# — agent-browser command execution ——————————————————————————————————


async def execute_browser(
    sandbox_id: str,
    cmd: str,
    *,
    timeout: float = 30.0,
) -> tuple[str, str, int]:
    """Execute an agent-browser command in the sandbox's isolated daemon.

    Uses --cdp to connect to the shared Chromium and --session for
    per-sandbox tab/storage isolation.
    """
    parts = ["agent-browser", "--cdp", str(CDP_PORT), "--session", sandbox_id]
    parts.extend(shlex.split(cmd))

    return await _run_parts(parts, timeout=timeout)


async def execute_browser_raw(
    sandbox_id: str,
    argv: list[str],
    *,
    cwd: str | None = None,
    profile: str | None = None,
    timeout: float = 30.0,
) -> tuple[str, str, int]:
    """Execute an agent-browser command from pre-built argv with custom cwd/profile.

    Used by shared mode with cargo path translation — /workspace paths in
    the incoming cmd have already been rewritten to per-sandbox cargo paths
    by _translate_and_split().  We accept the pre-translated argv directly
    and inject --cdp / --session / --profile.
    """
    parts = ["agent-browser", "--cdp", str(CDP_PORT), "--session", sandbox_id]
    if profile:
        parts.extend(["--profile", profile])
    parts.extend(argv)

    return await _run_parts(parts, timeout=timeout, cwd=cwd)


async def _run_parts(
    parts: list[str],
    *,
    timeout: float = 30.0,
    cwd: str | None = None,
) -> tuple[str, str, int]:
    """Run agent-browser with given argv parts."""
    proc = None
    try:
        proc = await asyncio.create_subprocess_exec(
            *parts,
            stdout=asyncio.subprocess.PIPE,
            stderr=asyncio.subprocess.PIPE,
            cwd=cwd,
        )
        stdout_bytes, stderr_bytes = await asyncio.wait_for(
            proc.communicate(),
            timeout=timeout,
        )
        stdout = stdout_bytes.decode("utf-8", errors="replace")
        stderr = stderr_bytes.decode("utf-8", errors="replace")
        return stdout, stderr, proc.returncode or 0
    except asyncio.TimeoutError:
        if proc is not None:
            try:
                proc.kill()
                await proc.wait()
            except Exception:
                pass
        return "", "Command timed out", 124
    except FileNotFoundError:
        return "", "agent-browser binary not found", 127
    except Exception as exc:
        return "", str(exc), 1


async def destroy_session(sandbox_id: str) -> None:
    """Close an agent-browser session daemon.

    agent-browser auto-saves state before closing.
    Best-effort — failure is logged but not raised.
    """
    try:
        await execute_browser(sandbox_id, "close", timeout=5)
    except Exception:
        logger.debug("[gull] Session %s close failed (may already be gone)", sandbox_id)
