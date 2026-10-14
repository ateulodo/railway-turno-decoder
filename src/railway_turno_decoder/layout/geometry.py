"""Display-only geometry and local interval searches."""

from bisect import bisect_left, bisect_right
from math import hypot


def center(box):
    return ((box[0] + box[2]) / 2, (box[1] + box[3]) / 2)


def bounds(boxes):
    return (
        min(b[0] for b in boxes),
        min(b[1] for b in boxes),
        max(b[2] for b in boxes),
        max(b[3] for b in boxes),
    )


def valid_box(box, page):
    return 0 <= box[0] < box[2] <= page.width and 0 <= box[1] < box[3] <= page.height


def gaps(a, b):
    return (max(a[0] - b[2], b[0] - a[2], 0), max(a[1] - b[3], b[1] - a[3], 0))


def measures(a, b, spacing, segment=None):
    xgap, ygap = gaps(a, b)
    ac, bc = center(a), center(b)
    distance = hypot(ac[0] - bc[0], ac[1] - bc[1])
    positions = []
    if a[2] < b[0]:
        positions.append("BEFORE")
    elif b[2] < a[0]:
        positions.append("AFTER")
    if a[3] < b[1]:
        positions.append("ABOVE")
    elif b[3] < a[1]:
        positions.append("BELOW")
    if not positions:
        positions.append("OVERLAPPING")
    result = dict(
        source_center=ac,
        target_center=bc,
        horizontal_overlap=max(0, min(a[2], b[2]) - max(a[0], b[0])),
        horizontal_gap=xgap,
        vertical_gap=ygap,
        center_distance=distance,
        normalized_distance=distance / spacing,
        relative_positions=positions,
    )
    if segment is not None:
        result.update(
            distance_to_start=hypot(
                ac[0] - segment.left.display_point[0], ac[1] - segment.left.display_point[1]
            ),
            distance_to_end=hypot(
                ac[0] - segment.right.display_point[0], ac[1] - segment.right.display_point[1]
            ),
            distance_to_center=distance,
        )
    return result


def segment_box(segment):
    a, b = segment.left.display_point, segment.right.display_point
    return min(a[0], b[0]), min(a[1], b[1]), max(a[0], b[0]), max(a[1], b[1])


class XIndex:
    def __init__(self, entries):
        self.entries = sorted(entries, key=lambda e: (center(e[1])[0], e[0]))
        self.xs = [center(b)[0] for _, b in self.entries]
        self.half_width = max(((b[2] - b[0]) / 2 for _, b in self.entries), default=0)

    def near(self, box, radius):
        lo = bisect_left(self.xs, box[0] - radius - self.half_width)
        hi = bisect_right(self.xs, box[2] + radius + self.half_width)
        return self.entries[lo:hi]
