[CmdletBinding()]
param()

Set-StrictMode -Version Latest
$ErrorActionPreference = "Stop"

$ProjectRoot = Split-Path -Parent $MyInvocation.MyCommand.Path
$InputDirectory = Join-Path $ProjectRoot "input"
$OutputDirectory = Join-Path $ProjectRoot "output"
$SetupScript = Join-Path $ProjectRoot "setup.ps1"
$WindowsEnvironment = Join-Path $ProjectRoot ".venv-windows"
$DefaultEnvironment = Join-Path $ProjectRoot ".venv"
$script:LastUtteranExitCode = 0

New-Item -ItemType Directory -Path $InputDirectory -Force | Out-Null
New-Item -ItemType Directory -Path $OutputDirectory -Force | Out-Null
Set-Location -LiteralPath $ProjectRoot

function Pause-Front {
    [void](Read-Host "Enterキーでメニューへ戻ります")
}

function Read-MenuChoice {
    param(
        [Parameter(Mandatory = $true)][string]$Prompt,
        [Parameter(Mandatory = $true)][string[]]$ValidChoices,
        [string]$Default = ""
    )

    while ($true) {
        $Answer = (Read-Host $Prompt).Trim().ToLowerInvariant()
        if ([string]::IsNullOrWhiteSpace($Answer) -and $Default) {
            return $Default
        }
        if ($ValidChoices -contains $Answer) {
            return $Answer
        }
        Write-Host "入力が正しくありません。選択肢: $($ValidChoices -join ', ')" `
            -ForegroundColor Yellow
    }
}

function Read-YesNo {
    param(
        [Parameter(Mandatory = $true)][string]$Prompt,
        [bool]$DefaultYes = $true
    )

    $Suffix = if ($DefaultYes) { "[Y/n]" } else { "[y/N]" }
    while ($true) {
        $Answer = (Read-Host "$Prompt $Suffix").Trim().ToLowerInvariant()
        if ([string]::IsNullOrWhiteSpace($Answer)) {
            return $DefaultYes
        }
        if ($Answer -in @("y", "yes")) {
            return $true
        }
        if ($Answer -in @("n", "no")) {
            return $false
        }
        Write-Host "y または n を入力してください。" -ForegroundColor Yellow
    }
}

function Read-PositiveInteger {
    param([Parameter(Mandatory = $true)][string]$Prompt)

    while ($true) {
        $Answer = (Read-Host $Prompt).Trim()
        $Value = 0
        if ([int]::TryParse($Answer, [ref]$Value) -and $Value -ge 1) {
            return $Value
        }
        Write-Host "1以上の整数を入力してください。" -ForegroundColor Yellow
    }
}

function Resolve-FrontPath {
    param(
        [Parameter(Mandatory = $true)][string]$PathText,
        [switch]$AllowMissing
    )

    $Expanded = [Environment]::ExpandEnvironmentVariables($PathText.Trim().Trim('"'))
    if (-not [IO.Path]::IsPathRooted($Expanded)) {
        $Expanded = Join-Path $ProjectRoot $Expanded
    }
    $FullPath = [IO.Path]::GetFullPath($Expanded)
    if (-not $AllowMissing -and -not (Test-Path -LiteralPath $FullPath)) {
        throw "パスが見つかりません: $FullPath"
    }
    return $FullPath
}

function Get-UtteranLauncher {
    $Candidates = @(
        (Join-Path $WindowsEnvironment "Scripts\utteran.exe"),
        (Join-Path $DefaultEnvironment "Scripts\utteran.exe")
    )
    foreach ($Candidate in $Candidates) {
        if (Test-Path -LiteralPath $Candidate -PathType Leaf) {
            return [pscustomobject]@{
                Command = $Candidate
                Prefix = [string[]]@()
            }
        }
    }

    $SelectedEnvironment = $null
    if (Test-Path -LiteralPath (Join-Path $WindowsEnvironment "Scripts\python.exe") -PathType Leaf) {
        $SelectedEnvironment = $WindowsEnvironment
    }
    elseif (Test-Path -LiteralPath (Join-Path $DefaultEnvironment "Scripts\python.exe") -PathType Leaf) {
        $SelectedEnvironment = $DefaultEnvironment
    }
    if ($null -eq $SelectedEnvironment) {
        throw "Windows実行環境がありません。先に .\setup.ps1 -Profile cpu|cuda|intel を実行してください。"
    }

    $UvCommand = Get-Command uv -ErrorAction SilentlyContinue
    if ($null -eq $UvCommand) {
        throw "utteran.exe と uv が見つかりません。setup.ps1 を実行してください。"
    }
    $env:UV_PROJECT_ENVIRONMENT = $SelectedEnvironment
    return [pscustomobject]@{
        Command = $UvCommand.Source
        Prefix = [string[]]@("run", "--no-sync", "utteran")
    }
}

function Invoke-Utteran {
    param([Parameter(Mandatory = $true)][string[]]$Arguments)

    $Launcher = Get-UtteranLauncher
    $InvocationArguments = @($Launcher.Prefix) + $Arguments
    & $Launcher.Command @InvocationArguments
    $script:LastUtteranExitCode = if ($null -eq $LASTEXITCODE) { 0 } else { $LASTEXITCODE }
    if ($script:LastUtteranExitCode -ne 0) {
        Write-Host "utteran は終了コード $script:LastUtteranExitCode で終了しました。" `
            -ForegroundColor Red
    }
}

