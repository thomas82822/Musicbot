"""
admin.py — v5.0 Ultimate
ban, unban, kick, mute, unmute, warn, clearwarn, promote, fpromote, demote,
pin, unpin, purge, admins, adminlist, report, staff, banall, unbanall
Auto-demote safety: 3+ bans in 10s → demote
"""

import asyncio
import time
from pyrogram import Client, filters
from clients import bot
from pyrogram.types import Message, ChatPermissions, InlineKeyboardMarkup, InlineKeyboardButton
from pyrogram.enums import ChatMemberStatus, ChatMembersFilter
from pyrogram.errors import UserAdminInvalid, PeerIdInvalid, ChatAdminRequired

from helpers.decorators import admin_only, owner_only, clean_peer
from helpers.logger_channel import log_admin_action
from database import warn_user, get_warns, clear_warns, track_admin_ban, reset_admin_ban_tracker


def _target(message: Message):
    if message.reply_to_message and message.reply_to_message.from_user:
        return message.reply_to_message.from_user
    if len(message.command) > 1:
        return message.command[1]
    return None


async def _resolve(client: Client, chat_id: int, target) -> tuple:
    if target is None:
        return None, "❌ Reply karein ya username/ID dein."
    try:
        if hasattr(target, "id"):
            return target, None
        # BUG FIX: raw string args (e.g. "@", digit-only IDs) caused
        # contacts.ResolveUsername → USERNAME_INVALID.  clean_peer() strips
        # "@", converts numeric strings to int (→ users.GetUsers, not
        # contacts.ResolveUsername), and rejects empty input immediately.
        user = await client.get_users(clean_peer(target))
        return user, None
    except Exception as e:
        return None, f"❌ User nahi mila: `{e}`"


def _reason(message: Message, offset: int = 2) -> str:
    parts = message.command[offset:]
    return " ".join(parts) if parts else "No reason given"


# ── /ban ──────────────────────────────────────────────────────────

@Client.on_message(filters.command(["ban"]) & filters.group)
@admin_only
async def ban_cmd(client: Client, message: Message):
    if client is not bot:
        return
    target = _target(message)
    user, err = await _resolve(client, message.chat.id, target)
    if err:
        return await message.reply(err)

    offset = 1 if hasattr(target, "id") else 2
    reason = _reason(message, offset)

    try:
        await client.ban_chat_member(message.chat.id, user.id)

        # Auto-demote safety: track bans by this admin
        if message.from_user:
            count = await track_admin_ban(message.chat.id, message.from_user.id)
            if count >= 3:
                await reset_admin_ban_tracker(message.chat.id, message.from_user.id)
                try:
                    # FIX: Remove can_manage_chat — some pyrofork builds reject
                    # it as an unexpected keyword. Removing all rights via the
                    # remaining False params is equivalent for demotion.
                    from pyrogram.types import ChatPrivileges
                    await client.promote_chat_member(
                        message.chat.id, message.from_user.id,
                        privileges=ChatPrivileges(
                            can_delete_messages=False,
                            can_manage_video_chats=False,
                            can_restrict_members=False,
                            can_invite_users=False,
                            can_pin_messages=False,
                        ),
                    )
                    await message.reply(
                        f"⚠️ **SAFETY ALERT!**\n\n"
                        f"👮 {message.from_user.mention} ne 10 seconds mein 3+ users ban kiye!\n"
                        f"🔽 **Auto-demote kar diya gaya (safety ke liye)**"
                    )
                    return
                except Exception:
                    pass

        await message.reply(
            f"🔨 **User Banned!**\n\n"
            f"> 👤 **User:** {user.mention}\n"
            f"> 🆔 **ID:** `{user.id}`\n"
            f"> 📝 **Reason:** {reason}\n"
            f"> 👮 **By:** {message.from_user.mention if message.from_user else 'Admin'}"
        )
        admin_str = message.from_user.mention if message.from_user else "Admin"
        chat_title = getattr(message.chat, "title", "") or str(message.chat.id)
        log_admin_action(message.chat.id, chat_title, admin_str, "ban",
                         f"{user.mention} (`{user.id}`)", reason)
    except UserAdminInvalid:
        await message.reply("❌ **Is user ko ban nahi kar sakta!**\n> User admin hai ya bot se upar hai.")
    except ChatAdminRequired:
        await message.reply("❌ **Ban ka right nahi hai!**\n> Mujhe **Ban Users** admin permission chahiye.")
    except Exception as e:
        await message.reply(f"❌ **Error:** `{e}`")


