param(
    [string]$ProjectRoot = ""
)

$ErrorActionPreference = "Stop"

function Fail {
    param([string]$Message)

    Write-Host ""
    Write-Host "[FAIL] $Message"
    exit 1
}

function HtmlEncode {
    param($Value)

    if ($null -eq $Value) {
        return "N/A"
    }

    return [System.Net.WebUtility]::HtmlEncode(
        [string]$Value
    )
}

function Format-Duration {
    param($Seconds)

    if ($null -eq $Seconds) {
        return "N/A"
    }

    try {
        $Value = [double]$Seconds
    }
    catch {
        return "N/A"
    }

    $Minutes = [math]::Floor($Value / 60)
    $Remaining = $Value % 60

    if ($Minutes -gt 0) {
        return "$Minutes min $([math]::Round($Remaining, 1)) sec"
    }

    return "$([math]::Round($Remaining, 1)) sec"
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

$RuntimeDir = Join-Path $ProjectRoot "runtime"
$ReportsDir = Join-Path $RuntimeDir "reports"

$ScenarioStatePath = Join-Path $RuntimeDir "scenario-state.json"
$TimelinePath = Join-Path $RuntimeDir "pipeline-timeline.json"
$FinalValidationPath = Join-Path $RuntimeDir "final-validation.json"

$DetailedReportPath = Join-Path `
    $ReportsDir `
    "ai-canary-deployment-report.html"

$EmailBodyPath = Join-Path `
    $ReportsDir `
    "ai-canary-email-summary.html"

$EmailMetadataPath = Join-Path `
    $ReportsDir `
    "email-metadata.json"

New-Item `
    -ItemType Directory `
    -Force `
    -Path $ReportsDir | Out-Null

# ============================================================
# REQUIRED EVIDENCE
# ============================================================

foreach ($Required in @(
    $ScenarioStatePath,
    $FinalValidationPath,
    $DetailedReportPath
)) {
    if (-not (Test-Path $Required)) {
        Fail "Required report evidence not found: $Required"
    }
}

$ScenarioState = Get-Content `
    $ScenarioStatePath `
    -Raw |
    ConvertFrom-Json

$FinalValidation = Get-Content `
    $FinalValidationPath `
    -Raw |
    ConvertFrom-Json

$Timeline = $null

if (Test-Path $TimelinePath) {
    $Timeline = Get-Content `
        $TimelinePath `
        -Raw |
        ConvertFrom-Json
}

# ============================================================
# DISCOVER LATEST CHECKPOINT DECISION
# ============================================================

$StagesDir = Join-Path $RuntimeDir "stages"

$LatestCheckpointDir = $null
$DecisionData = $null

if (Test-Path $StagesDir) {

    $CheckpointDirs = @(
        Get-ChildItem `
            -Path $StagesDir `
            -Directory `
            -Filter "checkpoint-*" |
        Where-Object {
            $_.Name -match '^checkpoint-(\d+)$'
        } |
        Sort-Object {
            [int](
                [regex]::Match(
                    $_.Name,
                    '\d+'
                ).Value
            )
        }
    )

    if ($CheckpointDirs.Count -gt 0) {
        $LatestCheckpointDir = $CheckpointDirs[-1]

        $DecisionPath = Join-Path `
            $LatestCheckpointDir.FullName `
            "ai_decision.json"

        if (Test-Path $DecisionPath) {
            $DecisionData = Get-Content `
                $DecisionPath `
                -Raw |
                ConvertFrom-Json
        }
    }
}

# ============================================================
# RESOLVE SUMMARY VALUES
# ============================================================

$Scenario = [string]$ScenarioState.scenario

$OriginalStable = [string]$ScenarioState.stable_version
$CandidateVersion = [string]$ScenarioState.canary_version

$FinalDecision = [string]$ScenarioState.last_ai_decision
$DecisionSource = [string]$ScenarioState.last_decision_source
$RiskScore = $ScenarioState.last_risk_score
$RiskLevel = [string]$ScenarioState.last_risk_level

$Confidence = $null
$AiModel = "N/A"
$AnalysisMode = "N/A"

