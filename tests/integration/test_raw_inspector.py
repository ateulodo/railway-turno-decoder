"""Raw persistence, inspector boundaries and safe browser output."""

import json
from io import BytesIO
from pathlib import Path
from uuid import UUID

import pymupdf
import pytest

from railway_turno_decoder.errors import PDFInspectionError, StorageError
from railway_turno_decoder.jobs import JobService, JobStore
from railway_turno_decoder.models import JobStatus
from railway_turno_decoder.pdf.extractor import extract_pdf
from railway_turno_decoder.pdf.render import render_page
from railway_turno_decoder.raw_models import RawDocument


def upload(client, pdf_bytes):
    response = client.post(
        "/jobs", files={"file": ("synthetic.pdf", pdf_bytes)}, follow_redirects=False
    )
    assert response.status_code == 303
    return response.headers["location"]


def test_raw_atomic_and_generation_sidecars(settings, pdf_bytes, tmp_path):
    service = JobService(settings)
    job, code = service.process(BytesIO(pdf_bytes), "synthetic.pdf")
    assert code == 200
    directory = service.store.directory(job.job_id)
    canonical = RawDocument.model_validate_json((directory / "raw.json").read_bytes())
    assert canonical.summary() == job.raw_summary
    assert service.store.load_raw_page(job, 0) == canonical.pages[0]
    assert not list(directory.glob("*.tmp"))
    assert len(list((directory / "raw_pages" / str(canonical.extraction_id)).iterdir())) == 2


@pytest.mark.parametrize("stage", ["sidecar", "replace", "budget"])
def test_failure_preserves_previous_raw_and_cleans_generation(
    settings, pdf_bytes, monkeypatch, stage
):
    service = JobService(settings)
    job, code = service.process(BytesIO(pdf_bytes), "synthetic.pdf")
    assert code == 200
    directory = service.store.directory(job.job_id)
    before = (directory / "raw.json").read_bytes()
    raw = extract_pdf(directory / "source.pdf").raw
    replace = Path.replace
    write = Path.write_text

    def broken_replace(path, target):
        if Path(target).name == "raw.json":
            raise OSError("disk failure")
        return replace(path, target)

    def broken_write(path, *args, **kwargs):
        if path.name == "1.json":
            path.write_bytes(b"partial")
            raise OSError("disk failure")
        return write(path, *args, **kwargs)

    calls = 0

    def check():
        nonlocal calls
        calls += 1
        if stage == "budget" and calls == 3:
            raise PDFInspectionError("budget")

    if stage == "replace":
        monkeypatch.setattr(Path, "replace", broken_replace)
    if stage == "sidecar":
        monkeypatch.setattr(Path, "write_text", broken_write)
    with pytest.raises(PDFInspectionError if stage == "budget" else StorageError):
        service.store.save_raw(job.job_id, raw, check)
    assert (directory / "raw.json").read_bytes() == before
    assert not (directory / "raw_pages" / str(raw.extraction_id)).exists()
    assert not list(directory.glob("raw.*.tmp"))
    assert service.store.load_raw_page(job, 0).page_index == 0


def test_raw_write_failure_marks_job_failed(settings, pdf_bytes, monkeypatch):
    service = JobService(settings)

    def broken(*args, **kwargs):
        raise StorageError()

    monkeypatch.setattr(service.store, "save_raw", broken)
    job, code = service.process(BytesIO(pdf_bytes), "synthetic.pdf")
    assert code == 503
    assert service.store.load(job.job_id).status == JobStatus.FAILED
    assert job.raw_summary is None


def test_inspector_and_render(client, pdf_bytes):
    location = upload(client, pdf_bytes)
    detail = client.get(location)
    assert "UNKNOWN: 2" in detail.text
    for index in [0, 1]:
        response = client.get(f"{location}/pages/{index}")
        assert response.status_code == 200
        assert f"Pagina {index + 1}" in response.text
        assert "layer-rows" in response.text and "raw-words" in response.text
        image = client.get(f"{location}/pages/{index}/image.png")
        assert image.status_code == 200
        assert image.headers["content-type"] == "image/png"
        assert image.headers["cache-control"] == "no-store"
        assert image.content.startswith(b"\x89PNG\r\n\x1a\n")
    assert client.get(f"{location}/source.pdf").status_code == 404
    assert client.get(f"{location}/raw.json").status_code == 404


@pytest.mark.parametrize("index", [-1, 2, 999999])
@pytest.mark.parametrize("suffix", ["", "/image.png"])
def test_page_index_rejected(client, pdf_bytes, index, suffix):
    location = upload(client, pdf_bytes)
    assert client.get(f"{location}/pages/{index}{suffix}").status_code == 404


