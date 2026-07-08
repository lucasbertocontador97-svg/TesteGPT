# LuquinhaBet Logic Reference

Este documento registra a logica aproveitada do LuquinhaBet para o EdgeBot AI.

## Ideias incorporadas

- API-Football como fonte de jogos ao vivo, estatisticas e eventos.
- Odds-API usada somente depois que o mercado foi escolhido.
- Pre-filtro por minuto: bloquear jogo muito cedo ou muito tarde.
- Motor matematico antes da IA.
- Poisson para estimar probabilidade de gols e escanteios.
- Bloqueio de jogo morto.
- Bloqueio por dados insuficientes.
- Confianca minima maior em jogos de alta variancia.
- Deduplicacao por chave de alerta.

## Ideias ainda nao implementadas

- Sportmonks.
- TheStatsAPI.
- Cartoes.
- Deep-links Bet365/Betano.
- Settlement avancado com lote e VOID apos 25 tentativas.
- Circuit breaker completo por janela movel.
- Backtest para desligar mercados ruins.
- Manus Value Scanner com EV real.

## Implementacao atual

O arquivo `src/betbot/deterministic.py` contem o primeiro motor matematico:

- calcula lambda de gols;
- calcula lambda de escanteios;
- aplica Poisson;
- avalia thresholds;
- retorna uma estrategia aprovada ou bloqueio.

Este motor nao substitui a IA. Ele funciona como porteiro: se a matematica nao aprova, a IA nao gera entrada oficial.
