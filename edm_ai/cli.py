from __future__ import annotations

import argparse
import json
import sys

from .config import App


def main(argv=None):
    ap = argparse.ArgumentParser(prog="edm-ai")
    ap.add_argument("-c", "--config", required=True)
    sub = ap.add_subparsers(dest="cmd", required=True)
    sub.add_parser("ingest", help="crawl documents into the store (ACL-aware, incremental)")
    a = sub.add_parser("ask")
    a.add_argument("question")
    a.add_argument("-u", "--user", required=True)
    a.add_argument("--json", action="store_true")
    t = sub.add_parser("token", help="issue a bearer token (needs EDM_AI_SECRET)")
    t.add_argument("-u", "--user", required=True)
    t.add_argument("--ttl", type=int, default=3600)
    e = sub.add_parser("eval", help="run golden-set evaluation; exits 1 on any leak or recall < --min-recall")
    e.add_argument("golden")
    e.add_argument("--min-recall", type=float, default=0.9)
    sub.add_parser("verify-audit", help="check the audit hash chain")
    s = sub.add_parser("serve")
    s.add_argument("--host", default="127.0.0.1")
    s.add_argument("--port", type=int, default=8080)
    s.add_argument("--ingest", action="store_true", help="ingest documents at startup")
    s.add_argument("--reindex-every", type=int, default=0, metavar="SEC", help="re-crawl documents periodically")
    s.add_argument("--demo-login", action="store_true", help="user picker page; NEVER enable in production")
    args = ap.parse_args(argv)

    app = App(args.config)
    if args.cmd == "ingest":
        print(json.dumps(app.ingest()))
    elif args.cmd == "token":
        import os
        from . import auth
        sec = os.environ.get("EDM_AI_SECRET", "")
        if len(sec) < 16 or args.user not in app.users:
            sys.exit("need EDM_AI_SECRET (>=16 chars) and a known user")
        print(auth.issue(sec, args.user, args.ttl))
    elif args.cmd == "eval":
        from pathlib import Path
        from .evaluate import run_eval
        r = run_eval(app, Path(args.golden))
        print(json.dumps(r, ensure_ascii=False, indent=2))
        sys.exit(0 if r["leaks"] == 0 and r["recall"] >= args.min_recall and not r["failures"] else 1)
    elif args.cmd == "verify-audit":
        ok, bad = app.store.verify_audit()
        print("OK" if ok else f"TAMPERED at audit id {bad}")
        sys.exit(0 if ok else 1)
    elif args.cmd == "ask":
        user = app.users.get(args.user)
        if not user:
            sys.exit(f"unknown user {args.user}")
        ans = app.engine.ask(user, args.question)
        if args.json:
            print(json.dumps(ans.to_dict(), ensure_ascii=False, indent=2))
        else:
            print(ans.text)
            for e in ans.evidence:
                print(f"  [{e.id}] {e.kind} {e.ref}")
    else:
        from .server import serve
        print(f"serving on http://{args.host}:{args.port}")
        serve(app, args.host, args.port, args.demo_login, args.ingest, args.reindex_every)


if __name__ == "__main__":
    main()
