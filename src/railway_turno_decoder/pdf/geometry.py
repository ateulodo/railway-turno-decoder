"""Convert official MuPDF geometry to our JSON-native representation."""

import pymupdf

from railway_turno_decoder.raw_models import RawDrawing, RawPrimitive


def pdf_to_original_matrix(page: pymupdf.Page) -> pymupdf.Matrix:
    """Map PDF user space to crop-relative, unrotated PyMuPDF coordinates.

    PyMuPDF 1.28.2 replaces the translated native CTM on rotated pages.
    Read its official CTM with rotation disabled, then restore the page even
    on error. Called only within the native PDF lock; no document is saved.
    Rotation is applied separately through page.rotation_matrix.
    """
    rotation = page.rotation
    if rotation == 0:
        return pymupdf.Matrix(page.transformation_matrix)
    try:
        page.set_rotation(0)
        return pymupdf.Matrix(page.transformation_matrix)
    finally:
        page.set_rotation(rotation)


def transform_bbox(box, matrix):
    return tuple(pymupdf.Rect(box) * matrix)


def native_json(value, matrix=None):
    if isinstance(value, pymupdf.Point):
        point = value * matrix if matrix is not None else value
        return {"kind": "point", "coordinates": list(point)}
    if isinstance(value, pymupdf.Rect):
        rect = value * matrix if matrix is not None else value
        return {"kind": "rect", "coordinates": list(rect)}
    if isinstance(value, pymupdf.Quad):
        quad = value * matrix if matrix is not None else value
        return {"kind": "quad", "vertices": [list(p) for p in quad]}
    if isinstance(value, (tuple, list)):
        return [native_json(item, matrix) for item in value]
    if isinstance(value, dict):
        return {key: native_json(item, matrix) for key, item in value.items()}
    if value is None or isinstance(value, (str, bool, int, float)):
        return value
    raise TypeError(f"Unsupported geometry value: {type(value).__name__}")


def primitive(item, index, matrix):
    points = []
    for argument in item[1:]:
        if isinstance(argument, pymupdf.Point):
            points.append(argument)
        elif isinstance(argument, pymupdf.Rect):
            points.extend((argument.tl, argument.tr, argument.br, argument.bl))
        elif isinstance(argument, pymupdf.Quad):
            points.extend((argument.ul, argument.ur, argument.lr, argument.ll))
    display = [point * matrix for point in points]

    def bounds(vertices):
        if not vertices:
            return None
        xs, ys = zip(*vertices, strict=True)
        return min(xs), min(ys), max(xs), max(ys)

    return RawPrimitive(
        item_index=index,
        kind=item[0],
        original_bbox=bounds(points),
        display_bbox=bounds(display),
        original_points=[tuple(p) for p in points],
        display_points=[tuple(p) for p in display],
        original_arguments=native_json(item[1:]),
        display_arguments=native_json(item[1:], matrix),
    )


def drawing(value, index, matrix):
    mapping = {
        "width": "width",
        "dashes": "dashes",
        "color": "stroke_color",
        "fill": "fill_color",
        "stroke_opacity": "stroke_opacity",
        "fill_opacity": "fill_opacity",
        "lineCap": "line_cap",
        "lineJoin": "line_join",
        "closePath": "close_path",
        "even_odd": "even_odd",
        "seqno": "seqno",
        "layer": "layer",
    }
    known = set(mapping) | {"items", "rect", "type"}
    return RawDrawing(
        drawing_index=index,
        type=value["type"],
        original_bbox=tuple(value["rect"]),
        display_bbox=transform_bbox(value["rect"], matrix),
        items=[primitive(item, i, matrix) for i, item in enumerate(value["items"])],
        extra={key: native_json(item) for key, item in value.items() if key not in known},
        **{destination: native_json(value.get(source)) for source, destination in mapping.items()},
    )
