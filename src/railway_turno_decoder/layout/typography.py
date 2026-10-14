"""Geometric text components, numeric regions and shared page band evidence."""

from bisect import bisect_right
from itertools import combinations
from statistics import median

from railway_turno_decoder.layout.geometry import XIndex, bounds, center, gaps, valid_box
from railway_turno_decoder.layout.models import LayoutReason, TextCluster


def numeric(text, digits, maximum=None):
    return (
        text.isascii()
        and text.isdigit()
        and len(text) <= digits
        and (maximum is None or int(text) <= maximum)
    )


def row_scale(page, temporal_row):
    return median(
        page.words[a.word_index].display_bbox[3] - page.words[a.word_index].display_bbox[1]
        for a in temporal_row.axis.anchors
    )


def usable_row(page, row, tolerance):
    refs = row.provenance
    return (
        valid_box(row.display_bbox, page)
        and len(refs.word_indices) <= tolerance.max_row_words
        and len(refs.word_indices) == len(set(refs.word_indices))
        and len(refs.anchor_word_indices) == len(set(refs.anchor_word_indices))
        and len(refs.drawing_item_refs)
        == len({(r.drawing_index, r.item_index) for r in refs.drawing_item_refs})
    )


def below_owners(page, temporal, tolerance):
    axes = sorted(
        (r.axis.display_y, r.row_index)
        for r in temporal.rows
        if r.axis and usable_row(page, page.rows[r.row_index], tolerance)
    )
    ys = [y for y, _ in axes]
    result = {r.row_index: [] for r in temporal.rows}
    anchor_words = {a.word_index for r in temporal.rows if r.axis for a in r.axis.anchors}
    heights = {r.row_index: row_scale(page, r) for r in temporal.rows if r.axis}
    for word in page.words:
        if (
            word.word_index in anchor_words
            or not valid_box(word.display_bbox, page)
            or not numeric(word.text, tolerance.numeric_digits)
        ):
            continue
        x, y = center(word.display_bbox)
        position = bisect_right(ys, y) - 1
        if position < 0:
            continue
        axis_y, index = axes[position]
        row = temporal.rows[index]
        axis = row.axis
        height = heights[index]
        if (
            axis.mapping.min_x <= x <= axis.mapping.max_x
            and height * tolerance.below_min_height
            <= y - axis_y
            <= height * tolerance.below_max_height
        ):
            result[index].append(word.word_index)
    return {
        i: sorted(refs, key=lambda j: (*page.words[j].display_bbox, j))
        for i, refs in result.items()
    }


def minute_zone(page, row, temporal_row, tolerance):
    axis = temporal_row.axis
    height = row_scale(page, temporal_row)
    return (
        axis.mapping.min_x,
        max(
            row.display_bbox[1],
            axis.display_y
            - (row.display_bbox[3] - row.display_bbox[1]) * tolerance.minute_zone_row_height,
        ),
        axis.mapping.max_x,
        axis.display_y - height * tolerance.below_min_height,
    )


def minute_words(page, row, temporal_row, tolerance, below_refs):
    if temporal_row.axis is None or not usable_row(page, row, tolerance):
        return []
    zone = minute_zone(page, row, temporal_row, tolerance)
    excluded = set(row.provenance.anchor_word_indices) | below_refs
    excluded |= {a.word_index for a in temporal_row.axis.anchors}
    return sorted(
        (
            i
            for i in row.provenance.word_indices
            if i not in excluded
            and valid_box(page.words[i].display_bbox, page)
            and numeric(page.words[i].text, 2, 59)
            and page.words[i].display_bbox[2] - page.words[i].display_bbox[0]
            <= temporal_row.axis.mapping.hour_spacing_points * tolerance.minute_width_spacing
            and zone[0] <= center(page.words[i].display_bbox)[0] <= zone[2]
            and zone[1] <= center(page.words[i].display_bbox)[1] <= zone[3]
        ),
        key=lambda i: (*page.words[i].display_bbox, i),
    )


