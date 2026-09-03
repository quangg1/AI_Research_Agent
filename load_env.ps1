# Load environment variables from .env file
# Usage: .\load_env.ps1

$envFile = ".env"

if (-Not (Test-Path $envFile)) {
    Write-Host "❌ .env file not found!" -ForegroundColor Red
    exit 1
}

Write-Host "🔧 Loading environment variables from $envFile..." -ForegroundColor Cyan

$loadedCount = 0
$skippedCount = 0

Get-Content $envFile | ForEach-Object {
    $line = $_.Trim()
    
    # Skip comments and empty lines
    if ($line -match '^#' -or $line -eq '') {
        return
    }
    
    # Match KEY=VALUE format
    if ($line -match '^([^=]+)=(.*)$') {
        $key = $matches[1].Trim()
        $value = $matches[2].Trim()
        
        # Remove quotes if present
        $value = $value.Trim('"').Trim("'")
        
        # Set environment variable
        Set-Item -Path "env:$key" -Value $value
        
        # Show confirmation (mask sensitive values)
        if ($value.Length -gt 10) {
            $masked = $value.Substring(0, 8) + "..."
        } else {
            $masked = "***"
        }
        
        Write-Host "  ✅ $key = $masked" -ForegroundColor Green
        $loadedCount++
    } else {
        Write-Host "  ⚠️  Skipped invalid line: $line" -ForegroundColor Yellow
        $skippedCount++
    }
}

Write-Host "`n📊 Summary:" -ForegroundColor Cyan
Write-Host "  Loaded: $loadedCount variables" -ForegroundColor Green
if ($skippedCount -gt 0) {
    Write-Host "  Skipped: $skippedCount lines" -ForegroundColor Yellow
}

Write-Host "`n✅ Environment variables loaded successfully!" -ForegroundColor Green
