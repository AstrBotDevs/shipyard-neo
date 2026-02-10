"""
End-to-end tests for Ship API.

These tests run against an actual running Ship container.
Use the `run_e2e_tests.sh` script to start a container and run these tests.
"""

import pytest
import requests


def is_ship_running(base_url: str) -> bool:
    """Check if Ship API is running"""
    try:
        response = requests.get(f"{base_url}/health", timeout=5)
        return response.status_code == 200
    except requests.exceptions.RequestException:
        return False


# Mark all tests in this module as e2e tests
pytestmark = pytest.mark.e2e


@pytest.fixture(scope="module")
def api_url(base_url: str):
    """Get base URL and skip if Ship is not running."""
    if not is_ship_running(base_url):
        pytest.skip("Ship API is not running. Use run_e2e_tests.sh to start it.")
    return base_url


class TestHealthEndpoints:
    """Test basic health and status endpoints"""

    def test_health_check(self, api_url):
        """Test /health endpoint"""
        response = requests.get(f"{api_url}/health")
        assert response.status_code == 200
        assert response.json()["status"] == "healthy"

    def test_root_endpoint(self, api_url):
        """Test / endpoint"""
        response = requests.get(f"{api_url}/")
        assert response.status_code == 200
        assert "Ship API is running" in response.json()["message"]

    def test_stat_endpoint(self, api_url):
        """Test /stat endpoint"""
        response = requests.get(f"{api_url}/stat")
        assert response.status_code == 200
        data = response.json()
        assert data["service"] == "ship"
        assert data["status"] == "running"


class TestFilesystemAPI:
    """Test filesystem API endpoints"""

    def test_create_and_read_file(self, api_url):
        """Test creating and reading a file"""
        # Create file
        response = requests.post(
            f"{api_url}/fs/create_file",
            json={"path": "e2e_test.txt", "content": "E2E Test Content", "mode": 0o644},
        )
        assert response.status_code == 200
        assert response.json()["success"] is True

        # Read file
        response = requests.post(
            f"{api_url}/fs/read_file", json={"path": "e2e_test.txt"}
        )
        assert response.status_code == 200
        assert response.json()["content"] == "E2E Test Content"

    def test_write_file(self, api_url):
        """Test writing to a file"""
        response = requests.post(
            f"{api_url}/fs/write_file",
            json={"path": "write_test.txt", "content": "Written via API", "mode": "w"},
        )
        assert response.status_code == 200
        assert response.json()["success"] is True

    def test_list_directory(self, api_url):
        """Test listing directory contents"""
        # Create some files first
        for name in ["list_test_1.txt", "list_test_2.txt"]:
            requests.post(
                f"{api_url}/fs/create_file", json={"path": name, "content": "test"}
            )

        response = requests.post(
            f"{api_url}/fs/list_dir", json={"path": ".", "show_hidden": False}
        )
        assert response.status_code == 200
        data = response.json()
        assert "files" in data
        file_names = [f["name"] for f in data["files"]]
        assert "list_test_1.txt" in file_names

    def test_delete_file(self, api_url):
        """Test deleting a file"""
        # Create file
        requests.post(
            f"{api_url}/fs/create_file",
            json={"path": "to_delete.txt", "content": "delete me"},
        )

        # Delete file
        response = requests.post(
            f"{api_url}/fs/delete_file", json={"path": "to_delete.txt"}
        )
        assert response.status_code == 200

        # Verify deleted
        response = requests.post(
            f"{api_url}/fs/read_file", json={"path": "to_delete.txt"}
        )
        assert response.status_code == 404

    def test_path_traversal_blocked(self, api_url):
        """Test that path traversal is blocked"""
        response = requests.post(
            f"{api_url}/fs/read_file", json={"path": "../../../etc/passwd"}
        )
        assert response.status_code == 403


class TestShellAPI:
    """Test shell API endpoints"""

    def test_execute_simple_command(self, api_url):
        """Test executing a simple shell command"""
        response = requests.post(
            f"{api_url}/shell/exec",
            json={"command": 'echo "Hello from shell"', "timeout": 10},
        )
        assert response.status_code == 200
        data = response.json()
        assert data["success"] is True
        assert data["return_code"] == 0
        assert "Hello from shell" in data["stdout"]

    def test_execute_with_cwd(self, api_url):
        """Test executing command in specific directory"""
        # Create a subdirectory first
        requests.post(f"{api_url}/shell/exec", json={"command": "mkdir -p test_subdir"})

        response = requests.post(
            f"{api_url}/shell/exec",
            json={"command": "pwd", "cwd": "test_subdir", "timeout": 10},
        )
        assert response.status_code == 200
        data = response.json()
        assert data["success"] is True
        assert "test_subdir" in data["stdout"]

    def test_execute_with_env(self, api_url):
        """Test executing command with environment variables"""
        response = requests.post(
            f"{api_url}/shell/exec",
            json={
                "command": 'echo "VAR=$MY_VAR"',
                "env": {"MY_VAR": "test_value"},
                "timeout": 10,
            },
        )
        assert response.status_code == 200
        data = response.json()
        assert data["success"] is True
        assert "test_value" in data["stdout"]

    def test_background_process(self, api_url):
        """Test running a background process"""
        response = requests.post(
            f"{api_url}/shell/exec",
            json={"command": "sleep 3 && echo done > bg_test.txt", "background": True},
        )
        assert response.status_code == 200
        data = response.json()
        assert data["success"] is True
        assert "process_id" in data
        assert "pid" in data

    def test_list_background_processes(self, api_url):
        """Test listing background processes"""
        response = requests.get(f"{api_url}/shell/processes")
        assert response.status_code == 200
        assert "processes" in response.json()


