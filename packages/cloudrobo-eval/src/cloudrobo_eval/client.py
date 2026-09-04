from typing import Any, Dict, List, Optional

from cloudrobo_core.sdk import BaseClient


class EvalClient(BaseClient):
    SERVICE = "cloudrobo-service"

    def create_eval_job(self, req: Dict) -> Dict:
        return self._client.post(self._url("/v1/skill-eval-jobs"), json=req)

    def list_eval_jobs(self, **params) -> Dict:
        return self._client.get(self._url("/v1/skill-eval-jobs"), params=params)

    def show_eval_job(self, job_id: str) -> Dict:
        return self._client.get(self._url(f"/v1/skill-eval-jobs/{job_id}"))

    def update_eval_job(self, job_id: str, req: Dict) -> Dict:
        return self._client.put(self._url(f"/v1/skill-eval-jobs/{job_id}"), json=req)

    def delete_eval_job(self, job_id: str) -> Any:
        return self._client.delete(self._url(f"/v1/skill-eval-jobs/{job_id}"))

    def batch_delete_eval_jobs(self, job_ids: List[str]) -> Any:
        return self._client.post(self._url("/v1/skill-eval-jobs/batch-delete"), json={"job_ids": job_ids})

    def list_executions(self, job_id: str, **params) -> Dict:
        return self._client.get(self._url(f"/v1/skill-eval-jobs/{job_id}/executions"), params=params)

    def show_execution(self, job_id: str, execution_id: str) -> Dict:
        return self._client.get(self._url(f"/v1/skill-eval-jobs/{job_id}/executions/{execution_id}"))

    def delete_execution(self, job_id: str, execution_id: str) -> Any:
        return self._client.delete(self._url(f"/v1/skill-eval-jobs/{job_id}/executions/{execution_id}"))

    def get_vnc_address(self, job_id: str, execution_id: str) -> Dict:
        return self._client.get(self._url(f"/v1/skill-eval-jobs/{job_id}/executions/{execution_id}/vnc-address"))

    def show_eval_stats(self, workspace_id: Optional[str] = None, **params) -> Dict:
        if workspace_id:
            params["workspace_id"] = workspace_id
        return self._client.get(self._url("/v1/skill-eval-stats"), params=params)
