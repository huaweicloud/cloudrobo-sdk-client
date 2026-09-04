"""Config-driven mappers for edge/cloud adapter pipelines."""

from __future__ import annotations

import logging
from dataclasses import dataclass, field
import inspect
from typing import (
    Any,
    Callable,
    Dict,
    Iterable,
    List,
    Mapping,
    MutableMapping,
    Optional,
    Sequence,
)

from cloudrobo_r2c.core.interfaces import IValueTransformer
from cloudrobo_r2c.core.internal.helpers import assign_dotted, lookup_dotted
from cloudrobo_r2c.core.transformers import build_transformer_registry

logger = logging.getLogger(__name__)

ValueTransformerLike = IValueTransformer | Callable[..., Any]


_VALID_KEY_RE = None  # compiled lazily


def _valid_key_re():
    global _VALID_KEY_RE
    if _VALID_KEY_RE is None:
        import re

        _VALID_KEY_RE = re.compile(r"^[a-zA-Z_][a-zA-Z0-9_-]*$")
    return _VALID_KEY_RE


def _validate_dotted_path(value: str, field_name: str) -> None:
    """Validate a dotted path.

    Each dot-separated segment must be a valid key:
    starts with a letter or underscore, followed by alphanumeric,
    underscore, or hyphen characters.
    """
    if not value or not value.strip():
        raise ValueError(f"{field_name} must not be empty")
    stripped = value.strip()
    if stripped.startswith(".") or stripped.endswith("."):
        raise ValueError(
            f"{field_name} must not start or end with '.': {stripped!r}"
        )
    if ".." in stripped:
        raise ValueError(
            f"{field_name} must not contain consecutive dots: {stripped!r}"
        )
    for segment in stripped.split("."):
        if not _valid_key_re().match(segment):
            raise ValueError(
                f"Invalid key segment {segment!r} in {field_name}={stripped!r}. "
                f"Each segment must start with a letter or underscore "
                f"and contain only letters, digits, underscores, and hyphens."
            )


@dataclass(frozen=True)
class TransformSpec:
    """Single transform entry with optional runtime config."""

    name: str
    config: Any = None

    def __post_init__(self) -> None:
        if not self.name or not self.name.strip():
            raise ValueError("TransformSpec name must be a non-empty string")


_VALID_DTYPES = frozenset({
    "FLOAT32", "FLOAT64", "INT32", "INT64",
    "UINT8", "BOOL", "STRING", "BYTES",
})


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
        for i, dim in enumerate(self.shape):
            if not isinstance(dim, int) or dim < 0:
                raise ValueError(
                    f"ExtensionSpec shape[{i}] must be a non-negative int, "
                    f"got {dim!r}"
                )
        if self.mime_type and not isinstance(self.mime_type, str):
            raise ValueError(
                "ExtensionSpec mime_type must be a string, "
                f"got {type(self.mime_type).__name__!r}"
            )

    @classmethod
    def from_mapping(cls, raw: Mapping[str, Any]) -> "ExtensionSpec":
        dtype = raw.get("dtype")
        if not dtype or not isinstance(dtype, str):
            raise ValueError("extension config must include a non-empty 'dtype'")
        return cls(
            dtype=dtype.strip().upper(),
            shape=list(raw.get("shape", [])),
            mime_type=str(raw.get("mime_type", "")),
        )


