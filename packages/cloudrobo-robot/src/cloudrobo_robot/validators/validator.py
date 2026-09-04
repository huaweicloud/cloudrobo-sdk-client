import functools
import inspect
import re
from typing import Any, Callable, List

from .rules import (
    CREATEROBOTREQUESTBODY_RULES,
    EXPORTROBOTCERTIFICATEREQUESTBODY_RULES,
    UPDATEROBOTREQUESTBODY_RULES,
)
from cloudrobo_core.sdk.exceptions import BadParameterError


_SCHEMA_RULES = {
    "CreateRobotRequestBody": CREATEROBOTREQUESTBODY_RULES,
    "UpdateRobotRequestBody": UPDATEROBOTREQUESTBODY_RULES,
    "ExportRobotCertificateRequestBody": EXPORTROBOTCERTIFICATEREQUESTBODY_RULES,
}


class RobotValidator:

    def validate(self, schema_name: str, params: dict) -> List[str]:
        rule = _SCHEMA_RULES.get(schema_name)
        if not rule:
            return [f"unknown schema rule: '{schema_name}'"]
        if rule.get("type") != "object":
            return [f"{schema_name} must be a JSON object"]
        return self._validate_object(rule, params)

    def validate_create_robot(self, params: dict) -> List[str]:
        return self.validate("CreateRobotRequestBody", params)

    def validate_update_robot(self, params: dict) -> List[str]:
        return self.validate("UpdateRobotRequestBody", params)

    def validate_export_robot_certificate(self, params: dict) -> List[str]:
        return self.validate("ExportRobotCertificateRequestBody", params)

    def validate_field(self, rule: dict, value: Any, path: str = "") -> List[str]:
        """Validate a single value against a rule dict (scalar or nested object /
        array, recursing into every sub-item).  CLI thin callbacks delegate here.
        """
        if value is None:
            return []
        return self._validate_field(path, value, rule)

    def _validate_object(self, rule: dict, data: Any, prefix: str = "") -> List[str]:
        errors: List[str] = []
        if not isinstance(data, dict):
            errors.append(f"{prefix or rule.get('source_doc') or 'body'} 必须为 JSON 对象")
            return errors

        if rule.get("max_properties") is not None and len(data) > rule["max_properties"]:
            errors.append(f"{prefix or 'body'} 键数量不能超过 {rule['max_properties']}，当前 {len(data)}")
        if rule.get("min_properties") is not None and len(data) < rule["min_properties"]:
            errors.append(f"{prefix or 'body'} 键数量不能少于 {rule['min_properties']}，当前 {len(data)}")

        field_rules = rule.get("fields") or {}
        for req in rule.get("required_fields", []):
            if req not in data or data[req] is None:
                src = field_rules.get(req, {}).get("source", req)
                errors.append(f"{src} 必填字段缺失 ({req} is required)")
        for field, fr in field_rules.items():
            if field not in data or data[field] is None:
                continue
            errors.extend(self._validate_field(field, data[field], fr))
        return errors

    def _validate_field(self, name: str, value: Any, rule: dict) -> List[str]:
        errors: List[str] = []
        src = rule.get("source", name)
        ftype = rule.get("type")

        if ftype == "string":
            errors.extend(self._check_string(src, value, rule))
        elif ftype == "integer":
            errors.extend(self._check_number(src, value, rule, integer=True))
        elif ftype == "number":
            errors.extend(self._check_number(src, value, rule, integer=False))
        elif ftype == "boolean":
            if not isinstance(value, bool):
                errors.append(f"{src} 必须为布尔值 (true/false)")
        elif ftype == "object":
            if not isinstance(value, dict):
                errors.append(f"{src} 必须为 JSON 对象")
            else:
                errors.extend(self._validate_object(rule, value, prefix=src))
        elif ftype in ("array", "array_of_string"):
            errors.extend(self._check_array(src, value, rule))
        return errors

    def _check_string(self, src: str, value: Any, rule: dict) -> List[str]:
        errors: List[str] = []
        if not isinstance(value, str):
            errors.append(f"{src} 必须为字符串")
            return errors
        if rule.get("min_length") is not None and len(value) < rule["min_length"]:
            errors.append(f"{src} 长度不能小于 {rule['min_length']}，当前 {len(value)}")
        if rule.get("max_length") is not None and len(value) > rule["max_length"]:
            errors.append(f"{src} 长度不能超过 {rule['max_length']}，当前 {len(value)}")
        if rule.get("enum") and value not in rule["enum"]:
            errors.append(
                f"{src} 非法枚举值: '{value}'，合法值: {', '.join(sorted(rule['enum']))}"
            )
        if rule.get("pattern") and not re.match(rule["pattern"], value):
            errors.append(f"{src} 不符合格式规则：{rule['pattern']}")
        return errors

    def _check_number(
        self, src: str, value: Any, rule: dict, integer: bool
    ) -> List[str]:
        errors: List[str] = []
        if integer and isinstance(value, bool):
            errors.append(f"{src} 必须为整数")
            return errors
        if not isinstance(value, (int, float)) or (integer and not isinstance(value, int)):
            errors.append(f"{src} 必须为整数" if integer else f"{src} 必须为数字")
            return errors
        if rule.get("minimum") is not None and value < rule["minimum"]:
            errors.append(f"{src} 不能小于 {rule['minimum']}，当前 {value}")
        if rule.get("maximum") is not None and value > rule["maximum"]:
            errors.append(f"{src} 不能大于 {rule['maximum']}，当前 {value}")
        return errors

    def _check_array(self, src: str, value: Any, rule: dict) -> List[str]:
        errors: List[str] = []
        if not isinstance(value, list):
            errors.append(f"{src} 必须为数组")
            return errors
        if rule.get("max_items") is not None and len(value) > rule["max_items"]:
            errors.append(f"{src} 元素个数不能超过 {rule['max_items']}，当前 {len(value)}")
        if rule.get("min_items") is not None and len(value) < rule["min_items"]:
            errors.append(f"{src} 元素个数不能少于 {rule['min_items']}，当前 {len(value)}")
        item_fields = rule.get("item_fields")
        item_type = rule.get("item_type")
        if item_type == "string":
            for i, item in enumerate(value):
                if not isinstance(item, str):
                    errors.append(f"{src}[{i}] 必须为字符串")
                elif rule.get("item_enum") and item not in rule["item_enum"]:
                    errors.append(f"{src}[{i}] 非法枚举值: '{item}'")
                elif rule.get("item_pattern") and not re.match(rule["item_pattern"], item):
                    errors.append(f"{src}[{i}] 不符合格式规则")
        elif item_fields:
            for i, item in enumerate(value):
                if not isinstance(item, dict):
                    errors.append(f"{src}[{i}] 必须为 JSON 对象")
                    continue
                errors.extend(
                    self._validate_object(
                        {
                            "fields": item_fields,
                            "required_fields": rule.get("item_required_fields", []),
                        },
                        item,
                        prefix=f"{src}[{i}]",
                    )
                )
        return errors


_validator = RobotValidator()


_VALIDATORS = {
    "create_robot": ("req", "validate_create_robot"),
    "update_robot": ("req", "validate_update_robot"),
    "export_robot_certificate": ("req", "validate_export_robot_certificate"),
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
