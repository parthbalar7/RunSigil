param(
    [switch]$IncludeIntegration
)

$ErrorActionPreference = "Stop"
$repositoryRoot = Resolve-Path "$PSScriptRoot/.."
$virtualEnvironmentPython = Join-Path $repositoryRoot ".venv/Scripts/python.exe"
$python = if (Test-Path -LiteralPath $virtualEnvironmentPython) {
    $virtualEnvironmentPython
} else {
    "python"
}

function Invoke-Checked {
    param([scriptblock]$Command, [string]$Name)
    Write-Output "==> $Name"
    & $Command
    if ($LASTEXITCODE -ne 0) { throw "$Name failed with exit code $LASTEXITCODE" }
}

Push-Location $repositoryRoot
try {
    Invoke-Checked { & $python -m ruff check apps packages adapters tests examples } "Ruff lint"
    Invoke-Checked { & $python -m ruff format --check apps packages adapters tests examples } "Ruff format"
    Invoke-Checked { & $python -m mypy } "Mypy strict type checking"
    Invoke-Checked {
        & $python -m pytest tests/unit tests/security/test_deployment_static.py -q -p no:cacheprovider
    } "Python unit and deployment checks"
    Invoke-Checked { npm --prefix apps/web ci } "Locked web install"
    Invoke-Checked { npm --prefix apps/web test } "Web component tests"
    Invoke-Checked { npm --prefix apps/web run build } "Web production build"
    Invoke-Checked { npm --prefix apps/web audit --audit-level=high } "Web dependency audit"

    if ($IncludeIntegration) {
        $required = @(
            "RUNSIGIL_TEST_DATABASE_URL",
            "RUNSIGIL_TEST_WORKER_DATABASE_URL",
            "RUNSIGIL_TEST_GATEWAY_AUTHORIZATION_DATABASE_URL",
            "RUNSIGIL_TEST_OWNER_DATABASE_URL",
            "RUNSIGIL_BOOTSTRAP_API_KEY"
        )
        $missing = $required | Where-Object { -not [Environment]::GetEnvironmentVariable($_) }
        if ($missing) {
            throw "Integration variables are missing: $($missing -join ', ')"
        }
        Invoke-Checked {
            & $python -m pytest tests/integration tests/security -q -p no:cacheprovider
        } "PostgreSQL integration and security proofs"
    }
    Write-Output "RunSigil verification passed."
} finally {
    Pop-Location
}
