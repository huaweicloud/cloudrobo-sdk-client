"""ProtoRegistry: resolve registered proto names via ``r2c_sdk.protos`` entry points.

Robot/vendor-specific protos live in their adapter packages and register a
name at install time:

    [project.entry-points."r2c_sdk.protos"]
    "acme_robot.waist_command" = "acme_robot.models.generated.waist_command_pb2:WaistCommand"

Configs reference such protos by their registered name (``acme_robot.waist_command``)
instead of a raw ``module:ClassName`` path. SDK-bundled protos are NOT
registered — configs keep using ``module:ClassName`` for them. Resolution is
registry-first: a name that no entry point claims falls back to the plain
import path, so existing configs keep working unchanged.

本模块同时是 **proto 引用解析的唯一实现**（§12-8"五份实现统一"）：
:func:`resolve_proto_class` 按 *mode* 服务 hardware.topics 绑定（binding）、
单次收发工具（wrapper）与监听器解码（decode）；pb2 消息类的 wrapper 协议
适配（:class:`Pb2MessageAdapter` / :class:`Pb2MessageView`）也在此处
（自 client/topic_manager.py 迁入）。
"""

from __future__ import annotations

import importlib
import logging
from typing import Any, Dict, List, Optional

from google.protobuf.json_format import ParseDict, MessageToDict

logger = logging.getLogger(__name__)

ENTRY_POINT_GROUP = "r2c_sdk.protos"

# 强制实现约束（FE §2.3.1① / §8 item②）：`module:ClassName` 裸路径仅限
# SDK 内建 proto。SDK 内建 = `cloudrobo_r2c.common.models` 包树（generated pb2
# 平台/数据流 proto + wrappers 类）。第三方/厂商私有 proto 不得使用裸路径，
# 必须经 `r2c_sdk.protos` entry_point 注册、配置只写注册短名。
SDK_BUILTIN_MODULE_PREFIX = "cloudrobo_r2c.common.models"

# fail-fast 提示文案（多处断言依赖此精确文案，修改需同步 FE §8 item②）
THIRD_PARTY_NEED_ENTRY_POINT = "第三方 proto 须经 entry_point 注册"

# wrapper 协议四方法（工具/绑定共用的校验面）
_WRAPPER_REQUIRED_ATTRS = ("from_dict", "serialize", "to_dict", "from_protobuf")


def ensure_sdk_builtin(module_path: str, where: str = "") -> None:
    """裸路径回退仅允许 SDK 内建 proto；第三方须经 ``r2c_sdk.protos`` 注册。

    在 ``module:ClassName`` 裸路径解析回退（import）之前 fail-fast 拒绝
    非 SDK 内建模块，提示经 entry_point 注册（FE §8 item②、§9 校验）；
    避免第三方裸路径 import 绕过注册机制。注册短名路径不经此校验。
    """
    if module_path.startswith(SDK_BUILTIN_MODULE_PREFIX):
        return
    raise ValueError(
        f"{module_path!r} is not an SDK-builtin proto module; "
        f"{THIRD_PARTY_NEED_ENTRY_POINT}"
    )


def _entry_point_map() -> dict:
    """Return {registered name: "module:attr"} for the group (cached by caller)."""
    from importlib.metadata import entry_points

    eps = entry_points()
    try:
        group = eps.select(group=ENTRY_POINT_GROUP)
    except AttributeError:  # py<3.10 fallback
        group = eps.get(ENTRY_POINT_GROUP, [])
    return {ep.name: ep.value for ep in group}


def resolve_registered(name: str, where: str = "") -> Optional[type]:
    """Resolve *name* via the ``r2c_sdk.protos`` entry-point group.

    Returns the resolved class, or ``None`` when *name* is not registered
    (caller falls back to plain ``module:ClassName`` resolution).
    """
    if not name or ":" in name or "." not in name:
        # raw import path or no registered-namespace shape — not ours
        return None
    mapping = _entry_point_map()
    target = mapping.get(name)
    if target is None:
        return None
    module_path, _, attr = target.partition(":")
    try:
        module = importlib.import_module(module_path)
        cls = getattr(module, attr)
    except (ImportError, AttributeError) as exc:
        raise ValueError(
            f"{where}: registered proto {name!r} ({target!r}) cannot be "
            f"imported: {exc}"
        ) from exc
    logger.debug("proto %r resolved via %s entry point", name, ENTRY_POINT_GROUP)
    return cls


def registered_names() -> list:
    """List registered proto names (diagnostics/startup logs)."""
    return sorted(_entry_point_map())


# ── pb2 消息类的 wrapper 协议适配（自 client/topic_manager.py 迁入）──────


def _normalize_list_of_messages(data: Dict[str, Any]) -> Dict[str, Any]:
    """[[...]] 形式的嵌套列表 → [{"data": [...]}]（repeated message 语义）。

    ParseDict 要求 repeated message 字段的元素是 dict；collector 产出的
    dict 用 [[f,...]] 平铺表示（如 end_effector_poses.pose），此处统一
    包装为 {"data": [...]} 以兼容两种表示。
    """
    out: Dict[str, Any] = {}
    for key, value in data.items():
        if isinstance(value, dict):
            out[key] = _normalize_list_of_messages(value)
        elif (
            isinstance(value, list)
            and value
            and isinstance(value[0], list)
            and all(isinstance(x, list) for x in value)
        ):
            out[key] = [
                (
                    {"data": list(x)}
                    if all(isinstance(v, (int, float)) for v in x)
                    else _normalize_list_of_messages({"items": x})["items"]
                )
                for x in value
            ]
        else:
            out[key] = value
    return out


