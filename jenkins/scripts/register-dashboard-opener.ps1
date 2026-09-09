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

$InteractiveUser = [System.Security.Principal.WindowsIdentity]::GetCurrent().Name

if ($InteractiveUser -ieq "NT AUTHORITY\SYSTEM") {
    Write-Host "[FAIL] Run this registration script from your normal logged-in Windows PowerShell session, not from Jenkins/SYSTEM."
    exit 1
}

Write-Section "REGISTER INTERACTIVE DASHBOARD OPENER"

Write-Host "Task Name  : $TaskName"
Write-Host "User       : $InteractiveUser"
Write-Host "Dashboard  : $DashboardUrl"
Write-Host ""

# cmd.exe 'start' delegates the URL to the logged-in user's default browser.
# If that browser is already open, Chrome/Edge normally opens a new tab.
$ActionArguments = "/c start `"`" `"$DashboardUrl`""

$Action = New-ScheduledTaskAction `
    -Execute "$env:SystemRoot\System32\cmd.exe" `
    -Argument $ActionArguments

$Principal = New-ScheduledTaskPrincipal `
    -UserId $InteractiveUser `
    -LogonType Interactive `
    -RunLevel Limited

$Settings = New-ScheduledTaskSettingsSet `
    -AllowStartIfOnBatteries `
    -DontStopIfGoingOnBatteries `
    -ExecutionTimeLimit (New-TimeSpan -Minutes 2)

try {
    Register-ScheduledTask `
        -TaskName $TaskName `
        -Action $Action `
        -Principal $Principal `
        -Settings $Settings `
        -Force | Out-Null
}
catch {
    Write-Host "[FAIL] Unable to register scheduled task: $($_.Exception.Message)"
    Write-Host "If Windows blocks task creation, reopen PowerShell as Administrator and run this script once."
    exit 1
}

$Task = Get-ScheduledTask -TaskName $TaskName -ErrorAction Stop

Write-Host "[PASS] Interactive dashboard opener registered."
Write-Host "       State : $($Task.State)"
Write-Host ""
Write-Host "Jenkins LocalSystem can now trigger '$TaskName'."
Write-Host "The browser opening step remains non-fatal if Windows blocks interactive launch."
Write-Host ""

exit 0
