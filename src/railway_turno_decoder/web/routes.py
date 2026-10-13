"""HTTP transport and template responses; no PDF interpretation here."""

import logging
from uuid import UUID

from fastapi import APIRouter, Request
from starlette.concurrency import run_in_threadpool
from starlette.datastructures import UploadFile
from starlette.exceptions import HTTPException
from starlette.requests import ClientDisconnect
from starlette.responses import RedirectResponse, Response

from railway_turno_decoder.errors import UserError
from railway_turno_decoder.jobs import JobService
from railway_turno_decoder.web.views import home_response

logger = logging.getLogger(__name__)
router = APIRouter()


@router.get("/")
async def index(request: Request) -> Response:
    return home_response(request)


@router.get("/health")
async def health() -> dict[str, str]:
    return {"status": "ok"}


@router.post("/jobs")
async def create_job(request: Request) -> Response:
    if request.headers.get("content-type", "").split(";", 1)[0].lower() != "multipart/form-data":
        raise UserError("Seleziona un PDF usando il modulo di caricamento.", 415)
    service: JobService = request.app.state.job_service
    try:
        async with request.form(max_files=1, max_fields=0) as form:
            upload = form.get("file")
            if not isinstance(upload, UploadFile) or not upload.filename:
                raise UserError("Seleziona un file PDF.", 400)
            # Extension is an initial filter only. PyMuPDF validates the actual content.
            if not upload.filename.lower().endswith(".pdf"):
                raise UserError("Sono ammessi soltanto file con estensione .pdf.", 415)
            if upload.size is not None and upload.size > service.settings.max_upload_size:
                raise UserError("Il PDF supera la dimensione massima consentita.", 413)
            job, status_code = await run_in_threadpool(
                service.process, upload.file, upload.filename
            )
    except HTTPException as exc:
        if getattr(request.state, "upload_limit_exceeded", False):
            raise UserError("Il caricamento supera la dimensione massima consentita.", 413) from exc
        raise UserError("Caricamento non valido: invia un solo PDF nel campo file.", 400) from exc
    except ClientDisconnect as exc:
        raise UserError("Il caricamento è stato interrotto. Riprova.", 400) from exc
    except OSError as exc:
        logger.exception("Temporary upload storage failed")
        raise UserError("Impossibile ricevere il PDF. Verifica spazio e permessi.", 503) from exc
    if status_code != 200:
        return request.app.state.templates.TemplateResponse(
            request=request, name="job.html", context={"job": job}, status_code=status_code
        )
    # HTMX keeps the upload form responsive, then navigates to a durable job URL.
    location = f"/jobs/{job.job_id}"
    if request.headers.get("HX-Request") == "true":
        return Response(status_code=200, headers={"HX-Redirect": location})
    return RedirectResponse(url=location, status_code=303)


@router.get("/jobs/{job_id}")
async def job_detail(request: Request, job_id: str) -> Response:
    try:
        parsed_id = UUID(job_id)
        if str(parsed_id) != job_id:
            raise ValueError
    except ValueError as exc:
        raise UserError("Job non trovato.", 404) from exc
    service: JobService = request.app.state.job_service
    job = await run_in_threadpool(service.store.load, parsed_id)
    return request.app.state.templates.TemplateResponse(
        request=request, name="job.html", context={"job": job}
    )
