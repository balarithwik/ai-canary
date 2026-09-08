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

if (-not (Test-Path $ConfigPath)) {
    Fail "pipeline-config.json not found: $ConfigPath"
}

$Config = Get-Content $ConfigPath -Raw | ConvertFrom-Json

$ClusterName = [string]$Config.project.cluster_name
$AppNamespace = [string]$Config.project.namespace
$ExpectedContext = "kind-$ClusterName"

$ArgoNamespace = "argo-rollouts"
$MonitoringNamespace = "monitoring"

$ArgoVersion = "v1.10.0"
$ArgoInstallUrl = "https://github.com/argoproj/argo-rollouts/releases/download/$ArgoVersion/install.yaml"

$MonitoringRelease = "monitoring"
$KubePrometheusChartVersion = "88.2.0"

Write-Section "INSTALL AI CANARY PLATFORM"

Write-Host "Project Root       : $ProjectRoot"
Write-Host "Cluster            : $ClusterName"
Write-Host "Expected Context   : $ExpectedContext"
Write-Host "Application NS     : $AppNamespace"
Write-Host "Argo Rollouts      : $ArgoVersion"
Write-Host "Monitoring Chart   : kube-prometheus-stack $KubePrometheusChartVersion"

# ============================================================
# SAFETY - VERIFY TARGET CLUSTER
# ============================================================

$CurrentContext = kubectl config current-context
Assert-NativeSuccess "Unable to read kubectl current context."

if ($CurrentContext.Trim() -ne $ExpectedContext) {
    Fail (
        "Refusing platform installation because kubectl context is " +
        "'$CurrentContext'. Expected '$ExpectedContext'."
    )
}

Write-Host ""
Write-Host "[PASS] Correct Kubernetes context: $ExpectedContext"

# ============================================================
# NAMESPACES
# ============================================================

Write-Section "CREATE NAMESPACES"

foreach ($Namespace in @(
    $AppNamespace,
    $ArgoNamespace,
    $MonitoringNamespace
)) {
    kubectl create namespace $Namespace --dry-run=client -o yaml |
        kubectl apply -f -

    Assert-NativeSuccess "Failed to create/verify namespace '$Namespace'."

    Write-Host "[PASS] Namespace ready: $Namespace"
}

# ============================================================
# ARGO ROLLOUTS
# ============================================================

Write-Section "INSTALL ARGO ROLLOUTS"

# Argo Rollouts v1.10 CRDs have large schemas. Client-side apply
# stores the complete manifest in kubectl.kubernetes.io/last-applied-configuration,
# which can exceed Kubernetes' 256 KiB annotation limit.
#
# Server-side apply avoids that annotation and is safe to rerun after a
# partially completed installation.
kubectl apply `
    --server-side `
    --force-conflicts `
    -n $ArgoNamespace `
    -f $ArgoInstallUrl

Assert-NativeSuccess "Argo Rollouts server-side installation failed."

Write-Host ""
Write-Host "[INFO] Waiting for Argo Rollouts controller..."

kubectl rollout status `
    deployment/argo-rollouts `
    -n $ArgoNamespace `
    --timeout=240s

Assert-NativeSuccess "Argo Rollouts controller did not become Ready."

$ArgoReady = kubectl get deployment argo-rollouts `
    -n $ArgoNamespace `
    -o jsonpath="{.status.readyReplicas}"

Assert-NativeSuccess "Unable to verify Argo Rollouts deployment."

if ([string]::IsNullOrWhiteSpace($ArgoReady) -or [int]$ArgoReady -lt 1) {
    Fail "Argo Rollouts controller has no Ready replicas."
}

Write-Host "[PASS] Argo Rollouts controller ready: $ArgoReady replica(s)"

# ============================================================
# PROMETHEUS + GRAFANA
# ============================================================

Write-Section "INSTALL PROMETHEUS AND GRAFANA"

helm repo add `
    prometheus-community `
    https://prometheus-community.github.io/helm-charts `
    --force-update

Assert-NativeSuccess "Unable to add/update prometheus-community Helm repository."

helm repo update
Assert-NativeSuccess "Helm repository update failed."

helm upgrade `
    --install $MonitoringRelease `
    prometheus-community/kube-prometheus-stack `
    --namespace $MonitoringNamespace `
    --version $KubePrometheusChartVersion `
    --set grafana.service.type=ClusterIP `
    --set 'grafana.grafana\.ini.auth\.anonymous.enabled=true' `
    --set-string 'grafana.grafana\.ini.auth\.anonymous.org_role=Viewer' `
    --set 'grafana.grafana\.ini.auth.disable_login_form=true' `
    --set prometheus.service.type=ClusterIP `
    --set alertmanager.service.type=ClusterIP `
    --wait `
    --timeout 10m

Assert-NativeSuccess "kube-prometheus-stack installation failed."

Write-Host "[PASS] Prometheus/Grafana stack installed."

