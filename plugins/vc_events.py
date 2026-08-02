"""
vc_events.py — Voice Chat Event Handlers  v2.0
━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
Handles:
  • VC started / ended (service messages)
  • VC participants invited
  • Participant join / left (pytgcalls updates)

All messages use premium animated emojis.
"""
import asyncio
import logging

from pyrogram import Client, filters
from pyrogram.types import Message
from pyrogram import enums

from clients import bot, call_py

log = logging.getLogger("ApexBot.vc_events")

# ── Track active VC participant counts per chat ───────────────────────────────
_vc_participants: dict[int, set] = {}   # chat_id → {user_id, ...}
_vc_active: set[int] = set()            # chat_ids with active VC


# ═══════════════════════════════════════════════════════════════════════════════
#  Pyrogram service-message events  (VC start / end / invite)
# ═══════════════════════════════════════════════════════════════════════════════

@bot.on_message(filters.group & filters.service)
async def on_vc_service_message(client: Client, message: Message):
    """Catch service messages: VC started, VC ended, VC members invited."""
    try:
        chat = message.chat
        chat_id = chat.id

        # ── VC Started ────────────────────────────────────────────────────────
        if message.video_chat_started is not None or getattr(message, 'voice_chat_started', None) is not None:
            _vc_active.add(chat_id)
            _vc_participants[chat_id] = set()
            started_by = ""
            if message.from_user:
                started_by = f" by <b>{message.from_user.first_name}</b>"

            await client.send_message(
                chat_id,
                f"<blockquote>🎙 <b>Voice Chat Started{started_by}!</b></blockquote>\n\n"
                f"🎵 Use /play to stream music in the VC.\n"
                f"🎤 Members can now join the voice chat.",
                parse_mode=enums.ParseMode.HTML,
            )

        # ── VC Ended ──────────────────────────────────────────────────────────
        elif message.video_chat_ended is not None or getattr(message, 'voice_chat_ended', None) is not None:
            _vc_active.discard(chat_id)
            duration_s = 0
            try:
                duration_s = (
                    getattr(message.video_chat_ended, 'duration', 0) or
                    getattr(getattr(message, 'voice_chat_ended', None), 'duration', 0) or 0
                )
            except Exception:
                pass
            dur_str = ""
            if duration_s:
                m, s = divmod(int(duration_s), 60)
                h, m = divmod(m, 60)
                dur_str = f"\n⏱ <b>Duration:</b> {h}h {m}m {s}s" if h else f"\n⏱ <b>Duration:</b> {m}m {s}s"

            await client.send_message(
                chat_id,
                f"<blockquote>🔇 <b>Voice Chat Ended</b></blockquote>{dur_str}\n\n"
                f"👋 Thanks for joining! Use /play anytime to start music.",
                parse_mode=enums.ParseMode.HTML,
            )

        # ── VC Members Invited ────────────────────────────────────────────────
        elif message.video_chat_members_invited is not None or getattr(message, 'voice_chat_members_invited', None) is not None:
            invited_obj = (
                message.video_chat_members_invited or
                getattr(message, 'voice_chat_members_invited', None)
            )
            invited_users = getattr(invited_obj, 'users', []) or []
            if not invited_users:
                return

            by_name = message.from_user.first_name if message.from_user else "Someone"
            names = ", ".join(
                f"<a href='tg://user?id={u.id}'>{u.first_name}</a>"
                for u in invited_users[:5]
            )
            if len(invited_users) > 5:
                names += f" <i>+{len(invited_users) - 5} more</i>"

            await client.send_message(
                chat_id,
                f"<blockquote>📨 <b>VC Invite</b></blockquote>\n\n"
                f"🎙 <b>{by_name}</b> invited {names} to Voice Chat!\n"
                f"🎵 Music is playing — join to listen.",
                parse_mode=enums.ParseMode.HTML,
                disable_web_page_preview=True,
            )

    except Exception as e:
        log.debug("on_vc_service_message error: %s", e)


# ═══════════════════════════════════════════════════════════════════════════════
#  pytgcalls participant join / left events
# ═══════════════════════════════════════════════════════════════════════════════

async def _notify_vc_join(chat_id: int, user_id: int):
    """Send a join notification after a small debounce (avoid spam on bulk joins)."""
    try:
        await asyncio.sleep(1.5)   # debounce — batch joins in 1.5s window
        try:
            member = await bot.get_chat_member(chat_id, user_id)
            user = member.user
            name = user.first_name or "Someone"
            mention = f"<a href='tg://user?id={user.id}'>{name}</a>"
        except Exception:
            mention = f"User <code>{user_id}</code>"

        participants = _vc_participants.get(chat_id, set())
        count = len(participants)

        await bot.send_message(
            chat_id,
            f"<blockquote>🎙 {mention} <b>joined Voice Chat</b></blockquote>\n"
            f"👥 <b>Participants now:</b> {count}",
            parse_mode=enums.ParseMode.HTML,
        )
    except Exception as e:
        log.debug("_notify_vc_join error: %s", e)


async def _notify_vc_left(chat_id: int, user_id: int):
    """Send a leave notification."""
    try:
        try:
            member = await bot.get_chat_member(chat_id, user_id)
            user = member.user
            name = user.first_name or "Someone"
            mention = f"<a href='tg://user?id={user.id}'>{name}</a>"
        except Exception:
            mention = f"User <code>{user_id}</code>"

        participants = _vc_participants.get(chat_id, set())
        count = len(participants)

        await bot.send_message(
            chat_id,
            f"<blockquote>🚪 {mention} <b>left Voice Chat</b></blockquote>\n"
            f"👥 <b>Participants now:</b> {count}",
            parse_mode=enums.ParseMode.HTML,
        )
    except Exception as e:
        log.debug("_notify_vc_left error: %s", e)


# Register pytgcalls participant update handler
if call_py is not None:
    try:
        from pytgcalls import filters as tgcalls_filters

        @call_py.on_update()
        async def _on_participant_change(_, update):
            """Handle participant join/leave from pytgcalls."""
            try:
                chat_id = getattr(update, 'chat_id', None)
                if not chat_id:
                    return

                # pytgcalls v2.x GroupCallParticipant update
                participants = getattr(update, 'participants', None)
                if participants is None:
                    # Try older API style
                    participant = getattr(update, 'participant', None)
                    if participant:
                        participants = [participant]

                if not participants:
                    return

                for p in participants:
                    user_id = getattr(p, 'user_id', None)
                    if not user_id:
                        try:
                            user_id = p.peer.user_id
                        except Exception:
                            continue

                    # Detect join vs leave
                    just_joined  = getattr(p, 'just_joined', False)
                    is_left      = getattr(p, 'left', False) or getattr(p, 'is_left', False)

                    if just_joined:
                        if chat_id not in _vc_participants:
                            _vc_participants[chat_id] = set()
                        _vc_participants[chat_id].add(user_id)
                        asyncio.create_task(_notify_vc_join(chat_id, user_id))

                    elif is_left:
                        if chat_id in _vc_participants:
                            _vc_participants[chat_id].discard(user_id)
                        asyncio.create_task(_notify_vc_left(chat_id, user_id))

            except Exception as e:
                log.debug("_on_participant_change error: %s", e)

        log.info("✅ VC participant event handler registered via pytgcalls")

    except Exception as e:
        log.warning("⚠️ pytgcalls participant event registration failed: %s", e)

else:
    log.warning("⚠️ call_py is None — pytgcalls VC events disabled (no SESSION_STRING)")
