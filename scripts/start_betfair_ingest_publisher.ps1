$ErrorActionPreference = "Stop"

$RepoRoot = Split-Path -Parent (Split-Path -Parent $MyInvocation.MyCommand.Path)
$Python = Join-Path $RepoRoot ".venv\Scripts\python.exe"
if (-not (Test-Path -LiteralPath $Python)) {
    $Python = "$env:USERPROFILE\.cache\codex-runtimes\codex-primary-runtime\dependencies\python\python.exe"
}

if (-not $env:BETFAIR_CERT_PATH) {
    $env:BETFAIR_CERT_PATH = "C:\BetfairCert\client-2048.crt"
}
if (-not $env:BETFAIR_KEY_PATH) {
    $env:BETFAIR_KEY_PATH = "C:\BetfairCert\client-2048.key"
}

$ProductionIngestUrl = "https://smart-betting-bot-5.emergent.host/api/betfair/ingest"
$IngestToken = $env:BETFAIR_INGEST_TOKEN
if (-not $IngestToken) {
    $IngestToken = $env:BFBM_SYNC_TOKEN
}
if (-not $IngestToken) {
    $IngestToken = $env:BFBM_TOKEN
}
if (-not $IngestToken) {
    throw "Configure BETFAIR_INGEST_TOKEN, BFBM_SYNC_TOKEN ou BFBM_TOKEN no ambiente antes de iniciar o publisher."
}

& $Python (Join-Path $RepoRoot "scripts\betfair_ingest_publisher.py") `
    --ingest-url $ProductionIngestUrl `
    --token $IngestToken `
    --hours-ahead 48 `
    --max-results 200 `
    --poll-seconds 60 `
    --auth-error-cooldown-seconds 3600
