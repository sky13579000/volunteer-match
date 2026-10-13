from __future__ import annotations

from .dbdrivers import Conn, SQLite
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

    AGGS = {"sum": "SUM", "avg": "AVG", "count": "COUNT", "min": "MIN", "max": "MAX"}

    def __init__(self, conn: Conn | str, tables: list[dict], metrics: list[dict] | None = None):
        self.db = SQLite(conn) if isinstance(conn, str) else conn   # str = sqlite path (backwards compatible)
        self.tables = {}
        for t in tables:
            real = self.db.columns(t["name"])
            wanted = set(t["columns"]) | set(t.get("link", {}).values()) | {t["key"]}
            if not wanted <= real:
                raise ValueError(f"table {t['name']}: unknown columns {sorted(wanted - real)}")
            self.tables[t["name"]] = t
        self.metrics = metrics or []
        for m in self.metrics:
            t = self.tables[m["table"]]
            if m["agg"] not in self.AGGS or m["column"] not in t["columns"] or \
                    (m.get("group_by") and m["group_by"] not in t["columns"]):
                raise ValueError(f"bad metric {m.get('name')}")

    def link_types(self) -> set[str]:
        return {et for t in self.tables.values() for et in t.get("link", {})}

    def distinct(self, table: str, column: str) -> list[str]:
        assert table in self.tables and column in self.tables[table]["columns"]
        rows = self.db.query(f'SELECT DISTINCT {_ident(column)} AS "v" FROM {_ident(table)}')
        return [r["v"] for r in rows if r["v"]]

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
                       f"WHERE {_ident(col)} = :v__ AND {clause} {self.db.limit(limit)}")
                for r in self.db.query(sql, {**params, "v__": value}):
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


    def metrics_for(self, question: str) -> list[dict]:
        return [m for m in self.metrics if any(k in question for k in m["keywords"])]

    def aggregate(self, p: Principal, m: dict, entities) -> dict | None:
        """Governed aggregate: only declared metrics; row policy applied BEFORE aggregation; every column
        used (measure, group, filters) must be visible to the caller, otherwise nothing is computed."""
        t = self.tables[m["table"]]
        used = [m["column"]] + ([m["group_by"]] if m.get("group_by") else [])
        by_type: dict[str, list[str]] = {}
        for ent in sorted(entities):
            et, _, v = ent.partition(":")
            if t.get("link", {}).get(et):
                by_type.setdefault(et, []).append(v)
        used += [t["link"][et] for et in by_type]
        if not all(self._col_visible(t["columns"][c], p) for c in used):
            return None
        clause, params = self._row_clause(t, p)
        if clause is None:
            return None
        conds, args = [clause], dict(params)
        for et, vals in by_type.items():
            names = [f":f_{et}_{i}" for i in range(len(vals))]
            conds.append(f"{_ident(t['link'][et])} IN ({', '.join(names)})")
            args.update({n[1:]: v for n, v in zip(names, vals)})
        g = _ident(m["group_by"]) + ' AS "g", ' if m.get("group_by") else ""
        sql = (f"SELECT {g}{self.AGGS[m['agg']]}({_ident(m['column'])}) AS \"v\", COUNT(*) AS \"n\" "
               f"FROM {_ident(t['name'])} WHERE {' AND '.join(conds)}" + (f" GROUP BY {_ident(m['group_by'])}" if g else ""))
        rows = self.db.query(sql, args)
        rows = [r for r in rows if r["n"]]
        return {"metric": m["name"], "label": m.get("label", m["name"]), "rows": rows,
                "group_by": m.get("group_by")} if rows else None


def _ident(name: str) -> str:
    if not name.replace("_", "").isalnum():
        raise ValueError(f"bad identifier: {name!r}")
    return f'"{name}"'
