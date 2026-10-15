"""PC sync agent: keeps a local folder centralised in the platform ("문서 중앙화 유도").

  python tools/edm_sync.py --url https://edm.example --key edmk_xxx --src "C:\\Users\\me\\Documents\\계약" --folder "개인문서/계약" [--interval 300] [--dry-run]

* one-way: new and changed files are uploaded (new version when the content changed), nothing is ever deleted on either side
* the key needs scope `write` and acts as ONE user: the files are registered in that user's name and permissions apply as usual
* state is kept in <src>/.edm_sync_state.json (hash per file), so only real changes are sent
* run it from Task Scheduler / cron, or leave it running with --interval
"""
import argparse
import fnmatch
import hashlib
import json
import sys
import time
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
from edm_ai.client import EdmApiError, EdmClient  # noqa: E402

EXCLUDE = ["~$*", "*.tmp", "*.temp", "Thumbs.db", "desktop.ini", ".DS_Store", "*.lnk", ".edm_sync_state.json", ".~lock*", "*.part", "*.crdownload"]
BLOCKED = {".exe", ".bat", ".cmd", ".com", ".scr", ".msi", ".dll", ".js", ".vbs", ".ps1", ".jar", ".sh", ".apk", ".app", ".lnk"}


def ensure_folder(c: EdmClient, path: str) -> int:
    parent, tree = None, c.folders()
    for part in [p for p in path.split("/") if p.strip()]:
        hit = next((f for f in tree if f["parent_id"] == parent and f["name"] == part), None)
        if hit:
            parent = hit["id"]
        else:
            parent = c.make_folder(parent, part)["id"]
            tree = c.folders()
    if parent is None:
        raise SystemExit("--folder is required")
    return parent


def sync_once(c: EdmClient, src: Path, folder_id: int, doc_type: str, exclude, max_mb: int, dry_run: bool, log=print) -> dict:
    state_file = src / ".edm_sync_state.json"
    try:
        state = json.loads(state_file.read_text("utf-8"))
    except (OSError, ValueError):
        state = {}
    stats = {"uploaded": 0, "updated": 0, "unchanged": 0, "skipped": 0, "errors": 0}
    for p in sorted(src.rglob("*")):
        if not p.is_file():
            continue
        rel = p.relative_to(src).as_posix()
        if any(fnmatch.fnmatch(p.name.lower(), x.lower()) or fnmatch.fnmatch(rel.lower(), x.lower()) for x in exclude) or p.suffix.lower() in BLOCKED:
            stats["skipped"] += 1
            continue
        size = p.stat().st_size
        if size == 0 or size > max_mb * 1048576:
            stats["skipped"] += 1
            continue
        sha = hashlib.sha256(p.read_bytes()).hexdigest()
        prev = state.get(rel)
        if prev and prev["sha"] == sha:
            stats["unchanged"] += 1
            continue
        try:
            if dry_run:
                stats["updated" if prev else "uploaded"] += 1
                log(f"[미리보기] {'새 버전' if prev else '업로드'}: {rel}")
                continue
            if prev:
                c.new_version(prev["doc_id"], str(p), comment="PC 동기화")
                stats["updated"] += 1
                doc_id = prev["doc_id"]
            else:
                d = c.upload(str(p), folder_id=folder_id, doc_type=doc_type, tags=["PC동기화"], props={"원본경로": rel[:180]})
                doc_id = d["id"]
                stats["uploaded"] += 1
            state[rel] = {"sha": sha, "doc_id": doc_id}
            log(f"{'새 버전' if prev else '업로드'}: {rel}")
        except EdmApiError as e:
            if e.status == 409 and "내용이 같" in e.message:
                state[rel] = {"sha": sha, "doc_id": prev["doc_id"] if prev else 0}
                stats["unchanged"] += 1
            else:
                stats["errors"] += 1
                log(f"오류: {rel}: {e.message}")
    if not dry_run:
        state_file.write_text(json.dumps(state, ensure_ascii=False), "utf-8")
    return stats


def main(argv=None):
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--url", required=True)
    ap.add_argument("--key", required=True, help="API key with scope write (Authorization: Bearer)")
    ap.add_argument("--src", required=True, help="local folder to keep centralised")
    ap.add_argument("--folder", required=True, help='destination folder path in the platform, e.g. "개인문서/계약" (created when missing)')
    ap.add_argument("--doc-type", default="GEN")
    ap.add_argument("--exclude", action="append", default=[], help="extra glob to skip (repeatable)")
    ap.add_argument("--max-mb", type=int, default=100)
    ap.add_argument("--interval", type=int, default=0, help="seconds between runs; 0 = run once")
    ap.add_argument("--dry-run", action="store_true")
    a = ap.parse_args(argv)
    src = Path(a.src)
    if not src.is_dir():
        raise SystemExit(f"폴더가 없습니다: {src}")
    c = EdmClient(a.url, token=a.key)
    fid = None if a.dry_run else ensure_folder(c, a.folder)
    while True:
        st = sync_once(c, src, fid, a.doc_type, EXCLUDE + a.exclude, a.max_mb, a.dry_run)
        print(time.strftime("%H:%M:%S"), json.dumps(st, ensure_ascii=False))
        if not a.interval:
            return 1 if st["errors"] else 0
        time.sleep(a.interval)


if __name__ == "__main__":
    sys.exit(main())
