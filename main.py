"""
main.py — Apex Bot v6.0 Ultimate
✅ FloodWait retry loop (no more crash-restart death spiral on Heroku)
✅ Proper shutdown handling
"""

import asyncio
import logging
import sys
import os

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

# ── CRITICAL FIX: pyrofork → pyrogram shim ────────────────────────────────────
# pytgcalls aur bot code dono `from pyrogram import X` use karte hain.
# pyrofork apna package `pyrofork` namespace mein install karta hai, isliye
# ye shim use import karna zaroori hai sabse pehle, taaki pyrogram.* imports
# pyrofork par redirect ho jaayein (InputGroupCallSlug issue fix).
import pyrogram_compat  # noqa: F401  — side-effect only

# ── FFmpeg PATH fix ───────────────────────────────────────────────────────────
# ntgcalls (used by pytgcalls) calls `shutil.which('ffprobe')` before every
# stream start.  If ffprobe is missing, every /play fails with
# "ffprobe not installed".
#
# Priority:
#  1. Vendor static build installed by bin/post_compile (Heroku)
#  2. static-ffmpeg Python package  ← cross-platform fallback, no OS install needed
#  3. System ffmpeg already in PATH (most Linux VPS / Docker images)

import shutil as _shutil

_HERE = os.path.dirname(os.path.abspath(__file__))
_FFMPEG_CANDIDATES = [
    os.path.join(_HERE, "vendor", "ffmpeg", "bin"),   # post_compile static build
    "/app/vendor/ffmpeg/bin",                           # explicit Heroku path
]
for _d in _FFMPEG_CANDIDATES:
    if os.path.isdir(_d) and _d not in os.environ.get("PATH", ""):
        os.environ["PATH"] = _d + ":" + os.environ.get("PATH", "")
        break

# Fallback: use static-ffmpeg Python package if ffprobe is still not in PATH.
# This handles VPS / Railway / Render / any non-Heroku host where the vendor
# directory doesn't exist and ffmpeg isn't pre-installed.
if not _shutil.which("ffprobe"):
    try:
        import static_ffmpeg
        static_ffmpeg.add_paths()          # downloads binaries once, then adds to PATH
    except ImportError:
        pass   # static-ffmpeg not installed; rely on system ffmpeg

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s | %(levelname)-8s | %(name)s | %(message)s",
    datefmt="%Y-%m-%d %H:%M:%S",
)
logging.getLogger("pyrogram").setLevel(logging.WARNING)
logging.getLogger("pytgcalls").setLevel(logging.WARNING)
logging.getLogger("ntgcalls").setLevel(logging.WARNING)

log = logging.getLogger("ApexBot")

# ── pytgcalls ffprobe patch ───────────────────────────────────────────────────
# PROBLEM: pytgcalls 2.3.x ka check_stream() YouTube CDN URLs pe fail karta hai.
#
# Root cause sequence (pytgcalls/ffmpeg.py):
#   1. ffprobe chalta hai stream URL ke saath — bina User-Agent headers ke
#   2. YouTube CDN (googlevideo.com) empty response deta hai (IP throttling
#      ya missing User-Agent) → stdout = b''
#   3. json.loads('') → JSONDecodeError  (line 56 of pytgcalls/ffmpeg.py)
#   4. pytgcalls ka except block: ffprobe.kill() try karta hai
#   5. ffprobe already exit ho chuka → ProcessLookupError (line 62)
#   6. ProcessLookupError call_py.play() se propagate hoti hai → /play crash
#
# FIX: pytgcalls.types.stream.media_stream ka check_stream reference patch
# karo. Patched version:
#   a. ffprobe ko YouTube User-Agent headers ke saath chalata hai
#   b. Agar woh bhi fail ho, minimal stream info return karta hai
#   c. Streaming proceed hoti hai — ffmpeg actual format detection khud karta hai
#
# NOTE: media_stream.py mein check_stream "from pytgcalls.ffmpeg import check_stream"
# se import hota hai, isliye module reference patch karna zaroori hai.

