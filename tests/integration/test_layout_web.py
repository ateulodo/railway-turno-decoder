"""Synthetic on-demand M3B and storage/fallback regression matrix."""

import json
from concurrent.futures import ThreadPoolExecutor
from copy import deepcopy
from pathlib import Path
from threading import Barrier
from uuid import UUID

import pytest
from layout_fixtures import upload_layout_diagram

from railway_turno_decoder.errors import StorageError
from railway_turno_decoder.jobs import JobStore
from railway_turno_decoder.layout.models import LayoutPage
from railway_turno_decoder.layout.storage import LayoutStore
from railway_turno_decoder.layout.tolerances import LayoutTolerances
from railway_turno_decoder.models import JobStatus
from railway_turno_decoder.temporal.storage import TemporalStore


def record(settings, location):
    store = JobStore(settings.data_dir)
    job = store.load(UUID(location.rsplit("/", 1)[1]))
    raw = store.load_raw_page(job, 0)
    temporal = TemporalStore(store).get_or_analyze(job, raw)
    return store, job, raw, temporal


def test_layout_on_demand_cache_hit_and_immutable_sources(client, settings, monkeypatch):
    location = upload_layout_diagram(client)
    store, job, raw, temporal = record(settings, location)
    directory = store.directory(job.job_id)
    paths = [directory / name for name in ["source.pdf", "raw.json", "job.json"]]
    paths += list((directory / "raw_pages").rglob("*.json"))
    paths += [TemporalStore(store).path(job, 0)]
    before = {p: p.read_bytes() for p in paths}
    cache = LayoutStore(store).path(job, 0, temporal)
    assert not cache.exists()
    response = client.get(location + "/pages/0/layout.json")
    assert response.status_code == 200 and response.headers["content-type"] == "application/json"
    assert response.headers["x-content-type-options"] == "nosniff"
    data = response.json()
    assert data["format"] == "railway-turno-decoder.layout" and data["schema_version"] == "0.1"
    assert len(data["rows"][0]["minute_tokens"]) == 4
    assert len(data["rows"][0]["bands"]) == 4

    def forbidden(*args, **kwargs):
        pytest.fail("cache hit must not repeat layout analysis")

    monkeypatch.setattr("railway_turno_decoder.layout.storage.analyze_layout", forbidden)
    assert client.get(location + "/pages/0/layout.json").content == response.content
    html = client.get(location + "/pages/0")
    assert html.status_code == 200
    for layer in [
        "text-clusters",
        "minute-bands",
        "minute-tokens",
        "explicit-pairs",
        "below-axis",
        "layout-relations",
        "layout-ambiguities",
        "time-axis",
        "words",
    ]:
        assert f'id="layer-{layer}"' in html.text
    selected = client.get(location + "/pages/0/layout.json?row_index=0").json()
    assert selected["row_index"] == 0
    for key in ("source_sha256", "source_extraction_id", "temporal_identity", "tolerances"):
        assert selected[key] == data[key]
    assert all(p.read_bytes() == value for p, value in before.items())
    assert not list(cache.parent.glob("*.tmp"))


