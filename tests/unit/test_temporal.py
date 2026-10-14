"""Synthetic raw geometry only; no confidential PDF or activity semantics."""

import math
from uuid import UUID

import pytest
from pydantic import ValidationError

from railway_turno_decoder.raw_models import (
    DrawingItemRef,
    RawDrawing,
    RawPage,
    RawPrimitive,
    RawRow,
    RawWord,
    RowProvenance,
)
from railway_turno_decoder.temporal.analysis import analyze_page, summarize
from railway_turno_decoder.temporal.models import TemporalPage
from railway_turno_decoder.temporal.tolerances import TemporalTolerances
from railway_turno_decoder.temporal.validation import validate_against_raw

GENERATION = UUID(int=0)
DIGEST = "0" * 64


def fixture_page(
    hours=(14, 15, 16, 17),
    intervals=((885, 888), (911, 920), (954, 977)),
    labels=True,
    axis_support=True,
):
    words = []
    drawings = []

    def word(text, x, y, width=6):
        i = len(words)
        box = (x - width / 2, y - 3, x + width / 2, y + 3)
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
                dashes="[ 1 1 ] 1",
                stroke_color=[0, 0, 0],
                items=[item],
            )
        )
        return i

    word("LMXGVS", 10, 25)
    absolute = []
    for hour in hours:
        value = hour % 24 * 60
        if absolute:
            while value <= absolute[-1]:
                value += 1440
        absolute.append(value)
    origin = absolute[0] if absolute else 840

    def x_at(minutes):
        return 100 + (minutes - origin) / 0.6

    for hour, value in zip(hours, absolute, strict=True):
        word(hour, x_at(value), 200)
    if axis_support and len(hours) >= 2:
        line((x_at(absolute[0]), 200), (x_at(absolute[-1]), 200))
    for start, end in intervals:
        x0, x1 = x_at(start), x_at(end)
        line((x0, 100), (x1, 100))
        if labels:
            word(f"{start % 60:02}", x0 + 3, 109)
            word(f"{end % 60:02}", x1 - 3, 115)
    row = RawRow(
        row_index=0,
        original_bbox=(0, 0, 600, 240),
        display_bbox=(0, 0, 600, 240),
        periodicity_marker="LMXGVS",
        provenance=RowProvenance(
            page_index=0,
            anchor_word_indices=[0],
            word_indices=list(range(len(words))),
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
        width=600,
        height=250,
        rotation=0,
        mediabox=(0, 0, 600, 250),
        cropbox=(0, 0, 600, 250),
        original_bounds=(0, 0, 600, 250),
        display_bounds=(0, 0, 600, 250),
        rotation_matrix=(1, 0, 0, 1, 0, 0),
        derotation_matrix=(1, 0, 0, 1, 0, 0),
        pdf_to_original_matrix=(1, 0, 0, -1, 0, 250),
        page_type="DUTY_DIAGRAM",
        words=words,
        drawings=drawings,
        rows=[row],
    )
    return page, word, line


def analyze(page, **kwargs):
    return analyze_page(page, source_extraction_id=GENERATION, source_sha256=DIGEST, **kwargs)


def append_word(page, text, x, y):
    i = len(page.words)
    box = (x - 3, y - 3, x + 3, y + 3)
    page.words.append(
        RawWord(
            word_index=i,
            text=text,
            original_bbox=box,
            display_bbox=box,
            block_number=0,
            line_number=0,
            word_number=i,
        )
    )
    page.rows[0].provenance.word_indices.append(i)
    return i


def test_three_intervals_geometry_and_provenance():
    page, _, _ = fixture_page()
    before = page.model_dump_json()
    result = analyze(page)
    row = result.rows[0]
    assert row.axis is not None
    assert row.axis.mapping.minutes_per_point == pytest.approx(0.6)
    assert row.axis.mapping.x_at(954) == pytest.approx(290)
    assert [a.absolute_minutes for a in row.axis.anchors] == [840, 900, 960, 1020]
    assert len(row.segments) == 3
    for segment, (left, right) in zip(
        row.segments, [(885, 888), (911, 920), (954, 977)], strict=True
    ):
        assert segment.left.absolute_minutes == pytest.approx(left)
        assert segment.right.absolute_minutes == pytest.approx(right)
        assert segment.status == "EXACT"
        assert segment.left.nearby_minute_label.minute_candidate == left % 60
        assert segment.right.nearby_minute_label.minute_candidate == right % 60
        primitive = page.drawings[segment.source.drawing_index].items[segment.source.item_index]
        assert segment.source_start == primitive.display_points[0]
        assert segment.style.dashes == "[ 1 1 ] 1"  # Preserved, never assigned an activity type.
    assert page.model_dump_json() == before
    assert TemporalPage.model_validate_json(result.model_dump_json()) == result
    assert analyze(page).model_dump_json() == result.model_dump_json()


def test_geometry_is_primary_without_numeric_labels():
    page, _, _ = fixture_page(labels=False)
    row = analyze(page).rows[0]
    assert row.axis is not None
    assert len(row.segments) == 3
    assert all(s.left.status == s.right.status == "DERIVED" for s in row.segments)
    assert row.segments[2].left.clock_label == "15:54"
    assert row.segments[2].right.clock_label == "16:17"


def test_midnight_monotone_and_day_offset():
    page, _, _ = fixture_page(hours=(22, 23, 24, 1, 2), intervals=((1430, 1460),))
    row = analyze(page).rows[0]
    assert [a.absolute_minutes for a in row.axis.anchors] == [1320, 1380, 1440, 1500, 1560]
    segment = row.segments[0]
    assert segment.left.clock_label == "23:50" and segment.left.day_offset == 0
    assert segment.right.clock_label == "00:20" and segment.right.day_offset == 1
    assert segment.left.absolute_minutes == pytest.approx(1430)
    assert segment.right.absolute_minutes == pytest.approx(1460)
    assert segment.right.minutes_from_axis_origin > segment.left.minutes_from_axis_origin
    assert segment.crosses_midnight


def test_missing_tick_preserves_mapping():
    page, _, _ = fixture_page(hours=(14, 16, 17))
    row = analyze(page).rows[0]
    assert row.axis is not None and len(row.axis.anchors) == 3
    assert row.segments[0].left.absolute_minutes == pytest.approx(885)


@pytest.mark.parametrize("label", ["9", "15", "99"])
def test_spurious_number_near_axis_is_not_accepted(label):
    page, _, _ = fixture_page()
    reference = append_word(page, label, 250, 200)
    row = analyze(page).rows[0]
    assert row.axis is not None
    assert reference not in [a.word_index for a in row.axis.anchors]
    assert [a.hour_candidate for a in row.axis.anchors] == [14, 15, 16, 17]


def test_two_compatible_overlapping_labels_are_ambiguous():
    page, _, _ = fixture_page(intervals=((954, 977),))
    original = next(w for w in page.words if w.text == "54")
    append_word(
        page, "54", sum(original.display_bbox[::2]) / 2, sum(original.display_bbox[1::2]) / 2
    )
    end = analyze(page).rows[0].segments[0].left
    assert end.status == "AMBIGUOUS"
    assert end.nearby_minute_label is None
    assert len([e for e in end.nearby_labels if e.compatible]) == 2
    assert end.absolute_minutes == pytest.approx(954)


def test_conflicting_labels_do_not_correct_geometry():
    page, _, _ = fixture_page(intervals=((954, 977),))
    word = next(w for w in page.words if w.text == "54")
    word.text = "50"
    end = analyze(page).rows[0].segments[0].left
    assert end.status == "AMBIGUOUS"
    assert end.absolute_minutes == pytest.approx(954)
    assert end.clock_label == "15:54"


@pytest.mark.parametrize(
    "points",
    [
        ((150, 60), (150, 130)),  # vertical
        ((150, 100), (150.1, 100)),  # sub-minute insignificant fragment
        ((150, 245), (180, 245)),  # outside row
        ((20, 100), (90, 100)),  # outside measured axis
    ],
)
def test_non_candidates_are_excluded(points):
    page, _, _ = fixture_page(intervals=())
    i = len(page.drawings)
    box = (
        min(points[0][0], points[1][0]),
        min(points[0][1], points[1][1]),
        max(points[0][0], points[1][0]),
        max(points[0][1], points[1][1]),
    )
    primitive = RawPrimitive(
        item_index=0,
        kind="l",
        original_bbox=box,
        display_bbox=box,
        original_points=list(points),
        display_points=list(points),
        original_arguments=[],
        display_arguments=[],
    )
    page.drawings.append(
        RawDrawing(
            drawing_index=i, type="s", original_bbox=box, display_bbox=box, items=[primitive]
        )
    )
    page.rows[0].provenance.drawing_indices.append(i)
    page.rows[0].provenance.drawing_item_refs.append(DrawingItemRef(drawing_index=i, item_index=0))
    assert analyze(page).rows[0].segments == []


@pytest.mark.parametrize("hours", [(14, 15), (14,)])
def test_insufficient_mapping_is_not_invented(hours):
    page, _, _ = fixture_page(hours=hours)
    row = analyze(page).rows[0]
    assert row.axis is None and row.status == "AMBIGUOUS" and row.segments == []


def test_vector_support_is_required():
    page, _, _ = fixture_page(axis_support=False, labels=False)
    assert analyze(page).rows[0].axis is None


def test_reversed_primitive_has_ordered_endpoints():
    page, _, _ = fixture_page(intervals=((954, 977),))
    item = page.drawings[1].items[0]
    item.display_points.reverse()
    segment = analyze(page).rows[0].segments[0]
    assert segment.source_start[0] > segment.source_end[0]
    assert segment.left.absolute_minutes < segment.right.absolute_minutes


def test_local_reference_order_preserves_temporal_order_and_provenance():
    page, _, _ = fixture_page(intervals=((954, 977), (885, 888), (911, 920)))
    before = analyze(page)
    assert [s.left.clock_label for s in before.rows[0].segments] == ["14:45", "15:11", "15:54"]
    assert [s.source.drawing_index for s in before.rows[0].segments] == [2, 3, 1]
    page.rows[0].provenance.word_indices.reverse()
    page.rows[0].provenance.drawing_item_refs.reverse()
    assert analyze(page).model_dump_json() == before.model_dump_json()


def test_small_float_perturbation_tolerated():
    page, _, _ = fixture_page()
    word = page.words[2]
    b = word.display_bbox
    word.display_bbox = (b[0] + 0.1, b[1] + 0.2, b[2] + 0.1, b[3] + 0.2)
    row = analyze(page).rows[0]
    assert row.axis is not None
    assert 0 < row.axis.mapping.max_residual_minutes < TemporalTolerances().axis_residual_minutes
    assert row.segments[2].left.absolute_minutes == pytest.approx(954, abs=0.1)


def test_malformed_provenance_rejected():
    page, _, _ = fixture_page()
    page.rows[0].provenance.word_indices.append(99999)
    with pytest.raises(ValidationError):
        analyze(page)


@pytest.mark.parametrize("value", [float("nan"), float("inf"), -1.0])
def test_invalid_tolerance_rejected(value):
    with pytest.raises(ValidationError):
        TemporalTolerances(axis_residual_minutes=value)


def test_mapping_inverse_rejects_nonfinite():
    page, _, _ = fixture_page()
    mapping = analyze(page).rows[0].axis.mapping
    with pytest.raises(ValueError):
        mapping.minutes_at(math.inf)
    with pytest.raises(ValueError):
        mapping.x_at(math.nan)


def test_cooperative_budget_propagates():
    page, _, _ = fixture_page()

    def expired():
        raise TimeoutError("synthetic budget")

    with pytest.raises(TimeoutError):
        analyze(page, check_budget=expired)


def test_non_duty_page_is_not_interpreted():
    page, _, _ = fixture_page()
    page.page_type = "UNKNOWN"
    result = analyze(page)
    assert result.rows == [] and result.warnings


def test_competing_supported_axes_are_ambiguous():
    page, _, _ = fixture_page(intervals=(), labels=False)
    for hour, x in [(14, 100), (15, 200), (16, 300), (17, 400)]:
        append_word(page, str(hour), x, 160)
    original = page.drawings[0]
    copied = original.model_copy(deep=True)
    copied.drawing_index = 1
    copied.original_bbox = copied.display_bbox = (100, 160, 400, 160)
    copied.items[0].original_points = copied.items[0].display_points = [(100, 160), (400, 160)]
    copied.items[0].original_bbox = copied.items[0].display_bbox = (100, 160, 400, 160)
    page.drawings.append(copied)
    page.rows[0].provenance.drawing_indices.append(1)
    page.rows[0].provenance.drawing_item_refs.append(DrawingItemRef(drawing_index=1, item_index=0))
    row = analyze(page).rows[0]
    assert row.axis is None and row.status == "AMBIGUOUS"
    assert any("multiple equally" in warning for warning in row.warnings)


def test_styles_do_not_change_candidate_interpretation():
    page, _, _ = fixture_page()
    before = analyze(page)
    for drawing in page.drawings:
        drawing.width = 8
        drawing.dashes = "[] 0"
    after = analyze(page)
    assert [
        (s.left.absolute_minutes, s.right.absolute_minutes) for s in before.rows[0].segments
    ] == [(s.left.absolute_minutes, s.right.absolute_minutes) for s in after.rows[0].segments]
    assert all(s.style.width == 8 and s.style.dashes == "[] 0" for s in after.rows[0].segments)


def test_display_coordinates_ignore_source_pdf_rotation():
    page, _, _ = fixture_page()
    before = analyze(page)
    for rotation in [90, 180, 270]:
        page.rotation = rotation
        after = analyze(page)
        assert after == before  # Only M2 display coordinates are consumed.


def test_endpoint_label_at_half_minute_boundary_is_not_exact():
    page, _, _ = fixture_page(intervals=((954, 977),))
    for item in page.drawings[1].items:
        left, right = item.display_points
        item.display_points = [(left[0] + 0.5 / 0.6, left[1]), right]
    end = analyze(page).rows[0].segments[0].left
    assert end.status == "AMBIGUOUS"
    assert end.clock_label == "15:55"
    assert end.absolute_minutes == pytest.approx(954.5)


def test_midnight_crossing_uses_unrounded_geometry():
    page, _, _ = fixture_page(hours=(22, 23, 24, 1), intervals=((1439.6, 1439.9),), labels=False)
    # Lower the significance floor only for this sub-minute boundary regression.
    result = analyze(page, tolerances=TemporalTolerances(min_segment_minutes=0.1))
    segment = result.rows[0].segments[0]
    assert segment.left.clock_label == segment.right.clock_label == "00:00"
    assert not segment.crosses_midnight
    assert segment.right.absolute_minutes < 1440


@pytest.mark.parametrize(
    "bbox",
    [
        (600, 0, 0, 240),
        (0, 200, 600, 100),
        (100, 0, 100, 240),
        (0, 100, 600, 100),
        (-1, 0, 600, 240),
        (0, -1, 600, 240),
        (0, 0, 601, 240),
        (0, 0, 600, 251),
    ],
)
def test_bad_row_bounds_are_invalid_not_invented(bbox):
    page, _, _ = fixture_page()
    page.rows[0].display_bbox = bbox
    row = analyze(page).rows[0]
    assert row.status == "INVALID" and row.axis is None and row.segments == []


def test_multiple_axis_support_references_are_canonical():
    page, _, _ = fixture_page()
    original = page.drawings[0].items[0]
    page.drawings[0].items = []
    for i, (left, right) in enumerate([(100, 200), (200, 300), (300, 400)]):
        item = original.model_copy(deep=True)
        item.item_index = i
        item.original_points = item.display_points = [(left, 200), (right, 200)]
        item.original_bbox = item.display_bbox = (left, 200, right, 200)
        page.drawings[0].items.append(item)
    refs = [DrawingItemRef(drawing_index=0, item_index=i) for i in range(3)]
    page.rows[0].provenance.drawing_item_refs[:1] = refs
    before = analyze(page)
    page.rows[0].provenance.word_indices.reverse()
    page.rows[0].provenance.drawing_item_refs.reverse()
    after = analyze(page)
    assert before.model_dump_json() == after.model_dump_json()
    assert after.rows[0].axis.support_refs == refs
    assert after.rows[0].axis.reasons[0].primitive_refs == refs
    validate_against_raw(after, page)
    legacy = before.model_dump(mode="json")
    legacy.pop("page_type")
    legacy["rows"][0]["axis"]["support_refs"].reverse()
    legacy["rows"][0]["axis"]["reasons"][0]["primitive_refs"].reverse()
    validate_against_raw(TemporalPage.model_validate(legacy), page)


def test_summary_counts_duty_page_without_rows_and_excludes_non_duty():
    page, _, _ = fixture_page()
    page.rows = []
    duty = analyze(page)
    page.page_type = "UNKNOWN"
    other = analyze(page)
    stats = summarize(iter([duty, other]))
    assert stats["duty_pages"] == stats["duty_pages_analyzed"] == 1
    assert stats["duty_pages_without_rows"] == 1
    assert stats["rows"] == stats["rows_with_valid_axis"] == 0
    assert summarize([])["duty_pages_analyzed"] == 0


@pytest.mark.parametrize(
    "kind",
    [
        "selected_label",
        "duplicate_evidence",
        "endpoint_point",
        "source_point",
        "segment_reason",
        "axis_time",
        "axis_residual",
        "axis_spacing",
        "axis_order",
        "endpoint_hour_ref",
        "relative_time",
        "status",
        "clock",
        "day",
        "crossing",
        "compatibility",
        "missing_axis",
        "non_duty_rows",
    ],
)
def test_nested_temporal_inconsistencies_are_model_errors(kind):
    page, _, _ = fixture_page(intervals=((954, 977),))
    data = analyze(page).model_dump(mode="json")
    row = data["rows"][0]
    axis = row["axis"]
    segment = row["segments"][0]
    endpoint = segment["left"]
    match kind:
        case "selected_label":
            endpoint["nearby_minute_label"]["word_index"] = 999
        case "duplicate_evidence":
            endpoint["nearby_labels"].append(endpoint["nearby_labels"][0])
        case "endpoint_point":
            endpoint["display_point"][1] += 1
        case "source_point":
            segment["source_start"][1] += 1
        case "segment_reason":
            segment["reasons"][0]["primitive_refs"][0]["drawing_index"] = 0
        case "axis_time":
            axis["anchors"][1]["absolute_minutes"] += 1
        case "axis_residual":
            axis["mapping"]["max_residual_minutes"] = 0.1
        case "axis_spacing":
            axis["mapping"]["hour_spacing_points"] += 1
        case "axis_order":
            axis["anchors"].reverse()
        case "endpoint_hour_ref":
            endpoint["supporting_hour_word_indices"] = [999]
        case "relative_time":
            endpoint["minutes_from_axis_origin"] += 1
        case "status":
            endpoint["status"] = "DERIVED"
        case "clock":
            endpoint["clock_label"] = "00:00"
        case "day":
            endpoint["day_offset"] = 1
        case "crossing":
            segment["crosses_midnight"] = True
        case "compatibility":
            endpoint["nearby_labels"][0]["compatible"] = False
            endpoint["nearby_minute_label"] = None
            endpoint["status"] = segment["status"] = row["status"] = "AMBIGUOUS"
        case "missing_axis":
            row["axis"] = None
        case "non_duty_rows":
            data["page_type"] = "UNKNOWN"
    with pytest.raises(ValidationError):
        TemporalPage.model_validate(data)


@pytest.mark.parametrize(
    "kind",
    [
        "missing_row",
        "extra_row",
        "source_bbox",
        "source_anchors",
        "anchor_word",
        "foreign_word",
        "foreign_primitive",
        "word_reason",
        "drawing_reason",
        "item_reason",
        "label_bbox",
        "label_word",
        "segment_points",
        "style",
    ],
)
def test_cross_raw_temporal_inconsistencies_are_external_errors(kind):
    page, _, _ = fixture_page(intervals=((954, 977),))
    data = analyze(page).model_dump(mode="json")
    row = data["rows"][0]
    segment = row["segments"][0]
    match kind:
        case "missing_row":
            data["rows"] = []
        case "extra_row":
            copied = dict(row, row_index=1)
            data["rows"].append(copied)
        case "source_bbox":
            row["source_row_bbox"][2] -= 1
        case "source_anchors":
            row["source_anchor_word_indices"] = []
        case "anchor_word":
            # Keep the internally valid axis, without dependent endpoint refs.
            row["segments"] = []
            row["axis"]["anchors"][0]["word_index"] = 999
        case "foreign_word":
            word = page.words[0].model_copy(deep=True)
            word.word_index = len(page.words)
            page.words.append(word)
            row["reasons"][0]["word_indices"] = [word.word_index]
        case "foreign_primitive":
            drawing = page.drawings[0].model_copy(deep=True)
            drawing.drawing_index = len(page.drawings)
            page.drawings.append(drawing)
            row["reasons"][0]["primitive_refs"] = [
                dict(drawing_index=drawing.drawing_index, item_index=0)
            ]
        case "word_reason":
            row["reasons"][0]["word_indices"] = [999]
        case "drawing_reason":
            row["reasons"][0]["primitive_refs"] = [dict(drawing_index=999, item_index=0)]
        case "item_reason":
            row["reasons"][0]["primitive_refs"] = [dict(drawing_index=0, item_index=999)]
        case "label_bbox":
            segment["left"]["nearby_labels"][0]["display_bbox"][0] += 1
            segment["left"]["nearby_minute_label"]["display_bbox"][0] += 1
        case "label_word":
            segment["left"]["nearby_labels"][0]["word_index"] = 0
            segment["left"]["nearby_minute_label"]["word_index"] = 0
        case "segment_points":
            segment["source_start"][1] += 1
            segment["left"]["display_point"][1] += 1
        case "style":
            segment["style"]["width"] += 1
    cached = TemporalPage.model_validate(data)
    with pytest.raises(ValueError):
        validate_against_raw(cached, page)
