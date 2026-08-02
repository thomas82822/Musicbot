"""
helpers/github_sync.py — Live GitHub data persistence for 4ST Music Bot
━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━

HOW IT WORKS
  On startup  → pull data/ folder from GitHub → import into SQLite DB
  Auto-save   → every DATA_SYNC_INTERVAL seconds → export DB → push to GitHub
  On shutdown → one final sync before exit
  On demand   → owner can trigger via owner panel button

WHAT IS SAVED (in data/ folder of BOT_REPO)
  data/users.json            — all registered users (id, username, name, first_seen)
  data/groups.json           — all chats/groups (chat_id, title, chat_type)
  data/economy.json          — coin balances (user_id, balance, total_earned)
  data/gbans.json            — global bans (user_id, reason, banned_by)
  data/warns.json            — warnings (user_id, chat_id, reason, warned_by)
  data/notes.json            — saved notes (chat_id, name, content)
  data/bot_settings.json     — key/value bot settings (start media, etc.)
  data/broadcast_targets.json— combined users + groups for broadcast (a2z live)
  data/sync_meta.json        — last_sync timestamp, record counts

WHAT IS NOT SAVED
  ❌ session_string  — Telegram session strings are private credentials;
                       storing them in a (possibly public) GitHub repo is a
                       security disaster. Users must re-login after a cold start.
  ❌ api_id / api_hash / bot_token — never synced to GitHub for security reasons.
  ❌ gemini_api_key  — third-party API keys stay in environment variables only.

CONFIGURATION
  BOT_REPO              = "owner/repo"  (e.g. "thomasir/4st_music")
  GITHUB_TOKEN          = personal access token with repo write scope
  DATA_SYNC_INTERVAL    = seconds between auto-saves (default 300 = 5 min)
  GITHUB_DATA_BRANCH    = branch to read/write data files (default "main")
"""

import asyncio
import base64
import json
import logging
import time
from typing import Any

import aiohttp

from config import GITHUB_TOKEN, BOT_REPO

log = logging.getLogger("ApexBot.github_sync")

# ── Config ────────────────────────────────────────────────────────
DATA_SYNC_INTERVAL  = 300          # seconds between auto-saves
GITHUB_DATA_BRANCH  = "main"
DATA_FOLDER         = "data"
GH_API              = "https://api.github.com"

# Shared flag so shutdown can cancel the loop cleanly
_sync_task: asyncio.Task | None = None
_last_sync_ts: float = 0.0


# ══════════════════════════════════════════════════════════════════
# ── Low-level GitHub file API
# ══════════════════════════════════════════════════════════════════

def _gh_headers() -> dict:
    h = {
        "User-Agent": "ApexBot-Sync/1.0",
        "Accept":     "application/vnd.github.v3+json",
    }
    if GITHUB_TOKEN:
        h["Authorization"] = f"token {GITHUB_TOKEN}"
    return h


async def _gh_get_file(path: str) -> tuple[str | None, str | None]:
    """
    Fetch a file from GitHub.
    Returns (content_str, sha) or (None, None) on failure.
    content_str is the decoded UTF-8 text.
    sha is needed to update the file later.
    """
    url = f"{GH_API}/repos/{BOT_REPO}/contents/{path}?ref={GITHUB_DATA_BRANCH}"
    try:
        async with aiohttp.ClientSession(
            timeout=aiohttp.ClientTimeout(total=15)
        ) as session:
            async with session.get(url, headers=_gh_headers()) as r:
                if r.status == 200:
                    data = await r.json()
                    content_b64 = data.get("content", "")
                    sha         = data.get("sha", "")
                    # GitHub adds newlines to base64 — strip them
                    content_bytes = base64.b64decode(content_b64.replace("\n", ""))
                    return content_bytes.decode("utf-8"), sha
                elif r.status == 404:
                    return None, None   # file doesn't exist yet
                elif r.status == 401:
                    log.error(
                        "gh_get_file HTTP 401 for %s — GITHUB_TOKEN is expired or invalid! "
                        "Go to Heroku → Settings → Config Vars and update GITHUB_TOKEN with "
                        "a new token from https://github.com/settings/tokens (needs repo scope).",
                        path,
                    )
                else:
                    log.warning("gh_get_file HTTP %d for %s", r.status, path)
    except Exception as e:
        log.warning("gh_get_file error [%s]: %s", path, e)
    return None, None


