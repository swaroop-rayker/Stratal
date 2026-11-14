# Run every local quality gate inside the dev container: format check, lint,
# strict type check, unit + meta tests, then integration tests against the real
# postgres and redis. Stops at the first failure.
#
#   .\scripts\check.ps1          # everything
#   .\scripts\check.ps1 -Fast    # skip integration tests

param([switch]$Fast)

$ErrorActionPreference = "Stop"
Set-Location (Split-Path $PSScriptRoot -Parent)

function Step($name, [scriptblock]$command) {
    Write-Host "== $name" -ForegroundColor Cyan
    & $command
    if ($LASTEXITCODE -ne 0) { Write-Host "FAILED: $name" -ForegroundColor Red; exit 1 }
}

Step "format check" { docker compose run --rm --no-deps dev ruff format --check . }
Step "lint"         { docker compose run --rm --no-deps dev ruff check . }
Step "type check"   { docker compose run --rm --no-deps dev pyright }
Step "unit + meta"  { docker compose run --rm --no-deps dev pytest -q }
if (-not $Fast) {
    Step "integration" { docker compose run --rm dev pytest -q -m integration }
}
Write-Host "All checks passed." -ForegroundColor Green
