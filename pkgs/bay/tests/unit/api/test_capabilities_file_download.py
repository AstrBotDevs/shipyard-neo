"""Regression tests for file download response headers."""

from email.message import Message
from types import SimpleNamespace
from unittest.mock import AsyncMock

import pytest

import app.api.v1.capabilities as caps_mod


@pytest.mark.asyncio
@pytest.mark.parametrize(
    "filename",
    [
        "download.txt",
        "测试.png",
        "report-😀.txt",
        "café.txt",
        "100% complete.txt",
        'report"; size="0.txt',
        "report\r\nX-Injected: value.txt",
    ],
)
async def test_download_preserves_filename_and_binary_content(monkeypatch, filename):
    content = bytes(range(256))
    sandbox = SimpleNamespace(id="sbx-1")
    sandbox_mgr = object()
    download = AsyncMock(return_value=content)
    monkeypatch.setattr(
        caps_mod,
        "CapabilityRouter",
        lambda _mgr: SimpleNamespace(download_file=download),
    )
    path = f"crops/{filename}"

    response = await caps_mod.download_file(
        sandbox=sandbox,
        sandbox_mgr=sandbox_mgr,
        path=path,
    )

    download.assert_awaited_once_with(sandbox=sandbox, path=path)
    assert response.status_code == 200
    assert response.body == content
    assert response.headers["content-type"] == "application/octet-stream"

    disposition = response.headers["content-disposition"]
    assert disposition.isascii()
    assert "\r" not in disposition
    assert "\n" not in disposition
    headers = Message()
    headers["Content-Disposition"] = disposition
    assert headers.get_content_disposition() == "attachment"
    assert headers.get_filename() == filename

    if filename == "download.txt":
        assert disposition == 'attachment; filename="download.txt"'
