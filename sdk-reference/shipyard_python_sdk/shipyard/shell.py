"""
Shipyard Python SDK - Shell component
"""

from typing import Dict, Any, Optional, TYPE_CHECKING

if TYPE_CHECKING:
    from .client import ShipyardClient


class ShellComponent:
    """Shell operations component"""

    def __init__(self, client: "ShipyardClient", ship_id: str, session_id: str):
        self._client = client
        self._ship_id = ship_id
        self._session_id = session_id

    async def exec(
        self,
        command: str,
        cwd: Optional[str] = None,
        env: Optional[Dict[str, str]] = None,
        timeout: Optional[int] = 30,
        shell: bool = True,
        background: bool = False,
    ) -> Dict[str, Any]:
        """Execute shell command"""
        payload = {
            "command": command,
            "cwd": cwd,
            "env": env,
            "timeout": timeout,
            "shell": shell,
            "background": background,
        }
        return await self._client._exec_operation(
            self._ship_id, "shell/exec", payload, self._session_id
        )
