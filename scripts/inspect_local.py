"""Optional local probe. Samples and generated jobs must remain ignored by Git."""

import argparse
import json
from pathlib import Path
from time import perf_counter

from railway_turno_decoder.config import Settings
from railway_turno_decoder.jobs import JobService


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--pdf", type=Path, required=True)
    parser.add_argument("--data-dir", type=Path, default=Path("data/local-validation"))
    args = parser.parse_args()
    if not args.pdf.is_file():
        print("SKIPPED: local PDF is absent.")
        return 0
    service = JobService(Settings(data_dir=args.data_dir))
    start = perf_counter()
    with args.pdf.open("rb") as source:
        job, status = service.process(source, args.pdf.name)
    report = {
        "status": status,
        "job_id": str(job.job_id),
        "elapsed_seconds": round(perf_counter() - start, 3),
        "page_count": job.document.page_count if job.document else None,
        "raw_summary": job.raw_summary.model_dump(mode="json", exclude={"pages"})
        if job.raw_summary
        else None,
        "errors": job.errors,
        "warnings": job.warnings,
    }
    print(json.dumps(report, indent=2, ensure_ascii=False))
    return 0 if status == 200 else 1


if __name__ == "__main__":
    raise SystemExit(main())
