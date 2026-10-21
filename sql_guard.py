"""Validasi SQL: hanya izinkan SATU statement SELECT murni.

Lapisan ini adalah pengaman tambahan. Pengaman utama tetap:
  1) user database yang hanya punya hak baca (GRANT SELECT), dan
  2) sesi/transaksi read-only di level database (lihat server.py).

Prinsip: fail-closed. Kalau SQL tidak bisa di-parse, ditolak.
"""

from __future__ import annotations

import re

import sqlglot
from sqlglot import exp

# Dialek sqlglot untuk tiap tipe koneksi
DIALECTS = {
    "postgresql": "postgres",
    "mysql": "mysql",
    "mssql": "tsql",
    "sqlite": "sqlite",
    "oracle": "oracle",
}

# Node yang menandakan perubahan data/struktur/sesi -> tolak di mana pun muncul
_FORBIDDEN_NODES = tuple(
    getattr(exp, name)
    for name in (
        "Insert", "Update", "Delete", "Merge", "Create", "Drop", "Alter",
        "TruncateTable", "Into", "Lock", "Set", "Use", "Transaction",
        "Commit", "Rollback", "Pragma", "Copy", "LoadData", "Grant",
        "Revoke", "Command",
    )
    if hasattr(exp, name)
)

# Fungsi yang punya efek samping / bisa dipakai untuk menyerang server
_FORBIDDEN_FUNCS = {
    # PostgreSQL
    "pg_sleep", "pg_sleep_for", "pg_sleep_until", "pg_terminate_backend",
    "pg_cancel_backend", "pg_reload_conf", "pg_rotate_logfile",
    "pg_read_file", "pg_read_binary_file", "pg_ls_dir", "pg_stat_file",
    "set_config", "nextval", "setval", "query_to_xml",
    "query_to_xml_and_xmlschema", "cursor_to_xml", "pg_logical_emit_message",
    # MySQL / MariaDB
    "sleep", "benchmark", "load_file", "get_lock", "release_lock",
    # SQL Server
    "openrowset", "opendatasource", "openquery", "openxml",
}
_FORBIDDEN_FUNC_PREFIXES = ("dblink", "pg_advisory", "pg_try_advisory", "lo_", "xp_", "sp_")
# Paket Oracle yang berbahaya (DBMS_LOCK.SLEEP, UTL_HTTP, UTL_FILE, ...)
_ORACLE_PKG = re.compile(r"\b(dbms|utl)_\w+\s*\.", re.IGNORECASE)
_WAITFOR = re.compile(r"\bwaitfor\b", re.IGNORECASE)


class UnsafeQuery(ValueError):
    pass


def _func_name(node: exp.Expression) -> str:
    if isinstance(node, exp.Anonymous):
        return str(node.this).lower()
    return node.sql_name().lower()


def validate_select(sql: str, db_type: str) -> str:
    """Kembalikan SQL (tanpa ; di akhir) jika aman, atau raise UnsafeQuery."""
    sql = (sql or "").strip().rstrip(";").strip()
    if not sql:
        raise UnsafeQuery("Empty SQL.")
    if _ORACLE_PKG.search(sql) or _WAITFOR.search(sql):
        raise UnsafeQuery("Calls to DBMS_/UTL_ packages or WAITFOR are not allowed.")

    dialect = DIALECTS[db_type]
    try:
        statements = [s for s in sqlglot.parse(sql, read=dialect) if s is not None]
    except Exception as e:  # parse gagal -> tolak (fail-closed)
        raise UnsafeQuery(f"SQL could not be validated (parse error): {e}") from None

    if len(statements) != 1:
        raise UnsafeQuery("Only one statement per call is allowed.")
    tree = statements[0]

    root = tree.this if isinstance(tree, exp.Subquery) else tree
    if not isinstance(root, (exp.Select, exp.SetOperation)):
        raise UnsafeQuery(f"Only SELECT is allowed (detected: {type(root).__name__}).")

    for node in tree.walk():
        if isinstance(node, _FORBIDDEN_NODES):
            raise UnsafeQuery(f"Query part not allowed: {type(node).__name__}.")
        if isinstance(node, exp.Func):
            name = _func_name(node)
            if name in _FORBIDDEN_FUNCS or name.startswith(_FORBIDDEN_FUNC_PREFIXES):
                raise UnsafeQuery(f"Function '{name}' is not allowed.")
    return sql
