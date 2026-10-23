"""Read-only source discovery and checked copies into private staging."""

from __future__ import annotations

import os
import shutil
import stat
from dataclasses import dataclass
from pathlib import Path

MARKER = "__UPLOAD_READY__"
DEFAULT_MAX_FILES_PER_BATCH = 50


class RejectedBatch(ValueError):
    """An immutable batch violates the source contract."""


class InterruptedBatch(Exception):
    pass


def is_link(path: Path) -> bool:
    # Reparse points include Windows junctions; Path.is_junction needs Python 3.12.
    s = path.lstat()
    return stat.S_ISLNK(s.st_mode) or bool(getattr(s, "st_file_attributes", 0) & 0x400)


def _walk(root: Path, stop=None):
    if is_link(root):
        raise RejectedBatch(f"不允许链接或 junction: {root.name}")
    for current, dirs, files in os.walk(
        root, followlinks=False, onerror=lambda e: (_ for _ in ()).throw(e)
    ):
        base = Path(current)
        for name in dirs + files:
            if stop is not None and stop.is_set():
                raise InterruptedBatch()
            p = base / name
            if is_link(p):
                raise RejectedBatch(f"不允许链接或 junction: {p.relative_to(root)}")
        yield base, dirs, files


def discover(root: Path):
    """Only direct children of source_dir can be ready batches."""
    if is_link(root):
        raise RejectedBatch("source_dir 不允许链接或 junction")
    for child in sorted(root.iterdir()):
        if is_link(child) or not child.is_dir():
            continue
        marker = child / MARKER
        if marker.exists() or marker.is_symlink():
            yield child


@dataclass(frozen=True)
class FileEntry:
    path: str
    size: int
    mtime_ns: int
    inode: int


def manifest(
    root: Path, max_files: int = DEFAULT_MAX_FILES_PER_BATCH, *, stop=None
) -> list[FileEntry]:
    marker = root / MARKER
    if is_link(marker) or not marker.is_file() or marker.stat().st_size:
        raise RejectedBatch("__UPLOAD_READY__ 必须是空的普通文件")
    entries = []
    for base, _, files in _walk(root, stop):
        for name in sorted(files):
            if stop is not None and stop.is_set():
                raise InterruptedBatch()
            if name == MARKER:
                continue
            p = base / name
            s = p.stat()
            if not stat.S_ISREG(s.st_mode):
                raise RejectedBatch(f"不是普通文件: {p.relative_to(root)}")
            entries.append(
                FileEntry(p.relative_to(root).as_posix(), s.st_size, s.st_mtime_ns, s.st_ino)
            )
            if len(entries) > max_files:
                raise RejectedBatch(f"批次文件数超过配置上限 {max_files}")
    return sorted(entries, key=lambda x: x.path)


def checked_copy(
    source: Path,
    target: Path,
    entries: list[FileEntry],
    stop,
    max_files: int = DEFAULT_MAX_FILES_PER_BATCH,
) -> None:
    target.mkdir(parents=True, exist_ok=False)
    for entry in entries:
        if stop.is_set():
            raise InterruptedBatch()
        src, dest = source / entry.path, target / entry.path
        if is_link(src):
            raise RejectedBatch(f"复制时发现链接: {entry.path}")
        dest.parent.mkdir(parents=True, exist_ok=True)
        # Opening through O_NOFOLLOW prevents replacing the final source file with a link.
        fd = os.open(src, os.O_RDONLY | getattr(os, "O_NOFOLLOW", 0))
        with os.fdopen(fd, "rb") as f, dest.open("xb") as out:
            s = os.fstat(f.fileno())
            if (s.st_size, s.st_mtime_ns, s.st_ino) != (entry.size, entry.mtime_ns, entry.inode):
                raise RejectedBatch(f"源文件已变化: {entry.path}")
            while True:
                if stop.is_set():
                    raise InterruptedBatch()
                block = f.read(1024 * 1024)
                if not block:
                    break
                out.write(block)
            after = os.fstat(f.fileno())
            if (after.st_size, after.st_mtime_ns) != (entry.size, entry.mtime_ns):
                raise RejectedBatch(f"复制期间源文件变化: {entry.path}")
    if manifest(source, max_files) != entries:
        raise RejectedBatch("复制期间源目录内容变化")


def remove_staging(path: Path) -> None:
    if not path.exists() and not path.is_symlink():
        return
    if is_link(path):
        raise RejectedBatch("工作副本不允许链接")
    shutil.rmtree(path)