@pytest.mark.parametrize("job_id", ["bad", "00000000-0000-0000-0000-000000000000"])
@pytest.mark.parametrize("suffix", ["", "/image.png"])
def test_inspector_job_id_rejected(client, job_id, suffix):
    assert client.get(f"/jobs/{job_id}/pages/0{suffix}").status_code == 404


@pytest.mark.parametrize("scale", ["nan", "inf", "-inf", "0", "-1", "0.249", "2.001", "1000", "x"])
def test_render_scale_rejected(client, pdf_bytes, scale):
    location = upload(client, pdf_bytes)
    assert client.get(f"{location}/pages/0/image.png?scale={scale}").status_code == 422


def test_pixel_limit_checked_before_pixmap(tmp_path, monkeypatch):
    path = tmp_path / "large.pdf"
    with pymupdf.open() as pdf:
        pdf.new_page(width=10000, height=10000)
        pdf.save(path)

    def forbidden(*args, **kwargs):
        pytest.fail("pixel ceiling must apply before allocating pixmap")

    monkeypatch.setattr(pymupdf.Page, "get_pixmap", forbidden)
    from railway_turno_decoder.errors import UserError

    with pytest.raises(UserError, match="pixel"):
        render_page(path, 0, 1, max_pages=1000, max_analysis_seconds=60)


def test_inspector_escaping(client):
    text = "<script>alert(1)</script>"
    with pymupdf.open() as pdf:
        pdf.new_page().insert_text((30, 50), text)
        data = pdf.tobytes()
    location = upload(client, data)
    response = client.get(f"{location}/pages/0")
    assert response.status_code == 200
    assert text not in response.text
    assert "&lt;script&gt;alert(1)&lt;/script&gt;" in response.text
    assert r"\u003cscript\u003e" in response.text
    # JSON embedded in an inert script is recoverable without HTML interpolation.
    start = response.text.index('<script type="application/json" id="raw-words">')
    body = response.text[start:].split(">", 1)[1].split("</script>", 1)[0]
    assert json.loads(body)[0]["text"] == text


@pytest.mark.parametrize("component", ["raw_pages", "generation", "page", "source"])
def test_inspector_symlinks_rejected(client, settings, pdf_bytes, tmp_path, component):
    location = upload(client, pdf_bytes)
    store = JobStore(settings.data_dir)
    from uuid import UUID

    job = store.load(UUID(location.rsplit("/", 1)[1]))
    directory = store.directory(job.job_id)
    generation = directory / "raw_pages" / str(job.raw_summary.extraction_id)
    if component == "source":
        path = directory / "source.pdf"
    elif component == "raw_pages":
        path = directory / "raw_pages"
    elif component == "generation":
        path = generation
    else:
        path = generation / "0.json"
    target = tmp_path / f"outside-{component}"
    path.rename(target)
    path.symlink_to(target, target_is_directory=target.is_dir())
    suffix = "/image.png" if component == "source" else ""
    response = client.get(f"{location}/pages/0{suffix}")
    assert response.status_code in (404, 503)
    assert str(tmp_path) not in response.text


def test_legacy_job_without_raw_has_controlled_inspector(client, settings, pdf_bytes):
    location = upload(client, pdf_bytes)
    path = next((settings.data_dir / "jobs").glob("*/job.json"))
    data = json.loads(path.read_text())
    del data["raw_summary"]
    path.write_text(json.dumps(data))
    assert client.get(location).status_code == 200
    assert client.get(location + "/pages/0").status_code == 404


def test_jobs_root_symlink_rejected(settings, tmp_path):
    store = JobStore(settings.data_dir)
    settings.data_dir.mkdir(parents=True)
    outside = tmp_path / "outside-jobs"
    outside.mkdir()
    store.root.symlink_to(outside, target_is_directory=True)
    with pytest.raises(StorageError):
        store.create("synthetic.pdf")


@pytest.mark.parametrize("scale", [0.25, 2.0])
def test_valid_render_scale_boundaries(client, pdf_bytes, scale):
    location = upload(client, pdf_bytes)
    assert client.get(f"{location}/pages/0/image.png?scale={scale}").status_code == 200


def test_render_budget_after_native_call(tmp_path, pdf_bytes, monkeypatch):
    path = tmp_path / "render.pdf"
    path.write_bytes(pdf_bytes)
    now = 0.0
    original = pymupdf.Page.get_pixmap

    def expensive(page, *args, **kwargs):
        nonlocal now
        result = original(page, *args, **kwargs)
        now = 1.0
        return result

    monkeypatch.setattr("railway_turno_decoder.pdf.render.monotonic", lambda: now)
    monkeypatch.setattr("railway_turno_decoder.pdf.reader.monotonic", lambda: now)
    monkeypatch.setattr(pymupdf.Page, "get_pixmap", expensive)
    with pytest.raises(PDFInspectionError, match="budget"):
        render_page(path, 0, 1, max_pages=1000, max_analysis_seconds=1)
    now = 0.0
    monkeypatch.setattr(pymupdf.Page, "get_pixmap", original)
    assert render_page(path, 0, 1, max_pages=1000, max_analysis_seconds=1).startswith(b"\x89PNG")