@dataclass(frozen=True)
class MapperRule:
    """A single mapping rule loaded from YAML-like configuration."""

    source: Optional[str]
    target: str
    source_paths: Optional[List[str]] = None
    source_index: Optional[int] = None
    source_name_ref: Optional[str] = None
    source_names_path: Optional[str] = None
    target_index: Optional[int] = None
    slice_start: Optional[int] = None
    slice_end: Optional[int] = None
    transforms: Optional[List[TransformSpec]] = None
    required: bool = True
    default: Any = None
    use_lookup_dotted: bool = True
    use_assign_dotted: bool = True
    list_mode: Optional[str] = None  # "extend" | "append" | None
    extension: Optional[ExtensionSpec] = None

    def __post_init__(self) -> None:
        _validate_mapper_rule(self)

    @classmethod
    def from_mapping(cls, rule: Mapping[str, Any]) -> "MapperRule":
        source = _first_non_empty(
            rule,
            keys=("source_path", "source_key", "source_tensor", "source"),
        )
        source_paths = _to_string_list_or_none(rule.get("source_paths"))
        target = _first_non_empty(rule, keys=("target_key", "target_path", "target"))
        default = rule.get("default")

        if source is None and not source_paths and default is None:
            raise ValueError(
                "Mapper rule must include source_path/source_key/source_tensor/source or source_paths or default"
            )
        if target is None:
            raise ValueError("Mapper rule must include target_key/target_path/target")

        extension_raw = rule.get("extension")
        extension = None
        if extension_raw is not None:
            if not isinstance(extension_raw, Mapping):
                raise ValueError(
                    "extension config must be a mapping with 'dtype' and "
                    "optionally 'shape' and 'mime_type'"
                )
            extension = ExtensionSpec.from_mapping(extension_raw)

        return cls(
            source=str(source) if source is not None else None,
            source_paths=source_paths,
            source_index=_to_int_or_none(rule.get("source_index")),
            source_name_ref=rule.get("source_name_ref"),
            source_names_path=rule.get("source_names_path"),
            target=str(target),
            target_index=_to_int_or_none(rule.get("target_index")),
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
        )


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
        self._transformers = build_transformer_registry(transformers)
        self._validate_rules()
        self._validate_transformer_configs()

    def _validate_transformer_configs(self) -> None:
        """Call ``validate_config`` on every src/cloudrobo_r2c/core/transformers.py transform in every rule.

        Raises ``ValueError`` with a descriptive message when a
        transformer rejects its configuration.
        """
        for rule in self.rules:
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
            # Duplicate target detection
            key = (rule.target, rule.target_index)
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
            # Protobuf serialization expects bytes for image fields.
            # A mapping rule targeting images.* without a transform
            # (e.g. ndarray_to_jpeg) would pass a raw numpy array
            # through and fail at serialization time.
            target = rule.target
            if target.startswith("images.") or ".images." in target:
                if not rule.transforms:
                    raise ValueError(
                        f"Rule {_rule_source_repr(rule)} → {target!r}: "
                        f"targets under 'images.*' require an image-encoding "
                        f"transform (e.g. ndarray_to_jpeg, ndarray_to_webp). "
                        f"No transforms configured."
                    )

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
                continue

            value = self._apply_transforms(value, rule, payload)
            value = _apply_slice(value, start=rule.slice_start, end=rule.slice_end)

            # ── extension: wrap as self-describing ExtensionValue ────
            if rule.extension is not None:
                value = _wrap_extension_value(value, rule.extension)

            # ── list_mode: extend or append ───────────────────────────
            if rule.list_mode is not None:
                if not isinstance(value, list):
                    raise ValueError(
                        f"list_mode={rule.list_mode!r} requires a list value, "
                        f"got {type(value).__name__!r} for target {rule.target!r}"
                    )
                _apply_list_mode(
                    output, rule, value,
                )
                continue

            if rule.target_index is None:
                if rule.use_assign_dotted:
                    assign_dotted(output, rule.target, value)
                else:
                    output[rule.target] = value
            else:
                if rule.use_assign_dotted:
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

        return output

    def check_completeness(self, source: Mapping[str, Any]) -> list[str]:
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
        incomplete: list[str] = []
        for rule in self.rules:
            source_desc = _rule_source_repr(rule)
            has_default = rule.default is not None
            try:
                value = _resolve_rule_value(source, rule)
            except KeyError:
                if rule.required and not has_default:
                    incomplete.append(f"'{source_desc}' (missing)")
                continue
            if value is None:
                if rule.required:
                    incomplete.append(f"'{source_desc}' (None)")
                else:
                    incomplete.append(f"'{source_desc}' (None, optional)")
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
        if rule.source_name_ref not in names:
            raise KeyError(
                f"Name '{rule.source_name_ref}' not found in {names_path}: {names}"
            )
        idx = names.index(rule.source_name_ref)
        if idx >= len(value):
            raise IndexError(
                f"Name ref index {idx} out of bounds for array of length {len(value)}"
            )
        return value[idx]

    if rule.source_index is None:
        return value
    if not isinstance(value, Sequence) or isinstance(value, (str, bytes, bytearray)):
        raise ValueError("source_index can only be used with list/tuple values")
    return value[rule.source_index]


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


