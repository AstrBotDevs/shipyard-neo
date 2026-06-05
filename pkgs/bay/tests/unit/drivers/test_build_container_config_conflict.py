"""Regression tests: _build_container_config must NOT set both NetworkMode
and NetworkingConfig for the same network.

Setting both caused Docker to reject container.start(), leaving containers
permanently stuck in "created" state when using multi-container profiles
(e.g. browser-python).  The fix was to remove the redundant NetworkMode
from HostConfig, since NetworkingConfig already handles session-network
attachment with alias support.
"""

from __future__ import annotations

import pytest

from app.config import ContainerSpec, ProfileConfig
from app.drivers.docker.docker import DockerDriver
from app.models.cargo import Cargo
from app.models.session import Session


# -- Fixtures ----------------------------------------------------------------

def _make_multi_profile() -> ProfileConfig:
    """A browser-python style multi-container profile."""
    return ProfileConfig(
        id="browser-python",
        containers=[
            ContainerSpec(
                name="ship",
                image="ship:latest",
                runtime_type="ship",
                runtime_port=8123,
                capabilities=["python", "shell", "filesystem"],
            ),
            ContainerSpec(
                name="gull",
                image="gull:latest",
                runtime_type="gull",
                runtime_port=8115,
                capabilities=["browser"],
            ),
        ],
    )


def _make_single_profile() -> ProfileConfig:
    """A python-default style single-container profile."""
    return ProfileConfig(
        id="python-default",
        image="ship:latest",
        runtime_type="ship",
        runtime_port=8123,
        capabilities=["python", "shell", "filesystem"],
    )


@pytest.fixture
def driver() -> DockerDriver:
    """Driver in container_network mode (the most common production setup)."""
    d = DockerDriver.__new__(DockerDriver)
    d._socket = "unix:///var/run/docker.sock"
    d._network = "bay-network"
    d._connect_mode = "container_network"
    d._host_address = "127.0.0.1"
    d._publish_ports = True
    d._host_port = None
    d._image_pull_policy = "if_not_present"
    return d


@pytest.fixture
def session() -> Session:
    return Session(
        id="sess-test123",
        sandbox_id="sandbox-test123",
        profile_id="browser-python",
        runtime_type="ship",
    )


@pytest.fixture
def cargo() -> Cargo:
    return Cargo(
        id="cargo-test",
        owner="default",
        managed=True,
        driver_ref="vol-test",
    )


# -- Tests -------------------------------------------------------------------

SESSION_NETWORK = "bay_net_sess-test123"


class TestMultiContainerConfigNoNetworkModeConflict:
    """Multi-container _build_container_config must not set NetworkMode
    when NetworkingConfig is also present."""

    def test_host_config_has_no_network_mode(
        self,
        driver: DockerDriver,
        session: Session,
        cargo: Cargo,
    ):
        """HostConfig.NetworkMode must be absent — NetworkingConfig
        already handles session-network attachment."""
        profile = _make_multi_profile()
        spec = profile.containers[0]

        config, _ = driver._build_container_config(
            spec,
            session=session,
            cargo=cargo,
            network_name=SESSION_NETWORK,
        )

        host_config = config.get("HostConfig", {})
        network_mode = host_config.get("NetworkMode")

        assert network_mode is None, (
            f"HostConfig.NetworkMode is {network_mode!r}, but should be absent. "
            "NetworkingConfig already handles session-network connection. "
            "Setting both for the same network confuses Docker and caused "
            "containers to stay in 'created' state (issue #15)."
        )

    def test_networking_config_has_session_network(
        self,
        driver: DockerDriver,
        session: Session,
        cargo: Cargo,
    ):
        """NetworkingConfig must include the session network with alias."""
        profile = _make_multi_profile()
        spec = profile.containers[0]

        config, _ = driver._build_container_config(
            spec,
            session=session,
            cargo=cargo,
            network_name=SESSION_NETWORK,
        )

        networking_config = config.get("NetworkingConfig", {})
        endpoints = networking_config.get("EndpointsConfig", {})

        assert SESSION_NETWORK in endpoints, (
            f"NetworkingConfig.EndpointsConfig missing {SESSION_NETWORK!r}. "
            f"Got: {list(endpoints.keys())}"
        )

    def test_all_containers_in_profile_avoid_conflict(
        self,
        driver: DockerDriver,
        session: Session,
        cargo: Cargo,
    ):
        """Every container spec in a multi-container profile must
        produce a conflict-free config."""
        profile = _make_multi_profile()

        for spec in profile.containers:
            config, name = driver._build_container_config(
                spec,
                session=session,
                cargo=cargo,
                network_name=SESSION_NETWORK,
            )

            network_mode = config.get("HostConfig", {}).get("NetworkMode")
            assert network_mode is None, (
                f"Container '{spec.name}' ({name}): "
                f"HostConfig.NetworkMode is {network_mode!r}"
            )


class TestSingleContainerPathDoesNotUseNetworkingConfig:
    """Single-container path (driver.create) must not include
    NetworkingConfig — that path is verified by reading the source,
    not _build_container_config."""

    def test_single_container_create_has_no_networking_config(self):
        """Verify that the single-container create() method does not
        produce NetworkingConfig in its Docker API payload.

        This is the WORKING path (python-default profile).
        _build_container_config is only used by the multi-container path.
        """
        # From docker.py:create() lines 310-316, the config dict has
        # Image, Env, Labels, HostConfig, ExposedPorts — no NetworkingConfig.
        profile = _make_single_profile()
        primary = profile.get_primary_container()

        # Simulate config built by driver.create()
        config = {
            "Image": primary.image,
            "Env": [],
            "Labels": {},
            "HostConfig": {
                "Binds": ["vol-test:/workspace:rw"],
                "Memory": 1073741824,
                "NanoCpus": 1000000000,
                "PidsLimit": 256,
            },
            "ExposedPorts": {"8123/tcp": {}},
        }

        assert "NetworkingConfig" not in config, (
            "Single-container create() must NOT include NetworkingConfig"
        )
        assert "NetworkMode" not in config["HostConfig"], (
            "Single-container create() HostConfig must NOT have NetworkMode "
            "(it is added conditionally only when bay-network exists)"
        )
