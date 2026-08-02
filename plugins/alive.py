"""
alive.py — /start, /help, /alive, /ping, /uptime
━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
Beautiful category-based button UI with premium emoji formatting.
"""
import time
import html as _html
from pyrogram import Client, filters, enums
from pyrogram.types import Message, InlineKeyboardMarkup, InlineKeyboardButton, CallbackQuery
from clients import bot
from config import BOT_NAME, BOT_VERSION, SUPPORT_CHAT, SESSION_STRING

_START_TIME = time.time()


def _uptime() -> str:
    secs = int(time.time() - _START_TIME)
    h, rem = divmod(secs, 3600)
    m, s = divmod(rem, 60)
    return f"{h}h {m}m {s}s"


# ── Category command lists ─────────────────────────────────────────
_CATEGORIES: dict[str, tuple[str, str]] = {
    "music": (
        "🎵 Music Commands",
        "<blockquote expandable>"
        "/play <code>[song/URL]</code> — Play in Voice Chat\n"
        "/vplay <code>[song/URL]</code> — Play Video in VC\n"
        "/playlist <code>[URL/name]</code> — Play full playlist\n"
        "/np — Now Playing card\n"
        "/queue — View song queue\n"
        "/skip — Skip current song\n"
        "/pause — Pause playback\n"
        "/resume — Resume playback\n"
        "/stop — Stop &amp; leave VC\n"
        "/loop — Toggle loop mode\n"
        "/shuffle — Shuffle queue\n"
        "/autoplay — Toggle autoplay\n"
        "/seek <code>[mm:ss]</code> — Jump to position\n"
        "/forward <code>[sec]</code> — Skip forward\n"
        "/backward <code>[sec]</code> — Go back\n"
        "/restart — Restart current song\n"
        "/volume <code>[0-200]</code> — Set volume\n"
        "/mute — Mute VC\n"
        "/unmute — Unmute VC"
        "</blockquote>",
    ),
    "effects": (
        "🎛 Audio Effects",
        "<blockquote expandable>"
        "/bass — Bass Boost\n"
        "/treble — Treble Boost\n"
        "/nightcore — Nightcore Effect\n"
        "/slowreverb — Slow &amp; Reverb\n"
        "/speed <code>[0.5–2.0]</code> — Playback speed\n"
        "/reset_audio — Reset to normal"
        "</blockquote>",
    ),
    "queue": (
        "📋 Queue Management",
        "<blockquote expandable>"
        "/queue — Show full queue\n"
        "/remove <code>[pos]</code> — Remove song\n"
        "/move <code>[from] [to]</code> — Move song\n"
        "/clearqueue — Clear entire queue\n"
        "/queueloop — Toggle queue loop\n"
        "/saveplaylist <code>[name]</code> — Save queue as playlist\n"
        "/loadplaylist <code>[name]</code> — Load saved playlist\n"
        "/myplaylists — Your playlists\n"
        "/deleteplaylist <code>[name]</code> — Delete playlist\n"
        "/shareplaylist <code>[name]</code> — Share playlist"
        "</blockquote>",
    ),
    "download": (
        "⬇️ Download &amp; Lyrics",
        "<blockquote expandable>"
        "/download <code>[song/URL]</code> — Download audio\n"
        "/song <code>[name]</code> — Search &amp; send audio\n"
        "/lyrics <code>[song]</code> — Get lyrics\n"
        "/radio <code>[station/URL]</code> — Play radio\n"
        "/radiolist — List radio stations\n"
        "/addradio <code>[name] [URL]</code> — Add radio"
        "</blockquote>",
    ),
    "admin": (
        "⚙️ Admin Commands",
        "<blockquote expandable>"
        "/ban — Ban a user\n"
        "/unban — Unban a user\n"
        "/kick — Kick a user\n"
        "/mute — Mute a member\n"
        "/unmute — Unmute a member\n"
        "/warn — Warn a user\n"
        "/warns — View user warns\n"
        "/clearwarn — Clear warns\n"
        "/promote — Promote to admin\n"
        "/demote — Demote from admin\n"
        "/pin — Pin a message\n"
        "/unpin — Unpin message\n"
        "/purge — Bulk delete messages\n"
        "/adminlist — Show all admins"
        "</blockquote>",
    ),
    "safety": (
        "🛡 Safety &amp; Protection",
        "<blockquote expandable>"
        "/antilink on/off — Block invite links\n"
        "/antispam on/off — Anti-flood protection\n"
        "/antioporn on/off — Block NSFW media\n"
        "/captcha on/off — Join captcha\n"
        "/gban <code>[user]</code> — Global ban\n"
        "/ungban <code>[user]</code> — Remove global ban"
        "</blockquote>",
    ),
    "fun": (
        "🎮 Fun &amp; Games",
        "<blockquote expandable>"
        "/trivia — Music trivia quiz\n"
        "/guess — Guess the number\n"
        "/8ball <code>[question]</code> — Magic 8-ball\n"
        "/roll — Dice roll\n"
        "/flip — Coin flip\n"
        "/joke — Random joke\n"
        "/quote — Music quote\n"
        "/ship — Compatibility meter\n"
        "/crypto — Crypto prices\n"
        "/lyrics <code>[song]</code> — Song lyrics"
        "</blockquote>",
    ),
    "stats": (
        "📊 Stats &amp; Info",
        "<blockquote expandable>"
        "/stats — Bot statistics\n"
        "/botinfo — Detailed bot info\n"
        "/mysongs — Your play history\n"
        "/top — Top played songs\n"
        "/alive — Bot status\n"
        "/ping — Latency check\n"
        "/uptime — Bot uptime\n"
        "/settings — Group settings panel\n"
        "/adminonly — Admin-only play toggle\n"
        "/autoleave — Auto-leave VC toggle\n"
        "/autoclear — Auto-clear NP cards\n"
        "/maxqueue <code>[N]</code> — Set max queue\n"
        "/videomode — Toggle video/audio\n"
        "/setthumb — Set custom NP thumbnail\n"
        "/delthumb — Remove custom thumbnail"
        "</blockquote>",
    ),
}


