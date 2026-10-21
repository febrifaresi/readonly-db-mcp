# readonly-db-mcp

MCP server **read-only** untuk PostgreSQL, MySQL/MariaDB, SQL Server, SQLite, dan Oracle. Kodenya sengaja dibuat kecil, sekitar 400 baris di dua file, supaya bisa dibaca dan diaudit sendiri.

| File | Isi |
|---|---|
| `server.py` | MCP server: koneksi, sesi read-only, 6 tools, audit log |
| `sql_guard.py` | Validasi SQL: hanya **satu** statement `SELECT` |
| `connections.toml` | Daftar koneksi (tanpa password) |
| `.env` | Connection URL + password (jangan di-commit) |
| `tests/` | 121 test (validator + end-to-end lewat protokol MCP) |

## Tools

| Tool | Fungsi |
|---|---|
| `list_connections` | Daftar koneksi yang diizinkan (tanpa kredensial) |
| `list_schemas` | Daftar schema/database |
| `list_tables` | Tabel & view dalam sebuah schema |
| `describe_table` | Kolom, tipe, PK, FK, index |
| `run_query` | Jalankan satu `SELECT`. Hasil dibatasi `max_rows`, ada flag `truncated` |
| `explain_query` | Query plan **tanpa** menjalankan query (tidak tersedia untuk Oracle) |

## Lapisan keamanan

1. **User database read-only (wajib).** Ini satu-satunya pengaman yang benar-benar kuat. Lihat skrip di bawah.
2. **Sesi read-only di level database:**
   - PostgreSQL: `SET SESSION CHARACTERISTICS AS TRANSACTION READ ONLY`
   - MySQL: `SET SESSION TRANSACTION READ ONLY`
   - Oracle: `SET TRANSACTION READ ONLY` per query
   - SQLite: file dibuka `mode=ro` + `PRAGMA query_only`
   - SQL Server **tidak punya** sesi read-only, jadi di SQL Server lapisan 1 mutlak.
3. **Validasi SQL (sqlglot, fail-closed).** Semua yang bukan `SELECT` ditolak, termasuk multi-statement, `SELECT INTO`, `FOR UPDATE`, CTE yang berisi `DELETE`, dan fungsi berbahaya seperti `pg_sleep`, `pg_terminate_backend`, `dblink`, `SLEEP`, `LOAD_FILE`, `xp_cmdshell`, `OPENROWSET`, `WAITFOR`, `DBMS_*` dan `UTL_*`.
4. **Selalu ROLLBACK.** Ada juga timeout query (default 30 detik), batas jumlah baris (default 500), dan teks panjang dipotong.
5. **Audit log.** Semua query, termasuk yang ditolak, dicatat di `audit.log` dalam format JSON Lines.

## Instalasi cepat (Windows)

Double-click **`setup.bat`**. Script ini membuat `.venv`, menginstall dependency, menyiapkan `connections.toml` dan `.env`, lalu mendaftarkan server ke Claude Desktop. Config Claude Desktop yang lama di-backup dulu ke `claude_desktop_config.json.bak-<tanggal>`. Setelah itu edit `connections.toml` dan `.env`, lalu restart Claude Desktop.

## Instalasi manual

Butuh Python 3.11 ke atas.

```bash
cd readonly-db-mcp
python -m venv .venv
# Windows: .venv\Scripts\activate     macOS/Linux: source .venv/bin/activate
pip install -r requirements.txt        # hapus driver yang tidak dipakai bila mau
cp connections.example.toml connections.toml
cp .env.example .env                   # isi URL + password user read-only
```

Format URL koneksi (password dengan karakter `@ : / # %` harus di-URL-encode, misalnya `@` menjadi `%40`):

```
postgresql+psycopg://user:pass@host:5432/dbname
mysql+pymysql://user:pass@host:3306/dbname
mssql+pymssql://user:pass@host:1433/dbname
oracle+oracledb://user:pass@host:1521/?service_name=ORCLPDB1
```

SQLite tidak butuh URL. Cukup isi `path = "..."` di `connections.toml`.

### Daftarkan ke Claude Desktop

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

Untuk macOS/Linux, `command` diisi `/path/readonly-db-mcp/.venv/bin/python`. Setelah itu restart Claude Desktop.

## Membuat user read-only

**PostgreSQL**

```sql
CREATE ROLE claude_ro LOGIN PASSWORD 'ganti-ini';
ALTER ROLE claude_ro SET default_transaction_read_only = on;
GRANT CONNECT ON DATABASE dwh TO claude_ro;
GRANT USAGE ON SCHEMA public TO claude_ro;
GRANT SELECT ON ALL TABLES IN SCHEMA public TO claude_ro;
ALTER DEFAULT PRIVILEGES IN SCHEMA public GRANT SELECT ON TABLES TO claude_ro;
-- PG 14+: alternatif cepat untuk semua tabel -> GRANT pg_read_all_data TO claude_ro;
```

**MySQL / MariaDB**

```sql
CREATE USER 'claude_ro'@'%' IDENTIFIED BY 'ganti-ini';
GRANT SELECT, SHOW VIEW ON app.* TO 'claude_ro'@'%';
```

**SQL Server**

```sql
CREATE LOGIN claude_ro WITH PASSWORD = 'ganti-ini';
USE ERP;
CREATE USER claude_ro FOR LOGIN claude_ro;
ALTER ROLE db_datareader ADD MEMBER claude_ro;
DENY EXECUTE TO claude_ro;
```

**Oracle**

```sql
CREATE USER claude_ro IDENTIFIED BY "ganti-ini";
GRANT CREATE SESSION TO claude_ro;
GRANT SELECT ON skema.nama_tabel TO claude_ro;   -- ulangi per tabel/view yang boleh dibaca
```

Untuk Oracle, tabel milik skema lain dibaca dengan mengisi parameter `schema` di tool.

Tabel sensitif (gaji, password, data pribadi) cukup **tidak di-GRANT**. Dengan begitu Claude memang tidak bisa melihatnya.

## Menjalankan test

```bash
pip install pytest
python -m pytest -q tests/
# + end-to-end PostgreSQL:
TEST_PG_URL="postgresql+psycopg://user@host:5432/db" python -m pytest -q tests/
```

## Status pengujian

- **SQLite & PostgreSQL 16** sudah diuji end-to-end lewat protokol MCP: query, batas baris, timeout, explain, penolakan query, dan audit log. Ada juga test bahwa sesi PostgreSQL tetap menolak `DELETE` walaupun validator dilewati dan user-nya superuser.
- **MySQL, SQL Server, Oracle:** validator SQL-nya sudah diuji, tapi belum dicoba ke server sungguhan. Coba dulu di database non-produksi.
- Kompatibel dengan MCP Python SDK 1.x dan 2.x.

## Lisensi

MIT — lihat [LICENSE](LICENSE).
