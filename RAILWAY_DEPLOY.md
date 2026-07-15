# Deploy no Railway

Este bot deve rodar como um worker 24/7, sem URL publica. O arquivo `railway.json` ja define o comando:

```bash
python -m src.betbot.main
```

## Passo a passo

1. Acesse [railway.com](https://railway.com/).
2. Clique em `New Project`.
3. Escolha `Deploy from GitHub repo`.
4. Selecione o repo `lucasbertocontador97-svg/TesteGPT`.
5. Em `Variables`, adicione:

```env
TELEGRAM_BOT_TOKEN=
TELEGRAM_CHAT_ID=
TELEGRAM_WEBHOOK_PATH=telegram/webhook
ODDS_API_KEY=
API_FOOTBALL_KEY=
SPORTMONKS_API_TOKEN=
THESTATSAPI_KEY=
TOTALCORNER_TOKEN=
OPENAI_API_KEY=
DRY_RUN=false
POLL_SECONDS=180
MIN_ODD=1.80
MIN_CONFIDENCE=70
BOOKMAKERS=Bet365,Betano
SPORT=football
MAX_LIVE_EVENTS=20
ODDS_DETAIL_LIMIT=5
STARTUP_ALERT=true
ODDS_USE_MULTI=false
```

`OPENAI_MODEL` pode ficar vazio; o bot usa o padrao do codigo.

Observacao para operacao BFBM/Betfair: `MAX_LIVE_EVENTS` continua limitando algumas fontes gerais, mas o cruzamento BFBM + TotalCorner varre no minimo 150 jogos ao vivo por ciclo. Isso evita perder jogos que aparecem mais abaixo na lista da TotalCorner enquanto a Betfair ja tem mercado carregado.

## Evitar conflito do Telegram

Para eliminar `Conflict: terminated by other getUpdates request`, use webhook no Railway:

1. No servico do bot, abra `Settings`.
2. Em `Networking`, gere um dominio publico.
3. O Railway normalmente cria `RAILWAY_PUBLIC_DOMAIN` automaticamente. Se isso nao aparecer, adicione:

```env
TELEGRAM_WEBHOOK_URL=https://seu-dominio.up.railway.app
TELEGRAM_WEBHOOK_PATH=telegram/webhook
```

Com URL publica, o bot usa webhook e para de usar `getUpdates`.

## Historico persistente

Crie um Volume no servico do bot para preservar o banco SQLite entre deploys. Para operacao real com BFBM, trate isso como obrigatorio.

Sugestao de mount path:

```text
/data
```

Configure tambem a variavel abaixo para deixar o caminho explicito:

```env
DATABASE_PATH=/data/bot.sqlite3
```

Sem Volume, o bot roda, mas o historico, auditoria BFBM e performance por estrategia podem ser perdidos quando houver redeploy/restart. O bot avisa isso no startup, em `/status`, `/envcheck` e no endpoint:

```text
/bfbm/system-health.json?token=SEU_BFBM_TOKEN
```

## Confirmar que esta funcionando

Depois do deploy, abra os logs do servico. Voce deve ver ciclos de monitoramento ou mensagens de `Sem entrada`.

No Telegram, teste:

```text
/status
/last
/performance
/scan
/force_live_alert
/test_analysis_no_odds
/official_no_odds
/force_verified_entry
/debug_live_filters
/debug_sportmonks
/debug_api_football_stats
/debug_thestatsapi
/debug_totalcorner
/envcheck
```

## Observacoes

- Nao precisa gerar dominio publico.
- Nao use Cron Job do Railway para este bot; ele ja tem loop interno.
- Comece com `DRY_RUN=true` se quiser testar os logs antes de enviar alertas reais.
- Se a Odds-API retornar `429 Too Many Requests`, aumente `POLL_SECONDS` para `300` ou mais.
