param(
    [ValidateSet(
        "ALL_STAGES_PROMOTE",
        "ROLLBACK_AT_50"
    )]
    [string]$Scenario = "ALL_STAGES_PROMOTE",

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
$TrafficStatePath = Join-Path $ProjectRoot "runtime\traffic-state.json"
$PortForwardStatePath = Join-Path $ProjectRoot "runtime\port-forwards.json"

$RendererPath = Join-Path $ProjectRoot "ai-engine\render_rollout.py"
$RenderedCanaryPath = Join-Path $ProjectRoot "runtime\rendered\canary-rollout.yaml"
$ScenarioStatePath = Join-Path $ProjectRoot "runtime\scenario-state.json"

switch ($Scenario) {
    "ALL_STAGES_PROMOTE" {
        $ScenarioFile = Join-Path $ProjectRoot "scenarios\promote-all.json"
    }

    "ROLLBACK_AT_50" {
        $ScenarioFile = Join-Path $ProjectRoot "scenarios\rollback-at-50.json"
    }
}

foreach ($required in @(
    $ConfigPath,
    $BuildInfoPath,
    $RendererPath,
    $ScenarioFile
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
$TrafficPod = [string]$Config.traffic.generator_pod
$PrometheusPort = [int]$Config.ports.prometheus

$ExpectedContext = "kind-$ClusterName"

$StableVersion = [string]$BuildInfo.stable_version
$CanaryVersion = [string]$BuildInfo.canary_version
$CanaryImage = [string]$BuildInfo.canary_image

if ([string]::IsNullOrWhiteSpace($CanaryVersion)) {
    Fail "canary_version is missing from runtime\build-info.json"
}

if ([string]::IsNullOrWhiteSpace($CanaryImage)) {
    Fail "canary_image is missing from runtime\build-info.json"
}

Write-Section "DEPLOY CANARY - FIRST CHECKPOINT"

Write-Host "Scenario       : $Scenario"
Write-Host "Stable Version : $StableVersion"
Write-Host "Canary Version : $CanaryVersion"
Write-Host "Canary Image   : $CanaryImage"
Write-Host "First Target   : 20%"

# ============================================================
# SAFETY / PRECONDITIONS
# ============================================================

$CurrentContext = kubectl config current-context
Assert-NativeSuccess "Unable to read kubectl current context."

if ($CurrentContext.Trim() -ne $ExpectedContext) {
    Fail (
        "Refusing Canary deployment because kubectl context is " +
        "'$CurrentContext'. Expected '$ExpectedContext'."
    )
}

$CurrentPhase = kubectl get rollout $RolloutName `
    -n $Namespace `
    -o jsonpath="{.status.phase}"

Assert-NativeSuccess "Unable to read current Rollout phase."

if ($CurrentPhase.Trim() -ne "Healthy") {
    Fail (
        "Initial Stable Rollout must be Healthy before Canary deployment. " +
        "Current phase: $CurrentPhase"
    )
}

$ObservedStable = kubectl get rollout $RolloutName `
    -n $Namespace `
    -o jsonpath="{.spec.template.metadata.labels.version}"

Assert-NativeSuccess "Unable to read current Stable version."

if ($ObservedStable.Trim() -ne $StableVersion) {
    Fail (
        "Current Rollout version does not match build metadata. " +
        "Expected Stable '$StableVersion', observed '$ObservedStable'."
    )
}

$TrafficPodPhase = kubectl get pod $TrafficPod `
    -n $Namespace `
    -o jsonpath="{.status.phase}"

Assert-NativeSuccess "Traffic generator '$TrafficPod' was not found."

if ($TrafficPodPhase.Trim() -ne "Running") {
    Fail "Traffic generator is not Running. Current phase: $TrafficPodPhase"
}

try {
    $PrometheusReady = Invoke-WebRequest `
        -Uri "http://localhost:$PrometheusPort/-/ready" `
        -UseBasicParsing `
        -TimeoutSec 5
}
catch {
    Fail (
        "Prometheus port-forward is not reachable on localhost:$PrometheusPort. " +
        "Keep start-port-forwards.ps1 running before Canary deployment."
    )
}

if ($PrometheusReady.StatusCode -ne 200) {
    Fail "Prometheus is not Ready."
}

Write-Host ""
Write-Host "[PASS] Stable, traffic, and Prometheus prerequisites are ready."

# ============================================================
# RENDER CANARY
# ============================================================

Write-Section "RENDER CANARY ROLLOUT"

python $RendererPath `
    --mode canary `
    --version $CanaryVersion `
    --image $CanaryImage `
    --scenario $ScenarioFile

Assert-NativeSuccess "Canary Rollout rendering failed."

if (-not (Test-Path $RenderedCanaryPath)) {
    Fail "Rendered Canary Rollout not found: $RenderedCanaryPath"
}

Write-Host "[PASS] Canary Rollout rendered for scenario: $Scenario"

# ============================================================
# APPLY CANARY REVISION
# ============================================================

Write-Section "START CANARY REVISION"

kubectl apply -f $RenderedCanaryPath
Assert-NativeSuccess "Canary Rollout deployment failed."

Write-Host "[PASS] Canary revision submitted to Argo Rollouts."

# ============================================================
# WAIT FOR FIRST PAUSE (20%)
# ============================================================

Write-Section "WAIT FOR 20% CHECKPOINT"

$TimeoutSeconds = 180
$PollSeconds = 3
$Elapsed = 0
$CheckpointReached = $false

while ($Elapsed -lt $TimeoutSeconds) {

    $RolloutJsonRaw = kubectl get rollout $RolloutName `
        -n $Namespace `
        -o json

    Assert-NativeSuccess "Unable to read Rollout JSON."

    $Rollout = $RolloutJsonRaw | ConvertFrom-Json

    $Phase = [string]$Rollout.status.phase
    $StepIndex = $Rollout.status.currentStepIndex
    $PauseCount = @($Rollout.status.pauseConditions).Count

    $PodsRaw = kubectl get pods `
        -n $Namespace `
        -l "app=$RolloutName" `
        -o json

    Assert-NativeSuccess "Unable to list Rollout pods."

    $Pods = $PodsRaw | ConvertFrom-Json

    $CanaryPods = @(
        $Pods.items |
        Where-Object {
            $_.metadata.labels.version -eq $CanaryVersion
        }
    )

    $ReadyCanaryPods = @(
        $CanaryPods |
        Where-Object {
            Test-PodReady $_
        }
    )

    Write-Host (
        "[INFO] Phase={0} StepIndex={1} PauseConditions={2} " +
        "CanaryReady={3}/{4} Elapsed={5}s" -f
        $Phase,
        $StepIndex,
        $PauseCount,
        $ReadyCanaryPods.Count,
        $CanaryPods.Count,
        $Elapsed
    )

    if ($Phase -eq "Degraded") {
        Fail "Canary Rollout entered Degraded state before AI analysis."
    }

    if (
        $PauseCount -gt 0 -and
        $CanaryPods.Count -ge 1 -and
        $ReadyCanaryPods.Count -eq $CanaryPods.Count
    ) {
        $CheckpointReached = $true
        break
    }

    Start-Sleep -Seconds $PollSeconds
    $Elapsed += $PollSeconds
}

if (-not $CheckpointReached) {
    Fail "Canary did not reach the first paused checkpoint within $TimeoutSeconds seconds."
}

Write-Host "[PASS] Canary reached the first paused checkpoint with Ready pods."

# ============================================================
# VERIFY TRAFFIC REACHES CANARY VIA PROMETHEUS
# ============================================================

Write-Section "VERIFY CANARY TELEMETRY"

$PrometheusBase = "http://localhost:$PrometheusPort"

$MetricQuery = (
    "sum(" +
    "http_requests_total{version=`"$CanaryVersion`",endpoint=`"/api/orders`"}" +
    " or " +
    "http_requests_total{version=`"$CanaryVersion`",exported_endpoint=`"/api/orders`"}" +
    ")"
)

$MetricTimeoutSeconds = 60
$MetricElapsed = 0
$CanaryRequests = 0
$MetricFound = $false

while ($MetricElapsed -lt $MetricTimeoutSeconds) {

    $MetricResponse = Invoke-RestMethod `
        -Uri (
            "$PrometheusBase/api/v1/query?query=" +
            [uri]::EscapeDataString($MetricQuery)
        ) `
        -Method Get `
        -TimeoutSec 15

    if (
        $MetricResponse.status -eq "success" -and
        $MetricResponse.data.result.Count -gt 0
    ) {
        $CanaryRequests = [double]$MetricResponse.data.result[0].value[1]

        if ($CanaryRequests -gt 0) {
            $MetricFound = $true
            break
        }
    }

    Write-Host (
        "[INFO] Waiting for Canary requests in Prometheus... " +
        "Elapsed=${MetricElapsed}s"
    )

    Start-Sleep -Seconds 5
    $MetricElapsed += 5
}

if (-not $MetricFound) {
    Fail (
        "Canary pods are Ready, but Prometheus has not observed /api/orders " +
        "traffic for '$CanaryVersion' within $MetricTimeoutSeconds seconds."
    )
}

Write-Host "[PASS] Prometheus has live Canary application telemetry."
Write-Host "       Canary Requests Seen : $CanaryRequests"

# ============================================================
# RECORD SCENARIO STATE
# ============================================================

$ScenarioState = [ordered]@{
    scenario = $Scenario
    scenario_file = $ScenarioFile
    stable_version = $StableVersion
    canary_version = $CanaryVersion
    current_checkpoint = 20
    rollout_phase = "Paused"
    ai_decision_pending = $true
    started_at = (Get-Date).ToString("o")
}

$ScenarioState |
    ConvertTo-Json -Depth 8 |
    Set-Content `
        -Path $ScenarioStatePath `
        -Encoding UTF8

Write-Host "[PASS] Scenario runtime state written:"
Write-Host "       $ScenarioStatePath"

# ============================================================
# DISPLAY
# ============================================================

Write-Host ""
kubectl get rollout $RolloutName -n $Namespace

Write-Host ""
kubectl get pods `
    -n $Namespace `
    -l "app=$RolloutName" `
    -L version

Write-Section "CANARY 20% READY FOR AI"

Write-Host "Scenario       : $Scenario"
Write-Host "Stable Version : $StableVersion"
Write-Host "Canary Version : $CanaryVersion"
Write-Host "Checkpoint     : 20%"
Write-Host "Status         : PAUSED - WAITING FOR AI DECISION"
Write-Host ""
Write-Host "Traffic remains active."
Write-Host "No promotion has been executed yet."
Write-Host ""

exit 0
