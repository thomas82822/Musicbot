"""
antioporn.py — Anti-NSFW media protection
Commands: /antioporn on/off
"""
import logging
from pyrogram import Client, filters
from pyrogram.types import Message
from pyrogram.enums import ChatMemberStatus
from clients import bot
from helpers.decorators import admin_only
from database import get_antiporn, set_antiporn

log = logging.getLogger("ApexBot.antioporn")


@bot.on_message(filters.command("antioporn") & filters.group)
@admin_only
async def antioporn_cmd(_, message: Message):
    chat_id = message.chat.id
    arg = message.command[1].lower() if len(message.command) > 1 else None
    if arg == "on":
        await set_antiporn(chat_id, True)
        await message.reply("🛡 **Anti-Porn** enabled! NSFW media will be deleted.")
    elif arg == "off":
        await set_antiporn(chat_id, False)
        await message.reply("✅ **Anti-Porn** disabled.")
    else:
        enabled = await get_antiporn(chat_id)
        status = "✅ ON" if enabled else "❌ OFF"
        await message.reply(f"🔞 Anti-Porn Status: {status}\nUsage: `/antioporn on/off`")


@bot.on_message(
    filters.group & ~filters.bot & (filters.photo | filters.video | filters.animation | filters.sticker),
    group=3
)
async def check_porn(client: Client, message: Message):
    if not message.from_user:
        return
    chat_id = message.chat.id
    if not await get_antiporn(chat_id):
        return

    # Skip admins
    try:
        member = await client.get_chat_member(chat_id, message.from_user.id)
        if member.status in (ChatMemberStatus.ADMINISTRATOR, ChatMemberStatus.OWNER):
            return
    except Exception:
        return

    # Basic NSFW detection — flag by file attributes
    # Production bots use ML APIs; this is a basic heuristic approach
    is_nsfw = False

    if message.animation:
        # Animated GIFs with certain attributes
        pass

    # Without an ML API, we detect based on caption keywords
    caption = (message.caption or "").lower()
    nsfw_keywords = ["nsfw", "nude", "naked", "xxx", "porn", "18+", "explicit"]
    if any(kw in caption for kw in nsfw_keywords):
        is_nsfw = True

    if is_nsfw:
        try:
            await message.delete()
            warn = await message.reply(
                f"⚠️ {message.from_user.mention}, NSFW content is not allowed!"
            )
            import asyncio
            await asyncio.sleep(5)
            await warn.delete()
        except Exception as e:
            log.debug("antioporn delete error: %s", e)
