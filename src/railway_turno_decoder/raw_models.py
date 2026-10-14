"""Versioned JSON-native PDF geometry, independent of job and railway semantics."""

from typing import Literal
from uuid import UUID, uuid4

from pydantic import BaseModel, ConfigDict, Field, FiniteFloat, JsonValue, model_validator

Point = tuple[FiniteFloat, FiniteFloat]
BBox = tuple[FiniteFloat, FiniteFloat, FiniteFloat, FiniteFloat]
Matrix = tuple[FiniteFloat, FiniteFloat, FiniteFloat, FiniteFloat, FiniteFloat, FiniteFloat]
PageType = Literal["INDEX", "DUTY_DIAGRAM", "TRAIN_PERIODICITY_NOTES", "UNKNOWN"]
PAGE_TYPES = ("INDEX", "DUTY_DIAGRAM", "TRAIN_PERIODICITY_NOTES", "UNKNOWN")


class RawModel(BaseModel):
    model_config = ConfigDict(extra="forbid")


class Reason(RawModel):
    code: str
    word_indices: list[int] = Field(default_factory=list)
    drawing_indices: list[int] = Field(default_factory=list)


class RawWord(RawModel):
    word_index: int = Field(ge=0)
    text: str
    original_bbox: BBox
    display_bbox: BBox
    block_number: int
    line_number: int
    word_number: int


class RawPrimitive(RawModel):
    item_index: int = Field(ge=0)
    kind: str
    original_bbox: BBox | None = None
    display_bbox: BBox | None = None
    original_points: list[Point] = Field(default_factory=list)
    display_points: list[Point] = Field(default_factory=list)
    original_arguments: list[JsonValue]
    display_arguments: list[JsonValue]


class RawDrawing(RawModel):
    drawing_index: int = Field(ge=0)
    type: str
    original_bbox: BBox
    display_bbox: BBox
    width: FiniteFloat | None = None
    dashes: str | None = None
    stroke_color: list[FiniteFloat] | None = None
    fill_color: list[FiniteFloat] | None = None
    stroke_opacity: FiniteFloat | None = None
    fill_opacity: FiniteFloat | None = None
    line_cap: list[int] | None = None
    line_join: FiniteFloat | None = None
    close_path: bool | None = None
    even_odd: bool | None = None
    seqno: int | None = None
    layer: str | None = None
    items: list[RawPrimitive]
    extra: dict[str, JsonValue] = Field(default_factory=dict)


class DrawingItemRef(RawModel):
    drawing_index: int
    item_index: int


class RowProvenance(RawModel):
    page_index: int
    anchor_word_indices: list[int]
    word_indices: list[int]
    drawing_indices: list[int]
    drawing_item_refs: list[DrawingItemRef]


class RawRow(RawModel):
    row_index: int = Field(ge=0)
    original_bbox: BBox
    display_bbox: BBox
    periodicity_marker: str
    service_number: str | None = None
    provenance: RowProvenance
    detection_reasons: list[Reason]


class RawPage(RawModel):
    page_number: int = Field(ge=1)
    page_index: int = Field(ge=0)
    width: FiniteFloat = Field(gt=0)
    height: FiniteFloat = Field(gt=0)
    rotation: Literal[0, 90, 180, 270]
    mediabox: BBox
    cropbox: BBox
    original_bounds: BBox
    display_bounds: BBox
    rotation_matrix: Matrix
    derotation_matrix: Matrix
    pdf_to_original_matrix: Matrix
    original_coordinate_system: str = "PyMuPDF unrotated, crop-relative, top-left; points"
    display_coordinate_system: str = "Rotated visible page, top-left, x right, y down; points"
    page_type: PageType = "UNKNOWN"
    classification_reasons: list[Reason] = Field(default_factory=list)
    words: list[RawWord]
    drawings: list[RawDrawing]
    rows: list[RawRow] = Field(default_factory=list)
    warnings: list[str] = Field(default_factory=list)

    @model_validator(mode="after")
    def check_references(self) -> "RawPage":
        if self.page_number != self.page_index + 1:
            raise ValueError("page numbering mismatch")
        for objects, attribute in (
            (self.words, "word_index"),
            (self.drawings, "drawing_index"),
            (self.rows, "row_index"),
        ):
            if [getattr(obj, attribute) for obj in objects] != list(range(len(objects))):
                raise ValueError("indices must preserve raw order")
        for drawing in self.drawings:
            if [item.item_index for item in drawing.items] != list(range(len(drawing.items))):
                raise ValueError("primitive indices mismatch")
        for row in self.rows:
            source = row.provenance
            if source.page_index != self.page_index:
                raise ValueError("row source page mismatch")
            if any(
                i < 0 or i >= len(self.words)
                for i in source.word_indices + source.anchor_word_indices
            ):
                raise ValueError("word reference out of bounds")
            if any(i < 0 or i >= len(self.drawings) for i in source.drawing_indices):
                raise ValueError("drawing reference out of bounds")
            for ref in source.drawing_item_refs:
                if not 0 <= ref.drawing_index < len(self.drawings) or not 0 <= ref.item_index < len(
                    self.drawings[ref.drawing_index].items
                ):
                    raise ValueError("primitive reference out of bounds")
        return self


class RowBrief(RawModel):
    row_index: int
    periodicity_marker: str
    service_number: str | None


class PageBrief(RawModel):
    page_index: int
    page_type: PageType
    word_count: int
    drawing_count: int
    rows: list[RowBrief]


class RawSummary(RawModel):
    extraction_id: UUID
    raw_schema_version: Literal["1.0"] = "1.0"
    sha256: str
    page_type_counts: dict[str, int]
    row_count: int
    pages: list[PageBrief]


class RawDocument(RawModel):
    raw_schema_version: Literal["1.0"] = "1.0"
    extraction_id: UUID = Field(default_factory=uuid4)
    sha256: str = Field(pattern=r"^[0-9a-f]{64}$")
    source_size_bytes: int = Field(ge=0)
    page_count: int = Field(gt=0)
    metadata: dict[str, str | None]
    uniform_rotation: Literal[0, 90, 180, 270] | None
    units: Literal["points"] = "points"
    pages: list[RawPage]

    @model_validator(mode="after")
    def check_pages(self) -> "RawDocument":
        if [page.page_index for page in self.pages] != list(range(self.page_count)):
            raise ValueError("document page indices mismatch")
        return self

    def summary(self) -> RawSummary:
        return RawSummary(
            extraction_id=self.extraction_id,
            sha256=self.sha256,
            page_type_counts={
                kind: sum(page.page_type == kind for page in self.pages) for kind in PAGE_TYPES
            },
            row_count=sum(len(page.rows) for page in self.pages),
            pages=[
                PageBrief(
                    page_index=page.page_index,
                    page_type=page.page_type,
                    word_count=len(page.words),
                    drawing_count=len(page.drawings),
                    rows=[
                        RowBrief(
                            row_index=row.row_index,
                            periodicity_marker=row.periodicity_marker,
                            service_number=row.service_number,
                        )
                        for row in page.rows
                    ],
                )
                for page in self.pages
            ],
        )
