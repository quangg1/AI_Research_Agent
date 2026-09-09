# Kiln / AI_Research_Agent â€” zero-clone quickstart (Windows / PowerShell)
# Usage:
#   irm https://raw.githubusercontent.com/quangg1/AI_Research_Agent/<ref>/scripts/quickstart.ps1 | iex
# Overrides: $env:KILN_REF, $env:KILN_HOME, $env:KILN_REPO, $env:KILN_USE_GHCR=0

$ErrorActionPreference = "Stop"

$RepoUrl = if ($env:KILN_REPO) { $env:KILN_REPO } else { "https://github.com/quangg1/AI_Research_Agent.git" }
$Ref = if ($env:KILN_REF) { $env:KILN_REF } else { "main" }
$InstallDir = if ($env:KILN_HOME) { $env:KILN_HOME } else { Join-Path $env:USERPROFILE ".kiln\AI_Research_Agent" }

Write-Host "==> Kiln quickstart"
Write-Host "    repo: $RepoUrl"
Write-Host "    ref:  $Ref"
Write-Host "    dir:  $InstallDir"

if (-not (Get-Command docker -ErrorAction SilentlyContinue)) {
  Write-Error "Docker is required. Install Docker Desktop, then re-run."
}
docker compose version | Out-Null
if ($LASTEXITCODE -ne 0) {
  Write-Error "Docker Compose v2 is required (docker compose)."
}

$parent = Split-Path -Parent $InstallDir
if (-not (Test-Path $parent)) { New-Item -ItemType Directory -Path $parent | Out-Null }

if (Test-Path (Join-Path $InstallDir ".git")) {
  Write-Host "==> Updating existing checkout..."
  git -C $InstallDir fetch --depth 1 origin $Ref
  git -C $InstallDir checkout -B $Ref FETCH_HEAD
} else {
  Write-Host "==> Shallow-cloning into $InstallDir (managed for you)..."
  if (Test-Path $InstallDir) { Remove-Item -Recurse -Force $InstallDir }
  git clone --depth 1 --branch $Ref $RepoUrl $InstallDir
  if (-not (Test-Path (Join-Path $InstallDir ".git"))) {
    git clone --depth 1 $RepoUrl $InstallDir
    Push-Location $InstallDir
    git checkout $Ref 2>$null
    Pop-Location
  }
}

Set-Location $InstallDir

if (-not (Test-Path ".env")) {
  Write-Host "==> Creating .env from .env.example (dev auth defaults)..."
  Copy-Item ".env.example" ".env"
} else {
  Write-Host "==> Keeping existing .env"
}

$useGhcr = $false
if ((Test-Path "docker-compose.ghcr.yml") -and ($env:KILN_USE_GHCR -ne "0")) {
  Write-Host "==> Trying prebuilt GHCR images..."
  docker compose -f docker-compose.yml -f docker-compose.ghcr.yml -f docker-compose.dev.yml pull 2>$null
  if ($LASTEXITCODE -eq 0) { $useGhcr = $true }
}

if ($useGhcr) {
  Write-Host "==> Using prebuilt GHCR images"
  docker compose -f docker-compose.yml -f docker-compose.ghcr.yml -f docker-compose.dev.yml up -d
} else {
  Write-Host "==> Building from source (first run can take several minutes)..."
  docker compose -f docker-compose.yml -f docker-compose.dev.yml up -d --build
}

$webPort = "5173"
$envLine = Get-Content .env -ErrorAction SilentlyContinue | Where-Object { $_ -match '^WEB_PORT=' } | Select-Object -First 1
if ($envLine) { $webPort = ($envLine -split '=', 2)[1].Trim() }

Write-Host ""
Write-Host "==> Kiln is starting."
Write-Host "    Web UI:        http://localhost:$webPort"
Write-Host "    API health:    http://localhost:3000/health"
Write-Host "    Agent health:  http://localhost:8000/health"
Write-Host ""
Write-Host "    Dev auth is on (AUTH_MODE=dev). Open the UI and submit a research question."
Write-Host "    Add at least one LLM key to $InstallDir\.env (GOOGLE_API_KEY recommended),"
Write-Host "    then recreate agent/api:"
Write-Host "      docker compose -f docker-compose.yml -f docker-compose.dev.yml up -d --force-recreate agent api"
Write-Host ""
Write-Host "    Install dir: $InstallDir"
Write-Host "    Logs:        docker compose -f docker-compose.yml -f docker-compose.dev.yml logs -f"