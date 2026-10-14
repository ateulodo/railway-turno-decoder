"""Structural statistics only; outputs are local QA data, not railway duties."""

from collections import Counter
from math import ceil
from statistics import median


def summarize_layout(pages):
    counts = Counter()
    bands = Counter()
    sizes = Counter()
    cluster_status = Counter()
    segment_status = Counter()
    relation_status = Counter()
    distributions = {
        k: []
        for k in [
            "token_to_tick_normalized",
            "token_to_cluster_points",
            "cluster_to_segment_normalized",
            "band_residual_height",
        ]
    }
    outliers = []
    for page in pages:
        if page.page_type != "DUTY_DIAGRAM":
            continue
        counts["duty_pages"] += 1
        counts["pages_without_rows"] += int(not page.rows)
        for row in page.rows:
            counts["rows"] += 1
            counts["rows_with_four_band_positions"] += int(
                len(row.bands) == 4 and all(b.display_y is not None for b in row.bands)
            )
            occupied = {t.band_index for t in row.minute_tokens if t.band_index is not None}
            counts["rows_with_four_occupied_bands"] += int(len(occupied) == 4)
            counts["rows_with_incomplete_occupied_bands"] += int(len(occupied) < 4)
            counts["rows_with_ambiguous_band_phase"] += int(
                any(t.band_index is None for t in row.minute_tokens)
            )
            counts["minute_tokens"] += len(row.minute_tokens)
            counts["explicit_pairs"] += len(row.explicit_pairs)
            counts["ambiguous_pairs"] += sum(p.status == "AMBIGUOUS" for p in row.explicit_pairs)
            counts["missing_start_slot_gaps"] += sum(
                g.expected_role == "START_SLOT" for g in row.gaps
            )
            counts["missing_end_slot_gaps"] += sum(g.expected_role == "END_SLOT" for g in row.gaps)
            counts["ambiguous_gaps"] += sum(g.status == "AMBIGUOUS" for g in row.gaps)
            counts["below_axis_numeric_evidence"] += len(row.below_axis)
            counts["text_clusters"] += len(row.clusters)
            counts["invalid_rows"] += int(row.status == "INVALID")
            counts["ambiguous_rows"] += int(row.status == "AMBIGUOUS")
            for t in row.minute_tokens:
                bands[str(t.band_index) if t.band_index is not None else "AMBIGUOUS"] += 1
                counts["hour_neighborhood_ambiguities"] += int(len(t.hour_candidates) > 1)
                counts["hour_neighborhood_unassociated"] += int(not t.hour_candidates)
                counts["tokens_with_next_day_candidate"] += int(
                    any(c.day_offset > 0 for c in t.hour_candidates)
                )
                if t.band_residual_height is not None:
                    distributions["band_residual_height"].append(t.band_residual_height)
                distributions["token_to_tick_normalized"].extend(
                    c.normalized_tick_distance for c in t.hour_candidates
                )
            for a, b in zip(row.stream_blocks, row.stream_blocks[1:], strict=False):
                counts["stream_midnight_transitions"] += int(
                    a.status == b.status == "UNIQUE"
                    and a.max_minutes // 1440 < b.min_minutes // 1440
                )
            sizes.update(len(c.word_indices) for c in row.clusters)
            cluster_status.update(c.segment_status for c in row.clusters)
            segment_status.update(s.status for s in row.segment_associations)
            for r in row.relations:
                if r.target.kind == "MINUTE":
                    distributions["token_to_cluster_points"].append(r.center_distance)
                if r.target.kind == "SEGMENT" and r.source.kind == "CLUSTER":
                    relation_status[r.status] += 1
                    distributions["cluster_to_segment_normalized"].append(r.normalized_distance)
            if row.status != "UNIQUE":
                outliers.append(
                    dict(
                        page_index=page.page_index,
                        row_index=row.row_index,
                        status=row.status,
                        ambiguous_token_word_indices=[
                            t.word_index for t in row.minute_tokens if t.status != "UNIQUE"
                        ],
                        ambiguous_cluster_indices=[
                            c.cluster_index for c in row.clusters if c.segment_status == "AMBIGUOUS"
                        ],
                        reason_codes=[r.code for r in row.reasons],
                    )
                )

    def distribution(values):
        v = sorted(values)
        return dict(
            count=len(v),
            median=median(v) if v else None,
            p95=v[ceil(len(v) * 0.95) - 1] if v else None,
            max=max(v) if v else None,
        )

    def statuses(c):
        return {s: c[s] for s in ["UNIQUE", "AMBIGUOUS", "UNASSOCIATED", "INVALID"]}

    return {
        **{
            k: counts[k]
            for k in [
                "duty_pages",
                "pages_without_rows",
                "rows",
                "rows_with_four_band_positions",
                "rows_with_four_occupied_bands",
                "rows_with_incomplete_occupied_bands",
                "rows_with_ambiguous_band_phase",
                "minute_tokens",
                "explicit_pairs",
                "ambiguous_pairs",
                "missing_start_slot_gaps",
                "missing_end_slot_gaps",
                "ambiguous_gaps",
                "below_axis_numeric_evidence",
                "text_clusters",
                "invalid_rows",
                "ambiguous_rows",
                "hour_neighborhood_ambiguities",
                "hour_neighborhood_unassociated",
                "tokens_with_next_day_candidate",
                "stream_midnight_transitions",
            ]
        },
        "minute_tokens_by_band": dict(sorted(bands.items())),
        "cluster_size_distribution": dict(sorted(sizes.items())),
        "cluster_segment_status": statuses(cluster_status),
        "segment_cluster_status": statuses(segment_status),
        "cluster_segment_relation_status": statuses(relation_status),
        "distances": {k: distribution(v) for k, v in distributions.items()},
        "geometric_outliers": outliers,
    }
