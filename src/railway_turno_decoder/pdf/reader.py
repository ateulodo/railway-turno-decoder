"""Inspect PDF structure without interpreting its content."""

from pathlib import Path
from threading import Lock
from time import monotonic

import pymupdf

from railway_turno_decoder.config import DEFAULT_MAX_PDF_ANALYSIS_SECONDS, DEFAULT_MAX_PDF_PAGES
from railway_turno_decoder.errors import PDFInspectionError
from railway_turno_decoder.models import DocumentInfo, PageInfo

# MuPDF operations must not run concurrently within one process. The lock covers
# the entire document lifetime, including opening and closing it.
_PDF_LOCK = Lock()


def inspect_pdf(
    path: Path,
    *,
    max_pages: int = DEFAULT_MAX_PDF_PAGES,
    max_analysis_seconds: float = DEFAULT_MAX_PDF_ANALYSIS_SECONDS,
) -> tuple[DocumentInfo, list[str]]:
    with _PDF_LOCK:
        # Waiting for another inspection is not part of the analysis budget.
        deadline = monotonic() + max_analysis_seconds
        return _inspect_pdf(path, max_pages=max_pages, deadline=deadline)


def _check_budget(deadline: float) -> None:
    if monotonic() >= deadline:
        raise PDFInspectionError("Il PDF supera il budget di tempo consentito per l'ispezione.")


def _inspect_pdf(path: Path, *, max_pages: int, deadline: float) -> tuple[DocumentInfo, list[str]]:
    try:
        with pymupdf.open(path) as pdf:
            # Cooperative checks cannot interrupt a native call already running.
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
    except (pymupdf.FileDataError, pymupdf.EmptyFileError, RuntimeError, ValueError) as exc:
        raise PDFInspectionError("Il PDF è corrotto o non può essere letto.") from exc
