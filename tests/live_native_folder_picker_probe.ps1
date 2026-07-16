param(
  [int]$TimeoutSeconds = 30,
  [string]$Initial = ''
)
$ErrorActionPreference = 'Stop'
$runtimeFile = Join-Path $env:LocalAppData 'MOKU\runtime\backend.json'
$runtime = Get-Content -LiteralPath $runtimeFile -Raw -Encoding UTF8 | ConvertFrom-Json
$responseFile = Join-Path $env:TEMP 'moku-folder-probe-response.json'
Remove-Item -LiteralPath $responseFile -Force -ErrorAction SilentlyContinue
$requestScript = Join-Path $env:TEMP 'moku-folder-probe-request.ps1'
$uri = "http://127.0.0.1:$($runtime.port)/api/system/select-folder"
$origin = "http://127.0.0.1:$($runtime.port)"
$health = (Invoke-WebRequest -UseBasicParsing -Uri ("http://127.0.0.1:$($runtime.port)/api/health") -TimeoutSec 5).Content | ConvertFrom-Json
$requestToken = [string]$health.requestToken
$bodyObject = @{ initial = $Initial }
$body = $bodyObject | ConvertTo-Json -Compress
$scriptLines = @(
  '$ErrorActionPreference = ''Stop''',
  ('$uri = ''' + $uri + ''''),
  ('$origin = ''' + $origin + ''''),
  ('$responseFile = ''' + $responseFile + ''''),
  ('$body = ''' + $body.Replace("'", "''") + ''''),
  'try {',
  '  $response = Invoke-WebRequest -UseBasicParsing -Method Post -Uri $uri -ContentType ''application/json'' -Headers @{ Origin = $origin; ''X-MOKU-Request-Token'' = ''' + $requestToken + ''' } -Body ([Text.Encoding]::UTF8.GetBytes($body)) -TimeoutSec 360',
  '  [pscustomobject]@{ ok = $true; status = $response.StatusCode; body = $response.Content } | ConvertTo-Json -Compress | Set-Content -LiteralPath $responseFile -Encoding UTF8',
  '} catch {',
  '  [pscustomobject]@{ ok = $false; error = $_.Exception.Message } | ConvertTo-Json -Compress | Set-Content -LiteralPath $responseFile -Encoding UTF8',
  '}'
)
[IO.File]::WriteAllLines($requestScript, $scriptLines, [Text.UTF8Encoding]::new($false))
$requestProcess = Start-Process -FilePath 'powershell.exe' -ArgumentList @('-NoProfile','-ExecutionPolicy','Bypass','-File',$requestScript) -PassThru -WindowStyle Hidden
Add-Type -AssemblyName UIAutomationClient
Add-Type -AssemblyName UIAutomationTypes
Add-Type @'
using System;
using System.Runtime.InteropServices;
public static class MokuFolderProbeNative {
  [DllImport("user32.dll")] public static extern bool PostMessage(IntPtr hWnd, UInt32 msg, IntPtr wp, IntPtr lp);
}
'@
$root = [System.Windows.Automation.AutomationElement]::RootElement
$deadline = (Get-Date).AddSeconds($TimeoutSeconds)
$dialog = $null
while ((Get-Date) -lt $deadline) {
  $all = $root.FindAll([System.Windows.Automation.TreeScope]::Children,[System.Windows.Automation.Condition]::TrueCondition)
  for ($i = 0; $i -lt $all.Count; $i++) {
    $candidate = $all.Item($i)
    $windowName = [string]$candidate.Current.Name
    $isFolderDialog = $windowName.Contains('MOKU')
    if ($candidate.Current.ClassName -eq '#32770') { $isFolderDialog = $true }
    if ($isFolderDialog) {
      $dialog = $candidate
      break
    }
  }
  if ($dialog) { break }
  Start-Sleep -Milliseconds 100
}
if (-not $dialog) {
  Stop-Process -Id $requestProcess.Id -Force -ErrorAction SilentlyContinue
  throw 'FolderBrowserDialog not found'
}
$evidence = [ordered]@{
  title = $dialog.Current.Name
  className = $dialog.Current.ClassName
  processId = $dialog.Current.ProcessId
  nativeWindowHandle = $dialog.Current.NativeWindowHandle
}
[void][MokuFolderProbeNative]::PostMessage([IntPtr]$dialog.Current.NativeWindowHandle,0x0010,[IntPtr]::Zero,[IntPtr]::Zero)
if (-not $requestProcess.WaitForExit(15000)) { Stop-Process -Id $requestProcess.Id -Force -ErrorAction SilentlyContinue }
$response = if (Test-Path -LiteralPath $responseFile) { Get-Content -LiteralPath $responseFile -Raw -Encoding UTF8 | ConvertFrom-Json } else { $null }
[pscustomobject]@{ dialog = $evidence; response = $response } | ConvertTo-Json -Depth 6
