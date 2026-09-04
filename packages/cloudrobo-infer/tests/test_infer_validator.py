from unittest.mock import MagicMock

import pytest
from cloudrobo_core.sdk.exceptions import BadParameterError
from cloudrobo_infer.client import InferClient
from cloudrobo_infer.validators import InferValidator


@pytest.fixture
def validator():
    return InferValidator()


BASE_REQ = {
    "name": "chat-api",
    "flavor": "cpu.2",
    "model": {"model_id": "m1", "model_version_id": "v1"},
    "workspace_id": "ws-1",
    "pool_id": "pool-public",
    "pool_type": "SHARED",
}


def _with(**overrides):
    req = {**BASE_REQ}
    req.update(overrides)
    return req


class TestCreateTopFields:
    def test_valid_base_passes(self, validator):
        assert validator.validate_create_infer_service(_with()) == []

    def test_missing_required_field(self, validator):
        req = {k: v for k, v in BASE_REQ.items() if k != "name"}
        errors = validator.validate_create_infer_service(req)
        assert any("Request.name is required" in e for e in errors)

    def test_type_error_string_field(self, validator):
        errors = validator.validate_create_infer_service(_with(flavor=123))
        assert any("flavor must be a string" in e for e in errors)

    def test_invalid_pool_type_enum(self, validator):
        errors = validator.validate_create_infer_service(_with(pool_type="BOGUS"))
        assert any("pool_type must be one of" in e for e in errors)

    def test_pool_type_case_sensitive(self, validator):
        errors = validator.validate_create_infer_service(_with(pool_type="shared"))
        assert any("pool_type must be one of" in e for e in errors)

    def test_name_too_short(self, validator):
        errors = validator.validate_create_infer_service(_with(name="ab"))
        assert any("name below min length 3" in e for e in errors)

    def test_name_invalid_pattern(self, validator):
        errors = validator.validate_create_infer_service(_with(name="bad name!"))
        assert any("name format invalid" in e for e in errors)

    def test_model_missing_required_id(self, validator):
        errors = validator.validate_create_infer_service(
            _with(model={"model_version_id": "v1"})
        )
        assert any("model.model_id is required" in e for e in errors)

    def test_deploy_timeout_below_min(self, validator):
        errors = validator.validate_create_infer_service(_with(deploy_timeout_minutes=0))
        assert any("deploy_timeout_minutes must be >= 1" in e for e in errors)

    def test_deploy_timeout_above_max(self, validator):
        errors = validator.validate_create_infer_service(_with(deploy_timeout_minutes=301))
        assert any("deploy_timeout_minutes must be <= 300" in e for e in errors)

    def test_deploy_timeout_type_error(self, validator):
        errors = validator.validate_create_infer_service(_with(deploy_timeout_minutes="60"))
        assert any("deploy_timeout_minutes must be an integer" in e for e in errors)

    def test_internet_access_enable_not_boolean(self, validator):
        errors = validator.validate_create_infer_service(_with(internet_access_enable="yes"))
        assert any("internet_access_enable must be a boolean" in e for e in errors)


class TestCreateServiceInvoke:
    def test_valid_service_invoke(self, validator):
        errors = validator.validate_create_infer_service(_with(service_invoke={
            "port": 8080, "protocol": "HTTP", "auth_type": "API_KEY",
        }))
        assert not any("service_invoke" in e for e in errors)

    def test_missing_required_auth_type(self, validator):
        errors = validator.validate_create_infer_service(_with(service_invoke={
            "port": 8080, "protocol": "HTTP",
        }))
        assert any("service_invoke.auth_type is required" in e for e in errors)

    def test_missing_required_port(self, validator):
        errors = validator.validate_create_infer_service(_with(service_invoke={
            "protocol": "HTTP", "auth_type": "NONE",
        }))
        assert any("service_invoke.port is required" in e for e in errors)

    def test_port_below_min(self, validator):
        errors = validator.validate_create_infer_service(_with(service_invoke={
            "port": 80, "protocol": "HTTP", "auth_type": "NONE",
        }))
        assert any("service_invoke.port must be >= 1024" in e for e in errors)

    def test_port_above_max(self, validator):
        errors = validator.validate_create_infer_service(_with(service_invoke={
            "port": 70000, "protocol": "HTTP", "auth_type": "NONE",
        }))
        assert any("service_invoke.port must be <= 65535" in e for e in errors)

    def test_port_type_error(self, validator):
        errors = validator.validate_create_infer_service(_with(service_invoke={
            "port": "8080", "protocol": "HTTP", "auth_type": "NONE",
        }))
        assert any("service_invoke.port must be an integer" in e for e in errors)

    def test_protocol_invalid_enum(self, validator):
        errors = validator.validate_create_infer_service(_with(service_invoke={
            "port": 8080, "protocol": "grpc", "auth_type": "NONE",
        }))
        assert any("service_invoke.protocol must be one of" in e for e in errors)

    def test_protocol_valid_enums(self, validator):
        for protocol in ("HTTP", "HTTPS", "WS", "WSS"):
            errors = validator.validate_create_infer_service(_with(service_invoke={
                "port": 8080, "protocol": protocol, "auth_type": "NONE",
            }))
            assert not any("service_invoke.protocol" in e for e in errors), protocol

    def test_auth_type_invalid_enum(self, validator):
        errors = validator.validate_create_infer_service(_with(service_invoke={
            "port": 8080, "protocol": "HTTP", "auth_type": "TOKEN",
        }))
        assert any("service_invoke.auth_type must be one of" in e for e in errors)


