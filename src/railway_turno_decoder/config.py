"""Central configuration; environment variables are read only here."""

import os
from pathlib import Path

from pydantic import BaseModel, ConfigDict, Field

DEFAULT_MAX_PDF_PAGES = 1000
DEFAULT_MAX_PDF_ANALYSIS_SECONDS = 60.0


class Settings(BaseModel):
    model_config = ConfigDict(frozen=True)

    data_dir: Path = Path("data")
    max_upload_size: int = Field(default=20 * 1024 * 1024, gt=0)
    max_pdf_pages: int = Field(default=DEFAULT_MAX_PDF_PAGES, gt=0)
    max_pdf_analysis_seconds: float = Field(
        default=DEFAULT_MAX_PDF_ANALYSIS_SECONDS, gt=0, allow_inf_nan=False
    )

    @classmethod
    def from_env(cls) -> "Settings":
        values: dict[str, str] = {}
        if "DATA_DIR" in os.environ:
            values["data_dir"] = os.environ["DATA_DIR"]
        if "MAX_UPLOAD_SIZE" in os.environ:
            values["max_upload_size"] = os.environ["MAX_UPLOAD_SIZE"]
        if "MAX_PDF_PAGES" in os.environ:
            values["max_pdf_pages"] = os.environ["MAX_PDF_PAGES"]
        if "MAX_PDF_ANALYSIS_SECONDS" in os.environ:
            values["max_pdf_analysis_seconds"] = os.environ["MAX_PDF_ANALYSIS_SECONDS"]
        return cls.model_validate(values)
