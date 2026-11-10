param(
  [switch]$SkipBuild,
  [switch]$Development,
  [string]$ReleaseKey = $env:MOKU_RELEASE_KEY,
  [string]$CodeSignThumbprint = $env:MOKU_CODESIGN_THUMBPRINT,
  [uri]$TimestampUrl
)
$ErrorActionPreference = 'Stop'
$Root = Split-Path -Parent $MyInvocation.MyCommand.Path
Set-Location -LiteralPath $Root
if (-not $Development) {
  if (-not $ReleaseKey) { throw 'Release requires a signing key; unsigned CI artifacts must use -Development.' }
  python -B sign-release.py check-key --key $ReleaseKey
  if ($LASTEXITCODE -ne 0) { throw 'Release signing key preflight failed' }
}
if (-not $SkipBuild) {
  & (Join-Path $Root 'build-portable.ps1') -Development:$Development -ReleaseKey $ReleaseKey -CodeSignThumbprint $CodeSignThumbprint -TimestampUrl $TimestampUrl
  if ($LASTEXITCODE -ne 0) { throw 'Build failed' }
}
$Tag = (python -B -c 'from edition_profile import RELEASE_TAG; print(RELEASE_TAG)').Trim()
if ($LASTEXITCODE -ne 0 -or $Tag -notmatch '^v\d+\.\d+\.\d+(?:-(?:alpha|beta|rc)\.\d+)?$') { throw 'Invalid Release tag' }
$Dist = Join-Path $Root 'dist\MOKU'
$Exe = Join-Path $Dist 'MOKU.exe'
python -B build_manifest.py verify (Join-Path $Dist 'BUILD_MANIFEST.json') $Exe
if ($LASTEXITCODE -ne 0) { throw 'Manifest verification failed' }
if (-not $Development) {
  python -B sign-release.py verify-bundle --root $Dist --version $Tag.Substring(1)
  if ($LASTEXITCODE -ne 0) { throw 'Signed bundle verification failed' }
}
foreach ($Name in @('logs','downloads','runtime','test-builds','__pycache__','.git')) {
  if (Test-Path -LiteralPath (Join-Path $Dist $Name)) { throw "Runtime or development files remain: $Name" }
}
$ReleaseRoot = Join-Path $Root $(if ($Development) { "release\development-$Tag" } else { "release\$Tag" })
if (Test-Path -LiteralPath $ReleaseRoot) { throw "Release already exists: $ReleaseRoot" }
New-Item -ItemType Directory -Path $ReleaseRoot | Out-Null
$ArchiveName = "MOKU-$Tag-windows-x64.zip"
$Archive = Join-Path $ReleaseRoot $ArchiveName
Compress-Archive -LiteralPath $Dist -DestinationPath $Archive -CompressionLevel Optimal
python -B -c 'import sys,zipfile; z=zipfile.ZipFile(sys.argv[1]); assert z.testzip() is None' $Archive
if ($LASTEXITCODE -ne 0) { throw 'Archive verification failed' }
$ArchiveHash = (Get-FileHash -LiteralPath $Archive -Algorithm SHA256).Hash
$ExeHash = (Get-FileHash -LiteralPath $Exe -Algorithm SHA256).Hash
[IO.File]::WriteAllText((Join-Path $ReleaseRoot 'SHA256SUMS.txt'), "$ArchiveHash  $ArchiveName`n$ExeHash  MOKU/MOKU.exe`n", [Text.UTF8Encoding]::new($false))
Copy-Item -LiteralPath (Join-Path $Root 'RELEASE_NOTES.md') -Destination $ReleaseRoot
Copy-Item -LiteralPath (Join-Path $Dist 'BUILD_MANIFEST.json') -Destination $ReleaseRoot
if (-not $Development) {
  python -B sign-release.py sign-release --root $ReleaseRoot --version $Tag.Substring(1) --key $ReleaseKey
  if ($LASTEXITCODE -ne 0) { throw 'Release metadata signing failed; do not publish this incomplete release folder.' }
  $PublicKeys = (python -B -c 'from release_trust import TRUSTED_RELEASE_KEYS; print("\n".join(sorted(TRUSTED_RELEASE_KEYS)))')
  if ($LASTEXITCODE -ne 0) { throw 'Public key fingerprint export failed' }
  [IO.File]::WriteAllText((Join-Path $ReleaseRoot 'RELEASE_PUBLIC_KEY_FINGERPRINTS.txt'), "$PublicKeys`n", [Text.UTF8Encoding]::new($false))
  Copy-Item -LiteralPath (Join-Path $Root 'RELEASE_SIGNING.md') -Destination $ReleaseRoot
} else {
  Write-Warning 'Development artifact: unsigned; not accepted by the official updater.'
}
Write-Output "Release verified: $ReleaseRoot"
