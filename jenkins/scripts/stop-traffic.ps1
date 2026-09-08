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

if ([string]::IsNullOrWhiteSpace($ProjectRoot)) {
    $ScriptDir = Split-Path -Parent $MyInvocation.MyCommand.Path
    $ProjectRoot = (Resolve-Path (Join-Path $ScriptDir "..\..")).Path
}
else {
    $ProjectRoot = (Resolve-Path $ProjectRoot).Path
}

$ConfigPath = Join-Path $ProjectRoot "jenkins\config\pipeline-config.json"
$TrafficStatePath = Join-Path $ProjectRoot "runtime\traffic-state.json"

if (-not (Test-Path $ConfigPath)) {
    Fail "pipeline-config.json not found: $ConfigPath"
}

$Config = Get-Content $ConfigPath -Raw | ConvertFrom-Json

$ClusterName = [string]$Config.project.cluster_name
$Namespace = [string]$Config.project.namespace
$TrafficPod = [string]$Config.traffic.generator_pod
$ExpectedContext = "kind-$ClusterName"

Write-Section "STOP LIVE APPLICATION TRAFFIC"

$CurrentContext = kubectl config current-context

if ($LASTEXITCODE -ne 0) {
    Fail "Unable to read kubectl current context."
}

if ($CurrentContext.Trim() -ne $ExpectedContext) {
    Fail (
        "Refusing traffic stop because kubectl context is " +
        "'$CurrentContext'. Expected '$ExpectedContext'."
    )
}

kubectl delete pod $TrafficPod `
    -n $Namespace `
    --ignore-not-found=true `
    --wait=true

if ($LASTEXITCODE -ne 0) {
    Fail "Unable to stop traffic-generator pod."
}

if (Test-Path $TrafficStatePath) {

    try {
        $State = Get-Content $TrafficStatePath -Raw | ConvertFrom-Json
        $State.active = $false

        $State |
            Add-Member `
                -NotePropertyName stopped_at `
                -NotePropertyValue (Get-Date).ToString("o") `
                -Force

        $State |
            ConvertTo-Json -Depth 5 |
            Set-Content `
                -Path $TrafficStatePath `
                -Encoding UTF8
    }
    catch {
        Write-Host "[WARN] Traffic stopped, but runtime state could not be updated."
    }
}

Write-Section "LIVE TRAFFIC STOPPED"

Write-Host "Traffic Pod : $TrafficPod"
Write-Host "Status      : STOPPED"
Write-Host ""

exit 0