function Format-CommandArgument {
    param([Parameter(Mandatory = $true)][string]$Value)

    if ($Value -match '^[A-Za-z0-9_./:\\,*?=+-]+$') {
        return $Value
    }
    return "'" + $Value.Replace("'", "''") + "'"
}

function Show-UtteranCommand {
    param([Parameter(Mandatory = $true)][string[]]$Arguments)

    $Shown = @("utteran") + $Arguments | ForEach-Object { Format-CommandArgument $_ }
    Write-Host ($Shown -join " ") -ForegroundColor DarkGray
}

function Select-InputPath {
    while ($true) {
        Write-Host "`n入力を選択してください。" -ForegroundColor Cyan
        $Items = @(
            Get-ChildItem -LiteralPath $InputDirectory -Force |
                Where-Object { $_.Name -ne ".gitkeep" } |
                Sort-Object -Property Name
        )
        for ($Index = 0; $Index -lt $Items.Count; $Index++) {
            $Kind = if ($Items[$Index].PSIsContainer) { "フォルダ" } else { "ファイル" }
            Write-Host "  $($Index + 1). [$Kind] $($Items[$Index].Name)"
        }
        if ($Items.Count -eq 0) {
            Write-Host "  inputフォルダは空です。音声・動画を配置するか任意パスを指定してください。"
        }
        Write-Host "  A. inputフォルダ全体を一括処理"
        Write-Host "  P. 任意のファイル／フォルダパスを入力"
        Write-Host "  R. 一覧を更新"
        Write-Host "  0. 戻る"
        $Answer = (Read-Host "選択").Trim()
        if ($Answer -eq "0") {
            return $null
        }
        if ($Answer -match '^[rR]$') {
            continue
        }
        if ($Answer -match '^[aA]$') {
            return $InputDirectory
        }
        if ($Answer -match '^[pP]$') {
            $ManualPath = (Read-Host "入力パス").Trim()
            if ([string]::IsNullOrWhiteSpace($ManualPath)) {
                continue
            }
            try {
                return Resolve-FrontPath -PathText $ManualPath
            }
            catch {
                Write-Host $_.Exception.Message -ForegroundColor Yellow
                continue
            }
        }
        $Number = 0
        if ([int]::TryParse($Answer, [ref]$Number) -and
            $Number -ge 1 -and $Number -le $Items.Count) {
            return $Items[$Number - 1].FullName
        }
        Write-Host "選択が正しくありません。" -ForegroundColor Yellow
    }
}

