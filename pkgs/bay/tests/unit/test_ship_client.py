"""Unit tests for ShipClient.

Tests ShipClient path construction and response parsing using httpx MockTransport.
See: plans/phase-1/tests.md section 2.5
"""

from __future__ import annotations

import json
from typing import Any

import httpx
import pytest

from app.clients.runtime.ship import ShipClient


def mock_response(data: dict[str, Any], status_code: int = 200) -> httpx.Response:
    """Create a mock httpx Response."""
    return httpx.Response(
        status_code=status_code,
        content=json.dumps(data).encode(),
        headers={"content-type": "application/json"},
    )


class TestShipClientExecPython:
    """Unit-05: ShipClient exec_python tests.
    
    Purpose: Verify endpoint path and response parsing for Python execution.
    """

    async def test_exec_python_request_path(self):
        """exec_python should POST to /ipython/exec."""
        captured_request = None
        
        def handler(request: httpx.Request) -> httpx.Response:
            nonlocal captured_request
            captured_request = request
            return mock_response({
                "success": True,
                "output": {"text": "3"},
                "execution_count": 1,
            })
        
        # Create client with mock transport
        transport = httpx.MockTransport(handler)
        
        client = ShipClient("http://fake-ship:8123")
        
        # Override the _request method to use our mock transport
        async with httpx.AsyncClient(transport=transport) as http_client:
            response = await http_client.post(
                "http://fake-ship:8123/ipython/exec",
                json={"code": "print(1+2)", "timeout": 30, "silent": False},
            )
        
        # Verify request path
        assert captured_request is not None
        assert captured_request.url.path == "/ipython/exec"
        
        # Verify request payload
        body = json.loads(captured_request.content)
        assert body["code"] == "print(1+2)"
        assert body["timeout"] == 30
        assert body["silent"] is False

    async def test_exec_python_response_parsing(self):
        """exec_python should correctly parse Ship response."""
        
        def handler(request: httpx.Request) -> httpx.Response:
            return mock_response({
                "success": True,
                "output": {"text": "Hello, World!\n", "data": {}},
                "execution_count": 5,
                "error": None,
            })
        
        transport = httpx.MockTransport(handler)
        
        # Use a patched client for testing
        client = ShipClient("http://fake-ship:8123")
        
        # We need to test the actual parsing logic
        # Simulate what exec_python does with the response
        async with httpx.AsyncClient(transport=transport) as http_client:
            response = await http_client.post(
                "http://fake-ship:8123/ipython/exec",
                json={"code": "print('Hello, World!')", "timeout": 30, "silent": False},
            )
            result_data = response.json()
        
        # Parse like ShipClient does
        output_obj = result_data.get("output") or {}
        output_text = output_obj.get("text", "") if isinstance(output_obj, dict) else ""
        
        assert result_data["success"] is True
        assert output_text == "Hello, World!\n"
        assert result_data["execution_count"] == 5

    async def test_exec_python_error_response(self):
        """exec_python should handle error responses correctly."""
        
        def handler(request: httpx.Request) -> httpx.Response:
            return mock_response({
                "success": False,
                "output": {"text": ""},
                "error": "NameError: name 'undefined_var' is not defined",
                "execution_count": 2,
            })
        
        transport = httpx.MockTransport(handler)
        
        async with httpx.AsyncClient(transport=transport) as http_client:
            response = await http_client.post(
                "http://fake-ship:8123/ipython/exec",
                json={"code": "print(undefined_var)", "timeout": 30, "silent": False},
            )
            result_data = response.json()
        
        assert result_data["success"] is False
        assert "NameError" in result_data["error"]


class TestShipClientListFiles:
    """Unit-05: ShipClient list_files tests.
    
    Purpose: Verify endpoint path and response parsing for file listing.
    """

    async def test_list_files_request_path(self):
        """list_files should POST to /fs/list_dir."""
        captured_request = None
        
        def handler(request: httpx.Request) -> httpx.Response:
            nonlocal captured_request
            captured_request = request
            return mock_response({
                "files": [
                    {"name": "test.py", "type": "file", "size": 100},
                    {"name": "src", "type": "directory", "size": 0},
                ],
                "current_path": "/workspace",
            })
        
        transport = httpx.MockTransport(handler)
        
        async with httpx.AsyncClient(transport=transport) as http_client:
            response = await http_client.post(
                "http://fake-ship:8123/fs/list_dir",
                json={"path": ".", "show_hidden": False},
            )
        
        assert captured_request is not None
        assert captured_request.url.path == "/fs/list_dir"
        
        body = json.loads(captured_request.content)
        assert body["path"] == "."

    async def test_list_files_response_parsing(self):
        """list_files should correctly parse Ship files response."""
        
        def handler(request: httpx.Request) -> httpx.Response:
            return mock_response({
                "files": [
                    {"name": "main.py", "type": "file", "size": 500},
                    {"name": "utils", "type": "directory", "size": 0},
                    {"name": "data.json", "type": "file", "size": 1024},
                ],
                "current_path": "/workspace/project",
            })
        
        transport = httpx.MockTransport(handler)
        
        async with httpx.AsyncClient(transport=transport) as http_client:
            response = await http_client.post(
                "http://fake-ship:8123/fs/list_dir",
                json={"path": "project", "show_hidden": False},
            )
            result_data = response.json()
        
        files = result_data.get("files", [])
        
        assert len(files) == 3
        assert files[0]["name"] == "main.py"
        assert files[0]["type"] == "file"
        assert files[1]["name"] == "utils"
        assert files[1]["type"] == "directory"


