[CmdletBinding()]
param(
    [ValidateSet("cpu", "cuda", "intel", "vulkan", "gui")]
    [string]$Profile,
    [switch]$List,
    [ValidateSet("cpu", "cuda", "intel", "vulkan", "gui")]
    [string]$Remove,
    [ValidateSet("cpu", "cuda", "intel", "vulkan")]
    [string]$SetDefault,
    [switch]$SkipFfmpeg,
    [string]$VenvDir,
    [switch]$Yes
)

Set-StrictMode -Version Latest
$ErrorActionPreference = "Stop"

if ([Console]::IsOutputRedirected) {
    # When this script's own stdout is piped rather than attached to a real
    # console (as when the GUI setup wizard launches it via
    # SetupWizardService.start_venv_build), Windows PowerShell 5.1 still
    # encodes Write-Host/Write-Step output using [Console]::OutputEncoding,
    # which for a non-console process resolves to the OEM codepage (e.g.
    # cp932 on Japanese Windows) - not UTF-8. The wizard decodes the pipe as
    # UTF-8 (utteran_gui.processes.build_popen_kwargs), so without this the
    # Japanese progress text it displays comes out as mojibake. Scoped to
    # the redirected case only, so interactive console runs (a user's own
    # terminal, whose OutputEncoding already matches its console codepage)
    # are unaffected.
    try {
        [Console]::OutputEncoding = [System.Text.Encoding]::UTF8
    }
    catch {
        # Some hosts refuse to change OutputEncoding; leave the default.
    }
}

$ProjectRoot = Split-Path -Parent $MyInvocation.MyCommand.Path
$DataRoot = Join-Path ([Environment]::GetFolderPath("LocalApplicationData")) "utteran"
$BinDir = Join-Path $DataRoot "bin"
$BundledFfmpeg = Join-Path $BinDir "ffmpeg.exe"
$EnvPath = Join-Path $ProjectRoot ".env"
$EnvExample = Join-Path $ProjectRoot ".env.example"
$LegacyDefaultEnvironment = Join-Path $ProjectRoot ".venv"
$LegacyWindowsEnvironment = Join-Path $ProjectRoot ".venv-windows"
$FfmpegUrl = "https://www.gyan.dev/ffmpeg/builds/ffmpeg-release-essentials.zip"
$FfmpegChecksumUrl = "$FfmpegUrl.sha256"
$UvReleaseApiUrl = "https://api.github.com/repos/astral-sh/uv/releases/latest"
$UvAssetName = "uv-x86_64-pc-windows-msvc.zip"

# Profile -> extras, matching src/utteran/profiles.py's PROFILE_EXTRAS. Kept
# in sync manually since this script has no Python runtime available before
# the first `uv sync` completes.
$ProfileExtras = @{
    "cpu"    = @("cpu")
    "cuda"   = @("cuda")
    "intel"  = @("xpu", "whisper-cpp", "openvino")
    "vulkan" = @("cpu", "whisper-cpp")
    "gui"    = @("gui")
}
$AllProfiles = @("cpu", "cuda", "intel", "vulkan", "gui")

function Write-Step {
    <#
    -Stage is optional and purely additive: when set, an extra machine-readable
    line is printed alongside the normal human-readable "==> message" line, so
    the GUI setup wizard (Phase 5c) can show a concrete stage name by parsing
    an exact prefix instead of guessing one from free text. Existing callers
    that omit -Stage are unaffected.
    #>
    param([string]$Message, [string]$Stage)
    Write-Host "`n==> $Message" -ForegroundColor Cyan
    if ($Stage) {
        Write-Host "##UTTERAN-WIZARD## stage=$Stage"
    }
}