function Select-OutputPath {
    while ($true) {
        Write-Host "`n出力先（Enterで既定）: $OutputDirectory" -ForegroundColor Cyan
        $Answer = (Read-Host "出力フォルダ").Trim()
        if ([string]::IsNullOrWhiteSpace($Answer)) {
            return $OutputDirectory
        }
        try {
            $SelectedPath = Resolve-FrontPath -PathText $Answer -AllowMissing
            New-Item -ItemType Directory -Path $SelectedPath -Force | Out-Null
            return $SelectedPath
        }
        catch {
            Write-Host "出力フォルダを準備できません: $($_.Exception.Message)" `
                -ForegroundColor Yellow
        }
    }
}

function Select-ASRModel {
    while ($true) {
        Write-Host "`nASRモデルを選択してください。" -ForegroundColor Cyan
        Write-Host "  1. Whisper large-v3-turbo（推奨、速度と精度のバランス）"
        Write-Host "  2. Kotoba-Whisper v2.0（日本語向け）"
        Write-Host "  3. Whisper large-v3（高精度、大容量）"
        Write-Host "  4. 登録済みモデルIDまたはローカルモデルパスを入力"
        $Choice = Read-MenuChoice -Prompt "選択 [1]" -ValidChoices @("1", "2", "3", "4") `
            -Default "1"
        switch ($Choice) {
            "1" { return "large-v3-turbo" }
            "2" { return "kotoba-whisper-v2.0" }
            "3" { return "large-v3" }
            "4" {
                $Value = (Read-Host "モデルIDまたはローカルパス").Trim().Trim('"')
                if ($Value) {
                    return $Value
                }
                Write-Host "モデルIDを入力してください。" -ForegroundColor Yellow
            }
        }
    }
}

function Select-DiarizationModel {
    while ($true) {
        Write-Host "`n話者分離モデルを選択してください。" -ForegroundColor Cyan
        Write-Host "  1. pyannote community-1（推奨）"
        Write-Host "  2. 登録済みモデルIDまたはローカルモデルパスを入力"
        $Choice = Read-MenuChoice -Prompt "選択 [1]" -ValidChoices @("1", "2") -Default "1"
        if ($Choice -eq "1") {
            return "pyannote/speaker-diarization-community-1"
        }
        $Value = (Read-Host "モデルIDまたはローカルパス").Trim().Trim('"')
        if ($Value) {
            return $Value
        }
        Write-Host "モデルIDを入力してください。" -ForegroundColor Yellow
    }
}

function Select-Device {
    Write-Host "`n実行デバイスを選択してください。" -ForegroundColor Cyan
    Write-Host "  1. auto（利用可能なCUDAを優先）"
    Write-Host "  2. cpu"
    Write-Host "  3. cuda:0"
    Write-Host "  4. その他のデバイスIDを入力"
    $Choice = Read-MenuChoice -Prompt "選択 [1]" -ValidChoices @("1", "2", "3", "4") `
        -Default "1"
    switch ($Choice) {
        "1" { return "auto" }
        "2" { return "cpu" }
        "3" { return "cuda:0" }
        "4" {
            while ($true) {
                $Value = (Read-Host "デバイスID（例: cuda:1）").Trim()
                if ($Value) {
                    return $Value
                }
            }
        }
    }
}

function Select-Language {
    Write-Host "`n音声言語を選択してください。" -ForegroundColor Cyan
    Write-Host "  1. 日本語 (ja)"
    Write-Host "  2. 自動判定 (auto)"
    Write-Host "  3. 英語 (en)"
    Write-Host "  4. その他の言語コードを入力"
    $Choice = Read-MenuChoice -Prompt "選択 [1]" -ValidChoices @("1", "2", "3", "4") `
        -Default "1"
    switch ($Choice) {
        "1" { return "ja" }
        "2" { return "auto" }
        "3" { return "en" }
        "4" {
            while ($true) {
                $Value = (Read-Host "言語コード（例: de, fr, zh）").Trim().ToLowerInvariant()
                if ($Value) {
                    return $Value
                }
            }
        }
    }
}

function Select-OutputFormats {
    while ($true) {
        Write-Host "`n出力形式を選択してください。" -ForegroundColor Cyan
        Write-Host "  1. 推奨: SRT, JSON, Markdown"
        Write-Host "  2. 全形式: SRT, VTT, JSON, TXT, Markdown"
        Write-Host "  3. 字幕: SRT, VTT"
        Write-Host "  4. テキスト: TXT, Markdown"
        Write-Host "  5. カンマ区切りで指定"
        $Choice = Read-MenuChoice -Prompt "選択 [1]" `
            -ValidChoices @("1", "2", "3", "4", "5") -Default "1"
        switch ($Choice) {
            "1" { return "srt,json,md" }
            "2" { return "srt,vtt,json,txt,md" }
            "3" { return "srt,vtt" }
            "4" { return "txt,md" }
            "5" {
                $RawFormats = (Read-Host "形式 (srt,vtt,json,txt,md)").Trim().ToLowerInvariant()
                $SelectedFormats = @(
                    $RawFormats -split "," |
                        ForEach-Object { $_.Trim() } |
                        Where-Object { $_ } |
                        Select-Object -Unique
                )
                $Invalid = @($SelectedFormats | Where-Object { $_ -notin @("srt", "vtt", "json", "txt", "md") })
                if ($SelectedFormats.Count -gt 0 -and $Invalid.Count -eq 0) {
                    return ($SelectedFormats -join ",")
                }
                Write-Host "対応形式から1つ以上選択してください。" -ForegroundColor Yellow
            }
        }
    }
}

function Add-GlobArguments {
    param(
        [Parameter(Mandatory = $true)]
        [System.Collections.Generic.List[string]]$CommandArguments,
        [Parameter(Mandatory = $true)][string]$Option,
        [string]$Prompt
    )

    $RawPatterns = (Read-Host $Prompt).Trim()
    foreach ($RawPattern in ($RawPatterns -split ",")) {
        $Pattern = $RawPattern.Trim()
        if ($Pattern) {
            $CommandArguments.Add($Option)
            $CommandArguments.Add($Pattern)
        }
    }
}

function Start-TranscriptionWizard {
    $SelectedInputPath = Select-InputPath
    if ($null -eq $SelectedInputPath) {
        return
    }
    $SelectedOutputPath = Select-OutputPath
    $InputIsDirectory = Test-Path -LiteralPath $SelectedInputPath -PathType Container

    Write-Host "`nASRバックエンドを選択してください。" -ForegroundColor Cyan
    Write-Host "  1. auto（現在はfaster-whisperを使用）"
    Write-Host "  2. faster-whisper"
    $BackendChoice = Read-MenuChoice -Prompt "選択 [1]" -ValidChoices @("1", "2") -Default "1"
    $SelectedASRBackend = if ($BackendChoice -eq "1") { "auto" } else { "faster-whisper" }
    $SelectedASRModel = Select-ASRModel
    $SelectedDevice = Select-Device
    $SelectedLanguage = Select-Language

    $UseDiarization = Read-YesNo -Prompt "話者分離を使用しますか?" -DefaultYes $true
    $SelectedDiarizationModel = ""
    $SpeakerSummary = "無効"
    $SpeakerMode = "auto"
    $ExactSpeakers = 0
    $MinimumSpeakers = 0
    $MaximumSpeakers = 0
    if ($UseDiarization) {
        $SelectedDiarizationModel = Select-DiarizationModel
        Write-Host "`n話者数を指定してください。" -ForegroundColor Cyan
        Write-Host "  1. 自動推定"
        Write-Host "  2. 正確な人数を指定"
        Write-Host "  3. 最小／最大人数を指定"
        $SpeakerMode = Read-MenuChoice -Prompt "選択 [1]" -ValidChoices @("1", "2", "3") `
            -Default "1"
        if ($SpeakerMode -eq "2") {
            $ExactSpeakers = Read-PositiveInteger -Prompt "話者数"
            $SpeakerSummary = "$ExactSpeakers 人"
        }
        elseif ($SpeakerMode -eq "3") {
            while ($true) {
                $MinimumSpeakers = Read-PositiveInteger -Prompt "最小話者数"
                $MaximumSpeakers = Read-PositiveInteger -Prompt "最大話者数"
                if ($MinimumSpeakers -le $MaximumSpeakers) {
                    break
                }
                Write-Host "最小話者数は最大話者数以下にしてください。" -ForegroundColor Yellow
            }
            $SpeakerSummary = "$MinimumSpeakers〜$MaximumSpeakers 人"
        }
        else {
            $SpeakerSummary = "自動推定"
        }
    }

    $SelectedFormats = Select-OutputFormats
    $CommandArguments = [System.Collections.Generic.List[string]]::new()
    $CommandArguments.Add("transcribe")
    $CommandArguments.Add($SelectedInputPath)
    $CommandArguments.Add("--output-dir")
    $CommandArguments.Add($SelectedOutputPath)
    $CommandArguments.Add("--asr-backend")
    $CommandArguments.Add($SelectedASRBackend)
    $CommandArguments.Add("--asr-model")
    $CommandArguments.Add($SelectedASRModel)
    $CommandArguments.Add("--device")
    $CommandArguments.Add($SelectedDevice)
    $CommandArguments.Add("--language")
    $CommandArguments.Add($SelectedLanguage)
    $CommandArguments.Add("--format")
    $CommandArguments.Add($SelectedFormats)

    if ($UseDiarization) {
        $CommandArguments.Add("--diarization-backend")
        $CommandArguments.Add("pyannote")
        $CommandArguments.Add("--diarization-model")
        $CommandArguments.Add($SelectedDiarizationModel)
        if ($SpeakerMode -eq "2") {
            $CommandArguments.Add("--num-speakers")
            $CommandArguments.Add([string]$ExactSpeakers)
        }
        elseif ($SpeakerMode -eq "3") {
            $CommandArguments.Add("--min-speakers")
            $CommandArguments.Add([string]$MinimumSpeakers)
            $CommandArguments.Add("--max-speakers")
            $CommandArguments.Add([string]$MaximumSpeakers)
        }
    }
    else {
        $CommandArguments.Add("--no-diarization")
    }

    if ($InputIsDirectory) {
        if (Read-YesNo -Prompt "サブフォルダも再帰的に処理しますか?" -DefaultYes $false) {
            $CommandArguments.Add("--recursive")
        }
        Add-GlobArguments -CommandArguments $CommandArguments -Option "--include" `
            -Prompt "含めるglob（複数はカンマ区切り、空欄で指定なし）"
        Add-GlobArguments -CommandArguments $CommandArguments -Option "--exclude" `
            -Prompt "除外するglob（複数はカンマ区切り、空欄で指定なし）"
    }

    Write-Host "`n再実行方法を選択してください。" -ForegroundColor Cyan
    Write-Host "  1. resume（完了済みステージを再利用）"
    Write-Host "  2. no-resume（キャッシュを利用せず処理）"
    Write-Host "  3. force（全ステージを強制再実行）"
    $RunMode = Read-MenuChoice -Prompt "選択 [1]" -ValidChoices @("1", "2", "3") -Default "1"
    if ($RunMode -eq "2") {
        $CommandArguments.Add("--no-resume")
    }
    elseif ($RunMode -eq "3") {
        $CommandArguments.Add("--force")
    }
    if (Read-YesNo -Prompt "古いジョブロックを強制解除しますか?" -DefaultYes $false) {
        $CommandArguments.Add("--force-unlock")
    }

    $ConfigAnswer = (Read-Host "config.tomlパス（空欄で既定設定）").Trim()
    if ($ConfigAnswer) {
        $SelectedConfigPath = Resolve-FrontPath -PathText $ConfigAnswer
        $CommandArguments.Add("--config")
        $CommandArguments.Add($SelectedConfigPath)
    }

    Write-Host "`nログ表示を選択してください。" -ForegroundColor Cyan
    Write-Host "  1. 通常"
    Write-Host "  2. 詳細 (verbose)"
    Write-Host "  3. 最小 (quiet)"
    $LogChoice = Read-MenuChoice -Prompt "選択 [1]" -ValidChoices @("1", "2", "3") -Default "1"
    if ($LogChoice -eq "2") {
        $CommandArguments.Add("--verbose")
    }
    elseif ($LogChoice -eq "3") {
        $CommandArguments.Add("--quiet")
    }

    Write-Host "`n===== 実行内容 =====" -ForegroundColor Green
    Write-Host "入力: $SelectedInputPath"
    Write-Host "出力: $SelectedOutputPath"
    Write-Host "ASR: $SelectedASRBackend / $SelectedASRModel / $SelectedDevice"
    Write-Host "言語: $SelectedLanguage"
    if ($UseDiarization) {
        Write-Host "話者分離: pyannote / $SelectedDiarizationModel / $SpeakerSummary"
    }
    else {
        Write-Host "話者分離: 無効"
    }
    Write-Host "形式: $SelectedFormats"
    Show-UtteranCommand -Arguments $CommandArguments.ToArray()

    while ($true) {
        Write-Host "`n  1. 文字起こしを実行"
        Write-Host "  2. 対象だけ確認（dry-run）"
        Write-Host "  0. キャンセル"
        $Action = Read-MenuChoice -Prompt "選択" -ValidChoices @("0", "1", "2")
        if ($Action -eq "0") {
            Write-Host "キャンセルしました。"
            return
        }
        if ($Action -eq "2") {
            $DryRunArguments = @($CommandArguments.ToArray()) + "--dry-run"
            Show-UtteranCommand -Arguments $DryRunArguments
            Invoke-Utteran -Arguments $DryRunArguments
            continue
        }
        Invoke-Utteran -Arguments $CommandArguments.ToArray()
        Pause-Front
        return
    }
}

function Show-ModelsMenu {
    while ($true) {
        Write-Host "`n===== モデル管理 =====" -ForegroundColor Cyan
        Write-Host "  1. 選択可能なモデル一覧"
        Write-Host "  2. 導入済みモデル一覧"
        Write-Host "  3. モデルを番号／IDで取得"
        Write-Host "  4. モデルを削除"
        Write-Host "  5. 導入済みモデルを検証"
        Write-Host "  6. モデル保存先を表示"
        Write-Host "  0. 戻る"
        $Choice = Read-MenuChoice -Prompt "選択" -ValidChoices @("0", "1", "2", "3", "4", "5", "6")
        if ($Choice -eq "0") { return }
        switch ($Choice) {
            "1" { Invoke-Utteran -Arguments @("models", "list", "--available") }
            "2" { Invoke-Utteran -Arguments @("models", "list") }
            "3" { Invoke-Utteran -Arguments @("models", "download") }
            "4" {
                Invoke-Utteran -Arguments @("models", "list")
                $ModelID = (Read-Host "削除するモデルID（空欄で中止）").Trim()
                if ($ModelID) { Invoke-Utteran -Arguments @("models", "remove", $ModelID) }
            }
            "5" {
                $ModelID = (Read-Host "検証するモデルID（空欄ですべて）").Trim()
                if ($ModelID) {
                    Invoke-Utteran -Arguments @("models", "verify", $ModelID)
                }
                else {
                    Invoke-Utteran -Arguments @("models", "verify")
                }
            }
            "6" { Invoke-Utteran -Arguments @("models", "path") }
        }
        Pause-Front
    }
}

function Show-DevicesMenu {
    while ($true) {
        Write-Host "`n===== デバイス／バックエンド =====" -ForegroundColor Cyan
        Write-Host "  1. 読みやすい診断表示"
        Write-Host "  2. JSON表示"
        Write-Host "  0. 戻る"
        $Choice = Read-MenuChoice -Prompt "選択" -ValidChoices @("0", "1", "2")
        if ($Choice -eq "0") { return }
        if ($Choice -eq "1") {
            Invoke-Utteran -Arguments @("devices")
        }
        else {
            Invoke-Utteran -Arguments @("devices", "--json")
        }
        Pause-Front
    }
}

function Show-JobsMenu {
    while ($true) {
        Write-Host "`n===== ジョブ管理 =====" -ForegroundColor Cyan
        Write-Host "  1. ジョブ一覧"
        Write-Host "  2. ジョブ詳細"
        Write-Host "  3. 失敗ジョブを削除"
        Write-Host "  4. 指定日数より古いジョブを削除"
        Write-Host "  5. すべてのジョブを削除"
        Write-Host "  0. 戻る"
        $Choice = Read-MenuChoice -Prompt "選択" -ValidChoices @("0", "1", "2", "3", "4", "5")
        if ($Choice -eq "0") { return }
        switch ($Choice) {
            "1" { Invoke-Utteran -Arguments @("jobs", "list") }
            "2" {
                $JobID = (Read-Host "job_id（空欄で中止）").Trim()
                if ($JobID) { Invoke-Utteran -Arguments @("jobs", "show", $JobID) }
            }
            "3" { Invoke-Utteran -Arguments @("jobs", "clean", "--failed") }
            "4" {
                $Days = Read-PositiveInteger -Prompt "何日より古いジョブを削除しますか?"
                Invoke-Utteran -Arguments @("jobs", "clean", "--older-than", [string]$Days)
            }
            "5" { Invoke-Utteran -Arguments @("jobs", "clean", "--all") }
        }
        Pause-Front
    }
}

function Show-ConfigMenu {
    while ($true) {
        Write-Host "`n===== 設定管理 =====" -ForegroundColor Cyan
        Write-Host "  1. 有効な設定を表示"
        Write-Host "  2. 設定ファイルの雛形を作成"
        Write-Host "  3. 既定の設定ファイルパスを表示"
        Write-Host "  0. 戻る"
        $Choice = Read-MenuChoice -Prompt "選択" -ValidChoices @("0", "1", "2", "3")
        if ($Choice -eq "0") { return }
        switch ($Choice) {
            "1" {
                $ConfigPath = (Read-Host "config.tomlパス（空欄で既定）").Trim()
                if ($ConfigPath) {
                    Invoke-Utteran -Arguments @("config", "show", "--path", (Resolve-FrontPath $ConfigPath))
                }
                else {
                    Invoke-Utteran -Arguments @("config", "show")
                }
            }
            "2" {
                $ConfigPath = (Read-Host "作成先（空欄で既定）").Trim()
                if ($ConfigPath) {
                    $ResolvedConfig = Resolve-FrontPath -PathText $ConfigPath -AllowMissing
                    Invoke-Utteran -Arguments @("config", "init", "--path", $ResolvedConfig)
                }
                else {
                    Invoke-Utteran -Arguments @("config", "init")
                }
            }
            "3" { Invoke-Utteran -Arguments @("config", "path") }
        }
        Pause-Front
    }
}

function Invoke-SetupMenu {
    Write-Host "`n===== セットアップ／profile切替 =====" -ForegroundColor Cyan
    Write-Host "  1. CPU"
    Write-Host "  2. NVIDIA CUDA"
    Write-Host "  3. Intel/OpenVINO準備（Phase 2では検出のみ）"
    Write-Host "  0. 戻る"
    $Choice = Read-MenuChoice -Prompt "選択" -ValidChoices @("0", "1", "2", "3")
    if ($Choice -eq "0") { return }
    $Profile = switch ($Choice) {
        "1" { "cpu" }
        "2" { "cuda" }
        "3" { "intel" }
    }
    $SkipFfmpeg = Read-YesNo -Prompt "ffmpegの確認／取得を省略しますか?" -DefaultYes $false
    $HostExecutable = (Get-Process -Id $PID).Path
    $SetupArguments = @(
        "-NoLogo", "-NoProfile", "-ExecutionPolicy", "Bypass",
        "-File", $SetupScript, "-Profile", $Profile
    )
    if ($SkipFfmpeg) {
        $SetupArguments += "-SkipFfmpeg"
    }
    & $HostExecutable @SetupArguments
    if ($LASTEXITCODE -ne 0) {
        Write-Host "setup.ps1 は終了コード $LASTEXITCODE で終了しました。" -ForegroundColor Red
    }
    Pause-Front
}

function Open-FrontFolder {
    param([Parameter(Mandatory = $true)][string]$Path)

    Start-Process -FilePath "explorer.exe" -ArgumentList @($Path)
}

Write-Host "utteran interactive front"
Write-Host "Project: $ProjectRoot"
Write-Host "入力: $InputDirectory"
Write-Host "出力: $OutputDirectory"

while ($true) {
    Write-Host "`n===== メインメニュー =====" -ForegroundColor Green
    Write-Host "  1. 文字起こしを開始"
    Write-Host "  2. モデル管理"
    Write-Host "  3. デバイス／バックエンド確認"
    Write-Host "  4. ジョブ管理"
    Write-Host "  5. 設定管理"
    Write-Host "  6. setup／実行profile切替"
    Write-Host "  7. inputフォルダを開く"
    Write-Host "  8. outputフォルダを開く"
    Write-Host "  0. 終了"
    $MainChoice = Read-MenuChoice -Prompt "選択" `
        -ValidChoices @("0", "1", "2", "3", "4", "5", "6", "7", "8")
    if ($MainChoice -eq "0") {
        Write-Host "終了します。"
        break
    }
    try {
        switch ($MainChoice) {
            "1" { Start-TranscriptionWizard }
            "2" { Show-ModelsMenu }
            "3" { Show-DevicesMenu }
            "4" { Show-JobsMenu }
            "5" { Show-ConfigMenu }
            "6" { Invoke-SetupMenu }
            "7" { Open-FrontFolder -Path $InputDirectory }
            "8" { Open-FrontFolder -Path $OutputDirectory }
        }
    }
    catch {
        Write-Host "操作を完了できません: $($_.Exception.Message)" -ForegroundColor Red
        Pause-Front
    }
}
