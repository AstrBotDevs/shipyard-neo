"""Helpers for evaluating persisted multi-container runtime groups."""

from __future__ import annotations

from collections.abc import Iterable, Mapping

from app.drivers.base import ContainerStatus, RuntimeInstance


def all_expected_runtimes_running(
    expected_containers: Iterable[Mapping[str, object]],
    instances: Iterable[RuntimeInstance],
) -> bool:
    """Return whether every distinct persisted runtime is currently running."""
    expected_containers = list(expected_containers)
    if not expected_containers:
        return False

    expected_ids: set[str] = set()
    for container in expected_containers:
        container_id = container.get("container_id")
        if not isinstance(container_id, str) or not container_id:
            return False
        expected_ids.add(container_id)

    running_ids = {
        instance.id for instance in instances if instance.state == ContainerStatus.RUNNING.value
    }
    return expected_ids.issubset(running_ids)
