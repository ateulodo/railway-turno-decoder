"""Local deterministic hour-lattice hypotheses, verified before affine fitting."""

from collections.abc import Callable
from math import sqrt
from statistics import median

from railway_turno_decoder.raw_models import RawPage, RawRow, RawWord
from railway_turno_decoder.temporal.models import (
    HourAnchor,
    TemporalAxis,
    TemporalReason,
    TimeMapping,
)
from railway_turno_decoder.temporal.tolerances import TemporalTolerances


def center(word: RawWord):
    b = word.display_bbox
    return ((b[0] + b[2]) / 2, (b[1] + b[3]) / 2)


def numeric(word: RawWord, maximum: int):
    return (
        word.text.isascii()
        and word.text.isdigit()
        and len(word.text) <= 2
        and int(word.text) <= maximum
    )


def horizontal_support(page, row, y, lo, hi, tolerance):
    refs = []
    intervals = []
    ys = []
    glyph_height = height_for_support(page, row)
    for ref in row.provenance.drawing_item_refs:
        item = page.drawings[ref.drawing_index].items[ref.item_index]
        if item.kind != "l" or len(item.display_points) != 2:
            continue
        a, b = item.display_points
        if (
            abs(a[1] - b[1]) > tolerance.geometry_points
            or abs((a[1] + b[1]) / 2 - y)
            > tolerance.support_height_fraction * glyph_height + tolerance.geometry_points
        ):
            continue
        left = max(lo, min(a[0], b[0]))
        right = min(hi, max(a[0], b[0]))
        if left < right:
            refs.append(ref)
            intervals.append((left, right))
            ys.append((a[1] + b[1]) / 2)
    total = 0.0
    end = lo
    for left, right in sorted(intervals):
        total += max(0.0, right - max(left, end))
        end = max(end, right)
    refs.sort(key=lambda ref: (ref.drawing_index, ref.item_index))
    return refs, median(ys) if ys else y, total / (hi - lo)


def height_for_support(page, row):
    # Typical numeric glyph height, independent of styles or activity names.
    heights = [
        page.words[i].display_bbox[3] - page.words[i].display_bbox[1]
        for i in row.provenance.word_indices
        if numeric(page.words[i], 24)
    ]
    return median(heights) if heights else 1.0


def _fit(words, tolerance):
    targets = [float(int(words[0].text) % 24 * 60)]
    for previous, current in zip(words, words[1:], strict=False):
        delta = (int(current.text) % 24 - int(previous.text) % 24) % 24
        if not 1 <= delta <= tolerance.max_hour_gap:
            return None
        targets.append(targets[-1] + 60 * delta)
    xs = [center(w)[0] for w in words]
    mx = sum(xs) / len(xs)
    mt = sum(targets) / len(targets)
    denominator = sum((x - mx) ** 2 for x in xs)
    if denominator <= 0:
        return None
    slope = sum((x - mx) * (t - mt) for x, t in zip(xs, targets, strict=True)) / denominator
    if slope <= 0:
        return None
    intercept = mt - slope * mx
    residuals = [abs(slope * x + intercept - t) for x, t in zip(xs, targets, strict=True)]
    if max(residuals) > tolerance.axis_residual_minutes:
        return None
    return (
        TimeMapping(
            minutes_per_point=slope,
            intercept_minutes=intercept,
            origin_x=xs[0],
            origin_absolute_minutes=targets[0],
            min_x=xs[0],
            max_x=xs[-1],
            hour_spacing_points=60 / slope,
            max_residual_minutes=max(residuals),
            rms_residual_minutes=sqrt(sum(r * r for r in residuals) / len(residuals)),
        ),
        targets,
        residuals,
    )


