"""M3B consumes immutable M2/M3A and describes typography, never activities."""

from collections.abc import Callable
from itertools import combinations

from railway_turno_decoder.layout.geometry import center, valid_box
from railway_turno_decoder.layout.identity import temporal_identity
from railway_turno_decoder.layout.models import (
    BelowAxisNumericEvidence,
    ExcludedWord,
    LayoutPage,
    LayoutReason,
    LayoutRow,
    MinuteBand,
    MinuteToken,
)
from railway_turno_decoder.layout.relations import build_relations
from railway_turno_decoder.layout.stream import detect_gaps, hour_candidates, pairs, stream_blocks
from railway_turno_decoder.layout.tolerances import LayoutTolerances
from railway_turno_decoder.layout.typography import (
    band_memberships,
    band_template,
    below_owners,
    minute_words,
    row_scale,
    text_clusters,
    usable_row,
)
from railway_turno_decoder.raw_models import RawPage
from railway_turno_decoder.temporal.models import TemporalPage
from railway_turno_decoder.temporal.validation import validate_against_raw


def analyze_layout(
    page: RawPage,
    temporal: TemporalPage,
    *,
    tolerances=None,
    check_budget: Callable[[], None] = lambda: None,
) -> LayoutPage:
    page = RawPage.model_validate(page.model_dump())
    temporal = TemporalPage.model_validate(temporal.model_dump())
    validate_against_raw(temporal, page, check_budget)
    tolerance = tolerances or LayoutTolerances()
    below = below_owners(page, temporal, tolerance)
    below_refs = {i for refs in below.values() for i in refs}
    candidates = (
        {
            r.row_index: minute_words(page, r, temporal.rows[r.row_index], tolerance, below_refs)
            for r in page.rows
        }
        if page.page_type == "DUTY_DIAGRAM"
        else {}
    )
    template = band_template(page, temporal, candidates, tolerance)
    rows = []
    for source in page.rows if page.page_type == "DUTY_DIAGRAM" else []:
        check_budget()
        tr = temporal.rows[source.row_index]
        row = LayoutRow(
            page_index=page.page_index,
            row_index=source.row_index,
            source_row_bbox=source.display_bbox,
            source_anchor_word_indices=sorted(source.provenance.anchor_word_indices),
            temporal_row_index=source.row_index,
            axis_hour_word_indices=[a.word_index for a in tr.axis.anchors] if tr.axis else [],
            status="AMBIGUOUS",
            reasons=[],
        )
        refs = source.provenance.word_indices
        if not usable_row(page, source, tolerance):
            row.status = "INVALID"
            row.reasons = [
                LayoutReason(
                    code="invalid_row_geometry_or_reference_budget", word_indices=sorted(set(refs))
                )
            ]
            rows.append(row)
            continue
        if tr.axis is None:
            row.reasons = [
                LayoutReason(code="temporal_axis_unavailable", word_indices=sorted(refs))
            ]
            rows.append(row)
            continue
        axis = tr.axis
        height = row_scale(page, tr)
        invalid = {
            i
            for i in refs
            if not valid_box(page.words[i].display_bbox, page)
            or not source.display_bbox[1]
            <= center(page.words[i].display_bbox)[1]
            < source.display_bbox[3]
        }
        excluded = {i: "INVALID_GEOMETRY" for i in invalid}
        excluded.update({i: "ROW_ANCHOR" for i in source.provenance.anchor_word_indices})
        excluded.update({a.word_index: "HOUR_ANCHOR" for a in axis.anchors})
        excluded.update({i: "BELOW_AXIS_OWNER" for i in refs if i in below_refs})
        minute_refs = [i for i in candidates[source.row_index] if i not in invalid]
        evidence_refs = {
            e.word_index
            for s in tr.segments
            for end in [s.left, s.right]
            for e in end.nearby_labels
        }
        for i in evidence_refs - set(minute_refs) - below_refs:
            excluded[i] = "UNRESOLVED_ENDPOINT_NUMERIC_EVIDENCE"
        for i in refs:
            x, y = center(page.words[i].display_bbox)
            if not axis.mapping.min_x <= x <= axis.mapping.max_x:
                excluded[i] = "OUTSIDE_AXIS_EXTENT"
        text_refs = [i for i in refs if i not in excluded and i not in minute_refs]
        row.clusters = text_clusters(page, text_refs, tolerance, check_budget)
        endpoint_refs = {}
        for segment in tr.segments:
            for endpoint in (segment.left, segment.right):
                for evidence in endpoint.nearby_labels:
                    if evidence.compatible:
                        endpoint_refs.setdefault(evidence.word_index, set()).add(
                            segment.segment_index
                        )
        for i in minute_refs:
            word = page.words[i]
            offset = (axis.display_y - center(word.display_bbox)[1]) / height
            choices, residual = band_memberships(offset, template, tolerance)
            roles = {"START_SLOT" if b % 2 else "END_SLOT" for b in choices}
            hours = hour_candidates(int(word.text), word.display_bbox, axis, tolerance)
            segment_refs = sorted(endpoint_refs.get(i, set()))
            row.minute_tokens.append(
                MinuteToken(
                    token_index=len(row.minute_tokens),
                    word_index=i,
                    raw_text=word.text,
                    value=int(word.text),
                    display_bbox=word.display_bbox,
                    center=center(word.display_bbox),
                    band_candidates=choices,
                    observed_band_groups=[
                        j
                        for j, v in enumerate(template)
                        if abs(v - offset) <= tolerance.band_y_height
                    ],
                    band_index=choices[0] if len(choices) == 1 else None,
                    role=next(iter(roles)) if len(roles) == 1 else None,
                    band_residual_height=residual,
                    hour_candidates=hours,
                    endpoint_segment_indices=segment_refs,
                    status="UNIQUE"
                    if len(choices) == len(hours) == 1
                    else "UNASSOCIATED"
                    if not hours
                    else "AMBIGUOUS",
                    reasons=[
                        LayoutReason(
                            code=(
                                "multiple_band_or_hour_alternatives"
                                if len(choices) > 1 or len(hours) > 1
                                else "no_bounded_hour_candidate"
                                if not hours
                                else "numeric_in_axis_relative_minute_zone"
                            ),
                            word_indices=[i],
                            segment_indices=segment_refs,
                        )
                    ],
                )
            )
        for b in range(1, 5):
            members = sorted(t.word_index for t in row.minute_tokens if b in t.band_candidates)
            row.bands.append(
                MinuteBand(
                    band_index=b,
                    role="START_SLOT" if b % 2 else "END_SLOT",
                    display_y=axis.display_y - template[b - 1] * height
                    if len(template) == 4
                    else None,
                    word_indices=members,
                    status="UNIQUE" if len(template) == 4 else "AMBIGUOUS",
                    reasons=[
                        LayoutReason(
                            code="page_relative_y_modes"
                            if len(template) == 4
                            else "incomplete_band_phase",
                            word_indices=members,
                        )
                    ],
                )
            )
        row.explicit_pairs = pairs(row.minute_tokens, tolerance, check_budget)
        row.stream_blocks = stream_blocks(row.minute_tokens)
        row.stream_status = (
            "UNASSOCIATED"
            if not row.minute_tokens
            else "UNIQUE"
            if all(t.status == "UNIQUE" for t in row.minute_tokens)
            and all(b.status == "UNIQUE" for b in row.stream_blocks)
            else "AMBIGUOUS"
        )
        row.gaps = detect_gaps(row.minute_tokens, row.stream_blocks)
        row.below_axis = [
            BelowAxisNumericEvidence(
                evidence_index=j,
                word_index=i,
                raw_text=page.words[i].text,
                value=int(page.words[i].text),
                display_bbox=page.words[i].display_bbox,
                center=center(page.words[i].display_bbox),
                reasons=[LayoutReason(code="axis_relative_below_region", word_indices=[i])],
            )
            for j, i in enumerate(below[source.row_index])
        ]
        row.excluded_words = [
            ExcludedWord(word_index=i, reason=reason) for i, reason in sorted(excluded.items())
        ]
        build_relations(row, tr, tolerance, check_budget)
        row.status = (
            "INVALID"
            if invalid
            else "AMBIGUOUS"
            if row.stream_status == "AMBIGUOUS"
            or any(c.status == "AMBIGUOUS" or c.segment_status == "AMBIGUOUS" for c in row.clusters)
            or any(p.status == "AMBIGUOUS" for p in row.explicit_pairs)
            or any(e.segment_status == "AMBIGUOUS" for e in row.below_axis)
            else "UNIQUE"
        )
        row.reasons = [LayoutReason(code="display_layout_only", word_indices=sorted(refs))]
        rows.append(row)
    check_budget()
    return LayoutPage(
        source_extraction_id=temporal.source_extraction_id,
        source_sha256=temporal.source_sha256,
        temporal_identity=temporal_identity(temporal),
        page_index=page.page_index,
        page_type=page.page_type,
        tolerances=tolerance,
        band_group_offsets=template,
        band_phase_candidates=[list(c) for c in combinations(range(1, 5), len(template))]
        if 0 < len(template) <= 4
        else [],
        rows=rows,
    )