def _apply_pytgcalls_ffprobe_patch():
    import asyncio
    import json
    import logging as _logging

    _plog = _logging.getLogger("ApexBot.ptc_patch")

    _YT_HEADERS = (
        "User-Agent: Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
        "AppleWebKit/537.36 (KHTML, like Gecko) Chrome/125.0.0.0 Safari/537.36\r\n"
        "Referer: https://www.youtube.com/\r\n"
    )

    async def _safe_ffprobe(url: str, timeout: float = 12.0) -> dict:
        """ffprobe ko proper headers ke saath chalao. Dict ya {} return karo."""
        try:
            proc = await asyncio.create_subprocess_exec(
                "ffprobe",
                "-headers", _YT_HEADERS,
                "-v", "quiet",
                "-print_format", "json",
                "-show_streams",
                url,
                stdout=asyncio.subprocess.PIPE,
                stderr=asyncio.subprocess.DEVNULL,
            )
            try:
                stdout, _ = await asyncio.wait_for(proc.communicate(), timeout=timeout)
            except asyncio.TimeoutError:
                try:
                    proc.kill()
                except Exception:
                    pass
                return {}
            if stdout:
                try:
                    return json.loads(stdout.decode("utf-8")) or {}
                except (json.JSONDecodeError, UnicodeDecodeError):
                    pass
        except Exception:
            pass
        return {}

    async def _patched_check_stream(media_path, *args, **kwargs):
        """
        Replacement for pytgcalls.ffmpeg.check_stream.
        YouTube CDN URLs pe fail hone par User-Agent ke saath retry karo,
        aur phir bhi fail hone par minimal audio stream info return karo
        taaki play() proceed ho sake.
        """
        url_str = str(media_path)

        # Pehle proper headers ke saath try karo
        result = await _safe_ffprobe(url_str)
        if result:
            return result

        # Fallback: minimal audio stream descriptor
        # pytgcalls ko batao stream audio hai — ffmpeg actual format detect karega
        _plog.debug(
            "ffprobe empty for %s — using minimal audio fallback", url_str[:80]
        )
        return {
            "streams": [
                {
                    "codec_type": "audio",
                    "codec_name": "aac",
                    "sample_rate": "44100",
                    "channels": 2,
                }
            ]
        }

    # Patch the reference inside media_stream module (where it's actually called)
    try:
        import pytgcalls.types.stream.media_stream as _ms_mod
        _ms_mod.check_stream = _patched_check_stream
        log.info("✅ pytgcalls ffprobe patch applied — YouTube CDN URLs will work")
    except Exception as _e:
        log.warning("⚠️ pytgcalls ffprobe patch failed: %s", _e)

_apply_pytgcalls_ffprobe_patch()


async def _start_client(client, name: str, max_retries: int = 10):
    """Start a Pyrogram client with FloodWait retry.

    This prevents the crash-restart death spiral on Heroku:
    - Without this: FloodWait → crash → Heroku restarts → FloodWait again → loop
    - With this: FloodWait → sleep inside the process → retry → success
    """
    from pyrogram.errors import FloodWait, AuthKeyUnregistered, UserDeactivated

    for attempt in range(1, max_retries + 1):
        try:
            await client.start()
            log.info(f"✅ {name} started successfully (attempt {attempt})")
            return
        except FloodWait as e:
            wait = e.value + 10   # add 10s buffer
            log.warning(
                f"⏳ FloodWait on {name}: Telegram says wait {e.value}s "
                f"(sleeping {wait}s, attempt {attempt}/{max_retries})..."
            )
            # Log progress every 30s so Heroku doesn't think we're dead
            elapsed = 0
            while elapsed < wait:
                chunk = min(30, wait - elapsed)
                await asyncio.sleep(chunk)
                elapsed += chunk
                remaining = wait - elapsed
                if remaining > 0:
                    log.info(f"  ⏳ {name}: {remaining:.0f}s remaining before retry...")
            log.info(f"  ✅ FloodWait over — retrying {name}...")
        except (AuthKeyUnregistered, UserDeactivated) as e:
            log.error(
                f"❌ {name} session is invalid: {e}\n"
                f"   → For BOT: regenerate BOT_TOKEN env var\n"
                f"   → For ASSISTANT: regenerate SESSION_STRING env var"
            )
            raise
        except Exception as e:
            err_str = str(e).lower()
            # BUG FIX: plugin import fail hone ke baad client TCP-level pe connected
            # rehta hai. Retry pe "Client is already connected" aata hai — ye success hai.
            if "already" in err_str and "connect" in err_str:
                log.info(f"✅ {name} already connected — treating as success")
                return
            log.error(f"❌ {name} start error (attempt {attempt}): {e}")
            if attempt >= max_retries:
                raise
            await asyncio.sleep(5 * attempt)   # exponential backoff

    raise RuntimeError(f"Could not start {name} after {max_retries} attempts")


