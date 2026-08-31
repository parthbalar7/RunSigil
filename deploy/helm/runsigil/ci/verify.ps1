$ErrorActionPreference = "Stop"
$chart = Resolve-Path "$PSScriptRoot/.."
helm lint $chart -f "$PSScriptRoot/values.yaml"
$rendered = helm template runsigil $chart -f "$PSScriptRoot/values.yaml" --namespace runsigil-system | Out-String
if ($rendered -notmatch "runAsNonRoot: true") { throw "non-root policy missing" }
if ($rendered -notmatch "readOnlyRootFilesystem: true") { throw "read-only root policy missing" }
if ($rendered -notmatch "kind: NetworkPolicy") { throw "NetworkPolicy missing" }
if ($rendered -notmatch "runsigil-migration") { throw "migration Job missing" }
Write-Output "RunSigil Helm verification passed"
