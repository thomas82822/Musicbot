"""
queue_manage.py — Queue management commands
Commands: /queue, /remove [pos], /move [from] [to], /clearqueue, /queueloop
Proper Telegram HTML formatting: <blockquote>, <b>, <code>
"""
import logging
import html as _html
from pyrogram import Client, filters, enums
from pyrogram.types import Message
from clients import bot
from helpers.queue import get_queue, get_current, remove_from_queue, move_in_queue, clear_queue

log = logging.getLogger("ApexBot.queue_manage")

SONGS_PER_PAGE = 10


def _fmt_time(secs: int) -> str:
    if not secs or secs <= 0:
        return "?:??"
    m, s = divmod(int(secs), 60)
    h, m = divmod(m, 60)
    return f"{h}:{m:02d}:{s:02d}" if h else f"{m}:{s:02d}"


def _esc(text: str) -> str:
    return _html.escape(str(text or ""))


@bot.on_message(filters.command(["queue", "q"]) & filters.group)
async def show_queue(_, message: Message):
    chat_id = message.chat.id
    current = get_current(chat_id)
    queue   = get_queue(chat_id)

    if not current and not queue:
        return await message.reply(
            "<blockquote>📋 <b>Queue is Empty</b></blockquote>\n\n"
            "Use /play <code>[song name]</code> to add songs!",
            parse_mode=enums.ParseMode.HTML,
        )

    lines = []

    if current:
        dur = _fmt_time(current.duration)
        lines.append(
            f"<blockquote>🎵 <b>Now Playing</b></blockquote>\n"
            f"└ {_esc(current.title)} <code>[{dur}]</code>\n"
            f"└ 👤 {_esc(current.requested_by or 'Unknown')}\n"
        )

    if queue:
        lines.append(f"<blockquote>📋 <b>Up Next — {len(queue)} Songs</b></blockquote>")
        page_songs = queue[:SONGS_PER_PAGE]
        for i, song in enumerate(page_songs, 1):
            dur = _fmt_time(song.duration)
            lines.append(
                f"<b>{i}.</b> {_esc(song.title)} "
                f"<code>[{dur}]</code> — <i>{_esc(song.requested_by or 'Unknown')}</i>"
            )
        if len(queue) > SONGS_PER_PAGE:
            remaining = len(queue) - SONGS_PER_PAGE
            lines.append(f"\n<i>...and {remaining} more songs in queue</i>")

    await message.reply(
        "\n".join(lines),
        parse_mode=enums.ParseMode.HTML,
        disable_web_page_preview=True,
    )


@bot.on_message(filters.command("remove") & filters.group)
async def remove_song(_, message: Message):
    if len(message.command) < 2:
        return await message.reply(
            "<blockquote>📖 <b>Usage</b></blockquote>\n"
            "/remove <code>[position]</code>\n"
            "<i>Example: /remove 2</i>",
            parse_mode=enums.ParseMode.HTML,
        )
    try:
        pos = int(message.command[1])
    except ValueError:
        return await message.reply(
            "❌ Position must be a number.",
            parse_mode=enums.ParseMode.HTML,
        )

    chat_id = message.chat.id
    queue = get_queue(chat_id)
    if pos < 1 or pos > len(queue):
        return await message.reply(
            f"❌ Invalid position. Queue has <b>{len(queue)}</b> songs.",
            parse_mode=enums.ParseMode.HTML,
        )

    try:
        removed = remove_from_queue(chat_id, pos)
        if removed:
            await message.reply(
                f"<blockquote>🗑 <b>Removed from Queue</b></blockquote>\n\n"
                f"<b>#{pos}:</b> {_esc(removed.title)}",
                parse_mode=enums.ParseMode.HTML,
            )
        else:
            await message.reply("❌ Could not remove that song.")
    except Exception as e:
        await message.reply(f"❌ Error: <code>{_esc(str(e))}</code>",
                            parse_mode=enums.ParseMode.HTML)


@bot.on_message(filters.command("move") & filters.group)
async def move_song(_, message: Message):
    if len(message.command) < 3:
        return await message.reply(
            "<blockquote>📖 <b>Usage</b></blockquote>\n"
            "/move <code>[from] [to]</code>\n"
            "<i>Example: /move 3 1</i>",
            parse_mode=enums.ParseMode.HTML,
        )
    try:
        from_pos = int(message.command[1])
        to_pos   = int(message.command[2])
    except ValueError:
        return await message.reply("❌ Positions must be numbers.")

    chat_id = message.chat.id
    queue = get_queue(chat_id)
    if not (1 <= from_pos <= len(queue)) or not (1 <= to_pos <= len(queue)):
        return await message.reply(
            f"❌ Invalid positions. Queue has <b>{len(queue)}</b> songs.",
            parse_mode=enums.ParseMode.HTML,
        )

    try:
        ok = move_in_queue(chat_id, from_pos, to_pos)
        if ok:
            await message.reply(
                f"<blockquote>✅ <b>Song Moved</b></blockquote>\n\n"
                f"<b>#{from_pos}</b> → <b>#{to_pos}</b>",
                parse_mode=enums.ParseMode.HTML,
            )
        else:
            await message.reply("❌ Move failed. Check positions.")
    except Exception as e:
        await message.reply(f"❌ Error: <code>{_esc(str(e))}</code>",
                            parse_mode=enums.ParseMode.HTML)


@bot.on_message(filters.command(["clearqueue", "cq"]) & filters.group)
async def clear_queue_cmd(_, message: Message):
    chat_id = message.chat.id
    count = len(get_queue(chat_id))
    if count == 0:
        return await message.reply(
            "<blockquote>📋 Queue is already empty</blockquote>",
            parse_mode=enums.ParseMode.HTML,
        )
    clear_queue(chat_id)
    await message.reply(
        f"<blockquote>🗑 <b>Queue Cleared</b></blockquote>\n\n"
        f"Removed <b>{count}</b> songs from the queue.",
        parse_mode=enums.ParseMode.HTML,
    )


@bot.on_message(filters.command("queueloop") & filters.group)
async def queue_loop(_, message: Message):
    await message.reply(
        "<blockquote>🔁 <b>Queue Loop</b></blockquote>\n\n"
        "Use /loop to loop the current song.",
        parse_mode=enums.ParseMode.HTML,
    )
