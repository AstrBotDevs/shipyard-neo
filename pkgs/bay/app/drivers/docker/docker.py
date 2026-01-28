"""Docker driver implementation using aiodocker.

Supports:
- Running Bay inside a container with mounted docker.sock
- Running Bay on host with direct docker.sock access
"""

from __future__ import annotations

from typing import TYPE_CHECKING

import aiodocker
import structlog
from aiodocker.exceptions import DockerError

from app.config import get_settings
from app.drivers.base import ContainerInfo, ContainerStatus, Driver

if TYPE_CHECKING:
    from app.config import ProfileConfig
    from app.models.session import Session
    from app.models.workspace import Workspace

logger = structlog.get_logger()

# Workspace mount path inside container (fixed)
WORKSPACE_MOUNT_PATH = "/workspace"

# Ship container port
SHIP_PORT = 8000


def _parse_memory(memory_str: str) -> int:
    """Parse memory string (e.g., '1g', '512m') to bytes."""
    memory_str = memory_str.lower().strip()
    multipliers = {
        "k": 1024,
        "m": 1024 * 1024,
        "g": 1024 * 1024 * 1024,
    }
    if memory_str[-1] in multipliers:
        return int(float(memory_str[:-1]) * multipliers[memory_str[-1]])
    return int(memory_str)


