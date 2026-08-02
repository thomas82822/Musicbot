"""
clients.py — 4ST Music Bot v7.0
✅ in_memory=True for bot: no .session file on ephemeral FS
✅ SESSION_STRING optional — users can login via bot DM (/start → Login)
✅ If SESSION_STRING missing, assistant = None (voice-chat disabled)
   CRITICAL FIX: Do NOT create a second Client with the same BOT_TOKEN.
   Two clients sharing one token compete for Telegram long-polling →
   updates get stolen by the token-less assistant dispatcher → all
   commands except timing-lucky ones (like /ping) fail silently.
"""

import os
import sys
import logging
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

from pyrogram import Client
from pytgcalls import PyTgCalls
from config import API_ID, API_HASH, BOT_TOKEN, SESSION_STRING

log = logging.getLogger("ApexBot.clients")

# ── Bot client (BOT_TOKEN) ─────────────────────────────────────────
bot = Client(
    "ApexBot",
    api_id=API_ID,
    api_hash=API_HASH,
    bot_token=BOT_TOKEN,
    in_memory=True,
    plugins=dict(root="plugins"),
)

# ── Assistant/Userbot client (SESSION_STRING — optional) ───────────
# CRITICAL: When SESSION_STRING is not set, assistant MUST be None.
# Creating a second Client with bot_token=BOT_TOKEN causes two clients
# to compete for Telegram's getUpdates long-polling. The assistant client
# (which has NO plugin handlers) steals updates → bot gets nothing →
# silent failure for ALL commands.
if SESSION_STRING:
    assistant = Client(
        "ApexAssistant",
        api_id=API_ID,
        api_hash=API_HASH,
        session_string=SESSION_STRING,
        in_memory=True,
    )
    log.info("✅ Assistant (userbot) client created from SESSION_STRING")
    call_py = PyTgCalls(assistant)
else:
    log.warning(
        "⚠️ SESSION_STRING not set — assistant is DISABLED. "
        "Voice chat streaming unavailable. Use /start in DM to login."
    )
    assistant = None   # ← CRITICAL: never use BOT_TOKEN here
    call_py = None     # ← PyTgCalls also disabled; guarded in main.py + plugins

# Load the shared Telegram presentation layer before plugins start handling
# updates. All replies, callback edits and media captions now use the same
# premium Apex quote-card language without changing plugin behaviour.
from helpers.premium_ui import install_premium_ui

install_premium_ui()
