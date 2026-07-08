from __future__ import annotations

import json
import sqlite3
from pathlib import Path
from typing import Any

from .models import Decision, GameSnapshot


class Storage:
    def __init__(self, path: Path) -> None:
        self.path = path
        self.conn = sqlite3.connect(path)
        self.conn.row_factory = sqlite3.Row
        self._init()

    def close(self) -> None:
        self.conn.close()

    def _init(self) -> None:
        self.conn.executescript(
            """
            create table if not exists alerts (
                id integer primary key autoincrement,
                created_at datetime default current_timestamp,
                event_id text not null,
                fixture_id integer,
                home text not null,
                away text not null,
                minute integer,
                market text not null,
                selection text not null,
                bookmaker text not null,
                odd real not null,
                line real,
                confidence integer not null,
                reason text not null,
                stake text not null,
                alert_key text not null unique,
                status text not null default 'SENT',
                user_action text not null default 'PENDING',
                result_note text,
                settled_at datetime
            );
            create index if not exists idx_alerts_status on alerts(status);
            """
        )
        columns = {row["name"] for row in self.conn.execute("pragma table_info(alerts)").fetchall()}
        if "user_action" not in columns:
            self.conn.execute("alter table alerts add column user_action text not null default 'PENDING'")
        self.conn.execute("create index if not exists idx_alerts_user_action on alerts(user_action)")
        self.conn.commit()

    def seen_alert(self, alert_key: str) -> bool:
        row = self.conn.execute("select 1 from alerts where alert_key = ?", (alert_key,)).fetchone()
        return row is not None

    @staticmethod
    def _market_group(market: str) -> str:
        lowered = str(market or "").lower()
        if any(word in lowered for word in ("corner", "escanteio")):
            return "corners"
        if any(word in lowered for word in ("goal", "gol")):
            return "goals"
        return lowered.strip()

    @staticmethod
    def _same_line(left: Any, right: Any) -> bool:
        if left is None and right is None:
            return True
        if left is None or right is None:
            return False
        try:
            return abs(float(left) - float(right)) <= 0.01
        except (TypeError, ValueError):
            return str(left) == str(right)

    def seen_similar_alert(self, game: GameSnapshot, decision: Decision) -> bool:
        if game.fixture_id is None:
            return False
        rows = self.conn.execute(
            """
            select market, selection, line
            from alerts
            where fixture_id = ?
              and status in ('SENT', 'WON', 'LOST', 'PUSH')
            """,
            (game.fixture_id,),
        ).fetchall()
        wanted_market = self._market_group(decision.market)
        wanted_selection = str(decision.selection or "").lower()
        for row in rows:
            if self._market_group(row["market"]) != wanted_market:
                continue
            if str(row["selection"] or "").lower() != wanted_selection:
                continue
            if self._same_line(row["line"], decision.line):
                return True
        return False

    def _alert_id(self, alert_key: str | None) -> int | None:
        if not alert_key:
            return None
        row = self.conn.execute("select id from alerts where alert_key = ?", (alert_key,)).fetchone()
        return int(row["id"]) if row else None

    def save_alert(self, game: GameSnapshot, decision: Decision) -> int | None:
        self.conn.execute(
            """
            insert or ignore into alerts (
                event_id, fixture_id, home, away, minute, market, selection, bookmaker,
                odd, line, confidence, reason, stake, alert_key
            ) values (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
            """,
            (
                game.event_id,
                game.fixture_id,
                game.home,
                game.away,
                game.minute,
                decision.market,
                decision.selection,
                decision.bookmaker,
                decision.odd,
                decision.line,
                decision.confidence,
                decision.reason,
                decision.stake,
                decision.alert_key,
            ),
        )
        self.conn.commit()
        return self._alert_id(decision.alert_key)

    def save_manual_alert(self, game: GameSnapshot, decision: Decision) -> int | None:
        self.conn.execute(
            """
            insert or ignore into alerts (
                event_id, fixture_id, home, away, minute, market, selection, bookmaker,
                odd, line, confidence, reason, stake, alert_key
            ) values (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
            """,
            (
                game.event_id,
                game.fixture_id,
                game.home,
                game.away,
                game.minute,
                decision.market,
                decision.selection,
                decision.bookmaker,
                decision.odd,
                decision.line,
                decision.confidence,
                decision.reason,
                decision.stake,
                decision.alert_key,
            ),
        )
        self.conn.commit()
        return self._alert_id(decision.alert_key)

    def last_alerts(self, limit: int = 5) -> list[dict[str, Any]]:
        rows = self.conn.execute("select * from alerts order by id desc limit ?", (limit,)).fetchall()
        return [dict(row) for row in rows]

    def pending_alerts(self) -> list[dict[str, Any]]:
        rows = self.conn.execute(
            "select * from alerts where status = 'SENT' and user_action = 'BET' and fixture_id is not null and odd > 0"
        ).fetchall()
        return [dict(row) for row in rows]

    def set_user_action(self, alert_id: int, action: str) -> bool:
        action = action.upper()
        if action not in {"BET", "IGNORED"}:
            return False
        cursor = self.conn.execute("update alerts set user_action = ? where id = ?", (action, alert_id))
        self.conn.commit()
        return cursor.rowcount > 0

    def settle_alert(self, alert_id: int, status: str, note: str) -> None:
        self.conn.execute(
            "update alerts set status = ?, result_note = ?, settled_at = current_timestamp where id = ?",
            (status, note, alert_id),
        )
        self.conn.commit()

    def performance(self) -> dict[str, Any]:
        rows = self.conn.execute("select status, count(*) as total from alerts where user_action = 'BET' group by status").fetchall()
        summary = {row["status"]: row["total"] for row in rows}
        settled = summary.get("WON", 0) + summary.get("LOST", 0) + summary.get("PUSH", 0)
        win_rate = round(summary.get("WON", 0) / settled * 100, 2) if settled else 0.0
        profit = self.conn.execute(
            """
            select coalesce(sum(
                case
                    when status = 'WON' then odd - 1
                    when status = 'LOST' then -1
                    else 0
                end
            ), 0) as profit
            from alerts
            where user_action = 'BET'
            """
        ).fetchone()["profit"]
        action_rows = self.conn.execute("select user_action, count(*) as total from alerts group by user_action").fetchall()
        actions = {row["user_action"]: row["total"] for row in action_rows}
        return {
            "summary": summary,
            "actions": actions,
            "settled": settled,
            "win_rate": win_rate,
            "profit_units": round(float(profit), 2),
        }

    def export_json(self) -> str:
        return json.dumps(self.last_alerts(20), ensure_ascii=False, indent=2)
