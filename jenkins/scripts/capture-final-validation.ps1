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
$RuntimeDir = Join-Path $ProjectRoot "runtime"
$OutputPath = Join-Path $RuntimeDir "final-validation.json"

if (-not (Test-Path $ConfigPath)) {
    Fail "pipeline-config.json not found."
}

if (-not (Test-Path $ScenarioStatePath)) {
    Fail "scenario-state.json not found."
}

New-Item -ItemType Directory -Force -Path $RuntimeDir | Out-Null

$Config = Get-Content $ConfigPath -Raw | ConvertFrom-Json
$State = Get-Content $ScenarioStatePath -Raw | ConvertFrom-Json

$ClusterName = [string]$Config.project.cluster_name
$Namespace = [string]$Config.project.namespace
$RolloutName = [string]$Config.project.rollout_name
$ServiceName = [string]$Config.project.service_name
$TrafficPod = [string]$Config.traffic.generator_pod

$ExpectedContext = "kind-$ClusterName"

$Scenario = [string]$State.scenario
$OriginalStable = [string]$State.stable_version
$CandidateVersion = [string]$State.canary_version

Write-Section "FINAL DEPLOYMENT VALIDATION"

Write-Host "Scenario        : $Scenario"
Write-Host "Original Stable : $OriginalStable"
Write-Host "Candidate       : $CandidateVersion"

# ============================================================
# VERIFY CONTEXT
# ============================================================

$CurrentContext = kubectl config current-context

if ($LASTEXITCODE -ne 0) {
    Fail "Unable to read Kubernetes context."
}

if ($CurrentContext.Trim() -ne $ExpectedContext) {
    Fail "Wrong Kubernetes context '$CurrentContext'. Expected '$ExpectedContext'."
}

Write-Host "[PASS] Kubernetes context verified."

# ============================================================
# READ ROLLOUT
# ============================================================

$RolloutRaw = kubectl get rollout $RolloutName `
    -n $Namespace `
    -o json

if ($LASTEXITCODE -ne 0) {
    Fail "Unable to read Argo Rollout."
}

$Rollout = $RolloutRaw | ConvertFrom-Json

$RolloutPhase = [string]$Rollout.status.phase
$DesiredPods = [int]$Rollout.spec.replicas

if ([string]::IsNullOrWhiteSpace($RolloutPhase)) {
    $RolloutPhase = "UNKNOWN"
}

Write-Host "Rollout Phase   : $RolloutPhase"
Write-Host "Desired Pods    : $DesiredPods"

if ($RolloutPhase -ne "Healthy") {
    Fail "Final Rollout is not Healthy. Current phase: $RolloutPhase"
}

Write-Host "[PASS] Rollout is Healthy."

# ============================================================
# DISCOVER ACTIVE APPLICATION PODS
# ============================================================

