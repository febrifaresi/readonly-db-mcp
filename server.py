"""readonly-db-mcp — MCP server database READ-ONLY yang kecil dan bisa diaudit.

Database: PostgreSQL, MySQL/MariaDB, SQL Server, SQLite, Oracle.
Tools  : list_connections, list_schemas, list_tables, describe_table,
         run_query (SELECT saja), explain_query.

Lapisan keamanan (dari yang terkuat):
  1. User database read-only (WAJIB, diatur di database — lihat README).
  2. Sesi/transaksi read-only di level database bila didukung.
  3. Validasi SQL dengan sqlglot (sql_guard.py), fail-closed.
  4. Setiap transaksi selalu di-ROLLBACK, batas baris & timeout.
  5. Audit log semua query (JSON Lines).
"""

from __future__ import annotations

import base64
import datetime as dt
import decimal
import json
import os
import re
import time
import tomllib
import uuid
from pathlib import Path
from typing import Any

from dotenv import load_dotenv
from mcp.types import ToolAnnotations

try:  # MCP Python SDK 2.x
    from mcp.server.mcpserver import MCPServer as _Server
except ImportError:  # MCP Python SDK 1.x
    from mcp.server.fastmcp import FastMCP as _Server
from sqlalchemy import create_engine, event, inspect
from sqlalchemy.engine import Connection, Engine

from sql_guard import UnsafeQuery, validate_select

# ---------------------------------------------------------------- konfigurasi
CONFIG_PATH = Path(os.environ.get("DBMCP_CONFIG", Path(__file__).with_name("connections.toml")))
load_dotenv(CONFIG_PATH.with_name(".env"))

with open(CONFIG_PATH, "rb") as f:
    _CFG = tomllib.load(f)

SETTINGS = _CFG.get("settings", {})
MAX_ROWS = int(SETTINGS.get("max_rows", 500))
TIMEOUT_S = int(SETTINGS.get("timeout_seconds", 30))
MAX_CELL_CHARS = int(SETTINGS.get("max_cell_chars", 2000))
AUDIT_LOG = Path(SETTINGS.get("audit_log", CONFIG_PATH.with_name("audit.log")))
CONNECTIONS: dict[str, dict] = _CFG.get("connections", {})

SUPPORTED = {"postgresql", "mysql", "mssql", "sqlite", "oracle"}
_IDENT = re.compile(r"^[A-Za-z0-9_$#@ .\-]{1,128}$")

for _name, _c in CONNECTIONS.items():
    if _c.get("type") not in SUPPORTED:
        raise SystemExit(f"Connection '{_name}': type must be one of {sorted(SUPPORTED)}")

mcp = _Server("readonly-db")
_RO = ToolAnnotations(readOnlyHint=True, destructiveHint=False, idempotentHint=True)
_engines: dict[str, Engine] = {}


# ---------------------------------------------------------------- engine/sesi
def _url_for(name: str, c: dict) -> str:
    if c["type"] == "sqlite":
        path = Path(c["path"]).expanduser().resolve()
        if not path.exists():
            raise ValueError(f"SQLite file not found: {path}")
        # mode=ro -> file dibuka read-only oleh SQLite sendiri
        return f"sqlite:///file:{path}?mode=ro&uri=true"
    env = c.get("url_env")
    url = os.environ.get(env or "")
    if not url:
        raise ValueError(f"Connection '{name}': environment variable '{env}' is not set (see .env).")
    return url


