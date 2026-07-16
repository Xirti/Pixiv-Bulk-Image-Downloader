param(
  [string]$Exe = '',
  [int]$TimeoutSeconds = 90
)
$ErrorActionPreference = 'Stop'
$root = Split-Path -Parent (Split-Path -Parent $MyInvocation.MyCommand.Path)
if (-not $Exe) { $Exe = Join-Path $root 'dist\MOKU\MOKU.exe' }
if (-not (Test-Path -LiteralPath $Exe)) { throw "EXE not found: $Exe" }

$probeId = [Guid]::NewGuid().ToString('N')
$probeRoot = Join-Path $env:TEMP ("moku-packaged-e2e-" + $probeId)
$runtimeDir = Join-Path $probeRoot 'runtime'
$descriptorFile = Join-Path $runtimeDir 'backend.json'
New-Item -ItemType Directory -Path $runtimeDir -Force | Out-Null
$dist = Split-Path -Parent $Exe
$tag = 'MOKU-PACKAGED-PROBE-' + $probeId
$downloadDir = Join-Path (Join-Path $dist 'downloads') $tag

$oldRuntime = $env:MOKU_RUNTIME_DIR
$oldMutex = $env:MOKU_MUTEX_NAME
$oldNoBrowser = $env:MOKU_NO_BROWSER
$oldExit = $env:MOKU_TEST_EXIT_AFTER_SECONDS
$oldFixtures = $env:MOKU_ENABLE_TEST_FIXTURES
$process = $null
$requestProcess = $null
try {
  $env:MOKU_RUNTIME_DIR = $runtimeDir
  $env:MOKU_MUTEX_NAME = 'Local\MOKU.PixivTagGallery.PackageE2E.' + $probeId
  $env:MOKU_NO_BROWSER = '1'
  $env:MOKU_TEST_EXIT_AFTER_SECONDS = '120'
$env:MOKU_ENABLE_TEST_FIXTURES = '1'
  $process = Start-Process -FilePath $Exe -ArgumentList '--serve-only' -WorkingDirectory $dist -PassThru

  $deadline = (Get-Date).AddSeconds($TimeoutSeconds)
  $runtime = $null
  $health = $null
  while ((Get-Date) -lt $deadline) {
    if ($process.HasExited) { throw "EXE exited early with code $($process.ExitCode)" }
    if (Test-Path -LiteralPath $descriptorFile) {
      try {
        $runtime = Get-Content -LiteralPath $descriptorFile -Raw -Encoding UTF8 | ConvertFrom-Json
        $base = "http://127.0.0.1:$([int]$runtime.port)"
        $health = Invoke-RestMethod -Uri ($base + '/api/health') -TimeoutSec 3
        if ($health.ok) { break }
      } catch {}
    }
    Start-Sleep -Milliseconds 200
  }
  if (-not $health -or -not $health.ok) { throw 'Packaged backend did not become ready' }

  $page = Invoke-WebRequest -UseBasicParsing -Uri ($base + '/') -TimeoutSec 5
  $style = Invoke-WebRequest -UseBasicParsing -Uri ($base + '/style.css') -TimeoutSec 5
  $script = Invoke-WebRequest -UseBasicParsing -Uri ($base + '/app.js') -TimeoutSec 5
  $origin = $base
  $headers = @{ Origin = $origin; 'X-MOKU-Request-Token' = [string]$health.requestToken }

  Add-Type -AssemblyName UIAutomationClient
  Add-Type -AssemblyName UIAutomationTypes
  Add-Type @'
using System;
using System.Collections.Generic;
using System.Runtime.InteropServices;
using System.Text;
public static class MokuPackagedProbeNative {
  public delegate bool EnumCallback(IntPtr hwnd, IntPtr state);
  [DllImport("user32.dll")] public static extern bool EnumWindows(EnumCallback callback, IntPtr state);
  [DllImport("user32.dll", CharSet=CharSet.Unicode)] public static extern int GetClassName(IntPtr hwnd, StringBuilder text, int count);
  [DllImport("user32.dll")] public static extern IntPtr SendMessage(IntPtr hwnd, UInt32 message, IntPtr wParam, IntPtr lParam);
  public static long[] Dialogs() {
    var rows = new List<long>();
    EnumWindows((hwnd, state) => {
      var className = new StringBuilder(128);
      GetClassName(hwnd, className, className.Capacity);
      if (className.ToString() == "#32770") rows.Add(hwnd.ToInt64());
      return true;
    }, IntPtr.Zero);
    return rows.ToArray();
  }
}
'@
  $before = @{}
  foreach ($handle in [MokuPackagedProbeNative]::Dialogs()) { $before[[string]$handle] = $true }
  $folderResponse = Join-Path $probeRoot 'folder-response.json'
  $folderRequest = Join-Path $probeRoot 'folder-request.ps1'
  $folderBody = (@{ initial = $probeRoot } | ConvertTo-Json -Compress).Replace("'", "''")
  $requestLines = @(
    '$ErrorActionPreference = ''Stop''',
    ('$response = Invoke-WebRequest -UseBasicParsing -Method Post -Uri ''' + $base + '/api/system/select-folder'' -ContentType ''application/json'' -Headers @{ Origin = ''' + $origin + '''; ''X-MOKU-Request-Token'' = ''' + [string]$health.requestToken + ''' } -Body ([Text.Encoding]::UTF8.GetBytes(''' + $folderBody + ''')) -TimeoutSec 90'),
    ('$response.Content | Set-Content -LiteralPath ''' + $folderResponse + ''' -Encoding UTF8')
  )
  [IO.File]::WriteAllLines($folderRequest, $requestLines, (New-Object Text.UTF8Encoding($false)))
  $requestProcess = Start-Process powershell.exe -ArgumentList @('-NoProfile','-ExecutionPolicy','Bypass','-File',$folderRequest) -PassThru -WindowStyle Hidden

  $dialog = $null
  $ok = $null
  $dialogDeadline = (Get-Date).AddSeconds(45)
  while ((Get-Date) -lt $dialogDeadline) {
    foreach ($handle in [MokuPackagedProbeNative]::Dialogs()) {
      if ($before.ContainsKey([string]$handle)) { continue }
      $candidate = [Windows.Automation.AutomationElement]::FromHandle([IntPtr]$handle)
      if (-not $candidate) { continue }
      $condition = New-Object Windows.Automation.PropertyCondition([Windows.Automation.AutomationElement]::AutomationIdProperty, '1')
      $candidateOk = $candidate.FindFirst([Windows.Automation.TreeScope]::Descendants, $condition)
      if ($candidateOk) { $dialog = $candidate; $ok = $candidateOk; break }
    }
    if ($dialog) { break }
    Start-Sleep -Milliseconds 100
  }
  if (-not $dialog) { throw 'Packaged folder dialog not found' }
  $dialogTitle = $dialog.Current.Name
  $dialogClass = $dialog.Current.ClassName
  $dialogPid = $dialog.Current.ProcessId
  $buttonName = $ok.Current.Name
  [void][MokuPackagedProbeNative]::SendMessage([IntPtr]$ok.Current.NativeWindowHandle, 0x00F5, [IntPtr]::Zero, [IntPtr]::Zero)
  if (-not $requestProcess.WaitForExit(15000)) { throw 'Packaged folder request did not finish' }
  $folderResult = Get-Content -LiteralPath $folderResponse -Raw -Encoding UTF8 | ConvertFrom-Json

  $downloadBody = @{ index = 0; pages = 2; quality = 'preview'; format = 'svg'; tag = $tag } | ConvertTo-Json -Compress
  $downloadResult = Invoke-RestMethod -Method Post -Uri ($base + '/api/download') -ContentType 'application/json' -Headers $headers -Body ([Text.Encoding]::UTF8.GetBytes($downloadBody)) -TimeoutSec 30
  $files = @()
  foreach ($rawPath in @($downloadResult.saved)) {
    $file = Get-Item -LiteralPath ([string]$rawPath) -ErrorAction Stop
    $text = [IO.File]::ReadAllText($file.FullName, [Text.Encoding]::UTF8)
    $files += [pscustomobject]@{ path = $file.FullName; bytes = $file.Length; svg = $text.StartsWith('<svg') }
  }
  $healthAfter = Invoke-RestMethod -Uri ($base + '/api/health') -TimeoutSec 5
  [pscustomobject]@{
    exe = $Exe
    exePid = $process.Id
    runtimePid = [int]$runtime.pid
    protocol = [int]$health.protocolVersion
    applicationId = [string]$health.applicationId
    generationMatch = ([string]$runtime.codeGeneration -eq [string]$health.codeGeneration)
    instanceMatch = ([string]$runtime.instanceId -eq [string]$health.instanceId)
    assets = [pscustomobject]@{ html = $page.StatusCode; css = $style.StatusCode; js = $script.StatusCode }
    folder = [pscustomobject]@{ title = $dialogTitle; class = $dialogClass; dialogPid = $dialogPid; button = $buttonName; selected = $folderResult.selected; cancelled = $folderResult.cancelled }
    download = [pscustomobject]@{ count = $files.Count; files = $files }
    healthAfter = [pscustomobject]@{ ok = $healthAfter.ok; protocol = $healthAfter.protocolVersion }
  } | ConvertTo-Json -Depth 8
} finally {
  if ($requestProcess -and -not $requestProcess.HasExited) { Stop-Process -Id $requestProcess.Id -Force -ErrorAction SilentlyContinue }
  if ($process -and -not $process.HasExited) { Stop-Process -Id $process.Id -Force -ErrorAction SilentlyContinue }
  if (Test-Path -LiteralPath $downloadDir) { Remove-Item -LiteralPath $downloadDir -Recurse -Force -ErrorAction SilentlyContinue }
  Remove-Item -LiteralPath $probeRoot -Recurse -Force -ErrorAction SilentlyContinue
  $env:MOKU_RUNTIME_DIR = $oldRuntime
  $env:MOKU_MUTEX_NAME = $oldMutex
  $env:MOKU_NO_BROWSER = $oldNoBrowser
  $env:MOKU_TEST_EXIT_AFTER_SECONDS = $oldExit
  $env:MOKU_ENABLE_TEST_FIXTURES = $oldFixtures
}