class TestCreateSkillConfig:
    def test_valid_skill_config(self, validator):
        errors = validator.validate_create_infer_service(_with(skill_config={
            "strict": True,
            "skills": [{"name": "x", "prompt": "y"}],
        }))
        assert not any("skill_config" in e for e in errors)

    def test_strict_type_error(self, validator):
        errors = validator.validate_create_infer_service(_with(skill_config={
            "strict": "yes",
            "skills": [{"name": "x", "prompt": "y"}],
        }))
        assert any("skill_config.strict must be a boolean" in e for e in errors)

    def test_skill_missing_name(self, validator):
        errors = validator.validate_create_infer_service(_with(skill_config={
            "skills": [{"prompt": "y"}],
        }))
        assert any("skill_config.skills[0].name is required" in e for e in errors)

    def test_skill_missing_prompt(self, validator):
        errors = validator.validate_create_infer_service(_with(skill_config={
            "skills": [{"name": "x"}],
        }))
        assert any("skill_config.skills[0].prompt is required" in e for e in errors)

    def test_skill_prompt_too_long(self, validator):
        errors = validator.validate_create_infer_service(_with(skill_config={
            "skills": [{"name": "x", "prompt": "y" * 1025}],
        }))
        assert any("skill_config.skills[0].prompt exceeds max length 1024" in e for e in errors)

    def test_skills_exceed_max_items(self, validator):
        skills = [{"name": f"skill{i}", "prompt": f"p{i}"} for i in range(51)]
        errors = validator.validate_create_infer_service(_with(skill_config={"skills": skills}))
        assert any("skill_config.skills exceeds max items 50" in e for e in errors)

    def test_skills_not_array(self, validator):
        errors = validator.validate_create_infer_service(_with(skill_config={"skills": "not-array"}))
        assert any("skill_config.skills must be an array" in e for e in errors)


class TestCreateModelExtMetadata:
    def test_valid_string(self, validator):
        errors = validator.validate_create_infer_service(_with(model_ext_metadata='{"model": "x"}'))
        assert not any("model_ext_metadata" in e for e in errors)

    def test_reject_yaml_string(self, validator):
        errors = validator.validate_create_infer_service(_with(model_ext_metadata="{model: x}"))
        assert any("model_ext_metadata format invalid: expected JSON content" in e for e in errors)

    def test_type_error_when_dict(self, validator):
        errors = validator.validate_create_infer_service(_with(model_ext_metadata={"model": "x"}))
        assert any("model_ext_metadata must be a string" in e for e in errors)

    def test_exceeds_max_length(self, validator):
        errors = validator.validate_create_infer_service(_with(model_ext_metadata="x" * 20481))
        assert any("model_ext_metadata exceeds max length 20480" in e for e in errors)


class TestUpdateInferService:
    def test_valid_update(self, validator):
        assert validator.validate_update_infer_service({"description": "updated"}) == []

    def test_update_description_too_long(self, validator):
        errors = validator.validate_update_infer_service({"description": "x" * 513})
        assert any("description exceeds max length 512" in e for e in errors)

    def test_update_model_ext_metadata_wrong_type(self, validator):
        errors = validator.validate_update_infer_service({"model_ext_metadata": {"a": 1}})
        assert any("model_ext_metadata must be a string" in e for e in errors)