# ── /unban ────────────────────────────────────────────────────────

@Client.on_message(filters.command(["unban"]) & filters.group)
@admin_only
async def unban_cmd(client: Client, message: Message):
    if client is not bot:
        return
    target = _target(message)
    user, err = await _resolve(client, message.chat.id, target)
    if err:
        return await message.reply(err)
    try:
        await client.unban_chat_member(message.chat.id, user.id)
        await message.reply(
            f"✅ **UNBANNED!**\n"
            f"━━━━━━━━━━━━━━━━━━━━━━\n\n"
            f"👤 **User:** {user.mention}\n"
            f"🆔 **ID:** `{user.id}`\n"
            f"👮 **By:** {message.from_user.mention if message.from_user else 'Admin'}"
        )
    except Exception as e:
        await message.reply(f"❌ **Error:** `{e}`")


# ── /kick ─────────────────────────────────────────────────────────

@Client.on_message(filters.command(["kick"]) & filters.group)
@admin_only
async def kick_cmd(client: Client, message: Message):
    if client is not bot:
        return
    target = _target(message)
    user, err = await _resolve(client, message.chat.id, target)
    if err:
        return await message.reply(err)

    offset = 1 if hasattr(target, "id") else 2
    reason = _reason(message, offset)

    try:
        await client.ban_chat_member(message.chat.id, user.id)
        await asyncio.sleep(1)
        await client.unban_chat_member(message.chat.id, user.id)
        await message.reply(
            f"👢 **KICKED!**\n"
            f"━━━━━━━━━━━━━━━━━━━━━━\n\n"
            f"👤 **User:** {user.mention}\n"
            f"🆔 **ID:** `{user.id}`\n"
            f"📝 **Reason:** {reason}\n"
            f"👮 **By:** {message.from_user.mention if message.from_user else 'Admin'}"
        )
    except UserAdminInvalid:
        await message.reply("❌ **Admin ko kick nahi kar sakta!**\n> Pehle demote karo.")
    except Exception as e:
        await message.reply(f"❌ **Error:** `{e}`")


# ── /mute ─────────────────────────────────────────────────────────

@Client.on_message(filters.command(["mute"]) & filters.group)
@admin_only
async def mute_cmd(client: Client, message: Message):
    if client is not bot:
        return
    target = _target(message)
    user, err = await _resolve(client, message.chat.id, target)
    if err:
        return await message.reply(err)

    offset = 1 if hasattr(target, "id") else 2
    reason = _reason(message, offset)

    try:
        await client.restrict_chat_member(
            message.chat.id, user.id,
            ChatPermissions(can_send_messages=False)
        )
        await message.reply(
            f"🔇 **MUTED!**\n"
            f"━━━━━━━━━━━━━━━━━━━━━━\n\n"
            f"👤 **User:** {user.mention}\n"
            f"🆔 **ID:** `{user.id}`\n"
            f"📝 **Reason:** {reason}\n"
            f"👮 **By:** {message.from_user.mention if message.from_user else 'Admin'}"
        )
    except UserAdminInvalid:
        await message.reply("❌ **Admin ko mute nahi kar sakta!**\n> Pehle demote karo.")
    except Exception as e:
        await message.reply(f"❌ **Error:** `{e}`")


# ── /unmute ───────────────────────────────────────────────────────

