from __future__ import annotations

import json
import sqlite3
from pathlib import Path
from typing import Any

from .models import Decision, GameSnapshot


class Storage:
    def __init__(self, path: Path) -> None:
        self.path = path
        if path.parent != Path("."):
            path.parent.mkdir(parents=True, exist_ok=True)
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
                settled_at datetime,
                betfair_market_id text,
                betfair_selection_id text,
                betfair_event_id text,
                betfair_start_time text
            );
            create index if not exists idx_alerts_status on alerts(status);
            create table if not exists bfbm_markets (
                id integer primary key autoincrement,
                captured_at datetime default current_timestamp,
                event_name text not null,
                market_name text not null,
                event_id text,
                market_id text,
                market_type text,
                status text,
                start_time text,
                live_score text,
                live_time text,
                favorite text,
                winner text,
                total_matched text,
                raw_json text,
                source_path text,
                source_modified_at text,
                source_age_seconds real
            );
            create index if not exists idx_bfbm_markets_captured_at on bfbm_markets(captured_at);
            create table if not exists bfbm_export_audit (
                id integer primary key autoincrement,
                alert_id integer,
                alert_key text,
                first_seen_at datetime default current_timestamp,
                last_seen_at datetime default current_timestamp,
                seen_count integer not null default 1,
                endpoint text not null,
                status text not null,
                reason text not null,
                home text,
                away text,
                event_name text,
                market text,
                selection text,
                line real,
                bfbm_event_name text,
                bfbm_market_name text,
                bfbm_selection_name text,
                bfbm_event_id text,
                bfbm_market_id text,
                bfbm_selection_id text,
                bfbm_start_time text,
                raw_json text,
                unique(alert_id, endpoint)
            );
            create index if not exists idx_bfbm_export_audit_last_seen on bfbm_export_audit(last_seen_at);
            create table if not exists bfbm_bet_notifications (
                id integer primary key autoincrement,
                created_at datetime default current_timestamp,
                bet_id text not null unique,
                size_matched text,
                success text,
                strategy text,
                raw_line text
            );
            """
        )
        columns = {row["name"] for row in self.conn.execute("pragma table_info(alerts)").fetchall()}
        if "user_action" not in columns:
            self.conn.execute("alter table alerts add column user_action text not null default 'PENDING'")
        for column in ("betfair_market_id", "betfair_selection_id", "betfair_event_id", "betfair_start_time"):
            if column not in columns:
                self.conn.execute(f"alter table alerts add column {column} text")
        self.conn.execute("create index if not exists idx_alerts_user_action on alerts(user_action)")
        market_columns = {row["name"] for row in self.conn.execute("pragma table_info(bfbm_markets)").fetchall()}
        for column, ddl_type in (
            ("source_path", "text"),
            ("source_modified_at", "text"),
            ("source_age_seconds", "real"),
            ("event_id", "text"),
            ("market_id", "text"),
            ("market_type", "text"),
        ):
            if column not in market_columns:
                self.conn.execute(f"alter table bfbm_markets add column {column} {ddl_type}")
        self.conn.commit()

    def record_bfbm_export_audit(self, rows: list[dict[str, Any]]) -> None:
        if not rows:
            return
        self.conn.executemany(
            """
            insert into bfbm_export_audit (
                alert_id, alert_key, endpoint, status, reason, home, away, event_name,
                market, selection, line, bfbm_event_name, bfbm_market_name,
                bfbm_selection_name, bfbm_event_id, bfbm_market_id, bfbm_selection_id,
                bfbm_start_time, raw_json
            ) values (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
            on conflict(alert_id, endpoint) do update set
                last_seen_at = current_timestamp,
                seen_count = seen_count + 1,
                status = excluded.status,
                reason = excluded.reason,
                bfbm_event_name = excluded.bfbm_event_name,
                bfbm_market_name = excluded.bfbm_market_name,
                bfbm_selection_name = excluded.bfbm_selection_name,
                bfbm_event_id = excluded.bfbm_event_id,
                bfbm_market_id = excluded.bfbm_market_id,
                bfbm_selection_id = excluded.bfbm_selection_id,
                bfbm_start_time = excluded.bfbm_start_time,
                raw_json = excluded.raw_json
            """,
            [
                (
                    row.get("alert_id"),
                    row.get("alert_key", ""),
                    row.get("endpoint", ""),
                    row.get("status", ""),
                    row.get("reason", ""),
                    row.get("home", ""),
                    row.get("away", ""),
                    row.get("event_name", ""),
                    row.get("market", ""),
                    row.get("selection", ""),
                    row.get("line"),
                    row.get("bfbm_event_name", ""),
                    row.get("bfbm_market_name", ""),
                    row.get("bfbm_selection_name", ""),
                    row.get("bfbm_event_id", ""),
                    row.get("bfbm_market_id", ""),
                    row.get("bfbm_selection_id", ""),
                    row.get("bfbm_start_time", ""),
                    json.dumps(row.get("raw", {}), ensure_ascii=False),
                )
                for row in rows
            ],
        )
        self.conn.commit()

    def record_bfbm_bet_notification(self, item: dict[str, Any]) -> None:
        bet_id = str(item.get("bet_id") or "").strip()
        if not bet_id:
            return
        self.conn.execute(
            """
            insert or ignore into bfbm_bet_notifications (
                bet_id, size_matched, success, strategy, raw_line
            ) values (?, ?, ?, ?, ?)
            """,
            (
                bet_id,
                str(item.get("size_matched") or ""),
                str(item.get("success") or ""),
                str(item.get("strategy") or ""),
                str(item.get("line") or ""),
            ),
        )
        self.conn.commit()

    def replace_bfbm_markets(self, rows: list[dict[str, Any]]) -> int:
        self.conn.execute("delete from bfbm_markets")
        if rows:
            self.conn.executemany(
                """
                insert into bfbm_markets (
                    event_name, market_name, event_id, market_id, market_type,
                    status, start_time, live_score, live_time,
                    favorite, winner, total_matched, raw_json, source_path,
                    source_modified_at, source_age_seconds
                ) values (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                """,
                [
                    (
                        row.get("event_name", ""),
                        row.get("market_name", ""),
                        row.get("event_id", ""),
                        row.get("market_id", ""),
                        row.get("market_type", ""),
                        row.get("status", ""),
                        row.get("start_time", ""),
                        row.get("live_score", ""),
                        row.get("live_time", ""),
                        row.get("favorite", ""),
                        row.get("winner", ""),
                        row.get("total_matched", ""),
                        row.get("raw_json", "{}"),
                        row.get("source_path", ""),
                        row.get("source_modified_at", ""),
                        row.get("source_age_seconds"),
                    )
                    for row in rows
                ],
            )
        self.conn.commit()
        return len(rows)

    def bfbm_markets(self, max_age_minutes: int = 15, max_source_age_seconds: int = 7 * 24 * 60 * 60) -> list[dict[str, Any]]:
        rows = self.conn.execute(
            """
            select *
            from bfbm_markets
            where captured_at >= datetime('now', ?)
              and coalesce(source_age_seconds, 999999) <= ?
            order by event_name, market_name
            """,
            (f"-{max(1, max_age_minutes)} minutes", max(1, max_source_age_seconds)),
        ).fetchall()
        return [dict(row) for row in rows]

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

    def seen_recent_game_alert(self, game: GameSnapshot, cooldown_minutes: int) -> bool:
        if cooldown_minutes <= 0 or game.fixture_id is None:
            return False
        row = self.conn.execute(
            """
            select 1
            from alerts
            where fixture_id = ?
              and status in ('SENT', 'WON', 'LOST', 'PUSH')
              and created_at >= datetime('now', ?)
            limit 1
            """,
            (game.fixture_id, f"-{cooldown_minutes} minutes"),
        ).fetchone()
        return row is not None

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

    def bfbm_tips(self, max_age_minutes: int, limit: int = 4) -> list[dict[str, Any]]:
        rows = self.conn.execute(
            """
            select *
            from alerts
            where status = 'SENT'
              and user_action != 'IGNORED'
              and created_at >= datetime('now', ?)
            order by id desc
            limit ?
            """,
            (f"-{max_age_minutes} minutes", max(1, limit)),
        ).fetchall()
        return [dict(row) for row in reversed(rows)]

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

    def strategy_report(self, limit: int = 80) -> dict[str, Any]:
        total = self.conn.execute("select count(*) as total from alerts").fetchone()["total"]
        by_status = {
            row["status"]: row["total"]
            for row in self.conn.execute("select status, count(*) as total from alerts group by status").fetchall()
        }
        by_action = {
            row["user_action"]: row["total"]
            for row in self.conn.execute("select user_action, count(*) as total from alerts group by user_action").fetchall()
        }
        by_market = [
            dict(row)
            for row in self.conn.execute(
                """
                select market, selection, line, status, user_action, count(*) as total,
                       round(avg(confidence), 1) as avg_confidence,
                       min(created_at) as first_at,
                       max(created_at) as last_at
                from alerts
                group by market, selection, line, status, user_action
                order by total desc, last_at desc
                limit ?
                """,
                (max(1, limit),),
            ).fetchall()
        ]
        recent = [
            dict(row)
            for row in self.conn.execute(
                """
                select id, created_at, home, away, minute, market, selection, line,
                       confidence, status, user_action, result_note
                from alerts
                order by id desc
                limit ?
                """,
                (max(1, limit),),
            ).fetchall()
        ]
        return {
            "total_alerts": total,
            "by_status": by_status,
            "by_action": by_action,
            "performance_betted": self.performance(),
            "by_market": by_market,
            "recent": recent,
        }

    def bfbm_export_report(self, limit: int = 100, hours: int = 24) -> dict[str, Any]:
        limit = max(1, limit)
        hours = max(1, hours)
        summary = [
            dict(row)
            for row in self.conn.execute(
                """
                select status, reason, count(*) as total,
                       min(first_seen_at) as first_seen_at,
                       max(last_seen_at) as last_seen_at
                from bfbm_export_audit
                where first_seen_at >= datetime('now', ?)
                group by status, reason
                order by total desc, last_seen_at desc
                """,
                (f"-{hours} hours",),
            ).fetchall()
        ]
        recent = [
            dict(row)
            for row in self.conn.execute(
                """
                select a.id as alert_id, a.created_at as alert_created_at,
                       e.first_seen_at, e.last_seen_at, e.seen_count,
                       e.endpoint, e.status as export_status, e.reason,
                       a.home, a.away, a.minute, a.market, a.selection, a.line,
                       e.bfbm_event_name, e.bfbm_market_name, e.bfbm_selection_name,
                       e.bfbm_event_id, e.bfbm_market_id, e.bfbm_selection_id,
                       e.bfbm_start_time, a.status as alert_status, a.user_action
                from bfbm_export_audit e
                left join alerts a on a.id = e.alert_id
                where e.first_seen_at >= datetime('now', ?)
                order by e.last_seen_at desc, e.id desc
                limit ?
                """,
                (f"-{hours} hours", limit),
            ).fetchall()
        ]
        sent_without_audit = [
            dict(row)
            for row in self.conn.execute(
                """
                select id, created_at, home, away, minute, market, selection, line,
                       status, user_action
                from alerts
                where created_at >= datetime('now', ?)
                  and id not in (
                      select alert_id from bfbm_export_audit where alert_id is not null
                  )
                order by id desc
                limit ?
                """,
                (f"-{hours} hours", limit),
            ).fetchall()
        ]
        bets = [
            dict(row)
            for row in self.conn.execute(
                """
                select *
                from bfbm_bet_notifications
                where created_at >= datetime('now', ?)
                order by id desc
                limit ?
                """,
                (f"-{hours} hours", limit),
            ).fetchall()
        ]
        return {
            "window_hours": hours,
            "summary": summary,
            "recent_exports": recent,
            "sent_without_export_audit": sent_without_audit,
            "bet_notifications": bets,
        }

    def export_json(self) -> str:
        return json.dumps(self.last_alerts(20), ensure_ascii=False, indent=2)
