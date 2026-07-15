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
