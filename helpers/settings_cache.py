"""
settings_cache.py — Ultra-fast in-memory TTL cache for per-chat settings.

WHY THIS EXISTS:
  The biggest response-time killer in a Telegram bot is reading per-chat
  settings (word filters, chatbot toggle, antiporn toggle, reaction toggle)
  from SQLite on EVERY incoming group message.  A group with 500 active users
  generates hundreds of messages/minute, each triggering multiple aiosqlite
  round-trips.  This module makes those reads instant for 95% of messages by
  keeping a short-lived in-memory copy.

  Cache miss (cold) → one DB read, then cached for `ttl` seconds.
  Cache hit  (warm) → pure in-memory dict lookup, ≈ 0 µs overhead.

  When an admin changes a setting (e.g. /antiporn on), the handler should call
  set_cached() immediately so subsequent messages see the new value without
  waiting for TTL expiry.

USAGE:
    from helpers.settings_cache import get_cached, set_cached, invalidate

    # Read (returns None on cache miss)
    val = get_cached("antiporn", chat_id)

    # Write — after DB read, or immediately after DB write
    set_cached("antiporn", chat_id, True, ttl=120)

    # Instant update after admin changes setting
    set_cached("antiporn", chat_id, new_value)   # default ttl = 120s
"""

import time
from typing import Any

# _store: namespace → {chat_id: (value, expires_monotonic)}
_store: dict[str, dict[int, tuple[Any, float]]] = {}

DEFAULT_TTL = 120  # seconds — most settings change very rarely


def get_cached(namespace: str, chat_id: int) -> Any:
    """Return cached value or None on miss / expiry."""
    ns = _store.get(namespace)
    if not ns:
        return None
    entry = ns.get(chat_id)
    if not entry:
        return None
    value, expires_at = entry
    if time.monotonic() > expires_at:
        del ns[chat_id]
        return None
    return value


def set_cached(namespace: str, chat_id: int, value: Any, ttl: float = DEFAULT_TTL) -> None:
    """Store value in cache for *ttl* seconds."""
    if namespace not in _store:
        _store[namespace] = {}
    _store[namespace][chat_id] = (value, time.monotonic() + ttl)
    # BUG FIX: without eviction, _store grows unboundedly — one entry per chat
    # per namespace, never removed until restart.  Evict all expired entries in
    # this namespace on every write (O(n) over the namespace, n = active chats,
    # typically small; write frequency is low so this is cheap).
    now = time.monotonic()
    ns = _store[namespace]
    expired = [cid for cid, (_, exp) in ns.items() if now > exp]
    for cid in expired:
        del ns[cid]


def invalidate(namespace: str, chat_id: int) -> None:
    """Remove cached entry immediately (call when a setting is deleted)."""
    ns = _store.get(namespace)
    if ns:
        ns.pop(chat_id, None)


# ══════════════════════════════════════════════════════════════════════════════
#  Per-chat settings  (get_setting / set_setting)
# ══════════════════════════════════════════════════════════════════════════════
#
#  These are persistent group settings toggled by admins (/adminonly,
#  /autoleave, /autoclear, /maxqueue).  They live in the bot_settings DB table
#  under keys formatted as "chat_{chat_id}_{key}" and are loaded into the
#  in-memory dict below at startup so get_setting() can be synchronous.
#
#  Usage (same as before):
#      val = get_setting(chat_id, "admin_only", False)   # sync
#      await set_setting(chat_id, "max_queue", 100)      # async, persists to DB

import json as _json

# In-memory store: { chat_id: { key: value } }
_settings_cache: dict[int, dict[str, Any]] = {}


def get_setting(chat_id: int, key: str, default: Any = None) -> Any:
    """Sync read of a per-chat setting.  Returns *default* if not set."""
    return _settings_cache.get(chat_id, {}).get(key, default)


async def set_setting(chat_id: int, key: str, value: Any) -> None:
    """Save per-chat setting in memory and persist to DB."""
    if chat_id not in _settings_cache:
        _settings_cache[chat_id] = {}
    _settings_cache[chat_id][key] = value
    try:
        from database import set_bot_setting
        await set_bot_setting(f"chat_{chat_id}_{key}", _json.dumps(value))
    except Exception:
        pass  # memory always updated; DB failure is non-fatal


async def load_all_chat_settings() -> None:
    """
    Load every per-chat setting from DB into _settings_cache.
    Call once at startup, after the GitHub/DB restore completes.
    """
    try:
        from database import get_all_chat_settings
        rows = await get_all_chat_settings()   # list of (key, value) where key="chat_{id}_{name}"
        for db_key, db_val in rows:
            # db_key format: "chat_{chat_id}_{setting_name}"
            parts = db_key.split("_", 2)          # ["chat", "<id>", "<name>"]
            if len(parts) != 3:
                continue
            try:
                chat_id = int(parts[1])
            except ValueError:
                continue
            setting_name = parts[2]
            try:
                value = _json.loads(db_val)
            except Exception:
                value = db_val
            if chat_id not in _settings_cache:
                _settings_cache[chat_id] = {}
            _settings_cache[chat_id][setting_name] = value
    except Exception:
        pass  # non-fatal; defaults will be used
