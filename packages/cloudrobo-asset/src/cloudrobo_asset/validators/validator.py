import functools
import inspect
import json
from typing import Any, Callable, Dict, List, Optional

from .rules import (
    ASSET_FIELD_RULES,
    EXT_METADATA_RULES,
    HYPER_PARAMS_VALUE_PATTERN,
    SKILL_MODEL_TYPES,
    TYPE_SUBTYPE_MAP,
    VALID_TYPES,
    VERSION_FIELD_RULES,
)


class ValidationError(ValueError):
    def __init__(self, errors: List[str]):
        self.errors = errors
        super().__init__("; ".join(errors))


class AssetValidator:

    def validate_create_asset(self, params: dict) -> List[str]:
        errors: List[str] = []
        errors.extend(self._validate_type_subtype(params))
        errors.extend(self._validate_top_fields(params, "create"))
        asset_type = params.get("type")
        if asset_type and asset_type != "image" and params.get("name") is None:
            errors.append("name is required when type is not 'image'")
        if params.get("parent_asset_version_id") and not params.get("generation_method"):
            errors.append("generation_method is required when parent_asset_version_id is provided")
        ext_metadata = params.get("ext_metadata")
        if asset_type and asset_type in EXT_METADATA_RULES:
            rules = EXT_METADATA_RULES[asset_type]
            sub_type = params.get("sub_type")
            required_fields = list(rules.get("required_fields", []))
            if asset_type == "simulation" and sub_type:
                sub_rules = rules.get("sub_type_rules", {}).get(sub_type, {})
                required_fields.extend(sub_rules.get("required_fields", []))
            if required_fields and ext_metadata is None:
                type_desc = f"'{asset_type}'" + (f"/{sub_type}" if sub_type else "")
                errors.append(
                    f"ext_metadata is required for type {type_desc} "
                    f"(missing required fields: {', '.join(required_fields)})"
                )
            elif ext_metadata is not None:
                errors.extend(self.validate_ext_metadata(asset_type, sub_type, ext_metadata))
        return errors

    def validate_update_asset(self, params: dict) -> List[str]:
        errors: List[str] = []
        if "type" in params and params["type"] is not None:
            errors.append("type cannot be modified on update")
        if "sub_type" in params and params["sub_type"] is not None:
            errors.append("sub_type cannot be modified on update")
        errors.extend(self._validate_top_fields(params, "update"))
        return errors

    def validate_create_version(self, params: dict) -> List[str]:
        errors: List[str] = []
        errors.extend(self._validate_version_fields(params, "create"))
        if params.get("parent_asset_version_id") and not params.get("generation_method"):
            errors.append("generation_method is required when parent_asset_version_id is provided")
        return errors

    def validate_update_version(self, params: dict) -> List[str]:
        errors: List[str] = []
        errors.extend(self._validate_version_fields(params, "update"))
        return errors

    def validate_ext_metadata(self, asset_type: str, sub_type: Optional[str], ext_metadata: Any) -> List[str]:
        errors: List[str] = []
        if asset_type not in EXT_METADATA_RULES:
            return errors
        if not isinstance(ext_metadata, dict):
            errors.append("ext_metadata must be a JSON object")
            return errors

        rules = EXT_METADATA_RULES[asset_type]

        for field_name in rules.get("required_fields", []):
            if field_name not in ext_metadata:
                errors.append(f"ext_metadata.{field_name} is required for type '{asset_type}'")

        field_rules = rules.get("fields", {})
        errors.extend(self._validate_ext_fields(ext_metadata, field_rules, "ext_metadata"))

        if asset_type == "simulation" and sub_type:
            sub_rules = rules.get("sub_type_rules", {}).get(sub_type)
            if sub_rules:
                for field_name in sub_rules.get("required_fields", []):
                    if field_name not in ext_metadata:
                        errors.append(f"ext_metadata.{field_name} is required for type '{asset_type}/{sub_type}'")
                errors.extend(self._validate_ext_fields(ext_metadata, sub_rules.get("fields", {}), "ext_metadata"))

        if asset_type == "algorithm":
            errors.extend(self._validate_algorithm_conditions(ext_metadata))

        if asset_type == "model":
            errors.extend(self._validate_model_conditions(ext_metadata))

        return errors

    def _validate_type_subtype(self, params: dict) -> List[str]:
        errors: List[str] = []
        asset_type = params.get("type")
        sub_type = params.get("sub_type")

        if not asset_type:
            errors.append("type is required")
            return errors

        if asset_type not in VALID_TYPES:
            errors.append(f"type must be one of: {', '.join(sorted(VALID_TYPES))}, got '{asset_type}'")
            return errors

        valid_subtypes = TYPE_SUBTYPE_MAP.get(asset_type, set())

        if asset_type == "simulation" and not sub_type:
            errors.append("sub_type is required when type is 'simulation'")
        elif sub_type and valid_subtypes and sub_type not in valid_subtypes:
            errors.append(f"sub_type must be one of: {', '.join(sorted(valid_subtypes))} for type '{asset_type}', got '{sub_type}'")
        elif sub_type and not valid_subtypes:
            errors.append(f"sub_type is not allowed for type '{asset_type}'")

        return errors

    def _validate_top_fields(self, params: dict, mode: str) -> List[str]:
        errors: List[str] = []
        for field_name, rule in ASSET_FIELD_RULES.items():
            value = params.get(field_name)
            if value is None:
                if rule.get("required_on") == mode:
                    errors.append(f"{field_name} is required")
                continue

            if rule.get("forbidden_on") == mode:
                errors.append(f"{field_name} cannot be modified on update")
                continue

            if "pattern" in rule:
                if not isinstance(value, str) or not rule["pattern"].match(value):
                    errors.append(f"{field_name} format invalid: expected {rule.get('pattern_desc', 'pattern match')}")
            elif "patterns" in rule:
                if not isinstance(value, str) or not any(p.match(value) for p in rule["patterns"]):
                    errors.append(f"{field_name} format invalid: expected {rule.get('pattern_desc', 'pattern match')}")
            if "max_length" in rule and isinstance(value, str) and len(value) > rule["max_length"]:
                errors.append(f"{field_name} exceeds max length {rule['max_length']}, got {len(value)}")
            if "enum" in rule and value not in rule["enum"]:
                errors.append(f"{field_name} must be one of: {', '.join(sorted(rule['enum']))}, got '{value}'")
            if field_name == "tags" and isinstance(value, list):
                if len(value) > rule.get("max_items", 100):
                    errors.append(f"tags exceeds max items {rule['max_items']}, got {len(value)}")
                for i, tag in enumerate(value):
                    if not isinstance(tag, str) or not rule["item_pattern"].match(tag):
                        errors.append(f"tags[{i}] format invalid: {rule.get('item_pattern_desc', '')}")
        return errors

    def _validate_version_fields(self, params: dict, mode: str) -> List[str]:
        errors: List[str] = []
        for field_name, rule in VERSION_FIELD_RULES.items():
            value = params.get(field_name)
            if value is None:
                continue

            if rule.get("forbidden_on") == mode:
                errors.append(f"{field_name} cannot be modified on update")
                continue

            if "pattern" in rule:
                if not isinstance(value, str) or not rule["pattern"].match(value):
                    errors.append(f"{field_name} format invalid: expected {rule.get('pattern_desc', 'pattern match')}")
            elif "patterns" in rule:
                if not isinstance(value, str) or not any(p.match(value) for p in rule["patterns"]):
                    errors.append(f"{field_name} format invalid: expected {rule.get('pattern_desc', 'pattern match')}")
            if "max_length" in rule and isinstance(value, str) and len(value) > rule["max_length"]:
                errors.append(f"{field_name} exceeds max length {rule['max_length']}, got {len(value)}")
            if "enum" in rule and value not in rule["enum"]:
                errors.append(f"{field_name} must be one of: {', '.join(sorted(rule['enum']))}, got '{value}'")
        return errors

    def _validate_ext_fields(self, data: dict, field_rules: dict, prefix: str) -> List[str]:
        errors: List[str] = []
        for field_name, rule in field_rules.items():
            value = data.get(field_name)
            if value is None:
                if rule.get("required"):
                    errors.append(f"{prefix}.{field_name} is required")
                continue

            ftype = rule.get("type")

            if ftype == "string":
                if not isinstance(value, str):
                    errors.append(f"{prefix}.{field_name} must be a string")
                elif "pattern" in rule and not rule["pattern"].match(value):
                    errors.append(f"{prefix}.{field_name} format invalid: {rule.get('pattern_desc', '')}")
                if "max_length" in rule and isinstance(value, str) and len(value) > rule["max_length"]:
                    errors.append(f"{prefix}.{field_name} exceeds max length {rule['max_length']}")
                if "enum" in rule and value not in rule["enum"]:
                    errors.append(f"{prefix}.{field_name} must be one of: {', '.join(sorted(rule['enum']))}")

            elif ftype == "boolean":
                if not isinstance(value, bool):
                    errors.append(f"{prefix}.{field_name} must be a boolean")

            elif ftype == "object":
                if not isinstance(value, dict):
                    errors.append(f"{prefix}.{field_name} must be a JSON object")
                else:
                    obj_rules = rule.get("fields", {})
                    errors.extend(self._validate_ext_fields(value, obj_rules, f"{prefix}.{field_name}"))
                    for req_field in rule.get("required_fields", []):
                        if req_field not in value:
                            errors.append(f"{prefix}.{field_name}.{req_field} is required")

            elif ftype == "array":
                if not isinstance(value, list):
                    errors.append(f"{prefix}.{field_name} must be an array")
                else:
                    max_items = rule.get("max_items")
                    if max_items and len(value) > max_items:
                        errors.append(f"{prefix}.{field_name} exceeds max items {max_items}, got {len(value)}")
                    item_fields = rule.get("item_fields")
                    if item_fields:
                        dup_field = rule.get("no_duplicate")
                        seen = set()
                        for i, item in enumerate(value):
                            if not isinstance(item, dict):
                                errors.append(f"{prefix}.{field_name}[{i}] must be a JSON object")
                                continue
                            for k, v_rule in item_fields.items():
                                iv = item.get(k)
                                if iv is None:
                                    if v_rule.get("required"):
                                        errors.append(f"{prefix}.{field_name}[{i}].{k} is required")
                                    continue
                                if v_rule.get("type") == "string" and not isinstance(iv, str):
                                    errors.append(f"{prefix}.{field_name}[{i}].{k} must be a string")
                                elif v_rule.get("type") == "string" and "pattern" in v_rule and not v_rule["pattern"].match(iv):
                                    errors.append(f"{prefix}.{field_name}[{i}].{k} format invalid: {v_rule.get('pattern_desc', '')}")
                                elif v_rule.get("type") == "string" and "enum" in v_rule and iv not in v_rule["enum"]:
                                    errors.append(f"{prefix}.{field_name}[{i}].{k} must be one of: {', '.join(sorted(v_rule['enum']))}")
                                if v_rule.get("type") == "array_of_string":
                                    if not isinstance(iv, list):
                                        errors.append(f"{prefix}.{field_name}[{i}].{k} must be an array of strings")
                                    else:
                                        for vi, ve in enumerate(iv):
                                            if not isinstance(ve, str):
                                                errors.append(f"{prefix}.{field_name}[{i}].{k}[{vi}] must be a string")
                                            elif "enum" in v_rule and ve not in v_rule["enum"]:
                                                errors.append(f"{prefix}.{field_name}[{i}].{k}[{vi}] must be one of: {', '.join(sorted(v_rule['enum']))}")
                                if v_rule.get("type") == "object" and isinstance(iv, dict):
                                    obj_r = v_rule.get("fields", {})
                                    errors.extend(self._validate_ext_fields(iv, obj_r, f"{prefix}.{field_name}[{i}].{k}"))
                                    for rf in v_rule.get("required_fields", []):
                                        if rf not in iv:
                                            errors.append(f"{prefix}.{field_name}[{i}].{k}.{rf} is required")
                                if "max_length" in v_rule and isinstance(iv, str) and len(iv) > v_rule["max_length"]:
                                    errors.append(f"{prefix}.{field_name}[{i}].{k} exceeds max length {v_rule['max_length']}")
                            if dup_field:
                                dv = item.get(dup_field)
                                if dv is not None:
                                    if dv in seen:
                                        errors.append(f"{prefix}.{field_name} has duplicate {dup_field}: '{dv}'")
                                    seen.add(dv)

            elif ftype == "array_of_string":
                if not isinstance(value, list):
                    errors.append(f"{prefix}.{field_name} must be an array of strings")
                else:
                    for i, item in enumerate(value):
                        if not isinstance(item, str):
                            errors.append(f"{prefix}.{field_name}[{i}] must be a string")
                        elif "enum" in rule and item not in rule["enum"]:
                            errors.append(f"{prefix}.{field_name}[{i}] must be one of: {', '.join(sorted(rule['enum']))}")

        return errors

    def _validate_algorithm_conditions(self, ext_metadata: dict) -> List[str]:
        errors: List[str] = []
        boot_file = ext_metadata.get("boot_file")
        code_dir = ext_metadata.get("code_dir")
        if boot_file is not None:
            if code_dir is None:
                errors.append("ext_metadata.code_dir is required when boot_file is provided")
            elif not boot_file.startswith(code_dir.rstrip("/") + "/"):
                errors.append("ext_metadata.boot_file must be under code_dir")
            if not boot_file.startswith("obs://"):
                errors.append("ext_metadata.boot_file must start with obs://")
            if not boot_file.endswith(".py"):
                errors.append("ext_metadata.boot_file must be a .py file")
        engine = ext_metadata.get("engine")
        if isinstance(engine, dict) and engine.get("image_source") == "preset" and not code_dir:
            errors.append("ext_metadata.code_dir is required when engine.image_source is 'preset'")
        errors.extend(self._validate_hyperparams_default_type(ext_metadata))
        return errors

    def _validate_hyperparams_default_type(self, ext_metadata: dict) -> List[str]:
        errors: List[str] = []
        hyperparams = ext_metadata.get("hyperparams")
        if not isinstance(hyperparams, list):
            return errors
        for i, param in enumerate(hyperparams):
            if not isinstance(param, dict):
                continue
            default = param.get("default")
            constraint = param.get("constraint")
            if default is None or not isinstance(constraint, dict):
                continue
            constraint_type = constraint.get("type")
            if not isinstance(default, str) or not isinstance(constraint_type, str):
                continue
            prefix = f"ext_metadata.hyperparams[{i}].default"
            if constraint_type == "Integer":
                try:
                    int(default)
                except (ValueError, OverflowError):
                    errors.append(f"{prefix} must be an integer for constraint.type 'Integer'")
            elif constraint_type == "Float":
                try:
                    float(default)
                except (ValueError, OverflowError):
                    errors.append(f"{prefix} must be a float for constraint.type 'Float'")
            elif constraint_type == "Boolean":
                if default.lower() not in ("true", "false"):
                    errors.append(f"{prefix} must be 'true' or 'false' for constraint.type 'Boolean'")
            else:
                if not HYPER_PARAMS_VALUE_PATTERN.match(default):
                    try:
                        parsed = json.loads(default)
                        if not isinstance(parsed, (dict, list)):
                            errors.append(f"{prefix} must match pattern or be valid JSON for constraint.type '{constraint_type}'")
                    except (ValueError):
                        errors.append(f"{prefix} must match pattern or be valid JSON for constraint.type '{constraint_type}'")
        return errors

    def _validate_model_conditions(self, ext_metadata: dict) -> List[str]:
        errors: List[str] = []
        model_type = ext_metadata.get("model_type")
        if model_type and model_type not in SKILL_MODEL_TYPES:
            if "skills" in ext_metadata:
                errors.append(f"ext_metadata.skills is only supported for model_type: {', '.join(sorted(SKILL_MODEL_TYPES))}, got '{model_type}'")
            if "strict" in ext_metadata:
                errors.append(f"ext_metadata.strict is only supported for model_type: {', '.join(sorted(SKILL_MODEL_TYPES))}, got '{model_type}'")
        return errors


