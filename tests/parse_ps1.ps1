$errors = $null
$tokens = $null
[void][Management.Automation.Language.Parser]::ParseFile($args[0],[ref]$tokens,[ref]$errors)
Write-Output ("ERROR_COUNT=" + $errors.Count)
foreach ($errorItem in $errors) {
  Write-Output ("LINE=" + $errorItem.Extent.StartLineNumber + " MSG=" + $errorItem.Message)
}
if ($errors.Count -gt 0) { exit 1 }