@pytest.mark.parametrize(
    "kind",
    [
        "json",
        "generation",
        "sha",
        "m3a",
        "algorithm",
        "previous_algorithm",
        "tolerance",
        "page",
        "rows",
        "duplicate_row",
        "word",
        "text",
        "bbox",
        "minute",
        "band",
        "below",
        "pair",
        "stream",
        "segment",
        "metrics",
        "missing_segment_metric",
        "relation_status",
        "band_phase",
    ],
)
def test_layout_bad_cache_does_not_break_m2_or_m3a(client, settings, kind):
    location = upload_layout_diagram(client)
    assert client.get(location + "/pages/0/layout.json").status_code == 200
    store, job, raw, temporal = record(settings, location)
    path = LayoutStore(store).path(job, 0, temporal)
    data = json.loads(path.read_text())
    row = data["rows"][0]
    row["reasons"][0]["code"] = "PRIVATE_LAYOUT_CACHE"
    match kind:
        case "generation":
            data["source_extraction_id"] = str(UUID(int=9))
        case "sha":
            data["source_sha256"] = "1" * 64
        case "m3a":
            data["temporal_identity"] = "1" * 64
        case "algorithm":
            data["algorithm_version"] = "other"
        case "previous_algorithm":
            data["algorithm_version"] = "0.1.0"
        case "tolerance":
            data["tolerances"]["text_gap_height"] = 0.5
        case "page":
            data["page_index"] = 99
        case "rows":
            extra = deepcopy(row)
            extra["row_index"] = extra["temporal_row_index"] = 1
            data["rows"].append(extra)
        case "duplicate_row":
            data["rows"].append(deepcopy(row))
        case "word":
            row["clusters"][0]["word_indices"][0] = 999
        case "text":
            row["clusters"][0]["fragments"][0] = "PRIVATE_LAYOUT_CACHE"
        case "bbox":
            row["clusters"][0]["display_bbox"][0] += 1
        case "minute":
            row["minute_tokens"][0]["hour_candidates"][0]["absolute_minutes"] += 60
        case "band":
            row["bands"][0]["word_indices"] = []
        case "below":
            row["below_axis"][0]["word_index"] = 0
        case "pair":
            row["explicit_pairs"][0]["end_token_index"] = 999
        case "stream":
            row["stream_blocks"].reverse()
        case "segment":
            row["clusters"][0]["candidate_segment_indices"] = [999]
        case "metrics":
            row["relations"][0]["normalized_distance"] += 1
        case "relation_status":
            row["relations"][0]["status"] = "INVALID"
        case "band_phase":
            data["band_phase_candidates"] = []
        case "missing_segment_metric":
            next(r for r in row["relations"] if r["target"]["kind"] == "SEGMENT")[
                "distance_to_start"
            ] = None
    path.write_text("PRIVATE_LAYOUT_CACHE invalid" if kind == "json" else json.dumps(data))
    before = path.read_bytes()
    api = client.get(location + "/pages/0/layout.json")
    html = client.get(location + "/pages/0")
    assert api.status_code == 503
    assert html.status_code == 200 and "M3B non disponibile" in html.text
    assert "layer-time-axis" in html.text and "layer-words" in html.text
    assert client.get(location + "/pages/0/temporal.json").status_code == 200
    assert client.get(location + "/pages/0/image.png").status_code == 200
    for response in [api, html]:
        assert all(
            token not in response.text
            for token in ["PRIVATE_LAYOUT_CACHE", "Traceback", str(settings.data_dir)]
        )
    assert path.read_bytes() == before


@pytest.mark.parametrize("part", ["layout", "generation", "m3a", "settings", "file"])
def test_layout_symlinks_rejected(client, settings, tmp_path, part):
    location = upload_layout_diagram(client)
    assert client.get(location + "/pages/0/layout.json").status_code == 200
    store, job, _, temporal = record(settings, location)
    path = LayoutStore(store).path(job, 0, temporal)
    selected = {
        "layout": path.parents[3],
        "generation": path.parents[2],
        "m3a": path.parents[1],
        "settings": path.parent,
        "file": path,
    }[part]
    target = tmp_path / "external-layout"
    selected.rename(target)
    selected.symlink_to(target, target_is_directory=target.is_dir())
    assert client.get(location + "/pages/0/layout.json").status_code == 503
    assert client.get(location + "/pages/0").status_code == 200


def test_changed_m3a_or_layout_config_isolates_cache(client, settings):
    location = upload_layout_diagram(client)
    store, job, raw, temporal = record(settings, location)
    first = LayoutStore(store)
    a = first.get_or_analyze(job, raw, temporal)
    old = first.path(job, 0, temporal)
    temporal.algorithm_version = "next-synthetic-version"
    b = first.get_or_analyze(job, raw, temporal)
    assert first.path(job, 0, temporal) != old and a.temporal_identity != b.temporal_identity
    second = LayoutStore(store, LayoutTolerances(text_gap_height=0.5))
    second.get_or_analyze(job, raw, temporal)
    assert second.path(job, 0, temporal) != first.path(job, 0, temporal)
    assert old.exists()


