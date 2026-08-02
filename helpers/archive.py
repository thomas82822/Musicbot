"""Telegram-backed media archive for instant, cookie-free music playback.

The archive is deliberately implemented with Telegram message IDs/file IDs
instead of local disk or a second database. Telegram keeps the uploaded media
available across dyno restarts, while the channel caption contains enough
metadata to rebuild the playback queue by scanning the channel.
"""

from __future__ import annotations

import asyncio
import logging
import os
import re
import shutil
import tempfile
import time as _time
from dataclasses import dataclass

from clients import assistant, bot
from config import ARCHIVE_SCAN_LIMIT, MUSIC_ARCHIVE_CHANNEL, LOG_CHANNEL

log = logging.getLogger("ApexBot.archive")

_VIDEO_ID = re.compile(r"(?im)^video_id:\s*([A-Za-z0-9_-]{11})\s*$")
_SOURCE_URL = re.compile(r"(?im)^source:\s*(\S+)\s*$")
_TITLE = re.compile(r"(?im)^title:\s*(.+?)\s*$")
_ARTIST = re.compile(r"(?im)^artist:\s*(.+?)\s*$")
_DURATION = re.compile(r"(?im)^duration:\s*(\d+)\s*$")
_scan_lock = asyncio.Lock()
_last_scan: float = 0.0
_index_by_id: dict[str, dict] = {}
_index_by_title: dict[str, dict] = {}
_SCAN_TTL = 30.0
_scan_unavailable_logged = False
# ⚡ SPEED FIX: permanently disable archive scan on fatal channel errors
# (CHANNEL_INVALID, PEER_ID_INVALID, etc.) so every /play after boot does
# not waste a Telegram round-trip retrying a channel that will never work.
_scan_permanently_disabled = False


@dataclass
class ArchiveRecord:
    video_id: str
    title: str
    artist: str
    source_url: str
    duration: int
    message_id: int
    file_id: str
    file_type: str


def _video_id(url: str) -> str:
    match = re.search(
        r"(?:[?&]v=|youtu\.be/|shorts/|embed/)([A-Za-z0-9_-]{11})",
        url or "",
    )
    return match.group(1) if match else ""


def _normalise(value: str) -> str:
    return " ".join((value or "").lower().split())


def _match_value(pattern: re.Pattern, text: str, default: str = "") -> str:
    match = pattern.search(text)
    return match.group(1).strip() if match else default


def _caption_for(
    *,
    video_id: str,
    title: str,
    artist: str,
    source_url: str,
    duration: int,
) -> str:
    # Keep this machine-readable and human-readable. It is also the fallback
    # index when the bot restarts and the local filesystem/database is empty.
    return (
        "ApexMusic Archive v1\n"
        f"Video_ID: {video_id}\n"
        f"Title: {title[:180]}\n"
        f"Artist: {(artist or 'Unknown')[:120]}\n"
        f"Duration: {int(duration or 0)}\n"
        f"Source: {source_url}\n"
        "Playback: Telegram cache (no YouTube cookies required)"
    )


def _record_from_message(message) -> ArchiveRecord | None:
    caption = getattr(message, "caption", None) or getattr(message, "text", None) or ""
    if "ApexMusic Archive" not in caption:
        return None
    video_id = _match_value(_VIDEO_ID, caption)
    source_url = _match_value(_SOURCE_URL, caption)
    video_id = video_id or _video_id(source_url)
    if not video_id:
        return None
    title = _match_value(_TITLE, caption, "Unknown")
    artist = _match_value(_ARTIST, caption, "Unknown")
    duration_raw = _match_value(_DURATION, caption, "0")
    try:
        duration = int(duration_raw)
    except ValueError:
        duration = 0

    media = (
        getattr(message, "audio", None)
        or getattr(message, "video", None)
        or getattr(message, "document", None)
    )
    if not media or not getattr(media, "file_id", None):
        return None
    if getattr(message, "audio", None):
        file_type = "audio"
    elif getattr(message, "video", None):
        file_type = "video"
    else:
        file_type = "document"
    return ArchiveRecord(
        video_id=video_id,
        title=title,
        artist=artist,
        source_url=source_url or f"https://www.youtube.com/watch?v={video_id}",
        duration=duration,
        message_id=int(message.id),
        file_id=media.file_id,
        file_type=file_type,
    )


