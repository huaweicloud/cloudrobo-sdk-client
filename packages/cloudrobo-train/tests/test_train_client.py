import pytest
from unittest.mock import MagicMock, patch

from cloudrobo_core.sdk import Config, HttpClient
from cloudrobo_train.client import TrainClient


def _make_mock_client():
    mock = MagicMock(spec=HttpClient)
    mock.config = MagicMock(spec=Config)
    mock.config.get_endpoint.side_effect = lambda svc: f"https://api.example.com/{svc}"
    mock.config.project_id = "proj1"
    mock.config.workspace_id = "ws-test"
    return mock


BASE = "https://api.example.com/cloudrobo-service"


class TestTrainClient:
    def setup_method(self):
        self.mock_http = _make_mock_client()
        self.client = TrainClient(self.mock_http)

    def test_create_train_task(self):
        self.mock_http.post.return_value = {"payload": {"item": {"task_id": "train1"}}}
        req = {"name": "finetune-job", "algorithm": {"algorithm_asset_id": "a1"}, "spec": "Ascend: 1 * SNT9B2", "train_mode": "MODEL_TUNING"}
        result = self.client.create_train_task(req)
        assert result == {"task_id": "train1"}
        self.mock_http.post.assert_called_with(
            f"{BASE}/v1/training/train-tasks", json={**req, "workspace_id": "ws-test"}
        )

    def test_list_train_tasks(self):
        self.mock_http.get.return_value = {
            "meta_info": {"cost_time": 10},
            "payload": {"list": [{"id": "t1", "name": "n1"}], "page_info": {"offset": 0, "limit": 10, "total": 1}},
        }
        result = self.client.list_train_tasks(status=["RUNNING"], offset=0, limit=10)
        assert result == {"list": [{"id": "t1", "name": "n1"}], "page_info": {"offset": 0, "limit": 10, "total": 1}}
        self.mock_http.get.assert_called_with(
            f"{BASE}/v1/training/train-tasks",
            params={"status": ["RUNNING"], "offset": 0, "limit": 10, "workspace_id": "ws-test"},
        )

    def test_batch_delete_train_tasks(self):
        self.mock_http.post.return_value = None
        result = self.client.batch_delete_train_tasks(["id1", "id2"])
        assert result is None
        self.mock_http.post.assert_called_with(
            f"{BASE}/v1/training/train-tasks/batch-delete",
            json={"execution_ids": ["id1", "id2"]},
        )

    def test_count_train_tasks_by_status(self):
        self.mock_http.get.return_value = {"total": 1, "count_by_status": {}}
        self.client.count_train_tasks_by_status("ws-1", user_id="u1")
        self.mock_http.get.assert_called_with(
            f"{BASE}/v1/training/train-tasks/stats",
            params={"workspace_id": "ws-1", "user_id": "u1"},
        )

    def test_resume_train_task(self):
        self.mock_http.post.return_value = {"payload": {"item": {"task_id": "t1"}}}
        result = self.client.resume_train_task("t1")
        assert result == {"task_id": "t1"}
        self.mock_http.post.assert_called_with(f"{BASE}/v1/training/train-tasks/t1/resume")

    def test_stop_train_task(self):
        self.mock_http.post.return_value = {"payload": {"item": {"task_id": "t1"}}}
        result = self.client.stop_train_task("t1")
        assert result == {"task_id": "t1"}
        self.mock_http.post.assert_called_with(f"{BASE}/v1/training/train-tasks/t1/stop")

    def test_restart_train_task(self):
        self.mock_http.get.return_value = {
            "name": "Train-job",
            "algorithm": {"algorithm_asset_id": "a1"},
            "spec": "Ascend: 1 * SNT9B2",
            "train_mode": "MODEL_TUNING",
            "train_method": "FFT",
            "workspace_id": "ws-old",
            "task_id": "t1",
            "execution_id": "e1",
            "status": "FINISHED",
        }
        self.mock_http.post.return_value = {"payload": {"item": {"task_id": "t1"}}}
        result = self.client.restart_train_task("t1")
        assert result == {"task_id": "t1"}
        self.mock_http.post.assert_called_with(
            f"{BASE}/v1/training/train-tasks/t1/restart",
            json={
                "name": "Train-job",
                "algorithm": {"algorithm_asset_id": "a1"},
                "spec": "Ascend: 1 * SNT9B2",
                "train_mode": "MODEL_TUNING",
                "train_method": "FFT",
                "workspace_id": "ws-test",
            },
        )

    def test_restart_train_task_with_req(self):
        self.mock_http.get.return_value = {
            "name": "Train-job",
            "algorithm": {"algorithm_asset_id": "a1"},
            "spec": "Ascend: 1 * SNT9B2",
            "train_mode": "MODEL_TUNING",
            "train_method": "FFT",
            "workspace_id": "ws-old",
            "task_id": "t1",
            "execution_id": "e1",
            "status": "FINISHED",
        }
        self.mock_http.post.return_value = {"payload": {"item": {"task_id": "t1"}}}
        user_req = {"description": "updated", "spec": "Ascend: 2 * SNT9B2"}
        result = self.client.restart_train_task("t1", req=user_req)
        assert result == {"task_id": "t1"}
        call_args = self.mock_http.post.call_args
        posted_req = call_args[1]["json"]
        assert posted_req["description"] == "updated"
        assert posted_req["spec"] == "Ascend: 2 * SNT9B2"
        assert posted_req["name"] == "Train-job"
        assert posted_req["workspace_id"] == "ws-test"

    def test_save_draft(self):
        self.mock_http.post.return_value = {"payload": {"item": {"task_id": "draft1"}}}
        result = self.client.save_draft({"name": "my-draft"})
        assert result == {"task_id": "draft1"}
        self.mock_http.post.assert_called_with(
            f"{BASE}/v1/training/train-tasks/draft", json={"name": "my-draft", "workspace_id": "ws-test"}
        )

    def test_update_train_task(self):
        self.mock_http.patch.return_value = {"payload": {"item": {"task_id": "t1"}}}
        result = self.client.update_train_task("t1", {"description": "updated"})
        assert result == {"task_id": "t1"}
        self.mock_http.patch.assert_called_with(
            f"{BASE}/v1/training/train-tasks/t1", json={"description": "updated"}
        )

    def test_show_train_task(self):
        self.mock_http.get.return_value = {"task_id": "t1"}
        result = self.client.show_train_task("t1", run_id="r1")
        assert result == {"task_id": "t1"}
        self.mock_http.get.assert_called_with(
            f"{BASE}/v1/training/train-tasks/t1", params={"run_id": "r1"}
        )

    def test_list_train_stages(self):
        self.mock_http.get.return_value = {"payload": {"list": [], "page_info": {}}}
        result = self.client.list_train_stages("t1")
        assert result == {"list": [], "page_info": {}}
        self.mock_http.get.assert_called_with(f"{BASE}/v1/training/train-tasks/t1/stages")

    def test_show_resource_usage(self):
        self.mock_http.get.return_value = {"payload": {"list": [], "page_info": {}}}
        result = self.client.show_resource_usage("t1", "cpu_util", 100, 200, worker_index=1)
        assert result == {"list": [], "page_info": {}}
        self.mock_http.get.assert_called_with(
            f"{BASE}/v1/training/train-tasks/t1/resource-usage",
            params={"metric": "cpu_util", "start": 100, "end": 200, "worker_index": 1},
        )

    def test_get_log_signed_url(self):
        self.mock_http.get.return_value = {"signed_url": "https://example.com"}
        result = self.client.get_log_signed_url("t1", "TRAIN", "worker0.log", catalog="logs")
        assert result == {"signed_url": "https://example.com"}
        self.mock_http.get.assert_called_with(
            f"{BASE}/v1/training/train-tasks/t1/observability/signed-url",
            params={"file_source": "TRAIN", "file_name": "worker0.log", "catalog": "logs"},
        )

    def test_get_log_content(self):
        self.mock_http.get.return_value = {"payload": {"list": [{"log_content": "..."}], "page_info": {}}}
        result = self.client.get_log_content("t1", file_name="worker0.log", catalog="logs")
        assert result == {"list": [{"log_content": "..."}], "page_info": {}}
        self.mock_http.get.assert_called_with(
            f"{BASE}/v1/training/train-tasks/t1/observability/content",
            params={"file_name": "worker0.log", "catalog": "logs"},
        )

    def test_list_events(self):
        self.mock_http.get.return_value = {"payload": {"list": [], "page_info": {}}}
        result = self.client.list_events("t1", 100, 200, level="Info")
        assert result == {"list": [], "page_info": {}}
        self.mock_http.get.assert_called_with(
            f"{BASE}/v1/training/train-tasks/t1/events",
            params={"start_time": 100, "end_time": 200, "level": "Info"},
        )

    def test_list_observations(self):
        self.mock_http.get.return_value = {"payload": {"list": [], "page_info": {}}}
        result = self.client.list_observations("t1", file_name="worker0.log")
        assert result == {"list": [], "page_info": {}}
        self.mock_http.get.assert_called_with(
            f"{BASE}/v1/training/train-tasks/t1/observability",
            params={"file_name": "worker0.log"},
        )

    def test_list_train_checkpoints(self):
        self.mock_http.get.return_value = {"checkpoints": [], "total_count": 0}
        result = self.client.list_train_checkpoints("t1", status="SUCCESS", limit=10)
        assert result == {"checkpoints": [], "total_count": 0}
        self.mock_http.get.assert_called_with(
            f"{BASE}/v1/training/train-tasks/t1/checkpoints",
            params={"status": "SUCCESS", "limit": 10},
        )

    def test_register_train_checkpoint(self):
        self.mock_http.post.return_value = {"id": "reg1", "status": "PENDING"}
        result = self.client.register_train_checkpoint("t1", {"save_mode": "NEW_VERSION", "checkpoint_name": "ckpt_1000"})
        assert result == {"id": "reg1", "status": "PENDING"}
        self.mock_http.post.assert_called_with(
            f"{BASE}/v1/training/train-tasks/t1/checkpoints/register",
            json={"save_mode": "NEW_VERSION", "checkpoint_name": "ckpt_1000"},
        )

    def test_create_train_task_missing_required(self):
        with pytest.raises(ValueError, match="训练任务"):
            self.client.create_train_task({"name": "incomplete"})

    def test_save_draft_missing_required(self):
        with pytest.raises(ValueError, match="训练任务草稿"):
            self.client.save_draft({})

    def test_register_checkpoint_new_model_without_name(self):
        with pytest.raises(ValueError, match="NEW_MODEL"):
            self.client.register_train_checkpoint("t1", {"save_mode": "NEW_MODEL", "checkpoint_name": "ckpt_1000"})

    def test_restart_train_task_missing_required(self):
        self.mock_http.get.return_value = {
            "name": "Train-job",
            "spec": "Ascend: 1 * SNT9B2",
            "train_mode": "MODEL_TUNING",
            "workspace_id": "ws-old",
            "task_id": "t1",
            "status": "FINISHED",
        }
        with pytest.raises(ValueError, match="训练任务重训"):
            self.client.restart_train_task("t1")


