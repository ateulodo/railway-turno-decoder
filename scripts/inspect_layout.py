"""Inspect layout of an existing M2 job; confidential output stays local."""

import argparse
import json
from pathlib import Path
from time import perf_counter
from uuid import UUID

from railway_turno_decoder.jobs import JobStore
from railway_turno_decoder.layout.statistics import summarize_layout
from railway_turno_decoder.layout.storage import LayoutStore
from railway_turno_decoder.temporal.storage import TemporalStore


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--data-dir", type=Path, default=Path("data"))
    parser.add_argument("--job-id", type=UUID, required=True)
    args = parser.parse_args()
    store = JobStore(args.data_dir)
    job = store.load(args.job_id)
    if job.raw_summary is None:
        print("SKIPPED: job has no M2 geometry.")
        return 0
    temporal_store = TemporalStore(store)
    layout_store = LayoutStore(store)
    pages = []
    start = perf_counter()
    for brief in job.raw_summary.pages:
        if brief.page_type != "DUTY_DIAGRAM":
            continue
        page = store.load_raw_page(job, brief.page_index)
        temporal = temporal_store.get_or_analyze(job, page)
        pages.append(layout_store.get_or_analyze(job, page, temporal))
    report = summarize_layout(pages)
    report["elapsed_seconds_including_reads_and_cache"] = perf_counter() - start
    print(json.dumps(report, indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