async def main():
    from pyrogram import idle
    from clients import bot, assistant, call_py
    from database import init_db
    from config import validate_config

    log.info("🚀 Starting Apex All-in-One Bot v6.0 ...")

    try:
        validate_config()
    except RuntimeError as exc:
        log.error("❌ Configuration error: %s", exc)
        # A worker with invalid credentials must fail loudly so Heroku marks
        # the dyno unhealthy instead of reporting a false successful exit.
        raise SystemExit(2) from exc

    await init_db()
    log.info("✅ Database initialised")

    # ── Restore live data from GitHub (before accepting any updates) ─
    try:
        from helpers.github_sync import restore_from_github, auto_sync_loop
        restore_result = await restore_from_github()
        if restore_result.get("ok"):
            restored = restore_result.get("restored", {})
            if restored:
                log.info("✅ GitHub restore: %s", restored)
            else:
                log.info("ℹ️ GitHub restore: no data yet (first run)")
        else:
            log.warning("⚠️ GitHub restore skipped: %s", restore_result.get("error"))
    except Exception as _ghe:
        log.warning("⚠️ GitHub restore failed (continuing): %s", _ghe)

    # ── Start clients with FloodWait protection ──────────────────────
    await _start_client(bot, "Bot")
    if assistant is not None:
        await _start_client(assistant, "Assistant")
    else:
        log.warning("⚠️ Assistant skipped — SESSION_STRING not set (voice-chat disabled)")

    if call_py is not None:
        await call_py.start()
        log.info("✅ PyTgCalls started")
    else:
        log.warning("⚠️ PyTgCalls skipped — no assistant (voice-chat disabled)")

    # ── Restore persistent reminders and schedules from DB ──────────
    try:
        from plugins.reminder import startup_restore_reminders
        asyncio.create_task(startup_restore_reminders(bot), name="restore-reminders")
        log.info("✅ Reminder restore task started")
    except Exception as _rre:
        log.warning("⚠️ Reminder restore failed: %s", _rre)

    try:
        from plugins.schedule import startup_restore_schedules
        asyncio.create_task(startup_restore_schedules(bot), name="restore-schedules")
        log.info("✅ Schedule restore task started")
    except Exception as _rse:
        log.warning("⚠️ Schedule restore failed: %s", _rse)

    # ── Start GitHub auto-sync background loop ───────────────────────
    try:
        asyncio.create_task(auto_sync_loop(), name="github-auto-sync")
        log.info("✅ GitHub auto-sync loop started")
    except Exception as _gse:
        log.warning("⚠️ GitHub auto-sync task failed to start: %s", _gse)

    bot_me  = await bot.get_me()
    asst_me = (await assistant.get_me()) if assistant is not None else None

    # ── Premium emoji registration ───────────────────────────────────────────
    # Check each client's Telegram Premium status and register the premium-capable
    # ones so that install_premium_ui() injects <tg-emoji> tags only for them.
    # Non-premium clients fall back to plain text / regular emojis automatically.
    try:
        from helpers.premium_ui import register_premium_client
        from config import FORCE_PREMIUM_EMOJIS
        # BUG FIX v7.2: Do NOT apply FORCE_PREMIUM_EMOJIS to the main bot.
        # FORCE_PREMIUM_EMOJIS auto-enables when SESSION_STRING is set (for the assistant).
        # If we incorrectly mark the main bot as premium, it injects <tg-emoji> HTML tags
        # into every message. Telegram rejects these for non-premium accounts with
        # [400 BAD_REQUEST: can't parse entities] → all messages + callback edits fail silently.
        # This caused ALL inline keyboard buttons to appear broken.
        # Fix: bot_premium only uses the real is_premium flag from Telegram, never forced.
        bot_premium = getattr(bot_me, "is_premium", False) or False
        # BUG FIX: previously this force-marked the assistant as "premium"
        # just because SESSION_STRING was set (bool(_ss)), regardless of
        # whether that account actually has Telegram Premium. That made a
        # non-premium assistant send <tg-emoji> HTML tags, which Telegram
        # rejects with "can't parse entities" for non-premium accounts —
        # breaking sends/edits (e.g. now-playing card updates) silently.
        # Now: trust ONLY the real is_premium flag from get_me(), with
        # FORCE_PREMIUM_EMOJIS as an explicit manual override for the rare
        # case pyrofork misreports it (see config.py — defaults to False).
        asst_premium = (getattr(asst_me, "is_premium", None) is True or FORCE_PREMIUM_EMOJIS) if asst_me is not None else False

        if bot_premium:
            register_premium_client(bot)
            log.info("✅ Bot is Telegram Premium — premium animated emojis ACTIVE")
        else:
            log.info(
                "ℹ️ Bot account is not Telegram Premium — "
                "using regular emojis"
            )

        if asst_premium and assistant is not None:
            register_premium_client(assistant)
            log.info("✅ Assistant is Telegram Premium — premium animated emojis ACTIVE for assistant")
        elif assistant is None:
            log.info("ℹ️ Assistant not active — premium emojis skipped for assistant")
        else:
            log.info(
                "ℹ️ Assistant is not Telegram Premium — "
                "regular emojis will be used for assistant messages"
            )
    except Exception as _pe:
        log.warning("⚠️ Premium client registration failed: %s", _pe)

    # Keep Telegram's "/" menu in sync with the real plugin handlers.
    # A failure here must not prevent the music bot from starting.
    try:
        from helpers.commands import register_bot_commands

        await register_bot_commands(bot)
        log.info("✅ Telegram slash-command menus registered")
    except Exception as exc:
        log.warning("⚠️ Could not register Telegram slash-command menus: %s", exc)

    log.info(f"🤖 Bot       : @{bot_me.username or 'unknown'} ({bot_me.first_name or '—'})")
    if asst_me is not None:
        log.info(f"👤 Assistant : @{asst_me.username or 'no_username'} ({asst_me.first_name or '— no name set'})")
    else:
        log.warning("👤 Assistant : DISABLED (SESSION_STRING not set)")
    log.info("━" * 55)
    log.info("  🎵 MUSIC  : /play /vplay /pause /resume /skip /stop")
    log.info("  👮 ADMIN  : /ban /kick /mute /warn /promote /demote")
    log.info("  🛡️  SAFETY : Anti-spam, Word Filter, GBAN, Captcha")
    log.info("  📝 TOOLS  : Notes, Stats, Broadcast, Welcome")
    log.info("  🎮 FUN    : Games, AI Chat, Jokes, Shayari")
    log.info("━" * 55)
    log.info("🔥 Apex Bot v6.0 is LIVE!")

    # Send startup notification to LOG_CHANNEL (-1004334848663) — 100x logging
    try:
        from helpers.logger_channel import log_startup
        log_startup(bot_me, asst_me)
    except Exception as _lse:
        log.warning("Startup channel log failed: %s", _lse)

    # ── Load per-chat settings into memory (get_setting works sync after this) ──
    try:
        from helpers.settings_cache import load_all_chat_settings
        await load_all_chat_settings()
        log.info("✅ Per-chat settings loaded")
    except Exception as _sce:
        log.warning("⚠️ Per-chat settings load failed: %s", _sce)

    # ── Prime branding cache from DB (so get_brand_sync works everywhere) ──
    try:
        from helpers.branding import prime_cache
        await prime_cache()
        log.info("✅ Branding cache primed")
    except Exception as _bce:
        log.warning("⚠️ Branding cache prime failed: %s", _bce)

    await idle()

    log.info("🛑 Shutting down...")

    # ── Final GitHub sync before exit ────────────────────────────────
    try:
        from helpers.github_sync import sync_all_data
        log.info("💾 Final GitHub sync on shutdown...")
        await sync_all_data(reason="shutdown")
        log.info("✅ Shutdown sync complete")
    except Exception as _gse:
        log.warning("⚠️ Shutdown sync failed: %s", _gse)

    try:
        from helpers.logger_channel import log_shutdown
        log_shutdown()
        import asyncio as _a
        await _a.sleep(0.5)  # Give log_shutdown task time to fire
    except Exception:
        pass
    try:
        if call_py is not None:
            await call_py.stop()
    except Exception:
        pass
    try:
        if assistant is not None:
            await assistant.stop()
    except Exception:
        pass
    try:
        await bot.stop()
    except Exception:
        pass
    log.info("👋 Goodbye!")


if __name__ == "__main__":
    # ── HEROKU STANDARD-2X FIX: Proper SIGTERM handling ──────────────────────
    # Problem: On Heroku Standard-2X dynos, Heroku sends SIGTERM when recycling
    # or scaling dynos. Without a handler, pyrogram's idle() catches the signal
    # but the bot process enters a half-shutdown state — it's "alive" but stops
    # processing Telegram updates silently.
    #
    # Fix: Install SIGTERM handler BEFORE asyncio.run() so the signal interrupts
    # idle() cleanly and triggers our shutdown sequence.
    #
    # Also guards against "two worker dynos" scenario: if the user accidentally
    # runs 2 worker dynos, both will try to get Telegram updates — one wins,
    # the other "silently" gets nothing. Always ensure Heroku formation has
    # worker quantity = 1 in app.json.
    import signal as _signal

    def _handle_sigterm(signum, frame):
        log.warning("⚠️  SIGTERM received — initiating graceful shutdown...")
        raise SystemExit(0)

    _signal.signal(_signal.SIGTERM, _handle_sigterm)
    _signal.signal(_signal.SIGINT,  _handle_sigterm)

    asyncio.run(main())
