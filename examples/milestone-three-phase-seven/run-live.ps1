$ErrorActionPreference = "Stop"
$repositoryRoot = Resolve-Path "$PSScriptRoot/../.."

if (-not $env:RUNSIGIL_API_URL) {
    $env:RUNSIGIL_API_URL = "http://localhost:8000"
}
if (-not $env:RUNSIGIL_API_KEY) {
    $environmentFile = Join-Path $repositoryRoot ".env"
    if (-not (Test-Path -LiteralPath $environmentFile)) {
        throw "Set RUNSIGIL_API_KEY or create the repository-root .env file."
    }
    $keyLine = Get-Content -LiteralPath $environmentFile | Where-Object {
        $_ -match '^RUNSIGIL_BOOTSTRAP_API_KEY='
    } | Select-Object -First 1
    if (-not $keyLine) {
        throw "RUNSIGIL_BOOTSTRAP_API_KEY is missing from .env."
    }
    $env:RUNSIGIL_API_KEY = ($keyLine -split '=', 2)[1].Trim()
}

$virtualEnvironmentPython = Join-Path $repositoryRoot ".venv/Scripts/python.exe"
$python = if (Test-Path -LiteralPath $virtualEnvironmentPython) {
    $virtualEnvironmentPython
} else {
    "python"
}

Push-Location $repositoryRoot
try {
    & $python "examples/milestone-three-phase-seven/live.py"
    if ($LASTEXITCODE -ne 0) {
        throw "The RunSigil Milestone 3 phase seven live proof failed."
    }
} finally {
    Pop-Location
}