def _engine(name: str) -> Engine:
    if name not in CONNECTIONS:
        raise ValueError(f"Connection '{name}' does not exist. Use list_connections to see the list.")
    if name in _engines:
        return _engines[name]

    c = CONNECTIONS[name]
    db = c["type"]
    ms = TIMEOUT_S * 1000
    connect_args: dict[str, Any] = {}
    if db == "postgresql":
        connect_args = {"connect_timeout": 10}
    elif db == "mysql":
        connect_args = {"connect_timeout": 10, "read_timeout": TIMEOUT_S + 5}
    elif db == "mssql":
        connect_args = {"login_timeout": 10, "timeout": TIMEOUT_S}

    kwargs: dict[str, Any] = {"pool_pre_ping": True, "connect_args": connect_args}
    if db != "sqlite":
        kwargs.update(pool_size=2, max_overflow=2, pool_recycle=1800)
    eng = create_engine(_url_for(name, c), **kwargs)

    @event.listens_for(eng, "connect")
    def _on_connect(dbapi_conn, _record):  # dijalankan sekali per koneksi fisik
        if db == "sqlite":
            dbapi_conn.execute("PRAGMA query_only = ON")
            return
        if db == "oracle":
            dbapi_conn.call_timeout = ms
            return
        cur = dbapi_conn.cursor()
        try:
            if db == "postgresql":
                cur.execute("SET SESSION CHARACTERISTICS AS TRANSACTION READ ONLY")
                cur.execute(f"SET statement_timeout = {ms}")
                cur.execute("SET idle_in_transaction_session_timeout = 60000")
            elif db == "mysql":
                cur.execute("SET SESSION TRANSACTION READ ONLY")
                for stmt in (f"SET SESSION max_execution_time = {ms}",        # MySQL
                             f"SET SESSION max_statement_time = {TIMEOUT_S}"):  # MariaDB
                    try:
                        cur.execute(stmt)
                    except Exception:
                        pass
            elif db == "mssql":
                cur.execute(f"SET LOCK_TIMEOUT {ms}")
        finally:
            cur.close()
        dbapi_conn.commit()

    _engines[name] = eng
    return eng


def _begin_readonly(conn: Connection, db: str) -> None:
    """Prelude per transaksi."""
    if db == "oracle":
        conn.exec_driver_sql("SET TRANSACTION READ ONLY")


def _sqlite_deadline(conn: Connection):
    raw = conn.connection.driver_connection
    deadline = time.monotonic() + TIMEOUT_S
    raw.set_progress_handler(lambda: 1 if time.monotonic() > deadline else 0, 10_000)
    return raw


# ---------------------------------------------------------------- utilitas
def _cell(v: Any) -> Any:
    if v is None or isinstance(v, (bool, int, float)):
        return v
    if isinstance(v, decimal.Decimal):
        return str(v)
    if isinstance(v, (dt.date, dt.datetime, dt.time)):
        return v.isoformat()
    if isinstance(v, dt.timedelta):
        return str(v)
    if isinstance(v, uuid.UUID):
        return str(v)
    if isinstance(v, (bytes, bytearray, memoryview)):
        b = bytes(v)
        return f"<binary {len(b)} bytes: {base64.b64encode(b[:48]).decode()}...>"
    if isinstance(v, (dict, list)):
        v = json.dumps(v, default=str, ensure_ascii=False)
    s = str(v)
    return s if len(s) <= MAX_CELL_CHARS else s[:MAX_CELL_CHARS] + f"…<+{len(s) - MAX_CELL_CHARS} chars>"


def _audit(**rec: Any) -> None:
    rec["ts"] = dt.datetime.now().astimezone().isoformat(timespec="seconds")
    try:
        with open(AUDIT_LOG, "a", encoding="utf-8") as f:
            f.write(json.dumps(rec, ensure_ascii=False, default=str) + "\n")
    except OSError:
        pass


def _err(e: Exception) -> str:
    orig = getattr(e, "orig", None)
    msg = str(orig if orig is not None else e).strip()
    return msg.splitlines()[0][:500] if msg else type(e).__name__


def _ident(value: str | None, what: str) -> str | None:
    if value is not None and not _IDENT.match(value):
        raise ValueError(f"Invalid {what} name: {value!r}")
    return value


# ---------------------------------------------------------------- tools
@mcp.tool(annotations=_RO)
def list_connections() -> list[dict]:
    """List the database connections that may be used (no credentials)."""
    return [
        {"name": n, "type": c["type"], "description": c.get("description", "")}
        for n, c in CONNECTIONS.items()
    ]


@mcp.tool(annotations=_RO)
def list_schemas(connection: str) -> list[str]:
    """List the schemas/databases of a connection."""
    return inspect(_engine(connection)).get_schema_names()


@mcp.tool(annotations=_RO)
def list_tables(connection: str, schema: str | None = None) -> dict:
    """List tables and views. If schema is empty, the connection's default schema is used."""
    _ident(schema, "schema")
    insp = inspect(_engine(connection))
    return {
        "schema": schema or insp.default_schema_name,
        "tables": insp.get_table_names(schema=schema),
        "views": insp.get_view_names(schema=schema),
    }


