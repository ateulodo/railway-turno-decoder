import json
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path
from threading import Barrier
from typing import BinaryIO
from uuid import UUID, uuid4

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient

from railway_turno_decoder.config import Settings
from railway_turno_decoder.jobs import JobService
from railway_turno_decoder.main import create_app


def test_home(client: TestClient) -> None:
    response = client.get("/")
    assert response.status_code == 200
    assert "Railway Turno Decoder" in response.text
    assert 'type="file"' in response.text
    assert "decoder SGI non è ancora implementato" in response.text
    assert "htmx.min.js" in response.text


def test_health(client: TestClient) -> None:
    response = client.get("/health")
    assert response.status_code == 200
    assert response.json() == {"status": "ok"}


@pytest.mark.parametrize("asset", ["app.css", "app.js", "htmx.min.js"])
def test_local_assets(client: TestClient, asset: str) -> None:
    response = client.get(f"/static/{asset}")
    assert response.status_code == 200
    assert len(response.content) > 100


def test_valid_pdf_creates_durable_job(
    client: TestClient, settings: Settings, pdf_bytes: bytes
) -> None:
    response = client.post(
        "/jobs",
        files={"file": ("roster.pdf", pdf_bytes, "application/pdf")},
        follow_redirects=False,
    )
    assert response.status_code == 303
    location = response.headers["location"]
    job_id = UUID(location.rsplit("/", 1)[1])
    assert job_id.version == 4
    directory = settings.data_dir / "jobs" / str(job_id)
    assert directory.is_dir()
    assert (directory / "source.pdf").read_bytes() == pdf_bytes
    data = json.loads((directory / "job.json").read_text())
    assert data["schema_version"] == "0.1"
    assert data["status"] == "completed"
    assert data["job_id"] == str(job_id)
    assert data["original_filename"] == "roster.pdf"
    assert data["source_size_bytes"] == len(pdf_bytes)
    assert data["document"]["page_count"] == 2
    assert data["document"]["metadata"]["title"] == "Synthetic roster"
    assert data["document"]["has_extractable_text"] is True
    assert data["warnings"] == data["errors"] == []
    detail = client.get(location)
    assert detail.status_code == 200
    for text in ["roster.pdf", str(job_id), "completed", "Synthetic roster", "90°", "400.00"]:
        assert text in detail.text
    assert detail.text.count('<th scope="row">') == 2


def test_htmx_upload_redirects(client: TestClient, pdf_bytes: bytes) -> None:
    response = client.post(
        "/jobs", files={"file": ("test.pdf", pdf_bytes)}, headers={"HX-Request": "true"}
    )
    assert response.status_code == 200
    assert client.get(response.headers["HX-Redirect"]).status_code == 200


def test_distinct_server_uuid_per_upload(client: TestClient, pdf_bytes: bytes) -> None:
    locations = [
        client.post(
            "/jobs", files={"file": ("same.pdf", pdf_bytes)}, follow_redirects=False
        ).headers["location"]
        for _ in range(2)
    ]
    assert locations[0] != locations[1]


@pytest.mark.parametrize("content", [b"this is not PDF", b"%PDF-1.7\nbroken", b""])
def test_invalid_content_is_failed_job(
    client: TestClient, settings: Settings, content: bytes
) -> None:
    response = client.post("/jobs", files={"file": ("fake.pdf", content, "application/pdf")})
    assert response.status_code == 422
    assert "Non riuscito" in response.text
    paths = list((settings.data_dir / "jobs").glob("*/job.json"))
    assert len(paths) == 1
    data = json.loads(paths[0].read_text())
    assert data["status"] == "failed"
    assert data["document"] is None
    assert data["errors"]
    assert client.get(f"/jobs/{data['job_id']}").status_code == 200


def test_non_pdf_extension_is_rejected(client: TestClient, pdf_bytes: bytes) -> None:
    response = client.post("/jobs", files={"file": ("file.txt", pdf_bytes, "application/pdf")})
    assert response.status_code == 415


def test_content_type_is_not_used_as_pdf_proof(client: TestClient, pdf_bytes: bytes) -> None:
    response = client.post(
        "/jobs",
        files={"file": ("test.PDF", pdf_bytes, "application/octet-stream")},
        follow_redirects=False,
    )
    assert response.status_code == 303


@pytest.mark.parametrize("filename", ["../../escape.pdf", "/tmp/escape.pdf", r"..\..\escape.pdf"])
def test_filename_cannot_build_paths(
    client: TestClient, settings: Settings, pdf_bytes: bytes, filename: str
) -> None:
    response = client.post("/jobs", files={"file": (filename, pdf_bytes)}, follow_redirects=False)
    assert response.status_code == 303
    jobs_root = settings.data_dir / "jobs"
    directories = list(jobs_root.iterdir())
    assert len(directories) == 1
    assert UUID(directories[0].name).version == 4
    assert {path.name for path in directories[0].iterdir()} == {"source.pdf", "job.json"}
    assert not (settings.data_dir.parent / "escape.pdf").exists()
    data = json.loads((directories[0] / "job.json").read_text())
    assert data["original_filename"] == filename