function Invoke-Utf8Captured {
    <#
    Run a scriptblock that captures an external command's stdout, forcing UTF-8 on both
    ends of the pipe. Setting $env:PYTHONIOENCODING alone (the previous fix) only forces
    the child process to *write* UTF-8; Windows PowerShell 5.1 still *decodes* captured
    external-command output using [Console]::OutputEncoding (the console's OEM/ANSI
    codepage, e.g. cp932), independently of what the child actually wrote. That mismatch
    corrupts any Japanese text or non-ASCII path in the result - including turning valid
    `devices --json` output into text ConvertFrom-Json can't parse - even though the
    child process encoded everything correctly.
    #>
    param([Parameter(Mandatory = $true)][scriptblock]$ScriptBlock)
    $HadPythonIoEncoding = Test-Path Env:PYTHONIOENCODING
    $PreviousPythonIoEncoding = $env:PYTHONIOENCODING
    $PreviousConsoleEncoding = [Console]::OutputEncoding
    try {
        $env:PYTHONIOENCODING = "utf-8"
        [Console]::OutputEncoding = [System.Text.Encoding]::UTF8
        & $ScriptBlock
    }
    finally {
        [Console]::OutputEncoding = $PreviousConsoleEncoding
        if ($HadPythonIoEncoding) {
            $env:PYTHONIOENCODING = $PreviousPythonIoEncoding
        }
        else {
            Remove-Item Env:PYTHONIOENCODING -ErrorAction SilentlyContinue
        }
    }
}

function Get-VenvRoot {
    if ($VenvDir) {
        return [IO.Path]::GetFullPath($VenvDir)
    }
    if ($env:UTTERAN_VENV_DIR) {
        return [IO.Path]::GetFullPath($env:UTTERAN_VENV_DIR)
    }
    return Join-Path $ProjectRoot ".venvs"
}

function Get-ProfileVenvPath {
    param([Parameter(Mandatory = $true)][string]$ProfileName, [Parameter(Mandatory = $true)][string]$Root)
    # Directory name mirrors profiles.venv_dir_name(): "<os>-<profile>". This
    # script only runs on Windows, so the OS slug is always "win" here.
    return Join-Path $Root "win-$ProfileName"
}

function Get-DirectorySize {
    param([Parameter(Mandatory = $true)][string]$Path)
    if (-not (Test-Path -LiteralPath $Path -PathType Container)) {
        return 0
    }
    $Items = Get-ChildItem -LiteralPath $Path -Recurse -File -ErrorAction SilentlyContinue
    if ($null -eq $Items) {
        return 0
    }
    return ($Items | Measure-Object -Property Length -Sum).Sum
}

function Format-ByteSize {
    param([double]$Bytes)
    $Units = "B", "KiB", "MiB", "GiB", "TiB"
    $Value = [double]$Bytes
    foreach ($Unit in $Units) {
        if ($Value -lt 1024 -or $Unit -eq "TiB") {
            return "{0:N1} {1}" -f $Value, $Unit
        }
        $Value /= 1024
    }
}

function Get-MainPackageVersions {
    param([Parameter(Mandatory = $true)][string]$VenvPath)
    # Read dist-info directory names directly instead of launching that
    # venv's Python: -List only needs to report presence and versions, not
    # start an interpreter per profile.
    $SitePackages = Join-Path $VenvPath "Lib\site-packages"
    if (-not (Test-Path -LiteralPath $SitePackages -PathType Container)) {
        return "-"
    }
    $Watched = "torch", "pyannote.audio", "openvino", "faster-whisper"
    $Found = [System.Collections.Generic.List[string]]::new()
    foreach ($Name in $Watched) {
        $Pattern = ($Name -replace "\.", "_" -replace "-", "_") + "-*.dist-info"
        $Match = Get-ChildItem -LiteralPath $SitePackages -Filter $Pattern -Directory -ErrorAction SilentlyContinue |
            Select-Object -First 1
        if ($null -ne $Match -and $Match.Name -match "^(.+)-([^-]+)\.dist-info$") {
            $Found.Add("$Name=$($Matches[2])")
        }
    }
    if ($Found.Count -eq 0) {
        return "-"
    }
    return ($Found -join ", ")
}

