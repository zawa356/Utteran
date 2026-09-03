<#
.SYNOPSIS
    Exercise silent, interactive, and overwrite-recovery uninstall paths safely.

.DESCRIPTION
    Compiles lab-only installer scripts with a distinct AppId and redirects every
    managed user path below .tmp. It seeds representative profile/model/cache and
    user-data markers, starts the frozen GUI once, and verifies deletion/retention.
    The production installer and the developer's actual user-data paths are never
    installed, removed, or rewritten by this harness.
#>
[CmdletBinding()]
param(
    [string]$BrokenRef = "origin/feat/phase-enhancement-ac-6-dialog-dnd"
)

Set-StrictMode -Version Latest
$ErrorActionPreference = "Stop"

$RepoRoot = Split-Path -Parent $PSScriptRoot
$LabRoot = [IO.Path]::GetFullPath((Join-Path $RepoRoot ".tmp\bugfix-i-uninstall-lab"))
$AllowedRoot = [IO.Path]::GetFullPath((Join-Path $RepoRoot ".tmp")) +
    [IO.Path]::DirectorySeparatorChar
if (-not $LabRoot.StartsWith($AllowedRoot, [StringComparison]::OrdinalIgnoreCase)) {
    throw "Unsafe lab path: $LabRoot"
}
if (Test-Path -LiteralPath $LabRoot) {
    Remove-Item -LiteralPath $LabRoot -Recurse -Force
}
$CompileDir = Join-Path $LabRoot "compiled"
New-Item -ItemType Directory -Path $CompileDir -Force | Out-Null

$Iscc = Join-Path $env:LocalAppData "Programs\Inno Setup 6\ISCC.exe"
if (-not (Test-Path -LiteralPath $Iscc -PathType Leaf)) {
    throw "Inno Setup compiler was not found: $Iscc"
}
$Utf8NoBom = New-Object System.Text.UTF8Encoding($false)
$LabAppId = "A34BB578-AD69-4B51-B59F-4416F172A91D"

function New-LabInstallerScript {
    param(
        [string]$Text,
        [string]$Name
    )

    $Result = $Text.Replace("E370A3A9-7D2D-46FB-BD71-4BC429AC5FED", $LabAppId)
    # Inno shell-folder constants ignore a child process's LOCALAPPDATA variable.
    # The lab copy alone uses the environment form so every target is isolated.
    $Result = $Result.Replace("{localappdata}", "{%LOCALAPPDATA}")
    $Result = [regex]::Replace(
        $Result,
        '(?m)^#define GuiDistDir .+$',
        "#define GuiDistDir `"$RepoRoot\dist\staging\installer-gui`""
    )
    $Result = [regex]::Replace(
        $Result,
        '(?m)^#define RepoRoot .+$',
        "#define RepoRoot `"$RepoRoot`""
    )
    $Result = [regex]::Replace($Result, '(?m)^OutputDir=.+$', "OutputDir=$CompileDir")
    $Result = [regex]::Replace($Result, '(?m)^OutputBaseFilename=.+$', "OutputBaseFilename=$Name")
    $Path = Join-Path $LabRoot "$Name.iss"
    [IO.File]::WriteAllText($Path, $Result, $Utf8NoBom)
    return $Path
}

function Invoke-LabCompile {
    param(
        [string]$Script,
        [string]$Version
    )

    $Log = "$Script.compile.log"
    $Process = Start-Process -FilePath $Iscc `
        -ArgumentList @("/DMyAppVersion=$Version", $Script) `
        -Wait -PassThru -NoNewWindow -RedirectStandardOutput $Log
    if ($Process.ExitCode -ne 0) {
        throw "ISCC failed ($($Process.ExitCode)): $Script"
    }
}

function Set-LabEnvironment {
    param([string]$Root)

    $env:LOCALAPPDATA = Join-Path $Root "localappdata"
    $env:APPDATA = Join-Path $Root "appdata"
    $env:USERPROFILE = Join-Path $Root "userprofile"
    foreach ($Path in @($env:LOCALAPPDATA, $env:APPDATA, $env:USERPROFILE)) {
        New-Item -ItemType Directory -Path $Path -Force | Out-Null
    }
}

function Install-Lab {
    param(
        [string]$Installer,
        [string]$Root,
        [string]$LogName
    )

    Set-LabEnvironment $Root
    $InstallDir = Join-Path $Root "install"
    $Log = Join-Path $Root $LogName
    $Process = Start-Process -FilePath $Installer -ArgumentList @(
        "/VERYSILENT", "/SUPPRESSMSGBOXES", "/NORESTART",
        "/DIR=`"$InstallDir`"", "/LOG=`"$Log`""
    ) -Wait -PassThru
    if ($Process.ExitCode -ne 0) {
        throw "Install failed ($($Process.ExitCode)): $Installer"
    }
    return $InstallDir
}

