# Goals Backtest

Estudo quantitativo de mercados de gols usando API-Football V3.

## Rodar

```powershell
cd research\goals_backtest
copy .env.example .env
# preencha API_FOOTBALL_KEY no .env
python run_goals_study.py diagnostic
python run_goals_study.py collect --from 2026-04-12 --to 2026-07-12 --max-fixtures 500
python run_goals_study.py backtest
```

Arquivos gerados em `output/`.
