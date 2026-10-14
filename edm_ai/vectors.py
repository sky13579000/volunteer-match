from __future__ import annotations

import json
import math
import re
import urllib.request

_STRIP = re.compile(r"[^0-9a-z가-힣]+")
DIM = 1 << 14


def fuzzy_vec(text: str) -> dict[int, float]:
    """Dependency-free sparse vector: hashed character trigrams, sublinear tf, L2-normalised.
    Robust to typos, spacing and josa variants. It is lexical-fuzzy, NOT semantic; plug in a real embedder for that."""
    s = _STRIP.sub("", text.lower())
    v: dict[int, float] = {}
    for i in range(len(s) - 2):
        h = hash(s[i:i + 3]) % DIM       # vectors are only ever compared inside one process, so Python's fast salted hash is fine
        v[h] = v.get(h, 0.0) + 1.0
    n = math.sqrt(sum((1 + math.log(x)) ** 2 for x in v.values())) or 1.0
    return {k: (1 + math.log(x)) / n for k, x in v.items()}


def cosine(a: dict[int, float], b: dict[int, float]) -> float:
    if len(a) > len(b):
        a, b = b, a
    return sum(x * b.get(k, 0.0) for k, x in a.items())


def normalise(v: list[float]) -> list[float]:
    n = math.sqrt(sum(x * x for x in v)) or 1.0
    return [x / n for x in v]


def dot(a: list[float], b: list[float]) -> float:
    return sum(x * y for x, y in zip(a, b))


class OpenAICompatEmbedder:
    """Any OpenAI-compatible /embeddings endpoint (e.g. vLLM / TEI / Ollama serving bge-m3 on-prem)."""

    def __init__(self, base_url: str, model: str, timeout: float = 60, batch: int = 32):
        self.url, self.model, self.timeout, self.batch = base_url.rstrip("/") + "/embeddings", model, timeout, batch

    def embed(self, texts: list[str]) -> list[list[float]]:
        out: list[list[float]] = []
        for i in range(0, len(texts), self.batch):
            body = json.dumps({"model": self.model, "input": texts[i:i + self.batch]}).encode()
            req = urllib.request.Request(self.url, body, {"Content-Type": "application/json"})
            with urllib.request.urlopen(req, timeout=self.timeout) as r:
                data = sorted(json.load(r)["data"], key=lambda d: d["index"])
            out.extend(normalise(d["embedding"]) for d in data)
        return out
