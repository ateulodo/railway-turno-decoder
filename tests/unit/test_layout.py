"""Synthetic visual grammar, including counterexamples to text and x ordering."""

from copy import deepcopy

import pytest
from layout_fixtures import make_layout_page

from railway_turno_decoder.layout.analysis import analyze_layout
from railway_turno_decoder.layout.identity import temporal_identity
from railway_turno_decoder.layout.models import LayoutPage
from railway_turno_decoder.layout.statistics import summarize_layout
from railway_turno_decoder.layout.stream import hour_candidates
from railway_turno_decoder.layout.tolerances import LayoutTolerances
from railway_turno_decoder.layout.validation import validate_layout
from railway_turno_decoder.temporal.analysis import analyze_page


def layout(**kwargs):
    page, temporal = make_layout_page(**kwargs)
    result = analyze_layout(page, temporal)
    validate_layout(result, page, temporal)
    return page, temporal, result


@pytest.mark.parametrize(
    "texts,count,orientation",
    [
        ([("Alpha", (110, 190, 130, 200)), ("Beta", (132, 190, 152, 200))], 1, "HORIZONTAL"),
        ([("Alpha", (110, 180, 120, 194)), ("Beta", (110, 198, 120, 210))], 1, "VERTICAL"),
        ([("Alpha", (110, 180, 130, 190)), ("Beta", (170, 180, 190, 190))], 2, "SINGLE"),
        ([("Alpha", (110, 180, 130, 190)), ("Beta", (132, 200, 152, 210))], 2, "SINGLE"),
        ([("(QRT", (110, 190, 130, 200)), ("Gamma", (132, 190, 152, 200))], 1, "HORIZONTAL"),
        ([("Z", (110, 190, 116, 200))], 1, "SINGLE"),
    ],
)
def test_text_components_use_geometry_and_preserve_fragments(texts, count, orientation):
    _, _, result = layout(texts=texts)
    clusters = result.rows[0].clusters
    assert len(clusters) == count
    assert all(c.orientation == orientation for c in clusters)
    assert sorted(f for c in clusters for f in c.fragments) == sorted(t[0] for t in texts)


def test_all_bands_and_dedicated_layers_are_disjoint():
    page, _, result = layout(
        texts=[("38", (200, 150, 206, 158)), ("Alpha", (110, 190, 130, 200))], below=(10,)
    )
    row = result.rows[0]
    assert [b.band_index for b in row.bands] == [1, 2, 3, 4]
    assert [t.band_index for t in row.minute_tokens] == [1, 2, 3, 4]
    assert [t.role for t in row.minute_tokens] == [
        "START_SLOT",
        "END_SLOT",
        "START_SLOT",
        "END_SLOT",
    ]
    refs = {t.word_index for t in row.minute_tokens}
    assert refs.isdisjoint(row.axis_hour_word_indices)
    assert refs.isdisjoint(b.word_index for b in row.below_axis)
    assert len(row.below_axis) == 1 and row.below_axis[0].raw_text == "10"
    evidence = row.below_axis[0]
    assert evidence.nearby_cluster_indices == [0]
    assert evidence.nearby_segment_indices == [0]
    assert evidence.segment_status == "UNIQUE"
    assert any(r.source.kind == "BELOW" and r.target.kind == "SEGMENT" for r in row.relations)
    assert any("38" in c.fragments for c in row.clusters)
    assert (
        page.words[row.below_axis[0].word_index].word_index
        not in page.rows[0].provenance.word_indices
    )


def test_incomplete_band_phase_keeps_all_alternatives():
    _, _, result = layout(events=[(310, 1, 0), (325, 2, 0)])
    row = result.rows[0]
    assert len(row.bands) == 4 and all(b.display_y is None for b in row.bands)
    assert row.minute_tokens[0].band_candidates == [1, 2, 3]
    assert row.minute_tokens[1].band_candidates == [2, 3, 4]
    assert result.band_phase_candidates == [[1, 2], [1, 3], [1, 4], [2, 3], [2, 4], [3, 4]]
    assert row.minute_tokens[0].observed_band_groups == [0]
    assert all(t.status == "AMBIGUOUS" for t in row.minute_tokens)
    assert not row.gaps


