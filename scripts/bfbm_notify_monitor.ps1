param(
    [string]$LogPath = "$env:LOCALAPPDATA\bfbotmanager.com\Bf Bot Manager V3\log.txt",
    [Parameter(Mandatory = $true)]
    [string]$NotifyUrl,
    [int]$PollSeconds = 2
)

$ErrorActionPreference = "Stop"
$seen = @{}

function ConvertTo-QueryValue([string]$Value) {
    return [System.Uri]::EscapeDataString($Value)
}

function Send-BfbmNotification([string]$Line) {
    if ($Line -notmatch "HandleOnPlaceBets:\s*Placed bet") {
        return
    }

    $betId = ""
    $sizeMatched = ""
    $success = ""
    $strategy = ""
    $placedAt = ""
    $sid = ""

    if ($Line -match "^(\d{2}/\d{2}/\d{4}\s+\d{2}:\d{2}:\d{2}):") { $placedAt = $Matches[1].Trim() }
    if ($Line -match "betId:\s*([^,]+)") { $betId = $Matches[1].Trim() }
    if ($Line -match "sizeMatched:\s*([^,]+)") { $sizeMatched = $Matches[1].Trim() }
    if ($Line -match "success:\s*([^,]+)") { $success = $Matches[1].Trim() }
    if ($Line -match "strategy:\s*([^,]+)") { $strategy = $Matches[1].Trim() }
    if ($Line -match "sid:\s*(.+)$") { $sid = $Matches[1].Trim() }

    $dedupeKey = if ($betId) { $betId } else { $Line }
    if ($seen.ContainsKey($dedupeKey)) {
        return
    }
    $seen[$dedupeKey] = $true

    $separator = if ($NotifyUrl.Contains("?")) { "&" } else { "?" }
    $url = $NotifyUrl + $separator +
        "bet_id=$(ConvertTo-QueryValue $betId)" +
        "&placed_at=$(ConvertTo-QueryValue $placedAt)" +
        "&size_matched=$(ConvertTo-QueryValue $sizeMatched)" +
        "&success=$(ConvertTo-QueryValue $success)" +
        "&strategy=$(ConvertTo-QueryValue $strategy)" +
        "&sid=$(ConvertTo-QueryValue $sid)" +
        "&line=$(ConvertTo-QueryValue $Line)"

    Invoke-WebRequest -UseBasicParsing -Uri $url | Out-Null
    Write-Host "Notificado BFBM betId=$betId sizeMatched=$sizeMatched success=$success"
}

if (-not (Test-Path -LiteralPath $LogPath)) {
    throw "Log do BFBM nao encontrado: $LogPath"
}

Write-Host "Monitorando BFBM log: $LogPath"
Write-Host "Destino: $NotifyUrl"

$stream = [System.IO.File]::Open($LogPath, [System.IO.FileMode]::Open, [System.IO.FileAccess]::Read, [System.IO.FileShare]::ReadWrite)
try {
    $stream.Seek(0, [System.IO.SeekOrigin]::End) | Out-Null
    $reader = New-Object System.IO.StreamReader($stream)
    while ($true) {
        $line = $reader.ReadLine()
        if ($null -eq $line) {
            Start-Sleep -Seconds $PollSeconds
            continue
        }
        Send-BfbmNotification $line
    }
}
finally {
    if ($reader) { $reader.Dispose() }
    $stream.Dispose()
}
