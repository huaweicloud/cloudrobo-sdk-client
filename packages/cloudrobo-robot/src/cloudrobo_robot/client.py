import base64
import json
from typing import Any

from cloudrobo_core.sdk import BaseClient
from cloudrobo_core.sdk.exceptions import validate_safe_id

from .validators import validate_params


class RobotClient(BaseClient):
    SERVICE = "cloudrobo-service"

    @validate_params("create_robot")
    def create_robot(self, req: dict) -> dict:
        return self._client.post(self._url("/v1/robots"), json=req)

    def list_robots(self, **params) -> dict:
        return self._client.get(self._url("/v1/robots"), params=params)

    def show_robot(self, robot_id: str) -> dict:
        robot_id = validate_safe_id(robot_id, "robot_id")
        return self._client.get(self._url(f"/v1/robots/{robot_id}"))

    @validate_params("update_robot")
    def update_robot(self, robot_id: str, req: dict) -> dict:
        robot_id = validate_safe_id(robot_id, "robot_id")
        return self._client.put(self._url(f"/v1/robots/{robot_id}"), json=req)

    def delete_robot(self, robot_id: str) -> Any:
        robot_id = validate_safe_id(robot_id, "robot_id")
        return self._client.delete(self._url(f"/v1/robots/{robot_id}"))

    @validate_params("export_robot_certificate")
    def export_robot_certificate(self, robot_id: str, req: dict) -> bytes:
        robot_id = validate_safe_id(robot_id, "robot_id")
        raw = self._client.post(self._url(f"/v1/robots/{robot_id}/certificate/export"), json=req, raw=True)
        return self._extract_certificate_bytes(raw)

    @staticmethod
    def _extract_certificate_bytes(raw: bytes) -> bytes:
        try:
            payload = json.loads(raw)
        except (ValueError, TypeError):
            return raw
        if isinstance(payload, dict):
            content = payload.get("content")
            if isinstance(content, str):
                return base64.b64decode(content)
        return raw

    def show_sdk(self) -> dict:
        return self._client.get(self._url("/v1/robots/sdk"))
