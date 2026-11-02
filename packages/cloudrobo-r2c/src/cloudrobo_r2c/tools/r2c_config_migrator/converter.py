"""旧 r2c_config（model_feature_mapping）→ 新 v3.4 routes[] 格式转换核心。

本模块是**纯函数**转换器：输入旧格式 dict，输出新的 v3.4 routes[] dict。
不依赖 skill-engine（self-contained），仅使用标准库。转换本身是字典到字典的
字段映射，便于 CLI 与函数库两用。

新 v3.4 结构（权威来源：cloudrobo_r2c RouteConfig / CloudConfigNormalizer）：

    schema_version: "v3.4"
    default_serial_transform: "msgpack_pack"    # 顶层默认序列化（msgpack）
    default_deserial_transform: "msgpack_unpack"  # 顶层默认反序列化（msgpack）
    routes:
      - name: "default"
        description: ...
        protocol:
          type: "wss"               # 默认 WebSocket 安全连接
          endpoint: "{{HOST_URL}}/" # 运行时模板替换为实际主机地址
          probe: true               # 建联后 recv 一次确认模型存活（仅 websocket）
        r2c_to_model:
          - source_path: ...        # 别名 source_key/source_tensor/source
            target_path: ...        # 别名 target_key/target
            transforms: [ ... ]     # 无 transforms 时省略该 key
        model_to_r2c:
          - source_path: ...
            target_path: ...

    注意：转换时**忽略**旧 `output_features[action].chunk_size` / `shape` 以及顶层
    `stop_condition`（max_iter_num / max_run_time）
"""

from __future__ import annotations

import copy
from typing import Any, Dict, List, Optional, Tuple

# 新格式默认的 schema 版本
DEFAULT_SCHEMA_VERSION = "v3.4"
# 默认 route 名称（旧单模型配置拆为一个 default route）
DEFAULT_ROUTE_NAME = "default"
# 顶层默认 payload 序列化 / 反序列化 transform。
# 名称与 cloudrobo_r2c.core.transformers.PAYLOAD_CODECS 的键精确对应
# （msgpack_pack / msgpack_unpack，不带 "_transform" 后缀），
# 以保证运行时 r2c_cloud_adapter 能精确匹配到 msgpack 流编解码函数。
DEFAULT_SERIAL_TRANSFORM = "msgpack_pack"
DEFAULT_DESERIAL_TRANSFORM = "msgpack_unpack"

# 默认 route 附带的 protocol（WebSocket 安全连接）。
# endpoint 使用 {{HOST_URL}} 模板——运行时由 cloudrobo_r2c normalizer 替换为实际
# 主机地址（见 r2c_config_normalizer._resolve_endpoint）。
# probe=True：WebSocket 建联后先 recv 一次确认模型存活（收到的数据直接丢弃），
# 仅对 websocket 客户端有效。
# connect_strategy：连接策略，默认 lazy_fresh（每次收到观测数据才建连，
# 推理完成后断开连接，见 ws_policy_client）。
DEFAULT_PROTOCOL_TYPE = "wss"
DEFAULT_PROTOCOL_ENDPOINT = "{{HOST_URL}}/"
DEFAULT_PROTOCOL_PROBE = True
DEFAULT_PROTOCOL_CONNECT_STRATEGY = "lazy_fresh"

# VISUAL 特征的默认 transforms（与 skill-engine 对齐：JPEG 解码为 ndarray）
VISUAL_TRANSFORM = "jpeg_to_ndarray"