function Show-ProfileList {
    $Root = Get-VenvRoot
    Write-Host "venv ルート: $Root"
    $Table = foreach ($Name in $AllProfiles) {
        $Path = Get-ProfileVenvPath -ProfileName $Name -Root $Root
        $Exists = Test-Path -LiteralPath $Path -PathType Container
        [pscustomobject]@{
            Profile   = $Name
            Extras    = ($ProfileExtras[$Name] -join ",")
            State     = if ($Exists) { "作成済み" } else { "未作成" }
            Size      = if ($Exists) { Format-ByteSize (Get-DirectorySize -Path $Path) } else { "-" }
            Packages  = if ($Exists) { Get-MainPackageVersions -VenvPath $Path } else { "-" }
            UpdatedAt = if ($Exists) { (Get-Item -LiteralPath $Path).LastWriteTime } else { "-" }
        }
    }
    $Table | Format-Table -AutoSize | Out-String -Width 200 | Write-Host
}

function Remove-ProfileVenv {
    param([Parameter(Mandatory = $true)][string]$ProfileName)
    $Root = Get-VenvRoot
    $Path = Get-ProfileVenvPath -ProfileName $ProfileName -Root $Root
    if (-not (Test-Path -LiteralPath $Path -PathType Container)) {
        Write-Host "プロファイル '$ProfileName' は作成されていません: $Path"
        return
    }
    $Size = Get-DirectorySize -Path $Path
    Write-Host "削除対象: $Path ($(Format-ByteSize $Size) 解放されます)"
    if (-not $Yes) {
        $Answer = Read-Host "削除しますか? [y/N]"
        if ($Answer.Trim().ToLowerInvariant() -notin @("y", "yes")) {
            Write-Host "キャンセルしました。"
            return
        }
    }
    Remove-Item -LiteralPath $Path -Recurse -Force
    Write-Host "削除しました: $Path" -ForegroundColor Green
}

function Get-AnyExistingProfileVenv {
    $Root = Get-VenvRoot
    foreach ($Name in $AllProfiles) {
        $Path = Get-ProfileVenvPath -ProfileName $Name -Root $Root
        if (Test-Path -LiteralPath (Join-Path $Path "Scripts\python.exe") -PathType Leaf) {
            return $Path
        }
    }
    return $null
}

