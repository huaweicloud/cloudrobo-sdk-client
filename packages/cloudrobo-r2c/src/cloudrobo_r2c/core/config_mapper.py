"""Config-driven mappers for edge/cloud adapter pipelines."""

from __future__ import annotations

import inspect
import logging
from dataclasses import dataclass, field
from typing import (Any, Callable, Dict, Iterable, List, Mapping,
                    MutableMapping, Optional, Sequence, Tuple, Union, cast)

import numpy as np

from cloudrobo_r2c.core.interfaces import IValueTransformer
from cloudrobo_r2c.core.internal.helpers import assign_dotted, lookup_dotted
from cloudrobo_r2c.core.transformers import (build_transformer_registry, index_value,
                                       normalize_index)

logger = logging.getLogger(__name__)

ValueTransformerLike = IValueTransformer | Callable[..., Any]

# Sentinel returned by ``_resolve_map_value`` when a rule produces no
# assignment (non-required source resolved to None); ``_map_one_rule``
# returns early without touching the output.
_SKIP = object()


_VALID_KEY_RE = None  # compiled lazily


def _valid_key_re():
    global _VALID_KEY_RE
    if _VALID_KEY_RE is None:
        import re

        _VALID_KEY_RE = re.compile(r"^[a-zA-Z_\u4e00-\u9fa5][a-zA-Z0-9\u4e00-\u9fa5_-]*$")
    return _VALID_KEY_RE


def _validate_dotted_path(value: str, field_name: str) -> None:
    """Validate a dotted path.

    Each dot-separated segment must be a valid key: starts with a letter,
    CJK character, or underscore, followed by alphanumeric, CJK, underscore,
    or hyphen characters (both ASCII and CJK key segments are accepted).
    """
    if not value or not value.strip():
        raise ValueError(f"{field_name} must not be empty")
    stripped = value.strip()
    if stripped.startswith(".") or stripped.endswith("."):
        raise ValueError(f"{field_name} must not start or end with '.': {stripped!r}")
    if ".." in stripped:
        raise ValueError(
            f"{field_name} must not contain consecutive dots: {stripped!r}"
        )
    for segment in stripped.split("."):
        if not _valid_key_re().match(segment):
            raise ValueError(
                f"Invalid key segment {segment!r} in {field_name}={stripped!r}. "
                f"Each segment must start with a letter, CJK character, or "
                f"underscore and contain only letters, digits, CJK characters, "
                f"underscores, and hyphens (ASCII and CJK both accepted)."
            )


@dataclass(frozen=True)
class TransformSpec:
    """Single transform entry with optional runtime config."""

    name: str
    config: Any = None

    def __post_init__(self) -> None:
        if not self.name or not self.name.strip():
            raise ValueError("TransformSpec name must be a non-empty string")


_VALID_DTYPES = frozenset(
    {
        "FLOAT32",
        "FLOAT64",
        "INT32",
        "INT64",
        "UINT8",
        "BOOL",
        "STRING",
        "BYTES",
    }
)

# mime_type 按 dtype 分类的允许集合(见 _mime_types_for_dtype):
_MIME_TYPES_BINARY = frozenset(
    {
        "",
        "application/octet-stream",
        "image/jpeg",
        "image/png",
        "image/webp",
    }
)
_MIME_TYPES_TEXT = frozenset({"", "text/plain", "application/json"})

# 全部合法值(仅用于报错提示展示)
_VALID_MIME_TYPES = _MIME_TYPES_BINARY | _MIME_TYPES_TEXT

# extension 块的合法键(未知键如 `mine_type` 拼写错误直接报错)
_KNOWN_KEYS = frozenset({"dtype", "shape", "mime_type"})


def _mime_types_for_dtype(dtype: str) -> frozenset[str]:
    """按 dtype 返回允许的 mime_type 集合。

    ``BYTES`` 承载二进制/图像载荷;``STRING`` 承载文本语义;数值型
    dtype(FLOAT32 等)mime_type 无意义,仅允许缺省值 ``""``。
    """
    if dtype == "BYTES":
        return _MIME_TYPES_BINARY
    if dtype == "STRING":
        return _MIME_TYPES_TEXT
    return frozenset({""})


@dataclass(frozen=True)
class ExtensionSpec:
    """Metadata for an extension field mapping rule.

    Tells the mapper how to encode the transformed value into a
    self-describing ``ExtensionValue``.
    """

    dtype: str
    shape: List[int] = field(default_factory=list)
    mime_type: str = ""

    def __post_init__(self) -> None:
        if not self.dtype or not isinstance(self.dtype, str):
            raise ValueError("ExtensionSpec dtype must be a non-empty string")
        if self.dtype not in _VALID_DTYPES:
            raise ValueError(
                f"ExtensionSpec dtype must be one of {sorted(_VALID_DTYPES)}, "
                f"got {self.dtype!r}"
            )
        if not isinstance(self.shape, list):
            raise ValueError(
                "ExtensionSpec shape must be a list, "
                f"got {type(self.shape).__name__!r}"
            )
        neg_one_count = 0
        for i, dim in enumerate(self.shape):
            if not isinstance(dim, int):
                raise ValueError(
                    f"ExtensionSpec shape[{i}] must be an int, " f"got {dim!r}"
                )
            if dim < -1:
                raise ValueError(
                    f"ExtensionSpec shape[{i}] must be >= -1, " f"got {dim!r}"
                )
            if dim == -1:
                neg_one_count += 1
        if neg_one_count > 1:
            raise ValueError(
                f"ExtensionSpec shape may contain at most one -1 "
                f"(inferred dimension), got {neg_one_count}: {self.shape}"
            )
        if not isinstance(self.mime_type, str):
            raise ValueError(
                "ExtensionSpec mime_type must be a string, "
                f"got {type(self.mime_type).__name__!r}"
            )
        if self.mime_type not in _VALID_MIME_TYPES:
            raise ValueError(
                f"ExtensionSpec mime_type must be one of "
                f"{sorted(_VALID_MIME_TYPES)}, got {self.mime_type!r}"
            )
        allowed = _mime_types_for_dtype(self.dtype)
        if self.mime_type not in allowed:
            raise ValueError(
                f"ExtensionSpec mime_type {self.mime_type!r} is not valid "
                f"for dtype {self.dtype!r}; allowed: {sorted(allowed)}"
            )

    @classmethod
    def from_mapping(cls, raw: Mapping[str, Any]) -> "ExtensionSpec":
        unknown = sorted(str(k) for k in raw.keys() if k not in _KNOWN_KEYS)
        if unknown:
            raise ValueError(
                f"extension config contains unknown key(s): {unknown}; "
                f"allowed keys: {sorted(str(k) for k in _KNOWN_KEYS)}"
            )
        dtype = raw.get("dtype")
        if not dtype or not isinstance(dtype, str):
            raise ValueError("extension config must include a non-empty 'dtype'")
        raw_mime = raw.get("mime_type")
        return cls(
            dtype=dtype.strip().upper(),
            shape=list(raw.get("shape", [])),
            # None (YAML `mime_type:` 空值) 视同缺省;MIME 大小写不敏感
            mime_type="" if raw_mime is None else str(raw_mime).strip().lower(),
        )


