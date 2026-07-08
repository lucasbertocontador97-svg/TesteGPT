# EdgeBot AI - Decision Engine v1.0

## Fluxo principal

1. Buscar jogos ao vivo na API-Football.
2. Buscar estatisticas do fixture.
3. Rejeitar jogos sem estatisticas acionaveis.
4. Aplicar filtros de risco.
5. Enviar jogo para IA escolher mercado e linha.
6. Se a IA responder `no bet`, parar.
7. Se odds estiverem habilitadas, consultar Odds-API apenas para o mercado escolhido.
8. Enviar entrada somente se todos os criterios forem satisfeitos.

## Estatisticas acionaveis

Um jogo tem estatisticas acionaveis quando ha pelo menos tres sinais relevantes entre:

- chutes totais;
- chutes no gol;
- escanteios;
- ataques;
- ataques perigosos;
- posse de bola.

Se isso nao existir, a decisao oficial deve ser bloqueada.

## Filtros eliminatorios

Bloquear entrada quando:

- nao ha jogo ao vivo;
- nao ha estatisticas detalhadas;
- nao ha linha definida;
- confianca abaixo do minimo;
- motivo nao cita dados concretos;
- jogo esta antes dos 25 minutos e nao ha pressao estatistica clara;
- jogo de alta variancia com confianca abaixo de 85%.

## Jogos de alta variancia

Exigir confianca minima de 85% em:

- amistosos;
- U20, U19, U21, U23;
- reservas;
- futebol feminino;
- jogos muito cedo;
- competicoes pouco confiaveis.

## Mercados permitidos

### Gols

Permitidos:

- Mais gols 0.5, 1.5, 2.5, 3.5;
- Menos gols 0.5, 1.5, 2.5, 3.5;
- linhas asiaticas 1.0, 2.0, 3.0 quando disponiveis.

### Escanteios

Permitidos:

- Mais escanteios 5.5, 6.5, 7.5, 8.5, 9.5;
- Menos escanteios 5.5, 6.5, 7.5, 8.5, 9.5;
- linhas asiaticas 7.0, 8.0, 9.0 quando disponiveis.

## Confiança

Padrao:

- minimo operacional: 70%;
- jogo cedo: 85%;
- jogo alta variancia: 85%;
- dados incompletos: bloquear.

## Stake

Regras:

- `baixa`: entrada aceita, mas com risco contextual;
- `media`: apenas com estatisticas fortes e mercado claro;
- `alta`: desabilitada por padrao no MVP;
- `sem entrada`: quando o cenario nao passa no filtro.

## Formato minimo de entrada

Toda entrada oficial precisa conter:

- mercado completo;
- linha;
- direcao;
- confianca;
- estatisticas usadas;
- motivo;
- status da odd.

Exemplo:

```text
ENTRADA OFICIAL
Mercado: Mais escanteios 8.5
Direcao: over
Confianca: 86%
Stake: baixa
Odd: conferir / 1.80+
Motivo: mandante com 6 escanteios, 18 ataques perigosos e pressao lateral constante.
```

