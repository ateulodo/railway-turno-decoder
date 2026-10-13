"""Filesystem job storage and the synchronous inspection lifecycle."""

import logging
from datetime import UTC, datetime
from pathlib import Path
from typing import BinaryIO
from uuid import UUID, uuid4

from pydantic import ValidationError

from railway_turno_decoder.config import Settings
from railway_turno_decoder.errors import PDFInspectionError, StorageError, UserError
from railway_turno_decoder.models import Job, JobStatus
from railway_turno_decoder.pdf.reader import inspect_pdf

logger = logging.getLogger(__name__)
COPY_CHUNK_SIZE = 64 * 1024


class JobStore:
    def __init__(self, data_dir: Path) -> None:
        self.root = data_dir.resolve() / "jobs"

    def directory(self, job_id: UUID) -> Path:
        # Only UUID objects can reach filesystem path construction.
        if not isinstance(job_id, UUID):
            raise TypeError("job_id must be a UUID")
        directory = self.root / str(job_id)
        if directory.is_symlink():
            raise StorageError()
        return directory

    def create(self, original_filename: str) -> Job:
        job = Job(job_id=uuid4(), original_filename=original_filename)
        directory = self.directory(job.job_id)
        try:
            self.root.mkdir(parents=True, exist_ok=True)
            directory.mkdir(mode=0o700)
        except OSError as exc:
            logger.exception("Cannot create job directory")
            raise StorageError() from exc
        try:
            self.save(job)
        except Exception:
            # mkdir succeeded exclusively for this UUID. rmdir removes only an
            # empty directory and never follows symlinks or deletes its contents.
            try:
                directory.rmdir()
            except OSError:
                logger.exception("Cannot remove new empty job directory %s", job.job_id)
            raise
        return job

    def save(self, job: Job) -> None:
        # Revalidation prevents writing an inconsistent completed/failed status.
        validated = Job.model_validate(job.model_dump())
        directory = self.directory(job.job_id)
        temporary = directory / "job.json.tmp"
        try:
            with temporary.open("w", encoding="utf-8") as handle:
                handle.write(validated.model_dump_json(indent=2))
                handle.write("\n")
            temporary.replace(directory / "job.json")
        except OSError as exc:
            logger.exception("Cannot save job %s", job.job_id)
            raise StorageError() from exc
        finally:
            try:
                temporary.unlink(missing_ok=True)
            except OSError:
                logger.exception("Cannot remove temporary JSON for job %s", job.job_id)

    def load(self, job_id: UUID) -> Job:
        try:
            path = self.directory(job_id) / "job.json"
            if path.is_symlink():
                raise StorageError()
            job = Job.model_validate_json(path.read_text(encoding="utf-8"))
            if job.job_id != job_id:
                raise StorageError()
            return job
        except FileNotFoundError as exc:
            raise UserError("Job non trovato.", 404) from exc
        except (OSError, ValidationError, UnicodeError) as exc:
            logger.exception("Cannot load job %s", job_id)
            raise StorageError() from exc


class JobService:
    def __init__(self, settings: Settings) -> None:
        self.settings = settings
        self.store = JobStore(settings.data_dir)

    def process(self, upload: BinaryIO, original_filename: str) -> tuple[Job, int]:
        """Copy a bounded stream, inspect it and persist all state transitions."""
        job = self.store.create(original_filename)
        directory = self.store.directory(job.job_id)
        temporary = directory / "source.pdf.part"
        job.status = JobStatus.PROCESSING
        job.updated_at = datetime.now(UTC)
        try:
            self.store.save(job)
            job.source_size_bytes = self._copy_source(upload, temporary)
            temporary.replace(directory / "source.pdf")
            job.document, job.warnings = inspect_pdf(
                directory / "source.pdf",
                max_pages=self.settings.max_pdf_pages,
                max_analysis_seconds=self.settings.max_pdf_analysis_seconds,
            )
            job.status = JobStatus.COMPLETED
            job.updated_at = datetime.now(UTC)
            self.store.save(job)
            logger.info("Completed PDF inspection for job %s", job.job_id)
            return job, 200
        except PDFInspectionError as exc:
            return self._fail(job, str(exc), 422)
        except UserError as exc:
            return self._fail(job, exc.message, exc.status_code)
        except OSError:
            logger.exception("Source write failed for job %s", job.job_id)
            return self._fail(job, "Impossibile salvare il PDF. Verifica spazio e permessi.", 503)
        except Exception:
            logger.exception("Unexpected inspection failure for job %s", job.job_id)
            return self._fail(job, "Errore inatteso durante l'analisi del PDF.", 500)
        finally:
            try:
                temporary.unlink(missing_ok=True)
            except OSError:
                logger.exception("Cannot remove partial source for job %s", job.job_id)

    def _copy_source(self, upload: BinaryIO, destination: Path) -> int:
        size = 0
        with destination.open("xb") as handle:
            while chunk := upload.read(COPY_CHUNK_SIZE):
                size += len(chunk)
                if size > self.settings.max_upload_size:
                    raise UserError("Il PDF supera la dimensione massima consentita.", 413)
                handle.write(chunk)
        if size == 0:
            raise PDFInspectionError("Il file caricato è vuoto.")
        return size

    def _fail(self, job: Job, message: str, status_code: int) -> tuple[Job, int]:
        job.status = JobStatus.FAILED
        job.document = None
        job.errors = [message]
        job.updated_at = datetime.now(UTC)
        # If persistence also fails, return a storage error, never a success page.
        self.store.save(job)
        logger.warning("PDF inspection failed for job %s (HTTP %s)", job.job_id, status_code)
        return job, status_code
