"""
play.py — Core music plugin  v4.0
===================================
Commands: /play, /vplay, /skip, /stop, /pause, /resume, /np, /volume,
          /loop, /shuffle, /autoplay, /mute, /unmute, /nowplaying

Fix 4: Instant VC join via silence trick — bot joins VC BEFORE search completes
Fix 4: vplay stuck fix — 15s timeout + auto fallback to audio mode

v4.0 — Now Playing card redesign:
  • No more auto-generated thumbnail image — lean text-only card.
  • Old cluttered keyboard (progress bar / queue-count / restart / Support
    Group / Close) replaced with a minimal control set + an Autoplay toggle.
  • Autoplay (persisted per-chat via database.get_autoplay/set_autoplay):
    when ON and the queue empties, the bot fetches a related YouTube track
    (via helpers.youtube.get_youtube_suggestions) and keeps streaming
    instead of leaving the call. Suggested/queued-up tracks are listed
    under the card.
"""
import asyncio
import logging
import time
import os
import tempfile

from pyrogram import Client, filters, enums
from pytgcalls import filters as tgcalls_filters
from pyrogram.types import (
    Message, InlineKeyboardMarkup, InlineKeyboardButton, CallbackQuery
)
from clients import bot, call_py
from config import MAX_QUEUE_SIZE, SUPPORT_CHAT
from helpers.queue import (
    Song, get_queue, get_current, add_to_queue, pop_queue,
    set_current, clear_queue, QueueFullError, shuffle_queue, queue_size
)
from helpers.youtube import search_and_resolve
from helpers.decorators import admin_only

log = logging.getLogger("ApexBot.play")

# ── Per-chat state ────────────────────────────────────────────────────────────
_loop:            dict[int, bool]          = {}
_paused:          dict[int, bool]          = {}   # UI-only: drives Pause/Resume button label
_volume:          dict[int, int]           = {}
_start_time:      dict[int, float]         = {}   # epoch when current song started
_np_message:      dict[int, Message]       = {}   # last sent now-playing message, for in-place edits
_extra_cache:     dict[int, str]           = {}   # cached "UP NEXT" / "AUTOPLAY SUGGESTIONS" block
_silence_playing: dict[int, bool]          = {}
_play_next_locks:    dict[int, asyncio.Lock]  = {}
_stream_changed_at:  dict[int, float]         = {}
# ↑ MEMORY/R14 FIX: per-chat mutex + stream-transition cooldown for _play_next.
#
# Root cause A (fixed): NTgCalls fires 8-10 stream_end events in rapid
# succession on a pipe failure → parallel yt-dlp spawns → 1.5 GB RAM → R14.
# Fix: per-chat Lock; if _play_next is already running, duplicates return early.
#
# Root cause B (this fix): after the pipe-fail retry, NTgCalls fires 4 more
# "Reached end of file" events (old pipe stream cleanup) 60-70 ms after
# change_stream returns — right after the lock is released.  These enter
# _play_next_inner with elapsed ≈ 0 s (timer just reset by _stream_song),
# trigger a second retry, and eventually call leave_group_call on the live
# call → "Call not found, already removed" warning + dropped playback.
#
# Fix: _stream_song stamps _stream_changed_at[chat_id] = now().
# _play_next checks the stamp and drops stream_end events that arrive within
# _STREAM_TRANSITION_GRACE seconds (5 s) of the last stream change.
# Legitimate next-song events (natural end of a full song) arrive much later.
_STREAM_TRANSITION_GRACE = 5.0   # seconds to suppress stream_end after change_stream
# ↑ SILENCE RACE FIX: True while the instant-join silence stream is active.
#
# Root cause: NTgCalls reads local MP3 files faster than real-time during
# its pipeline init window (~1-2s).  A 4-second silence file is consumed
# in ~1.5 s → stream_end fires BEFORE the song search completes.
# on_stream_end calls _play_next → queue is empty → bot leaves VC prematurely.
# 0.2 s later, the song is found → _stream_song rejoins VC.  This causes an
# unnecessary leave+rejoin blip and can race with change_stream.
#
# Fix: set this flag True in _join_vc_early, check it in on_stream_end.
# If True, the silence stream ended early — ignore stream_end and let the
# /play command's _stream_song call handle the VC transition directly.
# The flag is cleared in _stream_song (real song starts) and _join_vc_early
# cleanup paths.

# Support link from config
try:
    from config import SUPPORT_CHAT as _SUPPORT
except Exception:
    _SUPPORT = "https://t.me/ApexSupport"


async def _safe_get_autoplay(chat_id: int) -> bool:
    """Read the persisted per-chat autoplay flag; defaults to False on any error."""
    try:
        from database import get_autoplay
        return await get_autoplay(chat_id)
    except Exception as e:
        log.debug("get_autoplay failed for %d: %s", chat_id, e)
        return False


async def _safe_get_adder(chat_id: int) -> str:
    """Read per-chat 'My Cute Owner' name; returns '' on any error."""
    try:
        from database import get_chat_adder
        return await get_chat_adder(chat_id) or ""
    except Exception:
        return ""

# ── Silence file — for instant VC join ───────────────────────────────────────
_SILENCE_PATH: str | None = None
_SILENCE_LOCK = asyncio.Lock()


async def _get_silence_file() -> str | None:
    """
    Create (once) a 3-second PCM silence MP3 so the bot can join VC
    instantly while yt-dlp searches in parallel.
    """
    global _SILENCE_PATH
    if _SILENCE_PATH and os.path.exists(_SILENCE_PATH):
        return _SILENCE_PATH

    async with _SILENCE_LOCK:
        # Double-check after acquiring lock
        if _SILENCE_PATH and os.path.exists(_SILENCE_PATH):
            return _SILENCE_PATH

        path = os.path.join(tempfile.gettempdir(), "apexbot_vc_join_silence.mp3")
        try:
            proc = await asyncio.create_subprocess_exec(
                "ffmpeg", "-y",
                "-f", "lavfi", "-i", "anullsrc=channel_layout=stereo:sample_rate=48000",
                "-t", "4",
                "-c:a", "libmp3lame", "-b:a", "64k",
                path,
                stdout=asyncio.subprocess.DEVNULL,
                stderr=asyncio.subprocess.DEVNULL,
            )
            await asyncio.wait_for(proc.wait(), timeout=15)
            if os.path.exists(path) and os.path.getsize(path) > 0:
                _SILENCE_PATH = path
                log.info("✅ Silence file created: %s", path)
        except Exception as e:
            log.debug("Silence file creation failed: %s", e)

    return _SILENCE_PATH


