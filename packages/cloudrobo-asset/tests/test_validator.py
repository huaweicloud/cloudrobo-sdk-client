import pytest

from cloudrobo_asset.validators import AssetValidator, ValidationError


@pytest.fixture
def validator():
    return AssetValidator()


ALGO_ENGINE = {"image_url": "swr.region/c/ns/repo:tag"}
ALGO_BASE = {"engine": ALGO_ENGINE, "command": "run"}
ALGO_BASE_WITH_SOURCE = {"engine": {**ALGO_ENGINE, "image_source": "custom"}, "command": "run"}


@pytest.fixture
def mock_client():
    from unittest.mock import MagicMock
    from cloudrobo_asset.client import AssetClient
    mock_http = MagicMock()
    mock_http.config = MagicMock()
    mock_http.config.get_endpoint.side_effect = lambda svc: f"https://api.example.com/{svc}"
    mock_http.post.return_value = {"id": "asset1"}
    return AssetClient(mock_http)


class TestTypeSubtype:
    def test_valid_model(self, validator):
        errors = validator.validate_create_asset({"type": "model", "catalog_id": "a1b2c3d4-e5f6-7890-abcd-ef1234567890", "name": "test-model"})
        assert not any("type" in e or "sub_type" in e for e in errors)

    def test_invalid_type(self, validator):
        errors = validator.validate_create_asset({"type": "unknown"})
        assert any("type must be one of" in e for e in errors)

    def test_simulation_requires_subtype(self, validator):
        errors = validator.validate_create_asset({"type": "simulation"})
        assert any("sub_type is required" in e for e in errors)

    def test_simulation_invalid_subtype(self, validator):
        errors = validator.validate_create_asset({"type": "simulation", "sub_type": "invalid"})
        assert any("sub_type must be one of" in e for e in errors)

    def test_algorithm_valid_subtype(self, validator):
        errors = validator.validate_create_asset({"type": "algorithm", "sub_type": "training"})
        assert not any("sub_type" in e for e in errors)

    def test_algorithm_invalid_subtype(self, validator):
        errors = validator.validate_create_asset({"type": "algorithm", "sub_type": "robot"})
        assert any("sub_type must be one of" in e for e in errors)

    def test_missing_type(self, validator):
        errors = validator.validate_create_asset({})
        assert any("type is required" in e for e in errors)


class TestTopFields:
    def test_catalog_id_required_on_create(self, validator):
        errors = validator.validate_create_asset({"type": "model"})
        assert any("catalog_id is required" in e for e in errors)

    def test_catalog_id_not_required_on_update(self, validator):
        errors = validator.validate_update_asset({"name": "test"})
        assert not any("catalog_id" in e for e in errors)

    def test_invalid_catalog_id_format(self, validator):
        errors = validator.validate_create_asset({"type": "model", "catalog_id": "not-uuid"})
        assert any("catalog_id format invalid" in e for e in errors)

    def test_name_too_short(self, validator):
        errors = validator.validate_create_asset({"type": "model", "name": "ab"})
        assert any("name format invalid" in e for e in errors)

    def test_name_too_long(self, validator):
        errors = validator.validate_create_asset({"type": "model", "name": "a" * 65})
        assert any("name format invalid" in e for e in errors)

    def test_invalid_status(self, validator):
        errors = validator.validate_create_asset({"type": "model", "status": "INVALID"})
        assert any("status must be one of" in e for e in errors)

    def test_valid_status(self, validator):
        errors = validator.validate_create_asset({"type": "model", "status": "RELEASE"})
        assert not any("status" in e for e in errors)

    def test_valid_status_creating(self, validator):
        errors = validator.validate_create_asset({"type": "model", "status": "CREATING"})
        assert not any("status" in e for e in errors)

    def test_tags_exceed_max(self, validator):
        errors = validator.validate_create_asset({"type": "model", "tags": [f"tag{i}" for i in range(101)]})
        assert any("tags exceeds max items" in e for e in errors)

    def test_invalid_tag_format(self, validator):
        errors = validator.validate_create_asset({"type": "model", "tags": ["valid", "a" * 33]})
        assert any("tags[1] format invalid" in e for e in errors)


