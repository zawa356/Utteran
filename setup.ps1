[CmdletBinding()]
param(
    [ValidateSet("cpu", "cuda", "intel")]
    [string]$Profile = "cpu",
    [switch]$SkipModels,
    [switch]$SkipFfmpeg,
    [string]$ModelDir,
    [string[]]$Models = @()
)

Set-StrictMode -Version Latest
$ErrorActionPreference = "Stop"

$ProjectRoot = Split-Path -Parent $MyInvocation.MyCommand.Path
$DataRoot = Join-Path ([Environment]::GetFolderPath("LocalApplicationData")) "utteran"
$BinDir = Join-Path $DataRoot "bin"
$BundledFfmpeg = Join-Path $BinDir "ffmpeg.exe"
$EnvPath = Join-Path $ProjectRoot ".env"
$EnvExample = Join-Path $ProjectRoot ".env.example"
$DefaultProjectEnvironment = Join-Path $ProjectRoot ".venv"
$WindowsProjectEnvironment = Join-Path $ProjectRoot ".venv-windows"
$ProjectEnvironment = $DefaultProjectEnvironment
$FfmpegUrl = "https://www.gyan.dev/ffmpeg/builds/ffmpeg-release-essentials.zip"
$FfmpegChecksumUrl = "$FfmpegUrl.sha256"
$DependencySyncSucceeded = $false
$DeviceVerificationSucceeded = $false
$ProfileVerificationSucceeded = $false

function Write-Step {
    param([string]$Message)
    Write-Host "`n==> $Message" -ForegroundColor Cyan
}

