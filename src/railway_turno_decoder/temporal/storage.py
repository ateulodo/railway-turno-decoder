"""Atomic derived page cache, keyed by raw generation and parser/tolerances."""

import logging
from hashlib import sha256
from pathlib import Path
from time import monotonic
from uuid import uuid4

from railway_turno_decoder.errors import StorageError, UserError
from railway_turno_decoder.jobs import JobStore
from railway_turno_decoder.models import Job, JobStatus
from railway_turno_decoder.raw_models import RawPage
from railway_turno_decoder.temporal.analysis import analyze_page
from railway_turno_decoder.temporal.models import TemporalPage
from railway_turno_decoder.temporal.tolerances import ALGORITHM_VERSION, TemporalTolerances
from railway_turno_decoder.temporal.validation import validate_against_raw

logger = logging.getLogger(__name__)


class TemporalStore:
    def __init__(self, store: JobStore, tolerances: TemporalTolerances | None = None):
        self.store = store
        self.tolerances = tolerances or TemporalTolerances()
        self.cache_key = sha256(
            (ALGORITHM_VERSION + self.tolerances.model_dump_json()).encode()
        ).hexdigest()[:16]

    def path(self, job: Job, page_index: int) -> Path:
        if (
            job.status != JobStatus.COMPLETED
            or job.raw_summary is None
            or not 0 <= page_index < len(job.raw_summary.pages)
        ):
            raise UserError("Pagina temporale non disponibile.", 404)
        root = self.store.directory(job.job_id)
        parts = [root / "temporal", root / "temporal" / str(job.raw_summary.extraction_id)]
        parts.append(parts[-1] / self.cache_key)
        path = parts[-1] / f"{page_index}.json"
        if any(p.is_symlink() for p in [*parts, path]):
            raise StorageError()
        return path

    def get_or_analyze(
        self, job: Job, page: RawPage, *, max_analysis_seconds: float = 60
    ) -> TemporalPage:
        if page.page_type != "DUTY_DIAGRAM":
            raise UserError("La pagina non è un diagramma temporale.", 404)
        path = self.path(job, page.page_index)
        deadline = monotonic() + max_analysis_seconds

        def check():
            if monotonic() >= deadline:
                raise UserError("La pagina supera il budget temporale di analisi.", 422)

        temporary = path.parent / f".temporal-{uuid4()}.tmp"
        try:
            if path.exists():
                cached = TemporalPage.model_validate_json(path.read_text(encoding="utf-8"))
                if (
                    cached.source_extraction_id != job.raw_summary.extraction_id
                    or cached.source_sha256 != job.raw_summary.sha256
                    or cached.page_index != page.page_index
                    or cached.algorithm_version != ALGORITHM_VERSION
                    or cached.tolerances != self.tolerances
                ):
                    raise StorageError()
                validate_against_raw(cached, page, check)
                return cached
            result = analyze_page(
                page,
                source_extraction_id=job.raw_summary.extraction_id,
                source_sha256=job.raw_summary.sha256,
                tolerances=self.tolerances,
                check_budget=check,
            )
            validate_against_raw(result, page, check)
            path.parent.mkdir(parents=True, exist_ok=True)
            # Recheck path components after creation; browser input never supplies them.
            self.path(job, page.page_index)
            with temporary.open("x", encoding="utf-8") as handle:
                handle.write(result.model_dump_json())
            check()
            temporary.replace(path)
            return result
        except (OSError, ValueError) as exc:
            logger.exception("Cannot persist/read temporal page for job %s", job.job_id)
            raise StorageError() from exc
        finally:
            try:
                temporary.unlink(missing_ok=True)
            except OSError:
                logger.exception("Cannot clean temporal temporary for job %s", job.job_id)
