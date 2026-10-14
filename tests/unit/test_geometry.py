"""Synthetic PDFs only: geometry, classification and structural segmentation."""

import json

import pymupdf
import pytest
from pydantic import ValidationError

from railway_turno_decoder.errors import PDFInspectionError
from railway_turno_decoder.parser.page_classifier import classify_page
from railway_turno_decoder.pdf.extractor import extract_pdf
from railway_turno_decoder.pdf.geometry import native_json, pdf_to_original_matrix, primitive
from railway_turno_decoder.raw_models import RawDocument, RawPage


def duty_page(pdf, markers=("LMXGVS", "D"), numbers=("7", "7")):
    page = pdf.new_page(width=600, height=800)
    page.insert_text((15, 22), "TURNO: synthetic PROFILO: fixture")
    for i, marker in enumerate(markers):
        y = 100 + i * 150
        page.insert_text((10, y), marker)
        page.insert_text((10, y + 100), numbers[i])
        page.insert_text((150, y + 60), "alpha 23 beta 24")
        page.insert_text((430, y + 80), "Lav Cct Km Not Rip")
        page.draw_line((120, y + 60), (320, y + 60), width=0.7, dashes="[3 2] 0")
        page.draw_line((320, y + 60), (400, y + 60), width=0.7)
    return page


@pytest.fixture
def duty_path(tmp_path):
    path = tmp_path / "duty.pdf"
    with pymupdf.open() as pdf:
        duty_page(pdf)
        pdf.save(path)
    return path


@pytest.mark.parametrize("rotation", [0, 90, 180, 270])
@pytest.mark.parametrize("cropped", [False, True])
def test_word_coordinates_and_raw_order(tmp_path, rotation, cropped):
    path = tmp_path / "coords.pdf"
    with pymupdf.open() as pdf:
        page = pdf.new_page(width=400, height=500)
        page.insert_text((100, 130), "second")
        page.insert_text((80, 90), "first")  # deliberately not display sorted
        if cropped:
            page.set_cropbox(pymupdf.Rect(40, 50, 360, 450))
        page.set_rotation(rotation)
        pdf.save(path)
    result = extract_pdf(path)
    raw = result.raw.pages[0]
    assert [word.text for word in raw.words] == ["second", "first"]
    with pymupdf.open(path) as pdf:
        page = pdf[0]
        assert raw.cropbox == tuple(page.cropbox)
        assert raw.rotation_matrix == tuple(page.rotation_matrix)
        expected_pdf_matrix = (1, 0, 0, -1, -40, 450) if cropped else (1, 0, 0, -1, 0, 500)
        assert raw.pdf_to_original_matrix == pytest.approx(expected_pdf_matrix)
        width, height = page.cropbox.width, page.cropbox.height
        for word, native in zip(raw.words, page.get_text("words", sort=False), strict=True):
            x0, y0, x1, y1 = native[:4]
            expected = {
                0: (x0, y0, x1, y1),
                90: (height - y1, x0, height - y0, x1),
                180: (width - x1, height - y1, width - x0, height - y0),
                270: (y0, width - x1, y1, width - x0),
            }[rotation]
            assert word.original_bbox == pytest.approx(native[:4])
            assert word.display_bbox == pytest.approx(expected, abs=1e-4)
            assert tuple(pymupdf.Rect(word.display_bbox) * page.derotation_matrix) == pytest.approx(
                word.original_bbox, abs=1e-4
            )
    assert RawDocument.model_validate_json(result.raw.model_dump_json()) == result.raw
    assert result.raw.uniform_rotation == rotation


def test_vector_styles_and_native_primitives(tmp_path):
    path = tmp_path / "vectors.pdf"
    with pymupdf.open() as pdf:
        page = pdf.new_page(width=400, height=500)
        shape = page.new_shape()
        shape.draw_line((20, 30), (80, 30))
        shape.draw_bezier((30, 50), (60, 40), (70, 90), (100, 70))
        shape.draw_rect((100, 110, 150, 160))
        shape.draw_quad(pymupdf.Quad((160, 100), (190, 110), (150, 140), (180, 150)))
        shape.finish(
            width=2,
            color=(1, 0, 0),
            fill=(0, 1, 0),
            dashes="[3 2] 1",
            lineCap=1,
            lineJoin=2,
            stroke_opacity=0.7,
            fill_opacity=0.3,
        )
        shape.commit()
        page.set_rotation(90)
        pdf.save(path)
    page = extract_pdf(path).raw.pages[0]
    kinds = {item.kind for drawing in page.drawings for item in drawing.items}
    assert {"l", "c", "re"} <= kinds
    vector = next(d for d in page.drawings if d.width is not None)
    filled = next(d for d in page.drawings if d.fill_color is not None)
    assert vector.width == 2
    assert vector.stroke_color == [1, 0, 0]
    assert filled.fill_color == [0, 1, 0]
    assert vector.stroke_opacity == pytest.approx(0.7)
    assert filled.fill_opacity == pytest.approx(0.3)
    assert "3 2" in vector.dashes
    assert vector.seqno is not None
    assert vector.line_cap == [1, 1, 1]
    assert vector.line_join == 2
    assert vector.original_bbox != vector.display_bbox
    assert json.loads(page.model_dump_json())["drawings"][0]["items"]


