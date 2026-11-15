; MOKU — Xirti / Aperia — https://github.com/Xirti/Pixiv-Bulk-Image-Downloader
#ifndef AppVersion
  #error AppVersion is required
#endif
#ifndef NameSuffix
  #define NameSuffix ""
#endif

[Setup]
AppId=MOKU
AppName=MOKU
AppVersion={#AppVersion}
AppPublisher=Xirti / Aperia
AppPublisherURL=https://github.com/Xirti/Pixiv-Bulk-Image-Downloader
AppSupportURL=https://github.com/Xirti/Pixiv-Bulk-Image-Downloader/issues
DefaultDirName={localappdata}\Programs\MOKU
DefaultGroupName=MOKU
PrivilegesRequired=lowest
ArchitecturesAllowed=x64compatible
ArchitecturesInstallIn64BitMode=x64compatible
MinVersion=10.0
DisableProgramGroupPage=yes
DisableDirPage=no
SetupIconFile=..\assets\moku-icon.ico
UninstallDisplayIcon={app}\app\MOKU.exe
OutputDir={#OutputRoot}
OutputBaseFilename=MOKU-Setup-v{#AppVersion}-windows-x64{#NameSuffix}
Compression=lzma2
SolidCompression=yes
WizardStyle=modern
LicenseFile=..\LICENSE
AppMutex=Local\MOKU.InstallationGuard.v1
SetupMutex=Local\MOKU.Setup.v1
CloseApplications=yes
RestartApplications=no
UninstallDisplayName=MOKU

[Languages]
Name: "chinesesimplified"; MessagesFile: "compiler:Languages\ChineseSimplified.isl"
Name: "english"; MessagesFile: "compiler:Default.isl"

[Tasks]
Name: "desktopicon"; Description: "创建桌面快捷方式"; Flags: unchecked

[Files]
Source: "{#PayloadRoot}\*"; DestDir: "{app}\app"; Flags: ignoreversion recursesubdirs createallsubdirs
#ifdef WebViewInstaller
Source: "{#WebViewInstaller}"; DestName: "WebView2Setup.exe"; Flags: dontcopy
#endif

[Icons]
Name: "{userprograms}\MOKU"; Filename: "{app}\app\MOKU.exe"; WorkingDir: "{app}\app"
Name: "{userdesktop}\MOKU"; Filename: "{app}\app\MOKU.exe"; WorkingDir: "{app}\app"; Tasks: desktopicon

[Run]
Filename: "{app}\app\MOKU.exe"; Description: "启动 MOKU"; Flags: nowait postinstall skipifsilent

[UninstallDelete]
; Updated components can include files added after the original installation.
Type: filesandordirs; Name: "{app}\app\_internal"
Type: filesandordirs; Name: "{app}\.moku-update-*"
Type: filesandordirs; Name: "{app}\previous-internal"
Type: dirifempty; Name: "{app}\app"
Type: dirifempty; Name: "{app}"

[Code]
#include "transaction.iss"

var
  PurgeUserData: Boolean;
  MaintenanceHandle: THandle;

function CreateMaintenanceMutex(Attributes: NativeInt; Owner: Boolean; Name: String): THandle;
  external 'CreateMutexW@kernel32.dll stdcall';
function WaitMaintenance(Handle: THandle; Milliseconds: LongWord): LongWord;
  external 'WaitForSingleObject@kernel32.dll stdcall';
function ReleaseMaintenanceMutex(Handle: THandle): Boolean;
  external 'ReleaseMutex@kernel32.dll stdcall';
function CloseMaintenanceHandle(Handle: THandle): Boolean;
  external 'CloseHandle@kernel32.dll stdcall';

procedure ReleaseMaintenance;
begin
  if MaintenanceHandle <> 0 then
  begin
    ReleaseMaintenanceMutex(MaintenanceHandle);
    CloseMaintenanceHandle(MaintenanceHandle);
    MaintenanceHandle := 0;
  end;
end;

function AcquireMaintenance: Boolean;
var
  Status: LongWord;
begin
  MaintenanceHandle := CreateMaintenanceMutex(0, False, 'Local\MOKU.MaintenanceGate.v1');
  Result := MaintenanceHandle <> 0;
  if not Result then exit;
  Status := WaitMaintenance(MaintenanceHandle, 0);
  if (Status <> 0) and (Status <> $80) then
  begin
    CloseMaintenanceHandle(MaintenanceHandle);
    MaintenanceHandle := 0;
    Result := False;
    exit;
  end;
  Result := not CheckForMutexes('Local\MOKU.InstallationGuard.v1');
  if not Result then ReleaseMaintenance;
end;

function InitializeUninstall: Boolean;
var
  Form: TSetupForm;
  Choice: TNewCheckBox;
  Hint: TNewStaticText;
  Confirm: TNewButton;
  Cancel: TNewButton;
begin
  Result := AcquireMaintenance;
  if not Result then
  begin
    SuppressibleMsgBox('请关闭所有 MOKU 窗口或等待当前安装操作完成后重试。', mbError, MB_OK, IDOK);
    exit;
  end;
  try
    RestoreReplacement;
  except
    SuppressibleMsgBox(GetExceptionMessage, mbError, MB_OK, IDOK);
    ReleaseMaintenance;
    Result := False;
    exit;
  end;
  PurgeUserData := ExpandConstant('{param:PURGEDATA|0}') = '1';
  if UninstallSilent then exit;
  Form := CreateCustomForm(480, 165, True, True);
  try
    Form.Caption := '卸载 MOKU';
    Choice := TNewCheckBox.Create(Form);
    Choice.Parent := Form;
    Choice.SetBounds(ScaleX(24), ScaleY(26), ScaleX(432), ScaleY(24));
    Choice.Caption := '同时清除本机配置、登录状态、缓存和下载历史';
    Choice.Checked := False;
    Hint := TNewStaticText.Create(Form);
    Hint.Parent := Form;
    Hint.SetBounds(ScaleX(24), ScaleY(62), ScaleX(432), ScaleY(40));
    Hint.Caption := '个人数据由安装版和便携版共用。已下载的作品文件会保留。';
    Hint.WordWrap := True;
    Confirm := TNewButton.Create(Form);
    Confirm.Parent := Form;
    Confirm.SetBounds(ScaleX(276), ScaleY(119), ScaleX(84), ScaleY(26));
    Confirm.Caption := '卸载';
    Confirm.ModalResult := mrOK;
    Confirm.Default := True;
    Cancel := TNewButton.Create(Form);
    Cancel.Parent := Form;
    Cancel.SetBounds(ScaleX(372), ScaleY(119), ScaleX(84), ScaleY(26));
    Cancel.Caption := '取消';
    Cancel.ModalResult := mrCancel;
    Cancel.Cancel := True;
    Result := Form.ShowModal = mrOK;
    PurgeUserData := Result and Choice.Checked;
  finally
    Form.Free;
  end;
end;

procedure DeinitializeUninstall;
begin
  ReleaseMaintenance;
end;

procedure CurUninstallStepChanged(CurUninstallStep: TUninstallStep);
var
  Code: Integer;
begin
  if (CurUninstallStep = usUninstall) and PurgeUserData then
    if not Exec(ExpandConstant('{app}\app\MOKU.exe'), '--uninstall-data', '',
      SW_HIDE, ewWaitUntilTerminated, Code) or (Code <> 0) then
      RaiseException('个人数据清理失败，请处理提示后重试卸载。');
end;

function InitializeSetup: Boolean;
var
  InstalledVersion: String;
  InstalledPacked: Int64;
  SetupPacked: Int64;
begin
  Result := True;
  if RegQueryStringValue(HKCU, UninstallKey,
    'DisplayVersion', InstalledVersion) then
    if StrToVersion(InstalledVersion, InstalledPacked) and StrToVersion('{#AppVersion}', SetupPacked) and
      (ComparePackedVersion(InstalledPacked, SetupPacked) > 0) then
    begin
      SuppressibleMsgBox('已安装较新版本的 MOKU。', mbError, MB_OK, IDOK);
      Result := False;
    end;
  if Result then
  begin
    Result := AcquireMaintenance;
    if not Result then
      SuppressibleMsgBox('请关闭所有 MOKU 窗口或等待当前安装操作完成后重试。', mbError, MB_OK, IDOK);
  end;
end;

procedure CurStepChanged(CurStep: TSetupStep);
begin
  if (CurStep = ssInstall) and not TransactionPrepared then Abort;
  if CurStep = ssPostInstall then
  begin
    CommitReplacement;
    ReleaseMaintenance;
  end;
end;

function GetCustomSetupExitCode: Integer;
begin
  Result := 0;
  if TransactionPrepared and not TransactionCommitted then Result := 1;
end;

procedure DeinitializeSetup;
begin
  if MaintenanceHandle = 0 then exit;
  try
    if not TransactionCommitted then RestoreReplacement;
  finally
    ReleaseMaintenance;
  end;
end;

function WebViewAvailable: Boolean;
var
  Version: String;
  Client: String;
begin
  Client := 'Software\Microsoft\EdgeUpdate\Clients\{F3017226-FE2A-4295-8BDF-00C3A9A7E4C5}';
  Result := (RegQueryStringValue(HKCU, Client, 'pv', Version) and
    (Version <> '') and (Version <> '0.0.0.0')) or
    (RegQueryStringValue(HKLM32, Client, 'pv', Version) and
    (Version <> '') and (Version <> '0.0.0.0'));
end;

function PrepareToInstall(var NeedsRestart: Boolean): String;
#ifdef WebViewInstaller
var
  Code: Integer;
#endif
begin
  Result := '';
  if not WebViewAvailable then
  begin
#ifdef WebViewInstaller
    ExtractTemporaryFile('WebView2Setup.exe');
    if not Exec(ExpandConstant('{tmp}\WebView2Setup.exe'), '/silent /install', '',
      SW_HIDE, ewWaitUntilTerminated, Code) then
      Log('Could not start WebView2 installer');
#endif
    if not WebViewAvailable then
    begin
      Result := '请先安装 Microsoft Edge WebView2 Runtime，再继续安装 MOKU。' + #13#10 +
        'https://developer.microsoft.com/microsoft-edge/webview2/';
      exit;
    end;
  end;
  if TransactionPrepared then exit;
  try
    PrepareReplacement;
  except
    Result := GetExceptionMessage;
    try
      RestoreReplacement;
    except
      Result := Result + #13#10 + GetExceptionMessage;
    end;
  end;
end;
