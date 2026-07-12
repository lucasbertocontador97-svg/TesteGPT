param(
    [string]$LogPath = "$env:LOCALAPPDATA\bfbotmanager.com\Bf Bot Manager V3\log.txt",
    [Parameter(Mandatory = $true)]
    [string]$NotifyUrl,
    [string]$SidFilter = "CODEX-TESTEGPT",
    [switch]$IncludeZeroMatched
)

$ErrorActionPreference = "Stop"

function ConvertTo-QueryValue([string]$Value) {
    return [System.Uri]::EscapeDataString($Value)
}

if (-not (Test-Path -LiteralPath $LogPath)) {
    throw "Log do BFBM nao encontrado: $LogPath"
}

$count = 0
$sent = 0
$seen = @{}

Get-Content -LiteralPath $LogPath -Encoding UTF8 | ForEach-Object {
    $line = $_
    if ($line -notmatch "HandleOnPlaceBets:\s*Placed bet") {
        return
    }
    if ($SidFilter -and $line -notmatch [regex]::Escape($SidFilter)) {
        return
    }

    $placedAt = ""
    $betId = ""
    $sizeMatched = ""
    $success = ""
    $strategy = ""
    $sid = ""

    if ($line -match "^(\d{2}/\d{2}/\d{4}\s+\d{2}:\d{2}:\d{2}):") { $placedAt = $Matches[1].Trim() }
    if ($line -match "betId:\s*([^,]+)") { $betId = $Matches[1].Trim() }
    if ($line -match "sizeMatched:\s*([^,]+)") { $sizeMatched = $Matches[1].Trim() }
    if ($line -match "success:\s*([^,]+)") { $success = $Matches[1].Trim() }
    if ($line -match "strategy:\s*([^,]+)") { $strategy = $Matches[1].Trim() }
    if ($line -match "sid:\s*(.+)$") { $sid = $Matches[1].Trim() }

    if (-not $betId -or $seen.ContainsKey($betId)) {
        return
    }
    $seen[$betId] = $true

    $matchedNumber = 0.0
    [void][double]::TryParse($sizeMatched.Replace(",", "."), [Globalization.NumberStyles]::Any, [Globalization.CultureInfo]::InvariantCulture, [ref]$matchedNumber)
    if (-not $IncludeZeroMatched -and $matchedNumber -le 0) {
        return
    }

    $count += 1
    $separator = if ($NotifyUrl.Contains("?")) { "&" } else { "?" }
    $url = $NotifyUrl + $separator +
        "silent=1" +
        "&bet_id=$(ConvertTo-QueryValue $betId)" +
        "&placed_at=$(ConvertTo-QueryValue $placedAt)" +
        "&size_matched=$(ConvertTo-QueryValue $sizeMatched)" +
        "&success=$(ConvertTo-QueryValue $success)" +
        "&strategy=$(ConvertTo-QueryValue $strategy)" +
        "&sid=$(ConvertTo-QueryValue $sid)" +
        "&line=$(ConvertTo-QueryValue $line)"

    try {
        Invoke-WebRequest -UseBasicParsing -Uri $url -TimeoutSec 15 | Out-Null
        $sent += 1
    }
    catch {
        Write-Warning "Falhou betId=${betId}: $($_.Exception.Message)"
    }
}

Write-Host "Backfill concluido. Encontradas=$count Enviadas=$sent"
