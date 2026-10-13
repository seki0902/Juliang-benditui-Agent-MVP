"""Read-only Ocean Engine adapter boundary.

Only the local project-list path is preconfigured from the verified official
visual-debug documentation. Report and lead endpoints remain configurable
until the application's actual local-push permissions and fields are tested.
"""

from __future__ import annotations

from typing import Any

import requests

try:  # Support both PYTHONPATH=agent/src and package-style imports.
    from ..config import Settings
    from .token_store import TokenStore
except ImportError:  # pragma: no cover - exercised by CLI entrypoints.
    from config import Settings
    from skills.token_store import TokenStore


class OceanEngineApiError(RuntimeError):
    pass


class OceanEngineClient:
    def __init__(self, settings: Settings, token_store: TokenStore, session: requests.Session | None = None) -> None:
        self.settings = settings
        self.token_store = token_store
        self.session = session or requests.Session()

    def _url(self, path: str) -> str:
        return self.settings.oceanengine_base_url.rstrip("/") + "/" + path.lstrip("/")

    def _headers(self, access_token: str) -> dict[str, str]:
        return {"Access-Token": access_token, "Accept": "application/json"}

    def get_local_projects(self, access_token: str | None = None) -> dict[str, Any]:
        token = access_token or self.settings.oceanengine_access_token
        if not token:
            raise OceanEngineApiError("缺少 Access-Token，不能调用本地推项目列表接口")
        account_id = self.settings.oceanengine_local_account_id
        if not account_id:
            raise OceanEngineApiError("缺少 OCEANENGINE_LOCAL_ACCOUNT_ID")
        response = self.session.get(
            self._url(self.settings.oceanengine_project_list_path),
            headers=self._headers(token),
            params={"local_account_id": account_id},
            timeout=30,
        )
        return self._decode(response)

    def get_report(self, params: dict[str, Any], access_token: str | None = None) -> dict[str, Any]:
        """Call a verified report endpoint supplied through configuration.

        This method intentionally refuses to guess the report endpoint. The
        endpoint, dimensions, metrics and response fields must pass connector
        acceptance before entering the daily pipeline.
        """
        path = self.settings.oceanengine_report_path
        method = (self.settings.oceanengine_report_method or "").upper()
        if not path or method not in {"GET", "POST"}:
            raise OceanEngineApiError(
                "OCEANENGINE_REPORT_PATH/METHOD 尚未完成配置，先完成本地推报表接口验收"
            )
        token = access_token or self.settings.oceanengine_access_token
        if not token:
            raise OceanEngineApiError("缺少 Access-Token")
        request_kwargs: dict[str, Any] = {
            "headers": self._headers(token),
            "timeout": 60,
        }
        if method == "GET":
            request_kwargs["params"] = params
        else:
            request_kwargs["json"] = params
        response = self.session.request(method, self._url(path), **request_kwargs)
        return self._decode(response)

    @staticmethod
    def _decode(response: requests.Response) -> dict[str, Any]:
        if response.status_code >= 400:
            body = response.text[:1000]
            raise OceanEngineApiError(f"HTTP {response.status_code}: {body}")
        try:
            payload = response.json()
        except ValueError as exc:
            raise OceanEngineApiError("接口返回不是合法 JSON") from exc
        if not isinstance(payload, dict):
            raise OceanEngineApiError("接口返回结构不是 JSON object")
        return payload