class TestExtMetadataModel:
    def test_missing_model_type(self, validator):
        errors = validator.validate_ext_metadata("model", None, {})
        assert any("model_type is required" in e for e in errors)

    @pytest.mark.parametrize("model_type", ["planning", "perception", "vla", "vln"])
    def test_valid_model_type(self, validator, model_type):
        errors = validator.validate_ext_metadata("model", None, {"model_type": model_type})
        assert not any("model_type" in e for e in errors)

    def test_invalid_model_type(self, validator):
        errors = validator.validate_ext_metadata("model", None, {"model_type": "llm"})
        assert any("model_type must be one of" in e for e in errors)

    def test_skills_not_array(self, validator):
        errors = validator.validate_ext_metadata("model", None, {"model_type": "planning", "skills": "not-array"})
        assert any("skills must be an array" in e for e in errors)

    def test_skills_exceed_max(self, validator):
        skills = [{"name": f"skill{i}", "prompt": f"prompt{i}"} for i in range(51)]
        errors = validator.validate_ext_metadata("model", None, {"model_type": "planning", "skills": skills})
        assert any("skills exceeds max items 50" in e for e in errors)

    def test_skill_missing_name(self, validator):
        errors = validator.validate_ext_metadata("model", None, {"model_type": "planning", "skills": [{"prompt": "p"}]})
        assert any("skills[0].name is required" in e for e in errors)

    def test_skill_duplicate_prompt(self, validator):
        skills = [{"name": "s1", "prompt": "same"}, {"name": "s2", "prompt": "same"}]
        errors = validator.validate_ext_metadata("model", None, {"model_type": "planning", "skills": skills})
        assert any("skills has duplicate prompt" in e for e in errors)

    def test_strict_not_boolean(self, validator):
        errors = validator.validate_ext_metadata("model", None, {"model_type": "planning", "strict": "yes"})
        assert any("strict must be a boolean" in e for e in errors)

    def test_skills_only_for_vla_vln(self, validator):
        errors = validator.validate_ext_metadata("model", None, {
            "model_type": "planning",
            "skills": [{"name": "s1", "prompt": "p1"}],
        })
        assert any("skills is only supported for model_type" in e for e in errors)

    def test_strict_only_for_vla_vln(self, validator):
        errors = validator.validate_ext_metadata("model", None, {
            "model_type": "perception",
            "strict": True,
        })
        assert any("strict is only supported for model_type" in e for e in errors)

    def test_skills_allowed_for_vla(self, validator):
        errors = validator.validate_ext_metadata("model", None, {
            "model_type": "vla",
            "skills": [{"name": "s1", "prompt": "p1"}],
        })
        assert not any("skills is only supported" in e for e in errors)

    def test_strict_allowed_for_vln(self, validator):
        errors = validator.validate_ext_metadata("model", None, {
            "model_type": "vln",
            "strict": True,
        })
        assert not any("strict is only supported" in e for e in errors)


class TestExtMetadataDataset:
    def test_missing_annotation_status(self, validator):
        errors = validator.validate_ext_metadata("dataset", None, {})
        assert any("annotation_status is required" in e for e in errors)

    def test_annotation_status_not_boolean(self, validator):
        errors = validator.validate_ext_metadata("dataset", None, {"annotation_status": "true"})
        assert any("annotation_status must be a boolean" in e for e in errors)

    def test_valid_annotation_status(self, validator):
        errors = validator.validate_ext_metadata("dataset", None, {"annotation_status": True})
        assert not any("annotation_status" in e for e in errors)


