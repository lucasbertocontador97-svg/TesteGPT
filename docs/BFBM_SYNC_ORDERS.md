# Sincronizacao de Ordens do BFBM

Este endpoint recebe da ponte local/VPS as apostas casadas e liquidadas do BFBM/Betfair para o bot registrar lucro, prejuizo, green/red e reconciliar cada aposta com a tip original.

## Endpoint

```text
POST https://testegpt-production.up.railway.app/api/bfbm/sync-orders
```

Se voce estiver usando outro dominio de producao para o mesmo backend, por exemplo o dominio do Emergent, mantenha o mesmo caminho:

```text
POST https://smart-betting-bot-5.emergent.host/api/bfbm/sync-orders
```

## Seguranca

Configure uma chave secreta no Railway:

```text
BFBM_SYNC_TOKEN=uma_chave_grande_e_secreta
```

Toda requisicao deve enviar:

```text
X-Sync-Token: uma_chave_grande_e_secreta
Content-Type: application/json
```

Sem esse header, ou com chave errada, o endpoint retorna `401`. Se a variavel nao estiver configurada no Railway, retorna `503`.

## Formato aceito

O endpoint aceita diretamente o JSON que a ponte local expoe em:

```text
http://127.0.0.1:8787/betfair/orders.json
```

Exemplo:

```json
{
  "ok": true,
  "generated_at": "2026-07-16T10:00:00Z",
  "current": {
    "label": "current",
    "count": 1,
    "rows": [
      {
        "betId": "123",
        "marketId": "1.260000000",
        "selectionId": "47973",
        "price": 2.1,
        "size": 0.58,
        "side": "BACK",
        "status": "EXECUTION_COMPLETE",
        "placedDate": "2026-07-16T09:58:00Z"
      }
    ]
  },
  "cleared": {
    "label": "cleared",
    "count": 1,
    "rows": [
      {
        "betId": "123",
        "marketId": "1.260000000",
        "selectionId": "47973",
        "price": 2.1,
        "size": 0.58,
        "profit": 0.64,
        "side": "BACK",
        "status": "SETTLED",
        "placedDate": "2026-07-16T09:58:00Z",
        "settledDate": "2026-07-16T11:00:00Z"
      }
    ]
  }
}
```

Tambem aceita um formato simples com `rows`, mas o formato `current`/`cleared` e o recomendado.

## PowerShell pronto

Use isto em uma tarefa agendada no Windows, depois de trocar as duas chaves:

```powershell
$local = Invoke-RestMethod "http://127.0.0.1:8787/betfair/orders.json?hours=24&limit=1000&token=SUA_CHAVE_LOCAL_DA_PONTE"
$json = $local | ConvertTo-Json -Depth 8

Invoke-RestMethod -Method Post `
  -Uri "https://testegpt-production.up.railway.app/api/bfbm/sync-orders" `
  -Headers @{ "X-Sync-Token" = "SUA_CHAVE_BFBM_SYNC_TOKEN" } `
  -ContentType "application/json" `
  -Body $json
```

## Resposta

Resposta esperada:

```json
{
  "ok": true,
  "total_rows": 2,
  "matched": 2,
  "marked_bets": 1,
  "settled": 1,
  "unmatched": 0,
  "unmatched_samples": [],
  "reconciled": true
}
```

Campos:

- `total_rows`: total de ordens recebidas.
- `matched`: ordens que casaram com uma tip enviada pelo bot.
- `marked_bets`: tips marcadas como apostadas pelo BFBM.
- `settled`: tips liquidadas como `WON`, `LOST` ou `PUSH`.
- `unmatched`: ordens que nao encontraram tip correspondente.
- `unmatched_samples`: amostra para debug quando nao casar.
- `reconciled`: `true` quando houve casamento ou quando nao havia linhas.

## Como o casamento funciona

O bot casa as ordens usando:

```text
marketId + selectionId
```

Primeiro ele tenta achar uma tip em `alerts.betfair_market_id` e `alerts.betfair_selection_id`. Se nao encontrar, tenta pelo historico de exportacao do BFBM em `bfbm_export_audit`.

Quando a ordem vem liquidada com `profit`:

- `profit > 0`: marca `WON`.
- `profit < 0`: marca `LOST`.
- `profit = 0`: marca `PUSH`.

Isso alimenta os relatorios de green/red, lucro/prejuizo do dia e acumulados.
