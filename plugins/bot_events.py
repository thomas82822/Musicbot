"""
bot_events.py — Bot added/removed/admin event handler
━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
Jab bot kisi GC mein add hota hai, us GC ke liye
"My Cute Owner" (adder) ka naam store karta hai.
Ye naam playing card mein per-GC dikhta hai.
"""

import logging
from pyrogram import Client, filters
from pyrogram.types import Message
from clients import bot

log = logging.getLogger("ApexBot.bot_events")


# ── Detect when bot is added to a group (new_chat_members filter) ────────────

@bot.on_message(filters.new_chat_members & filters.group)
async def on_new_chat_member(client: Client, message: Message):
    """Bot ko group mein add karne wale ka naam store karo."""
    try:
        me = await client.get_me()
        # Check agar bot khud is new_chat_members list mein hai
        added_users = message.new_chat_members or []
        bot_was_added = any(u.id == me.id for u in added_users)

        if not bot_was_added:
            return  # Koi aur add hua, bot nahi

        chat    = message.chat
        adder   = message.from_user
        if not adder:
            return

        adder_name = adder.first_name or ""
        if adder.last_name:
            adder_name += f" {adder.last_name}"
        adder_name = adder_name.strip() or f"User {adder.id}"
        adder_username = adder.username or ""

        # Store in DB (with username for NP card display)
        from database import set_chat_adder, register_chat
        await set_chat_adder(chat.id, adder.id, adder_name, adder_username)
        await register_chat(chat.id, chat.title or "", str(chat.type))

        log.info(
            "✅ Bot added to '%s' (%d) by %s (%d)",
            chat.title, chat.id, adder_name, adder.id
        )

        # Log to channel
        try:
            from helpers.logger_channel import log_bot_added
            invite = ""
            try:
                link = await client.export_chat_invite_link(chat.id)
                invite = link or ""
            except Exception:
                pass
            log_bot_added(chat.id, chat.title or "", invite, str(chat.type),
                          adder_id=adder.id,
                          adder_username=getattr(adder, 'username', '') or '',
                          adder_name=adder_name)
        except Exception as _e:
            log.debug("log_bot_added failed: %s", _e)

    except Exception as e:
        log.debug("on_new_chat_member error: %s", e)


# ── Detect when bot is made admin (my_chat_member via ChatMemberUpdated) ──────
# This catches supergroup admin promotion which new_chat_members doesn't always fire for.

try:
    from pyrogram.types import ChatMemberUpdated
    from pyrogram.enums import ChatMemberStatus

    @bot.on_chat_member_updated(filters.group)
    async def on_chat_member_updated(client: Client, update: ChatMemberUpdated):
        """Supergroup member updates — detect when bot is added or promoted."""
        try:
            me = await client.get_me()
            if not update.new_chat_member or update.new_chat_member.user.id != me.id:
                return  # Not about this bot

            new_status = update.new_chat_member.status
            old_status = update.old_chat_member.status if update.old_chat_member else None

            # Bot joined / was added (wasn't member before, now is)
            was_absent = old_status in (
                None,
                ChatMemberStatus.LEFT,
                ChatMemberStatus.BANNED,
                ChatMemberStatus.RESTRICTED,
            )
            is_present = new_status in (
                ChatMemberStatus.MEMBER,
                ChatMemberStatus.ADMINISTRATOR,
            )

            if is_present and was_absent:
                chat   = update.chat
                adder  = update.from_user
                if not adder:
                    return

                adder_name = adder.first_name or ""
                if adder.last_name:
                    adder_name += f" {adder.last_name}"
                adder_name = adder_name.strip() or f"User {adder.id}"
                adder_username = adder.username or ""

                from database import set_chat_adder, register_chat
                await set_chat_adder(chat.id, adder.id, adder_name, adder_username)
                await register_chat(chat.id, chat.title or "", str(chat.type))

                log.info(
                    "✅ (CMU) Bot added to '%s' (%d) by %s (%d)",
                    chat.title, chat.id, adder_name, adder.id
                )

                try:
                    from helpers.logger_channel import log_bot_added
                    log_bot_added(chat.id, chat.title or "", "", str(chat.type),
                                  adder_id=getattr(adder, 'id', 0),
                                  adder_username=getattr(adder, 'username', '') or '',
                                  adder_name=adder_name)
                except Exception:
                    pass

            # Bot was made admin
            if (new_status == ChatMemberStatus.ADMINISTRATOR
                    and old_status != ChatMemberStatus.ADMINISTRATOR):
                chat  = update.chat
                by    = update.from_user
                by_str = f"{by.first_name} (@{by.username})" if by else "Unknown"
                try:
                    from helpers.logger_channel import log_bot_admin
                    invite = ""
                    try:
                        link = await client.export_chat_invite_link(chat.id)
                        invite = link or ""
                    except Exception:
                        pass
                    log_bot_admin(chat.id, chat.title or "", invite, by_str)
                except Exception:
                    pass

        except Exception as e:
            log.debug("on_chat_member_updated error: %s", e)

except ImportError:
    # Older pyrogram/pyrofork without ChatMemberUpdated — skip silently
    log.debug("ChatMemberUpdated not available — skipping supergroup adder detection")
