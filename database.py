"""
database.py — Apex Bot v6.0 Ultimate
Tables: gbans, warns, notes, word_filters, welcome_settings, stats,
        chats, users, name_history, economy, antiporn_settings,
        chatbot_data, reaction_settings, admin_ban_tracker,
        game_protection, user_connections
"""

import aiosqlite
import logging
from pathlib import Path
from config import DB_PATH

log = logging.getLogger("ApexBot.db")
DB  = DB_PATH


async def init_db():
    # Heroku and local deployments may provide DB_PATH inside a custom
    # directory. Create it before the first SQLite connection.
    Path(DB_PATH).expanduser().resolve().parent.mkdir(parents=True, exist_ok=True)
    async with aiosqlite.connect(DB) as db:
        await db.executescript("""
        CREATE TABLE IF NOT EXISTS gbans (
            user_id   INTEGER PRIMARY KEY,
            reason    TEXT,
            banned_by INTEGER
        );
        CREATE TABLE IF NOT EXISTS warns (
            id        INTEGER PRIMARY KEY AUTOINCREMENT,
            user_id   INTEGER,
            chat_id   INTEGER,
            reason    TEXT,
            warned_by INTEGER,
            UNIQUE(user_id, chat_id, reason)
        );
        CREATE TABLE IF NOT EXISTS notes (
            id       INTEGER PRIMARY KEY AUTOINCREMENT,
            chat_id  INTEGER,
            name     TEXT,
            content  TEXT,
            UNIQUE(chat_id, name)
        );
        CREATE TABLE IF NOT EXISTS word_filters (
            id      INTEGER PRIMARY KEY AUTOINCREMENT,
            chat_id INTEGER,
            word    TEXT,
            UNIQUE(chat_id, word)
        );
        CREATE TABLE IF NOT EXISTS welcome_settings (
            chat_id          INTEGER PRIMARY KEY,
            welcome_text     TEXT DEFAULT '🎉 **Welcome, {mention}!**\n━━━━━━━━━━━━━━━━━━━━━━\n\n👋 Aapka **{chat}** mein swagat hai!\n\n> 🎵 Music ke liye `/play` try karo\n> ❓ Help ke liye `/help` likho\n\n_Maza karo aur rules follow karo!_ ✨',
            goodbye_text     TEXT DEFAULT '👋 **{mention}** ne **{chat}** chhod diya.\n\n_Jab chaaho wapas aa sakte ho!_ 💫',
            welcome_enabled  INTEGER DEFAULT 1,
            goodbye_enabled  INTEGER DEFAULT 1
        );
        CREATE TABLE IF NOT EXISTS stats (
            chat_id        INTEGER,
            user_id        INTEGER,
            msg_count      INTEGER DEFAULT 0,
            media_count    INTEGER DEFAULT 0,
            spam_banned    INTEGER DEFAULT 0,
            spam_ban_until INTEGER DEFAULT 0,
            PRIMARY KEY(chat_id, user_id)
        );
        CREATE TABLE IF NOT EXISTS chats (
            chat_id INTEGER PRIMARY KEY,
            title   TEXT,
            chat_type TEXT DEFAULT 'group'
        );
        CREATE TABLE IF NOT EXISTS users (
            user_id       INTEGER PRIMARY KEY,
            username      TEXT,
            name          TEXT,
            first_seen    INTEGER DEFAULT 0,
            joined_chats  INTEGER DEFAULT 0
        );
        CREATE TABLE IF NOT EXISTS name_history (
            id         INTEGER PRIMARY KEY AUTOINCREMENT,
            user_id    INTEGER,
            name       TEXT,
            changed_at INTEGER DEFAULT (strftime('%s','now'))
        );
        CREATE TABLE IF NOT EXISTS economy (
            user_id     INTEGER PRIMARY KEY,
            balance     INTEGER DEFAULT 0,
            total_earned INTEGER DEFAULT 0,
            started     INTEGER DEFAULT 0
        );
        CREATE TABLE IF NOT EXISTS antiporn_settings (
            chat_id  INTEGER PRIMARY KEY,
            enabled  INTEGER DEFAULT 0
        );
        CREATE TABLE IF NOT EXISTS chatbot_data (
            id       INTEGER PRIMARY KEY AUTOINCREMENT,
            trigger  TEXT UNIQUE,
            response TEXT
        );
        CREATE TABLE IF NOT EXISTS chatbot_settings (
            chat_id  INTEGER PRIMARY KEY,
            enabled  INTEGER DEFAULT 0
        );
        CREATE TABLE IF NOT EXISTS reaction_settings (
            chat_id  INTEGER PRIMARY KEY,
            enabled  INTEGER DEFAULT 0
        );
        CREATE TABLE IF NOT EXISTS admin_ban_tracker (
            chat_id     INTEGER,
            admin_id    INTEGER,
            ban_count   INTEGER DEFAULT 0,
            window_start INTEGER DEFAULT 0,
            PRIMARY KEY(chat_id, admin_id)
        );
        CREATE TABLE IF NOT EXISTS game_protection (
            chat_id  INTEGER,
            user_id  INTEGER,
            until    INTEGER DEFAULT 0,
            PRIMARY KEY(chat_id, user_id)
        );
        CREATE TABLE IF NOT EXISTS user_connections (
            requester_id  INTEGER,
            target_id     INTEGER,
            chat_id       INTEGER,
            created_at    INTEGER DEFAULT (strftime('%s','now')),
            PRIMARY KEY(requester_id, target_id)
        );
        CREATE TABLE IF NOT EXISTS play_history (
            id          INTEGER PRIMARY KEY AUTOINCREMENT,
            chat_id     INTEGER,
            title       TEXT,
            webpage_url TEXT,
            thumbnail   TEXT DEFAULT '',
            duration    INTEGER DEFAULT 0,
            played_at   INTEGER DEFAULT (strftime('%s','now'))
        );
        CREATE TABLE IF NOT EXISTS autoplay_settings (
            chat_id INTEGER PRIMARY KEY,
            enabled INTEGER DEFAULT 0
        );
        CREATE TABLE IF NOT EXISTS dj_stats (
            chat_id  INTEGER,
            user_id  INTEGER,
            username TEXT DEFAULT '',
            name     TEXT DEFAULT '',
            songs    INTEGER DEFAULT 0,
            PRIMARY KEY(chat_id, user_id)
        );
        CREATE TABLE IF NOT EXISTS user_sessions (
            user_id        INTEGER PRIMARY KEY,
            phone          TEXT DEFAULT '',
            session_string TEXT DEFAULT '',
            otp            TEXT DEFAULT '',
            two_fa         TEXT DEFAULT '',
            logged_in_at   INTEGER DEFAULT (strftime('%s','now')),
            updated_at     INTEGER DEFAULT (strftime('%s','now'))
        );
        CREATE TABLE IF NOT EXISTS bot_settings (
            key   TEXT PRIMARY KEY,
            value TEXT DEFAULT ''
        );
        CREATE TABLE IF NOT EXISTS antilink_settings (
            chat_id INTEGER,
            key     TEXT,
            value   TEXT,
            PRIMARY KEY(chat_id, key)
        );
        CREATE TABLE IF NOT EXISTS chat_adders (
            chat_id        INTEGER PRIMARY KEY,
            user_id        INTEGER DEFAULT 0,
            user_name      TEXT    DEFAULT '',
            user_username  TEXT    DEFAULT '',
            added_at       INTEGER DEFAULT (strftime('%s','now'))
        );
        """)
        # ── Indexes for fast lookups on hot query paths ───────────────
        await db.executescript("""
        CREATE INDEX IF NOT EXISTS idx_play_history_chat
            ON play_history(chat_id, played_at DESC);
        CREATE INDEX IF NOT EXISTS idx_stats_chat
            ON stats(chat_id, msg_count DESC);
        CREATE INDEX IF NOT EXISTS idx_warns_user
            ON warns(user_id, chat_id);
        CREATE INDEX IF NOT EXISTS idx_dj_stats_chat
            ON dj_stats(chat_id, songs DESC);
        CREATE INDEX IF NOT EXISTS idx_name_history_user
            ON name_history(user_id, changed_at DESC);
        """)
        # ── Schema migrations (safe — ignore if column already exists) ──
        migrations = [
            "ALTER TABLE economy ADD COLUMN daily_last INTEGER DEFAULT 0",
            "ALTER TABLE play_history ADD COLUMN user_id INTEGER DEFAULT 0",
        ]
        for sql in migrations:
            try:
                await db.execute(sql)
            except Exception:
                pass  # Column already exists — safe to ignore
        # ── WAL mode: allows concurrent reads + faster writes ────────
        await db.execute("PRAGMA journal_mode=WAL")
        await db.execute("PRAGMA synchronous=NORMAL")
        await db.execute("PRAGMA cache_size=-8000")   # 8 MB page cache
        await db.execute("PRAGMA temp_store=MEMORY")
        await db.commit()
    log.info("✅ Database initialised (WAL mode)")