@Client.on_message(filters.command(["unmute"]) & filters.group)
@admin_only
async def unmute_cmd(client: Client, message: Message):
    if client is not bot:
        return
    target = _target(message)
    user, err = await _resolve(client, message.chat.id, target)
    if err:
        return await message.reply(err)
    try:
        await client.restrict_chat_member(
            message.chat.id, user.id,
            ChatPermissions(
                can_send_messages=True,
                can_send_media_messages=True,
                can_send_other_messages=True,
                can_add_web_page_previews=True,
            )
        )
        await message.reply(
            f"🔊 **UNMUTED!**\n"
            f"━━━━━━━━━━━━━━━━━━━━━━\n\n"
            f"👤 **User:** {user.mention}\n"
            f"🆔 **ID:** `{user.id}`\n"
            f"👮 **By:** {message.from_user.mention if message.from_user else 'Admin'}"
        )
    except Exception as e:
        await message.reply(f"❌ **Error:** `{e}`")


# ── /warn ─────────────────────────────────────────────────────────

@Client.on_message(filters.command(["warn"]) & filters.group)
@admin_only
async def warn_cmd(client: Client, message: Message):
    if client is not bot:
        return
    target = _target(message)
    user, err = await _resolve(client, message.chat.id, target)
    if err:
        return await message.reply(err)

    offset = 1 if hasattr(target, "id") else 2
    reason = _reason(message, offset)

    count = await warn_user(user.id, message.chat.id, reason, message.from_user.id)

    warn_progress = "🔴" * count + "⚪" * (3 - count)
    if count >= 3:
        await clear_warns(user.id, message.chat.id)
        try:
            await client.ban_chat_member(message.chat.id, user.id)
        except Exception:
            pass
        await message.reply(
            f"🚨 **3 WARNINGS = BANNED!**\n"
            f"━━━━━━━━━━━━━━━━━━━━━━\n\n"
            f"👤 **User:** {user.mention}\n"
            f"🆔 **ID:** `{user.id}`\n"
            f"📝 **Last Reason:** {reason}\n"
            f"⚡ **Action:** Auto-banned!"
        )
    else:
        await message.reply(
            f"⚠️ **WARNED!** `{count}/3`\n"
            f"━━━━━━━━━━━━━━━━━━━━━━\n\n"
            f"👤 **User:** {user.mention}\n"
            f"🆔 **ID:** `{user.id}`\n"
            f"📝 **Reason:** {reason}\n"
            f"👮 **By:** {message.from_user.mention if message.from_user else 'Admin'}\n\n"
            f"**Warns:** {warn_progress}\n"
            f"> ⚠️ _3rd warn pe automatic ban!_"
        )


# ── /warns ────────────────────────────────────────────────────────

@Client.on_message(filters.command(["warns"]) & filters.group)
async def warns_cmd(client: Client, message: Message):
    if client is not bot:
        return
    target = _target(message)
    user, err = await _resolve(client, message.chat.id, target)
    if err:
        return await message.reply(err)
    warns = await get_warns(user.id, message.chat.id)
    if not warns:
        await message.reply(
            f"✅ **Clean Record!**\n\n"
            f"👤 {user.mention} ka koi warn nahi hai. 🎉"
        )
        return
    warn_progress = "🔴" * len(warns) + "⚪" * (3 - len(warns))
    lines = [
        f"⚠️ **Warn History**\n"
        f"━━━━━━━━━━━━━━━━━━━━━━\n\n"
        f"👤 **User:** {user.mention}\n"
        f"**Warns:** {warn_progress} `{len(warns)}/3`\n\n"
        f"**Reasons:**"
    ]
    for i, w in enumerate(warns, 1):
        lines.append(f"`{i}.` {w}")
    await message.reply("\n".join(lines))


# ── /clearwarn ────────────────────────────────────────────────────

