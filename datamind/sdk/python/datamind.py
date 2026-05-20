"""
DataMind Python SDK — Partner API v1 stub.

Usage:
    import datamind
    client = datamind.Client(api_key="pk_...", base_url="https://api.datamind.ai")

    integrations = client.integrations.list("user@example.com")
    sync = client.sync.trigger("loyverse", user_email="user@example.com")
    records = client.records.list("loyverse", "products", user_email="user@example.com")
    analytics = client.analytics.run("rfm_analysis", provider="loyverse", user_email="user@example.com")
    usage = client.usage.get("user@example.com")
"""

import urllib.request
import urllib.parse
import json
from typing import Optional


class _Resource:
    def __init__(self, client: "Client"):
        self._client = client

    def _get(self, path: str, params: Optional[dict] = None) -> dict:
        return self._client._request("GET", path, params=params)

    def _post(self, path: str, body: dict) -> dict:
        return self._client._request("POST", path, body=body)


class IntegrationsResource(_Resource):
    def list(self, user_email: str) -> dict:
        return self._get("/v1/integrations", {"user_email": user_email})


class SyncResource(_Resource):
    def trigger(self, provider: str, user_email: str, full: bool = False) -> dict:
        return self._post(f"/v1/sync/{provider}", {"user_email": user_email, "full": full})


class RecordsResource(_Resource):
    def list(
        self,
        provider: str,
        record_type: str,
        user_email: str,
        limit: int = 100,
        offset: int = 0,
        store_id: Optional[str] = None,
        from_date: Optional[str] = None,
    ) -> dict:
        params: dict = {
            "user_email": user_email,
            "limit": limit,
            "offset": offset,
        }
        if store_id:
            params["store_id"] = store_id
        if from_date:
            params["from"] = from_date
        return self._get(f"/v1/records/{provider}/{record_type}", params)


class AnalyticsResource(_Resource):
    def run(self, template_id: str, provider: str, user_email: str) -> dict:
        return self._get(
            f"/v1/analytics/{template_id}",
            {"provider": provider, "user_email": user_email},
        )


class UsageResource(_Resource):
    def get(self, user_email: str) -> dict:
        return self._get("/v1/usage", {"user_email": user_email})


class Client:
    def __init__(self, api_key: str, base_url: str = "https://api.datamind.ai"):
        self._api_key = api_key
        self._base_url = base_url.rstrip("/")
        self.integrations = IntegrationsResource(self)
        self.sync = SyncResource(self)
        self.records = RecordsResource(self)
        self.analytics = AnalyticsResource(self)
        self.usage = UsageResource(self)

    def _request(self, method: str, path: str, params: Optional[dict] = None, body: Optional[dict] = None) -> dict:
        url = self._base_url + path
        if params:
            url += "?" + urllib.parse.urlencode(params)
        data = json.dumps(body).encode() if body else None
        headers = {
            "X-API-Key": self._api_key,
            "Content-Type": "application/json",
            "Accept": "application/json",
        }
        req = urllib.request.Request(url, data=data, headers=headers, method=method)
        with urllib.request.urlopen(req) as resp:
            return json.loads(resp.read().decode())