class TestExtMetadataAlgorithm:
    def test_missing_engine_and_command(self, validator):
        errors = validator.validate_ext_metadata("algorithm", None, {})
        assert any("engine is required" in e for e in errors)
        assert any("command is required" in e for e in errors)

    def test_missing_image_url(self, validator):
        errors = validator.validate_ext_metadata("algorithm", None, {"engine": {}, "command": "run"})
        assert any("image_url is required" in e for e in errors)

    def test_invalid_image_url(self, validator):
        errors = validator.validate_ext_metadata("algorithm", None, {
            "engine": {"image_url": "not-swr"},
            "command": "run",
        })
        assert any("image_url format invalid" in e for e in errors)

    def test_command_too_long(self, validator):
        errors = validator.validate_ext_metadata("algorithm", None, {
            **ALGO_BASE,
            "command": "x" * 4097,
        })
        assert any("command exceeds max length" in e for e in errors)

    def test_inputs_exceed_max(self, validator):
        inputs = [{"name": f"in{i}", "access_method": "env"} for i in range(11)]
        errors = validator.validate_ext_metadata("algorithm", None, {**ALGO_BASE, "inputs": inputs})
        assert any("inputs exceeds max items 10" in e for e in errors)

    def test_outputs_exceed_max(self, validator):
        outputs = [{"name": f"out{i}", "access_method": "parameter"} for i in range(6)]
        errors = validator.validate_ext_metadata("algorithm", None, {**ALGO_BASE, "outputs": outputs})
        assert any("outputs exceeds max items 5" in e for e in errors)

    def test_input_duplicate_name(self, validator):
        inputs = [{"name": "same", "access_method": "env"}, {"name": "same", "access_method": "parameter"}]
        errors = validator.validate_ext_metadata("algorithm", None, {**ALGO_BASE, "inputs": inputs})
        assert any("inputs has duplicate name" in e for e in errors)

    def test_input_invalid_access_method(self, validator):
        inputs = [{"name": "in1", "access_method": "invalid"}]
        errors = validator.validate_ext_metadata("algorithm", None, {**ALGO_BASE, "inputs": inputs})
        assert any("access_method must be one of" in e for e in errors)

    def test_hyperparams_missing_required_fields(self, validator):
        errors = validator.validate_ext_metadata("algorithm", None, {
            **ALGO_BASE,
            "hyperparams": [{"name": "lr"}],
        })
        assert any("default is required" in e for e in errors)
        assert any("constraint is required" in e for e in errors)

    def test_boot_file_without_code_dir(self, validator):
        errors = validator.validate_ext_metadata("algorithm", None, {
            **ALGO_BASE,
            "boot_file": "obs://bucket/code/train.py",
        })
        assert any("code_dir is required when boot_file" in e for e in errors)

    def test_boot_file_not_under_code_dir(self, validator):
        errors = validator.validate_ext_metadata("algorithm", None, {
            **ALGO_BASE,
            "boot_file": "obs://bucket/other/train.py",
            "code_dir": "obs://bucket/code/",
        })
        assert any("boot_file must be under code_dir" in e for e in errors)

    def test_boot_file_not_py(self, validator):
        errors = validator.validate_ext_metadata("algorithm", None, {
            **ALGO_BASE,
            "boot_file": "obs://bucket/code/train.sh",
            "code_dir": "obs://bucket/code/",
        })
        assert any("boot_file must be a .py file" in e for e in errors)

    def test_resource_invalid_key(self, validator):
        errors = validator.validate_ext_metadata("algorithm", None, {
            **ALGO_BASE,
            "resource": [{"key": "invalid_key", "operator": "in", "values": ["CPU"]}],
        })
        assert any("key must be one of" in e for e in errors)

    def test_resource_invalid_operator(self, validator):
        errors = validator.validate_ext_metadata("algorithm", None, {
            **ALGO_BASE,
            "resource": [{"key": "flavor_type", "operator": "eq", "values": ["CPU"]}],
        })
        assert any("operator must be one of" in e for e in errors)

    def test_resource_invalid_value(self, validator):
        errors = validator.validate_ext_metadata("algorithm", None, {
            **ALGO_BASE,
            "resource": [{"key": "flavor_type", "operator": "in", "values": ["INVALID"]}],
        })
        assert any("must be one of" in e for e in errors)

    @pytest.mark.parametrize("flavor_type", ["CPU", "GPU", "NPU"])
    def test_resource_valid_flavor_type(self, validator, flavor_type):
        errors = validator.validate_ext_metadata("algorithm", None, {
            **ALGO_BASE,
            "resource": [{"key": "flavor_type", "operator": "in", "values": [flavor_type]}],
        })
        assert not any("resource" in e for e in errors)

    @pytest.mark.parametrize("mode_key", ["device_distributed_mode", "host_distributed_mode"])
    @pytest.mark.parametrize("val", ["multiple", "singular"])
    def test_resource_valid_distributed_mode(self, validator, mode_key, val):
        errors = validator.validate_ext_metadata("algorithm", None, {
            **ALGO_BASE,
            "resource": [{"key": mode_key, "operator": "in", "values": [val]}],
        })
        assert not any("resource" in e for e in errors)

    def test_preset_image_requires_code_dir(self, validator):
        errors = validator.validate_ext_metadata("algorithm", None, {
            "engine": {**ALGO_ENGINE, "image_source": "preset"},
            "command": "run",
        })
        assert any("code_dir is required when engine.image_source is 'preset'" in e for e in errors)

    def test_image_source_required(self, validator):
        errors = validator.validate_ext_metadata("algorithm", None, {**ALGO_BASE})
        assert any("image_source is required" in e for e in errors)

    def test_image_source_invalid_enum(self, validator):
        errors = validator.validate_ext_metadata("algorithm", None, {
            "engine": {**ALGO_ENGINE, "image_source": "unknown"},
            "command": "run",
        })
        assert any("image_source must be one of" in e for e in errors)

    def test_image_source_valid_preset(self, validator):
        errors = validator.validate_ext_metadata("algorithm", None, {
            "engine": {**ALGO_ENGINE, "image_source": "preset"},
            "command": "run",
            "code_dir": "obs://bucket/code/",
        })
        assert not any("image_source" in e for e in errors)

    def test_image_source_valid_custom(self, validator):
        errors = validator.validate_ext_metadata("algorithm", None, {**ALGO_BASE_WITH_SOURCE})
        assert not any("image_source" in e for e in errors)

    def test_hyperparams_default_integer_valid(self, validator):
        errors = validator.validate_ext_metadata("algorithm", None, {
            **ALGO_BASE_WITH_SOURCE,
            "hyperparams": [{"name": "lr", "default": "10", "constraint": {"type": "Integer"}}],
        })
        assert not any("must be an integer" in e for e in errors)

    def test_hyperparams_default_integer_invalid(self, validator):
        errors = validator.validate_ext_metadata("algorithm", None, {
            **ALGO_BASE_WITH_SOURCE,
            "hyperparams": [{"name": "lr", "default": "abc", "constraint": {"type": "Integer"}}],
        })
        assert any("must be an integer for constraint.type 'Integer'" in e for e in errors)

    def test_hyperparams_default_float_valid(self, validator):
        errors = validator.validate_ext_metadata("algorithm", None, {
            **ALGO_BASE_WITH_SOURCE,
            "hyperparams": [{"name": "lr", "default": "0.01", "constraint": {"type": "Float"}}],
        })
        assert not any("must be a float" in e for e in errors)

    def test_hyperparams_default_float_invalid(self, validator):
        errors = validator.validate_ext_metadata("algorithm", None, {
            **ALGO_BASE_WITH_SOURCE,
            "hyperparams": [{"name": "lr", "default": "abc", "constraint": {"type": "Float"}}],
        })
        assert any("must be a float for constraint.type 'Float'" in e for e in errors)

    def test_hyperparams_default_boolean_valid(self, validator):
        errors = validator.validate_ext_metadata("algorithm", None, {
            **ALGO_BASE_WITH_SOURCE,
            "hyperparams": [{"name": "flag", "default": "true", "constraint": {"type": "Boolean"}}],
        })
        assert not any("must be 'true' or 'false'" in e for e in errors)

    def test_hyperparams_default_boolean_invalid(self, validator):
        errors = validator.validate_ext_metadata("algorithm", None, {
            **ALGO_BASE_WITH_SOURCE,
            "hyperparams": [{"name": "flag", "default": "yes", "constraint": {"type": "Boolean"}}],
        })
        assert any("must be 'true' or 'false' for constraint.type 'Boolean'" in e for e in errors)


