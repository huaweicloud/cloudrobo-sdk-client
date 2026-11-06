"""Setuptools build commands used by examples/songyi/robot/pyproject.toml.

Build-time protobuf compilation for the songyi-robot adapter package
(same pattern as the official SDK's src/cloudrobo_r2c/_build_commands.py, but
with relaxed dependency version constraints — see pyproject.toml):

- proto/waist_command.proto  ->  models/generated/waist_command_pb2.py

The generated pb2 is NOT checked into git (see .gitignore); it is
(re)generated on every install/editable-install/build, so it always
matches the protobuf runtime in the target environment.
"""

from __future__ import annotations

import re
import subprocess
import sys
from pathlib import Path

from setuptools import Command
from setuptools.command.build_py import build_py
from setuptools.command.egg_info import egg_info

try:
    from setuptools.command.build_editable import build_editable
except ImportError:  # setuptools < 64 (no build_editable command)
    build_editable = build_py  # fallback: editable install reuses build_py


def compile_protobuf_files() -> None:
    """Compile the package-bundled proto tree during package build.

    package_root is the directory containing pyproject.toml, i.e. this
    package root (examples/songyi/robot = the ``songyi_robot`` package).
    """
    package_root = Path(__file__).resolve().parents[0]
    jobs = [
        (
            package_root / "proto",
            package_root / "models" / "generated",
        ),
    ]

    for proto_dir, out_dir in jobs:
        out_dir.mkdir(parents=True, exist_ok=True)
        if not proto_dir.exists():
            raise FileNotFoundError(f"Proto directory not found: {proto_dir}")
        proto_files = list(proto_dir.glob("*.proto"))
        if not proto_files:
            print(f"[Build] Warning: no .proto files found in {proto_dir}")
            continue

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
            print(f"[Build] Protobuf compilation succeeded ({proto_dir.name}).")
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


class BuildEditableWithProtobuf(build_editable):
    """Run protobuf compilation before build_editable (pip install -e)."""

    def run(self) -> None:
        compile_protobuf_files()
        super().run()