def is_pb2_message_class(cls: type) -> bool:
    """True for generated protobuf message classes (DESCRIPTOR + serialize)."""
    return hasattr(cls, "DESCRIPTOR") and hasattr(cls, "SerializeToString")


class Pb2MessageView:
    """Wrapper view over a parsed pb2 message exposing serialize()/to_dict().

    属性读写转发到内部 pb 消息，使 session._ensure_state_source 可直接
    填充 source 等 proto 字段（hasattr(view, "source") 命中）。
    """

    def __init__(self, msg: Any) -> None:
        object.__setattr__(self, "_msg", msg)

    def __getattr__(self, name: str) -> Any:
        return getattr(self._msg, name)

    def __setattr__(self, name: str, value: Any) -> None:
        setattr(self._msg, name, value)

    def serialize(self) -> bytes:
        return self._msg.SerializeToString()

    def to_dict(self) -> Dict[str, Any]:
        return MessageToDict(
            self._msg,
            preserving_proto_field_name=True,
            always_print_fields_with_no_presence=True,
        )


class Pb2MessageAdapter:
    """Adapt a generated protobuf message class to the wrapper protocol.

    Registry entries may reference either a wrapper class (from_dict /
    serialize / ...) or a generated pb2 message class directly; the
    adapter provides the wrapper protocol via json_format.
    """

    def __init__(self, pb2_cls: type) -> None:
        self._pb2_cls = pb2_cls
        self.__name__ = pb2_cls.__name__

    def from_dict(self, data: Dict[str, Any]) -> Pb2MessageView:
        msg = self._pb2_cls()
        ParseDict(
            _normalize_list_of_messages(data),
            msg,
            ignore_unknown_fields=True,
        )
        return Pb2MessageView(msg)

    def from_protobuf(self, payload: bytes) -> Pb2MessageView:
        msg = self._pb2_cls()
        msg.ParseFromString(payload)
        return Pb2MessageView(msg)

    def to_dict(self, msg: Any) -> Dict[str, Any]:
        return Pb2MessageView(msg).to_dict()


# 旧私有名别名（外部如有引用保持兼容）
_Pb2MessageView = Pb2MessageView
_Pb2MessageAdapter = Pb2MessageAdapter


# ── 统一 proto 引用解析（原五份实现收敛于此）──────────────────────────


def resolve_proto_class(path: str, where: str = "", *, mode: str = "wrapper") -> type:
    """Resolve a proto reference: registered name first, then import path.

    按解析后的类别与 *mode* 校验/适配：

    - ``mode="wrapper"``：必须实现 wrapper 四方法（单次收发工具
      publisher/subscriber 用；pb2 消息类没有 from_dict，自然被拒）；
    - ``mode="decode"``：需 ``from_protobuf``；pb2 消息类（DESCRIPTOR +
      FromString）自动适配为 :class:`Pb2MessageAdapter`（监听器解码用）；
    - ``mode="binding"``：``hardware.topics`` 绑定——wrapper 校验四方法，
      pb2 消息类适配为 :class:`Pb2MessageAdapter`（TopicRuntime 不再
      自行判别）。

    ``where`` 仅作错误信息前缀。
    """
    cls = resolve_registered(path, where)
    if cls is None:
        if ":" in path:
            module_path, _, class_name = path.partition(":")
        else:
            module_path, _, class_name = path.rpartition(".")
        if not module_path or not class_name:
            raise ValueError(f"{where}: invalid proto path {path!r}")
        # 强制实现约束：裸路径仅限 SDK 内建 proto（第三方须经 entry_point 注册）
        ensure_sdk_builtin(path, where)
        try:
            module = importlib.import_module(module_path)
        except ImportError as exc:
            raise ValueError(
                f"{where}: cannot import module {module_path!r}: {exc}"
            ) from exc
        cls = getattr(module, class_name, None)
        if cls is None:
            raise ValueError(f"{where}: {module_path!r} has no attribute {class_name!r}")

    prefix = f"{where}: " if where else ""
    if mode == "decode":
        if callable(getattr(cls, "from_protobuf", None)):
            return cls
        if hasattr(cls, "DESCRIPTOR") and hasattr(cls, "FromString"):
            return Pb2MessageAdapter(cls)
        raise ValueError(f"{prefix}{path!r} is not a wrapper with from_protobuf()")
    if is_pb2_message_class(cls):
        if mode == "binding":
            return Pb2MessageAdapter(cls)
        raise ValueError(
            f"{prefix}{path!r} is a pb2 message class; a wrapper "
            f"(from_dict/serialize/to_dict/from_protobuf) is required"
        )
    missing = [
        attr
        for attr in _WRAPPER_REQUIRED_ATTRS
        if not callable(getattr(cls, attr, None))
    ]
    if missing:
        raise ValueError(
            f"{prefix}wrapper {cls.__name__} lacks callable attrs: {missing}"
        )
    return cls