class TestIPythonAPI:
    """Test IPython API endpoints"""

    def test_execute_simple_code(self, api_url):
        """Test executing simple Python code"""
        response = requests.post(
            f"{api_url}/ipython/exec",
            json={"code": "print('Hello from IPython')", "timeout": 30},
        )
        assert response.status_code == 200
        data = response.json()
        assert data["success"] is True
        assert "Hello from IPython" in data["output"].get("text", "")

    def test_execute_with_return_value(self, api_url):
        """Test executing code with return value"""
        response = requests.post(
            f"{api_url}/ipython/exec", json={"code": "1 + 2 + 3", "timeout": 30}
        )
        assert response.status_code == 200
        data = response.json()
        assert data["success"] is True
        assert "6" in data["output"].get("text", "")

    def test_kernel_persistence(self, api_url):
        """Test that kernel state persists between calls"""
        # Define variable
        response = requests.post(
            f"{api_url}/ipython/exec", json={"code": "x = 42", "timeout": 30}
        )
        assert response.status_code == 200
        assert response.json()["success"] is True

        # Use variable
        response = requests.post(
            f"{api_url}/ipython/exec", json={"code": "print(f'x = {x}')", "timeout": 30}
        )
        assert response.status_code == 200
        data = response.json()
        assert data["success"] is True
        assert "x = 42" in data["output"].get("text", "")

    def test_kernel_status(self, api_url):
        """Test kernel status endpoint"""
        # First execute something to ensure kernel exists
        requests.post(f"{api_url}/ipython/exec", json={"code": "1+1", "timeout": 30})

        response = requests.get(f"{api_url}/ipython/kernel/status")
        assert response.status_code == 200
        data = response.json()
        assert data["status"] in ["alive", "not_started"]
        assert "workspace" in data

    def test_execute_with_error(self, api_url):
        """Test executing code that produces an error"""
        response = requests.post(
            f"{api_url}/ipython/exec",
            json={"code": "undefined_variable", "timeout": 30},
        )
        assert response.status_code == 200
        data = response.json()
        assert data["success"] is False
        assert "NameError" in data["error"]

    def test_kernel_restart(self, api_url):
        """Test kernel restart endpoint"""
        # First set a variable
        response = requests.post(
            f"{api_url}/ipython/exec",
            json={"code": "test_var = 'before_restart'", "timeout": 30},
        )
        assert response.status_code == 200

        # Restart kernel
        response = requests.post(f"{api_url}/ipython/kernel/restart")
        assert response.status_code == 200
        assert response.json()["success"] is True

        # Verify variable is gone (new kernel)
        response = requests.post(
            f"{api_url}/ipython/exec", json={"code": "test_var", "timeout": 30}
        )
        assert response.status_code == 200
        data = response.json()
        assert data["success"] is False
        assert "NameError" in data["error"]


class TestUploadDownloadAPI:
    """Test upload/download API endpoints"""

    def test_upload_file(self, api_url):
        """Test file upload"""
        files = {"file": ("upload_test.txt", b"Uploaded content", "text/plain")}
        response = requests.post(
            f"{api_url}/fs/upload", files=files, data={"file_path": "uploaded_file.txt"}
        )
        assert response.status_code == 200
        data = response.json()
        assert data["success"] is True
        assert data["size"] == len(b"Uploaded content")

    def test_download_file(self, api_url):
        """Test file download"""
        # Create a file first
        requests.post(
            f"{api_url}/fs/create_file",
            json={"path": "download_test.txt", "content": "Download me!"},
        )

        response = requests.get(
            f"{api_url}/fs/download", params={"file_path": "download_test.txt"}
        )
        assert response.status_code == 200
        assert response.content == b"Download me!"


class TestCrossComponentIntegration:
    """Test integration between components"""

    def test_python_creates_shell_reads(self, api_url):
        """Test Python creating a file that shell can read"""
        # Create file with Python
        response = requests.post(
            f"{api_url}/ipython/exec",
            json={
                "code": "open('py_created.txt', 'w').write('From Python')",
                "timeout": 30,
            },
        )
        assert response.status_code == 200
        assert response.json()["success"] is True

        # Read with shell
        response = requests.post(
            f"{api_url}/shell/exec",
            json={"command": "cat py_created.txt", "timeout": 10},
        )
        assert response.status_code == 200
        data = response.json()
        assert data["success"] is True
        assert "From Python" in data["stdout"]

    def test_shell_creates_filesystem_reads(self, api_url):
        """Test shell creating a file that filesystem API can read"""
        # Create file with shell
        response = requests.post(
            f"{api_url}/shell/exec",
            json={"command": 'echo "From Shell" > shell_created.txt', "timeout": 10},
        )
        assert response.status_code == 200

        # Read with filesystem API
        response = requests.post(
            f"{api_url}/fs/read_file", json={"path": "shell_created.txt"}
        )
        assert response.status_code == 200
        assert "From Shell" in response.json()["content"]
