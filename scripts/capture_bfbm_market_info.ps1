Add-Type -AssemblyName UIAutomationClient
Add-Type -AssemblyName UIAutomationTypes

$root = [System.Windows.Automation.AutomationElement]::RootElement
$titleCondition = New-Object System.Windows.Automation.PropertyCondition(
    [System.Windows.Automation.AutomationElement]::NameProperty,
    'Informações do mercado'
)
$window = $root.FindFirst([System.Windows.Automation.TreeScope]::Descendants, $titleCondition)

if (-not $window) {
    Write-Output (@{
        ok = $false
        error = 'Abra a janela Informações do mercado no BFBM e execute novamente.'
    } | ConvertTo-Json -Compress)
    exit 1
}

$editCondition = New-Object System.Windows.Automation.PropertyCondition(
    [System.Windows.Automation.AutomationElement]::ControlTypeProperty,
    [System.Windows.Automation.ControlType]::Edit
)
$edit = $window.FindFirst([System.Windows.Automation.TreeScope]::Descendants, $editCondition)

if (-not $edit) {
    Write-Output (@{
        ok = $false
        error = 'Campo de texto da janela não encontrado.'
    } | ConvertTo-Json -Compress)
    exit 1
}

$rawText = ''
$pattern = $null
if ($edit.TryGetCurrentPattern([System.Windows.Automation.ValuePattern]::Pattern, [ref]$pattern)) {
    $rawText = $pattern.Current.Value
}
elseif ($edit.TryGetCurrentPattern([System.Windows.Automation.TextPattern]::Pattern, [ref]$pattern)) {
    $rawText = $pattern.DocumentRange.GetText(-1)
}

if (-not $rawText) {
    Write-Output (@{
        ok = $false
        error = 'Texto da janela está vazio ou inacessível.'
    } | ConvertTo-Json -Compress)
    exit 1
}

$fields = @{}
foreach ($line in ($rawText -split "`r?`n")) {
    if ($line -match '^\s*([^:]+):\s*(.+?)\s*$') {
        $fields[$matches[1].Trim()] = $matches[2].Trim()
    }
}

Write-Output (@{
    ok = $true
    market_id = $fields['ID do mercado']
    event_id = $fields['ID do evento']
    event_name = $fields['Nome do evento']
    market_name = $fields['Nome do mercado']
    market_type = $fields['Tipo de mercado']
    start_time = $fields['Hora de início']
    raw = $rawText
} | ConvertTo-Json -Depth 4)