async def _join_vc_early(chat_id: int) -> bool:
    """
    Join VC immediately with a short silence stream so the bot is already
    in the call when the real song URL arrives (change_stream is ~instant).
    Returns True if joined successfully.
    """
    if not call_py:
        return False
    silence = await _get_silence_file()
    if not silence:
        return False
    try:
        from pytgcalls.types import MediaStream, AudioQuality
        sstream = MediaStream(silence, audio_parameters=AudioQuality.STUDIO)
        # Mark silence as active BEFORE play() so on_stream_end can see it
        # even if NTgCalls reads the file instantly (speed-bug scenario).
        _silence_playing[chat_id] = True
        await asyncio.wait_for(call_py.play(chat_id, sstream), timeout=8.0)
        log.debug("✅ Early VC join done for %d", chat_id)
        return True
    except asyncio.TimeoutError:
        log.debug("Early VC join timed out for %d", chat_id)
        _silence_playing.pop(chat_id, None)
        return False
    except Exception as e:
        log.debug("Early VC join: %s", e)
        _silence_playing.pop(chat_id, None)
        return False


# ═══════════════════════════════════════════════════════════════════════════════
#  Helpers
# ═══════════════════════════════════════════════════════════════════════════════

def _fmt_time(secs: int) -> str:
    if secs <= 0:
        return "0:00"
    m, s = divmod(secs, 60)
    h, m = divmod(m, 60)
    if h:
        return f"{h}:{m:02d}:{s:02d}"
    return f"{m}:{s:02d}"


async def _build_extra_block(
    chat_id: int,
    song: Song,
    autoplay_on: bool | None = None,
) -> str:
    """
    Build the block shown under the caption:
      • If the user queue has songs waiting → "UP NEXT" list.
      • Else, if Autoplay is ON → "AUTOPLAY SUGGESTIONS" fetched from
        YouTube's related-tracks (Radio/Mix) for the current song.
      • Else → empty (nothing to show).

    Pass autoplay_on when already known to avoid a duplicate DB round-trip.
    """
    queue_list = get_queue(chat_id)
    if queue_list:
        lines = ["🎧 <b>UP NEXT</b>"]
        for i, s in enumerate(queue_list[:5], start=1):
            lines.append(f"{i}. {s.title}")
        return "\n".join(lines)

    # Only call DB if caller hasn't already fetched this
    if autoplay_on is None:
        autoplay_on = await _safe_get_autoplay(chat_id)
    if not autoplay_on:
        return ""

    try:
        from helpers.youtube import extract_video_id, get_youtube_suggestions
        vid = extract_video_id(song.webpage_url or song.url or "")
        suggestions = await get_youtube_suggestions(vid, max_results=4) if vid else []
    except Exception as e:
        log.debug("autoplay suggestions fetch failed for %d: %s", chat_id, e)
        suggestions = []

    if not suggestions:
        return ""
    lines = ["🎲 <b>AUTOPLAY SUGGESTIONS</b>"]
    for i, s in enumerate(suggestions[:4], start=1):
        lines.append(f"{i}. {s.get('title') or 'Unknown'}")
    return "\n".join(lines)


def _make_caption(
    song: Song,
    dur: str,
    chat_title: str = "",
    adder_name: str = "",
    extra_block: str = "",
    autoplay_on: bool = False,
) -> str:
    """
    HTML blockquote caption with premium emoji formatting:
      🎵 NOW PLAYING | GroupName
      🌀 TITLE : ...
      🌀 DURATION : ...
      🌀 BY : ...
      🎲 AUTOPLAY : ON/OFF
      🌸 My Cute Owner : AdderName  (per-GC)
      🎧 UP NEXT / 🎲 AUTOPLAY SUGGESTIONS  (below, own blockquote)
    """
    title  = song.title or "Unknown"
    req_by = song.requested_by or "Unknown"
    gc_line = f" <b>{chat_title}</b>" if chat_title else ""

    cap = (
        f"<blockquote>🎵 <b>NOW PLAYING</b>{gc_line}</blockquote>\n\n"
        f"🌀 <b>TITLE</b> : {title}\n"
        f"🌀 <b>DURATION</b> : {dur}\n"
        f"🌀 <b>BY</b> : {req_by}\n"
        f"🎲 <b>AUTOPLAY</b> : {'ON' if autoplay_on else 'OFF'}"
    )

    if adder_name:
        cap += f"\n\n<blockquote>🌸 <b>My Cute Owner</b> : {adder_name}</blockquote>"

    if extra_block:
        cap += f"\n\n<blockquote>{extra_block}</blockquote>"

    return cap


def _make_keyboard(chat_id: int, paused: bool = False, loop_on: bool = False, autoplay_on: bool = False) -> InlineKeyboardMarkup:
    """
    Lean, own-style play card keyboard (no thumbnail, no progress bar,
    no Support Group / Close clutter):
      Row 1 → Pause/Resume (toggle)  |  Skip  |  Stop
      Row 2 → Loop: ON/OFF           |  Autoplay: ON/OFF
    """
    play_pause_label  = "▶ Resume" if paused else "⏸ Pause"
    play_pause_action = "resume" if paused else "pause"

    return InlineKeyboardMarkup([
        [
            InlineKeyboardButton(play_pause_label, callback_data=f"np_{play_pause_action}_{chat_id}"),
            InlineKeyboardButton("⏭ Skip",         callback_data=f"np_skip_{chat_id}"),
            InlineKeyboardButton("⏹ Stop",         callback_data=f"np_stop_{chat_id}"),
        ],
        [
            InlineKeyboardButton(f"🔁 Loop: {'ON' if loop_on else 'OFF'}",     callback_data=f"np_loop_{chat_id}"),
            InlineKeyboardButton(f"🎲 Autoplay: {'ON' if autoplay_on else 'OFF'}", callback_data=f"np_autoplay_{chat_id}"),
        ],
    ])


