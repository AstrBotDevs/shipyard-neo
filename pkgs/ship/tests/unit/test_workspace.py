"""
Unit tests for workspace module.
"""

import pytest
from pathlib import Path
from unittest.mock import patch
from fastapi import HTTPException


# Mark all tests in this module as unit tests
pytestmark = pytest.mark.unit


class TestResolvePathUnit:
    """Test resolve_path function in isolation (mocking WORKSPACE_ROOT)"""

    def test_resolve_relative_path(self, tmp_path):
        """Test resolving a relative path within workspace"""
        with patch("app.workspace.WORKSPACE_ROOT", tmp_path):
            from app.workspace import resolve_path

            # Create a test file
            (tmp_path / "test.txt").touch()

            result = resolve_path("test.txt")
            assert result == tmp_path / "test.txt"

    def test_resolve_nested_path(self, tmp_path):
        """Test resolving nested relative path"""
        with patch("app.workspace.WORKSPACE_ROOT", tmp_path):
            from app.workspace import resolve_path

            # Create nested directory
            (tmp_path / "subdir").mkdir()
            (tmp_path / "subdir" / "file.txt").touch()

            result = resolve_path("subdir/file.txt")
            assert result == tmp_path / "subdir" / "file.txt"

    def test_resolve_absolute_path_within_workspace(self, tmp_path):
        """Test resolving absolute path that is within workspace"""
        with patch("app.workspace.WORKSPACE_ROOT", tmp_path):
            from app.workspace import resolve_path

            (tmp_path / "test.txt").touch()

            result = resolve_path(str(tmp_path / "test.txt"))
            assert result == tmp_path / "test.txt"

    def test_reject_path_outside_workspace(self, tmp_path):
        """Test that paths outside workspace are rejected"""
        with patch("app.workspace.WORKSPACE_ROOT", tmp_path):
            from app.workspace import resolve_path

            with pytest.raises(HTTPException) as exc_info:
                resolve_path("/etc/passwd")

            assert exc_info.value.status_code == 403
            assert "Access denied" in exc_info.value.detail

    def test_reject_path_traversal(self, tmp_path):
        """Test that path traversal attacks are rejected"""
        with patch("app.workspace.WORKSPACE_ROOT", tmp_path):
            from app.workspace import resolve_path

            with pytest.raises(HTTPException) as exc_info:
                resolve_path("../../../etc/passwd")

            assert exc_info.value.status_code == 403

    def test_resolve_dot_path(self, tmp_path):
        """Test resolving current directory path"""
        with patch("app.workspace.WORKSPACE_ROOT", tmp_path):
            from app.workspace import resolve_path

            result = resolve_path(".")
            assert result == tmp_path


class TestGetWorkspaceDir:
    """Test get_workspace_dir function"""

    def test_creates_workspace_if_not_exists(self, tmp_path):
        """Test that workspace directory is created if it doesn't exist"""
        workspace = tmp_path / "workspace"
        with patch("app.workspace.WORKSPACE_ROOT", workspace):
            from app.workspace import get_workspace_dir

            result = get_workspace_dir()
            assert result == workspace
            assert workspace.exists()

    def test_returns_existing_workspace(self, tmp_path):
        """Test that existing workspace directory is returned"""
        workspace = tmp_path / "workspace"
        workspace.mkdir()

        with patch("app.workspace.WORKSPACE_ROOT", workspace):
            from app.workspace import get_workspace_dir

            result = get_workspace_dir()
            assert result == workspace
