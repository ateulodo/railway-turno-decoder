"""Dimensionless layout tolerances, recorded in every artifact."""

from pydantic import Field, FiniteFloat

from railway_turno_decoder.raw_models import RawModel

ALGORITHM_VERSION = "0.1.1"


class LayoutTolerances(RawModel):
    text_baseline_height: FiniteFloat = Field(default=0.25, gt=0, le=0.5)
    text_gap_height: FiniteFloat = Field(default=0.65, gt=0, le=2)
    text_column_width: FiniteFloat = Field(default=0.25, gt=0, le=0.5)
    text_tall_aspect: FiniteFloat = Field(default=1.2, gt=1)
    minute_zone_row_height: FiniteFloat = Field(default=0.32, gt=0, le=0.5)
    band_y_height: FiniteFloat = Field(default=0.18, gt=0, le=0.3)
    below_min_height: FiniteFloat = Field(default=0.1, ge=0, le=0.5)
    below_max_height: FiniteFloat = Field(default=1.8, gt=0, le=3)
    pair_gap_width: FiniteFloat = Field(default=0.8, ge=0, le=1)
    pair_max_hours: FiniteFloat = Field(default=2, gt=0, le=4)
    hour_shift_spacing: FiniteFloat = Field(default=0.18, gt=0, lt=0.25)
    minute_width_spacing: FiniteFloat = Field(default=0.75, gt=0, le=0.75)
    relation_gap_spacing: FiniteFloat = Field(default=0.12, ge=0, le=0.5)
    relation_vertical_row: FiniteFloat = Field(default=0.7, gt=0, le=1)
    max_row_words: int = Field(default=1024, ge=1, le=4096)
    max_relations: int = Field(default=20000, ge=1, le=100000)
    numeric_digits: int = Field(default=9, ge=2, le=18)
