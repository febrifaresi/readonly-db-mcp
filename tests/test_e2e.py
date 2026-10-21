"""End-to-end: SQLite + PostgreSQL lokal, lewat protokol MCP sungguhan (stdio)."""
import asyncio, json, os, sqlite3, sys, tempfile
from pathlib import Path
import pytest
from mcp import ClientSession, StdioServerParameters
from mcp.client.stdio import stdio_client

ROOT = Path(__file__).resolve().parents[1]
PG_URL = os.environ.get("TEST_PG_URL")  # mis. postgresql+psycopg://postgres@/shop?host=/tmp&port=5433

@pytest.fixture(scope="module")
def cfg(tmp_path_factory):
    d = tmp_path_factory.mktemp("cfg")
    db = d / "shop.db"
    con = sqlite3.connect(db)
    con.execute("create table products(id integer primary key, name text, price real)")
    con.executemany("insert into products(name,price) values(?,?)", [(f"p{i}", i * 1.5) for i in range(50)])
    con.commit(); con.close()
    toml = f'[settings]\nmax_rows = 20\ntimeout_seconds = 3\naudit_log = "{(d/"audit.log").as_posix()}"\n'
    toml += f'[connections.lite]\ntype="sqlite"\npath="{db.as_posix()}"\n'
    if PG_URL:
        toml += '[connections.pg]\ntype="postgresql"\nurl_env="TEST_PG_URL"\n'
    (d / "connections.toml").write_text(toml)
    return d

def call(cfg, tool, **args):
    async def go():
        env = {**os.environ, "DBMCP_CONFIG": str(cfg / "connections.toml")}
        params = StdioServerParameters(command=sys.executable, args=[str(ROOT / "server.py")], env=env)
        async with stdio_client(params) as (r, w):
            async with ClientSession(r, w) as s:
                await s.initialize()
                res = await s.call_tool(tool, args)
                return json.loads(res.content[0].text) if len(res.content) == 1 else [json.loads(c.text) for c in res.content]
    return asyncio.run(go())

def test_tools_listed(cfg):
    async def go():
        env = {**os.environ, "DBMCP_CONFIG": str(cfg / "connections.toml")}
        p = StdioServerParameters(command=sys.executable, args=[str(ROOT / "server.py")], env=env)
        async with stdio_client(p) as (r, w):
            async with ClientSession(r, w) as s:
                await s.initialize()
                return sorted(t.name for t in (await s.list_tools()).tools)
    assert asyncio.run(go()) == ["describe_table", "explain_query", "list_connections",
                                 "list_schemas", "list_tables", "run_query"]

def test_sqlite_flow(cfg):
    assert "products" in call(cfg, "list_tables", connection="lite")["tables"]
    d = call(cfg, "describe_table", connection="lite", table="products")
    assert [c["name"] for c in d["columns"]] == ["id", "name", "price"]
    r = call(cfg, "run_query", connection="lite", sql="select * from products order by id")
    assert r["row_count"] == 20 and r["truncated"] is True
    assert "Rejected" in call(cfg, "run_query", connection="lite", sql="delete from products")["error"]
    assert "plan" in call(cfg, "explain_query", connection="lite", sql="select * from products where id=3")
    # timeout: query rekursif berat harus dihentikan
    slow = "with recursive c(x) as (select 1 union all select x+1 from c) select max(x) from c"
    assert "error" in call(cfg, "run_query", connection="lite", sql=slow)
    log = (cfg / "audit.log").read_text().splitlines()
    assert any('"blocked": true' in l for l in log)

@pytest.mark.skipif(not PG_URL, reason="TEST_PG_URL tidak diset")
def test_postgres_flow(cfg):
    r = call(cfg, "run_query", connection="pg", sql="select id, name, joined from customers order by id")
    assert r["row_count"] == 20 and r["truncated"] and r["rows"][0] == [1, "cust1", "2026-01-02"]
    assert "Rejected" in call(cfg, "run_query", connection="pg", sql="select nextval('s1')")["error"]
    assert "error" in call(cfg, "run_query", connection="pg", sql="select pg_sleep(0.1) from customers")
    # timeout di sisi database (statement_timeout = 3 detik)
    heavy = "select count(*) from generate_series(1,1) a, generate_series(1,100000000) b"
    assert "timeout" in call(cfg, "run_query", connection="pg", sql=heavy)["error"].lower()
    assert "plan" in call(cfg, "explain_query", connection="pg", sql="select * from customers where id=1")

@pytest.mark.skipif(not PG_URL, reason="TEST_PG_URL tidak diset")
def test_postgres_session_readonly_even_if_guard_bypassed(cfg, monkeypatch):
    """Lapisan 2: walau validator dilewati, sesi database tetap menolak tulis (user di sini superuser!)."""
    monkeypatch.setenv("DBMCP_CONFIG", str(cfg / "connections.toml"))
    sys.path.insert(0, str(ROOT))
    import importlib, server
    importlib.reload(server)
    with server._engine("pg").connect() as conn:
        with pytest.raises(Exception, match="read-only transaction"):
            conn.exec_driver_sql("delete from customers")