@dataclass(frozen=True)
class MapperRule:
    """A single mapping rule loaded from YAML-like configuration."""

    source: Optional[str]
    target: str
    source_paths: Optional[List[str]] = None
    source_index: Optional[Union[int, Sequence]] = None
    source_name_ref: Optional[str] = None
    source_names_path: Optional[str] = None
    target_index: Optional[Union[int, Sequence]] = None
    slice_start: Optional[int] = None
    slice_end: Optional[int] = None
    transforms: Optional[List[TransformSpec]] = None
    required: bool = True
    default: Any = None
    use_lookup_dotted: bool = True
    use_assign_dotted: bool = True
    list_mode: Optional[str] = None  # "extend" | "append" | None
    extension: Optional[ExtensionSpec] = None
    source_value: Optional[Any] = None

    def __post_init__(self) -> None:
        _validate_mapper_rule(self)

    @classmethod
    def from_mapping(cls, rule: Mapping[str, Any]) -> "MapperRule":
        source_value = rule.get("source_value")
        source = _first_non_empty(
            rule,
            keys=("source_path", "source_key", "source_tensor", "source"),
        )
        # source_value is mutually exclusive with source_path and takes priority.
        if source_value is not None:
            source = None
        source_paths = _to_string_list_or_none(rule.get("source_paths"))
        target = _first_non_empty(rule, keys=("target_path", "target_key", "target"))
        default = rule.get("default")

        if (
            source is None
            and not source_paths
            and source_value is None
            and default is None
        ):
            raise ValueError(
                "Mapper rule must include source_path/source_key/source_tensor/source "
                "or source_paths or source_value or default"
            )
        if target is None:
            raise ValueError("Mapper rule must include target_key/target_path/target")

        extension = None
        if "extension" in rule:
            extension_raw = rule["extension"]
            if extension_raw is None:
                raise ValueError(
                    "'extension' key is present but has no value. "
                    "Remove the key or configure at least 'dtype' "
                    "(and optionally 'shape' and 'mime_type')."
                )
            if not isinstance(extension_raw, Mapping):
                raise ValueError(
                    "extension config must be a mapping with 'dtype' and "
                    "optionally 'shape' and 'mime_type'"
                )
            extension = ExtensionSpec.from_mapping(extension_raw)

        return cls(
            source=str(source) if source is not None else None,
            source_paths=source_paths,
            source_index=_to_source_index_or_none(rule.get("source_index")),
            source_name_ref=rule.get("source_name_ref"),
            source_names_path=rule.get("source_names_path"),
            target=str(target),
            target_index=_to_target_index_or_none(rule.get("target_index")),
            slice_start=_to_int_or_none(rule.get("slice_start")),
            slice_end=_to_int_or_none(rule.get("slice_end")),
            transforms=_to_transform_specs_or_none(
                _raw_transforms_value(rule)
            ),
            required=bool(rule.get("required", True)),
            default=rule.get("default"),
            use_lookup_dotted=bool(rule.get("use_lookup_dotted", True)),
            use_assign_dotted=bool(rule.get("use_assign_dotted", True)),
            list_mode=_to_list_mode_or_none(rule.get("list_mode")),
            extension=extension,
            source_value=source_value,
        )


def _collect_images_transform_error(rule: MapperRule) -> Optional[str]:
    """Return an error when *rule* targets images.* without an encoding transform.

    Protobuf serialization expects bytes for image fields; a mapping rule
    targeting images.* without a transform (e.g. ``ndarray_to_jpeg``)
    would pass a raw numpy array through and fail at serialization time.

    Returns ``None`` when the rule is fine.  Used both by
    :meth:`ConfigDrivenMapper._validate_rules` (raise on first error) and
    by ``collect_mapping_config_errors`` (aggregate all errors).
    """
    target = rule.target
    if not (target.startswith("images.") or ".images." in target):
        return None
    if rule.transforms:
        return None
    return (
        f"Rule {_rule_source_repr(rule)} → {target!r}: "
        f"targets under 'images.*' require an image-encoding "
        f"transform (e.g. ndarray_to_jpeg, ndarray_to_webp). "
        f"No transforms configured."
    )


def _collect_transformer_errors(
    rule: MapperRule,
    transformers: Mapping[str, ValueTransformerLike],
) -> List[str]:
    """Return transformer errors for one rule (unknown name / invalid config).

    Used both by :meth:`ConfigDrivenMapper._validate_transformer_configs`
    (raise on first error) and by ``collect_mapping_config_errors``
    (aggregate all errors).
    """
    errors: List[str] = []
    if not rule.transforms:
        return errors
    for spec in rule.transforms:
        transformer = transformers.get(spec.name)
        if transformer is None:
            errors.append(
                f"Unknown transformer {spec.name!r} in rule "
                f"{_rule_source_repr(rule)} → {rule.target}"
            )
            continue
        validate = getattr(transformer, "validate_config", None)
        if validate is None:
            continue
        try:
            validate(spec.config)
        except Exception as exc:
            errors.append(
                f"Invalid config for transformer {spec.name!r} "
                f"in rule {_rule_source_repr(rule)} → {rule.target}: "
                f"{exc}"
            )
    return errors


