"""E2E-05: File upload and download (part of filesystem capability) tests.

Purpose: Verify binary file upload/download to/from sandbox.
"""

from __future__ import annotations

import httpx
import pytest

from .conftest import AUTH_HEADERS, BAY_BASE_URL, DEFAULT_PROFILE, e2e_skipif_marks

pytestmark = e2e_skipif_marks


class TestE2E05FileUploadDownload:
    """E2E-05: File upload and download (part of filesystem capability)."""

    async def test_upload_and_download_text_file(self):
        """Upload a text file and download it back."""
        async with httpx.AsyncClient(base_url=BAY_BASE_URL, headers=AUTH_HEADERS) as client:
            # Create sandbox
            create_response = await client.post(
                "/v1/sandboxes",
                json={"profile": DEFAULT_PROFILE},
            )
            assert create_response.status_code == 201
            sandbox_id = create_response.json()["id"]
            
            try:
                # Upload a text file
                file_content = b"Hello, World!\nThis is a test file."
                file_path = "test_upload.txt"
                
                upload_response = await client.post(
                    f"/v1/sandboxes/{sandbox_id}/filesystem/upload",
                    files={"file": ("test_upload.txt", file_content, "text/plain")},
                    data={"path": file_path},
                    timeout=120.0,
                )
                
                assert upload_response.status_code == 200, f"Upload failed: {upload_response.text}"
                upload_result = upload_response.json()
                assert upload_result["status"] == "ok"
                assert upload_result["path"] == file_path
                assert upload_result["size"] == len(file_content)
                
                # Download the file
                download_response = await client.get(
                    f"/v1/sandboxes/{sandbox_id}/filesystem/download",
                    params={"path": file_path},
                    timeout=30.0,
                )
                
                assert download_response.status_code == 200, f"Download failed: {download_response.text}"
                assert download_response.content == file_content
                
            finally:
                await client.delete(f"/v1/sandboxes/{sandbox_id}")

    async def test_upload_and_download_binary_file(self):
        """Upload a binary file and download it back."""
        async with httpx.AsyncClient(base_url=BAY_BASE_URL, headers=AUTH_HEADERS) as client:
            # Create sandbox
            create_response = await client.post(
                "/v1/sandboxes",
                json={"profile": DEFAULT_PROFILE},
            )
            assert create_response.status_code == 201
            sandbox_id = create_response.json()["id"]
            
            try:
                # Upload a binary file (simulated PNG header + random bytes)
                binary_content = bytes([0x89, 0x50, 0x4E, 0x47, 0x0D, 0x0A, 0x1A, 0x0A]) + bytes(range(256))
                file_path = "test_binary.bin"
                
                upload_response = await client.post(
                    f"/v1/sandboxes/{sandbox_id}/filesystem/upload",
                    files={"file": ("test_binary.bin", binary_content, "application/octet-stream")},
                    data={"path": file_path},
                    timeout=120.0,
                )
                
                assert upload_response.status_code == 200, f"Upload failed: {upload_response.text}"
                
                # Download and verify
                download_response = await client.get(
                    f"/v1/sandboxes/{sandbox_id}/filesystem/download",
                    params={"path": file_path},
                    timeout=30.0,
                )
                
                assert download_response.status_code == 200
                assert download_response.content == binary_content
                
            finally:
                await client.delete(f"/v1/sandboxes/{sandbox_id}")

    async def test_upload_to_nested_path(self):
        """Upload a file to a nested directory path."""
        async with httpx.AsyncClient(base_url=BAY_BASE_URL, headers=AUTH_HEADERS) as client:
            # Create sandbox
            create_response = await client.post(
                "/v1/sandboxes",
                json={"profile": DEFAULT_PROFILE},
            )
            assert create_response.status_code == 201
            sandbox_id = create_response.json()["id"]
            
            try:
                # Upload to nested path
                file_content = b"Nested file content"
                file_path = "subdir/nested/test_file.txt"
                
                upload_response = await client.post(
                    f"/v1/sandboxes/{sandbox_id}/filesystem/upload",
                    files={"file": ("test_file.txt", file_content, "text/plain")},
                    data={"path": file_path},
                    timeout=120.0,
                )
                
                assert upload_response.status_code == 200, f"Upload failed: {upload_response.text}"
                
                # Download and verify
                download_response = await client.get(
                    f"/v1/sandboxes/{sandbox_id}/filesystem/download",
                    params={"path": file_path},
                    timeout=30.0,
                )
                
                assert download_response.status_code == 200
                assert download_response.content == file_content
                
            finally:
                await client.delete(f"/v1/sandboxes/{sandbox_id}")

    async def test_download_nonexistent_file(self):
        """Download of non-existent file should return 404."""
        async with httpx.AsyncClient(base_url=BAY_BASE_URL, headers=AUTH_HEADERS) as client:
            # Create sandbox
            create_response = await client.post(
                "/v1/sandboxes",
                json={"profile": DEFAULT_PROFILE},
            )
            assert create_response.status_code == 201
            sandbox_id = create_response.json()["id"]
            
            try:
                # Try to download non-existent file
                download_response = await client.get(
                    f"/v1/sandboxes/{sandbox_id}/filesystem/download",
                    params={"path": "nonexistent_file.txt"},
                    timeout=120.0,  # First download triggers session creation
                )
                
                # Should return 404 for file not found
                assert download_response.status_code == 404, \
                    f"Expected 404 for nonexistent file, got: {download_response.status_code}"
                
                # Verify error response format
                error_body = download_response.json()
                assert "error" in error_body
                assert error_body["error"]["code"] == "file_not_found"
                
            finally:
                await client.delete(f"/v1/sandboxes/{sandbox_id}")
