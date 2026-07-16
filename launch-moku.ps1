# MOKU preload launcher: backend first, then choose a window mode.
param(
  [ValidateSet('Prompt','Desktop','Browser','Cancel')]
  [string]$Mode = 'Prompt'
)
$ErrorActionPreference = 'Stop'
$protocolVersion = 5
$applicationId = 'MOKU.PixivTagGallery'
$root = Split-Path -Parent $MyInvocation.MyCommand.Path
$logDir = Join-Path $root 'logs'; $log = Join-Path $logDir 'launcher.log'
New-Item -ItemType Directory -Path $logDir -Force | Out-Null
function Log([string]$message) { Add-Content -LiteralPath $log -Value "$(Get-Date -Format 'yyyy-MM-dd HH:mm:ss') $message" -Encoding UTF8 }

$generationFiles = @(
  'server.py', 'auth_store.py', 'fixture_gallery.py', 'folder_picker.py',
  'pixiv_login.py', 'moku_app.py', 'desktop_client.py',
  'network_config.py', 'pixiv_adapter.py', 'search_service.py', 'version.py',
  'web\index.html', 'web\app.js', 'web\style.css'
) | ForEach-Object { Join-Path $root $_ }
$missingGenerationFile = $generationFiles | Where-Object { -not (Test-Path -LiteralPath $_) } | Select-Object -First 1
if ($missingGenerationFile) { throw "MOKU generation input missing: $missingGenerationFile" }
$codeGeneration = (($generationFiles | ForEach-Object { (Get-FileHash -LiteralPath $_ -Algorithm SHA256).Hash }) -join ':').ToLowerInvariant()

$python = Join-Path $env:LocalAppData 'Programs\Python\Python312\python.exe'
if (-not (Test-Path -LiteralPath $python)) { throw "Python not found: $python" }