def detect_axis(
    page: RawPage, row: RawRow, tolerance: TemporalTolerances, check_budget: Callable[[], None]
):
    words = [
        page.words[i]
        for i in row.provenance.word_indices
        if numeric(page.words[i], 24) and i not in row.provenance.anchor_word_indices
    ]
    lines = []
    for word in sorted(words, key=lambda w: (center(w)[1], *w.display_bbox, w.text, w.word_index)):
        if lines:
            anchor = lines[-1][0]
            threshold = max(
                tolerance.line_tolerance_points,
                tolerance.line_height_fraction
                * min(
                    word.display_bbox[3] - word.display_bbox[1],
                    anchor.display_bbox[3] - anchor.display_bbox[1],
                ),
            )
            if center(word)[1] - center(anchor)[1] <= threshold:
                lines[-1].append(word)
                continue
        lines.append([word])
    hypotheses = {}
    warnings = []
    for line in lines:
        check_budget()
        if len(line) < tolerance.min_hour_anchors:
            continue
        if len(line) > tolerance.max_axis_words:
            warnings.append("Axis candidate line exceeds bounded hypothesis budget.")
            continue
        ordered = sorted(line, key=lambda w: (center(w)[0], *w.display_bbox, w.text, w.word_index))
        for i, left in enumerate(ordered):
            check_budget()
            for right in ordered[i + 1 : i + 1 + tolerance.pair_neighbors]:
                dx = center(right)[0] - center(left)[0]
                delta = (int(right.text) % 24 - int(left.text) % 24) % 24
                if dx <= 0 or not 1 <= delta <= tolerance.max_hour_gap:
                    continue
                slope = 60 * delta / dx
                intercept = (int(left.text) % 24) * 60 - slope * center(left)[0]
                inliers = []
                for word in ordered:
                    prediction = slope * center(word)[0] + intercept
                    label = (int(word.text) % 24) * 60
                    residual = abs(prediction - (label + 1440 * round((prediction - label) / 1440)))
                    if residual <= tolerance.axis_residual_minutes:
                        inliers.append(word)
                key = tuple(w.word_index for w in inliers)
                if (
                    key in hypotheses
                    or len(inliers) < tolerance.min_hour_anchors
                    or len(inliers) / len(line) < tolerance.min_axis_inlier_fraction
                ):
                    continue
                span = center(inliers[-1])[0] - center(inliers[0])[0]
                widths = median(w.display_bbox[2] - w.display_bbox[0] for w in inliers)
                if span <= 0 or span < tolerance.min_axis_span_label_widths * widths:
                    continue
                fitted = _fit(inliers, tolerance)
                if fitted is None:
                    continue
                refs, y, coverage = horizontal_support(
                    page,
                    row,
                    median(center(w)[1] for w in inliers),
                    center(inliers[0])[0],
                    center(inliers[-1])[0],
                    tolerance,
                )
                if coverage < tolerance.support_coverage_fraction:
                    continue
                mapping, targets, residuals = fitted
                hypotheses[key] = TemporalAxis(
                    display_y=y,
                    anchors=[
                        HourAnchor(
                            raw_label=w.text,
                            hour_candidate=int(w.text),
                            word_index=w.word_index,
                            display_point=center(w),
                            absolute_minutes=t,
                            residual_minutes=r,
                            reasons=[
                                TemporalReason(
                                    code="aligned_hour_lattice", word_indices=[w.word_index]
                                )
                            ],
                        )
                        for w, t, r in zip(inliers, targets, residuals, strict=True)
                    ],
                    mapping=mapping,
                    support_refs=refs,
                    rejected_word_indices=[
                        w.word_index for w in ordered if w.word_index not in key
                    ],
                    reasons=[
                        TemporalReason(
                            code="monotone_hour_lattice_and_horizontal_support",
                            word_indices=list(key),
                            primitive_refs=refs,
                        )
                    ],
                )
    if not hypotheses:
        return None, warnings + [
            "AMBIGUOUS: insufficient coherent hour anchors and vector support."
        ]
    strongest = max(len(axis.anchors) for axis in hypotheses.values())
    winners = [axis for axis in hypotheses.values() if len(axis.anchors) == strongest]
    if len(winners) != 1:
        return None, warnings + ["AMBIGUOUS: multiple equally supported temporal axes."]
    axis = winners[0]
    if axis.rejected_word_indices:
        warnings.append("Numeric outliers on the axis line were excluded; references retained.")
    return axis, warnings
