# API local da ponte BFBM

A ponte local roda no computador que tem acesso ao BFBM e ao certificado da Betfair.

Base local:

```text
http://127.0.0.1:8787
```

Todas as rotas protegidas usam query string:

```text
?token=SUA_CHAVE_DA_PONTE
```

## Tips para o BFBM

```text
GET /tips.csv?token=SUA_CHAVE_DA_PONTE
```

Retorna o CSV importado pelo BFBM.

## Status da ponte

```text
GET /status
```

Mostra se a ponte esta lendo o Railway e quantas tips estao ativas.

## Ordens/apostas Betfair

```text
GET /betfair/orders.json?token=SUA_CHAVE_DA_PONTE&hours=24&limit=50
```

Consulta a Betfair pelo certificado local e retorna:

- ordens atuais/em aberto;
- ordens liquidadas;
- lucro/prejuizo real nas liquidadas.

Parametros opcionais:

```text
hours=24
limit=50
betId=123
marketId=1.260000000
```

Exemplo de resposta:

```json
{
  "ok": true,
  "generated_at": "2026-07-15T16:30:00Z",
  "window": {
    "from": "2026-07-14T16:30:00Z",
    "to": "2026-07-15T16:30:00Z"
  },
  "current": {
    "label": "current",
    "count": 0,
    "moreAvailable": false,
    "totalProfit": 0,
    "rows": []
  },
  "cleared": {
    "label": "cleared",
    "count": 1,
    "moreAvailable": false,
    "totalProfit": 4.2,
    "rows": [
      {
        "betId": "435000000000",
        "marketId": "1.260000000",
        "selectionId": 47973,
        "handicap": 0,
        "price": 1.8,
        "size": 15,
        "profit": 12,
        "side": "BACK",
        "status": "SETTLED",
        "placedDate": "2026-07-15T15:00:00Z",
        "settledDate": "2026-07-15T16:00:00Z",
        "persistenceType": "LAPSE",
        "orderType": "LIMIT"
      }
    ]
  }
}
```

Observacao: essa rota nao envia apostas. Ela apenas consulta historico/ordens da Betfair.

## Notificacao automatica de resultado

Quando iniciada com `--result-notify-url`, a ponte consulta as ordens liquidadas da Betfair em loop e notifica o bot no Railway sempre que encontrar uma aposta nova resolvida.

Exemplo de inicializacao:

```text
python scripts/bfbm_bridge.py ^
  --result-notify-url "https://SEU_RAILWAY/bfbm/notify-bet-result?token=SEU_TOKEN" ^
  --orders-poll-seconds 60
```

Comportamento:

- no primeiro ciclo, a ponte marca as apostas liquidadas antigas como ja vistas;
- depois disso, cada nova aposta liquidada gera uma mensagem no Telegram;
- a mensagem mostra GREEN, RED ou VOID;
- tambem mostra lucro/prejuizo individual e lucro/prejuizo acumulado do dia;
- a ponte guarda os `betId` ja notificados para nao repetir mensagem.

Endpoint no Railway usado pela ponte:

```text
GET /bfbm/notify-bet-result?token=SEU_TOKEN&bet_id=...&profit=...&day_profit=...
```

Esse endpoint e interno da ponte. O BFBM nao precisa chamar ele diretamente.
