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

[Code]
var
  PreviousInternal: Boolean;
  InstallationComplete: Boolean;
  PurgeUserData: Boolean;

function InitializeUninstall: Boolean;
var
  Form: TSetupForm;
  Choice: TNewCheckBox;
  Hint: TNewStaticText;
  Confirm: TNewButton;
  Cancel: TNewButton;
begin
  PurgeUserData := ExpandConstant('{param:PURGEDATA|0}') = '1';
  Result := True;
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
  if RegQueryStringValue(HKCU, 'Software\Microsoft\Windows\CurrentVersion\Uninstall\MOKU_is1',
    'DisplayVersion', InstalledVersion) then
    if StrToVersion(InstalledVersion, InstalledPacked) and StrToVersion('{#AppVersion}', SetupPacked) and
      (ComparePackedVersion(InstalledPacked, SetupPacked) > 0) then
    begin
      SuppressibleMsgBox('已安装较新版本的 MOKU。', mbError, MB_OK, IDOK);
      Result := False;
    end;
end;

procedure CurStepChanged(CurStep: TSetupStep);
var
  Entry: TFindRec;
begin
  if CurStep = ssInstall then
  begin
    if DirExists(ExpandConstant('{app}\previous-internal')) then
    begin
      PreviousInternal := True;
      if not DelTree(ExpandConstant('{app}\app\_internal'), True, True, True) then
        RaiseException('请关闭 MOKU 后重试安装。');
    end
    else if DirExists(ExpandConstant('{app}\app\_internal')) then
    begin
      if not RenameFile(ExpandConstant('{app}\app\_internal'), ExpandConstant('{app}\previous-internal')) then
        RaiseException('请关闭 MOKU 后重试安装。');
      PreviousInternal := True;
    end;
  end;
  if CurStep = ssPostInstall then
  begin
    { Retire old update journals before launching the freshly installed app. }
    if FindFirst(ExpandConstant('{app}\.moku-update-*'), Entry) then
    begin
      try
        repeat
          if (Entry.Attributes and FILE_ATTRIBUTE_DIRECTORY) <> 0 then
            if not DelTree(ExpandConstant('{app}\') + Entry.Name, True, True, True) then
              RaiseException('更新暂存目录仍被占用，请关闭 MOKU 后重试安装。');
        until not FindNext(Entry);
      finally
        FindClose(Entry);
      end;
    end;
    InstallationComplete := True;
    if PreviousInternal then
      if not DelTree(ExpandConstant('{app}\previous-internal'), True, True, True) then
        Log('Old component backup retained for cleanup during uninstall');
  end;
end;

procedure DeinitializeSetup;
begin
  if PreviousInternal and not InstallationComplete then
  begin
    DelTree(ExpandConstant('{app}\app\_internal'), True, True, True);
    if not RenameFile(ExpandConstant('{app}\previous-internal'), ExpandConstant('{app}\app\_internal')) then
      SuppressibleMsgBox('组件已保留在 previous-internal，请重新运行安装程序恢复。', mbError, MB_OK, IDOK);
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
  if WebViewAvailable then exit;
#ifdef WebViewInstaller
  ExtractTemporaryFile('WebView2Setup.exe');
  if not Exec(ExpandConstant('{tmp}\WebView2Setup.exe'), '/silent /install', '',
    SW_HIDE, ewWaitUntilTerminated, Code) then
    Log('Could not start WebView2 installer');
  if WebViewAvailable then exit;
#endif
  Result := '请先安装 Microsoft Edge WebView2 Runtime，再继续安装 MOKU。' + #13#10 +
    'https://developer.microsoft.com/microsoft-edge/webview2/';
end;
