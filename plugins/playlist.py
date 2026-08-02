"""
playlist.py — User playlist management
Commands: /saveplaylist, /loadplaylist, /myplaylists, /deleteplaylist, /shareplaylist
"""
import logging
from pyrogram import Client, filters
from pyrogram.types import Message
from clients import bot
from helpers.queue import get_queue, get_current, add_to_queue, Song
from database import save_playlist, load_playlist, get_user_playlists, delete_playlist

log = logging.getLogger("ApexBot.playlist")


@bot.on_message(filters.command("saveplaylist"))
async def save_playlist_cmd(_, message: Message):
    if len(message.command) < 2:
        return await message.reply("**Usage:** `/saveplaylist [name]`")

    name = " ".join(message.command[1:]).strip()
    if len(name) > 50:
        return await message.reply("❌ Playlist name too long (max 50 chars).")

    user_id = message.from_user.id
    chat_id = message.chat.id if message.chat.type.name != "PRIVATE" else 0

    current = get_current(chat_id) if chat_id else None
    queue = get_queue(chat_id) if chat_id else []

    all_songs = []
    if current:
        all_songs.append({"title": current.title, "url": current.webpage_url or current.url})
    for s in queue:
        all_songs.append({"title": s.title, "url": s.webpage_url or s.url})

    if not all_songs:
        return await message.reply("❌ Nothing in queue to save!")

    try:
        await save_playlist(user_id, name, all_songs)
        await message.reply(
            f"✅ Playlist **{name}** saved with **{len(all_songs)}** songs!\n"
            f"Load it with: `/loadplaylist {name}`"
        )
    except Exception as e:
        await message.reply(f"❌ Error saving playlist: `{e}`")


@bot.on_message(filters.command("loadplaylist") & filters.group)
async def load_playlist_cmd(_, message: Message):
    if len(message.command) < 2:
        return await message.reply("**Usage:** `/loadplaylist [name]`")

    name = " ".join(message.command[1:]).strip()
    user_id = message.from_user.id
    chat_id = message.chat.id

    try:
        songs = await load_playlist(user_id, name)
    except Exception as e:
        return await message.reply(f"❌ Error: `{e}`")

    if not songs:
        return await message.reply(f"❌ Playlist **{name}** not found.")

    msg = await message.reply(f"📋 Loading playlist **{name}** ({len(songs)} songs)...")
    added = 0
    for song_data in songs:
        try:
            s = Song(title=song_data["title"], url=song_data["url"])
            add_to_queue(chat_id, s)
            added += 1
        except Exception:
            pass

    await msg.edit(
        f"✅ Loaded **{added}/{len(songs)}** songs from playlist **{name}** into queue!\n"
        f"Use `/play` to start playing."
    )


@bot.on_message(filters.command("myplaylists"))
async def my_playlists(_, message: Message):
    user_id = message.from_user.id
    try:
        playlists = await get_user_playlists(user_id)
    except Exception as e:
        return await message.reply(f"❌ Error: `{e}`")

    if not playlists:
        return await message.reply(
            "📋 You have no saved playlists.\n"
            "Create one with `/saveplaylist [name]` in a group!"
        )

    lines = ["🎵 **Your Playlists:**\n"]
    for name, count in playlists:
        lines.append(f"• **{name}** — {count} songs")
    lines.append(f"\n**Total:** {len(playlists)} playlists")
    await message.reply("\n".join(lines))


@bot.on_message(filters.command("deleteplaylist"))
async def delete_playlist_cmd(_, message: Message):
    if len(message.command) < 2:
        return await message.reply("**Usage:** `/deleteplaylist [name]`")
    name = " ".join(message.command[1:]).strip()
    user_id = message.from_user.id
    try:
        ok = await delete_playlist(user_id, name)
        if ok:
            await message.reply(f"🗑 Playlist **{name}** deleted.")
        else:
            await message.reply(f"❌ Playlist **{name}** not found.")
    except Exception as e:
        await message.reply(f"❌ Error: `{e}`")


@bot.on_message(filters.command("shareplaylist"))
async def share_playlist(_, message: Message):
    if len(message.command) < 2:
        return await message.reply("**Usage:** `/shareplaylist [name]`")
    name = " ".join(message.command[1:]).strip()
    user_id = message.from_user.id
    try:
        songs = await load_playlist(user_id, name)
    except Exception as e:
        return await message.reply(f"❌ Error: `{e}`")
    if not songs:
        return await message.reply(f"❌ Playlist **{name}** not found.")

    lines = [f"🎵 **Playlist: {name}** ({len(songs)} songs)\n"]
    for i, s in enumerate(songs[:30], 1):
        lines.append(f"{i}. {s['title']}")
    if len(songs) > 30:
        lines.append(f"...and {len(songs)-30} more")
    await message.reply("\n".join(lines))
