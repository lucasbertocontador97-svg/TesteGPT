$ErrorActionPreference = "Stop"
function Use-UserEnv([string] $Name) {
    if (-not [Environment]::GetEnvironmentVariable($Name, "Process")) {
        $value = [Environment]::GetEnvironmentVariable($Name, "User")
        if ($value) {
            [Environment]::SetEnvironmentVariable($Name, $value, "Process")
        }
    }
}

@(
    "BFBM_TOKEN",
    "TESTEGPT_RAILWAY_BASE",
    "BETFAIR_USERNAME",
    "BETFAIR_PASSWORD",
    "BETFAIR_APP_KEY",
    "BETFAIR_CERT_PATH",
    "BETFAIR_KEY_PATH"
) | ForEach-Object { Use-UserEnv $_ }

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
$RailwayBase = $env:TESTEGPT_RAILWAY_BASE
if (-not $RailwayBase) {
    $RailwayBase = "https://testegpt-production.up.railway.app"
}
$Token = $env:BFBM_TOKEN
if (-not $Token) {
    throw "Configure BFBM_TOKEN no ambiente antes de iniciar o publisher de mercados."
}
$PostUrl = "$RailwayBase/bfbm/markets/snapshot?token=$Token"
& $Python (Join-Path $RepoRoot "scripts\betfair_market_publisher.py") --post-url $PostUrl --hours-ahead 48 --max-results 200 --poll-seconds 60 --auth-error-cooldown-seconds 3600
