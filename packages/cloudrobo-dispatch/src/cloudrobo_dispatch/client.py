import time

from cloudrobo_core.sdk import BaseClient

from .validators import validate_params

RUNNING_STATE = "RUNNING"
TERMINAL_STATES = {"COMPLETED", "FAILED", "CANCELLED"}
POLL_INTERVAL = 5


class DispatchClient(BaseClient):
    SERVICE = "cloudrobo-service"

    # ===== 调度任务管理 (RoboDispatcherTaskManagement) =====

    @validate_params("create_dispatcher_task")
    def create_dispatcher_task(self, session_id: str, req: dict) -> dict:
        return self._client.post(
            self._url(f"/v1/robo-dispatcher/sessions/{session_id}/tasks"), json=req
        )

    def list_dispatcher_tasks(self, session_id: str, **params) -> dict:
        return self._client.get(
            self._url(f"/v1/robo-dispatcher/sessions/{session_id}/tasks"),
            params=params,
        )

    def show_dispatcher_task(self, session_id: str, task_id: str) -> dict:
        return self._client.get(
            self._url(f"/v1/robo-dispatcher/sessions/{session_id}/tasks/{task_id}")
        )

    def cancel_dispatcher_task(self, session_id: str, task_id: str) -> dict:
        return self._client.delete(
            self._url(f"/v1/robo-dispatcher/sessions/{session_id}/tasks/{task_id}")
        )

    def show_dispatcher_task_result(
        self, session_id: str, task_id: str, **params
    ) -> dict:
        return self._client.get(
            self._url(
                f"/v1/robo-dispatcher/sessions/{session_id}/tasks/{task_id}/result"
            ),
            params=params,
        )

    def wait_dispatcher_task(
        self, session_id: str, task_id: str, timeout: int = 600
    ) -> dict:
        deadline = time.monotonic() + timeout
        while True:
            data = self.show_dispatcher_task(session_id, task_id)
            status = (data.get("task") or {}).get("status")
            if status != RUNNING_STATE:
                return data
            if time.monotonic() >= deadline:
                raise TimeoutError(
                    f"等待任务 {task_id} 完成超时（{timeout} 秒），最后状态为 {status}"
                )
            time.sleep(POLL_INTERVAL)