def test_quad_serialization_and_zero_width_line():
    matrix = pymupdf.Matrix(0, 1, -1, 0, 500, 0)
    quad = pymupdf.Quad((10, 20), (40, 20), (10, 60), (40, 60))
    item = primitive(("qu", quad), 0, matrix)
    assert item.original_arguments[0]["kind"] == "quad"
    assert len(item.display_points) == 4
    line = primitive(("l", pymupdf.Point(10, 20), pymupdf.Point(10, 60)), 0, matrix)
    assert line.original_bbox == (10, 20, 10, 60)
    assert line.display_bbox == (440, 10, 480, 10)
    with pytest.raises(TypeError):
        native_json(object())


@pytest.mark.parametrize(
    ("text", "expected"),
    [
        ("PLANNING: PROFILO: TURNO: DATA PUBBLICAZIONE:", "INDEX"),
        ("Note sulla periodicita' dei treni del turno", "TRAIN_PERIODICITY_NOTES"),
        ("Note sulla periodicità dei treni del turno", "TRAIN_PERIODICITY_NOTES"),
        ("LMXGVS D random text", "UNKNOWN"),
        ("TURNO: PROFILO: Lav Cct Km Not Rip", "UNKNOWN"),
        ("ordinary text", "UNKNOWN"),
    ],
)
def test_content_classification(tmp_path, text, expected):
    path = tmp_path / "classify.pdf"
    with pymupdf.open() as pdf:
        pdf.new_page().insert_text((15, 25), text, fontsize=10)
        pdf.save(path)
    raw = extract_pdf(path).raw.pages[0]
    assert raw.page_type == expected
    assert raw.classification_reasons
    assert RawPage.model_validate_json(raw.model_dump_json()) == raw


def test_same_number_variants_and_provenance(duty_path):
    raw = extract_pdf(duty_path).raw.pages[0]
    assert raw.page_type == "DUTY_DIAGRAM"
    assert [(r.service_number, r.periodicity_marker) for r in raw.rows] == [
        ("7", "LMXGVS"),
        ("7", "D"),
    ]
    first, second = raw.rows
    assert first.display_bbox[3] == second.display_bbox[1]
    assert set(first.provenance.word_indices).isdisjoint(second.provenance.word_indices)
    for row in raw.rows:
        assert row.periodicity_marker in [
            raw.words[i].text for i in row.provenance.anchor_word_indices
        ]
        assert row.provenance.drawing_item_refs
        for ref in row.provenance.drawing_item_refs:
            assert raw.drawings[ref.drawing_index].items[ref.item_index].kind == "l"
    RawPage.model_validate(raw.model_dump())
    broken = raw.model_dump()
    broken["rows"][0]["provenance"]["word_indices"] = [10000]
    with pytest.raises(ValidationError):
        RawPage.model_validate(broken)


def test_single_marker_and_ambiguous_number(tmp_path):
    path = tmp_path / "single.pdf"
    with pymupdf.open() as pdf:
        page = duty_page(pdf, ("S",), ("9",))
        page.insert_text((10, 220), "8")
        pdf.save(path)
    raw = extract_pdf(path).raw.pages[0]
    assert len(raw.rows) == 1
    assert raw.rows[0].periodicity_marker == "S"
    assert raw.rows[0].service_number is None
    assert any("Ambiguous" in warning for warning in raw.warnings)


def test_no_geometry_support_does_not_invent_row(tmp_path):
    path = tmp_path / "isolated.pdf"
    with pymupdf.open() as pdf:
        page = duty_page(pdf, ("S", "D"), ("9", "9"))
        page.insert_text((10, 500), "LMX")
        pdf.save(path)
    raw = extract_pdf(path).raw.pages[0]
    assert len(raw.rows) == 2
    assert any("without horizontal" in warning for warning in raw.warnings)


