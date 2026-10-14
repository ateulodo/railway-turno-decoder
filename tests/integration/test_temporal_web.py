"""M3A consumes synthetic M2 jobs; caches are separate from immutable raw."""

import json
from copy import deepcopy
from pathlib import Path
from uuid import UUID

import pymupdf
import pytest

from railway_turno_decoder.errors import StorageError
from railway_turno_decoder.jobs import JobStore
from railway_turno_decoder.models import JobStatus
from railway_turno_decoder.temporal.storage import TemporalStore


def upload_diagram(client):
    with pymupdf.open() as pdf:
        page = pdf.new_page(width=600, height=400)
        page.insert_text((15, 22), "TURNO: synthetic PROFILO: fixture", fontsize=10)
        page.insert_text((10, 80), "S", fontsize=10)
        page.insert_text((10, 140), "7", fontsize=10)
        page.insert_text((430, 100), "Lav Cct Km Not Rip", fontsize=8)
        for hour, x in [(14, 100), (15, 200), (16, 300), (17, 400)]:
            text = str(hour)
            width = pymupdf.get_text_length(text, fontsize=6)
            page.insert_text((x - width / 2, 150), text, fontsize=6)
        page.draw_line((100, 150), (400, 150), width=0.5)
        for lo, hi in [(885, 888), (911, 920), (954, 977)]:
            x0 = 100 + (lo - 840) / 0.6
            x1 = 100 + (hi - 840) / 0.6
            page.draw_line((x0, 110), (x1, 110), width=1)
            page.insert_text((x0, 118), str(lo % 60), fontsize=6)
            text = str(hi % 60)
            width = pymupdf.get_text_length(text, fontsize=6)
            page.insert_text((x1 - width, 125), text, fontsize=6)
        content = pdf.tobytes()
    response = client.post(
        "/jobs", files={"file": ("synthetic-timeline.pdf", content)}, follow_redirects=False
    )
    assert response.status_code == 303
    return response.headers["location"]


def record(settings, location):
    store = JobStore(settings.data_dir)
    job = store.load(UUID(location.rsplit("/", 1)[1]))
    return store, job, store.load_raw_page(job, 0)


def test_temporal_api_cache_raw_immutable_and_inspector(client, settings, monkeypatch):
    location = upload_diagram(client)
    store, job, page = record(settings, location)
    directory = store.directory(job.job_id)
    before = {
        name: (directory / name).read_bytes() for name in ["raw.json", "job.json", "source.pdf"]
    }
    response = client.get(location + "/pages/0/temporal.json")
    assert response.status_code == 200
    assert response.headers["content-type"] == "application/json"
    assert response.headers["x-content-type-options"] == "nosniff"
    data = response.json()
    assert data["schema_version"] == "0.1"
    assert data["source_extraction_id"] == str(job.raw_summary.extraction_id)
    assert len(data["rows"]) == 1
    assert len(data["rows"][0]["segments"]) == 3
    assert data["rows"][0]["segments"][2]["left"]["clock_label"] == "15:54"
    assert data["rows"][0]["segments"][2]["right"]["clock_label"] == "16:17"
    path = TemporalStore(store).path(job, 0)
    mtime = path.stat().st_mtime_ns

    def forbidden(*args, **kwargs):
        pytest.fail("cache hit must not redo analysis")

    monkeypatch.setattr("railway_turno_decoder.temporal.storage.analyze_page", forbidden)
    assert client.get(location + "/pages/0/temporal.json").content == response.content
    html = client.get(location + "/pages/0")
    assert html.status_code == 200
    for layer in ["time-axis", "temporal-segments", "endpoint-labels"]:
        assert f'id="layer-{layer}"' in html.text
    assert 'data-segment="0:2"' in html.text
    assert "15:54" in html.text and "16:17" in html.text
    assert path.stat().st_mtime_ns == mtime
    assert not list(path.parent.glob("*.tmp"))
    for name, content in before.items():
        assert (directory / name).read_bytes() == content


@pytest.mark.parametrize("part", ["root", "generation", "settings", "file"])
def test_temporal_cache_symlinks_rejected(client, settings, tmp_path, part):
    location = upload_diagram(client)
    assert client.get(location + "/pages/0/temporal.json").status_code == 200
    store, job, _ = record(settings, location)
    path = TemporalStore(store).path(job, 0)
    selected = {
        "root": path.parent.parent.parent,
        "generation": path.parent.parent,
        "settings": path.parent,
        "file": path,
    }[part]
    target = tmp_path / "external-cache"
    selected.rename(target)
    selected.symlink_to(target, target_is_directory=target.is_dir())
    response = client.get(location + "/pages/0/temporal.json")
    assert response.status_code == 503
    assert str(tmp_path) not in response.text


