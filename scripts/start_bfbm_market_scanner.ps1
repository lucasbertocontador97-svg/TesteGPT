$ErrorActionPreference = "Stop"

$RepoRoot = Split-Path -Parent (Split-Path -Parent $MyInvocation.MyCommand.Path)
$Python = "$env:USERPROFILE\.cache\codex-runtimes\codex-primary-runtime\dependencies\python\python.exe"
if (-not (Test-Path -LiteralPath $Python)) {
    $Python = "python"
}

$Scanner = Join-Path $RepoRoot "scripts\bfbm_market_scanner.py"
$MarketFolder = Join-Path $env:USERPROFILE "TesteGPT-BFBM-Mercados"
$DesktopFolders = @(
    (Join-Path $env:USERPROFILE "OneDrive\Desktop"),
    (Join-Path $env:USERPROFILE "Desktop"),
    $MarketFolder
)

function Get-LatestCsv([string[]] $Patterns, [string] $FallbackPath) {
    $candidates = @()
    foreach ($folder in $DesktopFolders) {
        if (-not (Test-Path -LiteralPath $folder)) {
            continue
        }
        foreach ($pattern in $Patterns) {
            $candidates += Get-ChildItem -LiteralPath $folder -Filter $pattern -File -ErrorAction SilentlyContinue
        }
    }
    $latest = $candidates | Sort-Object LastWriteTime -Descending | Select-Object -First 1
    if ($latest) {
        return $latest.FullName
    }
    return $FallbackPath
}

$ExportPath = Get-LatestCsv @("EXPORTAR DADOS VISIVEIS*.csv", "EXPORTAR DADOS VISÍVEIS*.csv") (Join-Path $MarketFolder "EXPORTAR DADOS VISIVEIS.csv")
$MarketExportPath = Get-LatestCsv @("EX1*.csv", "EXPORTAR MERCADOS*.csv") (Join-Path $MarketFolder "EXPORTAR MERCADOS.csv")
$RailwayBase = $env:TESTEGPT_RAILWAY_BASE
if (-not $RailwayBase) {
    $RailwayBase = "https://testegpt-production.up.railway.app"
}
$Token = $env:BFBM_TOKEN
if (-not $Token) {
    throw "Configure BFBM_TOKEN no ambiente antes de iniciar o scanner de mercados."
}
$PostUrl = "$RailwayBase/bfbm/markets/snapshot?token=$Token"

Write-Host "BFBM scanner usando dados visiveis: $ExportPath"
Write-Host "BFBM scanner usando catalogo: $MarketExportPath"

& $Python $Scanner `
    --export-path $ExportPath `
    --market-export-path $MarketExportPath `
    --post-url $PostUrl `
    --poll-seconds 5 `
    --post-interval-seconds 30