function Invoke-Uv {
    param([string[]]$Arguments)
    & $script:UvCommand.Source @Arguments
    if ($LASTEXITCODE -ne 0) {
        throw "uv $($Arguments -join ' ') failed (exit $LASTEXITCODE)"
    }
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

Write-Host "utteran Windows setup"
Write-Host "Project: $ProjectRoot"
Write-Host "Profile: $Profile"
Write-Host "Planned actions: Python/uv check, dependency sync, ffmpeg check, .env helper,"
Write-Host "                 optional model download, CUDA dependency check, devices report."
Write-Host "No administrator privileges are required. Existing files are not overwritten."

Set-Location -LiteralPath $ProjectRoot

Write-Step "Checking Python 3.11 / 3.12"
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

Write-Step "Checking uv and syncing the selected profile"
$DefaultHasWindowsPython = Test-Path -LiteralPath `
    (Join-Path $DefaultProjectEnvironment "Scripts\python.exe") -PathType Leaf
$DefaultHasPosixLayout = (
    (Test-Path -LiteralPath (Join-Path $DefaultProjectEnvironment "bin") -PathType Container) -or
    (Test-Path -LiteralPath (Join-Path $DefaultProjectEnvironment "lib64"))
)
$WindowsEnvironmentExists = Test-Path -LiteralPath $WindowsProjectEnvironment -PathType Container
if ($WindowsEnvironmentExists -or
    ((Test-Path -LiteralPath $DefaultProjectEnvironment -PathType Container) -and
     -not $DefaultHasWindowsPython -and $DefaultHasPosixLayout)) {
    $ProjectEnvironment = $WindowsProjectEnvironment
    if ($DefaultHasPosixLayout -and -not $DefaultHasWindowsPython) {
        Write-Warning ".venv is a Linux/WSL environment and cannot be shared with Windows."
        Write-Host "Using a separate Windows environment without modifying .venv:"
    }
}
$env:UV_PROJECT_ENVIRONMENT = $ProjectEnvironment
Write-Host "Project environment: $ProjectEnvironment"

$script:UvCommand = Get-Command uv -ErrorAction SilentlyContinue
if ($null -eq $script:UvCommand) {
    Write-Warning "uv was not found. Install it without administrator rights, then rerun setup:"
    Write-Host "  winget install --id=astral-sh.uv -e"
    Write-Host "  https://docs.astral.sh/uv/getting-started/installation/"
}
else {
    Write-Host (& $script:UvCommand.Source --version)
    $SyncArgs = [System.Collections.Generic.List[string]]::new()
    $SyncArgs.Add("sync")
    $SyncArgs.Add("--locked")
    $SyncArgs.Add("--extra")
    $SyncArgs.Add($Profile)
    if ($Profile -eq "cuda") {
        Write-Host "CUDA profile uses the PyTorch CUDA 12.6 wheel (approximately 2.4 GiB)."
    }
    try {
        Invoke-Uv -Arguments ([string[]]$SyncArgs)
        $DependencySyncSucceeded = $true
    }
    catch {
        Write-Warning "Dependency sync failed: $($_.Exception.Message)"
        Write-Host "If this machine is offline, reconnect and run: uv $($SyncArgs -join ' ')"
    }
}

Write-Step "Checking ffmpeg"
Install-Ffmpeg

Write-Step "Preparing .env without exposing a token"
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

if ($ModelDir) {
    $env:UTTERAN_MODEL_DIR = $ModelDir
    Write-Host "UTTERAN_MODEL_DIR for this setup process: $ModelDir"
}

Write-Step "Optionally downloading models"
if ($SkipModels) {
    Write-Host "Model download skipped by -SkipModels."
}
elseif ($null -eq $script:UvCommand) {
    Write-Warning "Models cannot be downloaded until uv is installed."
}
elseif (-not $DependencySyncSucceeded) {
    Write-Warning "Models were skipped because dependency sync did not complete."
}
else {
    $SelectedModels = [System.Collections.Generic.List[string]]::new()
    foreach ($Model in $Models) {
        if (-not [string]::IsNullOrWhiteSpace($Model)) {
            $SelectedModels.Add($Model.Trim())
        }
    }
    if ($SelectedModels.Count -eq 0 -and [Environment]::UserInteractive) {
        Write-Host "Available IDs: run 'uv run utteran models list --available' for the full catalog."
        $Answer = Read-Host "Model IDs to download (comma-separated, blank to skip)"
        foreach ($Model in ($Answer -split ",")) {
            if (-not [string]::IsNullOrWhiteSpace($Model)) {
                $SelectedModels.Add($Model.Trim())
            }
        }
    }
    foreach ($Model in $SelectedModels) {
        try {
            Invoke-Uv -Arguments @("run", "utteran", "models", "download", $Model)
        }
        catch {
            Write-Warning "Model download failed for '$Model': $($_.Exception.Message)"
        }
    }
    if ($SelectedModels.Count -eq 0) {
        Write-Host "No models selected. Later, run: utteran models download <ID>"
    }
}

Write-Step "Checking CUDA libraries and final device selection"
if ($null -eq $script:UvCommand) {
    Write-Warning "Skipping 'utteran devices' until uv is installed and dependencies are synced."
}
elseif (-not $DependencySyncSucceeded) {
    Write-Warning "Skipping 'utteran devices' because dependency sync did not complete."
}
else {
    try {
        $DeviceText = (& $script:UvCommand.Source run utteran devices --json | Out-String)
        if ($LASTEXITCODE -ne 0) {
            throw "utteran devices --json failed (exit $LASTEXITCODE)"
        }
        $DeviceData = $DeviceText | ConvertFrom-Json
        if ($Profile -eq "cpu") {
            $ProfileVerificationSucceeded = (
                $DeviceData.backends.'faster-whisper' -and $DeviceData.backends.pyannote
            )
        }
        elseif ($Profile -eq "cuda") {
            $UsableCTranslate2Cuda = @(
                $DeviceData.ctranslate2.cuda_devices | Where-Object { $_.usable }
            ).Count -gt 0
            $UsableTorchCuda = [bool]$DeviceData.pytorch.cuda_available
            $ProfileVerificationSucceeded = $UsableCTranslate2Cuda -and $UsableTorchCuda
            if (-not $UsableCTranslate2Cuda) {
                Write-Warning "CUDA profile: faster-whisper cannot initialize CTranslate2 CUDA."
                Write-Host "Install CUDA 12 compatible cuDNN 9 and cuBLAS, ensure their DLL directories"
                Write-Host "are available, then rerun setup. See README.md and NVIDIA documentation."
            }
            if (-not $UsableTorchCuda) {
                Write-Warning "CUDA profile: pyannote cannot execute the PyTorch CUDA probe kernel."
                Write-Host "Check the NVIDIA driver, GPU compatibility, and the CUDA PyTorch build."
            }
        }
        else {
            $ProfileVerificationSucceeded = [bool]$DeviceData.openvino.available
            if (-not $ProfileVerificationSucceeded) {
                Write-Warning "Intel profile: OpenVINO could not initialize."
            }
        }
        & $script:UvCommand.Source run utteran devices
        if ($LASTEXITCODE -ne 0) {
            throw "utteran devices failed (exit $LASTEXITCODE)"
        }
        $DeviceVerificationSucceeded = $true
    }
    catch {
        Write-Warning "Device verification could not complete: $($_.Exception.Message)"
        Write-Host "After dependencies are available, run: uv run utteran devices"
    }
}

if (-not $DependencySyncSucceeded -or
    -not $DeviceVerificationSucceeded -or
    -not $ProfileVerificationSucceeded) {
    Write-Host "`nutteran setup is incomplete. Resolve the warnings above and rerun setup." `
        -ForegroundColor Red
    exit 1
}

Write-Host "`nutteran setup completed successfully." -ForegroundColor Green
if ($ProjectEnvironment -ne $DefaultProjectEnvironment) {
    Write-Host "Windows and WSL use separate project environments. In a new PowerShell session, run:"
    Write-Host "  `$env:UV_PROJECT_ENVIRONMENT = '$ProjectEnvironment'"
    Write-Host "before using 'uv run utteran ...', or execute:"
    Write-Host "  $ProjectEnvironment\Scripts\utteran.exe"
}
