"""Unit tests for browser trace payload helpers in capabilities API."""

from __future__ import annotations

from app.api.v1.capabilities import (
    _build_browser_batch_trace_payload,
    _build_browser_exec_trace_payload,
)


def test_build_browser_exec_trace_payload_sets_single_step_fields():
    payload = _build_browser_exec_trace_payload(
        cmd="open about:blank",
        result_output="opened",
        result_error=None,
        exit_code=0,
    )
    assert payload["kind"] == "browser_exec_trace"
    assert len(payload["steps"]) == 1
    step = payload["steps"][0]
    assert step["kind"] == "individual_action"
    assert step["cmd"] == "open about:blank"
    assert step["stdout"] == "opened"
    assert step["stderr"] == ""
    assert step["exit_code"] == 0


def test_build_browser_batch_trace_payload_skips_non_dict_steps_and_coerces_exit_code():
    payload = _build_browser_batch_trace_payload(
        request_commands=["open about:blank", "snapshot -i", "click @e1"],
        raw_result={
            "results": [
                {"cmd": "open about:blank", "stdout": "ok", "stderr": "", "exit_code": "0"},
                "not-a-step",
                {"stdout": "snapshot", "stderr": "", "exit_code": None, "step_index": 2},
            ],
            "total_steps": 3,
            "completed_steps": 2,
            "success": False,
            "duration_ms": 42,
        },
    )
    assert payload["kind"] == "browser_batch_trace"
    assert payload["total_steps"] == 3
    assert payload["completed_steps"] == 2
    assert payload["success"] is False
    assert payload["duration_ms"] == 42
    assert len(payload["steps"]) == 2
    assert payload["steps"][0]["exit_code"] == 0
    assert payload["steps"][1]["cmd"] == "click @e1"
    assert payload["steps"][1]["exit_code"] == -1


def test_build_browser_batch_trace_payload_uses_request_length_defaults():
    payload = _build_browser_batch_trace_payload(
        request_commands=["open about:blank"],
        raw_result={"results": []},
    )
    assert payload["total_steps"] == 1
    assert payload["completed_steps"] == 0
    assert payload["success"] is False
    assert payload["duration_ms"] == 0
    assert payload["steps"] == []
