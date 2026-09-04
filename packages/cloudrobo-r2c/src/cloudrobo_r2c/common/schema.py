"""机器人配置 Schema 导出接口 — 提取 `device_to_r2c` / `r2c_to_device` 映射规则的描述。

本模块提供一套只读、纯函数的编程接口,将 ``device_to_r2c`` 和
``r2c_to_device`` 下的 ``mappings`` 列表解析为结构化的数据模型
(:class:`RobotConfigSchema`),使上游工具(Agent Skill、文档生成器、
Dashboard、CI 校验)能够以结构化方式理解"这个机器人产生什么观测数据、
接收什么动作指令"。

与运行时管道的关系::

    YAML mappings → MapperRule → ConfigDrivenMapper
        → ConfigurableDeviceTranslator
                        │
                        └──► RobotConfigSchema (本模块,只提取描述性元数据)

设计要点:

1. **只读、纯函数** — 输入是 config dict,输出是结构化 schema,无副作用
2. **复用 ``MapperRule.from_mapping()``** — 与运行时共享同一套 YAML 解析器,
   保证 schema 描述与运行时行为一致;解析失败的单条规则被跳过(容错,日志 warning)
3. **完整覆盖** — 覆盖 ``MapperRule`` 的全部字段(包括 ``extension``、
   ``transforms``、``list_mode`` 等高级特性)
4. **人类可读 + 机器可读** — 所有模型提供 ``to_dict()``,可直接 ``json.dumps()``
"""

from __future__ import annotations

import json
import logging
import re
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Dict, Iterator, List, Mapping, Optional, Tuple, Union

import yaml

# NOTE: ``MapperRule`` is imported lazily inside ``_iter_mapper_rules`` (not at
# module top level) to break a package-level circular import:
# ``core.config_mapper -> core.interfaces -> common.models -> common.schema ->
# core.config_mapper``.  Importing ``cloudrobo_r2c.robots.robot_factory`` (or
# any top-level package member) otherwise fails during collection/import.

logger = logging.getLogger(__name__)

# ────────────────────────────────────────────────────────────────
# 类型推导规则
# ────────────────────────────────────────────────────────────────

_TARGET_TYPE_RULES: List[Tuple[str, str]] = [
    # (target_path 前缀, field_type)
    ("joint_states.", "joint_state"),
    ("end_effector_poses.", "end_effector_pose"),
    ("end_effector_states.", "end_effector_state"),
    ("images.color.", "color_image"),
    ("images.depth.", "depth_image"),
    ("images.", "image"),  # 旧版裸 images.* 目标(无 color/depth 分层)
    ("localization.", "localization"),
    ("pointclouds", "pointcloud"),
    ("extensions.", "extension"),
]

_IMAGE_TARGET_PREFIXES = ("images.color.", "images.depth.", "images.")

# r2c_to_device 目标键模式:
#   "joint_3" / "joint_3.pos" / "gripper"          → 索引式关节
#   "shoulder_pan.pos" 等命名关节的 `.pos` 后缀     → 命名关节
#   "tcp_x" / "eef_roll" / "tcp_qw" ...            → 笛卡尔分量
_JOINT_PATTERN = re.compile(
    r"^(joint_\d+|gripper)(\.pos(ition)?)?$",
    re.IGNORECASE,
)
_NAMED_JOINT_PATTERN = re.compile(
    r"^[a-z_][a-z0-9_-]*\.pos(ition)?$",
    re.IGNORECASE,
)
_CARTESIAN_PATTERN = re.compile(
    r"^(tcp_|eef_)(x|y|z|roll|pitch|yaw|qw|qx|qy|qz)", re.IGNORECASE
)
_JOINT_INDEX_PATTERN = re.compile(
    r"^(?:joint_|j)(\d+)(?:\.pos(ition)?)?$", re.IGNORECASE
)


def _derive_field_type(target_path: str) -> str:
    """根据 target_path 前缀推导 observation 字段的语义类型。"""
    for prefix, typ in _TARGET_TYPE_RULES:
        if target_path.startswith(prefix):
            return typ
    return "raw"


def _derive_action_semantic_type(target_key: str) -> str:
    """根据 target_key 推导语义类型: gripper / cartesian / joint / raw。"""
    if target_key in (
        "gripper",
        "gripper_position",
        "grip_cmd",
        "gripper.pos",
    ):
        return "gripper"
    if _CARTESIAN_PATTERN.match(target_key):
        return "cartesian"
    joint_match = _JOINT_PATTERN.match(target_key)
    named_match = _NAMED_JOINT_PATTERN.match(target_key)
    if joint_match or named_match:
        return "joint"
    return "raw"