class TestExtMetadataImage:
    def test_missing_arch_and_device_type(self, validator):
        errors = validator.validate_ext_metadata("image", None, {})
        assert any("arch is required" in e for e in errors)
        assert any("device_type is required" in e for e in errors)

    def test_arch_not_string(self, validator):
        errors = validator.validate_ext_metadata("image", None, {"arch": 123, "device_type": ["GPU"]})
        assert any("arch must be a string" in e for e in errors)

    def test_arch_invalid_enum(self, validator):
        errors = validator.validate_ext_metadata("image", None, {"arch": "riscv", "device_type": ["CPU"]})
        assert any("arch must be one of" in e for e in errors)

    @pytest.mark.parametrize("arch", ["x86_64", "arm"])
    def test_valid_arch(self, validator, arch):
        errors = validator.validate_ext_metadata("image", None, {"arch": arch, "device_type": ["CPU"]})
        assert not any("arch" in e for e in errors)

    def test_device_type_not_array(self, validator):
        errors = validator.validate_ext_metadata("image", None, {"arch": "x86_64", "device_type": "GPU"})
        assert any("device_type must be an array of strings" in e for e in errors)

    def test_device_type_invalid_enum(self, validator):
        errors = validator.validate_ext_metadata("image", None, {"arch": "x86_64", "device_type": ["TPU"]})
        assert any("device_type" in e and "must be one of" in e for e in errors)

    def test_device_type_valid(self, validator):
        errors = validator.validate_ext_metadata("image", None, {"arch": "x86_64", "device_type": ["CPU", "GPU", "ASCEND"]})
        assert not any("device_type" in e for e in errors)