async def _scan_archive() -> None:
    global _last_scan, _scan_unavailable_logged, _scan_permanently_disabled
    if MUSIC_ARCHIVE_CHANNEL == 0 or _scan_permanently_disabled:
        return
    async with _scan_lock:
        if _time.monotonic() - _last_scan < _SCAN_TTL:
            return
        try:
            # Telegram does not allow bot accounts to call messages.GetHistory.
            # The assistant is a user client, so use it for archive discovery;
            # the bot remains responsible for uploads and media downloads.
            #
            # BUG FIX: If SESSION_STRING is not set, `assistant` is created
            # using BOT_TOKEN and cannot call get_chat_history (user-only API).
            # Guard against this to avoid an AttributeError/FloodWait on
            # every /play when MUSIC_ARCHIVE_CHANNEL is set but SESSION_STRING
            # is not — previously this produced a cryptic error per play request.
            if assistant is None:
                log.warning("Archive scan skipped — no SESSION_STRING/assistant client")
                _last_scan = _time.monotonic()
                return
            async for message in assistant.get_chat_history(
                MUSIC_ARCHIVE_CHANNEL, limit=max(1, ARCHIVE_SCAN_LIMIT)
            ):
                record = _record_from_message(message)
                if record:
                    data = record.__dict__.copy()
                    _index_by_id[record.video_id] = data
                    _index_by_title[_normalise(record.title)] = data
            _last_scan = _time.monotonic()
            log.info(
                "✅ Archive scan complete | channel=%s | indexed=%d",
                MUSIC_ARCHIVE_CHANNEL,
                len(_index_by_id),
            )
        except Exception as exc:
            # Archive is an optimisation. A missing/incorrect channel must not
            # prevent regular YouTube playback.
            _last_scan = _time.monotonic()
            exc_str = str(exc).upper()
            # Permanent errors (bad channel ID) — disable forever so no future
            # /play wastes a Telegram RTT retrying a channel that will never work.
            if any(k in exc_str for k in (
                "CHANNEL_INVALID", "CHAT_INVALID", "PEER_ID_INVALID",
                "CHANNEL_PRIVATE", "CHAT_FORBIDDEN",
            )):
                _scan_permanently_disabled = True
                log.warning(
                    "Archive scan permanently disabled — invalid channel %s: %s",
                    MUSIC_ARCHIVE_CHANNEL, exc,
                )
            elif not _scan_unavailable_logged:
                log.warning("Archive scan unavailable for %s: %s", MUSIC_ARCHIVE_CHANNEL, exc)
                _scan_unavailable_logged = True


async def find_archived(query: str) -> dict | None:
    """Find an archived track by YouTube URL/video ID or title words."""
    await _scan_archive()
    video_id = _video_id(query)
    if video_id and video_id in _index_by_id:
        return _index_by_id[video_id].copy()

    needle = _normalise(query)
    if not needle:
        return None
    exact = _index_by_title.get(needle)
    if exact:
        return exact.copy()
    words = [word for word in needle.split() if len(word) > 2]
    for title, record in reversed(list(_index_by_title.items())):
        if words and all(word in title for word in words):
            return record.copy()
    return None


def _fmt_duration_archive(secs: int) -> str:
    secs = int(secs or 0)
    h, rem = divmod(secs, 3600)
    m, s   = divmod(rem, 60)
    return f"{h}:{m:02d}:{s:02d}" if h else f"{m}:{s:02d}"


async def _send_archive_log(
    *,
    video_id: str,
    title: str,
    artist: str,
    source_url: str,
    duration: int,
    is_video: bool,
    message_id: int,
    chat_id: int,
    chat_title: str,
    requested_by: str,
    thumbnail: str,
) -> None:
    """Rich log card to LOG_CHANNEL with full song details + thumbnail."""
    if not LOG_CHANNEL:
        return
    try:
        from pyrogram.types import InlineKeyboardMarkup, InlineKeyboardButton
        kind_label = "🎬 Video" if is_video else "🎵 Audio"
        dur_str    = _fmt_duration_archive(duration)
        yt_url     = source_url or f"https://www.youtube.com/watch?v={video_id}"

        detail_text = (
            "📥 **Naya Song Archive Mein Add Hua!**\n"
            "━━━━━━━━━━━━━━━━━━━━━━\n\n"
            f"🎶 **{title[:80]}**\n"
            f"👤 **Artist:** {artist or 'Unknown'}\n"
            f"⏱️ **Duration:** `{dur_str}`\n"
            f"🎬 **Type:** {kind_label}\n"
            f"🔗 **YouTube:** [Yahan Click Karo]({yt_url})\n\n"
            + (f"💬 **Group:** {chat_title or str(chat_id)}\n" if chat_id else "")
            + (f"👋 **Requested by:** {requested_by}\n" if requested_by else "")
            + "\n✅ **Cache Mein Save Ho Gaya!** Ab agli baar instantly play hoga.\n"
            f"📌 Archive Msg ID: `#{message_id}`"
        )
        markup = InlineKeyboardMarkup([[
            InlineKeyboardButton("▶️ YouTube pe Dekho", url=yt_url),
        ]])
        if thumbnail:
            try:
                await bot.send_photo(LOG_CHANNEL, photo=thumbnail,
                                     caption=detail_text, reply_markup=markup)
                return
            except Exception:
                pass
        await bot.send_message(LOG_CHANNEL, detail_text,
                               reply_markup=markup, disable_web_page_preview=False)
    except Exception as exc:
        log.debug("Archive log send failed: %s", exc)


