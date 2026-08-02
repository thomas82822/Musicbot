"""
spotify.py — Spotify link support
Commands: /play [spotify link] (auto-handled), /spotifyauth
"""
import re
import logging
from pyrogram import Client, filters
from pyrogram.types import Message
from clients import bot
from config import SPOTIFY_CLIENT_ID, SPOTIFY_CLIENT_SECRET

log = logging.getLogger("ApexBot.spotify")

SPOTIFY_TRACK_RE = re.compile(r"spotify\.com/track/([A-Za-z0-9]+)")
SPOTIFY_PLAYLIST_RE = re.compile(r"spotify\.com/playlist/([A-Za-z0-9]+)")
SPOTIFY_ALBUM_RE = re.compile(r"spotify\.com/album/([A-Za-z0-9]+)")

_sp = None


def _get_spotify():
    global _sp
    if _sp is not None:
        return _sp
    if not SPOTIFY_CLIENT_ID or not SPOTIFY_CLIENT_SECRET:
        return None
    try:
        import spotipy
        from spotipy.oauth2 import SpotifyClientCredentials
        auth = SpotifyClientCredentials(
            client_id=SPOTIFY_CLIENT_ID,
            client_secret=SPOTIFY_CLIENT_SECRET,
        )
        _sp = spotipy.Spotify(auth_manager=auth)
        return _sp
    except ImportError:
        log.warning("spotipy not installed")
    except Exception as e:
        log.warning("Spotify init error: %s", e)
    return None


async def resolve_spotify_url(url: str) -> list[str]:
    """
    Resolve Spotify URL to list of YouTube search queries.
    Returns list of "Artist - Title" strings.
    """
    sp = _get_spotify()
    if not sp:
        return []

    try:
        # Single track
        m = SPOTIFY_TRACK_RE.search(url)
        if m:
            track = sp.track(m.group(1))
            artists = ", ".join(a["name"] for a in track["artists"])
            return [f"{artists} - {track['name']}"]

        # Playlist
        m = SPOTIFY_PLAYLIST_RE.search(url)
        if m:
            results = sp.playlist_tracks(m.group(1), limit=50)
            queries = []
            for item in results["items"]:
                if item and item.get("track"):
                    t = item["track"]
                    artists = ", ".join(a["name"] for a in t["artists"])
                    queries.append(f"{artists} - {t['name']}")
            return queries

        # Album
        m = SPOTIFY_ALBUM_RE.search(url)
        if m:
            tracks = sp.album_tracks(m.group(1))
            queries = []
            for t in tracks["items"]:
                artists = ", ".join(a["name"] for a in t["artists"])
                queries.append(f"{artists} - {t['name']}")
            return queries

    except Exception as e:
        log.error("Spotify resolve error: %s", e)
    return []


@bot.on_message(filters.command("spotifyauth"))
async def spotify_auth(_, message: Message):
    if SPOTIFY_CLIENT_ID and SPOTIFY_CLIENT_SECRET:
        sp = _get_spotify()
        if sp:
            await message.reply(
                "✅ **Spotify Connected!**\n\n"
                "You can now use Spotify links with `/play`:\n"
                "• `/play [spotify track link]`\n"
                "• `/play [spotify playlist link]`\n"
                "• `/play [spotify album link]`"
            )
        else:
            await message.reply("❌ Spotify setup failed. Check SPOTIFY_CLIENT_ID and SPOTIFY_CLIENT_SECRET.")
    else:
        await message.reply(
            "❌ **Spotify not configured.**\n\n"
            "Add these to your environment:\n"
            "• `SPOTIFY_CLIENT_ID`\n"
            "• `SPOTIFY_CLIENT_SECRET`\n\n"
            "Get them from: https://developer.spotify.com/dashboard"
        )