async def _send_playing_card(
    chat_id: int,
    song: Song,
    reply_to: Message | None = None,
    chat_title: str = "",
    adder_name: str = "",
) -> Message | None:
    """
    Send the Now Playing card — lean, text-only (no auto-generated
    thumbnail image), tracked in `_np_message` so later transitions
    (skip / autoplay auto-advance / natural queue progression) can
    update it in place instead of spamming a new message each time.
    """
    _paused[chat_id] = False
    dur = _fmt_time(song.duration) if song.duration else "?"
    # Fetch autoplay flag once and pass it down — avoids a duplicate DB
    # round-trip inside _build_extra_block (was called independently before).
    autoplay_on = await _safe_get_autoplay(chat_id)
    extra_block = await _build_extra_block(chat_id, song, autoplay_on=autoplay_on)
    _extra_cache[chat_id] = extra_block

    cap = _make_caption(
        song, dur, chat_title=chat_title, adder_name=adder_name,
        extra_block=extra_block, autoplay_on=autoplay_on,
    )
    kb  = _make_keyboard(chat_id, paused=False, loop_on=_loop.get(chat_id, False), autoplay_on=autoplay_on)
    rto = reply_to.id if reply_to else None

    try:
        msg = await bot.send_message(
            chat_id                  = chat_id,
            text                     = cap,
            parse_mode               = enums.ParseMode.HTML,
            reply_markup             = kb,
            reply_to_message_id      = rto,
            disable_web_page_preview = True,
        )
        _np_message[chat_id] = msg
        return msg
    except Exception as e:
        log.error("_send_playing_card failed: %s", e)
        return None


async def _refresh_np_message(chat_id: int, song: Song) -> None:
    """
    Update the now-playing card in place for a song transition (skip,
    autoplay auto-advance, or natural queue progression on stream end).
    Falls back to sending a fresh card if the old message can't be edited
    (e.g. deleted, or too old for Telegram to edit).
    """
    _paused[chat_id] = False
    dur = _fmt_time(song.duration) if song.duration else "?"
    autoplay_on = await _safe_get_autoplay(chat_id)
    extra_block = await _build_extra_block(chat_id, song)
    _extra_cache[chat_id] = extra_block

    cap = _make_caption(song, dur, extra_block=extra_block, autoplay_on=autoplay_on)
    kb  = _make_keyboard(chat_id, paused=False, loop_on=_loop.get(chat_id, False), autoplay_on=autoplay_on)

    msg = _np_message.get(chat_id)
    if msg:
        try:
            await msg.edit(cap, parse_mode=enums.ParseMode.HTML, reply_markup=kb, disable_web_page_preview=True)
            return
        except Exception as e:
            log.debug("NP message edit failed, sending fresh card instead: %s", e)

    await _send_playing_card(chat_id, song)


# ═══════════════════════════════════════════════════════════════════════════════
#  Core streaming helpers
# ═══════════════════════════════════════════════════════════════════════════════

async def _fetch_autoplay_song(chat_id: int, prev: Song | None) -> Song | None:
    """
    Queue is empty and Autoplay is ON — pick a related YouTube track
    (via the Radio/Mix suggestions) and resolve it into a playable Song.
    Returns None if nothing could be resolved (caller then stops normally).
    """
    if not prev:
        return None
    try:
        from helpers.youtube import extract_video_id, get_youtube_suggestions
        vid = extract_video_id(prev.webpage_url or prev.url or "")
        if not vid:
            return None
        suggestions = await get_youtube_suggestions(vid, max_results=5)
        for s in suggestions:
            target = s.get("webpage_url") or s.get("title")
            if not target:
                continue
            song = await search_and_resolve(target)
            if song:
                song.is_video        = False
                song.requested_by    = "🎲 Autoplay"
                song.requested_by_id = 0
                song.is_autoplay     = True
                return song
    except Exception as e:
        log.debug("Autoplay fetch failed for chat %d: %s", chat_id, e)
    return None


async def _play_next(chat_id: int):
    # ── MEMORY / R14 FIX: per-chat mutex ───────────────────────────────────────
    # NTgCalls can fire multiple stream_end events in quick succession for the
    # same chat (pipe-fail scenario on Heroku CDN block).  Without a lock every
    # concurrent call enters the pipe-retry path and spawns its own yt-dlp
    # process → 8-10 parallel downloads → 1.5 GB RAM → R14 crash.
    # If _play_next is already running for this chat, skip the duplicate call.
    # Drop stream_end events that arrive within the grace window after a
    # stream change — they are stale cleanup events from the old stream.
    grace_age = time.time() - _stream_changed_at.get(chat_id, 0.0)
    if grace_age < _STREAM_TRANSITION_GRACE:
        log.debug(
            "_play_next grace-drop for %d — %.2fs since last stream change (< %.0fs grace)",
            chat_id, grace_age, _STREAM_TRANSITION_GRACE,
        )
        return

    if chat_id not in _play_next_locks:
        _play_next_locks[chat_id] = asyncio.Lock()
    lock = _play_next_locks[chat_id]
    if lock.locked():
        log.debug("_play_next already running for %d — dropping duplicate stream_end", chat_id)
        return
    async with lock:
        await _play_next_inner(chat_id)


