# Betfair History API

Este projeto consegue consultar o historico da Betfair sem enviar apostas.

## O que esta liberado

- Apostas abertas/atuais: `listCurrentOrders`
- Apostas liquidadas: `listClearedOrders`
- P/L por mercado: `listMarketProfitAndLoss`

Isso permite cruzar os `betId` capturados pelo BFBM com o resultado oficial da Betfair.

## Variaveis necessarias

Configure no ambiente local:

```powershell
$env:BETFAIR_USERNAME="..."
$env:BETFAIR_PASSWORD="..."
$env:BETFAIR_APP_KEY="..."
$env:BETFAIR_CERT_PATH="C:\BetfairCert\client-2048.crt"
$env:BETFAIR_KEY_PATH="C:\BetfairCert\client-2048.key"
```

Nao coloque esses valores em arquivos versionados.

## Teste seguro

Consulta as ultimas 48 horas sem enviar aposta:

```powershell
.\.venv\Scripts\python.exe scripts\betfair_orders_check.py --hours 48 --limit 50 --json-out outputs\betfair_orders_last48h.json
```

Filtrar por uma aposta especifica:

```powershell
.\.venv\Scripts\python.exe scripts\betfair_orders_check.py --bet-id 435029488590 --hours 72
```

Filtrar por mercado:

```powershell
.\.venv\Scripts\python.exe scripts\betfair_orders_check.py --market-id 1.259933828 --hours 72
```

## Como usar no relatorio automatico

1. BFBM faz uma aposta.
2. A ponte captura `betId`, estrategia e snapshot da tip.
3. O script consulta Betfair por `betId`.
4. O sistema cruza:
   - estrategia;
   - evento;
   - mercado;
   - selecao;
   - stake;
   - odd;
   - status;
   - lucro/prejuizo.

Assim o bot consegue montar um historico confiavel de green/red usando a liquidacao oficial da Betfair.

## Observacoes

- `listCurrentOrders` mostra apostas ainda abertas ou nao liquidadas.
- `listClearedOrders` mostra apostas finalizadas/liquidadas.
- A Betfair nao sabe qual estrategia do BFBM gerou a aposta. Essa informacao precisa vir do log/ponte local do BFBM e ser cruzada pelo `betId`.
