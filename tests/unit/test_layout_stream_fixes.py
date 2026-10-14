"""Synthetic regressions for anchor extents and unresolved structural gaps."""

from copy import deepcopy
from math import sqrt

import pytest
from layout_fixtures import make_layout_page

from railway_turno_decoder.layout.models import HourCandidate, LayoutReason, MinuteToken
from railway_turno_decoder.layout.stream import detect_gaps, hour_candidates, stream_blocks
from railway_turno_decoder.layout.tolerances import LayoutTolerances
from railway_turno_decoder.temporal.models import TemporalAxis


def residual_axis(offset, hours=(5, 6, 7, 8, 9)):
    _, temporal = make_layout_page(hours=hours, events=[], intervals=[])
    axis = temporal.rows[0].axis.model_copy(deep=True)
    axis.mapping.intercept_minutes += offset
    residuals = []
    for anchor in axis.anchors:
        anchor.residual_minutes = abs(
            axis.mapping.minutes_at(anchor.display_point[0]) - anchor.absolute_minutes
        )
        residuals.append(anchor.residual_minutes)
    axis.mapping.max_residual_minutes = max(residuals)
    axis.mapping.rms_residual_minutes = sqrt(sum(r * r for r in residuals) / len(residuals))
    return TemporalAxis.model_validate(axis.model_dump())


@pytest.mark.parametrize("offset", [0, 0.002, -0.002])
@pytest.mark.parametrize("boundary", [0, -1])
def test_boundary_hour_uses_observed_anchor_extent(offset, boundary):
    axis = residual_axis(offset)
    anchor = axis.anchors[boundary]
    x = anchor.display_point[0]
    result = hour_candidates(0, (x - 2, 230, x + 2, 238), axis, LayoutTolerances())
    assert [c.absolute_minutes for c in result] == [anchor.absolute_minutes]
    assert anchor.word_index in result[0].hour_word_indices
    assert axis.mapping.minutes_at(x) != anchor.absolute_minutes or offset == 0
    assert LayoutTolerances().hour_shift_spacing == 0.18


@pytest.mark.parametrize("boundary,value,delta", [(0, 55, -5), (-1, 5, 5)])
def test_nearby_hour_outside_anchor_time_extent_is_excluded(boundary, value, delta):
    axis = residual_axis(0.002)
    time = axis.anchors[boundary].absolute_minutes + delta
    x = axis.mapping.x_at(time)
    # Close enough to the physical boundary for the typographic margin, but
    # genuinely outside the observed temporal domain.
    assert abs(x - axis.anchors[boundary].display_point[0]) < (
        axis.mapping.hour_spacing_points * LayoutTolerances().hour_shift_spacing
    )
    assert hour_candidates(value, (x - 2, 230, x + 2, 238), axis, LayoutTolerances()) == []


@pytest.mark.parametrize("boundary", [0, -1])
def test_anchor_recovery_does_not_expand_typographic_margin(boundary):
    axis = residual_axis(0.002)
    x = axis.mapping.x_at(axis.anchors[boundary].absolute_minutes)
    margin = axis.mapping.hour_spacing_points * LayoutTolerances().hour_shift_spacing
    box = (x + margin + 1, 230, x + margin + 3, 238)
    assert hour_candidates(0, box, axis, LayoutTolerances()) == []


@pytest.mark.parametrize(
    "hours,boundary,offset,day", [((0, 1, 2), 0, 0.002, 0), ((22, 23, 24), -1, -0.002, 1)]
)
def test_midnight_anchor_boundary_retains_day_offset(hours, boundary, offset, day):
    axis = residual_axis(offset, hours)
    x = axis.anchors[boundary].display_point[0]
    result = hour_candidates(0, (x - 2, 230, x + 2, 238), axis, LayoutTolerances())
    assert len(result) == 1
    assert result[0].clock_label == "00:00" and result[0].day_offset == day


def test_boundary_candidates_are_deterministic_and_do_not_mutate_m3a():
    axis = residual_axis(0.002)
    before = axis.model_dump_json()
    box = (98, 230, 102, 238)
    first = hour_candidates(0, box, axis, LayoutTolerances())
    assert axis.model_dump_json() == before
    axis.support_refs.reverse()
    assert hour_candidates(0, box, axis, LayoutTolerances()) == first


def token(index, role, minutes, x, bands=None):
    value = minutes % 60 if minutes is not None else 7
    return MinuteToken(
        token_index=index,
        word_index=100 + index,
        raw_text=f"{value:02d}",
        value=value,
        display_bbox=(x, 10, x + 4, 18),
        center=(x + 2, 14),
        band_candidates=bands or ([1] if role == "START_SLOT" else [2]),
        band_index=None if bands else (1 if role == "START_SLOT" else 2),
        role=role,
        band_residual_height=None,
        hour_candidates=[]
        if minutes is None
        else [
            HourCandidate(
                absolute_minutes=minutes,
                clock_label=f"{minutes // 60 % 24:02d}:{value:02d}",
                day_offset=minutes // 1440,
                hour_word_indices=[10, 11],
                x_gap_points=0,
                normalized_tick_distance=0,
            )
        ],
        endpoint_segment_indices=[],
        status="UNASSOCIATED" if minutes is None else "AMBIGUOUS" if role is None else "UNIQUE",
        reasons=[LayoutReason(code="synthetic_word", word_indices=[100 + index])],
    )


