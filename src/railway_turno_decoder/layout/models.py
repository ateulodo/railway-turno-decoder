"""Separate layout 0.1 schema: references, geometry and partial orders only."""

from typing import Literal
from uuid import UUID

from pydantic import Field, FiniteFloat, model_validator

from railway_turno_decoder.layout.tolerances import ALGORITHM_VERSION, LayoutTolerances
from railway_turno_decoder.raw_models import BBox, PageType, Point, RawModel

Status = Literal["UNIQUE", "AMBIGUOUS", "UNASSOCIATED", "INVALID"]
Role = Literal["START_SLOT", "END_SLOT"]


class LayoutReason(RawModel):
    code: str
    word_indices: list[int] = Field(default_factory=list)
    segment_indices: list[int] = Field(default_factory=list)


class TextCluster(RawModel):
    cluster_index: int = Field(ge=0)
    word_indices: list[int]
    fragments: list[str]
    display_bbox: BBox
    orientation: Literal["HORIZONTAL", "VERTICAL", "SINGLE", "MIXED"]
    status: Status
    segment_status: Status = "UNASSOCIATED"
    candidate_segment_indices: list[int] = Field(default_factory=list)
    reasons: list[LayoutReason]


class MinuteBand(RawModel):
    band_index: int = Field(ge=1, le=4)
    role: Role
    display_y: FiniteFloat | None
    word_indices: list[int]
    status: Status
    reasons: list[LayoutReason]


class HourCandidate(RawModel):
    absolute_minutes: int
    clock_label: str
    day_offset: int
    hour_word_indices: list[int]
    x_gap_points: FiniteFloat = Field(ge=0)
    normalized_tick_distance: FiniteFloat = Field(ge=0)


class MinuteToken(RawModel):
    token_index: int = Field(ge=0)
    word_index: int = Field(ge=0)
    raw_text: str
    value: int = Field(ge=0, le=59)
    display_bbox: BBox
    center: Point
    band_candidates: list[int]
    observed_band_groups: list[int] = Field(default_factory=list)
    band_index: int | None
    role: Role | None
    band_residual_height: FiniteFloat | None
    hour_candidates: list[HourCandidate]
    endpoint_segment_indices: list[int]
    status: Status
    reasons: list[LayoutReason]


class ExplicitPair(RawModel):
    pair_index: int = Field(ge=0)
    start_token_index: int = Field(ge=0)
    end_token_index: int = Field(ge=0)
    display_bbox: BBox
    horizontal_gap_points: FiniteFloat = Field(ge=0)
    candidate_orderings: list[tuple[int, int]]
    status: Status
    reasons: list[LayoutReason]


class StreamBlock(RawModel):
    block_index: int = Field(ge=0)
    token_indices: list[int]
    min_minutes: int
    max_minutes: int
    status: Status
    reasons: list[LayoutReason] = Field(default_factory=list)


class StructuralGap(RawModel):
    gap_index: int = Field(ge=0)
    expected_role: Role
    before_token_index: int | None
    after_token_index: int | None
    status: Status = "UNIQUE"
    uncertain_token_indices: list[int] = Field(default_factory=list)
    reasons: list[LayoutReason]


class BelowAxisNumericEvidence(RawModel):
    evidence_index: int = Field(ge=0)
    word_index: int = Field(ge=0)
    raw_text: str
    value: int = Field(ge=0)
    display_bbox: BBox
    center: Point
    source_scope: Literal["BELOW_AXIS_EXTENSION"] = "BELOW_AXIS_EXTENSION"
    status: Status = "UNIQUE"
    nearby_cluster_indices: list[int] = Field(default_factory=list)
    nearby_segment_indices: list[int] = Field(default_factory=list)
    segment_status: Status = "UNASSOCIATED"
    reasons: list[LayoutReason]


class NodeRef(RawModel):
    kind: Literal["CLUSTER", "MINUTE", "PAIR", "BELOW", "SEGMENT"]
    index: int = Field(ge=0)


class SpatialRelation(RawModel):
    relation_index: int = Field(ge=0)
    source: NodeRef
    target: NodeRef
    source_center: Point
    target_center: Point
    horizontal_overlap: FiniteFloat = Field(ge=0)
    horizontal_gap: FiniteFloat = Field(ge=0)
    vertical_gap: FiniteFloat = Field(ge=0)
    center_distance: FiniteFloat = Field(ge=0)
    normalized_distance: FiniteFloat = Field(ge=0)
    relative_positions: list[Literal["BEFORE", "AFTER", "ABOVE", "BELOW", "OVERLAPPING"]]
    distance_to_start: FiniteFloat | None = None
    distance_to_end: FiniteFloat | None = None
    distance_to_center: FiniteFloat | None = None
    status: Status
    reasons: list[LayoutReason]


class SegmentAssociation(RawModel):
    segment_index: int = Field(ge=0)
    cluster_indices: list[int]
    status: Status
    reasons: list[LayoutReason] = Field(default_factory=list)


class ExcludedWord(RawModel):
    word_index: int = Field(ge=0)
    reason: str


