# readonly-db-mcp

A **read-only** MCP server for PostgreSQL, MySQL/MariaDB, SQL Server, SQLite, and Oracle. The code is intentionally small, about 400 lines across two files, so you can read and audit it yourself.

| File | Contents |
|---|---|
| `server.py` | MCP server: connections, read-only sessions, 6 tools, audit log |
| `sql_guard.py` | SQL validation: only **one** `SELECT` statement allowed |
| `connections.toml` | Connection list (no passwords) |
| `.env` | Connection URLs + passwords (never commit this) |
| `tests/` | 121 tests (validator + end-to-end over the MCP protocol) |

## Tools

| Tool | Purpose |
|---|---|
| `list_connections` | List allowed connections (no credentials) |
| `list_schemas` | List schemas/databases |
| `list_tables` | Tables & views in a schema |
| `describe_table` | Columns, types, PK, FK, indexes |
| `run_query` | Run a single `SELECT`. Results are capped at `max_rows`, with a `truncated` flag |
| `explain_query` | Query plan **without** running the query (not available for Oracle) |

## Security layers

1. **A read-only database user (required).** This is the only truly strong safeguard. See the scripts below.
2. **Read-only session at the database level:**
   - PostgreSQL: `SET SESSION CHARACTERISTICS AS TRANSACTION READ ONLY`
   - MySQL: `SET SESSION TRANSACTION READ ONLY`
   - Oracle: `SET TRANSACTION READ ONLY` per query
   - SQLite: file opened with `mode=ro` + `PRAGMA query_only`
   - SQL Server has **no** read-only session, so on SQL Server layer 1 is essential.
3. **SQL validation (sqlglot, fail-closed).** Anything that is not a `SELECT` is rejected, including multiple statements, `SELECT INTO`, `FOR UPDATE`, CTEs containing `DELETE`, and dangerous functions such as `pg_sleep`, `pg_terminate_backend`, `dblink`, `SLEEP`, `LOAD_FILE`, `xp_cmdshell`, `OPENROWSET`, `WAITFOR`, `DBMS_*` and `UTL_*`.
4. **Always ROLLBACK.** There is also a query timeout (30 seconds by default), a row limit (500 by default), and long text values are truncated.
5. **Audit log.** Every query, including rejected ones, is logged to `audit.log` in JSON Lines format.

> Note: tool descriptions and error messages from the server are currently in Indonesian.

## Quick install (Windows)

Double-click **`setup.bat`**. It creates `.venv`, installs dependencies, prepares `connections.toml` and `.env`, and registers the server with Claude Desktop. Your existing Claude Desktop config is backed up first to `claude_desktop_config.json.bak-<date>`. Then edit `connections.toml` and `.env`, and restart Claude Desktop.

## Manual install

Requires Python 3.11 or newer.

```bash
cd readonly-db-mcp
python -m venv .venv
# Windows: .venv\Scripts\activate     macOS/Linux: source .venv/bin/activate
pip install -r requirements.txt        # remove drivers you don't need if you like
cp connections.example.toml connections.toml
cp .env.example .env                   # fill in the URL + password of the read-only user
```

Connection URL format (passwords containing `@ : / # %` must be URL-encoded, e.g. `@` becomes `%40`):

```
postgresql+psycopg://user:pass@host:5432/dbname
mysql+pymysql://user:pass@host:3306/dbname
mssql+pymssql://user:pass@host:1433/dbname
oracle+oracledb://user:pass@host:1521/?service_name=ORCLPDB1
```

SQLite needs no URL. Just set `path = "..."` in `connections.toml` (on Windows, use forward slashes `/`).

### Register with Claude Desktop

Edit `claude_desktop_config.json`:

- Windows: `%APPDATA%\Claude\claude_desktop_config.json`
- macOS: `~/Library/Application Support/Claude/claude_desktop_config.json`

```json
{
  "mcpServers": {
    "readonly-db": {
      "command": "C:\\path\\readonly-db-mcp\\.venv\\Scripts\\python.exe",
      "args": ["C:\\path\\readonly-db-mcp\\server.py"]
    }
  }
}
```

On macOS/Linux, set `command` to `/path/readonly-db-mcp/.venv/bin/python`. Then restart Claude Desktop.

## Creating a read-only user

**PostgreSQL**

```sql
CREATE ROLE claude_ro LOGIN PASSWORD 'change-me';
ALTER ROLE claude_ro SET default_transaction_read_only = on;
GRANT CONNECT ON DATABASE dwh TO claude_ro;
GRANT USAGE ON SCHEMA public TO claude_ro;
GRANT SELECT ON ALL TABLES IN SCHEMA public TO claude_ro;
ALTER DEFAULT PRIVILEGES IN SCHEMA public GRANT SELECT ON TABLES TO claude_ro;
-- PG 14+: quick alternative for all tables -> GRANT pg_read_all_data TO claude_ro;
```

**MySQL / MariaDB**

```sql
CREATE USER 'claude_ro'@'%' IDENTIFIED BY 'change-me';
GRANT SELECT, SHOW VIEW ON app.* TO 'claude_ro'@'%';
```

**SQL Server**

```sql
CREATE LOGIN claude_ro WITH PASSWORD = 'change-me';
USE ERP;
CREATE USER claude_ro FOR LOGIN claude_ro;
ALTER ROLE db_datareader ADD MEMBER claude_ro;
DENY EXECUTE TO claude_ro;
```

**Oracle**

```sql
CREATE USER claude_ro IDENTIFIED BY "change-me";
GRANT CREATE SESSION TO claude_ro;
GRANT SELECT ON schema_name.table_name TO claude_ro;   -- repeat for each table/view that may be read
```

On Oracle, tables owned by another schema are read by setting the `schema` parameter of the tool.

For sensitive tables (salaries, passwords, personal data), simply **don't GRANT** access. That way Claude genuinely cannot see them.

## Running the tests

```bash
pip install pytest
python -m pytest -q tests/
# + end-to-end PostgreSQL:
TEST_PG_URL="postgresql+psycopg://user@host:5432/db" python -m pytest -q tests/
```

## Test status

- **SQLite & PostgreSQL 16** have been tested end-to-end over the MCP protocol: queries, row limits, timeouts, explain, query rejection, and the audit log. There is also a test showing that a PostgreSQL session still rejects `DELETE` even when the validator is bypassed and the user is a superuser.
- **MySQL, SQL Server, Oracle:** the SQL validator is tested, but they have not yet been tried against a real server. Try them on a non-production database first.
- Compatible with MCP Python SDK 1.x and 2.x.

## License

MIT — see [LICENSE](LICENSE).
