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
    Fail "pipeline-config.json not found: $ConfigPath"
}

if (-not (Test-Path $DashboardPath)) {
    Fail "Grafana dashboard JSON not found: $DashboardPath"
}

$Config = Get-Content $ConfigPath -Raw | ConvertFrom-Json

$ClusterName = [string]$Config.project.cluster_name
$ExpectedContext = "kind-$ClusterName"
$MonitoringNamespace = "monitoring"

$DashboardConfigMap = "ai-deployment-intelligence-dashboard"

# ============================================================
# VERIFY DASHBOARD
# ============================================================

Write-Section "PUBLISH MONITORING DASHBOARD"

$Dashboard = Get-Content $DashboardPath -Raw | ConvertFrom-Json

if ([string]::IsNullOrWhiteSpace([string]$Dashboard.uid)) {
    Fail "Dashboard UID is missing."
}

if ([string]::IsNullOrWhiteSpace([string]$Dashboard.title)) {
    Fail "Dashboard title is missing."
}

Write-Host "Dashboard Title : $($Dashboard.title)"
Write-Host "Dashboard UID   : $($Dashboard.uid)"
Write-Host "ConfigMap       : $DashboardConfigMap"

if ([string]$Dashboard.uid -ne "ai-deployment-intelligence-center") {
    Fail "Unexpected dashboard UID: $($Dashboard.uid)"
}

# ============================================================
# SAFETY - VERIFY KUBERNETES CONTEXT
# ============================================================

$CurrentContext = kubectl config current-context
Assert-NativeSuccess "Unable to read Kubernetes context."

if ($CurrentContext.Trim() -ne $ExpectedContext) {
    Fail "Wrong Kubernetes context '$CurrentContext'. Expected '$ExpectedContext'."
}

Write-Host "[PASS] Correct Kubernetes context: $ExpectedContext"

kubectl get namespace $MonitoringNamespace -o name | Out-Null
Assert-NativeSuccess "Monitoring namespace does not exist."

# ============================================================
# CREATE / UPDATE GRAFANA DASHBOARD CONFIGMAP
# ============================================================

Write-Section "REGISTER DASHBOARD WITH GRAFANA"

kubectl create configmap $DashboardConfigMap `
    -n $MonitoringNamespace `
    --from-file="ai-deployment-intelligence-dashboard.json=$DashboardPath" `
    --dry-run=client `
    -o yaml |
    kubectl apply -f -

Assert-NativeSuccess "Unable to create/update Grafana dashboard ConfigMap."

kubectl label configmap $DashboardConfigMap `
    -n $MonitoringNamespace `
    grafana_dashboard=1 `
    --overwrite

Assert-NativeSuccess "Unable to label Grafana dashboard ConfigMap."

# ============================================================
# VERIFY
# ============================================================

$Label = kubectl get configmap $DashboardConfigMap `
    -n $MonitoringNamespace `
    -o jsonpath="{.metadata.labels.grafana_dashboard}"

Assert-NativeSuccess "Unable to verify dashboard ConfigMap."

if ($Label.Trim() -ne "1") {
    Fail "Grafana dashboard label verification failed."
}

$DashboardKey = kubectl get configmap $DashboardConfigMap `
    -n $MonitoringNamespace `
    -o jsonpath="{.data.ai-deployment-intelligence-dashboard\.json}"

Assert-NativeSuccess "Unable to verify dashboard content."

if ([string]::IsNullOrWhiteSpace($DashboardKey)) {
    Fail "Dashboard JSON was not stored in the ConfigMap."
}

Write-Host ""
Write-Host "[PASS] Dashboard ConfigMap created."
Write-Host "[PASS] grafana_dashboard=1 label verified."
Write-Host "[PASS] Frozen dashboard JSON registered."
Write-Host ""
Write-Host "Dashboard URL:"
Write-Host "http://localhost:3001/d/$($Dashboard.uid)"
Write-Host ""

Write-Section "MONITORING DASHBOARD READY FOR GRAFANA"

exit 0
