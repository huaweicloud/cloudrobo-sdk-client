"""Reproducible serialization measurement (not an OBS throughput benchmark).

Run: python packages/cloudrobo-ingestion/benchmarks/serialize.py --files 5000
"""

import argparse
import json
import platform
import statistics
import time
import uuid

from cloudrobo_ingestion.filesystem import FileEntry
from cloudrobo_ingestion.progress import BatchProgress


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--files", type=int, default=5000)
    parser.add_argument("--iterations", type=int, default=100)
    args = parser.parse_args()
    if not 1 <= args.files <= 5000 or args.iterations < 2:
        parser.error("files must be 1..5000; iterations must be >=2")
    batch = dict(
        id=str(uuid.uuid4()),
        timestamp=time.time_ns() // 1_000_000,
        asset_path="obs://benchmark/data/batch/",
        task_type="超市取货",
        collect_method="遥操接管",
    )
    progress = BatchProgress(
        batch,
        [FileEntry(f"data/file-{i:05}.parquet", 100000, 0, 0) for i in range(args.files)],
        "r2c/local",
    )
    payload = progress.serialize()  # warm imports outside the measured samples
    samples = []
    for _ in range(args.iterations):
        start = time.perf_counter()
        progress.serialize()
        samples.append(1000 * (time.perf_counter() - start))
    print(
        json.dumps(
            dict(
                platform=platform.platform(),
                python=platform.python_version(),
                files=args.files,
                payload_bytes=len(payload),
                iterations=args.iterations,
                median_ms=statistics.median(samples),
                p95_ms=sorted(samples)[int(0.95 * len(samples)) - 1],
            ),
            indent=2,
        )
    )


if __name__ == "__main__":
    main()
