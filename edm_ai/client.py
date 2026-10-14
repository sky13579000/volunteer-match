"""Tiny dependency-free client for other systems (groupware, RPA, batch jobs).

    c = EdmClient("http://edm-ai:8080", token="...")        # token from `edm-ai token -u <user>`
    ans = c.ask("한빛전자 계약 금액은?")
    for e in ans["evidence"]: print(e["id"], e["ref"])
"""
from __future__ import annotations

import base64
import json
import urllib.error
import urllib.request
from pathlib import Path


class EdmApiError(Exception):
    def __init__(self, status: int, message: str):
        super().__init__(f"{status}: {message}")
        self.status, self.message = status, message


class EdmClient:
    def __init__(self, base_url: str, token: str | None = None, user: str | None = None, timeout: float = 60):
        self.base, self.timeout = base_url.rstrip("/"), timeout
        self.headers = {"Authorization": f"Bearer {token}"} if token else ({"X-User-Id": user} if user else {})

    def _call(self, method: str, path: str, body: dict | None = None):
        data = json.dumps(body).encode() if body is not None else None
        req = urllib.request.Request(self.base + path, data, {**self.headers, "Content-Type": "application/json"}, method=method)
        try:
            with urllib.request.urlopen(req, timeout=self.timeout) as r:
                return json.loads(r.read())
        except urllib.error.HTTPError as e:
            try:
                msg = json.loads(e.read()).get("error", e.reason)
            except Exception:
                msg = e.reason
            raise EdmApiError(e.code, str(msg)) from None

    def ask(self, question: str) -> dict:
        return self._call("POST", "/api/ask", {"question": question})

    def docs(self, q: str | None = None) -> list[dict]:
        from urllib.parse import quote
        return self._call("GET", "/api/docs" + (f"?q={quote(q)}" if q else ""))

    def doc(self, doc_id: int) -> dict:
        return self._call("GET", f"/api/docs/{int(doc_id)}")

    def upload(self, path: str, acl: dict | None = None) -> dict:
        p = Path(path)
        body = {"filename": p.name, "content_b64": base64.b64encode(p.read_bytes()).decode()}
        if acl is not None:
            body["acl"] = acl
        return self._call("POST", "/api/docs", body)

    def set_acl(self, doc_id: int, acl: dict) -> dict:
        return self._call("PUT", f"/api/docs/{int(doc_id)}/acl", {"acl": acl})

    def delete(self, doc_id: int) -> dict:
        return self._call("DELETE", f"/api/docs/{int(doc_id)}")

    def alerts(self) -> list[dict]:
        return self._call("GET", "/api/alerts")
