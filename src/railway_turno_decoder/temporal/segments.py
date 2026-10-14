"""Neutral vector candidates and geometry-first endpoint evidence."""

from bisect import bisect_left, bisect_right
from math import floor

from railway_turno_decoder.temporal.axis import center, numeric
from railway_turno_decoder.temporal.models import (
    MinuteEvidence,
    SegmentStyle,
    TemporalEndpoint,
    TemporalReason,
    TemporalSegment,
)


def minute_index(page, row, axis):
    excluded = (
        set(row.provenance.anchor_word_indices)
        | {a.word_index for a in axis.anchors}
        | set(axis.rejected_word_indices)
    )
    words = sorted(
        (
            page.words[i]
            for i in row.provenance.word_indices
            if i not in excluded and numeric(page.words[i], 59)
        ),
        key=lambda w: (center(w)[0], w.word_index),
    )
    return (
        words,
        [center(w)[0] for w in words],
        max(((w.display_bbox[2] - w.display_bbox[0]) / 2 for w in words), default=0.0),
    )


def endpoint(point, axis, row, index, tolerance):
    mapping = axis.mapping
    minutes = mapping.minutes_at(point[0])
    outside = max(mapping.min_x - point[0], point[0] - mapping.max_x, 0) * mapping.minutes_per_point
    anchor_x = [a.display_point[0] for a in axis.anchors]
    position = bisect_right(anchor_x, point[0])
    hour_refs = [
        axis.anchors[i].word_index
        for i in sorted({max(0, position - 1), min(len(anchor_x) - 1, position)})
    ]
    if outside > tolerance.endpoint_outside_minutes:
        return TemporalEndpoint(
            display_point=point,
            status="INVALID",
            absolute_minutes=None,
            minutes_from_axis_origin=None,
            clock_label=None,
            day_offset=None,
            mapping_residual_minutes=mapping.max_residual_minutes,
            rounding_residual_minutes=None,
            nearby_minute_label=None,
            nearby_labels=[],
            supporting_hour_word_indices=hour_refs,
            reasons=[TemporalReason(code="endpoint_outside_axis", word_indices=hour_refs)],
        )
    words, xs, half_width = index
    radius = max(
        tolerance.geometry_points, mapping.hour_spacing_points * tolerance.nearby_x_hour_fraction
    )
    vertical = (row.display_bbox[3] - row.display_bbox[1]) * tolerance.nearby_y_row_fraction
    evidence = []
    for word in words[
        bisect_left(xs, point[0] - radius - half_width) : bisect_right(
            xs, point[0] + radius + half_width
        )
    ]:
        b = word.display_bbox
        dx = min(abs(point[0] - b[0]), abs(point[0] - b[2]))
        dy = max(b[1] - point[1], point[1] - b[3], 0.0)
        if dx > radius or dy > vertical:
            continue
        label = int(word.text)
        nearest = label + 60 * round((minutes - label) / 60)
        residual = abs(minutes - nearest)
        evidence.append(
            MinuteEvidence(
                word_index=word.word_index,
                raw_label=word.text,
                minute_candidate=label,
                display_bbox=b,
                x_distance_points=dx,
                y_distance_points=dy,
                residual_minutes=residual,
                compatible=(
                    residual + mapping.max_residual_minutes
                    < tolerance.minute_label_residual_minutes
                ),
            )
        )
    evidence.sort(key=lambda e: (e.x_distance_points, e.y_distance_points, e.word_index))
    compatible = [e for e in evidence if e.compatible]
    selected = compatible[0] if len(compatible) == 1 else None
    status = "EXACT" if selected else ("AMBIGUOUS" if evidence else "DERIVED")
    rounded = floor(minutes + 0.5)
    clock = f"{(rounded // 60) % 24:02d}:{rounded % 60:02d}"
    reason = (
        "unique_compatible_minute_evidence"
        if selected
        else ("conflicting_or_multiple_minute_evidence" if evidence else "affine_geometry")
    )
    return TemporalEndpoint(
        display_point=point,
        status=status,
        absolute_minutes=minutes,
        minutes_from_axis_origin=minutes - mapping.origin_absolute_minutes,
        clock_label=clock,
        day_offset=rounded // 1440,
        mapping_residual_minutes=mapping.max_residual_minutes,
        rounding_residual_minutes=abs(rounded - minutes),
        nearby_minute_label=selected,
        nearby_labels=evidence,
        supporting_hour_word_indices=hour_refs,
        reasons=[TemporalReason(code=reason, word_indices=[e.word_index for e in evidence])],
    )


