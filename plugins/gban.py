"""
gban.py — Global ban system
Commands: /gban, /ungban, /gbans
"""
import logging
from pyrogram import Client, filters
from pyrogram.types import Message
from clients import bot
from helpers.decorators import owner_only
from database import gban_user, ungban_user, is_gbanned, get_all_gbans

log = logging.getLogger("ApexBot.gban")


@bot.on_message(filters.command("gban") & filters.private)
@owner_only
async def gban(client: Client, message: Message):
    target = None
    reason = "No reason provided"

    if message.reply_to_message and message.reply_to_message.from_user:
        target = message.reply_to_message.from_user
        reason = " ".join(message.command[1:]) if len(message.command) > 1 else reason
    elif len(message.command) > 1:
        try:
            uid = int(message.command[1])
            target = await client.get_users(uid)
        except Exception:
            return await message.reply("❌ Invalid user ID.")
        reason = " ".join(message.command[2:]) if len(message.command) > 2 else reason

    if not target:
        return await message.reply("❌ Specify a user to gban.")

    await gban_user(target.id, reason, message.from_user.id)
    await message.reply(
        f"🔨 **Globally Banned**\n\n"
        f"👤 User: {target.mention}\n"
        f"🆔 ID: `{target.id}`\n"
        f"📝 Reason: {reason}"
    )


@bot.on_message(filters.command("ungban") & filters.private)
@owner_only
async def ungban(client: Client, message: Message):
    target = None
    if message.reply_to_message and message.reply_to_message.from_user:
        target = message.reply_to_message.from_user
    elif len(message.command) > 1:
        try:
            uid = int(message.command[1])
            target = await client.get_users(uid)
        except Exception:
            return await message.reply("❌ Invalid user ID.")

    if not target:
        return await message.reply("❌ Specify a user to ungban.")

    ok = await ungban_user(target.id)
    if ok:
        await message.reply(f"✅ **{target.mention}** has been removed from global ban list.")
    else:
        await message.reply(f"❌ **{target.mention}** is not globally banned.")


@bot.on_message(filters.command("gbans"))
@owner_only
async def list_gbans(_, message: Message):
    gbans = await get_all_gbans()
    if not gbans:
        return await message.reply("✅ No globally banned users.")
    lines = [f"🔨 **Globally Banned Users ({len(gbans)}):**\n"]
    for uid, reason, by in gbans[:20]:
        lines.append(f"• `{uid}` — {reason}")
    if len(gbans) > 20:
        lines.append(f"\n...and {len(gbans)-20} more")
    await message.reply("\n".join(lines))


# Auto-check incoming messages for gbanned users
@bot.on_message(filters.group & ~filters.bot, group=-1)
async def gban_check(client: Client, message: Message):
    if message.from_user and await is_gbanned(message.from_user.id):
        try:
            await client.ban_chat_member(message.chat.id, message.from_user.id)
            await message.reply(
                f"🔨 Globally banned user **{message.from_user.mention}** has been banned from this group."
            )
        except Exception:
            pass
