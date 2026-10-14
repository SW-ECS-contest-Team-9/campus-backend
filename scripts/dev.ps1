# 개발 명령 모음 (Windows PowerShell). 사용: .\scripts\dev.ps1 <명령> [인자...]
param(
    [Parameter(Position = 0)][string]$Task = "help",
    [Parameter(ValueFromRemainingArguments = $true)][string[]]$Rest
)
$ErrorActionPreference = "Stop"
Set-Location (Split-Path $PSScriptRoot -Parent)

function Invoke-Step([string]$Exe, [string[]]$ArgList) {
    & $Exe @ArgList
    if ($LASTEXITCODE -ne 0) { exit $LASTEXITCODE }
}

switch ($Task) {
    "setup" {
        Invoke-Step uv @("sync")
        if (-not (Test-Path .env)) { Copy-Item .env.example .env; Write-Host ".env 를 만들었습니다. 값을 확인하세요." }
        Invoke-Step uv @("run", "pre-commit", "install")
    }
    "db-up" { Invoke-Step docker @("compose", "up", "-d", "--wait", "postgres", "redis") }
    "migrate" { Invoke-Step uv @("run", "alembic", "upgrade", "head") }
    "import-release" { Invoke-Step uv (@("run", "python", "scripts/import_release.py") + $Rest) }
    "run" {
        $port = if ($env:PORT) { $env:PORT } else { "8000" }
        Invoke-Step uv @("run", "uvicorn", "main:app", "--reload", "--port", $port)
    }
    "test" { Invoke-Step uv (@("run", "pytest") + $Rest) }
    "lint" {
        Invoke-Step uv @("run", "ruff", "check", ".")
        Invoke-Step uv @("run", "ruff", "format", "--check", ".")
    }
    "format" {
        Invoke-Step uv @("run", "ruff", "check", "--fix", ".")
        Invoke-Step uv @("run", "ruff", "format", ".")
    }
    default { Write-Host "명령: setup | db-up | migrate | import-release [인자] | run | test [인자] | lint | format" }
}