if ($null -ne $DecisionData) {

    if (
        $null -ne $DecisionData.final_assessment -and
        -not [string]::IsNullOrWhiteSpace(
            [string]$DecisionData.final_assessment.decision
        )
    ) {
        $FinalDecision = [string]$DecisionData.final_assessment.decision
    }

    if (
        $null -ne $DecisionData.final_assessment -and
        -not [string]::IsNullOrWhiteSpace(
            [string]$DecisionData.final_assessment.decision_source
        )
    ) {
        $DecisionSource = [string]$DecisionData.final_assessment.decision_source
    }

    if ($null -ne $DecisionData.final_assessment.risk_score) {
        $RiskScore = $DecisionData.final_assessment.risk_score
    }

    if (
        -not [string]::IsNullOrWhiteSpace(
            [string]$DecisionData.final_assessment.risk_level
        )
    ) {
        $RiskLevel = [string]$DecisionData.final_assessment.risk_level
    }

    if ($null -ne $DecisionData.ai_analysis) {

        $Confidence = $DecisionData.ai_analysis.confidence

        if ($null -eq $Confidence) {
            $Confidence = $DecisionData.ai_analysis.confidence_percent
        }

        if (
            -not [string]::IsNullOrWhiteSpace(
                [string]$DecisionData.ai_analysis.model
            )
        ) {
            $AiModel = [string]$DecisionData.ai_analysis.model
        }

        if (
            -not [string]::IsNullOrWhiteSpace(
                [string]$DecisionData.ai_analysis.analysis_mode
            )
        ) {
            $AnalysisMode = [string]$DecisionData.ai_analysis.analysis_mode
        }
    }

    if (
        $AiModel -eq "N/A" -and
        $null -ne $DecisionData.architecture -and
        -not [string]::IsNullOrWhiteSpace(
            [string]$DecisionData.architecture.ai_model
        )
    ) {
        $AiModel = [string]$DecisionData.architecture.ai_model
    }
}

if ([string]::IsNullOrWhiteSpace($FinalDecision)) {
    $FinalDecision = "UNKNOWN"
}

if ([string]::IsNullOrWhiteSpace($DecisionSource)) {
    $DecisionSource = "UNKNOWN"
}

if ([string]::IsNullOrWhiteSpace($RiskLevel)) {
    $RiskLevel = "UNKNOWN"
}

$FinalDecision = $FinalDecision.ToUpperInvariant()
$RiskLevel = $RiskLevel.ToUpperInvariant()

$ActiveVersion = [string]$FinalValidation.active_version
$RolloutStatus = [string]$FinalValidation.rollout_status
$ApplicationHealth = [string]$FinalValidation.application_health
$Outcome = [string]$FinalValidation.outcome

$StableWeight = $FinalValidation.stable_weight
$CanaryWeight = $FinalValidation.canary_weight

$ReadyPods = $FinalValidation.ready_pods
$DesiredPods = $FinalValidation.desired_pods

$TotalDuration = "N/A"