def parse_mapping_path(
    path: str, picked_name_num: bool = True
) -> Tuple[List[str], Optional[str], Optional[int]]:
    """轻量解析映射路径，支持 @{name_ref} 与 [index] 语法。

    自包含实现（不依赖 skill-engine 的 robot_config_helpers）：
      - "observation.joint_states.position@{j1}" → (["observation","joint_states","position"], "j1", None)
      - "observation.end_effector_poses.pose@{ee}[0]" → (..., "ee", 0)
      - "observations.images.color.front" → (path_parts, None, None)

    Args:
        path: 映射路径字符串。
        picked_name_num: 是否解析 @{name_ref} 和 [index]；False 时仅按 "." 分割。

    Returns:
        (路径组件列表, 名称引用, 数组索引) 元组。
    """
    path_parts = path.split(".")
    if not picked_name_num:
        return path_parts, None, None

    name_ref: Optional[str] = None
    array_index: Optional[int] = None
    last_part = path_parts[-1]
    if "@{" in last_part:
        field_part, rest = last_part.split("@{", 1)
        # 提取 name_ref
        if "}" not in rest:
            raise ValueError(f"Invalid name reference format in path: {path}")
        bname_part, _, after = rest.partition("}")
        name_ref = bname_part.strip() or None
        # 提取数组索引 [N]
        if "[" in after:
            idx_str = after.split("[", 1)[1].split("]", 1)[0].strip()
            try:
                array_index = int(idx_str)
            except ValueError:
                raise ValueError(f"Invalid array index in path: {path}")
        path_parts[-1] = field_part
    return path_parts, name_ref, array_index


def _normalize_image_path(path_parts: List[str]) -> str:
    """将旧格式图像路径转为 cloudrobo_r2c 的 source_path。

    去掉 "observations."/"observation." 前缀；2 段格式 [color|depth, name] 补齐 "images" 层。
    """
    parts = list(path_parts)
    if parts and parts[0] in ("observations", "observation"):
        parts = parts[1:]
    if len(parts) == 2 and parts[0] in ("color", "depth"):
        parts = ["images"] + parts
    return ".".join(parts)


def _is_prompt_feature(feature_key: str, feature_config: dict) -> bool:
    """检测 PROMPT 特征（type 为 PROMPT，或 dtype=string 且 value 指向 task）。"""
    if feature_config.get("type") == "PROMPT":
        return True
    if feature_config.get("dtype") == "string":
        value = feature_config.get("value", "")
        if not value:
            return False
        if value.startswith("task"):
            return True
        parts = value.split(".")
        if (
            len(parts) >= 2
            and parts[0].startswith("observation")
            and parts[1].startswith("task")
        ):
            return True
    return False


def _is_visual_feature(feature_key: str, feature_config: dict) -> bool:
    """检测 VISUAL 特征（value 指向图像路径）。"""
    value = feature_config.get("value", "")
    if not value:
        return False
    path_parts, _, _ = parse_mapping_path(value, picked_name_num=False)
    if len(path_parts) >= 3 and path_parts[0].startswith("observation"):
        return path_parts[1].startswith("image") and path_parts[2] in (
            "color",
            "depth",
        )
    if len(path_parts) == 2 and path_parts[0] in ("color", "depth"):
        return True
    return False


def _build_rule(
    source_path: str,
    target_path: str,
    transforms: Optional[List[Any]] = None,
    *,
    use_assign_dotted: bool = True,
) -> Dict[str, Any]:
    """构造一条 mapping rule。

    重要：仅当配置了 transforms 才包含 "transforms" key——否则 cloudrobo_r2c MapperRule
    会拒绝 "transforms" 存在但无值的 rule（'transforms' key present with no value）。
    """
    rule: Dict[str, Any] = {
        "source_path": source_path,
        "target_path": target_path,
        "use_assign_dotted": use_assign_dotted,
    }
    if transforms:
        rule["transforms"] = list(transforms)
    return rule


def convert_input_features(
    input_features: Dict[str, Any],
) -> List[Dict[str, Any]]:
    """将旧 input_features 转为新 r2c_to_model mapping 列表。

    转换规则（与 skill-engine r2c_config_adapter 对齐）：
      - PROMPT 特征 → 1 条 rule，source=task 路径，target=feature_key
      - VISUAL 特征 → 1 条 rule，source=图像路径，transforms=[jpeg_to_ndarray]
      - STATE 特征（values 数组）→ 每个 value 路径 1 条 rule
    """
    mappings: List[Dict[str, Any]] = []
    for feature_key, feature_config in input_features.items():
        if _is_prompt_feature(feature_key, feature_config):
            mappings.append(_convert_prompt_feature(feature_key, feature_config))
        elif _is_visual_feature(feature_key, feature_config):
            mappings.append(_convert_visual_feature(feature_key, feature_config))
        else:
            mappings.extend(_convert_state_feature(feature_key, feature_config))
    return mappings


