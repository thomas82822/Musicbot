"""
seek.py — Seek/position commands
Commands: /seek [mm:ss], /forward [sec], /backward [sec], /restart

BUG FIX: /forward and /backward previously called call_py.seek() with a
relative offset (e.g. 10 or -10 seconds) but pytgcalls seek() takes an
ABSOLUTE position.  Fixed: estimate current playback position from
_start_time and add/subtract the offset before calling seek().
"""
import logging
import time
from pyrogram import Client, filters
from pyrogram.types import Message
from clients import bot, call_py
from helpers.queue import get_current

log = logging.getLogger("ApexBot.seek")


def _parse_time(s: str) -> int:
    """Parse mm:ss or hh:mm:ss or bare seconds string to total seconds."""
    s = s.strip()
    if ':' in s:
        parts = s.split(':')
        if len(parts) == 2:
            return int(parts[0]) * 60 + int(parts[1])
        elif len(parts) == 3:
            return int(parts[0]) * 3600 + int(parts[1]) * 60 + int(parts[2])
    return int(s)


def _fmt(secs: int) -> str:
    secs = max(0, secs)
    m, s = divmod(secs, 60)
    h, m = divmod(m, 60)
    if h:
        return f"{h}:{m:02d}:{s:02d}"
    return f"{m}:{s:02d}"


def _current_position(chat_id: int) -> int:
    """
    Estimate current playback position in seconds by reading _start_time
    from play.py.  Returns 0 if unavailable (safe default for forward/backward).
    """
    try:
        from plugins.play import _start_time
        started = _start_time.get(chat_id, 0.0)
        if started:
            return max(0, int(time.time() - started))
    except Exception:
        pass
    return 0


@bot.on_message(filters.command("seek") & filters.group)
async def seek_cmd(_, message: Message):
    """Jump to a specific absolute position in the current track."""
    if not call_py:
        return await message.reply("❌ Voice chat unavailable.")
    if not get_current(message.chat.id):
        return await message.reply("❌ Nothing is playing.")
    if len(message.command) < 2:
        return await message.reply("**Usage:** `/seek [mm:ss]` e.g. `/seek 1:30`")
    try:
        secs = _parse_time(message.command[1])
        await call_py.seek(message.chat.id, secs)
        await message.reply(f"⏩ Seeked to `{_fmt(secs)}`")
    except Exception as e:
        await message.reply(f"❌ Seek failed: `{e}`")


@bot.on_message(filters.command("forward") & filters.group)
async def forward_cmd(_, message: Message):
    """Skip forward N seconds from current playback position."""
    if not call_py:
        return await message.reply("❌ Voice chat unavailable.")
    song = get_current(message.chat.id)
    if not song:
        return await message.reply("❌ Nothing is playing.")
    try:
        secs = int(message.command[1]) if len(message.command) > 1 else 10
    except ValueError:
        secs = 10

    try:
        # BUG FIX: seek() is ABSOLUTE — must add offset to current position.
        current = _current_position(message.chat.id)
        target  = current + secs
        await call_py.seek(message.chat.id, target)
        await message.reply(f"⏩ Forwarded `{secs}s` → `{_fmt(target)}`")
    except Exception as e:
        await message.reply(f"❌ Error: `{e}`")


@bot.on_message(filters.command("backward") & filters.group)
async def backward_cmd(_, message: Message):
    """Go back N seconds from current playback position."""
    if not call_py:
        return await message.reply("❌ Voice chat unavailable.")
    song = get_current(message.chat.id)
    if not song:
        return await message.reply("❌ Nothing is playing.")
    try:
        secs = int(message.command[1]) if len(message.command) > 1 else 10
    except ValueError:
        secs = 10
    try:
        # BUG FIX: seek() is ABSOLUTE — clamp target to 0 to avoid negative.
        current = _current_position(message.chat.id)
        target  = max(0, current - secs)
        await call_py.seek(message.chat.id, target)
        await message.reply(f"⏪ Rewound `{secs}s` → `{_fmt(target)}`")
    except Exception as e:
        await message.reply(f"❌ Error: `{e}`")


@bot.on_message(filters.command("restart") & filters.group)
async def restart_song(_, message: Message):
    """Restart current song from the beginning."""
    if not call_py:
        return await message.reply("❌ Voice chat unavailable.")
    song = get_current(message.chat.id)
    if not song:
        return await message.reply("❌ Nothing is playing.")
    try:
        await call_py.seek(message.chat.id, 0)
        await message.reply(f"🔄 Restarted: **{song.title}**")
    except Exception as e:
        await message.reply(f"❌ Error: `{e}`")
