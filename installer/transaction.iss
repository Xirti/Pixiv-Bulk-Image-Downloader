// Managed names are supplied by update_package.MANAGED at build time.
#ifndef ManagedNames
  #error ManagedNames is required
#endif

const
  UninstallKey = 'Software\Microsoft\Windows\CurrentVersion\Uninstall\{#SetupSetting("AppId")}_is1';
  BackupMarker = 'MOKU installer transaction 2';
  RecoveryEntryName = 'MOKU-Recover.lnk';
var
  TransactionPrepared: Boolean;
  TransactionCommitted: Boolean;

function FileAttributes(Name: String): LongWord;
  external 'GetFileAttributesW@kernel32.dll stdcall';

function BackupRoot: String;
begin
  Result := ExpandConstant('{app}\.moku-install-backup');
end;

function RecoveryEntryPath: String;
begin
  Result := ExpandConstant('{app}\app\') + RecoveryEntryName;
end;

function ComponentNames: TStringList;
var
  Names: String;
begin
  Names := '{#ManagedNames}';
  StringChangeEx(Names, '|', #13#10, True);
  Result := TStringList.Create;
  Result.Text := Names;
end;

function IsUninstallerFile(Name: String): Boolean;
begin
  Result := (Length(Name) = 12) and (Lowercase(Copy(Name, 1, 5)) = 'unins');
  if not Result then exit;
  Result := (Name[6] >= '0') and (Name[6] <= '9') and
    (Name[7] >= '0') and (Name[7] <= '9') and (Name[8] >= '0') and (Name[8] <= '9') and
    ((Lowercase(ExtractFileExt(Name)) = '.exe') or (Lowercase(ExtractFileExt(Name)) = '.dat') or
     (Lowercase(ExtractFileExt(Name)) = '.msg'));
end;

procedure CheckParents(Name: String);
var
  Attributes: LongWord;
begin
  while Name <> ExtractFileDir(Name) do
  begin
    Attributes := FileAttributes(Name);
    if (Attributes <> $FFFFFFFF) and ((Attributes and $400) <> 0) then
      RaiseException('安装路径包含链接，请选择普通文件夹。');
    Name := ExtractFileDir(Name);
  end;
end;

function OwnsRecoveryEntry: Boolean;
var
  Attributes: LongWord;
  Entry: TFindRec;
  Journal, DigestField: String;
  Raw: AnsiString;
  Size: Int64;
begin
  Result := False;
  CheckParents(ExpandConstant('{app}\app'));
  Attributes := FileAttributes(RecoveryEntryPath);
  if (Attributes = $FFFFFFFF) or ((Attributes and ($400 or FILE_ATTRIBUTE_DIRECTORY)) <> 0) then exit;
  if not FileSize64(RecoveryEntryPath, Size) or (Size > 65536) then exit;
  // Match the digest recorded by update_install.py before replacement began.
  DigestField := '"recoveryEntrySha256": "' + Lowercase(GetSHA256OfFile(RecoveryEntryPath)) + '"';
  if FindFirst(ExpandConstant('{app}\.moku-update-*'), Entry) then
  begin
    try
      repeat
        if (Length(Entry.Name) = 29) and
          ((Entry.Attributes and ($400 or FILE_ATTRIBUTE_DIRECTORY)) = FILE_ATTRIBUTE_DIRECTORY) then
        begin
          Journal := ExpandConstant('{app}\') + Entry.Name + '\transaction.json';
          Attributes := FileAttributes(Journal);
          if (Attributes <> $FFFFFFFF) and ((Attributes and ($400 or FILE_ATTRIBUTE_DIRECTORY)) = 0) and
            FileSize64(Journal, Size) and (Size <= 65536) and LoadStringFromFile(Journal, Raw) and
            (Pos(DigestField, String(Raw)) > 0) then
          begin
            Result := True;
            exit;
          end;
        end;
      until not FindNext(Entry);
    finally
      FindClose(Entry);
    end;
  end;
end;

procedure CheckTree(Name: String);
var
  Attributes: LongWord;
  Entry: TFindRec;
begin
  Attributes := FileAttributes(Name);
  if Attributes = $FFFFFFFF then exit;
  if (Attributes and $400) <> 0 then
    RaiseException('安装目录包含链接，请选择普通文件夹。');
  if (Attributes and FILE_ATTRIBUTE_DIRECTORY) <> 0 then
    if FindFirst(AddBackslash(Name) + '*', Entry) then
    begin
      try
        repeat
          if (Entry.Name <> '.') and (Entry.Name <> '..') then
            CheckTree(AddBackslash(Name) + Entry.Name);
        until not FindNext(Entry);
      finally
        FindClose(Entry);
      end;
    end;
end;

procedure RemoveComponent(Name: String);
begin
  CheckParents(ExtractFileDir(Name));
  CheckTree(Name);
  if DirExists(Name) then
  begin
    if not DelTree(Name, True, True, True) then RaiseException('组件恢复失败，完整备份已保留。');
  end
  else if FileExists(Name) then
    if not DeleteFile(Name) then RaiseException('组件恢复失败，完整备份已保留。');
end;

procedure MoveEntry(Source, Destination: String);
begin
  if not ForceDirectories(ExtractFileDir(Destination)) or not RenameFile(Source, Destination) then
    RaiseException('无法移动安装组件，请关闭占用文件的程序后重试。');
end;

function EncodeRegistrationLine(Value: String): String;
begin
  StringChangeEx(Value, '%', '%25', True);
  StringChangeEx(Value, #13, '%0D', True);
  StringChangeEx(Value, #10, '%0A', True);
  Result := Value;
end;

function DecodeRegistrationLine(Value: String): String;
begin
  StringChangeEx(Value, '%0D', #13, True);
  StringChangeEx(Value, '%0A', #10, True);
  StringChangeEx(Value, '%25', '%', True);
  Result := Value;
end;

procedure SaveRegistration;
var
  Names, Rows: TArrayOfString;
  Index, Count: Integer;
  Value, Kind: String;
  Number: Cardinal;
begin
  if RegKeyExists(HKCU, UninstallKey) then
  begin
    if not RegGetValueNames(HKCU, UninstallKey, Names) then
      RaiseException('无法读取旧安装登记。');
    for Index := 0 to GetArrayLength(Names) - 1 do
    begin
      Kind := '';
      if RegQueryStringValue(HKCU, UninstallKey, Names[Index], Value) then Kind := 'string'
      else if RegQueryDWordValue(HKCU, UninstallKey, Names[Index], Number) then
      begin
        Kind := 'number';
        Value := IntToStr(Number);
      end;
      if Kind = '' then RaiseException('旧安装登记包含无法备份的数据类型。');
      Count := GetArrayLength(Rows);
      SetArrayLength(Rows, Count + 3);
      Rows[Count] := EncodeRegistrationLine(Names[Index]);
      Rows[Count + 1] := Kind;
      Rows[Count + 2] := EncodeRegistrationLine(Value);
    end;
  end;
  if not SaveStringsToUTF8File(BackupRoot + '\registration.txt', Rows, False) then
    RaiseException('无法保存旧安装登记。');
end;

function ReadRegistration: TArrayOfString;
var
  Rows: TArrayOfString;
  Index: Integer;
  Number: Int64;
begin
  if not LoadStringsFromFile(BackupRoot + '\registration.txt', Rows) or
     ((GetArrayLength(Rows) mod 3) <> 0) then
    RaiseException('安装登记备份无法读取，请保留完整备份。');
  for Index := 0 to GetArrayLength(Rows) - 1 do
    if (Index mod 3) <> 1 then Rows[Index] := DecodeRegistrationLine(Rows[Index]);
  for Index := 0 to (GetArrayLength(Rows) div 3) - 1 do
  begin
    if (Rows[Index * 3 + 1] <> 'string') and (Rows[Index * 3 + 1] <> 'number') then
      RaiseException('安装登记备份格式无效。');
    if Rows[Index * 3 + 1] = 'number' then
    begin
      Number := StrToInt64Def(Rows[Index * 3 + 2], -1);
      if (Number < 0) or (Number > $FFFFFFFF) then RaiseException('安装登记备份数值无效。');
    end;
  end;
  Result := Rows;
end;

procedure RestoreRegistration(Rows: TArrayOfString);
var
  Index: Integer;
  Written: Boolean;
begin
  if RegKeyExists(HKCU, UninstallKey) then
    if not RegDeleteKeyIncludingSubkeys(HKCU, UninstallKey) then
      RaiseException('无法恢复旧安装登记，请检查权限。');
  for Index := 0 to (GetArrayLength(Rows) div 3) - 1 do
  begin
    if Rows[Index * 3 + 1] = 'string' then
      Written := RegWriteStringValue(HKCU, UninstallKey, Rows[Index * 3], Rows[Index * 3 + 2])
    else
      Written := RegWriteDWordValue(HKCU, UninstallKey, Rows[Index * 3], StrToInt64(Rows[Index * 3 + 2]));
    if not Written then RaiseException('安装登记恢复失败，完整备份已保留。');
  end;
end;

procedure RestoreReplacement;
var
  Marker, CommitMarker: AnsiString;
  Registration: TArrayOfString;
  Names, Originals: TStringList;
  Index: Integer;
  Source, Target: String;
  Entry: TFindRec;
begin
  if not DirExists(BackupRoot) then exit;
  CheckParents(BackupRoot);
  if not LoadStringFromFile(BackupRoot + '\owner', Marker) or (Marker <> BackupMarker) then
    RaiseException('安装备份目录无法识别，请保留该目录并检查。');
  CheckTree(BackupRoot);
  if LoadStringFromFile(BackupRoot + '\committed', CommitMarker) and (CommitMarker = BackupMarker) then
  begin
    if not DelTree(BackupRoot, True, True, True) then
      RaiseException('旧安装备份仍被占用，请关闭相关程序后重试。');
    exit;
  end;
  // No installed file is moved before the snapshot-ready marker is written.
  if not FileExists(BackupRoot + '\snapshot-ready') then
  begin
    if not DelTree(BackupRoot, True, True, True) then
      RaiseException('未完成的备份仍被占用，请关闭相关程序后重试。');
    exit;
  end;
  Registration := ReadRegistration;
  Names := ComponentNames;
  Originals := TStringList.Create;
  try
    Originals.LoadFromFile(BackupRoot + '\original-components.txt');
    for Index := 0 to Originals.Count - 1 do
      if Names.IndexOf(Originals[Index]) < 0 then RaiseException('组件备份清单无法识别。');
    for Index := 0 to Names.Count - 1 do
    begin
      Source := BackupRoot + '\components\' + Names[Index];
      if FileExists(Source) or DirExists(Source) then
      begin
        Target := ExpandConstant('{app}\app\') + Names[Index];
        RemoveComponent(Target);
        MoveEntry(Source, Target);
      end
      else if Originals.IndexOf(Names[Index]) < 0 then
        RemoveComponent(ExpandConstant('{app}\app\') + Names[Index]);
    end;
  finally
    Originals.Free;
    Names.Free;
  end;
  if FindFirst(BackupRoot + '\updates\*', Entry) then
  begin
    try
      repeat
        if (Entry.Name <> '.') and (Entry.Name <> '..') then
          MoveEntry(BackupRoot + '\updates\' + Entry.Name, ExpandConstant('{app}\') + Entry.Name);
      until not FindNext(Entry);
    finally
      FindClose(Entry);
    end;
  end;
  Source := BackupRoot + '\recovery\' + RecoveryEntryName;
  if FileExists(Source) then MoveEntry(Source, RecoveryEntryPath);
  if FindFirst(ExpandConstant('{app}\unins*.*'), Entry) then
  begin
    try
      repeat
        if IsUninstallerFile(Entry.Name) and not FileExists(BackupRoot + '\uninstall\' + Entry.Name) then
          if not DeleteFile(ExpandConstant('{app}\') + Entry.Name) then
            RaiseException('无法清理本次新增的卸载文件。');
      until not FindNext(Entry);
    finally
      FindClose(Entry);
    end;
  end;
  if FindFirst(BackupRoot + '\uninstall\*', Entry) then
  begin
    try
      repeat
        if (Entry.Attributes and FILE_ATTRIBUTE_DIRECTORY) = 0 then
          if not CopyFile(BackupRoot + '\uninstall\' + Entry.Name, ExpandConstant('{app}\') + Entry.Name, False) then
            RaiseException('卸载信息恢复失败，完整备份已保留。');
      until not FindNext(Entry);
    finally
      FindClose(Entry);
    end;
  end;
  RestoreRegistration(Registration);
  if not DelTree(BackupRoot, True, True, True) then
    RaiseException('组件已恢复，备份目录仍被占用，请关闭相关程序后重试。');
  RemoveDir(ExpandConstant('{app}\app'));
end;

procedure PrepareReplacement;
var
  Names, Originals: TStringList;
  Index: Integer;
  Name: String;
  Entry: TFindRec;
begin
  CheckParents(ExpandConstant('{app}\app'));
  RestoreReplacement;
  if not ForceDirectories(BackupRoot + '\components') or
     not SaveStringToFile(BackupRoot + '\owner', BackupMarker, False) then
    RaiseException('无法创建安装备份目录。');
  SaveRegistration;
  if FindFirst(ExpandConstant('{app}\unins*.*'), Entry) then
  begin
    try
      repeat
        if IsUninstallerFile(Entry.Name) and ((Entry.Attributes and FILE_ATTRIBUTE_DIRECTORY) = 0) then
        begin
          Name := ExpandConstant('{app}\') + Entry.Name;
          CheckTree(Name);
          if not ForceDirectories(BackupRoot + '\uninstall') or
             not CopyFile(Name, BackupRoot + '\uninstall\' + Entry.Name, False) then
            RaiseException('无法备份卸载信息。');
        end;
      until not FindNext(Entry);
    finally
      FindClose(Entry);
    end;
  end;
  Names := ComponentNames;
  Originals := TStringList.Create;
  try
    for Index := 0 to Names.Count - 1 do
    begin
      Name := ExpandConstant('{app}\app\') + Names[Index];
      CheckTree(Name);
      if FileExists(Name) or DirExists(Name) then Originals.Add(Names[Index]);
    end;
    Originals.SaveToFile(BackupRoot + '\original-components.txt');
  finally
    Originals.Free;
    Names.Free;
  end;
  if not SaveStringToFile(BackupRoot + '\snapshot-ready', BackupMarker, False) then
    RaiseException('无法提交旧组件备份。');
  if OwnsRecoveryEntry then
    MoveEntry(RecoveryEntryPath, BackupRoot + '\recovery\' + RecoveryEntryName);
  if FindFirst(ExpandConstant('{app}\.moku-update-*'), Entry) then
  begin
    try
      repeat
        Name := ExpandConstant('{app}\') + Entry.Name;
        CheckTree(Name);
        MoveEntry(Name, BackupRoot + '\updates\' + Entry.Name);
      until not FindNext(Entry);
    finally
      FindClose(Entry);
    end;
  end;
  Names := ComponentNames;
  try
    for Index := 0 to Names.Count - 1 do
    begin
      Name := ExpandConstant('{app}\app\') + Names[Index];
      if FileExists(Name) or DirExists(Name) then
        MoveEntry(Name, BackupRoot + '\components\' + Names[Index]);
    end;
  finally
    Names.Free;
  end;
  TransactionPrepared := True;
end;

procedure CommitReplacement;
begin
  if not SaveStringToFile(BackupRoot + '\committed', BackupMarker, False) then
    RaiseException('无法提交安装记录，旧版备份已保留。');
  TransactionCommitted := True;
  if not DelTree(BackupRoot, True, True, True) then
    Log('Committed installation backup retained for the next maintenance operation');
  if DirExists(ExpandConstant('{app}\previous-internal')) then
    if not DelTree(ExpandConstant('{app}\previous-internal'), True, True, True) then
      Log('Legacy component backup retained for cleanup during uninstall');
end;
