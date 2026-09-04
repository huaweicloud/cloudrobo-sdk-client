import time

from cloudrobo_core.sdk import BaseClient
from cloudrobo_core.sdk.exceptions import validate_safe_id

from .validators import validate_params

TERMINAL_STATES = {"FAILED", "RUNNING", "STOPPING", "STOPPED", "DELETING", "ERROR"}


class InferClient(BaseClient):
    SERVICE = "cloudrobo-service"

    @validate_params("create_infer_service")
    def create_infer_service(self, req: dict) -> dict:
        return self._client.post(self._url("/v1/infer-services"), json=req)

    def list_infer_services(self, **params) -> dict:
        return self._client.get(self._url("/v1/infer-services"), params=params)

    def show_infer_service(self, service_id: str) -> dict:
        validate_safe_id(service_id, "service_id")
        return self._client.get(self._url(f"/v1/infer-services/{service_id}"))

    @validate_params("update_infer_service")
    def update_infer_service(self, service_id: str, req: dict) -> dict:
        validate_safe_id(service_id, "service_id")
        return self._client.put(self._url(f"/v1/infer-services/{service_id}"), json=req)

    def delete_infer_service(self, service_id: str) -> dict:
        validate_safe_id(service_id, "service_id")
        return self._client.delete(self._url(f"/v1/infer-services/{service_id}"))

    def start_infer_service(self, service_id: str) -> dict:
        validate_safe_id(service_id, "service_id")
        return self._client.post(self._url(f"/v1/infer-services/{service_id}/start"))

    def stop_infer_service(self, service_id: str) -> dict:
        validate_safe_id(service_id, "service_id")
        return self._client.post(self._url(f"/v1/infer-services/{service_id}/stop"))

    @validate_params("list_infer_service_logs")
    def list_infer_service_logs(self, service_id: str, req: dict) -> dict:
        validate_safe_id(service_id, "service_id")
        return self._client.post(self._url(f"/v1/infer-services/{service_id}/logs"), json=req)

    def wait_deploy(self, service_id: str, timeout: int = 600) -> dict:
        validate_safe_id(service_id, "service_id")
        elapsed = 0
        while elapsed < timeout:
            service = self.show_infer_service(service_id)
            if service.get("status") != "DEPLOYING":
                return service
            time.sleep(5)
            elapsed += 5
        last = self.show_infer_service(service_id)
        raise RuntimeError(
            f"wait-deploy timeout after {timeout}s, last status: {last.get('status')}"
        )
