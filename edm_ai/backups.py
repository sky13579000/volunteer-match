"""Scheduled backups: a verified zip per run into a folder, with retention and a run log."""
from __future__ import annotations

import logging
import re
from datetime import datetime, timedelta, timezone
from pathlib import Path

from .backup import backup, verify
from .errors import DmsError

log = logging.getLogger("edm_ai")


class BackupScheduler:
    def __init__(self, app):
        self.app = app
        self.running = False

    @property
    def cfg(self) -> dict:
        return self.app.cfg.get("backup") or {}

    def enabled(self) -> bool:
        return bool(self.cfg.get("dir"))

    def _dir(self) -> Path:
        d = self.app._p(self.cfg["dir"])
        d.mkdir(parents=True, exist_ok=True)
        return d

    def due(self) -> bool:
        c = self.cfg
        if not self.enabled() or self.running or not c.get("every_hours"):
            return False
        with self.app.lock:
            last = self.app.store.db.execute("SELECT ts FROM backup_runs WHERE ok=1 ORDER BY id DESC LIMIT 1").fetchone()
        return not last or (datetime.now(timezone.utc) - datetime.fromisoformat(last[0])).total_seconds() >= float(c["every_hours"]) * 3600

    def run(self, by="system") -> dict:
        if not self.enabled():
            raise DmsError(400, "백업 폴더가 설정되지 않았습니다 (설정 backup.dir)")
        if self.running:
            raise DmsError(409, "백업이 이미 실행 중입니다")
        self.running = True
        db = self.app.store.db
        lock = self.app.lock
        try:
            out = self._dir() / f"edm-backup-{datetime.now().strftime('%Y%m%d-%H%M%S')}.zip"
            with self.app.lock:
                res = backup(db, self.app.dms.repo, out)
            ok, problems = verify(out)
            if ok:
                self._prune()
            with lock:
                db.execute("INSERT INTO backup_runs(ts,ok,path,bytes,files,note,by) VALUES(?,?,?,?,?,?,?)",
                           (datetime.now(timezone.utc).isoformat(timespec="seconds"), int(ok), str(out), res["bytes"], res["files"], "; ".join(problems)[:300], by))
                db.commit()
            if not ok:
                log.error("backup verification failed: %s", problems[:3])
            return {"ok": ok, "path": str(out), "bytes": res["bytes"], "problems": problems}
        except Exception as e:
            with lock:
                db.execute("INSERT INTO backup_runs(ts,ok,path,bytes,files,note,by) VALUES(?,?,?,?,?,?,?)",
                           (datetime.now(timezone.utc).isoformat(timespec="seconds"), 0, "", 0, 0, f"{type(e).__name__}: {str(e)[:200]}", by))
                db.commit()
            raise
        finally:
            self.running = False

    def _prune(self):
        keep = max(1, int(self.cfg.get("keep", 7)))
        files = sorted(p for p in self._dir().iterdir() if re.fullmatch(r"edm-backup-\d{8}-\d{6}\.zip", p.name))
        for p in files[:-keep]:
            p.unlink(missing_ok=True)

    def status(self) -> dict:
        rows = self.app.store.db.execute("SELECT ts,ok,path,bytes,files,note,by FROM backup_runs ORDER BY id DESC LIMIT 20").fetchall()
        runs = [dict(zip(("ts", "ok", "path", "bytes", "files", "note", "by"), r)) for r in rows]
        last_ok = next((r for r in runs if r["ok"]), None)
        age = int((datetime.now(timezone.utc) - datetime.fromisoformat(last_ok["ts"])).total_seconds()) if last_ok else None
        return {"enabled": self.enabled(), "dir": self.cfg.get("dir", ""), "every_hours": self.cfg.get("every_hours"), "keep": self.cfg.get("keep", 7),
                "running": self.running, "runs": runs, "last_ok_age_seconds": age,
                "note": "백업은 문서 DB와 저장 파일을 담습니다. 설정 파일·사용자 파일·암호화 키·외부 업무 DB는 포함되지 않으니 별도로 보관하세요. 백업 폴더는 다른 디스크/서버로 복제하세요."}
