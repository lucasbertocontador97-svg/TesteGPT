# Migracao da ponte BFBM para um VPS Windows

Este guia leva o BFBM + ponte local para outro computador/VPS Windows.

## O que vai rodar no VPS

- BFBM instalado e logado na Betfair.
- Certificado Betfair instalado no VPS.
- Ponte local TesteGPT em `http://127.0.0.1:8787`.
- BFBM importando tips por URL local:

```text
http://127.0.0.1:8787/tips.csv?token=SUA_CHAVE_DA_PONTE
```

## Arquivos/pastas para levar

Leve a pasta do projeto para o VPS:

```text
C:\TesteGPT
```

O jeito mais simples:

1. Baixar/clonar o repositorio no VPS; ou
2. Copiar a pasta atual por ZIP/RDP.

Nao copie arquivos com senha para lugares publicos.

## Instalar Python

No VPS, instale Python 3.11+ e depois rode:

```powershell
cd C:\TesteGPT
python -m venv .venv
.\.venv\Scripts\python.exe -m pip install --upgrade pip
.\.venv\Scripts\python.exe -m pip install -r requirements.txt
```

## Certificado Betfair

No VPS, crie ou copie o certificado para:

```text
C:\BetfairCert\client-2048.crt
C:\BetfairCert\client-2048.key
```

Depois confirme no site da Betfair que o certificado esta carregado e o acesso ao programa automatizado esta ligado.

## Script da ponte no VPS

Crie um arquivo local no VPS:

```text
C:\TesteGPT\scripts\start_bfbm_bridge_vps.ps1
```

Use este modelo e preencha os valores:

```powershell
$ErrorActionPreference = "Stop"

$RepoRoot = "C:\TesteGPT"
$Python = Join-Path $RepoRoot ".venv\Scripts\python.exe"
$Bridge = Join-Path $RepoRoot "scripts\bfbm_bridge.py"

$BridgeApiToken = "SUA_CHAVE_DA_PONTE"
$RailwayToken = "SEU_TOKEN_BFBM_DO_RAILWAY"
$RailwayBase = "https://testegpt-production.up.railway.app"

$SourceUrl = "$RailwayBase/bfbm/live-full.csv?token=$RailwayToken"
$NotifyUrl = "$RailwayBase/bfbm/notify-bet?token=$RailwayToken"
$ResultNotifyUrl = "$RailwayBase/bfbm/notify-bet-result?token=$RailwayToken"

$env:BETFAIR_USERNAME = "SEU_USUARIO_BETFAIR"
$env:BETFAIR_PASSWORD = "SUA_SENHA_BETFAIR"
$env:BETFAIR_APP_KEY = "SUA_APP_KEY_BETFAIR"
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
```

## Testar a ponte

Abra PowerShell no VPS:

```powershell
cd C:\TesteGPT
powershell -ExecutionPolicy Bypass -File .\scripts\start_bfbm_bridge_vps.ps1
```

Em outro PowerShell:

```powershell
Invoke-WebRequest -UseBasicParsing "http://127.0.0.1:8787/status"
Invoke-WebRequest -UseBasicParsing "http://127.0.0.1:8787/tips.csv?token=SUA_CHAVE_DA_PONTE"
Invoke-WebRequest -UseBasicParsing "http://127.0.0.1:8787/betfair/orders.json?token=SUA_CHAVE_DA_PONTE&hours=12&limit=5"
```

Se `/betfair/orders.json` responder, o certificado e login Betfair estao funcionando.

## Configurar o BFBM

No BFBM do VPS:

1. Configure a estrategia validada.
2. Configure importacao automatica de tips.
3. URL:

```text
http://127.0.0.1:8787/tips.csv?token=SUA_CHAVE_DA_PONTE
```

4. Ative recarregamento automatico a cada poucos segundos.
5. Inicie a estrategia.

## Iniciar automaticamente com o Windows

Crie uma tarefa no Agendador de Tarefas:

- Acionar: ao fazer logon.
- Programa:

```text
powershell.exe
```

- Argumentos:

```text
-ExecutionPolicy Bypass -File "C:\TesteGPT\scripts\start_bfbm_bridge_vps.ps1"
```

Tambem configure o BFBM para iniciar com Windows, se possivel.

## Checklist final

Antes de desligar o PC antigo:

- BFBM aberto no VPS.
- Estrategia iniciada no VPS.
- Importacao automatica ligada no VPS.
- `http://127.0.0.1:8787/status` mostra `ok: true`.
- `/betfair/orders.json` responde sem erro.
- O PC antigo nao deve continuar com o BFBM apostando ao mesmo tempo, para evitar duplicidade.

## Regra importante

Nao deixe a ponte rodando em dois computadores importando as mesmas tips ao mesmo tempo. Use apenas um BFBM operacional por vez.