@pytest.mark.parametrize("native_method", ["get_text", "get_drawings"])
def test_budget_after_native_calls(tmp_path, pdf_bytes, monkeypatch, native_method):
    path = tmp_path / "budget.pdf"
    path.write_bytes(pdf_bytes)
    now = 0.0
    original = getattr(pymupdf.Page, native_method)
    calls = []

    def expensive(page, *args, **kwargs):
        nonlocal now
        result = original(page, *args, **kwargs)
        calls.append(page.number)
        now = 1.0
        return result

    monkeypatch.setattr("railway_turno_decoder.pdf.reader.monotonic", lambda: now)
    monkeypatch.setattr(pymupdf.Page, native_method, expensive)
    with pytest.raises(PDFInspectionError, match="budget"):
        extract_pdf(path, max_analysis_seconds=1)
    assert calls == [0]
    monkeypatch.setattr(pymupdf.Page, native_method, original)
    now = 0.0
    assert extract_pdf(path).raw.page_count == 2


def test_page_limit_before_extraction(tmp_path, pdf_bytes, monkeypatch):
    path = tmp_path / "limit.pdf"
    path.write_bytes(pdf_bytes)

    def forbidden(*args, **kwargs):
        pytest.fail("must reject before loading pages")

    monkeypatch.setattr(pymupdf.Document, "load_page", forbidden)
    with pytest.raises(PDFInspectionError, match="limite"):
        extract_pdf(path, max_pages=1)


def test_hash_and_no_local_paths(duty_path):
    from hashlib import sha256

    result = extract_pdf(duty_path)
    assert result.raw.sha256 == sha256(duty_path.read_bytes()).hexdigest()
    assert result.raw.source_size_bytes == duty_path.stat().st_size
    assert str(duty_path.parent) not in result.raw.model_dump_json()
    assert result.raw.summary().row_count == 2


def test_near_overlapping_tokens_stay_distinct(tmp_path):
    path = tmp_path / "overlap.pdf"
    with pymupdf.open() as pdf:
        page = pdf.new_page()
        page.insert_text((100, 100), "23", fontsize=5)
        page.insert_text((100.3, 105), "24", fontsize=5)
        pdf.save(path)
    words = extract_pdf(path).raw.pages[0].words
    assert [word.text for word in words] == ["23", "24"]
    assert [word.word_index for word in words] == [0, 1]
    assert words[0].display_bbox != words[1].display_bbox
    assert pymupdf.Rect(words[0].display_bbox).intersects(pymupdf.Rect(words[1].display_bbox))


@pytest.mark.parametrize("rotation", [0, 90, 180, 270])
def test_structural_rows_use_display_space(tmp_path, rotation):
    path = tmp_path / "rotated-duty.pdf"
    with pymupdf.open() as source, pymupdf.open() as pdf:
        duty_page(source)
        width, height = (600, 800) if rotation in (0, 180) else (800, 600)
        page = pdf.new_page(width=width, height=height)
        page.show_pdf_page(page.rect, source, 0, rotate=rotation)
        page.set_rotation(rotation)
        pdf.save(path)
    raw = extract_pdf(path).raw.pages[0]
    assert raw.rotation == rotation
    assert raw.width == 600 and raw.height == 800
    assert raw.page_type == "DUTY_DIAGRAM"
    assert [(r.service_number, r.periodicity_marker) for r in raw.rows] == [
        ("7", "LMXGVS"),
        ("7", "D"),
    ]


@pytest.mark.parametrize(
    ("text", "y"),
    [
        ("footnote sulla periodicita dei treni del turno", 25),
        ("Note sulla periodicita dei treni del turno", 500),
    ],
)
def test_notes_heading_requires_whole_words_and_header_layout(tmp_path, text, y):
    path = tmp_path / "body.pdf"
    with pymupdf.open() as pdf:
        pdf.new_page().insert_text((15, y), text, fontsize=10)
        pdf.save(path)
    assert extract_pdf(path).raw.pages[0].page_type == "UNKNOWN"


