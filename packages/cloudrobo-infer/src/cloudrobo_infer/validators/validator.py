import functools
import inspect
import json
import re
from collections.abc import Callable
from typing import Any

from cloudrobo_core.sdk.exceptions import BadParameterError

from .rules import (
    CREATEINFERENCESERVICEREQUESTBODY_RULES,
    LISTINFERENCESERVICELOGSREQUESTBODY_RULES,
    UPDATEINFERENCESERVICEREQUESTBODY_RULES,
)

_PATTERN_CACHE: dict[str, "re.Pattern[str]"] = {}


def _compile_pattern(pattern: str) -> "re.Pattern[str]":
    compiled = _PATTERN_CACHE.get(pattern)
    if compiled is None:
        compiled = re.compile(pattern)
        _PATTERN_CACHE[pattern] = compiled
    return compiled


class InferValidator:

    def validate_create_infer_service(self, params: dict) -> list[str]:
        errors: list[str] = []
        errors.extend(self._validate_object(params, CREATEINFERENCESERVICEREQUESTBODY_RULES, "Request"))
        return errors

    def validate_update_infer_service(self, params: dict) -> list[str]:
        errors: list[str] = []
        errors.extend(self._validate_object(params, UPDATEINFERENCESERVICEREQUESTBODY_RULES, "Request"))
        return errors

    def validate_list_infer_service_logs(self, params: dict) -> list[str]:
        errors: list[str] = []
        errors.extend(self._validate_object(params, LISTINFERENCESERVICELOGSREQUESTBODY_RULES, "Request"))
        return errors

    def validate_field(self, rule: dict, value: Any, path: str = "") -> list[str]:
        """Validate a single value against a rule dict (scalar or nested object /
        array, recursing into every sub-item).  CLI thin callbacks delegate here.
        """
        if value is None:
            return []
        ftype = rule.get("type")
        if ftype == "object":
            if not isinstance(value, dict):
                return [f"{path} must be a JSON object"]
            return self._validate_object(value, rule, path)
        if ftype in ("array", "array_of_string"):
            return self._validate_array(value, rule, path, "")
        if ftype == "string":
            return self._validate_string(value, rule, path, "")
        if ftype == "integer":
            return self._validate_integer(value, rule, path, "")
        if ftype == "number":
            if isinstance(value, bool) or not isinstance(value, (int, float)):
                return [f"{path} must be a number"]
            return []
        if ftype == "boolean":
            return [] if isinstance(value, bool) else [f"{path} must be a boolean"]
        return []

    def _validate_object(self, data: dict, rules: dict, prefix: str) -> list[str]:
        errors: list[str] = []
        if not isinstance(data, dict):
            return [f"{prefix} must be a JSON object"]

        max_properties = rules.get("max_properties")
        if max_properties and len(data) > max_properties:
            errors.append(f"{prefix} exceeds max properties {max_properties}, got {len(data)}")
        min_properties = rules.get("min_properties")
        if min_properties and len(data) < min_properties:
            errors.append(f"{prefix} below min properties {min_properties}, got {len(data)}")

        for field_name in rules.get("required_fields", []):
            if field_name not in data:
                errors.append(f"{prefix}.{field_name} is required")

        for field_name, rule in (rules.get("fields") or {}).items():
            value = data.get(field_name)
            if value is None:
                if rule.get("required"):
                    errors.append(f"{prefix}.{field_name} is required")
                continue
            errors.extend(self._validate_field(field_name, value, rule, prefix))
        return errors

    def _validate_field(self, field_name: str, value: Any, rule: dict, prefix: str) -> list[str]:
        errors: list[str] = []
        path = f"{prefix}.{field_name}"
        ftype = rule.get("type")
        source = rule.get("source")
        source_suffix = f" [来源: {source}]" if source else ""

        if ftype == "string":
            errors.extend(self._validate_string(value, rule, path, source_suffix))
        elif ftype == "integer":
            errors.extend(self._validate_integer(value, rule, path, source_suffix))
        elif ftype == "boolean":
            if not isinstance(value, bool):
                errors.append(f"{path} must be a boolean{source_suffix}")
        elif ftype == "object":
            if not isinstance(value, dict):
                errors.append(f"{path} must be a JSON object{source_suffix}")
            else:
                errors.extend(self._validate_object(value, rule, path))
        elif ftype == "array":
            errors.extend(self._validate_array(value, rule, path, source_suffix))
        return errors

    def _validate_string(self, value: Any, rule: dict, path: str, source_suffix: str) -> list[str]:
        errors: list[str] = []
        if not isinstance(value, str):
            errors.append(f"{path} must be a string{source_suffix}")
            return errors
        if "min_length" in rule and len(value) < rule["min_length"]:
            errors.append(f"{path} below min length {rule['min_length']}, got {len(value)}{source_suffix}")
        if "max_length" in rule and len(value) > rule["max_length"]:
            errors.append(f"{path} exceeds max length {rule['max_length']}, got {len(value)}{source_suffix}")
        pattern = rule.get("pattern")
        if pattern and not _compile_pattern(pattern).match(value):
            errors.append(f"{path} format invalid: expected {pattern}")
        if rule.get("format") == "json" and not self._is_json(value):
            errors.append(
                f"{path} format invalid: expected JSON content"
            )
        if "enum" in rule and value not in rule["enum"]:
            errors.append(
                f"{path} must be one of: {', '.join(sorted(str(e) for e in rule['enum']))}, "
                f"got '{value}'{source_suffix}"
            )
        return errors

    @staticmethod
    def _is_json(value: str) -> bool:
        if not value.strip():
            return True
        try:
            json.loads(value)
            return True
        except (ValueError, TypeError):
            return False

    def _validate_integer(self, value: Any, rule: dict, path: str, source_suffix: str) -> list[str]:
        errors: list[str] = []
        if isinstance(value, bool) or not isinstance(value, int):
            errors.append(f"{path} must be an integer{source_suffix}")
            return errors
        if "minimum" in rule and value < rule["minimum"]:
            errors.append(f"{path} must be >= {rule['minimum']}, got {value}{source_suffix}")
        if "maximum" in rule and value > rule["maximum"]:
            errors.append(f"{path} must be <= {rule['maximum']}, got {value}{source_suffix}")
        return errors

    def _validate_array(self, value: Any, rule: dict, path: str, source_suffix: str) -> list[str]:
        errors: list[str] = []
        if not isinstance(value, list):
            errors.append(f"{path} must be an array{source_suffix}")
            return errors
        max_items = rule.get("max_items")
        if max_items and len(value) > max_items:
            errors.append(f"{path} exceeds max items {max_items}, got {len(value)}{source_suffix}")
        min_items = rule.get("min_items")
        if min_items and len(value) < min_items:
            errors.append(f"{path} below min items {min_items}, got {len(value)}{source_suffix}")

        item_fields = rule.get("item_fields")
        if not item_fields:
            return errors
        item_required = rule.get("item_required_fields", [])
        for i, item in enumerate(value):
            if not isinstance(item, dict):
                errors.append(f"{path}[{i}] must be a JSON object{source_suffix}")
                continue
            for req_field in item_required:
                if req_field not in item:
                    errors.append(f"{path}[{i}].{req_field} is required{source_suffix}")
            for k, v_rule in item_fields.items():
                iv = item.get(k)
                if iv is None:
                    if v_rule.get("required"):
                        errors.append(f"{path}[{i}].{k} is required{source_suffix}")
                    continue
                errors.extend(self._validate_field(k, iv, v_rule, f"{path}[{i}]"))
        return errors


_validator = InferValidator()


_VALIDATORS = {
    "create_infer_service": ("req", "validate_create_infer_service"),
    "update_infer_service": ("req", "validate_update_infer_service"),
    "list_infer_service_logs": ("req", "validate_list_infer_service_logs"),
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
                raise BadParameterError(errs)
            return func(self, *args, **kwargs)
        return wrapper
    return decorator