@pytest.mark.parametrize("kind", ["json", "generation", "page"])
def test_bad_temporal_cache_controlled_and_raw_ui_available(client, settings, kind):
    location = upload_diagram(client)
    assert client.get(location + "/pages/0/temporal.json").status_code == 200
    store, job, _ = record(settings, location)
    path = TemporalStore(store).path(job, 0)
    data = json.loads(path.read_text())
    if kind == "generation":
        data["source_extraction_id"] = str(UUID(int=1))
    if kind == "page":
        data["page_index"] = 99
    path.write_text("PRIVATE_INTERNAL invalid" if kind == "json" else json.dumps(data))
    response = client.get(location + "/pages/0/temporal.json")
    assert response.status_code == 503
    assert "PRIVATE_INTERNAL" not in response.text and "Traceback" not in response.text
    html = client.get(location + "/pages/0")
    assert html.status_code == 200 and "M3A non disponibile" in html.text
    assert "layer-words" in html.text


def test_atomic_failure_preserves_published_temporal_cache(client, settings, monkeypatch):
    location = upload_diagram(client)
    assert client.get(location + "/pages/0/temporal.json").status_code == 200
    store, job, page = record(settings, location)
    cache = TemporalStore(store)
    path = cache.path(job, 0)
    before = path.read_bytes()
    exists = Path.exists
    replace = Path.replace

    # Simulate a concurrent publication after our cache-miss check.
    def missing(candidate):
        return False if candidate == path else exists(candidate)

    def fail(candidate, target):
        if Path(target) == path:
            raise OSError("synthetic full disk")
        return replace(candidate, target)

    monkeypatch.setattr(Path, "exists", missing)
    monkeypatch.setattr(Path, "replace", fail)
    with pytest.raises(StorageError):
        cache.get_or_analyze(job, page)
    assert path.read_bytes() == before
    assert not list(path.parent.glob("*.tmp"))
    assert store.load(job.job_id).status == JobStatus.COMPLETED


@pytest.mark.parametrize("status", [JobStatus.CREATED, JobStatus.PROCESSING, JobStatus.FAILED])
def test_temporal_incomplete_jobs_unavailable(client, settings, status):
    location = upload_diagram(client)
    store, job, _ = record(settings, location)
    job.status = status
    job.errors = ["synthetic"] if status == JobStatus.FAILED else []
    store.save(job)
    assert client.get(location + "/pages/0/temporal.json").status_code == 404


@pytest.mark.parametrize("suffix", ["-1", "1", "999"])
def test_temporal_invalid_index(client, suffix):
    location = upload_diagram(client)
    assert client.get(f"{location}/pages/{suffix}/temporal.json").status_code == 404


def test_temporal_invalid_and_noncanonical_job(client, monkeypatch):
    monkeypatch.setattr(
        "railway_turno_decoder.jobs.uuid4", lambda: UUID("abcdef12-3456-4abc-9def-0123456789ab")
    )
    location = upload_diagram(client)
    assert client.get("/jobs/invalid/pages/0/temporal.json").status_code == 404
    assert (
        client.get(
            location.upper().replace("/JOBS/", "/jobs/") + "/pages/0/temporal.json"
        ).status_code
        == 404
    )


def test_temporal_non_duty_page_rejected(client, pdf_bytes):
    response = client.post(
        "/jobs", files={"file": ("synthetic.pdf", pdf_bytes)}, follow_redirects=False
    )
    assert client.get(response.headers["location"] + "/pages/0/temporal.json").status_code == 404


def test_temporal_budget_is_controlled_without_changing_job(client, settings, monkeypatch):
    location = upload_diagram(client)
    ticks = iter([0.0, 61.0])
    monkeypatch.setattr("railway_turno_decoder.temporal.storage.monotonic", lambda: next(ticks))
    response = client.get(location + "/pages/0/temporal.json")
    assert response.status_code == 422
    store, job, _ = record(settings, location)
    assert job.status == JobStatus.COMPLETED
    assert not TemporalStore(store).path(job, 0).exists()


def test_temporal_style_json_is_escaped_in_inspector(client, settings):
    location = upload_diagram(client)
    store, job, page = record(settings, location)
    generation = store.directory(job.job_id) / "raw_pages" / str(job.raw_summary.extraction_id)
    data = json.loads((generation / "0.json").read_text())
    payload = "</script><script>alert(1)</script>"
    for drawing in data["drawings"]:
        drawing["dashes"] = payload
    (generation / "0.json").write_text(json.dumps(data))
    response = client.get(location + "/pages/0")
    assert response.status_code == 200
    assert payload not in response.text
    assert r"\u003c/script\u003e" in response.text


