; utteran Windows installer (Phase 5d).
;
; Ships only the PyInstaller GUI shell plus enough source (pyproject.toml,
; uv.lock, src/utteran, src/utteran_gui) for setup.ps1 to build a profile
; venv on demand - never a pre-built profile venv or PyTorch. See
; docs/utteran_Phase5d_指示書.md and 要件定義.md 29章 for the reasoning.
;
; Built via build.ps1, which supplies /DMyAppVersion=<version> from
; pyproject.toml (a placeholder default below only applies to a manual
; ISCC invocation without that define, and is intentionally an obvious
; non-release string).
;
; No admin rights are required: PrivilegesRequired=lowest installs under
; the current user's own profile.

#ifndef MyAppVersion
  #define MyAppVersion "0.0.0-dev"
#endif
#define MyAppName "utteran"
#define MyAppPublisher "utteran contributors"
#define MyAppURL "https://github.com/zawa356/Utteran"
#define MyAppExeName "utteran-gui.exe"
#define GuiDistDir SourcePath + "..\dist\utteran-gui"
#define RepoRoot SourcePath + ".."

[Setup]
AppId={{E370A3A9-7D2D-46FB-BD71-4BC429AC5FED}
AppName={#MyAppName}
AppVersion={#MyAppVersion}
AppPublisher={#MyAppPublisher}
AppPublisherURL={#MyAppURL}
AppSupportURL={#MyAppURL}
AppUpdatesURL={#MyAppURL}
DefaultDirName={localappdata}\Programs\utteran
DefaultGroupName=utteran
DisableProgramGroupPage=yes
PrivilegesRequired=lowest
ArchitecturesAllowed=x64compatible
ArchitecturesInstallIn64BitMode=x64compatible
OutputDir={#RepoRoot}\dist\installer
OutputBaseFilename=utteran-setup-{#MyAppVersion}
Compression=lzma2/max
SolidCompression=yes
WizardStyle=modern
LicenseFile={#RepoRoot}\LICENSE
UninstallDisplayIcon={app}\{#MyAppExeName}
SetupIconFile={#RepoRoot}\icon\utteran.ico
ChangesEnvironment=no
; Set only when build.ps1 is given a signing command (-SignCommand); an
; unsigned build never references SignTool at all, so ISCC does not
; require one to be registered. See docs/utteran_Phase5d_指示書.md's
; "署名について" and 要件定義.md 29章 for why self-signing was rejected and
; this hook exists instead: SmartScreen reputation comes from a paid
; publish history a fresh self-signed cert cannot buy, but Azure Trusted
; Signing (or another CA) can be wired in later without touching this
; script structure.
#ifdef SignInstaller
SignTool=utteran
SignedUninstaller=yes
#endif

[Languages]
Name: "japanese"; MessagesFile: "compiler:Languages\Japanese.isl"
Name: "english"; MessagesFile: "compiler:Default.isl"

[CustomMessages]
japanese.DownloadsNoticeCaption=追加のダウンロードが必要です
japanese.DownloadsNoticeSubCaption=初回起動時にインターネット接続とディスク空き容量が必要です
japanese.DownloadsNoticeBody=このインストーラー自体には推論に使うライブラリやAIモデルを含めていません（含めると数GB〜十数GBになるためです）。%n%n初回にGUIを起動すると、セットアップウィザードが以下を追加でダウンロードします。%n%n  ・uv（まだ無い場合、約15MB）%n  ・選んだプロファイルの実行環境（数百MB〜約5GB、GPU種別による）%n  ・文字起こしモデル（数百MB〜数GB）%n%nこれらのダウンロードにはインターネット接続と、合計で数GB程度の空きディスク容量が必要です。ダウンロード前に内容と概算サイズが画面に表示されます。%n%nこのアプリはMicrosoft Edge WebView2ランタイムを使用します。Windows 11には標準搭載されています。Windows 10で画面が表示されない場合は、developer.microsoft.com/microsoft-edge/webview2/ から導入してください。
japanese.ThirdPartyNoticeCaption=サードパーティ ライセンスについて
japanese.ThirdPartyNoticeSubCaption=utteran 自体は MIT License ですが、依存ライブラリやモデルは別のライセンスです
japanese.ThirdPartyNoticeBody=utteran のソースコードは MIT License で提供されますが、初回起動時に追加取得する依存ライブラリやAIモデルには個別のライセンス・利用条件が適用されます。%n%n特に注意が必要な項目:%n  ・ffmpeg（gyan.dev配布ビルド）: GPLv3%n  ・pyannote speaker-diarization モデル: Hugging Face 上での利用条件への同意が必要（CC-BY-4.0）%n%n詳細はインストール先の THIRD_PARTY_NOTICES.md を参照してください。
japanese.UninstallOptionsIntro=アプリ本体と、再構築できる実行環境・キャッシュは常に削除されます（プロファイル .venvs、モデル、OpenVINOキャッシュ、デバイスキャッシュ、native build、ffmpeg）。%n%n続けて、利用者データを個別に削除するか確認します。「いいえ」を選ぶと保持されます。
japanese.UninstallOptionGuiSettings=GUI設定、CLI設定、メモリ較正データも削除しますか?
japanese.UninstallOptionJobs=ジョブ履歴と文字起こし結果も削除しますか? 本文を含みます。(%1)
japanese.UninstallOptionLogs=ログ、診断、ベンチマーク結果も削除しますか? 生ログは文字起こし本文を含む場合があります。(%1)
japanese.UninstallOptionToken=Windows資格情報マネージャーに保存したHugging Faceトークンも削除しますか?
japanese.UninstallTokenFailed=Hugging Faceトークンを削除できませんでした。Windows資格情報マネージャーで service「utteran」、user「huggingface」を確認してください。
japanese.UninstallRemainingDataIntro=以下のデータは選択されなかったため残っています:
english.DownloadsNoticeCaption=Additional downloads are required
english.DownloadsNoticeSubCaption=First launch needs an internet connection and free disk space
english.DownloadsNoticeBody=This installer does not bundle the inference libraries or AI models (doing so would add several to a dozen-plus GB).%n%nOn first launch, the setup wizard downloads:%n%n  - uv (if not already installed, about 15 MB)%n  - the runtime environment for your chosen profile (hundreds of MB to about 5 GB, depending on GPU)%n  - a transcription model (hundreds of MB to a few GB)%n%nThese downloads need an internet connection and a few GB of free disk space in total. Sizes are shown on screen before each download starts.%n%nThis app uses the Microsoft Edge WebView2 runtime, built into Windows 11. If the window fails to appear on Windows 10, install it from developer.microsoft.com/microsoft-edge/webview2/.
english.ThirdPartyNoticeCaption=Third-party licenses
english.ThirdPartyNoticeSubCaption=utteran itself is MIT-licensed, but its dependencies and models are not
english.ThirdPartyNoticeBody=utteran's own source code is MIT-licensed, but the dependencies and AI models fetched on first launch carry their own licenses and usage terms.%n%nNotably:%n  - ffmpeg (gyan.dev build): GPLv3%n  - pyannote speaker-diarization model: requires accepting usage terms on Hugging Face (CC-BY-4.0)%n%nSee THIRD_PARTY_NOTICES.md in the install folder for the full list.
english.UninstallOptionsIntro=The app body and all reproducible runtimes/caches are always removed (profile .venvs, models, OpenVINO caches, device cache, native builds, and ffmpeg).%n%nYou will now be asked about user data. Answering "No" preserves it.
english.UninstallOptionGuiSettings=Also remove GUI settings, CLI settings, and memory calibration data?
english.UninstallOptionJobs=Also remove job history and transcription results? This includes transcript text. (%1)
english.UninstallOptionLogs=Also remove logs, diagnostics, and benchmark results? Raw logs may contain transcript text. (%1)
english.UninstallOptionToken=Also remove the Hugging Face token stored in Windows Credential Manager?
english.UninstallTokenFailed=The Hugging Face token could not be removed. Check Windows Credential Manager for service "utteran", user "huggingface".
english.UninstallRemainingDataIntro=The following data was not selected and is still present:

[Tasks]
Name: "desktopicon"; Description: "{cm:CreateDesktopIcon}"; GroupDescription: "{cm:AdditionalIcons}"; Flags: unchecked

[Files]
Source: "{#GuiDistDir}\*"; DestDir: "{app}"; Flags: recursesubdirs ignoreversion
Source: "{#RepoRoot}\pyproject.toml"; DestDir: "{app}"; Flags: ignoreversion
Source: "{#RepoRoot}\uv.lock"; DestDir: "{app}"; Flags: ignoreversion
Source: "{#RepoRoot}\setup.ps1"; DestDir: "{app}"; Flags: ignoreversion
Source: "{#RepoRoot}\run.ps1"; DestDir: "{app}"; Flags: ignoreversion
Source: "{#RepoRoot}\.env.example"; DestDir: "{app}"; Flags: ignoreversion
Source: "{#RepoRoot}\README.md"; DestDir: "{app}"; Flags: ignoreversion
Source: "{#RepoRoot}\README.en.md"; DestDir: "{app}"; Flags: ignoreversion skipifsourcedoesntexist
Source: "{#RepoRoot}\LICENSE"; DestDir: "{app}"; Flags: ignoreversion
Source: "{#RepoRoot}\THIRD_PARTY_NOTICES.md"; DestDir: "{app}"; Flags: ignoreversion
Source: "{#RepoRoot}\icon\utteran-glyph-512.png"; DestDir: "{app}\icon"; Flags: ignoreversion
Source: "{#RepoRoot}\icon\utteran.ico"; DestDir: "{app}\icon"; Flags: ignoreversion
Source: "{#RepoRoot}\src\utteran\*"; DestDir: "{app}\src\utteran"; Flags: recursesubdirs ignoreversion; Excludes: "__pycache__,*.pyc"
Source: "{#RepoRoot}\src\utteran_gui\*"; DestDir: "{app}\src\utteran_gui"; Flags: recursesubdirs ignoreversion; Excludes: "__pycache__,*.pyc"

[Icons]
Name: "{autoprograms}\utteran"; Filename: "{app}\{#MyAppExeName}"; WorkingDir: "{app}"; IconFilename: "{app}\{#MyAppExeName}"
Name: "{autodesktop}\utteran"; Filename: "{app}\{#MyAppExeName}"; WorkingDir: "{app}"; IconFilename: "{app}\{#MyAppExeName}"; Tasks: desktopicon

; Do not launch the GUI from Setup's completion page. Inno Setup enables
; Windows RedirectionGuard on its own process. A GUI started here inherits
; that mitigation, and uv then fails with Win32 error 448 while inspecting
; the user's managed Python below AppData. Starting the installed shortcut
; after Setup exits creates a normal process and avoids that inherited state.
; Keep this invariant covered by tests: reintroducing a [Run] postinstall
; entry makes first-run setup fail even though the exact same GUI works after
; it is closed and opened again.

[Code]
{ ---- Install-time informational pages (no user input, just disclosure) ---- }

procedure InitializeWizard();
begin
  { Shown right after the license is accepted: dependency/model licenses
    differ from utteran's own MIT license (ffmpeg is GPLv3, the pyannote
    model requires separate Hugging Face agreement). }
  CreateOutputMsgPage(wpLicense,
    CustomMessage('ThirdPartyNoticeCaption'),
    CustomMessage('ThirdPartyNoticeSubCaption'),
    CustomMessage('ThirdPartyNoticeBody'));
  { Shown right after choosing the install folder, before the final
    confirmation page: this installer is only ~tens of MB: the real
    weight (profile venv, model) downloads on first GUI launch, and the
    user should know that before clicking Install. }
  CreateOutputMsgPage(wpSelectDir,
    CustomMessage('DownloadsNoticeCaption'),
    CustomMessage('DownloadsNoticeSubCaption'),
    CustomMessage('DownloadsNoticeBody'));
end;

{ ---- Uninstall: let the user choose what auxiliary data survives ---- }
// The app body under the install directory (this installer's own [Files]
// manifest) is always removed by Inno's default uninstaller behavior - no
// code needed for that. Everything below lives *outside* the install
// directory, in platformdirs locations (or, for .venvs, is a subdirectory
// setup.ps1 created after install that was never part of the installed
// manifest), so the default uninstaller leaves all of it alone unless this
// code explicitly removes it. That is what makes "leave selected-out data
// behind" the default instead of something this code has to special-case.

var
  DeleteUserSettings, DeleteJobs, DeleteLogs, DeleteToken: Boolean;

function GetDirSize(Path: String): Int64;
var
  FindRec: TFindRec;
  FullPath: String;
begin
  Result := 0;
  if FindFirst(Path + '\*', FindRec) then
  begin
    try
      repeat
        if (FindRec.Name <> '.') and (FindRec.Name <> '..') then
        begin
          FullPath := Path + '\' + FindRec.Name;
          if (FindRec.Attributes and FILE_ATTRIBUTE_DIRECTORY) <> 0 then
            Result := Result + GetDirSize(FullPath)
          else
            Result := Result + (Int64(FindRec.SizeHigh) * $100000000) + FindRec.SizeLow;
        end;
      until not FindNext(FindRec);
    finally
      FindClose(FindRec);
    end;
  end;
end;

function FormatByteSize(Bytes: Int64): String;
var
  Value: Extended;
  UnitIndex: Integer;
  UnitNames: array[0..4] of String;
begin
  UnitNames[0] := 'B';
  UnitNames[1] := 'KB';
  UnitNames[2] := 'MB';
  UnitNames[3] := 'GB';
  UnitNames[4] := 'TB';
  Value := Bytes;
  UnitIndex := 0;
  while (Value >= 1024) and (UnitIndex < 4) do
  begin
    Value := Value / 1024;
    UnitIndex := UnitIndex + 1;
  end;
  Result := Format('%.1f %s', [Value, UnitNames[UnitIndex]]);
end;

function GuiSettingsDir(): String;
begin
  { utteran_gui.settings.SettingsStore: platformdirs.user_config_dir("utteran-gui") }
  Result := ExpandConstant('{localappdata}\utteran-gui\utteran-gui');
end;

function CoreConfigFile(): String;
begin
  Result := ExpandConstant('{localappdata}\utteran\utteran\config.toml');
end;

function MemoryCalibrationFile(): String;
begin
  Result := ExpandConstant('{localappdata}\utteran\utteran\memory-calibration.json');
end;

function VenvsDir(): String;
begin
  // utteran_gui.cli.CliAdapter default venv_root: {app}\.venvs, since the
  // installed GUI's working directory (and CliAdapter.repo_root) is {app}.
  Result := ExpandConstant('{app}\.venvs');
end;

function ModelsDir(): String;
begin
  { utteran.models.manager: platformdirs.user_cache_dir("utteran")/models }
  Result := ExpandConstant('{localappdata}\utteran\utteran\Cache\models');
end;

function GenAICompiledCacheDir(): String;
begin
  { utteran.asr.openvino_genai: platformdirs.user_cache_dir("utteran") }
  Result := ExpandConstant('{localappdata}\utteran\utteran\Cache\openvino-genai-compiled');
end;

function CoreCacheDir(): String;
begin
  Result := ExpandConstant('{localappdata}\utteran\utteran\Cache');
end;

function DeviceProbeCacheFile(): String;
begin
  Result := CoreCacheDir() + '\device-probes-v1.json';
end;

function NativeBuildDir(): String;
begin
  Result := ExpandConstant('{userprofile}\.utteran\native');
end;

function JobsDir(): String;
begin
  { utteran.config: platformdirs.user_cache_dir("utteran")/jobs }
  Result := ExpandConstant('{localappdata}\utteran\utteran\Cache\jobs');
end;

function UserLogsDir(): String;
begin
  Result := ExpandConstant('{localappdata}\utteran\utteran\Logs');
end;

function InstallLogsDir(): String;
begin
  Result := ExpandConstant('{app}\logs');
end;

function FfmpegBinDir(): String;
begin
  { setup.ps1's $BinDir. Shared with uv.exe/uvx.exe, so removal below
    deletes only the ffmpeg files, never the directory itself. }
  Result := ExpandConstant('{localappdata}\utteran\bin');
end;

procedure DeleteFfmpegFiles();
var
  BinDir: String;
begin
  BinDir := FfmpegBinDir();
  DeleteFile(BinDir + '\ffmpeg.exe');
  DeleteFile(BinDir + '\ffprobe.exe');
end;

procedure DeleteRuntimeData();
begin
  DelTree(VenvsDir(), True, True, True);
  DelTree(ModelsDir(), True, True, True);
  DelTree(GenAICompiledCacheDir(), True, True, True);
  DeleteFile(DeviceProbeCacheFile());
  DelTree(NativeBuildDir(), True, True, True);
  DeleteFfmpegFiles();
end;

function InitializeUninstall(): Boolean;
begin
  Result := True;
  DeleteUserSettings := False;
  DeleteJobs := False;
  DeleteLogs := False;
  DeleteToken := False;

  { Silent uninstall removes reproducible runtime/cache data, but it must
    never delete user data or credentials without interactive consent. }
  if UninstallSilent() then
    exit;

  { A sequence of Yes/No confirmations rather than one checklist screen:
    TInputOptionWizardPage (as returned by CreateInputOptionPage) has no
    ShowModal of its own outside the installer's automatic page sequence,
    and the uninstaller has no such sequence to attach it to. MsgBox is the
    simple, safe building block, at the cost of one dialog per item instead
    of one combined screen. }
  MsgBox(CustomMessage('UninstallOptionsIntro'), mbInformation, MB_OK);

  DeleteUserSettings :=
    MsgBox(CustomMessage('UninstallOptionGuiSettings'), mbConfirmation, MB_YESNO) = IDYES;
  DeleteJobs :=
    MsgBox(FmtMessage(CustomMessage('UninstallOptionJobs'), [FormatByteSize(GetDirSize(JobsDir()))]),
      mbConfirmation, MB_YESNO) = IDYES;
  DeleteLogs :=
    MsgBox(FmtMessage(CustomMessage('UninstallOptionLogs'), [FormatByteSize(
      GetDirSize(UserLogsDir()) + GetDirSize(InstallLogsDir()))]),
      mbConfirmation, MB_YESNO) = IDYES;
  DeleteToken :=
    MsgBox(CustomMessage('UninstallOptionToken'), mbConfirmation, MB_YESNO) = IDYES;
end;

procedure CurUninstallStepChanged(CurUninstallStep: TUninstallStep);
var
  StillPresent: TStringList;
  ResultCode: Integer;
begin
  if CurUninstallStep = usUninstall then
  begin
    if DeleteToken then
    begin
      if (not Exec(ExpandConstant('{app}\{#MyAppExeName}'), '--delete-keyring-token',
        ExpandConstant('{app}'), SW_HIDE, ewWaitUntilTerminated, ResultCode)) or (ResultCode <> 0) then
        MsgBox(CustomMessage('UninstallTokenFailed'), mbError, MB_OK);
    end;
    { Delete app-local runtime and selected legacy logs before Inno removes
      its own files, so the install directory can become empty. }
    DeleteRuntimeData();
    if DeleteLogs then
      DelTree(InstallLogsDir(), True, True, True);
    exit;
  end;
  if CurUninstallStep <> usPostUninstall then
    exit;

  StillPresent := TStringList.Create;
  try
    DeleteRuntimeData();

    if DeleteUserSettings then
    begin
      DelTree(GuiSettingsDir(), True, True, True);
      DeleteFile(CoreConfigFile());
      DeleteFile(MemoryCalibrationFile());
    end
    else
    begin
      if DirExists(GuiSettingsDir()) then StillPresent.Add(GuiSettingsDir());
      if FileExists(CoreConfigFile()) then StillPresent.Add(CoreConfigFile());
      if FileExists(MemoryCalibrationFile()) then StillPresent.Add(MemoryCalibrationFile());
    end;

    if DeleteJobs then
      DelTree(JobsDir(), True, True, True)
    else if DirExists(JobsDir()) then
      StillPresent.Add(JobsDir());

    if DeleteLogs then
    begin
      DelTree(UserLogsDir(), True, True, True);
      DelTree(InstallLogsDir(), True, True, True);
    end
    else
    begin
      if DirExists(UserLogsDir()) then StillPresent.Add(UserLogsDir());
      if DirExists(InstallLogsDir()) then StillPresent.Add(InstallLogsDir());
    end;

    if (StillPresent.Count > 0) and not UninstallSilent() then
      MsgBox(CustomMessage('UninstallRemainingDataIntro') + #13#10#13#10 + StillPresent.Text,
        mbInformation, MB_OK);
  finally
    StillPresent.Free;
  end;
end;