async def upload_local(
    path: str,
    *,
    title: str,
    artist: str,
    source_url: str,
    duration: int,
    is_video: bool = False,
    chat_id: int = 0,
    chat_title: str = "",
    requested_by: str = "",
    thumbnail: str = "",
) -> dict | None:
    """Upload a downloaded track once and add it to the archive index.

    Also fires a rich log entry to LOG_CHANNEL so the channel serves as a
    full-detail music library. Next time the same song is requested, the bot
    finds it via find_archived() and plays it instantly without hitting YouTube.
    """
    if (
        MUSIC_ARCHIVE_CHANNEL == 0
        or not path
        or not os.path.isfile(path)
        or not source_url
    ):
        return None
    video_id = _video_id(source_url)
    if not video_id:
        return None
    existing = await find_archived(video_id)
    if existing:
        return existing
    try:
        caption = _caption_for(
            video_id=video_id,
            title=title,
            artist=artist,
            source_url=source_url,
            duration=duration,
        )
        if is_video:
            # Keep the original container untouched. Telegram documents can
            # store webm/mkv files that send_video would reject or transcode.
            sent = await bot.send_document(
                MUSIC_ARCHIVE_CHANNEL,
                document=path,
                caption=caption,
            )
        else:
            sent = await bot.send_audio(
                MUSIC_ARCHIVE_CHANNEL,
                audio=path,
                caption=caption,
                title=title[:64],
                performer=(artist or "YouTube")[:64],
                duration=int(duration or 0),
            )
        record = _record_from_message(sent)
        if not record:
            log.warning("Archive upload succeeded but media metadata was missing")
            return None
        data = record.__dict__.copy()
        _index_by_id[record.video_id] = data
        _index_by_title[_normalise(record.title)] = data
        log.info("✅ Archived audio | video_id=%s | message_id=%s", video_id, record.message_id)

        # Fire-and-forget: send rich detail log to LOG_CHANNEL
        asyncio.ensure_future(_send_archive_log(
            video_id=video_id, title=title, artist=artist,
            source_url=source_url, duration=duration, is_video=is_video,
            message_id=record.message_id, chat_id=chat_id,
            chat_title=chat_title, requested_by=requested_by,
            thumbnail=thumbnail,
        ))

        return data
    except Exception as exc:
        log.warning("Archive upload failed for %s: %s", title[:60], exc)
        return None


async def download_archived(record: dict) -> str:
    """Download Telegram media to a short-lived local path for PyTgCalls."""
    directory = tempfile.mkdtemp(prefix="apex_tg_")
    try:
        message_id = int(record["message_id"])
        last_error: Exception | None = None

        # File references in old Telegram Message objects expire. Always fetch
        # the origin message again immediately before downloading it, then use
        # the other connected client as a fallback if one session cannot read
        # the archive channel.
        for client in (bot, assistant):
            try:
                message = await client.get_messages(MUSIC_ARCHIVE_CHANNEL, message_id)
                if not message:
                    raise RuntimeError("archive message not found")
                result = await client.download_media(
                    message,
                    file_name=os.path.join(directory, "audio"),
                )
                if result and os.path.isfile(result):
                    return result
                raise RuntimeError("archive media download returned no file")
            except Exception as exc:
                last_error = exc
                log.warning(
                    "Archive media download failed via %s: %s",
                    "bot" if client is bot else "assistant",
                    exc,
                )

        raise last_error or RuntimeError("archive media download failed")
    except Exception:
        # os.rmdir fails if the directory is non-empty (e.g. partial
        # download wrote some bytes before failing). Use shutil.rmtree instead.
        try:
            shutil.rmtree(directory, ignore_errors=True)
        except Exception:
            pass
        raise


def record_to_song_fields(record: dict) -> dict:
    return {
        "title": record.get("title", "Unknown"),
        "artist": record.get("artist", "Unknown"),
        "duration": int(record.get("duration") or 0),
        "webpage_url": record.get("source_url", ""),
        "archive_message_id": int(record.get("message_id") or 0),
        "archive_file_id": record.get("file_id", ""),
    }