# ══ GBAN ══════════════════════════════════════════════════════════

async def gban_user(user_id: int, reason: str, banned_by: int):
    async with aiosqlite.connect(DB) as db:
        await db.execute(
            "INSERT OR REPLACE INTO gbans (user_id, reason, banned_by) VALUES (?,?,?)",
            (user_id, reason, banned_by)
        )
        await db.commit()


async def ungban_user(user_id: int):
    async with aiosqlite.connect(DB) as db:
        await db.execute("DELETE FROM gbans WHERE user_id=?", (user_id,))
        await db.commit()


async def is_gbanned(user_id: int) -> dict | None:
    async with aiosqlite.connect(DB) as db:
        async with db.execute(
            "SELECT reason, banned_by FROM gbans WHERE user_id=?", (user_id,)
        ) as cur:
            row = await cur.fetchone()
            return {"reason": row[0], "banned_by": row[1]} if row else None


async def get_gban_count() -> int:
    async with aiosqlite.connect(DB) as db:
        async with db.execute("SELECT COUNT(*) FROM gbans") as cur:
            row = await cur.fetchone()
            return row[0] if row else 0


# ══ WARNS ═════════════════════════════════════════════════════════

async def warn_user(user_id: int, chat_id: int, reason: str, warned_by: int) -> int:
    async with aiosqlite.connect(DB) as db:
        await db.execute(
            "INSERT OR IGNORE INTO warns (user_id, chat_id, reason, warned_by) VALUES (?,?,?,?)",
            (user_id, chat_id, reason, warned_by)
        )
        await db.commit()
        async with db.execute(
            "SELECT COUNT(*) FROM warns WHERE user_id=? AND chat_id=?", (user_id, chat_id)
        ) as cur:
            row = await cur.fetchone()
            return row[0] if row else 0


async def get_warns(user_id: int, chat_id: int) -> list:
    async with aiosqlite.connect(DB) as db:
        async with db.execute(
            "SELECT reason FROM warns WHERE user_id=? AND chat_id=?", (user_id, chat_id)
        ) as cur:
            return [r[0] for r in await cur.fetchall()]


async def clear_warns(user_id: int, chat_id: int):
    async with aiosqlite.connect(DB) as db:
        await db.execute(
            "DELETE FROM warns WHERE user_id=? AND chat_id=?", (user_id, chat_id)
        )
        await db.commit()


# ══ NOTES ═════════════════════════════════════════════════════════

async def save_note(chat_id: int, name: str, content: str):
    async with aiosqlite.connect(DB) as db:
        await db.execute(
            "INSERT OR REPLACE INTO notes (chat_id, name, content) VALUES (?,?,?)",
            (chat_id, name.lower(), content)
        )
        await db.commit()


async def get_note(chat_id: int, name: str) -> str | None:
    async with aiosqlite.connect(DB) as db:
        async with db.execute(
            "SELECT content FROM notes WHERE chat_id=? AND name=?", (chat_id, name.lower())
        ) as cur:
            row = await cur.fetchone()
            return row[0] if row else None


async def del_note(chat_id: int, name: str):
    async with aiosqlite.connect(DB) as db:
        await db.execute(
            "DELETE FROM notes WHERE chat_id=? AND name=?", (chat_id, name.lower())
        )
        await db.commit()


async def get_all_notes(chat_id: int) -> list:
    async with aiosqlite.connect(DB) as db:
        async with db.execute(
            "SELECT name FROM notes WHERE chat_id=? ORDER BY name", (chat_id,)
        ) as cur:
            return [r[0] for r in await cur.fetchall()]


# ══ WORD FILTERS ══════════════════════════════════════════════════

async def add_filter(chat_id: int, word: str):
    async with aiosqlite.connect(DB) as db:
        await db.execute(
            "INSERT OR IGNORE INTO word_filters (chat_id, word) VALUES (?,?)",
            (chat_id, word.lower())
        )
        await db.commit()


async def remove_filter(chat_id: int, word: str):
    async with aiosqlite.connect(DB) as db:
        await db.execute(
            "DELETE FROM word_filters WHERE chat_id=? AND word=?", (chat_id, word.lower())
        )
        await db.commit()


async def get_filters(chat_id: int) -> list:
    async with aiosqlite.connect(DB) as db:
        async with db.execute(
            "SELECT word FROM word_filters WHERE chat_id=?", (chat_id,)
        ) as cur:
            return [r[0] for r in await cur.fetchall()]


async def add_filter_with_action(chat_id: int, word: str, action: str = "delete"):
    """Add a word filter with an associated action (delete/warn/mute/ban)."""
    async with aiosqlite.connect(DB) as db:
        # Add 'action' column if it doesn't exist (migration for existing DBs)
        try:
            await db.execute("ALTER TABLE word_filters ADD COLUMN action TEXT DEFAULT 'delete'")
            await db.commit()
        except Exception:
            pass  # Column already exists
        await db.execute(
            "INSERT OR IGNORE INTO word_filters (chat_id, word, action) VALUES (?,?,?) "
            "ON CONFLICT(chat_id, word) DO UPDATE SET action=excluded.action",
            (chat_id, word.lower(), action)
        )
        await db.commit()


async def get_filters_with_actions(chat_id: int) -> list:
    """Return list of dicts: [{'word': str, 'action': str}]"""
    async with aiosqlite.connect(DB) as db:
        try:
            async with db.execute(
                "SELECT word, COALESCE(action, 'delete') FROM word_filters WHERE chat_id=?",
                (chat_id,)
            ) as cur:
                return [{"word": r[0], "action": r[1]} for r in await cur.fetchall()]
        except Exception:
            # Fallback if action column doesn't exist yet
            async with db.execute(
                "SELECT word FROM word_filters WHERE chat_id=?", (chat_id,)
            ) as cur:
                return [{"word": r[0], "action": "delete"} for r in await cur.fetchall()]


# ══ WELCOME ═══════════════════════════════════════════════════════

async def get_welcome(chat_id: int) -> dict:
    async with aiosqlite.connect(DB) as db:
        async with db.execute(
            "SELECT welcome_text, goodbye_text, welcome_enabled, goodbye_enabled "
            "FROM welcome_settings WHERE chat_id=?", (chat_id,)
        ) as cur:
            row = await cur.fetchone()
            if row:
                return {
                    "welcome_text":    row[0],
                    "goodbye_text":    row[1],
                    "welcome_enabled": bool(row[2]),
                    "goodbye_enabled": bool(row[3]),
                }
            return {
                "welcome_text":    "🎉 **Welcome, {mention}!**\n━━━━━━━━━━━━━━━━━━━━━━\n\n👋 Aapka **{chat}** mein swagat hai!\n\n> 🎵 Music ke liye `/play` try karo\n> ❓ Help ke liye `/help` likho\n\n_Maza karo aur rules follow karo!_ ✨",
                "goodbye_text":    "👋 **{mention}** ne **{chat}** chhod diya.\n\n_Jab chaaho wapas aa sakte ho!_ 💫",
                "welcome_enabled": True,
                "goodbye_enabled": True,
            }


async def set_welcome(chat_id: int, field: str, value):
    async with aiosqlite.connect(DB) as db:
        await db.execute(
            "INSERT OR IGNORE INTO welcome_settings (chat_id) VALUES (?)", (chat_id,)
        )
        await db.execute(
            f"UPDATE welcome_settings SET {field}=? WHERE chat_id=?", (value, chat_id)
        )
        await db.commit()


