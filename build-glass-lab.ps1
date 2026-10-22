param([switch]$SkipChecks)
$ErrorActionPreference = 'Stop'
$Root = Split-Path -Parent $MyInvocation.MyCommand.Path
Set-Location -LiteralPath $Root
if (-not $SkipChecks) {
  $SyntaxSource = @'
from pathlib import Path
paths = list(Path(".").glob("*.py")) + list(Path("build_hooks").glob("*.py")) + [Path("MOKU.spec")]
for path in paths:
    compile(path.read_text(encoding="utf-8-sig"), str(path), "exec")
print(f"Python syntax verified: {len(paths)} files")
'@
  $SyntaxSource | python -I -B -
  if ($LASTEXITCODE -ne 0) { throw 'Python syntax check failed' }
  foreach ($script in Get-ChildItem -LiteralPath $Root -Filter '*.ps1') {
    $parseTokens = $null
    $parseErrors = $null
    [System.Management.Automation.Language.Parser]::ParseFile($script.FullName, [ref]$parseTokens, [ref]$parseErrors) | Out-Null
    if ($parseErrors.Count) { throw "PowerShell syntax: $($script.Name): $parseErrors" }
  }
  foreach ($script in Get-ChildItem -LiteralPath (Join-Path $Root 'web') -Recurse -Filter '*.js') {
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
foreach ($name in @('RELEASE_NOTES.md','EXPERIMENT.md','LICENSE','PRIVACY.md','SECURITY.md','THIRD_PARTY_NOTICES.md')) {
  Copy-Item -LiteralPath (Join-Path $Root $name) -Destination (Join-Path $Dist $name)
}
python -B generate-third-party-licenses.py (Join-Path $Dist 'THIRD_PARTY_LICENSES.txt')
if ($LASTEXITCODE -ne 0) { throw 'Third-party notices failed' }
Get-Content -LiteralPath (Join-Path $Root 'web\vendor\lens\LICENSE') -Raw | Add-Content -LiteralPath (Join-Path $Dist 'THIRD_PARTY_LICENSES.txt') -Encoding UTF8
Get-Content -LiteralPath (Join-Path $Root 'web\vendor\lattice\LICENSE') -Raw | Add-Content -LiteralPath (Join-Path $Dist 'THIRD_PARTY_LICENSES.txt') -Encoding UTF8
$Hash = (Get-FileHash -LiteralPath $Exe -Algorithm SHA256).Hash
[IO.File]::WriteAllText((Join-Path $Dist 'SHA256.txt'), "$Hash  MOKU-Glass-Lab.exe`r`n", [Text.UTF8Encoding]::new($false))
python -B build_manifest.py write (Join-Path $Dist 'BUILD_MANIFEST.json') $Exe --expected-source-generation $SourceBefore
if ($LASTEXITCODE -ne 0) { throw 'Package fingerprint failed' }
python -B build_manifest.py verify (Join-Path $Dist 'BUILD_MANIFEST.json') $Exe
if ($LASTEXITCODE -ne 0) { throw 'Package verification failed' }
Write-Output "Built MOKU Glass Lab 01: $Exe"
