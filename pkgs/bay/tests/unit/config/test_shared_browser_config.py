"""Tests for shared browser pool — config parsing (items 1-7)."""

import pytest
from app.config import (
    BrowserServiceConfig,
    ContainerSpec,
    ProfileConfig,
    Settings,
)


class TestBrowserServiceConfig:
    """BrowserServiceConfig: defaults and parsing."""

    def test_default_is_disabled(self):
        cfg = BrowserServiceConfig()
        assert cfg.enabled is False
        assert cfg.endpoint == "http://gull-service:8115"

    def test_enabled_custom_endpoint(self):
        cfg = BrowserServiceConfig(enabled=True, endpoint="http://gull:9000")
        assert cfg.enabled is True
        assert cfg.endpoint == "http://gull:9000"

    def test_from_dict_like_yaml(self):
        cfg = BrowserServiceConfig(
            **{"enabled": True, "endpoint": "http://gull-shared:8115"}
        )
        assert cfg.enabled is True

    def test_settings_default_creates_disabled_instance(self):
        settings = Settings()
        assert settings.browser_service.enabled is False


class TestProfileBrowserField:
    """ProfileConfig.browser field: shared / isolated / None."""

    def test_not_set_defaults_to_none(self):
        p = ProfileConfig(id="test", image="ship:latest", capabilities=["python"])
        assert p.browser is None

    def test_shared_mode(self):
        p = ProfileConfig(
            id="browser-python",
            browser="shared",
            containers=[
                ContainerSpec(
                    name="ship", image="ship:latest",
                    runtime_type="ship", runtime_port=8123,
                    capabilities=["python", "shell", "filesystem", "browser"],
                ),
            ],
        )
        assert p.browser == "shared"
        gull = [c for c in p.containers if c.runtime_type == "gull"]
        assert not gull, "shared profile must not include a gull container"

    def test_isolated_mode_has_gull_container(self):
        p = ProfileConfig(
            id="browser-isolated",
            browser="isolated",
            containers=[
                ContainerSpec(
                    name="ship", image="ship:latest",
                    runtime_type="ship", runtime_port=8123,
                    capabilities=["python", "shell", "filesystem"],
                ),
                ContainerSpec(
                    name="gull", image="gull:latest",
                    runtime_type="gull", runtime_port=8115,
                    capabilities=["browser"],
                ),
            ],
        )
        assert p.browser == "isolated"
        gull = [c for c in p.containers if c.runtime_type == "gull"]
        assert len(gull) == 1, "isolated profile must include a gull container"

    def test_capabilities_still_report_browser_in_shared_mode(self):
        p = ProfileConfig(
            id="bs", browser="shared",
            containers=[
                ContainerSpec(
                    name="ship", image="ship:latest",
                    runtime_type="ship", runtime_port=8123,
                    capabilities=["python", "browser"],
                ),
            ],
        )
        assert "browser" in p.get_all_capabilities()


class TestBackwardCompat:
    """Profiles without the browser field remain per-sandbox."""

    def test_legacy_profile_no_browser_field(self):
        p = ProfileConfig(
            id="python-default", image="ship:latest",
            runtime_type="ship", runtime_port=8123,
            capabilities=["python", "shell", "filesystem"],
        )
        assert p.browser is None
        assert len(p.containers) == 1

    def test_legacy_browser_python_with_gull_unchanged(self):
        p = ProfileConfig(
            id="browser-python-legacy",
            containers=[
                ContainerSpec(
                    name="ship", image="ship:latest",
                    runtime_type="ship", runtime_port=8123,
                    capabilities=["python", "shell", "filesystem"],
                ),
                ContainerSpec(
                    name="gull", image="gull:latest",
                    runtime_type="gull", runtime_port=8115,
                    capabilities=["browser"],
                ),
            ],
        )
        assert p.browser is None
        assert len(p.containers) == 2
