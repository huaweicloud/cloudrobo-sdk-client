"""Validate shared real-upload workspace ownership."""

import json


def check_mode(config):
    marker = config.work_dir / "mode.json"
    if marker.exists():
        try:
            existing = json.loads(marker.read_text(encoding="utf-8"))["purpose"]
            if existing != "upload":
                raise ValueError("Unknown workspace purpose")
        except (OSError, ValueError, KeyError, TypeError) as exc:
            raise ValueError("工作目录 mode.json 无效或版本不支持，请使用新的工作目录") from exc
    elif (config.work_dir / "batches.sqlite3").exists():
        raise ValueError("工作目录缺少用途标记，请使用新的工作目录")


def bind_mode(config):
    from .lifecycle import write_json

    check_mode(config)
    marker = config.work_dir / "mode.json"
    if not marker.exists():
        write_json(marker, dict(purpose="upload"))
