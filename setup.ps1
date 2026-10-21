# Setup readonly-db-mcp di Windows:
#  1) buat virtualenv + install dependency
#  2) siapkan connections.toml dan .env (jika belum ada)
#  3) daftarkan ke Claude Desktop (config lama di-backup dulu)
$ErrorActionPreference = "Stop"
$here = Split-Path -Parent $MyInvocation.MyCommand.Path
Set-Location $here

# --- 1. Python & venv ---
$py = $null
$check = "import sys;print(sys.version_info[0]*100+sys.version_info[1])"
foreach ($cand in @(@("py","-3"), @("python"))) {
    if (-not (Get-Command $cand[0] -ErrorAction SilentlyContinue)) { continue }
    $extra = @($cand | Select-Object -Skip 1)
    try { $v = & $cand[0] @extra -c $check 2>$null } catch { continue }
    if ($LASTEXITCODE -eq 0 -and $v -and [int]$v -ge 311) { $py = $cand; break }
}
if (-not $py) { Write-Host "Python 3.11+ tidak ditemukan. Install dari https://www.python.org/downloads/ (centang 'Add to PATH')." -ForegroundColor Red; exit 1 }
Write-Host ("Python: " + ($py -join " ")) -ForegroundColor Cyan

if (-not (Test-Path ".venv\Scripts\python.exe")) {
    $extra = @($py | Select-Object -Skip 1)
    & $py[0] @extra -m venv .venv
    if ($LASTEXITCODE) { Write-Host "Gagal membuat venv." -ForegroundColor Red; exit 1 }
}
$venvPy = Join-Path $here ".venv\Scripts\python.exe"
& $venvPy -m pip install --upgrade pip -q
& $venvPy -m pip install -r requirements.txt -q
if ($LASTEXITCODE) { Write-Host "Install dependency gagal." -ForegroundColor Red; exit 1 }
Write-Host "Dependency terinstall." -ForegroundColor Green

# --- 2. File konfigurasi ---
if (-not (Test-Path "connections.toml")) { Copy-Item connections.example.toml connections.toml; Write-Host "Dibuat: connections.toml (silakan edit)" }
if (-not (Test-Path ".env"))             { Copy-Item .env.example .env;                         Write-Host "Dibuat: .env (isi URL + password user read-only)" }

# --- 3. Daftarkan ke Claude Desktop ---
$cfgDirs = @(Join-Path $env:APPDATA "Claude")
Get-ChildItem (Join-Path $env:LOCALAPPDATA "Packages") -Directory -Filter "Claude_*" -ErrorAction SilentlyContinue |
    ForEach-Object { $cfgDirs += (Join-Path $_.FullName "LocalCache\Roaming\Claude") }
$cfgDirs = $cfgDirs | Where-Object { Test-Path $_ }
if (-not $cfgDirs) { $cfgDirs = @(Join-Path $env:APPDATA "Claude"); New-Item -ItemType Directory -Force $cfgDirs[0] | Out-Null }

foreach ($dir in $cfgDirs) {
    $cfgFile = Join-Path $dir "claude_desktop_config.json"
    if (Test-Path $cfgFile) {
        $stamp = Get-Date -Format "yyyyMMdd-HHmmss"
        Copy-Item $cfgFile "$cfgFile.bak-$stamp"
        $raw = Get-Content $cfgFile -Raw
        $cfg = if ($raw.Trim()) { $raw | ConvertFrom-Json } else { [pscustomobject]@{} }
    } else { $cfg = [pscustomobject]@{} }
    if (-not $cfg.PSObject.Properties["mcpServers"]) { $cfg | Add-Member -NotePropertyName mcpServers -NotePropertyValue ([pscustomobject]@{}) }
    $entry = [pscustomobject]@{ command = $venvPy; args = @((Join-Path $here "server.py")) }
    if ($cfg.mcpServers.PSObject.Properties["readonly-db"]) { $cfg.mcpServers."readonly-db" = $entry }
    else { $cfg.mcpServers | Add-Member -NotePropertyName "readonly-db" -NotePropertyValue $entry }
    [IO.File]::WriteAllText($cfgFile, ($cfg | ConvertTo-Json -Depth 20), (New-Object Text.UTF8Encoding $false))
    Write-Host "Terdaftar di: $cfgFile" -ForegroundColor Green
}

Write-Host ""
Write-Host "Selesai. Langkah berikutnya:" -ForegroundColor Cyan
Write-Host "  1. Edit connections.toml dan .env di folder ini"
Write-Host "  2. Tutup Claude Desktop sepenuhnya (termasuk dari system tray), lalu buka lagi"
