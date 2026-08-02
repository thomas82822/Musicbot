"""
chatbot.py — AI Chat feature using Gemini
Commands: /chatbot on/off, /ask [question]
"""
import logging
import aiohttp
from pyrogram import Client, filters
from pyrogram.types import Message
from clients import bot
from config import GEMINI_API_KEY
from helpers.decorators import admin_only
from database import get_chatbot_enabled, set_chatbot

log = logging.getLogger("ApexBot.chatbot")

GEMINI_URL = "https://generativelanguage.googleapis.com/v1beta/models/gemini-pro:generateContent"


async def _ask_gemini(prompt: str) -> str:
    if not GEMINI_API_KEY:
        return "❌ Gemini API key not configured. Add GEMINI_API_KEY to secrets."
    try:
        async with aiohttp.ClientSession() as session:
            payload = {"contents": [{"parts": [{"text": prompt}]}]}
            async with session.post(
                f"{GEMINI_URL}?key={GEMINI_API_KEY}",
                json=payload,
                timeout=aiohttp.ClientTimeout(total=20),
            ) as resp:
                data = await resp.json()
                candidates = data.get("candidates", [])
                if candidates:
                    return candidates[0]["content"]["parts"][0]["text"]
                return "❌ No response from AI."
    except Exception as e:
        log.error("Gemini error: %s", e)
        return f"❌ AI error: {e}"


@bot.on_message(filters.command("chatbot") & filters.group)
@admin_only
async def chatbot_toggle(_, message: Message):
    chat_id = message.chat.id
    arg = message.command[1].lower() if len(message.command) > 1 else None
    if arg == "on":
        await set_chatbot(chat_id, True)
        await message.reply("🤖 **AI Chatbot** enabled! I'll respond to mentions and /ask.")
    elif arg == "off":
        await set_chatbot(chat_id, False)
        await message.reply("💤 **AI Chatbot** disabled.")
    else:
        enabled = await get_chatbot_enabled(chat_id)
        status = "✅ ON" if enabled else "❌ OFF"
        await message.reply(f"🤖 Chatbot Status: {status}\nUsage: `/chatbot on/off`")


@bot.on_message(filters.command("ask"))
async def ask_cmd(_, message: Message):
    if len(message.command) < 2:
        return await message.reply("**Usage:** `/ask [question]`")
    prompt = " ".join(message.command[1:])
    msg = await message.reply("🤔 Thinking...")
    answer = await _ask_gemini(prompt)
    await msg.edit(f"🤖 **AI Response:**\n\n{answer[:4000]}")


@bot.on_message(filters.group & filters.mentioned & ~filters.bot, group=5)
async def on_mention(client: Client, message: Message):
    if not message.text:
        return
    chat_id = message.chat.id
    if not await get_chatbot_enabled(chat_id):
        return
    me = await client.get_me()
    text = message.text.replace(f"@{me.username}", "").strip()
    if not text:
        return
    msg = await message.reply("🤔 Thinking...")
    answer = await _ask_gemini(text)
    await msg.edit(f"🤖 {answer[:4000]}")
