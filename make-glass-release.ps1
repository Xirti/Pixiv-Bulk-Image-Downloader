param([switch]$SkipBuild)
$ErrorActionPreference = 'Stop'
$Root = Split-Path -Parent $MyInvocation.MyCommand.Path
Set-Location -LiteralPath $Root
if (-not $SkipBuild) {
  & (Join-Path $Root 'build-glass-lab.ps1')
  if ($LASTEXITCODE -ne 0) { throw 'Glass Lab build failed' }
}
$Tag = (python -B -c 'from experiment_profile import RELEASE_TAG; print(RELEASE_TAG)').Trim()
if ($LASTEXITCODE -ne 0 -or $Tag -notmatch '^v\d+\.\d+\.\d+-glass-r[1-9]\d*$') { throw 'Invalid experimental release tag' }
$Dist = Join-Path $Root 'dist\MOKU-Glass-Lab'
$Exe = Join-Path $Dist 'MOKU-Glass-Lab.exe'
python -B build_manifest.py verify (Join-Path $Dist 'BUILD_MANIFEST.json') $Exe
if ($LASTEXITCODE -ne 0) { throw 'Glass Lab manifest verification failed' }
foreach ($Name in @('logs','downloads','runtime','test-builds','__pycache__','.git')) {
  if (Test-Path -LiteralPath (Join-Path $Dist $Name)) { throw "Runtime or development files remain: $Name" }
}
$ReleaseRoot = Join-Path $Root "release\$Tag"
if (Test-Path -LiteralPath $ReleaseRoot) { throw "Release already exists: $ReleaseRoot" }
New-Item -ItemType Directory -Path $ReleaseRoot | Out-Null
$ArchiveName = "MOKU-Glass-Lab-$Tag-windows-x64.zip"
$Archive = Join-Path $ReleaseRoot $ArchiveName
Compress-Archive -LiteralPath $Dist -DestinationPath $Archive -CompressionLevel Optimal
python -B -c 'import sys,zipfile; z=zipfile.ZipFile(sys.argv[1]); assert z.testzip() is None' $Archive
if ($LASTEXITCODE -ne 0) { throw 'Glass Lab archive verification failed' }
$ArchiveHash = (Get-FileHash -LiteralPath $Archive -Algorithm SHA256).Hash
$ExeHash = (Get-FileHash -LiteralPath $Exe -Algorithm SHA256).Hash
[IO.File]::WriteAllText((Join-Path $ReleaseRoot 'SHA256SUMS.txt'), "$ArchiveHash  $ArchiveName`n$ExeHash  MOKU-Glass-Lab/MOKU-Glass-Lab.exe`n", [Text.UTF8Encoding]::new($false))
Copy-Item -LiteralPath (Join-Path $Root 'RELEASE_NOTES.md') -Destination $ReleaseRoot
Copy-Item -LiteralPath (Join-Path $Dist 'BUILD_MANIFEST.json') -Destination $ReleaseRoot
Write-Output "Release verified: $ReleaseRoot"
