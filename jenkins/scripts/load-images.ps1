param(
    [string]$ProjectRoot = "",
    [string]$BuildId = "",
    [string]$ImageRepository = "ai-canary-demo"
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
$ApplicationDir = Join-Path $ProjectRoot "application"
$DockerfilePath = Join-Path $ApplicationDir "Dockerfile"
$RuntimeDir = Join-Path $ProjectRoot "runtime"
$BuildInfoPath = Join-Path $RuntimeDir "build-info.json"

if (-not (Test-Path $ConfigPath)) {
    Fail "pipeline-config.json not found: $ConfigPath"
}

if (-not (Test-Path $DockerfilePath)) {
    Fail "Application Dockerfile not found: $DockerfilePath"
}

$Config = Get-Content $ConfigPath -Raw | ConvertFrom-Json

$ClusterName = [string]$Config.project.cluster_name
$ExpectedContext = "kind-$ClusterName"

# ============================================================
# BUILD IDENTIFIER
#
# Jenkins:
#   Uses BUILD_NUMBER automatically when available.
#
# Manual/local validation:
#   Uses a timestamp.
# ============================================================

if ([string]::IsNullOrWhiteSpace($BuildId)) {

    if (-not [string]::IsNullOrWhiteSpace($env:BUILD_NUMBER)) {
        $BuildId = "jenkins-$($env:BUILD_NUMBER)"
    }
    else {
        $BuildId = "local-$(Get-Date -Format 'yyyyMMdd-HHmmss')"
    }
}

# Docker tag-safe, readable build identifier.
$SafeBuildId = $BuildId.ToLowerInvariant()
$SafeBuildId = $SafeBuildId -replace '[^a-z0-9_.-]', '-'
$SafeBuildId = $SafeBuildId.Trim('-', '.', '_')

if ([string]::IsNullOrWhiteSpace($SafeBuildId)) {
    Fail "BuildId became empty after Docker tag sanitization."
}

$StableVersion = "stable-$SafeBuildId"
$CanaryVersion = "canary-$SafeBuildId"

$StableImage = "${ImageRepository}:${StableVersion}"
$CanaryImage = "${ImageRepository}:${CanaryVersion}"

Write-Section "BUILD AND LOAD APPLICATION IMAGES"

Write-Host "Project Root   : $ProjectRoot"
Write-Host "Cluster        : $ClusterName"
Write-Host "Stable Version : $StableVersion"
Write-Host "Canary Version : $CanaryVersion"
Write-Host "Stable Image   : $StableImage"
Write-Host "Canary Image   : $CanaryImage"

# ============================================================
# SAFETY - VERIFY TARGET KIND CLUSTER
# ============================================================

$CurrentContext = kubectl config current-context
Assert-NativeSuccess "Unable to read kubectl current context."

if ($CurrentContext.Trim() -ne $ExpectedContext) {
    Fail (
        "Refusing image load because kubectl context is " +
        "'$CurrentContext'. Expected '$ExpectedContext'."
    )
}

kubectl get nodes | Out-Null
Assert-NativeSuccess "Kubernetes cluster is not reachable."

Write-Host ""
Write-Host "[PASS] Target Kind cluster is reachable."

# ============================================================
# BUILD APPLICATION IMAGE ONCE
#
# Stable and Canary use the same application code/image content.
# Runtime behavior is controlled by Rollout environment values.
#
# This keeps the POC honest:
#   - no special 'bad' image
#   - no extra revision for the 80% degradation event
# ============================================================

Write-Section "BUILD APPLICATION"

docker build `
    -f $DockerfilePath `
    -t $StableImage `
    $ApplicationDir

Assert-NativeSuccess "Docker application build failed."

Write-Host "[PASS] Built image: $StableImage"

# Candidate gets a separate readable tag while preserving
# identical application code/digest.

docker tag $StableImage $CanaryImage
Assert-NativeSuccess "Unable to create Canary image tag."

Write-Host "[PASS] Tagged image: $CanaryImage"

# ============================================================
# VERIFY LOCAL DOCKER IMAGES
# ============================================================

docker image inspect $StableImage | Out-Null
Assert-NativeSuccess "Stable image is missing after build."

docker image inspect $CanaryImage | Out-Null
Assert-NativeSuccess "Canary image is missing after tagging."

# ============================================================
# LOAD BOTH TAGS INTO KIND
# ============================================================

Write-Section "LOAD IMAGES INTO KIND"

kind load docker-image `
    $StableImage `
    $CanaryImage `
    --name $ClusterName

Assert-NativeSuccess "Unable to load application images into Kind."

Write-Host "[PASS] Application image tags loaded into Kind."

# ============================================================
# WRITE BUILD METADATA FOR LATER JENKINS STAGES
# ============================================================

New-Item `
    -ItemType Directory `
    -Force `
    -Path $RuntimeDir | Out-Null

$BuildInfo = [ordered]@{
    build_id = $SafeBuildId
    image_repository = $ImageRepository
    stable_version = $StableVersion
    canary_version = $CanaryVersion
    stable_image = $StableImage
    canary_image = $CanaryImage
    cluster_name = $ClusterName
    created_at = (Get-Date).ToString("o")
}

$BuildInfo |
    ConvertTo-Json -Depth 5 |
    Set-Content `
        -Path $BuildInfoPath `
        -Encoding UTF8

Write-Host ""
Write-Host "[PASS] Build metadata written:"
Write-Host "       $BuildInfoPath"

# ============================================================
# FINAL OUTPUT
# ============================================================

Write-Section "IMAGES READY"

Write-Host "Stable Version : $StableVersion"
Write-Host "Stable Image   : $StableImage"
Write-Host ""
Write-Host "Canary Version : $CanaryVersion"
Write-Host "Canary Image   : $CanaryImage"
Write-Host ""
Write-Host "Both tags use the same application code."
Write-Host "Stable/Canary behavior will be supplied by the rendered Rollout."
Write-Host ""

exit 0
