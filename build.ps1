<#
.SYNOPSIS
    Build the utteran Windows installer end to end: PyInstaller GUI shell,
    then Inno Setup installer, then SHA-256.

.DESCRIPTION
    One command from a clean checkout to a signed-or-not installer .exe plus
    its .sha256 sidecar:

      1. Sync a dedicated build venv (.venvs\win-gui-build) with the `gui`
         and `build` extras (pyinstaller lives only in `build`, never in a
         profile extra - see pyproject.toml).
      2. Run PyInstaller against packaging\gui.spec (onedir).
      3. Verify the PyInstaller output does not embed the inference core
         (belt-and-suspenders on top of gui.spec's own build-time check).
      4. Compile packaging\installer.iss with Inno Setup, passing the
         version read from pyproject.toml so the installer, the exe
         metadata, and the release tag can never drift apart.
      5. Compute and write the installer's SHA-256 next to it.

    Every previous dist\ and build\ directory is removed first so a stale
    artifact from an earlier version can never masquerade as this run's
    output.

.PARAMETER SignCommand
    Optional. A signtool.exe invocation template (Inno Setup's [Setup]
    SignTool syntax, e.g. 'signtool.exe sign /f cert.pfx /p $p $f') used to
    sign both utteran-gui.exe and the installer itself. Omitted by default:
    this project does not self-sign (see docs/utteran_Phase5d_指示書.md and
    要件定義.md 29章 for why - a fresh self-signed cert cannot buy the
    SmartScreen reputation only a paid publish history earns). Passing this
    is how a future Azure Trusted Signing (or other CA) step plugs in
    without changing installer.iss's structure.

.EXAMPLE
    .\build.ps1

.EXAMPLE
    .\build.ps1 -SignCommand 'signtool.exe sign /f cert.pfx /p secret /fd sha256 $f'
#>
[CmdletBinding()]
param(
    [string]$SignCommand
)

Set-StrictMode -Version Latest
$ErrorActionPreference = "Stop"

$RepoRoot = Split-Path -Parent $MyInvocation.MyCommand.Path
$PackagingDir = Join-Path $RepoRoot "packaging"
$DistDir = Join-Path $RepoRoot "dist"
$BuildDir = Join-Path $RepoRoot "build"
$GuiDistDir = Join-Path $DistDir "utteran-gui"
$PortableDistDir = Join-Path $DistDir "portable-stage"
$BuildVenvDir = Join-Path $RepoRoot ".venvs\win-gui-build"

function Write-BuildStep {
    param([string]$Message)
    Write-Host "`n==> $Message" -ForegroundColor Cyan
}

function Resolve-ProjectVersion {
    $PyprojectPath = Join-Path $RepoRoot "pyproject.toml"
    $Content = Get-Content -LiteralPath $PyprojectPath -Raw
    if ($Content -notmatch '(?m)^\s*version\s*=\s*"([^"]+)"\s*$') {
        throw "Could not resolve project version from pyproject.toml"
    }
    return $Matches[1]
}

function Find-InnoSetupCompiler {
    $Existing = Get-Command "ISCC.exe" -ErrorAction SilentlyContinue
    if ($null -ne $Existing) {
        return $Existing.Source
    }
    $Candidates = @(
        (Join-Path ${env:ProgramFiles(x86)} "Inno Setup 6\ISCC.exe"),
        (Join-Path $env:ProgramFiles "Inno Setup 6\ISCC.exe"),
        (Join-Path $env:LocalAppData "Programs\Inno Setup 6\ISCC.exe")
    )
    foreach ($Candidate in $Candidates) {
        if ($Candidate -and (Test-Path -LiteralPath $Candidate -PathType Leaf)) {
            return $Candidate
        }
    }
    return $null
}

# Fail fast, before touching uv/PyInstaller, when Inno Setup is not
# installed: this is by far the most likely missing prerequisite on a
# fresh machine (or CI, which intentionally does not install it - see
# 要件定義.md 29章), and there is no point spending minutes syncing a build
# venv only to fail at the very last step.
$Iscc = Find-InnoSetupCompiler
if ($null -eq $Iscc) {
    Write-Error @"
Inno Setup 6 (ISCC.exe) was not found on PATH or in its default install location.
Install it, then re-run this script:
  https://jrsoftware.org/isdl.php
Or via winget:
  winget install --id JRSoftware.InnoSetup -e
"@
    exit 2
}

$Version = Resolve-ProjectVersion
Write-Host "utteran installer build - version $Version"
Write-Host "Inno Setup compiler: $Iscc"

Write-BuildStep "Cleaning previous build output"
foreach ($Path in @($DistDir, $BuildDir)) {
    if (Test-Path -LiteralPath $Path) {
        Remove-Item -LiteralPath $Path -Recurse -Force
    }
}

Write-BuildStep "Syncing GUI build environment ($BuildVenvDir)"
$env:UV_PROJECT_ENVIRONMENT = $BuildVenvDir
uv sync --locked --extra gui --extra build
if ($LASTEXITCODE -ne 0) {
    throw "uv sync failed (exit $LASTEXITCODE)"
}
$PythonExe = Join-Path $BuildVenvDir "Scripts\python.exe"

Write-BuildStep "Running PyInstaller (onedir)"
$env:UTTERAN_BUILD_VERSION = $Version
$env:UTTERAN_BUILD_FLAVOR = "installer"
$env:UTTERAN_GUI_DIST_NAME = "utteran-gui"
& $PythonExe -m PyInstaller --noconfirm --distpath $DistDir --workpath $BuildDir `
    (Join-Path $PackagingDir "gui.spec")
if ($LASTEXITCODE -ne 0) {
    throw "PyInstaller failed (exit $LASTEXITCODE)"
}
$GuiExe = Join-Path $GuiDistDir "utteran-gui.exe"
if (-not (Test-Path -LiteralPath $GuiExe -PathType Leaf)) {
    throw "PyInstaller did not produce $GuiExe"
}
$GuiVersionInfo = (Get-Item -LiteralPath $GuiExe).VersionInfo
foreach ($EmbeddedVersion in @($GuiVersionInfo.ProductVersion, $GuiVersionInfo.FileVersion)) {
    if ($EmbeddedVersion -ne $Version) {
        throw "GUI embedded version '$EmbeddedVersion' does not match project version '$Version'"
    }
}

Write-BuildStep "Verifying the distributable excludes the inference core"
# Belt-and-suspenders on top of packaging\gui.spec's own build-time check
# (which inspects PyInstaller's dependency graph): this instead inspects
# what actually landed on disk, catching the case where a forbidden module
# is present only as data rather than as an analyzed pure-Python module.
$ForbiddenDirNames = @("torch", "utteran", "faster_whisper", "pyannote", "ctranslate2")
foreach ($Name in $ForbiddenDirNames) {
    $Hit = Get-ChildItem -LiteralPath $GuiDistDir -Recurse -Directory -Filter $Name -ErrorAction SilentlyContinue |
        Select-Object -First 1
    if ($null -ne $Hit) {
        throw "Distributable unexpectedly bundles '$Name': $($Hit.FullName)"
    }
}

Write-BuildStep "Compiling installer with Inno Setup"
$IsccArgs = [System.Collections.Generic.List[string]]::new()
$IsccArgs.Add("/DMyAppVersion=$Version")
if ($SignCommand) {
    $IsccArgs.Add("/DSignInstaller=1")
    $IsccArgs.Add("/Sutteran=$SignCommand")
}
$IsccArgs.Add((Join-Path $PackagingDir "installer.iss"))
& $Iscc @IsccArgs
if ($LASTEXITCODE -ne 0) {
    throw "Inno Setup compilation failed (exit $LASTEXITCODE)"
}

$InstallerDir = Join-Path $DistDir "installer"
$InstallerPath = Join-Path $InstallerDir "utteran-setup-$Version.exe"
if (-not (Test-Path -LiteralPath $InstallerPath -PathType Leaf)) {
    throw "Expected installer executable was not found: $InstallerPath"
}
$Installer = Get-Item -LiteralPath $InstallerPath
$InstallerVersionInfo = $Installer.VersionInfo
foreach ($EmbeddedVersion in @($InstallerVersionInfo.ProductVersion, $InstallerVersionInfo.FileVersion)) {
    if ($EmbeddedVersion.Trim() -ne $Version) {
        throw "Installer embedded version '$EmbeddedVersion' does not match project version '$Version'"
    }
}

Write-BuildStep "Building portable GUI shell"
$env:UTTERAN_BUILD_FLAVOR = "portable"
$env:UTTERAN_GUI_DIST_NAME = "portable-stage"
& $PythonExe -m PyInstaller --noconfirm --distpath $DistDir `
    --workpath (Join-Path $BuildDir "portable") (Join-Path $PackagingDir "gui.spec")
if ($LASTEXITCODE -ne 0) {
    throw "Portable PyInstaller build failed (exit $LASTEXITCODE)"
}
$PortableExe = Join-Path $PortableDistDir "utteran-gui.exe"
if (-not (Test-Path -LiteralPath $PortableExe -PathType Leaf)) {
    throw "Portable PyInstaller did not produce $PortableExe"
}
$PortableVersionInfo = (Get-Item -LiteralPath $PortableExe).VersionInfo
foreach ($EmbeddedVersion in @($PortableVersionInfo.ProductVersion, $PortableVersionInfo.FileVersion)) {
    if ($EmbeddedVersion -ne $Version) {
        throw "Portable GUI embedded version '$EmbeddedVersion' does not match project version '$Version'"
    }
}

Write-BuildStep "Adding portable setup payload"
$PortableFiles = @(
    "pyproject.toml", "uv.lock", "setup.ps1", "run.ps1", ".env.example",
    "LICENSE", "THIRD_PARTY_NOTICES.md"
)
foreach ($RelativePath in $PortableFiles) {
    Copy-Item -LiteralPath (Join-Path $RepoRoot $RelativePath) -Destination $PortableDistDir
}
Copy-Item -LiteralPath (Join-Path $PackagingDir "README.portable.md") `
    -Destination (Join-Path $PortableDistDir "README.md")
foreach ($Directory in @("icon", "src\utteran", "src\utteran_gui", "src\utteran_paths")) {
    $Destination = Join-Path $PortableDistDir $Directory
    New-Item -ItemType Directory -Path $Destination -Force | Out-Null
    Copy-Item -Path (Join-Path $RepoRoot "$Directory\*") -Destination $Destination -Recurse -Force
}
Get-ChildItem -LiteralPath $PortableDistDir -Recurse -Force |
    Where-Object { $_.Name -eq "__pycache__" -or $_.Extension -eq ".pyc" } |
    Remove-Item -Recurse -Force

Write-BuildStep "Computing SHA-256"
$Hash = Get-FileHash -LiteralPath $Installer.FullName -Algorithm SHA256
$HashLine = "$($Hash.Hash.ToLowerInvariant())  $($Installer.Name)"
$HashFile = "$($Installer.FullName).sha256"
$Utf8NoBom = New-Object System.Text.UTF8Encoding($false)
[IO.File]::WriteAllText($HashFile, "$HashLine`n", $Utf8NoBom)
Write-Host $HashLine

$PortablePath = Join-Path $DistDir "utteran-portable-$Version.zip"
Write-BuildStep "Creating portable ZIP"
Compress-Archive -Path (Join-Path $PortableDistDir "*") -DestinationPath $PortablePath `
    -CompressionLevel Optimal
$Portable = Get-Item -LiteralPath $PortablePath
$PortableHash = Get-FileHash -LiteralPath $Portable.FullName -Algorithm SHA256
$PortableHashLine = "$($PortableHash.Hash.ToLowerInvariant())  $($Portable.Name)"
$PortableHashFile = "$($Portable.FullName).sha256"
[IO.File]::WriteAllText($PortableHashFile, "$PortableHashLine`n", $Utf8NoBom)
Write-Host $PortableHashLine

Write-Host "`nBuild complete." -ForegroundColor Green
Write-Host "Installer: $($Installer.FullName)"
Write-Host "SHA-256:   $HashFile"
Write-Host "Portable:  $($Portable.FullName)"
Write-Host "SHA-256:   $PortableHashFile"
