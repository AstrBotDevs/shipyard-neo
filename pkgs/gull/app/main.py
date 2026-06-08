"""Gull - Browser Runtime REST Wrapper.

A thin HTTP wrapper around agent-browser CLI, providing:
- POST /exec: Execute any agent-browser command
- GET /health: Health check
- GET /meta: Runtime metadata and capabilities

Modes (via GULL_MODE env var):
- single (default): per-sandbox isolated browser (legacy).  Each Gull
  container serves exactly one sandbox; --session is fixed to SANDBOX_ID.
- shared: multi-tenant shared browser pool.  One Gull container serves
  all sandboxes; Chromium is started once on boot; agent-browser --cdp
  connects to the shared Chromium; --session is set from the request's
  sandbox_id for per-sandbox isolation.
"""

from __future__ import annotations

import asyncio
import logging
import os
import re
import shlex
import shutil
import time
import tomllib
from contextlib import asynccontextmanager
from pathlib import Path

from fastapi import FastAPI
from pydantic import BaseModel, Field

GULL_MODE = os.environ.get("GULL_MODE", "single")  # "single" | "shared"

# ── Shared-mode path translation ─────────────────────────────────
# agent-browser commands that accept file paths under /workspace.
# When running in shared mode, /workspace is translated to the
# per-sandbox cargo path so that screenshots and uploads land on the
# correct Cargo volume (not the shared Gull container's local fs).
_FILE_PATH_COMMANDS = frozenset({"screenshot", "pdf", "upload", "file_server"})

_CARGO_VOLUMES_ROOT = "/cargos"


def _translate_and_split(cmd: str, cargo_id: str) -> tuple[list[str], str, str]:
    """Translate /workspace paths and derive cwd + profile for shared mode.

    Parses the command line with shlex, then replaces every argument that
    starts with ``/workspace`` with the per-sandbox cargo path.  Only
    file-path commands (screenshot / pdf / upload / file_server) are
    touched; everything else passes through unchanged.

    Cargo directories are named ``bay-cargo-{cargo_id}`` under
    ``_CARGO_VOLUMES_ROOT`` (matching the naming convention in
    ``CargoManager._create_volume``).

    Returns:
        (argv, cwd, profile_path)
    """
    cargo_path = f"{_CARGO_VOLUMES_ROOT}/bay-cargo-{cargo_id}"
    try:
        argv = shlex.split(cmd)
    except ValueError:
        return [cmd], cargo_path, f"{cargo_path}/.browser/profile"

    if not argv or argv[0] not in _FILE_PATH_COMMANDS:
        return argv, cargo_path, f"{cargo_path}/.browser/profile"

    for i in range(len(argv)):
        arg = argv[i]
        if arg == "/workspace":
            argv[i] = cargo_path
        elif arg.startswith("/workspace/"):
            argv[i] = cargo_path + arg[len("/workspace"):]

    return argv, cargo_path, f"{cargo_path}/.browser/profile"

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [%(levelname)-5s] %(message)s",
    datefmt="%Y-%m-%d %H:%M:%S",
)
logger = logging.getLogger(__name__)


def get_version() -> str:
    """Get version from pyproject.toml (single source of truth)."""
    try:
        pyproject_path = Path(__file__).parent.parent / "pyproject.toml"
        with open(pyproject_path, "rb") as f:
            data = tomllib.load(f)
        return data.get("project", {}).get("version", "unknown")
    except Exception:
        return "unknown"


# Configuration from environment
SESSION_NAME = os.environ.get("SANDBOX_ID", os.environ.get("BAY_SANDBOX_ID", "default"))
WORKSPACE_PATH = os.environ.get("BAY_WORKSPACE_PATH", "/workspace")
# Persistent browser profile directory on shared Cargo Volume.
# agent-browser --profile automatically persists cookies, localStorage,
# IndexedDB, service workers, and cache to this directory.
BROWSER_PROFILE_DIR = os.path.join(WORKSPACE_PATH, ".browser", "profile")
GULL_VERSION = get_version()

# Browser readiness state.
# Interpreted as: "agent-browser CLI/daemon is responsive".
#
# Note: This does NOT guarantee the daemon was started with our desired
# `--profile` (see _ensure_browser_ready() for the trade-off).
_browser_ready: bool = False

