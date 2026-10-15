"""Sends one signed 'approval finished' message to the platform - for testing a groupware integration end to end,
or as the body of a small relay service when the groupware cannot sign requests itself.

    EDM_AI_GW_SECRET=<same secret as the server> python3 send_example.py http://127.0.0.1:9102 file.pdf kim@iccjeju.example
"""
import base64
import hashlib
import hmac
import json
import os
import sys
import time
import urllib.request
from pathlib import Path


def send(base: str, payload: dict, secret: str) -> tuple[int, dict]:
    body = json.dumps(payload, ensure_ascii=False).encode()
    ts = str(int(time.time()))
    sig = "sha256=" + hmac.new(secret.encode(), ts.encode() + b"." + body, hashlib.sha256).hexdigest()
    req = urllib.request.Request(base.rstrip("/") + "/api/adapters/groupware", body, {"X-EDM-Timestamp": ts, "X-EDM-Signature": sig}, method="POST")
    try:
        with urllib.request.urlopen(req, timeout=120) as r:
            return r.status, json.loads(r.read())
    except urllib.error.HTTPError as e:
        return e.code, json.loads(e.read() or b"{}")


if __name__ == "__main__":
    base, path, owner = sys.argv[1:4]
    f = Path(path)
    code, res = send(base, {
        "doc_id": "TEST-" + str(int(time.time())), "form": "계약품의", "title": f.stem, "owner": {"type": "email", "id": owner},
        "files": [{"filename": f.name, "content_b64": base64.b64encode(f.read_bytes()).decode()}],
        "approved": True, "approval_line": [{"name": "테스트", "title": "팀장", "result": "승인", "at": time.strftime("%Y-%m-%d %H:%M")}]},
        os.environ["EDM_AI_GW_SECRET"])
    print(code, json.dumps(res, ensure_ascii=False, indent=2))
