"""Cross-layer cache validation, without re-clustering text or refitting M3A."""

from itertools import combinations

from railway_turno_decoder.layout.geometry import bounds, center, measures, segment_box, valid_box
from railway_turno_decoder.layout.identity import temporal_identity
from railway_turno_decoder.layout.stream import detect_gaps, hour_candidates, pairs, stream_blocks
from railway_turno_decoder.layout.typography import (
    band_memberships,
    band_template,
    below_owners,
    minute_words,
    row_scale,
    usable_row,
)
from railway_turno_decoder.temporal.models import TemporalPage, same_number
from railway_turno_decoder.temporal.validation import validate_against_raw


def validate_layout(layout, page, temporal, check_budget=lambda: None):
    temporal = TemporalPage.model_validate(temporal.model_dump())
    validate_against_raw(temporal, page, check_budget)
    if (
        layout.page_index != page.page_index
        or layout.page_type != page.page_type
        or layout.source_sha256 != temporal.source_sha256
        or layout.source_extraction_id != temporal.source_extraction_id
        or layout.temporal_identity != temporal_identity(temporal)
        or len(layout.rows) != len(temporal.rows)
    ):
        raise ValueError("layout source identity/rows mismatch")
    tol = layout.tolerances
    below = below_owners(page, temporal, tol)
    below_set = {i for refs in below.values() for i in refs}
    candidates = (
        {
            r.row_index: minute_words(page, r, temporal.rows[r.row_index], tol, below_set)
            for r in page.rows
        }
        if page.page_type == "DUTY_DIAGRAM"
        else {}
    )
    template = band_template(page, temporal, candidates, tol)
    phases = (
        [list(c) for c in combinations(range(1, 5), len(template))]
        if 0 < len(template) <= 4
        else []
    )
    if layout.band_group_offsets != template or layout.band_phase_candidates != phases:
        raise ValueError("band phase alternatives mismatch")
    sources = page.rows if page.page_type == "DUTY_DIAGRAM" else []
    for row, source, tr in zip(layout.rows, sources, temporal.rows, strict=True):
        check_budget()
        source_refs = set(source.provenance.word_indices)
        allowed = (
            source_refs | set(below[row.row_index]) | set(source.provenance.anchor_word_indices)
        )
        if (
            row.row_index != source.row_index
            or row.source_row_bbox != source.display_bbox
            or row.source_anchor_word_indices != sorted(source.provenance.anchor_word_indices)
            or row.axis_hour_word_indices
            != ([a.word_index for a in tr.axis.anchors] if tr.axis else [])
        ):
            raise ValueError("layout source row mismatch")

        def word(i, scope=allowed):
            if not 0 <= i < len(page.words) or i not in scope:
                raise ValueError("layout word outside source region")
            return page.words[i]

        def segment(i, source=tr):
            if not 0 <= i < len(source.segments):
                raise ValueError("layout segment outside temporal row")
            return source.segments[i]

        objects = [
            *row.clusters,
            *row.bands,
            *row.minute_tokens,
            *row.explicit_pairs,
            *row.stream_blocks,
            *row.segment_associations,
            *row.gaps,
            *row.below_axis,
            *row.relations,
            row,
        ]
        for obj in objects:
            for reason in obj.reasons:
                for i in reason.word_indices:
                    word(i)
                for i in reason.segment_indices:
                    segment(i)
        bad_source = not usable_row(page, source, tol)
        if bad_source or tr.axis is None:
            expected = "INVALID" if bad_source else "AMBIGUOUS"
            if row.status != expected or any(
                [
                    row.clusters,
                    row.bands,
                    row.minute_tokens,
                    row.below_axis,
                    row.relations,
                    row.explicit_pairs,
                    row.gaps,
                    row.stream_blocks,
                    row.segment_associations,
                ]
            ):
                raise ValueError("unavailable source cannot claim layout interpretations")
            continue
        hour_refs = set(row.axis_hour_word_indices)
        seen = []
        for cluster in row.clusters:
            ws = [word(i, source_refs) for i in cluster.word_indices]
            if any(i in hour_refs or i in below_set for i in cluster.word_indices):
                raise ValueError("cluster contains dedicated-layer word")
            if cluster.fragments != [w.text for w in ws] or cluster.display_bbox != bounds(
                [w.display_bbox for w in ws]
            ):
                raise ValueError("cluster/raw geometry or text mismatch")
            seen.extend(cluster.word_indices)
        height = row_scale(page, tr)
        expected_minutes = [
            i
            for i in candidates[row.row_index]
            if source.display_bbox[1]
            <= center(page.words[i].display_bbox)[1]
            < source.display_bbox[3]
        ]
        if [t.word_index for t in row.minute_tokens] != expected_minutes:
            raise ValueError("minute membership/order mismatch")
        for token in row.minute_tokens:
            raw = word(token.word_index, source_refs)
            choices, residual = band_memberships(
                (tr.axis.display_y - center(raw.display_bbox)[1]) / height, template, tol
            )
            if (
                token.raw_text != raw.text
                or token.value != int(raw.text)
                or token.display_bbox != raw.display_bbox
                or token.center != center(raw.display_bbox)
                or token.band_candidates != choices
                or token.observed_band_groups
                != [
                    j
                    for j, v in enumerate(template)
                    if abs(v - (tr.axis.display_y - center(raw.display_bbox)[1]) / height)
                    <= tol.band_y_height
                ]
                or token.band_residual_height != residual
                or token.hour_candidates
                != hour_candidates(token.value, raw.display_bbox, tr.axis, tol)
            ):
                raise ValueError("minute token/source evidence mismatch")
            status = (
                "UNIQUE"
                if len(choices) == len(token.hour_candidates) == 1
                else "UNASSOCIATED"
                if not token.hour_candidates
                else "AMBIGUOUS"
            )
            if token.status != status:
                raise ValueError("minute status mismatch")
            for i in token.endpoint_segment_indices:
                s = segment(i)
                if not any(
                    e.word_index == token.word_index and e.compatible
                    for end in [s.left, s.right]
                    for e in end.nearby_labels
                ):
                    raise ValueError("minute endpoint provenance mismatch")
            seen.append(token.word_index)
        if len(row.bands) != 4:
            raise ValueError("missing band slots")
        for band in row.bands:
            index = band.band_index
            expected = sorted(t.word_index for t in row.minute_tokens if index in t.band_candidates)
            y = tr.axis.display_y - template[index - 1] * height if len(template) == 4 else None
            if (
                band.word_indices != expected
                or band.display_y != y
                or band.role != ("START_SLOT" if index % 2 else "END_SLOT")
                or band.status != ("UNIQUE" if len(template) == 4 else "AMBIGUOUS")
            ):
                raise ValueError("band membership/geometry mismatch")
        if row.explicit_pairs != pairs(row.minute_tokens, tol, check_budget):
            raise ValueError("explicit pair alternatives mismatch")
        if row.stream_blocks != stream_blocks(row.minute_tokens) or row.gaps != detect_gaps(
            row.minute_tokens, row.stream_blocks
        ):
            raise ValueError("stream/gap structure mismatch")
        if [b.word_index for b in row.below_axis] != below[row.row_index]:
            raise ValueError("below-axis ownership mismatch")
        for evidence in row.below_axis:
            raw = word(evidence.word_index)
            if (
                evidence.raw_text != raw.text
                or evidence.value != int(raw.text)
                or evidence.display_bbox != raw.display_bbox
                or evidence.center != center(raw.display_bbox)
            ):
                raise ValueError("below-axis/raw mismatch")
        for excluded in row.excluded_words:
            word(excluded.word_index, source_refs)
        covered = seen + [e.word_index for e in row.excluded_words]
        if set(covered) != source_refs or len(covered) != len(set(covered)):
            # A below-axis word may also be an explicit exclusion from its M2 row.
            raise ValueError("layout must account for every source-row word once")

        def box(ref, row=row, segment=segment):
            match ref.kind:
                case "CLUSTER":
                    return row.clusters[ref.index].display_bbox
                case "MINUTE":
                    return row.minute_tokens[ref.index].display_bbox
                case "PAIR":
                    return row.explicit_pairs[ref.index].display_bbox
                case "BELOW":
                    return row.below_axis[ref.index].display_bbox
                case "SEGMENT":
                    return segment_box(segment(ref.index))

        cluster_links = {c.cluster_index: [] for c in row.clusters}
        reverse = {s.segment_index: [] for s in tr.segments}
        below_links = {e.evidence_index: [] for e in row.below_axis}
        below_reverse = {s.segment_index: [] for s in tr.segments}
        below_clusters = {e.evidence_index: [] for e in row.below_axis}
        relation_keys = []
        for relation in row.relations:
            check_budget()
            key = (
                relation.source.kind,
                relation.source.index,
                relation.target.kind,
                relation.target.index,
            )
            relation_keys.append(key)
            if relation.source.kind not in ("CLUSTER", "BELOW") or (
                relation.source.kind == "BELOW" and relation.target.kind != "SEGMENT"
            ):
                raise ValueError("unsupported layout relation source")
            target = segment(relation.target.index) if relation.target.kind == "SEGMENT" else None
            expected = measures(
                box(relation.source),
                box(relation.target),
                tr.axis.mapping.hour_spacing_points,
                target,
            )
            for field, value in expected.items():
                actual = getattr(relation, field)
                if (
                    (actual is None or not same_number(value, actual))
                    if isinstance(value, (float, int))
                    else value != actual
                ):
                    raise ValueError("relation geometry mismatch")
            if target and relation.source.kind == "CLUSTER":
                cluster_links[relation.source.index].append(relation.target.index)
                reverse[relation.target.index].append(relation.source.index)
            elif target:
                below_links[relation.source.index].append(relation.target.index)
                below_reverse[relation.target.index].append(relation.source.index)
            elif relation.target.kind == "BELOW":
                below_clusters[relation.target.index].append(relation.source.index)
        if relation_keys != sorted(set(relation_keys)):
            raise ValueError("duplicate or noncanonical relations")
        for c in row.clusters:
            links = sorted(cluster_links[c.cluster_index])
            status = (
                "UNASSOCIATED"
                if not links
                else "UNIQUE"
                if len(links) == 1 and len(reverse[links[0]]) == 1
                else "AMBIGUOUS"
            )
            if c.candidate_segment_indices != links or c.segment_status != status:
                raise ValueError("cluster/segment alternative mismatch")
            if c.status != ("AMBIGUOUS" if c.orientation == "MIXED" else "UNIQUE"):
                raise ValueError("cluster geometry status mismatch")
        for evidence in row.below_axis:
            refs = sorted(below_links[evidence.evidence_index])
            status = (
                "UNASSOCIATED"
                if not refs
                else "UNIQUE"
                if len(refs) == 1 and len(below_reverse[refs[0]]) == 1
                else "AMBIGUOUS"
            )
            if (
                evidence.nearby_segment_indices != refs
                or evidence.nearby_cluster_indices
                != sorted(below_clusters[evidence.evidence_index])
                or evidence.segment_status != status
                or evidence.status != "UNIQUE"
            ):
                raise ValueError("below-axis candidate relations mismatch")
        for relation in row.relations:
            status = (
                (
                    row.clusters[relation.source.index].segment_status
                    if relation.source.kind == "CLUSTER"
                    else row.below_axis[relation.source.index].segment_status
                )
                if relation.target.kind == "SEGMENT"
                else "UNIQUE"
            )
            if relation.status != status:
                raise ValueError("relation status mismatch")
            if relation.target.kind != "SEGMENT" and any(
                v is not None
                for v in [
                    relation.distance_to_start,
                    relation.distance_to_end,
                    relation.distance_to_center,
                ]
            ):
                raise ValueError("non-segment relation cannot claim segment distances")
        if [a.segment_index for a in row.segment_associations] != list(range(len(tr.segments))):
            raise ValueError("missing segment association records")
        for association in row.segment_associations:
            refs = sorted(reverse[association.segment_index])
            status = (
                "UNASSOCIATED"
                if not refs
                else "UNIQUE"
                if len(refs) == 1 and len(cluster_links[refs[0]]) == 1
                else "AMBIGUOUS"
            )
            if association.cluster_indices != refs or association.status != status:
                raise ValueError("segment/cluster alternative mismatch")
        expected_stream = (
            "UNASSOCIATED"
            if not row.minute_tokens
            else "UNIQUE"
            if all(t.status == "UNIQUE" for t in row.minute_tokens)
            and all(b.status == "UNIQUE" for b in row.stream_blocks)
            else "AMBIGUOUS"
        )
        if row.stream_status != expected_stream:
            raise ValueError("stream status mismatch")
        invalid = any(
            not valid_box(page.words[i].display_bbox, page)
            or not source.display_bbox[1]
            <= center(page.words[i].display_bbox)[1]
            < source.display_bbox[3]
            for i in source_refs
        )
        expected_row = (
            "INVALID"
            if invalid
            else "AMBIGUOUS"
            if expected_stream == "AMBIGUOUS"
            or any(c.status == "AMBIGUOUS" or c.segment_status == "AMBIGUOUS" for c in row.clusters)
            or any(p.status == "AMBIGUOUS" for p in row.explicit_pairs)
            or any(e.segment_status == "AMBIGUOUS" for e in row.below_axis)
            else "UNIQUE"
        )
        if row.status != expected_row:
            raise ValueError("layout row status mismatch")
    check_budget()
