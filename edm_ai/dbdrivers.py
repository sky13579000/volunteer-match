from __future__ import annotations

import os
import re
import sqlite3
import threading

_NAMED = re.compile(r"(?<![:\w]):([A-Za-z_]\w*)")


class Conn:
    """Thin dialect layer so SqlGateway is database-agnostic. Subclasses only translate:
    placeholders, row limiting, schema introspection and read-only enforcement.
    All SQL handed in uses `:name` placeholders and double-quoted identifiers."""

    name = "base"

    def __init__(self):
        self._lock = threading.RLock()

    def query(self, sql: str, params: dict | None = None) -> list[dict]:
        raise NotImplementedError

    def columns(self, table: str) -> set[str]:
        raise NotImplementedError

    def limit(self, n: int) -> str:
        return f"LIMIT {int(n)}"  # int() so a limit can never carry SQL


class SQLite(Conn):
    name = "sqlite"

    def __init__(self, path: str):
        super().__init__()
        self.db = sqlite3.connect(f"file:{path}?mode=ro", uri=True, check_same_thread=False)
        self.db.row_factory = sqlite3.Row

    def query(self, sql, params=None):
        with self._lock:
            return [dict(r) for r in self.db.execute(sql, params or {})]

    def columns(self, table):
        return {r["name"] for r in self.query(f'PRAGMA table_info("{table}")')}


class Postgres(Conn):
    """PostgreSQL via psycopg 3 (`pip install "psycopg[binary]"`). Session is forced read-only server-side."""
    name = "postgres"

    def __init__(self, dsn: str, schema: str = "public"):
        super().__init__()
        try:
            import psycopg
            from psycopg.rows import dict_row
        except ImportError as e:
            raise SystemExit('postgres driver missing: pip install "psycopg[binary]"') from e
        self.schema = schema
        self.db = psycopg.connect(dsn, autocommit=True, row_factory=dict_row)
        # NOTE: psycopg's `read_only` attribute is ignored in autocommit mode (verified: DELETE succeeded),
        # so enforce it as a session default that every implicit transaction inherits.
        self.db.execute("SET default_transaction_read_only = on")
        self.db.execute("SET statement_timeout = 15000")

    def query(self, sql, params=None):
        # '%' must be doubled for psycopg; ':name' -> '%(name)s'
        pg = _NAMED.sub(lambda m: f"%({m.group(1)})s", sql.replace("%", "%%"))
        with self._lock:
            return list(self.db.execute(pg, params or {}).fetchall())

    def columns(self, table):
        return {r["column_name"] for r in self.query(
            "SELECT column_name FROM information_schema.columns WHERE table_schema = :s AND table_name = :t",
            {"s": self.schema, "t": table})}


class Oracle(Conn):
    """Oracle 12c+ via python-oracledb thin mode (`pip install oracledb`, no Instant Client needed).
    Quoted identifiers are case-sensitive: configure table/column names exactly as stored (usually UPPERCASE).
    Use a SELECT-only database account; there is no portable client-side read-only switch."""
    name = "oracle"

    def __init__(self, dsn: str, user: str, password: str):
        super().__init__()
        try:
            import oracledb
        except ImportError as e:
            raise SystemExit("oracle driver missing: pip install oracledb") from e
        self.db = oracledb.connect(user=user, password=password, dsn=dsn)
        self.db.call_timeout = 15000

    def query(self, sql, params=None):
        with self._lock, self.db.cursor() as cur:
            cur.execute(sql, params or {})
            names = [d[0] for d in cur.description]
            return [dict(zip(names, row)) for row in cur.fetchall()]

    def columns(self, table):
        return {r["COLUMN_NAME"] for r in self.query(
            "SELECT column_name FROM user_tab_columns WHERE table_name = :t", {"t": table})}

    def limit(self, n):
        return f"FETCH FIRST {int(n)} ROWS ONLY"


def connect(spec: dict, base_dir=None) -> Conn:
    """spec: {"driver": "sqlite", "path": ...} | {"driver": "postgres", "dsn_env": "ERP_DSN"} |
    {"driver": "oracle", "dsn_env": ..., "user_env": ..., "password_env": ...}.
    Credentials come from environment variables (never from the JSON file)."""
    d = spec.get("driver", "sqlite")
    if d == "sqlite":
        path = spec["path"]
        if base_dir and not os.path.isabs(path):
            path = os.path.join(base_dir, path)
        return SQLite(path)
    env = lambda k: os.environ.get(spec[k], "") if spec.get(k) else ""  # noqa: E731
    if d == "postgres":
        dsn = env("dsn_env") or spec.get("dsn", "")
        if not dsn:
            raise SystemExit(f"set env {spec.get('dsn_env')} to the PostgreSQL DSN")
        return Postgres(dsn, spec.get("schema", "public"))
    if d == "oracle":
        return Oracle(env("dsn_env"), env("user_env"), env("password_env"))
    raise ValueError(f"unknown driver {d}")
