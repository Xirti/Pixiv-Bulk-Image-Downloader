# Build and smoke-test the current WebView2 desktop architecture as a portable Windows folder.
param(
  # Only for rebuilding after the application tests have already been verified.
  [switch]$SkipTests
)
$ErrorActionPreference = 'Stop'
$Root = Split-Path -Parent $MyInvocation.MyCommand.Path
Set-Location -LiteralPath $Root

$SecurityModule = Join-Path $PSHOME 'Modules\Microsoft.PowerShell.Security\Microsoft.PowerShell.Security.psd1'
$env:PSModulePath = (Join-Path $PSHOME 'Modules') + [IO.Path]::PathSeparator + $env:PSModulePath
Import-Module -Name $SecurityModule -ErrorAction Stop
$PythonCommand = Get-Command python.exe -CommandType Application -ErrorAction Stop | Select-Object -First 1
$Python = [IO.Path]::GetFullPath([string]$PythonCommand.Source)
$PythonSignature = Get-AuthenticodeSignature -LiteralPath $Python
if ($PythonSignature.Status -ne 'Valid' -or -not $PythonSignature.SignerCertificate -or $PythonSignature.SignerCertificate.Subject -notmatch 'Python Software Foundation') {
  throw 'A Python Software Foundation signed python.exe is required'
}
$PythonIdentityRaw = & $Python -I -B -c "import json,sys;print(json.dumps({'implementation':sys.implementation.name,'major':sys.version_info.major,'minor':sys.version_info.minor,'executable':sys.executable}))"
if ($LASTEXITCODE -ne 0) { throw 'Could not verify the Python interpreter' }
$PythonIdentity = $PythonIdentityRaw | ConvertFrom-Json
if ([string]$PythonIdentity.implementation -ne 'cpython' -or [int]$PythonIdentity.major -ne 3 -or [int]$PythonIdentity.minor -ne 12) {
  throw 'CPython 3.12 is required'
}
$ReportedPython = [IO.Path]::GetFullPath([string]$PythonIdentity.executable)
if (-not [string]::Equals($Python, $ReportedPython, [StringComparison]::OrdinalIgnoreCase)) {
  throw 'Python executable identity mismatch'
}

$SourceBefore = (& $Python -I -B (Join-Path $Root 'build_manifest.py') 'source').Trim()
if ($LASTEXITCODE -ne 0 -or $SourceBefore -notmatch '^source-sha256:[0-9a-f]{64}$') {
  throw 'Could not fingerprint build inputs'
}
if (-not $SkipTests) {
  & $Python -I -B (Join-Path $Root 'run_tests.py') --app-only
  if ($LASTEXITCODE -ne 0) { throw 'Unit tests failed' }
}
foreach ($script in @('app.js', 'ugoira-preview.js', 'theme.js', 'selection-store.js', 'artwork-detail-view.js')) {
  node --check (Join-Path (Join-Path $Root 'web') $script)
  if ($LASTEXITCODE -ne 0) { throw "JavaScript syntax check failed: $script" }
}

