"""Foreground scenario runner. No customer files, OBS, database or daemon state."""

import hashlib
import json
import queue
import threading
import time
import uuid

from cloudrobo_ingestion.store import now_ms

from .scenarios import Packet, Phase, Scenarios


class MockRunner:
    def __init__(
        self,
        reporter,
        source,
        *,
        scenario="overview",
        file_count=100,
        time_scale=1,
        repeat=1,
        emit=None,
        clock=None,
        wait=None,
        publish_timeout=30,
    ):
        if type(repeat) is not int or repeat < 0:
            raise ValueError("repeat 必须为非负整数")
        self.reporter, self.scenario, self.repeat = reporter, scenario, repeat
        self.clock = clock or time.monotonic
        self.stop_event = threading.Event()
        self.wait = wait or self.stop_event.wait
        self.emit = emit or (
            lambda event: print(json.dumps(event, ensure_ascii=True, allow_nan=False), flush=True)
        )
        self.scenarios = Scenarios(source, self.clock, file_count, time_scale)
        self.publish_timeout = publish_timeout
        self.run_id = str(uuid.uuid4())
        self.sequence = self.sent = self.total_bytes = 0
        self.iteration = 0
        self.total_build_ms = self.total_publish_ms = 0.0
        self.started = self.clock()

    def event(self, kind, **fields):
        self.emit(
            dict(
                event=kind,
                run_id=self.run_id,
                scenario=self.scenario,
                timestamp_ms=now_ms(),
                elapsed_seconds=self.clock() - self.started,
                iteration=self.iteration,
                case=getattr(self.scenarios, "current", None),
                **fields,
            )
        )

    def check_stop(self):
        if self.stop_event.is_set():
            raise KeyboardInterrupt

    def send(self, packet, build_ms=0.0):
        self.check_stop()
        self.sequence += 1
        key = f"{self.run_id}/{self.sequence}"
        fields = dict(
            sequence=self.sequence,
            step=packet.label,
            batch_id=packet.batch_id,
            size_bytes=len(packet.payload),
            sha256=hashlib.sha256(packet.payload).hexdigest(),
            expected=packet.expected,
            build_ms=build_ms,
        )
        self.event("send", **fields)
        started = self.clock()
        self.reporter.set(key, packet.payload, terminal=True)
        while True:
            self.check_stop()
            try:
                batch_id, payload = self.reporter.acks.get_nowait()
            except queue.Empty:
                if self.clock() - started >= self.publish_timeout:
                    error = self.reporter.diagnostics().get("error", "")
                    self.event("publish_timeout", **fields, error=error)
                    raise TimeoutError(
                        f"进度上报超时：连续 {self.publish_timeout:g} 秒未完成本地发布；{error}"
                    )
                self.wait(0.02)
                continue
            if batch_id == key and payload == packet.payload:
                break
        duration_ms = (self.clock() - started) * 1000
        self.sent += 1
        self.total_bytes += len(packet.payload)
        self.total_build_ms += build_ms
        self.total_publish_ms += duration_ms
        self.event("local_published", **fields, publish_ms=duration_ms)

    def phase(self, step):
        self.event(
            "step",
            step=step.label,
            expected=step.expected,
            duration_seconds=step.duration,
            batch_ids=[p.batch["id"] for p in step.batches],
        )
        started = self.clock()
        while True:
            self.check_stop()
            elapsed = min(step.duration, self.clock() - started)
            if step.duration - elapsed < 1e-9:
                elapsed = step.duration
            before = self.clock()
            if step.sample:
                step.sample(elapsed)
            for p in step.batches:
                payload = p.serialize()
                self.send(
                    Packet(step.label, p.batch["id"], payload, step.expected),
                    build_ms=(self.clock() - before) * 1000,
                )
                before = self.clock()
            if elapsed >= step.duration:
                return
            self.wait(
                max(0, min(1.0, step.duration - elapsed) - (self.clock() - started - elapsed))
            )

    def run(self):
        self.event(
            "run_start",
            source=self.scenarios.source,
            topic=self.reporter.topic,
            repeat=self.repeat,
            file_count=self.scenarios.file_count,
            time_scale=self.scenarios.time_scale,
        )
        started = False
        try:
            self.reporter.start()
            started = True
            iteration = 0
            while self.repeat == 0 or iteration < self.repeat:
                self.check_stop()
                iteration += 1
                self.iteration = iteration
                self.event("iteration_start")
                steps = iter(self.scenarios.steps(self.scenario))
                while True:
                    self.check_stop()
                    before = self.clock()
                    try:
                        step = next(steps)
                    except StopIteration:
                        break
                    build_ms = (self.clock() - before) * 1000
                    if isinstance(step, Phase):
                        self.phase(step)
                    else:
                        self.send(step, build_ms)
                self.event("iteration_complete")
            self.event(
                "run_complete",
                messages=self.sent,
                bytes=self.total_bytes,
                mean_build_ms=self.total_build_ms / self.sent if self.sent else 0,
                mean_publish_ms=self.total_publish_ms / self.sent if self.sent else 0,
            )
        except KeyboardInterrupt:
            self.event("interrupted", messages=self.sent)
            raise
        except Exception as exc:
            self.event("run_failed", error=str(exc), messages=self.sent)
            raise
        finally:
            if started:
                self.reporter.close()