$runtimeDir = Join-Path $env:LocalAppData 'MOKU/runtime'
$runtimeFile = Join-Path $runtimeDir 'backend.json'
New-Item -ItemType Directory -Path $runtimeDir -Force | Out-Null
$mutex = New-Object System.Threading.Mutex($false, 'Local\MOKU.PixivTagGallery.Backend.v1')
$mutexHeld = $false
try {
  $mutexHeld = $mutex.WaitOne([TimeSpan]::FromSeconds(30))
  if (-not $mutexHeld) { throw 'MOKU backend launch lock timeout' }

$url = $null; $instanceId = $null
if (Test-Path -LiteralPath $runtimeFile) {
  try {
    $runtime = Get-Content -LiteralPath $runtimeFile -Raw -Encoding UTF8 | ConvertFrom-Json
    if ([int]$runtime.protocolVersion -eq $protocolVersion -and [string]$runtime.applicationId -eq $applicationId -and [string]$runtime.codeGeneration -eq $codeGeneration -and [int]$runtime.port -ge 1 -and [int]$runtime.port -le 65535 -and [string]$runtime.instanceId) {
      $candidateUrl = "http://127.0.0.1:$([int]$runtime.port)/"
      $candidate = Invoke-WebRequest -UseBasicParsing -Uri ($candidateUrl+'api/health') -TimeoutSec 3
      $candidateHealth = $candidate.Content | ConvertFrom-Json
      if ($candidate.StatusCode -eq 200 -and [int]$candidateHealth.protocolVersion -eq $protocolVersion -and [string]$candidateHealth.applicationId -eq $applicationId -and [string]$candidateHealth.codeGeneration -eq $codeGeneration -and [string]$candidateHealth.instanceId -eq [string]$runtime.instanceId -and [string]$candidateHealth.requestToken) {
        $url = $candidateUrl; $instanceId = [string]$runtime.instanceId; Log "reuse pid=$([int]$runtime.pid) url=$url instance=$instanceId"
      }
    }
  } catch { Log "runtime descriptor stale: $($_.Exception.Message)" }
}
if (-not $url) {
  $listener = [Net.Sockets.TcpListener]::new([Net.IPAddress]::Loopback, 0)
  try {
    $listener.Start()
    $port = [int]$listener.LocalEndpoint.Port
  } finally {
    $listener.Stop()
  }
  $instanceId=[Guid]::NewGuid().ToString('N'); $env:PORT=[string]$port; $env:MOKU_INSTANCE_ID=$instanceId; $env:MOKU_CODE_GENERATION=$codeGeneration
  $stdout=Join-Path $logDir ("server-$instanceId.stdout.log"); $stderr=Join-Path $logDir ("server-$instanceId.stderr.log")
  $process=Start-Process -FilePath $python -ArgumentList 'server.py' -WorkingDirectory $root -PassThru -WindowStyle Hidden -RedirectStandardOutput $stdout -RedirectStandardError $stderr
  $url="http://127.0.0.1:$port/"; Log "start pid=$($process.Id) url=$url instance=$instanceId"
}

$health = $null; $page = $null; $style = $null; $script = $null; $ready = $false
$deadline=(Get-Date).AddSeconds(60)
while((Get-Date)-lt $deadline){
  try {
    $health=Invoke-WebRequest -UseBasicParsing -Uri ($url.TrimEnd('/')+'/api/health') -TimeoutSec 3
    $page=Invoke-WebRequest -UseBasicParsing -Uri $url -TimeoutSec 3
    $style=Invoke-WebRequest -UseBasicParsing -Uri ($url+'style.css') -TimeoutSec 3
    $script=Invoke-WebRequest -UseBasicParsing -Uri ($url+'app.js') -TimeoutSec 3
    if($health.StatusCode-eq 200-and$page.StatusCode-eq 200-and$style.StatusCode-eq 200-and$script.StatusCode-eq 200){
      $healthData=$health.Content|ConvertFrom-Json
      if([int]$healthData.protocolVersion-eq$protocolVersion-and[string]$healthData.applicationId-eq$applicationId-and[string]$healthData.codeGeneration-eq$codeGeneration-and[string]$healthData.instanceId-eq$instanceId-and[string]$healthData.requestToken){$ready=$true;break}
    }
  } catch {}
  if ($process -and $process.HasExited) { break }
  Start-Sleep -Milliseconds 300
}
if(-not $ready){
  if ($process -and -not $process.HasExited) { Stop-Process -Id $process.Id -Force -ErrorAction SilentlyContinue }
  throw "MOKU preload timeout or instance mismatch. See $log"
}
  if ($process) {
    $descriptor = [ordered]@{ protocolVersion=$protocolVersion; applicationId=$applicationId; codeGeneration=$codeGeneration; instanceId=$instanceId; pid=$process.Id; port=$port; startedAt=(Get-Date).ToUniversalTime().ToString('o') }
    $tempRuntime = $runtimeFile + '.tmp'
    $descriptor | ConvertTo-Json -Compress | Set-Content -LiteralPath $tempRuntime -Encoding UTF8
    Move-Item -LiteralPath $tempRuntime -Destination $runtimeFile -Force
  }
  Log "preloaded health+html+css+js url=$url instance=$instanceId"
} finally {
  if ($mutexHeld) { $mutex.ReleaseMutex() }
  $mutex.Dispose()
}

$result = $null
if ($Mode -eq 'Prompt') {
  Add-Type -AssemblyName System.Windows.Forms
  $messageBytes = [Convert]::FromBase64String('TQBPAEsAVQAgAPJdjFtoUYSYoFJ9jwIwDQAKAA0ACgAvZhr/TGhil+967HLLepd641MNAAoAJlQa/1F/dZjveg0ACgDWU4htGv/dTwFjDlTvetCPTIg=')
  $message = [Text.Encoding]::Unicode.GetString($messageBytes)
  $result=[System.Windows.Forms.MessageBox]::Show($message,'MOKU Launcher',[System.Windows.Forms.MessageBoxButtons]::YesNoCancel,[System.Windows.Forms.MessageBoxIcon]::Information)
}
$desktopChosen = $Mode -eq 'Desktop' -or ($Mode -eq 'Prompt' -and [string]$result -eq 'Yes')
$browserChosen = $Mode -eq 'Browser' -or ($Mode -eq 'Prompt' -and [string]$result -eq 'No')
if($desktopChosen){
  Log 'mode=Desktop webview2'
  $env:MOKU_CODE_GENERATION=$codeGeneration
  $desktop = Start-Process -FilePath $python -ArgumentList 'moku_app.py' -WorkingDirectory $root -PassThru -WindowStyle Hidden
  Log "desktop pid=$($desktop.Id)"}elseif($browserChosen){
  Log 'mode=Browser'; Start-Process $url
}else{Log 'mode=Cancel'}
