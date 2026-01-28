"""Bay configuration management.

Configuration sources (in priority order):
1. Environment variables (BAY_ prefix)
2. Config file (config.yaml)
3. Defaults
"""

from __future__ import annotations

from functools import lru_cache
from pathlib import Path
from typing import Literal

import yaml
from pydantic import BaseModel, Field
from pydantic_settings import BaseSettings, SettingsConfigDict


class ServerConfig(BaseModel):
    """HTTP server configuration."""

    host: str = "0.0.0.0"
    port: int = 8000


class DatabaseConfig(BaseModel):
    """Database configuration."""

    # Phase 1: SQLite; 可切换到 postgresql+asyncpg:// 或 mysql+asyncmy://
    url: str = "sqlite+aiosqlite:///./bay.db"
    echo: bool = False


class DockerConfig(BaseModel):
    """Docker driver configuration."""

    socket: str = "unix:///var/run/docker.sock"
    network: str = "bay-network"


class K8sConfig(BaseModel):
    """Kubernetes driver configuration (Phase 2)."""

    namespace: str = "bay"
    kubeconfig: str | None = None


class DriverConfig(BaseModel):
    """Driver layer configuration."""

    type: Literal["docker", "k8s"] = "docker"
    docker: DockerConfig = Field(default_factory=DockerConfig)
    k8s: K8sConfig = Field(default_factory=K8sConfig)


class ResourceSpec(BaseModel):
    """Container resource specification."""

    cpus: float = 1.0
    memory: str = "1g"


class ProfileConfig(BaseModel):
    """Runtime profile configuration."""

    id: str
    image: str = "ship:latest"
    resources: ResourceSpec = Field(default_factory=ResourceSpec)
    capabilities: list[str] = Field(default_factory=lambda: ["filesystem", "shell", "ipython"])
    idle_timeout: int = 1800  # 30 minutes
    env: dict[str, str] = Field(default_factory=dict)


class WorkspaceConfig(BaseModel):
    """Workspace storage configuration."""

    # 宿主机路径，仅用于 Bay 管理，不暴露给运行时
    root_path: str = "/var/lib/bay/workspaces"
    default_size_limit_mb: int = 1024
    # 容器内挂载路径 (固定)
    mount_path: str = "/workspace"


class SecurityConfig(BaseModel):
    """Security configuration."""

    jwt_secret: str = "dev-secret-change-in-production"
    jwt_algorithm: str = "HS256"
    jwt_expire_minutes: int = 60
    blocked_hosts: list[str] = Field(
        default_factory=lambda: [
            "169.254.0.0/16",
            "10.0.0.0/8",
            "172.16.0.0/12",
            "192.168.0.0/16",
        ]
    )


class Settings(BaseSettings):
    """Bay application settings."""

    model_config = SettingsConfigDict(
        env_prefix="BAY_",
        env_nested_delimiter="__",
        case_sensitive=False,
    )

    server: ServerConfig = Field(default_factory=ServerConfig)
    database: DatabaseConfig = Field(default_factory=DatabaseConfig)
    driver: DriverConfig = Field(default_factory=DriverConfig)
    workspace: WorkspaceConfig = Field(default_factory=WorkspaceConfig)
    security: SecurityConfig = Field(default_factory=SecurityConfig)

    # Default profiles
    profiles: list[ProfileConfig] = Field(
        default_factory=lambda: [
            ProfileConfig(
                id="python-default",
                image="ship:latest",
                resources=ResourceSpec(cpus=1.0, memory="1g"),
                capabilities=["filesystem", "shell", "ipython"],
                idle_timeout=1800,
            ),
            ProfileConfig(
                id="python-data",
                image="ship:data",
                resources=ResourceSpec(cpus=2.0, memory="4g"),
                capabilities=["filesystem", "shell", "ipython"],
                idle_timeout=1800,
            ),
        ]
    )

    def get_profile(self, profile_id: str) -> ProfileConfig | None:
        """Get profile by ID."""
        for profile in self.profiles:
            if profile.id == profile_id:
                return profile
        return None


def _load_config_file() -> dict:
    """Load configuration from YAML file if exists.
    
    Looks for config file in order:
    1. BAY_CONFIG_FILE environment variable
    2. ./config.yaml
    3. /etc/bay/config.yaml
    """
    import os

    config_paths = [
        os.environ.get("BAY_CONFIG_FILE"),
        Path("config.yaml"),
        Path("/etc/bay/config.yaml"),
    ]

    for path in config_paths:
        if path is None:
            continue
        path = Path(path)
        if path.exists():
            with open(path) as f:
                return yaml.safe_load(f) or {}

    return {}


@lru_cache
def get_settings() -> Settings:
    """Get cached settings instance.
    
    Configuration is loaded from:
    1. YAML config file (if exists)
    2. Environment variables (override)
    3. Defaults
    """
    # Load from config file first
    file_config = _load_config_file()
    
    # Create settings with file config as initial values
    # Environment variables will override via pydantic-settings
    return Settings(**file_config)
