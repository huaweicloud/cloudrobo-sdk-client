"""Setuptools build commands used by pyproject.toml."""

from __future__ import annotations

import re
import subprocess
import sys
from pathlib import Path

from setuptools import Command
from setuptools.command.build_py import build_py
from setuptools.command.egg_info import egg_info


def compile_protobuf_files() -> None:
    """Compile protobuf files during package build."""
    project_root = Path(__file__).resolve().parents[2]
    proto_dir = project_root / "src" / "cloudrobo_r2c" / "common" / "protos"
    out_dir = project_root / "src" / "cloudrobo_r2c" / "common" / "models" / "generated"

    out_dir.mkdir(parents=True, exist_ok=True)

    if not proto_dir.exists():
        raise FileNotFoundError(f"Proto directory not found: {proto_dir}")

    proto_files = list(proto_dir.glob("*.proto"))
    if not proto_files:
        print(f"[Build] Warning: no .proto files found in {proto_dir}")
        return

    cmd = [
        sys.executable,
        "-m",
        "grpc_tools.protoc",
        f"-I{proto_dir}",
        f"--python_out={out_dir}",
        *[str(f) for f in proto_files],
    ]

    print(f"[Build] Running: {' '.join(cmd)}")
    try:
        subprocess.check_call(cmd)
        fix_generated_pb2_imports(out_dir)
        print("[Build] Protobuf compilation succeeded.")
    except subprocess.CalledProcessError as exc:
        raise RuntimeError(
            f"Protobuf compilation failed with exit code {exc.returncode}"
        ) from exc


def fix_generated_pb2_imports(generated_dir: Path) -> None:
    """Fix generated absolute pb2 imports to package-relative imports."""
    pattern = re.compile(r"^import\s+(\w+_pb2)\s+as\s+(\w+)$", re.MULTILINE)

    for pb2_file in generated_dir.glob("*_pb2.py"):
        original = pb2_file.read_text(encoding="utf-8")
        fixed = pattern.sub(r"from . import \1 as \2", original)
        if fixed != original:
            pb2_file.write_text(fixed, encoding="utf-8")
            print(f"[Build] Fixed imports in {pb2_file.name}")


class CompileProtobufCommand(Command):
    """Setuptools command to compile protobuf files."""

    description = "Compile protobuf files"
    user_options: list[tuple[str, str | None, str]] = []

    def initialize_options(self) -> None:  # noqa: D401
        pass

    def finalize_options(self) -> None:  # noqa: D401
        pass

    def run(self) -> None:
        compile_protobuf_files()


class BuildPyWithProtobuf(build_py):
    """Run protobuf compilation before build_py."""

    def run(self) -> None:
        compile_protobuf_files()
        super().run()


class EggInfoWithProtobuf(egg_info):
    """Run protobuf compilation before egg_info."""

    def run(self) -> None:
        compile_protobuf_files()
        super().run()
