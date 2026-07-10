$ErrorActionPreference = "Stop"

$RepoRoot = Split-Path -Parent (Split-Path -Parent $MyInvocation.MyCommand.Path)
$Python = "$env:USERPROFILE\.cache\codex-runtimes\codex-primary-runtime\dependencies\python\python.exe"
if (-not (Test-Path -LiteralPath $Python)) {
    $Python = "python"
}

$Scanner = Join-Path $RepoRoot "scripts\bfbm_market_scanner.py"
$ExportPath = Join-Path $env:USERPROFILE "OneDrive\Desktop\................csv"
$PostUrl = "https://testegpt-production.up.railway.app/bfbm/markets/snapshot?token=xBW42VXUy3h5Xhx3mSQeX83CuZ4-BldH"

& $Python $Scanner `
    --export-path $ExportPath `
    --post-url $PostUrl `
    --poll-seconds 5 `
    --post-interval-seconds 30
