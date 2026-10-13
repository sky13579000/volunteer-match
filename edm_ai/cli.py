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
    s = sub.add_parser("serve")
    s.add_argument("--host", default="127.0.0.1")
    s.add_argument("--port", type=int, default=8080)
    s.add_argument("--demo-login", action="store_true", help="user picker page; NEVER enable in production")
    args = ap.parse_args(argv)

    app = App(args.config)
    if args.cmd == "ingest":
        print(json.dumps(app.ingest()))
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
        serve(app, args.host, args.port, args.demo_login)


if __name__ == "__main__":
    main()
