"""Directory sync: build users.json from the organisation's own master data (HR system / groupware / AD export).

Sources (no extra packages):
  * CSV  - any export with a header row
  * LDIF - `ldapsearch -LLL -x -H ldaps://... -b "dc=corp" "(objectClass=person)" > users.ldif`
The mapping file says which source column/attribute means what, how departments and groups map to EDM-AI
departments/roles, and how job titles map to security levels. Default is a DRY RUN that prints the diff.
"""
from __future__ import annotations

import base64
import csv
import json
import os
import re
import tempfile
from pathlib import Path

DEFAULT_MAP = {
    "fields": {"id": "id", "name": "name", "dept": "dept", "title": "title", "groups": "groups", "email": "email", "slack": "slack"},
    "dept_map": {},          # source department name/code -> EDM-AI dept id.  Unmapped -> `unmapped_dept` (never a privileged dept)
    "unmapped_dept": "external",
    "group_roles": {},       # source group name -> EDM-AI role id(s), e.g. {"CN=finance-managers": "finance"}
    "title_levels": {},      # job title -> security level, e.g. {"팀장": 3, "과장": 2}
    "default_level": 1,
    "max_level": 3,          # levels above this can only be set by hand (admin-level 99 is never granted by sync)
    "external_fields": {},   # extra identities for adapters, e.g. {"empno": "사번"} -> users.json external.empno
    "multi_sep": ";",
    "id_from_email": False,  # derive the id from the e-mail local part when the id column is empty
    "preserve": [],          # ids that sync never touches (admins, service accounts)
    "ignore_inactive": {"field": "", "values": []},   # e.g. {"field": "status", "values": ["퇴직", "휴직"]}
}
ID_RE = re.compile(r"[a-z0-9][a-z0-9._-]{0,39}")


class DirectoryError(Exception):
    pass


def load_map(path: str | None) -> dict:
    m = json.loads(json.dumps(DEFAULT_MAP))
    if path:
        user = json.loads(Path(path).read_text("utf-8"))
        for k, v in user.items():
            if k not in DEFAULT_MAP:
                raise DirectoryError(f"unknown mapping key: {k}")
            m[k] = {**m[k], **v} if isinstance(m[k], dict) else v
    return m


# ---------------- readers ----------------
def read_csv(path: str) -> list[dict]:
    raw = Path(path).read_bytes()
    for enc in ("utf-8-sig", "cp949"):            # Excel on Korean Windows saves CSV as cp949
        try:
            text = raw.decode(enc)
            break
        except UnicodeDecodeError:
            continue
    else:
        raise DirectoryError("CSV must be UTF-8 or CP949")
    rows = list(csv.DictReader(text.splitlines()))
    if not rows:
        raise DirectoryError("CSV has no data rows")
    return [{(k or "").strip(): (v or "").strip() for k, v in r.items()} for r in rows]


def read_ldif(path: str) -> list[dict]:
    text = Path(path).read_bytes().decode("utf-8")
    text = re.sub(r"\r?\n[ \t]", "", text)        # RFC 2849 line folding
    out, cur = [], {}
    for line in text.splitlines() + [""]:
        if not line.strip():
            if cur:
                out.append(cur)
            cur = {}
            continue
        if line.startswith("#") or ":" not in line:
            continue
        k, _, v = line.partition(":")
        if v.startswith(":"):
            v = base64.b64decode(v[1:].strip()).decode("utf-8", "replace")
        else:
            v = v.strip()
        k = k.strip()
        cur[k] = f"{cur[k]};{v}" if k in cur else v   # multi-valued attributes (memberOf) are joined
    if not out:
        raise DirectoryError("LDIF has no entries")
    return out


# ---------------- mapping ----------------
def _multi(v: str, sep: str) -> list[str]:
    return [x.strip() for x in str(v or "").split(sep) if x.strip()]


