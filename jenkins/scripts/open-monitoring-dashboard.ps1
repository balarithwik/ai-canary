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
$DashboardPath = Join-Path $ProjectRoot "grafana\ai-deployment-intelligence-dashboard.json"

if (-not (Test-Path $ConfigPath)) {
    Fail "pipeline-config.json not found."
}

if (-not (Test-Path $DashboardPath)) {
    Fail "Grafana dashboard JSON not found."
}

$Config = Get-Content $ConfigPath -Raw | ConvertFrom-Json
$Dashboard = Get-Content $DashboardPath -Raw | ConvertFrom-Json

$GrafanaBaseUrl = [string]$Config.monitoring.grafana_url
$DashboardUid = [string]$Dashboard.uid
$DashboardTitle = [string]$Dashboard.title

if ([string]::IsNullOrWhiteSpace($GrafanaBaseUrl)) {
    Fail "Grafana URL is missing from pipeline-config.json."
}

if ([string]::IsNullOrWhiteSpace($DashboardUid)) {
    Fail "Dashboard UID is missing."
}

$GrafanaBaseUrl = $GrafanaBaseUrl.TrimEnd("/")
$DashboardUrl = "$GrafanaBaseUrl/d/${DashboardUid}?orgId=1&refresh=5s"
$DashboardApiUrl = "$GrafanaBaseUrl/api/dashboards/uid/$DashboardUid"

# ============================================================
# VERIFY DASHBOARD ACCESS
# ============================================================

Write-Section "MONITORING DASHBOARD"

Write-Host "Dashboard : $DashboardTitle"
Write-Host "UID       : $DashboardUid"
Write-Host "URL       : $DashboardUrl"
Write-Host ""

try {
    $Response = Invoke-WebRequest `
        -Uri $DashboardApiUrl `
        -UseBasicParsing `
        -TimeoutSec 15 `
        -ErrorAction Stop

    if ($Response.StatusCode -ne 200) {
        Fail "Grafana dashboard returned HTTP $($Response.StatusCode)."
    }

    $Payload = $Response.Content | ConvertFrom-Json

    if ([string]$Payload.dashboard.uid -ne $DashboardUid) {
        Fail "Unexpected dashboard UID returned by Grafana."
    }

    Write-Host "[PASS] Grafana reachable."
    Write-Host "[PASS] Dashboard available."
    Write-Host "[PASS] Anonymous Viewer access verified."
}
catch {
    Fail "Dashboard verification failed: $($_.Exception.Message)"
}

# ============================================================
# OPEN DASHBOARD
# ============================================================

Write-Section "OPEN DASHBOARD"

try {
    Start-Process $DashboardUrl

    Write-Host "[PASS] Dashboard browser launch requested."
}
catch {
    # Browser launch should not invalidate the deployment pipeline.
    Write-Host "[WARN] Unable to automatically open the browser."
    Write-Host "[WARN] Open this URL manually:"
    Write-Host $DashboardUrl
}

Write-Host ""
Write-Host "Dashboard remains available while the pipeline is running."
Write-Host ""

exit 0

