"""
seek.py — Seek/position commands
Commands: /seek [mm:ss], /forward [sec], /backward [sec], /restart
"""
import logging
from pyrogram import Client, filters
from pyrogram.types import Message
from clients import bot, call_py
from helpers.queue import get_current

log = logging.getLogger("ApexBot.seek")


def _parse_time(s: str) -> int:
    """Parse mm:ss or seconds string to total seconds."""
    s = s.strip()
    if ':' in s:
        parts = s.split(':')
        if len(parts) == 2:
            return int(parts[0]) * 60 + int(parts[1])
        elif len(parts) == 3:
            return int(parts[0]) * 3600 + int(parts[1]) * 60 + int(parts[2])
    return int(s)


def _fmt(secs: int) -> str:
    m, s = divmod(abs(secs), 60)
    h, m = divmod(m, 60)
    if h:
        return f"{h}:{m:02d}:{s:02d}"
    return f"{m}:{s:02d}"


@bot.on_message(filters.command("seek") & filters.group)
async def seek_cmd(_, message: Message):
    """Jump to specific position."""
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
    """Skip forward N seconds."""
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
        # Get current position — if not available, estimate
        await call_py.seek(message.chat.id, secs)  # relative forward
        await message.reply(f"⏩ Forwarded `{secs}s`")
    except Exception as e:
        await message.reply(f"❌ Error: `{e}`")


@bot.on_message(filters.command("backward") & filters.group)
async def backward_cmd(_, message: Message):
    """Go back N seconds."""
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
        await call_py.seek(message.chat.id, -secs)
        await message.reply(f"⏪ Rewound `{secs}s`")
    except Exception as e:
        await message.reply(f"❌ Error: `{e}`")


@bot.on_message(filters.command("restart") & filters.group)
async def restart_song(_, message: Message):
    """Restart current song from beginning."""
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
