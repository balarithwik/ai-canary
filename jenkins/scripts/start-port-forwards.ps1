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
#
# IMPORTANT - JENKINS / WINDOWS
#
# Long-running kubectl processes are created through Win32_Process.Create
# instead of Start-Process. This keeps them outside the Jenkins PowerShell
# durable-task process tree so the Pipeline step can finish normally.
#
# kubectl and kubeconfig are passed by absolute path so the detached process
# does not depend on the Jenkins child-process environment.
# ============================================================

try {
    $KubectlCommand = Get-Command "kubectl.exe" -ErrorAction Stop
    $KubectlPath = [string]$KubectlCommand.Source
}
catch {
    Fail "kubectl.exe could not be resolved."
}

if ([string]::IsNullOrWhiteSpace($KubectlPath)) {
    Fail "kubectl.exe path could not be resolved."
}

$KubeConfigPath = [string]$env:KUBECONFIG

if ([string]::IsNullOrWhiteSpace($KubeConfigPath)) {
    Fail (
        "KUBECONFIG is not set. " +
        "Configure Jenkins KUBECONFIG before starting monitoring port-forwards."
    )
}

if (-not (Test-Path $KubeConfigPath)) {
    Fail "Kubeconfig file not found: $KubeConfigPath"
}

$KubeConfigPath = (Resolve-Path $KubeConfigPath).Path

Write-Host "[INFO] kubectl    : $KubectlPath"
Write-Host "[INFO] KUBECONFIG : $KubeConfigPath"

$Definitions = @(
    @{
        Name = "Prometheus"
        Resource = $PrometheusService
        Mapping = "${PrometheusPort}:9090"
        LocalPort = $PrometheusPort
        OutLog = Join-Path $LogDir "port-forward-prometheus.out.log"
        ErrLog = Join-Path $LogDir "port-forward-prometheus.err.log"
    },
    @{
        Name = "Grafana"
        Resource = $GrafanaService
        Mapping = "${GrafanaPort}:80"
        LocalPort = $GrafanaPort
        OutLog = Join-Path $LogDir "port-forward-grafana.out.log"
        ErrLog = Join-Path $LogDir "port-forward-grafana.err.log"
    },
    @{
        Name = "Pushgateway"
        Resource = $PushgatewayService
        Mapping = "${PushgatewayPort}:9091"
        LocalPort = $PushgatewayPort
        OutLog = Join-Path $LogDir "port-forward-pushgateway.out.log"
        ErrLog = Join-Path $LogDir "port-forward-pushgateway.err.log"
    }
)

$Processes = @()

foreach ($definition in $Definitions) {

    Remove-Item $definition.OutLog -Force -ErrorAction SilentlyContinue
    Remove-Item $definition.ErrLog -Force -ErrorAction SilentlyContinue

    $SafeName = (
        [string]$definition.Name
    ) -replace '[^A-Za-z0-9_-]', '-'

    $LauncherPath = Join-Path `
        $RuntimeDir `
        ("port-forward-{0}.cmd" -f $SafeName.ToLowerInvariant())

    $LauncherContent = @"
@echo off
"$KubectlPath" --kubeconfig "$KubeConfigPath" port-forward -n "$MonitoringNamespace" "$($definition.Resource)" "$($definition.Mapping)" --address 127.0.0.1 1>>"$($definition.OutLog)" 2>>"$($definition.ErrLog)"
"@

    Set-Content `
        -Path $LauncherPath `
        -Value $LauncherContent `
        -Encoding ASCII

    $CommandLine = (
        'cmd.exe /d /s /c ""{0}""' -f $LauncherPath
    )

    try {
        $CreateResult = Invoke-CimMethod `
            -ClassName Win32_Process `
            -MethodName Create `
            -Arguments @{
                CommandLine = $CommandLine
            } `
            -ErrorAction Stop
    }
    catch {
        Fail (
            "Unable to create detached $($definition.Name) port-forward process. " +
            $_.Exception.Message
        )
    }

    if ([int]$CreateResult.ReturnValue -ne 0) {
        Fail (
            "Windows process creation failed for $($definition.Name). " +
            "Win32_Process.Create return value: $($CreateResult.ReturnValue)"
        )
    }

    $LauncherPid = [int]$CreateResult.ProcessId

    Write-Host (
        "[INFO] Detached launcher created for $($definition.Name) " +
        "(PID $LauncherPid)"
    )

    $PortForwardPid = $null
    $ListenElapsed = 0
    $ListenTimeoutSeconds = 30

    while ($ListenElapsed -lt $ListenTimeoutSeconds) {

        $Listener = Get-NetTCPConnection `
            -LocalPort ([int]$definition.LocalPort) `
            -State Listen `
            -ErrorAction SilentlyContinue |
            Where-Object {
                $_.LocalAddress -eq "127.0.0.1" -or
                $_.LocalAddress -eq "0.0.0.0" -or
                $_.LocalAddress -eq "::"
            } |
            Select-Object -First 1

        if ($Listener) {
            $PortForwardPid = [int]$Listener.OwningProcess
            break
        }

        $LauncherProcess = Get-Process `
            -Id $LauncherPid `
            -ErrorAction SilentlyContinue

        if (-not $LauncherProcess) {

            $ErrorTail = ""

            if (Test-Path $definition.ErrLog) {
                $ErrorTail = (
                    Get-Content $definition.ErrLog `
                        -Tail 20 `
                        -ErrorAction SilentlyContinue
                ) -join " "
            }

            Fail (
                "$($definition.Name) detached launcher exited before port " +
                "$($definition.LocalPort) became ready. $ErrorTail"
            )
        }

        Start-Sleep -Seconds 1
        $ListenElapsed += 1
    }

    if ($null -eq $PortForwardPid) {

        $ErrorTail = ""

        if (Test-Path $definition.ErrLog) {
            $ErrorTail = (
                Get-Content $definition.ErrLog `
                    -Tail 20 `
                    -ErrorAction SilentlyContinue
            ) -join " "
        }

        Fail (
            "$($definition.Name) port-forward did not listen on local port " +
            "$($definition.LocalPort) within $ListenTimeoutSeconds seconds. " +
            $ErrorTail
        )
    }

    $Processes += [ordered]@{
        name = $definition.Name
        pid = $PortForwardPid
        launcher_pid = $LauncherPid
        resource = $definition.Resource
        mapping = $definition.Mapping
        local_port = [int]$definition.LocalPort
        launcher = $LauncherPath
        started_at = (Get-Date).ToString("o")
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

    Write-Host (
        "[INFO] Started $($definition.Name) port-forward " +
        "PID $PortForwardPid (detached launcher PID $LauncherPid)"
    )
}

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
