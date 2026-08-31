$ErrorActionPreference = "Stop"
$cluster = "runsigil-dev"
$existing = @(kind get clusters)
if ($existing -notcontains $cluster) {
    Write-Output "$cluster does not exist"
    exit 0
}
kind delete cluster --name $cluster

