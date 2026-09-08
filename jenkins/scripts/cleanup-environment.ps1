param(
    [string]$ProjectRoot = ""
)

$ErrorActionPreference = "Continue"

function Write-Section {
    param([string]$Title)

    Write-Host ""
    Write-Host "============================================================"
    Write-Host " $Title"
    Write-Host "============================================================"
}

# ============================================================
# RESOLVE PROJECT ROOT
# ============================================================

if ([string]::IsNullOrWhiteSpace($ProjectRoot)) {
    $ScriptDir = Split-Path -Parent $MyInvocation.MyCommand.Path
    $ProjectRoot = (Resolve-Path (Join-Path $ScriptDir "..\..")).Path
}
else {
    $ProjectRoot = (Resolve-Path $ProjectRoot).Path
}

$ConfigPath = Join-Path `
    $ProjectRoot `
    "jenkins\config\pipeline-config.json"

if (-not (Test-Path $ConfigPath)) {
    Write-Host "[WARN] pipeline-config.json not found."
    exit 0
}

$Config = Get-Content `
    $ConfigPath `
    -Raw |
    ConvertFrom-Json

$ClusterName = [string]$Config.project.cluster_name

$StopTrafficScript = Join-Path `
    $ProjectRoot `
    "jenkins\scripts\stop-traffic.ps1"

$StopPortForwardScript = Join-Path `
    $ProjectRoot `
    "jenkins\scripts\stop-port-forwards.ps1"

Write-Section "CLEANUP ENVIRONMENT"

Write-Host "Cluster : $ClusterName"
Write-Host ""
Write-Host "Cleanup policy:"
Write-Host "  Kind cluster       : DELETE"
Write-Host "  Docker images      : PRESERVE"
Write-Host "  Ollama models      : PRESERVE"
Write-Host "  Runtime evidence   : PRESERVE"
Write-Host ""

# ============================================================
# STOP TRAFFIC
# ============================================================

Write-Section "STOP APPLICATION TRAFFIC"

if (Test-Path $StopTrafficScript) {

    try {
        & $StopTrafficScript `
            -ProjectRoot $ProjectRoot

        Write-Host "[PASS] Traffic cleanup completed."
    }
    catch {
        Write-Host (
            "[WARN] Traffic cleanup returned an error: " +
            $_.Exception.Message
        )
    }
}
else {
    Write-Host "[WARN] stop-traffic.ps1 not found."
}

# ============================================================
# STOP PORT FORWARDS
# ============================================================

Write-Section "STOP MONITORING CONNECTIONS"

if (Test-Path $StopPortForwardScript) {

    try {
        & $StopPortForwardScript `
            -ProjectRoot $ProjectRoot

        Write-Host "[PASS] Port-forward cleanup completed."
    }
    catch {
        Write-Host (
            "[WARN] Port-forward cleanup returned an error: " +
            $_.Exception.Message
        )
    }
}
else {
    Write-Host "[WARN] stop-port-forwards.ps1 not found."
}

# ============================================================
# DELETE KIND CLUSTER
# ============================================================

Write-Section "REMOVE EPHEMERAL KIND CLUSTER"

$Clusters = @()

try {
    $Clusters = @(
        kind get clusters 2>$null
    )
}
catch {
    $Clusters = @()
}

if ($Clusters -contains $ClusterName) {

    Write-Host "Deleting Kind cluster '$ClusterName'..."

    kind delete cluster `
        --name $ClusterName

    if ($LASTEXITCODE -ne 0) {
        Write-Host (
            "[WARN] Kind cluster deletion returned " +
            "exit code $LASTEXITCODE."
        )
    }
}
else {
    Write-Host (
        "[INFO] Kind cluster '$ClusterName' does not exist. " +
        "Nothing to delete."
    )
}

# ============================================================
# VERIFY
# ============================================================

$RemainingClusters = @()

try {
    $RemainingClusters = @(
        kind get clusters 2>$null
    )
}
catch {
    $RemainingClusters = @()
}

if ($RemainingClusters -contains $ClusterName) {

    Write-Host ""
    Write-Host (
        "[WARN] Cluster '$ClusterName' is still present after cleanup."
    )

    exit 1
}

Write-Host ""
Write-Host "[PASS] Kind cluster is absent."
Write-Host "[PASS] Docker images were preserved."
Write-Host "[PASS] Ollama models were preserved."
Write-Host "[PASS] Runtime/report evidence was preserved."

Write-Section "CLEANUP COMPLETE"

exit 0
