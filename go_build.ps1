# go_build.ps1
# Build a Go service binary for server deployment.
# Defaults are tailored for this repository:
#   - module: cloud-api/
#   - service entry: cmd/cloud-api
#   - target: linux/amd64
#   - output: dist/cloud-api/<yyyyMMdd-HHmmss>/linux-amd64/cloud-api

param(
    [string]$ModuleDir,                       # Go module directory. Auto-detected if omitted.
    [string]$ServiceName,                     # Entry point subdir under cmd/. Auto-detected if omitted.
    [string]$OutputDir,                       # Output directory root. Defaults to dist/<service>; <BuildId> is always appended.
    [string]$OutputName,                      # Optional: override binary output name.
    [string]$BuildId,                         # Build batch id. Defaults to yyyyMMdd-HHmmss.
    [ValidateSet('Module', 'Service')]
    [string]$NameSource = "Module",           # Source for binary name when -OutputName is omitted.
    [string]$GoOS = "linux",
    [string]$GoArch = "amd64",
    [switch]$CompressWithUpx,
    [string]$LdFlags = "-s -w"
)

Set-StrictMode -Version Latest
$ErrorActionPreference = "Stop"

$OutputEncoding = [System.Text.UTF8Encoding]::new()
[Console]::OutputEncoding = [System.Text.UTF8Encoding]::new()
[Console]::InputEncoding = [System.Text.UTF8Encoding]::new()
if (Get-Command chcp.com -ErrorAction SilentlyContinue) {
    chcp.com 65001 | Out-Null
}

$repoRoot = $PSScriptRoot
Set-Location $repoRoot

# -----------------------------------------------------------------------------
# Helper functions
# -----------------------------------------------------------------------------
function Fail-Build {
    param([string]$Message)
    Write-Host "ERROR: $Message" -ForegroundColor Red
    exit 1
}

function Resolve-ProjectPath {
    param(
        [Parameter(Mandatory = $true)]
        [string]$Path
    )

    if ([System.IO.Path]::IsPathRooted($Path)) {
        return [System.IO.Path]::GetFullPath($Path)
    }

    return [System.IO.Path]::GetFullPath((Join-Path $repoRoot $Path))
}

function Get-RelativePathFromRepo {
    param([string]$Path)

    $repoRootFullPath = [System.IO.Path]::GetFullPath($repoRoot)
    if (-not $repoRootFullPath.EndsWith([System.IO.Path]::DirectorySeparatorChar)) {
        $repoRootFullPath += [System.IO.Path]::DirectorySeparatorChar
    }

    $targetFullPath = [System.IO.Path]::GetFullPath($Path)
    $repoRootUri = [System.Uri]::new($repoRootFullPath)
    $targetUri = [System.Uri]::new($targetFullPath)
    $relativeUri = $repoRootUri.MakeRelativeUri($targetUri)
    $relative = [System.Uri]::UnescapeDataString($relativeUri.ToString()).Replace('/', [System.IO.Path]::DirectorySeparatorChar)

    if ([string]::IsNullOrWhiteSpace($relative)) {
        return "."
    }
    return $relative
}

function Resolve-GoModuleRoot {
    param([string]$RequestedModuleDir)

    if (-not [string]::IsNullOrWhiteSpace($RequestedModuleDir)) {
        $requestedPath = Resolve-ProjectPath $RequestedModuleDir
        if (-not (Test-Path -LiteralPath (Join-Path $requestedPath "go.mod"))) {
            Fail-Build "go.mod not found under requested -ModuleDir '$RequestedModuleDir' ($requestedPath)."
        }
        return (Resolve-Path -LiteralPath $requestedPath).Path
    }

    $rootGoMod = Join-Path $repoRoot "go.mod"
    if (Test-Path -LiteralPath $rootGoMod) {
        return $repoRoot
    }

    $cloudApiPath = Join-Path $repoRoot "cloud-api"
    if (Test-Path -LiteralPath (Join-Path $cloudApiPath "go.mod")) {
        return (Resolve-Path -LiteralPath $cloudApiPath).Path
    }

    $moduleDirs = @(Get-ChildItem -LiteralPath $repoRoot -Directory | Where-Object {
        Test-Path -LiteralPath (Join-Path $_.FullName "go.mod")
    } | Sort-Object Name)

    if ($moduleDirs.Count -eq 1) {
        return $moduleDirs[0].FullName
    }

    if ($moduleDirs.Count -gt 1) {
        $names = ($moduleDirs | ForEach-Object { Get-RelativePathFromRepo $_.FullName }) -join ", "
        Fail-Build "Multiple Go modules found ($names). Please specify -ModuleDir."
    }

    Fail-Build "No go.mod found at repository root or in a direct child directory. Please specify -ModuleDir."
}

