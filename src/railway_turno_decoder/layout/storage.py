"""Atomic page cache isolated by raw, M3A content and layout algorithm/config."""

import logging
from hashlib import sha256
from time import monotonic
from uuid import uuid4

from railway_turno_decoder.errors import StorageError, UserError
from railway_turno_decoder.layout.analysis import analyze_layout
from railway_turno_decoder.layout.identity import temporal_identity
from railway_turno_decoder.layout.models import LayoutPage
from railway_turno_decoder.layout.tolerances import ALGORITHM_VERSION, LayoutTolerances
from railway_turno_decoder.layout.validation import validate_layout
from railway_turno_decoder.models import JobStatus

logger = logging.getLogger(__name__)


class LayoutStore:
    def __init__(self, store, tolerances=None):
        self.store = store
        self.tolerances = tolerances or LayoutTolerances()
        self.cache_key = sha256(
            (ALGORITHM_VERSION + self.tolerances.model_dump_json()).encode()
        ).hexdigest()[:16]

    def path(self, job, page_index, temporal):
        if (
            job.status != JobStatus.COMPLETED
            or job.raw_summary is None
            or not 0 <= page_index < len(job.raw_summary.pages)
        ):
            raise UserError("Layout non disponibile.", 404)
        if (
            temporal.source_extraction_id != job.raw_summary.extraction_id
            or temporal.source_sha256 != job.raw_summary.sha256
            or temporal.page_index != page_index
        ):
            raise StorageError()
        root = self.store.directory(job.job_id)
        parts = [root / "layout"]
        for name in [
            str(job.raw_summary.extraction_id),
            temporal_identity(temporal),
            self.cache_key,
        ]:
            parts.append(parts[-1] / name)
        path = parts[-1] / f"{page_index}.json"
        if any(p.is_symlink() for p in [*parts, path]):
            raise StorageError()
        return path

    def get_or_analyze(self, job, page, temporal, *, max_analysis_seconds=60):
        if page.page_type != "DUTY_DIAGRAM":
            raise UserError("La pagina non è un diagramma.", 404)
        path = self.path(job, page.page_index, temporal)
        deadline = monotonic() + max_analysis_seconds

        def check():
            if monotonic() >= deadline:
                raise UserError("La pagina supera il budget di analisi layout.", 422)

        temporary = path.parent / f".layout-{uuid4()}.tmp"
        try:
            cache_hit = path.exists()
            if cache_hit:
                result = LayoutPage.model_validate_json(path.read_text(encoding="utf-8"))
                if (
                    result.algorithm_version != ALGORITHM_VERSION
                    or result.tolerances != self.tolerances
                ):
                    raise StorageError()
            else:
                result = analyze_layout(
                    page, temporal, tolerances=self.tolerances, check_budget=check
                )
            validate_layout(result, page, temporal, check)
            if not cache_hit:
                path.parent.mkdir(parents=True, exist_ok=True)
                self.path(job, page.page_index, temporal)
                with temporary.open("x", encoding="utf-8") as handle:
                    handle.write(result.model_dump_json())
                check()
                temporary.replace(path)
            return result
        except (OSError, ValueError) as exc:
            logger.exception("Cannot read/persist layout for job %s", job.job_id)
            raise StorageError() from exc
        finally:
            try:
                temporary.unlink(missing_ok=True)
            except OSError:
                logger.exception("Cannot clean layout temporary for job %s", job.job_id)
