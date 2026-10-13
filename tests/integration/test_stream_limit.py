import asyncio
from pathlib import Path
from tempfile import SpooledTemporaryFile

import pytest
from fastapi import FastAPI
from starlette.types import Message

from railway_turno_decoder.config import Settings
from railway_turno_decoder.main import create_app
from railway_turno_decoder.web.middleware import MULTIPART_OVERHEAD


@pytest.mark.parametrize("declared_length", [None, b"1"])
@pytest.mark.parametrize("failure", ["overflow", "disconnect"])
def test_stream_failure_closes_disk_spooled_upload(
    settings: Settings,
    declared_length: bytes | None,
    failure: str,
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    app: FastAPI = create_app(settings.model_copy(update={"max_upload_size": 128}))
    # A direct ASGI transport proves unread trailing chunks are never consumed.
    chunks = [
        b'--test\r\nContent-Disposition: form-data; name="file"; filename="test.pdf"'
        b"\r\nContent-Type: application/pdf\r\n\r\n" + b"x" * 64,
        b"x" * (128 + MULTIPART_OVERHEAD) if failure == "overflow" else None,
        b"never read\r\n--test--\r\n",
    ]
    consumed = 0
    messages: list[Message] = []
    opened = []

    def tracked_spool(*args: object, **kwargs: object) -> SpooledTemporaryFile:
        # Force real disk spooling inside the repository's pytest directory.
        kwargs.update(max_size=1, dir=tmp_path)
        handle = SpooledTemporaryFile(*args, **kwargs)
        opened.append(handle)
        return handle

    monkeypatch.setattr("starlette.formparsers.SpooledTemporaryFile", tracked_spool)

    async def receive() -> Message:
        nonlocal consumed
        body = chunks[consumed]
        consumed += 1
        if body is None:
            return {"type": "http.disconnect"}
        return {"type": "http.request", "body": body, "more_body": consumed < len(chunks)}

    async def send(message: Message) -> None:
        messages.append(message)

    headers = [(b"content-type", b"multipart/form-data; boundary=test")]
    if declared_length:
        headers.append((b"content-length", declared_length))
    scope = {
        "type": "http",
        "asgi": {"version": "3.0"},
        "http_version": "1.1",
        "method": "POST",
        "scheme": "http",
        "path": "/jobs",
        "raw_path": b"/jobs",
        "query_string": b"",
        "root_path": "",
        "headers": headers,
        "client": ("127.0.0.1", 12345),
        "server": ("testserver", 80),
    }
    asyncio.run(app(scope, receive, send))
    assert consumed == 2
    assert messages[0]["status"] == (413 if failure == "overflow" else 400)
    assert len(opened) == 1 and opened[0]._rolled and opened[0].closed
    assert not (settings.data_dir / "jobs").exists()