async def _play_next_inner(chat_id: int):
    try:
        prev = get_current(chat_id)
        elapsed = time.time() - _start_time.get(chat_id, 0.0)

        if _loop.get(chat_id) and prev:
            next_song = prev
        else:
            next_song = pop_queue(chat_id)

        # ── PIPE-FAILURE RETRY ──────────────────────────────────────────────
        # On Heroku (cdn_blocked), yt-dlp's FIFO subprocess can only download
        # the first DASH segment (~1 MB) before the CDN blocks subsequent
        # segments.  The stream ends after ~11 s → stream_end fires → we land
        # here with an empty queue and a song that barely started.
        #
        # Fix: detect "song played < 30 s AND queue empty AND no loop" as a
        # pipe failure, mark the URL so _resolve_stream takes the local-download
        # path (which uses curl_cffi / Chrome TLS fingerprint and bypasses the
        # CDN block), then re-resolve and replay the same track.
        #
        # BUG FIX: On cloud hosts (_ON_CLOUD_HOST=True) pipes are NEVER used —
        # _resolve_stream skips directly to local download on every call.
        # The elapsed<30s condition fires for any play failure (e.g. a vplay
        # video attempt that timed out), not just pipe failures.  Running the
        # retry on cloud causes the same track to be downloaded a second time
        # and then _stream_song is called mid-playback, causing a conflict and
        # freeze.  Guard: skip retry when cdn is already blocked (cloud host).
        from helpers.youtube import is_cdn_blocked as _is_cdn_blocked_retry
        if (
            not next_song
            and prev
            and not _loop.get(chat_id, False)
            and elapsed < 30
            and (prev.webpage_url or "")
            and not _is_cdn_blocked_retry()   # cloud hosts never use pipes
        ):
            retry_url = prev.webpage_url
            try:
                import dataclasses as _dc
                from helpers.youtube import mark_pipe_failed, get_stream
                mark_pipe_failed(retry_url)                  # force local-dl path
                stream_url, _, dur, hdrs = await get_stream(
                    retry_url, is_video=prev.is_video, force_refresh=True
                )
                if stream_url:
                    next_song = _dc.replace(
                        prev,
                        url          = stream_url,
                        duration     = dur or prev.duration,
                        http_headers = hdrs or prev.http_headers,
                    )
                    log.info(
                        "🔄 Pipe-fail retry via local-dl | %.1fs elapsed | %s",
                        elapsed, (prev.title or "?")[:60],
                    )
            except Exception as _re:
                log.warning("Pipe retry failed for %s: %s", prev.title, _re)
        # ── END PIPE-FAILURE RETRY ──────────────────────────────────────────

        if not next_song and await _safe_get_autoplay(chat_id):
            next_song = await _fetch_autoplay_song(chat_id, prev)

        if not next_song:
            set_current(chat_id, None)
            _start_time.pop(chat_id, None)
            _stream_changed_at.pop(chat_id, None)
            _paused.pop(chat_id, None)
            _extra_cache.pop(chat_id, None)
            try:
                if call_py:
                    await call_py.leave_group_call(chat_id)
            except Exception:
                pass
            msg = _np_message.pop(chat_id, None)
            if msg:
                try:
                    await msg.edit(
                        "<blockquote>⏹ <b>Queue Finished</b></blockquote>\n\n"
                        "Use /play to add more songs, or turn Autoplay ON "
                        "to keep the music going automatically.",
                        parse_mode=enums.ParseMode.HTML,
                    )
                except Exception:
                    pass
            return

        await _stream_song(chat_id, next_song)
        await _refresh_np_message(chat_id, next_song)
    except Exception as e:
        log.error("_play_next error: %s", e)


def _ffmpeg_params(url: str) -> str:
    """
    Return FFmpeg reconnect flags ONLY for HTTP/HTTPS streams.
    LOCAL FILE FIX: -reconnect_streamed causes FFmpeg to hang indefinitely
    when the input is a local file path (no network to reconnect to).
    yt-dlp downloads to /tmp/apex_dl_xxx/audio.mp4 on cloud hosts — passing
    reconnect flags to those paths is the root cause of the /play freeze.
    """
    if url.startswith("http://") or url.startswith("https://"):
        return "-reconnect 1 -reconnect_streamed 1 -reconnect_delay_max 5"
    return ""


async def _stream_song(chat_id: int, song: Song, already_in_vc: bool = False):
    """
    Start or change-stream to `song`.
    already_in_vc=True  → skip the failed play() attempt and go straight to
                          change_stream(); saves one round-trip when the
                          silence-trick early join is known to have succeeded.
    already_in_vc=False → try play() first, fall back to change_stream() on error.
    """
    if not call_py:
        return
    try:
        from pytgcalls.types import MediaStream, AudioQuality, VideoQuality

        set_current(chat_id, song)
        _start_time[chat_id] = time.time()

        # LOCAL FILE FIX: reconnect flags are for HTTP streams only.
        # On cloud hosts yt-dlp downloads to a local temp file; passing
        # -reconnect_streamed to a local path hangs FFmpeg indefinitely.
        ffparams = _ffmpeg_params(song.url)

        if song.is_video:
            stream = MediaStream(
                song.url,
                audio_parameters  = AudioQuality.STUDIO,
                video_parameters  = VideoQuality.SD_480p,
                ffmpeg_parameters = ffparams,
                headers           = song.http_headers,
            )
        else:
            stream = MediaStream(
                song.url,
                audio_parameters  = AudioQuality.STUDIO,
                ffmpeg_parameters = ffparams,
                headers           = song.http_headers,
            )

        await _try_play_or_change(chat_id, stream, prefer_change=already_in_vc)

        # ── STREAM TRANSITION GRACE STAMP ───────────────────────────────────
        # Record the moment change_stream/play returned so _play_next can
        # suppress stale "Reached end of file" events from the old stream.
        _stream_changed_at[chat_id] = time.time()

    except Exception as e:
        log.error("_stream_song error in %s: %s", chat_id, e)
    finally:
        # ── SILENCE RACE FIX (finally block) ────────────────────────────────
        # Moved to finally so _silence_playing is ALWAYS cleared even when
        # an exception propagates — mirrors the _stream_song_video_with_fallback
        # pattern.  Previously inside try: an exception in _try_play_or_change
        # would skip this line, leaving _silence_playing[chat_id]=True forever
        # and silently dropping all future stream_end events → bot frozen muted.
        _silence_playing.pop(chat_id, None)


