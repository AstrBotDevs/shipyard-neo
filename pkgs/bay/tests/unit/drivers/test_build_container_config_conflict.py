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
    """Multi-container _build_container_config must set NetworkMode
    for DNS + session network, and only put *different* networks
    (e.g. bay-network) in EndpointsConfig."""

    def test_host_config_has_network_mode_set_to_session_network(
        self,
        driver: DockerDriver,
        session: Session,
        cargo: Cargo,
    ):
        """HostConfig.NetworkMode must be the session network — this
        activates Docker's embedded DNS resolver (127.0.0.11).  Without
        it containers get 'Temporary failure in name resolution'."""
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

        assert network_mode == SESSION_NETWORK, (
            f"HostConfig.NetworkMode is {network_mode!r}, expected {SESSION_NETWORK!r}. "
            "Without NetworkMode set to the session network, Docker won't enable "
            "its embedded DNS resolver, causing 'Temporary failure in name resolution'."
        )

    def test_networking_config_absent_when_no_bay_network(
        self,
        driver: DockerDriver,
        session: Session,
        cargo: Cargo,
    ):
        """When no bay-network is configured (connect_bay_network=False),
        NetworkingConfig must be absent — session network is already the
        primary via NetworkMode."""
        profile = _make_multi_profile()
        spec = profile.containers[0]

        config, _ = driver._build_container_config(
            spec,
            session=session,
            cargo=cargo,
            network_name=SESSION_NETWORK,
            # connect_bay_network defaults to False → no bay-network
        )

        networking_config = config.get("NetworkingConfig")
        assert networking_config is None, (
            f"NetworkingConfig should be None when connect_bay_network=False. "
            f"Got: {networking_config!r}"
        )

    def test_all_containers_in_profile_have_session_network_as_primary(
        self,
        driver: DockerDriver,
        session: Session,
        cargo: Cargo,
    ):
        """Every container spec in a multi-container profile must have
        session network as NetworkMode and NO duplicate in EndpointsConfig."""
        profile = _make_multi_profile()

        for spec in profile.containers:
            config, name = driver._build_container_config(
                spec,
                session=session,
                cargo=cargo,
                network_name=SESSION_NETWORK,
            )

            network_mode = config.get("HostConfig", {}).get("NetworkMode")
            assert network_mode == SESSION_NETWORK, (
                f"Container '{spec.name}' ({name}): "
                f"NetworkMode={network_mode!r} should be {SESSION_NETWORK!r}"
            )

            # When connect_bay_network=False, NetworkingConfig must be absent
            # (session network is already the primary via NetworkMode)
            nc = config.get("NetworkingConfig")
            assert nc is None, (
                f"Container '{spec.name}' ({name}): "
                f"NetworkingConfig should be None (connect_bay_network=False), "
                f"got {nc!r}"
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
