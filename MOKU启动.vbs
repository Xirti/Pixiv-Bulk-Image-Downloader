Option Explicit
Dim shell, fso, projectDir, launcher, python, app, command
Set shell = CreateObject("WScript.Shell")
Set fso = CreateObject("Scripting.FileSystemObject")
projectDir = fso.GetParentFolderName(WScript.ScriptFullName)
app = projectDir & "\moku_app.py"
If Not fso.FileExists(app) Then
  MsgBox "MOKU desktop app was not found: " & app, 16, "MOKU"
  WScript.Quit 2
End If
python = shell.ExpandEnvironmentStrings("%LocalAppData%") & "\Programs\Python\Python312\pythonw.exe"
If Not fso.FileExists(python) Then
  MsgBox "Python 3.12 was not found: " & python, 16, "MOKU"
  WScript.Quit 3
End If
shell.CurrentDirectory = projectDir
shell.Environment("PROCESS")("PYTHONNET_RUNTIME") = "netfx"
command = Chr(34) & python & Chr(34) & " " & Chr(34) & app & Chr(34)
shell.Run command, 1, False
Set fso = Nothing
Set shell = Nothing
