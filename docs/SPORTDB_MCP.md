# SportDB MCP

## Objetivo

O MCP da SportDB permite que o Codex consulte a SportDB por ferramentas nativas, sem depender de chamadas manuais no terminal. Ele serve para diagnostico, validacao de jogos ao vivo, busca de partidas, estatisticas, eventos e odds expostas pela SportDB.

Importante: o MCP habilita o Codex local. O bot em producao continua precisando usar os endpoints REST/backend ja implementados. Ou seja, MCP nao substitui automaticamente a logica do Railway.

## Configuracao local

Arquivo:

```text
C:\Users\datab\.codex\config.toml
```

Bloco adicionado:

```toml
[mcp_servers.sportdbdotdev]
type = "remote"
url = "https://api.sportdb.dev/mcp/"
enabled = true

[mcp_servers.sportdbdotdev.http_headers]
X-API-Key = "<SPORTDB_API_KEY>"
```

Foi criado backup antes da alteracao:

```text
C:\Users\datab\.codex\config.toml.bak-sportdb
```

## Validacao

Health endpoint testado:

```text
https://api.sportdb.dev/mcp/health
```

Resultado:

```json
{"status":"ok"}
```

## Ferramentas esperadas

Quando o Codex recarregar a configuracao, o MCP deve expor ferramentas como:

- `flashscore_get_live`
- `flashscore_get_live_odds`
- `flashscore_get_match_stats`
- `flashscore_get_match_events`
- `flashscore_get_match_lineups`
- `flashscore_search`

## Observacao operacional

Se um jogo ao vivo voltar com placar/eventos, mas estatisticas de pressao zeradas, o bot nao deve considerar aquilo como estatistica acionavel. Essa protecao foi adicionada em `src/betbot/stats.py`: valores zerados nao contam como dados uteis para decisao.

