"""Tiny dependency-free client for other systems (groupware, RPA, batch jobs).

    c = EdmClient("http://edm-ai:8080", token="...")        # token from `edm-ai token -u <user>`
    ans = c.ask("제주관광공사 계약 금액은?")
    doc = c.upload("계약서.docx", doc_type="CT", tags=["계약"])
    c.submit(doc["id"], ["park"])                            # 결재 요청
    for hit in c.search("지체상금", type="CT")["items"]: print(hit["doc_no"], hit["title"])
"""
from __future__ import annotations

import base64
import json
import re
import urllib.error
import urllib.parse
import urllib.request
from pathlib import Path


class EdmApiError(Exception):
    def __init__(self, status: int, message: str):
        super().__init__(f"{status}: {message}")
        self.status, self.message = status, message


class EdmClient:
    def __init__(self, base_url: str, token: str | None = None, user: str | None = None, timeout: float = 120):
        self.base, self.timeout = base_url.rstrip("/"), timeout
        self.headers = {"Authorization": f"Bearer {token}"} if token else ({"X-User-Id": user} if user else {})

    def _open(self, method: str, path: str, body: dict | None):
        data = json.dumps(body).encode() if body is not None else None
        req = urllib.request.Request(self.base + path, data, {**self.headers, "Content-Type": "application/json"}, method=method)
        try:
            return urllib.request.urlopen(req, timeout=self.timeout)
        except urllib.error.HTTPError as e:
            try:
                msg = json.loads(e.read()).get("error", e.reason)
            except Exception:
                msg = e.reason
            raise EdmApiError(e.code, str(msg)) from None

    def _call(self, method: str, path: str, body: dict | None = None):
        with self._open(method, path, body) as r:
            return json.loads(r.read())

    # ---- AI ----
    def ask(self, question: str) -> dict:
        return self._call("POST", "/api/ask", {"question": question})

    # ---- documents ----
    def search(self, q: str | None = None, **filters) -> dict:
        params = {k: v for k, v in {"q": q, **filters}.items() if v not in (None, "")}
        return self._call("GET", "/api/docs" + ("?" + urllib.parse.urlencode(params) if params else ""))

    docs = search

    def doc(self, doc_id: int) -> dict:
        return self._call("GET", f"/api/docs/{int(doc_id)}")

    def upload(self, path: str, *, folder_id: int = 1, doc_type: str = "GEN", **meta) -> dict:
        p = Path(path)
        body = {"filename": p.name, "content_b64": base64.b64encode(p.read_bytes()).decode(), "folder_id": folder_id, "doc_type": doc_type, **meta}
        return self._call("POST", "/api/docs", body)

    def new_version(self, doc_id: int, path: str, comment: str = "") -> dict:
        p = Path(path)
        return self._call("POST", f"/api/docs/{int(doc_id)}/versions", {"filename": p.name, "content_b64": base64.b64encode(p.read_bytes()).decode(), "comment": comment})

    def update(self, doc_id: int, **meta) -> dict:
        return self._call("PUT", f"/api/docs/{int(doc_id)}", meta)

    def set_acl(self, doc_id: int, entries: list | None = None, inherit: bool | None = None, level: int | None = None) -> dict:
        return self._call("PUT", f"/api/docs/{int(doc_id)}/acl", {k: v for k, v in {"entries": entries, "inherit": inherit, "level": level}.items() if v is not None})

    def delete(self, doc_id: int) -> dict:
        return self._call("DELETE", f"/api/docs/{int(doc_id)}")

    def download(self, doc_id: int, ver: int | None = None) -> tuple[str, bytes]:
        with self._open("GET", f"/api/docs/{int(doc_id)}/download" + (f"?ver={int(ver)}" if ver else ""), None) as r:
            m = re.search(r"filename\*=UTF-8''([^;]+)", r.headers.get("Content-Disposition", ""))
            return (urllib.parse.unquote(m.group(1)) if m else "download"), r.read()

    # ---- approval / collaboration ----
    def submit(self, doc_id: int, approvers: list[str], mode: str = "sequential", note: str = "") -> dict:
        return self._call("POST", f"/api/docs/{int(doc_id)}/submit", {"approvers": approvers, "mode": mode, "note": note})

    def approve(self, doc_id: int, comment: str = "") -> dict:
        return self._call("POST", f"/api/docs/{int(doc_id)}/approve", {"comment": comment})

    def reject(self, doc_id: int, comment: str) -> dict:
        return self._call("POST", f"/api/docs/{int(doc_id)}/reject", {"comment": comment})

    def comment(self, doc_id: int, text: str) -> dict:
        return self._call("POST", f"/api/docs/{int(doc_id)}/comments", {"text": text})

    def folders(self) -> list[dict]:
        return self._call("GET", "/api/dms/folders")

    def make_folder(self, parent_id: int | None, name: str) -> dict:
        return self._call("POST", "/api/dms/folders", {"parent_id": parent_id, "name": name})

    def tasks(self) -> dict:
        return self._call("GET", "/api/dms/tasks")

    def notifications(self, unread: bool = False) -> dict:
        return self._call("GET", "/api/dms/notifications" + ("?unread=1" if unread else ""))

    def dashboard(self) -> dict:
        return self._call("GET", "/api/dms/dashboard")

    def alerts(self) -> list[dict]:
        return self._call("GET", "/api/alerts")
