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

function Get-PrometheusScalar {
    param(
        [string]$PrometheusUrl,
        [string]$Query
    )

    try {
        $Encoded = [System.Uri]::EscapeDataString($Query)
        $Response = Invoke-RestMethod `
            -Uri "$PrometheusUrl/api/v1/query?query=$Encoded" `
            -Method Get `
            -TimeoutSec 10

        if (
            $Response.status -ne "success" -or
            $null -eq $Response.data -or
            @($Response.data.result).Count -lt 1
        ) {
            return $null
        }

        return [double]$Response.data.result[0].value[1]
    }
    catch {
        return $null
    }
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
$FinalValidationPath = Join-Path $ProjectRoot "runtime\final-validation.json"
$DecisionPath = Join-Path $ProjectRoot "ai-engine\ai_decision.json"
$CheckpointMetricsPath = Join-Path $ProjectRoot "ai-engine\ai_metrics.prom"
$FinalMetricsPath = Join-Path $ProjectRoot "runtime\final-dashboard-metrics.prom"

foreach ($Required in @(
    $ConfigPath,
    $FinalValidationPath,
    $DecisionPath,
    $CheckpointMetricsPath
)) {
    if (-not (Test-Path $Required)) {
        Fail "Required final dashboard input not found: $Required"
    }
}

$Config = Get-Content $ConfigPath -Raw | ConvertFrom-Json
$FinalValidation = Get-Content $FinalValidationPath -Raw | ConvertFrom-Json
$DecisionData = Get-Content $DecisionPath -Raw | ConvertFrom-Json

$Scenario = [string]$FinalValidation.scenario
$FinalStable = [string]$FinalValidation.final_stable_version
$StableWeight = [int]$FinalValidation.stable_weight
$CanaryWeight = [int]$FinalValidation.canary_weight
$FinalStateVerified = [bool]$FinalValidation.final_state_verified
$RolloutState = [string]$FinalValidation.rollout_status

$Decision = ([string]$DecisionData.final_assessment.decision).ToUpperInvariant()
$DecisionSource = [string]$DecisionData.final_assessment.decision_source
$RiskLevel = [string]$DecisionData.final_assessment.risk_level
$RiskScore = $DecisionData.final_assessment.risk_score

$PrometheusUrl = ([string]$Config.monitoring.prometheus_url).TrimEnd("/")
$PushgatewayUrl = ([string]$Config.monitoring.pushgateway_url).TrimEnd("/")
$PushUrl = "$PushgatewayUrl/metrics/job/ai-canary-intelligence"

Write-Section "PUBLISH FINAL DASHBOARD STATE"

Write-Host "Scenario        : $Scenario"
Write-Host "Final Stable    : $FinalStable"
Write-Host "Rollout State   : $RolloutState"
Write-Host "Stable Traffic  : $StableWeight%"
Write-Host "Canary Traffic  : $CanaryWeight%"
Write-Host "AI Decision     : $Decision"
Write-Host "Decision Source : $DecisionSource"
Write-Host "Risk Level      : $RiskLevel"
Write-Host "Risk Score      : $RiskScore/100"

# ============================================================
# SAFETY - ONLY PUBLISH A VERIFIED FINAL STATE
# ============================================================

if (-not $FinalStateVerified) {
    Fail "final-validation.json does not confirm final_state_verified=true."
}

if ($RolloutState -ne "Healthy") {
    Fail "Final Rollout state is '$RolloutState', not Healthy."
}

if ($StableWeight -ne 100 -or $CanaryWeight -ne 0) {
    Fail (
        "Final validation did not record the required 100% Stable / 0% Canary state. " +
        "Observed Stable=$StableWeight% Canary=$CanaryWeight%."
    )
}

if ($Scenario -eq "ALL_STAGES_PROMOTE") {
    if ($Decision -ne "PROMOTE") {
        Fail "Promote scenario reached final validation with AI decision '$Decision'."
    }
}
elseif ($Scenario -eq "ROLLBACK_AT_50") {
    if ($Decision -ne "ROLLBACK") {
        Fail "Rollback scenario reached final validation with AI decision '$Decision'."
    }
}
else {
    Fail "Unsupported scenario '$Scenario'."
}

# ============================================================
# PRESERVE THE LAST AI ANALYSIS, UPDATE ONLY EXECUTED TRAFFIC
# ============================================================
#
# ai_metrics.prom is the last AI checkpoint payload. It contains
# the AI decision, risk, confidence, findings-derived metrics and
# the checkpoint's multi-window live telemetry history summary.
#
# Final Validation occurs AFTER that AI-approved action has been
# executed. Therefore we keep every AI intelligence metric exactly
# as produced by the decision engine and only replace the traffic
# gauges with the verified final runtime state (100/0).
# ============================================================

$MetricText = Get-Content $CheckpointMetricsPath -Raw

$StablePattern = '(?m)^ai_stable_weight_percent\s+[-+0-9.eE]+\s*$'
$CanaryPattern = '(?m)^ai_canary_weight_percent\s+[-+0-9.eE]+\s*$'

if (-not [regex]::IsMatch($MetricText, $StablePattern)) {
    Fail "ai_stable_weight_percent was not found in the AI metric payload."
}

if (-not [regex]::IsMatch($MetricText, $CanaryPattern)) {
    Fail "ai_canary_weight_percent was not found in the AI metric payload."
}

$MetricText = [regex]::Replace(
    $MetricText,
    $StablePattern,
    "ai_stable_weight_percent $StableWeight"
)

$MetricText = [regex]::Replace(
    $MetricText,
    $CanaryPattern,
    "ai_canary_weight_percent $CanaryWeight"
)

# Refresh only the publication timestamp. The AI analysis timestamp
# remains untouched because no new AI analysis is being invented here.
$PublishedTimestamp = [DateTimeOffset]::UtcNow.ToUnixTimeSeconds()
$PublishedPattern = '(?m)^ai_metrics_published_timestamp_seconds\s+[-+0-9.eE]+\s*$'

if ([regex]::IsMatch($MetricText, $PublishedPattern)) {
    $MetricText = [regex]::Replace(
        $MetricText,
        $PublishedPattern,
        "ai_metrics_published_timestamp_seconds $PublishedTimestamp"
    )
}

# Store a separate final-state payload so checkpoint evidence remains intact.
$MetricText | Set-Content -Path $FinalMetricsPath -Encoding UTF8

Write-Host ""
Write-Host "[PASS] Final dashboard payload prepared:"
Write-Host "       $FinalMetricsPath"
Write-Host "[PASS] AI decision/risk metrics preserved from the last AI checkpoint."
Write-Host "[PASS] Final runtime traffic overridden to 100% Stable / 0% Canary."

# ============================================================
# PUSH FINAL PAYLOAD
# ============================================================

try {
    Invoke-WebRequest `
        -Uri "$PushgatewayUrl/-/ready" `
        -UseBasicParsing `
        -TimeoutSec 10 | Out-Null
}
catch {
    Fail "Pushgateway is not reachable at $PushgatewayUrl."
}

try {
    $BodyBytes = [System.Text.Encoding]::UTF8.GetBytes($MetricText)

    $Response = Invoke-WebRequest `
        -Uri $PushUrl `
        -Method Put `
        -Body $BodyBytes `
        -ContentType "text/plain; version=0.0.4" `
        -UseBasicParsing `
        -TimeoutSec 15

    Write-Host "[PASS] Pushgateway accepted final dashboard state. HTTP $($Response.StatusCode)"
}
catch {
    Fail "Unable to publish final dashboard state to Pushgateway: $($_.Exception.Message)"
}

# ============================================================
# VERIFY PROMETHEUS / GRAFANA SOURCE DATA
# ============================================================

Write-Section "VERIFY FINAL DASHBOARD METRICS IN PROMETHEUS"

$ExpectedDecisionValue = 1.0
$WaitSeconds = 45
$PollSeconds = 5
$Elapsed = 0
$Verified = $false

$StableQuery = 'ai_stable_weight_percent{job="ai-canary-intelligence"}'
$CanaryQuery = 'ai_canary_weight_percent{job="ai-canary-intelligence"}'
$DecisionQuery = 'ai_decision_state{job="ai-canary-intelligence",decision="' + $Decision + '"}'

$LastStable = $null
$LastCanary = $null
$LastDecision = $null

while ($Elapsed -le $WaitSeconds) {
    $LastStable = Get-PrometheusScalar -PrometheusUrl $PrometheusUrl -Query $StableQuery
    $LastCanary = Get-PrometheusScalar -PrometheusUrl $PrometheusUrl -Query $CanaryQuery
    $LastDecision = Get-PrometheusScalar -PrometheusUrl $PrometheusUrl -Query $DecisionQuery

    if (
        $null -ne $LastStable -and
        $null -ne $LastCanary -and
        $null -ne $LastDecision -and
        [math]::Abs($LastStable - 100.0) -lt 0.01 -and
        [math]::Abs($LastCanary - 0.0) -lt 0.01 -and
        [math]::Abs($LastDecision - $ExpectedDecisionValue) -lt 0.01
    ) {
        $Verified = $true
        break
    }

    Write-Host (
        "[INFO] Waiting for final dashboard state in Prometheus... " +
        "Stable=$LastStable Canary=$LastCanary Decision=$LastDecision Elapsed=${Elapsed}s"
    )

    Start-Sleep -Seconds $PollSeconds
    $Elapsed += $PollSeconds
}

if (-not $Verified) {
    Fail (
        "Prometheus did not expose the verified final dashboard state within $WaitSeconds seconds. " +
        "Expected Stable=100 Canary=0 Decision=$Decision(1); " +
        "observed Stable=$LastStable Canary=$LastCanary Decision=$LastDecision."
    )
}

Write-Host "[PASS] Prometheus now exposes the verified final dashboard state."
Write-Host "       Stable Traffic : 100%"
Write-Host "       Canary Traffic : 0%"
Write-Host "       AI Decision    : $Decision"
Write-Host "       Decision Source: $DecisionSource"
Write-Host ""
Write-Host "Grafana refreshes every $($Config.monitoring.grafana_refresh_seconds) second(s)."
Write-Host "The final dashboard hold will now show the executed deployment state."
Write-Host ""

exit 0
