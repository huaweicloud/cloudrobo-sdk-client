"""Measure real directory discovery and bounded preparation, without OBS/Zenoh IO."""

import argparse
import json
import os
import platform
import tempfile
import time
from pathlib import Path

from cloudrobo_ingestion.config import IngestionConfig
from cloudrobo_ingestion.daemon import Supervisor
from cloudrobo_ingestion.progress import Reporter
from cloudrobo_ingestion.store import BatchStore


def peak_rss_mib():
    try:
        import resource
    except ImportError:
        return None  # Windows stdlib does not expose resource.getrusage.
    unit = 1024**2 if platform.system() == "Darwin" else 1024
    return round(resource.getrusage(resource.RUSAGE_SELF).ru_maxrss / unit, 2)


def measure(mode, number):
    history = number if mode == "history" else number // 2 if mode == "mixed" else 0
    with tempfile.TemporaryDirectory(prefix="cloudrobo-scan-pressure-") as directory:
        root = Path(directory)
        source, work = root / "source", root / "work"
        source.mkdir()
        work.mkdir()
        config = IngestionConfig.from_mapping(
            dict(
                source_dir=str(source),
                work_dir=str(work),
                obs_path="obs://bucket/pressure/",
                task_type="test",
                collect_method="test",
            ),
            root,
        )
        store = BatchStore(config)
        records = []
        for i in range(number):
            path = source / f"batch-{i:05}"
            path.mkdir()
            if i < history:
                records.append((str(i), path.name, "obs://bucket/pressure/", i))
            else:
                (path / "file.txt").write_bytes(b"sample")
                (path / "__UPLOAD_READY__").touch()
        with store.db:
            store.db.executemany(
                "INSERT INTO batches(id,path,asset_path,timestamp,state,task_type,collect_method) "
                "VALUES(?,?,?,?,'done','test','test')",
                records,
            )
        store.close()
        reporter = Reporter("unused", config.topic)  # Never start the transport thread.
        s = Supervisor(config, reporter, work / "status.json")
        s.running = True
        writes = 0

        def trace(statement):
            nonlocal writes
            if statement.startswith(("INSERT", "UPDATE")):
                writes += 1

        s.store.db.set_trace_callback(trace)
        cpu, started = time.process_time(), time.perf_counter()
        first = None
        drains, statuses = [], []
        max_queue = max_window = max_slots = next_status = 0
        s.scan(wait=False)
        try:
            while True:
                begin = time.perf_counter()
                s.drain()
                drains.append((time.perf_counter() - begin) * 1000)
                elapsed = time.perf_counter() - started
                max_queue = max(max_queue, s.scanner.events.qsize())
                max_window = max(max_window, len(set(s.progress) | set(s.preparing)))
                max_slots = max(max_slots, len(reporter.slots))
                if first is None and s.progress:
                    first = elapsed * 1000
                if elapsed >= next_status:
                    begin = time.perf_counter()
                    s.snapshot()
                    statuses.append((time.perf_counter() - begin) * 1000)
                    next_status = elapsed + 1
                if s.scan_rounds and not s.preparing:
                    state = s.snapshot()
                    if sum(state["counts"].values()) == number:
                        break
                if elapsed > 120:
                    raise TimeoutError("Discovery exceeded benchmark timeout")
                time.sleep(0.05)
            return dict(
                mode=mode,
                directories=number,
                platform=platform.platform(),
                python=platform.python_version(),
                cpus=os.cpu_count(),
                scan_seconds=round(time.perf_counter() - started, 3),
                cpu_seconds=round(time.process_time() - cpu, 3),
                peak_rss_mib=peak_rss_mib(),
                first_manifest_ms=round(first, 2) if first is not None else None,
                drain_p95_ms=round(
                    sorted(drains)[min(len(drains) - 1, int(len(drains) * 0.95))], 2
                ),
                status_max_ms=round(max(statuses), 2),
                database_writes=writes,
                max_result_queue=max_queue,
                max_manifest_window=max_window,
                max_report_slots=max_slots,
                pending_manifest_batches=state["scan"]["pending_manifest_batches"],
            )
        finally:
            s.store.close()


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--mode", choices=("history", "backlog", "mixed"), default="mixed")
    parser.add_argument("--directories", type=int, default=1000)
    args = parser.parse_args()
    if not 1 <= args.directories <= 10000:
        parser.error("directories must be 1..10000")
    print(json.dumps(measure(args.mode, args.directories), indent=2))


if __name__ == "__main__":
    main()
