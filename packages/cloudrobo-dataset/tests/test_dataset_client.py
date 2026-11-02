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

    def test_copy_task_fills_body_from_source_task(self):
        self.mock_http.get.return_value = {
            "payload": {
                "id": "task1",
                "status": "SUCCEEDED",
                "name": "origin",
                "workspace_id": "ws1",
                "algo_asset_type": "PRESET_ASSETS",
                "task_entrance": "bash entrypoint.sh",
                "target_asset_type": "BUILD_IN_ASSET",
                "target_asset_name": "old-output",
                "worker_spec": {"cpu": 2, "memory": 4},
            }
        }
        self.mock_http.post.return_value = {"payload": {"id": "task2", "status": "PENDING"}}
        result = self.client.copy_task("task1", {"name": "copy-task"})
        assert result["payload"]["id"] == "task2"

        # 先调用 show-task 获取源任务详情
        get_args, _ = self.mock_http.get.call_args
        assert get_args[0].endswith("/v1/data-eng/proc-tasks/task1")

        # 详情字段被映射填充进请求体，且以用户传入的 name 为准
        post_args, post_kwargs = self.mock_http.post.call_args
        assert post_args[0].endswith("/v1/data-eng/proc-tasks/task1/copy")
        body = post_kwargs["json"]
        assert body["name"] == "copy-task"
        assert body["workspace_id"] == "ws1"
        assert body["algo_type"] == "PRESET_ASSETS"
        assert body["algo_entrance"] == "bash entrypoint.sh"
        assert body["output_type"] == "BUILD_IN_ASSET"
        assert body["output_name"] == "old-output"
        assert body["worker_spec"] == {"cpu": 2, "memory": 4}
        # 运行态字段不应带入请求体
        assert "id" not in body
        assert "status" not in body

    def test_copy_task_user_config_overrides_source(self):
        self.mock_http.get.return_value = {"payload": {"name": "origin", "target_asset_name": "old"}}
        self.mock_http.post.return_value = {"payload": {"id": "task2"}}
        self.client.copy_task("task1", {"name": "copy-task", "output_name": "new"})
        _, post_kwargs = self.mock_http.post.call_args
        assert post_kwargs["json"]["name"] == "copy-task"
        assert post_kwargs["json"]["output_name"] == "new"

    def test_copy_task_requires_name(self):
        self.mock_http.get.return_value = {"payload": {"id": "task1"}}
        with pytest.raises(DatasetError, match="name"):
            self.client.copy_task("task1")
        self.mock_http.post.assert_not_called()

    def test_copy_task_requires_output_name(self):
        self.mock_http.get.return_value = {"payload": {"name": "origin"}}
        with pytest.raises(DatasetError, match="output_name"):
            self.client.copy_task("task1", {"name", "copy-task"})
        self.mock_http.post.assert_not_called()

    def test_copy_eval_task_fills_body_from_source_task(self):
        self.mock_http.get.return_value = {
            "payload": {
                "id": "task1",
                "status": "SUCCEEDED",
                "name": "origin-eval",
                "workspace_id": "ws1",
                "task_entrance": "bash entrypoint.sh",
                "source_asset_type": "BUILD_IN_ASSET",
                "source_asset_id": "ds1",
                "source_asset_name": "dataset-a",
                "source_asset_path": "obs://bucket/ds/",
                "algo_id": "algo1",
                "algo_name": "eval-algo",
                "robot_config": "obs://bucket/rb/",
                "worker_spec": {"cpu": 2, "memory": 4},
            }
        }
        self.mock_http.post.return_value = {"payload": {"id": "task2", "status": "PENDING"}}
        result = self.client.copy_eval_task("task1", {"name": "copy-eval"})
        assert result["payload"]["id"] == "task2"

        # 先调用 eval show-task 获取源任务详情
        get_args, _ = self.mock_http.get.call_args
        assert get_args[0].endswith("/v1/data-eng/eval-tasks/task1")

        # 详情字段被映射填充进请求体，且以用户传入的 name 为准
        post_args, post_kwargs = self.mock_http.post.call_args
        assert post_args[0].endswith("/v1/data-eng/eval-tasks/task1/copy")
        body = post_kwargs["json"]
        assert body["name"] == "copy-eval"
        assert body["workspace_id"] == "ws1"
        assert body["algo_id"] == "algo1"
        assert body["algo_name"] == "eval-algo"
        assert body["algo_entrance"] == "bash entrypoint.sh"
        assert body["dataset_type"] == "BUILD_IN_ASSET"
        assert body["dataset_id"] == "ds1"
        assert body["dataset_name"] == "dataset-a"
        assert body["dataset_path"] == "obs://bucket/ds/"
        # robot_config 已废弃（改为 inputs 挂载配置），历史任务中的值不再带入复制请求
        assert "robot_config" not in body
        assert body["worker_spec"] == {"cpu": 2, "memory": 4}
        # 运行态字段不应带入请求体
        assert "id" not in body
        assert "status" not in body

    def test_copy_eval_task_user_config_overrides_source(self):
        self.mock_http.get.return_value = {"payload": {"name": "origin", "source_asset_name": "old"}}
        self.mock_http.post.return_value = {"payload": {"id": "task2"}}
        self.client.copy_eval_task("task1", {"name": "copy-eval", "dataset_name": "new"})
        _, post_kwargs = self.mock_http.post.call_args
        assert post_kwargs["json"]["name"] == "copy-eval"
        assert post_kwargs["json"]["dataset_name"] == "new"

    def test_copy_eval_task_requires_name(self):
        self.mock_http.get.return_value = {"payload": {"id": "task1"}}
        with pytest.raises(DatasetError, match="name"):
            self.client.copy_eval_task("task1")
        self.mock_http.post.assert_not_called()

    def test_get_task_events(self):
        self.mock_http.get.return_value = {"payload": {"events": []}}
        result = self.client.get_task_events("task1")
        assert result["payload"]["events"] == []
        get_args, _ = self.mock_http.get.call_args
        assert get_args[0].endswith("/v1/data-eng/proc-tasks/task1/events")

    def test_get_eval_task_events(self):
        self.mock_http.get.return_value = {"payload": {"events": []}}
        result = self.client.get_eval_task_events("task1")
        assert result["payload"]["events"] == []
        get_args, _ = self.mock_http.get.call_args
        assert get_args[0].endswith("/v1/data-eng/eval-tasks/task1/events")

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

    def test_eval_config_without_robot_config_passes(self):
        config = {
            "name": "eval-task",
            "algo_id": "algo1",
            "algo_name": "eval-algo",
            "algo_entrance": "bash entrypoint.sh",
            "image": "swr.example/eval:1.0",
            "dataset_type": "BUILD_IN_ASSET",
            "dataset_id": "ds1",
            "dataset_name": "dataset-a",
            "dataset_path": "obs://bucket/ds/",
            "resource_pool_type": "PUBLIC_POOL",
            "worker_spec": {"cpu": 2, "memory": 4},
        }
        _validate_task_config(config, task_type="eval")
