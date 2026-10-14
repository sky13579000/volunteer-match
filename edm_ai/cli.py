from __future__ import annotations

import argparse
import json
import sys

from .config import App


def _lan_ip() -> str:
    import socket
    try:
        with socket.socket(socket.AF_INET, socket.SOCK_DGRAM) as sk:
            sk.connect(("10.255.255.255", 1))      # no packet is sent; picks the outbound interface
            return sk.getsockname()[0]
    except OSError:
        return socket.gethostname()


def _probe(host: str, port: int, timeout: float = 0.7):
    """What is already answering on host:port? -> ('free'|'edm-ai'|'other', version or None)"""
    import json as _json
    import socket
    import urllib.request
    with socket.socket() as s:
        s.settimeout(timeout)
        if s.connect_ex((host, port)) != 0:
            return "free", None
    try:
        with urllib.request.urlopen(f"http://{host}:{port}/api/config", timeout=timeout) as r:
            cfg = _json.loads(r.read())
            return "edm-ai", cfg.get("version", "0.3 이하(버전 표시 없음)")
    except Exception:  # noqa: BLE001
        return "other", None


def run_demo(args):
    import runpy
    import threading
    import webbrowser
    from pathlib import Path

    from . import __version__

    demo = Path(__file__).resolve().parent.parent / "examples" / "demo"
    if not (demo / "build_demo.py").exists():
        sys.exit("demo data not found: run this from the downloaded folder (or use the single-file edm-ai.pyz)")
    # An older EDM-AI left running on the classic port is the usual reason people keep seeing an old screen.
    if args.lan:
        args.host = "0.0.0.0"
    probe_host = "127.0.0.1" if args.host == "0.0.0.0" else args.host
    kind, ver = _probe(probe_host, 8080)
    stale = f"\n  ! 포트 8080 에서 이전 EDM-AI (버전 {ver}) 가 아직 실행 중입니다. 8080 으로 접속하면 옛 화면이 보입니다. 아래 새 주소만 쓰세요.\n" if kind == "edm-ai" and ver != __version__ else ""
    port = args.port
    for cand in range(args.port, args.port + 20):
        if _probe(probe_host, cand)[0] == "free":
            port = cand
            break
    else:
        sys.exit(f"{args.port}~{args.port + 19} 포트가 모두 사용 중입니다. --port 로 다른 번호를 지정하세요.")
    if args.reset:
        import shutil
        shutil.rmtree(demo / "workspace", ignore_errors=True)
        shutil.rmtree(demo / "docs" / "uploads", ignore_errors=True)
    try:
        runpy.run_path(str(demo / "build_demo.py"), run_name="__main__")      # ERP db + document ingest
    except PermissionError as e:
        sys.exit(f"\n데모 파일이 다른 프로세스(이전에 띄운 EDM-AI 서버)에서 사용 중입니다: {e.filename}\n"
                 "그 서버 창을 Ctrl+C 로 끄거나, start-server.bat 를 실행하면 이전 서버를 자동 종료합니다.")
    url = f"http://{probe_host}:{port}/"
    lan = ""
    if args.lan:
        lan = (f"\n  ! 같은 네트워크의 다른 PC 에서는  http://{_lan_ip()}:{port}/  로 접속합니다 (방화벽에서 {port} 허용 필요).\n"
               "  ! 데모는 로그인 비밀번호가 없습니다 - 신뢰할 수 있는 내부망에서만 쓰세요. 실제 운영은 docs/OPERATIONS.md 의 인증 설정을 따르세요.\n")
    bar = "=" * 66
    print(f"\n{bar}\n  ICC JEJU 문서·AI 플랫폼 v{__version__}  (제주국제컨벤션센터)\n"
          f"  이 주소를 여세요  ->  {url}\n{stale}{lan}"
          f"  상단에서 김유치(MICE유치) / 박재정(경영지원) / 최협력(외부) 를 바꿔 보세요.  종료: Ctrl+C\n{bar}\n")
    if not args.no_browser:
        threading.Timer(1.0, lambda: webbrowser.open(url)).start()
    from .server import serve
    try:
        serve(App(str(demo / "config.json")), args.host, port, demo_login=True)
    except OSError as e:
        sys.exit(f"cannot listen on {args.host}:{port} ({e}); try --port {port + 1}")
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
    d.add_argument("--port", type=int, default=8787, help="first port to try (the next free one is used if it is busy)")
    d.add_argument("--host", default="127.0.0.1")
    d.add_argument("--no-browser", action="store_true")
    d.add_argument("--lan", action="store_true", help="listen on all interfaces so other PCs on the same network can open it (demo login has NO password: trusted network only)")
    d.add_argument("--reset", action="store_true", help="delete the demo workspace first (fresh data, no leftovers from an older version)")
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
    su = sub.add_parser("sync-users", help="build/refresh users.json from a CSV or LDIF export (dry run unless --apply)")
    su.add_argument("source")
    su.add_argument("--map", help="mapping JSON (columns, dept_map, group_roles, title_levels ...)")
    su.add_argument("--format", choices=["csv", "ldif"])
    su.add_argument("--apply", action="store_true", help="write users.json (a .bak copy is kept); a running server reloads it automatically")
    su.add_argument("--prune", action="store_true", help="also remove users that are not in the source (admins are never removed)")
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
    if args.cmd == "sync-users":                        # needs only the config's users path, not the stores
        import json as _json
        from pathlib import Path
        from .directory import DirectoryError, sync
        cfgp = Path(args.config).resolve()
        upath = (cfgp.parent / _json.loads(cfgp.read_text("utf-8")).get("users", "users.json")).resolve()
        try:
            res = sync(args.source, upath, args.map, args.format, args.apply, args.prune)
        except (DirectoryError, OSError, ValueError, KeyError) as e:
            sys.exit(f"sync-users: {e}")
        print(_json.dumps(res, ensure_ascii=False, indent=2))
        if not args.apply:
            print("\n(dry run - nothing written. Add --apply to write " + str(upath) + ")")
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
