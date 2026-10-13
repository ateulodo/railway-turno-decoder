"""Shared template rendering for routes and error responses."""

from fastapi import Request
from starlette.responses import Response


def home_response(request: Request, error: str | None = None, status_code: int = 200) -> Response:
    return request.app.state.templates.TemplateResponse(
        request=request,
        name="index.html",
        context={"settings": request.app.state.settings, "error": error},
        status_code=status_code,
    )
