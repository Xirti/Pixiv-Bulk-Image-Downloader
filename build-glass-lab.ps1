param([switch]$SkipChecks)
$ErrorActionPreference = 'Stop'
$Root = Split-Path -Parent $MyInvocation.MyCommand.Path
Set-Location -LiteralPath $Root
if (-not $SkipChecks) {
  python -B run_tests.py test_experiment_profile test_packaged_app test_auth_store test_workspace_store test_search_service
  if ($LASTEXITCODE -ne 0) { throw 'Experimental checks failed' }
  foreach ($script in (Get-ChildItem -LiteralPath (Join-Path $Root 'web') -Recurse -Filter '*.js')) {
    node --check $script.FullName
    if ($LASTEXITCODE -ne 0) { throw "JavaScript syntax: $($script.Name)" }
  }
}
$SourceBefore = (python -B build_manifest.py source).Trim()
if ($LASTEXITCODE -ne 0) { throw 'Source fingerprint failed' }
python -B -m PyInstaller --noconfirm MOKU.spec
if ($LASTEXITCODE -ne 0) { throw 'Experimental package build failed' }
$Dist = Join-Path $Root 'dist\MOKU-Glass-Lab'
$Exe = Join-Path $Dist 'MOKU-Glass-Lab.exe'
Copy-Item -LiteralPath (Join-Path $Root 'MOKU.exe.config') -Destination (Join-Path $Dist 'MOKU-Glass-Lab.exe.config')
foreach ($name in @('EXPERIMENT.md','LICENSE','PRIVACY.md','SECURITY.md','THIRD_PARTY_NOTICES.md')) {
  Copy-Item -LiteralPath (Join-Path $Root $name) -Destination (Join-Path $Dist $name)
}
python -B generate-third-party-licenses.py (Join-Path $Dist 'THIRD_PARTY_LICENSES.txt')
if ($LASTEXITCODE -ne 0) { throw 'Third-party notices failed' }
Get-Content -LiteralPath (Join-Path $Root 'web\vendor\lens\LICENSE') -Raw | Add-Content -LiteralPath (Join-Path $Dist 'THIRD_PARTY_LICENSES.txt') -Encoding UTF8
$Hash = (Get-FileHash -LiteralPath $Exe -Algorithm SHA256).Hash
[IO.File]::WriteAllText((Join-Path $Dist 'SHA256.txt'), "$Hash  MOKU-Glass-Lab.exe`r`n", [Text.UTF8Encoding]::new($false))
python -B build_manifest.py write (Join-Path $Dist 'BUILD_MANIFEST.json') $Exe --expected-source-generation $SourceBefore
if ($LASTEXITCODE -ne 0) { throw 'Package fingerprint failed' }
python -B build_manifest.py verify (Join-Path $Dist 'BUILD_MANIFEST.json') $Exe
if ($LASTEXITCODE -ne 0) { throw 'Package verification failed' }
Write-Output "Built MOKU Glass Lab 01: $Exe"