class ConfigDrivenMapper:
    """Map dictionaries via declarative source/target rules."""

    def __init__(
        self,
        rules: Sequence[MapperRule],
        *,
        transformers: Mapping[str, ValueTransformerLike] | None = None,
    ) -> None:
        if not rules:
            raise ValueError("ConfigDrivenMapper requires at least one mapping rule")
        self.rules = list(rules)
        self._transformers = build_transformer_registry(
            cast(Mapping[str, IValueTransformer] | None, transformers)
        )
        self._validate_rules()
        self._validate_transformer_configs()

    def _validate_transformer_configs(self) -> None:
        """Call ``validate_config`` on every src/cloudrobo_r2c/core/transformers.py transform in every rule.

        Raises ``ValueError`` with a descriptive message when a
        transformer rejects its configuration.

        ``source_value`` rules do not support transforms: any transforms
        listed alongside a ``source_value`` are ignored (matching the
        feature branch semantics), so they are not validated here.
        Unknown transform names are resolved lazily at ``map()`` time
        (``_apply_transforms``), which raises an "Unknown mapper transform"
        error.
        """
        for rule in self.rules:
            if rule.source_value is not None:
                continue
            if not rule.transforms:
                continue
            for spec in rule.transforms:
                transformer = self._transformers.get(spec.name)
                if transformer is None:
                    raise ValueError(
                        f"Unknown transformer {spec.name!r} in rule "
                        f"{_rule_source_repr(rule)} → {rule.target}"
                    )
                validate = getattr(transformer, "validate_config", None)
                if validate is None:
                    continue
                try:
                    validate(spec.config)
                except Exception as exc:
                    raise ValueError(
                        f"Invalid config for transformer {spec.name!r} "
                        f"in rule {_rule_source_repr(rule)} → {rule.target}: "
                        f"{exc}"
                    ) from exc

    def _validate_rules(self) -> None:
        """Cross-rule validations that go beyond single-rule checks."""
        seen_targets: dict[str, int] = {}
        for i, rule in enumerate(self.rules):
            # Duplicate target detection. target_index may be an int or a list
            # (multi-position insertion); normalise list -> tuple to keep the
            # key hashable for the dict lookup.
            ti = rule.target_index
            key = (rule.target, tuple(ti) if isinstance(ti, (list, tuple)) else ti)
            if key not in seen_targets:
                seen_targets[key] = i
            else:
                logger.debug(
                    "Duplicate target %r (rule #%d and #%d); "
                    "later rule will overwrite earlier.",
                    rule.target,
                    seen_targets[key],
                    i,
                )

            # ── images.* targets require an encoding transform ─────────
            img_err = _collect_images_transform_error(rule)
            if img_err is not None:
                raise ValueError(img_err)

    @classmethod
    def from_rule_mappings(
        cls,
        rules: Iterable[Mapping[str, Any]],
        *,
        transformers: Mapping[str, ValueTransformerLike] | None = None,
    ) -> "ConfigDrivenMapper":
        return cls(
            [MapperRule.from_mapping(rule) for rule in rules],
            transformers=transformers,
        )

    @classmethod
    def from_config_section(
        cls,
        cfg: Mapping[str, Any],
        *,
        section_path: str,
        rules_key: str,
        transformers: Mapping[str, ValueTransformerLike] | None = None,
    ) -> "ConfigDrivenMapper":
        section = lookup_dotted(cfg, section_path)
        if not isinstance(section, Mapping):
            raise ValueError(f"{section_path} must be a mapping")

        raw_rules = section.get(rules_key)
        if not isinstance(raw_rules, list):
            raise ValueError(f"{section_path}.{rules_key} must be a list")
        return cls.from_rule_mappings(raw_rules, transformers=transformers)

    def map(self, payload: Mapping[str, Any]) -> Dict[str, Any]:
        output: Dict[str, Any] = {}
        for rule in self.rules:
            try:
                self._map_one_rule(output, payload, rule)
            except Exception as e:
                raise ValueError(f"{e}, the convert rule is {rule}")

        return output

    def _map_one_rule(
        self, output: MutableMapping[str, Any], payload: Mapping[str, Any],
        rule: MapperRule,
    ) -> None:
        """Resolve, transform and assign a single mapping rule into *output*.

        Extracted from :meth:`map` to keep mechanical complexity low; the
        behaviour (error handling order, ``continue`` semantics) is
        identical — a rule that produces no assignment returns early.
        """
        value = self._resolve_map_value(payload, rule)
        if value is _SKIP:
            return
        self._assign_rule_target(output, rule, value)

    def _resolve_map_value(
        self, payload: Mapping[str, Any], rule: MapperRule
    ) -> Any:
        """Resolve the rule's value through source/transforms/slice/extension.

        Returns the module-level ``_SKIP`` sentinel when the rule produces
        no assignment (a non-required source resolved to ``None``); the
        caller then returns early without touching the output.
        """
        if rule.source_value is not None:
            # source_value writes a constant directly to the target field
            # and does not support transforms (mutually exclusive with
            # source_path / source_key / source_tensor / source).
            value = rule.source_value
        else:
            try:
                value = _resolve_rule_value(payload, rule)
            except KeyError:
                if rule.default is not None:
                    value = rule.default
                elif rule.required:
                    raise KeyError(
                        f"Missing required source field "
                        f"'{_rule_source_repr(rule)}' for target '{rule.target}'"
                    )
                else:
                    value = None

            # A ``None`` value means the source field exists but carries no
            # data (e.g. a camera frame not yet ready).  For required rules
            # this is a hard error; for non-required rules we simply skip
            # the assignment — we do *not* inject ``None`` into the output,
            # because downstream consumers (protobuf, model preprocessors,
            # tensor constructors) cannot interpret it.
            if value is None:
                if rule.required:
                    raise ValueError(
                        f"Required source field '{_rule_source_repr(rule)}' "
                        f"resolved to None for target '{rule.target}'"
                    )
                return _SKIP

            value = self._apply_transforms(value, rule, payload)
            value = _apply_slice(
                value, start=rule.slice_start, end=rule.slice_end
            )

        # ── extension: wrap as self-describing ExtensionValue ────
        if rule.extension is not None:
            value = _wrap_extension_value(value, rule.extension)
        return value

    def _assign_rule_target(
        self, output: MutableMapping[str, Any], rule: MapperRule, value: Any
    ) -> None:
        """Write the resolved *value* to the rule's target (list_mode aware)."""
        # ── list_mode: extend or append ───────────────────────────
        if rule.list_mode is not None:
            if not isinstance(value, (list, np.ndarray)):
                raise ValueError(
                    f"list_mode={rule.list_mode!r} requires a list or "
                    f"ndarray value, got {type(value).__name__!r} "
                    f"for target {rule.target!r}"
                )
            _apply_list_mode(
                output, rule, value,
            )
            return

        if rule.target_index is None:
            if rule.use_assign_dotted:
                assign_dotted(output, rule.target, value)
            else:
                output[rule.target] = value
        else:
            if _is_multi_dim_full_assign(rule.target_index):
                _assign_ndarray_target(output, rule, value)
            elif rule.use_assign_dotted:
                _assign_dotted_list_index(
                    output,
                    rule.target,
                    target_index=rule.target_index,
                    value=value,
                )
            else:
                _assign_list_index(
                    output,
                    rule.target,
                    target_index=rule.target_index,
                    value=value,
                )

    def check_completeness(self, source: Mapping[str, Any]) -> Dict[str]:
        """Return descriptions of source fields that are missing or ``None``.

        A field is considered incomplete when:

        - ``required=True`` (default), no ``default`` value: the source key
          is absent OR its resolved value is ``None``.
        - ``required=True`` with ``default``: only ``None`` values are
          reported; missing keys are tolerated (the default is used).
        - ``required=False``: the source key is present but the resolved
          value is ``None`` (a missing key is silently tolerated).

        Returns a list of human-readable descriptions (one per incomplete
        field). An empty list means all fields are ready.
        """
        incomplete: Dict[str] = {"required": [], "optional": []}
        for rule in self.rules:
            source_desc = _rule_source_repr(rule)
            has_default = rule.default is not None
            try:
                value = _resolve_rule_value(source, rule)
            except KeyError as e:
                if rule.required and not has_default:
                    incomplete["required"].append(f"{source_desc} ({'; '.join(e.args)})")
                continue
            except IndexError as e:
                if rule.required and not has_default:
                    incomplete["required"].append(f"{source_desc} ({'; '.join(e.args)})")
                continue
            if value is None:
                if rule.required:
                    incomplete["required"].append(f"{source_desc} (value is None but required)")
                else:
                    incomplete["optional"].append(f"{source_desc} (None)")
        return incomplete

    def _apply_transforms(
        self, value: Any, rule: MapperRule, context: Any = None
    ) -> Any:
        if not rule.transforms:
            return value
        if value is None:
            return value
        transformed = value
        for transform_spec in rule.transforms:
            transformer = self._transformers.get(transform_spec.name)
            if transformer is None:
                raise ValueError(f"Unknown mapper transform: '{transform_spec.name}'")
            transformed = _call_transformer(
                transformer, transformed, transform_spec.config, context
            )
        return transformed