async def _stream_song_video_with_fallback(
    chat_id: int,
    song: Song,
    timeout: float = 15.0,
    already_in_vc: bool = False,
):
    """
    Like _stream_song but for video mode.
    If streaming hangs for more than `timeout` seconds, falls back to audio-only.
    already_in_vc=True skips the failed play() attempt (same as _stream_song).
    """
    if not call_py:
        return

    try:
        from pytgcalls.types import MediaStream, AudioQuality, VideoQuality
        from helpers.youtube import is_cdn_blocked as _is_cdn_blocked

        set_current(chat_id, song)
        _start_time[chat_id] = time.time()

        # LOCAL FILE FIX: reconnect flags are for HTTP streams only.
        ffparams = _ffmpeg_params(song.url)

        # BUG FIX: Cloud hosts (Heroku/Railway/Render) cannot download YouTube
        # DASH video+audio streams — the CDN blocks fragment fetches, so
        # _download_audio_sync ends up with an audio-only file regardless of
        # the requested format.  Passing that audio-only file with VideoQuality
        # set causes NTgCalls to hang (no video frames) → 15 s timeout fires →
        # TimeoutError propagates → _silence_playing never cleared → bot freezes
        # muted in VC for the rest of the session.
        #
        # Fix: detect cloud hosts via is_cdn_blocked() and skip the video
        # stream attempt entirely.  Stream audio directly — /vplay still works,
        # it just plays audio (same experience for voice chats without a video
        # surface anyway).
        if _is_cdn_blocked():
            log.info(
                "☁️ Cloud host — vplay falling back to audio mode (CDN blocks video DASH) | %d",
                chat_id,
            )
            audio_stream = MediaStream(
                song.url,
                audio_parameters  = AudioQuality.STUDIO,
                ffmpeg_parameters = ffparams,
                headers           = song.http_headers,
            )
            song.is_video = False
            await _try_play_or_change(chat_id, audio_stream, prefer_change=already_in_vc)
            return

        video_stream = MediaStream(
            song.url,
            audio_parameters  = AudioQuality.STUDIO,
            video_parameters  = VideoQuality.SD_480p,
            ffmpeg_parameters = ffparams,
            headers           = song.http_headers,
        )

        try:
            await asyncio.wait_for(
                _try_play_or_change(chat_id, video_stream, prefer_change=already_in_vc),
                timeout=timeout,
            )
            log.debug("✅ Video stream started for %d", chat_id)
        except asyncio.TimeoutError:
            log.warning("⚠️ vplay timed out (%ds) for %d — falling back to audio", timeout, chat_id)
            audio_stream = MediaStream(
                song.url,
                audio_parameters  = AudioQuality.STUDIO,
                ffmpeg_parameters = ffparams,
                headers           = song.http_headers,
            )
            song.is_video = False
            await _try_play_or_change(chat_id, audio_stream, prefer_change=already_in_vc)

    except Exception as e:
        log.error("_stream_song_video_with_fallback error in %s: %s", chat_id, e)
    finally:
        # SILENCE RACE FIX — moved to finally so _silence_playing is ALWAYS
        # cleared even when an exception or TimeoutError propagates.
        # Previously this was inside the try block — any exception in
        # _try_play_or_change (including CancelledError from wait_for) skipped
        # this line, leaving _silence_playing[chat_id]=True forever and causing
        # all future stream_end events to be silently dropped → bot frozen muted.
        _silence_playing.pop(chat_id, None)


# TIMEOUT FIX: pytgcalls play()/change_stream() can hang indefinitely when
# FFmpeg fails to open the stream (bad URL, local file with wrong params, etc.).
# Without a timeout the entire /play coroutine freezes — bot stays in VC but
# never actually streams, and all further commands are blocked.
_PLAY_TIMEOUT = 20.0   # seconds before we give up on a single play/change attempt


async def _try_play_or_change(chat_id: int, stream, prefer_change: bool = False):
    """
    Try play(); if already in VC, fall back to change_stream().
    prefer_change=True → attempt change_stream first (when early join is known
    to have succeeded) so we skip the guaranteed-to-fail play() round-trip.

    Each call is wrapped in a 20 s timeout so a hung pytgcalls/NTgCalls call
    never freezes the bot permanently.
    """
    if prefer_change:
        try:
            await asyncio.wait_for(
                call_py.change_stream(chat_id, stream),
                timeout=_PLAY_TIMEOUT,
            )
            return
        except asyncio.TimeoutError:
            log.warning("_try_play_or_change: change_stream timed out for %d — retrying via play()", chat_id)
        except Exception:
            pass  # Wasn't in VC after all — try play() below
    try:
        await asyncio.wait_for(
            call_py.play(chat_id, stream),
            timeout=_PLAY_TIMEOUT,
        )
    except asyncio.TimeoutError:
        log.warning("_try_play_or_change: play() timed out for %d — trying change_stream()", chat_id)
        await asyncio.wait_for(
            call_py.change_stream(chat_id, stream),
            timeout=_PLAY_TIMEOUT,
        )
    except Exception:
        await asyncio.wait_for(
            call_py.change_stream(chat_id, stream),
            timeout=_PLAY_TIMEOUT,
        )


# Register stream-end handler
if call_py:
    @call_py.on_update(tgcalls_filters.stream_end())
    async def on_stream_end(_, update):
        cid = update.chat_id
        # SILENCE RACE FIX — If the silence stream ended (NTgCalls reads local
        # MP3 at CPU speed → 4-s silence consumed in ~1.5 s), ignore this
        # stream_end event.  The /play command is still resolving the song URL
        # in parallel; _stream_song will call play()/change_stream() to start
        # the real song as soon as the URL is ready.  Calling _play_next here
        # would pop an empty queue → leave_group_call → bot leaves VC → then
        # _stream_song has to rejoin 0.2 s later, causing a blip.
        if _silence_playing.get(cid, False):
            # BUG FIX: Use .get() not .pop() — NTgCalls fires multiple stream_end
            # events for the same silence stream (we saw 3-4 in logs: 06:41:28).
            # pop() removes the key on first hit; subsequent events see False and
            # fall through to _play_next → empty queue → bot leaves VC prematurely.
            # .get() keeps the flag set until _stream_song explicitly clears it
            # via _silence_playing.pop(chat_id, None) after change_stream returns.
            log.debug("⏩ stream_end for silence ignored — real song pending | %d", cid)
            return
        await _play_next(cid)


# ═══════════════════════════════════════════════════════════════════════════════
#  Inline keyboard callbacks
# ═══════════════════════════════════════════════════════════════════════════════

