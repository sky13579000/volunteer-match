from __future__ import annotations

import argparse
import json
import sys

from .config import App


def run_demo(args):
    import runpy
    import threading
    import webbrowser
    from pathlib import Path

    demo = Path(__file__).resolve().parent.parent / "examples" / "demo"
    if not (demo / "build_demo.py").exists():
        sys.exit("demo data not found: run this from a checkout of the repository")
    runpy.run_path(str(demo / "build_demo.py"), run_name="__main__")      # ERP db + document ingest
    url = f"http://{args.host}:{args.port}/"
    print(f"\n  EDM-AI demo  ->  {url}\n  Pick a user at the top, then ask e.g. '한빛전자 MES 계약 금액과 납기 지연 이슈는?'\n"
          "  Try 김영업(영업) vs 박재무(재무) vs 최협력(외부). Ctrl+C to stop.\n")
    if not args.no_browser:
        threading.Timer(1.0, lambda: webbrowser.open(url)).start()
    from .server import serve
    try:
        serve(App(str(demo / "config.json")), args.host, args.port, demo_login=True)
    except OSError as e:
        sys.exit(f"cannot listen on {args.host}:{args.port} ({e}); try --port 8081")
    except KeyboardInterrupt:
        pass


def main(argv=None):
    for stream in (sys.stdout, sys.stderr):      # Windows consoles default to cp949: never crash on Korean / '●'
        if hasattr(stream, "reconfigure"):
            stream.reconfigure(encoding="utf-8", errors="replace")
    ap = argparse.ArgumentParser(prog="edm-ai")
    ap.add_argument("-c", "--config", help="config.json (not needed for `demo`)")
    sub = ap.add_subparsers(dest="cmd", required=True)
    d = sub.add_parser("demo", help="build the sample data and start the web UI (needs a repo checkout)")
    d.add_argument("--port", type=int, default=8080)
    d.add_argument("--host", default="127.0.0.1")
    d.add_argument("--no-browser", action="store_true")
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
    bk = sub.add_parser("backup", help="consistent snapshot of the document store + stored files (safe while serving)")
    bk.add_argument("out")
    rs = sub.add_parser("restore", help="verify and restore a backup into the configured store / repository")
    rs.add_argument("archive")
    rs.add_argument("--force", action="store_true", help="replace existing data")
    vb = sub.add_parser("verify-backup", help="check a backup archive against its manifest")
    vb.add_argument("archive")
    sub.add_parser("llm-check", help="verify the configured LLM / embedding server (connectivity, citations, grounding)")
    sub.add_parser("verify-audit", help="check the audit hash chain")
    s = sub.add_parser("serve")
    s.add_argument("--host", default="127.0.0.1")
    s.add_argument("--port", type=int, default=8080)
    s.add_argument("--ingest", action="store_true", help="ingest documents at startup")
    s.add_argument("--reindex-every", type=int, default=0, metavar="SEC", help="re-crawl documents periodically")
    s.add_argument("--demo-login", action="store_true", help="user picker page; NEVER enable in production")
    args = ap.parse_args(argv)

    if args.cmd == "demo":
        return run_demo(args)
    if not args.config:
        ap.error("-c/--config is required")
    if args.cmd == "verify-backup":                     # needs no running store
        from pathlib import Path
        from .backup import verify
        ok, problems = verify(Path(args.archive))
        print("OK" if ok else "FAILED\n" + "\n".join(problems))
        sys.exit(0 if ok else 1)
    if args.cmd == "restore":                           # must run BEFORE the store is opened
        import json as _json
        import os
        from pathlib import Path
        from .backup import restore
        cfgp = Path(args.config).resolve()
        cfg = _json.loads(cfgp.read_text("utf-8"))
        store_path = Path(os.environ.get("EDM_AI_STORE") or (cfgp.parent / cfg.get("store", "store.db"))).resolve()
        repo = (cfgp.parent / cfg["dms"]["repo"]).resolve() if cfg.get("dms", {}).get("repo") else store_path.parent / "repo"
        try:
            print(_json.dumps(restore(Path(args.archive), store_path, repo, args.force)))
        except (ValueError, FileExistsError) as e:
            sys.exit(str(e))
        sys.exit(0)
    app = App(args.config)
    if args.cmd == "backup":
        from pathlib import Path
        from .backup import backup
        with app.lock:
            print(json.dumps(backup(app.store.db, app.dms.repo, Path(args.out))))
        sys.exit(0)
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
        sys.exit(0 if r["leaks"] == 0 and r["recall"] >= args.min_recall and not r["failures"]
                 and not r["ungrounded_numbers"] else 1)
    elif args.cmd == "llm-check":
        from .llmcheck import run_check
        lines, ok = run_check(app)
        for level, msg in lines:
            print(f"[{level}] {msg}")
        sys.exit(0 if ok else 1)
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
