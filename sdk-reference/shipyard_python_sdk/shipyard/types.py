"""
Shipyard Python SDK - Type definitions and data models
"""

from typing import Optional
from dataclasses import dataclass
from datetime import datetime


@dataclass
class Spec:
    """Ship specification for resource allocation"""

    cpus: Optional[float] = None
    memory: Optional[str] = None


class ShipInfo:
    """Ship information wrapper"""

    def __init__(self, ship_data: dict):
        self._data = ship_data

    @property
    def id(self) -> str:
        """Ship ID"""
        return self._data["id"]

    @property
    def status(self) -> int:
        """Ship status (1: running, 0: stopped)"""
        return self._data["status"]

    @property
    def container_id(self) -> Optional[str]:
        """Container ID"""
        return self._data.get("container_id")

    @property
    def ip_address(self) -> Optional[str]:
        """Ship IP address"""
        return self._data.get("ip_address")

    @property
    def created_at(self) -> datetime:
        """Creation timestamp"""
        return datetime.fromisoformat(self._data["created_at"].replace("Z", "+00:00"))

    @property
    def ttl(self) -> int:
        """Time to live in seconds"""
        return self._data["ttl"]

    @property
    def max_session_num(self) -> int:
        """Maximum number of sessions"""
        return self._data["max_session_num"]

    @property
    def current_session_num(self) -> int:
        """Current number of sessions"""
        return self._data["current_session_num"]
