# Run every local quality gate inside the containers: format check, lint, strict
# type check, unit + meta tests, the frontend's checks, then integration tests
# against the real postgres and redis. Stops at the first failure.
#
#   .\scripts\check.ps1          # everything
#   .\scripts\check.ps1 -Fast    # skip integration tests
#   .\scripts\check.ps1 -Gpu     # also run the GPU-marked tests in the worker-gpu image
#   .\scripts\check.ps1 -Bench   # also run the timing benchmarks (NFR-11); needs a quiet machine

param([switch]$Fast, [switch]$Gpu, [switch]$Bench)

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
# Tests that need scikit-learn, skops, Optuna or sacreBLEU (M10, marker ml) run in the CPU
# worker's image, against the current source and tests; in the dev image they are skipped. The
# files are named because worker-cpu has no httpx, which the API's unit tests import.
Step "ml"           { docker compose run --rm --no-deps -v ./src:/app/src:ro -v ./tests:/app/tests:ro worker-cpu sh -c "pytest -q -m 'ml and not integration' tests/unit/test_ML_*.py tests/unit/test_EVAL_1_2_*.py" }
# Frontend (SPEC §7.5): typecheck, lint with the raw-hex rule, Vitest, production build.
Step "frontend"     { docker compose run --rm --no-deps frontend-tools sh -c "npm run typecheck && npm run lint && npm test && npm run build" }
if (-not $Fast) {
    Step "integration" { docker compose run --rm dev pytest -q -m "integration and not benchmark" }
    # Integration tests that run classical models (M10, marker ml) need scikit-learn: worker-cpu.
    Step "integration (ml)" { docker compose run --rm -v ./src:/app/src:ro -v ./tests:/app/tests:ro -v ./migrations:/app/migrations:ro worker-cpu pytest -q -m "ml and integration" tests/integration }
    # Tests that need PyTorch (training, M8; the builder, M9) run in the GPU worker's image,
    # against the current source. In the dev image they are skipped.
    Step "torch" { docker compose run --rm -v ./src:/app/src:ro -v ./migrations:/app/migrations:ro worker-gpu pytest -q -m torch tests/integration tests/unit }
}
if ($Gpu) {
    Step "gpu" { docker compose run --rm --no-deps worker-gpu pytest -q -m gpu tests/gpu }
}
if ($Bench) {
    Step "benchmark" { docker compose run --rm dev pytest -q -s -m benchmark }
}
Write-Host "All checks passed." -ForegroundColor Green
