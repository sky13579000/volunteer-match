from __future__ import annotations

import hashlib
import json
import shutil
import sqlite3
import tempfile
import zipfile
from datetime import datetime, timezone
from pathlib import Path, PurePosixPath

FORMAT = 1


def _sha(path: Path) -> str:
    h = hashlib.sha256()
    with open(path, "rb") as f:
        for chunk in iter(lambda: f.read(1 << 20), b""):
            h.update(chunk)
    return h.hexdigest()


def backup(store_db: sqlite3.Connection, repo: Path, out: Path) -> dict:
    """One zip with a consistent database snapshot + every stored file + a SHA-256 manifest.
    Uses SQLite's online-backup API, so it is safe while the server is running.
    NOT included: the external business DB, crawled source folders, configuration and secrets."""
    out = Path(out)
    with tempfile.TemporaryDirectory() as tmp:
        snap = Path(tmp) / "store.db"
        dst = sqlite3.connect(snap)
        try:
            store_db.backup(dst)
        finally:
            dst.close()
        files = {"store.db": _sha(snap)}
        repo_files = sorted(p for p in Path(repo).rglob("*") if p.is_file() and not p.name.endswith(".part"))
        for p in repo_files:
            files["repo/" + p.relative_to(repo).as_posix()] = _sha(p)
        manifest = {"format": FORMAT, "created": datetime.now(timezone.utc).isoformat(timespec="seconds"), "files": files,
                    "counts": {"repo_files": len(repo_files)}}
        tmp_zip = out.with_suffix(out.suffix + ".part")
        with zipfile.ZipFile(tmp_zip, "w", zipfile.ZIP_DEFLATED) as z:
            z.write(snap, "store.db")
            for p in repo_files:
                z.write(p, "repo/" + p.relative_to(repo).as_posix())
            z.writestr("MANIFEST.json", json.dumps(manifest, ensure_ascii=False, indent=1))
        tmp_zip.replace(out)
    return {"path": str(out), "files": len(files), "bytes": out.stat().st_size, "created": manifest["created"]}


def _safe(name: str) -> bool:
    p = PurePosixPath(name)
    return not p.is_absolute() and ".." not in p.parts and "\\" not in name and (name == "store.db" or name == "MANIFEST.json" or name.startswith("repo/"))


def verify(archive: Path) -> tuple[bool, list[str]]:
    """Checks the manifest against the archive: unexpected entries, unsafe paths, missing / altered / extra files."""
    problems: list[str] = []
    try:
        z = zipfile.ZipFile(archive)
    except (zipfile.BadZipFile, OSError) as e:
        return False, [f"not a readable archive: {e}"]
    with z:
        names = [i.filename for i in z.infolist() if not i.is_dir()]
        bad = [n for n in names if not _safe(n)]
        if bad:
            return False, [f"unsafe or unexpected path in archive: {n}" for n in bad[:5]]
        if "MANIFEST.json" not in names:
            return False, ["MANIFEST.json is missing"]
        try:
            manifest = json.loads(z.read("MANIFEST.json"))
        except ValueError:
            return False, ["MANIFEST.json is not valid JSON"]
        if manifest.get("format") != FORMAT:
            problems.append(f"unsupported backup format {manifest.get('format')}")
        want = manifest.get("files", {})
        for n, sha in want.items():
            if n not in names:
                problems.append(f"missing: {n}")
                continue
            h = hashlib.sha256()
            with z.open(n) as f:
                for chunk in iter(lambda: f.read(1 << 20), b""):
                    h.update(chunk)
            if h.hexdigest() != sha:
                problems.append(f"altered: {n}")
        problems += [f"not in manifest: {n}" for n in names if n != "MANIFEST.json" and n not in want]
    return not problems, problems


def restore(archive: Path, store_path: Path, repo: Path, force: bool = False) -> dict:
    """Verifies first, then replaces the store and repository. Refuses to overwrite existing data unless force=True."""
    ok, problems = verify(archive)
    if not ok:
        raise ValueError("backup verification failed: " + "; ".join(problems[:5]))
    store_path, repo = Path(store_path), Path(repo)
    existing = store_path.exists() or (repo.exists() and any(repo.iterdir()))
    if existing and not force:
        raise FileExistsError(f"{store_path} / {repo} already contain data; pass force=True (--force) to replace it")
    with tempfile.TemporaryDirectory(dir=store_path.parent if store_path.parent.exists() else None) as tmp:
        with zipfile.ZipFile(archive) as z:
            for n in z.namelist():
                if n == "MANIFEST.json" or n.endswith("/"):
                    continue
                dest = Path(tmp) / n
                dest.parent.mkdir(parents=True, exist_ok=True)
                with z.open(n) as src, open(dest, "wb") as out:
                    shutil.copyfileobj(src, out)
        store_path.parent.mkdir(parents=True, exist_ok=True)
        if repo.exists():
            shutil.rmtree(repo)
        (Path(tmp) / "repo").mkdir(exist_ok=True)
        shutil.move(str(Path(tmp) / "repo"), str(repo))
        for suffix in ("", "-wal", "-shm"):
            Path(str(store_path) + suffix).unlink(missing_ok=True)
        shutil.move(str(Path(tmp) / "store.db"), str(store_path))
    return {"restored_store": str(store_path), "restored_repo": str(repo)}