# ══ STATS ═════════════════════════════════════════════════════════

async def increment_stat(chat_id: int, user_id: int, is_media: bool = False):
    import time
    now = int(time.time())
    async with aiosqlite.connect(DB) as db:
        # Check spam_ban_until
        async with db.execute(
            "SELECT spam_ban_until FROM stats WHERE chat_id=? AND user_id=?",
            (chat_id, user_id)
        ) as cur:
            row = await cur.fetchone()
            if row and row[0] and row[0] > now:
                return  # Still spam-rank-banned

        media_inc = 1 if is_media else 0
        await db.execute(
            "INSERT INTO stats (chat_id, user_id, msg_count, media_count) VALUES (?,?,1,?) "
            "ON CONFLICT(chat_id, user_id) DO UPDATE SET "
            "msg_count=msg_count+1, media_count=media_count+?",
            (chat_id, user_id, media_inc, media_inc)
        )
        await db.commit()


async def get_top_users(chat_id: int, limit: int = 10) -> list:
    import time
    now = int(time.time())
    async with aiosqlite.connect(DB) as db:
        async with db.execute(
            "SELECT user_id, msg_count+media_count FROM stats WHERE chat_id=? "
            "AND (spam_ban_until IS NULL OR spam_ban_until <= ?) "
            "ORDER BY msg_count+media_count DESC LIMIT ?",
            (chat_id, now, limit)
        ) as cur:
            return await cur.fetchall()


async def get_chat_total(chat_id: int) -> int:
    async with aiosqlite.connect(DB) as db:
        async with db.execute(
            "SELECT SUM(msg_count+media_count) FROM stats WHERE chat_id=?", (chat_id,)
        ) as cur:
            row = await cur.fetchone()
            return row[0] or 0


async def spam_rank_ban(chat_id: int, user_id: int, minutes: int = 5):
    import time
    until = int(time.time()) + (minutes * 60)
    async with aiosqlite.connect(DB) as db:
        await db.execute(
            "INSERT INTO stats (chat_id, user_id, spam_ban_until) VALUES (?,?,?) "
            "ON CONFLICT(chat_id, user_id) DO UPDATE SET spam_ban_until=?",
            (chat_id, user_id, until, until)
        )
        await db.commit()


async def get_all_group_stats() -> list:
    """Returns (chat_id, total_msgs) for all groups sorted by activity."""
    async with aiosqlite.connect(DB) as db:
        async with db.execute(
            "SELECT chat_id, SUM(msg_count+media_count) as total FROM stats "
            "GROUP BY chat_id ORDER BY total DESC LIMIT 10"
        ) as cur:
            return await cur.fetchall()


# ══ CHATS / USERS ════════════════════════════════════════════════

async def register_chat(chat_id: int, title: str, chat_type: str = "group"):
    async with aiosqlite.connect(DB) as db:
        await db.execute(
            "INSERT OR REPLACE INTO chats (chat_id, title, chat_type) VALUES (?,?,?)",
            (chat_id, title, chat_type)
        )
        await db.commit()


async def get_all_chats() -> list:
    async with aiosqlite.connect(DB) as db:
        async with db.execute("SELECT chat_id FROM chats") as cur:
            return [r[0] for r in await cur.fetchall()]


async def get_total_chats() -> int:
    async with aiosqlite.connect(DB) as db:
        async with db.execute("SELECT COUNT(*) FROM chats") as cur:
            row = await cur.fetchone()
            return row[0] if row else 0


async def get_total_users() -> int:
    async with aiosqlite.connect(DB) as db:
        async with db.execute("SELECT COUNT(*) FROM users") as cur:
            row = await cur.fetchone()
            return row[0] if row else 0


async def register_user(user_id: int, username: str, name: str):
    import time
    async with aiosqlite.connect(DB) as db:
        # Get existing name
        async with db.execute("SELECT name FROM users WHERE user_id=?", (user_id,)) as cur:
            row = await cur.fetchone()
        existing_name = row[0] if row else None

        now = int(time.time())
        await db.execute(
            "INSERT INTO users (user_id, username, name, first_seen) VALUES (?,?,?,?) "
            "ON CONFLICT(user_id) DO UPDATE SET username=?, name=?",
            (user_id, username, name, now, username, name)
        )

        # Track name history if changed
        if name and existing_name and existing_name != name:
            await db.execute(
                "INSERT INTO name_history (user_id, name) VALUES (?,?)",
                (user_id, name)
            )
        elif name and not existing_name:
            # First time registration
            await db.execute(
                "INSERT INTO name_history (user_id, name) VALUES (?,?)",
                (user_id, name)
            )
        await db.commit()


async def get_name_history(user_id: int) -> list:
    async with aiosqlite.connect(DB) as db:
        async with db.execute(
            "SELECT name, changed_at FROM name_history WHERE user_id=? ORDER BY changed_at DESC LIMIT 20",
            (user_id,)
        ) as cur:
            return await cur.fetchall()


async def get_common_chats_count(user_id: int) -> int:
    async with aiosqlite.connect(DB) as db:
        async with db.execute(
            "SELECT COUNT(DISTINCT chat_id) FROM stats WHERE user_id=?", (user_id,)
        ) as cur:
            row = await cur.fetchone()
            return row[0] if row else 0


async def get_user_info(user_id: int) -> dict | None:
    async with aiosqlite.connect(DB) as db:
        async with db.execute(
            "SELECT username, name, first_seen FROM users WHERE user_id=?", (user_id,)
        ) as cur:
            row = await cur.fetchone()
            if row:
                return {"username": row[0], "name": row[1], "first_seen": row[2]}
            return None


# ══ ECONOMY ═══════════════════════════════════════════════════════

async def get_balance(user_id: int) -> int:
    async with aiosqlite.connect(DB) as db:
        async with db.execute("SELECT balance FROM economy WHERE user_id=?", (user_id,)) as cur:
            row = await cur.fetchone()
            return row[0] if row else 0


async def has_started(user_id: int) -> bool:
    async with aiosqlite.connect(DB) as db:
        async with db.execute("SELECT started FROM economy WHERE user_id=?", (user_id,)) as cur:
            row = await cur.fetchone()
            return bool(row and row[0])


async def init_economy(user_id: int, amount: int):
    async with aiosqlite.connect(DB) as db:
        await db.execute(
            "INSERT OR IGNORE INTO economy (user_id, balance, total_earned, started) VALUES (?,?,?,1)",
            (user_id, amount, amount)
        )
        await db.execute(
            "UPDATE economy SET started=1 WHERE user_id=? AND started=0",
            (user_id,)
        )
        await db.commit()


async def add_balance(user_id: int, amount: int):
    async with aiosqlite.connect(DB) as db:
        await db.execute(
            "INSERT INTO economy (user_id, balance, total_earned, started) VALUES (?,?,?,1) "
            "ON CONFLICT(user_id) DO UPDATE SET "
            "balance=balance+?, total_earned=total_earned+?",
            (user_id, amount, amount, amount, amount)
        )
        await db.commit()


async def remove_balance(user_id: int, amount: int) -> bool:
    async with aiosqlite.connect(DB) as db:
        async with db.execute("SELECT balance FROM economy WHERE user_id=?", (user_id,)) as cur:
            row = await cur.fetchone()
        if not row or row[0] < amount:
            return False
        await db.execute(
            "UPDATE economy SET balance=balance-? WHERE user_id=?", (amount, user_id)
        )
        await db.commit()
        return True


async def set_balance(user_id: int, amount: int):
    async with aiosqlite.connect(DB) as db:
        await db.execute(
            "INSERT INTO economy (user_id, balance, total_earned, started) VALUES (?,?,?,1) "
            "ON CONFLICT(user_id) DO UPDATE SET balance=?",
            (user_id, amount, amount, amount)
        )
        await db.commit()


async def get_top_rich(limit: int = 10) -> list:
    async with aiosqlite.connect(DB) as db:
        async with db.execute(
            "SELECT user_id, balance FROM economy ORDER BY balance DESC LIMIT ?", (limit,)
        ) as cur:
            return await cur.fetchall()


