from __future__ import annotations

import json
from pathlib import Path


def run_eval(app, golden_path: Path) -> dict:
    """Golden-set evaluation. Each case: {user, question, expect: [ref-prefix...], forbid: [ref-prefix...], refuse: bool}.
    Reports recall of expected evidence, MRR, and LEAKS (forbidden evidence returned) - leaks must be 0."""
    cases = json.loads(golden_path.read_text("utf-8"))
    hit = total = leaks = 0
    rr = []
    failures = []
    for c in cases:
        ans = app.engine.ask(app.users[c["user"]], c["question"])
        refs = [e.ref for e in ans.evidence]
        for f in c.get("forbid", []):
            if any(r.startswith(f) for r in refs):
                leaks += 1
                failures.append(f"LEAK {c['user']}: {c['question']} -> {f}")
        if c.get("refuse") and not ans.refused:
            failures.append(f"should refuse: {c['question']}")
        for e in c.get("expect", []):
            total += 1
            pos = next((i for i, r in enumerate(refs) if r.startswith(e)), None)
            hit += pos is not None
            rr.append(1 / (pos + 1) if pos is not None else 0.0)
            if pos is None:
                failures.append(f"MISS {c['user']}: {c['question']} -> {e}")
    return {"cases": len(cases), "recall": hit / total if total else 1.0,
            "mrr": sum(rr) / len(rr) if rr else 1.0, "leaks": leaks, "failures": failures}