def _main_keyboard(add_url: str) -> InlineKeyboardMarkup:
    """Main start menu keyboard — clean category grid."""
    return InlineKeyboardMarkup([
        [
            InlineKeyboardButton("🎵 Music",        callback_data="help_music"),
            InlineKeyboardButton("🎛 Effects",      callback_data="help_effects"),
        ],
        [
            InlineKeyboardButton("📋 Queue",        callback_data="help_queue"),
            InlineKeyboardButton("⬇️ Download",    callback_data="help_download"),
        ],
        [
            InlineKeyboardButton("⚙️ Admin",        callback_data="help_admin"),
            InlineKeyboardButton("🛡 Safety",        callback_data="help_safety"),
        ],
        [
            InlineKeyboardButton("🎮 Fun & Games",  callback_data="help_fun"),
            InlineKeyboardButton("📊 Stats & More", callback_data="help_stats"),
        ],
        [
            InlineKeyboardButton("➕ Add Me to Group", url=add_url),
        ],
        [
            InlineKeyboardButton("💬 Support Group", url=SUPPORT_CHAT),
        ],
    ])


def _category_keyboard() -> InlineKeyboardMarkup:
    return InlineKeyboardMarkup([
        [InlineKeyboardButton("🔙 Back to Menu", callback_data="help_back")],
    ])


def _welcome_text(name: str, bot_name: str, vc_note: str) -> str:
    return (
        f"<blockquote>🎵 <b>Welcome, {name}!</b></blockquote>\n\n"
        f"🤖 <b>{bot_name}</b>\n"
        f"🔖 Version: <code>{_html.escape(BOT_VERSION)}</code>\n"
        f"🎙 Voice Chat: {'<b>✅ Active</b>' if SESSION_STRING else '<b>❌ Setup needed</b>'}\n"
        f"{vc_note}\n\n"
        f"<blockquote>👇 <b>Select a category to view commands</b></blockquote>"
    )


# ═══════════════════════════════════════════════════════════════════════════════
#  /start  /help  — PRIVATE DM
# ═══════════════════════════════════════════════════════════════════════════════

@bot.on_message(filters.command(["start", "help"]) & filters.private)
async def start_private(_, message: Message):
    name     = _html.escape(message.from_user.first_name if message.from_user else "Friend")
    bot_name = _html.escape(BOT_NAME)
    vc_note  = "" if SESSION_STRING else "\n⚠️ <i>SESSION_STRING not set — VC disabled</i>"

    bot_me  = await bot.get_me()
    add_url = f"https://t.me/{bot_me.username}?startgroup=start"

    rows = _main_keyboard(add_url).inline_keyboard

    # Add login button if SESSION_STRING not set
    if not SESSION_STRING:
        rows = list(rows)
        rows.append([InlineKeyboardButton("🔑 Login Userbot (Enable VC)", callback_data="login_start")])

    await message.reply(
        _welcome_text(name, bot_name, vc_note),
        parse_mode=enums.ParseMode.HTML,
        reply_markup=InlineKeyboardMarkup(rows),
        disable_web_page_preview=True,
    )


# ═══════════════════════════════════════════════════════════════════════════════
#  Help category callbacks
# ═══════════════════════════════════════════════════════════════════════════════