class TestListInferServiceLogs:
    def test_valid_logs_request(self, validator):
        errors = validator.validate_list_infer_service_logs({
            "start_time": 1779782400000, "end_time": 1779868800000,
        })
        assert errors == []

    def test_missing_start_time(self, validator):
        errors = validator.validate_list_infer_service_logs({"end_time": 1779868800000})
        assert any("Request.start_time is required" in e for e in errors)

    def test_missing_end_time(self, validator):
        errors = validator.validate_list_infer_service_logs({"start_time": 1779782400000})
        assert any("Request.end_time is required" in e for e in errors)

    def test_non_object(self, validator):
        errors = validator.validate_list_infer_service_logs([1, 2])
        assert any("Request must be a JSON object" in e for e in errors)

    def test_keywords_too_long(self, validator):
        errors = validator.validate_list_infer_service_logs({
            "start_time": 0, "end_time": 1, "keywords": "k" * 257,
        })
        assert any("keywords exceeds max length 256" in e for e in errors)

    def test_start_time_negative(self, validator):
        errors = validator.validate_list_infer_service_logs({
            "start_time": -1, "end_time": 1,
        })
        assert any("start_time must be >= 0" in e for e in errors)

    def test_limit_below_min(self, validator):
        errors = validator.validate_list_infer_service_logs({
            "start_time": 0, "end_time": 1, "limit": 0,
        })
        assert any("limit must be >= 1" in e for e in errors)


class TestSourceMetadata:
    def test_error_includes_source(self, validator):
        errors = validator.validate_create_infer_service(_with(service_invoke={
            "port": 80, "protocol": "HTTP", "auth_type": "NONE",
        }))
        assert any("来源:" in e or "pilot-manager" in e for e in errors)


class TestContainerCardinality:
    """min_items / max_items / min_properties / max_properties consumption via
    the public `validate_field` engine used by CLI thin callbacks."""

    def test_array_below_min_items(self, validator):
        rule = {"type": "array", "min_items": 2, "max_items": 3}
        errors = validator.validate_field(rule, ["a"], "tags")
        assert any("tags below min items 2" in e for e in errors)

    def test_array_within_min_items_passes(self, validator):
        rule = {"type": "array", "min_items": 2, "max_items": 3}
        assert validator.validate_field(rule, ["a", "b"], "tags") == []

    def test_array_below_min_and_above_max_fail(self, validator):
        rule = {"type": "array", "min_items": 2, "max_items": 3}
        errs = validator.validate_field(rule, ["a", "b", "c", "d"], "tags")
        assert any("exceeds max items 3" in e for e in errs)

    def test_object_below_min_properties(self, validator):
        rule = {"type": "object", "min_properties": 2, "max_properties": 3}
        errors = validator.validate_field(rule, {"a": 1}, "cfg")
        assert any("below min properties 2" in e for e in errors)

    def test_object_above_max_properties(self, validator):
        rule = {"type": "object", "min_properties": 1, "max_properties": 2}
        errors = validator.validate_field(rule, {"a": 1, "b": 2, "c": 3}, "cfg")
        assert any("exceeds max properties 2" in e for e in errors)

    def test_object_within_bounds_passes(self, validator):
        rule = {"type": "object", "min_properties": 1, "max_properties": 3}
        assert validator.validate_field(rule, {"a": 1, "b": 2}, "cfg") == []


class TestValidateParamsDecorator:
    def test_valid_req_passes_client(self):
        mock_http = MagicMock()
        mock_http.config = MagicMock()
        mock_http.config.get_endpoint.side_effect = lambda svc: f"https://api.example.com/{svc}"
        mock_http.post.return_value = {"id": "svc1"}
        client = InferClient(mock_http)
        req = {**BASE_REQ}
        result = client.create_infer_service(req)
        assert result["id"] == "svc1"

    def test_invalid_req_raises_validation_error(self):
        mock_http = MagicMock()
        mock_http.config = MagicMock()
        mock_http.config.get_endpoint.side_effect = lambda svc: f"https://api.example.com/{svc}"
        client = InferClient(mock_http)
        with pytest.raises(BadParameterError):
            client.create_infer_service(_with(service_invoke={
                "port": 80, "protocol": "HTTP", "auth_type": "NONE",
            }))
        mock_http.post.assert_not_called()

    def test_list_logs_valid_req_passes_client(self):
        mock_http = MagicMock()
        mock_http.config = MagicMock()
        mock_http.config.get_endpoint.side_effect = lambda svc: f"https://api.example.com/{svc}"
        mock_http.post.return_value = {"logs": []}
        client = InferClient(mock_http)
        result = client.list_infer_service_logs(
            "svc1", {"start_time": 0, "end_time": 1, "keywords": "err"}
        )
        assert result == {"logs": []}

    def test_list_logs_missing_required_raises(self):
        mock_http = MagicMock()
        mock_http.config = MagicMock()
        mock_http.config.get_endpoint.side_effect = lambda svc: f"https://api.example.com/{svc}"
        client = InferClient(mock_http)
        with pytest.raises(BadParameterError):
            client.list_infer_service_logs("svc1", {"start_time": 0})
        mock_http.post.assert_not_called()
