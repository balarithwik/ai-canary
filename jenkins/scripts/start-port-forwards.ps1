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

function Test-PortInUse {
    param([int]$Port)

    try {
        $listener = Get-NetTCPConnection `
            -LocalPort $Port `
            -State Listen `
            -ErrorAction SilentlyContinue

        return ($null -ne $listener)
    }
    catch {
        return $false
    }
}

function Wait-Http {
    param(
        [string]$Name,
        [string]$Url,
        [int]$TimeoutSeconds = 60
    )

    $elapsed = 0

    while ($elapsed -lt $TimeoutSeconds) {

        try {
            $response = Invoke-WebRequest `
                -Uri $Url `
                -UseBasicParsing `
                -TimeoutSec 5

            if ($response.StatusCode -ge 200 -and $response.StatusCode -lt 500) {
                Write-Host "[PASS] $Name reachable: $Url"
                return
            }
        }
        catch {
            # Keep waiting while kubectl port-forward becomes ready.
        }

        Start-Sleep -Seconds 2
        $elapsed += 2
    }

    Fail "$Name did not become reachable at $Url within $TimeoutSeconds seconds."
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
$RuntimeDir = Join-Path $ProjectRoot "runtime"
$PidFile = Join-Path $RuntimeDir "port-forwards.json"
$LogDir = Join-Path $RuntimeDir "logs"

if (-not (Test-Path $ConfigPath)) {
    Fail "pipeline-config.json not found: $ConfigPath"
}

if (-not (Test-Path $BuildInfoPath)) {
    Fail "runtime\build-info.json not found: $BuildInfoPath"
}

$Config = Get-Content $ConfigPath -Raw | ConvertFrom-Json
$BuildInfo = Get-Content $BuildInfoPath -Raw | ConvertFrom-Json

$ClusterName = [string]$Config.project.cluster_name
$ExpectedContext = "kind-$ClusterName"

$PrometheusPort = [int]$Config.ports.prometheus
$GrafanaPort = [int]$Config.ports.grafana
$PushgatewayPort = [int]$Config.ports.pushgateway

$PrometheusService = "service/monitoring-kube-prometheus-prometheus"
$GrafanaService = "service/monitoring-grafana"
$PushgatewayService = "service/pushgateway"

$MonitoringNamespace = "monitoring"
$StableVersion = [string]$BuildInfo.stable_version

New-Item -ItemType Directory -Force -Path $RuntimeDir | Out-Null
New-Item -ItemType Directory -Force -Path $LogDir | Out-Null

Write-Section "START MONITORING PORT-FORWARDS"

Write-Host "Prometheus  : http://localhost:$PrometheusPort"
Write-Host "Grafana     : http://localhost:$GrafanaPort"
Write-Host "Pushgateway : http://localhost:$PushgatewayPort"

# ============================================================
# SAFETY - VERIFY KUBECTL CONTEXT
# ============================================================

$CurrentContext = kubectl config current-context

if ($LASTEXITCODE -ne 0) {
    Fail "Unable to read kubectl current context."
}

if ($CurrentContext.Trim() -ne $ExpectedContext) {
    Fail (
        "Refusing port-forward start because kubectl context is " +
        "'$CurrentContext'. Expected '$ExpectedContext'."
    )
}

# ============================================================
# CLEAN STALE RECORDED PROCESSES
# ============================================================

if (Test-Path $PidFile) {

    try {
        $oldState = Get-Content $PidFile -Raw | ConvertFrom-Json

        foreach ($entry in $oldState.processes) {

            $oldProcess = Get-Process `
                -Id ([int]$entry.pid) `
                -ErrorAction SilentlyContinue

            if ($oldProcess) {
                Write-Host "[INFO] Stopping stale recorded port-forward PID $($entry.pid) ($($entry.name))..."
                Stop-Process -Id ([int]$entry.pid) -Force -ErrorAction SilentlyContinue
            }
        }

        Remove-Item $PidFile -Force -ErrorAction SilentlyContinue
        Start-Sleep -Seconds 1
    }
    catch {
        Write-Host "[WARN] Could not fully process old port-forward state."
    }
}

# ============================================================
# CHECK LOCAL PORTS
# ============================================================