async def _gh_put_file(path: str, content: str, message: str, sha: str | None = None) -> bool:
    """
    Create or update a file in GitHub.
    Returns True on success.
    """
    if not GITHUB_TOKEN:
        log.warning("GITHUB_TOKEN not set — skipping sync for %s", path)
        return False

    url     = f"{GH_API}/repos/{BOT_REPO}/contents/{path}"
    payload: dict[str, Any] = {
        "message": message,
        "content": base64.b64encode(content.encode("utf-8")).decode("ascii"),
        "branch":  GITHUB_DATA_BRANCH,
    }
    if sha:
        payload["sha"] = sha

    try:
        async with aiohttp.ClientSession(
            timeout=aiohttp.ClientTimeout(total=30)
        ) as session:
            async with session.put(url, headers=_gh_headers(), json=payload) as r:
                if r.status in (200, 201):
                    return True
                body = await r.text()
                if r.status == 401:
                    log.error(
                        "gh_put_file HTTP 401 for %s — GITHUB_TOKEN is expired or invalid! "
                        "Update it in Heroku → Settings → Config Vars. "
                        "Get a new token at https://github.com/settings/tokens (repo scope needed).",
                        path,
                    )
                else:
                    log.warning("gh_put_file HTTP %d for %s: %s", r.status, path, body[:200])
    except Exception as e:
        log.warning("gh_put_file error [%s]: %s", path, e)
    return False


# ══════════════════════════════════════════════════════════════════
# ── Export helpers  (DB → Python dicts)
# ══════════════════════════════════════════════════════════════════

async def _export_all() -> dict[str, Any]:
    """Read every non-sensitive table from the DB and return as dict."""
    import aiosqlite
    from config import DB_PATH

    result: dict[str, Any] = {}
    async with aiosqlite.connect(DB_PATH) as db:
        db.row_factory = aiosqlite.Row

        # users
        async with db.execute(
            "SELECT user_id, username, name, first_seen FROM users"
        ) as cur:
            result["users"] = [dict(r) for r in await cur.fetchall()]

        # groups / chats
        async with db.execute(
            "SELECT chat_id, title, chat_type FROM chats"
        ) as cur:
            result["groups"] = [dict(r) for r in await cur.fetchall()]

        # economy
        async with db.execute(
            "SELECT user_id, balance, total_earned, started FROM economy"
        ) as cur:
            result["economy"] = [dict(r) for r in await cur.fetchall()]

        # gbans
        async with db.execute(
            "SELECT user_id, reason, banned_by FROM gbans"
        ) as cur:
            result["gbans"] = [dict(r) for r in await cur.fetchall()]

        # warns
        async with db.execute(
            "SELECT user_id, chat_id, reason, warned_by FROM warns"
        ) as cur:
            result["warns"] = [dict(r) for r in await cur.fetchall()]

        # notes
        async with db.execute(
            "SELECT chat_id, name, content FROM notes"
        ) as cur:
            result["notes"] = [dict(r) for r in await cur.fetchall()]

        # word_filters
        async with db.execute(
            "SELECT chat_id, word FROM word_filters"
        ) as cur:
            result["word_filters"] = [dict(r) for r in await cur.fetchall()]

        # welcome_settings
        async with db.execute(
            "SELECT chat_id, welcome_text, goodbye_text, welcome_enabled, goodbye_enabled "
            "FROM welcome_settings"
        ) as cur:
            result["welcome_settings"] = [dict(r) for r in await cur.fetchall()]

        # bot_settings  (start media, custom flags, etc.)
        async with db.execute("SELECT key, value FROM bot_settings") as cur:
            result["bot_settings"] = [dict(r) for r in await cur.fetchall()]

        # ── broadcast_targets: merged user_ids + chat_ids for broadcast
        async with db.execute("SELECT user_id FROM users") as cur:
            user_ids = [r[0] for r in await cur.fetchall()]
        async with db.execute("SELECT chat_id FROM chats") as cur:
            chat_ids = [r[0] for r in await cur.fetchall()]
        result["broadcast_targets"] = {
            "users":  user_ids,
            "groups": chat_ids,
            "all":    list(set(user_ids + chat_ids)),
            "total":  len(set(user_ids + chat_ids)),
        }

        # ── play_history (recent 500 entries to keep file size sane)
        async with db.execute(
            "SELECT chat_id, title, webpage_url, duration, played_at "
            "FROM play_history ORDER BY played_at DESC LIMIT 500"
        ) as cur:
            result["play_history"] = [dict(r) for r in await cur.fetchall()]

    return result