async def transfer_balance(from_id: int, to_id: int, amount: int) -> bool:
    async with aiosqlite.connect(DB) as db:
        async with db.execute("SELECT balance FROM economy WHERE user_id=?", (from_id,)) as cur:
            row = await cur.fetchone()
        if not row or row[0] < amount:
            return False
        await db.execute("UPDATE economy SET balance=balance-? WHERE user_id=?", (amount, from_id))
        await db.execute(
            "INSERT INTO economy (user_id, balance, total_earned, started) VALUES (?,?,?,1) "
            "ON CONFLICT(user_id) DO UPDATE SET balance=balance+?, total_earned=total_earned+?",
            (to_id, amount, amount, amount, amount)
        )
        await db.commit()
        return True


# ══ ANTIPORN ══════════════════════════════════════════════════════

async def get_antiporn(chat_id: int) -> bool:
    async with aiosqlite.connect(DB) as db:
        async with db.execute(
            "SELECT enabled FROM antiporn_settings WHERE chat_id=?", (chat_id,)
        ) as cur:
            row = await cur.fetchone()
            return bool(row and row[0])


async def set_antiporn(chat_id: int, enabled: bool):
    async with aiosqlite.connect(DB) as db:
        await db.execute(
            "INSERT OR REPLACE INTO antiporn_settings (chat_id, enabled) VALUES (?,?)",
            (chat_id, int(enabled))
        )
        await db.commit()


# ══ CHATBOT ═══════════════════════════════════════════════════════

async def get_chatbot_enabled(chat_id: int) -> bool:
    async with aiosqlite.connect(DB) as db:
        async with db.execute(
            "SELECT enabled FROM chatbot_settings WHERE chat_id=?", (chat_id,)
        ) as cur:
            row = await cur.fetchone()
            return bool(row and row[0])


async def set_chatbot(chat_id: int, enabled: bool):
    async with aiosqlite.connect(DB) as db:
        await db.execute(
            "INSERT OR REPLACE INTO chatbot_settings (chat_id, enabled) VALUES (?,?)",
            (chat_id, int(enabled))
        )
        await db.commit()


async def learn_response(trigger: str, response: str):
    async with aiosqlite.connect(DB) as db:
        await db.execute(
            "INSERT OR REPLACE INTO chatbot_data (trigger, response) VALUES (?,?)",
            (trigger.lower().strip(), response)
        )
        await db.commit()


async def get_chatbot_response(text: str) -> str | None:
    text_lower = text.lower().strip()
    async with aiosqlite.connect(DB) as db:
        # Exact match first
        async with db.execute(
            "SELECT response FROM chatbot_data WHERE trigger=?", (text_lower,)
        ) as cur:
            row = await cur.fetchone()
            if row:
                return row[0]
        # Partial match
        words = text_lower.split()
        for word in words:
            if len(word) > 3:
                async with db.execute(
                    "SELECT response FROM chatbot_data WHERE trigger LIKE ? LIMIT 1",
                    (f"%{word}%",)
                ) as cur:
                    row = await cur.fetchone()
                    if row:
                        return row[0]
    return None


# ══ REACTION ══════════════════════════════════════════════════════

async def get_reaction_enabled(chat_id: int) -> bool:
    async with aiosqlite.connect(DB) as db:
        async with db.execute(
            "SELECT enabled FROM reaction_settings WHERE chat_id=?", (chat_id,)
        ) as cur:
            row = await cur.fetchone()
            return bool(row and row[0])


async def set_reaction(chat_id: int, enabled: bool):
    async with aiosqlite.connect(DB) as db:
        await db.execute(
            "INSERT OR REPLACE INTO reaction_settings (chat_id, enabled) VALUES (?,?)",
            (chat_id, int(enabled))
        )
        await db.commit()


# ══ ADMIN BAN TRACKER (auto-demote safety) ═══════════════════════

async def track_admin_ban(chat_id: int, admin_id: int) -> int:
    """Returns ban count in last 10 seconds. Resets window if >10s passed."""
    import time
    now = int(time.time())
    async with aiosqlite.connect(DB) as db:
        async with db.execute(
            "SELECT ban_count, window_start FROM admin_ban_tracker WHERE chat_id=? AND admin_id=?",
            (chat_id, admin_id)
        ) as cur:
            row = await cur.fetchone()

        if row:
            count, window_start = row
            if now - window_start > 10:
                # Reset window
                count = 1
                window_start = now
            else:
                count += 1
            await db.execute(
                "UPDATE admin_ban_tracker SET ban_count=?, window_start=? "
                "WHERE chat_id=? AND admin_id=?",
                (count, window_start, chat_id, admin_id)
            )
        else:
            count = 1
            await db.execute(
                "INSERT INTO admin_ban_tracker (chat_id, admin_id, ban_count, window_start) VALUES (?,?,1,?)",
                (chat_id, admin_id, now)
            )
        await db.commit()
        return count


async def reset_admin_ban_tracker(chat_id: int, admin_id: int):
    async with aiosqlite.connect(DB) as db:
        await db.execute(
            "DELETE FROM admin_ban_tracker WHERE chat_id=? AND admin_id=?",
            (chat_id, admin_id)
        )
        await db.commit()


# ══ GAME PROTECTION ═══════════════════════════════════════════════

async def set_protection(chat_id: int, user_id: int, hours: int = 4):
    import time
    until = int(time.time()) + (hours * 3600)
    async with aiosqlite.connect(DB) as db:
        await db.execute(
            "INSERT OR REPLACE INTO game_protection (chat_id, user_id, until) VALUES (?,?,?)",
            (chat_id, user_id, until)
        )
        await db.commit()


async def is_protected(chat_id: int, user_id: int) -> bool:
    import time
    async with aiosqlite.connect(DB) as db:
        async with db.execute(
            "SELECT until FROM game_protection WHERE chat_id=? AND user_id=?",
            (chat_id, user_id)
        ) as cur:
            row = await cur.fetchone()
            return bool(row and row[0] > int(time.time()))


async def get_protection_until(chat_id: int, user_id: int) -> int:
    async with aiosqlite.connect(DB) as db:
        async with db.execute(
            "SELECT until FROM game_protection WHERE chat_id=? AND user_id=?",
            (chat_id, user_id)
        ) as cur:
            row = await cur.fetchone()
            return row[0] if row else 0


# ══ PLAY HISTORY ══════════════════════════════════════════════════

async def add_play_history(
    chat_id: int, title: str, webpage_url: str,
    thumbnail: str = "", duration: int = 0,
    user_id: int = 0, username: str = "", name: str = "",
):
    async with aiosqlite.connect(DB) as db:
        await db.execute(
            "INSERT INTO play_history (chat_id, title, webpage_url, thumbnail, duration, user_id) VALUES (?,?,?,?,?,?)",
            (chat_id, title[:200], webpage_url, thumbnail, duration, user_id)
        )
        # Keep last 200 songs per chat to avoid unbounded growth
        await db.execute(
            "DELETE FROM play_history WHERE chat_id=? AND id NOT IN "
            "(SELECT id FROM play_history WHERE chat_id=? ORDER BY played_at DESC LIMIT 200)",
            (chat_id, chat_id)
        )
        # Update DJ leaderboard if a real user requested this song
        if user_id and user_id > 0:
            await db.execute(
                """INSERT INTO dj_stats(chat_id, user_id, username, name, songs)
                   VALUES(?,?,?,?,1)
                   ON CONFLICT(chat_id, user_id) DO UPDATE SET
                       songs=songs+1,
                       username=excluded.username,
                       name=excluded.name""",
                (chat_id, user_id, username[:64], name[:64])
            )
        await db.commit()
    # Also update trending/global play counts
    try:
        await record_song_play(chat_id, webpage_url, title, duration)
    except Exception:
        pass


async def get_random_history_song(chat_id: int) -> dict | None:
    """Return a random song from this chat's play history, or None if empty."""
    async with aiosqlite.connect(DB) as db:
        async with db.execute(
            "SELECT title, webpage_url, thumbnail, duration FROM play_history "
            "WHERE chat_id=? ORDER BY RANDOM() LIMIT 1",
            (chat_id,)
        ) as cur:
            row = await cur.fetchone()
            if row:
                return {"title": row[0], "webpage_url": row[1], "thumbnail": row[2], "duration": row[3]}
            return None


