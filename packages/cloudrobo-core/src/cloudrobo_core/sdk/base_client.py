from .http_client import HttpClient


class BaseClient:
    SERVICE = ""

    def __init__(self, http_client: HttpClient):
        self._client = http_client
        self._base = self._client.config.get_endpoint(self.SERVICE)

    def _url(self, path: str) -> str:
        return f"{self._base}{path}"
