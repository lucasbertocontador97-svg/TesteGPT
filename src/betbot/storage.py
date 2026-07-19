from __future__ import annotations

import json
import sqlite3
from pathlib import Path
from typing import Any

from .bfbm_markets import normalize_event
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
                betfair_start_time text,
                strategy text,
                bfbm_bet_id text,
                bfbm_bet_placed_at text,
                result_notified_at datetime
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
                placed_at text,
                placed_at_iso text,
                bet_id text not null unique,
                size_matched text,
                success text,
                strategy text,
                sid text,
                raw_line text
            );
            create table if not exists bfbm_event_aliases (
                id integer primary key autoincrement,
                created_at datetime default current_timestamp,
                updated_at datetime default current_timestamp,
                source_event_name text not null,
                source_event_norm text not null,
                target_event_name text not null,
                target_event_norm text not null,
                score integer not null,
                seen_count integer not null default 1,
                unique(source_event_norm, target_event_norm)
            );
            """
        )
        columns = {row["name"] for row in self.conn.execute("pragma table_info(alerts)").fetchall()}
        if "user_action" not in columns:
            self.conn.execute("alter table alerts add column user_action text not null default 'PENDING'")
        added_result_notified_at = False
        for column in (
            "betfair_market_id",
            "betfair_selection_id",
            "betfair_event_id",
            "betfair_start_time",
            "strategy",
            "bfbm_bet_id",
            "bfbm_bet_placed_at",
            "result_notified_at",
        ):
            if column not in columns:
                self.conn.execute(f"alter table alerts add column {column} text")
                if column == "result_notified_at":
                    added_result_notified_at = True
        if added_result_notified_at:
            self.conn.execute(
                """
                update alerts
                set result_notified_at = current_timestamp
                where status in ('WON', 'LOST', 'PUSH')
                  and result_notified_at is null
                """
            )
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
        bet_columns = {row["name"] for row in self.conn.execute("pragma table_info(bfbm_bet_notifications)").fetchall()}
        for column, ddl_type in (
            ("placed_at", "text"),
            ("placed_at_iso", "text"),
            ("sid", "text"),
            ("alert_id", "integer"),
            ("market_id", "text"),
            ("selection_id", "text"),
            ("handicap", "text"),
            ("side", "text"),
            ("order_status", "text"),
            ("price", "real"),
            ("profit", "real"),
            ("settled_at", "text"),
            ("raw_json", "text"),
        ):
            if column not in bet_columns:
                self.conn.execute(f"alter table bfbm_bet_notifications add column {column} {ddl_type}")
        self.conn.commit()

    def record_bfbm_event_aliases(self, aliases: list[dict[str, Any]]) -> None:
        rows = []
        for alias in aliases:
            source = str(alias.get("source_event_name") or "").strip()
            target = str(alias.get("target_event_name") or "").strip()
            if not source or not target:
                continue
            source_norm = normalize_event(source)
            target_norm = normalize_event(target)
            if not source_norm or not target_norm or source_norm == target_norm:
                continue
            try:
                score = int(alias.get("score") or 0)
            except (TypeError, ValueError):
                score = 0
            if score < 75:
                continue
            rows.append((source, source_norm, target, target_norm, score))
        if not rows:
            return
        self.conn.executemany(
            """
            insert into bfbm_event_aliases (
                source_event_name, source_event_norm, target_event_name, target_event_norm, score
            ) values (?, ?, ?, ?, ?)
            on conflict(source_event_norm, target_event_norm) do update set
                updated_at = current_timestamp,
                source_event_name = excluded.source_event_name,
                target_event_name = excluded.target_event_name,
                score = max(score, excluded.score),
                seen_count = seen_count + 1
            """,
            rows,
        )
        self.conn.commit()

    def bfbm_event_aliases(self, limit: int = 1000) -> list[dict[str, Any]]:
        rows = self.conn.execute(
            """
            select *
            from bfbm_event_aliases
            order by updated_at desc
            limit ?
            """,
            (max(1, limit),),
        ).fetchall()
        return [dict(row) for row in rows]

    def record_bfbm_export_audit(self, rows: list[dict[str, Any]]) -> None:
        if not rows:
            return
        keyed_rows = [row for row in rows if row.get("alert_id") is None and row.get("alert_key")]
        insert_rows = [row for row in rows if not (row.get("alert_id") is None and row.get("alert_key"))]
        for row in keyed_rows:
            cursor = self.conn.execute(
                """
                update bfbm_export_audit
                set last_seen_at = current_timestamp,
                    seen_count = seen_count + 1,
                    status = ?,
                    reason = ?,
                    home = ?,
                    away = ?,
                    event_name = ?,
                    market = ?,
                    selection = ?,
                    line = ?,
                    bfbm_event_name = ?,
                    bfbm_market_name = ?,
                    bfbm_selection_name = ?,
                    bfbm_event_id = ?,
                    bfbm_market_id = ?,
                    bfbm_selection_id = ?,
                    bfbm_start_time = ?,
                    raw_json = ?
                where alert_id is null
                  and alert_key = ?
                  and endpoint = ?
                """,
                (
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
                    row.get("alert_key", ""),
                    row.get("endpoint", ""),
                ),
            )
            if cursor.rowcount == 0:
                insert_rows.append(row)
        rows = insert_rows
        if not rows:
            self.conn.commit()
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
            insert into bfbm_bet_notifications (
                placed_at, placed_at_iso, bet_id, size_matched, success, strategy, sid, raw_line,
                alert_id, market_id, selection_id, handicap, side, order_status, price, profit,
                settled_at, raw_json
            ) values (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
            on conflict(bet_id) do update set
                placed_at = coalesce(nullif(excluded.placed_at, ''), placed_at),
                placed_at_iso = coalesce(nullif(excluded.placed_at_iso, ''), placed_at_iso),
                size_matched = coalesce(nullif(excluded.size_matched, ''), size_matched),
                success = coalesce(nullif(excluded.success, ''), success),
                strategy = coalesce(nullif(excluded.strategy, ''), strategy),
                sid = coalesce(nullif(excluded.sid, ''), sid),
                raw_line = coalesce(nullif(excluded.raw_line, ''), raw_line),
                alert_id = coalesce(excluded.alert_id, alert_id),
                market_id = coalesce(nullif(excluded.market_id, ''), market_id),
                selection_id = coalesce(nullif(excluded.selection_id, ''), selection_id),
                handicap = coalesce(nullif(excluded.handicap, ''), handicap),
                side = coalesce(nullif(excluded.side, ''), side),
                order_status = coalesce(nullif(excluded.order_status, ''), order_status),
                price = coalesce(excluded.price, price),
                profit = coalesce(excluded.profit, profit),
                settled_at = coalesce(nullif(excluded.settled_at, ''), settled_at),
                raw_json = coalesce(nullif(excluded.raw_json, ''), raw_json)
            """,
            (
                str(item.get("placed_at") or ""),
                str(item.get("placed_at_iso") or ""),
                bet_id,
                str(item.get("size_matched") or ""),
                str(item.get("success") or ""),
                str(item.get("strategy") or ""),
                str(item.get("sid") or ""),
                str(item.get("line") or ""),
                item.get("alert_id"),
                str(item.get("market_id") or ""),
                str(item.get("selection_id") or ""),
                str(item.get("handicap") or ""),
                str(item.get("side") or ""),
                str(item.get("order_status") or item.get("status") or ""),
                item.get("price"),
                item.get("profit"),
                str(item.get("settled_at") or ""),
                json.dumps(item.get("raw", {}), ensure_ascii=False) if item.get("raw") is not None else "",
            ),
        )
        self.conn.commit()

    def find_alert_by_bfbm_order(self, market_id: str, selection_id: str) -> dict[str, Any] | None:
        market_id = str(market_id or "").strip()
        selection_id = str(selection_id or "").strip()
        if not market_id or market_id == "0" or not selection_id or selection_id == "0":
            return None
        row = self.conn.execute(
            """
            select *
            from alerts
            where betfair_market_id = ?
              and betfair_selection_id = ?
              and user_action != 'IGNORED'
            order by created_at desc, id desc
            limit 1
            """,
            (market_id, selection_id),
        ).fetchone()
        if row:
            return dict(row)
        row = self.conn.execute(
            """
            select a.*
            from bfbm_export_audit e
            join alerts a on a.id = e.alert_id
            where e.bfbm_market_id = ?
              and e.bfbm_selection_id = ?
              and a.user_action != 'IGNORED'
            order by e.last_seen_at desc, e.id desc
            limit 1
            """,
            (market_id, selection_id),
        ).fetchone()
        return dict(row) if row else None

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
        market_rows = [dict(row) for row in rows]
        if not market_rows:
            return []
        aliases = self.bfbm_event_aliases()
        if not aliases:
            return market_rows
        aliases_by_target: dict[str, list[dict[str, Any]]] = {}
        for alias in aliases:
            aliases_by_target.setdefault(str(alias.get("target_event_norm") or ""), []).append(alias)
        enriched = list(market_rows)
        seen_alias_rows: set[tuple[str, str, str]] = set()
        for row in market_rows:
            target_norm = normalize_event(str(row.get("event_name") or ""))
            for alias in aliases_by_target.get(target_norm, []):
                alias_name = str(alias.get("source_event_name") or "").strip()
                key = (str(row.get("market_id") or ""), target_norm, normalize_event(alias_name))
                if not alias_name or key in seen_alias_rows:
                    continue
                seen_alias_rows.add(key)
                alias_row = dict(row)
                alias_row["alias_event_name"] = alias_name
                alias_row["alias_score"] = alias.get("score")
                enriched.append(alias_row)
        return enriched

    def seen_alert(self, alert_key: str) -> bool:
        row = self.conn.execute(
            """
            select 1
            from alerts
            where alert_key = ?
              and created_at >= datetime('now', '-6 hours')
            """,
            (alert_key,),
        ).fetchone()
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
                odd, line, confidence, reason, stake, alert_key, strategy
            ) values (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
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
                decision.strategy,
            ),
        )
        self.conn.commit()
        return self._alert_id(decision.alert_key)

    def save_manual_alert(self, game: GameSnapshot, decision: Decision) -> int | None:
        self.conn.execute(
            """
            insert or ignore into alerts (
                event_id, fixture_id, home, away, minute, market, selection, bookmaker,
                odd, line, confidence, reason, stake, alert_key, strategy
            ) values (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
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
                decision.strategy,
            ),
        )
        self.conn.commit()
        return self._alert_id(decision.alert_key)

    def attach_betfair_export_ids(
        self,
        alert_id: int,
        *,
        event_id: str = "",
        market_id: str = "",
        selection_id: str = "",
        start_time: str = "",
    ) -> bool:
        cursor = self.conn.execute(
            """
            update alerts
            set betfair_event_id = coalesce(nullif(?, ''), betfair_event_id),
                betfair_market_id = coalesce(nullif(?, ''), betfair_market_id),
                betfair_selection_id = coalesce(nullif(?, ''), betfair_selection_id),
                betfair_start_time = coalesce(nullif(?, ''), betfair_start_time)
            where id = ?
            """,
            (
                str(event_id or ""),
                str(market_id or ""),
                str(selection_id or ""),
                str(start_time or ""),
                alert_id,
            ),
        )
        self.conn.commit()
        return cursor.rowcount > 0

    def last_alerts(self, limit: int = 5) -> list[dict[str, Any]]:
        rows = self.conn.execute("select * from alerts order by id desc limit ?", (limit,)).fetchall()
        return [dict(row) for row in rows]

    def pending_alerts(self) -> list[dict[str, Any]]:
        rows = self.conn.execute(
            """
            select *
            from alerts
            where status = 'SENT'
              and user_action != 'IGNORED'
              and created_at <= datetime('now', '-5 minutes')
            order by id asc
            limit 50
            """
        ).fetchall()
        return [dict(row) for row in rows]

    def bfbm_tips(self, max_age_minutes: int, limit: int = 12) -> list[dict[str, Any]]:
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

    def mark_bfbm_bet(self, alert_id: int, bet_id: str, placed_at: str = "") -> bool:
        cursor = self.conn.execute(
            """
            update alerts
            set user_action = 'BET',
                bfbm_bet_id = coalesce(nullif(?, ''), bfbm_bet_id),
                bfbm_bet_placed_at = coalesce(nullif(?, ''), bfbm_bet_placed_at)
            where id = ?
            """,
            (str(bet_id or ""), str(placed_at or ""), alert_id),
        )
        self.conn.commit()
        return cursor.rowcount > 0

    def settle_alert(self, alert_id: int, status: str, note: str) -> None:
        self.conn.execute(
            "update alerts set status = ?, result_note = ?, settled_at = current_timestamp where id = ?",
            (status, note, alert_id),
        )
        self.conn.commit()

    def pending_result_notifications(self, limit: int = 20) -> list[dict[str, Any]]:
        rows = self.conn.execute(
            """
            select *
            from alerts
            where user_action = 'BET'
              and status in ('WON', 'LOST', 'PUSH')
              and result_notified_at is null
            order by settled_at asc, id asc
            limit ?
            """,
            (max(1, limit),),
        ).fetchall()
        return [dict(row) for row in rows]

    def mark_result_notified(self, alert_id: int) -> bool:
        cursor = self.conn.execute(
            "update alerts set result_notified_at = current_timestamp where id = ?",
            (alert_id,),
        )
        self.conn.commit()
        return cursor.rowcount > 0

    def _performance_where(self, where_clause: str, params: tuple[Any, ...] = ()) -> dict[str, Any]:
        rows = self.conn.execute(
            f"select status, count(*) as total from alerts where {where_clause} group by status",
            params,
        ).fetchall()
        summary = {row["status"]: row["total"] for row in rows}
        settled = summary.get("WON", 0) + summary.get("LOST", 0) + summary.get("PUSH", 0)
        win_rate = round(summary.get("WON", 0) / settled * 100, 2) if settled else 0.0
        profit = self.conn.execute(
            f"""
            select coalesce(sum(
                case
                    when status = 'WON' and odd > 1 then odd - 1
                    when status = 'WON' then 1
                    when status = 'LOST' then -1
                    else 0
                end
            ), 0) as profit
            from alerts
            where {where_clause}
            """,
            params,
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

    def performance(self) -> dict[str, Any]:
        return self._performance_where("user_action = 'BET'")

    def signal_performance(self) -> dict[str, Any]:
        return self._performance_where("user_action != 'IGNORED'")

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
                select coalesce(strategy, 'sem_estrategia') as strategy,
                       market, selection, line, status, user_action, count(*) as total,
                       round(avg(confidence), 1) as avg_confidence,
                       min(created_at) as first_at,
                       max(created_at) as last_at
                from alerts
                group by coalesce(strategy, 'sem_estrategia'), market, selection, line, status, user_action
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
                select id, created_at, home, away, minute, market, selection, line, strategy,
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
            "performance_all_signals": self.signal_performance(),
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
                       coalesce(nullif(a.home, ''), e.home) as home,
                       coalesce(nullif(a.away, ''), e.away) as away,
                       a.minute,
                       coalesce(nullif(a.market, ''), e.market) as market,
                       coalesce(nullif(a.selection, ''), e.selection) as selection,
                       coalesce(a.line, e.line) as line,
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
                where coalesce(nullif(placed_at_iso, ''), created_at) >= datetime('now', ?)
                order by id desc
                limit ?
                """,
                (f"-{hours} hours", limit),
            ).fetchall()
        ]
        real_bets = [
            dict(row)
            for row in self.conn.execute(
                """
                select *
                from bfbm_bet_notifications
                where coalesce(nullif(placed_at_iso, ''), created_at) >= datetime('now', ?)
                  and (
                    sid like 'CODEX-TESTEGPT%'
                    or strategy like '%TesteGPT%'
                  )
                order by coalesce(nullif(placed_at_iso, ''), created_at) desc, id desc
                limit ?
                """,
                (f"-{hours} hours", limit),
            ).fetchall()
        ]
        real_summary_rows = self.conn.execute(
            """
            select
                count(*) as total,
                sum(case when replace(size_matched, ',', '.') + 0 > 0 then 1 else 0 end) as matched,
                round(sum(case when replace(size_matched, ',', '.') + 0 > 0 then replace(size_matched, ',', '.') + 0 else 0 end), 2) as matched_amount
            from bfbm_bet_notifications
            where coalesce(nullif(placed_at_iso, ''), created_at) >= datetime('now', ?)
              and (
                sid like 'CODEX-TESTEGPT%'
                or strategy like '%TesteGPT%'
              )
            """
            ,
            (f"-{hours} hours",),
        ).fetchone()
        return {
            "window_hours": hours,
            "summary": summary,
            "recent_exports": recent,
            "sent_without_export_audit": sent_without_audit,
            "bet_notifications": bets,
            "real_bet_summary": dict(real_summary_rows) if real_summary_rows else {},
            "real_bet_notifications": real_bets,
        }

    @staticmethod
    def _money_float(value: Any) -> float:
        if value is None or value == "":
            return 0.0
        try:
            return float(str(value).replace(",", "."))
        except (TypeError, ValueError):
            return 0.0

    @staticmethod
    def _result_label(profit: float) -> str:
        if profit > 0:
            return "GREEN"
        if profit < 0:
            return "RED"
        return "VOID"

    def _bfbm_result_rows(
        self,
        where_sql: str,
        params: tuple[Any, ...],
        limit: int = 500,
    ) -> list[dict[str, Any]]:
        rows = self.conn.execute(
            f"""
            select
                b.id as notification_id,
                coalesce(nullif(b.settled_at, ''), a.settled_at, nullif(b.placed_at_iso, ''), b.created_at) as sort_at,
                coalesce(nullif(b.settled_at, ''), a.settled_at, '') as settled_at,
                coalesce(nullif(b.placed_at_iso, ''), nullif(b.placed_at, ''), b.created_at) as placed_at,
                b.bet_id,
                coalesce(b.alert_id, a.id) as alert_id,
                coalesce(nullif(b.market_id, ''), a.betfair_market_id, '') as market_id,
                coalesce(nullif(b.selection_id, ''), a.betfair_selection_id, '') as selection_id,
                coalesce(nullif(b.handicap, ''), '') as handicap,
                coalesce(nullif(b.side, ''), 'BACK') as side,
                coalesce(nullif(b.order_status, ''), '') as order_status,
                coalesce(b.price, a.odd, 0) as price,
                replace(coalesce(nullif(b.size_matched, ''), '0'), ',', '.') + 0 as stake,
                coalesce(b.profit, 0) as profit,
                coalesce(nullif(b.strategy, ''), a.strategy, '') as strategy,
                coalesce(nullif(a.home || ' x ' || a.away, ' x '), '') as event_name,
                coalesce(a.home, '') as home,
                coalesce(a.away, '') as away,
                coalesce(a.minute, 0) as minute,
                coalesce(a.market, '') as market,
                coalesce(a.selection, '') as selection,
                a.line,
                coalesce(a.confidence, 0) as confidence,
                coalesce(a.reason, '') as reason,
                coalesce(a.status, '') as alert_status,
                coalesce(a.user_action, '') as user_action,
                coalesce(a.result_note, '') as result_note
            from bfbm_bet_notifications b
            left join alerts a
              on a.id = b.alert_id
              or (b.bet_id != '' and a.bfbm_bet_id = b.bet_id)
            where b.profit is not null
              and {where_sql}
            order by sort_at desc, b.id desc
            limit ?
            """,
            (*params, max(1, limit)),
        ).fetchall()
        result_rows: list[dict[str, Any]] = []
        for row in rows:
            item = dict(row)
            profit = self._money_float(item.get("profit"))
            stake = self._money_float(item.get("stake"))
            price = self._money_float(item.get("price"))
            item["profit"] = round(profit, 2)
            item["stake"] = round(stake, 2)
            item["price"] = round(price, 4)
            item["result"] = self._result_label(profit)
            item["roi_percent"] = round(profit / stake * 100, 2) if stake else 0.0
            item["cursor"] = f"{item.get('sort_at') or ''}|{item.get('notification_id') or ''}"
            result_rows.append(item)
        return result_rows

    def _summarize_bfbm_results(self, rows: list[dict[str, Any]]) -> dict[str, Any]:
        total = len(rows)
        green = sum(1 for row in rows if row["result"] == "GREEN")
        red = sum(1 for row in rows if row["result"] == "RED")
        void = sum(1 for row in rows if row["result"] == "VOID")
        profit = round(sum(self._money_float(row.get("profit")) for row in rows), 2)
        staked = round(sum(self._money_float(row.get("stake")) for row in rows), 2)
        by_strategy: dict[str, dict[str, Any]] = {}
        for row in rows:
            strategy = str(row.get("strategy") or "sem_estrategia")
            bucket = by_strategy.setdefault(
                strategy,
                {"bets": 0, "green": 0, "red": 0, "void": 0, "profit": 0.0, "staked": 0.0},
            )
            bucket["bets"] += 1
            bucket[row["result"].lower()] += 1
            bucket["profit"] += self._money_float(row.get("profit"))
            bucket["staked"] += self._money_float(row.get("stake"))
        strategy_rows = []
        for strategy, bucket in by_strategy.items():
            stake = self._money_float(bucket["staked"])
            profit_value = self._money_float(bucket["profit"])
            strategy_rows.append(
                {
                    "strategy": strategy,
                    "bets": bucket["bets"],
                    "green": bucket["green"],
                    "red": bucket["red"],
                    "void": bucket["void"],
                    "profit": round(profit_value, 2),
                    "staked": round(stake, 2),
                    "roi_percent": round(profit_value / stake * 100, 2) if stake else 0.0,
                }
            )
        strategy_rows.sort(key=lambda item: (item["profit"], item["bets"]), reverse=True)
        return {
            "bets": total,
            "green": green,
            "red": red,
            "void": void,
            "profit": profit,
            "staked": staked,
            "roi_percent": round(profit / staked * 100, 2) if staked else 0.0,
            "win_rate_percent": round(green / (green + red) * 100, 2) if green + red else 0.0,
            "by_strategy": strategy_rows,
        }

    def bfbm_results_for_day(self, day: str, limit: int = 500) -> dict[str, Any]:
        rows = self._bfbm_result_rows(
            "date(coalesce(nullif(b.settled_at, ''), a.settled_at, nullif(b.placed_at_iso, ''), b.created_at)) = ?",
            (day,),
            limit=limit,
        )
        return {"day": day, "summary": self._summarize_bfbm_results(rows), "bets": rows}

    def bfbm_results_for_month(self, month: str, limit: int = 5000) -> dict[str, Any]:
        rows = self._bfbm_result_rows(
            "substr(date(coalesce(nullif(b.settled_at, ''), a.settled_at, nullif(b.placed_at_iso, ''), b.created_at)), 1, 7) = ?",
            (month,),
            limit=limit,
        )
        return {"month": month, "summary": self._summarize_bfbm_results(rows), "bets": rows}

    def bfbm_results_since(self, cursor: str = "", limit: int = 200) -> dict[str, Any]:
        sort_at = ""
        notification_id = 0
        if cursor and "|" in cursor:
            sort_at, raw_id = cursor.rsplit("|", 1)
            try:
                notification_id = int(raw_id)
            except ValueError:
                notification_id = 0
        if sort_at:
            where_sql = """
                (
                    coalesce(nullif(b.settled_at, ''), a.settled_at, nullif(b.placed_at_iso, ''), b.created_at) > ?
                    or (
                        coalesce(nullif(b.settled_at, ''), a.settled_at, nullif(b.placed_at_iso, ''), b.created_at) = ?
                        and b.id > ?
                    )
                )
            """
            params: tuple[Any, ...] = (sort_at, sort_at, notification_id)
        else:
            where_sql = "1 = 1"
            params = ()
        rows = list(reversed(self._bfbm_result_rows(where_sql, params, limit=limit)))
        next_cursor = rows[-1]["cursor"] if rows else cursor
        return {
            "since": cursor,
            "next_cursor": next_cursor,
            "count": len(rows),
            "summary": self._summarize_bfbm_results(rows),
            "bets": rows,
        }

    def bfbm_sync_diagnostics(self, limit: int = 20) -> dict[str, Any]:
        stats = self.conn.execute(
            """
            select
                count(*) as total_orders,
                coalesce(sum(case when profit is not null then 1 else 0 end), 0) as with_profit,
                coalesce(sum(case when alert_id is not null then 1 else 0 end), 0) as matched_alerts,
                coalesce(sum(case when profit is not null and alert_id is not null then 1 else 0 end), 0) as matched_settled,
                max(created_at) as last_received_at,
                max(coalesce(nullif(settled_at, ''), nullif(placed_at_iso, ''), created_at)) as last_order_at
            from bfbm_bet_notifications
            """
        ).fetchone()
        today = self.conn.execute(
            """
            select
                count(*) as orders_today,
                coalesce(sum(case when profit is not null then 1 else 0 end), 0) as with_profit_today,
                coalesce(sum(case when alert_id is not null then 1 else 0 end), 0) as matched_today
            from bfbm_bet_notifications
            where date(coalesce(nullif(settled_at, ''), nullif(placed_at_iso, ''), created_at)) = date('now')
            """
        ).fetchone()
        recent = self.conn.execute(
            """
            select
                id,
                created_at,
                placed_at_iso,
                settled_at,
                bet_id,
                alert_id,
                market_id,
                selection_id,
                side,
                order_status,
                price,
                replace(coalesce(nullif(size_matched, ''), '0'), ',', '.') + 0 as stake,
                profit,
                strategy,
                success
            from bfbm_bet_notifications
            order by coalesce(nullif(settled_at, ''), nullif(placed_at_iso, ''), created_at) desc, id desc
            limit ?
            """,
            (max(1, limit),),
        ).fetchall()
        unmatched = self.conn.execute(
            """
            select
                bet_id,
                market_id,
                selection_id,
                order_status,
                price,
                profit,
                created_at
            from bfbm_bet_notifications
            where alert_id is null
            order by created_at desc, id desc
            limit 10
            """
        ).fetchall()
        return {
            "stats": dict(stats or {}),
            "today": dict(today or {}),
            "recent_orders": [dict(row) for row in recent],
            "recent_unmatched_orders": [dict(row) for row in unmatched],
        }

    def export_json(self) -> str:
        return json.dumps(self.last_alerts(20), ensure_ascii=False, indent=2)
