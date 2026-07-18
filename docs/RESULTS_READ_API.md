# API de Resultados BFBM

Esta API permite que outro sistema leia os resultados reais das apostas do BFBM/Betfair ja sincronizadas no TesteGPT.

Ela e somente leitura. Nao cria aposta, nao cria tip e nao consulta a Betfair diretamente.

Importante: esta API so retorna dados depois que a ponte local/VPS, ou outro writer autorizado, envia as ordens reais para:

```text
POST /api/bfbm/sync-orders
```

Se `/api/results/today` voltar `bets: 0`, significa que ainda nao houve aposta sincronizada para o dia, ou que o writer de ordens nao esta alimentando a base.

## Autenticacao

Configure no Railway:

```env
RESULTS_API_KEY=coloque_uma_chave_forte_aqui
```

Toda requisicao deve enviar:

```http
X-Api-Key: <RESULTS_API_KEY>
```

Tambem existe fallback por query string para teste manual:

```text
?token=<RESULTS_API_KEY>
```

Prefira o header em producao.

## Base URL

```text
https://testegpt-production.up.railway.app
```

## Resultado do Dia

Consulta o dia atual no horario de Sao Paulo:

```http
GET /api/results/today
X-Api-Key: <RESULTS_API_KEY>
```

Consulta um dia especifico:

```http
GET /api/results/day?day=2026-07-15
X-Api-Key: <RESULTS_API_KEY>
```

Alias equivalente:

```http
GET /api/results?date=2026-07-15
X-Api-Key: <RESULTS_API_KEY>
```

## Resultado do Mes

```http
GET /api/results/month?month=2026-07
X-Api-Key: <RESULTS_API_KEY>
```

## Mudancas Incrementais

Use para outro sistema sincronizar sem reler tudo:

```http
GET /api/results/changes?since=<cursor>&limit=200
X-Api-Key: <RESULTS_API_KEY>
```

Na primeira chamada, omita `since`.

A resposta retorna `next_cursor`. Guarde esse valor e envie na proxima chamada.

## Diagnostico da Sincronizacao

Use este endpoint quando `/api/results/today` estiver zerado e voce precisar descobrir se o problema esta na ponte, no writer de ordens, no casamento por IDs ou na liquidacao:

```http
GET /api/results/diagnostics?limit=20
X-Api-Key: <RESULTS_API_KEY>
```

Resposta resumida:

```json
{
  "ok": true,
  "stats": {
    "total_orders": 10,
    "with_profit": 6,
    "matched_alerts": 5,
    "matched_settled": 4,
    "last_received_at": "2026-07-18 10:00:00",
    "last_order_at": "2026-07-18T12:55:00Z"
  },
  "today": {
    "orders_today": 3,
    "with_profit_today": 2,
    "matched_today": 2
  },
  "recent_orders": [],
  "recent_unmatched_orders": []
}
```

Como interpretar:

| Cenario | Significado |
|---|---|
| `total_orders = 0` | A ponte/local writer nao esta enviando ordens para o Railway. |
| `total_orders > 0` e `with_profit = 0` | As ordens chegam, mas ainda sem lucro/prejuizo liquidado. |
| `with_profit > 0` e `matched_alerts = 0` | As ordens chegam, mas nao estao casando com tips por `marketId + selectionId`. |
| `matched_alerts > 0` e `matched_settled = 0` | As apostas casaram, mas ainda nao houve liquidacao com `profit`. |
| `matched_settled > 0` | Ja existe resultado real para aparecer nos relatorios. |

## Formato da Resposta

```json
{
  "ok": true,
  "source": "teste_gpt_bfbm_results",
  "generated_at": "2026-07-15T18:40:00-03:00",
  "day": "2026-07-15",
  "summary": {
    "bets": 3,
    "green": 2,
    "red": 1,
    "void": 0,
    "profit": 18.5,
    "staked": 45.0,
    "roi_percent": 41.11,
    "win_rate_percent": 66.67,
    "by_strategy": [
      {
        "strategy": "BTTS",
        "bets": 1,
        "green": 1,
        "red": 0,
        "void": 0,
        "profit": 21.0,
        "staked": 15.0,
        "roi_percent": 140.0
      }
    ]
  },
  "bets": [
    {
      "bet_id": "434...",
      "result": "GREEN",
      "profit": 21.0,
      "stake": 15.0,
      "price": 2.4,
      "strategy": "BTTS",
      "event_name": "Time A x Time B",
      "market": "Ambos os times marcam?",
      "selection": "Sim",
      "market_id": "1.260...",
      "selection_id": "30246",
      "placed_at": "2026-07-15 18:20:00",
      "settled_at": "2026-07-15 20:10:00",
      "cursor": "2026-07-15 20:10:00|123"
    }
  ]
}
```

## Codigos de Erro

| Codigo | Significado |
|---|---|
| `200` | Consulta realizada com sucesso. |
| `401` | Chave ausente ou incorreta. |
| `503` | `RESULTS_API_KEY` nao configurada no Railway. |

## Prompt Pronto Para Outra IA

Voce e um consumidor somente leitura dos resultados do bot TesteGPT.

Nao consulte Betfair diretamente. Nao envie apostas. Nao altere tips. Sua unica fonte para resultados reais e a API abaixo:

Base URL:

```text
https://testegpt-production.up.railway.app
```

Autenticacao:

```http
X-Api-Key: <RESULTS_API_KEY>
```

Endpoints:

```http
GET /api/results/today
GET /api/results/day?day=YYYY-MM-DD
GET /api/results/month?month=YYYY-MM
GET /api/results/changes?since=<cursor>&limit=200
```

Regras:

- Para saldo do dia, use `/api/results/today`.
- Para acumulado mensal, use `/api/results/month?month=YYYY-MM`.
- Para notificacoes novas, use `/api/results/changes`.
- Guarde o `next_cursor` retornado por `/api/results/changes`.
- Classifique `result=GREEN` como aposta ganha.
- Classifique `result=RED` como aposta perdida.
- Classifique `result=VOID` como aposta anulada/sem lucro.
- Use `profit` como lucro/prejuizo real em reais.
- Use `stake` como valor investido/correspondido.
- Use `strategy` para explicar de onde veio a entrada.
- Nunca calcule lucro por odd se `profit` ja veio preenchido. O `profit` e o valor real da Betfair/BFBM.

Exemplo de chamada:

```bash
curl -H "X-Api-Key: <RESULTS_API_KEY>" \
  "https://testegpt-production.up.railway.app/api/results/today"
```

Exemplo de rotina:

1. A cada 5 minutos, chamar `/api/results/changes?since=<cursor>&limit=200`.
2. Para cada nova aposta liquidada:
   - se `result=GREEN`, informar ganho;
   - se `result=RED`, informar prejuizo;
   - se `result=VOID`, informar aposta anulada.
3. Depois atualizar saldo do dia via `/api/results/today`.
4. A cada 2 dias, atualizar acumulado mensal via `/api/results/month?month=YYYY-MM`.
