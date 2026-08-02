"""
radio.py — Live radio/stream player
Commands: /radio [url/name], /addradio [name] [url], /radiolist
"""
import logging
from pyrogram import Client, filters
from pyrogram.types import Message
from clients import bot, call_py
from helpers.queue import set_current, Song
from helpers.decorators import admin_only

log = logging.getLogger("ApexBot.radio")

# Default radio stations
DEFAULT_STATIONS = {
    "lofi": ("Lofi Hip Hop", "https://streams.ilovemusic.de/iloveradio17.mp3"),
    "bollywood": ("Bollywood FM", "https://stream.desirulez.com/bollywood"),
    "chill": ("Chill Radio", "https://streams.ilovemusic.de/iloveradio2.mp3"),
    "jazz": ("Jazz FM", "https://stream.0nlineradio.de/jazz"),
    "classical": ("Classical Music", "https://stream.0nlineradio.de/classical"),
    "pop": ("Pop Radio", "https://streams.ilovemusic.de/iloveradio1.mp3"),
}

# User-saved stations (in memory; persist to DB for production)
_custom_stations: dict[str, tuple[str, str]] = {}


@bot.on_message(filters.command("radio") & filters.group)
async def radio_cmd(_, message: Message):
    if not call_py:
        return await message.reply("❌ SESSION_STRING not set. Voice chat unavailable.")

    if len(message.command) < 2:
        # Show station list
        lines = ["📻 **Radio Stations:**\n"]
        for key, (name, _) in DEFAULT_STATIONS.items():
            lines.append(f"• `/radio {key}` — {name}")
        if _custom_stations:
            lines.append("\n**Custom:**")
            for key, (name, _) in _custom_stations.items():
                lines.append(f"• `/radio {key}` — {name}")
        lines.append("\n**Or:** `/radio [direct stream URL]`")
        return await message.reply("\n".join(lines))

    query = " ".join(message.command[1:]).strip()
    chat_id = message.chat.id

    # Check if it's a saved station
    station_name = None
    stream_url = None

    if query.lower() in DEFAULT_STATIONS:
        station_name, stream_url = DEFAULT_STATIONS[query.lower()]
    elif query.lower() in _custom_stations:
        station_name, stream_url = _custom_stations[query.lower()]
    elif query.startswith("http"):
        station_name = "Custom Stream"
        stream_url = query
    else:
        return await message.reply(
            f"❌ Station `{query}` not found.\n"
            "Use `/radiolist` to see available stations."
        )

    msg = await message.reply(f"📻 Starting: **{station_name}**...")
    try:
        from pytgcalls.types import MediaStream, AudioQuality
        song = Song(
            title=f"📻 {station_name}",
            url=stream_url,
            duration=0,
            requested_by=message.from_user.first_name if message.from_user else "Unknown",
        )

        stream = MediaStream(
            stream_url,
            audio_parameters=AudioQuality.STUDIO,
            ffmpeg_parameters='-reconnect 1 -reconnect_streamed 1 -reconnect_delay_max 5',
        )
        try:
            await call_py.play(chat_id, stream)
        except Exception:
            await call_py.change_stream(chat_id, stream)

        set_current(chat_id, song)
        await msg.edit(f"📻 **Now Streaming:** {station_name}\n🔴 Live Radio")
    except Exception as e:
        await msg.edit(f"❌ Failed to start radio: `{e}`")


@bot.on_message(filters.command("addradio") & filters.group)
@admin_only
async def add_radio(_, message: Message):
    if len(message.command) < 3:
        return await message.reply("**Usage:** `/addradio [name] [url]`")
    name = message.command[1].lower()
    url = message.command[2]
    if not url.startswith("http"):
        return await message.reply("❌ URL must start with http/https")
    display_name = " ".join(message.command[1:-1]).title() if len(message.command) > 3 else name.title()
    _custom_stations[name] = (display_name, url)
    await message.reply(f"✅ Radio station **{display_name}** saved as `{name}`.")


@bot.on_message(filters.command("radiolist"))
async def radio_list(_, message: Message):
    lines = ["📻 **Available Radio Stations:**\n"]
    lines.append("**Default:**")
    for key, (name, _) in DEFAULT_STATIONS.items():
        lines.append(f"• `/radio {key}` — {name}")
    if _custom_stations:
        lines.append("\n**Custom:**")
        for key, (name, _) in _custom_stations.items():
            lines.append(f"• `/radio {key}` — {name}")
    await message.reply("\n".join(lines))
