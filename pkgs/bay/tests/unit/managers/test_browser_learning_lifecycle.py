"""Unit tests for browser learning scheduler lifecycle helpers."""

from __future__ import annotations

from types import SimpleNamespace

import pytest

from app.config import BrowserLearningConfig
from app.services.skills import lifecycle as lifecycle_module


@pytest.fixture(autouse=True)
def _reset_global_scheduler(monkeypatch: pytest.MonkeyPatch):
    monkeypatch.setattr(lifecycle_module, "_browser_learning_scheduler", None)


def _config(*, enabled: bool, run_on_startup: bool) -> BrowserLearningConfig:
    return BrowserLearningConfig(
        enabled=enabled,
        run_on_startup=run_on_startup,
        interval_seconds=60,
        batch_size=20,
        score_threshold=0.85,
        replay_success_threshold=0.95,
        min_samples=30,
        canary_window_hours=24,
        success_drop_threshold=0.03,
        error_rate_multiplier_threshold=2.0,
    )


class _FakeScheduler:
    def __init__(self, config: BrowserLearningConfig, *, raise_on_run_once: bool = False) -> None:
        self.config = config
        self.raise_on_run_once = raise_on_run_once
        self.run_once_calls = 0
        self.start_calls = 0
        self.stop_calls = 0

    async def run_once(self) -> None:
        self.run_once_calls += 1
        if self.raise_on_run_once:
            raise RuntimeError("startup cycle failed")

    async def start(self) -> None:
        self.start_calls += 1

    async def stop(self) -> None:
        self.stop_calls += 1


@pytest.mark.asyncio
async def test_init_disabled_creates_scheduler_without_startup_cycle(
    monkeypatch: pytest.MonkeyPatch,
):
    scheduler_holder: dict[str, _FakeScheduler] = {}

    def _factory(config: BrowserLearningConfig):
        scheduler = _FakeScheduler(config)
        scheduler_holder["scheduler"] = scheduler
        return scheduler

    monkeypatch.setattr(lifecycle_module, "BrowserLearningScheduler", _factory)
    monkeypatch.setattr(
        lifecycle_module,
        "get_settings",
        lambda: SimpleNamespace(
            browser_learning=_config(enabled=False, run_on_startup=True),
            browser_auto_release_enabled=True,
        ),
    )

    scheduler = await lifecycle_module.init_browser_learning_scheduler()
    fake = scheduler_holder["scheduler"]
    assert scheduler is fake
    assert fake.run_once_calls == 0
    assert fake.start_calls == 0
    assert lifecycle_module.get_browser_learning_scheduler() is fake


@pytest.mark.asyncio
async def test_init_enabled_without_startup_cycle_starts_scheduler(
    monkeypatch: pytest.MonkeyPatch,
):
    scheduler_holder: dict[str, _FakeScheduler] = {}

    def _factory(config: BrowserLearningConfig):
        scheduler = _FakeScheduler(config)
        scheduler_holder["scheduler"] = scheduler
        return scheduler

    monkeypatch.setattr(lifecycle_module, "BrowserLearningScheduler", _factory)
    monkeypatch.setattr(
        lifecycle_module,
        "get_settings",
        lambda: SimpleNamespace(
            browser_learning=_config(enabled=True, run_on_startup=False),
            browser_auto_release_enabled=True,
        ),
    )

    await lifecycle_module.init_browser_learning_scheduler()
    fake = scheduler_holder["scheduler"]
    assert fake.run_once_calls == 0
    assert fake.start_calls == 1


@pytest.mark.asyncio
async def test_init_enabled_with_startup_cycle_runs_once_then_starts(
    monkeypatch: pytest.MonkeyPatch,
):
    scheduler_holder: dict[str, _FakeScheduler] = {}

    def _factory(config: BrowserLearningConfig):
        scheduler = _FakeScheduler(config)
        scheduler_holder["scheduler"] = scheduler
        return scheduler

    monkeypatch.setattr(lifecycle_module, "BrowserLearningScheduler", _factory)
    monkeypatch.setattr(
        lifecycle_module,
        "get_settings",
        lambda: SimpleNamespace(
            browser_learning=_config(enabled=True, run_on_startup=True),
            browser_auto_release_enabled=True,
        ),
    )

    await lifecycle_module.init_browser_learning_scheduler()
    fake = scheduler_holder["scheduler"]
    assert fake.run_once_calls == 1
    assert fake.start_calls == 1


@pytest.mark.asyncio
async def test_init_startup_cycle_failure_does_not_block_start(
    monkeypatch: pytest.MonkeyPatch,
):
    scheduler_holder: dict[str, _FakeScheduler] = {}

    def _factory(config: BrowserLearningConfig):
        scheduler = _FakeScheduler(config, raise_on_run_once=True)
        scheduler_holder["scheduler"] = scheduler
        return scheduler

    monkeypatch.setattr(lifecycle_module, "BrowserLearningScheduler", _factory)
    monkeypatch.setattr(
        lifecycle_module,
        "get_settings",
        lambda: SimpleNamespace(
            browser_learning=_config(enabled=True, run_on_startup=True),
            browser_auto_release_enabled=True,
        ),
    )

    await lifecycle_module.init_browser_learning_scheduler()
    fake = scheduler_holder["scheduler"]
    assert fake.run_once_calls == 1
    assert fake.start_calls == 1


@pytest.mark.asyncio
async def test_shutdown_stops_scheduler_and_clears_global(monkeypatch: pytest.MonkeyPatch):
    fake = _FakeScheduler(_config(enabled=True, run_on_startup=True))
    monkeypatch.setattr(lifecycle_module, "_browser_learning_scheduler", fake)

    await lifecycle_module.shutdown_browser_learning_scheduler()
    assert fake.stop_calls == 1
    assert lifecycle_module.get_browser_learning_scheduler() is None


@pytest.mark.asyncio
async def test_shutdown_without_scheduler_is_noop():
    assert lifecycle_module.get_browser_learning_scheduler() is None
    await lifecycle_module.shutdown_browser_learning_scheduler()
    assert lifecycle_module.get_browser_learning_scheduler() is None
