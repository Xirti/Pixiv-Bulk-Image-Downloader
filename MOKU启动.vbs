Option Explicit
Dim shell, fso, projectDir, launcher
Set shell = CreateObject("WScript.Shell")
Set fso = CreateObject("Scripting.FileSystemObject")
projectDir = fso.GetParentFolderName(WScript.ScriptFullName)
launcher = projectDir & "\启动MOKU.cmd"
If Not fso.FileExists(launcher) Then
  MsgBox "MOKU launcher was not found: " & launcher, 16, "MOKU"
  WScript.Quit 2
End If
shell.CurrentDirectory = projectDir
shell.Run "cmd.exe /d /c """ & launcher & """", 1, False
Set fso = Nothing
Set shell = Nothing
