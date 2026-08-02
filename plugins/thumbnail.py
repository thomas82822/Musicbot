"""
thumbnail.py — Dynamic Now Playing card / thumbnail management
Commands: /thumbnail, /setthumb, /delthumb, /resetthumb
"""
import io
import os
import logging
from pyrogram import Client, filters
from pyrogram.types import Message
from clients import bot
from helpers.np_card import generate_np_card
from helpers.queue import get_current
from database import get_user_thumb, set_user_thumb, del_user_thumb

log = logging.getLogger("ApexBot.thumbnail")


@bot.on_message(filters.command("thumbnail") & filters.group)
async def thumbnail_cmd(_, message: Message):
    """Show Now Playing thumbnail card for current song."""
    chat_id = message.chat.id
    song = get_current(chat_id)
    if not song:
        return await message.reply("❌ Nothing is playing right now.")

    user = message.from_user
    msg = await message.reply("🎨 Generating thumbnail...")
    try:
        card_bytes = await generate_np_card(
            title=song.title,
            artist=song.artist or "Unknown",
            duration=song.duration,
            thumbnail_url=song.thumbnail,
            requested_by=song.requested_by,
            user_id=user.id if user else 0,
        )
        await message.reply_photo(
            photo=io.BytesIO(card_bytes),
            caption=f"🎵 **{song.title}**\n👤 {song.requested_by}"
        )
        await msg.delete()
    except Exception as e:
        log.error("thumbnail error: %s", e)
        await msg.edit(f"❌ Failed to generate thumbnail: `{e}`")


@bot.on_message(filters.command("setthumb") & filters.private)
async def set_thumb(_, message: Message):
    """Set custom thumbnail (reply to a photo in DM)."""
    user_id = message.from_user.id
    if not message.reply_to_message or not message.reply_to_message.photo:
        return await message.reply("❌ Reply to a **photo** to set as your custom thumbnail.")

    photo = message.reply_to_message.photo
    file_id = photo.file_id
    try:
        await set_user_thumb(user_id, file_id)
        await message.reply("✅ Custom thumbnail set! It will appear on your NP cards.")
    except Exception as e:
        await message.reply(f"❌ Error: `{e}`")


@bot.on_message(filters.command("delthumb") & filters.private)
async def del_thumb(_, message: Message):
    """Delete custom thumbnail."""
    user_id = message.from_user.id
    try:
        await del_user_thumb(user_id)
        await message.reply("🗑 Custom thumbnail deleted.")
    except Exception as e:
        await message.reply(f"❌ Error: `{e}`")


@bot.on_message(filters.command("resetthumb") & filters.private)
async def reset_thumb(_, message: Message):
    """Reset to default thumbnail."""
    user_id = message.from_user.id
    try:
        await del_user_thumb(user_id)
        await message.reply("🔄 Thumbnail reset to default.")
    except Exception as e:
        await message.reply(f"❌ Error: `{e}`")