def _apply_slice(value: Any, *, start: Optional[int], end: Optional[int]) -> Any:
    if start is None and end is None:
        return value
    if not isinstance(value, Sequence) or isinstance(value, (str, bytes, bytearray)):
        raise ValueError(
            "slice_start/slice_end can only be used with list/tuple values"
        )
    return list(value[start:end])


def _assign_dotted_list_index(
    output: MutableMapping[str, Any],
    dotted_key: str,
    *,
    target_index: int,
    value: Any,
) -> None:
    target_index = _target_position(target_index)
    if target_index < 0:
        raise ValueError("target_index must be >= 0")

    parts = dotted_key.split(".")
    current: MutableMapping[str, Any] = output
    for part in parts[:-1]:
        next_node = current.get(part)
        if not isinstance(next_node, MutableMapping):
            next_node = {}
            current[part] = next_node
        current = next_node

    leaf_key = parts[-1]
    leaf_value = current.get(leaf_key)
    if not isinstance(leaf_value, list):
        leaf_value = []
        current[leaf_key] = leaf_value

    while len(leaf_value) <= target_index:
        leaf_value.append(None)
    leaf_value[target_index] = value


def _resolve_rule_value(payload: Mapping[str, Any], rule: MapperRule) -> Any:
    if rule.source_paths:
        if rule.use_lookup_dotted:
            return [
                lookup_dotted(payload, dotted_key) for dotted_key in rule.source_paths
            ]
        return [payload[dotted_key] for dotted_key in rule.source_paths]
    if rule.source is None:
        if rule.default is None:
            raise KeyError("source")
        else:
            return rule.default

    if rule.use_lookup_dotted:
        value = lookup_dotted(payload, rule.source)
    else:
        value = payload[rule.source]

    # source_name_ref takes priority over source_index
    if rule.source_name_ref is not None:
        if rule.source_index is not None:
            logger.warning(
                "Both source_name_ref='%s' and source_index=%d set for target '%s'. "
                "source_name_ref takes priority.",
                rule.source_name_ref,
                rule.source_index,
                rule.target,
            )
        names_path = rule.source_names_path
        if names_path is None:
            # Auto-derive: "joint_states.position" → "joint_states.names"
            parts = rule.source.rsplit(".", 1)
            names_path = parts[0] + ".names" if len(parts) > 1 else "names"
        names = (
            lookup_dotted(payload, names_path)
            if rule.use_lookup_dotted
            else payload[names_path]
        )
        # source_name_ref 支持单个 name（字符串）或一组 name（list）。
        # 当为 list 时，按顺序提取每个 name 对应的值，并组成数组返回，
        # 便于后续配合 transforms（如 list_to_ndarray）整体转换为 numpy。
        if isinstance(rule.source_name_ref, (list, tuple)):
            return [
                _resolve_name_ref_value(
                    value, names, names_path, ref, rule.target
                )
                for ref in rule.source_name_ref
            ]
        return _resolve_name_ref_value(
            value, names, names_path, rule.source_name_ref, rule.target
        )

    if rule.source_index is None:
        return value
    return index_value(value, rule.source_index)


def _resolve_name_ref_value(
    value: Any,
    names: Sequence[Any],
    names_path: str,
    name_ref: str,
    target: str,
) -> Any:
    """Resolve a single ``source_name_ref`` to the value at its name index.

    ``names`` 是按 ``name_ref`` 名称定位数组下标的名称序列。
    返回 ``value`` 中对应名称下标处的元素。

    Args:
        value: 源数组/序列，按其下标取值。
        names: 名称序列（如 ``joint_states.names``），用于定位 name 的下标。
        names_path: 名称序列的路径，仅用于错误提示。
        name_ref: 要查找的单个名称（必须是字符串）。
        target: 目标字段路径，仅用于错误提示。

    Raises:
        KeyError: 当 ``name_ref`` 不在 ``names`` 中时。
        IndexError: 当解析得到下标超出 ``value`` 长度时。
    """
    if name_ref not in names:
        raise KeyError(
            f"Name '{name_ref}' not found in {names_path}: {names}"
        )
    idx = names.index(name_ref)
    if idx >= len(value):
        raise IndexError(
            f"Name ref index {idx} out of bounds for array of length {len(value)}"
        )
    return value[idx]


def _validate_mapper_rule(rule: MapperRule) -> None:
    """Validate a ``MapperRule``'s fields after construction."""
    _validate_mapper_target(rule)
    _validate_mapper_source(rule)
    _validate_mapper_indices(rule)
    _validate_mapper_list_mode(rule)
    _validate_mapper_extension(rule)
    _validate_mapper_slice(rule)


def _validate_mapper_target(rule: MapperRule) -> None:
    """Validate the ``target`` field."""
    _validate_dotted_path(rule.target, "target")


def _validate_mapper_source(rule: MapperRule) -> None:
    """Validate ``source`` and ``source_paths`` fields."""
    if rule.source is not None:
        _validate_dotted_path(rule.source, "source")
    if rule.source_paths is not None:
        _validate_source_paths(rule.source_paths)


