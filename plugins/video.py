"""
video.py — Video mode management
Commands: /vplay, /vqueue, /videomode
(Core vplay is in play.py; this adds management commands)
"""
import logging
from pyrogram import Client, filters
from pyrogram.types import Message
from clients import bot
from helpers.queue import get_queue, get_current

log = logging.getLogger("ApexBot.video")

_video_mode: dict[int, bool] = {}


@bot.on_message(filters.command("videomode") & filters.group)
async def video_mode_toggle(_, message: Message):
    """Toggle between audio and video mode."""
    chat_id = message.chat.id
    _video_mode[chat_id] = not _video_mode.get(chat_id, False)
    mode = "🎬 Video" if _video_mode[chat_id] else "🎵 Audio"
    await message.reply(
        f"Switched to **{mode} mode**.\n\n"
        f"{'Use `/vplay` or `/play` (auto-video) now.' if _video_mode[chat_id] else 'Use `/play` for audio.'}"
    )


@bot.on_message(filters.command("vqueue") & filters.group)
async def vqueue(_, message: Message):
    """Show video queue."""
    chat_id = message.chat.id
    current = get_current(chat_id)
    queue = get_queue(chat_id)

    video_current = current if (current and current.is_video) else None
    video_queue = [s for s in queue if s.is_video]

    if not video_current and not video_queue:
        return await message.reply(
            "🎬 No video queue.\n\nUse `/vplay [song/URL]` to add videos!"
        )

    lines = []
    if video_current:
        lines.append(f"🎬 **Now Playing (Video):**\n└ {video_current.title}")

    if video_queue:
        lines.append(f"\n📋 **Video Queue ({len(video_queue)}):**")
        for i, s in enumerate(video_queue[:10], 1):
            lines.append(f"{i}. {s.title} — {s.requested_by}")

    await message.reply("\n".join(lines))
