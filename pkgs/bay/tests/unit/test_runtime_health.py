from app.drivers.base import ContainerStatus, RuntimeInstance
from app.utils.runtime_health import all_expected_runtimes_running


def _runtime(runtime_id: str, state: ContainerStatus) -> RuntimeInstance:
    return RuntimeInstance(
        id=runtime_id,
        name=runtime_id,
        labels={},
        state=state.value,
    )


def test_all_expected_runtimes_running_requires_each_docker_container():
    expected = [
        {"name": "ship", "container_id": "ship-id"},
        {"name": "browser", "container_id": "browser-id"},
    ]

    assert not all_expected_runtimes_running(
        expected,
        [_runtime("ship-id", ContainerStatus.RUNNING)],
    )


def test_all_expected_runtimes_running_accepts_shared_kubernetes_pod():
    expected = [
        {"name": "ship", "container_id": "pod-id"},
        {"name": "browser", "container_id": "pod-id"},
    ]

    assert all_expected_runtimes_running(
        expected,
        [_runtime("pod-id", ContainerStatus.RUNNING)],
    )


def test_all_expected_runtimes_running_rejects_stopped_runtime():
    expected = [{"name": "ship", "container_id": "ship-id"}]

    assert not all_expected_runtimes_running(
        expected,
        [_runtime("ship-id", ContainerStatus.EXITED)],
    )


def test_all_expected_runtimes_running_rejects_missing_persisted_ids():
    assert not all_expected_runtimes_running(
        [{"name": "ship", "container_id": None}],
        [_runtime("ship-id", ContainerStatus.RUNNING)],
    )


def test_all_expected_runtimes_running_rejects_partially_missing_persisted_ids():
    expected = [
        {"name": "ship", "container_id": "ship-id"},
        {"name": "browser", "container_id": None},
    ]

    assert not all_expected_runtimes_running(
        expected,
        [_runtime("ship-id", ContainerStatus.RUNNING)],
    )