class DockerDriver(Driver):
    """Docker driver implementation using aiodocker."""

    def __init__(self) -> None:
        settings = get_settings()
        # Parse socket URL
        socket_url = settings.driver.docker.socket
        if socket_url.startswith("unix://"):
            self._socket = socket_url
        else:
            self._socket = f"unix://{socket_url}"

        self._network = settings.driver.docker.network
        self._log = logger.bind(driver="docker")
        self._client: aiodocker.Docker | None = None

    async def _get_client(self) -> aiodocker.Docker:
        """Get or create the aiodocker client."""
        if self._client is None:
            self._client = aiodocker.Docker(url=self._socket)
        return self._client

    async def close(self) -> None:
        """Close the docker client."""
        if self._client is not None:
            await self._client.close()
            self._client = None

    async def create(
        self,
        session: "Session",
        profile: "ProfileConfig",
        workspace: "Workspace",
        *,
        labels: dict[str, str] | None = None,
    ) -> str:
        """Create a container without starting it."""
        client = await self._get_client()

        # Build labels (required for reconciliation)
        container_labels = {
            "bay.owner": "default",  # TODO: get from session/sandbox
            "bay.sandbox_id": session.sandbox_id,
            "bay.session_id": session.id,
            "bay.workspace_id": workspace.id,
            "bay.profile_id": profile.id,
            "bay.managed": "true",
        }
        if labels:
            container_labels.update(labels)

        # Parse resource limits
        mem_limit = _parse_memory(profile.resources.memory)
        nano_cpus = int(profile.resources.cpus * 1e9)

        # Build environment
        env = [f"{k}={v}" for k, v in profile.env.items()]
        env.extend([
            f"BAY_SESSION_ID={session.id}",
            f"BAY_SANDBOX_ID={session.sandbox_id}",
            f"BAY_WORKSPACE_PATH={WORKSPACE_MOUNT_PATH}",
        ])

        self._log.info(
            "docker.create",
            session_id=session.id,
            image=profile.image,
            workspace=workspace.driver_ref,
        )

        # Create container config
        config = {
            "Image": profile.image,
            "Env": env,
            "Labels": container_labels,
            "HostConfig": {
                "Binds": [f"{workspace.driver_ref}:{WORKSPACE_MOUNT_PATH}:rw"],
                "Memory": mem_limit,
                "NanoCpus": nano_cpus,
                "NetworkMode": self._network,
                "PidsLimit": 256,
            },
        }

        container = await client.containers.create(
            config=config,
            name=f"bay-session-{session.id}",
        )

        container_id = container.id
        self._log.info("docker.created", container_id=container_id)
        return container_id

    async def start(self, container_id: str) -> str:
        """Start container and return Ship endpoint."""
        client = await self._get_client()
        self._log.info("docker.start", container_id=container_id)

        container = client.containers.container(container_id)
        await container.start()

        # Get container info to find IP
        info = await container.show()
        networks = info.get("NetworkSettings", {}).get("Networks", {})

        endpoint = None
        if self._network in networks:
            ip = networks[self._network].get("IPAddress")
            if ip:
                endpoint = f"http://{ip}:{SHIP_PORT}"

        # Fallback: use container name
        if not endpoint:
            name = info.get("Name", "").lstrip("/")
            endpoint = f"http://{name}:{SHIP_PORT}"

        self._log.info("docker.started", container_id=container_id, endpoint=endpoint)
        return endpoint

    async def stop(self, container_id: str) -> None:
        """Stop a running container."""
        client = await self._get_client()
        self._log.info("docker.stop", container_id=container_id)

        try:
            container = client.containers.container(container_id)
            await container.stop(timeout=10)
        except DockerError as e:
            if e.status == 404:
                self._log.warning("docker.stop.not_found", container_id=container_id)
            else:
                raise

    async def destroy(self, container_id: str) -> None:
        """Destroy (remove) a container."""
        client = await self._get_client()
        self._log.info("docker.destroy", container_id=container_id)

        try:
            container = client.containers.container(container_id)
            await container.delete(force=True)
        except DockerError as e:
            if e.status == 404:
                self._log.warning("docker.destroy.not_found", container_id=container_id)
            else:
                raise

    async def status(self, container_id: str) -> ContainerInfo:
        """Get container status."""
        client = await self._get_client()

        try:
            container = client.containers.container(container_id)
            info = await container.show()
        except DockerError as e:
            if e.status == 404:
                return ContainerInfo(
                    container_id=container_id,
                    status=ContainerStatus.NOT_FOUND,
                )
            raise

        docker_status = info.get("State", {}).get("Status", "unknown")

        if docker_status == "running":
            status = ContainerStatus.RUNNING
        elif docker_status == "created":
            status = ContainerStatus.CREATED
        elif docker_status in ("exited", "dead"):
            status = ContainerStatus.EXITED
        elif docker_status == "removing":
            status = ContainerStatus.REMOVING
        else:
            status = ContainerStatus.EXITED

        # Get endpoint if running
        endpoint = None
        if status == ContainerStatus.RUNNING:
            networks = info.get("NetworkSettings", {}).get("Networks", {})
            if self._network in networks:
                ip = networks[self._network].get("IPAddress")
                if ip:
                    endpoint = f"http://{ip}:{SHIP_PORT}"

        # Get exit code
        exit_code = info.get("State", {}).get("ExitCode")

        return ContainerInfo(
            container_id=container_id,
            status=status,
            endpoint=endpoint,
            exit_code=exit_code,
        )

    async def logs(self, container_id: str, tail: int = 100) -> str:
        """Get container logs."""
        client = await self._get_client()

        try:
            container = client.containers.container(container_id)
            logs = await container.log(stdout=True, stderr=True, tail=tail)
            return "".join(logs)
        except DockerError as e:
            if e.status == 404:
                return ""
            raise

    # Volume management

    async def create_volume(self, name: str, labels: dict[str, str] | None = None) -> str:
        """Create a Docker volume."""
        client = await self._get_client()
        self._log.info("docker.create_volume", name=name)

        volume_labels = {"bay.managed": "true"}
        if labels:
            volume_labels.update(labels)

        volume = await client.volumes.create({
            "Name": name,
            "Labels": volume_labels,
        })

        return volume["Name"]

    async def delete_volume(self, name: str) -> None:
        """Delete a Docker volume."""
        client = await self._get_client()
        self._log.info("docker.delete_volume", name=name)

        try:
            volume = await client.volumes.get(name)
            await volume.delete()
        except DockerError as e:
            if e.status == 404:
                self._log.warning("docker.delete_volume.not_found", name=name)
            else:
                raise

    async def volume_exists(self, name: str) -> bool:
        """Check if volume exists."""
        client = await self._get_client()

        try:
            await client.volumes.get(name)
            return True
        except DockerError as e:
            if e.status == 404:
                return False
            raise