def detect_segments(page, row, axis, tolerance, check_budget):
    index = minute_index(page, row, axis)
    candidates = []
    min_length = tolerance.min_segment_minutes / axis.mapping.minutes_per_point
    support = {(r.drawing_index, r.item_index) for r in axis.support_refs}
    for ref in row.provenance.drawing_item_refs:
        check_budget()
        drawing = page.drawings[ref.drawing_index]
        item = drawing.items[ref.item_index]
        if (
            item.kind != "l"
            or len(item.display_points) != 2
            or (ref.drawing_index, ref.item_index) in support
        ):
            continue
        start, end = item.display_points
        left, right = sorted((start, end), key=lambda p: (p[0], p[1]))
        dx = right[0] - left[0]
        dy = abs(right[1] - left[1])
        if dx < min_length or dy > max(tolerance.geometry_points, tolerance.segment_max_slope * dx):
            continue
        box = row.display_bbox
        if not all(box[0] <= p[0] <= box[2] and box[1] <= p[1] < box[3] for p in (left, right)):
            continue
        # Exclude the axis baseline even where it extends outside its anchors.
        if abs((left[1] + right[1]) / 2 - axis.display_y) <= tolerance.geometry_points:
            continue
        # No candidate may extrapolate far beyond the measured time axis.
        margin = tolerance.endpoint_outside_minutes / axis.mapping.minutes_per_point
        if left[0] < axis.mapping.min_x - margin or right[0] > axis.mapping.max_x + margin:
            continue
        a = endpoint(left, axis, row, index, tolerance)
        b = endpoint(right, axis, row, index, tolerance)
        status = (
            "INVALID"
            if "INVALID" in (a.status, b.status)
            else (
                "AMBIGUOUS"
                if "AMBIGUOUS" in (a.status, b.status)
                else ("EXACT" if a.status == b.status == "EXACT" else "DERIVED")
            )
        )
        candidates.append(
            TemporalSegment(
                segment_index=0,
                source=ref,
                source_start=start,
                source_end=end,
                left=a,
                right=b,
                style=SegmentStyle(
                    drawing_type=drawing.type,
                    width=drawing.width,
                    dashes=drawing.dashes,
                    stroke_color=drawing.stroke_color,
                    fill_color=drawing.fill_color,
                    stroke_opacity=drawing.stroke_opacity,
                    fill_opacity=drawing.fill_opacity,
                    line_cap=drawing.line_cap,
                    line_join=drawing.line_join,
                    seqno=drawing.seqno,
                    layer=drawing.layer,
                ),
                status=status,
                mapping_residual_minutes=axis.mapping.max_residual_minutes,
                crosses_midnight=a.day_offset is not None
                and b.day_offset is not None
                and floor(a.absolute_minutes / 1440) != floor(b.absolute_minutes / 1440),
                reasons=[
                    TemporalReason(
                        code="horizontal_vector_inside_row_and_axis_extent", primitive_refs=[ref]
                    )
                ],
            )
        )
    candidates.sort(
        key=lambda s: (
            s.left.display_point,
            s.right.display_point,
            s.source.drawing_index,
            s.source.item_index,
        )
    )
    for i, segment in enumerate(candidates):
        segment.segment_index = i
    return candidates
