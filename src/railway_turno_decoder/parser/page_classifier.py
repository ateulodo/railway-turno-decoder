"""Conservative content/layout classification; never page-number based."""

import re
import unicodedata

from railway_turno_decoder.raw_models import RawPage, RawWord, Reason

MARKER = re.compile(r"^[LMXGVSD]{1,7}$")


def normalized(text: str) -> str:
    text = "".join(
        c for c in unicodedata.normalize("NFKD", text.lower()) if not unicodedata.combining(c)
    )
    return re.sub(r"[^a-z0-9]+", " ", text).strip()


def gutter_markers(page: RawPage):
    return [
        word
        for word in page.words
        if MARKER.fullmatch(word.text)
        and word.display_bbox[0] <= page.width * 0.12
        and page.height * 0.04 <= word.display_bbox[1] <= page.height * 0.95
    ]


def horizontal_items(page: RawPage):
    for drawing in page.drawings:
        for item in drawing.items:
            if item.kind != "l" or len(item.display_points) != 2:
                continue
            a, b = item.display_points
            if (
                abs(a[1] - b[1]) <= max(0.5, page.height * 0.002)
                and abs(a[0] - b[0]) >= page.width * 0.015
                and max(a[0], b[0]) > page.width * 0.15
                and page.height * 0.04 <= (a[1] + b[1]) / 2 <= page.height * 0.95
            ):
                yield drawing.drawing_index, item.item_index, (a[1] + b[1]) / 2


def _header_runs(page: RawPage) -> list[list[RawWord]]:
    """Derived visual lines/runs only. Never mutate the raw word array.

    A line uses a fixed geometric anchor to avoid cumulative vertical drift.
    Large horizontal gaps split runs, so scattered heading words do not match.
    """
    header = [
        word
        for word in page.words
        if 0 <= word.display_bbox[1] < word.display_bbox[3] <= page.height * 0.18
    ]
    ordered = sorted(
        header,
        key=lambda w: (
            (w.display_bbox[1] + w.display_bbox[3]) / 2,
            w.display_bbox[0],
            w.display_bbox[1],
            w.display_bbox[2],
            w.display_bbox[3],
            w.text,
            w.word_index,
        ),
    )
    lines: list[list[RawWord]] = []
    for word in ordered:
        box = word.display_bbox
        center = (box[1] + box[3]) / 2
        if lines:
            anchor = lines[-1][0].display_bbox
            tolerance = max(0.5, min(box[3] - box[1], anchor[3] - anchor[1]) * 0.3)
            if center - (anchor[1] + anchor[3]) / 2 <= tolerance:
                lines[-1].append(word)
                continue
        lines.append([word])
    runs: list[list[RawWord]] = []
    for line in lines:
        visual = sorted(line, key=lambda w: (*w.display_bbox, w.text, w.word_index))
        run: list[RawWord] = []
        for word in visual:
            if run:
                previous, current = run[-1].display_bbox, word.display_bbox
                height = min(previous[3] - previous[1], current[3] - current[1])
                gap = current[0] - previous[2]
                if not -0.25 * height <= gap <= 1.5 * height:
                    runs.append(run)
                    run = []
            run.append(word)
        runs.append(run)
    return runs


def _notes_heading(page: RawPage) -> list[int] | None:
    phrase = ["note", "sulla", "periodicita", "dei", "treni", "del", "turno"]
    for run in _header_runs(page):
        parts = [(part, word.word_index) for word in run for part in normalized(word.text).split()]
        for start in range(len(parts) - len(phrase) + 1):
            window = parts[start : start + len(phrase)]
            if [part for part, _ in window] == phrase:
                return list(dict.fromkeys(index for _, index in window))
    return None


def classify_page(page: RawPage):
    tokens = [normalized(word.text) for word in page.words]
    refs = _notes_heading(page)
    if refs is not None:
        return "TRAIN_PERIODICITY_NOTES", [Reason(code="notes_heading", word_indices=refs)]
    header = {
        token
        for word, token in zip(page.words, tokens, strict=True)
        if word.display_bbox[1] < page.height * 0.18
    }
    if {"planning", "profilo", "turno", "data", "pubblicazione"} <= header:
        return "INDEX", [
            Reason(
                code="index_header_fields",
                word_indices=[
                    i
                    for i, t in enumerate(tokens)
                    if t in header
                    and t in {"planning", "profilo", "turno", "data", "pubblicazione"}
                    and page.words[i].display_bbox[1] < page.height * 0.18
                ],
            )
        ]
    markers = gutter_markers(page)
    lines = list(horizontal_items(page))
    columns = {"lav", "cct", "km", "not", "rip"}
    if (
        {"turno", "profilo"} <= header
        and len(set(tokens) & columns) >= 3
        and markers
        and len(lines) >= 2
    ):
        return "DUTY_DIAGRAM", [
            Reason(
                code="duty_header_and_columns",
                word_indices=[
                    i for i, t in enumerate(tokens) if t in columns | {"turno", "profilo"}
                ],
            ),
            Reason(
                code="left_markers_and_horizontal_paths",
                word_indices=[w.word_index for w in markers],
                drawing_indices=sorted({d for d, _, _ in lines}),
            ),
        ]
    return "UNKNOWN", [Reason(code="insufficient_structural_signals")]
