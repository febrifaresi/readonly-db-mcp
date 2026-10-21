import pytest
from sql_guard import UnsafeQuery, validate_select

ALL = ["postgresql", "mysql", "mssql", "sqlite", "oracle"]

OK = [
    "SELECT 1",
    "select id, name from customers where id = 1;",
    "WITH x AS (SELECT 1 AS a) SELECT a FROM x",
    "SELECT a FROM t UNION ALL SELECT b FROM u",
    "SELECT count(*), max(x) FROM t GROUP BY y HAVING count(*) > 1",
    "SELECT '10:30' AS jam, 'DROP TABLE x' AS teks",
]
BAD = [
    "DELETE FROM customers",
    "UPDATE customers SET name='x'",
    "INSERT INTO t VALUES (1)",
    "DROP TABLE customers",
    "TRUNCATE TABLE customers",
    "CREATE TABLE z (a int)",
    "ALTER TABLE t ADD c int",
    "GRANT SELECT ON t TO u",
    "SELECT 1; DROP TABLE t",
    "SELECT 1; SELECT 2",
    "SELECT * INTO backup FROM customers",
    "SELECT * FROM t FOR UPDATE",
    "",
]
PG_BAD = [
    "WITH d AS (DELETE FROM t RETURNING *) SELECT * FROM d",
    "SELECT pg_sleep(100)",
    "SELECT pg_terminate_backend(123)",
    "SELECT set_config('default_transaction_read_only','off',false)",
    "SELECT nextval('s1')",
    "SELECT * FROM dblink('host=x','select 1') AS t(a int)",
    "SELECT query_to_xml('delete from t', true, true, '')",
    "SET default_transaction_read_only = off",
    "COPY t TO '/tmp/x'",
    "EXPLAIN ANALYZE DELETE FROM t",
]

@pytest.mark.parametrize("db", ALL)
@pytest.mark.parametrize("sql", OK)
def test_ok(db, sql):
    validate_select(sql, db)

@pytest.mark.parametrize("db", ALL)
@pytest.mark.parametrize("sql", BAD)
def test_bad(db, sql):
    with pytest.raises(UnsafeQuery):
        validate_select(sql, db)

@pytest.mark.parametrize("sql", PG_BAD)
def test_pg_bad(sql):
    with pytest.raises(UnsafeQuery):
        validate_select(sql, "postgresql")

@pytest.mark.parametrize("sql", [
    "SELECT SLEEP(10)", "SELECT BENCHMARK(1000000, MD5('a'))",
    "SELECT LOAD_FILE('/etc/passwd')", "SELECT * FROM t INTO OUTFILE '/tmp/x'",
])
def test_mysql_bad(sql):
    with pytest.raises(UnsafeQuery):
        validate_select(sql, "mysql")

@pytest.mark.parametrize("sql", [
    "EXEC xp_cmdshell 'dir'", "WAITFOR DELAY '00:00:10'",
    "SELECT * FROM OPENROWSET('SQLNCLI','x','select 1')",
    "SELECT 1; WAITFOR DELAY '00:01'",
])
def test_mssql_bad(sql):
    with pytest.raises(UnsafeQuery):
        validate_select(sql, "mssql")

@pytest.mark.parametrize("sql", [
    "SELECT DBMS_LOCK.SLEEP(10) FROM dual",
    "SELECT UTL_HTTP.REQUEST('http://evil') FROM dual",
    "BEGIN EXECUTE IMMEDIATE 'drop table t'; END;",
])
def test_oracle_bad(sql):
    with pytest.raises(UnsafeQuery):
        validate_select(sql, "oracle")

def test_sqlite_pragma():
    with pytest.raises(UnsafeQuery):
        validate_select("PRAGMA writable_schema = ON", "sqlite")
    with pytest.raises(UnsafeQuery):
        validate_select("ATTACH DATABASE 'x.db' AS x", "sqlite")
