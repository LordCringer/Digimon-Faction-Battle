import aiosqlite
from contextlib import asynccontextmanager

import config

SCHEMA = """
CREATE TABLE IF NOT EXISTS factions (
    name        TEXT PRIMARY KEY,
    emoji       TEXT DEFAULT '',
    created_by  INTEGER,
    created_at  TEXT DEFAULT (datetime('now'))
);

CREATE TABLE IF NOT EXISTS members (
    discord_id   INTEGER PRIMARY KEY,
    faction_name TEXT NOT NULL REFERENCES factions(name) ON DELETE CASCADE,
    joined_at    TEXT DEFAULT (datetime('now'))
);

CREATE TABLE IF NOT EXISTS registrations (
    discord_id   INTEGER PRIMARY KEY,
    player_id    INTEGER,
    player_name  TEXT NOT NULL,
    player_slug  TEXT NOT NULL,
    registered_at TEXT DEFAULT (datetime('now'))
);

-- One row per (result_id) ever awarded, so re-polling never double-counts.
CREATE TABLE IF NOT EXISTS points_log (
    result_id    INTEGER PRIMARY KEY,
    discord_id   INTEGER NOT NULL,
    faction_name TEXT NOT NULL,
    points       REAL NOT NULL,
    placement    INTEGER,
    event_date   TEXT,
    event_type   TEXT,
    store_name   TEXT,
    reason       TEXT,
    awarded_at   TEXT DEFAULT (datetime('now'))
);

-- Tournaments fully processed by sync_points, so we don't re-fetch their
-- full standings (/api/tournament/{id}) on every sync run.
CREATE TABLE IF NOT EXISTS synced_tournaments (
    tournament_id INTEGER PRIMARY KEY,
    synced_at     TEXT DEFAULT (datetime('now'))
);

-- Tournament IDs an admin has explicitly blocked from ever awarding
-- points, checked by both auto-sync and the manual log-tournament-id command.
CREATE TABLE IF NOT EXISTS excluded_tournaments (
    tournament_id INTEGER PRIMARY KEY,
    excluded_by   INTEGER,
    reason        TEXT,
    excluded_at   TEXT DEFAULT (datetime('now'))
);

CREATE TABLE IF NOT EXISTS settings (
    key   TEXT PRIMARY KEY,
    value TEXT
);
"""


