"""Minute hour neighborhoods and partial ordering; never a global x stream."""

from bisect import bisect_right
from math import ceil, floor

from railway_turno_decoder.layout.geometry import XIndex, bounds, gaps
from railway_turno_decoder.layout.models import (
    ExplicitPair,
    HourCandidate,
    LayoutReason,
    StreamBlock,
    StructuralGap,
)


def hour_candidates(value, box, axis, tolerance):
    m = axis.mapping
    if box[2] - box[0] > m.hour_spacing_points * tolerance.minute_width_spacing:
        return []
    margin = m.hour_spacing_points * tolerance.hour_shift_spacing
    # The fit can have a residual at an observed boundary anchor. Its fitted
    # x extent must not move ceil/floor past that anchor's exact hour. Keep the
    # typographic window unchanged and bound time by the observed anchors.
    low = max(axis.anchors[0].absolute_minutes, m.minutes_at(box[0] - margin))
    high = min(axis.anchors[-1].absolute_minutes, m.minutes_at(box[2] + margin))
    first = ceil((low - value) / 60)
    last = floor((high - value) / 60)
    xs = [a.display_point[0] for a in axis.anchors]
    result = []
    for hour in range(first, last + 1):
        minutes = hour * 60 + value
        x = m.x_at(minutes)
        position = bisect_right(xs, x)
        refs = [
            axis.anchors[i].word_index
            for i in sorted({max(0, position - 1), min(len(xs) - 1, position)})
        ]
        result.append(
            HourCandidate(
                absolute_minutes=minutes,
                clock_label=f"{hour % 24:02d}:{value:02d}",
                day_offset=minutes // 1440,
                hour_word_indices=refs,
                x_gap_points=max(box[0] - x, x - box[2], 0),
                normalized_tick_distance=min(abs(x - a) for a in xs) / m.hour_spacing_points,
            )
        )
    return result


def pairs(tokens, tolerance, check_budget):
    ends = XIndex([(t.token_index, t.display_bbox) for t in tokens if t.role == "END_SLOT"])
    pairs = []
    for start in tokens:
        if start.role != "START_SLOT":
            continue
        check_budget()
        width = start.display_bbox[2] - start.display_bbox[0]
        for index, box in ends.near(start.display_bbox, width * tolerance.pair_gap_width):
            end = tokens[index]
            gap = gaps(start.display_bbox, box)[0]
            if gap > min(width, box[2] - box[0]) * tolerance.pair_gap_width:
                continue
            ordering = [
                (a.absolute_minutes, b.absolute_minutes)
                for a in start.hour_candidates
                for b in end.hour_candidates
                if 0 < b.absolute_minutes - a.absolute_minutes <= tolerance.pair_max_hours * 60
            ]
            if not ordering:
                continue
            pairs.append(
                ExplicitPair(
                    pair_index=0,
                    start_token_index=start.token_index,
                    end_token_index=index,
                    display_bbox=bounds([start.display_bbox, box]),
                    horizontal_gap_points=gap,
                    candidate_orderings=ordering,
                    status="UNIQUE",
                    reasons=[
                        LayoutReason(
                            code="opposite_slots_local_alignment_and_time_order",
                            word_indices=sorted([start.word_index, end.word_index]),
                        )
                    ],
                )
            )
    degree = {}
    for p in pairs:
        for i in [p.start_token_index, p.end_token_index]:
            degree[i] = degree.get(i, 0) + 1
    pairs.sort(key=lambda p: (p.start_token_index, p.end_token_index))
    for i, p in enumerate(pairs):
        p.pair_index = i
        if (
            degree[p.start_token_index] > 1
            or degree[p.end_token_index] > 1
            or len(p.candidate_orderings) > 1
            or tokens[p.start_token_index].status != "UNIQUE"
            or tokens[p.end_token_index].status != "UNIQUE"
        ):
            p.status = "AMBIGUOUS"
            p.reasons.append(
                LayoutReason(
                    code="multiple_pair_or_member_alternatives",
                    word_indices=sorted(
                        [
                            tokens[p.start_token_index].word_index,
                            tokens[p.end_token_index].word_index,
                        ]
                    ),
                )
            )
    return pairs


