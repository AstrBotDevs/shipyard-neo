"""
Shipyard Python SDK

A Python SDK for interacting with Shipyard containerized execution environments.
Provides convenient access to file system, shell, and Python execution capabilities.
"""

from .types import Spec, ShipInfo
from .client import ShipyardClient
from .session import SessionShip
from .filesystem import FileSystemComponent
from .shell import ShellComponent
from .python import PythonComponent
from .utils import create_session_ship

__version__ = "1.0.0"

__all__ = [
    "Spec",
    "ShipInfo",
    "ShipyardClient",
    "SessionShip",
    "FileSystemComponent",
    "ShellComponent",
    "PythonComponent",
    "create_session_ship",
]
