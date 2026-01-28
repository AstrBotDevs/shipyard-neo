"""
Shipyard Python SDK - Convenience functions
"""

from typing import Optional
from .types import Spec
from .client import ShipyardClient
from .session import SessionShip


async def create_session_ship(
    ttl: int,
    spec: Optional[Spec] = None,
    max_session_num: int = 1,
    endpoint_url: Optional[str] = None,
    access_token: Optional[str] = None,
    session_id: Optional[str] = None,
) -> SessionShip:
    """
    Convenience function to create a SessionShip directly

    Args:
        ttl: Time to live in seconds
        spec: Ship specifications for resource allocation
        max_session_num: Maximum number of sessions that can use this ship
        endpoint_url: Bay API endpoint URL (can also be set via SHIPYARD_ENDPOINT env var)
        access_token: Access token for authentication (can also be set via SHIPYARD_TOKEN env var)
        session_id: Session ID (if not provided, a random one will be generated)

    Returns:
        SessionShip: The created ship session
    """
    client = ShipyardClient(endpoint_url, access_token)
    return await client.create_ship(ttl, spec, max_session_num, session_id)
