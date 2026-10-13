from collections.abc import Iterator
from pathlib import Path

import pymupdf
import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient

from railway_turno_decoder.config import Settings
from railway_turno_decoder.main import create_app


@pytest.fixture
def pdf_bytes() -> bytes:
    with pymupdf.open() as pdf:
        first = pdf.new_page(width=595, height=842)
        first.insert_text((72, 72), "Synthetic railway PDF fixture")
        second = pdf.new_page(width=300, height=400)
        second.set_rotation(90)
        pdf.set_metadata({"title": "Synthetic roster", "author": "pytest", "subject": "Test only"})
        return pdf.tobytes()


@pytest.fixture
def settings(tmp_path: Path) -> Settings:
    return Settings(data_dir=tmp_path / "data", max_upload_size=1024 * 1024)


@pytest.fixture
def app(settings: Settings) -> FastAPI:
    return create_app(settings)


@pytest.fixture
def client(app: FastAPI) -> Iterator[TestClient]:
    with TestClient(app) as client:
        yield client
