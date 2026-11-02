"""Deterministic developer scenarios, with fresh identities on every invocation."""

import copy
import math
import uuid
from dataclasses import dataclass
from typing import Callable

from cloudrobo_ingestion.filesystem import FileEntry
from cloudrobo_ingestion.progress import BatchProgress, message_class
from cloudrobo_ingestion.store import now_ms

FUNCTIONAL = ("overview", "retry", "persistent-failure", "variable-speed")
SCENARIOS = (
    *FUNCTIONAL,
    "stress",
    "duplicate",
    "out-of-order",
    "invalid-fields",
    "malformed",
    "functional-suite",
)
MIB = 1024 * 1024


@dataclass
class Phase:
    label: str
    batches: tuple
    duration: float
    expected: str
    sample: Callable | None = None


@dataclass
class Packet:
    label: str
    batch_id: str
    payload: bytes
    expected: str


class Scenarios:
    def __init__(self, source, clock, file_count=100, time_scale=1):
        if type(file_count) is not int or not 1 <= file_count <= 5000:
            raise ValueError("file_count 必须为 1～5000 的整数")
        if type(time_scale) not in (int, float) or not math.isfinite(time_scale) or time_scale <= 0:
            raise ValueError("time_scale 必须为有限正数")
        self.source, self.clock = source, clock
        self.file_count, self.time_scale = file_count, time_scale
        self.last_timestamp = 0

    def batch(self, entries=None, classification=("超市取货", "遥操接管")):
        batch_id = str(uuid.uuid4())
        self.last_timestamp = max(now_ms(), self.last_timestamp + 1)
        batch = dict(
            id=batch_id,
            timestamp=self.last_timestamp,
            task_type=classification[0],
            collect_method=classification[1],
            asset_path=f"obs://mock-bucket/ingestion/{batch_id}/",
        )
        if entries is None:
            entries = [
                ("相机/片段.mp4", 8 * MIB),
                ("备用相机/片段.mp4", 2 * MIB),
                ("images/图像.jpg", MIB // 2),
                ("points/scene.pcd", MIB),
                ("meta/info.json", 1024),
                ("data/file.parquet", 4 * MIB),
                ("meta/empty.txt", 0),
            ]
        return BatchProgress(
            batch,
            [FileEntry(path, size, 0, 0) for path, size in entries],
            self.source,
            clock=self.clock,
        )

    def duration(self, seconds):
        duration = max(1.0, seconds / self.time_scale)
        if not math.isfinite(duration):
            raise ValueError("time_scale 产生了非有限的场景时长")
        return duration

    def upload(self, p, others=()):
        for path, entry in p.entries.items():
            p.update(path, 1, 0, begin=True)
            duration = self.duration(4)

            def sample(elapsed, path=path, size=entry.size, duration=duration):
                p.update(path, 1, int(size * min(1, elapsed / duration)))

            yield Phase(
                "uploading", (p, *others), duration, "当前文件上传中，其他文件保持已有状态", sample
            )
            p.update(path, 2, entry.size)
            yield Phase("file-done", (p, *others), 0, "当前文件完成，批次尚未全部成功")

    @staticmethod
    def finish(p):
        with p.lock:
            p.finished = True
        return Phase("finish", (p,), 0, "全批成功，所有节点 done/100%，task_state=1")

    def overview(self):
        a = self.batch()
        b = self.batch(classification=("仓库巡检", "自主采集"))
        c = self.batch(classification=("物料搬运", "遥操接管"))
        yield Phase(
            "queued", (a, b, c), 3, "三批次排队；五类数据、不同大小、中文同名文件及分类聚合"
        )
        for p, others in ((a, (b, c)), (b, (c,)), (c, ())):
            yield from self.upload(p, others)
            yield self.finish(p)

    def retry(self):
        p = self.batch([(f"data/{i}.parquet", MIB) for i in range(3)])
        yield Phase("queued", (p,), 3, "新批次排队")
        p.update("data/0.parquet", 2, MIB)
        p.update("data/1.parquet", 1, MIB // 3, begin=True)
        yield Phase("partial-upload", (p,), self.duration(4), "一个完成、一个上传中、一个排队")
        p.mark_failed()
        yield Phase("failed", (p,), 3, "节点状态 [done, failed, failed]；task_state=0")
        yield from self.upload(p)
        yield self.finish(p)

    def persistent_failure(self):
        p = self.batch([("data/first.parquet", MIB), ("data/second.parquet", MIB)])
        yield Phase("queued", (p,), 3, "新批次排队")
        for attempt in range(2):
            p.update("data/first.parquet", 1, 0, begin=True)
            yield Phase(
                f"attempt-{attempt + 1}", (p,), self.duration(3), "整批重试，身份和入队时间不变"
            )
            p.mark_failed()
            yield Phase("failed", (p,), 3, "持续失败及退避；两次尝试后保留 failed，task_state=0")

    def variable_speed(self):
        p = self.batch([("video/速率测试.mp4", 64 * MIB)])
        path = next(iter(p.nodes))
        yield Phase("queued", (p,), 3, "排队状态至少三个上报周期")
        p.update(path, 1, 0, begin=True)
        previous = 0
        for label, end, duration in (
            ("slow", 4 * MIB, self.duration(4)),
            ("fast", 20 * MIB, self.duration(4)),
            ("stalled", 20 * MIB, 6),
            ("resumed", 64 * MIB, self.duration(6)),
        ):

            def sample(elapsed, start=previous, end=end, duration=duration):
                p.update(path, 1, int(start + (end - start) * min(1, elapsed / duration)))

            yield Phase(
                label,
                (p,),
                duration,
                "按实际时间计算滑动窗口速率和 ETA；停滞后速率/ETA 归零",
                sample,
            )
            previous = end
        p.update(path, 2, 64 * MIB)
        yield self.finish(p)

    def stress(self):
        p = self.batch([(f"data/file-{i:05d}.parquet", MIB) for i in range(self.file_count)])
        yield Phase("queued", (p,), 3, "大批量完整节点排队")
        paths = list(p.nodes)

        def sample(elapsed):
            amount = min(self.file_count * MIB, int(self.file_count * MIB * elapsed / 30))
            for index, path in enumerate(paths):
                transferred = max(0, min(MIB, amount - index * MIB))
                node = p.nodes[path]
                if transferred == MIB:
                    p.update(path, 2, MIB)
                elif transferred > 0 or index == 0:
                    p.update(path, 1, transferred, begin=node.state == 0)

        yield Phase(
            "stress-30s", (p,), 30, f"{self.file_count} 个节点，每秒完整快照，持续 30 秒", sample
        )
        yield self.finish(p)

    def packet(self, label, p, snapshot=None, expected=""):
        payload = (
            p.serialize() if snapshot is None else message_class()(**snapshot).SerializeToString()
        )
        return Packet(label, p.batch["id"], payload, expected)

    def duplicate(self):
        p = self.batch([("duplicate.mp4", MIB)])
        queued = self.packet("queued", p, expected="首次排队")
        yield queued
        yield Packet(
            "duplicate-queued",
            queued.batch_id,
            queued.payload,
            "与上一条字节完全相同，检查去重和统计",
        )
        p.update("duplicate.mp4", 2, MIB)
        p.finished = True
        done = self.packet("finish", p, expected="首次成功")
        yield done
        yield Packet("duplicate-finish", done.batch_id, done.payload, "重复最终消息，不应重复计数")

    def out_of_order(self):
        p = self.batch([("out-of-order.mp4", MIB)])
        p.update("out-of-order.mp4", 1, MIB // 10, begin=True)
        old = self.packet("progress-10", p, expected="旧快照约 10%")
        yield old
        p.update("out-of-order.mp4", 1, MIB * 8 // 10)
        new = self.packet("progress-80", p, expected="新快照约 80%")
        yield new
        yield Packet(
            "late-progress-10", old.batch_id, old.payload, "同 UUID 旧进度晚到，观察云端处理策略"
        )
        p.update("out-of-order.mp4", 2, MIB)
        p.finished = True
        done = self.packet("finish", p, expected="成功快照")
        yield done
        yield Packet(
            "late-progress-after-finish", new.batch_id, new.payload, "成功后旧上传状态晚到"
        )
        yield Packet("corrected-finish", done.batch_id, done.payload, "正常最终对照消息")

    def invalid_fields(self):
        samples = [("source", ""), ("timestamp", -1), ("asset_path", ""), ("task_state", 99)]
        samples += [
            (f"nodes.{field}", value)
            for field, value in (
                ("file_id", ""),
                ("task_name", ""),
                ("data_type", 99),
                ("size", -1),
                ("enqueue_time", -1),
                ("start_time", -1),
                ("progress", -1),
                ("progress", 101),
                ("progress", float("nan")),
                ("progress", float("inf")),
                ("result_state", 99),
                ("eta_ms", -1),
                ("avg_bps", -1),
                ("avg_bps", float("nan")),
                ("avg_bps", float("inf")),
                ("task_type", ""),
                ("task_type", "测" * 65),
                ("collect_method", ""),
                ("collect_method", "测" * 65),
            )
        ]
        samples.append(("inconsistent-success", None))
        for index, (field, value) in enumerate(samples):
            p = self.batch([("validation.mp4", MIB)])
            snapshot = copy.deepcopy(p.snapshot())
            if field.startswith("nodes."):
                snapshot["nodes"][0][field.split(".")[1]] = value
            elif field == "inconsistent-success":
                snapshot["task_state"] = 1
                snapshot["nodes"][0]["result_state"] = 3
            else:
                snapshot[field] = value
            yield self.packet(
                f"invalid-{index + 1}-{field}",
                p,
                snapshot,
                "可解析的异常字段或矛盾状态，检查云端校验",
            )
            p.update("validation.mp4", 2, MIB)
            p.finished = True
            yield self.packet("normal-control", p, expected="同一任务的正常成功消息，验证后续处理")

    def malformed(self):
        from google.protobuf.message import DecodeError

        for label, payload in (
            ("truncated-protobuf", b"\x1a\xff"),
            ("json-instead-of-protobuf", b'{"invalid":true}'),
        ):
            try:
                message_class().FromString(payload)
            except DecodeError:
                pass
            else:
                raise ValueError(f"异常样本 {label} 意外可解析")
            p = self.batch([("control.mp4", MIB)])
            yield Packet(label, p.batch["id"], payload, "不可解析消息，检查接收端异常隔离")
            p.update("control.mp4", 2, MIB)
            p.finished = True
            yield self.packet("normal-control", p, expected="损坏消息后仍能接收正常消息")

    def steps(self, name):
        if name not in SCENARIOS:
            raise ValueError(f"未知场景：{name}")
        names = FUNCTIONAL if name == "functional-suite" else (name,)
        for scenario in names:
            self.current = scenario
            yield from getattr(self, scenario.replace("-", "_"))()