class TestExtMetadataSimulation:
    def test_robot_missing_required_fields(self, validator):
        errors = validator.validate_ext_metadata("simulation", "robot", {})
        assert any("robot_type is required" in e for e in errors)
        assert any("robot_manufacturer is required" in e for e in errors)

    @pytest.mark.parametrize("robot_type", [
        "humanoid", "mobile_manipulator", "robot_arm", "quadruped_robot", "wheeled_robot", "other",
    ])
    def test_robot_type_valid(self, validator, robot_type):
        errors = validator.validate_ext_metadata("simulation", "robot", {
            "robot_type": robot_type,
            "robot_manufacturer": "AzureLoong",
        })
        assert not any("robot_type" in e for e in errors)

    def test_robot_type_invalid(self, validator):
        errors = validator.validate_ext_metadata("simulation", "robot", {
            "robot_type": "arm",
            "robot_manufacturer": "AzureLoong",
        })
        assert any("robot_type must be one of" in e for e in errors)

    def test_robot_manufacturer_invalid_format(self, validator):
        errors = validator.validate_ext_metadata("simulation", "robot", {
            "robot_type": "robot_arm",
            "robot_manufacturer": "@" * 65,
        })
        assert any("robot_manufacturer format invalid" in e for e in errors)

    @pytest.mark.parametrize("manufacturer", [
        "Galaxea R1", "AGIBOT G1", "AzureLoong", "Universal Robots UR5e", "SO-ARM101",
    ])
    def test_robot_manufacturer_known_values(self, validator, manufacturer):
        errors = validator.validate_ext_metadata("simulation", "robot", {
            "robot_type": "robot_arm",
            "robot_manufacturer": manufacturer,
        })
        assert not any("robot_manufacturer" in e for e in errors)

    def test_robot_manufacturer_custom_value(self, validator):
        errors = validator.validate_ext_metadata("simulation", "robot", {
            "robot_type": "robot_arm",
            "robot_manufacturer": "My Custom Robot",
        })
        assert not any("robot_manufacturer" in e for e in errors)

    def test_update_url_forbidden(self, validator):
        errors = validator.validate_update_asset({"url": "obs://bucket/path"})
        assert any("url cannot be modified on update" in e for e in errors)

    def test_create_url_allowed(self, validator):
        errors = validator.validate_create_asset({
            "type": "model",
            "catalog_id": "a1b2c3d4-e5f6-7890-abcd-ef1234567890",
            "url": "obs://bucket/path",
        })
        assert not any("url cannot be modified" in e for e in errors)