def _json_safe(value: Any) -> Any:
    """递归转换为 JSON 可序列化结构(元组 → 列表,未知类型 → str)。"""
    if isinstance(value, Mapping):
        return {str(k): _json_safe(v) for k, v in value.items()}
    if isinstance(value, (list, tuple)):
        return [_json_safe(v) for v in value]
    if isinstance(value, (str, int, float, bool)) or value is None:
        return value
    return str(value)


def _section_type(config: Mapping[str, Any], key: str) -> str:
    """提取 config 某一 section 的 ``type`` 字段(缺失时返回空字符串)。"""
    section = config.get(key, {})
    if not isinstance(section, Mapping):
        return ""
    raw = section.get("type")
    return str(raw) if raw is not None else ""


# ────────────────────────────────────────────────────────────────
# 核心数据模型
# ────────────────────────────────────────────────────────────────


@dataclass(frozen=True)
class TransformInfo:
    """单个值转换步骤的描述。"""

    name: str
    config: Any = None

    def to_dict(self) -> Dict[str, Any]:
        return _json_safe({"name": self.name, "config": self.config})


@dataclass(frozen=True)
class ExtensionFieldInfo:
    """扩展字段(``extensions.*`` 目标)的类型元数据。"""

    # "FLOAT32" | "INT32" | "INT64" | "STRING" | "BYTES" | "BOOL"
    # | "FLOAT64" | "UINT8"
    dtype: str
    shape: List[int] = field(default_factory=list)  # 张量形状,[] 表示标量
    mime_type: str = ""  # MIME 类型(可选)

    def to_dict(self) -> Dict[str, Any]:
        return {
            "dtype": self.dtype,
            "shape": list(self.shape),
            "mime_type": self.mime_type,
        }


@dataclass(frozen=True)
class ObservationField:
    """描述 ``device_to_r2c`` 中的一条映射规则 — 即 Observation 的一个字段。"""

    # ── 身份 ──
    target_path: str  # R2C Observation 中的目标路径,如 "joint_states.position"

    # ── 来源 ──
    source_path: Optional[str] = None  # 单个源路径(与 source_paths 互斥)
    source_paths: Optional[List[str]] = None  # 多个源路径(与 source_path 互斥)
    source_name_ref: Optional[str] = None  # 按名称引用(名称查找模式)
    source_names_path: Optional[str] = None  # 名称列表路径
    source_index: Optional[int] = None  # 从源列表取第 N 个元素

    # ── 目标写入控制 ──
    target_index: Optional[int] = None  # 写入目标列表的第 N 个位置
    list_mode: Optional[str] = None  # "extend" | "append" | None

    # ── 值转换 ──
    transforms: List[TransformInfo] = field(default_factory=list)

    # ── 扩展元数据 ──
    extension: Optional[ExtensionFieldInfo] = None

    # ── 约束 ──
    required: bool = True  # 是否必需
    default: Any = None  # 默认值

    # ── 内部行为标记 ──
    use_lookup_dotted: bool = True
    use_assign_dotted: bool = True

    # ── 推导属性 ─────────────────────────────────────────────

    @property
    def field_type(self) -> str:
        """根据 target_path 自动推导语义类型。"""
        return _derive_field_type(self.target_path)

    @property
    def camera_name(self) -> Optional[str]:
        """如果是图像字段,返回相机名;否则 None。"""
        if self.field_type not in ("color_image", "depth_image", "image"):
            return None
        for prefix in _IMAGE_TARGET_PREFIXES:
            if self.target_path.startswith(prefix):
                rest = self.target_path.removeprefix(prefix)
                return rest or None
        return None

    # ── 工厂方法 ─────────────────────────────────────────────

    @classmethod
    def _from_mapper_rule(cls, rule: MapperRule) -> "ObservationField":
        """从已解析的 ``MapperRule`` 提取描述性字段(丢弃运行时状态)。"""
        transforms = []
        for t in rule.transforms or []:
            transforms.append(TransformInfo(name=t.name, config=t.config))
        extension = None
        if rule.extension is not None:
            extension = ExtensionFieldInfo(
                dtype=rule.extension.dtype,
                shape=list(rule.extension.shape),
                mime_type=rule.extension.mime_type,
            )
        source_paths = list(rule.source_paths) if rule.source_paths else None
        return cls(
            target_path=rule.target,
            source_path=rule.source,
            source_paths=source_paths,
            source_name_ref=rule.source_name_ref,
            source_names_path=rule.source_names_path,
            source_index=rule.source_index,
            target_index=rule.target_index,
            list_mode=rule.list_mode,
            transforms=transforms,
            extension=extension,
            required=rule.required,
            default=rule.default,
            use_lookup_dotted=rule.use_lookup_dotted,
            use_assign_dotted=rule.use_assign_dotted,
        )

    def to_dict(self) -> Dict[str, Any]:
        extension_dict = None
        if self.extension is not None:
            extension_dict = self.extension.to_dict()
        return _json_safe(
            {
                "field_type": self.field_type,
                "camera_name": self.camera_name,
                "target_path": self.target_path,
                "source_path": self.source_path,
                "source_paths": self.source_paths,
                "source_name_ref": self.source_name_ref,
                "source_names_path": self.source_names_path,
                "source_index": self.source_index,
                "target_index": self.target_index,
                "list_mode": self.list_mode,
                "transforms": [t.to_dict() for t in self.transforms],
                "extension": extension_dict,
                "required": self.required,
                "default": self.default,
                "use_lookup_dotted": self.use_lookup_dotted,
                "use_assign_dotted": self.use_assign_dotted,
            }
        )


