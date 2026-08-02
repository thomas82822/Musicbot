"""
lyrics.py — Fetch song lyrics
Commands: /lyrics [song name] or /lyrics (for current song)
Uses lyrics.ovh (free, no key) with Genius API fallback.
"""
import asyncio
import logging
import aiohttp
from pyrogram import Client, filters
from pyrogram.types import Message
from clients import bot
from helpers.queue import get_current
from config import GENIUS_KEY

log = logging.getLogger("ApexBot.lyrics")

LYRICS_OVH = "https://api.lyrics.ovh/v1/{artist}/{title}"
GENIUS_API = "https://api.genius.com/search"


async def _fetch_lyricsovh(title: str, artist: str = "unknown") -> str | None:
    """Fetch from lyrics.ovh (free)."""
    url = LYRICS_OVH.format(artist=artist, title=title)
    try:
        async with aiohttp.ClientSession() as session:
            async with session.get(url, timeout=aiohttp.ClientTimeout(total=8)) as resp:
                if resp.status == 200:
                    data = await resp.json()
                    return data.get("lyrics")
    except Exception as e:
        log.debug("lyrics.ovh error: %s", e)
    return None


async def _fetch_genius(query: str) -> str | None:
    """Fetch from Genius API (needs GENIUS_KEY)."""
    if not GENIUS_KEY:
        return None
    try:
        async with aiohttp.ClientSession() as session:
            async with session.get(
                GENIUS_API,
                headers={"Authorization": f"Bearer {GENIUS_KEY}"},
                params={"q": query},
                timeout=aiohttp.ClientTimeout(total=8),
            ) as resp:
                if resp.status == 200:
                    data = await resp.json()
                    hits = data.get("response", {}).get("hits", [])
                    if hits:
                        url = hits[0]["result"]["url"]
                        # Scrape lyrics page
                        async with session.get(url) as page:
                            html = await page.text()
                        import re
                        # Try to extract lyrics from Genius HTML
                        matches = re.findall(r'<div[^>]*data-lyrics-container[^>]*>(.*?)</div>', html, re.DOTALL)
                        if matches:
                            raw = " ".join(matches)
                            clean = re.sub(r'<br\s*/?>', '\n', raw)
                            clean = re.sub(r'<[^>]+>', '', clean)
                            return clean.strip()
    except Exception as e:
        log.debug("genius error: %s", e)
    return None


async def _fetch_lyrics(query: str) -> str | None:
    """Try multiple sources."""
    parts = query.split(" - ", 1)
    if len(parts) == 2:
        artist, title = parts[0].strip(), parts[1].strip()
    else:
        artist, title = "unknown", query.strip()

    lyrics = await _fetch_lyricsovh(title, artist)
    if lyrics:
        return lyrics

    lyrics = await _fetch_genius(query)
    return lyrics


def _split_text(text: str, limit: int = 4000) -> list[str]:
    """Split text into Telegram-safe chunks."""
    chunks = []
    while len(text) > limit:
        idx = text[:limit].rfind('\n')
        if idx < 1000:
            idx = limit
        chunks.append(text[:idx])
        text = text[idx:].lstrip()
    if text:
        chunks.append(text)
    return chunks


@bot.on_message(filters.command("lyrics"))
async def lyrics_cmd(_, message: Message):
    query = " ".join(message.command[1:]).strip()

    if not query:
        # Try current song
        song = get_current(message.chat.id)
        if song:
            query = song.title
            if song.artist:
                query = f"{song.artist} - {song.title}"
        if not query:
            return await message.reply(
                "**Usage:** `/lyrics [song name]`\nOr use in a group where music is playing."
            )

    msg = await message.reply(f"🔍 Fetching lyrics for: **{query}**...")

    lyrics = await _fetch_lyrics(query)
    if not lyrics:
        return await msg.edit(
            f"😔 **No lyrics found** for: `{query}`\n\n"
            "Try a more specific search like: `Artist - Song Title`"
        )

    await msg.delete()
    chunks = _split_text(lyrics.strip())
    header = f"🎵 **Lyrics: {query}**\n{'─' * 30}\n\n"

    for i, chunk in enumerate(chunks):
        text = (header if i == 0 else "") + chunk
        await message.reply(text)
        if len(chunks) > 1:
            await asyncio.sleep(0.5)
