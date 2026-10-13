from pathlib import Path

import pytest
from pydantic import ValidationError

from railway_turno_decoder.config import Settings


def test_defaults(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.delenv("DATA_DIR", raising=False)
    monkeypatch.delenv("MAX_UPLOAD_SIZE", raising=False)
    monkeypatch.delenv("MAX_PDF_PAGES", raising=False)
    monkeypatch.delenv("MAX_PDF_ANALYSIS_SECONDS", raising=False)
    assert Settings.from_env() == Settings(data_dir=Path("data"), max_upload_size=20 * 1024 * 1024)
    assert Settings.from_env().max_pdf_pages == 1000
    assert Settings.from_env().max_pdf_analysis_seconds == 60


def test_environment(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> None:
    monkeypatch.setenv("DATA_DIR", str(tmp_path / "configured"))
    monkeypatch.setenv("MAX_UPLOAD_SIZE", "4096")
    monkeypatch.setenv("MAX_PDF_PAGES", "750")
    monkeypatch.setenv("MAX_PDF_ANALYSIS_SECONDS", "12.5")
    settings = Settings.from_env()
    assert settings.data_dir == tmp_path / "configured"
    assert settings.max_upload_size == 4096
    assert settings.max_pdf_pages == 750
    assert settings.max_pdf_analysis_seconds == 12.5


@pytest.mark.parametrize("value", ["0", "-1", "not-an-integer"])
def test_invalid_limit(monkeypatch: pytest.MonkeyPatch, value: str) -> None:
    monkeypatch.setenv("MAX_UPLOAD_SIZE", value)
    with pytest.raises(ValidationError):
        Settings.from_env()


@pytest.mark.parametrize("value", ["0", "-1", "1.5", "invalid"])
def test_invalid_page_limit(monkeypatch: pytest.MonkeyPatch, value: str) -> None:
    monkeypatch.setenv("MAX_PDF_PAGES", value)
    with pytest.raises(ValidationError):
        Settings.from_env()


@pytest.mark.parametrize("value", ["0", "-1", "nan", "inf", "invalid"])
def test_invalid_analysis_budget(monkeypatch: pytest.MonkeyPatch, value: str) -> None:
    monkeypatch.setenv("MAX_PDF_ANALYSIS_SECONDS", value)
    with pytest.raises(ValidationError):
        Settings.from_env()