class DB:
    def __init__(self, path: str = None):
        self.path = path or config.DB_PATH
        self._conn: aiosqlite.Connection | None = None

    async def connect(self):
        self._conn = await aiosqlite.connect(self.path)
        self._conn.row_factory = aiosqlite.Row
        await self._conn.executescript(SCHEMA)
        await self._conn.commit()

    async def close(self):
        if self._conn:
            await self._conn.close()

    @asynccontextmanager
    async def cursor(self):
        cur = await self._conn.cursor()
        try:
            yield cur
        finally:
            await cur.close()

    # ---- settings ----
    async def get_setting(self, key: str, default=None):
        async with self.cursor() as cur:
            await cur.execute("SELECT value FROM settings WHERE key = ?", (key,))
            row = await cur.fetchone()
            return row["value"] if row else default

    async def set_setting(self, key: str, value: str):
        await self._conn.execute(
            "INSERT INTO settings (key, value) VALUES (?, ?) "
            "ON CONFLICT(key) DO UPDATE SET value = excluded.value",
            (key, value),
        )
        await self._conn.commit()

    # ---- factions ----
    async def create_faction(self, name: str, emoji: str, created_by: int) -> bool:
        try:
            await self._conn.execute(
                "INSERT INTO factions (name, emoji, created_by) VALUES (?, ?, ?)",
                (name, emoji, created_by),
            )
            await self._conn.commit()
            return True
        except aiosqlite.IntegrityError:
            return False

    async def delete_faction(self, name: str) -> bool:
        cur = await self._conn.execute("DELETE FROM factions WHERE name = ?", (name,))
        await self._conn.commit()
        return cur.rowcount > 0

    async def list_factions(self):
        async with self.cursor() as cur:
            await cur.execute("SELECT * FROM factions ORDER BY name")
            return await cur.fetchall()

    async def get_faction(self, name: str):
        async with self.cursor() as cur:
            await cur.execute("SELECT * FROM factions WHERE name = ?", (name,))
            return await cur.fetchone()

    async def get_faction_by_emoji(self, emoji: str):
        async with self.cursor() as cur:
            await cur.execute("SELECT * FROM factions WHERE emoji = ? AND emoji != ''", (emoji,))
            return await cur.fetchone()

    async def set_faction_emoji(self, name: str, emoji: str) -> bool:
        cur = await self._conn.execute(
            "UPDATE factions SET emoji = ? WHERE name = ?", (emoji, name)
        )
        await self._conn.commit()
        return cur.rowcount > 0

    async def ensure_factions(self, names: list[str]):
        """Seed factions that don't exist yet. Safe to call every startup."""
        for name in names:
            await self._conn.execute(
                "INSERT OR IGNORE INTO factions (name, emoji, created_by) VALUES (?, '', NULL)",
                (name,),
            )
        await self._conn.commit()

    # ---- membership ----
    async def join_faction(self, discord_id: int, faction_name: str):
        await self._conn.execute(
            "INSERT INTO members (discord_id, faction_name) VALUES (?, ?) "
            "ON CONFLICT(discord_id) DO UPDATE SET faction_name = excluded.faction_name, "
            "joined_at = datetime('now')",
            (discord_id, faction_name),
        )
        await self._conn.commit()

    async def switch_faction(self, discord_id: int, new_faction_name: str) -> bool:
        """
        Joins discord_id into new_faction_name. If they were already in a
        DIFFERENT faction, their accumulated points_log history is wiped
        first — switching factions means starting over at 0, not carrying
        points across (and not leaving them credited to the faction they
        just left, either). Returns True if this was an actual switch
        (points got reset); False if they joined fresh or re-picked the
        faction they were already in (no-op, nothing reset).
        """
        current = await self.get_member_faction(discord_id)
        reset = bool(current and current != new_faction_name)
        if reset:
            await self._conn.execute("DELETE FROM points_log WHERE discord_id = ?", (discord_id,))
            await self._conn.commit()
        await self.join_faction(discord_id, new_faction_name)
        return reset

    async def leave_faction(self, discord_id: int):
        cur = await self._conn.execute("DELETE FROM members WHERE discord_id = ?", (discord_id,))
        await self._conn.commit()
        return cur.rowcount > 0

    async def get_member_faction(self, discord_id: int):
        async with self.cursor() as cur:
            await cur.execute("SELECT faction_name FROM members WHERE discord_id = ?", (discord_id,))
            row = await cur.fetchone()
            return row["faction_name"] if row else None

    async def faction_members(self, faction_name: str):
        async with self.cursor() as cur:
            await cur.execute("SELECT discord_id FROM members WHERE faction_name = ?", (faction_name,))
            return [r["discord_id"] for r in await cur.fetchall()]

    # ---- registration (discord user <-> digilab player) ----
    async def register_player(self, discord_id: int, player_id: int, player_name: str, player_slug: str):
        await self._conn.execute(
            "INSERT INTO registrations (discord_id, player_id, player_name, player_slug) "
            "VALUES (?, ?, ?, ?) ON CONFLICT(discord_id) DO UPDATE SET "
            "player_id = excluded.player_id, player_name = excluded.player_name, "
            "player_slug = excluded.player_slug, registered_at = datetime('now')",
            (discord_id, player_id, player_name, player_slug),
        )
        await self._conn.commit()

    async def get_registration(self, discord_id: int):
        async with self.cursor() as cur:
            await cur.execute("SELECT * FROM registrations WHERE discord_id = ?", (discord_id,))
            return await cur.fetchone()

    async def get_registration_by_player_name(self, player_name: str):
        """Case-insensitive exact match on the linked DigiLab player name."""
        async with self.cursor() as cur:
            await cur.execute(
                "SELECT * FROM registrations WHERE LOWER(player_name) = LOWER(?)", (player_name.strip(),)
            )
            return await cur.fetchone()

    async def get_discord_id_for_slug(self, player_slug: str):
        async with self.cursor() as cur:
            await cur.execute(
                "SELECT discord_id FROM registrations WHERE player_slug = ?", (player_slug,)
            )
            row = await cur.fetchone()
            return row["discord_id"] if row else None

    async def all_registrations(self):
        async with self.cursor() as cur:
            await cur.execute("SELECT * FROM registrations")
            return await cur.fetchall()

    # ---- points ----
    async def already_awarded(self, result_id: int) -> bool:
        async with self.cursor() as cur:
            await cur.execute("SELECT 1 FROM points_log WHERE result_id = ?", (result_id,))
            return (await cur.fetchone()) is not None

    async def tournament_already_synced(self, tournament_id: int) -> bool:
        async with self.cursor() as cur:
            await cur.execute("SELECT 1 FROM synced_tournaments WHERE tournament_id = ?", (tournament_id,))
            return (await cur.fetchone()) is not None

    async def mark_tournament_synced(self, tournament_id: int):
        await self._conn.execute(
            "INSERT OR IGNORE INTO synced_tournaments (tournament_id) VALUES (?)", (tournament_id,)
        )
        await self._conn.commit()

    async def clear_all_points(self) -> int:
        """Wipes every points_log row (leaderboard reset). Does NOT touch
        synced_tournaments, so auto-sync won't immediately re-award the
        wiped points on its next run. Returns how many rows were deleted."""
        async with self.cursor() as cur:
            await cur.execute("SELECT COUNT(*) AS c FROM points_log")
            count = (await cur.fetchone())["c"]
        await self._conn.execute("DELETE FROM points_log")
        await self._conn.commit()
        return count

    # ---- tournament exclusions ----
    async def exclude_tournament(self, tournament_id: int, excluded_by: int, reason: str = ""):
        await self._conn.execute(
            "INSERT INTO excluded_tournaments (tournament_id, excluded_by, reason) VALUES (?, ?, ?) "
            "ON CONFLICT(tournament_id) DO UPDATE SET reason = excluded.reason, "
            "excluded_by = excluded.excluded_by, excluded_at = datetime('now')",
            (tournament_id, excluded_by, reason),
        )
        await self._conn.commit()

    async def include_tournament(self, tournament_id: int) -> bool:
        """Removes a tournament from the exclusion list, and clears its
        synced marker too — otherwise it'd stay permanently skipped by
        auto-sync even after being un-excluded. Returns True if it had
        actually been on the exclusion list."""
        cur = await self._conn.execute(
            "DELETE FROM excluded_tournaments WHERE tournament_id = ?", (tournament_id,)
        )
        was_excluded = cur.rowcount > 0
        await self._conn.execute(
            "DELETE FROM synced_tournaments WHERE tournament_id = ?", (tournament_id,)
        )
        await self._conn.commit()
        return was_excluded

    async def is_tournament_excluded(self, tournament_id: int) -> bool:
        async with self.cursor() as cur:
            await cur.execute("SELECT 1 FROM excluded_tournaments WHERE tournament_id = ?", (tournament_id,))
            return (await cur.fetchone()) is not None

    async def get_excluded_tournament_ids(self) -> set:
        async with self.cursor() as cur:
            await cur.execute("SELECT tournament_id FROM excluded_tournaments")
            return {r["tournament_id"] for r in await cur.fetchall()}

    async def list_excluded_tournaments(self):
        async with self.cursor() as cur:
            await cur.execute("SELECT * FROM excluded_tournaments ORDER BY excluded_at DESC")
            return await cur.fetchall()

    async def award_points(self, result_id, discord_id, faction_name, points,
                            placement=None, event_date=None, event_type=None,
                            store_name=None, reason=None):
        await self._conn.execute(
            "INSERT OR IGNORE INTO points_log "
            "(result_id, discord_id, faction_name, points, placement, event_date, "
            "event_type, store_name, reason) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)",
            (result_id, discord_id, faction_name, points, placement, event_date,
             event_type, store_name, reason),
        )
        await self._conn.commit()

    async def manual_award(self, discord_id: int, faction_name: str, points: float, reason: str):
        # Manual awards use a negative synthetic result_id so they never collide
        # with real DigiLab result_ids.
        async with self.cursor() as cur:
            await cur.execute("SELECT MIN(result_id) AS m FROM points_log")
            row = await cur.fetchone()
            next_id = min(-1, (row["m"] or 0) - 1)
        await self.award_points(next_id, discord_id, faction_name, points, reason=reason)
        return next_id

    async def faction_totals(self):
        async with self.cursor() as cur:
            await cur.execute(
                "SELECT faction_name, COALESCE(SUM(points), 0) AS total "
                "FROM points_log GROUP BY faction_name"
            )
            totals = {r["faction_name"]: r["total"] for r in await cur.fetchall()}
        factions = await self.list_factions()
        return {f["name"]: totals.get(f["name"], 0.0) for f in factions}

    async def member_totals(self, faction_name: str = None, limit: int = 10):
        query = (
            "SELECT discord_id, faction_name, COALESCE(SUM(points), 0) AS total "
            "FROM points_log"
        )
        params = ()
        if faction_name:
            query += " WHERE faction_name = ?"
            params = (faction_name,)
        query += " GROUP BY discord_id ORDER BY total DESC LIMIT ?"
        params = params + (limit,)
        async with self.cursor() as cur:
            await cur.execute(query, params)
            return await cur.fetchall()

    async def user_total(self, discord_id: int):
        async with self.cursor() as cur:
            await cur.execute(
                "SELECT COALESCE(SUM(points), 0) AS total FROM points_log WHERE discord_id = ?",
                (discord_id,),
            )
            row = await cur.fetchone()
            return row["total"] or 0.0

    async def user_history(self, discord_id: int, limit: int = 10):
        async with self.cursor() as cur:
            await cur.execute(
                "SELECT * FROM points_log WHERE discord_id = ? ORDER BY awarded_at DESC LIMIT ?",
                (discord_id, limit),
            )
            return await cur.fetchall()
