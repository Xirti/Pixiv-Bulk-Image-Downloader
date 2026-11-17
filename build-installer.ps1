param(
  [switch]$SkipBuild,
  [switch]$Development,
  [string]$VerificationRecord = $env:MOKU_VERIFICATION_RECORD,
  [string]$ReleaseKey = $env:MOKU_RELEASE_KEY,
  [string]$Compiler = $env:MOKU_ISCC,
  [string]$WebViewInstaller,
  [string]$CodeSignThumbprint = $env:MOKU_CODESIGN_THUMBPRINT,
  [uri]$TimestampUrl
)
$ErrorActionPreference = 'Stop'
$Root = $PSScriptRoot
Set-Location -LiteralPath $Root
if (-not $Development) {
  if (-not $VerificationRecord) { throw 'Formal installers require -VerificationRecord (or MOKU_VERIFICATION_RECORD) from the engineering verification run.' }
  python -B release_verification.py verify $VerificationRecord
  if ($LASTEXITCODE -ne 0) { throw 'Engineering verification failed; run the source-matched checks again.' }
}
if (-not $Compiler) {
  $command = Get-Command ISCC.exe -ErrorAction SilentlyContinue
  if ($command) { $Compiler = $command.Source }
}
if (-not $Compiler -or -not (Test-Path -LiteralPath $Compiler -PathType Leaf)) {
  throw 'Set MOKU_ISCC or pass -Compiler with the Inno Setup 7 ISCC.exe path.'
}
if ($CodeSignThumbprint -and -not $TimestampUrl) { throw 'Windows signing requires -TimestampUrl' }
if (-not $SkipBuild) {
  & (Join-Path $Root 'build-portable.ps1') -Development:$Development -VerificationRecord $VerificationRecord -ReleaseKey $ReleaseKey -CodeSignThumbprint $CodeSignThumbprint -TimestampUrl $TimestampUrl
  if ($LASTEXITCODE -ne 0) { throw 'Application build failed' }
}
$Payload = Join-Path $Root 'dist\MOKU'
$Exe = Join-Path $Payload 'MOKU.exe'
python -B build_manifest.py verify (Join-Path $Payload 'BUILD_MANIFEST.json') $Exe
if ($LASTEXITCODE -ne 0) { throw 'Application manifest verification failed' }
$Version = (python -B -c 'from version import __version__; print(__version__)').Trim()
if ($LASTEXITCODE -ne 0 -or $Version -notmatch '^\d+\.\d+\.\d+$') { throw 'Installer requires a stable numeric version' }
$Manifest = Get-Content -LiteralPath (Join-Path $Payload 'BUILD_MANIFEST.json') -Raw | ConvertFrom-Json
if ($Manifest.version -ne $Version) { throw 'Application version differs from source' }
if (-not $Development) {
  python -B release_verification.py verify $VerificationRecord --manifest (Join-Path $Payload 'BUILD_MANIFEST.json')
  if ($LASTEXITCODE -ne 0) { throw 'Package verification evidence differs from current source.' }
  python -B sign-release.py verify-bundle --root $Payload --version $Version
  if ($LASTEXITCODE -ne 0) { throw 'Signed application verification failed' }
}
foreach ($Name in @('logs','downloads','runtime','__pycache__')) {
  if (Test-Path -LiteralPath (Join-Path $Payload $Name)) { throw "Runtime data in payload: $Name" }
}
$Output = Join-Path $Root $(if ($Development) { "release\installer-development-v$Version" } else { "release\installer-v$Version" })
New-Item -ItemType Directory -Path $Output -Force | Out-Null
$Suffix = if ($Development) { '-development' } else { '' }
$ManagedNames = (python -B -c 'from update_package import MANAGED; print("|".join(sorted(MANAGED)))').Trim()
if ($LASTEXITCODE -ne 0 -or -not $ManagedNames) { throw 'Managed component inventory failed' }
$Arguments = @("/DAppVersion=$Version", "/DPayloadRoot=$Payload", "/DOutputRoot=$Output", "/DNameSuffix=$Suffix", "/DManagedNames=$ManagedNames")
if ($WebViewInstaller) {
  $WebViewInstaller = (Resolve-Path -LiteralPath $WebViewInstaller).Path
  $Signature = Get-AuthenticodeSignature -LiteralPath $WebViewInstaller
  if ($Signature.Status -ne 'Valid' -or $Signature.SignerCertificate.Subject -notmatch 'O=Microsoft Corporation') {
    throw 'WebView2 installer must have a valid Microsoft signature'
  }
  $Arguments += "/DWebViewInstaller=$WebViewInstaller"
}
& $Compiler @Arguments (Join-Path $Root 'installer\MOKU.iss')
if ($LASTEXITCODE -ne 0) { throw 'Installer compilation failed' }
$Installer = Join-Path $Output "MOKU-Setup-v$Version-windows-x64$Suffix.exe"
if ($CodeSignThumbprint) {
  & (Join-Path $Root 'sign-windows.ps1') -Files @($Installer) -CertificateThumbprint $CodeSignThumbprint -TimestampUrl $TimestampUrl
}
Get-FileHash -LiteralPath $Installer -Algorithm SHA256
Write-Output "Installer built: $Installer"
