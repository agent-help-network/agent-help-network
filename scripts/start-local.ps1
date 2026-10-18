param(
    [Parameter(Mandatory = $true)]
    [ValidateSet('Database', 'Api', 'Mcp', 'Monitor', 'Test')]
    [string]$Component,
    [string]$ConfigPath = (Join-Path $env:LOCALAPPDATA 'AgentHelpNetwork\local-config.json')
)

$ErrorActionPreference = 'Stop'
$workspace = Split-Path -Parent $PSScriptRoot
$python = Join-Path $workspace '.venv\Scripts\python.exe'
$localRoot = Split-Path -Parent $ConfigPath

if ($Component -eq 'Database') {
    $pgCtl = Join-Path $localRoot 'postgresql-17\bin\pg_ctl.exe'
    $data = Join-Path $localRoot 'data'
    if (-not (Test-Path -LiteralPath $pgCtl) -or -not (Test-Path -LiteralPath $data)) {
        throw 'PostgreSQL binary or initialized data directory is missing. See README.'
    }
    & $pgCtl start -D $data -l (Join-Path $localRoot 'postgresql.log') -o '-h 127.0.0.1 -p 55432' -w
    exit $LASTEXITCODE
}

if (-not (Test-Path -LiteralPath $ConfigPath)) { throw 'Local config is missing. See README.' }
if (-not (Test-Path -LiteralPath $python)) { throw 'Python virtual environment is missing. See README.' }
$cfg = Get-Content -LiteralPath $ConfigPath -Raw | ConvertFrom-Json
Set-Location -LiteralPath $workspace

switch ($Component) {
    'Api' {
        $env:AHN_DATABASE_URL = $cfg.database_url
        $env:AHN_KEY_PEPPER = $cfg.key_pepper
        $env:AHN_TEST_MODE = '1'
        $env:AHN_COLLECT_LOOKUP_METRICS = '1'
        $env:AHN_COLLECT_REQUEST_METRICS = '1'
        & $python -m uvicorn ahn.main:app --host 127.0.0.1 --port 8875 --no-access-log
    }
    'Mcp' {
        $env:AHN_API_BASE = 'http://127.0.0.1:8875'
        $env:AHN_KEY_PEPPER = $cfg.key_pepper
        $env:AHN_MCP_WRITE_KEY = $cfg.dots_key
        $env:AHN_MCP_PORT = '8876'
        & $python -m ahn.mcp_adapter
    }
    'Monitor' {
        $env:AHN_DATABASE_URL = $cfg.database_url
        $env:AHN_KEY_PEPPER = $cfg.key_pepper
        $env:AHN_TEST_MODE = '1'
        $env:AHN_COLLECT_LOOKUP_METRICS = '1'
        $env:AHN_COLLECT_REQUEST_METRICS = '1'
        & $python -m uvicorn ahn.monitor:app --host 127.0.0.1 --port 8877 --no-access-log
    }
    'Test' {
        $env:AHN_TEST_DATABASE_URL = $cfg.test_database_url
        $env:AHN_DATABASE_URL = $cfg.test_database_url
        $env:AHN_KEY_PEPPER = $cfg.key_pepper
        & $python -m pytest -q
    }
}

exit $LASTEXITCODE
