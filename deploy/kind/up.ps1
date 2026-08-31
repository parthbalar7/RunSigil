$ErrorActionPreference = "Stop"
$cluster = "runsigil-dev"
$existing = @(kind get clusters)
if ($existing -contains $cluster) {
    Write-Output "$cluster already exists"
    exit 0
}
kind create cluster --name $cluster --config "$PSScriptRoot/cluster.yaml"

