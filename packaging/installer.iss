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
japanese.UninstallOptionsIntro=アプリ本体（このインストーラーが配置したファイル）は、この後常に削除されます。%n%n続けて、それ以外のデータを個別に削除するか確認します。それぞれ「いいえ」を選ぶと、そのデータは削除されずに残ります。
japanese.UninstallOptionGuiSettings=GUI設定（テーマ・言語・既定フォルダー）も削除しますか?
japanese.UninstallOptionVenvs=プロファイル実行環境 (.venvs) も削除しますか? (%1)
japanese.UninstallOptionModels=ダウンロード済みモデルも削除しますか? (%1)
japanese.UninstallOptionJobs=ジョブ履歴と文字起こし結果も削除しますか? 本文を含みます。(%1)
japanese.UninstallOptionFfmpeg=ffmpegも削除しますか? (uvは共有フォルダーのため削除されません)
japanese.UninstallRemainingDataIntro=以下のデータは選択されなかったため残っています:
english.DownloadsNoticeCaption=Additional downloads are required
english.DownloadsNoticeSubCaption=First launch needs an internet connection and free disk space
english.DownloadsNoticeBody=This installer does not bundle the inference libraries or AI models (doing so would add several to a dozen-plus GB).%n%nOn first launch, the setup wizard downloads:%n%n  - uv (if not already installed, about 15 MB)%n  - the runtime environment for your chosen profile (hundreds of MB to about 5 GB, depending on GPU)%n  - a transcription model (hundreds of MB to a few GB)%n%nThese downloads need an internet connection and a few GB of free disk space in total. Sizes are shown on screen before each download starts.%n%nThis app uses the Microsoft Edge WebView2 runtime, built into Windows 11. If the window fails to appear on Windows 10, install it from developer.microsoft.com/microsoft-edge/webview2/.
english.ThirdPartyNoticeCaption=Third-party licenses
english.ThirdPartyNoticeSubCaption=utteran itself is MIT-licensed, but its dependencies and models are not
english.ThirdPartyNoticeBody=utteran's own source code is MIT-licensed, but the dependencies and AI models fetched on first launch carry their own licenses and usage terms.%n%nNotably:%n  - ffmpeg (gyan.dev build): GPLv3%n  - pyannote speaker-diarization model: requires accepting usage terms on Hugging Face (CC-BY-4.0)%n%nSee THIRD_PARTY_NOTICES.md in the install folder for the full list.
english.UninstallOptionsIntro=The app body (files this installer placed) will always be removed next.%n%nYou will now be asked about each other kind of data separately. Answering "No" leaves that data in place.
english.UninstallOptionGuiSettings=Also remove GUI settings (theme, language, default folders)?
english.UninstallOptionVenvs=Also remove profile environments (.venvs)? (%1)
english.UninstallOptionModels=Also remove downloaded models? (%1)
english.UninstallOptionJobs=Also remove job history and transcription results? This includes transcript text. (%1)
english.UninstallOptionFfmpeg=Also remove ffmpeg? (uv is kept - it shares the same folder)
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
Source: "{#RepoRoot}\src\utteran\*"; DestDir: "{app}\src\utteran"; Flags: recursesubdirs ignoreversion; Excludes: "__pycache__,*.pyc"
Source: "{#RepoRoot}\src\utteran_gui\*"; DestDir: "{app}\src\utteran_gui"; Flags: recursesubdirs ignoreversion; Excludes: "__pycache__,*.pyc"

[Icons]
Name: "{autoprograms}\utteran"; Filename: "{app}\{#MyAppExeName}"; WorkingDir: "{app}"
Name: "{autodesktop}\utteran"; Filename: "{app}\{#MyAppExeName}"; WorkingDir: "{app}"; Tasks: desktopicon

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
  DeleteGuiSettings, DeleteVenvs, DeleteModels, DeleteJobs, DeleteFfmpeg: Boolean;

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
  Result := ExpandConstant('{localappdata}\utteran-gui');
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

function JobsDir(): String;
begin
  { utteran.config: platformdirs.user_cache_dir("utteran")/jobs }
  Result := ExpandConstant('{localappdata}\utteran\utteran\Cache\jobs');
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

function InitializeUninstall(): Boolean;
begin
  Result := True;
  DeleteGuiSettings := False;
  DeleteVenvs := False;
  DeleteModels := False;
  DeleteJobs := False;
  DeleteFfmpeg := False;

  { A silent/unattended uninstall (e.g. from a script) must never delete
    multi-GB profile environments or transcription results without
    explicit interactive confirmation - it only removes the app body. }
  if UninstallSilent() then
    exit;

  { A sequence of Yes/No confirmations rather than one checklist screen:
    TInputOptionWizardPage (as returned by CreateInputOptionPage) has no
    ShowModal of its own outside the installer's automatic page sequence,
    and the uninstaller has no such sequence to attach it to. MsgBox is the
    simple, safe building block, at the cost of one dialog per item instead
    of one combined screen. }
  MsgBox(CustomMessage('UninstallOptionsIntro'), mbInformation, MB_OK);

  DeleteGuiSettings :=
    MsgBox(CustomMessage('UninstallOptionGuiSettings'), mbConfirmation, MB_YESNO) = IDYES;
  DeleteVenvs :=
    MsgBox(FmtMessage(CustomMessage('UninstallOptionVenvs'), [FormatByteSize(GetDirSize(VenvsDir()))]),
      mbConfirmation, MB_YESNO) = IDYES;
  DeleteModels :=
    MsgBox(FmtMessage(CustomMessage('UninstallOptionModels'), [FormatByteSize(GetDirSize(ModelsDir()))]),
      mbConfirmation, MB_YESNO) = IDYES;
  DeleteJobs :=
    MsgBox(FmtMessage(CustomMessage('UninstallOptionJobs'), [FormatByteSize(GetDirSize(JobsDir()))]),
      mbConfirmation, MB_YESNO) = IDYES;
  DeleteFfmpeg :=
    MsgBox(CustomMessage('UninstallOptionFfmpeg'), mbConfirmation, MB_YESNO) = IDYES;
end;

procedure CurUninstallStepChanged(CurUninstallStep: TUninstallStep);
var
  StillPresent: TStringList;
begin
  if CurUninstallStep <> usPostUninstall then
    exit;

  StillPresent := TStringList.Create;
  try
    if DeleteGuiSettings then
      DelTree(GuiSettingsDir(), True, True, True)
    else if DirExists(GuiSettingsDir()) then
      StillPresent.Add(GuiSettingsDir());

    if DeleteVenvs then
      DelTree(VenvsDir(), True, True, True)
    else if DirExists(VenvsDir()) then
      StillPresent.Add(VenvsDir());

    if DeleteModels then
      DelTree(ModelsDir(), True, True, True)
    else if DirExists(ModelsDir()) then
      StillPresent.Add(ModelsDir());

    if DeleteJobs then
      DelTree(JobsDir(), True, True, True)
    else if DirExists(JobsDir()) then
      StillPresent.Add(JobsDir());

    if DeleteFfmpeg then
      DeleteFfmpegFiles();

    if (StillPresent.Count > 0) and not UninstallSilent() then
      MsgBox(CustomMessage('UninstallRemainingDataIntro') + #13#10#13#10 + StillPresent.Text,
        mbInformation, MB_OK);
  finally
    StillPresent.Free;
  end;
end;
