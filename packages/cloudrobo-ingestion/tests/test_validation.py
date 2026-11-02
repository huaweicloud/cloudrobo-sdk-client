import json

import pytest
from cloudrobo_ingestion.filesystem import RejectedBatch
from cloudrobo_ingestion.validation import validate_lerobot


@pytest.fixture
def dataset(tmp_path):
    pa = pytest.importorskip("pyarrow")
    pq = pytest.importorskip("pyarrow.parquet")
    root = tmp_path / "dataset"
    for path in ("meta/episodes/chunk-000", "data/chunk-000"):
        (root / path).mkdir(parents=True)
    features = {
        k: dict(dtype="int64", shape=[1])
        for k in ("index", "frame_index", "episode_index", "task_index")
    }
    features["timestamp"] = dict(dtype="float32", shape=[1])
    info = dict(
        codebase_version="v3.0",
        total_episodes=1,
        total_frames=5,
        total_tasks=1,
        fps=10,
        features=features,
        data_path="data/chunk-{chunk_index:03d}/file-{file_index:03d}.parquet",
        video_path="videos/{video_key}/chunk-{chunk_index:03d}/file-{file_index:03d}.mp4",
    )
    (root / "meta/info.json").write_text(json.dumps(info))
    (root / "meta/stats.json").write_text("{}")
    pq.write_table(pa.table({"task_index": [0], "task": ["pick"]}), root / "meta/tasks.parquet")
    pq.write_table(
        pa.table(
            {
                "index": range(5),
                "frame_index": range(5),
                "episode_index": [0] * 5,
                "task_index": [0] * 5,
                "timestamp": pa.array([i / 10 for i in range(5)], type=pa.float32()),
            }
        ),
        root / "data/chunk-000/file-000.parquet",
    )
    pq.write_table(
        pa.table(
            {
                "episode_index": [0],
                "length": [5],
                "dataset_from_index": [0],
                "dataset_to_index": [5],
                "data/chunk_index": [0],
                "data/file_index": [0],
            }
        ),
        root / "meta/episodes/chunk-000/file-000.parquet",
    )
    return root


def test_valid_dataset(dataset):
    validate_lerobot(dataset)


@pytest.mark.parametrize(
    "file",
    [
        "meta/info.json",
        "meta/stats.json",
        "meta/tasks.parquet",
        "data/chunk-000/file-000.parquet",
        "meta/episodes/chunk-000/file-000.parquet",
    ],
)
def test_missing_metadata_or_data(dataset, file):
    (dataset / file).unlink()
    with pytest.raises(RejectedBatch):
        validate_lerobot(dataset)


def test_incomplete_parquet(dataset):
    (dataset / "data/chunk-000/file-000.parquet").write_bytes(b"PAR1unfinished")
    with pytest.raises(RejectedBatch, match="parquet"):
        validate_lerobot(dataset)


def test_wrong_version(dataset):
    path = dataset / "meta/info.json"
    info = json.loads(path.read_text())
    info["codebase_version"] = "v2.1"
    path.write_text(json.dumps(info))
    with pytest.raises(RejectedBatch, match="v3.0"):
        validate_lerobot(dataset)


@pytest.mark.parametrize(
    "field,value", [("data/file_index", 2), ("dataset_to_index", 6), ("episode_index", 4)]
)
def test_invalid_episode_reference_or_range(dataset, field, value):
    import pyarrow as pa
    import pyarrow.parquet as pq

    path = dataset / "meta/episodes/chunk-000/file-000.parquet"
    row = pq.read_table(path).to_pylist()[0]
    row[field] = value
    pq.write_table(pa.Table.from_pylist([row]), path)
    with pytest.raises(RejectedBatch):
        validate_lerobot(dataset)


def add_video_metadata(dataset):
    import pyarrow as pa
    import pyarrow.parquet as pq

    path = dataset / "meta/info.json"
    info = json.loads(path.read_text())
    info["features"]["observation.images.front"] = dict(dtype="video", shape=[16, 16, 3])
    path.write_text(json.dumps(info))
    path = dataset / "meta/episodes/chunk-000/file-000.parquet"
    row = pq.read_table(path).to_pylist()[0]
    for k, v in {
        "chunk_index": 0,
        "file_index": 0,
        "from_timestamp": 0.0,
        "to_timestamp": 0.5,
    }.items():
        row[f"videos/observation.images.front/{k}"] = v
    pq.write_table(pa.Table.from_pylist([row]), path)
    video = dataset / "videos/observation.images.front/chunk-000/file-000.mp4"
    video.parent.mkdir(parents=True)
    return video


def test_invalid_video(dataset):
    video = add_video_metadata(dataset)
    video.write_bytes(b"not-mp4")
    with pytest.raises(RejectedBatch, match="mp4"):
        validate_lerobot(dataset)


def test_valid_video_container(dataset):
    import av
    import numpy as np

    video = add_video_metadata(dataset)
    with av.open(str(video), "w") as container:
        stream = container.add_stream("mpeg4", rate=10)
        stream.width = stream.height = 16
        stream.pix_fmt = "yuv420p"
        for _ in range(5):
            frame = av.VideoFrame.from_ndarray(
                np.zeros((16, 16, 3), dtype=np.uint8), format="rgb24"
            )
            for packet in stream.encode(frame):
                container.mux(packet)
        for packet in stream.encode():
            container.mux(packet)
    validate_lerobot(dataset)