def to_users(rows: list[dict], m: dict) -> tuple[list[dict], list[str]]:
    f, users, warnings, seen = m["fields"], [], [], set()
    for n, r in enumerate(rows, 2):
        ig = m["ignore_inactive"]
        if ig.get("field") and r.get(ig["field"], "") in ig.get("values", []):
            continue
        email = r.get(f["email"], "").lower()
        uid = (r.get(f["id"], "") or (email.split("@")[0] if m["id_from_email"] and email else "")).strip().lower()
        if not ID_RE.fullmatch(uid or ""):
            warnings.append(f"row {n}: invalid or missing id {uid!r} - skipped")
            continue
        if uid in seen:
            warnings.append(f"row {n}: duplicate id {uid!r} - skipped")
            continue
        seen.add(uid)
        src_dept = r.get(f["dept"], "")
        dept = m["dept_map"].get(src_dept)
        if dept is None:
            dept = m["unmapped_dept"]
            warnings.append(f"{uid}: department {src_dept!r} is not in dept_map -> {dept}")
        roles = sorted({role for g in _multi(r.get(f["groups"], ""), m["multi_sep"])
                        for role in _multi(m["group_roles"].get(g, ""), ",")})
        if "admin" in roles:
            warnings.append(f"{uid}: the 'admin' role cannot be granted by sync - removed")
            roles.remove("admin")
        level = int(m["title_levels"].get(r.get(f["title"], ""), m["default_level"]))
        if level > m["max_level"]:
            warnings.append(f"{uid}: level {level} above max_level {m['max_level']} - capped")
            level = m["max_level"]
        u = {"id": uid, "name": r.get(f["name"], uid), "dept": dept, "roles": roles, "level": level}
        ext = {}
        if email:
            u["email"] = email
            ext["email"] = email
        if r.get(f["slack"]):
            ext["slack"] = r[f["slack"]]
        for key, col in m["external_fields"].items():
            if r.get(col):
                ext[key] = r[col]
        if ext:
            u["external"] = ext
        users.append(u)
    return users, warnings


def merge(existing: list[dict], incoming: list[dict], m: dict, prune: bool) -> tuple[list[dict], dict]:
    """Result + diff. Protected: `preserve` ids and anyone holding the admin role are never changed or removed."""
    protected = set(m["preserve"]) | {u["id"] for u in existing if "admin" in u.get("roles", [])}
    by_id = {u["id"]: u for u in existing}
    diff = {"added": [], "changed": [], "removed": [], "unchanged": 0, "protected_skipped": []}
    result = []
    for u in incoming:
        if u["id"] in protected:
            diff["protected_skipped"].append(u["id"])
            continue
        old = by_id.get(u["id"])
        merged = {**(old or {}), **u}
        if old and "external" in u:                               # keep identities the source does not manage (e.g. a Slack id)
            merged["external"] = {**(old.get("external") or {}), **u["external"]}
        if not old:
            diff["added"].append(u["id"])
        elif any(old.get(k) != merged.get(k) for k in u):
            diff["changed"].append({"id": u["id"], "from": {k: old.get(k) for k in u if old.get(k) != merged.get(k)}, "to": {k: merged[k] for k in u if old.get(k) != merged.get(k)}})
        else:
            diff["unchanged"] += 1
        result.append(merged)
    inc = {u["id"] for u in incoming}
    for u in existing:
        if u["id"] in protected:
            result.append(u)
        elif u["id"] not in inc:
            if prune:
                diff["removed"].append(u["id"])
            else:
                result.append(u)
    order = {u["id"]: i for i, u in enumerate(existing)}
    result.sort(key=lambda u: (order.get(u["id"], 10**6), u["id"]))
    return result, diff


def validate(users: list[dict]):
    if not any("admin" in u.get("roles", []) for u in users):
        raise DirectoryError("refusing to write a user list without any admin")
    if len({u["id"] for u in users}) != len(users):
        raise DirectoryError("duplicate ids")
    emails = [u["email"] for u in users if u.get("email")]
    if len(set(emails)) != len(emails):
        raise DirectoryError("two users share the same e-mail (SSO mapping would be ambiguous)")


def write_users(path: Path, users: list[dict]):
    """Atomic write + .bak of the previous file; the running server notices the new mtime and reloads."""
    validate(users)
    if path.exists():
        (path.parent / (path.name + ".bak")).write_bytes(path.read_bytes())
    fd, tmp = tempfile.mkstemp(dir=path.parent, suffix=".tmp")
    with os.fdopen(fd, "w", encoding="utf-8") as fh:
        json.dump(users, fh, ensure_ascii=False, indent=2)
    os.replace(tmp, path)


def sync(source: str, users_path: Path, map_path: str | None, fmt: str | None = None, apply: bool = False, prune: bool = False) -> dict:
    m = load_map(map_path)
    fmt = fmt or ("ldif" if source.lower().endswith((".ldif", ".ldf")) else "csv")
    rows = read_ldif(source) if fmt == "ldif" else read_csv(source)
    incoming, warnings = to_users(rows, m)
    existing = json.loads(users_path.read_text("utf-8")) if users_path.exists() else []
    result, diff = merge(existing, incoming, m, prune)
    diff["warnings"] = warnings
    diff["applied"] = False
    if apply:
        write_users(users_path, result)
        diff["applied"] = True
    else:
        validate(result)
    return diff
