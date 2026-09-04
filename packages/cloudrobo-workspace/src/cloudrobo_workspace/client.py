# Copyright (c) Huawei Technologies Co., Ltd. 2026-2026. All rights reserved.

import logging
import os
from typing import Any

from cloudrobo_core.sdk import BaseClient
from cloudrobo_core.sdk.exceptions import validate_safe_id

logger = logging.getLogger(__name__)


def is_debug_mode() -> bool:
    """检测是否处于 debug 模式"""
    env_val = os.environ.get("CLOUDROBO_DEBUG", "").lower()
    if env_val in ("1", "true", "yes"):
        return True
    return logger.isEnabledFor(logging.DEBUG)


class WorkspaceError(Exception):
    """工作空间模块自定义异常"""

    def __init__(self, message: str, suggestion: str = ""):
        self.message = message
        self.suggestion = suggestion
        super().__init__(message)

    def get_user_message(self) -> str:
        """获取用户友好的错误消息"""
        if self.suggestion:
            return f"{self.message}\n\n建议: {self.suggestion}"
        return self.message


class WorkspaceClient(BaseClient):
    SERVICE = "cloudrobo-service"

    def create_workspace(self, req: dict) -> dict:
        if not isinstance(req, dict) or not req:
            raise WorkspaceError("创建工作空间失败: 请求体不能为空")
        name = req.get("name")
        if not name or not str(name).strip():
            raise WorkspaceError(
                "创建工作空间失败: name 不能为空或纯空格",
                "请通过 --name 指定有效的工作空间名称"
            )
        obs_path = req.get("default_obs_path")
        if not obs_path or not str(obs_path).strip():
            raise WorkspaceError(
                "创建工作空间失败: default_obs_path 不能为空或纯空格",
                "请通过 --default-obs-path 指定有效的OBS路径"
            )
        return self._client.post(self._url("/v1/workspaces"), json=req)

    def list_workspaces(self, **params) -> dict:
        return self._client.get(self._url("/v1/workspaces"), params=params)

    def show_workspace(self, workspace_id: str) -> dict:
        validate_safe_id(workspace_id, "workspace_id")
        return self._client.get(self._url(f"/v1/workspaces/{workspace_id}"))

    def update_workspace(self, workspace_id: str, req: dict) -> dict:
        validate_safe_id(workspace_id, "workspace_id")
        if not isinstance(req, dict) or not req:
            raise WorkspaceError(
                "更新工作空间失败: 请求体不能为空",
                "请至少指定一个要更新的参数，或使用 --bind-obs-policy"
            )
        name = req.get("name")
        if name is not None and not str(name).strip():
            raise WorkspaceError(
                "更新工作空间失败: name 不能为空或纯空格",
                "请通过 --name 指定有效的工作空间名称"
            )
        return self._client.put(self._url(f"/v1/workspaces/{workspace_id}"), json=req)

    def delete_workspace(self, workspace_id: str) -> Any:
        validate_safe_id(workspace_id, "workspace_id")
        return self._client.delete(self._url(f"/v1/workspaces/{workspace_id}"))

    def add_workspace_members(self, workspace_id: str, req: dict) -> dict:
        validate_safe_id(workspace_id, "workspace_id")
        if not isinstance(req, dict) or not req:
            raise WorkspaceError("添加成员失败: 请求体不能为空")
        member_list = req.get("member_list")
        if not member_list:
            raise WorkspaceError(
                "添加成员失败: 缺少 member_list 参数",
                "请通过 --member-list 提供成员列表(JSON字符串)"
            )
        if not isinstance(member_list, list):
            raise WorkspaceError(
                "添加成员失败: member_list 必须为列表",
                "请提供合法的 JSON 数组，例如: [{\"user_id\":\"u1\",\"role_ids\":[\"r1\"]}]"
            )
        return self._client.post(self._url(f"/v1/workspaces/{workspace_id}/members"), json=req)

    def list_workspace_members(self, workspace_id: str) -> dict:
        validate_safe_id(workspace_id, "workspace_id")
        return self._client.get(self._url(f"/v1/workspaces/{workspace_id}/members"))

    def update_workspace_member(self, workspace_id: str, req: dict) -> dict:
        validate_safe_id(workspace_id, "workspace_id")
        if not isinstance(req, dict) or not req:
            raise WorkspaceError("更新成员失败: 请求体不能为空")
        user_id = req.get("user_id")
        if not user_id or not str(user_id).strip():
            raise WorkspaceError(
                "更新成员失败: user_id 不能为空或纯空格",
                "请通过 --user-id 指定有效的用户ID"
            )
        if not req.get("role_ids"):
            raise WorkspaceError(
                "更新成员失败: 缺少 role_ids 参数",
                "请通过 --role-ids 指定角色ID列表(逗号分隔)"
            )
        return self._client.put(self._url(f"/v1/workspaces/{workspace_id}/members"), json=req)

    def delete_workspace_members(self, workspace_id: str, user_ids: list[str]) -> Any:
        validate_safe_id(workspace_id, "workspace_id")
        if not user_ids:
            raise WorkspaceError(
                "删除成员失败: user_ids 不能为空",
                "请通过 --user-ids 指定要删除的用户ID列表(逗号分隔)"
            )
        return self._client.delete(
            self._url(f"/v1/workspaces/{workspace_id}/members"),
            params={"user_ids": user_ids},
        )

    def get_workspace_overview(self) -> dict:
        return self._client.get(self._url("/v1/workspaces/statistic/overview"))