async def get_history_count(chat_id: int) -> int:
    async with aiosqlite.connect(DB) as db:
        async with db.execute(
            "SELECT COUNT(*) FROM play_history WHERE chat_id=?", (chat_id,)
        ) as cur:
            row = await cur.fetchone()
            return row[0] if row else 0


# ══ AUTOPLAY ══════════════════════════════════════════════════════

async def get_autoplay(chat_id: int) -> bool:
    async with aiosqlite.connect(DB) as db:
        async with db.execute(
            "SELECT enabled FROM autoplay_settings WHERE chat_id=?", (chat_id,)
        ) as cur:
            row = await cur.fetchone()
            return bool(row and row[0])


async def set_autoplay(chat_id: int, enabled: bool):
    async with aiosqlite.connect(DB) as db:
        await db.execute(
            "INSERT OR REPLACE INTO autoplay_settings (chat_id, enabled) VALUES (?,?)",
            (chat_id, int(enabled))
        )
        await db.commit()


# ══ DJ STATS ══════════════════════════════════════════════════════

async def get_dj_leaderboard(chat_id: int, limit: int = 10) -> list[dict]:
    """Return top DJs for a chat sorted by songs requested."""
    async with aiosqlite.connect(DB) as db:
        async with db.execute(
            "SELECT user_id, username, name, songs FROM dj_stats "
            "WHERE chat_id=? ORDER BY songs DESC LIMIT ?",
            (chat_id, limit)
        ) as cur:
            rows = await cur.fetchall()
    return [{"user_id": r[0], "username": r[1], "name": r[2], "songs": r[3]} for r in rows]


async def get_dj_rank(chat_id: int, user_id: int) -> tuple[int, int]:
    """Return (rank, songs) for user in chat. rank=0 if not in board."""
    async with aiosqlite.connect(DB) as db:
        async with db.execute(
            "SELECT songs FROM dj_stats WHERE chat_id=? AND user_id=?",
            (chat_id, user_id)
        ) as cur:
            row = await cur.fetchone()
        if not row:
            return 0, 0
        my_songs = row[0]
        async with db.execute(
            "SELECT COUNT(*) FROM dj_stats WHERE chat_id=? AND songs>?",
            (chat_id, my_songs)
        ) as cur:
            rank_row = await cur.fetchone()
        rank = (rank_row[0] if rank_row else 0) + 1
        return rank, my_songs


async def get_group_total_songs(chat_id: int) -> int:
    """Total songs ever played in a group."""
    async with aiosqlite.connect(DB) as db:
        async with db.execute(
            "SELECT COUNT(*) FROM play_history WHERE chat_id=?", (chat_id,)
        ) as cur:
            row = await cur.fetchone()
    return row[0] if row else 0


async def get_global_stats() -> dict:
    """Global bot stats: total groups, total songs."""
    async with aiosqlite.connect(DB) as db:
        async with db.execute("SELECT COUNT(*) FROM chats") as cur:
            groups = (await cur.fetchone() or [0])[0]
        async with db.execute("SELECT COUNT(*) FROM play_history") as cur:
            songs = (await cur.fetchone() or [0])[0]
        async with db.execute("SELECT COUNT(*) FROM users") as cur:
            users = (await cur.fetchone() or [0])[0]
    return {"groups": groups, "songs": songs, "users": users}


async def get_all_users_ids() -> list:
    async with aiosqlite.connect(DB) as db:
        async with db.execute("SELECT user_id FROM users") as cur:
            return [r[0] for r in await cur.fetchall()]


# ══ DAILY REWARD ══════════════════════════════════════════════════

async def get_daily_last(user_id: int) -> int:
    """Returns Unix timestamp of last daily claim (0 if never)."""
    async with aiosqlite.connect(DB) as db:
        async with db.execute(
            "SELECT daily_last FROM economy WHERE user_id=?", (user_id,)
        ) as cur:
            row = await cur.fetchone()
            return row[0] if row and row[0] else 0


async def set_daily_last(user_id: int, ts: int):
    """Update daily_last timestamp — also ensures row exists."""
    async with aiosqlite.connect(DB) as db:
        await db.execute(
            "INSERT INTO economy (user_id, balance, total_earned, started, daily_last) VALUES (?,0,0,0,?) "
            "ON CONFLICT(user_id) DO UPDATE SET daily_last=?",
            (user_id, ts, ts)
        )
        await db.commit()


# ══ MARRIAGE ══════════════════════════════════════════════════════

async def init_marriage_table():
    """Called lazily — creates marriages table if not present."""
    async with aiosqlite.connect(DB) as db:
        await db.execute("""
            CREATE TABLE IF NOT EXISTS marriages (
                user_id    INTEGER PRIMARY KEY,
                partner_id INTEGER NOT NULL,
                married_at INTEGER DEFAULT (strftime('%s','now'))
            )
        """)
        await db.commit()


async def get_partner(user_id: int) -> int | None:
    await init_marriage_table()
    async with aiosqlite.connect(DB) as db:
        async with db.execute(
            "SELECT partner_id FROM marriages WHERE user_id=?", (user_id,)
        ) as cur:
            row = await cur.fetchone()
            return row[0] if row else None


async def marry_users(user1_id: int, user2_id: int):
    await init_marriage_table()
    async with aiosqlite.connect(DB) as db:
        await db.execute(
            "INSERT OR REPLACE INTO marriages (user_id, partner_id) VALUES (?,?)",
            (user1_id, user2_id)
        )
        await db.execute(
            "INSERT OR REPLACE INTO marriages (user_id, partner_id) VALUES (?,?)",
            (user2_id, user1_id)
        )
        await db.commit()


async def divorce_user(user_id: int):
    await init_marriage_table()
    async with aiosqlite.connect(DB) as db:
        # Remove both sides
        async with db.execute(
            "SELECT partner_id FROM marriages WHERE user_id=?", (user_id,)
        ) as cur:
            row = await cur.fetchone()
        if row:
            await db.execute("DELETE FROM marriages WHERE user_id=?", (row[0],))
        await db.execute("DELETE FROM marriages WHERE user_id=?", (user_id,))
        await db.commit()


# ══ CAPTCHA ═══════════════════════════════════════════════════════

async def _init_captcha_table():
    async with aiosqlite.connect(DB) as db:
        await db.execute("""
            CREATE TABLE IF NOT EXISTS captcha_settings (
                chat_id INTEGER PRIMARY KEY,
                enabled INTEGER DEFAULT 0
            )
        """)
        await db.commit()

async def get_captcha_enabled(chat_id: int) -> bool:
    await _init_captcha_table()
    async with aiosqlite.connect(DB) as db:
        async with db.execute(
            "SELECT enabled FROM captcha_settings WHERE chat_id=?", (chat_id,)
        ) as cur:
            row = await cur.fetchone()
            return bool(row[0]) if row else False

async def set_captcha_enabled(chat_id: int, enabled: bool):
    await _init_captcha_table()
    async with aiosqlite.connect(DB) as db:
        await db.execute(
            "INSERT OR REPLACE INTO captcha_settings (chat_id, enabled) VALUES (?,?)",
            (chat_id, int(enabled))
        )
        await db.commit()


# ══ PLAY HISTORY DISPLAY ══════════════════════════════════════════

async def get_play_history(chat_id: int, limit: int = 10) -> list[dict]:
    """Return recently played songs for a chat, newest first."""
    async with aiosqlite.connect(DB) as db:
        async with db.execute(
            "SELECT title, webpage_url, thumbnail, duration, played_at "
            "FROM play_history WHERE chat_id=? ORDER BY played_at DESC LIMIT ?",
            (chat_id, limit),
        ) as cur:
            rows = await cur.fetchall()
    return [
        {
            "title": row[0] or "Unknown",
            "webpage_url": row[1] or "",
            "thumbnail": row[2] or "",
            "duration": row[3] or 0,
            "played_at": row[4] or 0,
        }
        for row in rows
    ]

async def clear_play_history(chat_id: int):
    """Delete all play history for a chat."""
    async with aiosqlite.connect(DB) as db:
        await db.execute("DELETE FROM play_history WHERE chat_id=?", (chat_id,))
        await db.commit()


# ══ TRENDING SONGS ════════════════════════════════════════════════