@Client.on_message(filters.command(["clearwarn", "unwarn"]) & filters.group)
@admin_only
async def clearwarn_cmd(client: Client, message: Message):
    if client is not bot:
        return
    target = _target(message)
    user, err = await _resolve(client, message.chat.id, target)
    if err:
        return await message.reply(err)
    await clear_warns(user.id, message.chat.id)
    await message.reply(
        f"✅ **Warns Cleared!**\n\n"
        f"👤 {user.mention} ke saare warns remove kar diye.\n"
        f"**Warns:** ⚪⚪⚪ `0/3`"
    )


# ── /promote (basic, no ban rights) ──────────────────────────────

RANDOM_TITLES = [
    "🌟 Star Member", "💎 Diamond", "⚡ Power User", "🔥 Fire Admin",
    "🎯 Ace", "👑 Elite", "🦁 Alpha", "🚀 Rocket", "💫 Nova", "🌈 Rainbow"
]

@Client.on_message(filters.command(["promote"]) & filters.group)
@admin_only
async def promote_cmd(client: Client, message: Message):
    if client is not bot:
        return
    target = _target(message)
    user, err = await _resolve(client, message.chat.id, target)
    if err:
        return await message.reply(err)

    # Title from command: /promote @user My Title
    parts = message.command[2:] if not hasattr(target, "id") else message.command[1:]
    title = " ".join(parts) if parts else None
    if not title:
        import random
        title = random.choice(RANDOM_TITLES)

    try:
        # BUG FIX: removed can_manage_chat=True — some pyrofork builds raise
        # "unexpected keyword argument 'can_manage_chat'", same as demote fix.
        from pyrogram.types import ChatPrivileges
        await client.promote_chat_member(
            message.chat.id, user.id,
            privileges=ChatPrivileges(
                can_delete_messages=True,
                can_manage_video_chats=True,
                can_restrict_members=False,   # no ban rights
                can_invite_users=True,
                can_pin_messages=True,
                can_promote_members=False,    # no promote rights
            ),
        )
        try:
            await client.set_administrator_title(message.chat.id, user.id, title[:16])
        except Exception:
            pass
        await message.reply(
            f"⭐ **PROMOTED!**\n"
            f"━━━━━━━━━━━━━━━━━━━━━━\n\n"
            f"👤 **User:** {user.mention}\n"
            f"🆔 **ID:** `{user.id}`\n"
            f"🏷️ **Title:** `{title}`\n"
            f"🔒 **Rights:** Limited _(no ban rights)_\n"
            f"👮 **By:** {message.from_user.mention if message.from_user else 'Admin'}"
        )
    except ChatAdminRequired:
        await message.reply("❌ **Promote karne ka right nahi hai!**\n> Mujhe promote rights chahiye.")
    except Exception as e:
        await message.reply(f"❌ **Error:** `{e}`")


# ── /fpromote (full rights) ───────────────────────────────────────

@Client.on_message(filters.command(["fpromote"]) & filters.group)
@admin_only
async def fpromote_cmd(client: Client, message: Message):
    if client is not bot:
        return
    target = _target(message)
    user, err = await _resolve(client, message.chat.id, target)
    if err:
        return await message.reply(err)

    parts = message.command[2:] if not hasattr(target, "id") else message.command[1:]
    title = " ".join(parts) if parts else None
    if not title:
        import random
        title = random.choice(RANDOM_TITLES)

    try:
        # BUG FIX: removed can_manage_chat=True — some pyrofork builds raise
        # "unexpected keyword argument 'can_manage_chat'", crashing /fpromote.
        from pyrogram.types import ChatPrivileges
        await client.promote_chat_member(
            message.chat.id, user.id,
            privileges=ChatPrivileges(
                can_delete_messages=True,
                can_manage_video_chats=True,
                can_restrict_members=True,
                can_invite_users=True,
                can_pin_messages=True,
                can_promote_members=True,
            ),
        )
        try:
            await client.set_administrator_title(message.chat.id, user.id, title[:16])
        except Exception:
            pass
        await message.reply(
            f"👑 **FULL PROMOTED!**\n"
            f"━━━━━━━━━━━━━━━━━━━━━━\n\n"
            f"👤 **User:** {user.mention}\n"
            f"🆔 **ID:** `{user.id}`\n"
            f"🏷️ **Title:** `{title}`\n"
            f"🔓 **Rights:** Full Admin ✅\n"
            f"👮 **By:** {message.from_user.mention if message.from_user else 'Admin'}"
        )
    except ChatAdminRequired:
        await message.reply("❌ **Full promote ka right nahi hai!**")
    except Exception as e:
        await message.reply(f"❌ **Error:** `{e}`")


