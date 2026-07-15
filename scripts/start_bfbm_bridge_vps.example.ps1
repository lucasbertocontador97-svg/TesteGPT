$ErrorActionPreference = "Stop"

# Copie este arquivo para start_bfbm_bridge_vps.ps1 no VPS e preencha os valores.
# Nao coloque o arquivo preenchido no GitHub.

$RepoRoot = "C:\TesteGPT"
$Python = Join-Path $RepoRoot ".venv\Scripts\python.exe"
$Bridge = Join-Path $RepoRoot "scripts\bfbm_bridge.py"

$BridgeApiToken = "PREENCHA_CHAVE_LOCAL_DA_PONTE"
$RailwayToken = "PREENCHA_TOKEN_BFBM_DO_RAILWAY"
$RailwayBase = "https://testegpt-production.up.railway.app"

$SourceUrl = "$RailwayBase/bfbm/live-full.csv?token=$RailwayToken"
$NotifyUrl = "$RailwayBase/bfbm/notify-bet?token=$RailwayToken"
$ResultNotifyUrl = "$RailwayBase/bfbm/notify-bet-result?token=$RailwayToken"

$env:BETFAIR_USERNAME = "PREENCHA_USUARIO_BETFAIR"
$env:BETFAIR_PASSWORD = "PREENCHA_SENHA_BETFAIR"
$env:BETFAIR_APP_KEY = "PREENCHA_APP_KEY_BETFAIR"
$env:BETFAIR_CERT_PATH = "C:\BetfairCert\client-2048.crt"
$env:BETFAIR_KEY_PATH = "C:\BetfairCert\client-2048.key"

& $Python $Bridge `
    --host "127.0.0.1" `
    --port 8787 `
    --source-url $SourceUrl `
    --source-poll-seconds 20 `
    --notify-url $NotifyUrl `
    --result-notify-url $ResultNotifyUrl `
    --orders-poll-seconds 60 `
    --api-token $BridgeApiToken `
    --min-price 1.80 `
    --max-price 100.00 `
    --max-tips 12 `
    --tip-keep-seconds 600