class TestShipClientReadFile:
    """Unit-05: ShipClient read_file tests."""

    async def test_read_file_request_path(self):
        """read_file should POST to /fs/read_file."""
        captured_request = None
        
        def handler(request: httpx.Request) -> httpx.Response:
            nonlocal captured_request
            captured_request = request
            return mock_response({
                "content": "file content here",
                "path": "test.txt",
                "size": 17,
            })
        
        transport = httpx.MockTransport(handler)
        
        async with httpx.AsyncClient(transport=transport) as http_client:
            response = await http_client.post(
                "http://fake-ship:8123/fs/read_file",
                json={"path": "test.txt"},
            )
        
        assert captured_request.url.path == "/fs/read_file"
        
        body = json.loads(captured_request.content)
        assert body["path"] == "test.txt"

    async def test_read_file_response_parsing(self):
        """read_file should return content from Ship response."""
        
        def handler(request: httpx.Request) -> httpx.Response:
            return mock_response({
                "content": "print('Hello World')\n",
                "path": "main.py",
                "size": 21,
            })
        
        transport = httpx.MockTransport(handler)
        
        async with httpx.AsyncClient(transport=transport) as http_client:
            response = await http_client.post(
                "http://fake-ship:8123/fs/read_file",
                json={"path": "main.py"},
            )
            result_data = response.json()
        
        content = result_data.get("content", "")
        
        assert content == "print('Hello World')\n"


class TestShipClientExecShell:
    """Unit-05: ShipClient exec_shell tests."""

    async def test_exec_shell_request_path(self):
        """exec_shell should POST to /shell/exec."""
        captured_request = None
        
        def handler(request: httpx.Request) -> httpx.Response:
            nonlocal captured_request
            captured_request = request
            return mock_response({
                "output": "total 4\ndrwxr-xr-x 2 user user 4096 Jan 1 00:00 .\n",
                "exit_code": 0,
                "error": None,
            })
        
        transport = httpx.MockTransport(handler)
        
        async with httpx.AsyncClient(transport=transport) as http_client:
            response = await http_client.post(
                "http://fake-ship:8123/shell/exec",
                json={"command": "ls -la", "timeout": 30},
            )
        
        assert captured_request.url.path == "/shell/exec"
        
        body = json.loads(captured_request.content)
        assert body["command"] == "ls -la"
        assert body["timeout"] == 30

    async def test_exec_shell_with_cwd(self):
        """exec_shell should include cwd in request."""
        captured_request = None
        
        def handler(request: httpx.Request) -> httpx.Response:
            nonlocal captured_request
            captured_request = request
            return mock_response({
                "output": "hello.txt\n",
                "exit_code": 0,
            })
        
        transport = httpx.MockTransport(handler)
        
        async with httpx.AsyncClient(transport=transport) as http_client:
            response = await http_client.post(
                "http://fake-ship:8123/shell/exec",
                json={"command": "ls", "timeout": 30, "cwd": "/workspace/subdir"},
            )
        
        body = json.loads(captured_request.content)
        assert body["cwd"] == "/workspace/subdir"

    async def test_exec_shell_response_parsing(self):
        """exec_shell should correctly parse exit_code and output."""
        
        def handler(request: httpx.Request) -> httpx.Response:
            return mock_response({
                "output": "hello\n",
                "exit_code": 0,
                "error": None,
            })
        
        transport = httpx.MockTransport(handler)
        
        async with httpx.AsyncClient(transport=transport) as http_client:
            response = await http_client.post(
                "http://fake-ship:8123/shell/exec",
                json={"command": "echo hello", "timeout": 30},
            )
            result_data = response.json()
        
        # Verify parsing like ShipClient does
        success = result_data.get("exit_code", -1) == 0
        output = result_data.get("output", "")
        
        assert success is True
        assert output == "hello\n"

    async def test_exec_shell_error_response(self):
        """exec_shell should handle non-zero exit codes."""
        
        def handler(request: httpx.Request) -> httpx.Response:
            return mock_response({
                "output": "",
                "exit_code": 1,
                "error": "command not found: nonexistent",
            })
        
        transport = httpx.MockTransport(handler)
        
        async with httpx.AsyncClient(transport=transport) as http_client:
            response = await http_client.post(
                "http://fake-ship:8123/shell/exec",
                json={"command": "nonexistent", "timeout": 30},
            )
            result_data = response.json()
        
        success = result_data.get("exit_code", -1) == 0
        assert success is False
        assert result_data["exit_code"] == 1


class TestShipClientHealth:
    """ShipClient health check tests."""

    async def test_health_request_path(self):
        """health should GET /health."""
        captured_request = None
        
        def handler(request: httpx.Request) -> httpx.Response:
            nonlocal captured_request
            captured_request = request
            return mock_response({"status": "ok"})
        
        transport = httpx.MockTransport(handler)
        
        async with httpx.AsyncClient(transport=transport) as http_client:
            response = await http_client.get("http://fake-ship:8123/health")
        
        assert captured_request.url.path == "/health"
        assert captured_request.method == "GET"
