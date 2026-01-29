"""E2E-06: Filesystem operations (read/write/list/delete) tests.

Purpose: Verify text file read/write/list/delete via RESTful API.
"""

from __future__ import annotations

import httpx
import pytest

from .conftest import AUTH_HEADERS, BAY_BASE_URL, DEFAULT_PROFILE, e2e_skipif_marks

pytestmark = e2e_skipif_marks


class TestE2E06Filesystem:
    """E2E-06: Filesystem operations (read/write/list/delete)."""

    async def test_write_and_read_file(self):
        """Write a file and read it back."""
        async with httpx.AsyncClient(base_url=BAY_BASE_URL, headers=AUTH_HEADERS) as client:
            # Create sandbox
            create_response = await client.post(
                "/v1/sandboxes",
                json={"profile": DEFAULT_PROFILE},
            )
            assert create_response.status_code == 201
            sandbox_id = create_response.json()["id"]
            
            try:
                # Write a file (PUT /filesystem/files)
                file_content = "Hello from E2E test!\nLine 2\nLine 3"
                file_path = "test_write_read.txt"
                
                write_response = await client.put(
                    f"/v1/sandboxes/{sandbox_id}/filesystem/files",
                    json={"path": file_path, "content": file_content},
                    timeout=120.0,
                )
                
                assert write_response.status_code == 200, f"Write failed: {write_response.text}"
                
                # Read it back (GET /filesystem/files?path=...)
                read_response = await client.get(
                    f"/v1/sandboxes/{sandbox_id}/filesystem/files",
                    params={"path": file_path},
                    timeout=30.0,
                )
                
                assert read_response.status_code == 200, f"Read failed: {read_response.text}"
                result = read_response.json()
                assert result["content"] == file_content
                
            finally:
                await client.delete(f"/v1/sandboxes/{sandbox_id}")

    async def test_list_directory(self):
        """List directory after creating files."""
        async with httpx.AsyncClient(base_url=BAY_BASE_URL, headers=AUTH_HEADERS) as client:
            # Create sandbox
            create_response = await client.post(
                "/v1/sandboxes",
                json={"profile": DEFAULT_PROFILE},
            )
            assert create_response.status_code == 201
            sandbox_id = create_response.json()["id"]
            
            try:
                # Write files first (PUT /filesystem/files)
                await client.put(
                    f"/v1/sandboxes/{sandbox_id}/filesystem/files",
                    json={"path": "file1.txt", "content": "content1"},
                    timeout=120.0,
                )
                await client.put(
                    f"/v1/sandboxes/{sandbox_id}/filesystem/files",
                    json={"path": "file2.py", "content": "print(1)"},
                    timeout=30.0,
                )
                
                # List directory (GET /filesystem/directories?path=.)
                list_response = await client.get(
                    f"/v1/sandboxes/{sandbox_id}/filesystem/directories",
                    params={"path": "."},
                    timeout=30.0,
                )
                
                assert list_response.status_code == 200, f"List failed: {list_response.text}"
                result = list_response.json()
                
                # Should contain the files we created
                entries = result.get("entries", [])
                names = [e.get("name") for e in entries]
                assert "file1.txt" in names, f"file1.txt not in {names}"
                assert "file2.py" in names, f"file2.py not in {names}"
                
            finally:
                await client.delete(f"/v1/sandboxes/{sandbox_id}")

    async def test_delete_file(self):
        """Delete a file and verify it's gone."""
        async with httpx.AsyncClient(base_url=BAY_BASE_URL, headers=AUTH_HEADERS) as client:
            # Create sandbox
            create_response = await client.post(
                "/v1/sandboxes",
                json={"profile": DEFAULT_PROFILE},
            )
            assert create_response.status_code == 201
            sandbox_id = create_response.json()["id"]
            
            try:
                # Write a file (PUT /filesystem/files)
                file_path = "to_delete.txt"
                await client.put(
                    f"/v1/sandboxes/{sandbox_id}/filesystem/files",
                    json={"path": file_path, "content": "will be deleted"},
                    timeout=120.0,
                )
                
                # Verify it exists (GET /filesystem/files?path=...)
                read_response = await client.get(
                    f"/v1/sandboxes/{sandbox_id}/filesystem/files",
                    params={"path": file_path},
                    timeout=30.0,
                )
                assert read_response.status_code == 200
                
                # Delete the file (DELETE /filesystem/files?path=...)
                delete_response = await client.delete(
                    f"/v1/sandboxes/{sandbox_id}/filesystem/files",
                    params={"path": file_path},
                    timeout=30.0,
                )
                assert delete_response.status_code == 200, f"Delete failed: {delete_response.text}"
                
                # Try to download - should fail (Ship may return error differently)
                download_response = await client.get(
                    f"/v1/sandboxes/{sandbox_id}/filesystem/download",
                    params={"path": file_path},
                    timeout=30.0,
                )
                # File should not exist
                assert download_response.status_code == 404, \
                    f"Expected 404 after delete, got: {download_response.status_code}"
                
            finally:
                await client.delete(f"/v1/sandboxes/{sandbox_id}")

    async def test_write_to_nested_directory(self):
        """Write file to nested directory path."""
        async with httpx.AsyncClient(base_url=BAY_BASE_URL, headers=AUTH_HEADERS) as client:
            # Create sandbox
            create_response = await client.post(
                "/v1/sandboxes",
                json={"profile": DEFAULT_PROFILE},
            )
            assert create_response.status_code == 201
            sandbox_id = create_response.json()["id"]
            
            try:
                # Write to nested path (PUT /filesystem/files)
                file_path = "subdir/deep/file.txt"
                file_content = "nested content"
                
                write_response = await client.put(
                    f"/v1/sandboxes/{sandbox_id}/filesystem/files",
                    json={"path": file_path, "content": file_content},
                    timeout=120.0,
                )
                
                assert write_response.status_code == 200, f"Write failed: {write_response.text}"
                
                # Read it back (GET /filesystem/files?path=...)
                read_response = await client.get(
                    f"/v1/sandboxes/{sandbox_id}/filesystem/files",
                    params={"path": file_path},
                    timeout=30.0,
                )
                
                assert read_response.status_code == 200
                assert read_response.json()["content"] == file_content
                
            finally:
                await client.delete(f"/v1/sandboxes/{sandbox_id}")
