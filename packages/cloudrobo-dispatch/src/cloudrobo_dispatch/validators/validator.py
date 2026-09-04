import functools
import inspect
import re
from typing import Any, Callable, Dict, List

from cloudrobo_core.sdk.exceptions import BadParameterError

from .rules import CREATEDISPATCHERTASKREQUESTBODY_RULES


class DispatchValidator:
    """通用递归校验引擎：消费 gen_schemas.py 生成的 rules dict。"""

    def validate_create_dispatcher_task(self, params: dict) -> List[str]:
        errors: List[str] = []
        errors.extend(
            self._validate_object(params, CREATEDISPATCHERTASKREQUESTBODY_RULES, "body")
        )
        return errors

    def validate_field(
        self, rule: Dict[str, Any], value: Any, path: str = ""
    ) -> List[str]:
        """Validate a single value against a rule dict (scalar or nested object /
        array, recursing into every sub-item).  CLI thin callbacks delegate here.
        """
        if value is None:
            return []
        return self._validate_field(value, rule, path)

    def _validate_object(self, data: Any, rule: Dict[str, Any], path: str) -> List[str]:
        errors: List[str] = []
        if not isinstance(data, dict):
            errors.append(f"{path} must be a JSON object")
            return errors

        max_properties = rule.get("max_properties")
        if max_properties and len(data) > max_properties:
            errors.append(f"{path} exceeds max properties {max_properties}, got {len(data)}")
        min_properties = rule.get("min_properties")
        if min_properties and len(data) < min_properties:
            errors.append(f"{path} below min properties {min_properties}, got {len(data)}")

        for req_field in rule.get("required_fields", []):
            if req_field not in data:
                errors.append(f"{path}.{req_field} is required")

        for field, fr in (rule.get("fields") or {}).items():
            value = data.get(field)
            if value is None:
                if fr.get("required"):
                    errors.append(f"{path}.{field} is required")
                continue
            errors.extend(self._validate_field(value, fr, f"{path}.{field}"))
        return errors

    def _validate_field(self, value: Any, rule: Dict[str, Any], path: str) -> List[str]:
        errors: List[str] = []
        ftype = rule.get("type")

        if ftype == "string":
            if not isinstance(value, str):
                errors.append(f"{path} must be a string")
                return errors
            if "min_length" in rule and len(value) < rule["min_length"]:
                errors.append(
                    f"{path} must be at least {rule['min_length']} chars, got {len(value)}"
                )
            if "max_length" in rule and len(value) > rule["max_length"]:
                errors.append(
                    f"{path} exceeds max length {rule['max_length']}, got {len(value)}"
                )
            if "enum" in rule and value not in rule["enum"]:
                errors.append(
                    f"{path} must be one of: {', '.join(sorted(rule['enum']))}, got '{value}'"
                )
            if "pattern" in rule and not re.match(rule["pattern"], value):
                errors.append(
                    f"{path} format invalid: {rule['pattern']}"
                )

        elif ftype == "integer":
            if isinstance(value, bool) or not isinstance(value, int):
                errors.append(f"{path} must be an integer")
                return errors
            if "minimum" in rule and value < rule["minimum"]:
                errors.append(f"{path} must be >= {rule['minimum']}, got {value}")
            if "maximum" in rule and value > rule["maximum"]:
                errors.append(f"{path} must be <= {rule['maximum']}, got {value}")

        elif ftype == "boolean":
            if not isinstance(value, bool):
                errors.append(f"{path} must be a boolean")

        elif ftype == "object":
            if not isinstance(value, dict):
                errors.append(f"{path} must be a JSON object")
            else:
                errors.extend(self._validate_object(value, rule, path))

        elif ftype in ("array", "array_of_string"):
            if not isinstance(value, list):
                errors.append(f"{path} must be an array")
                return errors
            if "max_items" in rule and len(value) > rule["max_items"]:
                errors.append(f"{path} exceeds max items {rule['max_items']}, got {len(value)}")
            if "min_items" in rule and len(value) < rule["min_items"]:
                errors.append(f"{path} below min items {rule['min_items']}, got {len(value)}")
            if ftype == "array_of_string":
                for i, item in enumerate(value):
                    if not isinstance(item, str):
                        errors.append(f"{path}[{i}] must be a string")
                    elif "enum" in rule and item not in rule["enum"]:
                        errors.append(
                            f"{path}[{i}] must be one of: {', '.join(sorted(rule['enum']))}"
                        )
            item_fields = rule.get("item_fields")
            if item_fields:
                for i, item in enumerate(value):
                    if not isinstance(item, dict):
                        errors.append(f"{path}[{i}] must be a JSON object")
                        continue
                    for k, vr in item_fields.items():
                        iv = item.get(k)
                        if iv is None:
                            if vr.get("required"):
                                errors.append(f"{path}[{i}].{k} is required")
                            continue
                        errors.extend(self._validate_field(iv, vr, f"{path}[{i}].{k}"))
        return errors


_validator = DispatchValidator()


_VALIDATORS = {
    "create_dispatcher_task": ("req", "validate_create_dispatcher_task"),
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
