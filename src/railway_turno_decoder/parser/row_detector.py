"""Deterministic gutter-anchored geometric bands. No service interpretation."""

from bisect import bisect_right
from dataclasses import dataclass
from statistics import median

from railway_turno_decoder.parser.page_classifier import gutter_markers, horizontal_items
from railway_turno_decoder.raw_models import (
    BBox,
    DrawingItemRef,
    RawPage,
    RawWord,
    Reason,
    RowProvenance,
)


@dataclass
class RowCandidate:
    display_bbox: BBox
    marker: str
    service_number: str | None
    provenance: RowProvenance
    reasons: list[Reason]


def _band_boundaries(
    markers: list[RawWord], height: float
) -> tuple[list[float], list[float]] | None:
    """Keep each anchor inside its own positive band, preserving marker order.

    Clamp each preferred top to the gap after the previous anchor. A larger
    font can no longer move a boundary backwards. Overlapping anchors cannot
    safely be separated by horizontal bands and reject the page segmentation.
    """
    boxes = [word.display_bbox for word in markers]
    if any(box[3] <= box[1] for box in boxes) or any(
        previous[3] > current[1] for previous, current in zip(boxes, boxes[1:], strict=False)
    ):
        return None
    tops = []
    for index, box in enumerate(boxes):
        preferred = box[1] - (box[3] - box[1]) * 0.5
        lower = boxes[index - 1][3] if index else height * 0.04
        tops.append(max(lower, preferred))
    step = (
        median(b - a for a, b in zip(tops, tops[1:], strict=False))
        if len(tops) > 1
        else height * 0.25
    )
    last = boxes[-1]
    bottom = min(height * 0.95, max(tops[-1] + step, last[3] + (last[3] - last[1]) * 0.5))
    ends = tops[1:] + [bottom]
    # Explicit preconditions for bisect and full anchor containment. Do not
    # sort the boundaries independently of their source markers.
    if not all(a < b for a, b in zip(tops, tops[1:], strict=False)) or not all(
        top <= box[1] < box[3] <= end and top < end
        for top, end, box in zip(tops, ends, boxes, strict=True)
    ):
        return None
    return tops, ends


def detect_rows(page: RawPage) -> tuple[list[RowCandidate], list[str]]:
    if page.page_type != "DUTY_DIAGRAM":
        return [], []
    markers = gutter_markers(page)
    if not markers:
        return [], []
    left = min(w.display_bbox[0] for w in markers)
    markers = sorted(
        (w for w in markers if w.display_bbox[0] <= left + max(3, page.width * 0.015)),
        key=lambda w: (w.display_bbox[1], w.word_index),
    )
    boundaries = _band_boundaries(markers, page.height)
    if boundaries is None:
        return [], [
            "Ambiguous gutter marker layout: overlapping or out-of-bounds anchors; "
            "cannot construct positive ordered bands. Page segmentation omitted."
        ]
    tops, ends = boundaries
    warnings = []
    members = [[] for _ in markers]
    refs = [[] for _ in markers]
    for word in page.words:
        center = (word.display_bbox[1] + word.display_bbox[3]) / 2
        index = bisect_right(tops, center) - 1
        if 0 <= index < len(markers) and center < ends[index]:
            members[index].append(word.word_index)
    for drawing in page.drawings:
        for item in drawing.items:
            box = item.display_bbox
            if box is None or box[2] < 0 or box[0] > page.width:
                continue
            first = bisect_right(ends, box[1])
            last = bisect_right(tops, box[3])
            for index in range(first, min(last, len(markers))):
                refs[index].append(
                    DrawingItemRef(drawing_index=drawing.drawing_index, item_index=item.item_index)
                )
    support = [False for _ in markers]
    for _, _, y in horizontal_items(page):
        index = bisect_right(tops, y) - 1
        if 0 <= index < len(markers) and y < ends[index]:
            support[index] = True
    rows = []
    for index, marker in enumerate(markers):
        if not support[index]:
            warnings.append("Gutter marker without horizontal geometry; row omitted.")
            continue
        numbers = [
            page.words[i]
            for i in members[index]
            if page.words[i].text.isascii()
            and page.words[i].text.isdigit()
            and len(page.words[i].text) <= 4
            and page.words[i].display_bbox[0]
            <= min(
                page.width * 0.1,
                marker.display_bbox[0] + 2 * (marker.display_bbox[3] - marker.display_bbox[1]),
            )
            and page.words[i].display_bbox[1] > marker.display_bbox[3]
        ]
        if len(numbers) > 1:
            warnings.append("Ambiguous service number; raw tokens retained without assignment.")
        anchors = [marker.word_index] + ([numbers[0].word_index] if len(numbers) == 1 else [])
        rows.append(
            RowCandidate(
                display_bbox=(0, tops[index], page.width, ends[index]),
                marker=marker.text,
                service_number=numbers[0].text if len(numbers) == 1 else None,
                provenance=RowProvenance(
                    page_index=page.page_index,
                    anchor_word_indices=anchors,
                    word_indices=members[index],
                    drawing_indices=sorted({ref.drawing_index for ref in refs[index]}),
                    drawing_item_refs=refs[index],
                ),
                reasons=[
                    Reason(
                        code="aligned_gutter_marker_and_horizontal_geometry",
                        word_indices=anchors,
                        drawing_indices=sorted({ref.drawing_index for ref in refs[index]}),
                    )
                ],
            )
        )
    return rows, warnings
