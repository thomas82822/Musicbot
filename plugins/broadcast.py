"""
broadcast.py — Broadcast messages to all users/groups
Commands: /broadcast
"""
import asyncio
import logging
from pyrogram import Client, filters
from pyrogram.types import Message
from pyrogram.errors import FloodWait, UserIsBlocked, InputUserDeactivated
from clients import bot
from helpers.decorators import owner_only
from database import get_all_user_ids, get_all_chat_ids

log = logging.getLogger("ApexBot.broadcast")


@bot.on_message(filters.command("broadcast") & filters.private)
@owner_only
async def broadcast(client: Client, message: Message):
    if not message.reply_to_message:
        return await message.reply(
            "**Usage:** Reply to a message with `/broadcast`\n\n"
            "Add flags:\n"
            "`-users` — Send to all users\n"
            "`-groups` — Send to all groups\n"
            "`-all` — Send to both (default)"
        )

    args = " ".join(message.command[1:]).lower()
    to_users = "-users" in args or "-all" in args or not args
    to_groups = "-groups" in args or "-all" in args or not args

    msg = await message.reply("📡 Broadcasting...")
    success, failed = 0, 0

    broadcast_msg = message.reply_to_message

    if to_users:
        try:
            user_ids = await get_all_user_ids()
            for uid in user_ids:
                try:
                    await broadcast_msg.copy(uid)
                    success += 1
                    await asyncio.sleep(0.05)
                except FloodWait as fw:
                    await asyncio.sleep(fw.value)
                except (UserIsBlocked, InputUserDeactivated):
                    failed += 1
                except Exception:
                    failed += 1
        except Exception as e:
            log.error("broadcast users error: %s", e)

    if to_groups:
        try:
            chat_ids = await get_all_chat_ids()
            for cid in chat_ids:
                try:
                    await broadcast_msg.copy(cid)
                    success += 1
                    await asyncio.sleep(0.05)
                except FloodWait as fw:
                    await asyncio.sleep(fw.value)
                except Exception:
                    failed += 1
        except Exception as e:
            log.error("broadcast groups error: %s", e)

    await msg.edit(
        f"📡 **Broadcast Complete!**\n\n"
        f"✅ Success: `{success}`\n"
        f"❌ Failed: `{failed}`"
    )
