"""
decorators.py — Permission checks for commands + shared peer helpers
"""

import re
from functools import wraps
from pyrogram import Client
from pyrogram.types import Message
from config import OWNER_ID, SUDO_USERS

# Telegram username rules (as of 2024):
#   • 5–32 characters
#   • Must start with a letter (a-z / A-Z)
#   • Only letters, digits, and underscores allowed
# We validate BEFORE calling the API so we never hit contacts.ResolveUsername
# with a format-invalid string (e.g. "john.doe", "@", "hi", "123abc") —
# Telegram returns [400 USERNAME_INVALID] for all of these.
_USERNAME_RE = re.compile(r"^[a-zA-Z][a-zA-Z0-9_]{4,31}$")


def clean_peer(raw: str) -> "int | str":
    """Normalise a user-supplied username/ID before passing to get_users().

    Three classes of input are handled:

    1. Digit-only (with optional leading '-' for negative chat IDs):
       → converted to int so Pyrogram uses users.GetUsers, NOT
         contacts.ResolveUsername.  Passing "123456" as a string causes
         contacts.ResolveUsername → [400 USERNAME_INVALID] every time.

    2. Format-invalid string (empty, starts with digit, contains dots/
       hyphens/spaces, shorter than 5 chars, etc.):
       → raises ValueError immediately, no API call is made.
         The callers wrap this in except Exception so the user gets a
         clean "user not found" reply instead of an API error in the logs.

    3. Valid username string ("validuser_123"):
       → returned as-is; Pyrogram calls contacts.ResolveUsername.
    """
    value = raw.strip().lstrip("@").strip()
    if not value:
        raise ValueError("Username ya user ID dena zaroori hai!")
    # Negative IDs (supergroups / channels start with -100…)
    if value.lstrip("-").isdigit():
        return int(value)
    # Validate format BEFORE making the API call.
    # Invalid formats → USERNAME_INVALID from Telegram; catch client-side.
    if not _USERNAME_RE.match(value):
        raise ValueError(
            f"Invalid username `@{value}` — sirf letters, digits aur "
            f"underscore allowed hain, aur kam se kam 5 characters chahiye."
        )
    return value


def owner_only(func):
    """Only bot owner can use this command."""
    @wraps(func)
    async def wrapper(client: Client, message: Message, *args, **kwargs):
        if message.from_user and message.from_user.id == OWNER_ID:
            return await func(client, message, *args, **kwargs)
        await message.reply("❌ **Sirf bot owner use kar sakta hai.**")
    return wrapper


def sudo_only(func):
    """Sudo users + owner can use this command."""
    @wraps(func)
    async def wrapper(client: Client, message: Message, *args, **kwargs):
        if message.from_user and message.from_user.id in SUDO_USERS:
            return await func(client, message, *args, **kwargs)
        await message.reply("❌ **Yeh command authorized users ke liye hai.**")
    return wrapper


def admin_only(func):
    """Group admins + owner can use this command."""
    @wraps(func)
    async def wrapper(client: Client, message: Message, *args, **kwargs):
        if not message.from_user:
            return
        user_id = message.from_user.id
        # Owner / sudo always allowed
        if user_id in SUDO_USERS:
            return await func(client, message, *args, **kwargs)
        # BUG FIX: Private chats don't have chat members — get_chat_member() always
        # raises an exception in DMs, which was silently caught and fell through to
        # the "Sirf group admins" reply, blocking every DM use of admin commands.
        # Now we detect private chats explicitly and give a helpful error instead.
        try:
            from pyrogram.enums import ChatType
            if message.chat.type == ChatType.PRIVATE:
                await message.reply("❌ **Yeh command sirf group mein use hota hai.**")
                return
        except Exception:
            pass
        try:
            member = await client.get_chat_member(message.chat.id, user_id)
            from pyrogram.enums import ChatMemberStatus
            if member.status in (ChatMemberStatus.ADMINISTRATOR, ChatMemberStatus.OWNER):
                return await func(client, message, *args, **kwargs)
        except Exception as _e:
            import logging as _log
            _log.getLogger("ApexBot.decorators").warning(
                "admin_only check failed | user=%s | chat=%s | err=%s",
                user_id, getattr(message.chat, "id", "?"), _e
            )
        await message.reply("❌ **Sirf group admins use kar sakte hain.**")
    return wrapper
