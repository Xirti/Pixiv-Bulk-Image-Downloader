param(
  [int]$TimeoutSeconds = 45,
  [string]$Initial = 'C:\Users\Public\Documents\MOKU-Folder-Probe'
)
$ErrorActionPreference = 'Stop'
$runtimeFile = Join-Path $env:LocalAppData 'MOKU\runtime\backend.json'
$runtime = Get-Content -LiteralPath $runtimeFile -Raw -Encoding UTF8 | ConvertFrom-Json
$responseFile = Join-Path $env:TEMP 'moku-folder-select-response.json'
$requestFile = Join-Path $env:TEMP 'moku-folder-select-request.ps1'
Remove-Item -LiteralPath $responseFile -Force -ErrorAction SilentlyContinue
New-Item -ItemType Directory -Path $Initial -Force | Out-Null
$uri = "http://127.0.0.1:$($runtime.port)/api/system/select-folder"
$origin = "http://127.0.0.1:$($runtime.port)"
$health = (Invoke-WebRequest -UseBasicParsing -Uri ("http://127.0.0.1:$($runtime.port)/api/health") -TimeoutSec 5).Content | ConvertFrom-Json
$requestToken = [string]$health.requestToken
$body = (@{ initial = $Initial } | ConvertTo-Json -Compress).Replace("'", "''")
$lines = @(
  '$ErrorActionPreference = ''Stop''',
  ('$r = Invoke-WebRequest -UseBasicParsing -Method Post -Uri ''' + $uri + ''' -ContentType ''application/json'' -Headers @{ Origin = ''' + $origin + '''; ''X-MOKU-Request-Token'' = ''' + $requestToken + ''' } -Body ([Text.Encoding]::UTF8.GetBytes(''' + $body + ''')) -TimeoutSec 360'),
  ('$r.Content | Set-Content -LiteralPath ''' + $responseFile + ''' -Encoding UTF8')
)
[IO.File]::WriteAllLines($requestFile, $lines, (New-Object Text.UTF8Encoding($false)))

Add-Type -AssemblyName UIAutomationClient
Add-Type -AssemblyName UIAutomationTypes
Add-Type @'
using System;
using System.Collections.Generic;
using System.Runtime.InteropServices;
using System.Text;
public static class MokuFolderSelectNative {
  public delegate bool EnumCallback(IntPtr hwnd, IntPtr state);
  [DllImport("user32.dll")] public static extern bool EnumWindows(EnumCallback callback, IntPtr state);
  [DllImport("user32.dll", CharSet=CharSet.Unicode)] public static extern int GetClassName(IntPtr hwnd, StringBuilder text, int count);
  [DllImport("user32.dll", CharSet=CharSet.Unicode)] public static extern int GetWindowText(IntPtr hwnd, StringBuilder text, int count);
  [DllImport("user32.dll")] public static extern uint GetWindowThreadProcessId(IntPtr hwnd, out uint processId);
  [DllImport("user32.dll")] public static extern IntPtr SendMessage(IntPtr hwnd, UInt32 message, IntPtr wParam, IntPtr lParam);
  public static long[] FolderDialogs() {
    var rows = new List<long>();
    EnumWindows((hwnd, state) => {
      var className = new StringBuilder(128);
      var title = new StringBuilder(256);
      GetClassName(hwnd, className, className.Capacity);
      GetWindowText(hwnd, title, title.Capacity);
      if (className.ToString() == "#32770") rows.Add(hwnd.ToInt64());
      return true;
    }, IntPtr.Zero);
    return rows.ToArray();
  }
  public static uint ProcessId(IntPtr hwnd) { uint processId; GetWindowThreadProcessId(hwnd, out processId); return processId; }
}
'@
$preExisting = @{}
foreach ($handle in [MokuFolderSelectNative]::FolderDialogs()) { $preExisting[[string]$handle] = $true }

$requestProcess = Start-Process powershell.exe -ArgumentList @('-NoProfile','-ExecutionPolicy','Bypass','-File',$requestFile) -PassThru -WindowStyle Hidden
$deadline = (Get-Date).AddSeconds($TimeoutSeconds)
$dialog = $null
$ok = $null
while ((Get-Date) -lt $deadline) {
  foreach ($handle in [MokuFolderSelectNative]::FolderDialogs()) {
    if ($preExisting.ContainsKey([string]$handle)) { continue }
    $candidate = [Windows.Automation.AutomationElement]::FromHandle([IntPtr]$handle)
    if (-not $candidate) { continue }
    $okCondition = New-Object Windows.Automation.PropertyCondition([Windows.Automation.AutomationElement]::AutomationIdProperty, '1')
    $candidateOk = $candidate.FindFirst([Windows.Automation.TreeScope]::Descendants, $okCondition)
    if ($candidateOk) { $dialog = $candidate; $ok = $candidateOk; break }
  }
  if ($dialog) { break }
  Start-Sleep -Milliseconds 100
}
if (-not $dialog) {
  Stop-Process -Id $requestProcess.Id -Force -ErrorAction SilentlyContinue
  throw 'folder dialog not found'
}

$dialogTitle = $dialog.Current.Name
$dialogClass = $dialog.Current.ClassName
$dialogPid = $dialog.Current.ProcessId
$buttonName = $ok.Current.Name
[void][MokuFolderSelectNative]::SendMessage([IntPtr]$ok.Current.NativeWindowHandle, 0x00F5, [IntPtr]::Zero, [IntPtr]::Zero)
if (-not $requestProcess.WaitForExit(15000)) {
  Stop-Process -Id $requestProcess.Id -Force -ErrorAction SilentlyContinue
  throw 'request did not finish'
}
if (-not (Test-Path -LiteralPath $responseFile)) { throw 'response file missing' }
$result = Get-Content -LiteralPath $responseFile -Raw -Encoding UTF8 | ConvertFrom-Json
$postHealth = (Invoke-WebRequest -UseBasicParsing -Uri ("http://127.0.0.1:$($runtime.port)/api/health") -TimeoutSec 5).Content | ConvertFrom-Json
[pscustomobject]@{
  DialogTitle = $dialogTitle
  DialogClass = $dialogClass
  DialogPid = $dialogPid
  BackendPid = [int]$runtime.pid
  Button = $buttonName
  Initial = $Initial
  Response = $result
  HealthAfter = [pscustomobject]@{ ok = $postHealth.ok; protocolVersion = $postHealth.protocolVersion; instanceId = $postHealth.instanceId }
} | ConvertTo-Json -Depth 6
