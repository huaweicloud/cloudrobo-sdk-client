from unittest.mock import MagicMock

import pytest
from cloudrobo_core.sdk import Config, HttpClient
from cloudrobo_dataset.client import DatasetClient, DatasetError, _validate_task_config


def _make_mock_client():
    mock = MagicMock(spec=HttpClient)
    mock.config = MagicMock(spec=Config)
    mock.config.get_endpoint.side_effect = lambda svc: f"https://api.example.com/{svc}"
    mock.config.project_id = "proj1"
    return mock


def _make_valid_proc_config():
    return {
        "name": "test_task",
        "algo_type": "PRESET_ASSETS",
        "algo_name": "test_algo",
        "algo_entrance": "bash entrypoint.sh",
        "image": "test-image:latest",
        "algo_id": "9079102a-d0dc-5c14-b03c-b37c3f88c258",
        "catalog_id": "724bafc3-e0c3-4bba-8402-021084e0075c",
        "resource_pool_type": "PUBLIC_POOL",
        "cluster_type": "CCE",
        "task_framework_type": "K8S",
        "envs": '[{"key":"robot","value":"auto","description":"robot type"}]',
        "dataset_configs": '[{"obs_path":"obs://bucket/data/","dataset_type":"BUILD_IN_ASSET","asset_id":"test-asset","asset_name":"test","version_id":"v1"}]',
        "output_type": "BUILD_IN_ASSET",
        "output_path": "obs://bucket/output/",
        "output_name": "test_output",
        "head_spec": {"cpu": 0, "memory": 0, "gpu": 0, "npu": 0},
        "worker_spec": {"cpu": 2, "memory": 4, "gpu": 0, "npu": 0},
        "worker_num": 1,
        "evs_spec": 0,
    }


class TestDatasetClient:
    def setup_method(self):
        self.mock_http = _make_mock_client()
        self.client = DatasetClient(self.mock_http)

    def test_create_task(self):
        self.mock_http.post.return_value = {"id": "task1"}
        config = _make_valid_proc_config()
        result = self.client.create_task(config)
        assert result["id"] == "task1"

    def test_list_tasks(self):
        self.mock_http.get.return_value = {"items": []}
        result = self.client.list_tasks()
        assert "items" in result

    def test_restart_task(self):
        self.mock_http.post.return_value = {"status": "RUNNING"}
        result = self.client.restart_task("task1")
        assert result["status"] == "RUNNING"

    def test_get_task_log(self):
        self.mock_http.get.return_value = "log content"
        result = self.client.get_task_log("task1", "output.log")
        assert result == "log content"


class TestTaskConfigValidation:
    def test_valid_proc_config_passes(self):
        _validate_task_config(_make_valid_proc_config(), task_type="proc")

    def test_missing_required_field_raises(self):
        config = _make_valid_proc_config()
        del config["algo_name"]
        with pytest.raises(DatasetError, match="algo_name"):
            _validate_task_config(config, task_type="proc")

    def test_empty_string_field_raises(self):
        config = _make_valid_proc_config()
        config["algo_name"] = ""
        with pytest.raises(DatasetError, match="algo_name"):
            _validate_task_config(config, task_type="proc")

    def test_empty_head_spec_raises(self):
        config = _make_valid_proc_config()
        config["head_spec"] = {}
        with pytest.raises(DatasetError, match="head_spec"):
            _validate_task_config(config, task_type="proc")

    def test_head_spec_missing_cpu_key_raises(self):
        config = _make_valid_proc_config()
        config["head_spec"] = {"memory": 0}
        with pytest.raises(DatasetError, match="head_spec.cpu"):
            _validate_task_config(config, task_type="proc")

    def test_worker_spec_missing_memory_key_raises(self):
        config = _make_valid_proc_config()
        config["worker_spec"] = {"cpu": 2, "gpu": 0}
        with pytest.raises(DatasetError, match="worker_spec.memory"):
            _validate_task_config(config, task_type="proc")

    def test_empty_dataset_configs_not_allowed(self):
        config = _make_valid_proc_config()
        config["dataset_configs"] = ""
        with pytest.raises(DatasetError, match="dataset_configs"):
            _validate_task_config(config, task_type="proc")

    def test_empty_array_dataset_configs_rejected(self):
        config = _make_valid_proc_config()
        config["dataset_configs"] = "[]"
        with pytest.raises(DatasetError, match="dataset_configs"):
            _validate_task_config(config, task_type="proc")

    def test_obs_assets_requires_algo_path(self):
        config = _make_valid_proc_config()
        config["algo_type"] = "OBS_ASSETS"
        del config["algo_id"]
        config["algo_path"] = "obs://bucket/algo/"
        config["job_local_path"] = "/home/algo"
        _validate_task_config(config, task_type="proc")

    def test_obs_assets_missing_algo_path_raises(self):
        config = _make_valid_proc_config()
        config["algo_type"] = "OBS_ASSETS"
        del config["algo_id"]
        with pytest.raises(DatasetError, match="algo_path"):
            _validate_task_config(config, task_type="proc")

    def test_description_can_be_empty(self):
        config = _make_valid_proc_config()
        config["description"] = ""
        _validate_task_config(config, task_type="proc")

    def test_description_can_be_missing(self):
        config = _make_valid_proc_config()
        assert "description" not in config
        _validate_task_config(config, task_type="proc")