def _validate_source_paths(source_paths: List[str]) -> None:
    """Validate each entry in ``source_paths``."""
    if not source_paths:
        raise ValueError("source_paths must not be empty when provided")
    for i, sp in enumerate(source_paths):
        if not isinstance(sp, str) or not sp.strip():
            raise ValueError(f"source_paths[{i}] must be a non-empty string")
        _validate_dotted_path(sp, f"source_paths[{i}]")


def _validate_index_parts(index: Any) -> None:
    """Validate a normalized index (int or list<int|None>) is non-negative.

    ``None`` components represent the ``":"`` full-select specifier (from
    ``normalize_index``) and are skipped; integer components must be >= 0.
    """
    if isinstance(index, (list, tuple)):
        for item in index:
            if item is not None and item < 0:
                raise ValueError(
                    f"index elements must be >= 0, got {index!r}"
                )
        return
    if index < 0:
        raise ValueError(f"index must be >= 0, got {index!r}")


def _validate_mapper_indices(rule: MapperRule) -> None:
    """Validate ``source_index`` and ``target_index`` are non-negative."""
    if rule.source_index is not None:
        _validate_index_parts(rule.source_index)
    if rule.target_index is not None:
        _validate_target_index(rule.target_index)


def _validate_target_index(target_index: Any) -> None:
    """Validate a ``target_index`` (int or list of int/None) is non-negative.

    ``None`` components (the ``":"`` full-select specifier) are skipped.
    """
    _validate_index_parts(target_index)

def _is_multi_dim_full_assign(target_index: Any) -> bool:
    """True when ``target_index`` is a list containing a full-select ``None``.

    Such indices target a numpy multi-dimension array by column/position
    (e.g. ``[":", N]`` → ``[None, N]``), requiring the ndarray construction
    path rather than plain list slot assignment.
    """
    return isinstance(target_index, (list, tuple)) and any(
        p is None for p in target_index
    )


def _build_initial_ndarray(spec: Any, value_arr: Any) -> Any:
    """Build the initial target ndarray from the first rule's value.

    The fixed column ``N`` (the non-``None`` component of ``spec``) decides
    where the value lands and the initial width: the value ``(R,)`` is placed
    in column ``N`` of a ``(R, N+1)`` target (padding earlier columns NaN).
    This preserves column semantics even when the first rule is N>0.
    """
    arr = np.asarray(value_arr)
    rows = arr.shape[0] if arr.ndim >= 1 else int(arr)
    fixed_col = next((p for p in spec if p is not None), 0)
    width = fixed_col + 1
    target = np.full((rows, width), fill_value=np.nan, dtype=arr.dtype)
    target[:, fixed_col] = arr.reshape(rows)
    return target


def _grow_and_assign(target: Any, spec: Any, value_arr: Any) -> Any:
    """Grow ``target`` to fit the fixed column in ``spec`` and write the value.

    ``spec`` is a normalized index like ``[None, N]``: the ``None`` dim is a
    full-select row axis (length fixed by ``target``/``value``), ``N`` is the
    column to write into.  Grows the second axis to ``N+1`` (padding NaN)
    when needed.
    """
    fixed_col = next(p for p in spec if p is not None)
    needed = fixed_col + 1
    arr = np.asarray(value_arr)
    target = np.asarray(target)
    if arr.shape[0] != target.shape[0]:
        raise ValueError(
            f"target 行数与 value 不一致 ({target.shape[0]} vs {arr.shape[0]})"
        )
    if target.shape[-1] < needed:
        pad = np.full(
            (target.shape[0], needed - target.shape[-1]),
            fill_value=np.nan,
            dtype=target.dtype,
        )
        target = np.concatenate([target, pad], axis=1)
    col = arr.reshape(target.shape[0], 1)
    target[:, fixed_col] = col[:, 0]
    return target


def _assign_ndarray_target(
    output: MutableMapping[str, Any],
    rule: MapperRule,
    value: Any,
) -> None:
    """Multi-dimension (full-select) target construction for plain assignment.

    First rule on an empty target directly assigns ``value`` as ``(R, 1)``;
    subsequent rules grow the second axis to fit the requested column and write
    the value in, yielding ``(R, max_column+1)`` overall.
    """
    spec = rule.target_index  # already normalized, e.g. [None, N]
    value_arr = np.asarray(value)
    target = _get_existing_target(output, rule)
    if target is None or _target_is_empty(target):
        built = _build_initial_ndarray(spec, value_arr)
        _assign_target(output, rule, built)
        return
    grown = _grow_and_assign(target, spec, value_arr)
    _assign_target(output, rule, grown)


def _target_position(target_index: Any) -> int:
    """Return a concrete int position for list-target indexing.

    A plain int is returned as-is; a list drops ``None`` (unconstrained
    dimension) and uses its first int as the list position.
    """
    if isinstance(target_index, (list, tuple)):
        for item in target_index:
            if item is not None:
                return item
        return 0
    return target_index


def _ndarray_insert_obj(target_index: Any) -> Any:
    """Return the ``np.insert`` obj; drop ``None`` (do-not-constrain) parts.

    ``np.insert``'s ``obj`` accepts an int or a sequence of int insertion
    positions; ``None`` has no meaning there, so unconstrained dims are
    filtered out.
    """
    if isinstance(target_index, (list, tuple)):
        return [i for i in target_index if i is not None]
    return target_index


def _validate_mapper_list_mode(rule: MapperRule) -> None:
    """Validate ``list_mode`` compatibility with ``source_index``."""
    if rule.list_mode is not None and rule.source_index is not None:
        raise ValueError(
            "list_mode cannot be used with source_index "
            "(source_index returns a scalar, list_mode requires a list)"
        )



def _validate_mapper_extension(rule: MapperRule) -> None:
    """Validate ``extension`` compatibility with other fields."""
    if rule.extension is None:
        return
    if rule.list_mode is not None:
        raise ValueError(
            "extension and list_mode cannot be used together"
        )
    if rule.target_index is not None:
        raise ValueError(
            "extension and target_index cannot be used together"
        )
    # Extension rules must target extensions.* — otherwise the wrapped
    # ExtensionValue dict is assigned to a non-extension field and
    # _parse_extensions() will never see it.
    if not rule.target.startswith("extensions."):
        raise ValueError(
            f"extension mapping target must start with 'extensions.', "
            f"got {rule.target!r}. "
            f"ExtensionValue dicts assigned to other fields are silently "
            f"ignored by the extension parser."
        )


