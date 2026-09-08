param(
    [Parameter(Mandatory = $true)]
    [string]$Stage,

    [Parameter(Mandatory = $true)]
    [ValidateSet("START", "END")]
    [string]$Action,

    [ValidateSet("SUCCESS", "FAILED", "ABORTED", "RUNNING")]
    [string]$Status = "SUCCESS",

    [string]$ProjectRoot = ""
)

$ErrorActionPreference = "Stop"

function Fail {
    param([string]$Message)

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

$RuntimeDir = Join-Path $ProjectRoot "runtime"
$TimelinePath = Join-Path $RuntimeDir "pipeline-timeline.json"

New-Item -ItemType Directory -Force -Path $RuntimeDir | Out-Null

# ============================================================
# LOAD / INITIALIZE TIMELINE
# ============================================================

if (Test-Path $TimelinePath) {
    try {
        $Timeline = Get-Content $TimelinePath -Raw | ConvertFrom-Json
    }
    catch {
        Fail "Unable to parse existing pipeline timeline: $TimelinePath"
    }
}
else {
    $Now = [DateTimeOffset]::Now

    $Timeline = [PSCustomObject]@{
        pipeline_started_at = $Now.ToString("o")
        pipeline_ended_at   = $null
        total_duration_sec  = $null
        last_updated_at     = $Now.ToString("o")
        stages              = @()
    }
}

# Ensure stages is always an array.
$Stages = @($Timeline.stages)

$Now = [DateTimeOffset]::Now

# ============================================================
# START STAGE
# ============================================================

if ($Action -eq "START") {

    $ExistingOpenStage = $Stages |
        Where-Object {
            $_.name -eq $Stage -and
            [string]::IsNullOrWhiteSpace([string]$_.end_time)
        } |
        Select-Object -Last 1

    if ($null -ne $ExistingOpenStage) {
        Fail "Stage '$Stage' already has an active START record."
    }

    $StageRecord = [PSCustomObject]@{
        name             = $Stage
        start_time       = $Now.ToString("o")
        end_time         = $null
        duration_seconds = $null
        status           = "RUNNING"
    }

    $Stages += $StageRecord

    Write-Host "[TIMING] START : $Stage"
    Write-Host "[TIMING] Time  : $($Now.ToString('yyyy-MM-dd HH:mm:ss zzz'))"
}

# ============================================================
# END STAGE
# ============================================================

if ($Action -eq "END") {

    $OpenStage = $Stages |
        Where-Object {
            $_.name -eq $Stage -and
            [string]::IsNullOrWhiteSpace([string]$_.end_time)
        } |
        Select-Object -Last 1

    if ($null -eq $OpenStage) {
        Fail "No active START record found for stage '$Stage'."
    }

    $StartTime = [DateTimeOffset]::Parse([string]$OpenStage.start_time)

    $Duration = [math]::Round(
        ($Now - $StartTime).TotalSeconds,
        2
    )

    $OpenStage.end_time = $Now.ToString("o")
    $OpenStage.duration_seconds = $Duration
    $OpenStage.status = $Status

    Write-Host "[TIMING] END      : $Stage"
    Write-Host "[TIMING] Status   : $Status"
    Write-Host "[TIMING] Duration : $Duration second(s)"
}

# ============================================================
# UPDATE PIPELINE SUMMARY
# ============================================================

$Timeline.stages = @($Stages)
$Timeline.last_updated_at = $Now.ToString("o")

$CompletedStages = @(
    $Stages | Where-Object {
        -not [string]::IsNullOrWhiteSpace([string]$_.end_time)
    }
)

if ($CompletedStages.Count -gt 0) {

    $LatestEnd = $CompletedStages |
        Sort-Object {
            [DateTimeOffset]::Parse([string]$_.end_time)
        } |
        Select-Object -Last 1

    $PipelineStart = [DateTimeOffset]::Parse(
        [string]$Timeline.pipeline_started_at
    )

    $PipelineEnd = [DateTimeOffset]::Parse(
        [string]$LatestEnd.end_time
    )

    $Timeline.pipeline_ended_at = $PipelineEnd.ToString("o")
    $Timeline.total_duration_sec = [math]::Round(
        ($PipelineEnd - $PipelineStart).TotalSeconds,
        2
    )
}

# ============================================================
# SAFE WRITE
# ============================================================

$TempPath = "$TimelinePath.tmp"

$Timeline |
    ConvertTo-Json -Depth 10 |
    Set-Content $TempPath -Encoding UTF8

Move-Item $TempPath $TimelinePath -Force

Write-Host "[TIMING] Saved : $TimelinePath"

exit 0