@pytest.mark.parametrize(
    ("rotation", "expected_display"),
    [
        (0, (60, 70)),
        (90, (330, 60)),
        (180, (260, 330)),
        (270, (70, 260)),
    ],
)
def test_pdf_native_matrix_contract_round_trip_and_composition(
    tmp_path, rotation, expected_display
):
    path = tmp_path / "native-space.pdf"
    with pymupdf.open() as pdf:
        page = pdf.new_page(width=400, height=500)
        page.draw_line((100, 120), (150, 120))
        # Known PDF user-space coordinates, independent of the property under test.
        assert b"100 380 m" in pdf.xref_stream(page.get_contents()[0])
        page.set_cropbox(pymupdf.Rect(40, 50, 360, 450))
        page.set_rotation(rotation)
        pdf.save(path)
    before = path.read_bytes()
    raw = extract_pdf(path).raw.pages[0]
    matrix = pymupdf.Matrix(raw.pdf_to_original_matrix)
    display_matrix = pymupdf.Matrix(raw.rotation_matrix)
    native = pymupdf.Point(100, 380)
    original = native * matrix
    assert tuple(original) == pytest.approx((60, 70), abs=1e-4)
    assert tuple(original) == pytest.approx(raw.drawings[0].items[0].original_points[0], abs=1e-4)
    assert tuple(original * ~matrix) == pytest.approx(tuple(native), abs=1e-4)
    assert tuple(original * display_matrix) == pytest.approx(expected_display, abs=1e-4)
    assert tuple(native * (matrix * display_matrix)) == pytest.approx(expected_display, abs=1e-4)
    assert raw.rotation == rotation
    assert raw.mediabox == (0, 0, 400, 500) and raw.cropbox == (40, 50, 360, 450)
    assert path.read_bytes() == before


def test_native_matrix_restores_rotation_on_failure(monkeypatch):
    with pymupdf.open() as pdf:
        page = pdf.new_page()
        page.set_rotation(270)

        def fail(page):
            assert page.rotation == 0
            raise RuntimeError("synthetic property failure")

        monkeypatch.setattr(pymupdf.Page, "transformation_matrix", property(fail))
        with pytest.raises(RuntimeError, match="synthetic property failure"):
            pdf_to_original_matrix(page)
        assert page.rotation == 270


@pytest.mark.parametrize("rotation", [0, 90, 180, 270])
def test_notes_heading_uses_display_layout_not_emission_order(tmp_path, rotation):
    path = tmp_path / "notes-layout.pdf"
    tokens = ["Note", "SULLA", "periodicità", "DEI", "treni", "del", "turno"]
    orders = [list(range(7)), list(reversed(range(7))), [3, 1, 6, 0, 5, 2, 4]]
    positions = []
    x = 20
    for i, token in enumerate(tokens):
        positions.append((x, 40 + (i % 3 - 1) * 0.7))  # Small normal vertical jitter.
        x += pymupdf.get_text_length(token, fontsize=10) + 4
    with pymupdf.open() as source, pymupdf.open() as pdf:
        for order in orders:
            page = source.new_page(width=600, height=800)
            for i in order:
                page.insert_text(positions[i], tokens[i], fontsize=10)
        # Complete the source before grafting; PyMuPDF caches its object map.
        for page_index in range(len(orders)):
            width, height = (600, 800) if rotation in (0, 180) else (800, 600)
            target = pdf.new_page(width=width, height=height)
            target.show_pdf_page(target.rect, source, page_index, rotate=rotation)
            target.set_rotation(rotation)
        pdf.save(path)
    pages = extract_pdf(path).raw.pages
    with pymupdf.open(path) as pdf:
        for page, native in zip(pages, pdf, strict=True):
            assert [w.text for w in page.words] == [
                w[4] for w in native.get_text("words", sort=False)
            ]
    assert [w.text for w in pages[0].words] != [w.text for w in pages[1].words]
    for page in pages:
        assert page.page_type == "TRAIN_PERIODICITY_NOTES"
        reason = page.classification_reasons[0]
        assert [page.words[i].text for i in reason.word_indices] == tokens
        snapshot = page.model_dump_json()
        assert classify_page(page)[0] == "TRAIN_PERIODICITY_NOTES"
        assert page.model_dump_json() == snapshot
    assert {w.text: w.display_bbox for w in pages[0].words} == {
        w.text: w.display_bbox for w in pages[1].words
    }


@pytest.mark.parametrize("scattered", ["horizontal", "vertical"])
def test_notes_words_scattered_in_header_are_unknown(tmp_path, scattered):
    path = tmp_path / "scattered.pdf"
    tokens = ["Note", "sulla", "periodicita'", "dei", "treni", "del", "turno"]
    with pymupdf.open() as pdf:
        page = pdf.new_page(width=600, height=800)
        for i, token in enumerate(tokens):
            point = (20 + 80 * i, 40) if scattered == "horizontal" else (20, 30 + 15 * i)
            page.insert_text(point, token, fontsize=10)
        pdf.save(path)
    page = extract_pdf(path).raw.pages[0]
    assert [w.text for w in page.words] == tokens
    assert len(page.words) == 7
    assert page.page_type == "UNKNOWN"


