"""Mode-aware configuration and explicit CLI overrides, without runtime imports."""

from __future__ import annotations

import copy
from dataclasses import dataclass, field
from pathlib import Path
from urllib.parse import urlsplit

import yaml

from .filesystem import DEFAULT_MAX_FILES_PER_BATCH

RUN_MODES = ("upload_and_report", "upload_only")
OVERRIDE_PATHS = {
    "run_mode": ("run_mode",),
    "source_dir": ("source_dir",),
    "work_dir": ("work_dir",),
    "obs_path": ("obs_path",),
    "task_type": ("task_type",),
    "collect_method": ("collect_method",),
    "max_files_per_batch": ("max_files_per_batch",),
    "validation_enabled": ("validation", "enabled"),
    "zenoh_mode": ("zenoh", "mode"),
    "zenoh_project_id": ("zenoh", "project_id"),
    "zenoh_device_id": ("zenoh", "device_id"),
    "zenoh_client_id": ("zenoh", "client_id"),
}


def merge_overrides(data, overrides):
    """Only explicit values override YAML, including False and zero."""
    data = copy.deepcopy(data)
    final_mode = (overrides or {}).get("run_mode")
    if final_mode is None:
        final_mode = data.get("run_mode", "upload_and_report")
    for key, value in (overrides or {}).items():
        if value is None:
            continue
        if key not in OVERRIDE_PATHS and key != "zenoh_endpoint":
            raise ValueError(f"不支持的 CLI 覆盖参数: {key}")
        if final_mode == "upload_only" and key.startswith("zenoh_"):
            continue
        if key == "zenoh_endpoint":
            if not value:
                continue
            z = data.setdefault("zenoh", {})
            if not isinstance(z, dict):
                raise ValueError("zenoh 必须是对象")
            connect = z.setdefault("connect", {})
            if not isinstance(connect, dict):
                raise ValueError("zenoh.connect 必须是对象")
            z["endpoints"] = list(value)
            connect["endpoints"] = list(value)
            continue
        keys = OVERRIDE_PATHS[key]
        target = data
        for part in keys[:-1]:
            target = target.setdefault(part, {})
            if not isinstance(target, dict):
                raise ValueError(f"{part} 必须是对象")
        if key in ("source_dir", "work_dir"):
            value = str(Path(value).expanduser().absolute())
        target[keys[-1]] = value
    return data