# ── /demote ───────────────────────────────────────────────────────

@Client.on_message(filters.command(["demote"]) & filters.group)
@admin_only
async def demote_cmd(client: Client, message: Message):
    if client is not bot:
        return
    target = _target(message)
    user, err = await _resolve(client, message.chat.id, target)
    if err:
        return await message.reply(err)
    try:
        # FIX: Remove can_manage_chat — some pyrofork builds raise
        # "unexpected keyword argument 'can_manage_chat'" for this param.
        # Setting all other rights to False is sufficient for full demotion.
        from pyrogram.types import ChatPrivileges
        await client.promote_chat_member(
            message.chat.id, user.id,
            privileges=ChatPrivileges(
                can_delete_messages=False,
                can_manage_video_chats=False,
                can_restrict_members=False,
                can_invite_users=False,
                can_pin_messages=False,
            ),
        )
        await message.reply(
            f"🔽 **DEMOTED!**\n"
            f"━━━━━━━━━━━━━━━━━━━━━━\n\n"
            f"👤 **User:** {user.mention}\n"
            f"🆔 **ID:** `{user.id}`\n"
            f"👮 **By:** {message.from_user.mention if message.from_user else 'Admin'}"
        )
    except ChatAdminRequired:
        await message.reply("❌ **Demote ka right nahi hai!**\n> Mujhe **Add Admins** permission chahiye.")
    except Exception as e:
        await message.reply(f"❌ **Error:** `{e}`")


# ── /pin / /unpin ─────────────────────────────────────────────────

@Client.on_message(filters.command(["pin"]) & filters.group)
@admin_only
async def pin_cmd(client: Client, message: Message):
    if client is not bot:
        return
    if not message.reply_to_message:
        return await message.reply("❌ Kisi message pe reply karein.")
    try:
        await message.reply_to_message.pin()
        await message.reply("📌 **Message Pinned!**\n\n_Sab log dekh sakte hain ab!_")
    except Exception as e:
        await message.reply(f"❌ **Error:** `{e}`")


@Client.on_message(filters.command(["unpin"]) & filters.group)
@admin_only
async def unpin_cmd(client: Client, message: Message):
    if client is not bot:
        return
    try:
        await client.unpin_chat_message(message.chat.id)
        await message.reply("📌 **Message Unpinned!**")
    except Exception as e:
        await message.reply(f"❌ **Error:** `{e}`")


# ── /purge ────────────────────────────────────────────────────────

@Client.on_message(filters.command(["purge"]) & filters.group)
@admin_only
async def purge_cmd(client: Client, message: Message):
    if client is not bot:
        return
    if not message.reply_to_message:
        return await message.reply("❌ Jis message se purge karna hai usse reply karein.")
    from_msg_id = message.reply_to_message.id
    to_msg_id   = message.id
    msg_ids     = list(range(from_msg_id, to_msg_id + 1))
    deleted     = 0
    for i in range(0, len(msg_ids), 100):
        chunk = msg_ids[i:i+100]
        try:
            await client.delete_messages(message.chat.id, chunk)
            deleted += len(chunk)
        except Exception:
            pass
    note = await message.reply(f"🗑 **Purged {deleted} messages!**")
    # BUG FIX: asyncio.sleep in handler blocks event loop — moved to create_task
    async def _del_note():
        await asyncio.sleep(3)
        try:
            await note.delete()
        except Exception:
            pass
    asyncio.create_task(_del_note())