def test_filename_and_metadata_are_html_escaped(client: TestClient) -> None:
    import pymupdf

    with pymupdf.open() as pdf:
        pdf.new_page()
        pdf.set_metadata({"title": "<script>alert('metadata')</script>"})
        content = pdf.tobytes()
    response = client.post(
        "/jobs", files={"file": ("<script>alert('filename')</script>.pdf", content)}
    )
    assert response.status_code == 200
    assert "<script>alert(" not in response.text
    assert "&lt;script&gt;" in response.text


def test_exact_file_limit(settings: Settings, pdf_bytes: bytes) -> None:
    app = create_app(settings.model_copy(update={"max_upload_size": len(pdf_bytes)}))
    with TestClient(app) as client:
        response = client.post("/jobs", files={"file": ("test.pdf", pdf_bytes)})
        assert response.status_code == 200
        response = client.post("/jobs", files={"file": ("test.pdf", pdf_bytes + b" ")})
        assert response.status_code == 413


def test_oversized_content_length_rejected_before_parsing(
    client: TestClient, settings: Settings
) -> None:
    response = client.post(
        "/jobs", content=b"", headers={"Content-Length": str(settings.max_upload_size + 65537)}
    )
    assert response.status_code == 413
    assert not (settings.data_dir / "jobs").exists()


@pytest.mark.parametrize("content_length", ["-1", "invalid"])
def test_invalid_content_length(client: TestClient, content_length: str) -> None:
    response = client.post("/jobs", content=b"", headers={"Content-Length": content_length})
    assert response.status_code == 400


def test_extra_fields_and_client_job_id_are_rejected(client: TestClient, pdf_bytes: bytes) -> None:
    response = client.post(
        "/jobs", files={"file": ("test.pdf", pdf_bytes)}, data={"job_id": str(uuid4())}
    )
    assert response.status_code == 400


def test_multiple_files_are_rejected(client: TestClient, pdf_bytes: bytes) -> None:
    response = client.post(
        "/jobs", files=[("file", ("one.pdf", pdf_bytes)), ("file", ("two.pdf", pdf_bytes))]
    )
    assert response.status_code == 400


@pytest.mark.parametrize("job_id", ["not-a-uuid", str(uuid4()), "..%2F..%2Fsource.pdf"])
def test_missing_and_invalid_jobs(client: TestClient, job_id: str) -> None:
    assert client.get(f"/jobs/{job_id}").status_code == 404


def test_missing_file(client: TestClient) -> None:
    assert client.post("/jobs", files={"other": ("empty.pdf", b"")}).status_code == 400


def test_non_multipart_request(client: TestClient) -> None:
    assert client.post("/jobs", content=b"not multipart").status_code == 415


def test_invalid_multipart(client: TestClient) -> None:
    response = client.post(
        "/jobs", content=b"invalid", headers={"Content-Type": "multipart/form-data; boundary=x"}
    )
    assert response.status_code == 400


def test_unexpected_processing_failure_is_persisted(
    client: TestClient, settings: Settings, pdf_bytes: bytes, monkeypatch: pytest.MonkeyPatch
) -> None:
    def broken_reader(path: Path, **limits: object) -> None:
        raise RuntimeError("Private internal failure details")

    monkeypatch.setattr("railway_turno_decoder.jobs.inspect_pdf", broken_reader)
    response = client.post("/jobs", files={"file": ("test.pdf", pdf_bytes)})
    assert response.status_code == 500
    assert "Private internal failure" not in response.text
    data = json.loads(next((settings.data_dir / "jobs").glob("*/job.json")).read_text())
    assert data["status"] == "failed"
    assert data["errors"] == ["Errore inatteso durante l'analisi del PDF."]


def test_source_write_failure_is_persisted(
    client: TestClient, settings: Settings, pdf_bytes: bytes, monkeypatch: pytest.MonkeyPatch
) -> None:
    def broken_copy(*args: object) -> int:
        raise OSError("No space on device")

    monkeypatch.setattr(JobService, "_copy_source", broken_copy)
    response = client.post("/jobs", files={"file": ("test.pdf", pdf_bytes)})
    assert response.status_code == 503
    data = json.loads(next((settings.data_dir / "jobs").glob("*/job.json")).read_text())
    assert data["status"] == "failed"


def test_unwritable_storage_is_reported(pdf_bytes: bytes, tmp_path: Path) -> None:
    data_file = tmp_path / "not-a-directory"
    data_file.write_text("blocked")
    with TestClient(create_app(Settings(data_dir=data_file))) as client:
        response = client.post("/jobs", files={"file": ("test.pdf", pdf_bytes)})
    assert response.status_code == 503
    assert "DATA_DIR" in response.text


