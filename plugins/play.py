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
_active_join_tasks:  dict[int, asyncio.Task]  = {}   # per-chat early-join tasks; cancelled by /stop
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

# ── BUG FIX: premature stream_end retry ───────────────────────────────────────
# ntgcalls streams cloud/CDN-blocked songs through a one-shot FIFO pipe (see
# helpers/youtube._start_pipe_download). yt-dlp's startup (PO-token, cookies,
# extractor init) can take several seconds before the first audio bytes are
# available. If ntgcalls' internal ffmpeg reader gives up on the FIFO before
# that (its own "shell_reader.cpp: Reached end of the file" retries), the
# writer thread's next write raises a broken-pipe error and pytgcalls fires
# stream_end almost instantly — long before the song could have actually
# finished. Without this guard, on_stream_end treated that as "song over" and
# jumped straight to "Queue Finished" without ever playing audio. Track how
# long the current song has actually been streaming and, if stream_end fires
# suspiciously fast, re-resolve a fresh pipe/stream and retry the same song
# a bounded number of times before giving up and advancing normally.
_stream_retries: dict[int, int]  = {}
_PREMATURE_END_THRESHOLD = 6.0   # seconds — below this, stream_end is treated as a failed pipe, not a finished song
    # BUG FIX: raised 3.0 → 6.0 to cover the _STREAM_TRANSITION_GRACE (5s) dead zone.
    # Old value (3.0) < grace window (5.0): stream_end between 3–5s was silently
    # dropped (not premature → not retried; within grace → dropped by _play_next),
    # leaving bot muted in VC with no stream. Now any stream_end < 6s triggers retry.
_MAX_STREAM_RETRIES      = 2

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
    """Read per-chat adder info; returns formatted 'name | @username · id' string."""
    try:
        from database import get_chat_adder_full
        info = await get_chat_adder_full(chat_id)
        if not info:
            return ""
        uid, uname, uname_full = info
        parts = []
        if uname_full:
            parts.append(uname_full)
        if uname:
            parts.append(f"@{uname}")
        if uid:
            parts.append(f"<code>{uid}</code>")
        return " · ".join(parts) if parts else ""
    except Exception:
        try:
            from database import get_chat_adder
            return await get_chat_adder(chat_id) or ""
        except Exception:
            return ""

# ── Silence file — for instant VC join ───────────────────────────────────────
_SILENCE_PATH: str | None = None
_SILENCE_LOCK = asyncio.Lock()