def _validate_mapper_slice(rule: MapperRule) -> None:
    """Validate ``slice_start`` and ``slice_end`` fields."""
    _validate_slice_bound(rule.slice_start, "slice_start")
    _validate_slice_bound(rule.slice_end, "slice_end")
    _validate_slice_order(rule.slice_start, rule.slice_end)


def _validate_slice_bound(value: Optional[int], field_name: str) -> None:
    """Validate a single slice bound is non-negative."""
    if value is not None and value < 0:
        raise ValueError(f"{field_name} must be >= 0, got {value}")


def _validate_slice_order(
    slice_start: Optional[int], slice_end: Optional[int]
) -> None:
    """Validate that slice_end > slice_start when both are set."""
    if slice_start is None or slice_end is None:
        return
    if slice_end <= slice_start:
        raise ValueError(
            f"slice_end ({slice_end}) must be > slice_start "
            f"({slice_start})"
        )


def _rule_source_repr(rule: MapperRule) -> str:
    """Return a human-readable description of the rule's source.

    Used in error messages and log output.  When the rule has no source
    or source_paths (only a ``default``, common for extension rules),
    returns a descriptive string that includes the target path instead
    of the unhelpful ``"None"``.
    """
    if rule.source_paths:
        return ",".join(rule.source_paths)
    if rule.source is not None:
        return str(rule.source)
    # No source — rule is default-only (typical for extension fields).
    return f"[default]→{rule.target}"


def _first_non_empty(
    mapping: Mapping[str, Any], *, keys: Sequence[str]
) -> Optional[Any]:
    for key in keys:
        value = mapping.get(key)
        if value is not None and value != "":
            return value
    return None


def _to_list_mode_or_none(value: Any) -> Optional[str]:
    if value is None:
        return None
    if not isinstance(value, str):
        raise ValueError(f"list_mode must be a string, got {type(value).__name__!r}")
    mode = value.strip().lower()
    if mode not in ("extend", "append"):
        raise ValueError(
            f"list_mode must be 'extend' or 'append', got {mode!r}"
        )
    return mode


def _to_int_or_none(value: Any) -> Optional[int]:
    if value is None:
        return None
    return int(value)

def _to_target_index_or_none(
    value: Any,
) -> Optional[Union[int, Sequence]]:
    """Parse a ``target_index`` config value (int or list of ints).

    The list form holds per-dimension insertion positions.  Consistent with
    :func:`_to_source_index_or_none`, ``None``/``"none"`` components mean
    "do not constrain that dimension" and are dropped at use time.  A
    top-level ``None`` means "not set".
    """
    if value is None:
        return None
    return normalize_index(value)


def _to_source_index_or_none(value: Any) -> Optional[Union[int, Sequence]]:
    """Parse a ``source_index`` config value (int or per-dimension list).

    ``None`` means "not set".  A list supports per-dimension selection with
    ``None``/``"none"`` meaning "select the whole dimension"; a plain int is a
    single-dimension index.  Uses the shared :func:`normalize_index` so it is
    consistent with ``IndexTransformer``.
    """
    if value is None:
        return None
    return normalize_index(value)


def _to_string_list_or_none(value: Any) -> Optional[List[str]]:
    if isinstance(value, str):
        stripped = value.strip()
        return [stripped] if stripped else None
    if not isinstance(value, list):
        return None
    result = [str(item) for item in value if item is not None and str(item) != ""]
    return result or None


def _raw_transforms_value(rule: Mapping[str, Any]) -> Any:
    """Extract the raw transforms value, distinguishing 'key not present'
    (→ None) from 'key present with null' (→ raise)."""
    has_transform = "transform" in rule
    has_transforms = "transforms" in rule
    if has_transforms and has_transform:
        raise ValueError(
            "Use 'transforms' or 'transform' (singular) — not both."
        )
    if has_transforms:
        raw = rule["transforms"]
        if raw is None:
            raise ValueError(
                "'transforms' key is present but has no value. "
                "Remove the key or configure at least one transform."
            )
        return raw
    if has_transform:
        raw = rule["transform"]
        if raw is None:
            raise ValueError(
                "'transform' key is present but has no value. "
                "Remove the key or configure at least one transform."
            )
        return raw
    return None  # neither key present — no transforms


def _to_transform_specs_or_none(value: Any) -> Optional[List[TransformSpec]]:
    if value is None:
        return None
    raw_items: List[Any]
    if isinstance(value, list):
        if not value:
            raise ValueError(
                "'transforms' must not be an empty list. "
                "Remove the key or configure at least one transform."
            )
        raw_items = value
    else:
        raw_items = [value]

    results: List[TransformSpec] = []
    for item in raw_items:
        if isinstance(item, str):
            stripped = item.strip()
            if stripped:
                if ":" in stripped:
                    name, config_val = stripped.split(":", 1)
                    results.append(
                        TransformSpec(
                            name=name.strip(), config=config_val.strip()
                        )
                    )
                else:
                    results.append(TransformSpec(name=stripped))
            continue
        if isinstance(item, Mapping):
            if len(item) != 1:
                raise ValueError(
                    "Each transform mapping must include exactly one transform name"
                )
            name = next(iter(item.keys()))
            if not isinstance(name, str) or not name.strip():
                raise ValueError("Transform name must be a non-empty string")
            results.append(TransformSpec(name=name.strip(), config=item[name]))
            continue
        raise ValueError(
            "Invalid transform entry: expected string or single-key mapping"
        )

    return results or None


def _call_transformer(
    transformer: ValueTransformerLike, value: Any, config: Any = None,
    context: Any = None,
) -> Any:
    if isinstance(transformer, IValueTransformer):
        return transformer.transform(value, config, context)

    # IValueTransformer 是 Protocol(非 runtime_checkable),isinstance 后
    # mypy 无法收窄到 Callable 分支,这里显式转型。
    callable_transformer = cast(Callable[..., Any], transformer)
    parameters = inspect.signature(callable_transformer).parameters
    if len(parameters) >= 3:
        return callable_transformer(value, config, context)
    if len(parameters) >= 2:
        return callable_transformer(value, config)
    return callable_transformer(value)


def _flatten_numeric(
    value: Any, cast_fn: Any, out: List[Any],
) -> None:
    """Recursively flatten a nested list/tuple, casting each leaf element.

    Raises ``ValueError`` if *value* is not a list/tuple and the cast
    fails (e.g. ``int("abc")``), which produces a friendlier error than
    the raw ``TypeError`` from ``struct.pack``.
    """
    if isinstance(value, (list, tuple)):
        for item in value:
            _flatten_numeric(item, cast_fn, out)
    else:
        out.append(cast_fn(value))