def varied_markers_pdf(path, anchors):
    with pymupdf.open() as pdf:
        page = pdf.new_page(width=600, height=800)
        page.insert_text((15, 22), "TURNO: synthetic PROFILO: fixture", fontsize=10)
        page.insert_text((420, 50), "Lav Cct Km Not Rip", fontsize=10)
        for index, (marker, baseline, font_size) in enumerate(anchors):
            page.insert_text((10, baseline), marker, fontsize=font_size)
            box = next(w[:4] for w in page.get_text("words") if w[4] == marker)
            center = (box[1] + box[3]) / 2
            page.draw_line((120, center), (400, center))
            page.insert_text((150, baseline), f"token{index}", fontsize=font_size)
        pdf.save(path)


@pytest.mark.parametrize(
    "anchors",
    [
        [("S", 110, 10), ("D", 175, 50)],
        [("S", 110, 10), ("D", 125, 10)],
        [("S", 110, 10), ("D", 175, 50), ("LMX", 320, 15)],
        [("S", 110, 20), ("D", 200, 10), ("LMX", 300, 30)],
    ],
)
def test_row_bands_remain_positive_ordered_and_anchored(tmp_path, anchors):
    path = tmp_path / "varied-markers.pdf"
    varied_markers_pdf(path, anchors)
    page = extract_pdf(path).raw.pages[0]
    assert page.page_type == "DUTY_DIAGRAM"
    assert len(page.rows) == len(anchors)
    assert page.warnings == []
    for index, row in enumerate(page.rows):
        assert row.periodicity_marker == anchors[index][0]
        top, bottom = row.display_bbox[1], row.display_bbox[3]
        assert 0 <= top < bottom <= page.height * 0.95
        marker = page.words[row.provenance.anchor_word_indices[0]]
        assert marker.word_index in row.provenance.word_indices
        assert top <= marker.display_bbox[1] < marker.display_bbox[3] <= bottom
        texts = [page.words[i].text for i in row.provenance.word_indices]
        assert f"token{index}" in texts
        assert not any(f"token{other}" in texts for other in range(len(anchors)) if other != index)
        refs = row.provenance.drawing_item_refs
        assert len(refs) == 1
        item = page.drawings[refs[0].drawing_index].items[refs[0].item_index]
        assert item.kind == "l"
        assert top <= item.display_points[0][1] < bottom
        if index:
            previous = page.rows[index - 1]
            assert previous.display_bbox[1] < top
            assert previous.display_bbox[3] == top
    first = page.words[page.rows[0].provenance.anchor_word_indices[0]].display_bbox
    assert page.rows[0].display_bbox[1] == pytest.approx(
        max(page.height * 0.04, first[1] - (first[3] - first[1]) / 2)
    )
    assert (
        page.rows[-1].display_bbox[3]
        > page.words[page.rows[-1].provenance.anchor_word_indices[0]].display_bbox[3]
    )
    RawPage.model_validate_json(page.model_dump_json())


def test_overlapping_markers_reject_ambiguous_layout_explicitly(tmp_path):
    path = tmp_path / "ambiguous-anchors.pdf"
    varied_markers_pdf(path, [("S", 110, 10), ("D", 115, 10)])
    page = extract_pdf(path).raw.pages[0]
    assert page.page_type == "DUTY_DIAGRAM"
    assert page.rows == []
    assert any("Ambiguous gutter marker layout" in warning for warning in page.warnings)
    assert not any("without horizontal geometry" in warning for warning in page.warnings)
    assert {"S", "D"} <= {w.text for w in page.words}


def test_anchor_past_footer_limit_is_explicit_layout_ambiguity(tmp_path):
    path = tmp_path / "footer-anchor.pdf"
    with pymupdf.open() as pdf:
        page = pdf.new_page(width=600, height=800)
        page.insert_text((15, 22), "TURNO: synthetic PROFILO: fixture", fontsize=10)
        page.insert_text((420, 50), "Lav Cct Km Not Rip", fontsize=10)
        page.insert_text((10, 100), "S", fontsize=10)
        page.insert_text((10, 770), "D", fontsize=15)
        for y in (90, 120):
            page.draw_line((120, y), (400, y))
        pdf.save(path)
    page = extract_pdf(path).raw.pages[0]
    assert page.page_type == "DUTY_DIAGRAM"
    assert page.rows == []
    assert any("out-of-bounds anchors" in warning for warning in page.warnings)
    assert not any("without horizontal geometry" in warning for warning in page.warnings)
