from __future__ import annotations

import json
from pathlib import Path

from .llm import numbers_grounded


def run_eval(app, golden_path: Path) -> dict:
    """Golden-set evaluation. Each case: {user, question, expect: [ref-prefix...], forbid: [ref-prefix...], refuse: bool}.
    Reports recall of expected evidence, MRR, and LEAKS (forbidden evidence returned) - leaks must be 0."""
    cases = json.loads(golden_path.read_text("utf-8"))
    hit = total = leaks = 0
    gens: dict[str, int] = {}
    ungrounded: list[str] = []
    rr = []
    failures = []
    for c in cases:
        ans = app.engine.ask(app.users[c["user"]], c["question"])
        refs = [e.ref.lower() for e in ans.evidence]
        if not ans.refused:
            gens[ans.generator] = gens.get(ans.generator, 0) + 1
            bad = numbers_grounded(ans.text, [e.text for e in ans.evidence])
            if bad:
                ungrounded.append(f"{c['user']}: {c['question']} -> {bad}")
        flat = ans.text.replace(",", "")
        for fact in c.get("must_contain", []):        # facts the answer has to state (catches summaries that drop them)
            if fact.replace(",", "") not in flat:
                failures.append(f"MISSING FACT {c['user']}: {c['question']} -> {fact}")
        for word in c.get("must_not_contain", []):    # content that must never appear in the answer text itself
            if word in ans.text:
                leaks += 1
                failures.append(f"LEAK IN ANSWER {c['user']}: {c['question']} -> {word}")   # table-name case differs per DB (Oracle: UPPER)
        for f in c.get("forbid", []):
            if any(r.startswith(f.lower()) for r in refs):
                leaks += 1
                failures.append(f"LEAK {c['user']}: {c['question']} -> {f}")
        if c.get("refuse") and not ans.refused:
            failures.append(f"should refuse: {c['question']}")
        for e in c.get("expect", []):
            total += 1
            pos = next((i for i, r in enumerate(refs) if r.startswith(e.lower())), None)
            hit += pos is not None
            rr.append(1 / (pos + 1) if pos is not None else 0.0)
            if pos is None:
                failures.append(f"MISS {c['user']}: {c['question']} -> {e}")
    n = sum(gens.values()) or 1
    return {"cases": len(cases), "recall": hit / total if total else 1.0,
            "mrr": sum(rr) / len(rr) if rr else 1.0, "leaks": leaks, "failures": failures,
            "answers": gens, "llm_share": round(gens.get("llm", 0) / n, 3), "ungrounded_numbers": ungrounded}