class TestDecorator:
    def test_create_asset_valid(self, mock_client):
        result = mock_client.create_asset({
            "catalog_id": "a1b2c3d4-e5f6-7890-abcd-ef1234567890",
            "name": "my-model",
            "type": "model",
            "ext_metadata": {"model_type": "planning"},
        })
        assert result["id"] == "asset1"

    def test_create_asset_invalid_type(self, mock_client):
        with pytest.raises(ValidationError) as exc_info:
            mock_client.create_asset({"type": "invalid_type"})
        assert any("type must be one of" in e for e in exc_info.value.errors)

    def test_update_asset_invalid_name(self, mock_client):
        with pytest.raises(ValidationError) as exc_info:
            mock_client.update_asset("asset1", {"name": "ab"})
        assert any("name format invalid" in e for e in exc_info.value.errors)

    def test_create_asset_model_invalid_model_type(self, mock_client):
        with pytest.raises(ValidationError) as exc_info:
            mock_client.create_asset({
                "catalog_id": "a1b2c3d4-e5f6-7890-abcd-ef1234567890",
                "name": "my-model",
                "type": "model",
                "ext_metadata": {"model_type": "llm"},
            })
        assert any("model_type must be one of" in e for e in exc_info.value.errors)


class TestNameConditionalRequired:
    def test_name_required_for_non_image_type(self, validator):
        errors = validator.validate_create_asset({
            "type": "model",
            "catalog_id": "a1b2c3d4-e5f6-7890-abcd-ef1234567890",
        })
        assert any("name is required when type is not 'image'" in e for e in errors)

    def test_name_not_required_for_image_type(self, validator):
        errors = validator.validate_create_asset({
            "type": "image",
            "catalog_id": "a1b2c3d4-e5f6-7890-abcd-ef1234567890",
        })
        assert not any("name is required" in e for e in errors)

    def test_name_provided_for_non_image_type(self, validator):
        errors = validator.validate_create_asset({
            "type": "model",
            "catalog_id": "a1b2c3d4-e5f6-7890-abcd-ef1234567890",
            "name": "my-model",
        })
        assert not any("name is required" in e for e in errors)


class TestUpdateFieldRestrictions:
    def test_update_type_forbidden(self, validator):
        errors = validator.validate_update_asset({"type": "model"})
        assert any("type cannot be modified on update" in e for e in errors)

    def test_update_sub_type_forbidden(self, validator):
        errors = validator.validate_update_asset({"sub_type": "robot"})
        assert any("sub_type cannot be modified on update" in e for e in errors)

    def test_update_catalog_id_forbidden(self, validator):
        errors = validator.validate_update_asset({"catalog_id": "a1b2c3d4-e5f6-7890-abcd-ef1234567890"})
        assert any("catalog_id cannot be modified on update" in e for e in errors)

    def test_update_generation_method_forbidden(self, validator):
        errors = validator.validate_update_asset({"generation_method": "train"})
        assert any("generation_method cannot be modified on update" in e for e in errors)


