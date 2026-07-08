# EdgeBot AI - Architecture v1.0

## Objetivo

O EdgeBot AI e um bot de analise ao vivo para futebol. O sistema deve identificar possiveis mercados de entrada com base em estatisticas reais do jogo, proteger capital e evitar recomendacoes quando os dados forem insuficientes.

O bot nao deve existir para gerar volume de sinais. Ele deve existir para filtrar cenarios.

## Principios

1. Sem entrada e uma decisao valida.
2. A IA nao deve decidir usando apenas placar.
3. A API-Football e a fonte primaria para jogos ao vivo e estatisticas.
4. A Odds-API e usada somente para confirmar preco minimo quando necessario.
5. Uma entrada oficial precisa ter mercado, direcao, linha, confianca, motivo e dados usados.
6. Quando estatisticas detalhadas estiverem indisponiveis, o sistema deve rejeitar a entrada.
7. Jogos de alta variancia exigem criterio maior: amistosos, base, reservas, feminino e jogos muito cedo.

## Camadas

### 1. Coleta

Responsavel por buscar:

- jogos ao vivo;
- minuto;
- placar;
- estatisticas;
- odds quando o motor ja escolheu o mercado.

### 2. Normalizacao

Responsavel por transformar respostas das APIs em objetos internos:

- `GameSnapshot`;
- `MarketIdea`;
- `MarketOption`;
- `Decision`.

### 3. Filtros obrigatorios

Executados antes da IA ou antes do envio:

- jogo precisa estar ao vivo;
- estatisticas precisam ser acionaveis;
- mercado precisa ter linha;
- confianca minima precisa ser respeitada;
- odds precisam ser verificadas quando o modo exigir odds.

### 4. IA

A IA escolhe o mercado com base no jogo, nao na odd.

A IA pode escolher:

- `goals + over`;
- `goals + under`;
- `corners + over`;
- `corners + under`;
- `no bet`.

### 5. Confirmacao de odds

Quando habilitada, a Odds-API confirma:

- casa;
- mercado;
- selecao;
- linha;
- odd minima.

### 6. Entrega

O Telegram envia apenas sinais que passaram pelos filtros. Toda mensagem deve mostrar:

- jogo;
- minuto;
- placar;
- mercado e linha;
- confianca;
- stake;
- estatisticas usadas;
- motivo.

## Filosofia de protecao do capital

O EdgeBot deve preferir perder uma oportunidade a enviar uma entrada fraca.

Entradas devem ser bloqueadas quando:

- faltam estatisticas;
- o motivo e generico;
- a decisao depende so do placar;
- a linha nao foi definida;
- o jogo e muito cedo sem pressao clara;
- o jogo e de alta variancia e a confianca nao e alta.