def _validate_mapper_indices(rule: MapperRule) -> None:
    """Validate ``source_index`` and ``target_index`` are non-negative."""
    if rule.source_index is not None and rule.source_index < 0:
        raise ValueError(
            f"source_index must be >= 0, got {rule.source_index}"
        )
    if rule.target_index is not None and rule.target_index < 0:
        raise ValueError(
            f"target_index must be >= 0, got {rule.target_index}"
        )


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
    if rule.source_paths:
        return ",".join(rule.source_paths)
    return str(rule.source)


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

    parameters = inspect.signature(transformer).parameters
    if len(parameters) >= 3:
        return transformer(value, config, context)
    if len(parameters) >= 2:
        return transformer(value, config)
    return transformer(value)


def _wrap_extension_value(value: Any, spec: ExtensionSpec) -> Dict[str, Any]:
    """Wrap a transformed value as an ExtensionValue-compatible dict.

    The dict has the same keys as the ``ExtensionValue`` dataclass, so
    that ``Observations.from_dict()`` / ``Actions.from_dict()`` can
    parse it via ``_parse_extensions()``.
    """
    import struct

    dtype = spec.dtype

    # ── already-encoded bytes ───────────────────────────────────
    if isinstance(value, (bytes, bytearray, memoryview)):
        data = bytes(value)
    # ── string ───────────────────────────────────────────────────
    elif dtype == "STRING":
        data = str(value).encode("utf-8")
    # ── numpy ndarray ────────────────────────────────────────────
    elif hasattr(value, "tobytes"):
        data = value.tobytes()
    # ── scalar numeric types ─────────────────────────────────────
    elif dtype == "FLOAT32":
        data = struct.pack("<f", float(value))
    elif dtype == "FLOAT64":
        data = struct.pack("<d", float(value))
    elif dtype == "INT32":
        data = struct.pack("<i", int(value))
    elif dtype == "INT64":
        data = struct.pack("<q", int(value))
    elif dtype == "BOOL":
        data = b"\x01" if value else b"\x00"
    # ── fallback ─────────────────────────────────────────────────
    else:
        try:
            data = bytes(value)
        except TypeError:
            raise TypeError(
                f"Cannot encode value of type {type(value).__name__} "
                f"for extension dtype={dtype!r}. "
                f"Add a transform (e.g. ndarray_to_bytes) before the "
                f"extension mapping."
            ) from None
    return {
        "dtype": dtype,
        "shape": list(spec.shape),
        "data": data,
        "mime_type": spec.mime_type,
    }


def _apply_list_mode(
    output: MutableMapping[str, Any],
    rule: MapperRule,
    value: list,
) -> None:
    """Apply list_mode extend/append to a target list."""
    target_list = _get_or_create_target_list(output, rule)

    if rule.target_index is None:
        if rule.list_mode == "extend":
            target_list.extend(value)
        else:  # append
            target_list.append(value)
    else:
        idx = rule.target_index
        if rule.list_mode == "extend":
            target_list[idx:idx] = value  # splice at position
        else:  # append
            target_list.insert(idx, value)


def _get_or_create_target_list(
    output: MutableMapping[str, Any], rule: MapperRule
) -> list:
    """Get or create the target list, supporting dotted paths."""
    if rule.use_assign_dotted:
        parts = rule.target.split(".")
        current: MutableMapping[str, Any] = output
        for part in parts[:-1]:
            node = current.get(part)
            if not isinstance(node, MutableMapping):
                node = {}
                current[part] = node
            current = node
        leaf = parts[-1]
        lst = current.get(leaf)
        if not isinstance(lst, list):
            lst = []
            current[leaf] = lst
        return lst
    else:
        lst = output.get(rule.target)
        if not isinstance(lst, list):
            lst = []
            output[rule.target] = lst
        return lst


def _assign_list_index(
    output: MutableMapping[str, Any],
    key: str,
    *,
    target_index: int,
    value: Any,
) -> None:
    if target_index < 0:
        raise ValueError("target_index must be >= 0")

    leaf_value = output.get(key)
    if not isinstance(leaf_value, list):
        leaf_value = []
        output[key] = leaf_value

    while len(leaf_value) <= target_index:
        leaf_value.append(None)
    leaf_value[target_index] = value