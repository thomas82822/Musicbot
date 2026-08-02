"""
antispam.py — Anti-spam protection (flood detection)
Commands: /antispam on/off
"""
import time
import asyncio
import logging
from collections import defaultdict
from pyrogram import Client, filters
from pyrogram.types import Message
from pyrogram.enums import ChatMemberStatus
from clients import bot
from helpers.decorators import admin_only

log = logging.getLogger("ApexBot.antispam")

# Flood tracker: {chat_id: {user_id: [timestamps]}}
_flood: dict[int, dict[int, list]] = defaultdict(lambda: defaultdict(list))
_warned: dict[int, set[int]] = defaultdict(set)

FLOOD_LIMIT = 5    # messages
FLOOD_WINDOW = 5   # seconds
_antispam_enabled: dict[int, bool] = {}


@bot.on_message(filters.command("antispam") & filters.group)
@admin_only
async def antispam_cmd(_, message: Message):
    chat_id = message.chat.id
    arg = message.command[1].lower() if len(message.command) > 1 else None
    if arg == "on":
        _antispam_enabled[chat_id] = True
        await message.reply("🛡 **Anti-Spam** enabled!")
    elif arg == "off":
        _antispam_enabled[chat_id] = False
        await message.reply("✅ **Anti-Spam** disabled.")
    else:
        status = "✅ ON" if _antispam_enabled.get(chat_id) else "❌ OFF"
        await message.reply(f"🚫 Anti-Spam Status: {status}\nUsage: `/antispam on/off`")


@bot.on_message(filters.group & ~filters.bot, group=2)
async def flood_check(client: Client, message: Message):
    if not message.from_user:
        return
    chat_id = message.chat.id
    if not _antispam_enabled.get(chat_id):
        return

    user_id = message.from_user.id

    # Skip admins
    try:
        member = await client.get_chat_member(chat_id, user_id)
        if member.status in (ChatMemberStatus.ADMINISTRATOR, ChatMemberStatus.OWNER):
            return
    except Exception:
        return

    now = time.time()
    user_msgs = _flood[chat_id][user_id]
    user_msgs.append(now)
    # Remove old messages outside window
    _flood[chat_id][user_id] = [t for t in user_msgs if now - t <= FLOOD_WINDOW]

    if len(_flood[chat_id][user_id]) >= FLOOD_LIMIT:
        if user_id not in _warned[chat_id]:
            _warned[chat_id].add(user_id)
            try:
                await client.restrict_chat_member(
                    chat_id, user_id,
                    permissions=__import__('pyrogram').types.ChatPermissions(can_send_messages=False),
                )
                warn = await message.reply(
                    f"⚠️ {message.from_user.mention} has been muted for spamming!"
                )
                await asyncio.sleep(30)
                await client.restrict_chat_member(
                    chat_id, user_id,
                    permissions=__import__('pyrogram').types.ChatPermissions(can_send_messages=True),
                )
                await warn.edit(f"✅ {message.from_user.mention} has been unmuted.")
                _warned[chat_id].discard(user_id)
                _flood[chat_id][user_id] = []
            except Exception as e:
                log.debug("antispam restrict error: %s", e)
