import pytest
from unittest.mock import MagicMock, patch, call

from cloudrobo_core.sdk import Config, HttpClient
from cloudrobo_asset.client import AssetClient
from cloudrobo_dataset.client import DatasetClient
from cloudrobo_train.client import TrainClient
from cloudrobo_eval.client import EvalClient
from cloudrobo_robot.client import RobotClient
from cloudrobo_infer.client import InferClient
from cloudrobo_dispatch.client import DispatchClient
from cloudrobo_workspace.client import WorkspaceClient

def _make_mock_client():
    mock = MagicMock(spec=HttpClient)
    mock.config = MagicMock(spec=Config)
    mock.config.get_endpoint.side_effect = lambda svc: f"https://api.example.com/{svc}"
    mock.config.project_id = "proj1"
    return mock


class TestAssetClient:
    def setup_method(self):
        self.mock_http = _make_mock_client()
        self.client = AssetClient(self.mock_http)

    def test_list_repositories(self):
        self.mock_http.get.return_value = {"items": []}
        result = self.client.list_repositories()
        assert "items" in result

    def test_create_asset(self):
        self.mock_http.post.return_value = {"id": "asset1"}
        result = self.client.create_asset({"catalog_id": "a1b2c3d4-e5f6-7890-abcd-ef1234567890", "name": "model-a", "type": "model", "ext_metadata": {"model_type": "planning"}})
        assert result["id"] == "asset1"

    def test_list_assets_with_filters(self):
        self.mock_http.get.return_value = {"items": [], "total": 0}
        result = self.client.list_assets(type="model", status="RELEASE")
        self.mock_http.get.assert_called_once()

    def test_add_tags(self):
        self.mock_http.post.return_value = {"tags": ["tag1"]}
        self.client.add_tags("asset1", ["tag1"])
        self.mock_http.post.assert_called_once()

    def test_show_asset_tree(self):
        self.mock_http.get.return_value = {"nodes": []}
        result = self.client.show_asset_tree("asset1", "v1", "children")
        assert "nodes" in result