async def _seed_assistant_peer(chat_id: int) -> None:
    """
    ROOT FIX for [400 CHANNEL_INVALID] on every pytgcalls call.

    Problem:
      pytgcalls internally calls channels.GetChannels before play(),
      change_stream(), and leave_group_call().  That MTProto call requires
      an InputPeerChannel with the correct access_hash.  The assistant
      (userbot) is a fresh account — its Pyrogram peer cache starts empty.
      resolve_peer() and get_chat() BOTH call channels.GetChannels to
      populate the cache, but since the assistant hasn't been added to the
      group, Telegram returns [400 CHANNEL_INVALID] → cache stays empty →
      every subsequent pytgcalls call also fails with CHANNEL_INVALID.

    Fix:
      The BOT client (bot token) IS a member of every group it manages.
      bot.resolve_peer(chat_id) succeeds because Telegram trusts the bot
      and returns InputPeerChannel(channel_id, access_hash).
      We then write that access_hash directly into the assistant's Pyrogram
      storage so the assistant can use it without making its own network call.
      After this, pytgcalls can resolve the channel peer from local cache.
    """
    from clients import bot as _bot, assistant as _asst
    if _bot is None or _asst is None:
        return
    try:
        from pyrogram.raw.types import InputPeerChannel, InputPeerChat
        peer = await _bot.resolve_peer(chat_id)
        if isinstance(peer, InputPeerChannel):
            await _asst.storage.update_peers(
                [(peer.channel_id, peer.access_hash, "channel", None, None)]
            )
            log.info(
                "✅ Seeded assistant peer cache for %d (channel_id=%d)",
                chat_id, peer.channel_id,
            )
        elif isinstance(peer, InputPeerChat):
            # Regular group — no access_hash needed, but mark it known
            await _asst.storage.update_peers(
                [(peer.chat_id, 0, "chat", None, None)]
            )
            log.info("✅ Seeded assistant peer cache for %d (chat)", chat_id)
        else:
            log.warning(
                "Unexpected peer type %s for %d — VC ops may fail",
                type(peer).__name__, chat_id,
            )
    except Exception as _err:
        log.warning(
            "Peer cache seed FAILED for %d: %s — VC join will likely fail",
            chat_id, _err,
        )


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
        # Seed assistant peer cache from bot client before pytgcalls call.
        await _seed_assistant_peer(chat_id)

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
    Telegram blockquote (expandable) NOW PLAYING card with premium emojis.
    Format:
      [ 🎵 NOW PLAYING 🎶           ← expandable blockquote header
        🏠 GroupName ]
      🌀 TITLE : ...
      🌀 DURATION : ...
      🌀 BY : ...
      🎲 AUTOPLAY : ON/OFF
      🌸 My Cute Owner : name · @username · id
      🎧 UP NEXT / 🎲 AUTOPLAY SUGGESTIONS
    """
    try:
        from helpers.premium_emojis import _apply_premium_emojis as _pe
    except Exception:
        _pe = lambda x: x

    title  = song.title or "Unknown"
    req_by = song.requested_by or "Unknown"

    # Expandable blockquote header (Telegram collapses long ones by default)
    header_inner = f"🎵 <b>NOW PLAYING</b> 🎶"
    if chat_title:
        header_inner += f"\n🏠 <b>{chat_title}</b>"
    autoplay_status = "<b>ON ✅</b>" if autoplay_on else "OFF"

    cap = (
        f"<blockquote expandable>{header_inner}</blockquote>\n\n"
        f"🌀 <b>TITLE</b> : {title}\n"
        f"🌀 <b>DURATION</b> : {dur}\n"
        f"🌀 <b>BY</b> : {req_by}\n"
        f"🎲 <b>AUTOPLAY</b> : {autoplay_status}"
    )

    if adder_name:
        cap += f"\n\n<blockquote>🌸 <b>My Cute Owner</b> : {adder_name}</blockquote>"

    if extra_block:
        cap += f"\n\n<blockquote>{extra_block}</blockquote>"

    return _pe(cap)


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


async def _play_next(chat_id: int, force: bool = False):
    # ── MEMORY / R14 FIX: per-chat mutex ───────────────────────────────────────
    # NTgCalls can fire multiple stream_end events in quick succession for the
    # same chat (pipe-fail scenario on Heroku CDN block).  Without a lock every
    # concurrent call enters the pipe-retry path and spawns its own yt-dlp
    # process → 8-10 parallel downloads → 1.5 GB RAM → R14 crash.
    # If _play_next is already running for this chat, skip the duplicate call.
    # Drop stream_end events that arrive within the grace window after a
    # stream change — they are stale cleanup events from the old stream.
    #
    # SKIP FIX: grace period check is for stream_end events only — NOT for
    # manual user actions (skip button, /skip command, /stop command).
    # Pass force=True from user-triggered callers to bypass the grace check.
    # Without force=True, a manual skip fired within 5s of the last stream
    # change is silently dropped → bot stays in VC without advancing.
    if not force:
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
        # On Heroku/Railway/Render (cdn_blocked), _resolve_stream uses a FIFO
        # pipe for the FIRST attempt of each URL (when _pipe_failures[url]==0).
        # The yt-dlp subprocess can only download the first DASH segment
        # (~1-2 MB, ~10-25 s of audio) before the CDN blocks subsequent
        # segments.  The broken-pipe error fires → stream_end fires → we land
        # here with an empty queue and a song that barely started.
        #
        # Fix: detect "song played < 30 s AND queue empty AND no loop" AND
        # "a pipe actually failed for this URL" (had_pipe_failure) as a pipe
        # failure, mark the URL so _resolve_stream takes the local-download
        # path (which uses curl_cffi / Chrome TLS fingerprint and bypasses the
        # CDN block), then re-resolve and replay the same track.
        #
        # NOTE: The old guard `not is_cdn_blocked()` was WRONG — cloud hosts
        # DO use pipes for the first attempt (when _pipe_failures[url]==0).
        # Blocking the retry on cloud hosts caused "Queue Finished" immediately
        # after searching, because the pipe failed but the retry was suppressed.
        # The correct guard is had_pipe_failure(url): only retry when the pipe
        # specifically failed for this URL, regardless of host type.
        from helpers.youtube import is_cdn_blocked as _is_cdn_blocked_retry, had_pipe_failure as _had_pipe_failure
        # PIPE-FAILURE RETRY: Use a duration-aware window instead of a hard 30s cutoff.
        # With a hard 30s limit, a pipe that breaks at 40s into a 5-minute song
        # was skipping the retry → premature "Queue Finished". Now we retry as
        # long as the song hasn't played through at least 85% of its known duration.
        _song_dur = prev.duration if prev else 0
        _retry_window = max(30, int(_song_dur * 0.85)) if (_song_dur and _song_dur > 30) else 30
        if (
            not next_song
            and prev
            and not _loop.get(chat_id, False)
            and elapsed < _retry_window
            and (prev.webpage_url or "")
            and (not _is_cdn_blocked_retry() or _had_pipe_failure(prev.webpage_url or ""))
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

        # A genuinely new song is about to play (or the queue is ending) —
        # the premature-stream_end retry budget only applies to the song
        # that just failed, not whatever plays next.
        _stream_retries.pop(chat_id, None)

        if not next_song:
            set_current(chat_id, None)
            _start_time.pop(chat_id, None)
            _stream_changed_at.pop(chat_id, None)
            _paused.pop(chat_id, None)
            _extra_cache.pop(chat_id, None)
            _stream_retries.pop(chat_id, None)
            # QUEUE FINISHED DRAIN FIX: stream_end fires when yt-dlp closes the
            # FIFO (all bytes written), but NTgCalls' internal ffmpeg decoder may
            # still have decoded audio frames queued. A short sleep lets those
            # frames play out before we cut the VC connection, preventing the
            # "Queue Finished shown while music is still audible" experience.
            # We re-check current() after the sleep — a concurrent /play during
            # this window means a new song is starting; don't leave VC in that case.
            await asyncio.sleep(2.0)
            if get_current(chat_id) is not None or get_queue(chat_id):
                return  # New song added during drain — stay in VC
            try:
                if call_py:
                    # Seed assistant peer cache from bot client before leave call.
                    await _seed_assistant_peer(chat_id)
                    await asyncio.wait_for(
                        call_py.leave_group_call(chat_id), timeout=5.0
                    )
            except Exception as _leave_err:
                log.warning("leave_group_call failed for %d: %s", chat_id, _leave_err)
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

        # already_in_vc=True: bot is already in the call when queue advances.
        # Skips the guaranteed-to-fail play() attempt and goes straight to
        # change_stream() — eliminates the 20s _PLAY_TIMEOUT hang on skip.
        await _stream_song(chat_id, next_song, already_in_vc=True)
        await _refresh_np_message(chat_id, next_song)
    except Exception as e:
        log.error("_play_next error: %s", e)


def _ffmpeg_params(url: str) -> str:
    """
    Return FFmpeg flags for the given stream URL.

    HTTP/HTTPS streams: reconnect flags so ffmpeg recovers from transient drops.

    FIFO pipe streams (/tmp/apex_pipe_*): -re flag (read at native frame rate).
      ROOT CAUSE of 2x/6x speed bug on FIFO pipes:
        F_SETPIPE_SZ=4096 limits the kernel pipe BUFFER to 4 KB, but does not
        limit ffmpeg's DECODE rate.  ffmpeg decodes WebM/Opus 10-20x faster than
        real-time, drains the 4 KB buffer in microseconds, the writer immediately
        refills it (yt-dlp downloads at full network speed), and the cycle repeats
        → NTgCalls receives audio frames faster than real-time → song plays at 2-6x speed.
      Fix: -re tells ffmpeg to read its input at the stream's native frame rate
        (based on the WebM timestamps). ffmpeg sleeps between reads to maintain
        1x rate → the writer blocks on FIFO write → yt-dlp download is naturally
        paced. Audio plays at EXACTLY the correct speed from byte 0.
      Note: -re is placed before -i (input flag position) in pytgcalls/NTgCalls,
        so it correctly applies to the FIFO input, not the output.

    Local downloaded files (/tmp/apex_dl_*): no flags.
      -reconnect_streamed on a local path hangs ffmpeg indefinitely (no network).
    """
    if url.startswith("http://") or url.startswith("https://"):
        return "-reconnect 1 -reconnect_streamed 1 -reconnect_delay_max 5"
    if "/apex_pipe_" in url:
        return "-re"
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
            # VPLAY CLOUD FIX: If song.url is a video-format FIFO pipe (started
            # with is_video=True), clean it up and resolve a fresh audio-only
            # stream. Playing a video+audio FIFO via audio-only MediaStream wastes
            # bandwidth (full video download) and causes ffmpeg to demux an
            # unneeded video track — root cause of audio distortion on cloud.
            from helpers.youtube import cleanup_temp_file as _ctf, get_stream as _gs_audio
            audio_url = song.url
            if song.url and "/apex_pipe_" in song.url:
                _old_pipe = song.url
                cleanup_in_bg = asyncio.create_task(
                    asyncio.get_event_loop().run_in_executor(None, _ctf, _old_pipe)
                )
                try:
                    _wp = song.webpage_url or song.url
                    _au, _, _dur2, _hdrs2 = await _gs_audio(
                        _wp, is_video=False, force_refresh=True
                    )
                    if _au:
                        audio_url = _au
                        song.http_headers = _hdrs2 or {}
                        if _dur2:
                            song.duration = _dur2
                        log.info("☁️ vplay: swapped video pipe → audio-only stream | %d", chat_id)
                    else:
                        log.debug("vplay audio re-resolve returned empty — using original url")
                except Exception as _fe:
                    log.debug("vplay audio fallback re-resolve failed: %s", _fe)
                finally:
                    try:
                        await asyncio.wait_for(cleanup_in_bg, timeout=1.0)
                    except Exception:
                        pass

            ffparams = _ffmpeg_params(audio_url)
            audio_stream = MediaStream(
                audio_url,
                audio_parameters  = AudioQuality.STUDIO,
                ffmpeg_parameters = ffparams,
                headers           = song.http_headers,
            )
            song.is_video = False
            song.url = audio_url
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

    BUG FIX: Newer pytgcalls GitHub builds may not expose change_stream as an
    attribute (renamed or removed in some commit windows).  Runtime check via
    getattr() + fallback to play() ensures the bot never crashes with
    'PyTgCalls object has no attribute change_stream'.

    Each call is wrapped in a 20 s timeout so a hung pytgcalls/NTgCalls call
    never freezes the bot permanently.

    CHANNEL_INVALID ROOT FIX: pytgcalls internally calls channels.GetChannels
    when joining or switching streams in a voice chat. If the assistant client
    has never seen this chat (fresh restart / first /play in a group), the
    InputPeer is not in its cache → Telegram returns [400 CHANNEL_INVALID].
    Pre-resolving via assistant.get_chat() fixes the cache before every call.
    This is the root cause of ALL reported VC failures:
      • Bot doesn't join VC on /play
      • FIFO reader timeout — NTgCalls never started so no reader opened pipe
      • /skip and /stop silently fail — same GetChannels needed for leave/change
      • Bot stays in VC after queue ends — leave_group_call fails silently too
    """
    # Seed assistant peer cache from bot client before pytgcalls call.
    await _seed_assistant_peer(chat_id)

    _change_stream = getattr(call_py, 'change_stream', None)

    if prefer_change and _change_stream:
        try:
            await asyncio.wait_for(
                _change_stream(chat_id, stream),
                timeout=_PLAY_TIMEOUT,
            )
            return
        except asyncio.TimeoutError:
            log.warning("_try_play_or_change: change_stream timed out for %d — retrying via play()", chat_id)
        except AttributeError:
            log.warning("change_stream not available in this pytgcalls build — using play() only")
        except Exception:
            pass  # Wasn't in VC after all — try play() below
    try:
        await asyncio.wait_for(
            call_py.play(chat_id, stream),
            timeout=_PLAY_TIMEOUT,
        )
    except asyncio.TimeoutError:
        log.warning("_try_play_or_change: play() timed out for %d — trying change_stream()", chat_id)
        if _change_stream:
            await asyncio.wait_for(_change_stream(chat_id, stream), timeout=_PLAY_TIMEOUT)
        else:
            # No change_stream — retry play() with a brief delay
            await asyncio.sleep(0.5)
            await asyncio.wait_for(call_py.play(chat_id, stream), timeout=_PLAY_TIMEOUT)
    except Exception:
        if _change_stream:
            await asyncio.wait_for(_change_stream(chat_id, stream), timeout=_PLAY_TIMEOUT)
        else:
            # Newer pytgcalls: play() handles both joining and stream switching
            await asyncio.wait_for(call_py.play(chat_id, stream), timeout=_PLAY_TIMEOUT)