def test_reanalysis_generations_do_not_mix(settings, pdf_bytes):
    service = JobService(settings)
    job, code = service.process(BytesIO(pdf_bytes), "synthetic.pdf")
    assert code == 200
    directory = service.store.directory(job.job_id)
    previous = job.raw_summary.extraction_id
    raw = extract_pdf(directory / "source.pdf").raw
    raw.pages[0].words[0].text = "new generation"
    service.store.save_raw(job.job_id, raw)
    # Until job.json changes its generation, the previous page remains available.
    assert service.store.load_raw_page(job, 0).words[0].text != "new generation"
    job.raw_summary = raw.summary()
    service.store.save(job)
    reloaded = service.store.load(job.job_id)
    assert service.store.load_raw_page(reloaded, 0).words[0].text == "new generation"
    assert (directory / "raw_pages" / str(previous)).is_dir()


def test_raw_cleanup_failure_preserves_original_error(settings, pdf_bytes, monkeypatch):
    service = JobService(settings)
    job, code = service.process(BytesIO(pdf_bytes), "synthetic.pdf")
    assert code == 200
    directory = service.store.directory(job.job_id)
    before = (directory / "raw.json").read_bytes()
    raw = extract_pdf(directory / "source.pdf").raw

    def bad_cleanup(*args, **kwargs):
        raise OSError("cleanup failure")

    monkeypatch.setattr("railway_turno_decoder.jobs.shutil.rmtree", bad_cleanup)
    error = PDFInspectionError("budget expired")

    def check():
        raise error

    with pytest.raises(PDFInspectionError) as captured:
        service.store.save_raw(job.job_id, raw, check)
    assert captured.value is error
    assert (directory / "raw.json").read_bytes() == before


@pytest.mark.parametrize("suffix", ["", "/image.png"])
def test_uppercase_existing_job_is_rejected(client, pdf_bytes, monkeypatch, suffix):
    # A present job makes this fail if canonical validation is removed.
    identifier = UUID("abcdef12-3456-4abc-9def-0123456789ab")
    monkeypatch.setattr("railway_turno_decoder.jobs.uuid4", lambda: identifier)
    location = upload(client, pdf_bytes)
    assert location == f"/jobs/{identifier}"
    assert client.get(f"{location}/pages/0{suffix}").status_code == 200
    assert client.get(f"/jobs/{str(identifier).upper()}/pages/0{suffix}").status_code == 404


@pytest.mark.parametrize("suffix", ["", "/image.png"])
@pytest.mark.parametrize(
    "content", [b"PRIVATE_INTERNAL broken JSON", b'{"page_index":"PRIVATE_INTERNAL"}', b"\xff"]
)
def test_corrupt_sidecar_has_controlled_http_error(client, settings, pdf_bytes, suffix, content):
    location = upload(client, pdf_bytes)
    store = JobStore(settings.data_dir)
    job = store.load(UUID(location.rsplit("/", 1)[1]))
    sidecar = (
        store.directory(job.job_id) / "raw_pages" / str(job.raw_summary.extraction_id) / "0.json"
    )
    sidecar.write_bytes(content)
    response = client.get(f"{location}/pages/0{suffix}")
    assert response.status_code == 503
    assert "PRIVATE_INTERNAL" not in response.text
    assert "Traceback" not in response.text
    assert str(settings.data_dir) not in response.text
    assert "ValidationError" not in response.text


@pytest.mark.parametrize("status", [JobStatus.CREATED, JobStatus.PROCESSING, JobStatus.FAILED])
@pytest.mark.parametrize("suffix", ["", "/image.png"])
def test_incomplete_job_inspector_is_unavailable(client, settings, pdf_bytes, status, suffix):
    location = upload(client, pdf_bytes)
    store = JobStore(settings.data_dir)
    job = store.load(UUID(location.rsplit("/", 1)[1]))
    job.status = status
    job.errors = ["synthetic failure"] if status == JobStatus.FAILED else []
    # Retain valid raw data to exercise the status guard, independently of
    # the separate guard against absent raw geometry.
    assert job.raw_summary is not None
    store.save(job)
    assert client.get(location).status_code == 200
    response = client.get(f"{location}/pages/0{suffix}")
    assert response.status_code == 404
    assert "Traceback" not in response.text
    assert str(settings.data_dir) not in response.text