@dataclass(frozen=True)
class IngestionConfig:
    source_dir: Path
    work_dir: Path
    obs_path: str
    task_type: str
    collect_method: str
    validation_enabled: bool = False
    zenoh: dict = field(default_factory=dict)
    run_mode: str = "upload_and_report"
    max_files_per_batch: int = DEFAULT_MAX_FILES_PER_BATCH

    @property
    def uploads(self):
        return True

    @property
    def reports(self):
        return self.run_mode != "upload_only"

    @classmethod
    def load(cls, path: str | Path, overrides=None) -> IngestionConfig:
        path = Path(path).resolve()
        data = yaml.safe_load(path.read_text(encoding="utf-8"))
        if not isinstance(data, dict):
            raise ValueError("配置必须是 YAML 对象")
        return cls.from_mapping(merge_overrides(data, overrides), path.parent)

    @classmethod
    def from_mapping(cls, data, base_dir: Path) -> IngestionConfig:
        mode = data.get("run_mode", "upload_and_report")
        if mode not in RUN_MODES:
            raise ValueError(f"run_mode 必须为 {', '.join(RUN_MODES)}")
        reports = mode != "upload_only"
        if "mock" in data:
            raise ValueError(
                "mock 配置已移至独立开发工具 tools/ingestion-mock：python -m cloudrobo_ingestion_mock"
            )
        max_files = data.get("max_files_per_batch", DEFAULT_MAX_FILES_PER_BATCH)
        if type(max_files) is not int or max_files <= 0:
            raise ValueError("max_files_per_batch 必须是正整数")
        for key in ("work_dir", "source_dir", "obs_path"):
            if not isinstance(data.get(key), str) or not data[key]:
                raise ValueError(f"{key} 必须是非空字符串")
        metadata = {}
        for key in ("task_type", "collect_method"):
            if key not in data and not reports:
                metadata[key] = ""  # internal batch metadata, never serialized
                continue
            value = data.get(key)
            if not isinstance(value, str) or not 1 <= len(value) <= 64:
                raise ValueError(f"{key} 必须是长度为 1～64 的字符串")
            metadata[key] = value

        def resolve(value):
            p = Path(value).expanduser()
            p = base_dir / p if not p.is_absolute() else p
            from .filesystem import is_link

            if p.exists() and is_link(p):
                raise ValueError("源目录和工作目录不允许链接或 junction")
            return p.resolve()

        source = resolve(data["source_dir"])
        work = resolve(data["work_dir"])
        if not source.is_dir():
            raise ValueError("source_dir 必须是现有目录")
        if source == work or source in work.parents or work in source.parents:
            raise ValueError("源目录和工作目录不能相同或互相包含")
        obs_path = data["obs_path"]
        if not isinstance(obs_path, str):
            raise ValueError("obs_path 必须是字符串")
        uri = urlsplit(obs_path)
        if (
            uri.scheme != "obs"
            or not uri.netloc
            or uri.query
            or uri.fragment
            or "@" in uri.netloc
            or ":" in uri.netloc
            or any(p in (".", "..") for p in uri.path.split("/"))
        ):
            raise ValueError("obs_path 必须是 obs://bucket/prefix/ 路径")
        validation = data.get("validation", {})
        if not isinstance(validation, dict):
            raise ValueError("validation 必须是对象")
        enabled = validation.get("enabled", False)
        if not isinstance(enabled, bool):
            raise ValueError("validation.enabled 必须是布尔值")
        if validation.get("format", "lerobot_v3") != "lerobot_v3":
            raise ValueError("只支持 validation.format=lerobot_v3")
        if enabled:
            from .validation import require_dependencies

            require_dependencies()
        z = {}
        if reports:
            raw = data.get("zenoh", {})
            if not isinstance(raw, dict):
                raise ValueError("zenoh 必须是对象")
            z = dict(
                mode="client",
                endpoint_role="connect",
                endpoints=["tcp/127.0.0.1:7447"],
                project_id="r2c",
                device_id="local",
                client_id="cloudrobo-ingestion",
            ) | copy.deepcopy(raw)
            for key in ("project_id", "device_id", "client_id"):
                if not isinstance(z[key], str) or not z[key] or any(c in z[key] for c in "/*?#$"):
                    raise ValueError(f"zenoh.{key} 必须是单段身份字符串")
            if z["mode"] not in ("client", "peer") or z["endpoint_role"] != "connect":
                raise ValueError("zenoh mode 无效，endpoint_role 必须为 connect")
            if (
                not isinstance(z["endpoints"], list)
                or not z["endpoints"]
                or any(not isinstance(e, str) or not e for e in z["endpoints"])
            ):
                raise ValueError("zenoh.endpoints 必须是非空字符串列表")
        return cls(
            source,
            work,
            f"obs://{uri.netloc}/{uri.path.strip('/')}".rstrip("/") + "/",
            metadata["task_type"],
            metadata["collect_method"],
            enabled,
            z,
            mode,
            max_files,
        )

    def to_mapping(self):
        """Canonical effective settings for the child; source/work paths are absolute."""
        result = dict(
            run_mode=self.run_mode,
            work_dir=str(self.work_dir),
            obs_path=self.obs_path,
            validation=dict(enabled=self.validation_enabled, format="lerobot_v3"),
        )
        if self.source_dir is not None:
            result["source_dir"] = str(self.source_dir)
        if self.uploads:
            result["max_files_per_batch"] = self.max_files_per_batch
        if self.reports or self.task_type:
            result["task_type"] = self.task_type
        if self.reports or self.collect_method:
            result["collect_method"] = self.collect_method
        if self.reports:
            result["zenoh"] = copy.deepcopy(self.zenoh)
        return result

    @property
    def bucket(self) -> str:
        return urlsplit(self.obs_path).netloc

    @property
    def prefix(self) -> str:
        return urlsplit(self.obs_path).path.lstrip("/")

    @property
    def topic(self) -> str:
        return f"{self.zenoh['project_id']}/{self.zenoh['device_id']}/data_ingestion/upload_status"
