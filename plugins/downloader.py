"""
downloader.py — Download audio/video files
Commands: /download [url], /song [name]
"""
import os
import asyncio
import logging
from pyrogram import Client, filters
from pyrogram.types import Message
from clients import bot

log = logging.getLogger("ApexBot.downloader")

DOWNLOAD_DIR = "downloads"
os.makedirs(DOWNLOAD_DIR, exist_ok=True)


@bot.on_message(filters.command(["download", "dl"]))
async def download_cmd(_, message: Message):
    if len(message.command) < 2:
        return await message.reply(
            "**Usage:** `/download [YouTube URL or song name]`\n\n"
            "Sends the audio file directly to chat."
        )

    query = " ".join(message.command[1:]).strip()
    msg = await message.reply(f"⬇️ Downloading: `{query}`...")

    try:
        import yt_dlp

        ydl_opts = {
            "format": "bestaudio/best",
            "outtmpl": f"{DOWNLOAD_DIR}/%(title)s.%(ext)s",
            "postprocessors": [{
                "key": "FFmpegExtractAudio",
                "preferredcodec": "mp3",
                "preferredquality": "192",
            }],
            "quiet": True,
            "no_warnings": True,
            "noplaylist": True,
        }

        if not query.startswith("http"):
            ydl_opts["default_search"] = "ytsearch1"

        loop = asyncio.get_event_loop()

        def _download():
            with yt_dlp.YoutubeDL(ydl_opts) as ydl:
                info = ydl.extract_info(query, download=True)
                if "entries" in info:
                    info = info["entries"][0]
                return info

        info = await loop.run_in_executor(None, _download)
        title = info.get("title", "Unknown")
        duration = info.get("duration", 0)

        # Find the downloaded file
        expected = os.path.join(DOWNLOAD_DIR, f"{title}.mp3")
        if not os.path.exists(expected):
            # Search for any mp3 in downloads dir
            files = [f for f in os.listdir(DOWNLOAD_DIR) if f.endswith(".mp3")]
            if not files:
                return await msg.edit("❌ Download failed.")
            expected = os.path.join(DOWNLOAD_DIR, files[-1])

        file_size = os.path.getsize(expected) / (1024 * 1024)
        if file_size > 50:
            os.remove(expected)
            return await msg.edit(f"❌ File too large ({file_size:.1f}MB). Telegram limit is 50MB.")

        await msg.edit(f"📤 Uploading: **{title}**...")
        m, s = divmod(duration, 60)
        await message.reply_audio(
            audio=expected,
            title=title,
            duration=duration,
            caption=f"🎵 {title}\n⏱ {m}:{s:02d}",
        )
        await msg.delete()
        os.remove(expected)

    except Exception as e:
        log.error("download error: %s", e)
        await msg.edit(f"❌ Download failed: `{e}`")


@bot.on_message(filters.command("song"))
async def song_cmd(_, message: Message):
    """Alias for /download with auto YouTube search."""
    if len(message.command) < 2:
        return await message.reply("**Usage:** `/song [name]`")
    # Reuse download
    message.command[0] = "download"
    await download_cmd(_, message)
