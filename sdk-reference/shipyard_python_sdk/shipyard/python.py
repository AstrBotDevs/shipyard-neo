"""
Shipyard Python SDK - Python/IPython component
"""

from typing import Dict, Any, Optional, TYPE_CHECKING

if TYPE_CHECKING:
    from .client import ShipyardClient


class PythonComponent:
    """Python/IPython operations component"""

    def __init__(self, client: "ShipyardClient", ship_id: str, session_id: str):
        self._client = client
        self._ship_id = ship_id
        self._session_id = session_id

    async def exec(
        self,
        code: str,
        kernel_id: Optional[str] = None,
        timeout: int = 30,
        silent: bool = False,
    ) -> Dict[str, Any]:
        """Execute Python code"""
        payload = {
            "code": code,
            "kernel_id": kernel_id,
            "timeout": timeout,
            "silent": silent,
        }
        return await self._client._exec_operation(
            self._ship_id, "ipython/exec", payload, self._session_id
        )
