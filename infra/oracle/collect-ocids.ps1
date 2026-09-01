# Print OCIDs needed for retry-launch.ps1. Requires: oci setup config
param(
  [Parameter(Mandatory = $true)]
  [string]$CompartmentOcid
)

$ErrorActionPreference = "Stop"

if (-not (Get-Command oci -ErrorAction SilentlyContinue)) {
  Write-Error "OCI CLI not found. Install with: pip install oci-cli"
}

Write-Host "=== Availability domains (copy name into AVAILABILITY_DOMAIN) ===" -ForegroundColor Cyan
oci iam availability-domain list --output table

Write-Host ""
Write-Host "=== Subnets (pick a public subnet) ===" -ForegroundColor Cyan
oci network subnet list --compartment-id $CompartmentOcid --all --output table

Write-Host ""
Write-Host "=== Ubuntu ARM images for A1 (pick 22.04 or 24.04) ===" -ForegroundColor Cyan
oci compute image list `
  --compartment-id $CompartmentOcid `
  --operating-system "Canonical Ubuntu" `
  --shape "VM.Standard.A1.Flex" `
  --limit 20 `
  --output table

Write-Host ""
Write-Host "Fill retry.env then run retry-launch.ps1" -ForegroundColor Green
