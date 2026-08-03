# Auditoria BFBM Resultados

Este projeto só considera uma aposta como resultado real quando ela vem do BFBM/Betfair.

## Fonte da Verdade

1. A tip nasce no backend e entra no feed da ponte:
   - `GET /bfbm/live-full.csv?token=...&ids=1`
2. A ponte local entrega para o BFBM:
   - `http://127.0.0.1:8787/tips.csv?token=...`
3. O BFBM aposta.
4. A ponte consulta ordens Betfair:
   - `http://127.0.0.1:8787/betfair/orders.json?token=...`
5. A ponte envia as ordens para o backend:
   - `POST /api/bfbm/sync-orders`
   - Header obrigatório: `X-Sync-Token`
6. O backend grava em:
   - `bfbm_bet_notifications`

Se o passo 5 não acontecer, o sistema fica cego para GREEN/RED, lucro real, ROI e aprendizado.

## Como Ver Se Está Funcionando

Na janela da ponte precisa aparecer periodicamente algo parecido com:

```text
[sync] ordens enviadas: rows=... matched=... settled=...
```

Se aparecer apenas:

```text
[source] 0 tip(s) lidas do Railway.
```

isso só prova que a ponte está lendo tips. Não prova que está sincronizando resultados.

## Recuperar Histórico Quando A Ponte Falhar

Exporte o relatório de apostas do BFBM em CSV e rode:

```powershell
cd C:\TesteGPT\TesteGPT-main
.\.venv\Scripts\python.exe scripts\import_bfbm_report.py --csv "C:\CAMINHO\DO\RELATORIO.csv"
```

Para testar sem gravar:

```powershell
.\.venv\Scripts\python.exe scripts\import_bfbm_report.py --csv "C:\CAMINHO\DO\RELATORIO.csv" --dry-run
```

Para enviar o relatório para a produção usando o mesmo contrato da ponte:

```powershell
.\.venv\Scripts\python.exe scripts\import_bfbm_report.py `
  --csv "C:\CAMINHO\DO\RELATORIO.csv" `
  --sync-url "https://testegpt-production.up.railway.app/api/bfbm/sync-orders" `
  --sync-token "SEU_TOKEN_SYNC"
```

## O Que Foi Corrigido

- O script `scripts/import_bfbm_report.py` transforma o CSV real do BFBM em ordens liquidadas.
- Ele grava no banco local ou envia para `/api/bfbm/sync-orders`.
- Ele preserva Bet Id, stake, preço, lucro/prejuízo, status, estratégia e linha completa do mercado.

## Sinal De Alerta

Se o banco mostrar `bfbm_bet_notifications = 0`, o sistema ainda não tem resultado real registrado.

Nesse caso, uma destas coisas aconteceu:

- a ponte não está rodando;
- a ponte está usando script antigo;
- `BFBM_SYNC_TOKEN`/`BFBM_TOKEN` não bate com a produção;
- o BFBM não fez apostas;
- o relatório nunca foi importado.

## Aprendizado Automatico Antes De Enviar Tip

O bot agora usa os resultados liquidados reais do BFBM antes de gerar novas tips live.

Fonte usada:

- tabela `bfbm_bet_notifications`;
- lucro/prejuizo real;
- stake real;
- mercado;
- selecao;
- estrategia;
- linha bruta do BFBM quando a tip nao casou com um alerta salvo.

Endpoint de auditoria:

```text
/bfbm/learning.json?token=SEU_TOKEN_BFBM
```

Decisoes possiveis:

- `ALLOW`: nao existe amostra negativa suficiente para bloquear.
- `CAUTION`: existe historico ruim, mas ainda nao forte o bastante para bloquear.
- `BOOST`: existe historico positivo naquele mercado/selecao.
- `BLOCK`: o mercado/selecao ficou ruim o bastante para impedir a tip.

Regra importante:

O bloqueio nao usa apenas o nome geral da estrategia. Ele prioriza mercado e selecao.
Exemplo: se `Ambos marcam / Sim` esta muito negativo, ele pode ser bloqueado sem bloquear automaticamente `Over 2.5`.

Isso evita dois erros:

- continuar apostando em mercado claramente ruim;
- matar oportunidades boas porque outra familia de mercado estragou o resultado geral.
