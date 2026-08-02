"""
playlist_url.py — YouTube/Spotify Playlist URL Player  v2.0
━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
Commands:
  /playlist [url]   — Extract & queue all songs from a YouTube/Spotify playlist
  /playlist [name]  — Load a saved user playlist (falls back to saved playlists)

Usage:
  /playlist https://www.youtube.com/playlist?list=PLxxxx
  /playlist https://open.spotify.com/playlist/xxxxx
  /playlist my saved playlist name
"""
import asyncio
import logging

from pyrogram import Client, filters, enums
from pyrogram.types import Message, InlineKeyboardMarkup, InlineKeyboardButton

from clients import bot, call_py
from config import MAX_QUEUE_SIZE
from helpers.queue import Song, add_to_queue, get_current, QueueFullError
from helpers.decorators import admin_only

log = logging.getLogger("ApexBot.playlist_url")

_YTDL_OPTS = {
    "quiet": True,
    "no_warnings": True,
    "extract_flat": "in_playlist",
    "skip_download": True,
    "playlist_items": "1:50",      # limit to first 50 tracks
    "ignoreerrors": True,
}


async def _extract_playlist_entries(url: str) -> list[dict]:
    """Extract playlist entries using yt-dlp (runs in thread pool)."""
    import yt_dlp

    def _blocking():
        with yt_dlp.YoutubeDL(_YTDL_OPTS) as ydl:
            info = ydl.extract_info(url, download=False)
            if not info:
                return []
            entries = info.get("entries") or []
            result = []
            for e in entries:
                if not e:
                    continue
                title = e.get("title") or e.get("id") or "Unknown"
                webpage = (
                    e.get("webpage_url") or
                    e.get("url") or
                    (f"https://www.youtube.com/watch?v={e['id']}" if e.get("id") else "")
                )
                if webpage:
                    result.append({"title": title, "url": webpage})
            return result

    return await asyncio.get_event_loop().run_in_executor(None, _blocking)


# ─────────────────────────────────────────────────────────────────────────────
#  /playlist command
# ─────────────────────────────────────────────────────────────────────────────

