#Requires -Version 5.1
<#
.SYNOPSIS
    Run utteran under a specific (or the default) profile venv.

.DESCRIPTION
    Resolves the profile's venv under .venvs\win-<profile> and invokes its
    utteran.exe, passing every other argument straight through untouched.

    Deliberately not an advanced-function param() block: PowerShell's
    automatic positional binding would otherwise consume the first bare
    argument (e.g. "transcribe") into whichever named parameter is declared
    first when -Profile is omitted. Parsing $args by hand keeps -Profile
    opt-in without that trap - verified against both call styles below.

.EXAMPLE
    .\run.ps1 transcribe .\input\a.mp4

.EXAMPLE
    .\run.ps1 -Profile cuda transcribe .\input\a.mp4
#>

Set-StrictMode -Version Latest
$ErrorActionPreference = "Stop"

$ProjectRoot = Split-Path -Parent $MyInvocation.MyCommand.Path

function ConvertTo-FlatArgument {
    param($Value)

    # PowerShell parses an unquoted comma list on a .ps1 script's command
    # line (e.g. --format srt,vtt,json,txt,md) as an array literal, not a
    # single string - confirmed this happens only for .ps1 invocation, not
    # a direct utteran.exe call. By the time it reaches $args, each such
    # slot is an array whose elements must be rejoined with "," to recover
    # the value the user actually typed; otherwise it collapses to a
    # space-joined string ("srt vtt json txt md") the CLI rejects.
    if ($Value -is [array]) {
        return ($Value -join ",")
    }
    return [string]$Value
}

$SelectedProfile = $null
$PassThroughArguments = [System.Collections.Generic.List[string]]::new()
$RawArguments = $args
$Index = 0
while ($Index -lt $RawArguments.Count) {
    $Current = ConvertTo-FlatArgument $RawArguments[$Index]
    if ($Current -eq "-Profile" -and ($Index + 1) -lt $RawArguments.Count) {
        $SelectedProfile = ConvertTo-FlatArgument $RawArguments[$Index + 1]
        $Index += 2
        continue
    }
    $PassThroughArguments.Add($Current)
    $Index += 1
}

function Get-VenvRoot {
    if ($env:UTTERAN_VENV_DIR) {
        return [IO.Path]::GetFullPath($env:UTTERAN_VENV_DIR)
    }
    return Join-Path $ProjectRoot ".venvs"
}

function Get-DefaultProfileFromConfig {
    # Mirrors src/utteran/config.py's config.toml search: only the default
    # location is checked here, matching what setup.ps1 -SetDefault writes
    # to. A profile-specific --config override is the CLI's own concern,
    # not run.ps1's.
    $ConfigCandidates = @(
        (Join-Path $env:LOCALAPPDATA "utteran\utteran\config.toml")
    )
    foreach ($Candidate in $ConfigCandidates) {
        if (Test-Path -LiteralPath $Candidate -PathType Leaf) {
            $Match = Select-String -LiteralPath $Candidate -Pattern '^\s*default_profile\s*=\s*"([^"]*)"' |
                Select-Object -First 1
            if ($null -ne $Match -and $Match.Matches[0].Groups[1].Value) {
                return $Match.Matches[0].Groups[1].Value
            }
        }
    }
    return $null
}

$AllProfiles = @("cpu", "cuda", "intel", "vulkan")
$VenvRoot = Get-VenvRoot

if (-not $SelectedProfile) {
    $ConfiguredDefault = Get-DefaultProfileFromConfig
    if ($ConfiguredDefault) {
        $SelectedProfile = $ConfiguredDefault
    }
    else {
        $Existing = @($AllProfiles | Where-Object {
            Test-Path -LiteralPath (Join-Path $VenvRoot "win-$_") -PathType Container
        })
        if ($Existing.Count -eq 1) {
            $SelectedProfile = $Existing[0]
        }
        elseif ($Existing.Count -eq 0) {
            Write-Host "作成済みのプロファイルがありません。先に .\setup.ps1 -Profile <名前> を実行してください。" `
                -ForegroundColor Red
            exit 1
        }
        else {
            Write-Host (
                "既定プロファイルが未設定で、複数のプロファイルが存在します ($($Existing -join ', '))。" +
                " -Profile <名前> を指定するか、.\setup.ps1 -SetDefault <名前> を実行してください。"
            ) -ForegroundColor Red
            exit 1
        }
    }
}

if ($SelectedProfile -notin $AllProfiles) {
    Write-Host "未登録のプロファイルです: $SelectedProfile (既知: $($AllProfiles -join ', '))" -ForegroundColor Red
    exit 1
}

$VenvPath = Join-Path $VenvRoot "win-$SelectedProfile"
$Launcher = Join-Path $VenvPath "Scripts\utteran.exe"
if (-not (Test-Path -LiteralPath $Launcher -PathType Leaf)) {
    Write-Host (
        "プロファイル '$SelectedProfile' の venv が見つかりません: $VenvPath`n" +
        "先に次を実行してください: .\setup.ps1 -Profile $SelectedProfile"
    ) -ForegroundColor Red
    exit 1
}

Write-Host "[run.ps1] profile: $SelectedProfile" -ForegroundColor DarkGray
$env:UTTERAN_PROFILE = $SelectedProfile
& $Launcher @PassThroughArguments
exit $LASTEXITCODE
