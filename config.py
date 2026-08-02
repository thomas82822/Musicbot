import os
import tempfile
from pathlib import Path


def _env_int(name: str, default: int = 0, *, required: bool = False) -> int:
    raw = os.environ.get(name, "").strip()
    if not raw:
        if required:
            raise RuntimeError(f"{name} must be configured")
        return default
    try:
        return int(raw)
    except ValueError as exc:
        raise RuntimeError(f"{name} must be a whole number") from exc


def _env_float(name: str, default: float, *, lo: float = 0.0, hi: float = float("inf")) -> float:
    raw = os.environ.get(name, "").strip()
    if not raw:
        return default
    try:
        val = float(raw)
    except ValueError as exc:
        raise RuntimeError(f"{name} must be a number") from exc
    if not (lo <= val <= hi):
        raise RuntimeError(f"{name} must be between {lo} and {hi}")
    return val


# ── Telegram credentials ───────────────────────────────────────────
API_ID          = _env_int("API_ID")
API_HASH        = os.environ.get("API_HASH", "").strip()
BOT_TOKEN       = os.environ.get("BOT_TOKEN", "").strip()
SESSION_STRING  = os.environ.get("SESSION_STRING", "").strip()

# ── Owner & logging ────────────────────────────────────────────────
OWNER_ID        = _env_int("OWNER_ID")
OWNER_USERNAME  = os.environ.get("OWNER_USERNAME", "").strip()
# Log channel — 100x detailed event logging goes here
LOG_CHANNEL     = _env_int("LOG_CHANNEL", -1004334848663)
SUPPORT_CHAT    = os.environ.get("SUPPORT_CHAT", "https://t.me/ApexAssociation")
SUDO_USERS      = [OWNER_ID] if OWNER_ID else []

# ── Telegram media archive ─────────────────────────────────────────
# Songs are automatically downloaded and uploaded to this channel for
# instant, cookie-free playback on the next request.
# Default = 0 (disabled). Set MUSIC_ARCHIVE_CHANNEL env var to your channel ID to enable.
MUSIC_ARCHIVE_CHANNEL = _env_int("MUSIC_ARCHIVE_CHANNEL", 0)  # 0 = disabled; set to your channel ID to enable
ARCHIVE_SCAN_LIMIT     = _env_int("ARCHIVE_SCAN_LIMIT", 100)

# ── Must-join channel (leave empty to disable) ─────────────────────
# MUST_JOIN force-join system removed — always None
MUST_JOIN = None

# ── Bot identity ───────────────────────────────────────────────────
BOT_NAME        = "🎵 4ST Music Bot"
BOT_VERSION     = "v7.0 Ultimate"

# ── Database ──────────────────────────────────────────────────────
_PROJECT_DIR = Path(__file__).resolve().parent
DB_PATH = os.environ.get("DB_PATH", str(_PROJECT_DIR / "apex_bot.db")).strip()

# ── Download dir ──────────────────────────────────────────────────
DOWNLOAD_DIR = os.environ.get("DOWNLOAD_DIR", tempfile.gettempdir()).strip() or tempfile.gettempdir()

# ── Volume boost ───────────────────────────────────────────────────
# pytgcalls volume range is 1-200 (100 = original level).
# VOLUME_BOOST   — default starting volume multiplier (× 100).
#                  e.g. 1.5 → starts at 150 (louder than original).
# VOLUME_ULTRA_BOOST — cap used by audio_effects "ultra" preset.
#                  Both must be in the range 0.01-2.0 (maps to 1-200).
VOLUME_BOOST       = _env_float("VOLUME_BOOST",       1.5, lo=0.01, hi=2.0)
VOLUME_ULTRA_BOOST = _env_float("VOLUME_ULTRA_BOOST", 2.0, lo=0.01, hi=2.0)

# Translate multipliers to pytgcalls integer units (1-200), clamped.
_DEFAULT_VOLUME     = max(1, min(200, round(VOLUME_BOOST       * 100)))
_ULTRA_BOOST_VOLUME = max(1, min(200, round(VOLUME_ULTRA_BOOST * 100)))


def validate_config() -> None:
    """Fail early with an actionable message instead of a Telegram auth error."""
    missing = []
    if API_ID <= 0:
        missing.append("API_ID")
    if not API_HASH:
        missing.append("API_HASH")
    if not BOT_TOKEN:
        missing.append("BOT_TOKEN")
    # SESSION_STRING is now OPTIONAL — users can login via the bot's DM flow.
    # If not set, assistant/userbot features that need voice-chat streaming
    # will be limited until the owner sets a SESSION_STRING or a user logs in.
    if not SESSION_STRING:
        import logging as _log
        _log.getLogger("ApexBot.config").warning(
            "SESSION_STRING not set — userbot voice-chat features disabled. "
            "Use /start in DM to login via the bot."
        )
    # OWNER_ID is optional (some deployments set it later)
    if ARCHIVE_SCAN_LIMIT < 1 or ARCHIVE_SCAN_LIMIT > 1000:
        raise RuntimeError("ARCHIVE_SCAN_LIMIT must be between 1 and 1000")

# ── Optional API keys (features degrade gracefully if not set) ────
GEMINI_API_KEY        = os.environ.get("GEMINI_API_KEY", "").strip()         # AI chatbot (Gemini)
SPOTIFY_CLIENT_ID     = os.environ.get("SPOTIFY_CLIENT_ID", "").strip()      # Spotify track metadata
SPOTIFY_CLIENT_SECRET = os.environ.get("SPOTIFY_CLIENT_SECRET", "").strip()  # Spotify track metadata
OPENWEATHER_KEY       = os.environ.get("OPENWEATHER_KEY", "").strip()        # /weather fallback (wttr.in used when empty)
OMDB_API_KEY          = os.environ.get("OMDB_API_KEY", "").strip()           # /movie IMDB lookup
GITHUB_TOKEN          = os.environ.get("GITHUB_TOKEN", "").strip()           # /github_info rate-limit bypass
BOT_REPO              = os.environ.get("BOT_REPO", "thomas82822/Musicbot").strip()  # owner panel GitHub backup
GENIUS_KEY            = os.environ.get("GENIUS_KEY", "").strip()             # /lyrics Genius fallback

# ── Queue settings ─────────────────────────────────────────────────
# Maximum number of songs a user/group can queue at once.
MAX_QUEUE_SIZE = _env_int("MAX_QUEUE_SIZE", 50)

# ── Economy ───────────────────────────────────────────────────────
DAILY_REWARD_MIN = 500
DAILY_REWARD_MAX = 5000
FIRST_START_MIN  = 1000
FIRST_START_MAX  = 100000

# ── Premium emoji override ─────────────────────────────────────────
# Set FORCE_PREMIUM_EMOJIS=true if the assistant/owner account has
# Telegram Premium but pyrofork's get_me() doesn't detect it correctly.
#
# BUG FIX: this used to default to "true" whenever SESSION_STRING was set,
# regardless of whether that account actually had Telegram Premium. That
# forced <tg-emoji> HTML tags onto non-premium assistant accounts, which
# Telegram rejects with "can't parse entities" — breaking sends/edits.
# Now it defaults to OFF; only an explicit FORCE_PREMIUM_EMOJIS=true
# (set this only if you KNOW the assistant account has Telegram Premium)
# enables the override. The real is_premium flag from get_me() is always
# trusted first regardless of this setting (see main.py).
FORCE_PREMIUM_EMOJIS = os.environ.get("FORCE_PREMIUM_EMOJIS", "false").lower() in ("1", "true", "yes")