# ══════════════════════════════════════════════════════════════════
# ── Import helpers  (Python dicts → DB)
# ══════════════════════════════════════════════════════════════════

async def _import_all(data: dict[str, Any]) -> dict[str, int]:
    """Restore exported data into the DB. Returns counts of rows imported."""
    import aiosqlite
    from config import DB_PATH

    counts: dict[str, int] = {}

    async with aiosqlite.connect(DB_PATH) as db:

        # users
        rows = data.get("users", [])
        for r in rows:
            await db.execute(
                "INSERT OR IGNORE INTO users (user_id, username, name, first_seen) "
                "VALUES (?,?,?,?)",
                (r["user_id"], r.get("username",""), r.get("name",""), r.get("first_seen",0)),
            )
        counts["users"] = len(rows)

        # groups
        rows = data.get("groups", [])
        for r in rows:
            await db.execute(
                "INSERT OR IGNORE INTO chats (chat_id, title, chat_type) VALUES (?,?,?)",
                (r["chat_id"], r.get("title",""), r.get("chat_type","group")),
            )
        counts["groups"] = len(rows)

        # economy
        rows = data.get("economy", [])
        for r in rows:
            await db.execute(
                "INSERT OR IGNORE INTO economy (user_id, balance, total_earned, started) "
                "VALUES (?,?,?,?)",
                (r["user_id"], r.get("balance",0), r.get("total_earned",0), r.get("started",0)),
            )
        counts["economy"] = len(rows)

        # gbans
        rows = data.get("gbans", [])
        for r in rows:
            await db.execute(
                "INSERT OR IGNORE INTO gbans (user_id, reason, banned_by) VALUES (?,?,?)",
                (r["user_id"], r.get("reason",""), r.get("banned_by",0)),
            )
        counts["gbans"] = len(rows)

        # warns
        rows = data.get("warns", [])
        for r in rows:
            await db.execute(
                "INSERT OR IGNORE INTO warns (user_id, chat_id, reason, warned_by) "
                "VALUES (?,?,?,?)",
                (r["user_id"], r["chat_id"], r.get("reason",""), r.get("warned_by",0)),
            )
        counts["warns"] = len(rows)

        # notes
        rows = data.get("notes", [])
        for r in rows:
            await db.execute(
                "INSERT OR IGNORE INTO notes (chat_id, name, content) VALUES (?,?,?)",
                (r["chat_id"], r.get("name",""), r.get("content","")),
            )
        counts["notes"] = len(rows)

        # word_filters
        rows = data.get("word_filters", [])
        for r in rows:
            await db.execute(
                "INSERT OR IGNORE INTO word_filters (chat_id, word) VALUES (?,?)",
                (r["chat_id"], r.get("word","")),
            )
        counts["word_filters"] = len(rows)

        # welcome_settings
        rows = data.get("welcome_settings", [])
        for r in rows:
            await db.execute(
                "INSERT OR IGNORE INTO welcome_settings "
                "(chat_id, welcome_text, goodbye_text, welcome_enabled, goodbye_enabled) "
                "VALUES (?,?,?,?,?)",
                (
                    r["chat_id"],
                    r.get("welcome_text",""),
                    r.get("goodbye_text",""),
                    r.get("welcome_enabled",1),
                    r.get("goodbye_enabled",1),
                ),
            )
        counts["welcome_settings"] = len(rows)

        # bot_settings
        rows = data.get("bot_settings", [])
        for r in rows:
            await db.execute(
                "INSERT OR IGNORE INTO bot_settings (key, value) VALUES (?,?)",
                (r["key"], r.get("value","")),
            )
        counts["bot_settings"] = len(rows)

        await db.commit()

    return counts


