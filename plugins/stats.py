"""
stats.py — Bot statistics
Commands: /stats, /top, /mysongs, /botinfo, /uptime
"""
import time
import logging
import psutil
from pyrogram import Client, filters
from pyrogram.types import Message
from clients import bot

log = logging.getLogger("ApexBot.stats")
_START_TIME = time.time()


def _uptime_str() -> str:
    s = int(time.time() - _START_TIME)
    h, rem = divmod(s, 3600)
    m, s2 = divmod(rem, 60)
    return f"{h}h {m}m {s2}s"


@bot.on_message(filters.command("stats"))
async def stats_cmd(_, message: Message):
    from database import get_total_plays, get_total_users, get_total_chats
    try:
        plays = await get_total_plays()
        users = await get_total_users()
        chats = await get_total_chats()
    except Exception:
        plays = users = chats = "N/A"

    mem = psutil.virtual_memory()
    cpu = psutil.cpu_percent(interval=0.5)
    ram_used = round(mem.used / 1024**2)
    ram_total = round(mem.total / 1024**2)

    text = (
        "📊 **Bot Statistics**\n"
        "━━━━━━━━━━━━━━━━━\n"
        f"🎵 **Total Plays:** `{plays}`\n"
        f"👥 **Total Users:** `{users}`\n"
        f"💬 **Total Groups:** `{chats}`\n"
        f"⏱ **Uptime:** `{_uptime_str()}`\n"
        "━━━━━━━━━━━━━━━━━\n"
        f"💾 **RAM:** `{ram_used}MB / {ram_total}MB`\n"
        f"⚙️ **CPU:** `{cpu}%`\n"
    )
    await message.reply(text)


@bot.on_message(filters.command("top"))
async def top_songs(_, message: Message):
    from database import get_top_songs
    try:
        songs = await get_top_songs(10)
    except Exception:
        songs = []
    if not songs:
        return await message.reply("📈 No song history yet!")

    lines = ["🏆 **Top 10 Most Played Songs Today:**\n"]
    for i, (title, count) in enumerate(songs, 1):
        lines.append(f"{i}. {title} — `{count} plays`")
    await message.reply("\n".join(lines))


@bot.on_message(filters.command("mysongs"))
async def my_songs(_, message: Message):
    user_id = message.from_user.id if message.from_user else None
    if not user_id:
        return await message.reply("❌ Cannot identify you.")
    from database import get_user_history
    try:
        history = await get_user_history(user_id, limit=20)
    except Exception:
        history = []
    if not history:
        return await message.reply("🎵 You haven't played any songs yet!")

    lines = [f"🎵 **Your Last {len(history)} Songs:**\n"]
    for i, title in enumerate(history, 1):
        lines.append(f"{i}. {title}")
    await message.reply("\n".join(lines))


@bot.on_message(filters.command("botinfo"))
async def botinfo(_, message: Message):
    import sys
    me = await bot.get_me()
    mem = psutil.virtual_memory()
    cpu = psutil.cpu_percent(interval=0.5)

    text = (
        "🤖 **Bot Information**\n"
        "━━━━━━━━━━━━━━━━━\n"
        f"🔖 **Name:** {me.first_name}\n"
        f"📛 **Username:** @{me.username}\n"
        f"🆔 **ID:** `{me.id}`\n"
        "━━━━━━━━━━━━━━━━━\n"
        f"🐍 **Python:** `{sys.version.split()[0]}`\n"
        f"💾 **RAM:** `{round(mem.used/1024**2)}MB / {round(mem.total/1024**2)}MB`\n"
        f"⚙️ **CPU:** `{cpu}%`\n"
        f"⏱ **Uptime:** `{_uptime_str()}`\n"
    )
    await message.reply(text)


@bot.on_message(filters.command("uptime"))
async def uptime_cmd(_, message: Message):
    await message.reply(f"⏱ **Bot Uptime:** `{_uptime_str()}`")