def _validate_extension_element_count(
    flat_count: int,
    shape: List[int],
    dtype: str,
    *,
    strict: bool = False,
) -> None:
    """Check that the number of flat elements matches the shape product.

    Logs a warning on mismatch to help catch config errors early, but
    does not raise — the downstream ``ExtensionValue.to_ndarray()``
    will reject the mismatch definitively at consumption time.

    With ``strict=True`` (startup config validation), the mismatch
    raises ``ValueError`` instead.

    When *shape* contains a ``-1`` (inferred dimension), the check is
    skipped because the total element count is not known statically.
    """
    if -1 in shape:
        # Dynamic / inferred dimension — cannot validate count.
        return
    expected = 1
    for dim in shape:
        expected *= dim
    if expected == 0:
        # shape=[] means scalar — expect exactly 1 element
        expected = 1
    if flat_count != expected:
        message = (
            f"Extension value element count mismatch: "
            f"shape={shape} expects {expected} element(s), but value has "
            f"{flat_count} element(s). dtype={dtype}. "
            f"This will fail in ExtensionValue.to_ndarray() at consumption time."
        )
        if strict:
            raise ValueError(message)
        logger.warning(message)


def _wrap_extension_value(
    value: Any,
    spec: ExtensionSpec,
    *,
    strict: bool = False,
) -> Dict[str, Any]:
    """Wrap a transformed value as an ExtensionValue-compatible dict.

    The dict has the same keys as the ``ExtensionValue`` dataclass, so
    that ``Observations.from_dict()`` / ``Actions.from_dict()`` can
    parse it via ``_parse_extensions()``.

    For numeric extension types (INT32, FLOAT64, etc.), *value* may be
    a scalar or a (possibly nested) list/tuple matching the declared
    ``shape``.  The list is recursively flattened and packed element by
    element so that e.g. a ``default: [1, 1, 1]`` list with
    ``shape: [3]`` produces a 3-element ``struct.pack`` buffer.

    With ``strict=True`` (startup config validation of ``default``
    values), conditions that would normally only log a warning raise
    ``ValueError`` instead.
    """
    import struct

    dtype = spec.dtype
    shape = list(spec.shape)

    def _out(data: Any) -> Dict[str, Any]:
        return {
            "dtype": dtype,
            "shape": shape,
            "data": data,
            "mime_type": spec.mime_type,
        }

    # ── already-encoded bytes ───────────────────────────────────
    if isinstance(value, (bytes, bytearray, memoryview)):
        return _out(bytes(value))

    # ── string ───────────────────────────────────────────────────
    if dtype == "STRING":
        if not isinstance(value, str):
            message = (
                f"STRING extension received non-str value "
                f"(type={type(value).__name__}); "
                f"encoding via str() may produce surprising results."
            )
            if strict:
                raise ValueError(message)
            logger.warning(message)
        return _out(str(value).encode("utf-8"))

    # ── numpy ndarray ────────────────────────────────────────────
    if hasattr(value, "tobytes"):
        return _out(value.tobytes())

    # ── explicit bytes (BYTES dtype) ─────────────────────────────
    if dtype == "BYTES":
        if isinstance(value, (bytes, bytearray, memoryview)):
            return _out(bytes(value))
        if isinstance(value, (list, tuple)):
            # Allow list-of-ints (each 0–255) for BYTES fields
            return _out(bytes(value))
        if isinstance(value, int):
            raise TypeError(
                f"BYTES extension received an int ({value}); "
                f"bytes(int) creates a zero-filled buffer, which is "
                f"almost certainly not intended. Use a list of ints "
                f"(e.g. [0x01, 0x02]) or pass bytes directly."
            )
        return _out(bytes(value))

    # ── numeric types (scalar or sequence) ───────────────────────
    if dtype in ("FLOAT32", "FLOAT64", "INT32", "INT64", "UINT8", "BOOL"):
        _FMT: Dict[str, str] = {
            "FLOAT32": "f",
            "FLOAT64": "d",
            "INT32": "i",
            "INT64": "q",
            "UINT8": "B",
            "BOOL": "?",
        }
        _CAST: Dict[str, Any] = {
            "FLOAT32": float,
            "FLOAT64": float,
            "INT32": int,
            "INT64": int,
            "UINT8": int,
            "BOOL": bool,
        }
        fmt_char = _FMT[dtype]
        cast_fn = _CAST[dtype]
        if isinstance(value, (list, tuple)):
            flat: List[Any] = []
            _flatten_numeric(value, cast_fn, flat)
            # Warn on shape mismatch so config authors catch errors early.
            _validate_extension_element_count(
                len(flat),
                shape,
                dtype,
                strict=strict,
            )
            try:
                data = struct.pack(f"<{len(flat)}{fmt_char}", *flat)
            except struct.error as exc:
                raise ValueError(
                    f"Failed to pack {len(flat)} element(s) as "
                    f"extension dtype={dtype!r}: {exc}. "
                    f"Check that all values are within the valid range "
                    f"for {dtype}."
                ) from exc
            return _out(data)
        try:
            data = struct.pack(f"<{fmt_char}", cast_fn(value))
        except struct.error as exc:
            raise ValueError(
                f"Failed to pack value {value!r} as extension "
                f"dtype={dtype!r}: {exc}. "
                f"Check that the value is within the valid range "
                f"for {dtype}."
            ) from exc
        return _out(data)

    # ── fallback ─────────────────────────────────────────────────
    try:
        data = bytes(value)
    except TypeError:
        raise TypeError(
            f"Cannot encode value of type {type(value).__name__} "
            f"for extension dtype={dtype!r}. "
            f"Add a transform (e.g. ndarray_to_bytes) before the "
            f"extension mapping."
        ) from None
    return _out(data)


def _apply_list_mode(
    output: MutableMapping[str, Any],
    rule: MapperRule,
    value: Any,
) -> None:
    """Apply list_mode extend/append to a target list or ndarray.

    - If the current target value is empty (absent, ``[]`` or an empty
      ndarray), the incoming ``value`` is assigned directly so the target
      adopts ``value``'s own container type (``list`` or ``numpy.ndarray``).
    - If the target is non-empty, ``extend``/``append`` preserves the target's
      container type: a ``list`` target stays a ``list`` and an ``ndarray``
      target stays an ``ndarray`` before and after the mutation.
    """
    target = _get_existing_target(output, rule)

    if not isinstance(value, Iterable):
        value = [value]

    if _target_is_empty(target):
        # Direct assignment — the target takes the incoming value as-is.
        _assign_target(output, rule, value)
        return

    if _is_ndarray(target):
        # extend concatenates along axis 0; append stacks the value as a new
        # leading row (vstack / insert-a-row), preserving the ndarray type.
        arr_value = np.asarray(value)
        if rule.target_index is None:
            if rule.list_mode == "extend":
                result = np.concatenate([target, arr_value], axis=0)
            else:  # append
                result = np.vstack([target, arr_value])
        else:
            obj = _ndarray_insert_obj(rule.target_index)
            result = np.insert(target, obj, arr_value, axis=0)
        _assign_target(output, rule, result)
        return

    # Python list target — mutated in place so the type stays ``list``.
    if rule.target_index is None:
        if rule.list_mode == "extend":
            target.extend(_as_flat_list(value))
        else:  # append
            target.append(value)
    else:
        idx = _target_position(rule.target_index)
        if rule.list_mode == "extend":
            target[idx:idx] = _as_flat_list(value)  # splice at position
        else:  # append
            target.insert(idx, value)


