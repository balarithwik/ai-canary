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

function Run-Python {
    param(
        [string]$ScriptName,
        [string]$FailureMessage
    )

    & python $ScriptName

    if ($LASTEXITCODE -ne 0) {
        Fail $FailureMessage
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
$ScenarioStatePath = Join-Path $ProjectRoot "runtime\scenario-state.json"
$AiDir = Join-Path $ProjectRoot "ai-engine"

$RiskContextScript = Join-Path $AiDir "risk_context.py"
$RiskEngineScript = Join-Path $AiDir "ai_risk_engine.py"
$PublisherScript = Join-Path $AiDir "ai_metrics_publisher.py"

$ContextFile = Join-Path $AiDir "ai_risk_context.json"
$DecisionFile = Join-Path $AiDir "ai_decision.json"
$MetricsFile = Join-Path $AiDir "ai_metrics.prom"

foreach ($required in @(
    $ConfigPath,
    $ScenarioStatePath,
    $RiskContextScript,
    $RiskEngineScript,
    $PublisherScript
)) {
    if (-not (Test-Path $required)) {
        Fail "Required file not found: $required"
    }
}

$Config = Get-Content $ConfigPath -Raw | ConvertFrom-Json
$ScenarioState = Get-Content $ScenarioStatePath -Raw | ConvertFrom-Json

$ClusterName = [string]$Config.project.cluster_name
$Namespace = [string]$Config.project.namespace
$RolloutName = [string]$Config.project.rollout_name
$ExpectedContext = "kind-$ClusterName"

$RequiredModel = [string]$Config.ai.model
$WindowCount = [int]$Config.ai.observation_windows
$WindowInterval = [int]$Config.ai.observation_interval_seconds
$PushgatewayUrl = [string]$Config.monitoring.pushgateway_url
$PrometheusPort = [int]$Config.ports.prometheus
$PrometheusUrl = "http://localhost:$PrometheusPort"

$Checkpoint = [int]$ScenarioState.current_checkpoint
$ExpectedStable = [string]$ScenarioState.stable_version
$ExpectedCanary = [string]$ScenarioState.canary_version

Write-Section "AI CHECKPOINT ANALYSIS"

Write-Host "Scenario       : $($ScenarioState.scenario)"
Write-Host "Checkpoint     : $Checkpoint%"
Write-Host "Stable Version : $ExpectedStable"
Write-Host "Canary Version : $ExpectedCanary"
Write-Host "AI Model       : $RequiredModel"
Write-Host "Live Windows   : $WindowCount"
Write-Host "Window Gap     : $WindowInterval second(s)"
Write-Host ""
Write-Host "AI is the normal deployment decision authority."
Write-Host "Deterministic logic is supporting evidence plus hard safety governance only."

# ============================================================
# SAFETY - LIVE ROLLOUT MUST STILL MATCH THE CHECKPOINT
# ============================================================

$CurrentContext = kubectl config current-context

if ($LASTEXITCODE -ne 0) {
    Fail "Unable to read kubectl current context."
}

if ($CurrentContext.Trim() -ne $ExpectedContext) {
    Fail (
        "Refusing AI analysis because kubectl context is " +
        "'$CurrentContext'. Expected '$ExpectedContext'."
    )
}

$RolloutRaw = kubectl get rollout $RolloutName `
    -n $Namespace `
    -o json

if ($LASTEXITCODE -ne 0) {
    Fail "Unable to read current Argo Rollout."
}

$Rollout = $RolloutRaw | ConvertFrom-Json

$PauseCount = @($Rollout.status.pauseConditions).Count

if ($PauseCount -lt 1) {
    Fail "Rollout is not paused at an analysis checkpoint."
}

$PodsRaw = kubectl get pods `
    -n $Namespace `
    -l "app=$RolloutName" `
    -o json

if ($LASTEXITCODE -ne 0) {
    Fail "Unable to discover Stable/Canary pods."
}

$Pods = $PodsRaw | ConvertFrom-Json

$StablePods = @(
    $Pods.items |
    Where-Object {
        $_.metadata.labels.version -eq $ExpectedStable -and
        $_.status.phase -eq "Running"
    }
)

$CanaryPods = @(
    $Pods.items |
    Where-Object {
        $_.metadata.labels.version -eq $ExpectedCanary -and
        $_.status.phase -eq "Running"
    }
)

if ($StablePods.Count -lt 1) {
    Fail "No Running Stable pods found for '$ExpectedStable'."
}

if ($CanaryPods.Count -lt 1) {
    Fail "No Running Canary pods found for '$ExpectedCanary'."
}

Write-Host "[PASS] Live rollout is paused with both Stable and Canary active."

# ============================================================
# VERIFY REQUIRED AI MODEL IS REALLY CONFIGURED IN ENGINE
#
# This prevents an older Phi3/Qwen model file from silently
# running inside Jenkins after the project reorganization.
# ============================================================

$RiskEngineText = Get-Content $RiskEngineScript -Raw
$PublisherText = Get-Content $PublisherScript -Raw

if ($RiskEngineText -notmatch [regex]::Escape($RequiredModel)) {
    Fail (
        "ai_risk_engine.py does not reference configured model '$RequiredModel'. " +
        "Do not run the checkpoint with an older AI engine."
    )
}

if ($PublisherText -notmatch [regex]::Escape($RequiredModel)) {
    Fail (
        "ai_metrics_publisher.py does not reference configured model '$RequiredModel'. " +
        "Dashboard publishing could report the wrong model."
    )
}

Write-Host "[PASS] AI engine and dashboard publisher match configured model '$RequiredModel'."

# ============================================================
# VERIFY OLLAMA MODEL/API
# ============================================================

try {
    $OllamaUrl = ([string]$Config.ai.ollama_url).TrimEnd("/")

    Invoke-RestMethod `
        -Uri "$OllamaUrl/api/tags" `
        -Method Get `
        -TimeoutSec 10 | Out-Null
}
catch {
    Fail "Ollama API is not reachable at $($Config.ai.ollama_url)."
}

$OllamaList = ollama list

if ($LASTEXITCODE -ne 0) {
    Fail "Unable to list Ollama models."
}

if (($OllamaList -join "`n") -notmatch [regex]::Escape($RequiredModel)) {
    Fail "Required Ollama model '$RequiredModel' is not installed."
}

Write-Host "[PASS] Ollama and required AI model are available."

# ============================================================
# CLEAR STALE AI CYCLE OUTPUTS
# ============================================================

Write-Section "RESET CHECKPOINT AI OUTPUT"

foreach ($file in @(
    $ContextFile,
    $DecisionFile,
    $MetricsFile
)) {
    if (Test-Path $file) {
        Remove-Item $file -Force
    }
}

Write-Host "[PASS] Previous AI cycle output cleared."

# ============================================================
# RUN FROM ai-engine DIRECTORY
#
# risk_context.py currently writes ai_risk_context.json using a
# relative path, so running from this directory is intentional.
# ============================================================

Push-Location $AiDir

try {

    # ========================================================
    # 1. FOUR LIVE OBSERVATION WINDOWS
    # ========================================================

    Write-Section "COLLECT MULTI-WINDOW LIVE TELEMETRY"

    Write-Host (
        "Collecting $WindowCount live windows with " +
        "$WindowInterval-second spacing."
    )

    Run-Python `
        -ScriptName "risk_context.py" `
        -FailureMessage "risk_context.py returned a failure."

    if (-not (Test-Path $ContextFile)) {
        Fail (
            "risk_context.py completed without producing " +
            "ai_risk_context.json."
        )
    }

    $ContextData = Get-Content $ContextFile -Raw | ConvertFrom-Json

    $ContextStable = [string]$ContextData.deployment.stable_version
    $ContextCanary = [string]$ContextData.deployment.canary_version

    if (
        $ContextStable -ne $ExpectedStable -or
        $ContextCanary -ne $ExpectedCanary
    ) {
        Fail (
            "Telemetry context versions do not match the live scenario. " +
            "Expected Stable='$ExpectedStable' Canary='$ExpectedCanary'; " +
            "Context Stable='$ContextStable' Canary='$ContextCanary'."
        )
    }

    $ValidWindows = [int]$ContextData.data_quality.valid_windows
    $TotalWindows = [int]$ContextData.data_quality.total_windows

    Write-Host ""
    Write-Host "[PASS] AI context created."
    Write-Host "       Valid Windows : $ValidWindows/$TotalWindows"
    Write-Host "       Aggregation   : $($ContextData.data_quality.aggregation_method)"
    Write-Host "       Overall Trend : $($ContextData.overall_trend)"

    # ========================================================
    # 2. AI PRIMARY DECISION ENGINE
    # ========================================================

    Write-Section "RUN AI PRIMARY DECISION ENGINE"

    Run-Python `
        -ScriptName "ai_risk_engine.py" `
        -FailureMessage "ai_risk_engine.py returned a failure."

    if (-not (Test-Path $DecisionFile)) {
        Fail (
            "AI engine completed without producing ai_decision.json. " +
            "No Kubernetes action is allowed."
        )
    }

    $DecisionData = Get-Content $DecisionFile -Raw | ConvertFrom-Json

    $Final = $DecisionData.final_assessment
    $AiAnalysis = $DecisionData.ai_analysis
    $Architecture = $DecisionData.architecture

    if ($null -eq $Final) {
        Fail "ai_decision.json does not contain final_assessment."
    }

    $Decision = ([string]$Final.decision).ToUpperInvariant()
    $DecisionSource = [string]$Final.decision_source
    $RiskScore = $Final.risk_score
    $RiskLevel = [string]$Final.risk_level

    if ($Decision -notin @(
        "PROMOTE",
        "PAUSE",
        "ROLLBACK"
    )) {
        Fail "AI engine produced invalid decision '$Decision'."
    }

    $AllowedSources = @(
        "AI_DECISION_ENGINE",
        "HARD_SAFETY_GUARDRAIL",
        "AI_UNAVAILABLE_FAILSAFE"
    )

    if ($DecisionSource -notin $AllowedSources) {
        Fail (
            "Unexpected decision source '$DecisionSource'. " +
            "This indicates an older decision architecture is still in use."
        )
    }

    $ModelUsed = [string]$AiAnalysis.model

    if ([string]::IsNullOrWhiteSpace($ModelUsed)) {
        $ModelUsed = [string]$Architecture.ai_model
    }

    if (
        -not [string]::IsNullOrWhiteSpace($ModelUsed) -and
        $ModelUsed -ne $RequiredModel
    ) {
        Fail (
            "AI decision reports model '$ModelUsed' but pipeline configuration " +
            "requires '$RequiredModel'."
        )
    }

    $Confidence = $AiAnalysis.confidence

    if ($null -eq $Confidence) {
        $Confidence = $AiAnalysis.confidence_percent
    }

    # ========================================================
    # 3. PUBLISH DASHBOARD METRICS
    # ========================================================

    Write-Section "PUBLISH AI DASHBOARD METRICS"

    Run-Python `
        -ScriptName "ai_metrics_publisher.py" `
        -FailureMessage "ai_metrics_publisher.py returned a failure."

    if (-not (Test-Path $MetricsFile)) {
        Fail (
            "AI metrics publisher completed without creating " +
            "ai_metrics.prom."
        )
    }

    try {
        Invoke-WebRequest `
            -Uri "$PushgatewayUrl/-/ready" `
            -UseBasicParsing `
            -TimeoutSec 10 | Out-Null
    }
    catch {
        Fail "Pushgateway is no longer reachable at $PushgatewayUrl."
    }

    # ========================================================
    # VERIFY PROMETHEUS CAN SEE THE FRESH CHECKPOINT METRICS
    #
    # Pushgateway HTTP 200 only confirms that the metrics were
    # accepted by Pushgateway. Grafana reads from Prometheus, so
    # do not mark the dashboard update as successful until
    # Prometheus has scraped the current checkpoint values.
    # ========================================================

    Write-Section "VERIFY DASHBOARD METRICS IN PROMETHEUS"

    $PrometheusWaitSeconds = 45
    $PrometheusPollSeconds = 3
    $PrometheusElapsed = 0
    $MetricsVisible = $false
    $LastObservedWeight = $null

    while ($PrometheusElapsed -lt $PrometheusWaitSeconds) {

        try {
            $WeightQuery = 'ai_canary_weight_percent{job="ai-canary-intelligence"}'
            $DecisionQuery = 'ai_decision_code{job="ai-canary-intelligence"}'

            $WeightUri = (
                "$PrometheusUrl/api/v1/query?query=" +
                [uri]::EscapeDataString($WeightQuery)
            )

            $DecisionUri = (
                "$PrometheusUrl/api/v1/query?query=" +
                [uri]::EscapeDataString($DecisionQuery)
            )

            $WeightResponse = Invoke-RestMethod `
                -Uri $WeightUri `
                -Method Get `
                -TimeoutSec 10

            $DecisionResponse = Invoke-RestMethod `
                -Uri $DecisionUri `
                -Method Get `
                -TimeoutSec 10

            $WeightResults = @(
                $WeightResponse.data.result
            )

            $DecisionResults = @(
                $DecisionResponse.data.result
            )

            if ($WeightResults.Count -gt 0) {
                $LastObservedWeight = [double]$WeightResults[0].value[1]
            }

            if (
                $WeightResults.Count -gt 0 -and
                $DecisionResults.Count -gt 0 -and
                $null -ne $LastObservedWeight -and
                [math]::Abs($LastObservedWeight - $Checkpoint) -lt 0.01
            ) {
                $MetricsVisible = $true
                break
            }
        }
        catch {
            # Prometheus may be between scrapes. Keep polling until timeout.
        }

        Write-Host (
            "[INFO] Waiting for Prometheus to expose dashboard metrics " +
            "for checkpoint $Checkpoint%... Elapsed=${PrometheusElapsed}s"
        )

        Start-Sleep -Seconds $PrometheusPollSeconds
        $PrometheusElapsed += $PrometheusPollSeconds
    }

    if (-not $MetricsVisible) {

        $ObservedText = "NONE"

        if ($null -ne $LastObservedWeight) {
            $ObservedText = "$LastObservedWeight%"
        }

        Fail (
            "AI metrics reached Pushgateway, but Prometheus did not expose " +
            "the current checkpoint within $PrometheusWaitSeconds seconds. " +
            "Expected Canary weight: $Checkpoint%. " +
            "Last observed: $ObservedText. " +
            "Check Pushgateway ServiceMonitor honorLabels and scrape status."
        )
    }

    Write-Host "[PASS] Prometheus has current AI dashboard metrics."
    Write-Host "       Canary Weight : $LastObservedWeight%"
    Write-Host "       Checkpoint    : $Checkpoint%"
    Write-Host "       Dashboard Job : ai-canary-intelligence"
    Write-Host "[PASS] AI decision metrics are available to the existing Grafana dashboard."

}
finally {
    Pop-Location
}

# ============================================================
# SNAPSHOT CHECKPOINT ARTIFACTS
# ============================================================

Write-Section "SAVE CHECKPOINT EVIDENCE"

$CheckpointDir = Join-Path `
    $ProjectRoot `
    "runtime\stages\checkpoint-$Checkpoint"

New-Item `
    -ItemType Directory `
    -Force `
    -Path $CheckpointDir | Out-Null

Copy-Item `
    $ContextFile `
    (Join-Path $CheckpointDir "ai_risk_context.json") `
    -Force

Copy-Item `
    $DecisionFile `
    (Join-Path $CheckpointDir "ai_decision.json") `
    -Force

Copy-Item `
    $MetricsFile `
    (Join-Path $CheckpointDir "ai_metrics.prom") `
    -Force

$ScenarioState |
    Add-Member `
        -NotePropertyName last_ai_decision `
        -NotePropertyValue $Decision `
        -Force

$ScenarioState |
    Add-Member `
        -NotePropertyName last_decision_source `
        -NotePropertyValue $DecisionSource `
        -Force

$ScenarioState |
    Add-Member `
        -NotePropertyName last_risk_score `
        -NotePropertyValue $RiskScore `
        -Force

$ScenarioState |
    Add-Member `
        -NotePropertyName last_risk_level `
        -NotePropertyValue $RiskLevel `
        -Force

$ScenarioState |
    Add-Member `
        -NotePropertyName decision_generated_at `
        -NotePropertyValue (Get-Date).ToString("o") `
        -Force

$ScenarioState |
    Add-Member `
        -NotePropertyName decision_pending_execution `
        -NotePropertyValue $true `
        -Force

$ScenarioState |
    ConvertTo-Json -Depth 10 |
    Set-Content `
        -Path $ScenarioStatePath `
        -Encoding UTF8

Write-Host "[PASS] Checkpoint evidence saved to:"
Write-Host "       $CheckpointDir"

# ============================================================
# FINAL DISPLAY
# ============================================================

Write-Section "AI CHECKPOINT DECISION READY"

Write-Host "Checkpoint      : $Checkpoint%"
Write-Host "Decision        : $Decision"
Write-Host "Decision Source : $DecisionSource"
Write-Host "Risk Score      : $RiskScore/100"
Write-Host "Risk Level      : $RiskLevel"

if ($null -ne $Confidence) {
    Write-Host "AI Confidence   : $Confidence"
}

if (-not [string]::IsNullOrWhiteSpace($ModelUsed)) {
    Write-Host "AI Model        : $ModelUsed"
}

Write-Host ""
Write-Host "IMPORTANT: This script did NOT change Argo traffic."
Write-Host "The Rollout remains paused until the deployment controller executes this decision."
Write-Host ""

exit 0