# ── /admins / /adminlist ──────────────────────────────────────────

@Client.on_message(filters.command(["admins", "adminlist", "staff"]) & filters.group)
async def admins_cmd(client: Client, message: Message):
    if client is not bot:
        return
    msg = await message.reply("⏳ Loading admins...")
    admins = []
    async for member in client.get_chat_members(
        message.chat.id, filter=ChatMembersFilter.ADMINISTRATORS
    ):
        if member.status in (ChatMemberStatus.ADMINISTRATOR, ChatMemberStatus.OWNER):
            u = member.user
            name = u.first_name or str(u.id)
            title = member.custom_title or ("👑 Owner" if member.status == ChatMemberStatus.OWNER else "🛡 Admin")
            admins.append(f"• {name} — _{title}_")

    if not admins:
        return await msg.edit("❌ Koi admin nahi mila.")

    text = f"👮 **Admins of {message.chat.title}** ({len(admins)})\n\n" + "\n".join(admins)
    await msg.edit(text)


# ── /report ───────────────────────────────────────────────────────

@Client.on_message(filters.command(["report"]) & filters.group)
async def report_cmd(client: Client, message: Message):
    if client is not bot:
        return
    if not message.reply_to_message:
        return await message.reply("❌ Kisi message ko reply karein report karne ke liye.")

    reason = " ".join(message.command[1:]) or "No reason given"
    reporter = message.from_user
    reported_msg = message.reply_to_message
    reported_user = reported_msg.from_user

    # Notify all admins
    mention_list = []
    async for member in client.get_chat_members(message.chat.id, filter=ChatMembersFilter.ADMINISTRATORS):
        if member.status in (ChatMemberStatus.ADMINISTRATOR, ChatMemberStatus.OWNER):
            if not member.user.is_bot:
                mention_list.append(member.user.mention)

    admin_text = " ".join(mention_list[:5]) if mention_list else "Admins"
    await message.reply(
        f"🚨 **REPORT!**\n\n"
        f"📢 {admin_text}\n\n"
        f"👤 Reported by: {reporter.mention}\n"
        f"🎯 Reported: {reported_user.mention if reported_user else 'Unknown'}\n"
        f"📝 Reason: {reason}\n"
        f"🔗 [Go to message]({reported_msg.link})",
        disable_web_page_preview=True,
    )


# ── /banall (owner only) ──────────────────────────────────────────

@Client.on_message(filters.command(["banall"]) & filters.group)
@owner_only
async def banall_cmd(client: Client, message: Message):
    if client is not bot:
        return
    msg = await message.reply("⏳ Banning all non-admin members...")
    banned = 0
    async for member in client.get_chat_members(message.chat.id):
        if member.status in (ChatMemberStatus.MEMBER, ChatMemberStatus.RESTRICTED):
            try:
                await client.ban_chat_member(message.chat.id, member.user.id)
                banned += 1
                await asyncio.sleep(0.3)
            except Exception:
                pass
    await msg.edit(f"🔨 **BanAll complete!** {banned} users banned.")


# ── /unbanall ─────────────────────────────────────────────────────

@Client.on_message(filters.command(["unbanall"]) & filters.group)
@admin_only
async def unbanall_cmd(client: Client, message: Message):
    if client is not bot:
        return
    msg = await message.reply("⏳ Unbanning all banned users...")
    unbanned = 0
    async for member in client.get_chat_members(
        message.chat.id, filter=ChatMembersFilter.BANNED
    ):
        if member.status == ChatMemberStatus.BANNED:
            try:
                await client.unban_chat_member(message.chat.id, member.user.id)
                unbanned += 1
                await asyncio.sleep(0.3)
            except Exception:
                pass
    await msg.edit(f"✅ **UnbanAll complete!** {unbanned} users unbanned.")
