"""
antilink.py — Anti-link protection
Commands: /antilink on/off, /whitelist [domain], /blacklist [domain]
"""
import re
import logging
from pyrogram import Client, filters
from pyrogram.types import Message
from clients import bot
from helpers.decorators import admin_only
from database import get_antilink_settings, set_antilink_setting

log = logging.getLogger("ApexBot.antilink")

LINK_PATTERN = re.compile(
    r"(https?://|www\.|t\.me/|telegram\.me/|discord\.gg/|bit\.ly/)",
    re.IGNORECASE
)
TG_INVITE_PATTERN = re.compile(r"t\.me/[\+joinchat]|telegram\.me/", re.IGNORECASE)


@bot.on_message(filters.command("antilink") & filters.group)
@admin_only
async def antilink_cmd(_, message: Message):
    chat_id = message.chat.id
    arg = message.command[1].lower() if len(message.command) > 1 else None

    if arg == "on":
        await set_antilink_setting(chat_id, "enabled", True)
        await message.reply("🛡 **Anti-Link** enabled! Links will be deleted.")
    elif arg == "off":
        await set_antilink_setting(chat_id, "enabled", False)
        await message.reply("✅ **Anti-Link** disabled.")
    else:
        settings = await get_antilink_settings(chat_id)
        status = "✅ ON" if settings.get("enabled") else "❌ OFF"
        await message.reply(
            f"🔗 **Anti-Link Status:** {status}\n\n"
            "Usage:\n`/antilink on` — Enable\n`/antilink off` — Disable"
        )


@bot.on_message(filters.group & ~filters.bot & filters.text, group=1)
async def check_links(client: Client, message: Message):
    if not message.from_user:
        return
    chat_id = message.chat.id
    settings = await get_antilink_settings(chat_id)
    if not settings.get("enabled"):
        return

    # Check if user is admin
    try:
        member = await client.get_chat_member(chat_id, message.from_user.id)
        from pyrogram.enums import ChatMemberStatus
        if member.status in (ChatMemberStatus.ADMINISTRATOR, ChatMemberStatus.OWNER):
            return
    except Exception:
        return

    text = message.text or ""
    if LINK_PATTERN.search(text):
        try:
            await message.delete()
            warn_msg = await message.reply(
                f"⚠️ {message.from_user.mention}, links are not allowed here!"
            )
            import asyncio
            await asyncio.sleep(5)
            await warn_msg.delete()
        except Exception as e:
            log.debug("antilink delete error: %s", e)
