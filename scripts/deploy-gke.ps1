[CmdletBinding()]
param(
    [Parameter(Mandatory = $true)][string]$ProjectId,
    [string]$Region = "us-central1",
    [string]$Cluster = "load-shed",
    [string]$Tag = (Get-Date -Format "yyyyMMddHHmmss")
)
$ErrorActionPreference = "Stop"
function Run {
    param([string]$Tool, [Parameter(ValueFromRemainingArguments = $true)][string[]]$Arguments)
    & $Tool @Arguments
    if ($LASTEXITCODE -ne 0) { throw "$Tool failed with exit code $LASTEXITCODE" }
}
foreach ($tool in @("gcloud", "kubectl", "docker")) {
    if (-not (Get-Command $tool -ErrorAction SilentlyContinue)) { throw "Install $tool and add it to PATH first." }
}
Push-Location (Join-Path $PSScriptRoot "..")
try {
    Run docker @("info", "--format", "{{.OSType}}")
    Run gcloud @("projects", "describe", $ProjectId, "--format=value(projectId)")
    Run gcloud @("services", "enable", "container.googleapis.com", "artifactregistry.googleapis.com", "monitoring.googleapis.com", "logging.googleapis.com", "compute.googleapis.com", "iam.googleapis.com", "--project=$ProjectId")
    & gcloud artifacts repositories describe load-shed "--location=$Region" "--project=$ProjectId" 2>$null
    if ($LASTEXITCODE -ne 0) {
        Run gcloud @("artifacts", "repositories", "create", "load-shed", "--repository-format=docker", "--location=$Region", "--project=$ProjectId")
    }
    $nodeAccount = "load-shed-nodes@$ProjectId.iam.gserviceaccount.com"
    & gcloud iam service-accounts describe $nodeAccount "--project=$ProjectId" 2>$null
    if ($LASTEXITCODE -ne 0) {
        Run gcloud @("iam", "service-accounts", "create", "load-shed-nodes", "--project=$ProjectId")
        Start-Sleep -Seconds 10
    }
    foreach ($role in @("roles/container.defaultNodeServiceAccount", "roles/artifactregistry.reader")) {
        Run gcloud @("projects", "add-iam-policy-binding", $ProjectId, "--member=serviceAccount:$nodeAccount", "--role=$role", "--condition=None", "--quiet")
    }
    & gcloud container clusters describe $Cluster "--region=$Region" "--project=$ProjectId" 2>$null
    if ($LASTEXITCODE -ne 0) {
        Run gcloud @("container", "clusters", "create-auto", $Cluster, "--region=$Region", "--project=$ProjectId", "--service-account=$nodeAccount", "--release-channel=regular")
    }
    Run gcloud @("container", "clusters", "update", $Cluster, "--region=$Region", "--project=$ProjectId", "--enable-managed-prometheus")
    Run gcloud @("container", "clusters", "get-credentials", $Cluster, "--region=$Region", "--project=$ProjectId")
    $image = "$Region-docker.pkg.dev/$ProjectId/load-shed/load-shed-api:$Tag"
    Run gcloud @("auth", "configure-docker", "$Region-docker.pkg.dev", "--quiet")
    Run docker @("build", "--platform=linux/amd64", "-t", $image, ".")
    Run docker @("push", $image)
    $rendered = & kubectl kustomize deploy/gke
    if ($LASTEXITCODE -ne 0) { throw "Kustomize render failed" }
    $rendered = ($rendered -join "`n").Replace("REGION-docker.pkg.dev/PROJECT_ID/load-shed/load-shed-api:REPLACE_WITH_COMMIT", $image)
    $manifest = Join-Path ([System.IO.Path]::GetTempPath()) "load-shed-$Tag.yaml"
    [System.IO.File]::WriteAllText($manifest, $rendered)
    Run kubectl @("apply", "-f", $manifest)
    Run kubectl @("-n", "load-shed", "rollout", "status", "deployment/load-shed-api", "--timeout=600s")
    Run kubectl @("-n", "load-shed", "rollout", "status", "deployment/load-shed-upstream", "--timeout=600s")
    Run gcloud @("monitoring", "dashboards", "create", "--config-from-file=dashboards/cloud-monitoring.json", "--project=$ProjectId")
    Run kubectl @("-n", "load-shed", "get", "ingress,hpa,pods")
    Write-Host "Image: $image"
    Write-Host "Ingress may take several minutes to receive an IP and healthy backends. Follow docs/gke.md for verification."
} finally { Pop-Location }
