"""Application factory and Uvicorn entry point."""

from pathlib import Path

from fastapi import FastAPI, Request
from starlette.exceptions import HTTPException
from starlette.responses import Response
from starlette.staticfiles import StaticFiles
from starlette.templating import Jinja2Templates

from railway_turno_decoder import __version__
from railway_turno_decoder.config import Settings
from railway_turno_decoder.errors import UserError
from railway_turno_decoder.jobs import JobService
from railway_turno_decoder.web.middleware import UploadBodyLimitMiddleware
from railway_turno_decoder.web.routes import router
from railway_turno_decoder.web.views import home_response

WEB_DIR = Path(__file__).parent / "web"


def create_app(settings: Settings | None = None) -> FastAPI:
    settings = settings or Settings.from_env()
    app = FastAPI(title="Railway Turno Decoder", version=__version__)
    app.state.settings = settings
    app.state.job_service = JobService(settings)
    app.state.templates = Jinja2Templates(directory=WEB_DIR / "templates")
    app.add_middleware(UploadBodyLimitMiddleware, max_upload_size=settings.max_upload_size)
    app.mount("/static", StaticFiles(directory=WEB_DIR / "static"), name="static")
    app.include_router(router)

    @app.exception_handler(UserError)
    async def user_error(request: Request, exc: UserError) -> Response:
        return home_response(request, exc.message, exc.status_code)

    @app.exception_handler(HTTPException)
    async def http_error(request: Request, exc: HTTPException) -> Response:
        message = "Pagina non trovata." if exc.status_code == 404 else "Richiesta non valida."
        return home_response(request, message, exc.status_code)

    return app


app = create_app()