@mcp.tool(annotations=_RO)
def describe_table(connection: str, table: str, schema: str | None = None) -> dict:
    """Table structure: columns, primary key, foreign keys, indexes."""
    _ident(table, "table")
    _ident(schema, "schema")
    insp = inspect(_engine(connection))
    cols = [
        {
            "name": c["name"],
            "type": str(c["type"]),
            "nullable": c.get("nullable"),
            "default": None if c.get("default") is None else str(c.get("default")),
            "comment": c.get("comment"),
        }
        for c in insp.get_columns(table, schema=schema)
    ]
    out: dict[str, Any] = {"table": table, "schema": schema or insp.default_schema_name, "columns": cols}
    for key, fn in (("primary_key", insp.get_pk_constraint),
                    ("foreign_keys", insp.get_foreign_keys),
                    ("indexes", insp.get_indexes)):
        try:
            out[key] = json.loads(json.dumps(fn(table, schema=schema), default=str))
        except Exception as e:
            out[key] = f"not available: {_err(e)}"
    return out


@mcp.tool(annotations=_RO)
def run_query(connection: str, sql: str, max_rows: int | None = None) -> dict:
    """Run ONE SELECT query (read-only). Any other query is rejected.

    Results are limited to max_rows (default & maximum are set in connections.toml).
    'truncated' = true means there are more rows that were not returned;
    narrow the query with WHERE / aggregation if needed.
    """
    db = CONNECTIONS.get(connection, {}).get("type")
    limit = max(1, min(int(max_rows or MAX_ROWS), MAX_ROWS))
    t0 = time.monotonic()
    try:
        if db is None:
            raise ValueError(f"Connection '{connection}' does not exist.")
        safe_sql = validate_select(sql, db)
        with _engine(connection).connect() as conn:
            try:
                _begin_readonly(conn, db)
                raw = _sqlite_deadline(conn) if db == "sqlite" else None
                result = conn.exec_driver_sql(safe_sql)  # tanpa parsing bind-param
                columns = list(result.keys())
                rows = result.fetchmany(limit + 1)
                if raw is not None:
                    raw.set_progress_handler(None, 0)
            finally:
                conn.rollback()  # selalu rollback, apa pun yang terjadi
        truncated = len(rows) > limit
        rows = [[_cell(v) for v in r] for r in rows[:limit]]
        elapsed = round((time.monotonic() - t0) * 1000)
        _audit(tool="run_query", connection=connection, sql=sql, ok=True, rows=len(rows), ms=elapsed)
        return {"columns": columns, "rows": rows, "row_count": len(rows),
                "truncated": truncated, "max_rows": limit, "elapsed_ms": elapsed}
    except UnsafeQuery as e:
        _audit(tool="run_query", connection=connection, sql=sql, ok=False, blocked=True, error=str(e))
        return {"error": f"Rejected: {e}"}
    except Exception as e:
        _audit(tool="run_query", connection=connection, sql=sql, ok=False, error=_err(e))
        return {"error": _err(e)}


@mcp.tool(annotations=_RO)
def explain_query(connection: str, sql: str) -> dict:
    """Show the execution plan (query plan) of a SELECT WITHOUT running it."""
    db = CONNECTIONS.get(connection, {}).get("type")
    try:
        if db is None:
            raise ValueError(f"Connection '{connection}' does not exist.")
        if db == "oracle":
            return {"error": "EXPLAIN PLAN on Oracle writes to PLAN_TABLE, so it is not available in read-only mode."}
        safe_sql = validate_select(sql, db)
        with _engine(connection).connect() as conn:
            try:
                if db == "mssql":
                    conn.exec_driver_sql("SET SHOWPLAN_TEXT ON")
                    try:
                        plan = [list(r) for r in conn.exec_driver_sql(safe_sql).fetchall()]
                    finally:
                        conn.exec_driver_sql("SET SHOWPLAN_TEXT OFF")
                else:
                    prefix = {"postgresql": "EXPLAIN (FORMAT JSON) ",
                              "mysql": "EXPLAIN FORMAT=JSON ",
                              "sqlite": "EXPLAIN QUERY PLAN "}[db]
                    plan = [[_cell(v) for v in r] for r in conn.exec_driver_sql(prefix + safe_sql).fetchall()]
            finally:
                conn.rollback()
        _audit(tool="explain_query", connection=connection, sql=sql, ok=True)
        return {"plan": plan}
    except UnsafeQuery as e:
        _audit(tool="explain_query", connection=connection, sql=sql, ok=False, blocked=True, error=str(e))
        return {"error": f"Rejected: {e}"}
    except Exception as e:
        _audit(tool="explain_query", connection=connection, sql=sql, ok=False, error=_err(e))
        return {"error": _err(e)}


if __name__ == "__main__":
    mcp.run()
