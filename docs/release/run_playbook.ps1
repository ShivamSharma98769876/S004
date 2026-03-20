param(
    [string]$DbHost = "127.0.0.1",
    [int]$DbPort = 5432,
    [string]$DbName = "tradingpro",
    [string]$DbUser = "shivams",
    [string]$DbPassword = "changeme",
    [switch]$SkipSchema,
    [switch]$ApplyK8s
)

$ErrorActionPreference = "Stop"

function Step($msg) {
    Write-Host ""
    Write-Host "=== $msg ===" -ForegroundColor Cyan
}

function Run-Cmd($cmd) {
    Write-Host ">> $cmd" -ForegroundColor DarkGray
    Invoke-Expression $cmd
}

function Assert-Command($name) {
    if (-not (Get-Command $name -ErrorAction SilentlyContinue)) {
        throw "Required command '$name' not found in PATH."
    }
}

function Test-Http($url, $label) {
    try {
        $resp = Invoke-RestMethod -Uri $url -TimeoutSec 20
        Write-Host "[OK] $label -> $url" -ForegroundColor Green
        return $resp
    } catch {
        throw "[FAIL] $label -> $url : $($_.Exception.Message)"
    }
}

function Invoke-PsqlFile($filePath) {
    $cmd = "psql -h $DbHost -p $DbPort -U $DbUser -d $DbName -f `"$filePath`""
    Run-Cmd $cmd
}

try {
    Step "Validating prerequisites"
    Assert-Command "docker"
    Assert-Command "psql"

    $releaseDir = Split-Path -Parent $MyInvocation.MyCommand.Path
    $w09Dir = Split-Path -Parent $releaseDir
    $w03Dir = Join-Path (Split-Path -Parent $w09Dir) "W03"
    $w04Dir = Join-Path (Split-Path -Parent $w09Dir) "W04"
    $w05Dir = Join-Path (Split-Path -Parent $w09Dir) "W05"
    $w06Dir = Join-Path (Split-Path -Parent $w09Dir) "W06"
    $w07Dir = Join-Path (Split-Path -Parent $w09Dir) "W07"
    $w08Dir = Join-Path (Split-Path -Parent $w09Dir) "W08"

    $schemaFiles = @(
        (Join-Path $w03Dir "analytics_schema.sql"),
        (Join-Path $w04Dir "ranking_schema.sql"),
        (Join-Path $w05Dir "recommendation_schema.sql"),
        (Join-Path $w06Dir "execution_schema.sql"),
        (Join-Path $w07Dir "alert_audit_schema.sql"),
        (Join-Path $w08Dir "strategy_catalog_schema.sql"),
        (Join-Path $w09Dir "migration_bundle.sql")
    )

    foreach ($f in $schemaFiles) {
        if (-not (Test-Path $f)) {
            throw "Required schema file not found: $f"
        }
    }

    Step "Starting postgres service"
    Push-Location $w09Dir
    Run-Cmd "docker compose up -d postgres"
    Pop-Location

    if (-not $SkipSchema) {
        Step "Applying schema files"
        $env:PGPASSWORD = $DbPassword
        foreach ($f in $schemaFiles) {
            Invoke-PsqlFile $f
        }

        Step "Validating schema version marker"
        Run-Cmd "psql -h $DbHost -p $DbPort -U $DbUser -d $DbName -c `"SELECT version_tag, applied_at FROM s004_schema_versions ORDER BY applied_at DESC LIMIT 5;`""
    } else {
        Step "Skipping schema application (SkipSchema switch set)"
    }

    Step "Starting full docker compose stack"
    Push-Location $w09Dir
    Run-Cmd "docker compose up -d"
    Run-Cmd "docker compose ps"
    Pop-Location

    Step "Health checks"
    $health = Test-Http "http://127.0.0.1:8000/api/health" "Backend health"
    Write-Host "Backend response: $($health | ConvertTo-Json -Compress)" -ForegroundColor DarkGreen

    try {
        $null = Invoke-WebRequest -Uri "http://localhost:3000" -TimeoutSec 20
        Write-Host "[OK] Frontend reachable -> http://localhost:3000" -ForegroundColor Green
    } catch {
        Write-Host "[WARN] Frontend did not respond yet: $($_.Exception.Message)" -ForegroundColor Yellow
    }

    try {
        $null = Invoke-WebRequest -Uri "http://localhost:9090/-/healthy" -TimeoutSec 20
        Write-Host "[OK] Prometheus reachable -> http://localhost:9090" -ForegroundColor Green
    } catch {
        Write-Host "[WARN] Prometheus health check failed: $($_.Exception.Message)" -ForegroundColor Yellow
    }

    try {
        $null = Invoke-WebRequest -Uri "http://localhost:3001" -TimeoutSec 20
        Write-Host "[OK] Grafana reachable -> http://localhost:3001" -ForegroundColor Green
    } catch {
        Write-Host "[WARN] Grafana health check failed: $($_.Exception.Message)" -ForegroundColor Yellow
    }

    if ($ApplyK8s) {
        Step "Applying Kubernetes manifests"
        Assert-Command "kubectl"
        $k8sDir = Join-Path $w09Dir "k8s"
        Push-Location $k8sDir
        Run-Cmd "kubectl apply -f .\backend-deployment.yaml"
        Run-Cmd "kubectl apply -f .\frontend-deployment.yaml"
        Run-Cmd "kubectl apply -f .\hpa-backend.yaml"
        Run-Cmd "kubectl get pods"
        Run-Cmd "kubectl get svc"
        Run-Cmd "kubectl get hpa"
        Pop-Location
    } else {
        Step "Skipping Kubernetes apply (ApplyK8s switch not set)"
    }

    Step "Playbook automation completed"
    Write-Host "Manual follow-ups:" -ForegroundColor Cyan
    Write-Host "1) Import Grafana dashboard: .\observability\grafana-dashboard.json"
    Write-Host "2) Execute load test plan: .\release\load_test_plan.md"
    Write-Host "3) Complete go-live checklist: .\release\go_live_checklist.md"
    Write-Host ""
    Write-Host "URLs:" -ForegroundColor Cyan
    Write-Host "- Frontend:   http://localhost:3000"
    Write-Host "- Backend:    http://127.0.0.1:8000"
    Write-Host "- Prometheus: http://localhost:9090"
    Write-Host "- Grafana:    http://localhost:3001"
}
catch {
    Write-Host ""
    Write-Host "Playbook failed: $($_.Exception.Message)" -ForegroundColor Red
    exit 1
}