foreach ($port in @(
    $PrometheusPort,
    $GrafanaPort,
    $PushgatewayPort
)) {
    if (Test-PortInUse -Port $port) {
        Fail (
            "Local port $port is already in use. " +
            "Stop the existing process before starting demo port-forwards."
        )
    }
}

# ============================================================
# START PORT-FORWARDS
# ============================================================

$Definitions = @(
    @{
        Name = "Prometheus"
        Resource = $PrometheusService
        Mapping = "${PrometheusPort}:9090"
        OutLog = Join-Path $LogDir "port-forward-prometheus.out.log"
        ErrLog = Join-Path $LogDir "port-forward-prometheus.err.log"
    },
    @{
        Name = "Grafana"
        Resource = $GrafanaService
        Mapping = "${GrafanaPort}:80"
        OutLog = Join-Path $LogDir "port-forward-grafana.out.log"
        ErrLog = Join-Path $LogDir "port-forward-grafana.err.log"
    },
    @{
        Name = "Pushgateway"
        Resource = $PushgatewayService
        Mapping = "${PushgatewayPort}:9091"
        OutLog = Join-Path $LogDir "port-forward-pushgateway.out.log"
        ErrLog = Join-Path $LogDir "port-forward-pushgateway.err.log"
    }
)

$Processes = @()

foreach ($definition in $Definitions) {

    Remove-Item $definition.OutLog -Force -ErrorAction SilentlyContinue
    Remove-Item $definition.ErrLog -Force -ErrorAction SilentlyContinue

    $arguments = @(
        "port-forward",
        "-n",
        $MonitoringNamespace,
        $definition.Resource,
        $definition.Mapping,
        "--address",
        "127.0.0.1"
    )

    # Detach long-running port-forward processes from both the Jenkins
    # Pipeline node process tree and Durable Task process tracking.
    #
    # Jenkins Pipeline uses JENKINS_NODE_COOKIE for node process tracking,
    # while Durable Task uses JENKINS_SERVER_COOKIE for the running step.
    # Give the port-forward child process different cookie values, then restore
    # the Jenkins PowerShell process environment immediately after launch.
    $OriginalJenkinsNodeCookie = $env:JENKINS_NODE_COOKIE
    $OriginalJenkinsServerCookie = $env:JENKINS_SERVER_COOKIE

    try {

        $SafeName = (
            [string]$definition.Name
        ) -replace '[^A-Za-z0-9_-]', '-'

        $DetachedCookie = (
            "ai-canary-port-forward-" +
            $SafeName +
            "-" +
            [guid]::NewGuid().ToString("N")
        )

        $env:JENKINS_NODE_COOKIE = $DetachedCookie
        $env:JENKINS_SERVER_COOKIE = $DetachedCookie

        $process = Start-Process `
            -FilePath "kubectl.exe" `
            -ArgumentList $arguments `
            -WindowStyle Hidden `
            -RedirectStandardOutput $definition.OutLog `
            -RedirectStandardError $definition.ErrLog `
            -PassThru
    }
    finally {

        if ($null -eq $OriginalJenkinsNodeCookie) {
            Remove-Item Env:JENKINS_NODE_COOKIE `
                -ErrorAction SilentlyContinue
        }
        else {
            $env:JENKINS_NODE_COOKIE = $OriginalJenkinsNodeCookie
        }

        if ($null -eq $OriginalJenkinsServerCookie) {
            Remove-Item Env:JENKINS_SERVER_COOKIE `
                -ErrorAction SilentlyContinue
        }
        else {
            $env:JENKINS_SERVER_COOKIE = $OriginalJenkinsServerCookie
        }
    }

    $Processes += [ordered]@{
        name = $definition.Name
        pid = $process.Id
        resource = $definition.Resource
        mapping = $definition.Mapping
        started_at = (Get-Date).ToString("o")
    }

    Write-Host "[INFO] Started $($definition.Name) port-forward PID $($process.Id)"
}

$State = [ordered]@{
    active = $true
    namespace = $MonitoringNamespace
    processes = $Processes
}

$State |
    ConvertTo-Json -Depth 8 |
    Set-Content `
        -Path $PidFile `
        -Encoding UTF8