$PodsRaw = kubectl get pods `
    -n $Namespace `
    -l "app=$RolloutName" `
    -o json

if ($LASTEXITCODE -ne 0) {
    Fail "Unable to read application pods."
}

$Pods = ($PodsRaw | ConvertFrom-Json).items

$RunningReadyPods = @(
    $Pods | Where-Object {
        $_.status.phase -eq "Running" -and
        (Test-PodReady $_)
    }
)

$ReadyPods = $RunningReadyPods.Count

if ($ReadyPods -ne $DesiredPods) {
    Fail "Expected $DesiredPods Ready pods but found $ReadyPods."
}

Write-Host "[PASS] Ready Pods: $ReadyPods/$DesiredPods"

$ActiveVersions = @(
    $RunningReadyPods |
    ForEach-Object {
        [string]$_.metadata.labels.version
    } |
    Where-Object {
        -not [string]::IsNullOrWhiteSpace($_)
    } |
    Sort-Object -Unique
)

if ($ActiveVersions.Count -ne 1) {
    Fail "Expected exactly one final active application version. Found: $($ActiveVersions -join ', ')"
}

$ActiveVersion = [string]$ActiveVersions[0]

Write-Host "Active Version  : $ActiveVersion"

# ============================================================
# FINAL VERSION EXPECTATION
# ============================================================

if ($Scenario -eq "ALL_STAGES_PROMOTE") {

    if ($ActiveVersion -ne $CandidateVersion) {
        Fail (
            "Promotion scenario expected Candidate '$CandidateVersion' " +
            "to become the final Stable, but active version is '$ActiveVersion'."
        )
    }

    $Outcome = "NEW_STABLE_ACTIVE"
    $RecoveryVerified = "N/A"

    Write-Host "[PASS] Candidate successfully became the final Stable."
}
elseif ($Scenario -eq "ROLLBACK_AT_50") {

    if ($ActiveVersion -ne $OriginalStable) {
        Fail (
            "Recovery scenario expected previous Stable '$OriginalStable' " +
            "but active version is '$ActiveVersion'."
        )
    }

    $RejectedCanaryPods = @(
        $RunningReadyPods |
        Where-Object {
            [string]$_.metadata.labels.version -eq $CandidateVersion
        }
    )

    if ($RejectedCanaryPods.Count -gt 0) {
        Fail "Rejected Candidate still has active Ready pods."
    }

    $Outcome = "PREVIOUS_STABLE_RESTORED"
    $RecoveryVerified = $true

    Write-Host "[PASS] Previous Stable restored."
    Write-Host "[PASS] Rejected Candidate is not active."
}
else {
    Fail "Unsupported scenario '$Scenario'."
}

# ============================================================
# APPLICATION HEALTH VERIFICATION
# ============================================================

Write-Section "APPLICATION HEALTH VERIFICATION"

$TrafficStatus = kubectl get pod $TrafficPod `
    -n $Namespace `
    -o jsonpath="{.status.phase}"

if ($LASTEXITCODE -ne 0) {
    Fail "Unable to verify traffic-generator pod."
}

if ($TrafficStatus.Trim() -ne "Running") {
    Fail "Traffic-generator is not Running."
}

$HealthSamples = @()
$HealthVersions = @()

for ($i = 1; $i -le 5; $i++) {

    $HealthRaw = kubectl exec `
        -n $Namespace `
        $TrafficPod `
        -- curl -sS --max-time 10 `
        "http://${ServiceName}/health"

    if ($LASTEXITCODE -ne 0) {
        Fail "Application health request $i failed."
    }

    try {
        $Health = $HealthRaw | ConvertFrom-Json
    }
    catch {
        Fail "Application health response $i was not valid JSON: $HealthRaw"
    }

    $Status = [string]$Health.status
    $Version = [string]$Health.version

    Write-Host "Health $i : $Status / $Version"

    if ($Status -ne "UP") {
        Fail "Health request $i returned status '$Status'."
    }

    if ($Version -ne $ActiveVersion) {
        Fail (
            "Health request $i returned version '$Version' " +
            "instead of active version '$ActiveVersion'."
        )
    }

    $HealthSamples += [PSCustomObject]@{
        sample  = $i
        status  = $Status
        version = $Version
    }

    $HealthVersions += $Version
}

$UniqueHealthVersions = @(
    $HealthVersions |
    Sort-Object -Unique
)

if (
    $UniqueHealthVersions.Count -ne 1 -or
    $UniqueHealthVersions[0] -ne $ActiveVersion
) {
    Fail "Health verification observed inconsistent application versions."
}

Write-Host "[PASS] Application health verified across 5 requests."

# ============================================================
# DETERMINE FINAL TRAFFIC STATE
# ============================================================

# Final Healthy state contains one active application version.
# That version is the current Stable role regardless of whether
# it originated as the old Stable or the Candidate.
$StableWeight = 100
$CanaryWeight = 0
$CanaryActive = $false

# ============================================================
# BUILD OUTPUT
# ============================================================

$Output = [PSCustomObject]@{
    timestamp               = [DateTimeOffset]::Now.ToString("o")
    scenario                = $Scenario

    rollout_status          = $RolloutPhase
    outcome                 = $Outcome

    original_stable_version = $OriginalStable
    candidate_version       = $CandidateVersion
    active_version          = $ActiveVersion
    final_stable_version    = $ActiveVersion

    stable_weight           = $StableWeight
    canary_weight           = $CanaryWeight
    canary_active           = $CanaryActive

    desired_pods            = $DesiredPods
    ready_pods              = $ReadyPods

    application_health      = "PASS"
    health_samples          = @($HealthSamples)

    recovery_verified       = $RecoveryVerified
    final_state_verified    = $true
}

$TempPath = "$OutputPath.tmp"

$Output |
ConvertTo-Json -Depth 10 |
Set-Content $TempPath -Encoding UTF8

Move-Item $TempPath $OutputPath -Force

# ============================================================
# SUMMARY
# ============================================================

Write-Section "FINAL STATE VERIFIED"

Write-Host "Rollout State      : $RolloutPhase"
Write-Host "Final Stable       : $ActiveVersion"
Write-Host "Stable Traffic     : 100%"
Write-Host "Canary Traffic     : 0%"
Write-Host "Ready Pods         : $ReadyPods/$DesiredPods"
Write-Host "Application Health : PASS"
Write-Host "Outcome            : $Outcome"

if ($Scenario -eq "ROLLBACK_AT_50") {
    Write-Host "Recovery Verified  : YES"
}

Write-Host ""
Write-Host "[PASS] Final validation saved:"
Write-Host $OutputPath

exit 0