def test_previous_layout_algorithm_cache_is_preserved_in_separate_namespace(
    client, settings, monkeypatch
):
    location = upload_layout_diagram(client)
    store, job, raw, temporal = record(settings, location)
    current = LayoutStore(store)
    path = current.path(job, 0, temporal)
    with monkeypatch.context() as patch:
        patch.setattr("railway_turno_decoder.layout.storage.ALGORITHM_VERSION", "0.1.0")
        old = LayoutStore(store).path(job, 0, temporal)
    old.parent.mkdir(parents=True)
    # The old namespace must not be read or silently replaced, even if corrupt.
    old.write_text("old synthetic cache")
    assert path != old and not path.exists()
    result = current.get_or_analyze(job, raw, temporal)
    assert result.algorithm_version == "0.1.1" and path.exists()
    assert old.read_text() == "old synthetic cache"


def test_atomic_failure_preserves_old_layout_and_cleans_partial(client, settings, monkeypatch):
    location = upload_layout_diagram(client)
    store, job, raw, temporal = record(settings, location)
    cache = LayoutStore(store)
    cache.get_or_analyze(job, raw, temporal)
    path = cache.path(job, 0, temporal)
    before = path.read_bytes()
    exists = Path.exists
    replace = Path.replace

    def missing(p):
        return False if p == path else exists(p)

    def fail(p, target):
        if Path(target) == path:
            raise OSError("synthetic storage failure")
        return replace(p, target)

    monkeypatch.setattr(Path, "exists", missing)
    monkeypatch.setattr(Path, "replace", fail)
    with pytest.raises(StorageError):
        cache.get_or_analyze(job, raw, temporal)
    assert path.read_bytes() == before and not list(path.parent.glob("*.tmp"))
    assert store.load(job.job_id).status == JobStatus.COMPLETED


def test_concurrent_publication_produces_complete_identical_artifact(client, settings, monkeypatch):
    location = upload_layout_diagram(client)
    store, job, raw, temporal = record(settings, location)
    from railway_turno_decoder.layout import storage

    original = storage.analyze_layout
    barrier = Barrier(2)

    def concurrent(*args, **kwargs):
        barrier.wait(timeout=5)
        return original(*args, **kwargs)

    monkeypatch.setattr(storage, "analyze_layout", concurrent)
    with ThreadPoolExecutor(max_workers=2) as executor:
        futures = [
            executor.submit(LayoutStore(store).get_or_analyze, job, raw, temporal) for _ in range(2)
        ]
        a, b = [f.result() for f in futures]
    path = LayoutStore(store).path(job, 0, temporal)
    assert a == b == LayoutPage.model_validate_json(path.read_text())
    assert not list(path.parent.glob("*.tmp"))


@pytest.mark.parametrize(
    "suffix",
    [
        "-1/layout.json",
        "99/layout.json",
        "0/layout.json?row_index=-1",
        "0/layout.json?row_index=99",
    ],
)
def test_invalid_page_and_row(client, suffix):
    location = upload_layout_diagram(client)
    assert client.get(location + "/pages/" + suffix).status_code == 404


def test_invalid_uuid_and_non_duty(client, pdf_bytes, monkeypatch):
    monkeypatch.setattr(
        "railway_turno_decoder.jobs.uuid4", lambda: UUID("abcdef12-3456-4abc-9def-0123456789ab")
    )
    location = upload_layout_diagram(client)
    assert client.get("/jobs/invalid/pages/0/layout.json").status_code == 404
    assert (
        client.get(
            location.upper().replace("/JOBS/", "/jobs/") + "/pages/0/layout.json"
        ).status_code
        == 404
    )
    monkeypatch.setattr("railway_turno_decoder.jobs.uuid4", lambda: UUID(int=2))
    response = client.post(
        "/jobs", files={"file": ("synthetic.pdf", pdf_bytes)}, follow_redirects=False
    )
    assert client.get(response.headers["location"] + "/pages/0/layout.json").status_code == 404


def test_budget_failure_leaves_m3a_available(client, settings, monkeypatch):
    location = upload_layout_diagram(client)
    record(settings, location)
    ticks = iter([0.0, 61.0])
    monkeypatch.setattr("railway_turno_decoder.layout.storage.monotonic", lambda: next(ticks))
    assert client.get(location + "/pages/0/layout.json").status_code == 422
    assert client.get(location + "/pages/0/temporal.json").status_code == 200


def test_layout_text_is_escaped(client):
    payload = "</script><svg/onload=alert(1)>"
    location = upload_layout_diagram(client, extra_text=payload)
    html = client.get(location + "/pages/0")
    assert html.status_code == 200 and payload not in html.text
    assert r"\u003c" in html.text and 'id="layout-data"' in html.text