@bot.on_callback_query(filters.regex(r"^np_(resume|pause|skip|stop|loop|autoplay)_(-?\d+)$"))
async def np_callback(client: Client, query: CallbackQuery):
    data   = query.data
    parts  = data.split("_")
    action = parts[1]
    try:
        chat_id = int(parts[2])
    except (IndexError, ValueError):
        return await query.answer("Invalid.", show_alert=True)

    song = get_current(chat_id)

    if action in ("pause", "resume"):
        if not call_py:
            return await query.answer("❌ Voice chat not available.", show_alert=True)
        try:
            if action == "pause":
                await call_py.pause(chat_id)
                _paused[chat_id] = True
                await query.answer("⏸ Paused.")
            else:
                await call_py.resume(chat_id)
                _paused[chat_id] = False
                await query.answer("▶ Resumed.")
            if song:
                kb = _make_keyboard(
                    chat_id, paused=_paused.get(chat_id, False),
                    loop_on=_loop.get(chat_id, False),
                    autoplay_on=await _safe_get_autoplay(chat_id),
                )
                try:
                    await query.message.edit_reply_markup(kb)
                except Exception:
                    pass
        except Exception as e:
            await query.answer(f"❌ {e}", show_alert=True)

    elif action == "skip":
        if not call_py:
            return await query.answer("❌ Voice chat not available.", show_alert=True)
        if not song:
            return await query.answer("Nothing to skip.", show_alert=True)
        await query.answer("⏭ Skipped.")
        await _play_next(chat_id)  # refreshes the NP message internally

    elif action == "stop":
        clear_queue(chat_id)
        set_current(chat_id, None)
        _loop.pop(chat_id, None)
        _paused.pop(chat_id, None)
        _start_time.pop(chat_id, None)
        _stream_changed_at.pop(chat_id, None)
        _extra_cache.pop(chat_id, None)
        _np_message.pop(chat_id, None)
        _play_next_locks.pop(chat_id, None)
        if call_py:
            try:
                await call_py.leave_group_call(chat_id)
            except Exception:
                pass
        await query.answer("⏹ Stopped.")
        try:
            await query.message.edit(
                "<blockquote>⏹ <b>Playback Stopped</b></blockquote>\n\n"
                "Queue cleared. Use /play to start again!",
                parse_mode=enums.ParseMode.HTML,
            )
        except Exception:
            pass

    elif action == "loop":
        _loop[chat_id] = not _loop.get(chat_id, False)
        await query.answer(f"🔁 Loop {'ON' if _loop[chat_id] else 'OFF'}.")
        if song:
            kb = _make_keyboard(
                chat_id, paused=_paused.get(chat_id, False),
                loop_on=_loop[chat_id],
                autoplay_on=await _safe_get_autoplay(chat_id),
            )
            try:
                await query.message.edit_reply_markup(kb)
            except Exception:
                pass

    elif action == "autoplay":
        try:
            from database import get_autoplay, set_autoplay
            new_state = not await get_autoplay(chat_id)
            await set_autoplay(chat_id, new_state)
        except Exception as e:
            return await query.answer(f"❌ {e}", show_alert=True)
        await query.answer(f"🎲 Autoplay {'ON' if new_state else 'OFF'}.")
        if song:
            await _refresh_np_message(chat_id, song)


# ═══════════════════════════════════════════════════════════════════════════════
#  /play  —  INSTANT VC JOIN + parallel search
# ═══════════════════════════════════════════════════════════════════════════════

@bot.on_message(filters.command(["play", "p"]) & filters.group)
async def play(client: Client, message: Message):
    if not call_py:
        return await message.reply(
            "<blockquote>❌ <b>Voice Chat Unavailable</b></blockquote>\n\n"
            "SESSION_STRING set nahi hai. /start karein aur login karein.",
            parse_mode=enums.ParseMode.HTML,
        )

    if len(message.command) < 2 and not message.reply_to_message:
        return await message.reply(
            "<blockquote>🎵 <b>Usage: /play</b></blockquote>\n\n"
            "/play <code>[song name ya URL]</code>\n\n"
            "Example: /play Arijit Singh Tum Hi Ho",
            parse_mode=enums.ParseMode.HTML,
        )

    query = " ".join(message.command[1:])
    if message.reply_to_message and message.reply_to_message.text:
        query = query or message.reply_to_message.text

    chat_id    = message.chat.id
    chat_title = message.chat.title or ""
    current    = get_current(chat_id)

    # ── INSTANT: fire VC join + search + adder ALL simultaneously ───
    # 1. Early VC join (silence trick) — bot is in VC before search finishes.
    # 2. search_and_resolve — starts BEFORE reply is awaited (~250ms head-start).
    # 3. _safe_get_adder    — runs concurrently with search (separate DB call).
    # 4. message.reply      — sent while search runs; we await it to get `msg`.
    # Net result: search gets 250 ms free head-start; adder never blocks anything.
    join_task: asyncio.Task | None = None
    if current is None and call_py:
        join_task = asyncio.create_task(_join_vc_early(chat_id))

    search_task = asyncio.create_task(search_and_resolve(query))
    adder_task  = asyncio.create_task(_safe_get_adder(chat_id))

    msg = await message.reply(
        "<blockquote>🔍 <b>Searching…</b></blockquote>",
        parse_mode=enums.ParseMode.HTML,
    )

    try:
        song = await search_task
    except Exception as e:
        adder_task.cancel()
        if join_task:
            join_task.cancel()
        return await msg.edit(
            f"<blockquote>❌ <b>Search Error</b></blockquote>\n\n<code>{e}</code>",
            parse_mode=enums.ParseMode.HTML,
        )

    if not song:
        adder_task.cancel()
        if join_task:
            join_task.cancel()
            try:
                await call_py.leave_group_call(chat_id)
            except Exception:
                pass
        return await msg.edit(
            "<blockquote>❌ <b>No Results Found</b></blockquote>\n\n"
            "Try another song name or paste a YouTube link.",
            parse_mode=enums.ParseMode.HTML,
        )

    # Force audio-only for /play (NOT video)
    song.is_video        = False
    song.requested_by    = message.from_user.first_name if message.from_user else "Unknown"
    song.requested_by_id = message.from_user.id if message.from_user else 0

    # adder is almost certainly done by now (ran during ~1.5s search)
    try:
        adder_name = await adder_task
    except Exception:
        adder_name = ""

    if current is None:
        # ── New song: stream immediately ────────────────────────────
        set_current(chat_id, song)
        asyncio.create_task(msg.delete())   # fire-and-forget — don't block stream

        # Determine if early join succeeded (check result without extra wait if done)
        joined_early = False
        if join_task:
            if join_task.done():
                try:
                    joined_early = join_task.result() or False
                except Exception:
                    pass
            else:
                # Still running — wait briefly (silence+play usually ≤2s total)
                try:
                    joined_early = await asyncio.wait_for(
                        asyncio.shield(join_task), timeout=2.0
                    ) or False
                except (asyncio.TimeoutError, Exception):
                    pass

        # Stream + send card concurrently; skip failed play() if already in VC
        await asyncio.gather(
            _stream_song(chat_id, song, already_in_vc=joined_early),
            _send_playing_card(
                chat_id, song, reply_to=message,
                chat_title=chat_title, adder_name=adder_name,
            ),
        )
    else:
        # ── Queue mode ──────────────────────────────────────────────
        if join_task:
            join_task.cancel()
        try:
            pos = add_to_queue(chat_id, song)
        except QueueFullError:
            return await msg.edit(
                f"<blockquote>❌ <b>Queue Full!</b></blockquote>\n\n"
                f"Maximum {MAX_QUEUE_SIZE} songs allowed.",
                parse_mode=enums.ParseMode.HTML,
            )
        dur = _fmt_time(song.duration) if song.duration else "?"
        await msg.edit(
            f"<blockquote>📋 <b>Added to Queue — #{pos}</b></blockquote>\n\n"
            f"🎶 <b>{song.title}</b>\n"
            f"⏱ Duration: <code>{dur}</code>\n"
            f"👤 By: {song.requested_by}",
            parse_mode=enums.ParseMode.HTML,
        )


