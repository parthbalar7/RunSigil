$ErrorActionPreference = "Stop"
$cluster = "runsigil-dev"
$existing = @(kind get clusters)
if ($existing -notcontains $cluster) {
    Write-Output "$cluster is not running"
    exit 1
}
kubectl --context "kind-$cluster" get nodes