def _get_existing_target(
    output: MutableMapping[str, Any], rule: MapperRule
) -> Any:
    """Return the current target value, leaving an ndarray untouched.

    Unlike the old helper, this does *not* clobber a non-list target (e.g. an
    ndarray) with ``[]``; an absent/untouched target returns ``None``.
    """
    if rule.use_assign_dotted:
        parts = rule.target.split(".")
        current: MutableMapping[str, Any] = output
        for part in parts[:-1]:
            node = current.get(part)
            if not isinstance(node, MutableMapping):
                return None
            current = node
        return current.get(parts[-1])
    return output.get(rule.target)


def _target_is_empty(target: Any) -> bool:
    """A target is 'empty' when it is absent or has no elements."""
    if target is None:
        return True
    try:
        return len(target) == 0
    except (TypeError, AttributeError):
        return False


def _is_ndarray(value: Any) -> bool:
    """True when ``value`` is a numpy ndarray (without a hard import crash)."""
    return isinstance(value, np.ndarray)


def _as_flat_list(value: Any) -> Any:
    """Coerce an ndarray to a Python list (for list-type target extend)."""
    if _is_ndarray(value):
        return value.tolist()
    return value


def _assign_target(
    output: MutableMapping[str, Any],
    rule: MapperRule,
    value: Any,
) -> None:
    """Write ``value`` to the rule's target, honouring dotted assign paths."""
    if rule.use_assign_dotted:
        assign_dotted(output, rule.target, value)
    else:
        output[rule.target] = value


def _assign_list_index(
    output: MutableMapping[str, Any],
    key: str,
    *,
    target_index: int,
    value: Any,
) -> None:
    target_index = _target_position(target_index)
    if target_index < 0:
        raise ValueError("target_index must be >= 0")

    leaf_value = output.get(key)
    if not isinstance(leaf_value, list):
        leaf_value = []
        output[key] = leaf_value

    while len(leaf_value) <= target_index:
        leaf_value.append(None)
    leaf_value[target_index] = value


# ────────────────────────────────────────────────────────────────
# 启动前聚合校验(load_yaml / schema 提取共用)
# ────────────────────────────────────────────────────────────────


def _collect_duplicate_target_errors(
    parsed: List[Tuple[int, MapperRule]],
    section_key: str,
    errors: List[str],
) -> None:
    """Report rules that write the same target (later overwrites earlier).

    Rules that all use ``list_mode`` are exempt — extend/append into one
    target list is the documented accumulation pattern.
    """
    by_key: Dict[Tuple[str, Optional[int]], List[Tuple[int, MapperRule]]] = {}
    for i, rule in parsed:
        by_key.setdefault((rule.target, rule.target_index), []).append((i, rule))
    for (target, _target_index), group in by_key.items():
        if len(group) < 2:
            continue
        if all(rule.list_mode is not None for _, rule in group):
            continue
        indices = ", ".join(f"mappings[{i}]" for i, _ in group)
        errors.append(
            f"{section_key}: duplicate target {target!r} ({indices}) — "
            f"the later rule overwrites the earlier one"
        )


def collect_mapping_config_errors(config: Mapping[str, Any]) -> List[str]:
    """Collect human-readable errors for every mapping rule in *config*.

    Startup-time validation for file-based robot configs: parses every
    rule with the same ``MapperRule.from_mapping`` used at runtime, and
    additionally reports problems the single-rule parser cannot see:

    - ``extension`` 键存在但无值 / 非法子配置(在单条规则解析中报出)
    - ``extensions.*`` 目标但规则未配置 ``extension`` 块
    - 重复写入同一 target(后写覆盖先写;``list_mode`` 累加模式豁免)
    - ``images.*`` 目标缺少编码 transform / 未知 transformer
    - ``default`` 值无法按 extension 的 dtype/shape 编码(严格预验)

    Returns an empty list when the mapping sections are absent or fully
    valid.  Sections that are missing or not mappings are tolerated here
    — the CLI's pydantic layer (``RobotConfig``) enforces their presence.
    """
    errors: List[str] = []
    transformers = build_transformer_registry()
    for section_key in ("device_to_r2c", "r2c_to_device"):
        section = config.get(section_key)
        if not isinstance(section, Mapping):
            continue
        raw_mappings = section.get("mappings")
        if not isinstance(raw_mappings, list):
            continue
        parsed: List[Tuple[int, MapperRule]] = []
        for i, raw in enumerate(raw_mappings):
            if not isinstance(raw, Mapping):
                errors.append(
                    f"{section_key}.mappings[{i}] must be a mapping, "
                    f"got {type(raw).__name__}"
                )
                continue
            try:
                rule = MapperRule.from_mapping(raw)
            except (ValueError, KeyError, TypeError) as exc:
                errors.append(f"{section_key}.mappings[{i}]: {exc}")
                continue
            parsed.append((i, rule))

            target = rule.target
            if target.startswith("extensions.") and rule.extension is None:
                errors.append(
                    f"{section_key}.mappings[{i}] (target {target!r}): "
                    f"rules targeting 'extensions.*' must configure an "
                    f"'extension' block with 'dtype' (and optionally "
                    f"'shape' / 'mime_type')"
                )
            img_err = _collect_images_transform_error(rule)
            if img_err is not None:
                errors.append(f"{section_key}.mappings[{i}]: {img_err}")
            for transformer_err in _collect_transformer_errors(rule, transformers):
                errors.append(f"{section_key}.mappings[{i}]: {transformer_err}")
            if rule.default is not None and rule.extension is not None:
                try:
                    _wrap_extension_value(rule.default, rule.extension, strict=True)
                except (ValueError, TypeError) as exc:
                    errors.append(
                        f"{section_key}.mappings[{i}] (target {target!r}): "
                        f"extension 'default' value cannot be encoded: {exc}"
                    )
        _collect_duplicate_target_errors(parsed, section_key, errors)
    return errors