# Module-global lock to prevent concurrent pre-warm races.
#
# We intentionally instantiate this at import time to avoid a check-then-set
# race when many requests hit Gull at once.
_browser_ready_lock: asyncio.Lock = asyncio.Lock()


async def _ensure_browser_ready() -> None:
    """Ensure agent-browser is ready, while avoiding noisy daemon warnings.

    Problem:
      agent-browser uses a long-lived daemon process. If we pass `--profile`
      on every command while the daemon is already running, agent-browser prints
      a warning to stderr that `--profile` is ignored.

    Strategy ("B"):
      1) Probe agent-browser without `--profile`.
         - If it is responsive, mark `_browser_ready=True` and stop injecting
           `--profile` for subsequent commands (eliminates the warning).
      2) If probe fails, start/pre-warm once with `--profile`.

    Trade-off:
      If a daemon is already running but was started without our desired profile,
      we choose to accept the existing daemon (avoid restarts) and suppress the
      warning by omitting `--profile` in subsequent commands.

      If you must guarantee the profile is applied, you would need an explicit
      close → open(with profile) restart policy (not implemented here).
    """
    global _browser_ready

    if _browser_ready:
        return

    async with _browser_ready_lock:
        if _browser_ready:
            return

        # Probe without --profile to avoid daemon warning.
        _, _, probe_code = await _run_agent_browser(
            "session list",
            session=None,
            profile=None,
            timeout=5,
        )
        if probe_code == 0:
            _browser_ready = True
            logger.info("[gull] agent-browser probe OK (daemon/CLI responsive)")
            return

        # Pre-warm: start the daemon with our desired profile.
        _, stderr, code = await _run_agent_browser(
            "open about:blank",
            session=SESSION_NAME,
            profile=BROWSER_PROFILE_DIR,
            timeout=30,
        )
        if code == 0:
            _browser_ready = True
            logger.info("[gull] Browser daemon started (profile applied)")
        else:
            logger.warning(
                "[gull] Browser pre-warm failed (will fall back to per-command profile): exit=%s stderr=%s",
                code,
                (stderr or "").strip(),
            )


class ExecRequest(BaseModel):
    """Request to execute an agent-browser command.

    In shared mode, sandbox_id is required for session isolation.
    In single mode, sandbox_id is ignored (uses global SANDBOX_ID).

    cargo_id is used in shared mode to translate /workspace paths to the
    per-sandbox Cargo volume's real filesystem path.
    """

    cmd: str = Field(
        ..., description="agent-browser command (without 'agent-browser' prefix)"
    )
    sandbox_id: str | None = Field(
        default=None, description="Sandbox ID for session isolation (shared mode)"
    )
    cargo_id: str | None = Field(
        default=None, description="Cargo volume ID for path translation (shared mode)"
    )
    timeout: int = Field(default=30, description="Timeout in seconds", ge=1, le=300)


class ExecResponse(BaseModel):
    """Response from command execution."""

    stdout: str
    stderr: str
    exit_code: int


class BatchExecRequest(BaseModel):
    """Request to execute a batch of agent-browser commands."""

    commands: list[str] = Field(
        ..., min_length=1, description="List of commands (without agent-browser prefix)"
    )
    timeout: int = Field(
        default=60, ge=1, le=600, description="Overall timeout (seconds)"
    )
    stop_on_error: bool = Field(default=True, description="Stop if a command fails")
    sandbox_id: str | None = Field(
        default=None, description="Sandbox ID for session isolation (shared mode)"
    )
    cargo_id: str | None = Field(
        default=None, description="Cargo volume ID for path translation (shared mode)"
    )


class BatchStepResult(BaseModel):
    """Result of a single step in a batch."""

    cmd: str
    stdout: str
    stderr: str
    exit_code: int
    step_index: int
    duration_ms: int


class BatchExecResponse(BaseModel):
    """Response from batch command execution."""

    results: list[BatchStepResult]
    total_steps: int
    completed_steps: int
    success: bool
    duration_ms: int


class HealthResponse(BaseModel):
    """Health check response."""

    status: str  # healthy | degraded | unhealthy
    browser_active: bool
    browser_ready: bool
    session: str
    version: str


