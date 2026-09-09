param(
    [string]$ProjectRoot = "",
    [string]$TaskName = "AI-Canary-Open-Dashboard"
)

$ErrorActionPreference = "Stop"

function Write-Section {
    param([string]$Title)

    Write-Host ""
    Write-Host "============================================================"
    Write-Host " $Title"
    Write-Host "============================================================"
}

function Warn {
    param([string]$Message)
    Write-Host "[WARN] $Message"
}

if ([string]::IsNullOrWhiteSpace($ProjectRoot)) {
    $ScriptDir = Split-Path -Parent $MyInvocation.MyCommand.Path
    $ProjectRoot = (Resolve-Path (Join-Path $ScriptDir "..\..")).Path
}
else {
    $ProjectRoot = (Resolve-Path $ProjectRoot).Path
}

$ConfigPath = Join-Path $ProjectRoot "jenkins\config\pipeline-config.json"

if (-not (Test-Path $ConfigPath)) {
    Write-Host "[FAIL] pipeline-config.json not found."
    exit 1
}

$Config = Get-Content $ConfigPath -Raw | ConvertFrom-Json
$GrafanaUrl = ([string]$Config.monitoring.grafana_url).TrimEnd("/")
$RefreshSeconds = [int]$Config.monitoring.grafana_refresh_seconds
$DashboardUid = "ai-deployment-intelligence-center"
$DashboardUrl = "${GrafanaUrl}/d/${DashboardUid}?orgId=1&refresh=${RefreshSeconds}s"

Write-Section "MONITORING DASHBOARD"

Write-Host "Dashboard : AI Deployment Intelligence Center"
Write-Host "UID       : $DashboardUid"
Write-Host "URL       : $DashboardUrl"
Write-Host ""

try {
    Invoke-WebRequest `
        -Uri "$GrafanaUrl/login" `
        -UseBasicParsing `
        -TimeoutSec 10 | Out-Null

    Write-Host "[PASS] Grafana reachable."
}
catch {
    Write-Host "[FAIL] Grafana is not reachable at $GrafanaUrl."
    exit 1
}

try {
    $DashboardResponse = Invoke-WebRequest `
        -Uri $DashboardUrl `
        -UseBasicParsing `
        -TimeoutSec 10

    if ($null -eq $DashboardResponse) {
        throw "No dashboard response returned."
    }

    if ($DashboardResponse.StatusCode -lt 200 -or $DashboardResponse.StatusCode -ge 400) {
        throw "Dashboard returned HTTP $($DashboardResponse.StatusCode)."
    }

    $FinalUri = [string]$DashboardResponse.BaseResponse.ResponseUri.AbsoluteUri

    if ($FinalUri -match "/login") {
        throw "Grafana redirected the dashboard request to the login page."
    }

    Write-Host "[PASS] Dashboard available."
    Write-Host "[PASS] Anonymous Viewer access verified."
}
catch {
    Write-Host "[FAIL] Dashboard is not anonymously reachable at $DashboardUrl."
    exit 1
}

Write-Section "OPEN DASHBOARD"

$Identity = [System.Security.Principal.WindowsIdentity]::GetCurrent().Name
Write-Host "Execution Account : $Identity"

# Jenkins runs as LocalSystem, which is isolated from the logged-in user's
# interactive desktop. A normal Start-Process from Session 0 cannot create a
# visible browser tab. When the one-time interactive scheduled task is present,
# Jenkins triggers that task so Windows opens the dashboard in the user's session.
if ($Identity -ieq "NT AUTHORITY\SYSTEM") {

    try {
        $Task = Get-ScheduledTask `
            -TaskName $TaskName `
            -ErrorAction SilentlyContinue

        if ($null -ne $Task) {
            Start-ScheduledTask -TaskName $TaskName
            Write-Host "[PASS] Dashboard open request sent to the interactive desktop."
            Write-Host "[PASS] The default browser should open the dashboard in a new tab/window."
        }
        else {
            Warn "Interactive dashboard opener task '$TaskName' is not registered."
            Warn "Run jenkins\scripts\register-dashboard-opener.ps1 once from your normal Windows PowerShell session."
            Warn "Open this URL manually:"
            Write-Host $DashboardUrl
        }
    }
    catch {
        Warn "Unable to trigger interactive dashboard opener: $($_.Exception.Message)"
        Warn "Open this URL manually:"
        Write-Host $DashboardUrl
    }
}
else {

    try {
        Start-Process $DashboardUrl
        Write-Host "[PASS] Dashboard open request sent to the current interactive session."
    }
    catch {
        Warn "Unable to automatically open the browser: $($_.Exception.Message)"
        Warn "Open this URL manually:"
        Write-Host $DashboardUrl
    }
}

Write-Host ""
Write-Host "Dashboard remains available while the pipeline is running."
Write-Host ""

exit 0