@pytest.mark.parametrize(
    "kind",
    [
        "extra_row",
        "missing_row",
        "row_index",
        "duplicate_row",
        "word_ref",
        "drawing_ref",
        "primitive_ref",
        "source_row",
        "source_anchors",
        "selected_label",
        "endpoint_source",
        "segment_provenance",
        "hour_ref",
        "negative_ref",
        "axis_anchors",
        "label_bbox",
        "anchor_word_ref",
        "nearby_word_ref",
        "axis_support_ref",
        "segment_drawing_ref",
        "segment_primitive_ref",
    ],
)
def test_incoherent_cache_api_503_and_raw_inspector_200(client, settings, kind):
    location = upload_diagram(client)
    assert client.get(location + "/pages/0/temporal.json").status_code == 200
    store, job, _ = record(settings, location)
    cache = TemporalStore(store).path(job, 0)
    directory = store.directory(job.job_id)
    immutable = {p: p.read_bytes() for p in [directory / "job.json", directory / "raw.json"]}
    data = json.loads(cache.read_text())
    row = data["rows"][0]
    segment = row["segments"][2]
    row["reasons"][0]["code"] = "PRIVATE_CACHE_DETAIL"
    match kind:
        case "extra_row":
            extra = deepcopy(row)
            extra["row_index"] = 1
            data["rows"].append(extra)
        case "missing_row":
            data["rows"] = []
        case "row_index":
            row["row_index"] = 99
        case "duplicate_row":
            data["rows"].append(deepcopy(row))
        case "word_ref":
            row["reasons"][0]["word_indices"] = [999]
        case "drawing_ref":
            row["reasons"][0]["primitive_refs"] = [dict(drawing_index=999, item_index=0)]
        case "primitive_ref":
            row["reasons"][0]["primitive_refs"] = [dict(drawing_index=0, item_index=999)]
        case "source_row":
            row["source_row_bbox"][2] -= 1
        case "source_anchors":
            row["source_anchor_word_indices"] = []
        case "selected_label":
            segment["left"]["nearby_minute_label"]["word_index"] = 999
        case "endpoint_source":
            segment["left"]["display_point"][1] += 1
        case "segment_provenance":
            segment["reasons"][0]["primitive_refs"][0]["drawing_index"] = 0
        case "hour_ref":
            segment["left"]["supporting_hour_word_indices"] = [999]
        case "negative_ref":
            row["reasons"][0]["word_indices"] = [-1]
        case "axis_anchors":
            row["axis"]["anchors"] = []
        case "anchor_word_ref":
            row["axis"]["anchors"][0]["word_index"] = 999
            row["segments"] = []
            row["status"] = "DERIVED"
        case "nearby_word_ref":
            segment["left"]["nearby_labels"][0]["word_index"] = 999
            segment["left"]["nearby_minute_label"]["word_index"] = 999
        case "axis_support_ref":
            row["axis"]["support_refs"][0]["item_index"] = 999
        case "segment_drawing_ref":
            segment["source"]["drawing_index"] = 999
            segment["reasons"][0]["primitive_refs"][0]["drawing_index"] = 999
        case "segment_primitive_ref":
            segment["source"]["item_index"] = 999
            segment["reasons"][0]["primitive_refs"][0]["item_index"] = 999
        case "label_bbox":
            segment["left"]["nearby_labels"][0]["display_bbox"][0] += 1
            segment["left"]["nearby_minute_label"]["display_bbox"][0] += 1
    cache.write_text(json.dumps(data))
    corrupted = cache.read_bytes()
    api = client.get(location + "/pages/0/temporal.json")
    html = client.get(location + "/pages/0")
    assert api.status_code == 503
    assert html.status_code == 200
    assert "M3A non disponibile" in html.text and "layer-words" in html.text
    for response in [api, html]:
        assert all(
            value not in response.text
            for value in [
                "PRIVATE_CACHE_DETAIL",
                "Traceback",
                str(settings.data_dir),
                str(cache),
            ]
        )
    assert cache.read_bytes() == corrupted
    for path, content in immutable.items():
        assert path.read_bytes() == content


def test_legacy_cache_without_page_type_remains_readable(client, settings, monkeypatch):
    location = upload_diagram(client)
    assert client.get(location + "/pages/0/temporal.json").status_code == 200
    store, job, _ = record(settings, location)
    path = TemporalStore(store).path(job, 0)
    data = json.loads(path.read_text())
    del data["page_type"]
    # Legacy cache ordering need not already be canonical to remain valid.
    data["rows"][0]["axis"]["support_refs"].reverse()
    path.write_text(json.dumps(data))
    before = path.read_bytes()

    def forbidden(*args, **kwargs):
        pytest.fail("legacy valid cache must not be recomputed")

    monkeypatch.setattr("railway_turno_decoder.temporal.storage.analyze_page", forbidden)
    assert client.get(location + "/pages/0/temporal.json").status_code == 200
    assert client.get(location + "/pages/0").status_code == 200
    assert path.read_bytes() == before