@dataclass(frozen=True)
class ActionField:
    """描述 ``r2c_to_device`` 中的一条映射规则 — Action 中的一个输入维度。"""

    # ── 身份 ──
    target_key: str  # 设备命令中的目标键,如 "shoulder_pan.pos", "tcp_x"

    # ── 来源 ──
    source_path: Optional[str] = (
        None  # R2C Action 中的源路径,如 "joint_states.position"
    )
    source_index: Optional[int] = None  # 从源列表中取第 N 个元素

    # ── 切片 ──
    slice_start: Optional[int] = None
    slice_end: Optional[int] = None

    # ── 值转换 ──
    transforms: List[TransformInfo] = field(default_factory=list)

    # ── 约束 ──
    required: bool = True

    # ── 内部行为 ──
    use_assign_dotted: bool = True

    # ── 推导属性 ─────────────────────────────────────────────

    @property
    def semantic_type(self) -> str:
        """根据 target_key 模式推导语义类型。"""
        return _derive_action_semantic_type(self.target_key)

    @property
    def dimension_index(self) -> Optional[int]:
        """在目标维度向量中的索引。"""
        if self.source_index is not None:
            return self.source_index
        if self.slice_start is not None:
            return self.slice_start
        match = _JOINT_INDEX_PATTERN.match(self.target_key)
        if match:
            return int(match.group(1))
        return None

    # ── 工厂方法 ─────────────────────────────────────────────

    @classmethod
    def _from_mapper_rule(cls, rule: MapperRule) -> "ActionField":
        """从已解析的 ``MapperRule`` 提取描述性字段(丢弃运行时状态)。"""
        transforms = []
        for t in rule.transforms or []:
            transforms.append(TransformInfo(name=t.name, config=t.config))
        return cls(
            target_key=rule.target,
            source_path=rule.source,
            source_index=rule.source_index,
            slice_start=rule.slice_start,
            slice_end=rule.slice_end,
            transforms=transforms,
            required=rule.required,
            use_assign_dotted=rule.use_assign_dotted,
        )

    def to_dict(self) -> Dict[str, Any]:
        return _json_safe(
            {
                "semantic_type": self.semantic_type,
                "dimension_index": self.dimension_index,
                "target_key": self.target_key,
                "source_path": self.source_path,
                "source_index": self.source_index,
                "slice_start": self.slice_start,
                "slice_end": self.slice_end,
                "transforms": [t.to_dict() for t in self.transforms],
                "required": self.required,
                "use_assign_dotted": self.use_assign_dotted,
            }
        )


# ────────────────────────────────────────────────────────────────
# 顶层容器
# ────────────────────────────────────────────────────────────────


def _iter_mapper_rules(
    config: Mapping[str, Any], section_key: str
) -> Iterator[MapperRule]:
    """遍历 config 某一 section 下的 mappings,解析为 MapperRule。

    解析失败的单条规则被跳过并记录 warning(容错),不中断整个提取。
    """
    # Lazy import — see the note at the top of this module about the
    # package-level circular import with ``core.config_mapper``.
    from cloudrobo_r2c.core.config_mapper import MapperRule

    section = config.get(section_key, {})
    if not isinstance(section, Mapping):
        return
    raw_mappings = section.get("mappings", [])
    if not isinstance(raw_mappings, list):
        return
    for i, raw in enumerate(raw_mappings):
        if not isinstance(raw, Mapping):
            continue
        try:
            yield MapperRule.from_mapping(raw)
        except (ValueError, KeyError, TypeError) as exc:
            logger.warning("%s.mappings[%d] 解析失败,已跳过: %s", section_key, i, exc)