def test_page_band_evidence_resolves_partially_occupied_row():
    page, temporal = make_layout_page()
    originals = list(page.words)
    offset = len(originals)
    for word in originals:
        copy = word.model_copy(deep=True)
        copy.word_index += offset
        box = word.display_bbox
        copy.original_bbox = copy.display_bbox = (box[0], box[1] + 150, box[2], box[3] + 150)
        page.words.append(copy)
    draw_offset = len(page.drawings)
    for drawing in list(page.drawings):
        copy = drawing.model_copy(deep=True)
        copy.drawing_index += draw_offset
        box = copy.display_bbox
        copy.original_bbox = copy.display_bbox = (box[0], box[1] + 150, box[2], box[3] + 150)
        for item in copy.items:
            item.original_points = item.display_points = [
                (x, y + 150) for x, y in item.display_points
            ]
            b = item.display_bbox
            item.original_bbox = item.display_bbox = (b[0], b[1] + 150, b[2], b[3] + 150)
        page.drawings.append(copy)
    row = page.rows[0].model_copy(deep=True)
    row.row_index = 1
    row.original_bbox = row.display_bbox = (0, 290, 700, 414)
    row.provenance.page_index = 0
    omitted = {i + offset for i in [7, 9]}  # The two even slots in this synthetic fixture.
    row.provenance.word_indices = [
        i + offset for i in row.provenance.word_indices if i + offset not in omitted
    ]
    row.provenance.anchor_word_indices = [offset]
    row.provenance.drawing_indices = [i + draw_offset for i in row.provenance.drawing_indices]
    for ref in row.provenance.drawing_item_refs:
        ref.drawing_index += draw_offset
    page.rows.append(row)
    page.height = 600
    page.mediabox = page.cropbox = page.original_bounds = page.display_bounds = (0, 0, 700, 600)
    temporal = analyze_page(
        page,
        source_extraction_id=temporal.source_extraction_id,
        source_sha256=temporal.source_sha256,
    )
    result = analyze_layout(page, temporal)
    validate_layout(result, page, temporal)
    assert [t.band_index for t in result.rows[1].minute_tokens] == [1, 3]


def test_small_y_jitter_preserves_bands():
    page, temporal = make_layout_page()
    for word in page.words:
        if word.text == "12":
            b = word.display_bbox
            word.display_bbox = (b[0], b[1] + 0.2, b[2], b[3] + 0.2)
    temporal = analyze_page(
        page,
        source_extraction_id=temporal.source_extraction_id,
        source_sha256=temporal.source_sha256,
    )
    result = analyze_layout(page, temporal)
    assert [t.band_index for t in result.rows[0].minute_tokens] == [1, 2, 3, 4]


def test_structural_stream_gaps_and_hour_neighborhood():
    events = [(310, 3, 0), (321, 4, 0), (402, 2, -2), (426, 4, -4)]
    # Add upper-slot evidence elsewhere so page band phase is determined.
    events += [(490, 1, 0), (501, 2, 0)]
    _, _, result = layout(events=events)
    row = result.rows[0]
    stream = [row.minute_tokens[b.token_indices[0]] for b in row.stream_blocks]
    assert [t.hour_candidates[0].absolute_minutes for t in stream] == [310, 321, 402, 426, 490, 501]
    end_refs = {
        t.token_index for t in stream if t.hour_candidates[0].absolute_minutes in [402, 426]
    }
    assert end_refs <= {g.after_token_index for g in row.gaps if g.expected_role == "START_SLOT"}
    assert all(not hasattr(g, "inherited_minutes") for g in row.gaps)
    assert any(
        p.start_token_index == stream[0].token_index and p.end_token_index == stream[1].token_index
        for p in row.explicit_pairs
    )


def test_stream_is_not_global_x_sort():
    events = [(310, 1, 0), (325, 2, 0), (402, 4, 10), (410, 2, -10), (417, 3, 0), (428, 4, 0)]
    _, _, result = layout(events=events)
    row = result.rows[0]
    by_x = [t.value for t in sorted(row.minute_tokens, key=lambda t: t.center[0])]
    by_time = [row.minute_tokens[b.token_indices[0]].value for b in row.stream_blocks]
    assert by_x != by_time
    assert by_time == [10, 25, 42, 50, 57, 8]


@pytest.mark.parametrize("value", [3, 4, 8])
def test_new_hour_candidate_survives_leftward_typographic_shift(value):
    page, temporal = make_layout_page()
    axis = temporal.rows[0].axis
    tick = axis.mapping.x_at(360)
    candidates = hour_candidates(value, (tick - 3, 240, tick + 1, 248), axis, LayoutTolerances())
    assert [c.absolute_minutes for c in candidates] == [360 + value]
    assert all(c.absolute_minutes != 300 + value for c in candidates)


def test_two_hours_remain_ambiguous_and_arbitrary_hour_shift_is_rejected():
    _, temporal = make_layout_page()
    axis = temporal.rows[0].axis
    candidates = hour_candidates(30, (135, 230, 180, 238), axis, LayoutTolerances())
    assert [c.absolute_minutes for c in candidates] == [330, 390]
    assert hour_candidates(30, (150, 230, 154, 238), axis, LayoutTolerances()) == []


