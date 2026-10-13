from __future__ import annotations

import sqlite3

from .models import Principal

MASK = "●●●"


class SqlGateway:
    """Policy-enforcing read gateway to the structured DB (the 'single door').

    * Only whitelisted tables/columns (validated against the live schema at startup).
    * Column-level masking by role / clearance level.
    * Row-level policy: unconditional roles OR parameterised WHERE rules.
    * Lookups are by business key only (entity links); values are always bound parameters.
    No free-form SQL from users or from an LLM ever reaches the database.
    Any DB-API driver works (sqlite3 shown; swap connect() for Oracle/PostgreSQL/Tibero on-prem)."""

    def __init__(self, path: str, tables: list[dict]):
        self.db = sqlite3.connect(f"file:{path}?mode=ro", uri=True, check_same_thread=False)
        self.db.row_factory = sqlite3.Row
        self.tables = {}
        for t in tables:
            real = {r["name"] for r in self.db.execute(f"PRAGMA table_info({_ident(t['name'])})")}
            wanted = set(t["columns"]) | set(t.get("link", {}).values()) | {t["key"]}
            if not wanted <= real:
                raise ValueError(f"table {t['name']}: unknown columns {sorted(wanted - real)}")
            self.tables[t["name"]] = t

    def link_types(self) -> set[str]:
        return {et for t in self.tables.values() for et in t.get("link", {})}

    def distinct(self, table: str, column: str) -> list[str]:
        assert table in self.tables and column in self.tables[table]["columns"]
        return [r[0] for r in self.db.execute(f"SELECT DISTINCT {_ident(column)} FROM {_ident(table)}") if r[0]]

    @staticmethod
    def _col_visible(spec: dict, p: Principal) -> bool:
        if p.level < spec.get("min_level", 0):
            return False
        roles = spec.get("roles")
        return not roles or bool(p.roles & set(roles))

    def _row_clause(self, t: dict, p: Principal):
        rules = t.get("row_policy")
        if not rules:
            return "1=1", {}
        if any(p.roles & set(r["roles"]) for r in rules if "roles" in r):
            return "1=1", {}
        wheres = [r["where"] for r in rules if "where" in r]
        if not wheres:
            return None, {}
        return "(" + " OR ".join(wheres) + ")", {"uid": p.id, "dept": p.dept, "level": p.level}

    def lookup(self, p: Principal, entities, limit: int = 6) -> list[dict]:
        """entities: iterable of 'type:value'. Returns visible rows with masking applied."""
        out, seen = [], set()
        for ent in sorted(entities):
            etype, _, value = ent.partition(":")
            for t in self.tables.values():
                col = t.get("link", {}).get(etype)
                if not col or not self._col_visible(t["columns"][col], p):
                    continue  # never let a masked column be probed via filtering
                clause, params = self._row_clause(t, p)
                if clause is None:
                    continue
                cols = list(t["columns"])
                sql = (f"SELECT {', '.join(_ident(c) for c in cols)} FROM {_ident(t['name'])} "
                       f"WHERE {_ident(col)} = :__v AND {clause} LIMIT :__n")
                for r in self.db.execute(sql, {**params, "__v": value, "__n": limit}):
                    if (t["name"], r[t["key"]]) in seen:
                        continue  # same row reached through several entity links
                    seen.add((t["name"], r[t["key"]]))
                    vals, masked = {}, []
                    for c in cols:
                        if self._col_visible(t["columns"][c], p):
                            vals[c] = r[c]
                        else:
                            vals[c] = MASK
                            masked.append(c)
                    out.append({"table": t["name"], "title": t.get("title", t["name"]),
                                "key": r[t["key"]], "values": vals, "masked": tuple(masked),
                                "labels": {c: t["columns"][c].get("label", c) for c in cols}})
                    if len(out) >= limit:
                        return out
        return out


def _ident(name: str) -> str:
    if not name.replace("_", "").isalnum():
        raise ValueError(f"bad identifier: {name!r}")
    return f'"{name}"'
