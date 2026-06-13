param(
    [string]$DistDir = "dist/client",
    [string]$WorkDir = ".cache/pyinstaller",
    [string]$SpecDir = ".cache/pyinstaller-spec",
    [string]$BuildId,
    [switch]$DryRun
)

$ErrorActionPreference = "Stop"
$OutputEncoding = [System.Text.UTF8Encoding]::new()
[Console]::OutputEncoding = [System.Text.UTF8Encoding]::new()
[Console]::InputEncoding = [System.Text.UTF8Encoding]::new()
chcp 65001 | Out-Null

$RepoRoot = Split-Path -Parent $MyInvocation.MyCommand.Path
$ClientDir = Join-Path $RepoRoot "client"
if ([string]::IsNullOrWhiteSpace($BuildId)) {
    $BuildId = (Get-Date).ToString("yyyyMMdd-HHmmss")
}
if ($BuildId -match '[\\/:*?"<>|]') {
    throw "Invalid -BuildId '$BuildId'. BuildId must be a Windows-safe folder name."
}

$env:UV_LINK_MODE = "copy"
$env:UV_CACHE_DIR = Join-Path $RepoRoot ".cache/uv"
$env:TMP = Join-Path $RepoRoot ".cache/tmp"
$env:TEMP = Join-Path $RepoRoot ".cache/tmp"

New-Item -ItemType Directory -Force -Path $env:UV_CACHE_DIR, $env:TMP | Out-Null

$Args = @(
    "run",
    "python",
    "-m",
    "auto_backup_client.release_build",
    "--dist-dir",
    (Join-Path $RepoRoot $DistDir),
    "--work-dir",
    (Join-Path $RepoRoot $WorkDir),
    "--spec-dir",
    (Join-Path $RepoRoot $SpecDir)
)
$Args += @(
    "--build-id",
    $BuildId
)

if ($DryRun) {
    $Args += "--dry-run"
}

Push-Location $ClientDir
try {
    & uv @Args
    if ($LASTEXITCODE -ne 0) {
        throw "client package build failed with exit code $LASTEXITCODE"
    }
}
finally {
    Pop-Location
}
