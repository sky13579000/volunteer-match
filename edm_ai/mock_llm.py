"""MOCK OpenAI-compatible server for offline demos and CI.  *** NOT A REAL MODEL. ***

It exercises the full HTTP path (chat + embeddings) without a GPU: the 'answer' is assembled from the evidence
block it receives, citing [E#]; embeddings are deterministic hashed trigram vectors. Use it to verify wiring,
`llm-check` and `eval`; never to judge answer quality. Real models: vLLM / Ollama / llama.cpp server.

    python -m edm_ai.mock_llm --port 8001         # then config: "llm": {"base_url": "http://127.0.0.1:8001/v1", "model": "mock"}
"""
from __future__ import annotations

import argparse
import hashlib
import json
import math
import re
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer


DENSE = 256
_EV = re.compile(r"\[(E\d+)\] \((.*?)\) (.*?)(?=\n\n\[E\d+\] \(|\Z)", re.S)


_STRIP = re.compile(r"[^0-9a-z가-힣]+")


def embed(text: str) -> list[float]:
    """Deterministic across processes and restarts (a real embedding server must be: vectors are cached in the DB)."""
    s = _STRIP.sub("", text.lower())
    v = [0.0] * DENSE
    for i in range(len(s) - 2):
        v[int.from_bytes(hashlib.blake2b(s[i:i + 3].encode(), digest_size=4).digest(), "big") % DENSE] += 1.0
    n = math.sqrt(sum(x * x for x in v)) or 1.0
    return [x / n for x in v]


def answer(prompt: str) -> str:
    items = _EV.findall(prompt)
    if not items:
        return "근거가 부족합니다."
    lines = []
    for eid, title, text in items[:3]:
        first = re.split(r"(?<=[.!?])\s+|\n", text.strip())[0][:140]
        lines.append(f"{first} [{eid}]")
    return "요약: " + " ".join(lines)


class H(BaseHTTPRequestHandler):
    def do_POST(self):
        body = json.loads(self.rfile.read(int(self.headers["Content-Length"])))
        if self.path.endswith("/chat/completions"):
            out = {"choices": [{"message": {"role": "assistant", "content": answer(body["messages"][-1]["content"])}}]}
        elif self.path.endswith("/embeddings"):
            inp = body["input"] if isinstance(body["input"], list) else [body["input"]]
            out = {"data": [{"index": i, "embedding": embed(t)} for i, t in enumerate(inp)]}
        else:
            self.send_error(404)
            return
        raw = json.dumps(out).encode()
        self.send_response(200)
        self.send_header("Content-Type", "application/json")
        self.send_header("Content-Length", str(len(raw)))
        self.end_headers()
        self.wfile.write(raw)

    def log_message(self, *a):
        pass


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--port", type=int, default=8001)
    a = ap.parse_args()
    print(f"MOCK LLM on http://127.0.0.1:{a.port}/v1  (not a real model)")
    ThreadingHTTPServer(("127.0.0.1", a.port), H).serve_forever()


if __name__ == "__main__":
    main()
