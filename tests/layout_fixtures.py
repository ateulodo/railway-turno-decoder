"""Independent synthetic geometry; no proprietary document or vocabulary."""

from uuid import UUID

import pymupdf

from railway_turno_decoder.raw_models import (
    DrawingItemRef,
    RawDrawing,
    RawPage,
    RawPrimitive,
    RawRow,
    RawWord,
    RowProvenance,
)
from railway_turno_decoder.temporal.analysis import analyze_page


def make_layout_page(events=None, texts=None, intervals=None, hours=(5, 6, 7, 8, 9), below=(17,)):
    events = events if events is not None else [(310, 1, 0), (325, 2, 0), (372, 3, 0), (384, 4, 0)]
    intervals = intervals if intervals is not None else [(310, 325), (372, 384)]
    words = []
    drawings = []
    origin = hours[0] % 24 * 60

    def x(minutes):
        return 100 + minutes - origin

    def add(text, box):
        i = len(words)
        words.append(
            RawWord(
                word_index=i,
                text=str(text),
                original_bbox=box,
                display_bbox=box,
                block_number=0,
                line_number=0,
                word_number=i,
            )
        )
        return i

    def line(a, b):
        i = len(drawings)
        box = (min(a[0], b[0]), min(a[1], b[1]), max(a[0], b[0]), max(a[1], b[1]))
        item = RawPrimitive(
            item_index=0,
            kind="l",
            original_bbox=box,
            display_bbox=box,
            original_points=[a, b],
            display_points=[a, b],
            original_arguments=[],
            display_arguments=[],
        )
        drawings.append(
            RawDrawing(
                drawing_index=i,
                type="s",
                original_bbox=box,
                display_bbox=box,
                width=1,
                dashes="[] 0",
                items=[item],
            )
        )

    add("M", (10, 145, 18, 155))
    previous = None
    absolute = []
    for hour in hours:
        value = hour % 24 * 60
        while previous is not None and value <= previous:
            value += 1440
        previous = value
        absolute.append(value)
        add(str(hour), (x(value) - 3, 256, x(value) + 3, 264))
    line((100, 260), (x(absolute[-1]), 260))
    for lo, hi in intervals:
        line((x(lo), 220), (x(hi), 220))
    for minutes, band, shift in events:
        at = x(minutes) + shift
        y = 218 + 8 * band
        box = (at, y - 4, at + 4, y + 4) if band % 2 else (at - 4, y - 4, at, y + 4)
        add(f"{minutes % 60:02d}", box)
    texts = (
        texts
        if texts is not None
        else [("Alpha", (110, 190, 130, 200)), ("Beta", (110, 203, 130, 213))]
    )
    for text, box in texts:
        add(text, box)
    for k, value in enumerate(below):
        add(str(value), (110 + 10 * k, 265, 115 + 10 * k, 273))
    refs = [w.word_index for w in words if 140 <= (w.display_bbox[1] + w.display_bbox[3]) / 2 < 264]
    row = RawRow(
        row_index=0,
        original_bbox=(0, 140, 700, 264),
        display_bbox=(0, 140, 700, 264),
        periodicity_marker="M",
        provenance=RowProvenance(
            page_index=0,
            anchor_word_indices=[0],
            word_indices=refs,
            drawing_indices=list(range(len(drawings))),
            drawing_item_refs=[
                DrawingItemRef(drawing_index=i, item_index=0) for i in range(len(drawings))
            ],
        ),
        detection_reasons=[],
    )
    page = RawPage(
        page_number=1,
        page_index=0,
        width=700,
        height=400,
        rotation=0,
        mediabox=(0, 0, 700, 400),
        cropbox=(0, 0, 700, 400),
        original_bounds=(0, 0, 700, 400),
        display_bounds=(0, 0, 700, 400),
        rotation_matrix=(1, 0, 0, 1, 0, 0),
        derotation_matrix=(1, 0, 0, 1, 0, 0),
        pdf_to_original_matrix=(1, 0, 0, -1, 0, 400),
        page_type="DUTY_DIAGRAM",
        words=words,
        drawings=drawings,
        rows=[row],
    )
    temporal = analyze_page(page, source_extraction_id=UUID(int=1), source_sha256="0" * 64)
    return page, temporal


def upload_layout_diagram(client, extra_text="Alpha"):
    with pymupdf.open() as pdf:
        p = pdf.new_page(width=600, height=400)
        p.insert_text((15, 22), "TURNO: synthetic PROFILO: fixture", fontsize=10)
        p.insert_text((10, 80), "S", fontsize=10)
        p.insert_text((10, 140), "7", fontsize=10)
        p.insert_text((430, 100), "Lav Cct Km Not Rip", fontsize=8)
        for hour in range(5, 10):
            text = str(hour)
            width = pymupdf.get_text_length(text, fontsize=6)
            p.insert_text((100 + 60 * (hour - 5) - width / 2, 150), text, fontsize=6)
        p.draw_line((100, 150), (340, 150), width=0.5)
        for lo, hi in [(310, 325), (372, 384)]:
            p.draw_line((100 + lo - 300, 125), (100 + hi - 300, 125))
        for minutes, band in [(310, 1), (325, 2), (372, 3), (384, 4)]:
            text = f"{minutes % 60:02d}"
            width = pymupdf.get_text_length(text, fontsize=5)
            at = 100 + minutes - 300
            p.insert_text((at if band % 2 else at - width, 124 + 5 * band + 1.94), text, fontsize=5)
        p.insert_text((110, 115), extra_text, fontsize=7)
        p.insert_text((172, 115), "Beta", fontsize=7)
        p.insert_text((110, 160), "17", fontsize=5)
        content = pdf.tobytes()
    response = client.post(
        "/jobs", files={"file": ("synthetic-layout.pdf", content)}, follow_redirects=False
    )
    assert response.status_code == 303
    return response.headers["location"]
