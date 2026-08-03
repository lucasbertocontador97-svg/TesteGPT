# Estrategia unica BFBM multi-mercado

Esta estrategia nao bloqueia mercados por nome. O objetivo e analisar todo mercado que estiver disponivel no BFBM/Betfair e permitir entrada somente quando existir:

1. mercado com ID real no BFBM/Betfair;
2. modelo matematico confiavel para aquele tipo de mercado;
3. leitura ao vivo coerente com as estatisticas;
4. odd real ou limite minimo compativel com valor esperado.

Mercados que ainda nao possuem modelo confiavel nao devem ser apostados por chute. Eles ficam como "sem modelo suficiente", nao como "mercado proibido".

## Filosofia

O bot deve disputar valor, nao volume cego.

Para cada entrada possivel, a logica precisa estimar:

- probabilidade propria;
- odd justa: `1 / probabilidade`;
- probabilidade implicita da odd: `1 / odd`;
- edge: `probabilidade - probabilidade_implicita`;
- EV: `(probabilidade * odd) - 1`;
- risco contextual;
- motivo da entrada.

Preferencia operacional: odds a partir de 2.30.

Exemplos:

- odd 2.30 exige mais de 43.48% de acerto para empatar;
- odd 2.50 exige mais de 40.00% de acerto para empatar;
- se a chance real estimada for 52% numa odd 2.30, existe valor teorico;
- se a chance real estimada for 35% numa odd 2.30, nao existe valor.

## Mercados

### Ja modelados hoje

O codigo atual consegue gerar sinais matematicos para:

- gols over/under por linha disponivel;
- over 0.5 HT/FT quando o placar e o minuto justificam;
- proximo gol via linha dinamica;
- BTTS sim/nao quando o mercado existe;
- escanteios over por linha disponivel.

Esses mercados nao sao permitidos automaticamente. Eles entram na lista de candidatos e depois passam por contexto, probabilidade e disponibilidade real no BFBM.

### Disponiveis no exportador, mas sem modelo seguro ainda

O BFBM consegue carregar outros mercados, como:

- resultado da partida;
- chance dupla;
- empate anula;
- handicap asiatico;
- placar correto;
- time a marcar;
- cartoes;
- mercados combinados.

Eles so devem entrar em producao quando houver uma funcao de probabilidade propria para cada selecao. Sem isso, o bot pode ate reconhecer o mercado, mas nao deve apostar, porque nao ha como medir edge com seriedade.

## Regra de decisao

Uma tip so deve sair quando:

1. O jogo esta ao vivo.
2. O mercado existe no catalogo/ponte do BFBM.
3. O mercado tem EventId, MarketId e SelectionId reais.
4. O motor consegue estimar probabilidade para a selecao.
5. A odd real atende ao minimo configurado e compensa a probabilidade.
6. O contexto ao vivo confirma a leitura.

Se qualquer etapa falhar, a tip deve ser rejeitada com motivo claro.

## Aprendizado por resultado

Toda entrada precisa ser classificada depois do resultado:

1. boa analise + green;
2. boa analise + red por variancia normal;
3. analise ruim + green por sorte;
4. analise ruim + red.

O sistema nao deve concluir que todo green foi boa analise nem que todo red foi erro. O aprendizado deve procurar padroes de situacao ruim dentro de cada mercado, sem banir o mercado inteiro automaticamente.

## Regra anti-falha

Nao inventar probabilidade onde nao existe modelo.
Nao mandar entrada sem ID real.
Nao mandar entrada so porque o mercado existe.
Nao bloquear mercado para sempre por resultado passado.
Bloquear somente situacoes ruins e falta de edge.
