# Starter SahamBuddy untuk Windows (PowerShell).
# Pakai: .\run.ps1
#
# Padanan run.sh: memuat .env, memakai .venv kalau ada, lalu menjalankan bot
# di foreground (log ke layar). Hentikan dengan Ctrl+C.
$ErrorActionPreference = 'Stop'
Set-Location $PSScriptRoot

# muat .env (format KEY=VALUE) ke environment proses ini
if (Test-Path .env) {
    Get-Content .env | ForEach-Object {
        $line = $_.Trim()
        if ($line -and -not $line.StartsWith('#') -and $line.Contains('=')) {
            $k, $v = $line -split '=', 2
            [Environment]::SetEnvironmentVariable($k.Trim(), $v.Trim(), 'Process')
        }
    }
}

$py = Join-Path $PSScriptRoot '.venv\Scripts\python.exe'
if (-not (Test-Path $py)) { $py = 'python' }

if (-not $env:BOT_TOKEN -or $env:BOT_TOKEN -like 'tempel*') {
    Write-Host "BOT_TOKEN belum di-set. Salin .env.example -> .env lalu isi token dari @BotFather." -ForegroundColor Red
    exit 1
}

# fix NO_PROXY berisi IPv6 berkurung yang membuat httpx bengkak (sama seperti run.sh)
$env:NO_PROXY = 'localhost,127.0.0.1,::1'
$env:no_proxy = $env:NO_PROXY

& $py bot.py