# ══════════════════════════════════════════════════════════════════
# ── Public API
# ══════════════════════════════════════════════════════════════════

async def sync_all_data(reason: str = "auto") -> dict[str, Any]:
    """
    Export DB → JSON → push every file to GitHub data/ folder.
    Returns a summary dict with counts and timing.
    """
    global _last_sync_ts

    if not GITHUB_TOKEN:
        log.warning("sync_all_data: GITHUB_TOKEN not set — skipping")
        return {"ok": False, "error": "GITHUB_TOKEN not set"}
    if not BOT_REPO:
        log.warning("sync_all_data: BOT_REPO not set — skipping")
        return {"ok": False, "error": "BOT_REPO not set"}

    t0 = time.monotonic()
    log.info("🔄 GitHub sync start [%s]", reason)

    try:
        all_data = await _export_all()
    except Exception as e:
        log.error("sync_all_data: export failed: %s", e)
        return {"ok": False, "error": str(e)}

    now_ts = int(time.time())

    # Per-table files
    file_map = {
        f"{DATA_FOLDER}/users.json":             all_data.get("users", []),
        f"{DATA_FOLDER}/groups.json":            all_data.get("groups", []),
        f"{DATA_FOLDER}/economy.json":           all_data.get("economy", []),
        f"{DATA_FOLDER}/gbans.json":             all_data.get("gbans", []),
        f"{DATA_FOLDER}/warns.json":             all_data.get("warns", []),
        f"{DATA_FOLDER}/notes.json":             all_data.get("notes", []),
        f"{DATA_FOLDER}/word_filters.json":      all_data.get("word_filters", []),
        f"{DATA_FOLDER}/welcome_settings.json":  all_data.get("welcome_settings", []),
        f"{DATA_FOLDER}/bot_settings.json":      all_data.get("bot_settings", []),
        f"{DATA_FOLDER}/broadcast_targets.json": all_data.get("broadcast_targets", {}),
        f"{DATA_FOLDER}/play_history.json":      all_data.get("play_history", []),
    }

    # Build sync meta LAST
    meta = {
        "last_sync":      now_ts,
        "last_sync_iso":  time.strftime("%Y-%m-%d %H:%M:%S UTC", time.gmtime(now_ts)),
        "reason":         reason,
        "counts": {
            table: (len(v) if isinstance(v, list) else v.get("total", 0))
            for table, v in all_data.items()
        },
    }
    file_map[f"{DATA_FOLDER}/sync_meta.json"] = meta

    # Push each file — fetch current SHA first (needed for updates)
    pushed = failed_files = 0
    for gh_path, payload in file_map.items():
        content_str = json.dumps(payload, ensure_ascii=False, indent=2)
        _, sha = await _gh_get_file(gh_path)
        commit_msg = f"🤖 bot-sync [{reason}] {time.strftime('%Y-%m-%d %H:%M UTC')}"
        ok = await _gh_put_file(gh_path, content_str, commit_msg, sha)
        if ok:
            pushed += 1
        else:
            failed_files += 1

    elapsed = time.monotonic() - t0
    _last_sync_ts = time.time()

    summary = {
        "ok":           failed_files == 0,
        "pushed_files": pushed,
        "failed_files": failed_files,
        "elapsed_s":    round(elapsed, 2),
        "reason":       reason,
        "counts":       meta["counts"],
    }
    log.info(
        "✅ GitHub sync done [%s] | pushed=%d failed=%d | %.1fs",
        reason, pushed, failed_files, elapsed,
    )
    return summary