function Set-DefaultProfileInConfig {
    param([Parameter(Mandatory = $true)][string]$ProfileName)
    $AnyVenv = Get-AnyExistingProfileVenv
    if ($null -eq $AnyVenv) {
        throw "既定プロファイルを設定するには、先に少なくとも1つのプロファイルを作成してください。"
    }
    $env:UV_PROJECT_ENVIRONMENT = $AnyVenv
    $UvCommand = Get-Command uv -ErrorAction SilentlyContinue
    if ($null -eq $UvCommand) {
        throw "uv が見つかりません。"
    }
    $ConfigPathText = (Invoke-Utf8Captured {
            & $UvCommand.Source run --no-sync utteran config path | Out-String
        }).Trim()
    if (-not (Test-Path -LiteralPath $ConfigPathText -PathType Leaf)) {
        Invoke-Utf8Captured { & $UvCommand.Source run --no-sync utteran config init | Out-Null } |
            Out-Null
    }
    $Content = Get-Content -LiteralPath $ConfigPathText -Raw -Encoding UTF8
    if ($Content -match '(?m)^\s*default_profile\s*=.*$') {
        $Content = $Content -replace '(?m)^\s*default_profile\s*=.*$', "default_profile = `"$ProfileName`""
    }
    elseif ($Content -match '(?m)^\[general\]\s*$') {
        $Content = $Content -replace '(?m)^\[general\]\s*$', "[general]`ndefault_profile = `"$ProfileName`""
    }
    else {
        $Content = "[general]`ndefault_profile = `"$ProfileName`"`n`n" + $Content
    }
    $Utf8NoBom = New-Object System.Text.UTF8Encoding($false)
    [IO.File]::WriteAllText($ConfigPathText, $Content, $Utf8NoBom)
    Write-Host "既定プロファイルを '$ProfileName' に設定しました: $ConfigPathText" -ForegroundColor Green
}

function Show-LegacyEnvironmentNotice {
    $LegacyFound = @()
    if (Test-Path -LiteralPath $LegacyWindowsEnvironment -PathType Container) {
        $LegacyFound += $LegacyWindowsEnvironment
    }
    if (Test-Path -LiteralPath $LegacyDefaultEnvironment -PathType Container) {
        $LegacyFound += $LegacyDefaultEnvironment
    }
    if ($LegacyFound.Count -eq 0) {
        return
    }
    Write-Host "`n旧方式の環境が見つかりました（変更していません）:" -ForegroundColor Yellow
    foreach ($Path in $LegacyFound) {
        Write-Host "  $Path"
    }
    Write-Host "新しいプロファイル別 venv (.venvs\win-<profile>) が動作することを確認したら、"
    Write-Host "上記フォルダは手動で削除できます: Remove-Item -Recurse -Force <パス>"
}

function Install-Ffmpeg {
    if ($SkipFfmpeg) {
        Write-Host "ffmpeg setup skipped by -SkipFfmpeg."
        return
    }
    $PathFfmpeg = Get-Command ffmpeg -ErrorAction SilentlyContinue
    if ($null -ne $PathFfmpeg) {
        Write-Host "ffmpeg already available: $($PathFfmpeg.Source)"
        return
    }
    if (Test-Path -LiteralPath $BundledFfmpeg -PathType Leaf) {
        Write-Host "utteran-managed ffmpeg already available: $BundledFfmpeg"
        return
    }

    Write-Host "Downloading the release essentials build linked by ffmpeg.org:"
    Write-Host "  $FfmpegUrl"
    Write-Host "This gyan.dev static build is GPLv3. FFmpeg license information:"
    Write-Host "  https://ffmpeg.org/legal.html"

    $TempRoot = Join-Path ([IO.Path]::GetTempPath()) ("utteran-setup-" + [Guid]::NewGuid())
    $Archive = Join-Path $TempRoot "ffmpeg.zip"
    $ChecksumFile = Join-Path $TempRoot "ffmpeg.zip.sha256"
    $Expanded = Join-Path $TempRoot "expanded"
    New-Item -ItemType Directory -Path $Expanded -Force | Out-Null
    try {
        Invoke-WebRequest -Uri $FfmpegUrl -OutFile $Archive -UseBasicParsing
        Invoke-WebRequest -Uri $FfmpegChecksumUrl -OutFile $ChecksumFile -UseBasicParsing
        $ExpectedHash = ((Get-Content -LiteralPath $ChecksumFile -Raw).Trim() -split "\s+")[0]
        $ActualHash = (Get-FileHash -LiteralPath $Archive -Algorithm SHA256).Hash
        if ($ActualHash -ne $ExpectedHash) {
            throw "ffmpeg archive SHA-256 verification failed."
        }
        Expand-Archive -LiteralPath $Archive -DestinationPath $Expanded -Force
        $FfmpegSource = Get-ChildItem -LiteralPath $Expanded -Filter "ffmpeg.exe" -Recurse |
            Select-Object -First 1
        if ($null -eq $FfmpegSource) {
            throw "Downloaded archive did not contain ffmpeg.exe."
        }
        New-Item -ItemType Directory -Path $BinDir -Force | Out-Null
        Copy-Item -LiteralPath $FfmpegSource.FullName -Destination $BundledFfmpeg -Force
        $FfprobeSource = Get-ChildItem -LiteralPath $Expanded -Filter "ffprobe.exe" -Recurse |
            Select-Object -First 1
        if ($null -ne $FfprobeSource) {
            Copy-Item -LiteralPath $FfprobeSource.FullName `
                -Destination (Join-Path $BinDir "ffprobe.exe") -Force
        }
        Write-Host "Installed ffmpeg: $BundledFfmpeg" -ForegroundColor Green
    }
    catch {
        Write-Warning "ffmpeg download/setup failed: $($_.Exception.Message)"
        Write-Host "Offline/manual setup: place ffmpeg.exe at $BundledFfmpeg or add it to PATH."
    }
    finally {
        if (Test-Path -LiteralPath $TempRoot) {
            Remove-Item -LiteralPath $TempRoot -Recurse -Force
        }
    }
}

function Show-EnvHelper {
    if (Test-Path -LiteralPath $EnvPath -PathType Leaf) {
        Write-Host ".env already exists; leaving it unchanged."
    }
    elseif (Test-Path -LiteralPath $EnvExample -PathType Leaf) {
        Copy-Item -LiteralPath $EnvExample -Destination $EnvPath
        Write-Host "Created $EnvPath from .env.example."
    }
    else {
        Write-Warning ".env.example was not found; .env was not created."
    }
    Write-Host "For pyannote, create a read token at https://huggingface.co/settings/tokens"
    Write-Host "and accept https://huggingface.co/pyannote/speaker-diarization-community-1,"
    Write-Host "then set HF_TOKEN in .env. The token is never requested or printed by this script."
}

function Install-Uv {
    <#
    Mirrors Install-Ffmpeg's "download, verify SHA-256, then extract" shape:
    astral-sh/uv publishes a per-asset <name>.sha256 sidecar on every GitHub
    release (same "<hash> *<filename>" format ffmpeg's gyan.dev build uses),
    so this applies the same standard to uv instead of piping the official
    install script (irm .../install.ps1 | iex) straight into a shell unseen.
    Does nothing if uv is already on PATH.
    #>
    $Existing = Get-Command uv -ErrorAction SilentlyContinue
    if ($null -ne $Existing) {
        Write-Host "uv already available: $($Existing.Source)"
        return
    }

    Write-Host "uv was not found."
    Write-Host "This will download uv (~15 MB) from astral-sh/uv's official GitHub releases:"
    Write-Host "  $UvReleaseApiUrl"
    Write-Host "The archive is verified against its published SHA-256 checksum before anything"
    Write-Host "is extracted or run, the same way this script already verifies ffmpeg."
    if (-not $Yes) {
        $Answer = Read-Host "Download and install uv for the current user now? [y/N]"
        if ($Answer.Trim().ToLowerInvariant() -notin @("y", "yes")) {
            Write-Host "Skipped uv installation. Install it manually, then rerun setup:"
            Write-Host "  https://docs.astral.sh/uv/getting-started/installation/"
            return
        }
    }

    $TempRoot = Join-Path ([IO.Path]::GetTempPath()) ("utteran-setup-uv-" + [Guid]::NewGuid())
    $Archive = Join-Path $TempRoot $UvAssetName
    $ChecksumFile = Join-Path $TempRoot "$UvAssetName.sha256"
    $Expanded = Join-Path $TempRoot "expanded"
    New-Item -ItemType Directory -Path $Expanded -Force | Out-Null
    try {
        $Release = Invoke-RestMethod -Uri $UvReleaseApiUrl -UseBasicParsing
        $Asset = $Release.assets | Where-Object { $_.name -eq $UvAssetName } | Select-Object -First 1
        $ChecksumAsset = $Release.assets |
            Where-Object { $_.name -eq "$UvAssetName.sha256" } | Select-Object -First 1
        if ($null -eq $Asset -or $null -eq $ChecksumAsset) {
            throw "Could not find $UvAssetName or its .sha256 in the latest uv release."
        }
        Write-Host "Downloading uv $($Release.tag_name):"
        Write-Host "  $($Asset.browser_download_url)"
        Invoke-WebRequest -Uri $Asset.browser_download_url -OutFile $Archive -UseBasicParsing
        Invoke-WebRequest -Uri $ChecksumAsset.browser_download_url -OutFile $ChecksumFile `
            -UseBasicParsing
        $ExpectedHash = ((Get-Content -LiteralPath $ChecksumFile -Raw).Trim() -split "\s+")[0]
        $ActualHash = (Get-FileHash -LiteralPath $Archive -Algorithm SHA256).Hash
        if ($ActualHash -ne $ExpectedHash) {
            throw "uv archive SHA-256 verification failed."
        }
        Expand-Archive -LiteralPath $Archive -DestinationPath $Expanded -Force
        $UvSource = Get-ChildItem -LiteralPath $Expanded -Filter "uv.exe" -Recurse |
            Select-Object -First 1
        if ($null -eq $UvSource) {
            throw "Downloaded archive did not contain uv.exe."
        }
        New-Item -ItemType Directory -Path $BinDir -Force | Out-Null
        Copy-Item -LiteralPath $UvSource.FullName -Destination (Join-Path $BinDir "uv.exe") -Force
        $UvxSource = Get-ChildItem -LiteralPath $Expanded -Filter "uvx.exe" -Recurse |
            Select-Object -First 1
        if ($null -ne $UvxSource) {
            Copy-Item -LiteralPath $UvxSource.FullName -Destination (Join-Path $BinDir "uvx.exe") `
                -Force
        }
        $env:PATH = "$BinDir;$env:PATH"
        $UserPath = [Environment]::GetEnvironmentVariable("Path", "User")
        $UserPathEntries = if ([string]::IsNullOrEmpty($UserPath)) { @() } else { $UserPath -split ";" }
        if ($UserPathEntries -notcontains $BinDir) {
            $NewUserPath = if ([string]::IsNullOrEmpty($UserPath)) { $BinDir } else { "$UserPath;$BinDir" }
            [Environment]::SetEnvironmentVariable("Path", $NewUserPath, "User")
            Write-Host "Added $BinDir to your user PATH. Open a new terminal for it to apply there."
        }
        Write-Host "Installed uv: $(Join-Path $BinDir 'uv.exe')" -ForegroundColor Green
    }
    catch {
        Write-Warning "uv download/setup failed: $($_.Exception.Message)"
        Write-Host "Offline/manual setup: https://docs.astral.sh/uv/getting-started/installation/"
    }
    finally {
        if (Test-Path -LiteralPath $TempRoot) {
            Remove-Item -LiteralPath $TempRoot -Recurse -Force
        }
    }
}

function Invoke-VulkanPrerequisiteCheck {
    param([Parameter(Mandatory = $true)][string]$VenvPath)
    $PythonExe = Join-Path $VenvPath "Scripts\python.exe"
    $Probe = @"
from utteran.native import probe_glslc, probe_vulkan_runtime
build = probe_glslc()
runtime, device = probe_vulkan_runtime()
print(f"BUILD={build.available}|{build.detail or ''}")
print(f"RUNTIME={runtime.available}|{device or runtime.detail or ''}")
"@
    # Windows PowerShell 5.1 strips the quotes inside f-strings when a multiline
    # script is passed as the value of `python -c`. Feed the probe over stdin so
    # Python receives the source verbatim on every supported PowerShell version.
    $Result = $Probe | & $PythonExe - 2>&1
    $BuildLine = $Result | Where-Object { $_ -like "BUILD=*" }
    $RuntimeLine = $Result | Where-Object { $_ -like "RUNTIME=*" }
    $BuildOk = $BuildLine -like "BUILD=True*"
    $RuntimeOk = $RuntimeLine -like "RUNTIME=True*"
    if ($BuildOk) {
        Write-Host "Vulkanビルド前提 (glslc): 利用可能" -ForegroundColor Green
    }
    else {
        Write-Warning "Vulkanビルド前提 (glslc) が利用できません: $BuildLine"
        Write-Host "Vulkan SDK (https://vulkan.lunarg.com/) を導入してください。"
    }
    if ($RuntimeOk) {
        Write-Host "Vulkanランタイム: 利用可能 ($RuntimeLine)" -ForegroundColor Green
    }
    else {
        Write-Warning "Vulkanランタイムが利用できません: $RuntimeLine"
    }
    return $BuildOk -and $RuntimeOk
}

function Invoke-ProfileSetup {
    param([Parameter(Mandatory = $true)][string]$ProfileName)

    $Root = Get-VenvRoot
    $VenvPath = Get-ProfileVenvPath -ProfileName $ProfileName -Root $Root
    $Extras = $ProfileExtras[$ProfileName]

    Write-Host "utteran Windows setup"
    Write-Host "Project: $ProjectRoot"
    Write-Host "Profile: $ProfileName (extras: $($Extras -join ', '))"
    Write-Host "venv: $VenvPath"
    Write-Host "No administrator privileges are required. Existing files are not overwritten."
    Write-Host "This profile's venv is independent; other profiles are not affected."

    Set-Location -LiteralPath $ProjectRoot
    Show-LegacyEnvironmentNotice

    Write-Step "Checking Python 3.11 / 3.12" -Stage "python_check"
    $PythonCommand = Get-Command python -ErrorAction SilentlyContinue
    if ($null -eq $PythonCommand) {
        Write-Warning "python was not found on PATH. Install Python 3.11/3.12 or let uv manage it."
    }
    else {
        $PythonVersion = & $PythonCommand.Source --version 2>&1
        Write-Host $PythonVersion
        if ($PythonVersion -notmatch "Python 3\.(11|12)\.") {
            Write-Warning "utteran supports Python 3.11 and 3.12."
        }
    }

    Write-Step "Checking uv" -Stage "uv_install"
    Install-Uv

    Write-Step "Syncing profile '$ProfileName'" -Stage "venv_sync"
    $DependencySyncSucceeded = $false
    $script:UvCommand = Get-Command uv -ErrorAction SilentlyContinue
    if ($null -eq $script:UvCommand) {
        Write-Warning "uv is still not available. Install it manually, then rerun setup:"
        Write-Host "  https://docs.astral.sh/uv/getting-started/installation/"
    }
    else {
        Write-Host (& $script:UvCommand.Source --version)
        $env:UV_PROJECT_ENVIRONMENT = $VenvPath
        $SyncArgs = [System.Collections.Generic.List[string]]::new()
        $SyncArgs.Add("sync")
        $SyncArgs.Add("--locked")
        foreach ($Extra in $Extras) {
            $SyncArgs.Add("--extra")
            $SyncArgs.Add($Extra)
        }
        if ($ProfileName -eq "cuda") {
            Write-Host "CUDA profile uses the PyTorch CUDA 12.6 wheel (approximately 2.4 GiB)."
        }
        try {
            & $script:UvCommand.Source @SyncArgs
            if ($LASTEXITCODE -ne 0) {
                throw "uv $($SyncArgs -join ' ') failed (exit $LASTEXITCODE)"
            }
            $DependencySyncSucceeded = $true
        }
        catch {
            Write-Warning "Dependency sync failed: $($_.Exception.Message)"
            Write-Host "If this machine is offline, reconnect and run: uv $($SyncArgs -join ' ')"
        }
    }

    if ($ProfileName -ne "gui") {
        Write-Step "Checking ffmpeg" -Stage "ffmpeg"
        Install-Ffmpeg

        Write-Step "Preparing .env without exposing a token" -Stage "env_setup"
        Show-EnvHelper
    }

    Write-Step "Verifying profile '$ProfileName'" -Stage "verify"
    $ProfileVerificationSucceeded = $false
    if (-not $DependencySyncSucceeded) {
        Write-Warning "Skipping verification because dependency sync did not complete."
    }
    else {
        try {
            if ($ProfileName -eq "gui") {
                $GuiProbe = & (Join-Path $VenvPath "Scripts\python.exe") -c `
                    "import importlib.util; import fastapi, uvicorn, webview, utteran_gui; assert importlib.util.find_spec('torch') is None; assert importlib.util.find_spec('faster_whisper') is None; print('GUI_OK')" 2>&1
                if ($LASTEXITCODE -ne 0 -or $GuiProbe -notmatch "GUI_OK") {
                    throw "GUI dependency/isolation probe failed: $GuiProbe"
                }
                $ProfileVerificationSucceeded = $true
                Write-Host "GUI environment: lightweight and isolated" -ForegroundColor Green
            }
            else {
                Write-Step "Inspecting runtime devices (the first probe can take a while)" `
                    -Stage "verify_devices"
                $DeviceProbeTimer = [System.Diagnostics.Stopwatch]::StartNew()
                $DeviceText = Invoke-Utf8Captured {
                    & $script:UvCommand.Source run --no-sync utteran devices --json | Out-String
                }
                $DeviceProbeTimer.Stop()
                Write-Host (
                    "Runtime device probe completed in {0:N1} seconds." -f `
                        $DeviceProbeTimer.Elapsed.TotalSeconds
                ) -ForegroundColor Green
                if ($LASTEXITCODE -ne 0) {
                    throw "utteran devices --json failed (exit $LASTEXITCODE)"
                }
                $DeviceData = $DeviceText | ConvertFrom-Json
                if ($ProfileName -eq "cpu") {
                    $ProfileVerificationSucceeded = (
                        $DeviceData.backends.'faster-whisper' -and $DeviceData.backends.pyannote
                    )
                }
                elseif ($ProfileName -eq "cuda") {
                    $UsableCTranslate2Cuda = @(
                        $DeviceData.ctranslate2.cuda_devices | Where-Object { $_.usable }
                    ).Count -gt 0
                    $UsableTorchCuda = [bool]$DeviceData.pytorch.cuda_available
                    $ProfileVerificationSucceeded = $UsableCTranslate2Cuda -and $UsableTorchCuda
                    if (-not $UsableCTranslate2Cuda) {
                        Write-Warning "CUDA profile: faster-whisper cannot initialize CTranslate2 CUDA."
                        Write-Host "Install CUDA 12 compatible cuDNN 9 and cuBLAS, then rerun setup."
                    }
                    if (-not $UsableTorchCuda) {
                        Write-Warning "CUDA profile: pyannote cannot execute the PyTorch CUDA probe kernel."
                    }
                }
                elseif ($ProfileName -eq "intel") {
                    $OpenVinoOk = [bool]$DeviceData.openvino.available
                    # devices --json already imports torch and performs the XPU
                    # availability/device probe. Reusing it avoids a second costly
                    # Python process and repeated DLL/device initialization.
                    $XpuOk = [bool]$DeviceData.pytorch.xpu_available
                    $ProfileVerificationSucceeded = $OpenVinoOk
                    if (-not $OpenVinoOk) {
                        Write-Warning "Intel profile: OpenVINO could not initialize."
                    }
                    if ($XpuOk) {
                        Write-Host "torch XPU: 利用可能" -ForegroundColor Green
                    }
                    else {
                        Write-Host "torch XPU: 未検出"
                    }
                }
                else {
                    # vulkan
                    $ProfileVerificationSucceeded = Invoke-VulkanPrerequisiteCheck -VenvPath $VenvPath
                }
                Write-Step "Summarizing verified devices" -Stage "verify_summary"
                Write-Host "PyTorch: $($DeviceData.pytorch.version)"
                Write-Host "OpenVINO devices: $(@($DeviceData.openvino.values) -join ', ')"
                Write-Host "CTranslate2 CUDA devices: $(@($DeviceData.ctranslate2.cuda_devices).Count)"
            }
        }
        catch {
            Write-Warning "Verification could not complete: $($_.Exception.Message)"
        }
    }

    if (-not $DependencySyncSucceeded -or -not $ProfileVerificationSucceeded) {
        Write-Host "`nutteran setup (profile: $ProfileName) is incomplete. Resolve the warnings above and rerun." `
            -ForegroundColor Red
        exit 1
    }

    Write-Host "`nutteran setup completed successfully for profile '$ProfileName'." -ForegroundColor Green
    if ($ProfileName -eq "gui") {
        Write-Host "Start the GUI with:"
        Write-Host "  .\gui.ps1"
    }
    else {
        Write-Host "Models are managed separately after setup. To choose from a numbered list, run:"
        Write-Host "  .\run.ps1 -Profile $ProfileName models download"
        Write-Host "To run transcription with this profile:"
        Write-Host "  .\run.ps1 -Profile $ProfileName transcribe <入力ファイル>"
        Write-Host "Or, if this is your only profile, simply:"
        Write-Host "  .\run.ps1 transcribe <入力ファイル>"
    }
}

if ($Remove) {
    Remove-ProfileVenv -ProfileName $Remove
    exit 0
}
if ($List) {
    Show-ProfileList
    exit 0
}
if ($SetDefault) {
    Set-DefaultProfileInConfig -ProfileName $SetDefault
    exit 0
}
if (-not $Profile) {
    $Profile = "cpu"
}
Invoke-ProfileSetup -ProfileName $Profile