# ============================================================
# VERIFY LOCAL ENDPOINTS
# ============================================================

Write-Section "VERIFY MONITORING ENDPOINTS"

Wait-Http `
    -Name "Prometheus" `
    -Url "http://localhost:$PrometheusPort/-/ready"

Wait-Http `
    -Name "Grafana" `
    -Url "http://localhost:$GrafanaPort/login"

Wait-Http `
    -Name "Pushgateway" `
    -Url "http://localhost:$PushgatewayPort/-/ready"

# ============================================================
# VERIFY PROMETHEUS SEES APPLICATION METRICS
# ============================================================

Write-Section "VERIFY PROMETHEUS APPLICATION SCRAPE"

$PrometheusBase = "http://localhost:$PrometheusPort"

$TargetQuery = 'up{namespace="ai-canary",service="ai-canary-demo"}'

$TargetResponse = Invoke-RestMethod `
    -Uri (
        "$PrometheusBase/api/v1/query?query=" +
        [uri]::EscapeDataString($TargetQuery)
    ) `
    -Method Get `
    -TimeoutSec 15

if ($TargetResponse.status -ne "success") {
    Fail "Prometheus target query failed."
}

$UpValues = @(
    $TargetResponse.data.result |
    ForEach-Object {
        [double]$_.value[1]
    }
)

if ($UpValues.Count -eq 0) {
    Fail (
        "Prometheus has not discovered the ai-canary-demo ServiceMonitor target yet. " +
        "Wait a few seconds and rerun this script."
    )
}

if (($UpValues | Measure-Object -Maximum).Maximum -lt 1) {
    Fail "Prometheus discovered ai-canary-demo but the target is DOWN."
}

Write-Host "[PASS] Prometheus is scraping ai-canary-demo."

# ServiceMonitor target labels can conflict with an application metric
# label named "endpoint". With honorLabels=true the application label is
# preserved. The exported_endpoint fallback keeps this validation safe while
# Prometheus reloads the updated ServiceMonitor configuration.

$MetricQuery = (
    "sum(" +
    "http_requests_total{version=`"$StableVersion`",endpoint=`"/api/orders`"}" +
    " or " +
    "http_requests_total{version=`"$StableVersion`",exported_endpoint=`"/api/orders`"}" +
    ")"
)

$MetricTimeoutSeconds = 60
$MetricElapsed = 0
$MetricFound = $false
$RequestCount = 0

while ($MetricElapsed -lt $MetricTimeoutSeconds) {

    $MetricResponse = Invoke-RestMethod `
        -Uri (
            "$PrometheusBase/api/v1/query?query=" +
            [uri]::EscapeDataString($MetricQuery)
        ) `
        -Method Get `
        -TimeoutSec 15

    if ($MetricResponse.status -ne "success") {
        Fail "Prometheus application metric query failed."
    }

    if ($MetricResponse.data.result.Count -gt 0) {
        $RequestCount = [double]$MetricResponse.data.result[0].value[1]

        if ($RequestCount -gt 0) {
            $MetricFound = $true
            break
        }
    }

    Write-Host (
        "[INFO] Waiting for Stable /api/orders metrics in Prometheus... " +
        "Elapsed=${MetricElapsed}s"
    )

    Start-Sleep -Seconds 5
    $MetricElapsed += 5
}

if (-not $MetricFound) {
    Fail (
        "Prometheus target is UP, but no /api/orders metric was found for Stable version " +
        "'$StableVersion' within $MetricTimeoutSeconds seconds."
    )
}

Write-Host "[PASS] Stable request metric found in Prometheus."
Write-Host "       Version       : $StableVersion"
Write-Host "       Requests seen : $RequestCount"

# ============================================================
# FINAL
# ============================================================

Write-Section "MONITORING ACCESS READY"

Write-Host "Prometheus  : http://localhost:$PrometheusPort"
Write-Host "Grafana     : http://localhost:$GrafanaPort"
Write-Host "Pushgateway : http://localhost:$PushgatewayPort"
Write-Host ""
Write-Host "Stable application telemetry is visible in Prometheus."
Write-Host "Keep these port-forwards running for the remaining demo stages."
Write-Host ""

exit 0