def _convert_prompt_feature(
    feature_key: str, feature_config: dict
) -> Dict[str, Any]:
    """PROMPT 特征 → mapping rule。source 指向 task 字段。"""
    value = feature_config.get("value", "task")
    path_parts, _, _ = parse_mapping_path(value, picked_name_num=False)
    # 去掉 "observation." 前缀得到 source_path
    source_path = ".".join(path_parts[1:]) if path_parts and path_parts[0].startswith(
        "observation"
    ) else ".".join(path_parts)
    return _build_rule(
        source_path=source_path or "task",
        target_path=feature_key,
        use_assign_dotted=False,
    )


def _convert_visual_feature(
    feature_key: str, feature_config: dict
) -> Dict[str, Any]:
    """VISUAL 特征 → mapping rule，source=图像路径，transforms=[jpeg_to_ndarray]。"""
    img_field = feature_config.get("value", "")
    path_parts, _, _ = parse_mapping_path(img_field, picked_name_num=False)
    source_path = _normalize_image_path(path_parts)
    return _build_rule(
        source_path=source_path,
        target_path=feature_key,
        transforms=[VISUAL_TRANSFORM],
        use_assign_dotted=False,
    )


def _convert_state_feature(
    feature_key: str, feature_config: dict
) -> List[Dict[str, Any]]:
    """STATE 特征（values 数组）→ rule 列表。

    数据语义：`joint_states.position` 是**单个数组**（6 个关节的 position 按
    `joint_states.names` 顺序排列，见 cloudrobo_r2c JointObservation 模型）。因此当
    values 含多个条目（如 "observation.joint_states.position@{joint_N}"）时，
    feature key 应作为【数组】保存，而不是展开成 `.joint_N` 子字段。

    转换逻辑（与用户核心示例对齐）：
      - 单 value 且无 name_ref：保留标量行为（target=feature_key，1 条 rule）。
      - values 含多个条目（含 @name_ref）：**逐 value 展开成多条 rule**，每条
        从同一条 source 数组中按 `source_name_ref` 取出对应关节的取值，并使用
        `list_mode: extend` 累加进 feature_key 数组（target）。这**推翻**旧的
        "单条整体拷贝 rule" 逻辑——现在逐 value 生成 rule，target 依序累加
        各关节值，最终得到 `observation.state == [6 个关节值]`。
        每条 rule 的 `transforms` 为字符串 `list_to_ndarray`。
    """
    values = feature_config.get("values", [])
    base_key = feature_key
    if not values:
        return []

    # 解析全部 value 路径 → (source_path, name_ref, array_index)
    parsed: List[Tuple[str, Optional[str], Optional[int]]] = []
    for value_path in values:
        path_parts, name_ref, array_index = parse_mapping_path(value_path)
        # 去掉 "observation." 前缀 → source_path
        source_path = (
            ".".join(path_parts[1:])
            if path_parts and path_parts[0].startswith("observation")
            else ".".join(path_parts)
        )
        parsed.append((source_path, name_ref, array_index))

    # 单 value 且无 name_ref → 标量 target（保留原行为）
    if len(parsed) == 1 and parsed[0][1] is None:
        return [_build_rule(parsed[0][0], base_key, use_assign_dotted=False)]

    # 多 value（数组语义）：逐 value 展开成多条 rule，target 依序累加。
    # 每条 rule 从同一条 source 数组（如 joint_states.position）中按
    # source_name_ref（list 形式，单个元素）取对应关节的值，经
    # list_to_ndarray 转成标量后以 list_mode=extend 累进 feature_key 数组。
    rules: List[Dict[str, Any]] = []
    for source_path, name_ref, array_index in parsed:
        rule: Dict[str, Any] = {
            "source_path": source_path,
            "target_path": base_key,
        }
        # source_name_ref 为 list 形式（单元素）：运行时从 source_names_path
        # （如 joint_states.names）解析出该关节对应的取值。
        if name_ref:
            rule["source_name_ref"] = [name_ref]
        if array_index is not None:
            rule["source_index"] = array_index
        # transforms 使用字符串形式（list_to_ndarray）——与用户示例一致。
        rule["transforms"] = "list_to_ndarray"
        # list_mode=extend：空 target 直接赋值，非空则 np.concatenate 累加，
        # 使 target（feature_key）依序成为 6 关节值数组。
        rule["list_mode"] = "extend"
        # 注意：list_mode 与 source_index 不可同用；此处仅当 value 显式带 [N]
        # 下标时才设 source_index（list 形式与用户示例的 target_index 保持一致
        # 性，但实操中带下标的状态 value 极少出现）。
        rule["use_assign_dotted"] = False
        rules.append(rule)
    return rules


