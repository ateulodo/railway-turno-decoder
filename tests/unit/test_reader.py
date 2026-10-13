from pathlib import Path

import pymupdf
import pytest

from railway_turno_decoder.errors import PDFInspectionError
from railway_turno_decoder.pdf.reader import inspect_pdf


def test_document_structure(tmp_path: Path, pdf_bytes: bytes) -> None:
    path = tmp_path / "synthetic.pdf"
    path.write_bytes(pdf_bytes)
    document, warnings = inspect_pdf(path)
    assert document.page_count == 2
    assert document.metadata["title"] == "Synthetic roster"
    assert document.metadata["author"] == "pytest"
    assert document.has_extractable_text
    assert document.pages[0].has_extractable_text
    assert not document.pages[1].has_extractable_text
    assert document.pages[0].width_points == 595
    assert document.pages[0].height_points == 842
    assert document.pages[0].rotation_degrees == 0
    assert document.pages[1].width_points == 400
    assert document.pages[1].height_points == 300
    assert document.pages[1].rotation_degrees == 90
    assert warnings == []


def test_no_text_warning(tmp_path: Path) -> None:
    path = tmp_path / "blank.pdf"
    with pymupdf.open() as pdf:
        pdf.new_page()
        pdf.save(path)
    document, warnings = inspect_pdf(path)
    assert not document.has_extractable_text
    assert len(warnings) == 1
    assert "OCR" in warnings[0]


def test_password_protected_pdf(tmp_path: Path) -> None:
    path = tmp_path / "encrypted.pdf"
    with pymupdf.open() as pdf:
        pdf.new_page()
        pdf.save(path, encryption=pymupdf.PDF_ENCRYPT_AES_256, user_pw="secret", owner_pw="owner")
    with pytest.raises(PDFInspectionError, match="password"):
        inspect_pdf(path)


def test_supported_image_disguised_as_pdf_is_rejected(tmp_path: Path) -> None:
    path = tmp_path / "disguised.pdf"
    pixmap = pymupdf.Pixmap(pymupdf.csRGB, pymupdf.IRect(0, 0, 1, 1))
    path.write_bytes(pixmap.tobytes("png"))
    with pytest.raises(PDFInspectionError, match="non è un documento PDF"):
        inspect_pdf(path)


def test_repaired_pdf_is_reported(tmp_path: Path, pdf_bytes: bytes) -> None:
    path = tmp_path / "repairable.pdf"
    # Keep the synthetic PDF objects but remove xref/trailer to trigger recovery.
    path.write_bytes(pdf_bytes[: pdf_bytes.rfind(b"\nxref")] + b"\n%%EOF\n")
    document, warnings = inspect_pdf(path)
    assert document.page_count == 2
    assert any("riparato" in warning for warning in warnings)


@pytest.mark.parametrize("content", [b"", b"not a PDF", b"%PDF-1.7\ncorrupt"])
def test_invalid_pdf(tmp_path: Path, content: bytes) -> None:
    path = tmp_path / "invalid.pdf"
    path.write_bytes(content)
    with pytest.raises(PDFInspectionError):
        inspect_pdf(path)


def test_page_limit_rejects_before_loading_pages(
    tmp_path: Path, pdf_bytes: bytes, monkeypatch: pytest.MonkeyPatch
) -> None:
    path = tmp_path / "two-pages.pdf"
    path.write_bytes(pdf_bytes)

    def forbidden_page_load(*args: object, **kwargs: object) -> None:
        pytest.fail("An oversized document must be rejected before loading any page")

    monkeypatch.setattr(pymupdf.Document, "load_page", forbidden_page_load)
    with pytest.raises(PDFInspectionError, match="limite consentito di 1 pagine"):
        inspect_pdf(path, max_pages=1)


def test_exact_page_limit_is_allowed(tmp_path: Path, pdf_bytes: bytes) -> None:
    path = tmp_path / "two-pages.pdf"
    path.write_bytes(pdf_bytes)
    document, _ = inspect_pdf(path, max_pages=2)
    assert document.page_count == 2


def test_default_allows_more_than_400_pages(tmp_path: Path) -> None:
    path = tmp_path / "many-pages.pdf"
    with pymupdf.open() as pdf:
        for _ in range(401):
            pdf.new_page()
        pdf.save(path)
    document, _ = inspect_pdf(path)
    assert document.page_count == 401


def test_cooperative_budget_stops_after_current_native_call(
    tmp_path: Path, pdf_bytes: bytes, monkeypatch: pytest.MonkeyPatch
) -> None:
    path = tmp_path / "two-pages.pdf"
    path.write_bytes(pdf_bytes)
    now = 0.0
    calls = []
    original_get_text = pymupdf.Page.get_text

    def expensive_get_text(page: pymupdf.Page, *args: object, **kwargs: object) -> str:
        nonlocal now
        text = original_get_text(page, *args, **kwargs)
        calls.append(page.number)
        now = 1.0  # Expire exactly at the deadline; no sleeps or timing races.
        return text

    monkeypatch.setattr("railway_turno_decoder.pdf.reader.monotonic", lambda: now)
    monkeypatch.setattr(pymupdf.Page, "get_text", expensive_get_text)
    with pytest.raises(PDFInspectionError, match="budget di tempo"):
        inspect_pdf(path, max_analysis_seconds=1)
    assert calls == [0]
    # The failed inspection released the lock and closed its document.
    now = 0.0
    monkeypatch.setattr(pymupdf.Page, "get_text", original_get_text)
    assert inspect_pdf(path, max_analysis_seconds=1)[0].page_count == 2


def test_cooperative_budget_checks_after_opening(
    tmp_path: Path, pdf_bytes: bytes, monkeypatch: pytest.MonkeyPatch
) -> None:
    path = tmp_path / "two-pages.pdf"
    path.write_bytes(pdf_bytes)
    now = 0.0
    original_open = pymupdf.open
    opened = []

    def expensive_open(*args: object, **kwargs: object) -> pymupdf.Document:
        nonlocal now
        document = original_open(*args, **kwargs)
        opened.append(document)
        now = 2.0
        return document

    monkeypatch.setattr("railway_turno_decoder.pdf.reader.monotonic", lambda: now)
    monkeypatch.setattr(pymupdf, "open", expensive_open)
    with pytest.raises(PDFInspectionError, match="budget di tempo"):
        inspect_pdf(path, max_analysis_seconds=1)
    assert opened[0].is_closed
