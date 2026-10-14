"""Derived temporal schema. References identify raw objects, never activities."""

import math
from bisect import bisect_right
from typing import Literal
from uuid import UUID

from pydantic import Field, FiniteFloat, model_validator

from railway_turno_decoder.raw_models import BBox, DrawingItemRef, PageType, Point, RawModel
from railway_turno_decoder.temporal.tolerances import ALGORITHM_VERSION, TemporalTolerances

Status = Literal["EXACT", "DERIVED", "AMBIGUOUS", "INVALID"]


def same_number(a: float, b: float) -> bool:
    """Serialization/arithmetic roundoff only; never a detection tolerance."""
    return math.isclose(a, b, rel_tol=1e-12, abs_tol=1e-8)


class TemporalReason(RawModel):
    code: str
    word_indices: list[int] = Field(default_factory=list)
    primitive_refs: list[DrawingItemRef] = Field(default_factory=list)

    @model_validator(mode="after")
    def nonnegative_references(self):
        if any(i < 0 for i in self.word_indices) or any(
            r.drawing_index < 0 or r.item_index < 0 for r in self.primitive_refs
        ):
            raise ValueError("negative temporal reference")
        return self


class HourAnchor(RawModel):
    raw_label: str
    hour_candidate: int = Field(ge=0, le=24)
    word_index: int = Field(ge=0)
    display_point: Point
    absolute_minutes: FiniteFloat
    residual_minutes: FiniteFloat = Field(ge=0)
    reasons: list[TemporalReason]

    @model_validator(mode="after")
    def label_consistency(self):
        if (
            not self.raw_label.isascii()
            or not self.raw_label.isdigit()
            or len(self.raw_label) > 2
            or int(self.raw_label) != self.hour_candidate
            or not same_number(self.absolute_minutes % 1440, self.hour_candidate % 24 * 60)
        ):
            raise ValueError("hour anchor label/time mismatch")
        return self


class TimeMapping(RawModel):
    minutes_per_point: FiniteFloat = Field(gt=0)
    intercept_minutes: FiniteFloat
    origin_x: FiniteFloat
    origin_absolute_minutes: FiniteFloat
    min_x: FiniteFloat
    max_x: FiniteFloat
    hour_spacing_points: FiniteFloat = Field(gt=0)
    max_residual_minutes: FiniteFloat = Field(ge=0)
    rms_residual_minutes: FiniteFloat = Field(ge=0)
    # Day zero is the synthetic midnight preceding the first observed hour.
    time_convention: Literal["minutes_since_axis_day_zero_midnight"] = (
        "minutes_since_axis_day_zero_midnight"
    )

    @model_validator(mode="after")
    def ordered(self):
        if self.min_x >= self.max_x:
            raise ValueError("axis must have positive extent")
        if not same_number(self.minutes_per_point * self.hour_spacing_points, 60):
            raise ValueError("hour spacing/mapping mismatch")
        if not same_number(self.origin_x, self.min_x):
            raise ValueError("axis origin must be its first anchor")
        if not 0 <= self.origin_absolute_minutes < 1440:
            raise ValueError("axis origin must be in synthetic day zero")
        if self.rms_residual_minutes > self.max_residual_minutes and not same_number(
            self.rms_residual_minutes, self.max_residual_minutes
        ):
            raise ValueError("RMS exceeds maximum residual")
        return self

    def minutes_at(self, x: float) -> float:
        if not math.isfinite(x):
            raise ValueError("x must be finite")
        return self.minutes_per_point * x + self.intercept_minutes

    def x_at(self, minutes: float) -> float:
        if not math.isfinite(minutes):
            raise ValueError("minutes must be finite")
        return (minutes - self.intercept_minutes) / self.minutes_per_point


class TemporalAxis(RawModel):
    status: Status = "DERIVED"
    display_y: FiniteFloat
    anchors: list[HourAnchor]
    mapping: TimeMapping
    support_refs: list[DrawingItemRef]
    rejected_word_indices: list[int]
    reasons: list[TemporalReason]

    @model_validator(mode="after")
    def coherent_anchors(self):
        if self.status != "DERIVED" or len(self.anchors) < 3 or not self.support_refs:
            raise ValueError("axis requires derived mapping, anchors and support")
        if len({a.word_index for a in self.anchors}) != len(self.anchors):
            raise ValueError("duplicate hour anchor")
        if any(
            a.display_point[0] >= b.display_point[0] or a.absolute_minutes >= b.absolute_minutes
            for a, b in zip(self.anchors, self.anchors[1:], strict=False)
        ):
            raise ValueError("hour anchors must increase in x and time")
        m = self.mapping
        if not (
            same_number(m.min_x, self.anchors[0].display_point[0])
            and same_number(m.max_x, self.anchors[-1].display_point[0])
            and same_number(m.origin_absolute_minutes, self.anchors[0].absolute_minutes)
        ):
            raise ValueError("axis extent/origin mismatch")
        residuals = [
            abs(m.minutes_at(a.display_point[0]) - a.absolute_minutes) for a in self.anchors
        ]
        if any(
            not same_number(r, a.residual_minutes)
            for a, r in zip(self.anchors, residuals, strict=True)
        ) or not (
            same_number(max(residuals), m.max_residual_minutes)
            and same_number(
                math.sqrt(sum(r * r for r in residuals) / len(residuals)), m.rms_residual_minutes
            )
        ):
            raise ValueError("axis residual mismatch")
        if set(self.rejected_word_indices) & {a.word_index for a in self.anchors}:
            raise ValueError("accepted/rejected hour references overlap")
        return self