def convert_output_features(
    output_features: Dict[str, Any],
) -> List[Dict[str, Any]]:
    """将旧 output_features 转为 model_to_r2c mapping 列表。

    旧 output_features 形如 {"actions": {"chunk_size": N, "shape": [...], "values": [...]}}。

    **忽略旧 chunk_size / shape：** 用户明确要求不再迁移这两个字段到新 route
    顶层（新 v3.4 RouteConfig 无 chunk_size / shape 字段，RouteConfig.from_dict
    会忽略它们）。因此这里只生成 model_to_r2c 映射列表。

    单 action key 时生成其 values 对应的 rules；多个 action key 时全部合并。

    数组语义：当 values 含多个条目时，action 路径（target）作为【数组】保存，
    按 target_index 逐 value 写入，不再把 name_ref 拼成 ".{name_ref}" 子字段。
    """
    mappings: List[Dict[str, Any]] = []

    for action_key, action_config in output_features.items():
        values = action_config.get("values", [])
        if not values:
            continue

        # 解析各 value → (source_path, target_path, name_ref, array_index)
        parsed: List[Tuple[str, str, Optional[str], Optional[int]]] = []
        for value_path in values:
            # 旧格式 value 形如 "actions.joint_states.position@{j1}"。
            # 解析得到 ["actions", "joint_states", "position"]，第一段为 source tensor key。
            path_parts, name_ref, array_index = parse_mapping_path(value_path)
            # source：动作 key（output_features 的 key，如 "actions"/"action"），
            # target：去第一段前缀的路径（如 joint_states.position）。
            # 注：source_path 使用动作 key（action_key），与用户示例一致
            # （例如 action key = "action" 时 source_path 即为 "action"）。
            source_path = action_key
            target_path = ".".join(path_parts[1:])
            parsed.append((source_path, target_path, name_ref, array_index))

        # 单 value 且无 name_ref → 标量 target（保留原行为）
        if len(parsed) == 1 and parsed[0][2] is None:
            mappings.append(
                _build_rule(
                    source_path=parsed[0][0],
                    target_path=parsed[0][1],
                    use_assign_dotted=True,
                )
            )
            continue

        # 多 value（数组语义，如 6× "@joint_N" 关节动作）：
        #   1) 首先生成 names 常量 rule：把各 value 的 name_ref 依序收集成
        #      "default" 常量列表，写进 target 的 names 路径（如
        #      joint_states.position → joint_states.names）。该 rule 无
        #      source_path、无 use_assign_dotted、无 index——纯常量写路径。
        #   2) 逐 value 生成关节 rule：target（action 路径）作为【数组】，按
        #      source_index / target_index = [":", N]（N=0..5）多维持取/写入，
        #      use_assign_dotted=True。
        # 注意：输出侧 source 是模型动作张量 key（如 "actions"），其内部关节名
        # 结构无法静态确定，因此不设置 source_name_ref（否则 mapper 运行时会从
        # policy 输出中查找不存在的 names 路径而报错）；关节下标 N 由 target
        # 数组的第几个 value 静态推导（0..len-1）。
        name_refs = [p[2] for p in parsed if p[2] is not None]
        if name_refs and target_path:
            # 从 target 路径推导 names 路径：替换最后一个字段为 ".names"。
            names_target = target_path.rsplit(".", 1)[0] + ".names"
            mappings.append(
                {"default": list(name_refs), "target_path": names_target}
            )

        for i, (source_path, target_path, name_ref, array_index) in enumerate(parsed):
            rule: Dict[str, Any] = {
                "source_path": source_path,
                "target_path": target_path,
                "use_assign_dotted": True,
                "source_index": [":", i],
                "target_index": [":", i],
            }
            mappings.append(rule)

    return mappings


