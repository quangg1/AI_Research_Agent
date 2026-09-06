# Trust Bench E2E - Score Automation Script
# Run this from D:\AI_Research_Agent

Write-Host "🔬 Trust Bench E2E - Scoring Verdicts" -ForegroundColor Cyan
Write-Host ""

# Paths
$claimsPath = "C:\Users\ADMIN\AppData\Local\Temp\claude\d--AI-Research-Agent\91b984e0-c7e9-4a8a-9195-71671f6db3b7\scratchpad\audit_packet.claims.json"
$verdictsPath = "data\eval\verdicts_gemini.json"

# Check files exist
if (-Not (Test-Path $claimsPath)) {
    Write-Host "❌ Claims file not found: $claimsPath" -ForegroundColor Red
    exit 1
}

if (-Not (Test-Path $verdictsPath)) {
    Write-Host "❌ Verdicts file not found: $verdictsPath" -ForegroundColor Red
    Write-Host "Creating verdicts file from Gemini response..." -ForegroundColor Yellow
    
    $verdicts = @"
[
  {"id": 1, "verdict": "NOT_SUPPORTED", "reason": "The excerpt does not contain the 80% statistic or discuss statistical ties between SAS and MAS[cite: 1]."},
  {"id": 2, "verdict": "SUPPORTED", "reason": "The provided table data explicitly shows SMTL-100 at 43.6 and SMTL-300 at 48.6, confirming the +5.0 increase as interaction steps scale[cite: 1]."},
  {"id": 3, "verdict": "CANNOT_VERIFY", "reason": "The excerpt cuts off while discussing prior studies and is too short to verify the claim regarding structural superiority[cite: 1]."},
  {"id": 4, "verdict": "SUPPORTED", "reason": "The excerpt directly states that switching from Gemini-1.5-Flash to Gemini-2.0-Flash increases 'Both Pass' ties while SAS and MAS Wins remain relatively unchanged[cite: 1]."},
  {"id": 5, "verdict": "SUPPORTED", "reason": "The text confirms that agent routing provides tangible operational cost efficiency by unlocking new accuracy-efficiency tradeoffs[cite: 1]."},
  {"id": 6, "verdict": "SUPPORTED", "reason": "The table data explicitly confirms that the SMTL-100 model achieves a score of 43.6[cite: 1]."},
  {"id": 7, "verdict": "NOT_SUPPORTED", "reason": "The excerpt discusses token consumption but contains none of the specific monetary prices or percentage routing allocations mentioned in the claim[cite: 1]."},
  {"id": 8, "verdict": "SUPPORTED", "reason": "The table confirms the baseline 43.6% for SMTL-100, 43.4% for Tongyi, and 41.2% for MiroThinker, fully supporting the calculated margins[cite: 1]."},
  {"id": 9, "verdict": "SUPPORTED", "reason": "The text explicitly confirms that the model reaches 78.0% on XBench-DeepSearch[cite: 1]."},
  {"id": 10, "verdict": "NOT_SUPPORTED", "reason": "The memo presents 88.1% and +12.0 percentage points as absolute fixed values, whereas the source states 'up to 88.1%' and a range of '1.1-12%', making the specific absolute numbers in the claim unsupported[cite: 1]."}
]
"@
    
    New-Item -Path (Split-Path $verdictsPath -Parent) -ItemType Directory -Force | Out-Null
    $verdicts | Out-File -FilePath $verdictsPath -Encoding utf8
    Write-Host "✅ Created: $verdictsPath" -ForegroundColor Green
}

Write-Host "📁 Files:" -ForegroundColor Cyan
Write-Host "  Claims:   $claimsPath"
Write-Host "  Verdicts: $verdictsPath"
Write-Host ""

# Activate conda environment if needed
if ($env:CONDA_DEFAULT_ENV -ne "base") {
    Write-Host "⚠️  Note: Make sure conda base environment is activated" -ForegroundColor Yellow
}

# Run the score command
Write-Host "🔬 Running score command..." -ForegroundColor Cyan
Write-Host ""

cd apps\agent

python -m app.eval.trust_bench_e2e score `
    "$claimsPath" `
    "..\..\$verdictsPath"

$exitCode = $LASTEXITCODE

if ($exitCode -eq 0) {
    Write-Host ""
    Write-Host "✅ Score completed successfully!" -ForegroundColor Green
    Write-Host ""
    Write-Host "📊 Results saved to: data\eval\trust_bench_e2e_history.jsonl" -ForegroundColor Cyan
} else {
    Write-Host ""
    Write-Host "❌ Score command failed with exit code: $exitCode" -ForegroundColor Red
}

cd ..\..
