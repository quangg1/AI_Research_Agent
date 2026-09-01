# Retry VM.Standard.A1.Flex launch until capacity is available (run overnight on Windows).
# Uses Python OCI SDK (installed with pip install oci-cli) - avoids Windows CLI JSON quoting bugs.
param(
  [string]$EnvFile = (Join-Path $PSScriptRoot "retry.env")
)

if (-not (Test-Path $EnvFile)) {
  throw "Missing $EnvFile - copy retry.env.example to retry.env and fill OCIDs."
}

$python = Get-Command python -ErrorAction SilentlyContinue
if (-not $python) {
  throw "Python not found. Use conda base or: pip install oci-cli"
}

& $python.Source (Join-Path $PSScriptRoot "retry-launch.py")
exit $LASTEXITCODE
