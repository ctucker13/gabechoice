import json
import aiosqlite
from datetime import datetime, timezone


CREATE_TABLE = """
CREATE TABLE IF NOT EXISTS games (
    appid INTEGER PRIMARY KEY,
    payload TEXT NOT NULL,
    cached_at TEXT NOT NULL
);
"""
CREATE_INDEX = "CREATE INDEX IF NOT EXISTS idx_cached_at ON games(cached_at);"


class GameCache:
    def __init__(self, db_path: str):
        self.db_path = db_path

    async def init_db(self) -> None:
        async with aiosqlite.connect(self.db_path) as db:
            await db.execute(CREATE_TABLE)
            await db.execute(CREATE_INDEX)
            await db.commit()

    async def get(self, appid: int) -> dict | None:
        async with aiosqlite.connect(self.db_path) as db:
            async with db.execute(
                "SELECT payload FROM games WHERE appid = ?", (appid,)
            ) as cursor:
                row = await cursor.fetchone()
        return json.loads(row[0]) if row else None

    async def set(self, appid: int, enrichment: dict) -> None:
        now = datetime.now(timezone.utc).isoformat()
        payload = {**enrichment, "cached_at": now}
        async with aiosqlite.connect(self.db_path) as db:
            await db.execute(
                "INSERT OR REPLACE INTO games (appid, payload, cached_at) VALUES (?, ?, ?)",
                (appid, json.dumps(payload), now),
            )
            await db.commit()

    async def get_many(self, appids: list[int]) -> dict[int, dict]:
        if not appids:
            return {}
        placeholders = ",".join("?" * len(appids))
        async with aiosqlite.connect(self.db_path) as db:
            async with db.execute(
                f"SELECT appid, payload FROM games WHERE appid IN ({placeholders})",
                appids,
            ) as cursor:
                rows = await cursor.fetchall()
        return {row[0]: json.loads(row[1]) for row in rows}

    async def prune_stale(self, ttl_days: int) -> int:
        from datetime import timedelta
        cutoff_str = (datetime.now(timezone.utc) - timedelta(days=ttl_days)).isoformat()
        async with aiosqlite.connect(self.db_path) as db:
            cursor = await db.execute(
                "DELETE FROM games WHERE cached_at < ?", (cutoff_str,)
            )
            count = cursor.rowcount
            await db.commit()
        return count
