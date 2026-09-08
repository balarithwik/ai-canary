param(
    [string]$ProjectRoot = "",
    [switch]$RecreateExisting
)

$ErrorActionPreference = "Stop"

function Write-Section {
    param([string]$Title)

    Write-Host ""
    Write-Host "============================================================"
    Write-Host " $Title"
    Write-Host "============================================================"
}

function Fail {
    param([string]$Message)

    Write-Host ""
    Write-Host "[FAIL] $Message"
    exit 1
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

$ConfigPath = Join-Path $ProjectRoot "jenkins\config\pipeline-config.json"
$KindConfigPath = Join-Path $ProjectRoot "ai-canary-config.yaml"

if (-not (Test-Path $ConfigPath)) {
    Fail "pipeline-config.json not found: $ConfigPath"
}

if (-not (Test-Path $KindConfigPath)) {
    Fail "Kind config not found: $KindConfigPath"
}

$Config = Get-Content $ConfigPath -Raw | ConvertFrom-Json

$ClusterName = [string]$Config.project.cluster_name
$ExpectedContext = "kind-$ClusterName"

Write-Section "CREATE EPHEMERAL KIND CLUSTER"

Write-Host "Project Root : $ProjectRoot"
Write-Host "Cluster Name : $ClusterName"
Write-Host "Kind Config  : $KindConfigPath"
Write-Host ""

# ============================================================
# CHECK EXISTING CLUSTER
#
# Windows PowerShell 5.x converts native stderr into a
# NativeCommandError when ErrorActionPreference = Stop.
#
# kind prints "No kind clusters found." to stderr, so invoke
# this one query through cmd.exe and inspect the exit code/output
# ourselves. No cluster is a NORMAL state for this demo.
# ============================================================

$kindOutput = & cmd.exe /d /c "kind get clusters 2>&1"
$kindExitCode = $LASTEXITCODE

$kindText = (
    $kindOutput |
    ForEach-Object { $_.ToString().Trim() } |
    Where-Object { -not [string]::IsNullOrWhiteSpace($_) }
)

$ExistingClusters = @()

if ($kindExitCode -eq 0) {

    $ExistingClusters = @($kindText)

    if ($ExistingClusters.Count -gt 0) {
        Write-Host "[INFO] Existing Kind cluster(s): $($ExistingClusters -join ', ')"
    }
}
elseif (($kindText -join " ") -match "No kind clusters found") {

    Write-Host "[INFO] No existing Kind clusters found."
    $ExistingClusters = @()
}
else {

    Fail (
        "Unable to query existing Kind clusters. " +
        "Exit code: $kindExitCode. Output: " +
        ($kindText -join " ")
    )
}

# ============================================================
# HANDLE EXISTING TARGET CLUSTER
# ============================================================

if ($ExistingClusters -contains $ClusterName) {

    if ($RecreateExisting) {

        Write-Host "[INFO] Existing cluster '$ClusterName' detected."
        Write-Host "[INFO] RecreateExisting enabled - deleting old cluster."

        kind delete cluster --name $ClusterName

        if ($LASTEXITCODE -ne 0) {
            Fail "Unable to delete existing Kind cluster '$ClusterName'."
        }
    }
    else {

        Fail (
            "Kind cluster '$ClusterName' already exists. " +
            "Use -RecreateExisting only when you intentionally want a fresh demo environment."
        )
    }
}

# ============================================================
# CREATE CLUSTER
# ============================================================

Write-Host "[INFO] Creating Kind cluster..."

kind create cluster `
    --name $ClusterName `
    --config $KindConfigPath

if ($LASTEXITCODE -ne 0) {
    Fail "Kind cluster creation failed."
}

# ============================================================
# VERIFY KUBECTL CONTEXT
# ============================================================

Write-Host ""
Write-Host "[INFO] Verifying kubectl context..."

$CurrentContext = kubectl config current-context

if ($LASTEXITCODE -ne 0) {
    Fail "Unable to read kubectl current context."
}

if ($CurrentContext.Trim() -ne $ExpectedContext) {

    Write-Host "[INFO] Current context is '$CurrentContext'."
    Write-Host "[INFO] Switching to '$ExpectedContext'."

    kubectl config use-context $ExpectedContext | Out-Null

    if ($LASTEXITCODE -ne 0) {
        Fail "Unable to switch kubectl context to '$ExpectedContext'."
    }
}

# ============================================================
# WAIT FOR NODES
# ============================================================

Write-Host ""
Write-Host "[INFO] Waiting for Kubernetes nodes to become Ready..."

kubectl wait `
    --for=condition=Ready `
    nodes `
    --all `
    --timeout=180s

if ($LASTEXITCODE -ne 0) {
    Fail "Kubernetes nodes did not become Ready within 180 seconds."
}

# ============================================================
# FINAL VERIFICATION
# ============================================================

$FinalContext = kubectl config current-context

if ($FinalContext.Trim() -ne $ExpectedContext) {
    Fail "Unexpected kubectl context after cluster creation: $FinalContext"
}

Write-Host ""
kubectl get nodes -o wide

if ($LASTEXITCODE -ne 0) {
    Fail "kubectl get nodes failed after cluster creation."
}

Write-Section "CLUSTER READY"

Write-Host "Cluster Name : $ClusterName"
Write-Host "Context      : $ExpectedContext"
Write-Host "Status       : READY"
Write-Host ""
Write-Host "Ephemeral Kubernetes cluster creation completed successfully."

exit 0