# ═══════════════════════════════════════════════════════════════════════════════
#  /vplay  (video mode) — with stuck-fix timeout + audio fallback
# ═══════════════════════════════════════════════════════════════════════════════

@bot.on_message(filters.command(["vplay", "vp"]) & filters.group)
async def vplay(client: Client, message: Message):
    if not call_py:
        return await message.reply(
            "<blockquote>❌ <b>Voice Chat Unavailable</b></blockquote>\n\n"
            "SESSION_STRING set nahi hai.",
            parse_mode=enums.ParseMode.HTML,
        )
    if len(message.command) < 2:
        return await message.reply(
            "<blockquote>🎬 <b>Usage: /vplay</b></blockquote>\n\n"
            "/vplay <code>[song name ya URL]</code>",
            parse_mode=enums.ParseMode.HTML,
        )

    query      = " ".join(message.command[1:])
    chat_id    = message.chat.id
    chat_title = message.chat.title or ""
    current    = get_current(chat_id)

    # ── Same instant-parallel strategy as /play ──────────────────────
    join_task: asyncio.Task | None = None
    if current is None and call_py:
        join_task = asyncio.create_task(_join_vc_early(chat_id))

    search_task = asyncio.create_task(search_and_resolve(query, video=True))
    adder_task  = asyncio.create_task(_safe_get_adder(chat_id))

    msg = await message.reply(
        "<blockquote>🔍 <b>Searching (Video Mode)…</b></blockquote>",
        parse_mode=enums.ParseMode.HTML,
    )

    try:
        song = await search_task
    except Exception as e:
        adder_task.cancel()
        if join_task:
            join_task.cancel()
        return await msg.edit(
            f"<blockquote>❌ <b>Search Error</b></blockquote>\n\n<code>{e}</code>",
            parse_mode=enums.ParseMode.HTML,
        )

    if not song:
        adder_task.cancel()
        if join_task:
            join_task.cancel()
            try:
                await call_py.leave_group_call(chat_id)
            except Exception:
                pass
        return await msg.edit(
            "<blockquote>❌ <b>No Results Found</b></blockquote>",
            parse_mode=enums.ParseMode.HTML,
        )

    song.is_video        = True
    song.requested_by    = message.from_user.first_name if message.from_user else "Unknown"
    song.requested_by_id = message.from_user.id if message.from_user else 0

    try:
        adder_name = await adder_task
    except Exception:
        adder_name = ""

    if current is None:
        set_current(chat_id, song)
        asyncio.create_task(msg.delete())   # fire-and-forget

        joined_early = False
        if join_task:
            if join_task.done():
                try:
                    joined_early = join_task.result() or False
                except Exception:
                    pass
            else:
                try:
                    joined_early = await asyncio.wait_for(
                        asyncio.shield(join_task), timeout=2.0
                    ) or False
                except (asyncio.TimeoutError, Exception):
                    pass

        # Stream with video + 15s timeout + audio fallback; skip play() if in VC
        await asyncio.gather(
            _stream_song_video_with_fallback(
                chat_id, song, timeout=15.0, already_in_vc=joined_early
            ),
            _send_playing_card(
                chat_id, song, reply_to=message,
                chat_title=chat_title, adder_name=adder_name,
            ),
        )
    else:
        if join_task:
            join_task.cancel()
        try:
            pos = add_to_queue(chat_id, song)
        except QueueFullError:
            return await msg.edit(
                "<blockquote>❌ <b>Queue Full!</b></blockquote>",
                parse_mode=enums.ParseMode.HTML,
            )
        dur = _fmt_time(song.duration) if song.duration else "?"
        await msg.edit(
            f"<blockquote>🎬 <b>Added to Video Queue — #{pos}</b></blockquote>\n\n"
            f"🎥 <b>{song.title}</b>\n"
            f"⏱ Duration: <code>{dur}</code>",
            parse_mode=enums.ParseMode.HTML,
        )


# ═══════════════════════════════════════════════════════════════════════════════
#  /np  — Now Playing card (manual refresh)
# ═══════════════════════════════════════════════════════════════════════════════

@bot.on_message(filters.command(["np", "nowplaying", "current"]) & filters.group)
async def now_playing(_, message: Message):
    chat_id = message.chat.id
    song    = get_current(chat_id)
    if not song:
        return await message.reply(
            "<blockquote>❌ <b>Nothing Playing</b></blockquote>\n\n"
            "Use /play to start music!",
            parse_mode=enums.ParseMode.HTML,
        )

    await _send_playing_card(chat_id, song, reply_to=message)


# ═══════════════════════════════════════════════════════════════════════════════
#  Basic controls
# ═══════════════════════════════════════════════════════════════════════════════