def gaps_for(tokens):
    before = [t.model_dump_json() for t in tokens]
    result = detect_gaps(tokens, stream_blocks(tokens))
    assert [t.model_dump_json() for t in tokens] == before
    return result


def test_unresolved_start_before_observed_end_is_an_explicit_alternative():
    tokens = [token(0, "START_SLOT", None, 10), token(1, "END_SLOT", 337, 100)]
    gap = gaps_for(tokens)[0]
    assert gap.before_token_index is None and gap.after_token_index == 1
    assert gap.status == "AMBIGUOUS" and gap.uncertain_token_indices == [0]
    assert gap.reasons[-1].word_indices == [100]
    assert not tokens[0].hour_candidates and not hasattr(gap, "absolute_minutes")


def test_unresolved_end_after_observed_start_is_an_explicit_alternative():
    gap = gaps_for([token(0, "START_SLOT", 307, 10), token(1, "END_SLOT", None, 100)])[0]
    assert gap.after_token_index is None and gap.before_token_index == 0
    assert gap.status == "AMBIGUOUS" and gap.uncertain_token_indices == [1]


def test_unresolved_internal_event_only_affects_its_related_gap():
    gaps = gaps_for(
        [
            token(0, "END_SLOT", 307, 10),
            token(1, "START_SLOT", None, 40),
            token(2, "END_SLOT", 367, 100),
        ]
    )
    assert gaps[0].before_token_index is None and gaps[0].status == "UNIQUE"
    assert gaps[1].status == "AMBIGUOUS" and gaps[1].uncertain_token_indices == [1]


@pytest.mark.parametrize("role,x", [("START_SLOT", 200), ("END_SLOT", 40), ("START_SLOT", -30)])
def test_unrelated_position_or_role_does_not_contaminate_internal_gap(role, x):
    gaps = gaps_for(
        [token(0, "END_SLOT", 307, 10), token(1, role, None, x), token(2, "END_SLOT", 367, 100)]
    )
    internal = next(g for g in gaps if g.before_token_index == 0 and g.after_token_index == 2)
    assert internal.status == "UNIQUE" and internal.uncertain_token_indices == []


def test_multiple_unresolved_alternatives_preserve_all_word_references():
    tokens = [
        token(0, "START_SLOT", None, 10),
        token(1, "START_SLOT", None, 40),
        token(2, "END_SLOT", 337, 100),
    ]
    gap = gaps_for(tokens)[0]
    assert gap.status == "AMBIGUOUS" and gap.uncertain_token_indices == [0, 1]
    assert gap.reasons[-1].word_indices == [100, 101]


@pytest.mark.parametrize("role", ["START_SLOT", "END_SLOT"])
def test_open_sequence_without_compatible_alternative_remains_unique(role):
    gap = gaps_for([token(0, role, 307, 10)])[0]
    assert gap.status == "UNIQUE" and not gap.uncertain_token_indices


@pytest.mark.parametrize(
    "role,other,x",
    [("END_SLOT", "START_SLOT", 200), ("START_SLOT", "END_SLOT", 10)],
)
def test_unresolved_event_on_wrong_side_does_not_contaminate_open_gap(role, other, x):
    gaps = gaps_for([token(0, role, 337, 100), token(1, other, None, x)])
    assert len(gaps) == 1 and gaps[0].status == "UNIQUE"
    assert gaps[0].uncertain_token_indices == []


def test_ambiguous_band_phase_keeps_compatible_role_and_provenance():
    tokens = [
        token(0, "END_SLOT", 307, 10),
        token(1, None, None, 40, [1, 2]),
        token(2, "END_SLOT", 367, 100),
    ]
    first = gaps_for(tokens)
    assert first[1].status == "AMBIGUOUS" and first[1].uncertain_token_indices == [1]
    reordered = deepcopy(tokens)
    reordered[1].band_candidates.reverse()
    for t in reordered:
        for c in t.hour_candidates:
            c.hour_word_indices.reverse()
    assert [g.model_dump_json() for g in gaps_for(reordered)] == [
        g.model_dump_json() for g in first
    ]


def test_known_hour_of_ambiguous_phase_must_overlap_gap_time_window():
    tokens = [
        token(0, "END_SLOT", 307, 10),
        token(1, None, 397, 40, [1, 2]),
        token(2, "END_SLOT", 367, 100),
    ]
    internal = next(g for g in gaps_for(tokens) if g.before_token_index == 0)
    assert internal.status == "UNIQUE" and internal.uncertain_token_indices == []