# ============================================================
# PUSHGATEWAY
#
# We use a small fixed Kubernetes manifest so the service name
# remains predictable: monitoring/pushgateway.
#
# No demo-fault metric is exposed here.
# ============================================================

Write-Section "INSTALL PUSHGATEWAY"

$PushgatewayManifest = @"
apiVersion: apps/v1
kind: Deployment
metadata:
  name: pushgateway
  namespace: $MonitoringNamespace
  labels:
    app: pushgateway
spec:
  replicas: 1
  selector:
    matchLabels:
      app: pushgateway
  template:
    metadata:
      labels:
        app: pushgateway
    spec:
      containers:
        - name: pushgateway
          image: prom/pushgateway:v1.11.1
          imagePullPolicy: IfNotPresent
          ports:
            - name: http
              containerPort: 9091
          resources:
            requests:
              cpu: 25m
              memory: 32Mi
            limits:
              cpu: 150m
              memory: 128Mi
---
apiVersion: v1
kind: Service
metadata:
  name: pushgateway
  namespace: $MonitoringNamespace
  labels:
    app: pushgateway
spec:
  selector:
    app: pushgateway
  ports:
    - name: http
      port: 9091
      targetPort: http
"@

$PushgatewayManifest |
    kubectl apply -f -

Assert-NativeSuccess "Pushgateway installation failed."

kubectl rollout status `
    deployment/pushgateway `
    -n $MonitoringNamespace `
    --timeout=240s

Assert-NativeSuccess "Pushgateway did not become Ready."

Write-Host "[PASS] Pushgateway ready."

# ============================================================
# PUSHGATEWAY SERVICEMONITOR
# ============================================================

Write-Section "CONFIGURE PUSHGATEWAY MONITORING"

$PushgatewayServiceMonitor = @"
apiVersion: monitoring.coreos.com/v1
kind: ServiceMonitor
metadata:
  name: ai-pushgateway
  namespace: $MonitoringNamespace
  labels:
    release: $MonitoringRelease
spec:
  selector:
    matchLabels:
      app: pushgateway
  namespaceSelector:
    matchNames:
      - $MonitoringNamespace
  endpoints:
    - port: http
      interval: 5s
      path: /metrics
"@

$PushgatewayServiceMonitor |
    kubectl apply -f -

Assert-NativeSuccess "Pushgateway ServiceMonitor creation failed."

Write-Host "[PASS] Pushgateway ServiceMonitor ready."

# ============================================================
# FINAL PLATFORM VERIFICATION
# ============================================================

Write-Section "VERIFY PLATFORM"

Write-Host ""
Write-Host "Argo Rollouts:"
kubectl get pods -n $ArgoNamespace
Assert-NativeSuccess "Unable to list Argo Rollouts pods."

Write-Host ""
Write-Host "Monitoring:"
kubectl get pods -n $MonitoringNamespace
Assert-NativeSuccess "Unable to list monitoring pods."

Write-Host ""
Write-Host "Monitoring Services:"
kubectl get svc -n $MonitoringNamespace
Assert-NativeSuccess "Unable to list monitoring services."

Write-Host ""
Write-Host "ServiceMonitors:"
kubectl get servicemonitor -n $MonitoringNamespace
Assert-NativeSuccess "Unable to list ServiceMonitors."

# Verify key services exist.

# The kube-prometheus-stack chart does not expose Prometheus with
# app.kubernetes.io/name=prometheus on the Service in this release.
# Use the deterministic Helm-generated service names instead.

$GrafanaServiceName = "$MonitoringRelease-grafana"
$PrometheusServiceName = "$MonitoringRelease-kube-prometheus-prometheus"
$PushgatewayServiceName = "pushgateway"

kubectl get svc $GrafanaServiceName `
    -n $MonitoringNamespace `
    -o name | Out-Null

Assert-NativeSuccess "Unable to find Grafana service '$GrafanaServiceName'."

kubectl get svc $PrometheusServiceName `
    -n $MonitoringNamespace `
    -o name | Out-Null

Assert-NativeSuccess "Unable to find Prometheus service '$PrometheusServiceName'."

kubectl get svc $PushgatewayServiceName `
    -n $MonitoringNamespace `
    -o name | Out-Null

Assert-NativeSuccess "Unable to find Pushgateway service '$PushgatewayServiceName'."

$GrafanaService = $GrafanaServiceName
$PrometheusService = $PrometheusServiceName
$PushgatewayService = $PushgatewayServiceName

Write-Section "PLATFORM READY"

Write-Host "Argo Rollouts : READY"
Write-Host "Prometheus     : READY ($PrometheusService)"
Write-Host "Grafana        : READY ($GrafanaService)"
Write-Host "Pushgateway    : READY (pushgateway)"
Write-Host ""
Write-Host "Grafana dashboard has NOT been changed."
Write-Host "Port-forwards will be started in a later Jenkins stage."
Write-Host ""

exit 0

