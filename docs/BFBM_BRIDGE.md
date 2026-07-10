# Ponte Local BFBM

Esta ponte roda no PC onde o BFBM esta aberto.

Fluxo:

```text
Railway/TesteGPT -> ponte local -> BFBM -> Betfair -> Telegram
```

## URL para colocar no BFBM

Use esta URL em "Carregamento automatico de dicas":

```text
http://127.0.0.1:8787/tips.csv
```

## Iniciar manualmente

```powershell
cd C:\Users\datab\Documents\Codex\2026-07-08\ten\outputs\betting_telegram_ai_bot
powershell -ExecutionPolicy Bypass -File .\scripts\start_bfbm_bridge.ps1
```

## Ver status

Abra no navegador:

```text
http://127.0.0.1:8787/status
```

## O que ela faz

- Puxa as tips do Railway.
- Usa o feed completo do bot (`/bfbm/live-full.csv`), entao tambem recebe entradas criadas pelo Telegram/Railway.
- Mantem no maximo 4 tips.
- Remove duplicada por jogo/mercado.
- Garante odd minima `1.80`.
- Entrega CSV local para o BFBM.
- Monitora o log do BFBM.
- Avisa no Telegram quando o BFBM registrar aposta feita.

## Requisitos

- BFBM aberto.
- BFBM logado na Betfair.
- Estrategia corrigida iniciada.
- Mercados carregados no BFBM.
- BFBM apontando para `http://127.0.0.1:8787/tips.csv`.

## Scanner de mercados

Para o bot saber quais mercados o BFBM realmente esta vendo, o PC local precisa manter um export dos mercados em:

```text
C:\Users\datab\OneDrive\Desktop\EXPORTAR DADOS VISIVEIS.csv
C:\Users\datab\OneDrive\Desktop\EXPORTAR MERCADOS.csv
```

Depois rode em outro terminal:

```powershell
powershell -ExecutionPolicy Bypass -File .\scripts\start_bfbm_market_scanner.ps1
```

O scanner le esses CSVs a cada poucos segundos, salva um snapshot local e envia para o Railway em `/bfbm/markets/snapshot`. O arquivo `EXPORTAR DADOS VISIVEIS.csv` traz status, placar e tempo; o arquivo `EXPORTAR MERCADOS.csv` traz `EventId`, `MarketId`, `MarketType` e horario. O feed `/bfbm/live-full.csv` usa esse catalogo para ajustar nomes de evento, mercado e selecao para o mesmo texto que o BFBM usa.

O scanner nao decide odds. Ele apenas confirma que o mercado existe no BFBM; a odd minima continua protegida pelo `MinPrice` do CSV e pela estrategia do BFBM.