async def _retry_premature_stream(chat_id: int, song: Song) -> bool:
    """
    Re-resolve a FRESH stream/pipe for `song` and restart playback in place.

    The stale `song.url` from the failed attempt is a one-shot FIFO path
    that has already been torn down (see helpers/youtube._start_pipe_download's
    cleanup in its `finally` block) — reusing it would just fail again.

    On cloud hosts (Heroku) where the YouTube CDN is IP-blocked, the FIFO pipe
    always fails after the first DASH segment (~1-2 MB).  The background writer
    thread that records the failure in _pipe_failures runs concurrently and may
    not have updated the counter by the time this retry fires (race condition).
    We therefore call mark_pipe_failed() explicitly here so that the subsequent
    get_stream() call sees _pipe_failures > 0 and goes straight to the
    local-download path (_download_audio_sync / curl_cffi) instead of starting
    yet another doomed FIFO pipe.

    Returns True if a retry attempt was launched, False if re-resolution
    failed (caller should then fall back to advancing the queue normally).
    """
    from helpers.youtube import get_stream, mark_pipe_failed

    target_url = song.webpage_url or song.url

    # Force the next get_stream() call to skip the pipe and use local download.
    # This is safe even if the pipe hadn't formally failed yet — the stream_end
    # firing in < _PREMATURE_END_THRESHOLD seconds is proof enough the pipe
    # can't deliver audio on this host.
    mark_pipe_failed(target_url)
    log.info("📥 Premature-end retry: pipe marked failed → will use local download | %s", target_url[:80])

    try:
        stream_url, _audio_url, dur, http_headers = await get_stream(
            target_url, is_video=song.is_video
        )
    except Exception as exc:
        log.warning("Premature-end retry: get_stream failed for %r: %s", target_url[:80], exc)
        return False

    if not stream_url:
        log.warning("Premature-end retry: get_stream returned empty URL for %r", target_url[:80])
        return False

    song.url = stream_url
    song.http_headers = http_headers or {}
    if dur:
        song.duration = dur

    if song.is_video:
        await _stream_song_video_with_fallback(chat_id, song, already_in_vc=True)
    else:
        await _stream_song(chat_id, song, already_in_vc=True)
    await _refresh_np_message(chat_id, song)
    return True