class TestHyperparamsDefaultStringFallback:
    def _make_hyperparams(self, default, constraint_type="String"):
        return {
            **ALGO_BASE_WITH_SOURCE,
            "hyperparams": [{"name": "p1", "default": default, "constraint": {"type": constraint_type}}],
        }

    def test_string_pattern_valid(self, validator):
        errors = validator.validate_ext_metadata("algorithm", None, self._make_hyperparams("learning_rate"))
        assert not any("must match pattern or be valid JSON" in e for e in errors)

    def test_string_json_object_valid(self, validator):
        errors = validator.validate_ext_metadata("algorithm", None, self._make_hyperparams('{"key":"val"}'))
        assert not any("must match pattern or be valid JSON" in e for e in errors)

    def test_string_json_array_valid(self, validator):
        errors = validator.validate_ext_metadata("algorithm", None, self._make_hyperparams('[1,2,3]'))
        assert not any("must match pattern or be valid JSON" in e for e in errors)

    def test_string_invalid_value(self, validator):
        errors = validator.validate_ext_metadata("algorithm", None, self._make_hyperparams("@#$%^&*"))
        assert any("must match pattern or be valid JSON" in e for e in errors)

    def test_string_json_string_invalid(self, validator):
        errors = validator.validate_ext_metadata("algorithm", None, self._make_hyperparams('"just a string"'))
        assert any("must match pattern or be valid JSON" in e for e in errors)


class TestCreateVersion:
    def test_valid_version(self, validator):
        errors = validator.validate_create_version({"version": "v1.0"})
        assert not errors

    @pytest.mark.parametrize("version", ["v1.0", "1.0.0", "release-2", "V2_0"])
    def test_version_pattern_valid(self, validator, version):
        errors = validator.validate_create_version({"version": version})
        assert not any("version format invalid" in e for e in errors)

    @pytest.mark.parametrize("version", [".1", "-1", "a"])
    def test_version_pattern_invalid(self, validator, version):
        errors = validator.validate_create_version({"version": version})
        assert any("version format invalid" in e for e in errors)

    def test_version_too_long(self, validator):
        errors = validator.validate_create_version({"version": "a" * 129})
        assert any("version format invalid" in e for e in errors)

    def test_description_max_length(self, validator):
        errors = validator.validate_create_version({"description": "x" * 513})
        assert any("description exceeds max length" in e for e in errors)

    def test_invalid_status(self, validator):
        errors = validator.validate_create_version({"status": "INVALID"})
        assert any("status must be one of" in e for e in errors)

    def test_valid_status(self, validator):
        errors = validator.validate_create_version({"status": "RELEASE"})
        assert not any("status" in e for e in errors)

    def test_valid_status_creating(self, validator):
        errors = validator.validate_create_version({"status": "CREATING"})
        assert not any("status" in e for e in errors)

    def test_url_valid_obs(self, validator):
        errors = validator.validate_create_version({"url": "obs://bucket/path"})
        assert not any("url" in e for e in errors)

    def test_url_invalid(self, validator):
        errors = validator.validate_create_version({"url": "https://example.com"})
        assert any("url format invalid" in e for e in errors)

    def test_parent_version_requires_generation_method(self, validator):
        errors = validator.validate_create_version({
            "parent_asset_version_id": "a1b2c3d4-e5f6-7890-abcd-ef1234567890",
        })
        assert any("generation_method is required when parent_asset_version_id" in e for e in errors)

    def test_parent_version_with_generation_method(self, validator):
        errors = validator.validate_create_version({
            "parent_asset_version_id": "a1b2c3d4-e5f6-7890-abcd-ef1234567890",
            "generation_method": "finetune",
        })
        assert not any("generation_method" in e for e in errors)

    def test_invalid_parent_version_id(self, validator):
        errors = validator.validate_create_version({
            "parent_asset_version_id": "not-uuid",
            "generation_method": "finetune",
        })
        assert any("parent_asset_version_id format invalid" in e for e in errors)

    def test_invalid_generation_method(self, validator):
        errors = validator.validate_create_version({"generation_method": "123abc"})
        assert any("generation_method format invalid" in e for e in errors)


class TestUpdateVersion:
    def test_url_forbidden_on_update(self, validator):
        errors = validator.validate_update_version({"url": "obs://bucket/path"})
        assert any("url cannot be modified on update" in e for e in errors)

    def test_valid_update(self, validator):
        errors = validator.validate_update_version({"description": "updated", "status": "RELEASE"})
        assert not errors