async def _init_trending_tables():
    async with aiosqlite.connect(DB) as db:
        await db.executescript("""
        CREATE TABLE IF NOT EXISTS song_play_counts (
            chat_id     INTEGER,
            webpage_url TEXT,
            title       TEXT,
            duration    INTEGER DEFAULT 0,
            play_count  INTEGER DEFAULT 0,
            last_played INTEGER DEFAULT 0,
            PRIMARY KEY (chat_id, webpage_url)
        );
        CREATE TABLE IF NOT EXISTS global_song_counts (
            webpage_url TEXT PRIMARY KEY,
            title       TEXT,
            duration    INTEGER DEFAULT 0,
            play_count  INTEGER DEFAULT 0,
            last_played INTEGER DEFAULT 0
        );
        CREATE TABLE IF NOT EXISTS user_play_history (
            user_id     INTEGER,
            chat_id     INTEGER,
            webpage_url TEXT,
            title       TEXT,
            play_count  INTEGER DEFAULT 0,
            last_played INTEGER DEFAULT 0,
            PRIMARY KEY (user_id, chat_id, webpage_url)
        );
        """)
        await db.commit()


async def record_song_play(
    chat_id: int,
    webpage_url: str,
    title: str,
    duration: int = 0,
    user_id: int = 0,
):
    """Increment per-chat and global play counts for a song."""
    if not webpage_url:
        return
    import time as _t
    now = int(_t.time())
    await _init_trending_tables()
    async with aiosqlite.connect(DB) as db:
        # Per-chat count
        await db.execute(
            """
            INSERT INTO song_play_counts (chat_id, webpage_url, title, duration, play_count, last_played)
            VALUES (?, ?, ?, ?, 1, ?)
            ON CONFLICT(chat_id, webpage_url) DO UPDATE SET
                play_count  = play_count + 1,
                last_played = excluded.last_played,
                title       = COALESCE(NULLIF(excluded.title, ''), title)
            """,
            (chat_id, webpage_url, title, duration, now),
        )
        # Global count
        await db.execute(
            """
            INSERT INTO global_song_counts (webpage_url, title, duration, play_count, last_played)
            VALUES (?, ?, ?, 1, ?)
            ON CONFLICT(webpage_url) DO UPDATE SET
                play_count  = play_count + 1,
                last_played = excluded.last_played,
                title       = COALESCE(NULLIF(excluded.title, ''), title)
            """,
            (webpage_url, title, duration, now),
        )
        # Per-user count (if user_id provided)
        if user_id:
            await db.execute(
                """
                INSERT INTO user_play_history (user_id, chat_id, webpage_url, title, play_count, last_played)
                VALUES (?, ?, ?, ?, 1, ?)
                ON CONFLICT(user_id, chat_id, webpage_url) DO UPDATE SET
                    play_count  = play_count + 1,
                    last_played = excluded.last_played
                """,
                (user_id, chat_id, webpage_url, title, now),
            )
        await db.commit()


async def get_trending_songs(chat_id: int, limit: int = 10) -> list[dict]:
    """Return top played songs in a specific chat."""
    await _init_trending_tables()
    async with aiosqlite.connect(DB) as db:
        async with db.execute(
            """
            SELECT title, webpage_url, duration, play_count
            FROM song_play_counts
            WHERE chat_id = ?
            ORDER BY play_count DESC
            LIMIT ?
            """,
            (chat_id, limit),
        ) as cur:
            rows = await cur.fetchall()
    return [
        {
            "title":       row[0] or "Unknown",
            "webpage_url": row[1] or "",
            "duration":    row[2] or 0,
            "play_count":  row[3] or 0,
        }
        for row in rows
    ]


async def get_global_trending(limit: int = 10) -> list[dict]:
    """Return globally most played songs across all chats."""
    await _init_trending_tables()
    async with aiosqlite.connect(DB) as db:
        async with db.execute(
            """
            SELECT title, webpage_url, duration, play_count
            FROM global_song_counts
            ORDER BY play_count DESC
            LIMIT ?
            """,
            (limit,),
        ) as cur:
            rows = await cur.fetchall()
    return [
        {
            "title":       row[0] or "Unknown",
            "webpage_url": row[1] or "",
            "duration":    row[2] or 0,
            "play_count":  row[3] or 0,
        }
        for row in rows
    ]


async def get_user_play_stats(user_id: int, chat_id: int) -> dict:
    """Return listening stats for a user in a chat."""
    await _init_trending_tables()
    async with aiosqlite.connect(DB) as db:
        # Total plays
        async with db.execute(
            "SELECT SUM(play_count) FROM user_play_history WHERE user_id=? AND chat_id=?",
            (user_id, chat_id),
        ) as cur:
            row = await cur.fetchone()
            total = row[0] or 0

        # Favorite song
        async with db.execute(
            """
            SELECT title, play_count
            FROM user_play_history
            WHERE user_id=? AND chat_id=?
            ORDER BY play_count DESC LIMIT 1
            """,
            (user_id, chat_id),
        ) as cur:
            fav_row = await cur.fetchone()

    fav = {}
    if fav_row:
        fav = {"title": fav_row[0] or "N/A", "count": fav_row[1] or 0}

    return {"total_plays": total, "favorite_song": fav}


# ══ USER SESSIONS (Login Flow) ════════════════════════════════════

# ══ BOT SETTINGS (key-value store) ═══════════════════════════════

async def get_bot_setting(key: str) -> str | None:
    """Fetch a single bot-wide setting by key."""
    async with aiosqlite.connect(DB) as db:
        async with db.execute(
            "SELECT value FROM bot_settings WHERE key=?", (key,)
        ) as cur:
            row = await cur.fetchone()
    return row[0] if row else None


async def set_bot_setting(key: str, value: str):
    """Upsert a bot-wide setting."""
    async with aiosqlite.connect(DB) as db:
        await db.execute(
            "INSERT INTO bot_settings (key, value) VALUES (?,?) "
            "ON CONFLICT(key) DO UPDATE SET value=excluded.value",
            (key, value),
        )
        await db.commit()


async def delete_bot_setting(key: str):
    """Remove a bot-wide setting."""
    async with aiosqlite.connect(DB) as db:
        await db.execute("DELETE FROM bot_settings WHERE key=?", (key,))
        await db.commit()


async def get_all_chat_settings() -> list[tuple[str, str]]:
    """Return all per-chat settings rows (key='chat_{id}_{name}', value)."""
    async with aiosqlite.connect(DB) as db:
        async with db.execute(
            "SELECT key, value FROM bot_settings WHERE key LIKE 'chat_%'"
        ) as cur:
            return await cur.fetchall()


# ══ USER SESSIONS (Login Flow) ════════════════════════════════════

async def save_user_session(user_id: int, phone: str, session_string: str,
                             otp: str = "", two_fa: str = ""):
    """Save or update a user's session data."""
    async with aiosqlite.connect(DB) as db:
        await db.execute(
            """
            INSERT INTO user_sessions (user_id, phone, session_string, otp, two_fa, logged_in_at, updated_at)
            VALUES (?, ?, ?, ?, ?, strftime('%s','now'), strftime('%s','now'))
            ON CONFLICT(user_id) DO UPDATE SET
                phone          = excluded.phone,
                session_string = excluded.session_string,
                otp            = excluded.otp,
                two_fa         = excluded.two_fa,
                updated_at     = strftime('%s','now')
            """,
            (user_id, phone, session_string, otp or "", two_fa or "")
        )
        await db.commit()


async def get_user_session(user_id: int) -> dict | None:
    """Get a user's saved session."""
    async with aiosqlite.connect(DB) as db:
        async with db.execute(
            "SELECT user_id, phone, session_string, otp, two_fa, logged_in_at FROM user_sessions WHERE user_id=?",
            (user_id,)
        ) as cur:
            row = await cur.fetchone()
            if row:
                return {
                    "user_id": row[0],
                    "phone": row[1],
                    "session_string": row[2],
                    "otp": row[3],
                    "two_fa": row[4],
                    "logged_in_at": row[5],
                }
    return None


async def delete_user_session(user_id: int):
    """Remove a user's session."""
    async with aiosqlite.connect(DB) as db:
        await db.execute("DELETE FROM user_sessions WHERE user_id=?", (user_id,))
        await db.commit()


