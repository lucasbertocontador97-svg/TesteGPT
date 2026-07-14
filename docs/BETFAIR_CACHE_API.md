# Betfair Cache API

Esta API transforma o TesteGPT na base central de mercados Betfair para outros sistemas.

A ideia e simples:

```text
Betfair/BFBM local -> TesteGPT/Railway -> outros sistemas
```

O outro sistema nao precisa consultar a Betfair. Ele consulta o TesteGPT, que devolve o ultimo snapshot Betfair ja salvo no banco.

## Objetivo

- Reduzir chamadas diretas na Betfair.
- Evitar duplicidade de consulta em varios sistemas.
- Centralizar os dados de `EventId`, `MarketId`, `SelectionId`, nomes de mercados e odds.
- Permitir que outros sistemas usem os mesmos mercados que o BFBM/bot ja conhece.

## Importante

O endpoint abaixo e somente leitura.

Ele nao envia aposta, nao cria tips e nao consulta a Betfair em tempo real. Ele apenas le o cache salvo pelo bot.

## Configuracao

Cadastre esta variavel no ambiente onde o bot roda:

```env
BETFAIR_CACHE_API_KEY=coloque_uma_chave_forte_aqui
```

No Railway:

1. Abra o projeto.
2. Va em `Variables`.
3. Adicione `BETFAIR_CACHE_API_KEY`.
4. Faca redeploy.

Nao coloque a chave real dentro do repositorio.

## Endpoint

```http
GET /api/betfair/cache
X-Api-Key: <BETFAIR_CACHE_API_KEY>
```

URL de producao:

```text
https://testegpt-production.up.railway.app/api/betfair/cache
```

## Exemplo PowerShell

```powershell
Invoke-RestMethod `
  -Uri "https://testegpt-production.up.railway.app/api/betfair/cache?max_age_minutes=15&limit=5000" `
  -Headers @{ "X-Api-Key" = "SUA_CHAVE_AQUI" }
```

## Exemplo curl

```bash
curl -H "X-Api-Key: SUA_CHAVE_AQUI" \
  "https://testegpt-production.up.railway.app/api/betfair/cache?max_age_minutes=15&limit=5000"
```

## Parametros opcionais

| Parametro | Padrao | Maximo | Descricao |
|---|---:|---:|---|
| `max_age_minutes` | `15` | `1440` | Idade maxima dos mercados retornados. |
| `limit` | `5000` | `10000` | Quantidade maxima de mercados retornados. |
| `market_type` | vazio | - | Filtra por tipo Betfair. Ex.: `MATCH_ODDS`, `OVER_UNDER_25`. |
| `event` | vazio | - | Filtra pelo nome do jogo/evento. |
| `include_raw` | `0` | - | Se `1`, inclui o JSON bruto salvo com runners/selecoes. |

## Exemplos de filtros

### Apenas Match Odds

```text
/api/betfair/cache?market_type=MATCH_ODDS
```

### Apenas mercados de Over/Under 2.5

```text
/api/betfair/cache?market_type=OVER_UNDER_25
```

### Buscar um jogo pelo nome

```text
/api/betfair/cache?event=Flamengo
```

### Trazer runners/selecoes completos

```text
/api/betfair/cache?include_raw=1
```

Use `include_raw=1` quando o outro sistema precisar ler runners, odds ou dados internos que nao aparecem nas colunas normalizadas.

## Resposta

```json
{
  "ok": true,
  "source": "bfbm_betfair_cache",
  "count": 2,
  "max_age_minutes": 15,
  "filters": {
    "market_type": "",
    "event": "",
    "include_raw": false
  },
  "markets": [
    {
      "event_name": "Exemplo FC x Visitante FC",
      "market_name": "Resultado da partida",
      "event_id": "35800000",
      "market_id": "1.259000000",
      "market_type": "MATCH_ODDS",
      "status": "OPEN",
      "start_time": "2026-07-14 20:00",
      "live_score": "0 - 0",
      "live_time": "12'",
      "favorite": "Exemplo FC",
      "winner": "",
      "total_matched": "R$ 10.000,00",
      "source_age_seconds": 4
    }
  ]
}
```

Os campos podem variar conforme o dado que chegou do BFBM/publisher local.

## Codigos de erro

| Status | Motivo |
|---:|---|
| `200` | Consulta autorizada e respondida. |
| `401` | Chave ausente ou incorreta no header `X-Api-Key`. |
| `503` | `BETFAIR_CACHE_API_KEY` nao foi configurada no ambiente. |

## Frequencia recomendada

O outro sistema pode consultar a cada `5` a `10` segundos.

Essa consulta nao chama a Betfair. Mesmo assim, evite polling agressivo sem necessidade. A resposta tem:

```http
Cache-Control: private, max-age=5
```

Ou seja, o outro sistema pode tratar a resposta como valida por aproximadamente 5 segundos.

## Contrato para outro sistema

O outro sistema deve:

1. Consultar `GET /api/betfair/cache`.
2. Enviar a chave no header `X-Api-Key`.
3. Usar `max_age_minutes` baixo para dados ao vivo, normalmente `5` a `15`.
4. Nao chamar Betfair se o dado ja existir neste endpoint.
5. Ignorar mercados sem `event_id`, `market_id` ou `selection_id` quando precisar operar automaticamente.
6. Usar `include_raw=1` somente quando precisar dos runners/selecoes completas.

## Fluxo operacional recomendado

```text
1. Publisher local consulta Betfair/BFBM.
2. Publisher envia snapshot ao TesteGPT.
3. TesteGPT salva no banco.
4. Bot usa esse cache para casar tips.
5. Sistema externo consulta /api/betfair/cache.
6. Sistema externo decide/mostra/processa sem chamar Betfair.
```

## Seguranca

- Nao use query string para a chave.
- Use sempre header `X-Api-Key`.
- Nao salve a chave em planilha.
- Se a chave vazar, gere outra e altere `BETFAIR_CACHE_API_KEY` no Railway.
- Este endpoint e somente leitura, mas ainda assim expoe dados operacionais do bot.

## Limites do endpoint

Este endpoint nao garante que um mercado ainda esteja aberto neste exato milissegundo. Ele garante apenas que o mercado estava no ultimo snapshot recebido.

Para reduzir risco:

- use `max_age_minutes` baixo;
- prefira snapshots com `source_age_seconds` baixo;
- o BFBM ainda deve validar odd, mercado aberto e disponibilidade antes da aposta.

## Checklist de deploy

Antes de usar em producao:

- `BETFAIR_CACHE_API_KEY` cadastrada na Railway.
- Redeploy feito.
- Publisher local alimentando o bot.
- `/api/betfair/cache` respondendo `200` com a chave correta.
- Outro sistema configurado para consultar o TesteGPT, nao a Betfair.