def test_midnight_stream_preserves_day_offset():
    _, _, result = layout(
        hours=(22, 23, 24, 1, 2),
        events=[(1370, 1, 0), (1382, 2, 0), (1437, 3, 0), (1448, 4, 0)],
        intervals=[(1370, 1382), (1437, 1448)],
    )
    row = result.rows[0]
    times = [row.minute_tokens[b.token_indices[0]].hour_candidates[0] for b in row.stream_blocks]
    assert [c.absolute_minutes for c in times] == [1370, 1382, 1437, 1448]
    assert times[-1].clock_label == "00:08" and times[-1].day_offset == 1
    assert summarize_layout([result])["stream_midnight_transitions"] == 1


def test_pairs_are_not_restricted_to_fixed_band_couples():
    _, _, result = layout(events=[(310, 1, 0), (321, 4, 0), (372, 3, 0), (384, 2, 0)])
    row = result.rows[0]
    assert any(
        row.minute_tokens[p.start_token_index].band_index == 1
        and row.minute_tokens[p.end_token_index].band_index == 4
        for p in row.explicit_pairs
    )


def test_many_to_many_and_unassociated_nodes_are_preserved():
    _, _, result = layout(
        texts=[
            ("Alpha", (110, 190, 118, 210)),
            ("Beta", (122, 190, 130, 210)),
            ("Far", (300, 190, 310, 210)),
        ],
        intervals=[(310, 325), (320, 340), (450, 460)],
    )
    row = result.rows[0]
    assert row.clusters[0].segment_status == "AMBIGUOUS"
    assert len(row.clusters[0].candidate_segment_indices) == 2
    assert row.clusters[-1].segment_status == "UNASSOCIATED"
    assert row.segment_associations[-1].status == "UNASSOCIATED"
    assert len(row.segment_associations[0].cluster_indices) >= 2


def test_equal_geometric_candidates_are_not_ranked_arbitrarily():
    _, _, result = layout(
        texts=[("Alpha", (125, 190, 135, 210))], intervals=[(310, 320), (340, 350)]
    )
    c = result.rows[0].clusters[0]
    assert c.candidate_segment_indices == [0, 1] and c.segment_status == "AMBIGUOUS"


def test_determinism_provenance_and_source_immutability():
    page, temporal = make_layout_page()
    raw_before = page.model_dump_json()
    temporal_before = temporal.model_dump_json()
    a = analyze_layout(page, temporal)
    assert page.model_dump_json() == raw_before and temporal.model_dump_json() == temporal_before
    page.rows[0].provenance.word_indices.reverse()
    page.rows[0].provenance.drawing_item_refs.reverse()
    temporal.rows[0].axis.support_refs.reverse()
    temporal.rows[0].axis.reasons[0].primitive_refs.reverse()
    b = analyze_layout(page, temporal)
    assert a.model_dump_json() == b.model_dump_json()
    assert temporal_identity(temporal) == a.temporal_identity
    assert LayoutPage.model_validate_json(a.model_dump_json()) == a
    assert all(
        c.fragments == [page.words[i].text for i in c.word_indices] for c in a.rows[0].clusters
    )


@pytest.mark.parametrize(
    "kind", ["inverted", "zero", "outside_row", "duplicate", "missing_ref", "bad_temporal"]
)
def test_malformed_inputs_are_conservative(kind):
    page, temporal = make_layout_page()
    if kind == "inverted":
        page.rows[0].display_bbox = (700, 140, 0, 264)
    elif kind == "zero":
        page.words[10].display_bbox = (110, 190, 110, 200)
    elif kind == "outside_row":
        page.words[10].display_bbox = (110, 50, 130, 60)
    elif kind == "duplicate":
        page.rows[0].provenance.word_indices.append(10)
    elif kind == "missing_ref":
        page.rows[0].provenance.word_indices.append(999)
    elif kind == "bad_temporal":
        temporal.rows[0].segments[0].source_start = (0, 0)
    if kind in ["missing_ref", "bad_temporal"]:
        with pytest.raises(ValueError):
            analyze_layout(page, temporal)
    else:
        if kind == "inverted":
            temporal = analyze_page(
                page,
                source_extraction_id=temporal.source_extraction_id,
                source_sha256=temporal.source_sha256,
            )
        result = analyze_layout(page, temporal)
        assert result.rows[0].status == "INVALID"