function Add-LabData {
    param([string]$InstallDir)

    $Directories = @(
        (Join-Path $InstallDir ".venvs\win-cpu"),
        (Join-Path $env:LOCALAPPDATA "utteran\utteran\Cache\models\model-a"),
        (Join-Path $env:LOCALAPPDATA "utteran\utteran\Cache\openvino-genai-compiled"),
        (Join-Path $env:LOCALAPPDATA "utteran\utteran\Cache\jobs\job-user-data"),
        (Join-Path $env:LOCALAPPDATA "utteran\utteran\Logs"),
        (Join-Path $env:LOCALAPPDATA "utteran-gui\utteran-gui"),
        (Join-Path $env:LOCALAPPDATA "utteran\bin"),
        (Join-Path $env:USERPROFILE ".utteran\native\build-a")
    )
    foreach ($Path in $Directories) {
        New-Item -ItemType Directory -Path $Path -Force | Out-Null
        Set-Content -LiteralPath (Join-Path $Path "lab.marker") -Value "bugfix-i marker"
    }
    Set-Content -LiteralPath (
        Join-Path $env:LOCALAPPDATA "utteran\utteran\Cache\device-probes-v1.json"
    ) -Value "{}"
    Set-Content -LiteralPath (
        Join-Path $env:LOCALAPPDATA "utteran\utteran\config.toml"
    ) -Value "# keep"
    Set-Content -LiteralPath (
        Join-Path $env:LOCALAPPDATA "utteran\utteran\memory-calibration.json"
    ) -Value "{}"
    Set-Content -LiteralPath (Join-Path $env:LOCALAPPDATA "utteran\bin\ffmpeg.exe") -Value "marker"
    Set-Content -LiteralPath (Join-Path $env:LOCALAPPDATA "utteran\bin\ffprobe.exe") -Value "marker"
    Set-Content -LiteralPath (Join-Path $env:LOCALAPPDATA "utteran\bin\uv.exe") -Value "keep"
}

function Assert-LabUninstallState {
    param([string]$InstallDir)

    $Removed = @(
        $InstallDir,
        (Join-Path $env:LOCALAPPDATA "utteran\utteran\Cache\models"),
        (Join-Path $env:LOCALAPPDATA "utteran\utteran\Cache\openvino-genai-compiled"),
        (Join-Path $env:LOCALAPPDATA "utteran\utteran\Cache\device-probes-v1.json"),
        (Join-Path $env:USERPROFILE ".utteran\native"),
        (Join-Path $env:LOCALAPPDATA "utteran\bin\ffmpeg.exe"),
        (Join-Path $env:LOCALAPPDATA "utteran\bin\ffprobe.exe")
    )
    $Kept = @(
        (Join-Path $env:LOCALAPPDATA "utteran\utteran\Cache\jobs\job-user-data\lab.marker"),
        (Join-Path $env:LOCALAPPDATA "utteran\utteran\config.toml"),
        (Join-Path $env:LOCALAPPDATA "utteran\utteran\memory-calibration.json"),
        (Join-Path $env:LOCALAPPDATA "utteran\utteran\Logs\lab.marker"),
        (Join-Path $env:LOCALAPPDATA "utteran-gui\utteran-gui\lab.marker"),
        (Join-Path $env:LOCALAPPDATA "utteran\bin\uv.exe")
    )
    foreach ($Path in $Removed) {
        if (Test-Path -LiteralPath $Path) {
            throw "Expected removed: $Path"
        }
    }
    foreach ($Path in $Kept) {
        if (-not (Test-Path -LiteralPath $Path)) {
            throw "Expected preserved: $Path"
        }
    }
}