$BuildReleaseMutex = [Threading.Mutex]::new($false, 'Local\MOKU.PixivTagGallery.BuildRelease')
$BuildReleaseLockHeld = $false
try {
  try {
    $BuildReleaseLockHeld = $BuildReleaseMutex.WaitOne([TimeSpan]::FromMinutes(10))
  } catch [Threading.AbandonedMutexException] {
    $BuildReleaseLockHeld = $true
  }
  if (-not $BuildReleaseLockHeld) { throw 'Another MOKU build or release is still running' }

$SourceLocked = (& $Python -I -B (Join-Path $Root 'build_manifest.py') 'source').Trim()
if ($LASTEXITCODE -ne 0 -or $SourceLocked -ne $SourceBefore) {
  throw 'Build inputs changed while tests were running'
}
& $Python -I -B -m PyInstaller --noconfirm --clean MOKU.spec
if ($LASTEXITCODE -ne 0) { throw 'PyInstaller build failed' }
$SourceAfter = (& $Python -I -B (Join-Path $Root 'build_manifest.py') 'source').Trim()
if ($LASTEXITCODE -ne 0 -or $SourceAfter -ne $SourceBefore) {
  throw 'Build inputs changed while the portable build was running'
}

$Dist = Join-Path $Root 'dist\MOKU'
$Exe = Join-Path $Dist 'MOKU.exe'
if (-not (Test-Path -LiteralPath $Exe)) { throw "Built executable not found: $Exe" }
$VersionSource = [IO.File]::ReadAllText((Join-Path $Root 'version.py'), [Text.Encoding]::UTF8)
if ($VersionSource -notmatch '__version__\s*=\s*"([0-9]+\.[0-9]+\.[0-9]+)"') { throw 'Invalid version.py' }
$Version = $Matches[1]

$Readme = @'
MOKU Lite __MOKU_VERSION__

完整解压后双击 MOKU.exe 即可使用，无需安装 Python。
程序文件请放在同一文件夹，MOKU.exe.config 也一起保留。

运行环境：Windows 10/11 x64、Microsoft Edge WebView2 Runtime。
网络需要能访问 www.pixiv.net 和 i.pximg.net；可使用 Windows 已开启的本地 HTTP 代理或 TUN。

搜图小提示
- 多标签用 ; 或 ； 分隔，结果同时包含全部标签。
- pid:作品ID 查作品，uid:画师ID 查画师，author:画师名 按名字查找。
- 自定义日期的开始和结束设成同一天，就能查询当天发布的作品。
- 采集篮支持任意数量的作品，最多选择 1,000 张图片。
- 最近 8 次搜索可一键恢复标签和筛选条件。

左侧图标栏切换页面，右上角切换日夜主题。
输入框右键可剪切、复制、粘贴和全选，普通文字也可右键复制。
下载历史里可以查看保存路径、打开文件夹、复制路径或删除记录，图片保留在原文件夹。
采集篮自动保存，重开后保留勾选。未完成的下载可在下载页点击「继续」。
默认跳过已下载且仍在原位置的图片，也可取消该选项重新下载。

下载默认保留原图。动图可选原始帧 ZIP + JSON、GIF 或 MP4：
GIF 内置转换；MP4 需要本机安装支持 libx264 的 FFmpeg 并加入 PATH，安装后重启 MOKU。

登录 Pixiv 后可查看账号权限范围内的作品。
搜索或图片加载不顺畅时，可打开右上角「使用说明」里的网络检查。

项目与更新：https://github.com/Xirti/Pixiv-Bulk-Image-Downloader
'@
$Readme = $Readme.Replace('__MOKU_VERSION__', $Version)
[IO.File]::WriteAllText((Join-Path $Dist 'README.txt'), $Readme, [Text.UTF8Encoding]::new($false))
Copy-Item -LiteralPath (Join-Path $Root 'MOKU.exe.config') -Destination (Join-Path $Dist 'MOKU.exe.config') -Force
Copy-Item -LiteralPath (Join-Path $Root 'PRIVACY.md') -Destination (Join-Path $Dist 'PRIVACY.md') -Force
Copy-Item -LiteralPath (Join-Path $Root 'SECURITY.md') -Destination (Join-Path $Dist 'SECURITY.md') -Force
Copy-Item -LiteralPath (Join-Path $Root 'THIRD_PARTY_NOTICES.md') -Destination (Join-Path $Dist 'THIRD_PARTY_NOTICES.md') -Force
if (Test-Path -LiteralPath (Join-Path $Root 'LICENSE')) {
  Copy-Item -LiteralPath (Join-Path $Root 'LICENSE') -Destination (Join-Path $Dist 'LICENSE') -Force
}
& $Python -I -B (Join-Path $Root 'generate-third-party-licenses.py') (Join-Path $Dist 'THIRD_PARTY_LICENSES.txt')
if ($LASTEXITCODE -ne 0) { throw 'Third-party license generation failed' }

$SmokeRoot = Join-Path $env:TEMP ("moku-build-smoke-" + [Guid]::NewGuid().ToString('N'))
$Runtime = Join-Path $SmokeRoot 'runtime'
New-Item -ItemType Directory -Path $Runtime -Force | Out-Null
$oldRuntime = $env:MOKU_RUNTIME_DIR
$oldMutex = $env:MOKU_MUTEX_NAME
$oldNoBrowser = $env:MOKU_NO_BROWSER
$oldExit = $env:MOKU_TEST_EXIT_AFTER_SECONDS
$oldGeneration = $env:MOKU_CODE_GENERATION
$oldFixtures = $env:MOKU_ENABLE_TEST_FIXTURES
try {
  $env:MOKU_CODE_GENERATION = $null
  $env:MOKU_ENABLE_TEST_FIXTURES = $null
  $env:MOKU_RUNTIME_DIR = $Runtime
  $env:MOKU_MUTEX_NAME = 'Local\MOKU.PixivTagGallery.BuildSmoke.' + [Guid]::NewGuid().ToString('N')
  $env:MOKU_NO_BROWSER = '1'
  $env:MOKU_TEST_EXIT_AFTER_SECONDS = '15'
  $process = Start-Process -FilePath $Exe -ArgumentList '--serve-only' -WorkingDirectory $Dist -WindowStyle Hidden -PassThru
  $descriptorFile = Join-Path $Runtime 'backend.json'
  $deadline = (Get-Date).AddSeconds(30)
  $ready = $false
  while ((Get-Date) -lt $deadline) {
    if ($process.HasExited) { break }
    if (Test-Path -LiteralPath $descriptorFile) {
      try {
        $runtimeData = Get-Content -LiteralPath $descriptorFile -Raw -Encoding UTF8 | ConvertFrom-Json
        $base = "http://127.0.0.1:$([int]$runtimeData.port)/"
        $healthResponse = Invoke-WebRequest -UseBasicParsing -Uri ($base + 'api/health') -Headers @{ 'Sec-Fetch-Site' = 'same-origin' } -TimeoutSec 3
        $health = $healthResponse.Content | ConvertFrom-Json
        $page = Invoke-WebRequest -UseBasicParsing -Uri $base -TimeoutSec 3
        $style = Invoke-WebRequest -UseBasicParsing -Uri ($base + 'style.css') -TimeoutSec 3
        $script = Invoke-WebRequest -UseBasicParsing -Uri ($base + 'app.js') -TimeoutSec 3
        $previewScript = Invoke-WebRequest -UseBasicParsing -Uri ($base + 'ugoira-preview.js') -TimeoutSec 3
        if ($healthResponse.StatusCode -eq 200 `
          -and $page.StatusCode -eq 200 `
          -and $style.StatusCode -eq 200 `
          -and $script.StatusCode -eq 200 `
          -and $previewScript.StatusCode -eq 200 `
          -and $previewScript.Content.Contains('globalThis.createUgoiraPreview') `
          -and [int]$runtimeData.protocolVersion -eq 5 `
          -and [string]$health.version -eq $Version `
          -and [string]$runtimeData.applicationId -eq 'MOKU.PixivTagGallery' `
          -and [string]$health.codeGeneration -match '^exe-sha256:[0-9a-f]{64}$' `
          -and [string]$runtimeData.applicationId -eq [string]$health.applicationId `
          -and [string]$runtimeData.codeGeneration -eq [string]$health.codeGeneration `
          -and [string]$runtimeData.instanceId -eq [string]$health.instanceId `
          -and [string]$health.requestToken `
          -and $page.Content.Contains('id="helpBtn"') `
          -and $page.Content.Contains('id="helpDialog"') `
          -and $page.Content.Contains('id="networkCheck"') `
          -and $script.Content.Contains('/api/network/diagnose')) {
          $ready = $true
          break
        }
      } catch {}
    }
    Start-Sleep -Milliseconds 200
  }
  if (-not $ready) { throw "Built EXE smoke test failed. See $Dist\logs\moku-app.log" }
} finally {
  if ($process -and -not $process.HasExited) { Stop-Process -Id $process.Id -Force -ErrorAction SilentlyContinue }
  $env:MOKU_RUNTIME_DIR = $oldRuntime
  $env:MOKU_MUTEX_NAME = $oldMutex
  $env:MOKU_NO_BROWSER = $oldNoBrowser
  $env:MOKU_TEST_EXIT_AFTER_SECONDS = $oldExit
  $env:MOKU_CODE_GENERATION = $oldGeneration
  $env:MOKU_ENABLE_TEST_FIXTURES = $oldFixtures
  Remove-Item -LiteralPath $SmokeRoot -Recurse -Force -ErrorAction SilentlyContinue
}

Remove-Item -LiteralPath (Join-Path $Dist 'logs') -Recurse -Force -ErrorAction SilentlyContinue
$Hash = (Get-FileHash -LiteralPath $Exe -Algorithm SHA256).Hash
[IO.File]::WriteAllText((Join-Path $Dist 'SHA256.txt'), ("$Hash  MOKU.exe`r`n"), [Text.UTF8Encoding]::new($false))
& $Python -I -B (Join-Path $Root 'build_manifest.py') 'write' (Join-Path $Dist 'BUILD_MANIFEST.json') $Exe --expected-source-generation $SourceBefore
if ($LASTEXITCODE -ne 0) { throw 'Build manifest generation failed' }
& $Python -B (Join-Path $Root 'tests\packaged_startup_probe.py') --exe $Exe --mark-of-web
if ($LASTEXITCODE -ne 0) { throw 'Built EXE failed native startup with download markers' }
Write-Host "Build verified: $Exe"
Write-Host "Version: $Version"
Write-Host "SHA256: $Hash"
} finally {
  if ($BuildReleaseLockHeld) { $BuildReleaseMutex.ReleaseMutex() }
  $BuildReleaseMutex.Dispose()
}