def test_axis_unavailable_does_not_invent_layout():
    page, temporal = make_layout_page(hours=(5, 6))
    assert temporal.rows[0].axis is None
    result = analyze_layout(page, temporal)
    validate_layout(result, page, temporal)
    assert result.rows[0].status == "AMBIGUOUS" and not result.rows[0].minute_tokens


@pytest.mark.parametrize(
    "kind",
    ["row", "word", "fragment", "bbox", "hour", "below", "segment", "metrics", "band", "stream"],
)
def test_cache_cross_layer_inconsistency_rejected(kind):
    page, temporal, result = layout()
    data = result.model_dump(mode="json")
    row = data["rows"][0]
    if kind == "row":
        extra = deepcopy(row)
        extra["row_index"] = extra["temporal_row_index"] = 1
        data["rows"].append(extra)
    elif kind == "word":
        row["clusters"][0]["word_indices"][0] = 999
    elif kind == "fragment":
        row["clusters"][0]["fragments"][0] = "different"
    elif kind == "bbox":
        row["clusters"][0]["display_bbox"][0] += 1
    elif kind == "hour":
        row["minute_tokens"][0]["hour_candidates"][0]["absolute_minutes"] += 60
    elif kind == "below":
        row["below_axis"][0]["word_index"] = 0
    elif kind == "segment":
        row["clusters"][0]["candidate_segment_indices"] = [999]
    elif kind == "metrics":
        row["relations"][0]["horizontal_gap"] += 1
    elif kind == "band":
        row["bands"][0]["word_indices"] = []
    elif kind == "stream":
        row["stream_blocks"].reverse()
    with pytest.raises(ValueError):
        cached = LayoutPage.model_validate(data)
        validate_layout(cached, page, temporal)


def test_budget_and_non_duty_page():
    page, temporal = make_layout_page()

    def expired():
        raise TimeoutError("synthetic budget")

    with pytest.raises(TimeoutError):
        analyze_layout(page, temporal, check_budget=expired)
    page.page_type = "UNKNOWN"
    temporal = analyze_page(
        page,
        source_extraction_id=temporal.source_extraction_id,
        source_sha256=temporal.source_sha256,
    )
    result = analyze_layout(page, temporal)
    validate_layout(result, page, temporal)
    assert result.rows == [] and summarize_layout([result])["duty_pages"] == 0


@pytest.mark.parametrize(
    "events,expected",
    [
        ([(310, 1, 0), (322, 3, 0), (372, 2, 0), (384, 4, 0)], "END_SLOT"),
        ([(310, 2, 0), (322, 4, 0), (372, 1, 0), (384, 3, 0)], "START_SLOT"),
    ],
)
def test_consecutive_same_roles_and_boundaries_leave_unfilled_gaps(events, expected):
    _, _, result = layout(events=events)
    row = result.rows[0]
    assert any(g.expected_role == expected for g in row.gaps)
    assert all(not hasattr(g, "absolute_minutes") for g in row.gaps)


def test_overlapping_distinct_words_preserve_ambiguous_order():
    page, temporal = make_layout_page()
    original = page.words[6]
    copy = original.model_copy(deep=True)
    copy.word_index = len(page.words)
    page.words.append(copy)
    page.rows[0].provenance.word_indices.append(copy.word_index)
    temporal = analyze_page(
        page,
        source_extraction_id=temporal.source_extraction_id,
        source_sha256=temporal.source_sha256,
    )
    result = analyze_layout(page, temporal)
    validate_layout(result, page, temporal)
    block = next(b for b in result.rows[0].stream_blocks if b.min_minutes == 310)
    assert block.reasons[0].code == "overlapping_candidate_time_intervals"
    assert block.status == "AMBIGUOUS" and len(block.token_indices) == 2
    assert result.rows[0].stream_status == "AMBIGUOUS"


def test_uncertain_hour_event_does_not_make_a_unique_pair_or_total_order():
    page, temporal = make_layout_page(events=[(330, 1, 0), (400, 2, 0), (432, 3, 0), (444, 4, 0)])
    word = page.words[6]
    word.display_bbox = (135, 222, 180, 230)
    temporal = analyze_page(
        page,
        source_extraction_id=temporal.source_extraction_id,
        source_sha256=temporal.source_sha256,
    )
    result = analyze_layout(page, temporal)
    validate_layout(result, page, temporal)
    token = next(t for t in result.rows[0].minute_tokens if t.word_index == 6)
    assert len(token.hour_candidates) == 2 and token.status == "AMBIGUOUS"
    assert result.rows[0].stream_status == "AMBIGUOUS"
    assert all(
        p.status == "AMBIGUOUS"
        for p in result.rows[0].explicit_pairs
        if p.start_token_index == token.token_index
    )
