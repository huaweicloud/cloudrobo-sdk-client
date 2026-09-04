import json
import logging
import os
import time
from typing import Any, Callable, Dict, List, Optional

from cloudrobo_core.sdk import BaseClient, Config

logger = logging.getLogger(__name__)


def is_debug_mode() -> bool:
    """检测是否处于 debug 模式"""
    env_val = os.environ.get("CLOUDROBO_DEBUG", "").lower()
    if env_val in ("1", "true", "yes"):
        return True
    return logger.isEnabledFor(logging.DEBUG)


def get_workspace_id(workspace_id: Optional[str] = None) -> Optional[str]:
    """获取 workspace_id：优先使用外部传入的值，其次从系统配置获取"""
    if workspace_id:
        return workspace_id
    config = Config()
    return config.workspace_id if config.workspace_id else None


PROC_TASK_REQUIRED_FIELDS = [
    "name", "algo_type", "algo_name", "algo_entrance", "image",
    "catalog_id", "resource_pool_type", "cluster_type",
    "task_framework_type", "dataset_configs", "output_type",
    "output_name", "head_spec", "worker_spec",
    "worker_num", "evs_spec",
]

EVAL_TASK_REQUIRED_FIELDS = [
    "name", "algo_id", "algo_name", "algo_entrance", "image",
    "dataset_type", "dataset_name", "dataset_path",
    "robot_config", "resource_pool_type", "worker_spec",
]

SPEC_REQUIRED_KEYS = ["cpu", "memory"]


def _validate_task_config(req: Dict, task_type: str = "proc") -> None:
    """校验任务配置参数：除 description 外，其他参数不能为空值

    Args:
        req: 任务配置字典
        task_type: "proc" 或 "eval"

    Raises:
        DatasetError: 当必填字段缺失或为空值时
    """
    if task_type == "proc":
        required = list(PROC_TASK_REQUIRED_FIELDS)
        algo_type = req.get("algo_type", "")
        if algo_type == "OBS_ASSETS":
            required.extend(["algo_path", "job_local_path"])
        else:
            required.append("algo_id")
        output_type = req.get("output_type", "")
        if output_type == "UDF_OBS_ASSET":
            required.extend(["output_path"])
    else:
        required = list(EVAL_TASK_REQUIRED_FIELDS)
        dataset_type = req.get("dataset_type", "")
        if dataset_type == "BUILD_IN_ASSET":
            required.extend(["dataset_id"])

    missing = []
    empty = []

    for field in required:
        value = req.get(field)
        if value is None:
            missing.append(field)
        elif isinstance(value, str) and value.strip() == "":
            missing.append(field)
        elif isinstance(value, dict) and len(value) == 0:
            empty.append(field)

    for field_name in ["dataset_configs"]:
        value = req.get(field_name)
        if isinstance(value, str):
            try:
                parsed = json.loads(value)
                if isinstance(parsed, list) and len(parsed) == 0:
                    empty.append(field_name)
            except (json.JSONDecodeError, TypeError):
                pass

    for spec_field in ["head_spec", "worker_spec"]:
        spec = req.get(spec_field)
        if isinstance(spec, dict) and spec:
            for key in SPEC_REQUIRED_KEYS:
                if key not in spec:
                    empty.append(f"{spec_field}.{key}")

    errors = []
    if missing:
        errors.append(f"缺少必填字段或值为空: {', '.join(missing)}")
    if empty:
        errors.append(f"字段缺少必要子字段: {', '.join(empty)}")

    if errors:
        raise DatasetError(
            "任务配置校验失败: " + "; ".join(errors),
            "除 description 外，所有参数都不能为空值。head_spec/worker_spec 必须包含 cpu/memory 字段"
        )


class DatasetError(Exception):
    """数据集模块自定义异常"""

    def __init__(self, message: str, suggestion: str = ""):
        self.message = message
        self.suggestion = suggestion
        super().__init__(message)

    def get_user_message(self) -> str:
        """获取用户友好的错误消息"""
        if self.suggestion:
            return f"{self.message}\n\n建议: {self.suggestion}"
        return self.message


