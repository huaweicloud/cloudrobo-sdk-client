import pytest
from cloudrobo_robot.validators import RobotValidator, validate_params
from cloudrobo_core.sdk.exceptions import BadParameterError


@pytest.fixture
def validator():
    return RobotValidator()


VALID_CREATE = {
    "name": "arm-01",
    "type": "ARM",
    "manufacturer": "hms",
    "robot_model": "model-x",
    "workspace_id": "ws-1",
}


def _errors(validator, method, params):
    return getattr(validator, method)(params)


class TestCreateRobotValidator:
    def test_valid_body_passes(self, validator):
        assert _errors(validator, "validate_create_robot", VALID_CREATE) == []

    def test_missing_required_field(self, validator):
        req = dict(VALID_CREATE)
        req.pop("robot_model")
        errs = _errors(validator, "validate_create_robot", req)
        assert any("robot_model" in e and "必填" in e for e in errs)

    def test_missing_multiple_required(self, validator):
        errs = _errors(validator, "validate_create_robot", {})
        joined = "; ".join(errs)
        for field in ("manufacturer", "name", "robot_model", "type", "workspace_id"):
            assert field in joined

    def test_type_error_non_string_name(self, validator):
        req = dict(VALID_CREATE, name=123)
        assert any("必须为字符串" in e for e in _errors(validator, "validate_create_robot", req))

    def test_illegal_enum(self, validator):
        req = dict(VALID_CREATE, type="FLYING")
        errs = _errors(validator, "validate_create_robot", req)
        assert any("非法枚举值" in e and "FLYING" in e for e in errs)

    def test_name_too_short_below_min_length(self, validator):
        req = dict(VALID_CREATE, name="ab")
        errs = _errors(validator, "validate_create_robot", req)
        assert any("长度不能小于 3" in e for e in errs)

    def test_name_too_long_over_max_length(self, validator):
        req = dict(VALID_CREATE, name="x" * 65)
        errs = _errors(validator, "validate_create_robot", req)
        assert any("长度不能超过 64" in e for e in errs)

    def test_description_over_max_length(self, validator):
        req = dict(VALID_CREATE, description="d" * 513)
        errs = _errors(validator, "validate_create_robot", req)
        assert any("长度不能超过 512" in e for e in errs)

    def test_wrong_top_level_type(self, validator):
        errs = _errors(validator, "validate_create_robot", ["not", "a", "dict"])
        assert errs and "必须为 JSON 对象" in errs[0]

    def test_non_object_body_field(self, validator):
        req = dict(VALID_CREATE, description=["not", "a", "string"])
        errs = _errors(validator, "validate_create_robot", req)
        assert any("description" in e and "必须为字符串" in e for e in errs)


class TestUpdateRobotValidator:
    def test_empty_body_passes(self, validator):
        assert _errors(validator, "validate_update_robot", {"workspace_id": "ws-1"}) == []

    def test_valid_name_passes(self, validator):
        assert _errors(validator, "validate_update_robot", {"workspace_id": "ws-1", "name": "new-name"}) == []

    def test_illegal_name_pattern(self, validator):
        errs = _errors(validator, "validate_update_robot", {"workspace_id": "ws-1", "name": "!! invalid !!"})
        assert any("name" in e and ("格式" in e or "相符" in e) for e in errs)


class TestExportCertificateValidator:
    def test_empty_body_passes(self, validator):
        assert _errors(validator, "validate_export_robot_certificate", {}) == []

    def test_password_over_max_length(self, validator):
        errs = _errors(validator, "validate_export_robot_certificate", {"password": "p" * 33})
        assert any("不能超过 32" in e for e in errs)


class TestValidateParamsDecorator:
    def test_raises_validation_error_on_invalid(self):
        class Fake:
            @validate_params("create_robot")
            def create_robot(self, req: dict):
                return "called"

        with pytest.raises(BadParameterError) as ei:
            Fake().create_robot({})
        assert "manufacturer" in str(ei.value)

    def test_passes_valid_body_to_method(self):
        class Fake:
            @validate_params("create_robot")
            def create_robot(self, req: dict):
                return req

        assert Fake().create_robot(VALID_CREATE) == VALID_CREATE

    def test_unknown_method_name_raises(self):
        with pytest.raises(ValueError):

            class Fake:
                @validate_params("nope")
                def f(self, req: dict):
                    return req

    def test_missing_req_param_raises(self):
        with pytest.raises(ValueError):

            class Fake:
                @validate_params("create_robot")
                def f(self, other):
                    return other


class TestSourceMetadata:
    def test_error_reports_qualified_source(self, validator):
        errs = _errors(validator, "validate_create_robot", dict(VALID_CREATE, type="BAD"))
        joined = "; ".join(errs)
        assert "CreateRobotRequestBody.type" in joined


class TestContainerCardinality:
    """min_items / max_items / min_properties / max_properties consumption via
    the public `validate_field` engine used by CLI thin callbacks."""

    def test_array_below_min_items(self, validator):
        rule = {"type": "array", "min_items": 2, "max_items": 3}
        errs = validator.validate_field(rule, ["a"], "tags")
        assert any("元素个数不能少于 2" in e for e in errs)

    def test_array_within_min_items_passes(self, validator):
        rule = {"type": "array", "min_items": 2, "max_items": 3}
        assert validator.validate_field(rule, ["a", "b"], "tags") == []

    def test_array_above_max_items(self, validator):
        rule = {"type": "array", "min_items": 1, "max_items": 3}
        errs = validator.validate_field(rule, ["a", "b", "c", "d"], "tags")
        assert any("元素个数不能超过 3" in e for e in errs)

    def test_object_below_min_properties(self, validator):
        rule = {"type": "object", "min_properties": 2, "max_properties": 3}
        errs = validator.validate_field(rule, {"a": 1}, "cfg")
        assert any("键数量不能少于 2" in e for e in errs)

    def test_object_above_max_properties(self, validator):
        rule = {"type": "object", "min_properties": 1, "max_properties": 2}
        errs = validator.validate_field(rule, {"a": 1, "b": 2, "c": 3}, "cfg")
        assert any("键数量不能超过 2" in e for e in errs)

    def test_object_within_bounds_passes(self, validator):
        rule = {"type": "object", "min_properties": 1, "max_properties": 3}
        assert validator.validate_field(rule, {"a": 1, "b": 2}, "cfg") == []