_validator = AssetValidator()


_VALIDATORS = {
    "create_asset": ("req", "validate_create_asset"),
    "update_asset": ("req", "validate_update_asset"),
    "create_version": ("req", "validate_create_version"),
    "update_version": ("req", "validate_update_version"),
}


def validate_params(method_name: str):
    def decorator(func: Callable) -> Callable:
        if method_name not in _VALIDATORS:
            raise ValueError(f"Unknown validate_params method_name: '{method_name}'")

        req_param_name, validator_method = _VALIDATORS[method_name]
        sig = inspect.signature(func)
        if req_param_name not in sig.parameters:
            raise ValueError(
                f"@validate_params('{method_name}') requires parameter '{req_param_name}' "
                f"in {func.__qualname__}{sig}, but '{req_param_name}' not found"
            )
        req_param_idx = list(sig.parameters.keys()).index(req_param_name) - 1

        @functools.wraps(func)
        def wrapper(self, *args, **kwargs):
            req = None
            if req_param_name in kwargs:
                req = kwargs[req_param_name]
            elif req_param_idx >= 0 and req_param_idx < len(args):
                req = args[req_param_idx]

            if req is None:
                req = {}

            errs = getattr(_validator, validator_method)(req)
            if errs:
                raise ValidationError(errs)
            return func(self, *args, **kwargs)
        return wrapper
    return decorator
