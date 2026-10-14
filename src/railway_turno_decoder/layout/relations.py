"""Local many-to-many geometric candidates, without semantic assignment."""

from railway_turno_decoder.layout.geometry import XIndex, gaps, measures, segment_box
from railway_turno_decoder.layout.models import (
    LayoutReason,
    NodeRef,
    SegmentAssociation,
    SpatialRelation,
)


def build_relations(row, temporal_row, tolerance, check_budget):
    spacing = temporal_row.axis.mapping.hour_spacing_points
    radius = spacing * tolerance.relation_gap_spacing
    vertical = (row.source_row_bbox[3] - row.source_row_bbox[1]) * tolerance.relation_vertical_row
    targets = {
        "MINUTE": [(t.token_index, t.display_bbox) for t in row.minute_tokens],
        "PAIR": [(p.pair_index, p.display_bbox) for p in row.explicit_pairs],
        "BELOW": [(b.evidence_index, b.display_bbox) for b in row.below_axis],
        "SEGMENT": [(s.segment_index, segment_box(s)) for s in temporal_row.segments],
    }
    indexes = {kind: XIndex(items) for kind, items in targets.items()}
    relations = []
    associations = {s.segment_index: [] for s in temporal_row.segments}

    def add(cluster, kind, index, box, status="UNIQUE"):
        if len(relations) >= tolerance.max_relations:
            raise ValueError("layout relation budget exceeded")
        segment = temporal_row.segments[index] if kind == "SEGMENT" else None
        relations.append(
            SpatialRelation(
                relation_index=0,
                source=NodeRef(kind="CLUSTER", index=cluster.cluster_index),
                target=NodeRef(kind=kind, index=index),
                **measures(cluster.display_bbox, box, spacing, segment),
                status=status,
                reasons=[
                    LayoutReason(
                        code="local_display_geometry",
                        word_indices=sorted(cluster.word_indices),
                        segment_indices=[index] if segment else [],
                    )
                ],
            )
        )

    for cluster in row.clusters:
        check_budget()
        for kind, index in indexes.items():
            for j, box in index.near(cluster.display_bbox, radius):
                xgap, ygap = gaps(cluster.display_bbox, box)
                if xgap > radius or ygap > vertical:
                    continue
                add(cluster, kind, j, box)
                if kind == "SEGMENT":
                    cluster.candidate_segment_indices.append(j)
                    associations[j].append(cluster.cluster_index)
        cluster.candidate_segment_indices.sort()
    for a, b in zip(row.clusters, row.clusters[1:], strict=False):
        add(a, "CLUSTER", b.cluster_index, b.display_bbox)
    below_reverse = {s.segment_index: [] for s in temporal_row.segments}
    below_clusters = {e.evidence_index: [] for e in row.below_axis}
    for relation in relations:
        if relation.target.kind == "BELOW":
            below_clusters[relation.target.index].append(relation.source.index)
    for evidence in row.below_axis:
        check_budget()
        evidence.nearby_cluster_indices = sorted(below_clusters[evidence.evidence_index])
        for j, box in indexes["SEGMENT"].near(evidence.display_bbox, radius):
            if (
                gaps(evidence.display_bbox, box)[0] > radius
                or gaps(evidence.display_bbox, box)[1] > vertical
            ):
                continue
            evidence.nearby_segment_indices.append(j)
            below_reverse[j].append(evidence.evidence_index)
            if len(relations) >= tolerance.max_relations:
                raise ValueError("layout relation budget exceeded")
            relations.append(
                SpatialRelation(
                    relation_index=0,
                    source=NodeRef(kind="BELOW", index=evidence.evidence_index),
                    target=NodeRef(kind="SEGMENT", index=j),
                    **measures(evidence.display_bbox, box, spacing, temporal_row.segments[j]),
                    status="UNIQUE",
                    reasons=[
                        LayoutReason(
                            code="below_axis_segment_proximity",
                            word_indices=[evidence.word_index],
                            segment_indices=[j],
                        )
                    ],
                )
            )
        evidence.nearby_segment_indices.sort()
    for evidence in row.below_axis:
        refs = evidence.nearby_segment_indices
        evidence.segment_status = (
            "UNASSOCIATED"
            if not refs
            else "UNIQUE"
            if len(refs) == 1 and len(below_reverse[refs[0]]) == 1
            else "AMBIGUOUS"
        )
        evidence.reasons.append(
            LayoutReason(
                code="below_segment_candidate_degrees",
                word_indices=[evidence.word_index],
                segment_indices=refs,
            )
        )
    for cluster in row.clusters:
        matches = cluster.candidate_segment_indices
        cluster.segment_status = (
            "UNASSOCIATED"
            if not matches
            else "UNIQUE"
            if len(matches) == 1 and len(associations[matches[0]]) == 1
            else "AMBIGUOUS"
        )
        cluster.reasons.append(
            LayoutReason(
                code="cluster_segment_candidate_degrees",
                word_indices=sorted(cluster.word_indices),
                segment_indices=matches,
            )
        )
    for relation in relations:
        if relation.target.kind == "SEGMENT":
            relation.status = (
                row.clusters[relation.source.index].segment_status
                if relation.source.kind == "CLUSTER"
                else row.below_axis[relation.source.index].segment_status
            )
    relations.sort(key=lambda r: (r.source.kind, r.source.index, r.target.kind, r.target.index))
    for i, relation in enumerate(relations):
        relation.relation_index = i
    row.relations = relations
    row.segment_associations = [
        SegmentAssociation(
            segment_index=i,
            cluster_indices=sorted(refs),
            status="UNASSOCIATED"
            if not refs
            else "UNIQUE"
            if len(refs) == 1 and len(row.clusters[refs[0]].candidate_segment_indices) == 1
            else "AMBIGUOUS",
            reasons=[
                LayoutReason(
                    code="cluster_segment_candidate_degrees",
                    word_indices=sorted({j for c in refs for j in row.clusters[c].word_indices}),
                    segment_indices=[i],
                )
            ],
        )
        for i, refs in associations.items()
    ]
