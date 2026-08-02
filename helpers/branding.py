"""
branding.py — Dynamic Bot Branding System
━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
Owner panel se bot ka naam, support link, description sab ek jagah se
change karo — pure bot mein automatically apply ho jaata hai.
Users ko kabhi pata nahi chalta kiska bot hai.

TWO INTERFACES:
  1. Async (fresh DB read):  `await get_brand("bot_name")`
  2. Sync  (from hot cache): `get_brand_sync("bot_name")`

Sync cache:
  - Startup pe `await prime_cache()` se DB se load hota hai
  - Owner panel branding save hone pe `update_cache(key, value)` se update hota hai
  - In-memory hone ki wajah se zero latency
"""

import logging
from database import get_bot_setting, set_bot_setting, delete_bot_setting

log = logging.getLogger("ApexBot.branding")

# ── Sync in-memory cache (prime_cache se populate hota hai) ───────
_cache: dict[str, str] = {}

# ── Branding keys + display labels ────────────────────────────────
BRANDING_KEYS = {
    "bot_name":        "🤖 Bot Name",
    "bot_version":     "🏷️ Bot Version",
    "support_link":    "💬 Support Link",
    "updates_channel": "📢 Updates Channel",
    "bot_description": "📝 Bot Description",
    "bot_tagline":     "✨ Bot Tagline",
}

# ── Example hints shown in owner panel ─────────────────────────────
BRANDING_HINTS = {
    "bot_name":        "e.g. `🎵 XYZ Music Bot`",
    "bot_version":     "e.g. `v2.0 Premium`",
    "support_link":    "e.g. `https://t.me/YourGroup`",
    "updates_channel": "e.g. `https://t.me/YourChannel`",
    "bot_description": "e.g. `HD Music & Group Management`",
    "bot_tagline":     "e.g. `Play • Control • Enjoy`",
}


def _get_defaults() -> dict:
    """config.py se default values — failsafe fallback."""
    try:
        from config import BOT_NAME, BOT_VERSION, SUPPORT_CHAT
        return {
            "bot_name":        BOT_NAME,
            "bot_version":     BOT_VERSION,
            "support_link":    SUPPORT_CHAT,
            "updates_channel": "https://t.me/ApexAssociation",
            "bot_description": "Premium Music Experience",
            "bot_tagline":     "HD Music • Group Management • AI Chat",
        }
    except Exception:
        return {
            "bot_name":        "🎵 Music Bot",
            "bot_version":     "v1.0",
            "support_link":    "https://t.me/support",
            "updates_channel": "https://t.me/updates",
            "bot_description": "Premium Music Experience",
            "bot_tagline":     "HD Music • Group Management",
        }


# ══════════════════════════════════════════════════════════════════
# SYNC INTERFACE (zero-latency — from in-memory cache)
# ══════════════════════════════════════════════════════════════════

def get_brand_sync(key: str) -> str:
    """
    Instant sync access to branding value.
    Returns cached value → config default → hardcoded fallback.
    NO await needed. Use this in sync contexts or when speed matters.

    NOTE: Call `await prime_cache()` at bot startup to populate this.
    """
    if key in _cache:
        return _cache[key]
    return _get_defaults().get(key, "")


def update_cache(key: str, value: str) -> None:
    """
    Instantly update in-memory cache after owner saves a branding value.
    Called by owner_panel.py after DB write — no restart needed.
    """
    _cache[key] = value
    log.debug("Branding cache updated: %s = %s", key, value[:60])


def invalidate_cache(key: str) -> None:
    """Remove a key from cache (called after reset to defaults)."""
    _cache.pop(key, None)


# ══════════════════════════════════════════════════════════════════
# ASYNC INTERFACE (reads from DB — for cold starts or forced refresh)
# ══════════════════════════════════════════════════════════════════

async def get_brand(key: str) -> str:
    """
    Async: DB override → config default → hardcoded fallback.
    Also updates the sync cache so subsequent sync calls are fast.
    """
    db_key = f"brand_{key}"
    try:
        val = await get_bot_setting(db_key)
        if val:
            _cache[key] = str(val)  # keep cache warm
            return str(val)
    except Exception as e:
        log.debug("DB read error for brand key %s: %s", key, e)
    # Fallback: config/defaults (don't overwrite cache if DB was unreadable)
    default = _get_defaults().get(key, "")
    if key not in _cache:
        _cache[key] = default
    return default


async def get_all_branding() -> dict:
    """Return all branding keys and their current values (refreshes cache too)."""
    result = {}
    for key in BRANDING_KEYS:
        result[key] = await get_brand(key)
    return result


# ══════════════════════════════════════════════════════════════════
# STARTUP CACHE PRIMER
# ══════════════════════════════════════════════════════════════════

async def prime_cache() -> None:
    """
    Call ONCE at bot startup (in main.py before idle()).
    Loads all branding keys from DB into _cache.
    After this, get_brand_sync() returns current values instantly.
    """
    log.info("🎨 Priming branding cache from DB...")
    try:
        for key in BRANDING_KEYS:
            await get_brand(key)  # populates _cache as side effect
        log.info("✅ Branding cache ready: %s", {k: v[:30] for k, v in _cache.items()})
    except Exception as e:
        log.warning("⚠️ Branding cache prime partial/failed: %s", e)
