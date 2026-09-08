param(
    [string]$ProjectRoot = ""
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

function Assert-NativeSuccess {
    param([string]$Message)

    if ($LASTEXITCODE -ne 0) {
        Fail $Message
    }
}

# ============================================================
# RESOLVE PROJECT ROOT / CONFIG
# ============================================================

if ([string]::IsNullOrWhiteSpace($ProjectRoot)) {
    $ScriptDir = Split-Path -Parent $MyInvocation.MyCommand.Path
    $ProjectRoot = (Resolve-Path (Join-Path $ScriptDir "..\..")).Path
}
else {
    $ProjectRoot = (Resolve-Path $ProjectRoot).Path
}

$ConfigPath = Join-Path $ProjectRoot "jenkins\config\pipeline-config.json"
$BuildInfoPath = Join-Path $ProjectRoot "runtime\build-info.json"

$RendererPath = Join-Path $ProjectRoot "ai-engine\render_rollout.py"

$ServicePath = Join-Path $ProjectRoot "kubernetes\service.yaml"
$ServiceMonitorPath = Join-Path $ProjectRoot "kubernetes\servicemonitor.yaml"

$RenderedStablePath = Join-Path $ProjectRoot "runtime\rendered\stable-rollout.yaml"

foreach ($required in @(
    $ConfigPath,
    $BuildInfoPath,
    $RendererPath,
    $ServicePath,
    $ServiceMonitorPath
)) {
    if (-not (Test-Path $required)) {
        Fail "Required file not found: $required"
    }
}

$Config = Get-Content $ConfigPath -Raw | ConvertFrom-Json
$BuildInfo = Get-Content $BuildInfoPath -Raw | ConvertFrom-Json

$ClusterName = [string]$Config.project.cluster_name
$Namespace = [string]$Config.project.namespace
$RolloutName = [string]$Config.project.rollout_name
$ExpectedContext = "kind-$ClusterName"

$StableVersion = [string]$BuildInfo.stable_version
$StableImage = [string]$BuildInfo.stable_image

if ([string]::IsNullOrWhiteSpace($StableVersion)) {
    Fail "stable_version is missing from runtime\build-info.json"
}

if ([string]::IsNullOrWhiteSpace($StableImage)) {
    Fail "stable_image is missing from runtime\build-info.json"
}

Write-Section "DEPLOY INITIAL STABLE"

Write-Host "Project Root   : $ProjectRoot"
Write-Host "Cluster        : $ClusterName"
Write-Host "Namespace      : $Namespace"
Write-Host "Rollout        : $RolloutName"
Write-Host "Stable Version : $StableVersion"
Write-Host "Stable Image   : $StableImage"

# ============================================================
# SAFETY - VERIFY KUBECTL CONTEXT
# ============================================================

$CurrentContext = kubectl config current-context
Assert-NativeSuccess "Unable to read kubectl current context."

if ($CurrentContext.Trim() -ne $ExpectedContext) {
    Fail (
        "Refusing deployment because kubectl context is " +
        "'$CurrentContext'. Expected '$ExpectedContext'."
    )
}

kubectl get namespace $Namespace | Out-Null
Assert-NativeSuccess "Application namespace '$Namespace' does not exist."

Write-Host ""
Write-Host "[PASS] Correct Kubernetes context and namespace."

# ============================================================
# RENDER STABLE ROLLOUT
# ============================================================

Write-Section "RENDER STABLE ROLLOUT"

python $RendererPath `
    --mode stable `
    --version $StableVersion `
    --image $StableImage

Assert-NativeSuccess "Stable Rollout rendering failed."

if (-not (Test-Path $RenderedStablePath)) {
    Fail "Rendered Stable Rollout was not created: $RenderedStablePath"
}

Write-Host "[PASS] Stable Rollout rendered."

# ============================================================
# APPLY APPLICATION SERVICE + MONITORING
# ============================================================

Write-Section "APPLY APPLICATION RESOURCES"

kubectl apply -f $ServicePath
Assert-NativeSuccess "Application Service deployment failed."

kubectl apply -f $ServiceMonitorPath
Assert-NativeSuccess "Application ServiceMonitor deployment failed."

Write-Host "[PASS] Application Service and ServiceMonitor ready."

# ============================================================
# APPLY INITIAL STABLE ROLLOUT
# ============================================================

Write-Section "APPLY STABLE ROLLOUT"

kubectl apply -f $RenderedStablePath
Assert-NativeSuccess "Initial Stable Rollout deployment failed."

Write-Host "[PASS] Stable Rollout submitted."

# ============================================================
# WAIT FOR ARGO ROLLOUT HEALTH
# ============================================================

Write-Section "WAIT FOR STABLE HEALTH"

$TimeoutSeconds = 300
$PollSeconds = 5
$Elapsed = 0
$Healthy = $false

while ($Elapsed -lt $TimeoutSeconds) {

    $Phase = kubectl get rollout $RolloutName `
        -n $Namespace `
        -o jsonpath="{.status.phase}" 2>$null

    if ($LASTEXITCODE -ne 0) {
        Fail "Unable to read Argo Rollout status."
    }

    $ReadyReplicas = kubectl get rollout $RolloutName `
        -n $Namespace `
        -o jsonpath="{.status.readyReplicas}" 2>$null

    if ($LASTEXITCODE -ne 0) {
        $ReadyReplicas = "0"
    }

    $Replicas = kubectl get rollout $RolloutName `
        -n $Namespace `
        -o jsonpath="{.status.replicas}" 2>$null

    if ($LASTEXITCODE -ne 0) {
        $Replicas = "0"
    }

    Write-Host (
        "[INFO] Phase={0} Ready={1}/{2} Elapsed={3}s" -f
        $Phase,
        $(if ($ReadyReplicas) { $ReadyReplicas } else { "0" }),
        $(if ($Replicas) { $Replicas } else { "0" }),
        $Elapsed
    )

    if ($Phase -eq "Healthy") {
        $Healthy = $true
        break
    }

    if ($Phase -eq "Degraded") {
        Fail "Stable Rollout entered Degraded state."
    }

    Start-Sleep -Seconds $PollSeconds
    $Elapsed += $PollSeconds
}

if (-not $Healthy) {
    Fail "Stable Rollout did not become Healthy within $TimeoutSeconds seconds."
}

# ============================================================
# VERIFY VERSION / PODS
# ============================================================

Write-Section "VERIFY INITIAL STABLE"

$ObservedVersion = kubectl get rollout $RolloutName `
    -n $Namespace `
    -o jsonpath="{.spec.template.metadata.labels.version}"

Assert-NativeSuccess "Unable to read deployed Stable version."

if ($ObservedVersion.Trim() -ne $StableVersion) {
    Fail (
        "Stable version mismatch. Expected '$StableVersion', " +
        "observed '$ObservedVersion'."
    )
}

kubectl get rollout $RolloutName -n $Namespace
Assert-NativeSuccess "Unable to display Rollout."

Write-Host ""
kubectl get pods -n $Namespace -l "app=$RolloutName" -o wide
Assert-NativeSuccess "Unable to display Stable pods."

Write-Host ""
kubectl get svc -n $Namespace
Assert-NativeSuccess "Unable to display application Service."

Write-Host ""
kubectl get servicemonitor -n $Namespace
Assert-NativeSuccess "Unable to display application ServiceMonitor."

# ============================================================
# FINAL RESULT
# ============================================================

Write-Section "INITIAL STABLE READY"

Write-Host "Version : $StableVersion"
Write-Host "Image   : $StableImage"
Write-Host "Status  : HEALTHY"
Write-Host ""
Write-Host "The initial Stable revision is now the live baseline."
Write-Host "No Canary revision has been deployed yet."
Write-Host ""

exit 0
