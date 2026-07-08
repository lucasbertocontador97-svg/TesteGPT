# Bot Telegram de entradas ao vivo com IA

MVP em Python para monitorar jogos ao vivo, buscar odds na Odds-API.io, buscar estatisticas na API-Football, pedir uma decisao para IA e enviar entradas no Telegram quando a odd estiver acima de 1.80.

## O que ele faz

- Busca jogos ao vivo na Odds-API.io.
- Busca fixtures ao vivo e estatisticas na API-Football.
- Casa os jogos por nome dos times.
- Filtra mercados com odd minima configuravel.
- Aceita mercados de gols, escanteios/corners, over/under e asiaticos quando aparecem na resposta de odds.
- Usa IA para decidir se entra ou responde `NO_BET`.
- Envia alerta no Telegram.
- Salva historico em SQLite.
- Evita repetir a mesma entrada.
- Tem comandos `/status`, `/last` e `/performance`.
- Tem comando `/scan` para forcar uma varredura manual.
- Tem comando `/force_live_alert` para enviar um alerta de teste usando um jogo ao vivo real.
- Tem comando `/test_analysis_no_odds` para testar a analise usando apenas API-Football.
- Tem comando `/official_no_odds` para enviar uma entrada oficial da IA sem consultar odds.
- Tem comando `/force_verified_entry` para buscar a melhor entrada verificada sem odds.
- Tem comando `/debug_live_filters` para ver por que jogos ao vivo foram bloqueados.
- Tem comando `/debug_sportmonks` para ver se a Sportmonks esta retornando jogos e estatisticas.
- Tem comando `/debug_api_football_stats` para ver o retorno bruto resumido da API-Football.
- Tem comando `/debug_thestatsapi` para testar autenticação, jogos live e cobertura da TheStatsAPI.
- Tem comando `/envcheck` para conferir quais variaveis o deploy esta enxergando.
- Tenta liquidar entradas de gols e escanteios quando houver dados finais suficientes.
- Inclui documentacao oficial do EdgeBot AI em `docs/`.

## Instalar

```powershell
cd C:\Users\datab\Documents\Codex\2026-07-08\ten\outputs\betting_telegram_ai_bot
python -m venv .venv
.\.venv\Scripts\Activate.ps1
pip install -r requirements.txt
Copy-Item .env.example .env
```

Edite o arquivo `.env` e coloque suas chaves.

Para descobrir seu `TELEGRAM_CHAT_ID`, mande uma mensagem para o bot e rode:

```powershell
python -m src.betbot.chat_id
```

Depois preencha `TELEGRAM_CHAT_ID` no `.env`.

## Rodar

Primeiro teste sem enviar alerta real:

```powershell
python -m src.betbot.main once
```

Quando estiver tudo certo, mude `DRY_RUN=false` no `.env` e rode:

```powershell
python -m src.betbot.main
```

## Hospedar no Railway

O projeto ja inclui `railway.json`. Para hospedar 24/7, use o guia:

[RAILWAY_DEPLOY.md](RAILWAY_DEPLOY.md)

## Aviso importante

Isto e uma ferramenta de analise e alerta, nao garantia de lucro. Use stake baixa no inicio, acompanhe o historico e ajuste os filtros antes de apostar dinheiro real.
