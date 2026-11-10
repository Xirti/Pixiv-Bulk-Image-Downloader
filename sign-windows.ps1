param(
  [Parameter(Mandatory=$true)][string[]]$Files,
  [Parameter(Mandatory=$true)][string]$CertificateThumbprint,
  [Parameter(Mandatory=$true)][uri]$TimestampUrl,
  [string]$SignToolPath
)
$ErrorActionPreference = 'Stop'
if ($CertificateThumbprint -notmatch '^[a-fA-F0-9]{40}$') { throw 'Invalid code-signing certificate thumbprint' }
if ($TimestampUrl.Scheme -ne 'https') { throw 'Timestamp service must use HTTPS' }
if (-not $SignToolPath) {
  $SdkBin = Join-Path ${env:ProgramFiles(x86)} 'Windows Kits\10\bin'
  $Tools = @(Get-ChildItem -Path (Join-Path $SdkBin '*\x64\signtool.exe') -File | Sort-Object FullName -Descending)
  if (-not $Tools.Count) { throw 'Install Windows SDK SignTool first' }
  $SignToolPath = $Tools[0].FullName
}
$SignToolPath = (Resolve-Path -LiteralPath $SignToolPath).Path
foreach ($File in $Files) {
  $Target = (Resolve-Path -LiteralPath $File).Path
  & $SignToolPath sign /sha1 $CertificateThumbprint /fd SHA256 /tr $TimestampUrl.AbsoluteUri /td SHA256 $Target
  if ($LASTEXITCODE -ne 0) { throw "Windows signing failed: $Target" }
  & $SignToolPath verify /pa /all /v $Target
  if ($LASTEXITCODE -ne 0) { throw "Windows signature verification failed: $Target" }
  $Signature = Get-AuthenticodeSignature -LiteralPath $Target
  if ($Signature.Status -ne 'Valid' -or -not $Signature.TimeStamperCertificate -or
      $Signature.SignerCertificate.Thumbprint -ne $CertificateThumbprint) {
    throw "Publisher signature or timestamp not verified: $Target"
  }
}
