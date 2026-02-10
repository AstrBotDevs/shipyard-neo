"""
Unit tests for user_manager module (command execution).
"""

import pytest
from unittest.mock import patch, MagicMock, AsyncMock
from pathlib import Path


# Mark all tests in this module as unit tests
pytestmark = pytest.mark.unit


class TestBackgroundProcessRegistry:
    """Test background process management"""

    def test_generate_process_id(self):
        """Test process ID generation"""
        from app.components.user_manager import generate_process_id

        pid1 = generate_process_id()
        pid2 = generate_process_id()

        # Should be 8 characters
        assert len(pid1) == 8
        assert len(pid2) == 8
        # Should be unique
        assert pid1 != pid2

    def test_register_and_get_processes(self):
        """Test registering and retrieving background processes"""
        from app.components.user_manager import (
            register_background_process,
            get_background_processes,
            _background_processes,
        )

        # Clear existing processes
        _background_processes.clear()

        # Create a mock process
        mock_process = MagicMock()
        mock_process.returncode = None
        mock_process.pid = 12345

        register_background_process(
            process_id="test1234",
            pid=12345,
            command="sleep 10",
            process=mock_process,
        )

        processes = get_background_processes()
        assert len(processes) == 1
        assert processes[0]["process_id"] == "test1234"
        assert processes[0]["pid"] == 12345
        assert processes[0]["command"] == "sleep 10"
        assert processes[0]["status"] == "running"

        # Clean up
        _background_processes.clear()

    def test_cleanup_completed_processes(self):
        """Test that completed processes are automatically cleaned up"""
        from app.components.user_manager import (
            register_background_process,
            get_background_processes,
            _background_processes,
        )

        # Clear existing processes
        _background_processes.clear()

        # Create mock processes: 2 running, 1 completed, 1 failed
        running_process1 = MagicMock()
        running_process1.returncode = None
        running_process1.pid = 1001

        running_process2 = MagicMock()
        running_process2.returncode = None
        running_process2.pid = 1002

        completed_process = MagicMock()
        completed_process.returncode = 0  # completed
        completed_process.pid = 1003

        failed_process = MagicMock()
        failed_process.returncode = 1  # failed
        failed_process.pid = 1004

        register_background_process("running1", 1001, "sleep 100", running_process1)
        register_background_process("running2", 1002, "sleep 200", running_process2)
        register_background_process("completed", 1003, "echo done", completed_process)
        register_background_process("failed", 1004, "false", failed_process)

        # Before cleanup, should have 4 entries
        assert len(_background_processes) == 4

        # Call get_background_processes which triggers cleanup
        processes = get_background_processes()

        # After cleanup, only 2 running processes should remain
        assert len(processes) == 2
        assert len(_background_processes) == 2

        # Verify only running processes remain
        process_ids = [p["process_id"] for p in processes]
        assert "running1" in process_ids
        assert "running2" in process_ids
        assert "completed" not in process_ids
        assert "failed" not in process_ids

        # Clean up
        _background_processes.clear()

    def test_cleanup_all_completed(self):
        """Test cleanup when all processes are completed"""
        from app.components.user_manager import (
            register_background_process,
            get_background_processes,
            _background_processes,
        )

        # Clear existing processes
        _background_processes.clear()

        # Create only completed processes
        for i in range(3):
            mock_process = MagicMock()
            mock_process.returncode = 0
            mock_process.pid = 2000 + i
            register_background_process(f"done{i}", 2000 + i, f"echo {i}", mock_process)

        # Before cleanup
        assert len(_background_processes) == 3

        # Call get_background_processes which triggers cleanup
        processes = get_background_processes()

        # All should be cleaned up
        assert len(processes) == 0
        assert len(_background_processes) == 0

    def test_no_cleanup_when_all_running(self):
        """Test no cleanup when all processes are still running"""
        from app.components.user_manager import (
            register_background_process,
            get_background_processes,
            _background_processes,
        )

        # Clear existing processes
        _background_processes.clear()

        # Create only running processes
        for i in range(3):
            mock_process = MagicMock()
            mock_process.returncode = None  # still running
            mock_process.pid = 3000 + i
            register_background_process(f"run{i}", 3000 + i, f"sleep {i}", mock_process)

        # Call get_background_processes
        processes = get_background_processes()

        # All should remain
        assert len(processes) == 3
        assert len(_background_processes) == 3

        # Clean up
        _background_processes.clear()


class TestProcessResult:
    """Test ProcessResult dataclass"""

    def test_success_result(self):
        """Test creating a successful result"""
        from app.components.user_manager import ProcessResult

        result = ProcessResult(
            success=True,
            stdout="Hello, World!",
            stderr="",
            return_code=0,
            pid=123,
        )

        assert result.success is True
        assert result.stdout == "Hello, World!"
        assert result.stderr == ""
        assert result.return_code == 0

    def test_failure_result(self):
        """Test creating a failure result"""
        from app.components.user_manager import ProcessResult

        result = ProcessResult(
            success=False,
            stdout="",
            stderr="Error occurred",
            return_code=1,
            error="Command failed",
        )

        assert result.success is False
        assert result.error == "Command failed"
        assert result.return_code == 1


class TestBackgroundProcessEntry:
    """Test BackgroundProcessEntry class"""

    def test_status_running(self):
        """Test status when process is running"""
        from app.components.user_manager import BackgroundProcessEntry

        mock_process = MagicMock()
        mock_process.returncode = None

        entry = BackgroundProcessEntry(
            process_id="test1234",
            pid=123,
            command="sleep 10",
            process=mock_process,
        )

        assert entry.status == "running"

    def test_status_completed(self):
        """Test status when process completed successfully"""
        from app.components.user_manager import BackgroundProcessEntry

        mock_process = MagicMock()
        mock_process.returncode = 0

        entry = BackgroundProcessEntry(
            process_id="test1234",
            pid=123,
            command="echo hello",
            process=mock_process,
        )

        assert entry.status == "completed"

    def test_status_failed(self):
        """Test status when process failed"""
        from app.components.user_manager import BackgroundProcessEntry

        mock_process = MagicMock()
        mock_process.returncode = 1

        entry = BackgroundProcessEntry(
            process_id="test1234",
            pid=123,
            command="false",
            process=mock_process,
        )

        assert entry.status == "failed"
