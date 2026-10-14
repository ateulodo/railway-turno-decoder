"""Bounded on-demand page rendering; shares the native PDF lock."""

import math
from pathlib import Path
from time import monotonic

import pymupdf

from railway_turno_decoder.errors import PDFInspectionError, UserError
from railway_turno_decoder.pdf.reader import _PDF_LOCK, _check_budget

MIN_RENDER_SCALE = 0.25
MAX_RENDER_SCALE = 2.0
MAX_RENDER_PIXELS = 8_000_000


def render_page(
    path: Path, page_index: int, scale: float, *, max_pages: int, max_analysis_seconds: float
) -> bytes:
    if not math.isfinite(scale) or not MIN_RENDER_SCALE <= scale <= MAX_RENDER_SCALE:
        raise UserError("Scala di rendering non valida (0.25–2).", 422)
    if path.is_symlink():
        raise UserError("Sorgente non disponibile.", 404)
    with _PDF_LOCK:
        deadline = monotonic() + max_analysis_seconds
        try:
            with pymupdf.open(path) as pdf:
                _check_budget(deadline)
                if (
                    not pdf.is_pdf
                    or pdf.needs_pass
                    or pdf.page_count > max_pages
                    or not 0 <= page_index < pdf.page_count
                ):
                    raise UserError("Pagina non disponibile.", 404)
                page = pdf.load_page(page_index)
                # Ceil matches pixmap integer bounds for a page origin at (0,0).
                pixels = math.ceil(page.rect.width * scale) * math.ceil(page.rect.height * scale)
                if pixels > MAX_RENDER_PIXELS:
                    raise UserError("La pagina supera il limite di pixel consentito.", 422)
                _check_budget(deadline)
                pixmap = page.get_pixmap(
                    matrix=pymupdf.Matrix(scale, scale),
                    alpha=False,
                    colorspace=pymupdf.csRGB,
                    annots=False,
                )
                _check_budget(deadline)
                result = pixmap.tobytes("png")
                _check_budget(deadline)
                return result
        except (OSError, pymupdf.FileDataError, RuntimeError, ValueError) as exc:
            raise PDFInspectionError("Impossibile renderizzare la pagina PDF.") from exc
