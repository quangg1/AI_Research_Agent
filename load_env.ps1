# Simple .env loader for PowerShell
# Usage: .\load_env.ps1

$envFile = ".env"

if (-Not (Test-Path $envFile)) {
    Write-Host "ERROR: .env file not found!" -ForegroundColor Red
    exit 1
}

Write-Host "Loading environment variables from .env..." -ForegroundColor Green

$loaded = 0

Get-Content $envFile | ForEach-Object {
    $line = $_.Trim()
    
    # Skip empty lines and comments
    if ([string]::IsNullOrWhiteSpace($line) -or $line.StartsWith('#')) {
        return
    }
    
    # Parse KEY=VALUE
    if ($line -match '^([^=]+)=(.*)$') {
        $key = $matches[1].Trim()
        $value = $matches[2].Trim()
        
        # Remove surrounding quotes
        if ($value.StartsWith('"') -and $value.EndsWith('"')) {
            $value = $value.Substring(1, $value.Length - 2)
        }
        if ($value.StartsWith("'") -and $value.EndsWith("'")) {
            $value = $value.Substring(1, $value.Length - 2)
        }
        
        # Set the environment variable
        [Environment]::SetEnvironmentVariable($key, $value, "Process")
        
        # Show what was loaded (masked)
        if ($value.Length -gt 10) {
            $display = $value.Substring(0, 8) + "..."
        } else {
            $display = "***"
        }
        
        Write-Host "  $key = $display" -ForegroundColor Cyan
        $loaded++
    }
}

Write-Host ""
Write-Host "Loaded $loaded environment variables successfully!" -ForegroundColor Green