def build_default_route(
    model_feature_mapping: Dict[str, Any],
    route_name: str = DEFAULT_ROUTE_NAME,
    description: Optional[str] = None,
) -> Dict[str, Any]:
    """从旧 model_feature_mapping 构建一个默认 route dict。

    Args:
        model_feature_mapping: 旧格式的 model_feature_mapping 字典。
        route_name: route 名称，默认 "default"。
        description: route 描述（可选）。

    Returns:
        新 v3.4 的单个 route 字典（含默认 protocol）。
    """
    input_features = model_feature_mapping.get("input_features", {})
    output_features = model_feature_mapping.get("output_features", {})

    r2c_to_model = convert_input_features(input_features)
    model_to_r2c = convert_output_features(output_features)

    route: Dict[str, Any] = {"name": route_name}
    if description:
        route["description"] = description

    # 默认附带 WebSocket protocol（type=wss + {{HOST_URL}}/ 模板 endpoint + probe
    # + connect_strategy 连接策略）。
    # 旧配置若已显式指定输入 / 输出映射，仍按映射生成；protocol 始终使用默认值。
    route["protocol"] = {
        "type": DEFAULT_PROTOCOL_TYPE,
        "endpoint": DEFAULT_PROTOCOL_ENDPOINT,
        "probe": DEFAULT_PROTOCOL_PROBE,
        "connect_strategy": DEFAULT_PROTOCOL_CONNECT_STRATEGY,
    }

    # 仅当存在时才包含对应 key（与 cloudrobo_r2c 兼容：空 mapping 列表会导致校验失败）
    if r2c_to_model:
        route["r2c_to_model"] = r2c_to_model
    if model_to_r2c:
        route["model_to_r2c"] = model_to_r2c

    # 注意：忽略旧 chunk_size / shape / stop_condition——
    # 不在 route 顶层生成这些字段（新 v3.4 RouteConfig 无 chunk_size/shape）。

    return route


def convert_legacy_to_routes(
    old_config: Dict[str, Any],
    route_name: str = DEFAULT_ROUTE_NAME,
    description: Optional[str] = None,
) -> Dict[str, Any]:
    """将旧 r2c_config 转换为新的 v3.4 routes[] 结构。

    Args:
        old_config: 旧格式 r2c_config 字典（含 model_feature_mapping / stop_condition）。
        route_name: 生成的 default route 名称。
        description: route 描述。

    Returns:
        新 v3.4 配置字典（含 schema_version + 顶层 default_serial_transform /
        default_deserial_transform + routes 列表）。
    """
    model_feature_mapping = old_config.get("model_feature_mapping", {})

    route = build_default_route(
        model_feature_mapping,
        route_name=route_name,
        description=description,
    )
    return {
        "schema_version": DEFAULT_SCHEMA_VERSION,
        "default_serial_transform": DEFAULT_SERIAL_TRANSFORM,
        "default_deserial_transform": DEFAULT_DESERIAL_TRANSFORM,
        "routes": [route],
    }


def convert_r2c_config(
    old_config: Dict[str, Any],
    route_name: str = DEFAULT_ROUTE_NAME,
    description: Optional[str] = None,
) -> Dict[str, Any]:
    """旧 r2c_config → 新 v3.4 配置的主入口（纯函数，old → new dict）。

    对输入做深拷贝，避免修改调用方数据；返回新的 v3.4 结构。
    """
    if not isinstance(old_config, dict):
        raise TypeError(f"old_config must be a dict, got {type(old_config)!r}")
    config = copy.deepcopy(old_config)
    return convert_legacy_to_routes(
        config, route_name=route_name, description=description
    )