class MinuteEvidence(RawModel):
    word_index: int = Field(ge=0)
    raw_label: str
    minute_candidate: int = Field(ge=0, le=59)
    display_bbox: BBox
    x_distance_points: FiniteFloat = Field(ge=0)
    y_distance_points: FiniteFloat = Field(ge=0)
    residual_minutes: FiniteFloat = Field(ge=0)
    compatible: bool

    @model_validator(mode="after")
    def label_consistency(self):
        if (
            not self.raw_label.isascii()
            or not self.raw_label.isdigit()
            or len(self.raw_label) > 2
            or int(self.raw_label) != self.minute_candidate
        ):
            raise ValueError("minute label/value mismatch")
        return self


class TemporalEndpoint(RawModel):
    display_point: Point
    status: Status
    absolute_minutes: FiniteFloat | None
    minutes_from_axis_origin: FiniteFloat | None
    clock_label: str | None
    day_offset: int | None
    mapping_residual_minutes: FiniteFloat = Field(ge=0)
    rounding_residual_minutes: FiniteFloat | None = Field(ge=0)
    nearby_minute_label: MinuteEvidence | None
    nearby_labels: list[MinuteEvidence]
    supporting_hour_word_indices: list[int]
    reasons: list[TemporalReason]

    @model_validator(mode="after")
    def coherent_evidence(self):
        values = (
            self.absolute_minutes,
            self.minutes_from_axis_origin,
            self.clock_label,
            self.day_offset,
            self.rounding_residual_minutes,
        )
        if self.status == "INVALID":
            if any(v is not None for v in values) or self.nearby_labels or self.nearby_minute_label:
                raise ValueError("invalid endpoint must not claim a time or label")
            return self
        if any(v is None for v in values):
            raise ValueError("valid endpoint requires complete time information")
        compatible = [e for e in self.nearby_labels if e.compatible]
        selected = compatible[0] if len(compatible) == 1 else None
        expected = "EXACT" if selected else ("AMBIGUOUS" if self.nearby_labels else "DERIVED")
        if self.status != expected or self.nearby_minute_label != selected:
            raise ValueError("endpoint status/selected label mismatch")
        if len({e.word_index for e in self.nearby_labels}) != len(self.nearby_labels):
            raise ValueError("duplicate minute evidence reference")
        rounded = math.floor(self.absolute_minutes + 0.5)
        if (
            self.clock_label != f"{(rounded // 60) % 24:02d}:{rounded % 60:02d}"
            or self.day_offset != rounded // 1440
            or not same_number(self.rounding_residual_minutes, abs(rounded - self.absolute_minutes))
        ):
            raise ValueError("endpoint time/display mismatch")
        for evidence in self.nearby_labels:
            label = evidence.minute_candidate
            nearest = label + 60 * round((self.absolute_minutes - label) / 60)
            if not same_number(evidence.residual_minutes, abs(self.absolute_minutes - nearest)):
                raise ValueError("minute evidence residual mismatch")
        return self


class SegmentStyle(RawModel):
    drawing_type: str
    width: FiniteFloat | None
    dashes: str | None
    stroke_color: list[FiniteFloat] | None
    fill_color: list[FiniteFloat] | None
    stroke_opacity: FiniteFloat | None
    fill_opacity: FiniteFloat | None
    line_cap: list[int] | None
    line_join: FiniteFloat | None
    seqno: int | None
    layer: str | None


class TemporalSegment(RawModel):
    segment_index: int = Field(ge=0)
    source: DrawingItemRef
    source_start: Point
    source_end: Point
    left: TemporalEndpoint
    right: TemporalEndpoint
    style: SegmentStyle
    status: Status
    mapping_residual_minutes: FiniteFloat
    crosses_midnight: bool
    reasons: list[TemporalReason]

    @model_validator(mode="after")
    def ordered(self):
        if self.source.drawing_index < 0 or self.source.item_index < 0:
            raise ValueError("negative segment reference")
        left, right = sorted((self.source_start, self.source_end), key=lambda p: (p[0], p[1]))
        if self.left.display_point != left or self.right.display_point != right:
            raise ValueError("endpoint/source segment mismatch")
        if self.left.display_point[0] > self.right.display_point[0]:
            raise ValueError("endpoints must be normalized left to right")
        if (
            self.left.absolute_minutes is not None
            and self.right.absolute_minutes is not None
            and self.left.absolute_minutes > self.right.absolute_minutes
        ):
            raise ValueError("time must increase with display x")
        expected = (
            "INVALID"
            if "INVALID" in (self.left.status, self.right.status)
            else "AMBIGUOUS"
            if "AMBIGUOUS" in (self.left.status, self.right.status)
            else "EXACT"
            if self.left.status == self.right.status == "EXACT"
            else "DERIVED"
        )
        crossing = (
            self.left.absolute_minutes is not None
            and self.right.absolute_minutes is not None
            and math.floor(self.left.absolute_minutes / 1440)
            != math.floor(self.right.absolute_minutes / 1440)
        )
        if self.status != expected or self.crosses_midnight != crossing:
            raise ValueError("segment status/crossing mismatch")
        refs = [ref for reason in self.reasons for ref in reason.primitive_refs]
        if not refs or any(ref != self.source for ref in refs):
            raise ValueError("segment reason/source mismatch")
        return self


