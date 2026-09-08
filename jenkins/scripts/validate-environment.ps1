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

function Write-Check {
    param(
        [string]$Name,
        [bool]$Passed,
        [string]$Details
    )

    if ($Passed) {
        Write-Host ("[PASS] {0,-24} {1}" -f $Name, $Details)
    }
    else {
        Write-Host ("[FAIL] {0,-24} {1}" -f $Name, $Details)
    }
}

function Resolve-CommandPath {
    param([string]$CommandName)

    $command = Get-Command $CommandName -ErrorAction SilentlyContinue

    if ($null -eq $command) {
        return $null
    }

    return $command.Source
}

function Test-Tool {
    param(
        [string]$Name,
        [string]$Command,
        [string[]]$Arguments
    )

    $resolved = Resolve-CommandPath $Command

    if (-not $resolved) {
        return @{
            Name    = $Name
            Passed  = $false
            Details = "$Command not found in PATH"
        }
    }

    try {
        $output = & $Command @Arguments 2>&1

        if ($LASTEXITCODE -ne 0) {
            return @{
                Name    = $Name
                Passed  = $false
                Details = "command failed"
            }
        }

        $text = ($output | Select-Object -First 1).ToString().Trim()

        if (-not $text) {
            $text = "available at $resolved"
        }

        return @{
            Name    = $Name
            Passed  = $true
            Details = $text
        }
    }
    catch {
        return @{
            Name    = $Name
            Passed  = $false
            Details = $_.Exception.Message
        }
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

if (-not (Test-Path $ConfigPath)) {
    throw "pipeline-config.json not found: $ConfigPath"
}

$Config = Get-Content $ConfigPath -Raw | ConvertFrom-Json

Write-Section "AI CANARY DEMO - ENVIRONMENT VALIDATION"

Write-Host "Project Root : $ProjectRoot"
Write-Host "Cluster Name : $($Config.project.cluster_name)"
Write-Host "Namespace    : $($Config.project.namespace)"
Write-Host "AI Model     : $($Config.ai.model)"

$Failures = New-Object System.Collections.Generic.List[string]

# ============================================================
# REQUIRED TOOLS
# ============================================================

$ToolChecks = @(
    @{ Name = "Git";     Command = "git";     Arguments = @("--version") },
    @{ Name = "Docker";  Command = "docker";  Arguments = @("--version") },
    @{ Name = "Kind";    Command = "kind";    Arguments = @("version") },
    @{ Name = "kubectl"; Command = "kubectl"; Arguments = @("version", "--client") },
    @{ Name = "Helm";    Command = "helm";    Arguments = @("version", "--short") },
    @{ Name = "Python";  Command = "python";  Arguments = @("--version") },
    @{ Name = "Ollama";  Command = "ollama";  Arguments = @("--version") }
)

Write-Section "TOOL VALIDATION"

foreach ($check in $ToolChecks) {
    $result = Test-Tool `
        -Name $check.Name `
        -Command $check.Command `
        -Arguments $check.Arguments

    Write-Check `
        -Name $result.Name `
        -Passed $result.Passed `
        -Details $result.Details

    if (-not $result.Passed) {
        $Failures.Add($result.Name)
    }
}

# ============================================================
# DOCKER ENGINE
# ============================================================

try {
    $dockerServer = docker info --format "{{.ServerVersion}}" 2>&1

    if ($LASTEXITCODE -eq 0 -and $dockerServer) {
        Write-Check `
            -Name "Docker Engine" `
            -Passed $true `
            -Details "running - server $dockerServer"
    }
    else {
        Write-Check `
            -Name "Docker Engine" `
            -Passed $false `
            -Details "Docker Desktop/Engine is not responding"

        $Failures.Add("Docker Engine")
    }
}
catch {
    Write-Check `
        -Name "Docker Engine" `
        -Passed $false `
        -Details $_.Exception.Message

    $Failures.Add("Docker Engine")
}

# ============================================================
# ARGO ROLLOUTS CLI
#
# NOTE:
# kubectl-argo-rollouts v1.10.x uses:
#   kubectl-argo-rollouts.exe version
# rather than:
#   version --client
# ============================================================

$ArgoCommand = Resolve-CommandPath "kubectl-argo-rollouts"

if (-not $ArgoCommand) {
    $ArgoCommand = Resolve-CommandPath "kubectl-argo-rollouts.exe"
}

if ($ArgoCommand) {
    try {
        $argoOutput = & $ArgoCommand version 2>&1

        if ($LASTEXITCODE -eq 0) {
            $argoText = ($argoOutput | Select-Object -First 3) -join " "
            $argoText = $argoText.Trim()

            if (-not $argoText) {
                $argoText = "available"
            }

            Write-Check `
                -Name "Argo Rollouts CLI" `
                -Passed $true `
                -Details "$argoText [$ArgoCommand]"
        }
        else {
            Write-Check `
                -Name "Argo Rollouts CLI" `
                -Passed $false `
                -Details "CLI found but 'version' command failed"

            $Failures.Add("Argo Rollouts CLI")
        }
    }
    catch {
        Write-Check `
            -Name "Argo Rollouts CLI" `
            -Passed $false `
            -Details $_.Exception.Message

        $Failures.Add("Argo Rollouts CLI")
    }
}
else {
    Write-Check `
        -Name "Argo Rollouts CLI" `
        -Passed $false `
        -Details "kubectl-argo-rollouts.exe not found in PATH"

    $Failures.Add("Argo Rollouts CLI")
}

# ============================================================
# OLLAMA MODEL + API
# ============================================================

try {
    $ollamaList = ollama list 2>&1

    if ($LASTEXITCODE -ne 0) {
        throw "ollama list failed"
    }

    $RequiredModel = [string]$Config.ai.model
    $ModelFound = $false

    foreach ($line in $ollamaList) {
        if ($line -match [regex]::Escape($RequiredModel)) {
            $ModelFound = $true
            break
        }
    }

    if ($ModelFound) {
        Write-Check `
            -Name "AI Model" `
            -Passed $true `
            -Details "$RequiredModel available"
    }
    else {
        Write-Check `
            -Name "AI Model" `
            -Passed $false `
            -Details "$RequiredModel not found"

        $Failures.Add("AI Model")
    }
}
catch {
    Write-Check `
        -Name "AI Model" `
        -Passed $false `
        -Details $_.Exception.Message

    $Failures.Add("AI Model")
}

try {
    $OllamaUrl = ([string]$Config.ai.ollama_url).TrimEnd("/")

    Invoke-RestMethod `
        -Uri "$OllamaUrl/api/tags" `
        -Method Get `
        -TimeoutSec 10 | Out-Null

    Write-Check `
        -Name "Ollama API" `
        -Passed $true `
        -Details "$OllamaUrl reachable"
}
catch {
    Write-Check `
        -Name "Ollama API" `
        -Passed $false `
        -Details "not reachable at $($Config.ai.ollama_url)"

    $Failures.Add("Ollama API")
}

# ============================================================
# REQUIRED PROJECT FILES
# ============================================================

Write-Section "PROJECT FILE VALIDATION"

$RequiredFiles = @(
    "ai-canary-config.yaml",
    "application\app.py",
    "application\Dockerfile",
    "application\requirements.txt",
    "ai-engine\discover_rollout.py",
    "ai-engine\collect_intelligence_metrics.py",
    "ai-engine\risk_context.py",
    "ai-engine\ai_risk_engine.py",
    "ai-engine\ai_metrics_publisher.py",
    "ai-engine\deployment_controller.py",
    "ai-engine\render_rollout.py",
    "kubernetes\service.yaml",
    "kubernetes\rollout-template.yaml",
    "kubernetes\servicemonitor.yaml",
    "kubernetes\ai-pushgateway-servicemonitor.yaml",
    "scenarios\promote-all.json",
    "scenarios\rollback-at-80.json",
    "jenkins\config\pipeline-config.json"
)

$MissingFiles = @()

foreach ($relativePath in $RequiredFiles) {
    $fullPath = Join-Path $ProjectRoot $relativePath

    if (-not (Test-Path $fullPath)) {
        $MissingFiles += $relativePath
    }
}

if ($MissingFiles.Count -eq 0) {
    Write-Check `
        -Name "Project Files" `
        -Passed $true `
        -Details "all required files found"
}
else {
    Write-Check `
        -Name "Project Files" `
        -Passed $false `
        -Details ("missing: " + ($MissingFiles -join ", "))

    $Failures.Add("Project Files")
}

# ============================================================
# PYTHON SYNTAX CHECKS
# ============================================================

$PythonFiles = @(
    "application\app.py",
    "ai-engine\discover_rollout.py",
    "ai-engine\collect_intelligence_metrics.py",
    "ai-engine\risk_context.py",
    "ai-engine\ai_risk_engine.py",
    "ai-engine\ai_metrics_publisher.py",
    "ai-engine\deployment_controller.py",
    "ai-engine\render_rollout.py"
)

$SyntaxFailures = @()

foreach ($relativePath in $PythonFiles) {
    $fullPath = Join-Path $ProjectRoot $relativePath

    if (Test-Path $fullPath) {
        python -m py_compile $fullPath 2>$null

        if ($LASTEXITCODE -ne 0) {
            $SyntaxFailures += $relativePath
        }
    }
}

if ($SyntaxFailures.Count -eq 0) {
    Write-Check `
        -Name "Python Syntax" `
        -Passed $true `
        -Details "validated"
}
else {
    Write-Check `
        -Name "Python Syntax" `
        -Passed $false `
        -Details ("failed: " + ($SyntaxFailures -join ", "))

    $Failures.Add("Python Syntax")
}

# ============================================================
# FINAL RESULT
# ============================================================

if ($Failures.Count -gt 0) {
    Write-Section "VALIDATION FAILED"

    Write-Host "The Jenkins demo must NOT continue."
    Write-Host ""
    Write-Host "Failed checks:"

    foreach ($failure in $Failures) {
        Write-Host " - $failure"
    }

    exit 1
}

Write-Section "VALIDATION PASSED"

Write-Host "Jenkins agent is ready for the AI Canary demo."
Write-Host "No Kubernetes cluster is required at this stage."

exit 0