function Quote-ProcessArgument {
    param([string]$Argument)

    if ($null -eq $Argument -or $Argument.Length -eq 0) {
        return '""'
    }
    if ($Argument -notmatch '[\s"]') {
        return $Argument
    }

    $result = '"'
    $backslashes = 0
    foreach ($char in $Argument.ToCharArray()) {
        if ($char -eq '\') {
            $backslashes++
            continue
        }
        if ($char -eq '"') {
            if ($backslashes -gt 0) {
                $result += ('\' * ($backslashes * 2))
            }
            $result += '\"'
            $backslashes = 0
            continue
        }
        if ($backslashes -gt 0) {
            $result += ('\' * $backslashes)
            $backslashes = 0
        }
        $result += $char
    }
    if ($backslashes -gt 0) {
        $result += ('\' * ($backslashes * 2))
    }
    $result += '"'

    return $result
}

function Invoke-GoCapture {
    param(
        [string[]]$Arguments,
        [string]$WorkingDirectory
    )

    $startInfo = [System.Diagnostics.ProcessStartInfo]::new()
    $startInfo.FileName = "go"
    $startInfo.Arguments = ($Arguments | ForEach-Object { Quote-ProcessArgument $_ }) -join " "
    $startInfo.WorkingDirectory = $WorkingDirectory
    $startInfo.UseShellExecute = $false
    $startInfo.RedirectStandardOutput = $true
    $startInfo.RedirectStandardError = $true

    $process = [System.Diagnostics.Process]::new()
    $process.StartInfo = $startInfo

    try {
        [void]$process.Start()
        $stdout = $process.StandardOutput.ReadToEnd()
        $stderr = $process.StandardError.ReadToEnd()
        $process.WaitForExit()
        $output = (($stdout, $stderr) -join "").Trim()
        $exitCode = $process.ExitCode
    } finally {
        $process.Dispose()
    }

    [pscustomobject]@{
        ExitCode = $exitCode
        Output   = $output
    }
}

function Get-GoVersionFromText {
    param([string]$VersionOutput)

    if ($VersionOutput -notmatch "go version go(?<Version>\d+\.\d+(?:\.\d+)?)") {
        return $null
    }
    return [version]$Matches.Version
}

function Get-BinaryNameFromModule {
    param([string]$ModuleName)

    if ($ModuleName -match '/') {
        $parts = $ModuleName -split '/'
        $lastPart = $parts[-1]
        if ([string]::IsNullOrWhiteSpace($lastPart)) {
            return $null
        }
        return $lastPart
    }

    return $ModuleName
}

function Get-OutputFileName {
    param(
        [string]$BaseName,
        [string]$TargetGoOS
    )

    if ($TargetGoOS -eq "windows" -and [System.IO.Path]::GetExtension($BaseName) -eq "") {
        return "$BaseName.exe"
    }

    return $BaseName
}

function Get-ValidBuildId {
    param([string]$RequestedBuildId)

    if ([string]::IsNullOrWhiteSpace($RequestedBuildId)) {
        return (Get-Date).ToString("yyyyMMdd-HHmmss")
    }

    if ($RequestedBuildId -match '[\\/:*?"<>|]') {
        Fail-Build "Invalid -BuildId '$RequestedBuildId'. BuildId must be a Windows-safe folder name."
    }

    return $RequestedBuildId
}

# -----------------------------------------------------------------------------
# Resolve Go module
# -----------------------------------------------------------------------------
$moduleRoot = Resolve-GoModuleRoot $ModuleDir
$moduleRelativePath = Get-RelativePathFromRepo $moduleRoot
$goModPath = Join-Path $moduleRoot "go.mod"

Write-Host "Repository root: $repoRoot" -ForegroundColor Gray
Write-Host "Go module root: $moduleRelativePath" -ForegroundColor Cyan

# -----------------------------------------------------------------------------
# Parse go.mod
# -----------------------------------------------------------------------------
Write-Host "Parsing go.mod ..." -ForegroundColor Cyan

$goModContent = Get-Content -LiteralPath $goModPath -Raw -Encoding UTF8

$moduleMatch = [regex]::Match($goModContent, '^module\s+(\S+)', 'Multiline')
if (-not $moduleMatch.Success) {
    Fail-Build "go.mod does not contain a module declaration."
}
$moduleName = $moduleMatch.Groups[1].Value

$goVersionMatch = [regex]::Match($goModContent, '^go\s+(\d+\.\d+(?:\.\d+)?)', 'Multiline')
if (-not $goVersionMatch.Success) {
    Fail-Build "go.mod does not specify a Go version (missing 'go 1.x' directive)."
}
$requiredGoVersion = [version]$goVersionMatch.Groups[1].Value
Write-Host "Module: $moduleName" -ForegroundColor Gray
Write-Host "Requires Go >= $requiredGoVersion" -ForegroundColor Gray

$derivedModuleName = Get-BinaryNameFromModule $moduleName
if ([string]::IsNullOrWhiteSpace($derivedModuleName)) {
    Fail-Build "Cannot derive binary name from module '$moduleName'. Please specify -OutputName."
}

# -----------------------------------------------------------------------------
# Detect service entry point from cmd/
# -----------------------------------------------------------------------------
$cmdPath = Join-Path $moduleRoot "cmd"
if (-not (Test-Path -LiteralPath $cmdPath)) {
    Fail-Build "cmd/ directory not found at $cmdPath"
}

$availableServices = @(Get-ChildItem -LiteralPath $cmdPath -Directory | Where-Object {
    Test-Path -LiteralPath (Join-Path $_.FullName "main.go")
} | ForEach-Object { $_.Name } | Sort-Object)

if ($availableServices.Count -eq 0) {
    Fail-Build "No service found under $moduleRelativePath/cmd (no subdirectory contains main.go)."
}

if ([string]::IsNullOrWhiteSpace($ServiceName)) {
    if ($availableServices.Count -eq 1) {
        $ServiceName = $availableServices[0]
        Write-Host "Auto-detected service entry point: $ServiceName" -ForegroundColor Yellow
    } else {
        Write-Host "Available services: $($availableServices -join ', ')" -ForegroundColor Yellow
        Fail-Build "Multiple services found. Please specify -ServiceName."
    }
} else {
    if ($availableServices -notcontains $ServiceName) {
        Write-Host "Available services: $($availableServices -join ', ')" -ForegroundColor Yellow
        Fail-Build "Service '$ServiceName' not found under $moduleRelativePath/cmd (no main.go in cmd/$ServiceName)."
    }
}

# -----------------------------------------------------------------------------
# Determine final binary output path
# -----------------------------------------------------------------------------
$BuildId = Get-ValidBuildId $BuildId
Write-Host "Build ID: $BuildId" -ForegroundColor Gray

if (-not [string]::IsNullOrWhiteSpace($OutputName)) {
    $baseOutputName = $OutputName
    Write-Host "Binary name overridden by -OutputName: $baseOutputName" -ForegroundColor Gray
} else {
    switch ($NameSource) {
        'Module' {
            $baseOutputName = $derivedModuleName
            Write-Host "Binary name derived from module: $baseOutputName" -ForegroundColor Gray
        }
        'Service' {
            $baseOutputName = $ServiceName
            Write-Host "Binary name derived from service: $baseOutputName" -ForegroundColor Gray
        }
        default {
            Fail-Build "Invalid -NameSource value: $NameSource. Must be 'Module' or 'Service'."
        }
    }
}

if ([string]::IsNullOrWhiteSpace($baseOutputName)) {
    Fail-Build "Binary name is empty. Check -OutputName or -NameSource settings."
}

$outputFileName = Get-OutputFileName -BaseName $baseOutputName -TargetGoOS $GoOS

if ([string]::IsNullOrWhiteSpace($OutputDir)) {
    $outputRootDir = Join-Path $repoRoot (Join-Path "dist" $ServiceName)
} else {
    $outputRootDir = Resolve-ProjectPath $OutputDir
}
$resolvedOutputDir = Join-Path (Join-Path $outputRootDir $BuildId) "$GoOS-$GoArch"
$resolvedOutputDir = [System.IO.Path]::GetFullPath($resolvedOutputDir)
New-Item -ItemType Directory -Force -Path $resolvedOutputDir | Out-Null

$outputPath = Join-Path $resolvedOutputDir $outputFileName
$outputRelativePath = Get-RelativePathFromRepo $outputPath
Write-Host "Output: $outputRelativePath" -ForegroundColor Cyan

# -----------------------------------------------------------------------------
# Prepare local caches
# -----------------------------------------------------------------------------
Write-Host "[1/5] Preparing local Go caches..." -ForegroundColor Cyan
$env:GOCACHE = Join-Path $repoRoot (Join-Path ".cache\go-build" (Join-Path $ServiceName (Join-Path $BuildId "$GoOS-$GoArch")))
$env:GOMODCACHE = Join-Path $repoRoot ".cache\go-mod"
New-Item -ItemType Directory -Force -Path $env:GOCACHE | Out-Null
New-Item -ItemType Directory -Force -Path $env:GOMODCACHE | Out-Null
Write-Host "GOCACHE: $(Get-RelativePathFromRepo $env:GOCACHE)" -ForegroundColor Gray
Write-Host "GOMODCACHE: $(Get-RelativePathFromRepo $env:GOMODCACHE)" -ForegroundColor Gray

# -----------------------------------------------------------------------------
# Check Go toolchain version
# -----------------------------------------------------------------------------
Write-Host "[2/5] Checking Go toolchain..." -ForegroundColor Cyan
$goToolchainResult = Invoke-GoCapture -Arguments @("env", "GOTOOLCHAIN") -WorkingDirectory $moduleRoot
if ($goToolchainResult.ExitCode -ne 0) {
    Fail-Build "Unable to read GOTOOLCHAIN. Output: $($goToolchainResult.Output)"
}
$goToolchain = $goToolchainResult.Output

$goVersionResult = Invoke-GoCapture -Arguments @("version") -WorkingDirectory $moduleRoot
if ($goVersionResult.ExitCode -ne 0) {
    Fail-Build "Unable to run go version. Install Go $requiredGoVersion or newer, or allow GOTOOLCHAIN=auto to select it. Output: $($goVersionResult.Output)"
}

$goVersionText = $goVersionResult.Output
$currentGoVersion = Get-GoVersionFromText $goVersionText
if ($null -eq $currentGoVersion) {
    Fail-Build "Unable to parse Go version from: $goVersionText"
}

if ($currentGoVersion -lt $requiredGoVersion) {
    Fail-Build "Detected $goVersionText with GOTOOLCHAIN=$goToolchain.`nThis build requires Go $requiredGoVersion or newer; install a newer Go toolchain or set GOTOOLCHAIN=auto."
}

Write-Host "Using $goVersionText (GOTOOLCHAIN=$goToolchain)" -ForegroundColor Gray

# -----------------------------------------------------------------------------
# Build
# -----------------------------------------------------------------------------
Write-Host "[3/5] Building Go binary for $GoOS/$GoArch ..." -ForegroundColor Cyan

$env:GOOS = $GoOS
$env:GOARCH = $GoArch
$env:CGO_ENABLED = "0"

# -----------------------------------------------------------------------------
# Check target standard library availability
# -----------------------------------------------------------------------------
Write-Host "Checking target Go standard library for $GoOS/$GoArch ..." -ForegroundColor Cyan
$stdLibPackages = @("crypto/rand", "net/url", "log/slog", "os/signal", "runtime")
$stdLibResult = Invoke-GoCapture -Arguments (@("list") + $stdLibPackages) -WorkingDirectory $moduleRoot
if ($stdLibResult.ExitCode -ne 0) {
    $goEnvResult = Invoke-GoCapture -Arguments @("env", "GOROOT", "GOTOOLCHAIN", "GOOS", "GOARCH", "GOCACHE", "GOMODCACHE") -WorkingDirectory $moduleRoot
    $goEnvSummary = $goEnvResult.Output
    Fail-Build "Target Go standard library check failed. This can be caused by a polluted Go build cache or a broken Go toolchain. Output: $($stdLibResult.Output)`nGo env summary:`n$goEnvSummary"
}

$entryPoint = "./cmd/$ServiceName"
$buildArgs = @(
    "build",
    "-trimpath",
    "-buildvcs=false",
    "-ldflags=$LdFlags",
    "-o", $outputPath,
    $entryPoint
)

Write-Host "Running in ${moduleRelativePath}: go $($buildArgs -join ' ')" -ForegroundColor Gray
$buildResult = Invoke-GoCapture -Arguments $buildArgs -WorkingDirectory $moduleRoot
if ($buildResult.Output) {
    Write-Host $buildResult.Output
}
if ($buildResult.ExitCode -ne 0) {
    Fail-Build "Build failed"
}

Write-Host "[4/5] Build succeeded: $outputRelativePath" -ForegroundColor Green

$fileInfo = Get-Item -LiteralPath $outputPath
Write-Host "File size: $([math]::Round($fileInfo.Length / 1MB, 2)) MB" -ForegroundColor Yellow

# -----------------------------------------------------------------------------
# Optional UPX compression
# -----------------------------------------------------------------------------
if ($CompressWithUpx) {
    Write-Host "[5/5] Compressing with UPX..." -ForegroundColor Cyan
    if (Get-Command upx -ErrorAction SilentlyContinue) {
        upx --best --lzma $outputPath
        $compressedInfo = Get-Item -LiteralPath $outputPath
        Write-Host "Compressed size: $([math]::Round($compressedInfo.Length / 1MB, 2)) MB" -ForegroundColor Green
    } else {
        Write-Host "Warning: UPX not found. Skipping compression." -ForegroundColor Yellow
    }
} else {
    Write-Host "[5/5] Skipping UPX compression." -ForegroundColor Gray
}

Write-Host "Done. Binary: $outputRelativePath" -ForegroundColor Cyan