class MetaResponse(BaseModel):
    """Runtime metadata response."""

    runtime: dict
    workspace: dict
    capabilities: dict
    built_in_skills: list[dict] = []


async def _run_agent_browser(
    cmd: str,
    *,
    timeout: float = 30.0,
    session: str | None = None,
    profile: str | None = None,
    cwd: str = WORKSPACE_PATH,
) -> tuple[str, str, int]:
    """Execute an agent-browser command via subprocess.

    Automatically injects --session (for browser isolation) and --profile
    (for persistent state on Cargo Volume) parameters.

    Args:
        cmd: Command string (without 'agent-browser' prefix)
        timeout: Timeout in seconds
        session: Session name for browser isolation
        profile: Profile directory for persistent browser state
        cwd: Working directory

    Returns:
        Tuple of (stdout, stderr, exit_code)
    """
    # Build full command with session + profile injection
    parts = ["agent-browser"]
    if session:
        parts.extend(["--session", session])
    if profile:
        parts.extend(["--profile", profile])

    # Use shlex.split to preserve quoted arguments.
    # Example: fill @e1 "hello world"
    parts.extend(shlex.split(cmd))

    try:
        process = await asyncio.create_subprocess_exec(
            *parts,
            stdout=asyncio.subprocess.PIPE,
            stderr=asyncio.subprocess.PIPE,
            cwd=cwd,
        )

        try:
            stdout, stderr = await asyncio.wait_for(
                process.communicate(),
                timeout=timeout,
            )
        except asyncio.TimeoutError:
            # Kill the process on timeout
            try:
                process.kill()
                await process.wait()
            except ProcessLookupError:
                pass
            return "", f"Command timed out after {timeout}s", -1

        return (
            stdout.decode("utf-8", errors="replace"),
            stderr.decode("utf-8", errors="replace"),
            process.returncode or 0,
        )

    except FileNotFoundError:
        return "", "agent-browser not found. Is it installed?", -1
    except Exception as e:
        return "", f"Failed to execute command: {e}", -1


# ---------------------------------------------------------------------------
# Built-in skills helpers
# ---------------------------------------------------------------------------

SKILLS_SRC_DIR = Path("/app/skills")


def _scan_built_in_skills(root: Path = SKILLS_SRC_DIR) -> list[dict]:
    """Scan /app/skills/*/SKILL.md, parse YAML frontmatter, return metadata."""
    skills: list[dict] = []
    if not root.exists():
        return skills

    for skill_dir in sorted(root.iterdir()):
        skill_md = skill_dir / "SKILL.md"
        if not skill_md.is_file():
            continue
        try:
            text = skill_md.read_text(encoding="utf-8")
            meta = _parse_frontmatter(text)
            skills.append(
                {
                    "name": meta.get("name", skill_dir.name),
                    "description": meta.get("description", ""),
                    "path": str(skill_md),
                }
            )
        except Exception as exc:  # pragma: no cover
            logger.warning("Failed to parse %s: %s", skill_md, exc)
    return skills


def _parse_frontmatter(text: str) -> dict:
    """Extract YAML frontmatter from markdown text (simple parser).

    Tolerates:
    - CRLF line endings
    - optional leading BOM
    - leading whitespace/blank lines before the opening '---'

    Note: This is intentionally a *simple* frontmatter parser (flat key/value
    pairs only), not a full YAML implementation.
    """
    match = re.match(r"^\ufeff?\s*---\s*\r?\n(.*?)\r?\n---", text, re.DOTALL)
    if not match:
        return {}
    result: dict[str, str] = {}
    for line in match.group(1).splitlines():
        if ":" in line:
            key, _, value = line.partition(":")
            result[key.strip()] = value.strip().strip("'\"")
    return result


