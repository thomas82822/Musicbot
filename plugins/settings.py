"""
settings.py — Per-group settings panel
Commands: /settings, /setprefix, /adminonly, /autoleave, /autoclear, /maxqueue
"""
import logging
from pyrogram import Client, filters
from pyrogram.types import Message, InlineKeyboardMarkup, InlineKeyboardButton, CallbackQuery
from clients import bot
from helpers.decorators import admin_only
from helpers.settings_cache import get_setting, set_setting

log = logging.getLogger("ApexBot.settings")


def _settings_keyboard(chat_id: int) -> InlineKeyboardMarkup:
    admin_only = get_setting(chat_id, "admin_only", False)
    autoleave = get_setting(chat_id, "autoleave", True)
    autoclear = get_setting(chat_id, "autoclear", True)

    def tog(val): return "✅" if val else "❌"

    return InlineKeyboardMarkup([
        [InlineKeyboardButton(f"{tog(admin_only)} Admin Only Play", callback_data=f"setting_adminonly_{chat_id}")],
        [InlineKeyboardButton(f"{tog(autoleave)} Auto Leave VC", callback_data=f"setting_autoleave_{chat_id}")],
        [InlineKeyboardButton(f"{tog(autoclear)} Auto Clear NP Msgs", callback_data=f"setting_autoclear_{chat_id}")],
        [InlineKeyboardButton("❌ Close", callback_data=f"setting_close")],
    ])


@bot.on_message(filters.command("settings") & filters.group)
@admin_only
async def settings_cmd(_, message: Message):
    chat_id = message.chat.id
    text = (
        "⚙️ **Group Settings**\n"
        "━━━━━━━━━━━━━━━━━\n"
        "Toggle options below:"
    )
    await message.reply(text, reply_markup=_settings_keyboard(chat_id))


@bot.on_callback_query(filters.regex(r"^setting_"))
async def settings_callback(_, cb: CallbackQuery):
    data = cb.data
    if data == "setting_close":
        await cb.message.delete()
        return

    parts = data.split("_")
    action = parts[1]
    chat_id = int(parts[2])

    # Check if user is admin
    member = await bot.get_chat_member(chat_id, cb.from_user.id)
    from pyrogram.enums import ChatMemberStatus
    if member.status not in (ChatMemberStatus.ADMINISTRATOR, ChatMemberStatus.OWNER):
        await cb.answer("❌ Admins only!", show_alert=True)
        return

    key_map = {"adminonly": "admin_only", "autoleave": "autoleave", "autoclear": "autoclear"}
    key = key_map.get(action)
    if key:
        current = get_setting(chat_id, key, False)
        await set_setting(chat_id, key, not current)
        await cb.message.edit_reply_markup(_settings_keyboard(chat_id))
        await cb.answer(f"{'✅ Enabled' if not current else '❌ Disabled'} {action}")


@bot.on_message(filters.command("adminonly") & filters.group)
@admin_only
async def adminonly_cmd(_, message: Message):
    chat_id = message.chat.id
    current = get_setting(chat_id, "admin_only", False)
    await set_setting(chat_id, "admin_only", not current)
    status = "✅ Enabled" if not current else "❌ Disabled"
    await message.reply(f"{status} **Admin Only** mode.\n"
                        f"{'Only admins can use /play now.' if not current else 'Everyone can use /play.'}")


@bot.on_message(filters.command("autoleave") & filters.group)
@admin_only
async def autoleave_cmd(_, message: Message):
    chat_id = message.chat.id
    current = get_setting(chat_id, "autoleave", True)
    await set_setting(chat_id, "autoleave", not current)
    status = "✅ Enabled" if not current else "❌ Disabled"
    await message.reply(f"{status} **Auto Leave** when VC is empty.")


@bot.on_message(filters.command("autoclear") & filters.group)
@admin_only
async def autoclear_cmd(_, message: Message):
    chat_id = message.chat.id
    current = get_setting(chat_id, "autoclear", True)
    await set_setting(chat_id, "autoclear", not current)
    status = "✅ Enabled" if not current else "❌ Disabled"
    await message.reply(f"{status} **Auto Clear** NP messages.")


@bot.on_message(filters.command("maxqueue") & filters.group)
@admin_only
async def maxqueue_cmd(_, message: Message):
    if len(message.command) < 2:
        current = get_setting(message.chat.id, "max_queue", 50)
        return await message.reply(f"📋 Current max queue: **{current}**\nUsage: `/maxqueue [5-200]`")
    try:
        val = int(message.command[1])
        if not 5 <= val <= 200:
            return await message.reply("❌ Must be between 5 and 200.")
        await set_setting(message.chat.id, "max_queue", val)
        await message.reply(f"✅ Max queue set to **{val}** songs.")
    except ValueError:
        await message.reply("❌ Must be a number.")