class TestDatasetClient:
    def setup_method(self):
        self.mock_http = _make_mock_client()
        self.client = DatasetClient(self.mock_http)

    def test_create_task(self):
        self.mock_http.post.return_value = {"id": "task1"}
        config = {
            "name": "preprocess",
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


class TestTrainClient:
    def setup_method(self):
        self.mock_http = _make_mock_client()
        self.client = TrainClient(self.mock_http)

    def test_create_train_task(self):
        self.mock_http.post.return_value = {"id": "train1"}
        result = self.client.create_train_task({"name": "finetune-job"})
        assert result["id"] == "train1"

    def test_stop_train_task(self):
        self.mock_http.post.return_value = {"status": "STOPPED"}
        result = self.client.stop_train_task("train1")
        assert result["status"] == "STOPPED"

    def test_save_draft(self):
        self.mock_http.post.return_value = {"id": "draft1"}
        result = self.client.save_draft({"name": "my-draft"})
        assert result["id"] == "draft1"


class TestEvalClient:
    def setup_method(self):
        self.mock_http = _make_mock_client()
        self.client = EvalClient(self.mock_http)

    def test_create_eval_job(self):
        self.mock_http.post.return_value = {"id": "eval1"}
        result = self.client.create_eval_job({"name": "sim-eval"})
        assert result["id"] == "eval1"

    def test_batch_delete_eval_jobs(self):
        self.mock_http.post.return_value = ""
        self.client.batch_delete_eval_jobs(["j1", "j2"])
        self.mock_http.post.assert_called_once()

    def test_get_vnc_address(self):
        self.mock_http.get.return_value = {"vnc_address": "vnc://xxx"}
        result = self.client.get_vnc_address("j1", "e1")
        assert "vnc_address" in result


class TestRobotClient:
    def setup_method(self):
        self.mock_http = _make_mock_client()
        self.client = RobotClient(self.mock_http)

    def test_create_robot(self):
        self.mock_http.post.return_value = {"id": "robot1"}
        result = self.client.create_robot(
            {
                "name": "arm-1",
                "type": "ARM",
                "manufacturer": "hms",
                "robot_model": "model-x",
                "workspace_id": "ws1",
            }
        )
        assert result["id"] == "robot1"

    def test_list_robots(self):
        self.mock_http.get.return_value = {"items": []}
        result = self.client.list_robots()
        assert "items" in result


class TestInferClient:
    def setup_method(self):
        self.mock_http = _make_mock_client()
        self.client = InferClient(self.mock_http)

    def test_start_infer_service(self):
        self.mock_http.post.return_value = {"status": "RUNNING"}
        result = self.client.start_infer_service("svc1")
        assert result["status"] == "RUNNING"


class TestDispatchClient:
    def setup_method(self):
        self.mock_http = _make_mock_client()
        self.client = DispatchClient(self.mock_http)

    def test_create_dispatcher_task(self):
        self.mock_http.post.return_value = {"task_id": "task1"}
        result = self.client.create_dispatcher_task(
            "sess1",
            {"name": "t", "task": "grasp", "constraints": {"model": {"exec_model_id": "m1"}, "robot_id": "r1"}},
        )
        assert result["task_id"] == "task1"

    def test_list_dispatcher_tasks(self):
        self.mock_http.get.return_value = {"tasks": []}
        result = self.client.list_dispatcher_tasks("sess1", limit=10)
        assert result["tasks"] == []

    def test_show_dispatcher_task_result(self):
        self.mock_http.get.return_value = {"task": {}, "log_items": []}
        result = self.client.show_dispatcher_task_result("sess1", "task1", inverse=True)
        assert result["log_items"] == []


class TestWorkspaceClient:
    def setup_method(self):
        self.mock_http = _make_mock_client()
        self.client = WorkspaceClient(self.mock_http)

    def test_create_workspace(self):
        self.mock_http.post.return_value = {"workspace": {"workspace_id": "ws1"}}
        result = self.client.create_workspace({"name": "my-workspace", "default_obs_path": "obs://bucket/path"})
        assert result["workspace"]["workspace_id"] == "ws1"

    def test_list_workspaces(self):
        self.mock_http.get.return_value = {"workspaces": []}
        result = self.client.list_workspaces()
        assert "workspaces" in result

    def test_show_workspace(self):
        self.mock_http.get.return_value = {"workspace": {"workspace_id": "ws1", "name": "my-workspace"}}
        result = self.client.show_workspace("ws1")
        assert result["workspace"]["workspace_id"] == "ws1"

    def test_update_workspace(self):
        self.mock_http.put.return_value = {"workspace": {"workspace_id": "ws1", "name": "updated"}}
        result = self.client.update_workspace("ws1", {"name": "updated"})
        assert result["workspace"]["name"] == "updated"

    def test_delete_workspace(self):
        self.mock_http.delete.return_value = ""
        self.client.delete_workspace("ws1")
        self.mock_http.delete.assert_called_once()

    def test_add_workspace_members(self):
        self.mock_http.post.return_value = {"members": []}
        result = self.client.add_workspace_members("ws1", {"member_list": [{"user_id": "u1", "role_ids": ["r1"]}]})
        assert "members" in result

    def test_list_workspace_members(self):
        self.mock_http.get.return_value = {"members": []}
        result = self.client.list_workspace_members("ws1")
        assert "members" in result

    def test_update_workspace_member(self):
        self.mock_http.put.return_value = {"member": {"user_id": "u1"}}
        result = self.client.update_workspace_member("ws1", {"user_id": "u1", "role_ids": ["r1"]})
        assert result["member"]["user_id"] == "u1"

    def test_delete_workspace_members(self):
        self.mock_http.delete.return_value = ""
        self.client.delete_workspace_members("ws1", ["u1", "u2"])
        self.mock_http.delete.assert_called_once()

    def test_get_workspace_overview(self):
        self.mock_http.get.return_value = {"workspace_capacity": 10, "member_count": 5}
        result = self.client.get_workspace_overview()
        assert result["workspace_capacity"] == 10
