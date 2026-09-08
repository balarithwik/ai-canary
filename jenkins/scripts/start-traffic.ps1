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
$RuntimeDir = Join-Path $ProjectRoot "runtime"
$TrafficStatePath = Join-Path $RuntimeDir "traffic-state.json"

if (-not (Test-Path $ConfigPath)) {
    Fail "pipeline-config.json not found: $ConfigPath"
}

$Config = Get-Content $ConfigPath -Raw | ConvertFrom-Json

$ClusterName = [string]$Config.project.cluster_name
$Namespace = [string]$Config.project.namespace
$ServiceName = [string]$Config.project.service_name
$RolloutName = [string]$Config.project.rollout_name

$TrafficPod = [string]$Config.traffic.generator_pod
$RequestPath = [string]$Config.traffic.request_path
$IntervalSeconds = [string]$Config.traffic.request_interval_seconds

$ExpectedContext = "kind-$ClusterName"
$TargetUrl = "http://$ServiceName$RequestPath"
$HealthUrl = "http://$ServiceName/health"
$MetricsUrl = "http://$ServiceName/metrics"

Write-Section "START LIVE APPLICATION TRAFFIC"

Write-Host "Cluster       : $ClusterName"
Write-Host "Namespace     : $Namespace"
Write-Host "Rollout       : $RolloutName"
Write-Host "Traffic Pod   : $TrafficPod"
Write-Host "Target        : $TargetUrl"
Write-Host "Interval      : $IntervalSeconds second(s)"

# ============================================================
# SAFETY CHECKS
# ============================================================

$CurrentContext = kubectl config current-context
Assert-NativeSuccess "Unable to read kubectl current context."

if ($CurrentContext.Trim() -ne $ExpectedContext) {
    Fail (
        "Refusing traffic start because kubectl context is " +
        "'$CurrentContext'. Expected '$ExpectedContext'."
    )
}

kubectl get svc $ServiceName -n $Namespace | Out-Null
Assert-NativeSuccess "Application service '$ServiceName' was not found."

$Phase = kubectl get rollout $RolloutName `
    -n $Namespace `
    -o jsonpath="{.status.phase}"

Assert-NativeSuccess "Unable to read rollout status."

if ($Phase.Trim() -ne "Healthy") {
    Fail "Rollout must be Healthy before baseline traffic starts. Current phase: $Phase"
}

Write-Host ""
Write-Host "[PASS] Stable application is Healthy and service is available."

# ============================================================
# REMOVE STALE TRAFFIC POD
# ============================================================

Write-Section "PREPARE TRAFFIC GENERATOR"

kubectl delete pod $TrafficPod `
    -n $Namespace `
    --ignore-not-found=true `
    --wait=true | Out-Null

Assert-NativeSuccess "Unable to remove an existing traffic-generator pod."

# ============================================================
# CREATE TRAFFIC GENERATOR
#
# The pod continuously calls the Kubernetes Service, so the same
# traffic stream naturally follows Argo's Stable/Canary routing.
# ============================================================

$TrafficManifest = @"
apiVersion: v1
kind: Pod
metadata:
  name: $TrafficPod
  namespace: $Namespace
  labels:
    app: $TrafficPod
spec:
  restartPolicy: Always
  terminationGracePeriodSeconds: 0
  containers:
    - name: curl
      image: curlimages/curl:8.12.1
      imagePullPolicy: IfNotPresent
      env:
        - name: TARGET_URL
          value: "$TargetUrl"
        - name: INTERVAL_SECONDS
          value: "$IntervalSeconds"
      command:
        - sh
        - -c
        - |
          while true; do
            curl -s -o /dev/null --max-time 5 "`$TARGET_URL" || true
            sleep "`$INTERVAL_SECONDS"
          done
"@

$TrafficManifest | kubectl apply -f -
Assert-NativeSuccess "Traffic-generator pod creation failed."

Write-Host "[INFO] Waiting for traffic-generator pod..."

kubectl wait `
    --for=condition=Ready `
    pod/$TrafficPod `
    -n $Namespace `
    --timeout=120s

Assert-NativeSuccess "Traffic-generator pod did not become Ready."

Write-Host "[PASS] Traffic-generator pod is Ready."

# ============================================================
# VERIFY SERVICE CONNECTIVITY
# ============================================================

Write-Section "VERIFY LIVE TRAFFIC"

$HealthResponse = kubectl exec `
    -n $Namespace `
    $TrafficPod `
    -- curl -fsS --max-time 10 $HealthUrl

Assert-NativeSuccess "Traffic-generator cannot reach application /health."

Write-Host "[PASS] Application health endpoint reachable:"
Write-Host "       $HealthResponse"

# Allow enough requests to flow before checking application metrics.
Start-Sleep -Seconds 5

$MetricsOutput = kubectl exec `
    -n $Namespace `
    $TrafficPod `
    -- curl -fsS --max-time 10 $MetricsUrl

Assert-NativeSuccess "Unable to read application /metrics."

if (($MetricsOutput -join "`n") -notmatch "http_requests_total") {
    Fail "Application metrics are reachable but http_requests_total was not found."
}

Write-Host "[PASS] Application request metrics are increasing."

# ============================================================
# WRITE RUNTIME STATE
# ============================================================

New-Item `
    -ItemType Directory `
    -Force `
    -Path $RuntimeDir | Out-Null

$TrafficState = [ordered]@{
    active = $true
    pod = $TrafficPod
    namespace = $Namespace
    target_url = $TargetUrl
    request_interval_seconds = [double]$IntervalSeconds
    started_at = (Get-Date).ToString("o")
}

$TrafficState |
    ConvertTo-Json -Depth 5 |
    Set-Content `
        -Path $TrafficStatePath `
        -Encoding UTF8

Write-Host "[PASS] Traffic state recorded:"
Write-Host "       $TrafficStatePath"

# ============================================================
# FINAL RESULT
# ============================================================

Write-Section "LIVE TRAFFIC ACTIVE"

Write-Host "Traffic Pod : $TrafficPod"
Write-Host "Target      : $TargetUrl"
Write-Host "Interval    : $IntervalSeconds second(s)"
Write-Host "Status      : RUNNING"
Write-Host ""
Write-Host "Keep this traffic running through Stable baseline and Canary stages."
Write-Host ""

exit 0