def band_template(page, temporal, candidates, tolerance):
    samples = []
    for row in temporal.rows:
        if not row.axis:
            continue
        height = row_scale(page, row)
        samples.extend(
            (row.axis.display_y - center(page.words[i].display_bbox)[1]) / height
            for i in candidates[row.row_index]
        )
    groups = []
    for value in sorted(samples, reverse=True):
        if groups and groups[-1][0] - value <= tolerance.band_y_height:
            groups[-1].append(value)
        else:
            groups.append([value])
    return [median(g) for g in groups]


def band_memberships(offset, template, tolerance):
    matched = [
        i for i, value in enumerate(template) if abs(value - offset) <= tolerance.band_y_height
    ]
    if len(template) == 4:
        return [i + 1 for i in matched] or [1, 2, 3, 4], min(
            (abs(v - offset) for v in template), default=None
        )
    if 0 < len(template) < 4 and matched:
        choices = combinations(range(1, 5), len(template))
        return sorted({slots[i] for slots in choices for i in matched}), None
    return [1, 2, 3, 4], None


def text_clusters(page, refs, tolerance, check_budget):
    refs = sorted(refs, key=lambda i: (*page.words[i].display_bbox, i))
    parent = {i: i for i in refs}
    edge_types = []
    index = XIndex([(i, page.words[i].display_bbox) for i in refs])

    def root(i):
        while parent[i] != i:
            parent[i] = parent[parent[i]]
            i = parent[i]
        return i

    max_height = max(
        (page.words[i].display_bbox[3] - page.words[i].display_bbox[1] for i in refs), default=0
    )
    for i in refs:
        check_budget()
        a = page.words[i].display_bbox
        for j, b in index.near(a, max_height * tolerance.text_gap_height):
            if j <= i:
                continue
            ah, bh = a[3] - a[1], b[3] - b[1]
            aw, bw = a[2] - a[0], b[2] - b[0]
            xgap, ygap = gaps(a, b)
            tall = ah > aw * tolerance.text_tall_aspect and bh > bw * tolerance.text_tall_aspect
            horizontal = (
                not tall
                and (a[2] <= b[0] or b[2] <= a[0])
                and abs(a[3] - b[3]) <= min(ah, bh) * tolerance.text_baseline_height
                and xgap <= min(ah, bh) * tolerance.text_gap_height
            )
            vertical = (
                abs(center(a)[0] - center(b)[0]) <= min(aw, bw) * tolerance.text_column_width
                and (a[3] <= b[1] or b[3] <= a[1])
                and ygap <= min(ah, bh) * tolerance.text_gap_height
            )
            if horizontal or vertical:
                parent[root(j)] = root(i)
                edge_types.append((i, j, "HORIZONTAL" if horizontal else "VERTICAL"))
    groups = {}
    for i in refs:
        groups.setdefault(root(i), []).append(i)
    orientations_by_root = {}
    for a, _, kind in edge_types:
        orientations_by_root.setdefault(root(a), set()).add(kind)
    components = []
    for key, members in groups.items():
        orientations = orientations_by_root.get(key, set())
        orientation = (
            next(iter(orientations))
            if len(orientations) == 1
            else ("MIXED" if orientations else "SINGLE")
        )
        members.sort(
            key=lambda i: (
                (page.words[i].display_bbox[1], page.words[i].display_bbox[0], i)
                if orientation == "VERTICAL"
                else (*page.words[i].display_bbox, i)
            )
        )
        components.append(
            (bounds([page.words[i].display_bbox for i in members]), members, orientation)
        )
    components.sort(key=lambda item: (item[0], tuple(item[1])))
    return [
        TextCluster(
            cluster_index=i,
            word_indices=members,
            fragments=[page.words[j].text for j in members],
            display_bbox=box,
            orientation=orientation,
            status="AMBIGUOUS" if orientation == "MIXED" else "UNIQUE",
            reasons=[LayoutReason(code="bbox_connected_component", word_indices=sorted(members))],
        )
        for i, (box, members, orientation) in enumerate(components)
    ]
