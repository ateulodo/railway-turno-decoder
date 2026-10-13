"""Bound the HTTP body before multipart parsing, even without Content-Length."""

from starlette.datastructures import Headers
from starlette.formparsers import MultiPartException
from starlette.requests import Request
from starlette.types import ASGIApp, Message, Receive, Scope, Send

from railway_turno_decoder.web.views import home_response

# File bytes have their own exact limit; this is bounded multipart header overhead.
MULTIPART_OVERHEAD = 64 * 1024


class UploadBodyLimitMiddleware:
    def __init__(self, app: ASGIApp, max_upload_size: int) -> None:
        self.app = app
        self.max_body_size = max_upload_size + MULTIPART_OVERHEAD

    async def __call__(self, scope: Scope, receive: Receive, send: Send) -> None:
        if scope["type"] != "http" or scope["method"] != "POST":
            await self.app(scope, receive, send)
            return
        content_length = Headers(scope=scope).get("content-length")
        if content_length:
            try:
                size = int(content_length)
                if size < 0:
                    raise ValueError
            except ValueError:
                response = home_response(Request(scope), "Content-Length non valido.", 400)
                await response(scope, receive, send)
                return
            if size > self.max_body_size:
                response = home_response(
                    Request(scope), "Il caricamento supera la dimensione massima consentita.", 413
                )
                await response(scope, receive, send)
                return
        received = 0

        async def limited_receive() -> Message:
            nonlocal received
            message = await receive()
            if message["type"] == "http.request":
                received += len(message.get("body", b""))
                if received > self.max_body_size:
                    scope.setdefault("state", {})["upload_limit_exceeded"] = True
                    # Starlette closes partially spooled files on MultiPartException.
                    raise MultiPartException("Upload body limit exceeded")
            return message

        await self.app(scope, limited_receive, send)
