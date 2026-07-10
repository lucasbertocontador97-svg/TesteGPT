$ErrorActionPreference = "Stop"

$RepoRoot = Split-Path -Parent (Split-Path -Parent $MyInvocation.MyCommand.Path)
$Python = "$env:USERPROFILE\.cache\codex-runtimes\codex-primary-runtime\dependencies\python\python.exe"
if (-not (Test-Path -LiteralPath $Python)) {
    $Python = "python"
}

$SourceUrl = "https://testegpt-production.up.railway.app/bfbm/live-full.csv?token=xBW42VXUy3h5Xhx3mSQeX83CuZ4-BldH"
$NotifyUrl = "https://testegpt-production.up.railway.app/bfbm/notify-bet?token=xBW42VXUy3h5Xhx3mSQeX83CuZ4-BldH"
$Bridge = Join-Path $RepoRoot "scripts\bfbm_bridge.py"

& $Python $Bridge `
    --host "127.0.0.1" `
    --port 8787 `
    --source-url $SourceUrl `
    --source-poll-seconds 20 `
    --notify-url $NotifyUrl `
    --min-price 1.80 `
    --max-price 100.00 `
    --max-tips 4