async def restore_from_github() -> dict[str, Any]:
    """
    On startup: pull data/ folder from GitHub and import into local DB.
    Returns a summary dict.
    """
    if not GITHUB_TOKEN or not BOT_REPO:
        log.info("restore_from_github: no token/repo — starting fresh")
        return {"ok": False, "error": "GITHUB_TOKEN or BOT_REPO not set"}

    log.info("📥 Restoring data from GitHub [%s/data/]...", BOT_REPO)
    t0 = time.monotonic()

    # Read all table files
    table_files = [
        ("users",            f"{DATA_FOLDER}/users.json"),
        ("groups",           f"{DATA_FOLDER}/groups.json"),
        ("economy",          f"{DATA_FOLDER}/economy.json"),
        ("gbans",            f"{DATA_FOLDER}/gbans.json"),
        ("warns",            f"{DATA_FOLDER}/warns.json"),
        ("notes",            f"{DATA_FOLDER}/notes.json"),
        ("word_filters",     f"{DATA_FOLDER}/word_filters.json"),
        ("welcome_settings", f"{DATA_FOLDER}/welcome_settings.json"),
        ("bot_settings",     f"{DATA_FOLDER}/bot_settings.json"),
        ("play_history",     f"{DATA_FOLDER}/play_history.json"),
    ]

    # Fetch all files in parallel
    tasks  = [_gh_get_file(path) for _, path in table_files]
    results = await asyncio.gather(*tasks, return_exceptions=True)

    merged: dict[str, Any] = {}
    for (key, _), result in zip(table_files, results):
        if isinstance(result, Exception):
            log.warning("restore: fetch error for %s: %s", key, result)
            continue
        content, _ = result
        if content is None:
            log.info("restore: %s not found on GitHub (first run?)", key)
            continue
        try:
            merged[key] = json.loads(content)
        except json.JSONDecodeError as e:
            log.warning("restore: JSON parse error for %s: %s", key, e)

    if not merged:
        log.info("restore_from_github: no data files found — starting fresh DB")
        return {"ok": True, "restored": {}, "note": "no data files on GitHub yet"}

    try:
        counts = await _import_all(merged)
    except Exception as e:
        log.error("restore_from_github: import failed: %s", e)
        return {"ok": False, "error": str(e)}

    elapsed = time.monotonic() - t0
    log.info(
        "✅ GitHub restore done | tables=%d | %.1fs | %s",
        len(counts), elapsed, counts,
    )
    return {"ok": True, "restored": counts, "elapsed_s": round(elapsed, 2)}


async def auto_sync_loop():
    """Background task: sync every DATA_SYNC_INTERVAL seconds."""
    global _sync_task
    _sync_task = asyncio.current_task()
    log.info("⏱️ GitHub auto-sync loop started (interval=%ds)", DATA_SYNC_INTERVAL)

    # Wait one full interval before first auto-save
    # (startup restore already pulled fresh data)
    await asyncio.sleep(DATA_SYNC_INTERVAL)

    while True:
        try:
            await sync_all_data(reason="auto-timer")
        except asyncio.CancelledError:
            log.info("auto_sync_loop cancelled — final sync on exit")
            break
        except Exception as e:
            log.error("auto_sync_loop error: %s", e)

        try:
            await asyncio.sleep(DATA_SYNC_INTERVAL)
        except asyncio.CancelledError:
            break

    log.info("⏱️ GitHub auto-sync loop exited")


def get_last_sync_ts() -> float:
    """Return Unix timestamp of last successful sync (0 if never)."""
    return _last_sync_ts
