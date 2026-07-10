$ErrorActionPreference = "Stop"

$RepoRoot = Split-Path -Parent (Split-Path -Parent $MyInvocation.MyCommand.Path)
$Python = "$env:USERPROFILE\.cache\codex-runtimes\codex-primary-runtime\dependencies\python\python.exe"
if (-not (Test-Path -LiteralPath $Python)) {
    $Python = "python"
}

$Scanner = Join-Path $RepoRoot "scripts\bfbm_market_scanner.py"
$MarketFolder = Join-Path $env:USERPROFILE "TesteGPT-BFBM-Mercados"
$ExportPath = Join-Path $MarketFolder "EXPORTAR DADOS VISIVEIS.csv"
$MarketExportPath = Join-Path $MarketFolder "EXPORTAR MERCADOS.csv"
$PostUrl = "https://testegpt-production.up.railway.app/bfbm/markets/snapshot?token=xBW42VXUy3h5Xhx3mSQeX83CuZ4-BldH"

& $Python $Scanner `
    --export-path $ExportPath `
    --market-export-path $MarketExportPath `
    --post-url $PostUrl `
    --poll-seconds 5 `
    --post-interval-seconds 30
