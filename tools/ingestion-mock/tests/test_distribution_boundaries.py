"""Build real artifacts so developer code cannot leak into customer releases."""

import importlib.util
import shutil
import subprocess
import sys
import tarfile
import zipfile
from email.parser import Parser
from pathlib import Path

import pytest
import yaml
from packaging.requirements import Requirement

REPO = Path(__file__).resolve().parents[3]
TOOL = REPO / "tools/ingestion-mock"


@pytest.fixture(scope="module")
def distributions(tmp_path_factory):
    work = tmp_path_factory.mktemp("ingestion-distributions")
    result = {}
    for name, source in (
        ("customer", REPO / "packages/cloudrobo-ingestion"),
        ("bundle", REPO),
        ("tool", TOOL),
    ):
        target = work / name
        if name == "bundle":
            target.mkdir()
            for filename in ("pyproject.toml", "README.md", "LICENSE"):
                if (source / filename).exists():
                    shutil.copy2(source / filename, target / filename)
        else:
            shutil.copytree(
                source,
                target,
                ignore=shutil.ignore_patterns(
                    "build", "dist", "*.egg-info", "__pycache__", ".pytest_cache", ".ruff_cache"
                ),
            )
        output = work / f"{name}-dist"
        output.mkdir()
        # Use the installed backend, with no index access or dependency installation.
        # Source copies exclude stale build/lib content from prior development builds.
        proc = subprocess.run(
            [
                sys.executable,
                "-c",
                "import sys; from setuptools.build_meta import build_wheel, build_sdist; "
                "output = sys.argv[1]; build_wheel(output); build_sdist(output)",
                str(output),
            ],
            cwd=target,
            capture_output=True,
            text=True,
            timeout=60,
        )
        assert proc.returncode == 0, proc.stdout + proc.stderr
        result[name] = (next(output.glob("*.whl")), next(output.glob("*.tar.gz")))
    return result


def metadata(wheel):
    with zipfile.ZipFile(wheel) as archive:
        path = next(n for n in archive.namelist() if n.endswith(".dist-info/METADATA"))
        return Parser().parsestr(archive.read(path).decode("utf-8"))


def test_customer_artifacts_exclude_mock_code_and_dependency(distributions):
    for name in ("customer", "bundle"):
        wheel, sdist = distributions[name]
        with zipfile.ZipFile(wheel) as archive:
            assert not any("mock" in n.lower() for n in archive.namelist())
        with tarfile.open(sdist) as archive:
            assert not any("mock" in n.lower() for n in archive.getnames())
        dependencies = [Requirement(r).name for r in metadata(wheel).get_all("Requires-Dist", [])]
        assert "cloudrobo-ingestion-mock" not in dependencies


def test_tool_artifacts_are_independent_and_reuse_sdk(distributions):
    wheel, sdist = distributions["tool"]
    with zipfile.ZipFile(wheel) as archive:
        names = archive.namelist()
        assert "cloudrobo_ingestion_mock/__main__.py" in names
        assert "cloudrobo_ingestion_mock/runtime.py" in names
        assert not any(n.startswith("cloudrobo_ingestion/") for n in names)
        entrypoints = [n for n in names if n.endswith(".dist-info/entry_points.txt")]
        assert not entrypoints  # No customer CLI plugin registration.
    with tarfile.open(sdist) as archive:
        names = archive.getnames()
        assert any(n.endswith("/tests/test_mock_runtime.py") for n in names)
        assert any(n.endswith("/README.md") for n in names)
    dependencies = [Requirement(r).name for r in metadata(wheel).get_all("Requires-Dist", [])]
    assert "hw-cloudrobo-ingestion" in dependencies and "hw-cloudrobo-r2c" in dependencies


def test_old_customer_module_is_not_available():
    assert importlib.util.find_spec("cloudrobo_ingestion.mock") is None
    assert importlib.util.find_spec("cloudrobo_ingestion.mock_runtime") is None
    assert importlib.util.find_spec("cloudrobo_ingestion.mock_scenarios") is None


def test_tool_not_in_release_matrix_or_version_sync(tmp_path):
    workflow = yaml.safe_load(
        (REPO / ".github/workflows/publish-to-pypi.yml").read_text(encoding="utf-8")
    )
    builds = workflow["jobs"]["build"]["strategy"]["matrix"]["include"]
    assert all(not entry["dir"].startswith("tools/") for entry in builds)
    for job in workflow["jobs"].values():
        packages = job.get("strategy", {}).get("matrix", {}).get("package", [])
        assert "cloudrobo-ingestion-mock" not in packages
    tool_project = tmp_path / "tools/ingestion-mock/pyproject.toml"
    tool_project.parent.mkdir(parents=True)
    original = (TOOL / "pyproject.toml").read_bytes()
    tool_project.write_bytes(original)
    (tmp_path / "pyproject.toml").write_text('version = "0.1.0"\n', encoding="utf-8")
    proc = subprocess.run(
        [
            sys.executable,
            str(REPO / ".github/scripts/sync_version.py"),
            "1.2.3",
            "--root",
            str(tmp_path),
        ],
        capture_output=True,
        text=True,
    )
    assert proc.returncode == 0, proc.stderr
    assert tool_project.read_bytes() == original
    assert 'version = "1.2.3"' in (tmp_path / "pyproject.toml").read_text(encoding="utf-8")
