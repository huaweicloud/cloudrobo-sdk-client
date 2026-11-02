import subprocess
import sys
from pathlib import Path


def test_release_sync_requires_matching_companion_apis(tmp_path):
    root = Path(__file__).resolve().parents[3]
    (tmp_path / "packages/cloudrobo-ingestion").mkdir(parents=True)
    (tmp_path / "pyproject.toml").write_text(
        'version = "0.1.0"\ndependencies = ["hw-cloudrobo-ingestion==0.1.0"]\n'
    )
    companion = tmp_path / "packages/cloudrobo-ingestion/pyproject.toml"
    companion.write_text((root / "packages/cloudrobo-ingestion/pyproject.toml").read_text())
    subprocess.run(
        [
            sys.executable,
            str(root / ".github/scripts/sync_version.py"),
            "0.2.3",
            "--root",
            str(tmp_path),
        ],
        check=True,
    )
    text = companion.read_text()
    assert "hw-cloudrobo-core>=0.2.3" in text
    assert "hw-cloudrobo-r2c[client]>=0.2.3" in text
    assert "hw-cloudrobo-ingestion==0.2.3" in (tmp_path / "pyproject.toml").read_text()
