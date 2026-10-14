"""Versioned job envelope and PDF inspection data, without SGI semantics."""

from datetime import UTC, datetime
from enum import StrEnum
from typing import Literal
from uuid import UUID

from pydantic import BaseModel, ConfigDict, Field, model_validator

from railway_turno_decoder.raw_models import RawSummary


class StrictModel(BaseModel):
    model_config = ConfigDict(extra="forbid")


class JobStatus(StrEnum):
    CREATED = "created"
    PROCESSING = "processing"
    COMPLETED = "completed"
    FAILED = "failed"


class PageInfo(StrictModel):
    page_number: int = Field(ge=1)
    width_points: float = Field(gt=0, allow_inf_nan=False)
    height_points: float = Field(gt=0, allow_inf_nan=False)
    rotation_degrees: Literal[0, 90, 180, 270]
    has_extractable_text: bool


class DocumentInfo(StrictModel):
    page_count: int = Field(gt=0)
    metadata: dict[str, str | None] = Field(default_factory=dict)
    has_extractable_text: bool
    pages: list[PageInfo]

    @model_validator(mode="after")
    def check_pages(self) -> "DocumentInfo":
        if self.page_count != len(self.pages):
            raise ValueError("page_count must match the page list")
        if [page.page_number for page in self.pages] != list(range(1, self.page_count + 1)):
            raise ValueError("pages must be ordered and numbered from 1")
        if self.has_extractable_text != any(page.has_extractable_text for page in self.pages):
            raise ValueError("document text presence must match its pages")
        return self


class Job(StrictModel):
    schema_version: Literal["0.1"] = "0.1"
    job_id: UUID
    original_filename: str
    status: JobStatus = JobStatus.CREATED
    created_at: datetime = Field(default_factory=lambda: datetime.now(UTC))
    updated_at: datetime = Field(default_factory=lambda: datetime.now(UTC))
    source_size_bytes: int = Field(default=0, ge=0)
    document: DocumentInfo | None = None
    raw_summary: RawSummary | None = None
    warnings: list[str] = Field(default_factory=list)
    errors: list[str] = Field(default_factory=list)

    @model_validator(mode="after")
    def check_status(self) -> "Job":
        if self.status == JobStatus.COMPLETED and (self.document is None or self.errors):
            raise ValueError("completed jobs require a document and no errors")
        if self.status == JobStatus.FAILED and not self.errors:
            raise ValueError("failed jobs require an error")
        return self