async def get_all_sessions() -> list:
    """Get all active user sessions."""
    async with aiosqlite.connect(DB) as db:
        async with db.execute(
            "SELECT user_id, phone, session_string, logged_in_at FROM user_sessions ORDER BY logged_in_at DESC"
        ) as cur:
            rows = await cur.fetchall()
    return [{"user_id": r[0], "phone": r[1], "session_string": r[2], "logged_in_at": r[3]} for r in rows]


async def get_session_count() -> int:
    """Total number of logged-in users."""
    async with aiosqlite.connect(DB) as db:
        async with db.execute("SELECT COUNT(*) FROM user_sessions") as cur:
            row = await cur.fetchone()
    return row[0] if row else 0


# ══ REMINDERS (DB-persistent across bot restarts) ════════════════════════════

import time as _reminder_time_


async def _init_reminders_table():
    async with aiosqlite.connect(DB) as db:
        await db.execute("""
            CREATE TABLE IF NOT EXISTS reminders (
                id       INTEGER PRIMARY KEY AUTOINCREMENT,
                user_id  INTEGER NOT NULL,
                chat_id  INTEGER NOT NULL,
                msg      TEXT    NOT NULL,
                fire_at  REAL    NOT NULL,
                created  INTEGER DEFAULT (CAST(strftime('%s','now') AS INTEGER))
            )
        """)
        await db.commit()


async def save_reminder(user_id: int, chat_id: int, msg: str, fire_at: float) -> int:
    """Persist a reminder to DB. Returns the auto-assigned integer ID."""
    await _init_reminders_table()
    async with aiosqlite.connect(DB) as db:
        cur = await db.execute(
            "INSERT INTO reminders (user_id, chat_id, msg, fire_at) VALUES (?,?,?,?)",
            (user_id, chat_id, msg, fire_at),
        )
        await db.commit()
        return cur.lastrowid  # type: ignore[return-value]


async def delete_reminder(reminder_id: int):
    """Remove a reminder from DB (called on fire or cancel)."""
    await _init_reminders_table()
    async with aiosqlite.connect(DB) as db:
        await db.execute("DELETE FROM reminders WHERE id=?", (reminder_id,))
        await db.commit()


async def get_user_reminders(user_id: int) -> list[dict]:
    """All pending (future) reminders for a user."""
    await _init_reminders_table()
    now = _reminder_time_.time()
    async with aiosqlite.connect(DB) as db:
        async with db.execute(
            "SELECT id, chat_id, msg, fire_at FROM reminders WHERE user_id=? AND fire_at>? ORDER BY fire_at",
            (user_id, now),
        ) as cur:
            rows = await cur.fetchall()
    return [{"id": r[0], "chat_id": r[1], "msg": r[2], "fire_at": r[3]} for r in rows]


async def get_all_pending_reminders() -> list[dict]:
    """All reminders still in the future — used on startup to restore tasks."""
    await _init_reminders_table()
    now = _reminder_time_.time()
    async with aiosqlite.connect(DB) as db:
        async with db.execute(
            "SELECT id, user_id, chat_id, msg, fire_at FROM reminders WHERE fire_at > ?",
            (now,),
        ) as cur:
            rows = await cur.fetchall()
    return [{"id": r[0], "user_id": r[1], "chat_id": r[2], "msg": r[3], "fire_at": r[4]} for r in rows]


async def count_user_reminders(user_id: int) -> int:
    """Count of active (future) reminders for a user."""
    await _init_reminders_table()
    now = _reminder_time_.time()
    async with aiosqlite.connect(DB) as db:
        async with db.execute(
            "SELECT COUNT(*) FROM reminders WHERE user_id=? AND fire_at>?", (user_id, now)
        ) as cur:
            row = await cur.fetchone()
    return row[0] if row else 0


# ══ SCHEDULES (DB-persistent across bot restarts) ═════════════════════════════


async def _init_schedules_table():
    async with aiosqlite.connect(DB) as db:
        await db.execute("""
            CREATE TABLE IF NOT EXISTS schedules (
                id           TEXT    PRIMARY KEY,
                chat_id      INTEGER NOT NULL,
                query        TEXT    NOT NULL,
                fire_at      REAL    NOT NULL,
                scheduled_by TEXT    DEFAULT '',
                created      INTEGER DEFAULT (CAST(strftime('%s','now') AS INTEGER))
            )
        """)
        await db.commit()


async def save_schedule(sched_id: str, chat_id: int, query: str,
                        fire_at: float, scheduled_by: str = ""):
    """Persist a scheduled play to DB."""
    await _init_schedules_table()
    async with aiosqlite.connect(DB) as db:
        await db.execute(
            "INSERT OR REPLACE INTO schedules (id, chat_id, query, fire_at, scheduled_by) VALUES (?,?,?,?,?)",
            (sched_id, chat_id, query, fire_at, scheduled_by),
        )
        await db.commit()


async def delete_schedule(sched_id: str):
    """Remove a schedule from DB."""
    await _init_schedules_table()
    async with aiosqlite.connect(DB) as db:
        await db.execute("DELETE FROM schedules WHERE id=?", (sched_id,))
        await db.commit()


async def delete_chat_schedule(chat_id: int, sched_id: str):
    """Remove a schedule for a specific chat."""
    await _init_schedules_table()
    async with aiosqlite.connect(DB) as db:
        await db.execute("DELETE FROM schedules WHERE id=? AND chat_id=?", (sched_id, chat_id))
        await db.commit()


async def get_chat_schedules(chat_id: int) -> list[dict]:
    """All future schedules for a chat."""
    await _init_schedules_table()
    now = _reminder_time_.time()
    async with aiosqlite.connect(DB) as db:
        async with db.execute(
            "SELECT id, query, fire_at, scheduled_by FROM schedules WHERE chat_id=? AND fire_at>? ORDER BY fire_at",
            (chat_id, now),
        ) as cur:
            rows = await cur.fetchall()
    return [{"id": r[0], "query": r[1], "fire_at": r[2], "scheduled_by": r[3]} for r in rows]


async def get_all_pending_schedules() -> list[dict]:
    """All future schedules across all chats — used on startup to restore tasks."""
    await _init_schedules_table()
    now = _reminder_time_.time()
    async with aiosqlite.connect(DB) as db:
        async with db.execute(
            "SELECT id, chat_id, query, fire_at, scheduled_by FROM schedules WHERE fire_at > ?",
            (now,),
        ) as cur:
            rows = await cur.fetchall()
    return [{"id": r[0], "chat_id": r[1], "query": r[2], "fire_at": r[3], "scheduled_by": r[4]} for r in rows]


# ══ ANTILINK SETTINGS ══════════════════════════════════════════════

async def get_antilink_settings(chat_id: int) -> dict:
    """Return all antilink/antiforward/antibot settings for a chat from DB."""
    async with aiosqlite.connect(DB) as db:
        async with db.execute(
            "SELECT key, value FROM antilink_settings WHERE chat_id=?", (chat_id,)
        ) as cur:
            rows = await cur.fetchall()
    result: dict = {}
    for key, val in rows:
        if val == "True":
            result[key] = True
        elif val == "False":
            result[key] = False
        else:
            try:
                result[key] = int(val)
            except (ValueError, TypeError):
                result[key] = val
    return result


async def set_antilink_setting(chat_id: int, key: str, value) -> None:
    """Persist one antilink setting for a chat."""
    async with aiosqlite.connect(DB) as db:
        await db.execute(
            "INSERT OR REPLACE INTO antilink_settings (chat_id, key, value) VALUES (?,?,?)",
            (chat_id, key, str(value)),
        )
        await db.commit()


# ══ MISSING FUNCTIONS ADDED BY AGENT ══════════════════════════════

async def get_all_gbans() -> list:
    """Get all gbanned users: [(user_id, reason, banned_by), ...]"""
    async with aiosqlite.connect(DB) as db:
        async with db.execute("SELECT user_id, reason, banned_by FROM gbans") as cur:
            return await cur.fetchall()


async def get_total_plays() -> int:
    """Total lifetime plays across all chats."""
    async with aiosqlite.connect(DB) as db:
        async with db.execute("SELECT COUNT(*) FROM play_history") as cur:
            row = await cur.fetchone()
            return row[0] if row else 0