class LayoutRow(RawModel):
    page_index: int = Field(ge=0)
    row_index: int = Field(ge=0)
    source_row_bbox: BBox
    source_anchor_word_indices: list[int]
    temporal_row_index: int = Field(ge=0)
    axis_hour_word_indices: list[int]
    clusters: list[TextCluster] = Field(default_factory=list)
    bands: list[MinuteBand] = Field(default_factory=list)
    minute_tokens: list[MinuteToken] = Field(default_factory=list)
    explicit_pairs: list[ExplicitPair] = Field(default_factory=list)
    stream_blocks: list[StreamBlock] = Field(default_factory=list)
    stream_status: Status = "UNASSOCIATED"
    gaps: list[StructuralGap] = Field(default_factory=list)
    below_axis: list[BelowAxisNumericEvidence] = Field(default_factory=list)
    relations: list[SpatialRelation] = Field(default_factory=list)
    segment_associations: list[SegmentAssociation] = Field(default_factory=list)
    excluded_words: list[ExcludedWord] = Field(default_factory=list)
    status: Status
    reasons: list[LayoutReason]

    @model_validator(mode="after")
    def internal_references(self):
        for objects, key in [
            (self.clusters, "cluster_index"),
            (self.minute_tokens, "token_index"),
            (self.explicit_pairs, "pair_index"),
            (self.stream_blocks, "block_index"),
            (self.gaps, "gap_index"),
            (self.below_axis, "evidence_index"),
            (self.relations, "relation_index"),
        ]:
            if [getattr(x, key) for x in objects] != list(range(len(objects))):
                raise ValueError("layout indices must be sequential")
        if self.bands and [b.band_index for b in self.bands] != [1, 2, 3, 4]:
            raise ValueError("layout must describe all four band slots")
        owned = [i for c in self.clusters for i in c.word_indices]
        owned += [t.word_index for t in self.minute_tokens] + [
            b.word_index for b in self.below_axis
        ]
        if len(set(owned)) != len(owned):
            raise ValueError("word cannot belong to multiple layout layers")
        for c in self.clusters:
            if not c.word_indices or len(c.word_indices) != len(c.fragments):
                raise ValueError("cluster must preserve each fragment")
        n = len(self.minute_tokens)
        for token in self.minute_tokens:
            if not token.band_candidates or any(
                i not in (1, 2, 3, 4) for i in token.band_candidates
            ):
                raise ValueError("invalid minute band candidates")
            if token.band_index != (
                token.band_candidates[0] if len(token.band_candidates) == 1 else None
            ):
                raise ValueError("band choice must be univocal")
            roles = {"START_SLOT" if i % 2 else "END_SLOT" for i in token.band_candidates}
            if token.role != (next(iter(roles)) if len(roles) == 1 else None):
                raise ValueError("role must describe band parity only")
            if token.hour_candidates != sorted(
                token.hour_candidates, key=lambda c: c.absolute_minutes
            ):
                raise ValueError("hour candidates must be canonical")
        for pair in self.explicit_pairs:
            if not 0 <= pair.start_token_index < n or not 0 <= pair.end_token_index < n:
                raise ValueError("pair token outside row")
            if (
                self.minute_tokens[pair.start_token_index].role != "START_SLOT"
                or self.minute_tokens[pair.end_token_index].role != "END_SLOT"
            ):
                raise ValueError("pair requires opposite structural roles")
        streamed = [i for b in self.stream_blocks for i in b.token_indices]
        eligible = {t.token_index for t in self.minute_tokens if t.hour_candidates}
        if len(streamed) != len(set(streamed)) or set(streamed) != eligible:
            raise ValueError("partial stream must preserve every timed token once")
        for gap in self.gaps:
            if any(
                i is not None and not 0 <= i < n
                for i in [gap.before_token_index, gap.after_token_index]
            ):
                raise ValueError("gap reference outside row")
            if any(not 0 <= i < n for i in gap.uncertain_token_indices):
                raise ValueError("uncertain gap reference outside row")
        limits = {
            "CLUSTER": len(self.clusters),
            "MINUTE": n,
            "PAIR": len(self.explicit_pairs),
            "BELOW": len(self.below_axis),
        }
        for rel in self.relations:
            for ref in [rel.source, rel.target]:
                if ref.kind != "SEGMENT" and ref.index >= limits[ref.kind]:
                    raise ValueError("relation reference outside row")
        return self


class LayoutPage(RawModel):
    format: Literal["railway-turno-decoder.layout"] = "railway-turno-decoder.layout"
    schema_version: Literal["0.1"] = "0.1"
    algorithm_version: str = ALGORITHM_VERSION
    source_extraction_id: UUID
    source_sha256: str = Field(pattern=r"^[0-9a-f]{64}$")
    temporal_identity: str = Field(pattern=r"^[0-9a-f]{64}$")
    page_index: int = Field(ge=0)
    page_type: PageType
    coordinate_system: Literal["M2 display coordinates; points"] = "M2 display coordinates; points"
    tolerances: LayoutTolerances
    band_group_offsets: list[FiniteFloat] = Field(default_factory=list)
    band_phase_candidates: list[list[int]] = Field(default_factory=list)
    rows: list[LayoutRow]

    @model_validator(mode="after")
    def rows_match_page(self):
        if self.band_group_offsets != sorted(self.band_group_offsets, reverse=True):
            raise ValueError("band observations must preserve vertical order")
        for phase in self.band_phase_candidates:
            if (
                len(phase) != len(self.band_group_offsets)
                or phase != sorted(set(phase))
                or any(i not in (1, 2, 3, 4) for i in phase)
            ):
                raise ValueError("invalid correlated band phase")
        if any(
            i < 0 or i >= len(self.band_group_offsets)
            for r in self.rows
            for t in r.minute_tokens
            for i in t.observed_band_groups
        ):
            raise ValueError("minute observation outside band groups")
        if [r.row_index for r in self.rows] != list(range(len(self.rows))):
            raise ValueError("layout row indices must match source order")
        if any(
            r.page_index != self.page_index or r.temporal_row_index != r.row_index
            for r in self.rows
        ):
            raise ValueError("layout page/temporal row mismatch")
        if self.page_type != "DUTY_DIAGRAM" and self.rows:
            raise ValueError("non-duty page cannot contain layout rows")
        return self