# Register stream-end handler
if call_py:
    @call_py.on_update(tgcalls_filters.stream_end())
    async def on_stream_end(_, update):
        chat_id = update.chat_id

        # SILENCE RACE FIX — If the silence stream ended (NTgCalls reads local
        # MP3 at CPU speed → 4-s silence consumed in ~1.5 s), ignore this
        # stream_end event.  The /play command is still resolving the song URL
        # in parallel; _stream_song will call play()/change_stream() to start
        # the real song as soon as the URL is ready.  Calling _play_next here
        # would pop an empty queue → leave_group_call → bot leaves VC → then
        # _stream_song has to rejoin 0.2 s later, causing a blip.
        if _silence_playing.get(chat_id, False):
            # BUG FIX: Use .get() not .pop() — NTgCalls fires multiple stream_end
            # events for the same silence stream (we saw 3-4 in logs: 06:41:28).
            # pop() removes the key on first hit; subsequent events see False and
            # fall through to _play_next → empty queue → bot leaves VC prematurely.
            # .get() keeps the flag set until _stream_song explicitly clears it
            # via _silence_playing.pop(chat_id, None) after change_stream returns.
            log.debug("⏩ stream_end for silence ignored — real song pending | %d", chat_id)
            return

        # PREMATURE PIPE END FIX — FIFO pipe stream_end fires within a few
        # seconds when yt-dlp's startup (PO-token, cookies, extractor init)
        # takes too long and ntgcalls' ffmpeg reader gives up on the empty pipe.
        # If stream_end fires suspiciously fast and a song is actually current,
        # treat it as a broken pipe and re-resolve a fresh stream (up to 2x).
        song = get_current(chat_id)
        started_at = _start_time.get(chat_id)
        elapsed = (time.time() - started_at) if started_at else None

        if (
            song is not None
            and elapsed is not None
            and elapsed < _PREMATURE_END_THRESHOLD
            and _stream_retries.get(chat_id, 0) < _MAX_STREAM_RETRIES
        ):
            _stream_retries[chat_id] = _stream_retries.get(chat_id, 0) + 1
            log.warning(
                "⚠️ stream_end fired after only %.1fs for chat %d — likely a "
                "broken FIFO pipe, not a finished song. Retrying (%d/%d): %s",
                elapsed, chat_id, _stream_retries[chat_id], _MAX_STREAM_RETRIES,
                song.title[:60],
            )
            if await _retry_premature_stream(chat_id, song):
                return
            log.warning("Premature-end retry failed for chat %d — advancing queue instead.", chat_id)

        _stream_retries.pop(chat_id, None)
        await _play_next(chat_id)


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
        await _play_next(chat_id, force=True)  # force=True: bypass grace period for manual skip

    elif action == "stop":
        # Cancel any in-progress early VC join task for this chat
        _jt = _active_join_tasks.pop(chat_id, None)
        if _jt and not _jt.done():
            _jt.cancel()
        clear_queue(chat_id)
        set_current(chat_id, None)
        _loop.pop(chat_id, None)
        _paused.pop(chat_id, None)
        _start_time.pop(chat_id, None)
        _stream_changed_at.pop(chat_id, None)
        _extra_cache.pop(chat_id, None)
        _stream_retries.pop(chat_id, None)
        _silence_playing.pop(chat_id, None)
        _np_message.pop(chat_id, None)
        _play_next_locks.pop(chat_id, None)
        if call_py:
            try:
                await asyncio.wait_for(call_py.leave_group_call(chat_id), timeout=5.0)
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
        _active_join_tasks[chat_id] = join_task
        def _clear_join_play(t, _cid=chat_id):
            if _active_join_tasks.get(_cid) is t:
                _active_join_tasks.pop(_cid, None)
        join_task.add_done_callback(_clear_join_play)

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
        _active_join_tasks[chat_id] = join_task
        def _clear_join_vplay(t, _cid=chat_id):
            if _active_join_tasks.get(_cid) is t:
                _active_join_tasks.pop(_cid, None)
        join_task.add_done_callback(_clear_join_vplay)

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
    await _play_next(chat_id, force=True)  # force=True: user-triggered, bypass grace period


@bot.on_message(filters.command(["stop", "end"]) & filters.group)
async def stop(_, message: Message):
    chat_id = message.chat.id
    # Cancel any in-progress early VC join so it can't re-enter the call
    # after /stop clears state (would leave _silence_playing True → future
    # stream_end events silently dropped → bot frozen muted in VC).
    _jt = _active_join_tasks.pop(chat_id, None)
    if _jt and not _jt.done():
        _jt.cancel()
    clear_queue(chat_id)
    set_current(chat_id, None)
    _loop.pop(chat_id, None)
    _paused.pop(chat_id, None)
    _start_time.pop(chat_id, None)
    _stream_changed_at.pop(chat_id, None)
    _extra_cache.pop(chat_id, None)
    _stream_retries.pop(chat_id, None)
    _silence_playing.pop(chat_id, None)
    _np_message.pop(chat_id, None)
    _play_next_locks.pop(chat_id, None)
    if call_py:
        try:
            await asyncio.wait_for(call_py.leave_group_call(chat_id), timeout=5.0)
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
