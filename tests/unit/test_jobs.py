from collections.abc import Iterator
from contextlib import contextmanager
from io import BytesIO
from pathlib import Path
from typing import BinaryIO
from uuid import UUID, uuid4

import pytest
from pydantic import ValidationError

from railway_turno_decoder.config import Settings
from railway_turno_decoder.errors import StorageError
from railway_turno_decoder.jobs import JobService, JobStore
from railway_turno_decoder.models import Job, JobStatus


def test_store_uses_only_uuid(settings: Settings) -> None:
    store = JobStore(settings.data_dir)
    with pytest.raises(TypeError):
        store.directory("../../escape")  # type: ignore[arg-type]


def test_completed_job_requires_document() -> None:
    with pytest.raises(ValidationError):
        Job(job_id=uuid4(), original_filename="test.pdf", status=JobStatus.COMPLETED)


def test_failed_job_requires_error() -> None:
    with pytest.raises(ValidationError):
        Job(job_id=uuid4(), original_filename="test.pdf", status=JobStatus.FAILED)


def test_service_limit_removes_partial_source(settings: Settings, pdf_bytes: bytes) -> None:
    service = JobService(settings.model_copy(update={"max_upload_size": 10}))
    job, code = service.process(BytesIO(pdf_bytes), "test.pdf")
    directory = service.store.directory(job.job_id)
    assert code == 413
    assert service.store.load(job.job_id).status == JobStatus.FAILED
    assert not (directory / "source.pdf.part").exists()
    assert not (directory / "source.pdf").exists()


def test_processing_states_are_persisted(
    settings: Settings, pdf_bytes: bytes, monkeypatch: pytest.MonkeyPatch
) -> None:
    service = JobService(settings)
    states = []
    original_save = service.store.save

    def record_save(job: Job) -> None:
        states.append(job.status)
        original_save(job)

    monkeypatch.setattr(service.store, "save", record_save)
    job, code = service.process(BytesIO(pdf_bytes), "test.pdf")
    assert code == 200
    assert states == [JobStatus.CREATED, JobStatus.PROCESSING, JobStatus.COMPLETED]
    assert service.store.load(job.job_id) == job


def test_final_json_write_failure_never_leaves_completed(
    settings: Settings, pdf_bytes: bytes, monkeypatch: pytest.MonkeyPatch
) -> None:
    service = JobService(settings)
    original_save = service.store.save

    def fail_completed(job: Job) -> None:
        if job.status == JobStatus.COMPLETED:
            raise StorageError()
        original_save(job)

    monkeypatch.setattr(service.store, "save", fail_completed)
    job, code = service.process(BytesIO(pdf_bytes), "test.pdf")
    assert code == 503
    assert job.document is None
    assert service.store.load(job.job_id).status == JobStatus.FAILED


def test_json_is_written_atomically(settings: Settings) -> None:
    store = JobStore(settings.data_dir)
    job = store.create("test.pdf")
    directory = store.directory(job.job_id)
    assert store.load(job.job_id) == job
    assert not (directory / "job.json.tmp").exists()


def test_corrupted_job_json(settings: Settings) -> None:
    store = JobStore(settings.data_dir)
    job = store.create("test.pdf")
    (store.directory(job.job_id) / "job.json").write_text("broken", encoding="utf-8")
    with pytest.raises(StorageError):
        store.load(job.job_id)


def test_symlink_job_is_not_followed(settings: Settings, tmp_path: Path) -> None:
    store = JobStore(settings.data_dir)
    store.root.mkdir(parents=True)
    job_id = uuid4()
    (store.root / str(job_id)).symlink_to(tmp_path, target_is_directory=True)
    with pytest.raises(StorageError):
        store.load(job_id)


def test_mismatching_stored_uuid_is_rejected(settings: Settings) -> None:
    store = JobStore(settings.data_dir)
    job = store.create("test.pdf")
    different_job = job.model_copy(update={"job_id": UUID(int=0)})
    (store.directory(job.job_id) / "job.json").write_text(different_job.model_dump_json())
    with pytest.raises(StorageError):
        store.load(job.job_id)


def test_first_save_failure_removes_only_new_empty_job_directory(
    settings: Settings, monkeypatch: pytest.MonkeyPatch
) -> None:
    store = JobStore(settings.data_dir)
    existing = store.create("keep.pdf")
    existing_directory = store.directory(existing.job_id)
    previous_json = (existing_directory / "job.json").read_bytes()
    error = StorageError()

    def fail_first_save(job: Job) -> None:
        raise error

    monkeypatch.setattr(store, "save", fail_first_save)
    with pytest.raises(StorageError) as captured:
        store.create("new.pdf")
    assert captured.value is error
    assert list(store.root.iterdir()) == [existing_directory]
    assert (existing_directory / "job.json").read_bytes() == previous_json