function Uninstall-LabSilent {
    param(
        [string]$InstallDir,
        [string]$Log
    )

    $Process = Start-Process -FilePath (Join-Path $InstallDir "unins000.exe") -ArgumentList @(
        "/VERYSILENT", "/SUPPRESSMSGBOXES", "/NORESTART", "/LOG=`"$Log`""
    ) -Wait -PassThru
    if ($Process.ExitCode -ne 0) {
        throw "Silent uninstall failed: $($Process.ExitCode)"
    }
}

$FixedText = Get-Content -LiteralPath (Join-Path $RepoRoot "packaging\installer.iss") -Raw
$BrokenLines = & git show "${BrokenRef}:packaging/installer.iss"
if ($LASTEXITCODE -ne 0) {
    throw "Could not read installer.iss from $BrokenRef"
}
$BrokenText = ($BrokenLines -join "`n") + "`n"
$FixedScript = New-LabInstallerScript $FixedText "fixed-0.1.27-lab"
$BrokenScript = New-LabInstallerScript $BrokenText "broken-0.1.26-lab"
Invoke-LabCompile $BrokenScript "0.1.26"
Invoke-LabCompile $FixedScript "0.1.27"
$FixedInstaller = Join-Path $CompileDir "fixed-0.1.27-lab.exe"
$BrokenInstaller = Join-Path $CompileDir "broken-0.1.26-lab.exe"

# Clean install, frozen GUI startup/shutdown, and silent uninstall.
$SilentRoot = Join-Path $LabRoot "silent"
$InstallDir = Install-Lab $FixedInstaller $SilentRoot "install.log"
Add-LabData $InstallDir
$Gui = Start-Process -FilePath (Join-Path $InstallDir "utteran-gui.exe") -PassThru
$Responding = $false
for ($Index = 0; $Index -lt 20; $Index++) {
    Start-Sleep -Milliseconds 500
    $Gui.Refresh()
    if ($Gui.HasExited) { break }
    if ($Gui.Responding) { $Responding = $true; break }
}
if (-not $Responding) {
    if (-not $Gui.HasExited) { Stop-Process -Id $Gui.Id -Force }
    throw "Isolated installed GUI did not become responsive"
}
$null = $Gui.CloseMainWindow()
if (-not $Gui.WaitForExit(15000)) {
    Stop-Process -Id $Gui.Id -Force
    $Gui.WaitForExit()
}
Uninstall-LabSilent $InstallDir (Join-Path $SilentRoot "uninstall.log")
Assert-LabUninstallState $InstallDir

# Interactive uninstall. Answer No to all four optional user-data deletions.
$InteractiveRoot = Join-Path $LabRoot "interactive"
$InstallDir = Install-Lab $FixedInstaller $InteractiveRoot "install.log"
Add-LabData $InstallDir
$InteractiveLog = Join-Path $InteractiveRoot "uninstall.log"
$InteractiveStarted = [DateTime]::Now
$Process = Start-Process -FilePath (Join-Path $InstallDir "unins000.exe") `
    -ArgumentList @("/LANG=english", "/LOG=`"$InteractiveLog`"") -PassThru
Add-Type -AssemblyName UIAutomationClient
function Invoke-UninstallerDialogButton {
    param(
        [System.Diagnostics.Process]$WindowProcess,
        [string]$NamePattern
    )

    for ($Attempt = 0; $Attempt -lt 50; $Attempt++) {
        Start-Sleep -Milliseconds 100
        $WindowProcess.Refresh()
        $ProcessCondition = New-Object System.Windows.Automation.PropertyCondition(
            [System.Windows.Automation.AutomationElement]::ProcessIdProperty,
            $WindowProcess.Id
        )
        $Windows = [System.Windows.Automation.AutomationElement]::RootElement.FindAll(
            [System.Windows.Automation.TreeScope]::Children,
            $ProcessCondition
        )
        $Condition = New-Object System.Windows.Automation.PropertyCondition(
            [System.Windows.Automation.AutomationElement]::ControlTypeProperty,
            [System.Windows.Automation.ControlType]::Button
        )
        foreach ($Window in $Windows) {
            $Buttons = $Window.FindAll(
                [System.Windows.Automation.TreeScope]::Descendants,
                $Condition
            )
            foreach ($Button in $Buttons) {
                if ($Button.Current.Name -match $NamePattern) {
                    $Pattern = $Button.GetCurrentPattern(
                        [System.Windows.Automation.InvokePattern]::Pattern
                    )
                    $Pattern.Invoke()
                    return
                }
            }
        }
    }
    throw "Could not find dialog button matching: $NamePattern"
}

$WindowProcess = $null
for ($Index = 0; $Index -lt 30; $Index++) {
    Start-Sleep -Milliseconds 200
    $WindowProcess = Get-Process -ErrorAction SilentlyContinue | Where-Object {
        $_.ProcessName -like "*_unins*" -and
        $_.StartTime -ge $InteractiveStarted.AddSeconds(-1) -and
        $_.MainWindowHandle -ne 0
    } | Select-Object -First 1
    if ($null -ne $WindowProcess) { break }
}
if ($null -eq $WindowProcess) {
    throw "Could not locate the interactive uninstaller window"
}
Invoke-UninstallerDialogButton $WindowProcess '^OK$'
foreach ($Choice in 1..4) {
    Invoke-UninstallerDialogButton $WindowProcess '^(No|いいえ)'
}
Invoke-UninstallerDialogButton $WindowProcess '^(Yes|はい)'
Invoke-UninstallerDialogButton $WindowProcess '^OK$'
# Inno displays its own successful-uninstall confirmation after our list.
Invoke-UninstallerDialogButton $WindowProcess '^OK$'
if (-not $WindowProcess.WaitForExit(30000)) {
    Stop-Process -Id $WindowProcess.Id -Force
    throw "Interactive uninstall automation timed out"
}
if (-not $Process.HasExited) {
    $Process.WaitForExit(5000)
}
Assert-LabUninstallState $InstallDir

# A broken 0.1.26 install is repaired by overwriting it with 0.1.27.
$UpgradeRoot = Join-Path $LabRoot "upgrade"
$InstallDir = Install-Lab $BrokenInstaller $UpgradeRoot "install-broken.log"
Add-LabData $InstallDir
$InstallDir = Install-Lab $FixedInstaller $UpgradeRoot "install-fixed-overwrite.log"
Uninstall-LabSilent $InstallDir (Join-Path $UpgradeRoot "uninstall-after-overwrite.log")
Assert-LabUninstallState $InstallDir

Write-Host "PASS: silent uninstall, interactive uninstall, and 0.1.26 -> 0.1.27 recovery"
Write-Host "Lab: $LabRoot"
