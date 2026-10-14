"""Validate derived references against one immutable M2 page, without refitting."""

from collections.abc import Callable
from statistics import median

from railway_turno_decoder.raw_models import RawPage
from railway_turno_decoder.temporal.axis import center
from railway_turno_decoder.temporal.models import TemporalPage, same_number


def validate_against_raw(
    temporal: TemporalPage, page: RawPage, check_budget: Callable[[], None] = lambda: None
) -> None:
    """Raise ValueError for cross-raw inconsistencies; storage owns the error contract.

    Membership sets are local to a row. No raw objects are copied or persisted,
    and validation does not repeat axis hypotheses or minute-candidate searches.
    """
    if temporal.page_index != page.page_index or temporal.page_type != page.page_type:
        raise ValueError("temporal/raw page mismatch")
    expected_rows = page.rows if page.page_type == "DUTY_DIAGRAM" else []
    if len(temporal.rows) != len(expected_rows):
        raise ValueError("temporal/raw row count mismatch")
    for row, source in zip(temporal.rows, expected_rows, strict=True):
        check_budget()
        if (
            row.row_index != source.row_index
            or row.source_row_bbox != source.display_bbox
            or row.source_anchor_word_indices != source.provenance.anchor_word_indices
        ):
            raise ValueError("temporal/raw source row mismatch")
        words = set(source.provenance.word_indices) | set(source.provenance.anchor_word_indices)
        primitives = {(r.drawing_index, r.item_index) for r in source.provenance.drawing_item_refs}

        def word(index, allowed=words):
            if not 0 <= index < len(page.words) or index not in allowed:
                raise ValueError("temporal word reference outside source row")
            return page.words[index]

        def primitive(ref, allowed=primitives):
            if (
                not 0 <= ref.drawing_index < len(page.drawings)
                or not 0 <= ref.item_index < len(page.drawings[ref.drawing_index].items)
                or (ref.drawing_index, ref.item_index) not in allowed
            ):
                raise ValueError("temporal primitive reference outside source row")
            return page.drawings[ref.drawing_index].items[ref.item_index]

        def reasons(values):
            for reason in values:
                for index in reason.word_indices:
                    word(index)
                for ref in reason.primitive_refs:
                    primitive(ref)

        for index in row.source_anchor_word_indices:
            word(index)
        reasons(row.reasons)
        left, top, right, bottom = source.display_bbox
        bounds_valid = 0 <= left < right <= page.width and 0 <= top < bottom <= page.height
        if not bounds_valid and (row.status != "INVALID" or row.axis is not None or row.segments):
            raise ValueError("malformed source row must be invalid")
        if row.axis is None:
            continue
        axis = row.axis
        reasons(axis.reasons)
        for index in axis.rejected_word_indices:
            word(index)
        ys = []
        for ref in axis.support_refs:
            item = primitive(ref)
            if item.kind != "l" or len(item.display_points) != 2:
                raise ValueError("axis support must reference a line")
            ys.append(sum(p[1] for p in item.display_points) / 2)
        if not same_number(axis.display_y, median(ys)):
            raise ValueError("axis support position mismatch")
        for anchor in axis.anchors:
            check_budget()
            raw = word(anchor.word_index)
            if anchor.raw_label != raw.text or anchor.display_point != center(raw):
                raise ValueError("hour anchor/raw word mismatch")
            reasons(anchor.reasons)
        for segment in row.segments:
            check_budget()
            item = primitive(segment.source)
            if item.kind != "l" or item.display_points != [
                segment.source_start,
                segment.source_end,
            ]:
                raise ValueError("segment/raw primitive mismatch")
            drawing = page.drawings[segment.source.drawing_index]
            for key, value in segment.style.model_dump().items():
                if value != getattr(drawing, "type" if key == "drawing_type" else key):
                    raise ValueError("segment/raw style mismatch")
            reasons(segment.reasons)
            for endpoint in (segment.left, segment.right):
                for index in endpoint.supporting_hour_word_indices:
                    word(index)
                reasons(endpoint.reasons)
                evidence = list(endpoint.nearby_labels)
                if endpoint.nearby_minute_label is not None:
                    evidence.append(endpoint.nearby_minute_label)
                for label in evidence:
                    raw = word(label.word_index)
                    if label.raw_label != raw.text or label.display_bbox != raw.display_bbox:
                        raise ValueError("minute evidence/raw word mismatch")
                    box = raw.display_bbox
                    x, y = endpoint.display_point
                    if not (
                        same_number(label.x_distance_points, min(abs(x - box[0]), abs(x - box[2])))
                        and same_number(label.y_distance_points, max(box[1] - y, y - box[3], 0))
                    ):
                        raise ValueError("minute evidence/raw distance mismatch")
    check_budget()