if (
    $null -ne $Timeline -and
    $null -ne $Timeline.total_duration_sec
) {
    $TotalDuration = Format-Duration `
        $Timeline.total_duration_sec
}

$BuildNumber = $env:BUILD_NUMBER

if ([string]::IsNullOrWhiteSpace($BuildNumber)) {
    $BuildNumber = "LOCAL"
}

# ============================================================
# VISUAL STATUS
# ============================================================

switch ($FinalDecision) {
    "PROMOTE" {
        $DecisionColor = "#16a34a"
        $DecisionBackground = "#dcfce7"
    }

    "ROLLBACK" {
        $DecisionColor = "#dc2626"
        $DecisionBackground = "#fee2e2"
    }

    "PAUSE" {
        $DecisionColor = "#d97706"
        $DecisionBackground = "#fef3c7"
    }

    default {
        $DecisionColor = "#475569"
        $DecisionBackground = "#e2e8f0"
    }
}

# ============================================================
# SUBJECT
# ============================================================

$Subject = (
    "AI Canary Deployment Report | " +
    "$Scenario | Build #$BuildNumber | " +
    "$FinalDecision"
)

# ============================================================
# HTML EMAIL BODY
# ============================================================

$EmailBody = @"
<!DOCTYPE html>
<html>
<head>
<meta charset="UTF-8">
</head>

<body style="
    margin:0;
    padding:0;
    background:#f4f7fb;
    font-family:Segoe UI,Arial,sans-serif;
    color:#1e293b;
">

<table width="100%" cellspacing="0" cellpadding="0" border="0">
<tr>
<td align="center" style="padding:28px 14px;">

<table width="760" cellspacing="0" cellpadding="0" border="0"
       style="
       width:760px;
       max-width:100%;
       background:#ffffff;
       border-radius:14px;
       overflow:hidden;
       border:1px solid #dbe4ee;
       ">

<tr>
<td style="
    padding:28px 32px;
    background:#0f2744;
    color:#ffffff;
">
    <div style="
        font-size:13px;
        color:#9fc4e8;
        text-transform:uppercase;
        letter-spacing:1px;
        margin-bottom:6px;
    ">
        AI-Enabled Release Intelligence
    </div>

    <div style="
        font-size:27px;
        font-weight:700;
    ">
        Canary Deployment Execution Report
    </div>

    <div style="
        margin-top:8px;
        color:#c8d9eb;
        font-size:13px;
    ">
        Build #$(HtmlEncode $BuildNumber) •
        Scenario $(HtmlEncode $Scenario)
    </div>
</td>
</tr>

<tr>
<td style="padding:26px 32px;">

<table width="100%" cellspacing="0" cellpadding="0"
       style="
       margin-bottom:22px;
       background:$DecisionBackground;
       border-radius:10px;
       border:1px solid $DecisionColor;
       ">
<tr>
<td style="padding:18px 20px;">
    <div style="
        color:#64748b;
        font-size:11px;
        font-weight:700;
        letter-spacing:.6px;
        text-transform:uppercase;
    ">
        AI Final Decision
    </div>

    <div style="
        color:$DecisionColor;
        font-size:27px;
        font-weight:750;
        margin-top:3px;
    ">
        $(HtmlEncode $FinalDecision)
    </div>
</td>

<td align="right" style="
    padding:18px 20px;
    color:#334155;
">
    <div style="font-size:12px;">
        Risk Level
    </div>

    <div style="
        font-size:19px;
        font-weight:700;
    ">
        $(HtmlEncode $RiskLevel)
    </div>
</td>
</tr>
</table>

<table width="100%" cellspacing="0" cellpadding="8"
       style="
       border-collapse:collapse;
       font-size:13px;
       ">

<tr>
<td style="
    width:34%;
    color:#64748b;
    border-bottom:1px solid #e2e8f0;
">
    Execution Duration
</td>
<td style="
    font-weight:600;
    border-bottom:1px solid #e2e8f0;
">
    $(HtmlEncode $TotalDuration)
</td>
</tr>

<tr>
<td style="
    color:#64748b;
    border-bottom:1px solid #e2e8f0;
">
    AI Risk Score
</td>
<td style="
    font-weight:600;
    border-bottom:1px solid #e2e8f0;
">
    $(HtmlEncode $RiskScore)/100
</td>
</tr>

<tr>
<td style="
    color:#64748b;
    border-bottom:1px solid #e2e8f0;
">
    AI Confidence
</td>
<td style="
    font-weight:600;
    border-bottom:1px solid #e2e8f0;
">
    $(HtmlEncode $Confidence)%
    <span style="
        color:#94a3b8;
        font-size:11px;
    ">
        (model self-reported)
    </span>
</td>
</tr>

<tr>
<td style="
    color:#64748b;
    border-bottom:1px solid #e2e8f0;
">
    Decision Source
</td>
<td style="
    font-weight:600;
    border-bottom:1px solid #e2e8f0;
">
    $(HtmlEncode $DecisionSource)
</td>
</tr>

<tr>
<td style="
    color:#64748b;
    border-bottom:1px solid #e2e8f0;
">
    Final Active Version
</td>
<td style="
    font-weight:600;
    border-bottom:1px solid #e2e8f0;
">
    $(HtmlEncode $ActiveVersion)
</td>
</tr>

<tr>
<td style="
    color:#64748b;
    border-bottom:1px solid #e2e8f0;
">
    Final Traffic State
</td>
<td style="
    font-weight:600;
    border-bottom:1px solid #e2e8f0;
">
    Stable $(HtmlEncode $StableWeight)% /
    Canary $(HtmlEncode $CanaryWeight)%
</td>
</tr>

<tr>
<td style="
    color:#64748b;
    border-bottom:1px solid #e2e8f0;
">
    Rollout State
</td>
<td style="
    font-weight:600;
    border-bottom:1px solid #e2e8f0;
">
    $(HtmlEncode $RolloutStatus)
</td>
</tr>

<tr>
<td style="
    color:#64748b;
    border-bottom:1px solid #e2e8f0;
">
    Application Health
</td>
<td style="
    font-weight:600;
    border-bottom:1px solid #e2e8f0;
">
    $(HtmlEncode $ApplicationHealth)
</td>
</tr>

<tr>
<td style="
    color:#64748b;
    border-bottom:1px solid #e2e8f0;
">
    Pod Readiness
</td>
<td style="
    font-weight:600;
    border-bottom:1px solid #e2e8f0;
">
    $(HtmlEncode $ReadyPods)/$(HtmlEncode $DesiredPods)
</td>
</tr>

<tr>
<td style="color:#64748b;">
    Deployment Outcome
</td>
<td style="font-weight:600;">
    $(HtmlEncode $Outcome)
</td>
</tr>

</table>

<div style="
    margin-top:24px;
    padding:17px 19px;
    background:#f8fafc;
    border-left:4px solid #2563eb;
    border-radius:7px;
    font-size:13px;
    line-height:1.7;
">
    The attached detailed report includes the complete
    multi-window live telemetry history, Stable-versus-Canary
    latency and error analysis, infrastructure utilization,
    AI risk assessment, AI findings, decision reasoning,
    pipeline timings, graphs, and final deployment validation.
</div>

<div style="
    margin-top:24px;
    font-size:13px;
    color:#475569;
">
    <strong>Attachment:</strong><br>
    ai-canary-deployment-report.html
</div>

</td>
</tr>

<tr>
<td style="
    padding:17px 32px;
    background:#f8fafc;
    border-top:1px solid #e2e8f0;
    color:#94a3b8;
    font-size:11px;
    text-align:center;
">
    AI-Enabled Canary Deployment POC •
    Automated Jenkins Report Delivery
</td>
</tr>

</table>

</td>
</tr>
</table>

</body>
</html>
"@

# ============================================================
# WRITE EMAIL BODY
# ============================================================

$EmailBody |
    Set-Content `
        -Path $EmailBodyPath `
        -Encoding UTF8

# ============================================================
# WRITE EMAIL METADATA
# ============================================================

$Metadata = [ordered]@{
    generated_at = [DateTimeOffset]::Now.ToString("o")

    subject = $Subject

    build_number = $BuildNumber
    scenario = $Scenario

    final_decision = $FinalDecision
    risk_level = $RiskLevel
    risk_score = $RiskScore
    confidence = $Confidence
    decision_source = $DecisionSource

    final_active_version = $ActiveVersion
    rollout_status = $RolloutStatus
    application_health = $ApplicationHealth
    outcome = $Outcome

    email_body = "runtime/reports/ai-canary-email-summary.html"

    attachment = "runtime/reports/ai-canary-deployment-report.html"
}

$Metadata |
    ConvertTo-Json -Depth 8 |
    Set-Content `
        -Path $EmailMetadataPath `
        -Encoding UTF8

# ============================================================
# VALIDATE OUTPUT
# ============================================================

if (-not (Test-Path $EmailBodyPath)) {
    Fail "Email summary HTML was not created."
}

if (-not (Test-Path $EmailMetadataPath)) {
    Fail "Email metadata JSON was not created."
}

Write-Host ""
Write-Host "============================================================"
Write-Host " REPORT EMAIL READY"
Write-Host "============================================================"
Write-Host ""
Write-Host "Subject    : $Subject"
Write-Host "Email Body : $EmailBodyPath"
Write-Host "Attachment : $DetailedReportPath"
Write-Host ""
Write-Host "[PASS] Jenkins email content prepared."
Write-Host ""

exit 0