def test_initial_atomic_replace_failure_cleans_new_directory(
    settings: Settings, monkeypatch: pytest.MonkeyPatch
) -> None:
    store = JobStore(settings.data_dir)
    error = OSError("simulated replace failure")

    def fail_replace(*args: object, **kwargs: object) -> None:
        raise error

    monkeypatch.setattr(Path, "replace", fail_replace)
    with pytest.raises(StorageError) as captured:
        store.create("new.pdf")
    assert captured.value.__cause__ is error
    assert list(store.root.iterdir()) == []


def test_cleanup_refuses_nonempty_new_job_directory(
    settings: Settings, monkeypatch: pytest.MonkeyPatch
) -> None:
    store = JobStore(settings.data_dir)
    error = StorageError()
    marker = None

    def fail_with_unexpected_file(job: Job) -> None:
        nonlocal marker
        marker = store.directory(job.job_id) / "do-not-remove.txt"
        marker.write_text("preserve this file")
        raise error

    monkeypatch.setattr(store, "save", fail_with_unexpected_file)
    with pytest.raises(StorageError) as captured:
        store.create("new.pdf")
    assert captured.value is error
    assert marker is not None and marker.read_text() == "preserve this file"


def test_cleanup_failure_preserves_original_exception(
    settings: Settings, monkeypatch: pytest.MonkeyPatch
) -> None:
    store = JobStore(settings.data_dir)
    error = StorageError()

    def fail_save(job: Job) -> None:
        raise error

    def fail_cleanup(*args: object, **kwargs: object) -> None:
        raise PermissionError("simulated cleanup permission failure")

    monkeypatch.setattr(store, "save", fail_save)
    monkeypatch.setattr(Path, "rmdir", fail_cleanup)
    with pytest.raises(StorageError) as captured:
        store.create("new.pdf")
    assert captured.value is error
    assert len(list(store.root.iterdir())) == 1


def test_uuid_collision_never_removes_existing_job(
    settings: Settings, monkeypatch: pytest.MonkeyPatch
) -> None:
    store = JobStore(settings.data_dir)
    existing = store.create("keep.pdf")
    directory = store.directory(existing.job_id)
    previous_json = (directory / "job.json").read_bytes()
    monkeypatch.setattr("railway_turno_decoder.jobs.uuid4", lambda: existing.job_id)
    with pytest.raises(StorageError):
        store.create("new.pdf")
    assert (directory / "job.json").read_bytes() == previous_json


def test_atomic_replace_failure_preserves_previous_valid_json(
    settings: Settings, monkeypatch: pytest.MonkeyPatch
) -> None:
    store = JobStore(settings.data_dir)
    job = store.create("before.pdf")
    directory = store.directory(job.job_id)
    previous_json = (directory / "job.json").read_bytes()
    job.original_filename = "after.pdf"
    error = OSError("simulated atomic replace failure")

    def fail_replace(*args: object, **kwargs: object) -> None:
        raise error

    monkeypatch.setattr(Path, "replace", fail_replace)
    with pytest.raises(StorageError) as captured:
        store.save(job)
    assert captured.value.__cause__ is error
    assert (directory / "job.json").read_bytes() == previous_json
    assert store.load(job.job_id).original_filename == "before.pdf"
    assert not (directory / "job.json.tmp").exists()


def test_partial_pdf_write_failure_removes_partial_source(
    settings: Settings, pdf_bytes: bytes, monkeypatch: pytest.MonkeyPatch
) -> None:
    service = JobService(settings)
    original_open = Path.open
    written = []

    class FailingWriter:
        def __init__(self, handle: BinaryIO) -> None:
            self.handle = handle

        def write(self, chunk: bytes) -> int:
            written.append(self.handle.write(chunk[:8]))
            raise OSError("simulated disk full after partial write")

    @contextmanager
    def failing_open(path: Path, *args: object, **kwargs: object) -> Iterator[object]:
        with original_open(path, *args, **kwargs) as handle:
            yield FailingWriter(handle) if path.name == "source.pdf.part" else handle

    monkeypatch.setattr(Path, "open", failing_open)
    job, code = service.process(BytesIO(pdf_bytes), "test.pdf")
    directory = service.store.directory(job.job_id)
    assert written == [8]
    assert code == 503
    assert service.store.load(job.job_id).status == JobStatus.FAILED
    assert not (directory / "source.pdf.part").exists()
    assert not (directory / "source.pdf").exists()


def test_symlink_job_json_is_not_followed(settings: Settings, tmp_path: Path) -> None:
    store = JobStore(settings.data_dir)
    job = store.create("test.pdf")
    path = store.directory(job.job_id) / "job.json"
    external = tmp_path / "external-record.json"
    external.write_bytes(path.read_bytes())
    path.unlink()
    path.symlink_to(external)
    with pytest.raises(StorageError):
        store.load(job.job_id)
