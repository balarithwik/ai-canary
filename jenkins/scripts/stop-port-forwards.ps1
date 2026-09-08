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

if ([string]::IsNullOrWhiteSpace($ProjectRoot)) {
    $ScriptDir = Split-Path -Parent $MyInvocation.MyCommand.Path
    $ProjectRoot = (Resolve-Path (Join-Path $ScriptDir "..\..")).Path
}
else {
    $ProjectRoot = (Resolve-Path $ProjectRoot).Path
}

$PidFile = Join-Path $ProjectRoot "runtime\port-forwards.json"

Write-Section "STOP MONITORING PORT-FORWARDS"

if (-not (Test-Path $PidFile)) {
    Write-Host "[INFO] No recorded port-forwards found."
    exit 0
}

try {
    $State = Get-Content $PidFile -Raw | ConvertFrom-Json

    foreach ($entry in $State.processes) {

        $process = Get-Process `
            -Id ([int]$entry.pid) `
            -ErrorAction SilentlyContinue

        if ($process) {
            Stop-Process `
                -Id ([int]$entry.pid) `
                -Force `
                -ErrorAction SilentlyContinue

            Write-Host "[PASS] Stopped $($entry.name) PID $($entry.pid)"
        }
        else {
            Write-Host "[INFO] $($entry.name) PID $($entry.pid) is already stopped."
        }
    }

    $State.active = $false

    $State |
        Add-Member `
            -NotePropertyName stopped_at `
            -NotePropertyValue (Get-Date).ToString("o") `
            -Force

    $State |
        ConvertTo-Json -Depth 8 |
        Set-Content `
            -Path $PidFile `
            -Encoding UTF8
}
catch {
    Write-Host "[WARN] Unable to fully process recorded port-forward state."
    exit 1
}

Write-Host ""
Write-Host "Monitoring port-forwards stopped."
Write-Host ""

exit 0