class TestSimRLClient:
    def setup_method(self):
        self.mock_http = _make_mock_client()
        self.client = TrainClient(self.mock_http)

    def test_count_sim_rl_tasks_by_status(self):
        self.mock_http.get.return_value = {"total": 0, "count_by_status": {}}
        self.client.count_sim_rl_tasks_by_status("ws-1")
        self.mock_http.get.assert_called_with(
            f"{BASE}/v1/training/rl-tasks/simulation/stats",
            params={"workspace_id": "ws-1"},
        )

    def test_list_sim_rl_tasks(self):
        self.mock_http.get.return_value = {"payload": {"list": [], "page_info": {}}}
        result = self.client.list_sim_rl_tasks(status=["RUNNING"])
        assert result == {"list": [], "page_info": {}}
        self.mock_http.get.assert_called_with(
            f"{BASE}/v1/training/rl-tasks/simulation",
            params={"status": ["RUNNING"], "workspace_id": "ws-test"},
        )

    def test_create_sim_rl_task(self):
        self.mock_http.post.return_value = {"payload": {"item": {"task_id": "s1"}}}
        req = {"name": "sim-job", "config_mode": "SIMPLE", "spec": "Ascend: 1 * SNT9B2",
               "input_models": [{"model_asset_id": "m1"}], "output_models": [{"model_name": "out1"}]}
        result = self.client.create_sim_rl_task(req)
        assert result == {"task_id": "s1"}
        self.mock_http.post.assert_called_with(
            f"{BASE}/v1/training/rl-tasks/simulation", json={**req, "workspace_id": "ws-test"}
        )

    def test_create_sim_rl_task_draft(self):
        self.mock_http.post.return_value = {"payload": {"item": {"task_id": "d1"}}}
        result = self.client.create_sim_rl_task_draft({"name": "sim-draft"})
        assert result == {"task_id": "d1"}
        self.mock_http.post.assert_called_with(
            f"{BASE}/v1/training/rl-tasks/simulation/draft", json={"name": "sim-draft", "workspace_id": "ws-test"}
        )

    def test_show_sim_rl_task(self):
        self.mock_http.get.return_value = {"payload": {"item": {"task_id": "s1"}}}
        result = self.client.show_sim_rl_task("s1")
        assert result == {"task_id": "s1"}
        self.mock_http.get.assert_called_with(f"{BASE}/v1/training/rl-tasks/simulation/s1")

    def test_update_sim_rl_task(self):
        self.mock_http.patch.return_value = {"payload": {"item": {"task_id": "s1"}}}
        result = self.client.update_sim_rl_task("s1", {"description": "x"})
        assert result == {"task_id": "s1"}
        self.mock_http.patch.assert_called_with(
            f"{BASE}/v1/training/rl-tasks/simulation/s1", json={"description": "x"}
        )

    def test_delete_sim_rl_task(self):
        self.mock_http.delete.return_value = None
        result = self.client.delete_sim_rl_task("s1")
        assert result is None
        self.mock_http.delete.assert_called_with(f"{BASE}/v1/training/rl-tasks/simulation/s1")

    def test_stop_sim_rl_task(self):
        self.mock_http.post.return_value = {"payload": {"item": {"task_id": "s1"}}}
        result = self.client.stop_sim_rl_task("s1")
        assert result == {"task_id": "s1"}
        self.mock_http.post.assert_called_with(
            f"{BASE}/v1/training/rl-tasks/simulation/s1/stop"
        )

    def test_copy_sim_rl_task(self):
        task_detail = {
            "payload": {"item": {
                "name": "SimRL-job",
                "workspace_id": "ws-test",
                "input_models": [{"model_asset_id": "m1", "model_type": "vla", "save_mode": "NEW_VERSION"}],
                "task_set": "LIBERO_SPATIAL",
                "config_mode": "SIMPLE",
                "spec": "Ascend: 1 * SNT9B2",
                "output_models": [{
                    "model_asset_id": "ma1",
                    "model_name": "out1",
                    "model_type": "vla",
                    "save_mode": "NEW_VERSION",
                    "version_id": "v1",
                    "version_name": "v1.0",
                    "skills": [],
                    "strict": False,
                    "source_type": "CUSTOM_MODEL_ASSET",
                    "local_dir": "/home/user/input",
                    "generation_method": "TRAIN",
                }],
                "cluster_id": "pool-a1b2c3d4-e5f6-7890-g1h2-i3j4k5l6m7n8",
                "enable_jupyter": True,
                "task_id": "s1",
                "status": "STOPPED",
            }}
        }
        versions_resp = {"payload": {"list": [{"version": "v1.0"}]}}
        self.mock_http.get.side_effect = [task_detail, versions_resp]
        self.mock_http.post.return_value = {"payload": {"item": {"task_id": "s2"}}}
        result = self.client.copy_sim_rl_task("s1")
        assert result == {"task_id": "s2"}
        call_args = self.mock_http.post.call_args
        assert call_args[0][0] == f"{BASE}/v1/training/rl-tasks/simulation/s1/copy"
        body = call_args[1]["json"]
        assert body["name"].startswith("SimRL-job-copy-")
        assert len(body["name"].split("-")[-1]) == 4
        assert body["workspace_id"] == "ws-test"
        assert body["input_models"] == [{"model_asset_id": "m1"}]
        assert body["config_mode"] == "SIMPLE"
        assert body["spec"] == "Ascend: 1 * SNT9B2"
        assert body["output_models"] == [{
            "model_asset_id": "ma1",
            "model_name": "out1",
            "model_type": "vla",
            "save_mode": "NEW_VERSION",
            "version_name": "v1.1",
            "skills": [],
            "strict": False,
        }]

    def test_copy_sim_rl_task_with_custom_name(self):
        self.mock_http.get.return_value = {
            "payload": {"item": {
                "name": "SimRL-job",
                "workspace_id": "ws-test",
                "input_models": [{"model_asset_id": "m1"}],
                "task_set": "LIBERO_SPATIAL",
                "config_mode": "SIMPLE",
                "spec": "Ascend: 1 * SNT9B2",
                "output_models": [{"model_name": "out1"}],
                "task_id": "s1",
                "status": "STOPPED",
            }}
        }
        self.mock_http.post.return_value = {"payload": {"item": {"task_id": "s2"}}}
        result = self.client.copy_sim_rl_task("s1", req={"name": "my-custom-name"})
        assert result == {"task_id": "s2"}
        call_args = self.mock_http.post.call_args
        body = call_args[1]["json"]
        assert body["name"] == "my-custom-name"

    def test_copy_sim_rl_task_cleans_output_models_from_req(self):
        task_detail = {
            "payload": {"item": {
                "name": "SimRL-job",
                "workspace_id": "ws-test",
                "input_models": [{"model_asset_id": "m1"}],
                "config_mode": "SIMPLE",
                "spec": "Ascend: 1 * SNT9B2",
                "output_models": [{
                    "model_asset_id": "ma1",
                    "model_name": "out1",
                    "model_type": "vla",
                    "save_mode": "NEW_VERSION",
                    "version_id": "v1",
                    "version_name": "11113.1",
                    "skills": [],
                    "strict": False,
                }],
                "task_id": "s1",
                "status": "STOPPED",
            }}
        }
        versions_resp = {"payload": {"list": [{"version": "11113.1"}]}}
        self.mock_http.get.side_effect = [task_detail, versions_resp]
        self.mock_http.post.return_value = {"payload": {"item": {"task_id": "s2"}}}
        req_with_runtime = {"output_models": [{
            "model_asset_id": "ma1",
            "model_name": "out1",
            "model_type": "vla",
            "save_mode": "NEW_VERSION",
            "version_name": "11113.1",
            "skills": [],
            "strict": False,
            "version_id": "v1",
            "source_type": "CUSTOM_MODEL_ASSET",
        }]}
        result = self.client.copy_sim_rl_task("s1", req=req_with_runtime)
        assert result == {"task_id": "s2"}
        body = self.mock_http.post.call_args[1]["json"]
        assert body["output_models"] == [{
            "model_asset_id": "ma1",
            "model_name": "out1",
            "model_type": "vla",
            "save_mode": "NEW_VERSION",
            "version_name": "11113.2",
            "skills": [],
            "strict": False,
        }]

    def test_restart_sim_rl_task(self):
        self.mock_http.get.return_value = {
            "payload": {"item": {
                "name": "SimRL-job",
                "workspace_id": "ws-old",
                "input_models": [{"model_asset_id": "m1"}],
                "task_set": "LIBERO_SPATIAL",
                "config_mode": "SIMPLE",
                "spec": "Ascend: 1 * SNT9B2",
                "output_models": [{"model_name": "out1"}],
                "cluster_id": "pool-a1b2c3d4-e5f6-7890-g1h2-i3j4k5l6m7n8",
                "enable_jupyter": True,
                "task_id": "s1",
                "status": "STOPPED",
            }}
        }
        self.mock_http.post.return_value = {"payload": {"item": {"task_id": "s1"}}}
        result = self.client.restart_sim_rl_task("s1")
        assert result == {"task_id": "s1"}
        self.mock_http.post.assert_called_with(
            f"{BASE}/v1/training/rl-tasks/simulation/s1/restart",
            json={
                "name": "SimRL-job",
                "input_models": [{"model_asset_id": "m1"}],
                "task_set": "LIBERO_SPATIAL",
                "config_mode": "SIMPLE",
                "spec": "Ascend: 1 * SNT9B2",
                "output_models": [{"model_name": "out1"}],
                "cluster_id": "pool-a1b2c3d4-e5f6-7890-g1h2-i3j4k5l6m7n8",
                "enable_jupyter": True,
                "workspace_id": "ws-test",
            },
        )

    def test_restart_sim_rl_task_with_req(self):
        self.mock_http.get.return_value = {
            "payload": {"item": {
                "name": "SimRL-job",
                "workspace_id": "ws-old",
                "input_models": [{"model_asset_id": "m1"}],
                "task_set": "LIBERO_SPATIAL",
                "config_mode": "SIMPLE",
                "spec": "Ascend: 1 * SNT9B2",
                "output_models": [{"model_name": "out1"}],
                "cluster_id": "pool-a1b2c3d4-e5f6-7890-g1h2-i3j4k5l6m7n8",
                "enable_jupyter": True,
                "task_id": "s1",
                "status": "DRAFT",
            }}
        }
        self.mock_http.post.return_value = {"payload": {"item": {"task_id": "s1"}}}
        user_req = {"description": "updated", "spec": "Ascend: 2 * SNT9B2"}
        result = self.client.restart_sim_rl_task("s1", req=user_req)
        assert result == {"task_id": "s1"}
        call_args = self.mock_http.post.call_args
        posted_req = call_args[1]["json"]
        assert posted_req["description"] == "updated"
        assert posted_req["spec"] == "Ascend: 2 * SNT9B2"
        assert posted_req["name"] == "SimRL-job"
        assert posted_req["workspace_id"] == "ws-test"

    def test_show_sim_rl_task_resource_usage(self):
        self.mock_http.get.return_value = {"payload": {"list": [], "page_info": {}}}
        result = self.client.show_sim_rl_task_resource_usage("s1", "gpu_util", 10, 20)
        assert result == {"list": [], "page_info": {}}
        self.mock_http.get.assert_called_with(
            f"{BASE}/v1/training/rl-tasks/simulation/s1/resource-usage",
            params={"metric": "gpu_util", "start": 10, "end": 20},
        )

    def test_list_sim_rl_task_events(self):
        self.mock_http.get.return_value = {"payload": {"list": [], "page_info": {}}}
        result = self.client.list_sim_rl_task_events("s1", 100, 200)
        assert result == {"list": [], "page_info": {}}
        self.mock_http.get.assert_called_with(
            f"{BASE}/v1/training/rl-tasks/simulation/s1/events",
            params={"start_time": 100, "end_time": 200},
        )

    def test_show_sim_rl_task_observations_signed_url(self):
        self.mock_http.get.return_value = {"signed_url": "https://example.com"}
        result = self.client.show_sim_rl_task_observations_signed_url("s1", "TRAIN", "worker0.log")
        assert result == {"signed_url": "https://example.com"}
        self.mock_http.get.assert_called_with(
            f"{BASE}/v1/training/rl-tasks/simulation/s1/observability/signed-url",
            params={"file_source": "TRAIN", "file_name": "worker0.log"},
        )

    def test_create_sim_rl_task_missing_required(self):
        with pytest.raises(ValueError, match="仿真强化学习任务"):
            self.client.create_sim_rl_task({"name": "incomplete"})

    def test_copy_sim_rl_task_missing_required(self):
        self.mock_http.get.return_value = {
            "payload": {"item": {
                "name": "SimRL-job",
                "workspace_id": "ws-test",
                "input_models": [{"model_asset_id": "m1"}],
                "config_mode": "SIMPLE",
                "task_id": "s1",
                "status": "STOPPED",
            }}
        }
        with pytest.raises(ValueError, match="仿真强化学习克隆"):
            self.client.copy_sim_rl_task("s1")