@dataclass(frozen=True)
class ObservationSchema:
    """``device_to_r2c`` 映射的完整结构化描述。"""

    task: str = ""  # task 描述字符串
    fields: List[ObservationField] = field(default_factory=list)

    # ── 便捷查询 ─────────────────────────────────────────────

    @property
    def joint_fields(self) -> List[ObservationField]:
        return [f for f in self.fields if f.field_type == "joint_state"]

    @property
    def image_fields(self) -> List[ObservationField]:
        return [
            f
            for f in self.fields
            if f.field_type in ("color_image", "depth_image", "image")
        ]

    @property
    def extension_fields(self) -> List[ObservationField]:
        return [f for f in self.fields if f.field_type == "extension"]

    @property
    def required_fields(self) -> List[ObservationField]:
        return [f for f in self.fields if f.required]

    @property
    def optional_fields(self) -> List[ObservationField]:
        return [f for f in self.fields if not f.required]

    def get_field(self, target_path: str) -> Optional[ObservationField]:
        """按 target_path 查找字段(存在重复 target 时返回第一条)。"""
        for f in self.fields:
            if f.target_path == target_path:
                return f
        return None

    # ── 工厂方法 ─────────────────────────────────────────────

    @classmethod
    def _from_config(cls, config: Mapping[str, Any]) -> "ObservationSchema":
        section = config.get("device_to_r2c", {})
        task = ""
        if isinstance(section, Mapping):
            raw_task = section.get("task")
            task = str(raw_task) if raw_task is not None else ""
        fields = [
            ObservationField._from_mapper_rule(rule)
            for rule in _iter_mapper_rules(config, "device_to_r2c")
        ]
        return cls(task=task, fields=fields)

    def to_dict(self) -> Dict[str, Any]:
        return {
            "task": self.task,
            "fields": [f.to_dict() for f in self.fields],
        }


@dataclass(frozen=True)
class ActionSchema:
    """``r2c_to_device`` 映射的完整结构化描述。"""

    fields: List[ActionField] = field(default_factory=list)

    # ── 便捷查询 ─────────────────────────────────────────────

    @property
    def target_keys(self) -> List[str]:
        """所有目标键。"""
        return [f.target_key for f in self.fields]

    @property
    def source_paths(self) -> List[str]:
        """去重后的源路径(保持出现顺序)。"""
        seen: List[str] = []
        for f in self.fields:
            if f.source_path and f.source_path not in seen:
                seen.append(f.source_path)
        return seen

    @property
    def dimension_count(self) -> int:
        """动作空间维度数。

        取所有字段覆盖到的最大源元素上界:``source_index + 1`` 或
        ``slice_end``(二者取大)。全部字段都无法推导时退化为字段数。
        """
        upper = 0
        known = False
        for f in self.fields:
            if f.slice_end is not None:
                upper = max(upper, f.slice_end)
                known = True
            if f.source_index is not None:
                upper = max(upper, f.source_index + 1)
                known = True
        return upper if known else len(self.fields)

    def get_fields_by_source(self, source_path: str) -> List[ActionField]:
        """按源路径查找全部字段。"""
        return [f for f in self.fields if f.source_path == source_path]

    # ── 工厂方法 ─────────────────────────────────────────────

    @classmethod
    def _from_config(cls, config: Mapping[str, Any]) -> "ActionSchema":
        fields = [
            ActionField._from_mapper_rule(rule)
            for rule in _iter_mapper_rules(config, "r2c_to_device")
        ]
        return cls(fields=fields)

    def to_dict(self) -> Dict[str, Any]:
        return {
            "fields": [f.to_dict() for f in self.fields],
        }


@dataclass(frozen=True)
class RobotConfigSchema:
    """机器人配置文件的完整 schema 描述。"""

    hardware_type: str = ""  # hardware.type
    translator_type: str = ""  # translator.type
    observation: ObservationSchema = field(default_factory=ObservationSchema)
    action: ActionSchema = field(default_factory=ActionSchema)

    # ── 工厂方法 ─────────────────────────────────────────────

    @classmethod
    def from_yaml(cls, path: Union[str, Path]) -> "RobotConfigSchema":
        """从 YAML 文件加载并提取 schema。"""
        with open(path, "r", encoding="utf-8") as handle:
            data = yaml.safe_load(handle) or {}
        if not isinstance(data, Mapping):
            logger.warning("YAML 文件 %s 未解析为 mapping,返回空 schema", path)
            return cls()
        return cls.from_dict(data)

    @classmethod
    def from_dict(cls, config: Mapping[str, Any]) -> "RobotConfigSchema":
        """从 config dict 提取 schema(纯函数,无副作用)。"""
        hardware_type = _section_type(config, "hardware")
        translator_type = _section_type(config, "translator")
        return cls(
            hardware_type=hardware_type,
            translator_type=translator_type,
            observation=ObservationSchema._from_config(config),
            action=ActionSchema._from_config(config),
        )

    def to_dict(self) -> Dict[str, Any]:
        return {
            "hardware_type": self.hardware_type,
            "translator_type": self.translator_type,
            "observation": self.observation.to_dict(),
            "action": self.action.to_dict(),
        }

    def to_json(self, indent: int = 2) -> str:
        """序列化为 JSON 字符串。"""
        return json.dumps(self.to_dict(), indent=indent, ensure_ascii=False)
