"""Pure M2 consumer: no PDF access, raw mutation, header correction or activities."""

from collections import Counter
from collections.abc import Callable, Iterable
from uuid import UUID

from railway_turno_decoder.raw_models import RawPage
from railway_turno_decoder.temporal.axis import detect_axis
from railway_turno_decoder.temporal.models import TemporalPage, TemporalReason, TemporalRow
from railway_turno_decoder.temporal.segments import detect_segments
from railway_turno_decoder.temporal.tolerances import TemporalTolerances


def analyze_page(
    page: RawPage,
    *,
    source_extraction_id: UUID,
    source_sha256: str,
    tolerances: TemporalTolerances | None = None,
    check_budget: Callable[[], None] = lambda: None,
) -> TemporalPage:
    # Validate mutable model instances too; malformed references must not silently
    # select unrelated raw objects. This copy is local to one page, never a document.
    page = RawPage.model_validate(page.model_dump())
    tolerance = tolerances or TemporalTolerances()
    rows = []
    if page.page_type == "DUTY_DIAGRAM":
        for row in page.rows:
            check_budget()
            left, top, right, bottom = row.display_bbox
            if not (0 <= left < right <= page.width and 0 <= top < bottom <= page.height):
                axis = None
                warnings = ["INVALID: row bounds outside display page."]
                segments = []
                status = "INVALID"
            else:
                axis, warnings = detect_axis(page, row, tolerance, check_budget)
                segments = detect_segments(page, row, axis, tolerance, check_budget) if axis else []
                status = (
                    "INVALID"
                    if any(s.status == "INVALID" for s in segments)
                    else (
                        "AMBIGUOUS"
                        if axis is None or any(s.status == "AMBIGUOUS" for s in segments)
                        else "DERIVED"
                    )
                )
            rows.append(
                TemporalRow(
                    page_index=page.page_index,
                    row_index=row.row_index,
                    source_row_bbox=row.display_bbox,
                    source_anchor_word_indices=row.provenance.anchor_word_indices,
                    status=status,
                    axis=axis,
                    segments=segments,
                    warnings=warnings,
                    reasons=[
                        TemporalReason(
                            code="m2_row_reference", word_indices=row.provenance.anchor_word_indices
                        )
                    ],
                )
            )
    check_budget()
    return TemporalPage(
        source_extraction_id=source_extraction_id,
        source_sha256=source_sha256,
        page_index=page.page_index,
        page_type=page.page_type,
        tolerances=tolerance,
        rows=rows,
        warnings=[]
        if page.page_type == "DUTY_DIAGRAM"
        else ["Not a DUTY_DIAGRAM; no temporal interpretation."],
    )


def summarize(pages: Iterable[TemporalPage]):
    rows = []
    endpoint_counts = Counter()
    axis_errors = []
    label_errors = []
    segments = 0
    anchors = 0
    crossings = 0
    axis_crossings = 0
    duty_pages = 0
    duty_pages_without_rows = 0
    for page in pages:
        if page.page_type != "DUTY_DIAGRAM":
            continue
        duty_pages += 1
        duty_pages_without_rows += int(not page.rows)
        for row in page.rows:
            rows.append(row)
            if row.axis:
                anchors += len(row.axis.anchors)
                axis_errors.append(row.axis.mapping.max_residual_minutes)
                axis_crossings += int(
                    row.axis.anchors[0].absolute_minutes // 1440
                    != row.axis.anchors[-1].absolute_minutes // 1440
                )
            for segment in row.segments:
                segments += 1
                crossings += int(segment.crosses_midnight)
                for endpoint in (segment.left, segment.right):
                    endpoint_counts[endpoint.status] += 1
                    if endpoint.nearby_labels:
                        label_errors.append(min(e.residual_minutes for e in endpoint.nearby_labels))

    def distribution(values):
        ordered = sorted(values)
        return {
            "count": len(values),
            "median": ordered[len(ordered) // 2] if ordered else None,
            "p95": ordered[min(len(ordered) - 1, int(len(ordered) * 0.95))] if ordered else None,
            "max": max(ordered) if ordered else None,
        }

    return {
        "duty_pages": duty_pages,
        "duty_pages_analyzed": duty_pages,
        "duty_pages_without_rows": duty_pages_without_rows,
        "rows": len(rows),
        "rows_with_valid_axis": sum(r.axis is not None for r in rows),
        "rows_without_axis": sum(r.axis is None for r in rows),
        "row_status": dict(Counter(r.status for r in rows)),
        "rows_ambiguous": sum(r.status == "AMBIGUOUS" for r in rows),
        "rows_invalid": sum(r.status == "INVALID" for r in rows),
        "endpoint_outlier_rows": [
            {
                "page_index": r.page_index,
                "row_index": r.row_index,
                "ambiguous_endpoints": sum(
                    e.status == "AMBIGUOUS" for s in r.segments for e in (s.left, s.right)
                ),
                "primitive_refs": [
                    s.source.model_dump() for s in r.segments if s.status == "AMBIGUOUS"
                ],
            }
            for r in rows
            if any(s.status == "AMBIGUOUS" for s in r.segments)
        ],
        "hour_anchors": anchors,
        "candidate_segments": segments,
        "endpoint_status": {
            s: endpoint_counts[s] for s in ["EXACT", "DERIVED", "AMBIGUOUS", "INVALID"]
        },
        "axis_max_residual_minutes": distribution(axis_errors),
        "nearest_label_residual_minutes": distribution(label_errors),
        "segments_crossing_midnight": crossings,
        "axes_crossing_midnight": axis_crossings,
        "outliers": [
            {
                "page_index": r.page_index,
                "row_index": r.row_index,
                "status": r.status,
                "warnings": r.warnings,
            }
            for r in rows
            if r.axis is None or r.warnings
        ],
    }
