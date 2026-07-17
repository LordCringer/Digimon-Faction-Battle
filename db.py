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