def test_job_survives_app_restart(client: TestClient, settings: Settings, pdf_bytes: bytes) -> None:
    response = client.post("/jobs", files={"file": ("test.pdf", pdf_bytes)}, follow_redirects=False)
    with TestClient(create_app(settings)) as restarted:
        assert restarted.get(response.headers["location"]).status_code == 200


def test_htmx_failed_upload_renders_error(client: TestClient) -> None:
    response = client.post(
        "/jobs", files={"file": ("test.pdf", b"not PDF")}, headers={"HX-Request": "true"}
    )
    assert response.status_code == 422
    assert "HX-Redirect" not in response.headers
    assert 'id="page-content"' in response.text
    assert "Non riuscito" in response.text


def test_page_limit_is_a_controlled_persisted_failure(settings: Settings, pdf_bytes: bytes) -> None:
    settings = settings.model_copy(update={"max_pdf_pages": 1})
    with TestClient(create_app(settings)) as client:
        response = client.post("/jobs", files={"file": ("two-pages.pdf", pdf_bytes)})
    assert response.status_code == 422
    assert "limite consentito di 1 pagine" in response.text
    assert "Traceback" not in response.text
    assert str(settings.data_dir) not in response.text
    data = json.loads(next((settings.data_dir / "jobs").glob("*/job.json")).read_text())
    assert data["status"] == "failed"
    assert data["document"] is None
    assert len(data["errors"]) == 1


def test_cooperative_budget_is_a_controlled_persisted_failure(
    settings: Settings, pdf_bytes: bytes, monkeypatch: pytest.MonkeyPatch
) -> None:
    # Start and first post-open check: deterministically expired, without sleeps.
    ticks = iter([0.0, 1.0])
    monkeypatch.setattr("railway_turno_decoder.pdf.reader.monotonic", lambda: next(ticks))
    settings = settings.model_copy(update={"max_pdf_analysis_seconds": 1.0})
    with TestClient(create_app(settings)) as client:
        response = client.post("/jobs", files={"file": ("test.pdf", pdf_bytes)})
    assert response.status_code == 422
    assert "budget di tempo" in response.text
    assert "Traceback" not in response.text
    data = json.loads(next((settings.data_dir / "jobs").glob("*/job.json")).read_text())
    assert data["status"] == "failed"
    assert data["document"] is None


def test_failed_state_replace_error_returns_storage_error(
    client: TestClient, settings: Settings, monkeypatch: pytest.MonkeyPatch
) -> None:
    original_replace = Path.replace

    def fail_failed_replace(path: Path, target: Path) -> Path:
        if path.name == "job.json.tmp" and json.loads(path.read_text())["status"] == "failed":
            raise OSError("simulated inability to persist failed state")
        return original_replace(path, target)

    monkeypatch.setattr(Path, "replace", fail_failed_replace)
    response = client.post("/jobs", files={"file": ("invalid.pdf", b"not a PDF")})
    assert response.status_code == 503
    assert "DATA_DIR" in response.text
    assert "simulated" not in response.text
    assert "Traceback" not in response.text
    assert str(settings.data_dir) not in response.text
    path = next((settings.data_dir / "jobs").glob("*/job.json"))
    assert json.loads(path.read_text())["status"] == "processing"
    assert not path.with_name("job.json.tmp").exists()
    assert not path.with_name("source.pdf.part").exists()


def test_concurrent_requests_keep_jobs_isolated(
    app: FastAPI, settings: Settings, pdf_bytes: bytes, monkeypatch: pytest.MonkeyPatch
) -> None:
    # Synchronize only file copying. Native PyMuPDF inspection retains its
    # production lock; no unsupported parallel native calls are introduced.
    barrier = Barrier(2)
    original_copy = JobService._copy_source

    def synchronized_copy(service: JobService, upload: BinaryIO, destination: Path) -> int:
        size = original_copy(service, upload, destination)
        barrier.wait(timeout=5)
        return size

    def upload_one(filename: str) -> str:
        with TestClient(app) as client:
            response = client.post(
                "/jobs", files={"file": (filename, pdf_bytes)}, follow_redirects=False
            )
            assert response.status_code == 303
            return response.headers["location"]

    monkeypatch.setattr(JobService, "_copy_source", synchronized_copy)
    with ThreadPoolExecutor(max_workers=2) as pool:
        locations = list(pool.map(upload_one, ["first.pdf", "second.pdf"]))
    assert len(set(locations)) == 2
    records = [
        json.loads(path.read_text()) for path in (settings.data_dir / "jobs").glob("*/job.json")
    ]
    assert {record["original_filename"] for record in records} == {"first.pdf", "second.pdf"}
    assert all(record["status"] == "completed" for record in records)
    for record in records:
        directory = settings.data_dir / "jobs" / record["job_id"]
        assert (directory / "source.pdf").read_bytes() == pdf_bytes
        assert not (directory / "source.pdf.part").exists()
