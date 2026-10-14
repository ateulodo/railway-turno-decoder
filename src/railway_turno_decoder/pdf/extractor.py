"""Single-open text/vector extraction, followed by pure structural parsers."""

from dataclasses import dataclass
from hashlib import sha256
from pathlib import Path

from railway_turno_decoder.config import DEFAULT_MAX_PDF_ANALYSIS_SECONDS, DEFAULT_MAX_PDF_PAGES
from railway_turno_decoder.models import DocumentInfo, PageInfo
from railway_turno_decoder.parser.page_classifier import classify_page
from railway_turno_decoder.parser.row_detector import detect_rows
from railway_turno_decoder.pdf.geometry import drawing, pdf_to_original_matrix, transform_bbox
from railway_turno_decoder.pdf.reader import _check_budget, open_pdf
from railway_turno_decoder.raw_models import RawDocument, RawPage, RawRow, RawWord


@dataclass
class ExtractionResult:
    document: DocumentInfo
    raw: RawDocument
    warnings: list[str]
    deadline: float

    def check_budget(self):
        _check_budget(self.deadline)


def extract_pdf(
    path: Path,
    *,
    max_pages=DEFAULT_MAX_PDF_PAGES,
    max_analysis_seconds=DEFAULT_MAX_PDF_ANALYSIS_SECONDS,
) -> ExtractionResult:
    with open_pdf(path, max_pages=max_pages, max_analysis_seconds=max_analysis_seconds) as (
        pdf,
        deadline,
        warnings,
    ):
        digest = sha256()
        size = 0
        with path.open("rb") as source:
            while chunk := source.read(65536):
                _check_budget(deadline)
                digest.update(chunk)
                size += len(chunk)
        pages = []
        infos = []
        for index in range(pdf.page_count):
            _check_budget(deadline)
            page = pdf.load_page(index)
            pdf_to_original = pdf_to_original_matrix(page)
            rotation = page.rotation_matrix
            native_words = page.get_text("words", sort=False)
            _check_budget(deadline)
            words = [
                RawWord(
                    word_index=i,
                    text=w[4],
                    original_bbox=w[:4],
                    display_bbox=transform_bbox(w[:4], rotation),
                    block_number=w[5],
                    line_number=w[6],
                    word_number=w[7],
                )
                for i, w in enumerate(native_words)
            ]
            _check_budget(deadline)
            native_drawings = page.get_drawings()
            _check_budget(deadline)
            drawings = []
            for i, value in enumerate(native_drawings):
                _check_budget(deadline)
                drawings.append(drawing(value, i, rotation))
            raw_page = RawPage(
                page_number=index + 1,
                page_index=index,
                width=page.rect.width,
                height=page.rect.height,
                rotation=page.rotation,
                mediabox=tuple(page.mediabox),
                cropbox=tuple(page.cropbox),
                original_bounds=transform_bbox(page.rect, page.derotation_matrix),
                display_bounds=tuple(page.rect),
                rotation_matrix=tuple(rotation),
                derotation_matrix=tuple(page.derotation_matrix),
                pdf_to_original_matrix=tuple(pdf_to_original),
                words=words,
                drawings=drawings,
            )
            raw_page.page_type, raw_page.classification_reasons = classify_page(raw_page)
            candidates, raw_page.warnings = detect_rows(raw_page)
            raw_page.rows = [
                RawRow(
                    row_index=i,
                    original_bbox=transform_bbox(candidate.display_bbox, page.derotation_matrix),
                    display_bbox=candidate.display_bbox,
                    periodicity_marker=candidate.marker,
                    service_number=candidate.service_number,
                    provenance=candidate.provenance,
                    detection_reasons=candidate.reasons,
                )
                for i, candidate in enumerate(candidates)
            ]
            _check_budget(deadline)
            pages.append(raw_page)
            infos.append(
                PageInfo(
                    page_number=index + 1,
                    width_points=page.rect.width,
                    height_points=page.rect.height,
                    rotation_degrees=page.rotation,
                    has_extractable_text=bool(words),
                )
            )
        has_text = any(info.has_extractable_text for info in infos)
        if not has_text:
            warnings.append("Nessun testo estraibile rilevato. Non viene eseguito alcun OCR.")
        document = DocumentInfo(
            page_count=pdf.page_count,
            metadata=pdf.metadata or {},
            pages=infos,
            has_extractable_text=has_text,
        )
        rotations = {page.rotation for page in pages}
        raw = RawDocument(
            sha256=digest.hexdigest(),
            source_size_bytes=size,
            page_count=len(pages),
            metadata=pdf.metadata or {},
            uniform_rotation=next(iter(rotations)) if len(rotations) == 1 else None,
            pages=pages,
        )
        _check_budget(deadline)
        return ExtractionResult(document, raw, warnings, deadline)
