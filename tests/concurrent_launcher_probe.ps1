param(
  [int]$Count = 8,
  [string]$Project = ''
)
$ErrorActionPreference = 'Stop'
if (-not $Project) { $Project = Split-Path -Parent (Split-Path -Parent $MyInvocation.MyCommand.Path) }
$launcher = Join-Path $Project 'launch-moku.ps1'
$log = Join-Path $Project 'logs\launcher.log'
$runtimeFile = Join-Path $env:LocalAppData 'MOKU\runtime\backend.json'
$before = @([IO.File]::ReadAllLines($log)).Count
$workers = @()
for ($i = 0; $i -lt $Count; $i++) {
  $stdout = Join-Path $env:TEMP ("moku-concurrent-$i.out")
  $stderr = Join-Path $env:TEMP ("moku-concurrent-$i.err")
  Remove-Item -LiteralPath $stdout,$stderr -Force -ErrorAction SilentlyContinue
  $workers += Start-Process -FilePath 'powershell.exe' -ArgumentList @('-NoProfile','-ExecutionPolicy','Bypass','-File',$launcher,'-Mode','Cancel') -PassThru -WindowStyle Hidden -RedirectStandardOutput $stdout -RedirectStandardError $stderr
}
foreach ($worker in $workers) {
  if (-not $worker.WaitForExit(90000)) { Stop-Process -Id $worker.Id -Force; throw "worker timeout $($worker.Id)" }
  $worker.Refresh()
}
$lines = [IO.File]::ReadAllLines($log)
$newLines = @($lines | Select-Object -Skip $before)
$runtime = Get-Content -LiteralPath $runtimeFile -Raw -Encoding UTF8 | ConvertFrom-Json
$health = (Invoke-WebRequest -UseBasicParsing -Uri ("http://127.0.0.1:$($runtime.port)/api/health") -Headers @{ 'Sec-Fetch-Site' = 'same-origin' } -TimeoutSec 5).Content | ConvertFrom-Json
$listeners = @(Get-NetTCPConnection -State Listen -ErrorAction SilentlyContinue | Where-Object { $_.LocalAddress -eq '127.0.0.1' -and $_.LocalPort -ge 48721 -and $_.LocalPort -le 48730 })
[pscustomobject]@{
  workers = $workers.Count
  exitCodes = @($workers | ForEach-Object { $_.ExitCode })
  starts = @($newLines | Where-Object { $_ -match ' start pid=' }).Count
  reuses = @($newLines | Where-Object { $_ -match ' reuse pid=' }).Count
  cancels = @($newLines | Where-Object { $_ -match ' mode=Cancel' }).Count
  listenerCount = $listeners.Count
  ports = @($listeners.LocalPort)
  pids = @($listeners.OwningProcess)
  runtimePid = $runtime.pid
  runtimeInstance = $runtime.instanceId
  healthInstance = $health.instanceId
  healthOk = $health.ok
  log = $newLines
} | ConvertTo-Json -Depth 5