def stream_blocks(tokens):
    # Connected overlapping time intervals form unresolved partial-order blocks.
    # All candidate values remain on their tokens; no total order is chosen inside
    # a block. Disjoint blocks have a proven chronological order, independent of x.
    timed = sorted(
        (t for t in tokens if t.hour_candidates),
        key=lambda t: (
            t.hour_candidates[0].absolute_minutes,
            t.hour_candidates[-1].absolute_minutes,
            t.token_index,
        ),
    )
    blocks = []
    for token in timed:
        lo, hi = (
            token.hour_candidates[0].absolute_minutes,
            token.hour_candidates[-1].absolute_minutes,
        )
        if blocks and lo <= blocks[-1].max_minutes:
            blocks[-1].token_indices.append(token.token_index)
            blocks[-1].max_minutes = max(blocks[-1].max_minutes, hi)
            blocks[-1].status = "AMBIGUOUS"
        else:
            blocks.append(
                StreamBlock(
                    block_index=len(blocks),
                    token_indices=[token.token_index],
                    min_minutes=lo,
                    max_minutes=hi,
                    status="UNIQUE" if lo == hi else "AMBIGUOUS",
                )
            )
    for b in blocks:
        b.token_indices.sort()
        b.reasons = [
            LayoutReason(
                code="overlapping_candidate_time_intervals"
                if b.status == "AMBIGUOUS"
                else "single_candidate_time",
                word_indices=sorted(tokens[i].word_index for i in b.token_indices),
            )
        ]
    return blocks


def detect_gaps(tokens, blocks):
    result = []
    previous = None
    at_boundary = True
    for block in blocks:
        if block.status != "UNIQUE":
            previous = None
            at_boundary = False
            continue
        current = tokens[block.token_indices[0]]
        if current.role is None:
            previous = None
            at_boundary = False
            continue
        expected = None
        if previous is None and at_boundary and current.role == "END_SLOT":
            expected = "START_SLOT"
        elif previous is not None and previous.role == current.role:
            expected = "START_SLOT" if current.role == "END_SLOT" else "END_SLOT"
        if expected:
            result.append(
                StructuralGap(
                    gap_index=len(result),
                    expected_role=expected,
                    before_token_index=previous.token_index if previous else None,
                    after_token_index=current.token_index,
                    reasons=[
                        LayoutReason(
                            code="structural_role_gap_no_value_inferred",
                            word_indices=sorted(
                                [current.word_index] + ([previous.word_index] if previous else [])
                            ),
                        )
                    ],
                )
            )
        previous = current
        at_boundary = False
    if previous is not None and previous.role == "START_SLOT":
        result.append(
            StructuralGap(
                gap_index=len(result),
                expected_role="END_SLOT",
                before_token_index=previous.token_index,
                after_token_index=None,
                reasons=[
                    LayoutReason(
                        code="open_trailing_start_slot", word_indices=[previous.word_index]
                    )
                ],
            )
        )
    unresolved = XIndex(
        [(t.token_index, t.display_bbox) for t in tokens if not t.hour_candidates or t.role is None]
    )
    min_x = min((t.display_bbox[0] for t in tokens), default=0)
    max_x = max((t.display_bbox[2] for t in tokens), default=0)
    for gap in result:
        members = [
            tokens[i] for i in [gap.before_token_index, gap.after_token_index] if i is not None
        ]
        # Open gaps include the observed prefix/suffix, not just the remaining
        # boundary token's bbox. X limits select related visual evidence only;
        # they never order the stream or assign a time to unresolved tokens.
        lo = min_x if gap.before_token_index is None else min(t.display_bbox[0] for t in members)
        hi = max_x if gap.after_token_index is None else max(t.display_bbox[2] for t in members)
        before = tokens[gap.before_token_index] if gap.before_token_index is not None else None
        after = tokens[gap.after_token_index] if gap.after_token_index is not None else None
        uncertain = [
            tokens[i]
            for i, box in unresolved.near((lo, 0, hi, 0), 0)
            if box[0] <= hi
            and box[2] >= lo
            and gap.expected_role
            in {"START_SLOT" if b % 2 else "END_SLOT" for b in tokens[i].band_candidates}
            and (
                not tokens[i].hour_candidates
                or any(
                    (
                        before is None
                        or c.absolute_minutes >= before.hour_candidates[0].absolute_minutes
                    )
                    and (
                        after is None
                        or c.absolute_minutes <= after.hour_candidates[-1].absolute_minutes
                    )
                    for c in tokens[i].hour_candidates
                )
            )
        ]
        if uncertain:
            gap.status = "AMBIGUOUS"
            gap.uncertain_token_indices = sorted(t.token_index for t in uncertain)
            gap.reasons.append(
                LayoutReason(
                    code="unresolved_local_event_alternatives",
                    word_indices=sorted(t.word_index for t in uncertain),
                )
            )
    return result