class TemporalRow(RawModel):
    page_index: int = Field(ge=0)
    row_index: int = Field(ge=0)
    source_row_bbox: BBox
    source_anchor_word_indices: list[int]
    status: Status
    axis: TemporalAxis | None
    segments: list[TemporalSegment]
    warnings: list[str]
    reasons: list[TemporalReason]

    @model_validator(mode="after")
    def coherent_axis_and_segments(self):
        if self.axis is None:
            if self.segments or self.status not in ("AMBIGUOUS", "INVALID"):
                raise ValueError("row without axis cannot claim temporal segments")
            return self
        expected = (
            "INVALID"
            if any(s.status == "INVALID" for s in self.segments)
            else "AMBIGUOUS"
            if any(s.status == "AMBIGUOUS" for s in self.segments)
            else "DERIVED"
        )
        if self.status != expected:
            raise ValueError("row status/segments mismatch")
        m = self.axis.mapping
        xs = [a.display_point[0] for a in self.axis.anchors]
        for segment in self.segments:
            if not same_number(segment.mapping_residual_minutes, m.max_residual_minutes):
                raise ValueError("segment/axis residual mismatch")
            for e in (segment.left, segment.right):
                position = bisect_right(xs, e.display_point[0])
                refs = [
                    self.axis.anchors[i].word_index
                    for i in sorted({max(0, position - 1), min(len(xs) - 1, position)})
                ]
                if e.supporting_hour_word_indices != refs or not same_number(
                    e.mapping_residual_minutes, m.max_residual_minutes
                ):
                    raise ValueError("endpoint/axis provenance mismatch")
                if e.absolute_minutes is not None and not (
                    same_number(e.absolute_minutes, m.minutes_at(e.display_point[0]))
                    and same_number(
                        e.minutes_from_axis_origin, e.absolute_minutes - m.origin_absolute_minutes
                    )
                ):
                    raise ValueError("endpoint/axis time mismatch")
        return self


class TemporalPage(RawModel):
    format: Literal["railway-turno-decoder.temporal"] = "railway-turno-decoder.temporal"
    schema_version: Literal["0.1"] = "0.1"
    algorithm_version: str = ALGORITHM_VERSION
    raw_schema_version: Literal["1.0"] = "1.0"
    source_extraction_id: UUID
    source_sha256: str = Field(pattern=r"^[0-9a-f]{64}$")
    page_index: int = Field(ge=0)
    # Existing persisted 0.1 caches were exclusively DUTY_DIAGRAM pages.
    page_type: PageType = "DUTY_DIAGRAM"
    coordinate_system: Literal["M2 display coordinates; points"] = "M2 display coordinates; points"
    tolerances: TemporalTolerances
    rows: list[TemporalRow]
    warnings: list[str] = Field(default_factory=list)

    @model_validator(mode="after")
    def references(self):
        if self.page_type != "DUTY_DIAGRAM" and self.rows:
            raise ValueError("non-duty page cannot contain temporal rows")
        if [r.row_index for r in self.rows] != list(range(len(self.rows))):
            raise ValueError("row indices must match M2 order")
        if any(r.page_index != self.page_index for r in self.rows):
            raise ValueError("row page mismatch")
        for row in self.rows:
            if [s.segment_index for s in row.segments] != list(range(len(row.segments))):
                raise ValueError("segment indices must be sequential")
            if row.axis:
                if (
                    len(row.axis.anchors) < self.tolerances.min_hour_anchors
                    or row.axis.mapping.max_residual_minutes > self.tolerances.axis_residual_minutes
                    or any(
                        not 1
                        <= (b.absolute_minutes - a.absolute_minutes) / 60
                        <= self.tolerances.max_hour_gap
                        for a, b in zip(row.axis.anchors, row.axis.anchors[1:], strict=False)
                    )
                ):
                    raise ValueError("axis violates recorded tolerances")
                for s in row.segments:
                    for e in (s.left, s.right):
                        if any(
                            v.compatible
                            != (
                                v.residual_minutes + row.axis.mapping.max_residual_minutes
                                < self.tolerances.minute_label_residual_minutes
                            )
                            for v in e.nearby_labels
                        ):
                            raise ValueError("minute compatibility/tolerance mismatch")
        return self
