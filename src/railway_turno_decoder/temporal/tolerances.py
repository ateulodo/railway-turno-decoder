"""Central geometric/time tolerances, recorded with each analysis."""

from pydantic import Field, FiniteFloat

from railway_turno_decoder.raw_models import RawModel

ALGORITHM_VERSION = "0.1.0"


class TemporalTolerances(RawModel):
    min_hour_anchors: int = Field(default=3, ge=3)
    max_hour_gap: int = Field(default=4, ge=1, le=12)
    # Bound local hypothesis generation even for dense or malicious raw input.
    max_axis_words: int = Field(default=128, ge=3, le=512)
    pair_neighbors: int = Field(default=4, ge=1, le=8)
    line_tolerance_points: FiniteFloat = Field(default=0.6, gt=0)
    line_height_fraction: FiniteFloat = Field(default=0.35, gt=0, le=0.5)
    min_axis_span_label_widths: FiniteFloat = Field(default=3.0, gt=0)
    min_axis_inlier_fraction: FiniteFloat = Field(default=0.65, gt=0.5, le=1)
    axis_residual_minutes: FiniteFloat = Field(default=0.35, gt=0, lt=1)
    support_height_fraction: FiniteFloat = Field(default=0.8, gt=0)
    support_coverage_fraction: FiniteFloat = Field(default=0.4, gt=0, le=1)
    geometry_points: FiniteFloat = Field(default=0.5, gt=0)
    segment_max_slope: FiniteFloat = Field(default=0.03, ge=0)
    min_segment_minutes: FiniteFloat = Field(default=1.0, gt=0)
    endpoint_outside_minutes: FiniteFloat = Field(default=1.0, ge=0)
    nearby_x_hour_fraction: FiniteFloat = Field(default=0.25, gt=0, le=0.5)
    nearby_y_row_fraction: FiniteFloat = Field(default=0.3, gt=0, le=0.5)
    minute_label_residual_minutes: FiniteFloat = Field(default=0.5, gt=0, lt=2)
