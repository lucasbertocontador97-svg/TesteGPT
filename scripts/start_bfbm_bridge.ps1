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
    "BFBM_SYNC_TOKEN",
    "BFBM_BRIDGE_API_TOKEN",
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
if (-not (Test-Path -LiteralPath $Python)) {
    $Python = "python"
}

$RailwayBase = $env:TESTEGPT_RAILWAY_BASE
if (-not $RailwayBase) {
    $RailwayBase = "https://testegpt-production.up.railway.app"
}
$RailwayToken = $env:BFBM_TOKEN
$SyncToken = $env:BFBM_SYNC_TOKEN
$BridgeApiToken = $env:BFBM_BRIDGE_API_TOKEN
if (-not $RailwayToken) {
    throw "Configure BFBM_TOKEN no ambiente antes de iniciar a ponte."
}
if (-not $BridgeApiToken) {
    $BridgeApiToken = $RailwayToken
}
$SourceUrl = "$RailwayBase/bfbm/live-full.csv?token=$RailwayToken&limit=100"
$NotifyUrl = "$RailwayBase/bfbm/notify-bet?token=$RailwayToken"
$ResultNotifyUrl = "$RailwayBase/bfbm/notify-bet-result?token=$RailwayToken"
$SyncOrdersUrl = "$RailwayBase/api/bfbm/sync-orders"
$Bridge = Join-Path $RepoRoot "scripts\bfbm_bridge.py"

if (-not $env:BETFAIR_CERT_PATH) {
    $env:BETFAIR_CERT_PATH = "C:\BetfairCert\client-2048.crt"
}
if (-not $env:BETFAIR_KEY_PATH) {
    $env:BETFAIR_KEY_PATH = "C:\BetfairCert\client-2048.key"
}

$BridgeArgs = @(
    $Bridge,
    "--host", "127.0.0.1",
    "--port", "8787",
    "--source-url", $SourceUrl,
    "--source-poll-seconds", "20",
    "--notify-url", $NotifyUrl,
    "--result-notify-url", $ResultNotifyUrl,
    "--orders-poll-seconds", "60",
    "--api-token", $BridgeApiToken,
    "--min-price", "1.80",
    "--max-price", "100.00",
    "--max-tips", "100",
    "--tip-keep-seconds", "14400",
    "--allow-missing-ids"
)

if ($SyncToken) {
    $BridgeArgs += @("--sync-orders-url", $SyncOrdersUrl, "--sync-token", $SyncToken)
} else {
    Write-Host "BFBM_SYNC_TOKEN ausente: sync de ordens via /api/bfbm/sync-orders desativado; tips e notificacoes seguem ativas."
}

& $Python @BridgeArgs