@bot.on_callback_query(filters.regex(r"^help_(music|effects|queue|download|admin|safety|fun|stats|back)$"))
async def help_category_cb(_, cb: CallbackQuery):
    data = cb.data.split("_", 1)[1]

    if data == "back":
        # Return to main menu
        bot_me  = await bot.get_me()
        add_url = f"https://t.me/{bot_me.username}?startgroup=start"
        name    = _html.escape(cb.from_user.first_name if cb.from_user else "Friend")
        bot_name = _html.escape(BOT_NAME)
        vc_note  = "" if SESSION_STRING else "\n⚠️ <i>SESSION_STRING not set</i>"

        rows = _main_keyboard(add_url).inline_keyboard
        if not SESSION_STRING:
            rows = list(rows)
            rows.append([InlineKeyboardButton("🔑 Login Userbot", callback_data="login_start")])

        await cb.message.edit_text(
            _welcome_text(name, bot_name, vc_note),
            parse_mode=enums.ParseMode.HTML,
            reply_markup=InlineKeyboardMarkup(rows),
            disable_web_page_preview=True,
        )
        return await cb.answer()

    cat = _CATEGORIES.get(data)
    if not cat:
        return await cb.answer("Category not found.", show_alert=True)

    title, cmds = cat
    text = (
        f"<blockquote><b>{title}</b></blockquote>\n\n"
        f"{cmds}"
    )
    await cb.message.edit_text(
        text,
        parse_mode=enums.ParseMode.HTML,
        reply_markup=_category_keyboard(),
        disable_web_page_preview=True,
    )
    await cb.answer()


# ═══════════════════════════════════════════════════════════════════════════════
#  /start  /help  — GROUP
# ═══════════════════════════════════════════════════════════════════════════════

@bot.on_message(filters.command(["start", "help"]) & filters.group)
async def start_group(_, message: Message):
    bot_me   = await bot.get_me()
    bot_name = _html.escape(BOT_NAME)

    await message.reply(
        f"<blockquote>🎵 <b>{bot_name}</b> — Ready to play!</blockquote>\n\n"
        f"🎶 /play <code>[song/URL]</code> — Start music\n"
        f"🎬 /vplay <code>[song/URL]</code> — Video mode\n"
        f"📋 /np — Now Playing\n"
        f"🗂 /queue — View queue\n"
        f"⏭ /skip  ⏸ /pause  ▶️ /resume  ⏹ /stop\n\n"
        f"<blockquote>📖 Send /help in DM for full command list</blockquote>",
        parse_mode=enums.ParseMode.HTML,
        reply_markup=InlineKeyboardMarkup([
            [
                InlineKeyboardButton("📖 Full Commands", url=f"https://t.me/{bot_me.username}?start=help"),
                InlineKeyboardButton("💬 Support", url=SUPPORT_CHAT),
            ],
        ]),
    )


# ═══════════════════════════════════════════════════════════════════════════════
#  /alive
# ═══════════════════════════════════════════════════════════════════════════════

@bot.on_message(filters.command("alive"))
async def alive(_, message: Message):
    t0   = time.time()
    msg  = await message.reply("⏳")
    ping = round((time.time() - t0) * 1000, 2)

    vc_status = "✅ Active" if SESSION_STRING else "❌ No SESSION_STRING"

    await msg.edit(
        f"<blockquote>✅ <b>Bot is Alive!</b></blockquote>\n\n"
        f"🤖 <b>Bot:</b> {_html.escape(BOT_NAME)}\n"
        f"🔖 <b>Version:</b> <code>{_html.escape(BOT_VERSION)}</code>\n"
        f"⏱ <b>Uptime:</b> <code>{_uptime()}</code>\n"
        f"🏓 <b>Ping:</b> <code>{ping} ms</code>\n"
        f"🎙 <b>Voice Chat:</b> {vc_status}",
        parse_mode=enums.ParseMode.HTML,
    )


# ═══════════════════════════════════════════════════════════════════════════════
#  /ping
# ═══════════════════════════════════════════════════════════════════════════════

@bot.on_message(filters.command("ping"))
async def ping(_, message: Message):
    t0  = time.time()
    msg = await message.reply("🏓")
    ms  = round((time.time() - t0) * 1000, 2)
    await msg.edit(
        f"<blockquote>🏓 <b>Pong!</b></blockquote>\n\n"
        f"⚡ Latency: <code>{ms} ms</code>",
        parse_mode=enums.ParseMode.HTML,
    )


# ═══════════════════════════════════════════════════════════════════════════════
#  /uptime
# ═══════════════════════════════════════════════════════════════════════════════

@bot.on_message(filters.command("uptime"))
async def uptime(_, message: Message):
    await message.reply(
        f"<blockquote>⏱ <b>Bot Uptime</b></blockquote>\n\n"
        f"Running since: <code>{_uptime()}</code>",
        parse_mode=enums.ParseMode.HTML,
    )
