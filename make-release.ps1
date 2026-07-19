param(
  [switch]$SkipBuild
)
$ErrorActionPreference = 'Stop'
$Root = Split-Path -Parent $MyInvocation.MyCommand.Path
Set-Location -LiteralPath $Root

$License = Join-Path $Root 'LICENSE'
if (-not (Test-Path -LiteralPath $License)) {
  throw 'LICENSE is missing. Choose the project license before creating a public release.'
}

if (-not $SkipBuild) {
  & powershell.exe -NoProfile -ExecutionPolicy Bypass -File (Join-Path $Root 'build-portable.ps1')
  if ($LASTEXITCODE -ne 0) { throw 'Portable build failed' }
}

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

$BuildReleaseMutex = [Threading.Mutex]::new($false, 'Local\MOKU.PixivTagGallery.BuildRelease')
$BuildReleaseLockHeld = $false
try {
  try {
    $BuildReleaseLockHeld = $BuildReleaseMutex.WaitOne([TimeSpan]::FromMinutes(10))
  } catch [Threading.AbandonedMutexException] {
    $BuildReleaseLockHeld = $true
  }
  if (-not $BuildReleaseLockHeld) { throw 'Another MOKU build or release is still running' }

$SourceInitial = (& $Python -I -B (Join-Path $Root 'build_manifest.py') 'source').Trim()
if ($LASTEXITCODE -ne 0 -or $SourceInitial -notmatch '^source-sha256:[0-9a-f]{64}$') {
  throw 'Could not fingerprint release inputs'
}

$VersionText = [IO.File]::ReadAllText((Join-Path $Root 'version.py'), [Text.Encoding]::UTF8)
if ($VersionText -notmatch '__version__\s*=\s*"([0-9]+\.[0-9]+\.[0-9]+)"') {
  throw 'Invalid version.py'
}
$Version = $Matches[1]
$SourceAfterMetadata = (& $Python -I -B (Join-Path $Root 'build_manifest.py') 'source').Trim()
if ($LASTEXITCODE -ne 0 -or $SourceAfterMetadata -ne $SourceInitial) {
  throw 'Release inputs changed while metadata was being read'
}

$Dist = Join-Path $Root 'dist\MOKU'
$Exe = Join-Path $Dist 'MOKU.exe'
$Manifest = Join-Path $Dist 'SHA256.txt'
$BuildManifest = Join-Path $Dist 'BUILD_MANIFEST.json'
$Notices = Join-Path $Dist 'THIRD_PARTY_LICENSES.txt'
$DistLicense = Join-Path $Dist 'LICENSE'
foreach ($required in @($Exe, $Manifest, $BuildManifest, $Notices, $DistLicense, (Join-Path $Dist 'PRIVACY.md'))) {
  if (-not (Test-Path -LiteralPath $required)) { throw "Release input missing: $required" }
}
if ((Get-FileHash -LiteralPath $License -Algorithm SHA256).Hash -ne (Get-FileHash -LiteralPath $DistLicense -Algorithm SHA256).Hash) {
  throw 'Project LICENSE changed after the portable build; rebuild first'
}
$ExeHash = (Get-FileHash -LiteralPath $Exe -Algorithm SHA256).Hash
$ExpectedManifest = "$ExeHash  MOKU.exe`r`n"
if ([IO.File]::ReadAllText($Manifest, [Text.Encoding]::UTF8) -ne $ExpectedManifest) {
  throw 'SHA256.txt does not match MOKU.exe'
}
& $Python -I -B (Join-Path $Root 'build_manifest.py') 'verify' $BuildManifest $Exe
if ($LASTEXITCODE -ne 0) { throw 'Build manifest does not match the current source and executable' }
if (Test-Path -LiteralPath (Join-Path $Dist 'logs')) { throw 'Runtime logs remain in dist\MOKU' }
$DownloadFiles = @(Get-ChildItem -LiteralPath (Join-Path $Dist 'downloads') -File -Recurse -ErrorAction SilentlyContinue)
if ($DownloadFiles.Count) { throw 'Downloaded files remain in dist\MOKU\downloads' }

try {
  $env:MOKU_PROBE_EXE = $Exe
  & $Python -B (Join-Path $Root 'tests\packaged_visual_style_probe.py') --exe $Exe
  if ($LASTEXITCODE -ne 0) { throw 'Packaged visual style probe failed' }
  & $Python -B (Join-Path $Root 'tests\final_packaged_search_probe.py')
  if ($LASTEXITCODE -ne 0) { throw 'Packaged search probe failed' }
  & $Python -B (Join-Path $Root 'tests\final_packaged_tag_cache_probe.py')
  if ($LASTEXITCODE -ne 0) { throw 'Packaged tag/cache probe failed' }
} finally {
  Remove-Item Env:MOKU_PROBE_EXE -ErrorAction SilentlyContinue
}
$ProbeLogs = Join-Path $Dist 'logs'
if (Test-Path -LiteralPath $ProbeLogs) { Remove-Item -LiteralPath $ProbeLogs -Recurse -Force }
$DownloadFiles = @(Get-ChildItem -LiteralPath (Join-Path $Dist 'downloads') -File -Recurse -ErrorAction SilentlyContinue)
if ($DownloadFiles.Count) { throw 'Packaged probes left downloaded files in dist\MOKU\downloads' }
& $Python -I -B (Join-Path $Root 'build_manifest.py') 'verify' $BuildManifest $Exe
if ($LASTEXITCODE -ne 0) { throw 'Packaged probes changed the verified distribution' }

$ReleaseRoot = Join-Path $Root "release\v$Version"
if (Test-Path -LiteralPath $ReleaseRoot) {
  throw "Release directory already exists: $ReleaseRoot"
}
$Staging = Join-Path $env:TEMP ("moku-release-" + [Guid]::NewGuid().ToString('N'))
$ReleaseComplete = $false
try {
  New-Item -ItemType Directory -Path $Staging -Force | Out-Null
  Copy-Item -LiteralPath $Dist -Destination (Join-Path $Staging 'MOKU') -Recurse
  & $Python -I -B (Join-Path $Root 'build_manifest.py') 'verify' (Join-Path $Staging 'MOKU\BUILD_MANIFEST.json') (Join-Path $Staging 'MOKU\MOKU.exe')
  if ($LASTEXITCODE -ne 0) { throw 'Staged distribution failed build-manifest verification' }
  $ArchiveName = "MOKU-v$Version-windows-x64.zip"
  $TemporaryArchive = Join-Path $Staging $ArchiveName
  Compress-Archive -LiteralPath (Join-Path $Staging 'MOKU') -DestinationPath $TemporaryArchive -CompressionLevel Optimal
  $Expanded = Join-Path $Staging 'expanded'
  Expand-Archive -LiteralPath $TemporaryArchive -DestinationPath $Expanded
  & $Python -I -B (Join-Path $Root 'build_manifest.py') 'verify' (Join-Path $Expanded 'MOKU\BUILD_MANIFEST.json') (Join-Path $Expanded 'MOKU\MOKU.exe')
  if ($LASTEXITCODE -ne 0) { throw 'Release archive failed build-manifest verification' }
  $ArchiveHash = (Get-FileHash -LiteralPath $TemporaryArchive -Algorithm SHA256).Hash
  New-Item -ItemType Directory -Path $ReleaseRoot -Force | Out-Null
  $Archive = Join-Path $ReleaseRoot $ArchiveName
  Move-Item -LiteralPath $TemporaryArchive -Destination $Archive
  $FinalArchiveHash = (Get-FileHash -LiteralPath $Archive -Algorithm SHA256).Hash
  if ($FinalArchiveHash -ne $ArchiveHash) { throw 'Release archive hash changed while moving to the release directory' }
  $Sums = "$FinalArchiveHash  $ArchiveName`r`n$ExeHash  MOKU/MOKU.exe`r`n"
  [IO.File]::WriteAllText((Join-Path $ReleaseRoot 'SHA256SUMS.txt'), $Sums, [Text.UTF8Encoding]::new($false))
  Copy-Item -LiteralPath (Join-Path $Root 'CHANGELOG.md') -Destination (Join-Path $ReleaseRoot 'RELEASE_NOTES.md')
  $SourceFinal = (& $Python -I -B (Join-Path $Root 'build_manifest.py') 'source').Trim()
  if ($LASTEXITCODE -ne 0 -or $SourceFinal -ne $SourceInitial) {
    throw 'Release inputs changed while the archive was being created'
  }
  $ReleaseComplete = $true
  Write-Host "Release assets verified: $ReleaseRoot"
  Write-Host "Archive SHA256: $FinalArchiveHash"
} finally {
  Remove-Item -LiteralPath $Staging -Recurse -Force -ErrorAction SilentlyContinue
  if (-not $ReleaseComplete -and (Test-Path -LiteralPath $ReleaseRoot)) {
    Remove-Item -LiteralPath $ReleaseRoot -Recurse -Force
  }
}
} finally {
  if ($BuildReleaseLockHeld) { $BuildReleaseMutex.ReleaseMutex() }
  $BuildReleaseMutex.Dispose()
}