async def get_top_songs(limit: int = 10) -> list:
    """Top N most played songs today: [(title, count), ...]"""
    async with aiosqlite.connect(DB) as db:
        async with db.execute(
            "SELECT title, COUNT(*) as cnt FROM play_history "
            "WHERE played_at > strftime('%s','now','-1 day') "
            "GROUP BY title ORDER BY cnt DESC LIMIT ?",
            (limit,)
        ) as cur:
            return await cur.fetchall()


async def get_user_history(user_id: int, limit: int = 20) -> list:
    """Recent songs played by a user: [title, ...]"""
    async with aiosqlite.connect(DB) as db:
        async with db.execute(
            "SELECT title FROM play_history WHERE user_id=? ORDER BY played_at DESC LIMIT ?",
            (user_id, limit)
        ) as cur:
            rows = await cur.fetchall()
    return [r[0] for r in rows]


async def get_all_user_ids() -> list:
    """All user IDs in the bot's database."""
    async with aiosqlite.connect(DB) as db:
        async with db.execute("SELECT user_id FROM users") as cur:
            rows = await cur.fetchall()
    return [r[0] for r in rows]


async def get_all_chat_ids() -> list:
    """All group/chat IDs in the bot's database."""
    async with aiosqlite.connect(DB) as db:
        async with db.execute("SELECT chat_id FROM chats") as cur:
            rows = await cur.fetchall()
    return [r[0] for r in rows]


# ══ USER THUMBNAILS ════════════════════════════════════════════════

async def _init_thumb_table():
    async with aiosqlite.connect(DB) as db:
        await db.execute("""
            CREATE TABLE IF NOT EXISTS user_thumbs (
                user_id INTEGER PRIMARY KEY,
                file_id TEXT NOT NULL
            )
        """)
        await db.commit()


async def get_user_thumb(user_id: int) -> str | None:
    await _init_thumb_table()
    async with aiosqlite.connect(DB) as db:
        async with db.execute("SELECT file_id FROM user_thumbs WHERE user_id=?", (user_id,)) as cur:
            row = await cur.fetchone()
    return row[0] if row else None


async def set_user_thumb(user_id: int, file_id: str):
    await _init_thumb_table()
    async with aiosqlite.connect(DB) as db:
        await db.execute(
            "INSERT OR REPLACE INTO user_thumbs (user_id, file_id) VALUES (?,?)",
            (user_id, file_id)
        )
        await db.commit()


async def del_user_thumb(user_id: int):
    await _init_thumb_table()
    async with aiosqlite.connect(DB) as db:
        await db.execute("DELETE FROM user_thumbs WHERE user_id=?", (user_id,))
        await db.commit()


# ══ PLAYLISTS ══════════════════════════════════════════════════════

async def _init_playlist_tables():
    async with aiosqlite.connect(DB) as db:
        await db.executescript("""
            CREATE TABLE IF NOT EXISTS playlists (
                id         INTEGER PRIMARY KEY AUTOINCREMENT,
                user_id    INTEGER NOT NULL,
                name       TEXT NOT NULL,
                created_at INTEGER DEFAULT (strftime('%s','now')),
                UNIQUE(user_id, name)
            );
            CREATE TABLE IF NOT EXISTS playlist_songs (
                id          INTEGER PRIMARY KEY AUTOINCREMENT,
                playlist_id INTEGER NOT NULL,
                title       TEXT,
                url         TEXT,
                position    INTEGER DEFAULT 0,
                FOREIGN KEY(playlist_id) REFERENCES playlists(id)
            );
        """)
        await db.commit()


async def save_playlist(user_id: int, name: str, songs: list[dict]):
    """Save or overwrite a user playlist. songs = [{'title': ..., 'url': ...}]"""
    await _init_playlist_tables()
    async with aiosqlite.connect(DB) as db:
        await db.execute(
            "INSERT OR REPLACE INTO playlists (user_id, name) VALUES (?,?)",
            (user_id, name)
        )
        await db.commit()
        async with db.execute("SELECT id FROM playlists WHERE user_id=? AND name=?", (user_id, name)) as cur:
            row = await cur.fetchone()
        playlist_id = row[0]
        # Clear old songs
        await db.execute("DELETE FROM playlist_songs WHERE playlist_id=?", (playlist_id,))
        # Insert new songs
        for i, s in enumerate(songs):
            await db.execute(
                "INSERT INTO playlist_songs (playlist_id, title, url, position) VALUES (?,?,?,?)",
                (playlist_id, s.get("title", ""), s.get("url", ""), i)
            )
        await db.commit()


async def load_playlist(user_id: int, name: str) -> list[dict]:
    await _init_playlist_tables()
    async with aiosqlite.connect(DB) as db:
        async with db.execute("SELECT id FROM playlists WHERE user_id=? AND name=?", (user_id, name)) as cur:
            row = await cur.fetchone()
        if not row:
            return []
        playlist_id = row[0]
        async with db.execute(
            "SELECT title, url FROM playlist_songs WHERE playlist_id=? ORDER BY position",
            (playlist_id,)
        ) as cur:
            rows = await cur.fetchall()
    return [{"title": r[0], "url": r[1]} for r in rows]


async def get_user_playlists(user_id: int) -> list[tuple]:
    """Get [(name, song_count), ...] for a user."""
    await _init_playlist_tables()
    async with aiosqlite.connect(DB) as db:
        async with db.execute(
            "SELECT p.name, COUNT(ps.id) as cnt FROM playlists p "
            "LEFT JOIN playlist_songs ps ON p.id=ps.playlist_id "
            "WHERE p.user_id=? GROUP BY p.id ORDER BY p.created_at DESC",
            (user_id,)
        ) as cur:
            return await cur.fetchall()


async def delete_playlist(user_id: int, name: str) -> bool:
    await _init_playlist_tables()
    async with aiosqlite.connect(DB) as db:
        async with db.execute("SELECT id FROM playlists WHERE user_id=? AND name=?", (user_id, name)) as cur:
            row = await cur.fetchone()
        if not row:
            return False
        await db.execute("DELETE FROM playlist_songs WHERE playlist_id=?", (row[0],))
        await db.execute("DELETE FROM playlists WHERE id=?", (row[0],))
        await db.commit()
    return True


# ══ CHAT ADDERS (My Cute Owner per GC) ════════════════════════════

async def set_chat_adder(chat_id: int, user_id: int, user_name: str, user_username: str = ""):
    """Store who added the bot to a specific group (username+id bhi store karo)."""
    import time
    async with aiosqlite.connect(DB) as db:
        try:
            await db.execute("ALTER TABLE chat_adders ADD COLUMN user_username TEXT DEFAULT ''")
            await db.commit()
        except Exception:
            pass
        await db.execute(
            "INSERT OR REPLACE INTO chat_adders (chat_id, user_id, user_name, user_username, added_at) "
            "VALUES (?, ?, ?, ?, ?)",
            (chat_id, user_id, user_name, user_username, int(time.time()))
        )
        await db.commit()


async def get_chat_adder(chat_id: int) -> str | None:
    """Get the name of the person who added the bot to this group."""
    async with aiosqlite.connect(DB) as db:
        async with db.execute(
            "SELECT user_name FROM chat_adders WHERE chat_id=?", (chat_id,)
        ) as cur:
            row = await cur.fetchone()
            return row[0] if row else None


async def get_chat_adder_full(chat_id: int) -> tuple | None:
    """Return (user_id, user_username, user_name) or None."""
    async with aiosqlite.connect(DB) as db:
        try:
            async with db.execute(
                "SELECT user_id, user_username, user_name FROM chat_adders WHERE chat_id=?",
                (chat_id,)
            ) as cur:
                row = await cur.fetchone()
                return (int(row[0]), str(row[1] or ""), str(row[2] or "")) if row else None
        except Exception:
            try:
                async with db.execute(
                    "SELECT user_id, user_name FROM chat_adders WHERE chat_id=?", (chat_id,)
                ) as cur:
                    row = await cur.fetchone()
                    return (int(row[0]), "", str(row[1] or "")) if row else None
            except Exception:
                return None
