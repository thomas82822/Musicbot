"""
captcha.py — Welcome captcha for new members
Commands: /captcha on/off
"""
import asyncio
import random
import logging
from pyrogram import Client, filters
from pyrogram.types import (
    Message, ChatMemberUpdated, InlineKeyboardMarkup, InlineKeyboardButton, CallbackQuery
)
from pyrogram.enums import ChatMemberStatus
from clients import bot
from helpers.decorators import admin_only

log = logging.getLogger("ApexBot.captcha")

_captcha_enabled: dict[int, bool] = {}
_pending_captcha: dict[str, dict] = {}  # key: f"{chat_id}:{user_id}"

CAPTCHA_TIMEOUT = 60  # seconds
EMOJIS = ["🎵", "🎸", "🎹", "🎺", "🎻", "🥁", "🎷", "🎤", "🎧", "🎼"]


@bot.on_message(filters.command("captcha") & filters.group)
@admin_only
async def captcha_toggle(_, message: Message):
    chat_id = message.chat.id
    arg = message.command[1].lower() if len(message.command) > 1 else None
    if arg == "on":
        _captcha_enabled[chat_id] = True
        await message.reply("🔐 **Captcha** enabled! New members must verify.")
    elif arg == "off":
        _captcha_enabled[chat_id] = False
        await message.reply("✅ **Captcha** disabled.")
    else:
        status = "✅ ON" if _captcha_enabled.get(chat_id) else "❌ OFF"
        await message.reply(f"🔐 Captcha Status: {status}\nUsage: `/captcha on/off`")


@bot.on_chat_member_updated()
async def on_new_member(client: Client, member: ChatMemberUpdated):
    if member.old_chat_member and member.old_chat_member.status != ChatMemberStatus.BANNED:
        if not (member.new_chat_member and member.new_chat_member.status == ChatMemberStatus.MEMBER):
            return

    chat_id = member.chat.id
    if not _captcha_enabled.get(chat_id):
        return

    user = member.new_chat_member.user if member.new_chat_member else None
    if not user or user.is_bot:
        return

    # Restrict user until captcha solved
    try:
        from pyrogram.types import ChatPermissions
        await client.restrict_chat_member(
            chat_id, user.id,
            permissions=ChatPermissions(can_send_messages=False),
        )
    except Exception:
        return

    # Generate captcha
    correct_emoji = random.choice(EMOJIS)
    wrong_emojis = random.sample([e for e in EMOJIS if e != correct_emoji], 3)
    all_emojis = [correct_emoji] + wrong_emojis
    random.shuffle(all_emojis)

    key = f"{chat_id}:{user.id}"
    _pending_captcha[key] = {"correct": correct_emoji, "user_id": user.id}

    buttons = [
        [InlineKeyboardButton(e, callback_data=f"captcha_{chat_id}_{user.id}_{e}")]
        for e in all_emojis
    ]

    msg = await client.send_message(
        chat_id,
        f"👋 Welcome {user.mention}!\n\n"
        f"🔐 To verify, click the **{correct_emoji}** emoji within {CAPTCHA_TIMEOUT}s:",
        reply_markup=InlineKeyboardMarkup(buttons),
    )

    # Auto-ban after timeout
    await asyncio.sleep(CAPTCHA_TIMEOUT)
    if key in _pending_captcha:
        del _pending_captcha[key]
        try:
            await client.ban_chat_member(chat_id, user.id)
            await msg.edit(f"⏰ {user.mention} didn't verify in time and was removed.")
        except Exception:
            pass


@bot.on_callback_query(filters.regex(r"^captcha_"))
async def captcha_answer(client: Client, cb: CallbackQuery):
    _, chat_id_str, user_id_str, emoji = cb.data.split("_", 3)
    chat_id = int(chat_id_str)
    user_id = int(user_id_str)

    if cb.from_user.id != user_id:
        await cb.answer("❌ This isn't your captcha!", show_alert=True)
        return

    key = f"{chat_id}:{user_id}"
    pending = _pending_captcha.get(key)
    if not pending:
        await cb.answer("⏰ Captcha expired.", show_alert=True)
        return

    if emoji == pending["correct"]:
        del _pending_captcha[key]
        try:
            from pyrogram.types import ChatPermissions
            await client.restrict_chat_member(
                chat_id, user_id,
                permissions=ChatPermissions(can_send_messages=True),
            )
            await cb.message.edit(f"✅ {cb.from_user.mention} verified successfully! Welcome!")
        except Exception:
            pass
        await cb.answer("✅ Verified!")
    else:
        await cb.answer("❌ Wrong! Try again.", show_alert=True)
