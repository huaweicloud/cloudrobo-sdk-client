import pytest
import yaml
from cloudrobo_ingestion.config import IngestionConfig


@pytest.fixture
def config_file(tmp_path):
    (tmp_path / "source").mkdir()
    path = tmp_path / "ingestion.yaml"
    path.write_text(
        yaml.safe_dump(
            dict(
                source_dir="source",
                work_dir="work",
                obs_path="obs://bucket/prefix/",
                task_type="超市取货",
                collect_method="遥操接管",
            )
        ),
        encoding="utf-8",
    )
    return path


@pytest.fixture
def config(config_file):
    c = IngestionConfig.load(config_file)
    c.work_dir.mkdir()
    return c


@pytest.fixture
def batch_dir(config):
    root = config.source_dir / "批次001"
    (root / "data").mkdir(parents=True)
    (root / "__UPLOAD_READY__").touch()
    (root / "data/file-000.parquet").write_bytes(b"customer-data")
    return root
