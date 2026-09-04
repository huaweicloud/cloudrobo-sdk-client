import pytest
from cloudrobo_core.sdk.exceptions import BadParameterError
from cloudrobo_dispatch.validators import validate_params
from cloudrobo_dispatch.validators.validator import DispatchValidator

# 合法请求体样例
VALID_REQ = {
    "name": "pick-red-cube",
    "task": "pick up the red cube and place it in the bin",
    "constraints": {
        "model": {"exec_model_id": "ext_aaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaa"},
        "robot_id": "1234567890abcdef1234567890abcdef",
        "exec_constraints": {"max_iter_num": 100, "max_run_time": 10},
    },
}


def _errors(params):
    return DispatchValidator().validate_create_dispatcher_task(params)


def test_valid_req_passes():
    assert _errors(VALID_REQ) == []


def test_required_top_fields_missing():
    errs = _errors({})
    joined = "; ".join(errs)
    assert "name is required" in joined
    assert "task is required" in joined
    assert "constraints is required" in joined


def test_constraints_required_fields_missing():
    req = {
        "name": "n",
        "task": "t",
        "constraints": {"model": {"exec_model_id": "m"}},
    }
    errs = _errors(req)
    assert any("constraints.robot_id is required" in e for e in errs)


def test_exec_constraints_max_iter_num_out_of_range_high():
    req = {
        "name": "n",
        "task": "t",
        "constraints": {
            "model": {"exec_model_id": "m"},
            "robot_id": "r",
            "exec_constraints": {"max_iter_num": 300001, "max_run_time": 10},
        },
    }
    errs = _errors(req)
    assert any("max_iter_num" in e and "300000" in e for e in errs)


def test_exec_constraints_max_run_time_out_of_range_high():
    req = {
        "name": "n",
        "task": "t",
        "constraints": {
            "model": {"exec_model_id": "m"},
            "robot_id": "r",
            "exec_constraints": {"max_iter_num": 100, "max_run_time": 301},
        },
    }
    errs = _errors(req)
    assert any("max_run_time" in e and "300" in e for e in errs)


def test_exec_constraints_min_boundary_low():
    req = {
        "name": "n",
        "task": "t",
        "constraints": {
            "model": {"exec_model_id": "m"},
            "robot_id": "r",
            "exec_constraints": {"max_iter_num": 0, "max_run_time": 0},
        },
    }
    errs = _errors(req)
    assert any("max_iter_num" in e for e in errs)
    assert any("max_run_time" in e for e in errs)


def test_exec_constraints_type_error():
    # 数组被拒（结构漂移回归：exec_constraints 应为 object）
    req = {
        "name": "n",
        "task": "t",
        "constraints": {
            "model": {"exec_model_id": "m"},
            "robot_id": "r",
            "exec_constraints": [{"key": "gripper", "value": "soft"}],
        },
    }
    errs = _errors(req)
    assert any("exec_constraints" in e and ("object" in e or "JSON object" in e) for e in errs)


def test_name_type_error():
    req = {**VALID_REQ, "name": 123}
    errs = _errors(req)
    assert any("name" in e and "string" in e for e in errs)


def test_exec_model_id_required_in_model():
    req = {
        "name": "n",
        "task": "t",
        "constraints": {"model": {}, "robot_id": "r"},
    }
    errs = _errors(req)
    assert any("exec_model_id" in e and "required" in e for e in errs)


class _FakeClient:
    @validate_params("create_dispatcher_task")
    def create_dispatcher_task(self, session_id: str, req: dict) -> dict:
        return {"ok": True}


def test_validate_params_raises_validation_error():
    client = _FakeClient()
    with pytest.raises(BadParameterError):
        client.create_dispatcher_task("s1", {"name": "n"})  # 缺 task/constraints


def test_validate_params_passes_valid():
    client = _FakeClient()
    result = client.create_dispatcher_task("s1", VALID_REQ)
    assert result == {"ok": True}


def test_validate_params_unknown_method():
    with pytest.raises(ValueError):

        class _BadClient:
            @validate_params("no_such_method")
            def create_dispatcher_task(self, session_id: str, req: dict) -> dict:
                return {}


class TestContainerCardinality:
    """min_items / max_items / min_properties / max_properties consumption via
    the public `validate_field` engine used by CLI thin callbacks."""

    def _v(self):
        return DispatchValidator()

    def test_array_below_min_items(self):
        rule = {"type": "array", "min_items": 2, "max_items": 3}
        errs = self._v().validate_field(rule, ["a"], "tags")
        assert any("tags below min items 2" in e for e in errs)

    def test_array_within_min_items_passes(self):
        rule = {"type": "array", "min_items": 2, "max_items": 3}
        assert self._v().validate_field(rule, ["a", "b"], "tags") == []

    def test_array_above_max_items(self):
        rule = {"type": "array", "min_items": 1, "max_items": 3}
        errs = self._v().validate_field(rule, ["a", "b", "c", "d"], "tags")
        assert any("tags exceeds max items 3" in e for e in errs)

    def test_object_below_min_properties(self):
        rule = {"type": "object", "min_properties": 2, "max_properties": 3}
        errs = self._v().validate_field(rule, {"a": 1}, "cfg")
        assert any("cfg below min properties 2" in e for e in errs)

    def test_object_above_max_properties(self):
        rule = {"type": "object", "min_properties": 1, "max_properties": 2}
        errs = self._v().validate_field(rule, {"a": 1, "b": 2, "c": 3}, "cfg")
        assert any("cfg exceeds max properties 2" in e for e in errs)

    def test_object_within_bounds_passes(self):
        rule = {"type": "object", "min_properties": 1, "max_properties": 3}
        assert self._v().validate_field(rule, {"a": 1, "b": 2}, "cfg") == []