@bot.on_message(filters.command(["skip", "s"]) & filters.group)
async def skip(_, message: Message):
    chat_id = message.chat.id
    cur = get_current(chat_id)
    if not cur:
        return await message.reply(
            "<blockquote>❌ <b>Nothing is playing right now.</b></blockquote>",
            parse_mode=enums.ParseMode.HTML,
        )
    n = 1
    if len(message.command) > 1:
        try:
            n = int(message.command[1])
        except ValueError:
            pass
    for _ in range(n - 1):
        pop_queue(chat_id)
    await message.reply(
        f"<blockquote>⏭ <b>Skipped {n} song{'s' if n > 1 else ''}!</b></blockquote>",
        parse_mode=enums.ParseMode.HTML,
    )
    await _play_next(chat_id)


@bot.on_message(filters.command(["stop", "end"]) & filters.group)
async def stop(_, message: Message):
    chat_id = message.chat.id
    clear_queue(chat_id)
    set_current(chat_id, None)
    _loop.pop(chat_id, None)
    _start_time.pop(chat_id, None)
    _stream_changed_at.pop(chat_id, None)
    _play_next_locks.pop(chat_id, None)
    if call_py:
        try:
            await call_py.leave_group_call(chat_id)
        except Exception:
            pass
    await message.reply(
        "<blockquote>⏹ <b>Playback Stopped</b></blockquote>\n\n"
        "Queue cleared. Use /play to start again!",
        parse_mode=enums.ParseMode.HTML,
    )


@bot.on_message(filters.command("pause") & filters.group)
async def pause(_, message: Message):
    if not call_py:
        return
    try:
        await call_py.pause(message.chat.id)
        await message.reply(
            "<blockquote>⏸ <b>Paused</b></blockquote>\n\nUse /resume to continue.",
            parse_mode=enums.ParseMode.HTML,
        )
    except Exception as e:
        await message.reply(f"❌ <code>{e}</code>", parse_mode=enums.ParseMode.HTML)


@bot.on_message(filters.command("resume") & filters.group)
async def resume(_, message: Message):
    if not call_py:
        return
    try:
        await call_py.resume(message.chat.id)
        await message.reply(
            "<blockquote>▶️ <b>Resumed!</b></blockquote>",
            parse_mode=enums.ParseMode.HTML,
        )
    except Exception as e:
        await message.reply(f"❌ <code>{e}</code>", parse_mode=enums.ParseMode.HTML)


@bot.on_message(filters.command(["mute", "m"]) & filters.group)
async def mute(_, message: Message):
    if not call_py:
        return
    try:
        await call_py.mute(message.chat.id)
        await message.reply(
            "<blockquote>🔇 <b>Muted</b></blockquote>",
            parse_mode=enums.ParseMode.HTML,
        )
    except Exception as e:
        await message.reply(f"❌ <code>{e}</code>", parse_mode=enums.ParseMode.HTML)


@bot.on_message(filters.command("unmute") & filters.group)
async def unmute(_, message: Message):
    if not call_py:
        return
    try:
        await call_py.unmute(message.chat.id)
        await message.reply(
            "<blockquote>🔊 <b>Unmuted</b></blockquote>",
            parse_mode=enums.ParseMode.HTML,
        )
    except Exception as e:
        await message.reply(f"❌ <code>{e}</code>", parse_mode=enums.ParseMode.HTML)


@bot.on_message(filters.command("volume") & filters.group)
async def volume(_, message: Message):
    if not call_py:
        return
    if len(message.command) < 2:
        cur = _volume.get(message.chat.id, 100)
        return await message.reply(
            f"<blockquote>🔊 <b>Volume</b></blockquote>\n\n"
            f"Current: <code>{cur}%</code>\n\n"
            f"Usage: /volume <code>[0-200]</code>",
            parse_mode=enums.ParseMode.HTML,
        )
    try:
        vol = int(message.command[1])
        if not 0 <= vol <= 200:
            return await message.reply(
                "❌ Volume must be between <code>0</code> and <code>200</code>.",
                parse_mode=enums.ParseMode.HTML,
            )
        await call_py.change_volume_call(message.chat.id, vol)
        _volume[message.chat.id] = vol
        emoji = "🔇" if vol < 20 else "🔉" if vol < 70 else "🔊" if vol < 130 else "📢"
        await message.reply(
            f"<blockquote>{emoji} <b>Volume set to {vol}%</b></blockquote>",
            parse_mode=enums.ParseMode.HTML,
        )
    except Exception as e:
        await message.reply(f"❌ <code>{e}</code>", parse_mode=enums.ParseMode.HTML)


@bot.on_message(filters.command("loop") & filters.group)
async def loop_cmd(_, message: Message):
    chat_id        = message.chat.id
    _loop[chat_id] = not _loop.get(chat_id, False)
    if _loop[chat_id]:
        await message.reply(
            "<blockquote>🔁 <b>Loop ON</b></blockquote>\n\nCurrent song will repeat.",
            parse_mode=enums.ParseMode.HTML,
        )
    else:
        await message.reply(
            "<blockquote>➡️ <b>Loop OFF</b></blockquote>",
            parse_mode=enums.ParseMode.HTML,
        )


@bot.on_message(filters.command("shuffle") & filters.group)
async def shuffle_cmd(_, message: Message):
    shuffle_queue(message.chat.id)
    await message.reply(
        "<blockquote>🔀 <b>Queue Shuffled!</b></blockquote>",
        parse_mode=enums.ParseMode.HTML,
    )


@bot.on_message(filters.command("autoplay") & filters.group)
async def autoplay_cmd(_, message: Message):
    chat_id = message.chat.id
    try:
        from database import get_autoplay, set_autoplay
        new_state = not await get_autoplay(chat_id)
        await set_autoplay(chat_id, new_state)
    except Exception as e:
        return await message.reply(f"❌ <code>{e}</code>", parse_mode=enums.ParseMode.HTML)

    if new_state:
        await message.reply(
            "<blockquote>🎲 <b>Autoplay ON</b></blockquote>\n\n"
            "Queue khatam hone par YouTube se related songs khud-ba-khud bajenge.",
            parse_mode=enums.ParseMode.HTML,
        )
    else:
        await message.reply(
            "<blockquote>🎲 <b>Autoplay OFF</b></blockquote>",
            parse_mode=enums.ParseMode.HTML,
        )

    song = get_current(chat_id)
    if song:
        await _refresh_np_message(chat_id, song)
