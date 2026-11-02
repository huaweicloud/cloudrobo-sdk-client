"""Read-only structural checks for the standard LeRobot v3.0 disk format.

This is not a training-quality check or a full media decode. No optional
library is imported when validation is disabled.
"""

from __future__ import annotations

import json
import math
from pathlib import Path

from .filesystem import RejectedBatch


def require_dependencies():
    try:
        import av
        import pyarrow.parquet as pq
    except ImportError as exc:
        raise ValueError(
            "LeRobot 校验需要 pip install 'hw-cloudrobo-ingestion[lerobot-validation]'"
        ) from exc
    return pq, av


def validate_lerobot(root: Path, stop=None):
    pq, av = require_dependencies()
    import pyarrow as pa

    def check_stop():
        if stop is not None and stop.is_set():
            from .filesystem import InterruptedBatch

            raise InterruptedBatch()

    def fail(message):
        raise RejectedBatch(f"LeRobot v3 校验失败: {message}")

    def json_file(relative):
        try:
            obj = json.loads((root / relative).read_text(encoding="utf-8"))
            if not isinstance(obj, dict):
                fail(f"{relative} 必须是对象")
            return obj
        except (OSError, ValueError) as exc:
            fail(f"{relative}: {exc}")

    def local_path(template, **values):
        if not isinstance(template, str):
            fail("路径模板必须是字符串")
        try:
            rel = Path(template.format(**values))
        except (ValueError, KeyError, TypeError) as exc:
            fail(f"路径模板无效: {exc}")
        if (
            rel.is_absolute()
            or ".." in rel.parts
            or (root / rel).resolve().is_relative_to(root.resolve()) is False
        ):
            fail("元数据路径不能越出数据集")
        return root / rel

    def parquet(path):
        check_stop()
        try:
            return pq.ParquetFile(path)
        except Exception as exc:
            fail(f"{path.relative_to(root)}: {exc}")

    info = json_file("meta/info.json")
    if info.get("codebase_version") != "v3.0":
        fail("codebase_version 必须为 v3.0（仅支持标准 Parquet/MP4 后端）")
    for key in ("total_episodes", "total_frames", "total_tasks"):
        if type(info.get(key)) is not int or info[key] < 1:
            fail(f"info.json.{key} 必须是正整数")
    fps = info.get("fps")
    if (
        not isinstance(fps, (int, float))
        or isinstance(fps, bool)
        or not math.isfinite(fps)
        or fps <= 0
    ):
        fail("fps 必须为正数")
    features = info.get("features")
    if not isinstance(features, dict) or not features:
        fail("features 必须是非空对象")
    required = {"index", "frame_index", "episode_index", "task_index", "timestamp"}
    if not required <= features.keys():
        fail("features 缺少 index/frame_index/episode_index/task_index/timestamp")
    for name, ft in features.items():
        if (
            not isinstance(ft, dict)
            or not isinstance(ft.get("dtype"), str)
            or not isinstance(ft.get("shape"), list)
        ):
            fail(f"features.{name} 缺少 dtype/shape")
        if not all(type(n) is int and n > 0 for n in ft["shape"]):
            fail(f"features.{name}.shape 无效")
        if ft["dtype"] == "image":
            fail("第一版校验仅支持 MP4 视频或无视频数据，不支持内嵌 image 特征")
    json_file("meta/stats.json")
    tasks = parquet(root / "meta/tasks.parquet")
    if (
        tasks.metadata.num_rows != info["total_tasks"]
        or "task_index" not in tasks.schema_arrow.names
    ):
        fail("tasks.parquet 行数或 task_index 无效")
    task_ids = tasks.read(columns=["task_index"]).column(0).to_pylist()
    if len(set(task_ids)) != len(task_ids) or any(type(i) is not int or i < 0 for i in task_ids):
        fail("tasks.parquet task_index 必须唯一且非负")
    data_paths = sorted((root / "data").rglob("*.parquet"))
    if not data_paths:
        fail("缺少 data Parquet 文件")
    data_files = {}
    expected = {k for k, v in features.items() if v["dtype"] != "video"}
    total_rows = 0
    for path in data_paths:
        pf = parquet(path)
        if not expected <= set(pf.schema_arrow.names):
            fail(f"{path.relative_to(root)} 缺少声明的特征列")
        for key in required - {"timestamp"}:
            if str(pf.schema_arrow.field(key).type) != "int64":
                fail(f"{path.relative_to(root)}.{key} 必须为 int64")
        if str(pf.schema_arrow.field("timestamp").type) not in ("float", "double"):
            fail(f"{path.relative_to(root)}.timestamp 必须是浮点数")
        for key in expected:
            dtype = features[key]["dtype"]
            actual = pf.schema_arrow.field(key).type
            while (
                pa.types.is_list(actual)
                or pa.types.is_large_list(actual)
                or pa.types.is_fixed_size_list(actual)
            ):
                actual = actual.value_type
            try:
                expected_type = pa.string() if dtype == "string" else pa.from_numpy_dtype(dtype)
            except (TypeError, ValueError, NotImplementedError):
                fail(f"features.{key}.dtype 不支持: {dtype}")
            if actual != expected_type and not (
                dtype == "string" and pa.types.is_large_string(actual)
            ):
                fail(f"{path.relative_to(root)}.{key} 类型不符合 features 声明")
        data_files[path.resolve()] = pf
        total_rows += pf.metadata.num_rows
    if total_rows != info["total_frames"]:
        fail("total_frames 与数据文件行数不一致")
    episode_paths = sorted((root / "meta/episodes").rglob("*.parquet"))
    if not episode_paths:
        fail("缺少 meta/episodes Parquet 文件")
    videos = {k for k, v in features.items() if v["dtype"] == "video"}
    seen, lengths, video_checked = set(), 0, {}
    intervals, shard_bounds = {}, {}
    columns = {
        "episode_index",
        "length",
        "dataset_from_index",
        "dataset_to_index",
        "data/chunk_index",
        "data/file_index",
    }
    for key in videos:
        columns |= {
            f"videos/{key}/{suffix}"
            for suffix in ("chunk_index", "file_index", "from_timestamp", "to_timestamp")
        }
    for path in episode_paths:
        pf = parquet(path)
        if not columns <= set(pf.schema_arrow.names):
            fail(f"{path.relative_to(root)} 缺少 episode 引用列")
        for block in pf.iter_batches(batch_size=512, columns=sorted(columns)):
            check_stop()
            for row in block.to_pylist():
                idx, length, start, end = (
                    row[k]
                    for k in ("episode_index", "length", "dataset_from_index", "dataset_to_index")
                )
                if (
                    any(type(n) is not int for n in (idx, length, start, end))
                    or idx in seen
                    or idx < 0
                    or length <= 0
                    or start < 0
                    or end - start != length
                    or end > total_rows
                ):
                    fail("episode 索引、长度或数据范围无效")
                seen.add(idx)
                lengths += length
                if start in intervals:
                    fail("episode 数据范围重叠")
                intervals[start] = end
                p = local_path(
                    info.get("data_path"),
                    chunk_index=row["data/chunk_index"],
                    file_index=row["data/file_index"],
                )
                data = data_files.get(p.resolve())
                if data is None:
                    fail(f"引用的数据文件不存在: {p.relative_to(root)}")
                # Read only bookkeeping columns for the referenced shard.
                # Cache episode bounds rather than materializing sensor arrays.
                if p.resolve() not in shard_bounds:
                    bounds = {}
                    for b in data.iter_batches(
                        columns=["index", "episode_index"], batch_size=65536
                    ):
                        check_stop()
                        for frame in b.to_pylist():
                            e, i = frame["episode_index"], frame["index"]
                            if type(e) is not int or type(i) is not int:
                                fail("数据文件包含无效 frame 索引")
                            lo, hi, count = bounds.get(e, (i, i - 1, 0))
                            if i != hi + 1:
                                fail("episode 在数据文件中的 index 不连续")
                            bounds[e] = (lo, i, count + 1)
                    shard_bounds[p.resolve()] = bounds
                if shard_bounds[p.resolve()].get(idx) != (start, end - 1, length):
                    fail(f"episode {idx} 与引用文件范围不一致")
                for key in videos:
                    prefix = f"videos/{key}/"
                    p = local_path(
                        info.get("video_path"),
                        video_key=key,
                        chunk_index=row[prefix + "chunk_index"],
                        file_index=row[prefix + "file_index"],
                    )
                    a, b = row[prefix + "from_timestamp"], row[prefix + "to_timestamp"]
                    if (
                        any(not isinstance(n, (float, int)) or not math.isfinite(n) for n in (a, b))
                        or a < 0
                        or b <= a
                    ):
                        fail(f"episode {idx} 视频时间范围无效")
                    if p not in video_checked:
                        try:
                            with av.open(str(p)) as container:
                                if not container.streams.video:
                                    fail(f"{p.relative_to(root)} 缺少视频流")
                                stream = container.streams.video[0]
                                video_checked[p] = (
                                    float(stream.duration * stream.time_base)
                                    if stream.duration is not None
                                    else None
                                )
                        except Exception as exc:
                            fail(f"{p.relative_to(root)}: {exc}")
                    duration = video_checked[p]
                    if duration is not None and b > duration + 1 / fps:
                        fail(f"episode {idx} 超出视频时长")
    if seen != set(range(info["total_episodes"])) or lengths != total_rows:
        fail("total_episodes/total_frames 与 episode 元数据不一致")
    cursor = 0
    for start, end in sorted(intervals.items()):
        if start != cursor:
            fail("episode 数据范围存在重叠或空洞")
        cursor = end