@asynccontextmanager
async def lifespan(app: FastAPI):
    """Application lifespan manager.

    In single mode: pre-warm browser via agent-browser daemon.
    In shared mode: start shared Chromium, no per-command profile needed.
    """
    global _browser_ready

    if GULL_MODE == "shared":
        # ── Shared mode ──────────────────────────────────────────────
        from app.session import start_shared_chromium, stop_shared_chromium

        logger.info("[gull] Starting in shared mode (CDP port=9222), version=%s", GULL_VERSION)
        try:
            await start_shared_chromium()
            _browser_ready = True
            logger.info("[gull] Shared Chromium started")
        except Exception as e:
            _browser_ready = False
            logger.error("[gull] Failed to start shared Chromium: %s", e)

        yield

        logger.info("[gull] Shutting down shared mode...")
        await stop_shared_chromium()
        _browser_ready = False
        logger.info("[gull] Shared Chromium stopped.")
        return

    # ── Single mode (legacy) ────────────────────────────────────────
    os.makedirs(BROWSER_PROFILE_DIR, exist_ok=True)
    logger.info("[gull] Starting Gull v%s, session=%s", GULL_VERSION, SESSION_NAME)
    logger.info("[gull] Browser profile dir: %s", BROWSER_PROFILE_DIR)

    try:
        logger.info("[gull] Pre-warming browser (open about:blank)...")
        await _ensure_browser_ready()
        if _browser_ready:
            logger.info("[gull] Browser pre-warmed successfully")
        else:
            logger.warning(
                "[gull] Browser pre-warm did not complete (will fall back to per-command --profile)"
            )
    except Exception as e:
        logger.warning("[gull] Failed to pre-warm browser: %s", e)

    yield

    logger.info("[gull] Shutting down, closing browser...")
    await _run_agent_browser(
        "close",
        session=SESSION_NAME,
        profile=None,
        timeout=5,
    )
    _browser_ready = False
    print("[gull] Browser closed.")


app = FastAPI(
    title="Gull - Browser Runtime",
    version=GULL_VERSION,
    lifespan=lifespan,
)


@app.post("/exec", response_model=ExecResponse)
async def exec_command(request: ExecRequest) -> ExecResponse:
    """Execute an agent-browser command.

    The command is transparently passed to the agent-browser CLI with
    automatic --session injection for browser context isolation.

    In shared mode, uses sandbox_id from request for --session isolation
    connecting to the shared Chromium via --cdp.  When cargo_id is also
    provided, /workspace paths are translated to the per-sandbox Cargo
    volume so screenshots and uploads land on the correct filesystem.

    Examples:
        {"cmd": "open https://example.com"}
        {"cmd": "snapshot -i"}
        {"cmd": "screenshot /workspace/page.png"}
    """
    sandbox_id = request.sandbox_id

    if GULL_MODE == "shared" and sandbox_id:
        # ── Shared mode ────────────────────────────────────────
        if request.cargo_id:
            argv, cwd, profile_path = _translate_and_split(request.cmd, request.cargo_id)
            from app.session import execute_browser_raw

            stdout, stderr, exit_code = await execute_browser_raw(
                sandbox_id,
                argv,
                cwd=cwd,
                profile=profile_path,
                timeout=request.timeout,
            )
        else:
            from app.session import execute_browser

            stdout, stderr, exit_code = await execute_browser(
                sandbox_id,
                request.cmd,
                timeout=request.timeout,
            )
        return ExecResponse(stdout=stdout, stderr=stderr, exit_code=exit_code)

    # ── Single mode (legacy) ────────────────────────────────────────
    await _ensure_browser_ready()
    profile = None if _browser_ready else BROWSER_PROFILE_DIR
    stdout, stderr, exit_code = await _run_agent_browser(
        request.cmd,
        session=SESSION_NAME,
        profile=profile,
        timeout=request.timeout,
    )
    return ExecResponse(stdout=stdout, stderr=stderr, exit_code=exit_code)


