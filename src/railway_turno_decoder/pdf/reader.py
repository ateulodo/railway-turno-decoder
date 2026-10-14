"""Shared native PDF lifetime, validation and cooperative analysis budget."""

from contextlib import contextmanager
from pathlib import Path
from threading import Lock
from time import monotonic

import pymupdf

from railway_turno_decoder.config import DEFAULT_MAX_PDF_ANALYSIS_SECONDS, DEFAULT_MAX_PDF_PAGES
from railway_turno_decoder.errors import PDFInspectionError
from railway_turno_decoder.models import DocumentInfo, PageInfo

# All native PDF operations, including rendering, share this process-wide lock.
_PDF_LOCK = Lock()


def _check_budget(deadline: float) -> None:
    if monotonic() >= deadline:
        raise PDFInspectionError("Il PDF supera il budget di tempo consentito per l'ispezione.")


@contextmanager
def open_pdf(path: Path, *, max_pages: int, max_analysis_seconds: float):
    """One native document lifetime. Lock wait is outside the cooperative budget."""
    with _PDF_LOCK:
        deadline = monotonic() + max_analysis_seconds
        try:
            with pymupdf.open(path) as pdf:
                _check_budget(deadline)
                if not pdf.is_pdf:
                    raise PDFInspectionError("Il contenuto caricato non è un documento PDF.")
                if pdf.needs_pass:
                    raise PDFInspectionError(
                        "Il PDF è protetto da password. Carica una copia senza protezione."
                    )
                if pdf.page_count == 0:
                    raise PDFInspectionError("Il PDF non contiene pagine.")
                if pdf.page_count > max_pages:
                    raise PDFInspectionError(
                        f"Il PDF supera il limite consentito di {max_pages} pagine."
                    )
                warnings = []
                if pdf.is_repaired:
                    warnings.append(
                        "PyMuPDF ha riparato la struttura del PDF durante l'apertura. "
                        "Verifica il documento originale."
                    )
                yield pdf, deadline, warnings
                _check_budget(deadline)
        except (pymupdf.FileDataError, pymupdf.EmptyFileError, RuntimeError, ValueError) as exc:
            raise PDFInspectionError("Il PDF è corrotto o non può essere letto.") from exc


def inspect_pdf(
    path: Path,
    *,
    max_pages: int = DEFAULT_MAX_PDF_PAGES,
    max_analysis_seconds: float = DEFAULT_MAX_PDF_ANALYSIS_SECONDS,
) -> tuple[DocumentInfo, list[str]]:
    """Lightweight metadata-only API retained for callers that do not need geometry."""
    with open_pdf(path, max_pages=max_pages, max_analysis_seconds=max_analysis_seconds) as (
        pdf,
        deadline,
        warnings,
    ):
        pages = []
        for index in range(pdf.page_count):
            _check_budget(deadline)
            page = pdf.load_page(index)
            info = PageInfo(
                page_number=index + 1,
                width_points=page.rect.width,
                height_points=page.rect.height,
                rotation_degrees=page.rotation,
                has_extractable_text=bool(page.get_text("text").strip()),
            )
            _check_budget(deadline)
            pages.append(info)
        has_text = any(page.has_extractable_text for page in pages)
        if not has_text:
            warnings.append("Nessun testo estraibile rilevato. Non viene eseguito alcun OCR.")
        document = DocumentInfo(
            page_count=pdf.page_count,
            metadata=pdf.metadata or {},
            has_extractable_text=has_text,
            pages=pages,
        )
        _check_budget(deadline)
        return document, warnings
