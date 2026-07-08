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
ODDS_API_KEY=
API_FOOTBALL_KEY=
OPENAI_API_KEY=
DRY_RUN=false
POLL_SECONDS=45
MIN_ODD=1.80
MIN_CONFIDENCE=70
BOOKMAKERS=Bet365,Betano
SPORT=football
MAX_LIVE_EVENTS=20
```

`OPENAI_MODEL` pode ficar vazio; o bot usa o padrao do codigo.

## Historico persistente

Crie um Volume no servico do bot para preservar o banco SQLite entre deploys.

Sugestao de mount path:

```text
/data
```

Quando o Railway tiver `RAILWAY_VOLUME_MOUNT_PATH`, o bot salva automaticamente o banco em:

```text
$RAILWAY_VOLUME_MOUNT_PATH/bot.sqlite3
```

Sem Volume, o bot roda, mas o historico pode ser perdido quando houver redeploy.

## Confirmar que esta funcionando

Depois do deploy, abra os logs do servico. Voce deve ver ciclos de monitoramento ou mensagens de `Sem entrada`.

No Telegram, teste:

```text
/status
/last
/performance
```

## Observacoes

- Nao precisa gerar dominio publico.
- Nao use Cron Job do Railway para este bot; ele ja tem loop interno.
- Comece com `DRY_RUN=true` se quiser testar os logs antes de enviar alertas reais.