@app.post("/exec_batch", response_model=BatchExecResponse)
async def exec_batch(request: BatchExecRequest) -> BatchExecResponse:
    """Execute a batch of agent-browser commands sequentially.

    In shared mode, sandbox-aware with optional cargo path translation;
    in single mode, uses legacy session.
    """
    sandbox_id = getattr(request, "sandbox_id", None)
    cargo_id = getattr(request, "cargo_id", None)
    batch_start = time.perf_counter()
    results: list[BatchStepResult] = []

    for i, cmd in enumerate(request.commands):
        elapsed = time.perf_counter() - batch_start
        remaining_timeout = request.timeout - elapsed
        if remaining_timeout <= 0:
            break

        step_start = time.perf_counter()

        if GULL_MODE == "shared" and sandbox_id:
            if cargo_id:
                argv, cwd, profile_path = _translate_and_split(cmd, cargo_id)
                from app.session import execute_browser_raw

                stdout, stderr, exit_code = await execute_browser_raw(
                    sandbox_id, argv, cwd=cwd, profile=profile_path,
                    timeout=remaining_timeout,
                )
            else:
                from app.session import execute_browser

                stdout, stderr, exit_code = await execute_browser(
                    sandbox_id, cmd, timeout=remaining_timeout,
                )
        else:
            profile = None if _browser_ready else BROWSER_PROFILE_DIR
            stdout, stderr, exit_code = await _run_agent_browser(
                cmd,
                session=SESSION_NAME,
                profile=profile,
                timeout=remaining_timeout,
            )

        step_duration_ms = int((time.perf_counter() - step_start) * 1000)
        results.append(
            BatchStepResult(
                cmd=cmd, stdout=stdout, stderr=stderr,
                exit_code=exit_code, step_index=i, duration_ms=step_duration_ms,
            )
        )
        if request.stop_on_error and exit_code != 0:
            break

    total_duration_ms = int((time.perf_counter() - batch_start) * 1000)
    return BatchExecResponse(
        results=results,
        total_steps=len(request.commands),
        completed_steps=len(results),
        success=(
            len(results) == len(request.commands)
            and all(r.exit_code == 0 for r in results)
        ),
        duration_ms=total_duration_ms,
    )


@app.get("/health", response_model=HealthResponse)
async def health() -> HealthResponse:
    """Health check endpoint.

    In shared mode: checks Chromium is alive via CDP port.
    In single mode: checks agent-browser daemon is responsive.
    """
    # ── Shared mode ──────────────────────────────────────────────────
    if GULL_MODE == "shared":
        from app.session import check_chromium_health

        chromium_ok = await check_chromium_health()
        agent_ok = shutil.which("agent-browser") is not None
        return HealthResponse(
            status="healthy" if (chromium_ok and agent_ok) else "degraded",
            browser_active=chromium_ok,
            browser_ready=chromium_ok,
            session="shared",
            version=GULL_VERSION,
        )

    # ── Single mode (legacy) ────────────────────────────────────────
    agent_browser_available = shutil.which("agent-browser") is not None
    if not agent_browser_available:
        return HealthResponse(
            status="unhealthy",
            browser_active=False,
            browser_ready=False,
            session=SESSION_NAME,
            version=GULL_VERSION,
        )

    # Check if our session is active
    stdout, stderr, code = await _run_agent_browser(
        "session list",
        # Do NOT bind to a session here; we want to list all active sessions.
        session=None,
        profile=None,
        timeout=5,
    )

    probe_ok = code == 0
    browser_active = SESSION_NAME in stdout if probe_ok else False
    status = "healthy" if probe_ok else "degraded"

    return HealthResponse(
        status=status,
        browser_active=browser_active,
        browser_ready=_browser_ready,
        session=SESSION_NAME,
        version=GULL_VERSION,
    )


@app.delete("/sessions/{sandbox_id}")
async def delete_session(sandbox_id: str) -> dict:
    """Destroy a browser session for a sandbox (shared mode only).

    Calls agent-browser close for the session daemon.  Best-effort —
    returns successfully even if the session was already gone.
    """
    if GULL_MODE == "shared":
        from app.session import destroy_session
        await destroy_session(sandbox_id)
    return {"sandbox_id": sandbox_id, "destroyed": True}


@app.get("/meta", response_model=MetaResponse)
async def meta() -> MetaResponse:
    """Runtime metadata endpoint.

    Returns capabilities and version information for Bay's CapabilityRouter.
    Format matches Ship's /meta response for consistency.

    Note: screenshot is NOT a separate capability. Use agent-browser's
    `screenshot /workspace/xxx.png` command via browser exec, then download
    via Ship's filesystem capability (both containers share the Cargo Volume).
    """
    return MetaResponse(
        runtime={
            "name": "gull",
            "version": GULL_VERSION,
            "api_version": "v1",
        },
        workspace={
            "mount_path": WORKSPACE_PATH,
        },
        capabilities={
            "browser": {"version": "1.0"},
        },
        built_in_skills=_scan_built_in_skills(),
    )
