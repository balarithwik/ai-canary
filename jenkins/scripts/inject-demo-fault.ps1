param(
    [string]$ProjectRoot = "",
    [double]$DelayMs = 140,
    [double]$ErrorRate = 0.08
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

function Test-PodReady {
    param($Pod)

    foreach ($condition in @($Pod.status.conditions)) {
        if (
            $condition.type -eq "Ready" -and
            $condition.status -eq "True"
        ) {
            return $true
        }
    }

    return $false
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
$ScenarioStatePath = Join-Path $ProjectRoot "runtime\scenario-state.json"

if (-not (Test-Path $ConfigPath)) {
    Fail "pipeline-config.json not found."
}

if (-not (Test-Path $ScenarioStatePath)) {
    Fail "scenario-state.json not found."
}

$Config = Get-Content $ConfigPath -Raw | ConvertFrom-Json
$ScenarioState = Get-Content $ScenarioStatePath -Raw | ConvertFrom-Json

$ClusterName = [string]$Config.project.cluster_name
$Namespace = [string]$Config.project.namespace
$RolloutName = [string]$Config.project.rollout_name
$TrafficPod = [string]$Config.traffic.generator_pod

$ExpectedContext = "kind-$ClusterName"

$CanaryVersion = [string]$ScenarioState.canary_version
$Checkpoint = [int]$ScenarioState.current_checkpoint
$Scenario = [string]$ScenarioState.scenario

Write-Section "CANARY CONTROLLED DEGRADATION"

Write-Host "Scenario       : $Scenario"
Write-Host "Checkpoint     : $Checkpoint%"
Write-Host "Canary Version : $CanaryVersion"
Write-Host "Delay          : $DelayMs ms"
Write-Host "Error Rate     : $($ErrorRate * 100)%"

# ============================================================
# SAFETY CHECKS
# ============================================================

if ($Scenario -ne "ROLLBACK_AT_50") {
    Fail "This action is allowed only for ROLLBACK_AT_50."
}

if ($Checkpoint -ne 50) {
    Fail "Expected checkpoint 50%, current checkpoint is $Checkpoint%."
}

$CurrentContext = kubectl config current-context

if ($LASTEXITCODE -ne 0) {
    Fail "Unable to read Kubernetes context."
}

if ($CurrentContext.Trim() -ne $ExpectedContext) {
    Fail "Wrong Kubernetes context: $CurrentContext"
}

$RolloutRaw = kubectl get rollout $RolloutName `
    -n $Namespace `
    -o json

if ($LASTEXITCODE -ne 0) {
    Fail "Unable to read Argo Rollout."
}

$Rollout = $RolloutRaw | ConvertFrom-Json

$PauseCount = @($Rollout.status.pauseConditions).Count

if (
    [string]$Rollout.status.phase -ne "Paused" -or
    $PauseCount -lt 1
) {
    Fail "Rollout must be paused before applying Canary degradation."
}

Write-Host "[PASS] Rollout is paused at the AI checkpoint."

# ============================================================
# DISCOVER CURRENT CANARY PODS
# ============================================================

$PodsRaw = kubectl get pods `
    -n $Namespace `
    -l "app=$RolloutName" `
    -o json

if ($LASTEXITCODE -ne 0) {
    Fail "Unable to list Rollout pods."
}

$Pods = $PodsRaw | ConvertFrom-Json

$CanaryPods = @(
    $Pods.items |
    Where-Object {
        $_.metadata.labels.version -eq $CanaryVersion -and
        $_.status.phase -eq "Running" -and
        (Test-PodReady $_)
    }
)

if ($CanaryPods.Count -lt 1) {
    Fail "No Ready Canary pods found for '$CanaryVersion'."
}

Write-Host "[PASS] Active Canary pods discovered: $($CanaryPods.Count)"

foreach ($Pod in $CanaryPods) {
    Write-Host "       $($Pod.metadata.name) - $($Pod.status.podIP)"
}

# ============================================================
# VERIFY TRAFFIC GENERATOR
# ============================================================

$TrafficPhase = kubectl get pod $TrafficPod `
    -n $Namespace `
    -o jsonpath="{.status.phase}"

if ($LASTEXITCODE -ne 0) {
    Fail "Traffic generator '$TrafficPod' was not found."
}

if ($TrafficPhase.Trim() -ne "Running") {
    Fail "Traffic generator is not Running."
}

Write-Host "[PASS] Traffic generator is available."

# ============================================================
# APPLY DEGRADATION TO CANARY PODS ONLY
# ============================================================

$Invariant = [System.Globalization.CultureInfo]::InvariantCulture
$DelayText = $DelayMs.ToString("0.###", $Invariant)
$ErrorText = $ErrorRate.ToString("0.###", $Invariant)

Write-Section "APPLY CANARY RUNTIME CONTROL"

foreach ($Pod in $CanaryPods) {

    $PodName = [string]$Pod.metadata.name
    $PodIp = [string]$Pod.status.podIP

    if ([string]::IsNullOrWhiteSpace($PodIp)) {
        Fail "Pod IP missing for $PodName."
    }

    Write-Host ""
    Write-Host "Target Canary : $PodName"

    $FaultUrl = (
        "http://${PodIp}:5000/demo/fault" +
        "?delay_ms=$DelayText&error_rate=$ErrorText"
    )

    $ResponseRaw = & kubectl exec `
        -n $Namespace `
        $TrafficPod `
        -- curl -sS --max-time 10 -X POST $FaultUrl

    if ($LASTEXITCODE -ne 0) {
        Fail "Unable to apply runtime control to $PodName."
    }

    try {
        $Response = ($ResponseRaw -join "`n") | ConvertFrom-Json
    }
    catch {
        Fail "Invalid response received from $PodName."
    }

    if ($Response.status -ne "CONTROL_APPLIED") {
        Fail "Runtime control was not accepted by $PodName."
    }

    if ([string]$Response.version -ne $CanaryVersion) {
        Fail "Version mismatch while targeting $PodName."
    }

    Write-Host "[PASS] Runtime control accepted."

    # --------------------------------------------------------
    # VERIFY CURRENT STATE
    # --------------------------------------------------------

    $StateUrl = "http://${PodIp}:5000/demo/state"

    $StateRaw = & kubectl exec `
        -n $Namespace `
        $TrafficPod `
        -- curl -sS --max-time 10 $StateUrl

    if ($LASTEXITCODE -ne 0) {
        Fail "Unable to verify runtime state on $PodName."
    }

    try {
        $CurrentState = ($StateRaw -join "`n") | ConvertFrom-Json
    }
    catch {
        Fail "Invalid state response from $PodName."
    }

    if ([string]$CurrentState.version -ne $CanaryVersion) {
        Fail "Canary version verification failed on $PodName."
    }

    Write-Host "[PASS] Canary runtime state verified."
    Write-Host "       Delay      : $($CurrentState.delay_ms) ms"
    Write-Host "       Error Rate : $([double]$CurrentState.error_rate * 100)%"
}

Write-Section "CANARY DEGRADATION ACTIVE"

Write-Host "Canary Version : $CanaryVersion"
Write-Host "Canary Pods    : $($CanaryPods.Count)"
Write-Host "Traffic        : CONTINUES"
Write-Host "Argo Revision  : UNCHANGED"
Write-Host "Rollout        : REMAINS PAUSED"
Write-Host ""
Write-Host "No deployment promotion or rollback was executed."
Write-Host "The next step is fresh AI telemetry analysis."