@bot.on_message(filters.command("playlist") & filters.group)
async def playlist_cmd(client: Client, message: Message):
    """
    /playlist [YouTube/Spotify playlist URL]  — queue all songs from the URL
    /playlist [saved playlist name]           — load a previously saved playlist
    """
    args = " ".join(message.command[1:]).strip()
    if not args:
        return await message.reply(
            "<blockquote>📋 <b>Playlist Commands</b></blockquote>\n\n"
            "🔗 <b>Play from URL:</b>\n"
            "<code>/playlist https://youtube.com/playlist?list=...</code>\n\n"
            "💾 <b>Load saved playlist:</b>\n"
            "<code>/playlist my playlist name</code>\n\n"
            "📌 <b>Save current queue:</b>\n"
            "<code>/saveplaylist name</code>",
            parse_mode=enums.ParseMode.HTML,
        )

    chat_id   = message.chat.id
    user      = message.from_user
    requester = user.first_name if user else "Unknown"
    user_id   = user.id if user else 0

    # ── Detect if it's a URL ──────────────────────────────────────────────────
    is_url = args.startswith("http://") or args.startswith("https://")

    if is_url:
        msg = await message.reply(
            f"<blockquote>📋 <b>Fetching Playlist…</b></blockquote>\n\n"
            f"🔗 <code>{args[:80]}</code>",
            parse_mode=enums.ParseMode.HTML,
        )
        try:
            entries = await asyncio.wait_for(_extract_playlist_entries(args), timeout=30)
        except asyncio.TimeoutError:
            return await msg.edit("❌ Playlist fetch timed out. Try again.")
        except Exception as e:
            log.error("playlist_url extract error: %s", e)
            return await msg.edit(f"❌ Failed to fetch playlist: <code>{e}</code>",
                                  parse_mode=enums.ParseMode.HTML)

        if not entries:
            return await msg.edit(
                "❌ No songs found in this playlist.\n"
                "<i>Make sure the playlist is public and the URL is correct.</i>",
                parse_mode=enums.ParseMode.HTML,
            )

        # Limit to MAX_QUEUE_SIZE
        if len(entries) > MAX_QUEUE_SIZE:
            entries = entries[:MAX_QUEUE_SIZE]

        await msg.edit(
            f"<blockquote>⏳ <b>Queuing {len(entries)} songs…</b></blockquote>",
            parse_mode=enums.ParseMode.HTML,
        )

        added = 0
        for e in entries:
            s = Song(
                title        = e["title"],
                url          = e["url"],
                webpage_url  = e["url"],
                requested_by = requester,
                requested_by_id = user_id,
            )
            try:
                add_to_queue(chat_id, s)
                added += 1
            except QueueFullError:
                break
            except Exception:
                continue

        # Auto-start if nothing playing
        if call_py and get_current(chat_id) is None and added > 0:
            first = entries[0]
            await msg.edit(
                f"<blockquote>🎵 <b>Playlist Queued!</b></blockquote>\n\n"
                f"📋 <b>{added}</b> songs added to queue.\n"
                f"▶️ Starting first song…",
                parse_mode=enums.ParseMode.HTML,
            )
            # Trigger play of first song from queue via existing mechanism
            from helpers.youtube import search_and_resolve
            first_song = await search_and_resolve(first["url"])
            if first_song:
                first_song.requested_by    = requester
                first_song.requested_by_id = user_id
                from helpers.queue import set_current
                from plugins.play import _stream_song, _send_playing_card
                set_current(chat_id, first_song)
                chat_title = message.chat.title or ""
                await asyncio.gather(
                    _stream_song(chat_id, first_song),
                    _send_playing_card(chat_id, first_song,
                                       reply_to=message,
                                       chat_title=chat_title),
                )
            else:
                await msg.edit(
                    f"<blockquote>📋 <b>Playlist Queued — {added} songs</b></blockquote>\n\n"
                    f"Use /play to start playing!",
                    parse_mode=enums.ParseMode.HTML,
                )
        else:
            pos_info = f"Starting from position #{get_current(chat_id) and 'next' or 1}" if get_current(chat_id) else ""
            await msg.edit(
                f"<blockquote>📋 <b>Playlist Queued!</b></blockquote>\n\n"
                f"✅ <b>{added}</b> songs added to queue.\n"
                f"🎵 They will play after the current song.\n\n"
                f"<i>Use /queue to see the full list.</i>",
                parse_mode=enums.ParseMode.HTML,
            )

    else:
        # ── Try loading as saved playlist name ────────────────────────────────
        try:
            from database import load_playlist, get_user_playlists
            songs = await load_playlist(user_id, args)
        except Exception as e:
            return await message.reply(
                f"❌ Could not load playlist <b>{args}</b>: <code>{e}</code>",
                parse_mode=enums.ParseMode.HTML,
            )

        if not songs:
            # Show available playlists
            try:
                playlists = await get_user_playlists(user_id)
                if playlists:
                    lines = [f"<blockquote>❌ Playlist <b>{args}</b> not found.</blockquote>\n\n📋 <b>Your saved playlists:</b>"]
                    for n, c in playlists[:10]:
                        lines.append(f"▸ <code>{n}</code> — {c} songs")
                    lines.append(f"\n<i>Load with:</i> <code>/playlist [name]</code>")
                    return await message.reply("\n".join(lines), parse_mode=enums.ParseMode.HTML)
            except Exception:
                pass
            return await message.reply(
                f"❌ Playlist <b>{args}</b> not found.\n\n"
                f"Create one with <code>/saveplaylist [name]</code> in a group.",
                parse_mode=enums.ParseMode.HTML,
            )

        msg = await message.reply(
            f"<blockquote>📋 <b>Loading playlist: {args}</b></blockquote>",
            parse_mode=enums.ParseMode.HTML,
        )
        added = 0
        for sd in songs:
            try:
                s = Song(
                    title           = sd.get("title", "Unknown"),
                    url             = sd.get("url", ""),
                    webpage_url     = sd.get("url", ""),
                    requested_by    = requester,
                    requested_by_id = user_id,
                )
                add_to_queue(chat_id, s)
                added += 1
            except QueueFullError:
                break
            except Exception:
                continue

        await msg.edit(
            f"<blockquote>✅ <b>Playlist <i>{args}</i> loaded!</b></blockquote>\n\n"
            f"📋 <b>{added}/{len(songs)}</b> songs queued.\n"
            f"🎵 Use /play to start!",
            parse_mode=enums.ParseMode.HTML,
        )
