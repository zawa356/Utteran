#Requires -Version 5.1
[CmdletBinding()]
param(
    [switch]$CheckOnly
)

Set-StrictMode -Version Latest
$ErrorActionPreference = "Stop"

$ProjectRoot = Split-Path -Parent $MyInvocation.MyCommand.Path
$LocalData = [Environment]::GetFolderPath("LocalApplicationData")
$LogRoot = Join-Path $LocalData "utteran\utteran\Logs"
$LogPath = Join-Path $LogRoot ("python-launch-{0}.log" -f (Get-Date -Format "yyyyMMdd-HHmmss"))
$TranscriptStarted = $false

function Stop-WithGuidance {
    param([string]$Problem, [string[]]$NextSteps)
    Write-Host "`n起動できません: $Problem" -ForegroundColor Red
    Write-Host "次に行うこと:"
    foreach ($Step in $NextSteps) {
        Write-Host "  $Step"
    }
    Write-Host "診断ログ: $LogPath"
    exit 1
}

try {
    New-Item -ItemType Directory -Path $LogRoot -Force | Out-Null
    Start-Transcript -LiteralPath $LogPath -Force | Out-Null
    $TranscriptStarted = $true
    [Console]::OutputEncoding = [System.Text.Encoding]::UTF8
    $env:PYTHONIOENCODING = "utf-8"
    $env:PYTHONUTF8 = "1"

    Write-Host "utteran Python直起動"
    Write-Host "この起動だけExecutionPolicy Bypassを使用します。恒久的な設定変更は行いません。"
    Write-Host "前提条件を確認します。Python、uv、ffmpegを自動導入しません。"

    $PythonPath = $null
    $PythonVersion = $null
    $PythonCommand = Get-Command python -ErrorAction SilentlyContinue
    if ($null -ne $PythonCommand) {
        $CandidateVersion = & $PythonCommand.Source -c "import sys; print(f'{sys.version_info.major}.{sys.version_info.minor}.{sys.version_info.micro}')" 2>&1
        if ($LASTEXITCODE -eq 0 -and $CandidateVersion -match '^3\.(11|12)\.') {
            $PythonPath = $PythonCommand.Source
            $PythonVersion = $CandidateVersion
        }
    }
    if ($null -eq $PythonPath) {
        $PyLauncher = Get-Command py -ErrorAction SilentlyContinue
        if ($null -ne $PyLauncher) {
            foreach ($Minor in @("3.12", "3.11")) {
                $CandidatePath = & $PyLauncher.Source "-$Minor" -c "import sys; print(sys.executable)" 2>$null
                if ($LASTEXITCODE -eq 0 -and $CandidatePath) {
                    $PythonPath = $CandidatePath
                    $PythonVersion = & $PythonPath -c "import sys; print(f'{sys.version_info.major}.{sys.version_info.minor}.{sys.version_info.micro}')"
                    break
                }
            }
        }
    }
    if ($null -eq $PythonPath) {
        Stop-WithGuidance "対応Python 3.11/3.12が見つかりません。" @(
            "Python 3.11または3.12を導入し、そのpython.exeがPATHで先に見つかるようにする",
            "python --version で確認してから再実行する"
        )
    }
    Write-Host "Python: $PythonPath ($PythonVersion)" -ForegroundColor Green

    $UvCommand = Get-Command uv -ErrorAction SilentlyContinue
    if ($null -eq $UvCommand) {
        Stop-WithGuidance "uvがPATHに見つかりません。" @(
            "https://docs.astral.sh/uv/getting-started/installation/ の手順でuvを導入する",
            "新しいターミナルで uv --version を確認し、このbatを再実行する"
        )
    }
    $UvVersion = & $UvCommand.Source --version 2>&1
    if ($LASTEXITCODE -ne 0) {
        Stop-WithGuidance "uvを実行できません（$UvVersion）。" @(
            "uvを再導入し、uv --versionが成功することを確認する"
        )
    }
    Write-Host "uv: $($UvCommand.Source) ($UvVersion)" -ForegroundColor Green

    $ManagedFfmpeg = Join-Path $LocalData "utteran\utteran\bin\ffmpeg.exe"
    $FfmpegCommand = Get-Command ffmpeg -ErrorAction SilentlyContinue
    if ($null -eq $FfmpegCommand -and -not (Test-Path -LiteralPath $ManagedFfmpeg -PathType Leaf)) {
        Stop-WithGuidance "ffmpegがPATHまたはutteranのユーザー領域に見つかりません。" @(
            "https://ffmpeg.org/download.html からffmpegを導入する",
            "ffmpeg -versionが成功することを確認してから再実行する"
        )
    }
    $FfmpegPath = if ($null -ne $FfmpegCommand) { $FfmpegCommand.Source } else { $ManagedFfmpeg }
    Write-Host "ffmpeg: $FfmpegPath" -ForegroundColor Green

    if ($CheckOnly) {
        Write-Host "前提条件の確認が完了しました。診断ログ: $LogPath" -ForegroundColor Green
        exit 0
    }

    $GuiPython = Join-Path $ProjectRoot ".venvs\win-gui\Scripts\python.exe"
    if (-not (Test-Path -LiteralPath $GuiPython -PathType Leaf)) {
        Write-Host "`nGUI環境を構築しています。数分かかる場合がありますが、処理中は失敗ではありません。" -ForegroundColor Cyan
        $env:UV_PROJECT_ENVIRONMENT = Split-Path -Parent (Split-Path -Parent $GuiPython)
        & $UvCommand.Source sync --locked --extra gui --python $PythonPath `
            --no-python-downloads --project $ProjectRoot
        if ($LASTEXITCODE -ne 0 -or -not (Test-Path -LiteralPath $GuiPython -PathType Leaf)) {
            Stop-WithGuidance "GUI用venvの構築に失敗しました（uv exit $LASTEXITCODE）。" @(
                "ネットワーク接続と空き容量を確認する",
                "ログ末尾のuvエラーを確認し、このbatを再実行する"
            )
        }
    }

    Write-Host "`nGUIをPythonインタプリタから起動します。" -ForegroundColor Cyan
    Write-Host "データ配置はsource起動の既定値です（venvはrepository配下、その他はWindowsユーザー領域）。"
    Write-Host "診断ログ: $LogPath"
    $env:UTTERAN_PROJECT_ROOT = $ProjectRoot
    $env:UTTERAN_PYTHON_DIRECT = "1"
    & $GuiPython -m utteran_gui
    if ($LASTEXITCODE -ne 0) {
        Stop-WithGuidance "GUIが終了コード$LASTEXITCODEで停止しました。" @(
            "診断ログの末尾を確認する",
            "profile構築中ならネットワーク、空き容量、表示されたcmake/MSVC/Vulkan SDK案内を確認する"
        )
    }
}
catch {
    Stop-WithGuidance $_.Exception.Message @(
        "診断ログの末尾を確認する",
        "表示された不足項目を導入または修正してから再実行する"
    )
}
finally {
    if ($TranscriptStarted) {
        Stop-Transcript | Out-Null
    }
}
