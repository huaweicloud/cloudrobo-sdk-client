import json
import json
import logging
import uuid
from typing import Any, Dict, List, Optional

from cloudrobo_core.sdk import BaseClient

logger = logging.getLogger(__name__)

_TRAIN_TASK_DTO_FIELDS = {
    "name", "run_user", "algorithm", "parameters", "env", "datasets",
    "worker_num", "spec", "description", "train_mode", "train_method",
    "output_models", "input_models", "inputs", "outputs", "cluster_id",
    "workspace_id", "log_path", "checkpoint_max_keep", "output_retention_days",
    "enable_jupyter",
}

_SIM_RL_TASK_DTO_FIELDS = {
    "name", "description", "workspace_id", "input_models", "task_set",
    "config_mode", "simple_params", "rl_config_content", "spec",
    "worker_num", "output_models", "cluster_id", "enable_jupyter",
}

_OUTPUT_MODEL_SUBMIT_FIELDS = {
    "model_asset_id", "model_name", "model_type", "save_mode",
    "version_name", "skills", "strict",
}

_INPUT_MODEL_SUBMIT_FIELDS = {
    "model_asset_id", "version_id", "source_type", "model_name", "version_name",
}

_TRAIN_REQUIRED_FIELDS = {"algorithm", "name", "spec", "train_mode", "workspace_id"}
_SIM_RL_REQUIRED_FIELDS = {"name", "workspace_id", "config_mode", "spec", "input_models", "output_models"}
_DRAFT_TRAIN_REQUIRED_FIELDS = {"name", "workspace_id"}
_DRAFT_SIM_RL_REQUIRED_FIELDS = {"name", "workspace_id"}


def _validate_required_fields(config: Dict, required_fields: set, task_type: str):
    """校验配置中的必填字段，缺失则抛出 ValueError"""
    missing = required_fields - {k for k, v in config.items() if v is not None and v != "" and v != []}
    if missing:
        raise ValueError(
            f"{task_type}缺少必要字段: {', '.join(sorted(missing))}。"
            f"请通过 --config 或 --config-file 补充缺失字段。"
        )


def _extract_dto(task_detail: Dict, dto_fields: set) -> Dict:
    """从任务详情中提取 DTO 所需字段"""
    return {k: v for k, v in task_detail.items() if k in dto_fields and v is not None}


def _increment_version(version_name: str) -> str:
    """递增版本号，取末尾数字部分加一。
    - 11113 → 11114
    - v0.0.33 → v0.0.34
    - v1.0 → v1.1
    """
    import re
    m = re.search(r"(\d+)(?!.*\d)", version_name)
    if not m:
        return version_name + ".1"
    last_num = m.group(1)
    start, end = m.start(1), m.end(1)
    return version_name[:start] + str(int(last_num) + 1) + version_name[end:]


def _serialize_string_typed_json_fields(base_req: Dict):
    """将字符串化的 JSON 字段从 list/dict 序列化为 JSON 字符串。
    API schema 中某些字段定义为 type: string，但实际存储 JSON 结构。
    """
    for field in ("simple_params", "rl_config_content"):
        if field in base_req and isinstance(base_req[field], (list, dict)):
            base_req[field] = json.dumps(base_req[field], ensure_ascii=False)


def _clean_models(base_req: Dict, http_client=None):
    """清理 input_models 和 output_models，只保留提交字段。
    同时处理 output_models 的 version_name 递增和 save_mode 转换：
    - NEW_MODEL → NEW_VERSION，查询最新版本后加一
    - NEW_VERSION → 查询最新版本后加一
    """
    if "input_models" in base_req:
        base_req["input_models"] = [
            {k: v for k, v in m.items() if k in _INPUT_MODEL_SUBMIT_FIELDS}
            for m in base_req["input_models"]
        ]
    if "output_models" in base_req:
        cleaned = []
        for m in base_req["output_models"]:
            cleaned_model = {k: v for k, v in m.items() if k in _OUTPUT_MODEL_SUBMIT_FIELDS}
            save_mode = cleaned_model.get("save_mode")
            if save_mode in ("NEW_MODEL", "NEW_VERSION"):
                if save_mode == "NEW_MODEL":
                    cleaned_model["save_mode"] = "NEW_VERSION"
                # 查询最新版本并加一
                if http_client and cleaned_model.get("model_asset_id"):
                    latest_version = _get_latest_version(http_client, cleaned_model["model_asset_id"])
                    if latest_version:
                        cleaned_model["version_name"] = _increment_version(latest_version)
            cleaned.append(cleaned_model)
        base_req["output_models"] = cleaned