class DatasetClient(BaseClient):
    SERVICE = "cloudrobo-service"

    def create_task(self, req: Dict, workspace_id: Optional[str] = None) -> Dict:
        """创建数据处理任务"""
        _validate_task_config(req, task_type="proc")
        ws_id = get_workspace_id(workspace_id)
        if not ws_id:
            raise DatasetError(
                "创建任务失败: 缺少 workspace_id 参数",
                "请通过 --workspace-id 指定工作空间，或运行 'cloudrobo workspace use' 设置默认工作空间"
            )
        req["workspace_id"] = ws_id
        return self._client.post(self._url("/v1/data-eng/proc-tasks"), json=req)

    def list_tasks(self, workspace_id: Optional[str] = None, **params) -> Dict:
        """获取数据处理任务列表"""
        ws_id = get_workspace_id(workspace_id)
        if not ws_id:
            raise DatasetError(
                "查询任务失败: 缺少 workspace_id 参数",
                "请通过 --workspace-id 指定工作空间，或运行 'cloudrobo workspace use' 设置默认工作空间"
            )
        params["workspace_id"] = ws_id
        return self._client.get(self._url("/v1/data-eng/proc-tasks"), params=params)

    def delete_tasks(self, task_ids: List[str]) -> Any:
        return self._client.delete(self._url("/v1/data-eng/proc-tasks"), params={"ids": ",".join(task_ids)})

    def update_task(self, task_id: str, req: Dict) -> Dict:
        return self._client.patch(self._url(f"/v1/data-eng/proc-tasks/{task_id}"), json=req)

    def get_task_detail(self, task_id: str) -> Dict:
        return self._client.get(self._url(f"/v1/data-eng/proc-tasks/{task_id}"))

    def restart_task(self, task_id: str) -> Dict:
        return self._client.post(self._url(f"/v1/data-eng/proc-tasks/{task_id}/restart"))

    def list_log_files(self, task_id: str, is_system: bool = True) -> Dict:
        """获取任务日志文件列表"""
        return self._client.get(
            self._url(f"/v1/data-eng/proc-tasks/{task_id}/logs"),
            params={"is_system": str(is_system).lower()}
        )

    def get_task_log(self, task_id: str, file_name: str, start_byte: int = 0,
                     end_byte: int = 1000000, file_path: str = "",
                     job_id: str = "") -> Any:
        """获取任务日志内容

        Args:
            task_id: 任务ID
            file_name: 日志文件名（system-std-output.log 或 job-std-output.log）
            start_byte: 起始字节偏移
            end_byte: 结束字节偏移
            file_path: 日志文件路径（必填，格式: proc-task/logs/{task_id}/{file_name}，
                       通过list_log_files获取）
            job_id: 作业ID（可选）
        """
        return self._client.get(
            self._url(f"/v1/data-eng/proc-tasks/{task_id}/logs/{file_name}"),
            params={k: v for k, v in {
                "start_byte": start_byte, "end_byte": end_byte,
                "file_path": file_path or f"proc-task/logs/{task_id}/{file_name}",
                "job_id": job_id,
            }.items() if v != "" and v is not None}
        )

    def get_task_log_tail(self, task_id: str, file_name: str, tail_bytes: int = 65536,
                          file_path: str = "", job_id: str = "") -> Any:
        """获取任务日志尾部内容（默认最新64KB）

        先请求获取总大小，再从尾部读取。小文件直接返回全部内容。

        Args:
            task_id: 任务ID
            file_name: 日志文件名
            tail_bytes: 从尾部读取的字节数，默认65536(64KB)
            file_path: 日志文件路径
            job_id: 作业ID（可选）
        """
        resolved_path = file_path or f"proc-task/logs/{task_id}/{file_name}"
        result = self._client.get(
            self._url(f"/v1/data-eng/proc-tasks/{task_id}/logs/{file_name}"),
            params={k: v for k, v in {
                "start_byte": 0, "end_byte": tail_bytes,
                "file_path": resolved_path, "job_id": job_id,
            }.items() if v != "" and v is not None}
        )
        payload = result.get("payload", result)
        item = payload.get("item", {}) if isinstance(payload, dict) else {}
        total_size = item.get("end_byte", 0)
        if total_size < tail_bytes:
            return result
        return self._client.get(
            self._url(f"/v1/data-eng/proc-tasks/{task_id}/logs/{file_name}"),
            params={k: v for k, v in {
                "start_byte": max(0, total_size - tail_bytes), "end_byte": total_size,
                "file_path": resolved_path, "job_id": job_id,
            }.items() if v != "" and v is not None}
        )

    def get_task_frames(self, task_id: str, prefix: str = "") -> Dict:
        return self._client.get(
            self._url(f"/v1/data-eng/proc-tasks/{task_id}/frames"),
            params={"prefix": prefix} if prefix else None
        )

    def get_task_preview(self, task_id: str, file_name: str) -> Dict:
        return self._client.get(
            self._url(f"/v1/data-eng/proc-tasks/{task_id}/preview"),
            params={"file_name": file_name}
        )

    def get_task_resource_usage(self, task_id: str, metric: str, start: int, end: int, step: int,
                                pod_name: str) -> Dict:
        """获取数据处理任务的资源监控数据

        Args:
            task_id: 任务ID
            metric: 指标类型 (CPU_UTIL/CPU_USED_CORE/MEM_UTIL/MEM_USED_MB/NETWORK_TX_RATE/NETWORK_RX_RATE/DISK_READ_KB/DISK_WRITE_KB)
            start: 起始时间戳（秒）
            end: 结束时间戳（秒）
            step: 采样间隔（秒），范围 10-3600
            pod_name: 容器名
        """
        return self._client.get(
            self._url(f"/v1/data-eng/proc-tasks/{task_id}/resource-usage"),
            params={"metric": metric, "start": start, "end": end, "step": step, "pod_name": pod_name}
        )

    def download_task_log(self, task_id: str, file_name: str, file_path: str) -> Any:
        """下载任务日志文件"""
        return self._client.get(
            self._url(f"/v1/data-eng/proc-tasks/{task_id}/logs/{file_name}/download"),
            params={"file_path": file_path}
        )

    # ---- eval-tasks ----

    def create_eval_task(self, req: Dict, workspace_id: Optional[str] = None) -> Dict:
        """创建数据评测任务"""
        _validate_task_config(req, task_type="eval")
        ws_id = get_workspace_id(workspace_id)
        if not ws_id:
            raise DatasetError(
                "创建评测任务失败: 缺少 workspace_id 参数",
                "请通过 --workspace-id 指定工作空间，或运行 'cloudrobo workspace use' 设置默认工作空间"
            )
        req["workspace_id"] = ws_id
        return self._client.post(self._url("/v1/data-eng/eval-tasks"), json=req)

    def list_eval_tasks(self, workspace_id: Optional[str] = None, **params) -> Dict:
        """获取数据评测任务列表"""
        ws_id = get_workspace_id(workspace_id)
        if not ws_id:
            raise DatasetError(
                "查询评测任务失败: 缺少 workspace_id 参数",
                "请通过 --workspace-id 指定工作空间，或运行 'cloudrobo workspace use' 设置默认工作空间"
            )
        params["workspace_id"] = ws_id
        return self._client.get(self._url("/v1/data-eng/eval-tasks"), params=params)

    def get_eval_task_detail(self, task_id: str) -> Dict:
        return self._client.get(self._url(f"/v1/data-eng/eval-tasks/{task_id}"))

    def update_eval_task(self, task_id: str, req: Dict) -> Dict:
        return self._client.patch(self._url(f"/v1/data-eng/eval-tasks/{task_id}"), json=req)

    def delete_eval_task(self, task_id: str) -> Dict:
        return self._client.delete(self._url(f"/v1/data-eng/eval-tasks/{task_id}"))

    def list_eval_log_files(self, task_id: str, is_system: bool = True) -> Dict:
        return self._client.get(
            self._url(f"/v1/data-eng/eval-tasks/{task_id}/logs"),
            params={"is_system": str(is_system).lower()}
        )

    def get_eval_task_log(self, task_id: str, file_name: str, start_byte: int = 0,
                          end_byte: int = 1000000, file_path: str = "",
                          job_id: str = "") -> Any:
        return self._client.get(
            self._url(f"/v1/data-eng/eval-tasks/{task_id}/logs/{file_name}"),
            params={k: v for k, v in {
                "start_byte": start_byte, "end_byte": end_byte,
                "file_path": file_path or f"proc-task/logs/{task_id}/{file_name}",
                "job_id": job_id,
            }.items() if v != "" and v is not None}
        )

    def get_eval_task_log_tail(self, task_id: str, file_name: str, tail_bytes: int = 65536,
                               file_path: str = "", job_id: str = "") -> Any:
        """获取任务日志尾部内容（默认最新64KB）

        先请求获取总大小，再从尾部读取。小文件直接返回全部内容。

        Args:
            task_id: 任务ID
            file_name: 日志文件名
            tail_bytes: 从尾部读取的字节数，默认65536(64KB)
            file_path: 日志文件路径
            job_id: 作业ID（可选）
        """
        resolved_path = file_path or f"proc-task/logs/{task_id}/{file_name}"
        result = self._client.get(
            self._url(f"/v1/data-eng/eval-tasks/{task_id}/logs/{file_name}"),
            params={k: v for k, v in {
                "start_byte": 0, "end_byte": tail_bytes,
                "file_path": resolved_path, "job_id": job_id,
            }.items() if v != "" and v is not None}
        )
        payload = result.get("payload", result)
        item = payload.get("item", {}) if isinstance(payload, dict) else {}
        total_size = item.get("end_byte", 0)
        if total_size < tail_bytes:
            return result
        return self._client.get(
            self._url(f"/v1/data-eng/eval-tasks/{task_id}/logs/{file_name}"),
            params={k: v for k, v in {
                "start_byte": max(0, total_size - tail_bytes), "end_byte": total_size,
                "file_path": resolved_path, "job_id": job_id,
            }.items() if v != "" and v is not None}
        )

    def get_eval_task_preview(self, task_id: str, file_name: str, is_download: bool = False) -> Dict:
        """获取评测报告的 OBS 临时链接

        Args:
            task_id: 评测任务ID
            file_name: 评测报告文件名
            is_download: 是否下载（True=下载，False=预览）
        """
        return self._client.get(
            self._url(f"/v1/data-eng/eval-tasks/{task_id}/preview"),
            params={"file_name": file_name, "isDownload": is_download}
        )

    def download_eval_task_log(self, task_id: str, file_name: str, file_path: str) -> Any:
        """下载评测任务日志文件"""
        return self._client.get(
            self._url(f"/v1/data-eng/eval-tasks/{task_id}/logs/{file_name}/download"),
            params={"file_path": file_path}
        )

    TERMINAL_STATES = {"SUCCEEDED", "FAILED", "DELETED"}

    def wait_task(self, task_id: str, timeout: int = 1800, interval: int = 10,
                  on_status: Optional[Callable[[str, Dict], None]] = None) -> Dict:
        """轮询等待任务到达终态。

        Args:
            task_id: 任务ID
            timeout: 超时秒数，默认1800
            interval: 轮询间隔秒数，默认10
            on_status: 状态变更回调，参数为 (new_status, detail)

        Returns:
            最终任务详情
        """
        elapsed = 0
        last_status = None
        while elapsed < timeout:
            detail = self.get_task_detail(task_id)
            status = detail.get("payload", detail).get("status", "UNKNOWN")
            if status != last_status:
                if on_status:
                    on_status(status, detail)
                else:
                    logger.info(f"Task {task_id}: {last_status} → {status}")
                last_status = status
            if status in self.TERMINAL_STATES:
                return detail
            time.sleep(interval)
            elapsed += interval
        return detail

    def wait_eval_task(self, task_id: str, timeout: int = 1800, interval: int = 10,
                       on_status: Optional[Callable[[str, Dict], None]] = None) -> Dict:
        """轮询等待评测任务到达终态。

        Args:
            task_id: 评测任务ID
            timeout: 超时秒数，默认1800
            interval: 轮询间隔秒数，默认10
            on_status: 状态变更回调，参数为 (new_status, detail)

        Returns:
            最终任务详情
        """
        elapsed = 0
        last_status = None
        while elapsed < timeout:
            detail = self.get_eval_task_detail(task_id)
            status = detail.get("payload", detail).get("status", "UNKNOWN")
            if status != last_status:
                if on_status:
                    on_status(status, detail)
                else:
                    logger.info(f"Eval task {task_id}: {last_status} → {status}")
                last_status = status
            if status in self.TERMINAL_STATES:
                return detail
            time.sleep(interval)
            elapsed += interval
        return detail
