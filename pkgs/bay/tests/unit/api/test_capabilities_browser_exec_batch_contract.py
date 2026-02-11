"""Contract tests for browser exec_batch response/history consistency."""

from __future__ import annotations

from types import SimpleNamespace

import pytest

import app.api.v1.capabilities as caps_mod
from app.api.v1.capabilities import BrowserBatchExecRequest, exec_browser_batch


class _FakeCapabilityRouter:
    def __init__(self, _sandbox_mgr):
        self.calls: list[dict] = []

    async def exec_browser_batch(
        self,
        *,
        sandbox,
        commands: list[str],
        timeout: int,
        stop_on_error: bool,
    ):
        self.calls.append(
            {
                "sandbox": sandbox,
                "commands": commands,
                "timeout": timeout,
                "stop_on_error": stop_on_error,
            }
        )
        return {
            "results": [
                {
                    "cmd": "open about:blank",
                    "stdout": "ok",
                    "stderr": "",
                    "exit_code": 0,
                    "step_index": 0,
                    "duration_ms": 10,
                },
                {
                    "cmd": "snapshot -i",
                    "stdout": "",
                    "stderr": "step failed",
                    "exit_code": 2,
                    "step_index": 1,
                    "duration_ms": 11,
                },
            ],
            "total_steps": 3,
            "completed_steps": 2,
            "success": False,
            "duration_ms": 42,
        }


class _FakeSkillService:
    def __init__(self):
        self.execution_kwargs = None
        self.artifact_kwargs = None

    async def create_artifact_blob(self, **kwargs):
        self.artifact_kwargs = kwargs
        return SimpleNamespace(id="blob-1")

    def make_blob_ref(self, blob_id: str) -> str:
        return f"blob:{blob_id}"

    async def create_execution(self, **kwargs):
        self.execution_kwargs = kwargs
        return SimpleNamespace(id="exec-1")


class _FakeSandboxManager:
    async def get_current_session(self, _sandbox):
        return SimpleNamespace(id="sess-1")


@pytest.mark.asyncio
async def test_exec_batch_preserves_raw_batch_summary_and_execution_success(monkeypatch):
    fake_router = _FakeCapabilityRouter(None)
    monkeypatch.setattr(caps_mod, "CapabilityRouter", lambda _mgr: fake_router)

    sandbox = SimpleNamespace(id="sbx-1")
    sandbox_mgr = _FakeSandboxManager()
    skill_svc = _FakeSkillService()

    response = await exec_browser_batch(
        request=BrowserBatchExecRequest(
            commands=["open about:blank", "snapshot -i", "get title"],
            timeout=60,
            stop_on_error=True,
            include_trace=False,
            learn=False,
        ),
        sandbox=sandbox,
        sandbox_mgr=sandbox_mgr,
        skill_svc=skill_svc,
        owner="default",
    )

    assert response.total_steps == 3
    assert response.completed_steps == 2
    assert response.success is False
    assert response.duration_ms == 42
    assert response.execution_id == "exec-1"

    assert skill_svc.execution_kwargs is not None
    assert skill_svc.execution_kwargs["success"] is False
    assert skill_svc.execution_kwargs["exec_type"] == caps_mod.ExecutionType.BROWSER_BATCH


@pytest.mark.asyncio
async def test_exec_batch_with_trace_stores_trace_payload_with_matching_summary(monkeypatch):
    fake_router = _FakeCapabilityRouter(None)
    monkeypatch.setattr(caps_mod, "CapabilityRouter", lambda _mgr: fake_router)

    sandbox = SimpleNamespace(id="sbx-1")
    sandbox_mgr = _FakeSandboxManager()
    skill_svc = _FakeSkillService()

    response = await exec_browser_batch(
        request=BrowserBatchExecRequest(
            commands=["open about:blank", "snapshot -i", "get title"],
            timeout=60,
            stop_on_error=True,
            include_trace=True,
            learn=False,
        ),
        sandbox=sandbox,
        sandbox_mgr=sandbox_mgr,
        skill_svc=skill_svc,
        owner="default",
    )

    assert response.trace_ref == "blob:blob-1"

    assert skill_svc.artifact_kwargs is not None
    payload = skill_svc.artifact_kwargs["payload"]
    assert payload["kind"] == "browser_batch_trace"
    assert payload["total_steps"] == 3
    assert payload["completed_steps"] == 2
    assert payload["success"] is False
    assert len(payload["steps"]) == 2
    assert payload["steps"][1]["exit_code"] == 2