def _get_latest_version(http_client, model_asset_id: str) -> Optional[str]:
    """查询模型资产的最新版本号"""
    try:
        from cloudrobo_asset.client import AssetClient
        asset_client = AssetClient(http_client)
        result = asset_client.list_asset_versions(
            model_asset_id,
            sort_key="version",
            sort_dir="desc",
            limit=1
        )
        versions = result.get("data", [])
        if versions:
            return versions[0].get("version")
    except Exception as e:
        logger.warning(f"查询模型版本失败: {e}")
    return None


def _unwrap_payload(resp, item: bool = False):
    """从标准响应信封中解出 payload。

    item=True 时进一步解出 payload.item（用于单条详情查询）。
    非标准响应（无 payload 键）原样返回。
    """
    if not isinstance(resp, dict) or "payload" not in resp:
        return resp
    payload = resp["payload"]
    if item and isinstance(payload, dict) and "item" in payload:
        return payload["item"]
    return payload


class TrainClient(BaseClient):
    SERVICE = "cloudrobo-service"

    _TASKS = "/v1/training/train-tasks"
    _SIM = "/v1/training/rl-tasks/simulation"

    def _resolve_workspace_id(self, workspace_id: Optional[str] = None) -> str:
        if workspace_id:
            return workspace_id
        ws_id = getattr(self._client.config, "workspace_id", "")
        if ws_id:
            return ws_id
        from cloudrobo_workspace.client import WorkspaceClient
        ws_client = WorkspaceClient(self._client)
        result = ws_client.list_workspaces()
        workspaces = result.get("workspaces", []) if isinstance(result, dict) else []
        if not workspaces:
            raise ValueError(
                "未找到可用工作空间。请先运行 'cloudrobo workspace list' 查看可用工作空间，"
                "再运行 'cloudrobo workspace use --workspace-id <id>' 设置默认工作空间"
            )
        ws = workspaces[0]
        ws_id = ws.get("workspace_id", "")
        if not ws_id:
            raise ValueError("工作空间列表返回数据缺少 workspace_id 字段")
        from cloudrobo_workspace.config import save_workspace
        save_workspace({
            "workspace_id": ws_id,
            "name": ws.get("name", ""),
            "asset_catalog_id": ws.get("asset_catalog_id", ""),
            "default_obs_path": ws.get("default_obs_path", ""),
        })
        logger.info("自动配置工作空间: %s (%s)", ws.get("name", ""), ws_id)
        return ws_id

    def create_train_task(self, req: Dict, workspace_id: Optional[str] = None) -> Dict:
        req.setdefault("workspace_id", self._resolve_workspace_id(workspace_id))
        _validate_required_fields(req, _TRAIN_REQUIRED_FIELDS, "训练任务")
        return _unwrap_payload(self._client.post(self._url(self._TASKS), json=req), item=True)

    def list_train_tasks(self, **params) -> Dict:
        if not params.get("workspace_id"):
            params["workspace_id"] = self._resolve_workspace_id(params.get("workspace_id"))
        return _unwrap_payload(self._client.get(self._url(self._TASKS), params=params))

    def batch_delete_train_tasks(self, execution_ids: List[str]) -> Any:
        return self._client.post(
            self._url("/v1/training/train-tasks/batch-delete"),
            json={"execution_ids": execution_ids},
        )

    def count_train_tasks_by_status(self, workspace_id: Optional[str] = None, user_id: Optional[str] = None) -> Dict:
        ws_id = self._resolve_workspace_id(workspace_id)
        params = {"workspace_id": ws_id}
        if user_id:
            params["user_id"] = user_id
        return self._client.get(self._url("/v1/training/train-tasks/stats"), params=params)

    def resume_train_task(self, task_id: str) -> Dict:
        return _unwrap_payload(self._client.post(self._url(f"/v1/training/train-tasks/{task_id}/resume")), item=True)

    def stop_train_task(self, task_id: str) -> Dict:
        return _unwrap_payload(self._client.post(self._url(f"{self._TASKS}/{task_id}/stop")), item=True)

    def restart_train_task(self, task_id: str, req: Optional[Dict] = None,
                            workspace_id: Optional[str] = None, task_detail: Optional[Dict] = None) -> Dict:
        if task_detail is None:
            task_detail = self.show_train_task(task_id)
        base_req = _extract_dto(task_detail, _TRAIN_TASK_DTO_FIELDS)
        if req:
            base_req.update(req)
        base_req["workspace_id"] = self._resolve_workspace_id(workspace_id)
        _serialize_string_typed_json_fields(base_req)
        _clean_models(base_req, self._client)
        _validate_required_fields(base_req, _TRAIN_REQUIRED_FIELDS, "训练任务重训")
        return _unwrap_payload(self._client.post(self._url(f"{self._TASKS}/{task_id}/restart"), json=base_req),
                               item=True)

    def save_draft(self, req: Dict, workspace_id: Optional[str] = None) -> Dict:
        req.setdefault("workspace_id", self._resolve_workspace_id(workspace_id))
        _validate_required_fields(req, _DRAFT_TRAIN_REQUIRED_FIELDS, "训练任务草稿")
        return _unwrap_payload(self._client.post(self._url(f"{self._TASKS}/draft"), json=req), item=True)

    def update_train_task(self, task_id: str, req: Dict) -> Dict:
        return _unwrap_payload(self._client.patch(self._url(f"{self._TASKS}/{task_id}"), json=req), item=True)

    def show_train_task(self, task_id: str, **params) -> Dict:
        return self._client.get(self._url(f"/v1/training/train-tasks/{task_id}"), params=params)

    def list_train_stages(self, task_id: str) -> Dict:
        return _unwrap_payload(self._client.get(self._url(f"{self._TASKS}/{task_id}/stages")))

    def show_resource_usage(self, task_id: str, metric: str, start: int, end: int, **params) -> Dict:
        params = {"metric": metric, "start": start, "end": end, **params}
        return _unwrap_payload(self._client.get(self._url(f"{self._TASKS}/{task_id}/resource-usage"), params=params))

    def list_observations(self, task_id: str, **params) -> Dict:
        return _unwrap_payload(self._client.get(self._url(f"{self._TASKS}/{task_id}/observability"), params=params))

    def get_log_signed_url(self, task_id: str, file_source: str, file_name: str, **params) -> Dict:
        params = {"file_source": file_source, "file_name": file_name, **params}
        return _unwrap_payload(
            self._client.get(self._url(f"{self._TASKS}/{task_id}/observability/signed-url"), params=params))

    def get_log_content(self, task_id: str, **params) -> Dict:
        return _unwrap_payload(
            self._client.get(self._url(f"{self._TASKS}/{task_id}/observability/content"), params=params))

    def list_events(self, task_id: str, start_time: int, end_time: int, **params) -> Dict:
        params = {"start_time": start_time, "end_time": end_time, **params}
        return _unwrap_payload(self._client.get(self._url(f"{self._TASKS}/{task_id}/events"), params=params))

    def list_train_checkpoints(self, task_id: str, **params) -> Dict:
        return self._client.get(self._url(f"{self._TASKS}/{task_id}/checkpoints"), params=params)

    def register_train_checkpoint(self, task_id: str, req: Dict) -> Dict:
        if req.get("save_mode") == "NEW_MODEL" and not req.get("model_name"):
            raise ValueError("save_mode 为 NEW_MODEL 时，model_name 为必填字段")
        return _unwrap_payload(self._client.post(self._url(f"{self._TASKS}/{task_id}/checkpoints/register"), json=req))

    def count_sim_rl_tasks_by_status(self, workspace_id: Optional[str] = None, user_id: Optional[str] = None) -> Dict:
        ws_id = self._resolve_workspace_id(workspace_id)
        params = {"workspace_id": ws_id}
        if user_id:
            params["user_id"] = user_id
        return self._client.get(self._url("/v1/training/rl-tasks/simulation/stats"), params=params)

    def list_sim_rl_tasks(self, **params) -> Dict:
        if not params.get("workspace_id"):
            params["workspace_id"] = self._resolve_workspace_id(params.get("workspace_id"))
        return _unwrap_payload(self._client.get(self._url(self._SIM), params=params))

    def create_sim_rl_task(self, req: Dict, workspace_id: Optional[str] = None) -> Dict:
        req.setdefault("workspace_id", self._resolve_workspace_id(workspace_id))
        _validate_required_fields(req, _SIM_RL_REQUIRED_FIELDS, "仿真强化学习任务")
        return _unwrap_payload(self._client.post(self._url(self._SIM), json=req), item=True)

    def create_sim_rl_task_draft(self, req: Dict, workspace_id: Optional[str] = None) -> Dict:
        req.setdefault("workspace_id", self._resolve_workspace_id(workspace_id))
        _validate_required_fields(req, _DRAFT_SIM_RL_REQUIRED_FIELDS, "仿真强化学习任务草稿")
        return _unwrap_payload(self._client.post(self._url(f"{self._SIM}/draft"), json=req), item=True)

    def show_sim_rl_task(self, task_id: str) -> Dict:
        return _unwrap_payload(self._client.get(self._url(f"{self._SIM}/{task_id}")), item=True)

    def update_sim_rl_task(self, task_id: str, req: Dict) -> Dict:
        return _unwrap_payload(self._client.patch(self._url(f"{self._SIM}/{task_id}"), json=req), item=True)

    def delete_sim_rl_task(self, task_id: str) -> Any:
        return self._client.delete(self._url(f"{self._SIM}/{task_id}"))

    def stop_sim_rl_task(self, task_id: str) -> Dict:
        return _unwrap_payload(self._client.post(self._url(f"{self._SIM}/{task_id}/stop")), item=True)

    def copy_sim_rl_task(self, task_id: str, req: Optional[Dict] = None, task_detail: Optional[Dict] = None) -> Dict:
        if task_detail is None:
            task_detail = self.show_sim_rl_task(task_id)
        base_req = _extract_dto(task_detail, _SIM_RL_TASK_DTO_FIELDS)
        if req:
            base_req.update(req)
        _serialize_string_typed_json_fields(base_req)
        _clean_models(base_req, self._client)
        if not (req and "name" in req):
            suffix = uuid.uuid4().hex[:4]
            base_req["name"] = f"{base_req.get('name', 'task')}-copy-{suffix}"
        _validate_required_fields(base_req, _SIM_RL_REQUIRED_FIELDS, "仿真强化学习克隆")
        return _unwrap_payload(self._client.post(self._url(f"{self._SIM}/{task_id}/copy"), json=base_req), item=True)

    def restart_sim_rl_task(self, task_id: str, req: Optional[Dict] = None, workspace_id: Optional[str] = None,
                            task_detail: Optional[Dict] = None) -> Dict:
        if task_detail is None:
            task_detail = self.show_sim_rl_task(task_id)
        base_req = _extract_dto(task_detail, _SIM_RL_TASK_DTO_FIELDS)
        if req:
            base_req.update(req)
        base_req["workspace_id"] = self._resolve_workspace_id(workspace_id)
        _serialize_string_typed_json_fields(base_req)
        _clean_models(base_req, self._client)
        _validate_required_fields(base_req, _SIM_RL_REQUIRED_FIELDS, "仿真强化学习重训")
        return _unwrap_payload(self._client.post(self._url(f"{self._SIM}/{task_id}/restart"), json=base_req), item=True)

    def show_sim_rl_task_resource_usage(self, task_id: str, metric: str, start: int, end: int, **params) -> Dict:
        params = {"metric": metric, "start": start, "end": end, **params}
        return _unwrap_payload(self._client.get(self._url(f"{self._SIM}/{task_id}/resource-usage"), params=params))

    def list_sim_rl_task_events(self, task_id: str, start_time: int, end_time: int, **params) -> Dict:
        params = {"start_time": start_time, "end_time": end_time, **params}
        return _unwrap_payload(self._client.get(self._url(f"{self._SIM}/{task_id}/events"), params=params))

    def list_sim_rl_task_observations(self, task_id: str, **params) -> Dict:
        return _unwrap_payload(self._client.get(self._url(f"{self._SIM}/{task_id}/observability"), params=params))

    def show_sim_rl_task_observations_content(self, task_id: str, **params) -> Dict:
        return _unwrap_payload(
            self._client.get(self._url(f"{self._SIM}/{task_id}/observability/content"), params=params))

    def show_sim_rl_task_observations_signed_url(self, task_id: str, file_source: str, file_name: str,
                                                 **params) -> Dict:
        params = {"file_source": file_source, "file_name": file_name, **params}
        return _unwrap_payload(
            self._client.get(self._url(f"{self._SIM}/{task_id}/observability/signed-url"), params=params))
