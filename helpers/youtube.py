"""
YouTube search and stream resolution.

Cookies are optional. They can help with age-restricted videos, but making them
mandatory breaks normal playback and turns a missing optional setting into a
misleading "token" error.
"""

import os
import re
import errno
import asyncio
import aiohttp
import fcntl
import logging
import time
import tempfile
import json
import shutil
import sys
import subprocess
import threading
import urllib.request
from urllib.parse import quote_plus, urljoin, urlsplit
from concurrent.futures import ThreadPoolExecutor

# The build hook installs the bgutil yt-dlp plugin under vendor/. Add that
# namespace before importing yt-dlp so plugin discovery also works when this
# module is imported outside the main process.
_BGUTIL_PLUGIN_DIR = os.path.join(
    os.path.dirname(os.path.dirname(os.path.abspath(__file__))),
    "vendor", "bgutil-ytdlp-pot-provider", "plugin",
)
if os.path.isdir(_BGUTIL_PLUGIN_DIR) and _BGUTIL_PLUGIN_DIR not in sys.path:
    sys.path.insert(0, _BGUTIL_PLUGIN_DIR)

import yt_dlp

log = logging.getLogger("ApexBot.youtube")
# SPEED FIX: workers 4→8 — parallel yt-dlp extractions + downloads
# ⚡ SPEED FIX: 8→16 workers — more parallel yt-dlp searches + downloads.
# With 8 workers, simultaneous /play from multiple users queued behind each
# other (each yt-dlp search = 1 thread for ~1.5s). 16 eliminates that bottleneck.
_exec = ThreadPoolExecutor(max_workers=16)
# ⚡ PLAY-RACE FIX: _PIPE_SLOT_TIMEOUT is how long the writer thread waits
# to acquire a download slot before giving up.  Previously _PIPE_CONNECT_TIMEOUT
# (15 s) was used for BOTH the slot wait AND the FIFO-reader-connection wait.
# With _PIPE_MAX_CONCURRENT=3, a 4th simultaneous /play command had to wait the
# full 15 s → users observed a "10–15 s delay before song starts" and the bot
# appearing to "play at 2× speed" to catch up.  Fix: fail fast (5 s) on the
# slot so the premature-end retry kicks in sooner, and raise the concurrent
# limit so slots are rarely exhausted in the first place.
_PIPE_SLOT_TIMEOUT   = 5.0   # seconds to wait for a free download slot
_PIPE_CONNECT_TIMEOUT = 15.0  # seconds to wait for the FIFO reader to connect
# ⚡ PLAY-RACE FIX: 3 → 8 concurrent pipe downloads.
# Each pipe holds a slot while yt-dlp downloads (typically 30–90 s per song).
# With 3 slots a bot serving >3 groups simultaneously always exhausted the pool,
# causing every 4th+ /play to stall 15 s.  8 covers typical multi-group usage.
_PIPE_MAX_CONCURRENT = 8
_pipe_slots = threading.BoundedSemaphore(_PIPE_MAX_CONCURRENT)
_pipe_states: dict[str, dict] = {}
_pipe_states_lock = threading.Lock()
# A FIFO is intentionally optimistic: PyTgCalls can accept the path before
# yt-dlp has finished its handshake. If yt-dlp then exits before producing a
# complete stream, do not keep retrying the same broken FIFO forever. The
# reliable local-file downloader is used for that URL on the next attempt.
_pipe_failures: dict[str, int] = {}
_BGUTIL_STATUS_LOGGED = False

# ── Cloud-host CDN-block detection ───────────────────────────────
# On Heroku/Railway/Render/Fly.io the YouTube CDN (googlevideo.com) is
# IP-blocked. Trying to stream from a CDN URL always causes an immediate
# EOF and a 12-second silence before the retry. Detect cloud hosts at
# startup and skip CDN probing entirely — go straight to Invidious/download.
_ON_CLOUD_HOST: bool = bool(
    os.environ.get("DYNO")                  # Heroku
    or os.environ.get("RAILWAY_ENVIRONMENT")  # Railway
    or os.environ.get("RENDER_SERVICE_ID")    # Render
    or os.environ.get("FLY_APP_NAME")         # Fly.io
    or os.environ.get("K_SERVICE")            # Google Cloud Run
    or os.environ.get("WEBSITE_INSTANCE_ID")  # Azure App Service
)

# Runtime flag — flipped True on first premature stream end (CDN EOF).
# Starts True on known cloud hosts so the very first play skips CDN.
_cdn_blocked: bool = _ON_CLOUD_HOST


def mark_cdn_blocked() -> None:
    """Call when a CDN stream fails (premature end) so future plays skip CDN."""
    global _cdn_blocked
    if not _cdn_blocked:
        log.info(
            "🚫 CDN stream premature EOF — switching to Invidious/download-first "
            "mode for all future plays on this host."
        )
        _cdn_blocked = True


def is_cdn_blocked() -> bool:
    """Return True when CDN streaming is known-blocked (cloud host or after a premature EOF)."""
    return _cdn_blocked


def mark_pipe_failed(url: str) -> None:
    """
    Mark a URL as having failed FIFO pipe streaming so the next call to
    get_stream() / _resolve_stream() for this URL takes the local-download
    path instead of retrying the FIFO.

    Called from play.py when stream_end fires after < 30 s of playback
    (premature end caused by yt-dlp stalling on DASH segment 2+ over
    Heroku's blocked CDN).  Pre-incrementing here ensures the retry
    happens even if the writer-thread exception handler hasn't run yet.
    """
    with _pipe_states_lock:
        _pipe_failures[url] = max(1, _pipe_failures.get(url, 0) + 1)
    log.debug("📌 Pipe marked failed — will retry via local-dl | %s", url[:80])


def had_pipe_failure(url: str) -> bool:
    """
    Return True if this URL has had at least one FIFO pipe failure.

    Used by play.py's pipe-failure retry logic to distinguish between:
      a) Song ended naturally (no pipe failure → don't retry)
      b) Pipe failed mid-stream (pipe failure recorded → retry via local-dl)

    This lets the retry fire on cloud hosts (where is_cdn_blocked() is True)
    when a pipe was used for the first attempt and then failed — which is the
    exact scenario that caused "Queue Finished" immediately after searching.
    """
    return _pipe_failures.get(url, 0) >= 1

# ── Cache ─────────────────────────────────────────────────────────
# tuple: (media_url, audio_url, duration, http_headers, expires_at)
_stream_cache: dict[tuple[str, bool], tuple[str, str | None, int, dict, float]] = {}
_search_cache: dict[tuple[str, bool], tuple[dict, float]] = {}
_stream_tasks: dict[tuple[str, bool], asyncio.Task] = {}
# YouTube CDN URLs are signed and should not be kept for a full hour.
STREAM_TTL = 900
SEARCH_TTL = 1800


def _url_expiry_ttl(cdn_url: str, default_ttl: float = STREAM_TTL) -> float:
    """Parse expire= from a googlevideo CDN URL and return a safe cache TTL.

    YouTube CDN URLs carry an expire= Unix timestamp that can be much shorter
    than STREAM_TTL.  Using a fixed 900-s TTL lets stale (already-expired) URLs
    sit in the cache, causing ntgcalls shell_reader to hit EOF immediately and
    the bot to leave VC after ~1 second.
    """
    import re as _re
    m = _re.search(r"[?&]expire=(\d+)", cdn_url)
    if not m:
        return default_ttl
    expire_epoch = int(m.group(1))
    # Compare against wall-clock; cache itself uses monotonic time.
    ttl_from_url = expire_epoch - time.time() - 120  # 2-min safety buffer
    if ttl_from_url <= 0:
        return 0.0  # URL already expired — must not be cached
    return min(default_ttl, ttl_from_url)


def _stream_key(url: str, is_video: bool) -> tuple[str, bool]:
    return url.strip(), is_video


def _cached_stream(
    url: str,
    is_video: bool,
) -> tuple[str, str | None, int, dict] | None:
    e = _stream_cache.get(_stream_key(url, is_video))
    if e and time.monotonic() < e[4]:
        return e[0], e[1], e[2], e[3]
    _stream_cache.pop(_stream_key(url, is_video), None)
    return None


def _cache_stream(
    url: str,
    is_video: bool,
    su: str,
    audio_url: str | None,
    dur: int,
    headers: dict | None = None,
):
    # Respect the URL's own expiry so we never serve an already-expired CDN URL.
    ttl = _url_expiry_ttl(su) if su else STREAM_TTL
    if ttl <= 0:
        log.debug("Skipping cache for expired/expiring CDN URL: %s", (su or "")[:80])
        return
    _stream_cache[_stream_key(url, is_video)] = (
        su,
        audio_url,
        dur,
        headers or {},
        time.monotonic() + ttl,
    )


def clear_cache_for_url(url: str, is_video: bool = False):
    """Remove a URL from the stream cache so the next play fetches a fresh URL."""
    _stream_cache.pop(_stream_key(url, is_video), None)
    _stream_cache.pop(_stream_key(url.strip(), is_video), None)


def _cached_search(q: str, is_video: bool) -> dict | None:
    e = _search_cache.get((q.strip().lower(), is_video))
    if e and time.monotonic() < e[1]:
        return e[0]
    _search_cache.pop((q.strip().lower(), is_video), None)
    return None


def _cache_search(q: str, is_video: bool, info: dict):
    _search_cache[(q.strip().lower(), is_video)] = (
        info,
        time.monotonic() + SEARCH_TTL,
    )


# ── Cookie setup — LAZY (no crash at startup) ─────────────────────
_COOKIE_FILE: str | None = None
_COOKIE_CHECKED: bool = False


def _resolve_cookie_file() -> str | None:
    """Lazy cookie resolution — called on first use, not at import time."""
    global _COOKIE_FILE, _COOKIE_CHECKED
    if _COOKIE_CHECKED:
        return _COOKIE_FILE
    _COOKIE_CHECKED = True

    # 1. Local cookies/youtube.txt
    local = os.path.join(
        os.path.dirname(os.path.dirname(os.path.abspath(__file__))),
        "cookies", "youtube.txt"
    )
    if os.path.isfile(local):
        log.info(f"🍪 Cookies loaded from file: {local}")
        _COOKIE_FILE = local
        return _COOKIE_FILE

    # 2. YOUTUBE_COOKIES env var
    raw = os.environ.get("YOUTUBE_COOKIES", "").strip()
    if raw:
        try:
            tmp = tempfile.NamedTemporaryFile(
                mode="w", suffix=".txt", delete=False, prefix="yt_cookies_"
            )
            if not raw.startswith("# Netscape HTTP Cookie File"):
                tmp.write("# Netscape HTTP Cookie File\n\n")
            tmp.write(raw)
            tmp.flush()
            tmp.close()
            log.info(f"🍪 Cookies from env → {tmp.name}")
            _COOKIE_FILE = tmp.name
            return _COOKIE_FILE
        except Exception as e:
            log.warning(f"Cookie env parse failed: {e}")

    # COOKIES REQUIRED (enforced): cookies ke bina YouTube cloud IPs pe 403/sign-in wall deta hai.
    # cookies/youtube.txt daalo ya YOUTUBE_COOKIES env var set karo (Netscape format).
    raise RuntimeError(
        "🍪 **YouTube Cookies nahi mili!**\n\n"
        "Bot ko cookies chahiye YouTube se play karne ke liye.\n\n"
        "**Setup:**\n"
        "1️⃣  Browser mein YouTube pe login karo\n"
        "2️⃣  Extension use karo: _'Get cookies.txt LOCALLY'_ (Chrome/Firefox)\n"
        "3️⃣  Export karo `youtube.com` ke cookies\n"
        "4️⃣  `cookies/youtube.txt` mein paste karo ya `YOUTUBE_COOKIES` env var set karo\n\n"
        "_Bina cookies ke Heroku/cloud IPs pe YouTube block ho jata hai._"
    )


# ── yt-dlp options ───────────────────────────────────────────────
def _opts(
    audio_only: bool = True,
    fmt: str | None = None,
    player_client: list | None = None,
    skip_cookies: bool = False,
    ignore_no_formats: bool = False,
    skip_webpage: bool = False,
) -> dict:
    # skip_cookies=True → mobile client attempts (mobile clients don't use
    # browser session cookies — mixing them causes auth conflicts on YouTube).
    cookie = None if skip_cookies else _resolve_cookie_file()

    # Do not force the old mobile clients here. YouTube now gates many of
    # their formats behind PO tokens, while current yt-dlp knows how to pick
    # the best compatible clients when "default" is used.
    default_fmt = (
        "bestaudio/best"
        if audio_only
        else "bestvideo[height<=1080][vcodec!=none]+bestaudio/best[height<=1080][vcodec!=none][acodec!=none]/best[height<=1080]/best"
    )
    clients = player_client or ["default"]
    extractor_args: dict = {
        "player_client": clients,
    }
    provider_args: dict = {
        "youtube": extractor_args,
    }
    bgutil_server = _bgutil_server_home()
    bgutil_script = os.path.join(bgutil_server, "src", "generate_once.ts")
    if os.path.isfile(bgutil_script):
        provider_args["youtubepot-bgutilscript"] = {
            "server_home": [bgutil_server],
        }
        global _BGUTIL_STATUS_LOGGED
        if not _BGUTIL_STATUS_LOGGED:
            log.info("✅ bgutil PO-token provider configured: %s", bgutil_server)
            _BGUTIL_STATUS_LOGGED = True
    elif not _BGUTIL_STATUS_LOGGED:
        log.warning(
            "⚠️ bgutil PO-token provider is not installed at %s; "
            "YouTube cloud extraction will use built-in providers only",
            bgutil_server,
        )
        _BGUTIL_STATUS_LOGGED = True

    opts: dict = {
        "format":                   fmt if fmt is not None else default_fmt,
        "quiet":                    True,
        "no_warnings":              True,
        "noplaylist":               True,
        "geo_bypass":               True,
        "geo_bypass_country":       "US",    # spoof US location — helps with regional blocks
        "check_formats":            False,   # don't pre-verify URL reachability
        "allow_unplayable_formats": False,
        # socket_timeout: bina iske yt-dlp cloud IPs pe indefinitely hang karta tha.
        # 20s per attempt reasonable hai; combos ka loop overall timeout control karta hai.
        "socket_timeout":           6,   # SPEED FIX: fail-fast per combo; overall cap = wait_for() timeout
        # yt-dlp's YouTube extractor needs the external JS challenge solver.
        # The build hook installs Deno when the host does not provide it.
        "js_runtimes":              {"deno": {"path": _deno_path()}},
        "remote_components":         ["ejs:github"],
        "extractor_args":            provider_args,
        "http_headers": {
            "User-Agent": (
                "Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
                "AppleWebKit/537.36 (KHTML, like Gecko) "
                "Chrome/125.0.0.0 Safari/537.36"
            ),
            "Referer": "https://www.youtube.com/",
        },
    }
    if ignore_no_formats:
        opts["ignore_no_formats_error"] = True
        opts["allow_unplayable_formats"] = True
    if cookie:
        opts["cookiefile"] = cookie
    # YTDLP_PROXY: set in Heroku Config Vars to route yt-dlp through a proxy.
    # Validate the complete URL, not just its prefix. A value such as
    # ``socks5://host:1080socks5:`` starts with a valid scheme but makes the
    # requests backend fail with "Port could not be cast to integer".
    proxy = _proxy_from_environment()
    if proxy is not None:
        # An empty proxy explicitly disables inherited HTTP(S)_PROXY values.
        opts["proxy"] = proxy
    return opts


def _bgutil_server_home() -> str:
    """Return the build-bundled bgutil provider server directory."""
    configured_root = os.environ.get("YTDLP_BGUTIL_HOME", "").strip()
    if configured_root:
        configured_server = os.path.join(configured_root, "server")
        if os.path.isdir(configured_server):
            return configured_server
    return os.path.join(
        os.path.dirname(os.path.dirname(os.path.abspath(__file__))),
        "vendor", "bgutil-ytdlp-pot-provider", "server",
    )


def _deno_path() -> str:
    """Return the bundled/system Deno path used by yt-dlp EJS."""
    configured = os.environ.get("DENO_PATH", "").strip()
    if configured and os.path.isfile(configured):
        return configured

    bundled = os.path.join(
        os.path.dirname(os.path.dirname(os.path.abspath(__file__))),
        "vendor", "deno", "bin", "deno",
    )
    if os.path.isfile(bundled):
        return bundled

    return shutil.which("deno") or "deno"


def _valid_proxy_url(value: str) -> str | None:
    """Return a usable proxy URL, or None for a malformed value.

    yt-dlp eventually hands this value to urllib3/requests, whose error for a
    bad port is cryptic and otherwise aborts every search attempt. Validate it
    here so a bad optional proxy can never take down playback.
    """
    value = value.strip()
    if not value:
        return ""

    try:
        parsed = urlsplit(value)
        if parsed.scheme.lower() not in {
            "http", "https", "socks4", "socks4a", "socks5", "socks5h"
        }:
            return None
        if not parsed.hostname or parsed.port is None:
            return None
        if not 1 <= parsed.port <= 65535:
            return None
    except ValueError:
        return None
    return value


def _proxy_from_environment() -> str | None:
    """Read and validate the optional yt-dlp proxy configuration.

    ``YTDLP_PROXY`` is the app-specific setting. If it is absent, leave valid
    system proxy settings alone, but explicitly disable malformed inherited
    values because requests may discover them automatically.
    """
    configured = os.environ.get("YTDLP_PROXY", "").strip()
    if configured:
        proxy = _valid_proxy_url(configured)
        if proxy is None:
            log.error(
                "❌ YTDLP_PROXY invalid hai; direct connection use ho rahi hai. "
                "Format: socks5://host:port ya http://host:port"
            )
            return ""
        return proxy

    for name in (
        "HTTPS_PROXY",
        "https_proxy",
        "HTTP_PROXY",
        "http_proxy",
        "ALL_PROXY",
        "all_proxy",
    ):
        inherited = os.environ.get(name, "").strip()
        if inherited and _valid_proxy_url(inherited) is None:
            log.error(
                "❌ Inherited proxy setting %s invalid hai; yt-dlp ke liye "
                "direct connection use ho rahi hai.",
                name,
            )
            return ""
    return None


# ── URL validation ────────────────────────────────────────────────
def _is_streamable_url(url: str) -> bool:
    """
    Return True only if 'url' is a direct CDN media URL that ffmpeg/ntgcalls
    can actually pipe.

    fmt=None + ignore_no_formats sometimes returns:
      - youtube.com/watch?v=... (HTML page → 0 bytes → ntgcalls EOF)
      - youtu.be/... (redirect, not media)
      - HLS .m3u8 manifests (ntgcalls shell_reader can't follow segments)
      - DASH .mpd manifests (same problem)

    All of the above cause "Reached end of the file" in shell_reader.cpp
    and the bot leaving VC within 1 second of joining.
    """
    if not url:
        return False
    bad = (
        "youtube.com/watch",
        "youtu.be/",
        "youtube.com/shorts",
        "youtube.com/embed",
        ".m3u8",
        ".mpd",
    )
    if any(b in url for b in bad):
        return False
    # Real YouTube audio/video CDN URLs always contain googlevideo.com
    # or the videoplayback path. Accept those; reject everything else.
    good = ("googlevideo.com", "videoplayback")
    return any(g in url for g in good)


# ── Sync extractors ───────────────────────────────────────────────
def _pick_urls(info: dict, audio_only: bool) -> tuple[str, str | None]:
    """Extract media and optional separate audio URLs from yt-dlp output."""
    fmts = info.get("formats") or []
    requested = info.get("requested_formats") or []

    if audio_only:
        # Do not accidentally return a combined video URL for /play.
        # Prefer audio-only streams sorted by bitrate
        af = [f for f in fmts if f.get("acodec") != "none" and f.get("vcodec") == "none"]
        if af:
            return (
                max(af, key=lambda f: f.get("abr") or f.get("tbr") or 0).get("url", ""),
                None,
            )
    else:
        # yt-dlp returns requested_formats for bestvideo+bestaudio. PyTgCalls
        # can consume those as media_path + audio_path and FFmpeg combines
        # them while preserving the signed request headers.
        if requested:
            video = next(
                (f for f in requested if f.get("vcodec") != "none" and f.get("url")),
                None,
            )
            audio = next(
                (f for f in requested if f.get("acodec") != "none" and f.get("url")),
                None,
            )
            if video and audio:
                return video["url"], audio["url"]

        # If only progressive formats are available, use one URL.
        av = [
            f for f in fmts
            if f.get("acodec") != "none" and f.get("vcodec") != "none"
        ]
        if av:
            best = max(
                av,
                key=lambda f: (
                    f.get("height") or 0,
                    f.get("fps") or 0,
                    f.get("tbr") or 0,
                ),
            )
            return best.get("url", ""), None

    # Direct extraction results do not always include a formats list.
    # NEVER return manifest_url here — ntgcalls shell_reader can't follow
    # HLS/DASH segment manifests and immediately hits EOF ("Reached end of file").
    url = info.get("url") or ""
    if url and (
        audio_only
        or (info.get("acodec", "none") != "none" and info.get("vcodec", "none") != "none")
    ):
        return url, None
    best = sorted(fmts, key=lambda f: f.get("quality") or f.get("tbr") or 0, reverse=True)
    return (best[0].get("url", ""), None) if best else ("", None)


def _pick_url(info: dict, audio_only: bool) -> str:
    """Compatibility helper for search metadata."""
    return _pick_urls(info, audio_only)[0]


def _extract_sync(url: str, audio_only: bool = True) -> dict | None:
    """Try (format, clients) combos in priority order — always with cookies.

    When a cloud/Heroku IP is flagged by YouTube, ALL clients (web + mobile)
    return "Sign in to confirm". yt-dlp extracts visitor_data + auth tokens
    from the cookiefile and injects them into mobile API calls too — so
    always pass cookies. Never skip them.

    "ba/b" = yt-dlp shorthand: best-audio / best — no ext filter, accepts
    any codec the client returns (more permissive than [ext=m4a]).
    """
    cookie = _resolve_cookie_file()
    # fmt=None means "let yt-dlp decide — no restriction"
    # NOTE: fmt=None + ignore_no_formats combos often return storyboard thumbnail
    # URLs (i.ytimg.com) on cloud/Heroku IPs. _is_streamable_url() rejects those.
    #
    # Combo tuple: (format_selector, player_clients, ignore_no_formats, skip_webpage)
    # Keep the list short: every failed client adds latency and can trigger
    # more YouTube rate limiting. The default client set is maintained by
    # yt-dlp and is the most reliable option for current YouTube changes.
    # COOKIE PRIORITY FIX: jab cookies available hain, cookie-optimised combos
    # PEHLE try karo. "web" client browser session cookies se best authenticate
    # karta hai aur age-restricted / sign-in-wall videos ko unlock karta hai.
    # Cookie combos ke baad standard combos as fallback.
    if audio_only:
        combos: list[tuple[str | None, list, bool, bool]] = []
        if cookie:
            # Cookie-first: web client authenticates fully with browser cookies
            combos += [
                ("bestaudio[ext=m4a]/bestaudio/best", ["web"],           False, False),
                ("bestaudio/best",                    ["web", "default"], False, False),
            ]
        else:
            # No cookies path (dead code — _resolve_cookie_file raises if missing).
            # Kept as safety fallback only.
            combos += [
                ("bestaudio/best", ["default"],      False, False),
                ("bestaudio/best", ["web_embedded"], False, False),
                (None,             ["default"],      True,  False),
            ]
    else:
        combos = []
        if cookie:
            # Cookie-first for video: web client with cookies unlocks HD streams
            combos += [
                (
                    "bestvideo[height<=1080][vcodec!=none]+bestaudio/best[height<=1080]/best[height<=1080]/best",
                    ["web"], False, False,
                ),
            ]
        else:
            # No cookies fallback (dead code in practice).
            combos += [
                # 1080p first — best quality for vplay/movies
                ("bestvideo[height<=1080][vcodec!=none]+bestaudio/best[height<=1080][vcodec!=none][acodec!=none]/best[height<=1080]/best", ["default"],      False, False),
                ("bestvideo[height<=1080][vcodec!=none]+bestaudio/best[height<=1080][vcodec!=none][acodec!=none]/best[height<=1080]/best", ["web_embedded"], False, False),
                (None,                                                                                                                     ["default"],      True,  False),
            ]

    _RETRYABLE = (
        "Requested format is not available",
        "format is not available",
        "No video formats found",
        "Sign in to confirm",
        "This video is not available",
        "HTTP Error 403",
        "HTTP Error 429",
        "requires payment",
        "members-only",
    )

    for fmt, clients, ignore_no_fmt, skip_wp in combos:
        try:
            opts = _opts(
                audio_only,
                fmt=fmt,
                player_client=clients,
                skip_cookies=False,
                ignore_no_formats=ignore_no_fmt,
                skip_webpage=skip_wp,
            )
            with yt_dlp.YoutubeDL(opts) as ydl:
                info = ydl.extract_info(url, download=False)
                if info:
                    # Collect all candidate URLs (never manifest_url — ntgcalls
                    # shell_reader can't follow HLS/DASH segments and hits EOF).
                    candidate_urls = [
                        u for u in (
                            [info.get("url")]
                            + [f.get("url") for f in (info.get("formats") or [])]
                        ) if u
                    ]
                    url_ok = bool(candidate_urls)

                    if url_ok and ignore_no_fmt:
                        # fmt=None last-resort combos sometimes return HTML
                        # redirects (youtube.com/watch) or manifest URLs —
                        # these cause ntgcalls "Reached end of the file" and
                        # the bot leaving VC in 1 second.
                        # Only accept if at least one URL is a real CDN URL.
                        cdn_urls = [u for u in candidate_urls if _is_streamable_url(u)]
                        if not cdn_urls:
                            log.warning(
                                f"fmt=None returned no CDN URL (got: {candidate_urls[0][:80]!r}) "
                                f"— skipping clients={clients}"
                            )
                            continue   # try next combo

                    if url_ok:
                        log.info(f"✅ yt-dlp OK | fmt={fmt!r} clients={clients} | {url[:55]}")
                        return info
                    log.warning(f"yt-dlp returned info but no URL | fmt={fmt!r} clients={clients}")
        except yt_dlp.utils.DownloadError as e:
            err = str(e)
            if any(r in err for r in _RETRYABLE):
                log.warning(f"Retrying: fmt={fmt!r} clients={clients} — {err[:80]}")
                continue
            log.error(f"yt-dlp fatal error: {e}")
            return None
        except RuntimeError:
            raise
        except Exception as e:
            log.error(f"yt-dlp unexpected error: {e}")
            return None

    log.error(f"❌ All formats exhausted for {url[:60]}")
    if not cookie:
        log.error(
            "➡️  FIX: Export YouTube cookies from Chrome/Firefox (Netscape format) "
            "and set as YOUTUBE_COOKIES env var in Heroku dashboard → Settings → Config Vars."
        )
    else:
        log.error(
            "➡️  Cookies are set but YouTube is still blocking this IP. "
            "Try refreshing cookies (re-export from browser while logged into YouTube)."
        )
    return None


def _search_sync(query: str, audio_only: bool = True) -> dict | None:
    if query.startswith("http://") or query.startswith("https://"):
        return _extract_sync(query, audio_only)

    # ── Fast path: extract_flat search (just metadata, no stream URL) ──────
    # FIX: noplaylist=False is REQUIRED here.
    # _opts() sets noplaylist=True (correct for direct video URLs), but
    # ytsearch1: returns a "SearchResultsPlaylist" internally. With
    # noplaylist=True, yt-dlp 2025.x refuses to process it and returns
    # nothing — causing silent "Nahi mila" errors with no log entry.
    try:
        search_opts = {
            **_opts(audio_only, player_client=["default"]),
            "extract_flat": "in_playlist",
            "check_formats": False,
            "noplaylist":    False,   # CRITICAL FIX: allow ytsearch playlist processing
            # ⚡ SPEED FIX: flat search ko 4s mein fail-fast karo — yt-dlp default
            # socket_timeout 6s hai jo _opts() se aata hai. Search ke liye 4s kaafi
            # hai; agar 4s mein koi response nahi to Invidious async result use hoga.
            "socket_timeout": 4,
        }
        with yt_dlp.YoutubeDL(search_opts) as ydl:
            info = ydl.extract_info(f"ytsearch1:{query}", download=False)
            if info and "entries" in info and info["entries"]:
                entry = info["entries"][0]
                # Ensure webpage_url is set so _do_play_inner can fetch stream later
                if entry.get("url") and not entry.get("webpage_url"):
                    entry["webpage_url"] = entry["url"]
                vid_id = entry.get("id", "")
                if not entry.get("webpage_url") and vid_id:
                    entry["webpage_url"] = f"https://www.youtube.com/watch?v={vid_id}"
                log.info(f"✅ Search OK (flat): {entry.get('title', query)[:50]!r}")
                return entry
            log.warning(f"⚠️  extract_flat returned no entries for: {query[:50]!r}")
    except yt_dlp.utils.DownloadError as e:
        log.error(f"yt-dlp flat-search error: {e}")
    except Exception as e:
        log.error(f"yt-dlp flat-search error: {e}")

    # ── Fallback: full extraction search (slower but more reliable) ─────────
    # Used when extract_flat returns nothing (e.g. regional blocks, sign-in walls).
    log.info(f"🔄 Trying full-extraction fallback search for: {query[:50]!r}")
    try:
        fallback_opts = {
            **_opts(audio_only, player_client=["default"]),
            "noplaylist": False,   # same fix applies here
        }
        with yt_dlp.YoutubeDL(fallback_opts) as ydl:
            info = ydl.extract_info(f"ytsearch1:{query}", download=False)
            if info and "entries" in info and info["entries"]:
                entry = info["entries"][0]
                vid_id = entry.get("id", "")
                if entry.get("url") and not entry.get("webpage_url"):
                    entry["webpage_url"] = entry["url"]
                if not entry.get("webpage_url") and vid_id:
                    entry["webpage_url"] = f"https://www.youtube.com/watch?v={vid_id}"
                log.info(f"✅ Search OK (fallback): {entry.get('title', query)[:50]!r}")
                return entry
            # Last resort: info itself might be the video entry
            if info and info.get("id"):
                return info
    except yt_dlp.utils.DownloadError as e:
        log.error(f"yt-dlp fallback-search error: {e}")
    except Exception as e:
        log.error(f"yt-dlp fallback-search error: {e}")

    # ── Last resort: search through Invidious ──────────────────────────────
    # A blocked YouTube/Heroku IP can make yt-dlp fail before it even gets a
    # video ID. Invidious can still return the ID, after which get_stream()
    # uses its proxied audio fallback below.
    fallback_entry = _invidious_search_sync(query)
    if fallback_entry:
        log.info(
            "✅ Search OK (Invidious fallback): %r",
            fallback_entry.get("title", query)[:50],
        )
        return fallback_entry

    log.error(f"❌ All search methods failed for: {query[:60]!r}")
    return None


def is_playlist_url(url: str) -> bool:
    """Return True for YouTube playlist URLs, not ordinary video links."""
    if not url or "youtube.com" not in url.lower():
        return False
    return bool(re.search(r"(?:[?&])list=[A-Za-z0-9_-]+", url))


def _resolve_playlist_sync(
    url: str,
    is_video: bool = False,
    max_results: int = 50,
) -> list[dict]:
    """Resolve a YouTube playlist to lightweight playable entries."""
    if not is_playlist_url(url):
        return []
    cookie = _resolve_cookie_file()
    opts = {
        **_opts(not is_video, player_client=["default"]),
        "extract_flat": "in_playlist",
        "noplaylist": False,
        "playlistend": max(1, min(max_results, 100)),
        "skip_download": True,
    }
    if cookie:
        opts["cookiefile"] = cookie
    try:
        with yt_dlp.YoutubeDL(opts) as ydl:
            info = ydl.extract_info(url, download=False)
        entries = []
        for entry in (info or {}).get("entries") or []:
            if not entry:
                continue
            video_id = entry.get("id") or ""
            webpage_url = (
                entry.get("webpage_url")
                or entry.get("url")
                or (f"https://www.youtube.com/watch?v={video_id}" if video_id else "")
            )
            if not webpage_url:
                continue
            entries.append({
                "id": video_id,
                "title": (entry.get("title") or "Unknown")[:100],
                "webpage_url": webpage_url,
                "duration": int(entry.get("duration") or 0),
                "thumbnail": entry.get("thumbnail") or "",
                "uploader": entry.get("uploader") or entry.get("channel") or "",
            })
        log.info("✅ Playlist resolved | entries=%d | url=%s", len(entries), url[:80])
        return entries
    except Exception as exc:
        log.warning("Playlist resolve failed for %s: %s", url[:80], exc)
        return []


async def resolve_playlist(
    url: str,
    is_video: bool = False,
    max_results: int = 50,
) -> list[dict]:
    """Async playlist resolver used by /play and /vplay."""
    loop = asyncio.get_running_loop()
    return await loop.run_in_executor(
        _exec, _resolve_playlist_sync, url, is_video, max_results
    )


def _invidious_search_sync(query: str) -> dict | None:
    """Search public Invidious instances without inheriting bad proxy env."""
    request_headers = {"User-Agent": "Mozilla/5.0 (compatible; ApexBot/1.0)"}
    encoded_query = quote_plus(query)

    for instance in _INVIDIOUS_INSTANCES:
        try:
            endpoint = f"{instance}/api/v1/search?q={encoded_query}&type=video"
            request = urllib.request.Request(endpoint, headers=request_headers)
            # ProxyHandler({}) is intentional: a malformed HTTP(S)_PROXY
            # setting must not break this emergency search path.
            opener = urllib.request.build_opener(urllib.request.ProxyHandler({}))
            with opener.open(request, timeout=10) as response:
                if response.status != 200:
                    continue
                items = json.loads(response.read().decode("utf-8"))

            result = next(
                (
                    item for item in items
                    if item.get("type") == "video" and item.get("videoId")
                ),
                None,
            )
            if not result:
                continue

            thumbnails = result.get("videoThumbnails") or []
            thumbnail = (
                next(
                    (
                        item.get("url", "")
                        for item in thumbnails
                        if item.get("quality") in {"medium", "high"}
                    ),
                    "",
                )
                or (thumbnails[0].get("url", "") if thumbnails else "")
            )
            video_id = result["videoId"]
            return {
                "id": video_id,
                "title": result.get("title") or query,
                "webpage_url": f"https://www.youtube.com/watch?v={video_id}",
                "duration": int(result.get("lengthSeconds", 0) or 0),
                "thumbnail": thumbnail,
                "uploader": result.get("author") or "",
                "view_count": int(result.get("viewCount", 0) or 0),
            }
        except Exception as exc:
            log.debug("Invidious search failed (%s): %s", instance, exc)
            continue
    return None


# ── Public async API ──────────────────────────────────────────────

async def _invidious_search_async(query: str, timeout: float = 3.0) -> dict | None:
    """
    ⚡ SPEED FIX: Async Invidious search — races ALL instances in parallel.

    WHY THIS IS FAST:
    - Pure HTTP GET to a public Invidious API — no subprocess, no yt-dlp overhead.
    - aiohttp: non-blocking, runs on the event loop directly (no thread pool).
    - Multiple instances probed simultaneously; whichever responds first wins.
    - Typical latency: 200-500 ms vs yt-dlp flat-search ~1-1.5 s.

    This is run in PARALLEL with yt-dlp flat search so whichever completes
    first provides the song metadata.  Invidious result has no CDN URL — the
    stream is resolved later by get_stream() / _start_pipe_download() as usual.
    """
    encoded_query = quote_plus(query)
    # Request only the fields we need — reduces response size → faster parse.
    fields = "videoId,title,lengthSeconds,author,viewCount,videoThumbnails,type"
    headers = {"User-Agent": "Mozilla/5.0 (compatible; ApexBot/2.0)"}

    async def _probe(instance: str) -> dict | None:
        url = (
            f"{instance}/api/v1/search"
            f"?q={encoded_query}&type=video&fields={fields}"
        )
        try:
            async with aiohttp.ClientSession(
                timeout=aiohttp.ClientTimeout(total=timeout)
            ) as s:
                async with s.get(url, headers=headers) as r:
                    if r.status != 200:
                        return None
                    items = await r.json(content_type=None)
            result = next(
                (i for i in items if i.get("type") == "video" and i.get("videoId")),
                None,
            )
            if not result:
                return None
            video_id = result["videoId"]
            thumbs = result.get("videoThumbnails") or []
            thumbnail = (
                next(
                    (t.get("url", "") for t in thumbs
                     if t.get("quality") in {"medium", "high"}),
                    thumbs[0].get("url", "") if thumbs else "",
                )
            )
            return {
                "id":          video_id,
                "title":       result.get("title") or query,
                "webpage_url": f"https://www.youtube.com/watch?v={video_id}",
                "duration":    int(result.get("lengthSeconds", 0) or 0),
                "thumbnail":   thumbnail,
                "uploader":    result.get("author") or "",
                "view_count":  int(result.get("viewCount", 0) or 0),
                "url":         "",          # no CDN URL — stream resolved later
                "http_headers": {},
            }
        except Exception:
            return None

    # Race ALL instances simultaneously — fastest datacenter wins.
    tasks = [asyncio.create_task(_probe(inst)) for inst in _INVIDIOUS_INSTANCES]
    result = None
    try:
        pending = set(tasks)
        while pending:
            done, pending = await asyncio.wait(
                pending, return_when=asyncio.FIRST_COMPLETED
            )
            for t in done:
                try:
                    r = t.result()
                    if r and r.get("webpage_url"):
                        result = r
                        break
                except Exception:
                    pass
            if result:
                break
    finally:
        for t in tasks:
            if not t.done():
                t.cancel()
        await asyncio.gather(*tasks, return_exceptions=True)

    if result:
        log.info(
            "⚡ Invidious async search WIN | %r | %s",
            query[:40], result.get("title", "")[:50],
        )
    return result


def _build_search_result(info: dict, query: str, is_video: bool) -> dict:
    """Normalize a raw yt-dlp or Invidious search result into the standard dict."""
    http_headers = info.get("http_headers") or {}
    vid_id = info.get("id", "")
    webpage_url = (
        info.get("webpage_url")
        or info.get("original_url")
        or (f"https://www.youtube.com/watch?v={vid_id}" if vid_id else "")
    )
    # Only yt-dlp results carry a CDN URL; Invidious results have url=""
    raw_url = info.get("url") or _pick_url(info, not is_video) or ""
    is_cdn_url = bool(
        raw_url
        and "youtube.com/watch" not in raw_url
        and "youtu.be/" not in raw_url
    )
    return {
        "title":        info.get("title", query)[:100],
        "url":          raw_url if is_cdn_url else "",
        "duration":     info.get("duration", 0) or 0,
        "thumbnail":    info.get("thumbnail") or "",
        "webpage_url":  webpage_url,
        "uploader":     info.get("uploader") or info.get("channel") or "",
        "view_count":   info.get("view_count") or 0,
        "http_headers": http_headers,
    }


async def search_song(query: str, is_video: bool = False) -> dict | None:
    """
    Search for a song/video — returns info dict or None.

    ⚡ SPEED ARCHITECTURE (why this bot plays before every other bot):
    Two search engines race in PARALLEL from the moment /play is received:

      1. yt-dlp flat-search  (~1.0-1.5 s) — run in thread pool (subprocess)
         → returns full metadata + sometimes a CDN stream URL

      2. Invidious async HTTP (~0.2-0.5 s) — runs on event loop (no subprocess)
         → returns lightweight metadata (no CDN URL; stream resolved by pipe)

    Whichever completes first with a valid result is used immediately.
    The loser is cancelled.  For popular songs Invidious typically wins,
    cutting search latency from ~1.5 s → ~300 ms — a 5× improvement.
    """
    cached = _cached_search(query, is_video)
    if cached:
        log.debug("⚡ Search cache hit | %s", query[:40])
        return cached

    loop = asyncio.get_running_loop()

    # ── Fire both engines simultaneously ──────────────────────────
    ytdlp_task  = loop.run_in_executor(_exec, _search_sync, query, not is_video)
    inv_task    = asyncio.create_task(_invidious_search_async(query))

    winner: dict | None = None
    remaining = {ytdlp_task, inv_task}
    while remaining and winner is None:
        done, remaining = await asyncio.wait(
            remaining, return_when=asyncio.FIRST_COMPLETED
        )
        for t in done:
            try:
                r = t.result()
                if r and r.get("webpage_url"):
                    winner = r
                    break
            except Exception as exc:
                log.debug("Search engine failed: %s", exc)

    # Cancel the slower engine — we already have our result.
    for t in remaining:
        t.cancel()
    await asyncio.gather(*remaining, return_exceptions=True)

    if not winner:
        log.warning("❌ Both search engines failed for: %r", query[:50])
        return None

    result = _build_search_result(winner, query, is_video)

    # Prime the stream cache if yt-dlp gave us a CDN URL (saves a round-trip
    # in _do_play_inner for non-FIFO VPS playback paths).
    if result["webpage_url"] and result["url"]:
        _cache_stream(
            result["webpage_url"], is_video,
            result["url"], None, result["duration"], result["http_headers"],
        )
    _cache_search(query, is_video, result)
    return result


def _download_audio_sync(url: str, audio_only: bool) -> tuple[str, int]:
    """
    Download audio/video to a local temp file using yt-dlp.
    Returns (local_file_path, duration_secs), or ("", 0) on failure.

    WHY THIS WORKS: yt-dlp ships with curl_cffi which impersonates Chrome's
    TLS fingerprint. YouTube CDN (googlevideo.com) blocks requests with a
    generic OpenSSL TLS stack (ntgcalls/ffmpeg) but allows Chrome-fingerprint
    downloads even from Heroku/cloud IPs. Reading from a local file bypasses
    all streaming CDN restrictions entirely.

    BUG FIX: Previously used a single "bestaudio/best" format with ["default"]
    client — on Heroku/cloud IPs this triggers YouTube's sign-in wall and
    returns "Requested format is not available".  Fixed: try multiple
    format+client combos in priority order (same strategy as _extract_sync),
    with "web" client + cookies first since it authenticates best via browser
    session cookies.
    """
    cookie = _resolve_cookie_file()

    # Format + client combos tried in priority order.
    # Cookie-first: "web" client authenticates fully with browser cookies and
    # unlocks formats that the generic "default" client cannot access on cloud IPs.
    _VIDEO_FMT = (
        # BUG FIX: Was 1080p → 1.7 GB+ for a 2-hour movie before playback even
        # started.  Capped to 720p: still excellent quality in a Telegram Voice
        # Chat but ~60 % smaller.  This is the FALLBACK path (pipe failed); the
        # primary path uses FIFO pipe streaming (no full download needed).
        "bestvideo[height<=720][vcodec!=none]+bestaudio"
        "/best[height<=720][vcodec!=none][acodec!=none]"
        "/best[height<=720]/best"
    )
    if audio_only:
        combos: list[tuple[str, list]] = (
            [
                ("bestaudio[ext=m4a]/bestaudio/best", ["web"]),
                ("bestaudio/best",                    ["web", "default"]),
                ("bestaudio/best",                    ["default"]),
            ] if cookie else [
                ("bestaudio/best", ["default"]),
                ("bestaudio/best", ["web_embedded"]),
            ]
        )
    else:
        combos = (
            [
                (_VIDEO_FMT, ["web"]),
                (_VIDEO_FMT, ["web", "default"]),
                (_VIDEO_FMT, ["default"]),
            ] if cookie else [
                (_VIDEO_FMT, ["default"]),
            ]
        )

    _RETRYABLE = (
        "Requested format is not available",
        "format is not available",
        "No video formats found",
        "Sign in to confirm",
        "This video is not available",
        "HTTP Error 403",
        "HTTP Error 429",
        "requires payment",
        "members-only",
    )

    for fmt, clients in combos:
        tmpdir = tempfile.mkdtemp(prefix="apex_dl_")
        opts: dict = {
            "format": fmt,
            "outtmpl": os.path.join(tmpdir, "audio.%(ext)s"),
            "quiet": True,
            "no_warnings": True,
            "noprogress": True,
            # ⚡ SPEED FIX: 16 parallel fragment threads — ultra-fast DASH/HLS downloads
            "concurrent_fragment_downloads": 16,
            # ⚡ SPEED FIX: larger read buffer for high-speed downloads
            # NOTE: yt-dlp expects bytes as int — string like "32M" causes
            # "'<' not supported between instances of 'str' and 'int'" error.
            "buffersize": 32 * 1024 * 1024,
            # ⚡ SPEED FIX: skip slow format pre-verification
            "check_formats": False,
            # ⚡ SPEED FIX: more retries = fewer full restarts
            "retries": 5,
            "fragment_retries": 5,
        }
        if cookie:
            opts["cookiefile"] = cookie

        # Carry over bgutil/PO-token extractor args, headers, and other
        # settings from _opts() so the correct player client is used.
        base = _opts(audio_only, player_client=clients)
        for k in ("extractor_args", "http_headers", "js_runtimes",
                  "remote_components", "geo_bypass", "geo_bypass_country",
                  "socket_timeout", "noplaylist"):
            if k in base:
                opts[k] = base[k]

        try:
            with yt_dlp.YoutubeDL(opts) as ydl:
                info = ydl.extract_info(url, download=True)
                if not info:
                    shutil.rmtree(tmpdir, ignore_errors=True)
                    continue

                ext = info.get("ext", "opus")
                path = os.path.join(tmpdir, f"audio.{ext}")
                if not os.path.exists(path):
                    files = [
                        f for f in os.listdir(tmpdir)
                        if os.path.isfile(os.path.join(tmpdir, f))
                    ]
                    if not files:
                        shutil.rmtree(tmpdir, ignore_errors=True)
                        continue
                    path = os.path.join(tmpdir, files[0])

                dur = info.get("duration", 0) or 0
                size_mb = os.path.getsize(path) / 1e6
                log.info(
                    "✅ yt-dlp download OK | fmt=%r clients=%s | %s → %s (%.1f MB, %ds)",
                    fmt, clients, url[:60], os.path.basename(path), size_mb, dur,
                )
                return path, dur
        except yt_dlp.utils.DownloadError as e:
            err = str(e)
            shutil.rmtree(tmpdir, ignore_errors=True)
            if any(r in err for r in _RETRYABLE):
                log.warning(
                    "⚠️ Download retry | fmt=%r clients=%s — %s",
                    fmt, clients, err[:100],
                )
                continue
            log.error("❌ yt-dlp local download fatal: %s", e)
            return "", 0
        except Exception as e:
            log.error("❌ yt-dlp local download failed: %s", e)
            shutil.rmtree(tmpdir, ignore_errors=True)
            return "", 0

    log.error("❌ yt-dlp local download: all format combos exhausted for %s", url[:60])
    return "", 0


def cleanup_temp_file(path: str) -> None:
    """
    Delete the apex_dl_ / apex_tg_ / apex_pipe_ temp directory.
    Safe to call with any path — no-op if path is not one of those prefixes.
    """
    if not path:
        return
    try:
        parent = os.path.dirname(os.path.abspath(path))
        with _pipe_states_lock:
            state = _pipe_states.pop(path, None)
            if state is not None:
                state["cancelled"] = True
                proc = state.get("process")
                if proc is not None and proc.poll() is None:
                    try:
                        proc.kill()
                    except Exception:
                        pass
        if (
            os.path.basename(parent).startswith(("apex_dl_", "apex_tg_", "apex_pipe_"))
            and os.path.isdir(parent)
        ):
            shutil.rmtree(parent, ignore_errors=True)
            log.debug("🗑️ Cleaned up temp dir: %s", parent)
    except Exception as e:
        log.debug("Temp cleanup error for %s: %s", path, e)


def _start_pipe_download(url: str, audio_only: bool) -> str:
    """
    Start yt-dlp writing audio to a named pipe (FIFO) in a daemon thread.
    Returns the FIFO path IMMEDIATELY — yt-dlp runs in background.

    ⚡ HOW THIS SAVES TIME:
    - yt-dlp subprocess starts RIGHT NOW (metadata fetch begins immediately).
    - The FIFO open() blocks the writer thread until ntgcalls opens the read
      end inside call_py.play(). By that time (~1-2s later) yt-dlp has already
      spent 1-2s on its ~2-3s metadata fetch, so data starts flowing sooner.
    - ffmpeg reads the FIFO as a continuous stream — music plays as soon as
      the first audio frames arrive, without waiting for the full file download.
    - Combined with the fire-and-forget status edit this saves ~1-2s vs full
      pre-download on Heroku.
    """
    tmpdir = tempfile.mkdtemp(prefix="apex_pipe_")
    pipe_path = os.path.join(tmpdir, "audio.webm")
    # BUG FIX: os.mkfifo() raises OSError on platforms that don't support
    # named pipes (some Docker containers, restricted cloud envs, Windows).
    # Wrap with try/except, log a warning, clean up the temp dir, and re-raise
    # so callers fall back to local-download mode instead of silently dying.
    try:
        os.mkfifo(pipe_path)
    except OSError as _mke:
        log.warning(
            "os.mkfifo() failed (%s) — named pipes not supported on this host. "
            "Cleaning up; caller will fall back to normal download.", _mke
        )
        shutil.rmtree(tmpdir, ignore_errors=True)
        raise OSError(
            f"FIFO not supported on this platform ({_mke}). "
            "Pipe streaming unavailable — will fall back to local download."
        ) from _mke
    with _pipe_states_lock:
        _pipe_states[pipe_path] = {
            "process": None,
            "cancelled": False,
            "url": url,
        }

    # FIX: prefer WebM/Opus for audio — streamable through FIFO without seeking.
    # AAC/M4A containers require seeking (moov atom at EOF) and break pipe mode.
    # Now that PO-token (bgutil) works via the Python API subprocess, YouTube
    # serves WebM/Opus on cloud IPs, so we can safely prefer it again.
    # "bestaudio/best" is kept as ultimate fallback only.
    fmt = (
        "bestaudio[ext=webm]/bestaudio[ext=opus]/bestaudio/best"
        if audio_only
        else (
            # BUG FIX v3 — Video pipe format:
            # Prefer single-container formats first (no muxing → stream starts
            # instantly).  Fall back to DASH merge (bestvideo+bestaudio) which
            # yt-dlp muxes to mkv on stdout — still streamable, just slightly
            # slower to start.  Cap at 720p so a 2-hour movie pipes at ~700 MB
            # instead of 1.7 GB.
            "best[height<=720][vcodec!=none][acodec!=none]"
            "/bestvideo[height<=720][vcodec!=none]+bestaudio"
            "/best[height<=720]/best"
        )
    )
    cookie = _resolve_cookie_file()

    # ROOT-CAUSE FIX — Replace CLI yt-dlp subprocess with Python API subprocess.
    #
    # Why CLI yt-dlp failed: the CLI subprocess couldn't replicate all settings
    # that _opts() has:
    #   • bgutil PO-token plugin loads via sys.path in the main process, but
    #     --plugin-dirs in subprocess yt-dlp doesn't add to sys.path, so bgutil
    #     fails to import its own deps → no PO token → YouTube returns empty
    #     format list on cloud IPs → "Requested format is not available"
    #   • geo_bypass / geo_bypass_country — no CLI equivalent
    #   • js_runtimes (Deno) + remote_components — no CLI equivalent
    #
    # Fix: run a `python -c` subprocess that uses yt-dlp Python API directly.
    # The inline script loads bgutil via sys.path (same as main process) and
    # uses _opts()-equivalent settings. outtmpl="-" makes yt-dlp write raw
    # audio bytes to stdout; parent thread relays those bytes to the FIFO.
    bgutil_server = _bgutil_server_home()
    bgutil_plugin_dir = os.path.join(os.path.dirname(bgutil_server), "plugin")
    deno = _deno_path()

    # Build extractor_args dict for the inline script
    _extractor_args_repr_parts = [
        '"youtube": {"player_client": ["web", "default"]}',
    ]
    if os.path.isdir(bgutil_plugin_dir):
        _extractor_args_repr_parts.append(
            f'"youtubepot-bgutilscript": {{"server_home": [{repr(bgutil_server)}]}}'
        )
    _extractor_args_repr = "{" + ", ".join(_extractor_args_repr_parts) + "}"

    _cookie_line = f'opts["cookiefile"] = {repr(cookie)}' if cookie else ""
    _proxy = _proxy_from_environment()
    _proxy_line = f'opts["proxy"] = {repr(_proxy)}' if _proxy is not None else ""
    _bgutil_path_line = (
        f'sys.path.insert(0, {repr(bgutil_plugin_dir)})'
        if os.path.isdir(bgutil_plugin_dir)
        else ""
    )

    # Inline Python script — runs as subprocess, writes audio bytes to stdout.
    # outtmpl="-" tells yt-dlp to output to stdout (sys.stdout.buffer).
    _script = (
        "import sys, os\n"
        + (_bgutil_path_line + "\n" if _bgutil_path_line else "")
        + "import yt_dlp\n"
        + f"opts = {{\n"
        + f'    "format": {repr(fmt)},\n'
        + '    "outtmpl": "-",\n'
        + '    "quiet": True,\n'
        + '    "no_warnings": True,\n'
        + '    "noplaylist": True,\n'
        + '    "geo_bypass": True,\n'
        + '    "geo_bypass_country": "US",\n'
        + '    "check_formats": False,\n'
        + '    "socket_timeout": 8,\n'
        + '    "retries": 3,\n'
        + '    "fragment_retries": 3,\n'
        # ROOT-CAUSE FIX — concurrent_fragment_downloads must be 1 for FIFO pipe.
        #
        # With concurrent_fragment_downloads=N, yt-dlp downloads N fragments
        # simultaneously and then writes them to stdout as one burst (N × ~80 KB
        # ≈ N × 5 s of Opus audio).  After writing the batch it pauses while
        # downloading the NEXT N fragments.  During that pause, proc.stdout.read()
        # blocks → no new bytes reach the FIFO → NTgCalls' internal ffmpeg waits
        # → ffmpeg read-timeout fires → ffmpeg closes the FIFO read-end →
        # the next write from this thread raises [Errno 32] Broken pipe.
        #
        # With N=1 (sequential), each fragment is downloaded and written to
        # stdout immediately as it completes.  The data flows continuously with
        # only millisecond gaps between fragments — well within ffmpeg's read
        # buffer — so the pipe never starves and Broken pipe never occurs.
        # Sequential is actually FASTER for streaming: the first fragment is
        # available after ~0.1 s instead of waiting for all 16 to finish.
        + '    "concurrent_fragment_downloads": 1,\n'
        + f'    "js_runtimes": {{"deno": {{"path": {repr(deno)}}}}},\n'
        + '    "remote_components": ["ejs:github"],\n'
        + f'    "extractor_args": {_extractor_args_repr},\n'
        + '    "http_headers": {\n'
        + '        "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) '
        + 'AppleWebKit/537.36 (KHTML, like Gecko) Chrome/125.0.0.0 Safari/537.36",\n'
        + '        "Referer": "https://www.youtube.com/",\n'
        + '    },\n'
        + "}\n"
        + (_cookie_line + "\n" if _cookie_line else "")
        + (_proxy_line + "\n" if _proxy_line else "")
        # DO NOT redirect sys.stdout here. yt-dlp's outtmpl="-" internally
        # calls sys.stdout.buffer to write binary audio. If we replace
        # sys.stdout with sys.stdout.buffer, the .buffer attribute disappears
        # → yt-dlp raises AttributeError → falls back to writing a file named
        # "-" on disk instead of to the subprocess stdout pipe → relay gets
        # nothing/garbage → FIFO is empty → ntgcalls hits EOF → Broken pipe.
        + f"with yt_dlp.YoutubeDL(opts) as ydl:\n"
        + f"    ydl.download([{repr(url)}])\n"
    )

    cmd = [sys.executable, "-c", _script]

    def _writer() -> None:
        proc = None
        fifo_fd = None
        slot_acquired = False
        total_bytes = 0
        process_error = ""
        try:
            # ⚡ PLAY-RACE FIX: use _PIPE_SLOT_TIMEOUT (5 s), not _PIPE_CONNECT_TIMEOUT
            # (15 s).  If all slots are taken for >5 s the FIFO is abandoned quickly
            # so the premature-end retry path can start a fresh pipe sooner, rather
            # than the old 15 s stall that the user observed as "10–15 s delay".
            slot_acquired = _pipe_slots.acquire(timeout=_PIPE_SLOT_TIMEOUT)
            if not slot_acquired:
                log.warning("Pipe concurrency limit reached; abandoning FIFO | %s", url[:60])
                return
            # yt-dlp subprocess starts immediately — metadata fetch runs
            # in background while the event loop continues.
            proc = subprocess.Popen(
                cmd,
                stdout=subprocess.PIPE,
                stderr=subprocess.PIPE,
            )
            with _pipe_states_lock:
                state = _pipe_states.get(pipe_path)
                if state is None or state["cancelled"]:
                    proc.kill()
                    return
                state["process"] = proc

            # ⚡ SPEED FIX: relay yt-dlp stdout DIRECTLY to FIFO — no middle ffmpeg.
            #
            # OLD approach: yt-dlp → ffmpeg(ogg transcode) → FIFO
            #   Problem: ffmpeg encodes opus faster than realtime, pre-filling the
            #   FIFO pipe buffer (~64 KB = ~3s of audio). When pytgcalls opens the
            #   pipe, it reads this pre-filled buffer quickly → first 10s plays at
            #   1.5x speed (the "speed bug"). Also adds ~0.3s CPU overhead per song.
            #
            # NEW approach: yt-dlp → FIFO directly (webm/opus passthrough)
            #   yt-dlp downloads at network speed (not CPU speed), so no pre-fill.
            #   pytgcalls ffmpeg auto-detects webm from the 4-byte EBML magic header
            #   (no seeking needed). Music plays at exact correct speed from byte 0.

            # ROOT-CAUSE FIX — Replace O_NONBLOCK polling with a blocking open.
            #
            # Old approach (O_NONBLOCK polling, 100 ms interval):
            #   The writer polls os.open(O_NONBLOCK) every 100 ms. When ntgcalls
            #   opens the FIFO read-end, there is a window of up to 100 ms where
            #   the read-end IS open but the write-end is NOT. During that window,
            #   ntgcalls reads from the FIFO and immediately gets EOF (POSIX:
            #   read() returns 0 when no writer has the write-end open). ntgcalls
            #   closes the read-end. When the writer's next poll succeeds and it
            #   starts writing, there is no longer a reader → [Errno 32] Broken pipe.
            #
            # New approach (blocking open in a daemon sub-thread):
            #   os.open(O_WRONLY) without O_NONBLOCK blocks until a reader opens
            #   the read-end. The write-end therefore opens ATOMICALLY with the
            #   read-end — there is zero window where the read-end is open without
            #   a writer → ntgcalls never sees a spurious EOF → no broken pipe.
            #   A daemon sub-thread is used so we can enforce the connect timeout
            #   and check cancellation without blocking the outer writer thread.
            _open_evt = threading.Event()
            _open_result: list = [None]

            def _blocking_open() -> None:
                try:
                    _open_result[0] = os.open(pipe_path, os.O_WRONLY)
                except Exception as _be:
                    _open_result[0] = _be
                finally:
                    _open_evt.set()

            _opener_t = threading.Thread(target=_blocking_open, daemon=True,
                                         name="apex-fifo-opener")
            _opener_t.start()

            _conn_deadline = time.monotonic() + _PIPE_CONNECT_TIMEOUT
            while not _open_evt.wait(timeout=0.3):
                if time.monotonic() >= _conn_deadline:
                    log.warning(
                        "FIFO reader did not connect in %.1fs | %s",
                        _PIPE_CONNECT_TIMEOUT,
                        url[:60],
                    )
                    # BUG FIX: mark this URL's pipe as failed so _play_next_inner
                    # knows to retry via local download instead of showing
                    # "Queue Finished" immediately.  Without this, _had_pipe_failure()
                    # returned False for reader-timeout failures (the rc!=0 path that
                    # normally increments _pipe_failures was never reached), causing
                    # the retry guard in _play_next_inner to skip re-resolve entirely.
                    with _pipe_states_lock:
                        _pipe_failures[url] = max(1, _pipe_failures.get(url, 0) + 1)
                    log.debug("📌 Pipe marked failed (reader timeout) — will retry via local-dl | %s", url[:80])
                    # Unblock _blocking_open by briefly opening a dummy reader.
                    try:
                        _dummy = os.open(pipe_path, os.O_RDONLY | os.O_NONBLOCK)
                        os.close(_dummy)
                    except Exception:
                        pass
                    return
                with _pipe_states_lock:
                    state = _pipe_states.get(pipe_path)
                    _cancelled = state is None or state["cancelled"]
                if _cancelled:
                    try:
                        _dummy = os.open(pipe_path, os.O_RDONLY | os.O_NONBLOCK)
                        os.close(_dummy)
                    except Exception:
                        pass
                    return

            if isinstance(_open_result[0], Exception):
                raise _open_result[0]
            fifo_fd = _open_result[0]
            # Ensure blocking mode (O_WRONLY is blocking by default, but confirm).
            flags = fcntl.fcntl(fifo_fd, fcntl.F_GETFL)
            fcntl.fcntl(fifo_fd, fcntl.F_SETFL, flags & ~os.O_NONBLOCK)

            # ⚡ SPEED FIX v5 — Dual fix for "fast speed at start" bug.
            #
            # Root cause:
            #   ntgcalls opens the FIFO read-end ~100-200ms before its internal
            #   audio pipeline is fully initialized and draining at real-time rate.
            #   During that window ANY data pre-filled in the kernel pipe buffer
            #   (default 64 KB ≈ ~4 s of 128 kbps audio) is consumed at RAM
            #   speed → first 1-4 seconds play at 2-5x speed.
            #
            # Fix A — Minimize kernel pipe buffer (Linux F_SETPIPE_SZ = 1031).
            #   Reduces pre-fillable buffer from 64 KB to 4 KB (≈ 0.25 s).
            #   Even if ntgcalls reads at RAM speed initially it can only consume
            #   0.25 s before writes block, eliminating the audible speed-up.
            try:
                fcntl.fcntl(fifo_fd, 1031, 4096)   # F_SETPIPE_SZ → 4 096 bytes
            except Exception:
                pass  # Non-Linux platforms (macOS) silently ignored
            #
            # Fix B — Startup delay (200 ms) in background writer thread.
            #
            # Root cause of 2x speed bug (especially on Heroku Standard-2X):
            #   ntgcalls opens the FIFO read-end and starts consuming bytes
            #   BEFORE its internal audio pipeline has reached real-time drain
            #   rate. On faster dynos (Standard-2X with 2x CPU + 1 GB RAM)
            #   yt-dlp downloads arrive faster, so more data accumulates in the
            #   kernel buffer during that init window → first 2-5 seconds play
            #   at 2x speed.
            #
            # Fix:
            #   - F_SETPIPE_SZ=4096 (Fix A above) → limits kernel buffer to
            #     4 KB ≈ 0.25 s of audio. Writes block until reader drains.
            #   - time.sleep(0.20) (Fix B) → give ntgcalls' pipeline 200 ms to
            #     reach steady-state drain rate before any bytes enter the FIFO.
            #   Combined: audio starts after ~200 ms and plays at EXACTLY 1x
            #   speed from the very first byte.
            #
            # User experience: call_py.play() has already returned (0-sec VC
            # join), so this 200 ms sleep is invisible — it runs in the writer
            # background thread while the "NOW PLAYING" card is being sent.
            #
            # buffering=0: os.fdopen without it creates an 8 KB BufferedWriter
            # that stacks on the 4 KB kernel FIFO → 12 KB pre-fill. FileIO
            # makes every write() a direct syscall so pre-fill stays at 4 KB.
            time.sleep(0.25)  # ← FIX: allow ntgcalls audio pipeline to init (0.25s for robust init)
            _CHUNK_SIZE = 4096
            with os.fdopen(fifo_fd, "wb", buffering=0) as fifo_out:
                fifo_fd = None
                while True:
                    chunk = proc.stdout.read(_CHUNK_SIZE)
                    if not chunk:
                        break
                    fifo_out.write(chunk)
                    total_bytes += len(chunk)

            return_code = proc.wait(timeout=5)
            stderr_data = proc.stderr.read() if proc.stderr else b""
            process_error = stderr_data.decode("utf-8", errors="replace").strip()
            if return_code != 0:
                with _pipe_states_lock:
                    _pipe_failures[url] = _pipe_failures.get(url, 0) + 1
                log.warning(
                    "❌ Pipe yt-dlp exited rc=%s | bytes=%s | url=%s | error=%s",
                    return_code,
                    total_bytes,
                    url[:80],
                    process_error[-600:] or "no stderr",
                )
            else:
                log.info(
                    "✅ Pipe yt-dlp completed | bytes=%s | url=%s",
                    total_bytes,
                    url[:80],
                )
        except Exception as exc:
            with _pipe_states_lock:
                _pipe_failures[url] = _pipe_failures.get(url, 0) + 1
            log.warning(
                "❌ Pipe writer failed | bytes=%s | url=%s | error=%s",
                total_bytes,
                url[:80],
                process_error or exc,
            )
        finally:
            if fifo_fd is not None:
                try:
                    os.close(fifo_fd)
                except OSError:
                    pass
            if proc is not None and proc.poll() is None:
                try:
                    proc.kill()
                    proc.wait(timeout=2)
                except Exception:
                    pass
            if proc is not None and proc.stderr is not None:
                try:
                    trailing_error = proc.stderr.read().decode(
                        "utf-8", errors="replace"
                    ).strip()
                    if trailing_error and not process_error:
                        log.warning(
                            "Pipe yt-dlp stderr after failure | %s",
                            trailing_error[-600:],
                        )
                except Exception:
                    pass
            with _pipe_states_lock:
                _pipe_states.pop(pipe_path, None)
            if slot_acquired:
                _pipe_slots.release()
            try:
                shutil.rmtree(tmpdir, ignore_errors=True)
            except Exception:
                pass

    threading.Thread(target=_writer, daemon=True, name="apex-pipe-writer").start()
    log.info("⚡ Pipe download started | %s → %s", url[:60], pipe_path)
    return pipe_path


async def _resolve_stream(
    url: str,
    is_video: bool = False,
    force_refresh: bool = False,
) -> tuple[str, str | None, int, dict]:
    """
    Get direct stream URL for playback.
    Returns (media_url, optional_audio_url, duration_secs, http_headers).

    ⚡ SPEED ARCHITECTURE:
    - skip_cdn=True  (cloud host OR force_refresh):
        yt-dlp starts a FIFO download and the FIFO is returned immediately.
        PyTgCalls can start consuming it while yt-dlp finishes its handshake.
    - skip_cdn=False (VPS / home server, CDN not known blocked):
        yt-dlp signed URL + Invidious race. CDN wins if URL is streamable.
        Falls back to local download only if both fail.
    """
    cached = None if force_refresh else _cached_stream(url, is_video)
    if cached:
        return cached

    loop = asyncio.get_running_loop()
    vid_id = _extract_video_id(url)

    # ── Fast path: CDN known-blocked OR force_refresh ─────────────
    # On cloud/Heroku IPs, the CDN is always blocked. Trying it first wastes
    # 12 s of silence before the premature-end retry kicks in.
    #
    # ⚡ PIPE STREAMING ARCHITECTURE:
    # 1. _start_pipe_download() starts yt-dlp subprocess IMMEDIATELY and returns
    #    a FIFO path at once (no waiting for download to complete).
    # 2. Return the pipe immediately. yt-dlp runs in the background and ffmpeg
    #    starts playing as soon as the first audio frames arrive, without
    #    waiting for the full file.
    skip_cdn = force_refresh or _cdn_blocked
    # ── CLOUD HOST FIX ───────────────────────────────────────────────────────
    # On Heroku/Railway/Render/Fly.io the YouTube CDN is IP-blocked. yt-dlp's
    # FIFO pipe can only deliver the first ~1 MB (one DASH segment) before the
    # connection is dropped, causing a premature stream_end after ~10 s.
    # Trying the pipe on the first attempt wastes those 10 s and floods logs
    # with ntgcalls "Reached end of file" warnings.
    #
    # Fix: skip the pipe path entirely on known cloud hosts. Go straight to
    # the local-download path which uses curl_cffi's Chrome TLS fingerprint
    # and does not suffer from the CDN IP block.
    if skip_cdn and _pipe_failures.get(url, 0) == 0:
        reason = "force_refresh" if force_refresh else "cdn_blocked"
        log.info("⚡ skip_cdn=%s | immediate pipe playback | %s", reason, url[:60])

        # Start pipe download immediately — yt-dlp subprocess runs in background.
        # FIFO path returned at once; the event loop never blocks on download.
        # BUG FIX: wrap _start_pipe_download in try/except so that if mkfifo
        # fails on this platform we fall through to the local-download path
        # below instead of raising an unhandled exception.
        try:
            pipe_path = _start_pipe_download(url, not is_video)
        except OSError as _pe:
            log.warning(
                "⚠️ FIFO pipe unavailable (%s) — falling back to local download | %s",
                _pe, url[:60]
            )
            # Fall through to local-download path
            local_path, dur = await loop.run_in_executor(
                _exec, _download_audio_sync, url, not is_video
            )
            if local_path:
                return local_path, None, dur, {}
            raise Exception(f"❌ Pipe failed and local download failed: {url[:60]}") from _pe

        # Do not wait for an Invidious probe here. The old 3.5s race window was
        # visible in production as an almost exact 3.5s gap between
        # "Pipe download started" and "Pipe path ready". The FIFO is already
        # backed by yt-dlp, so returning it immediately lets VC setup and
        # ffmpeg start while yt-dlp finishes its metadata handshake.
        #
        # Duration is unknown until yt-dlp finishes; NP card will show 0:00
        # briefly, which is acceptable. Do NOT cache FIFO paths — one-use only.
        log.info("⚡ Pipe path ready (streaming while downloading) | %s", pipe_path)
        return pipe_path, None, 0, {}

    if skip_cdn:
        # Pipe previously failed for this URL — fall back to full local download.
        log.warning("📥 Pipe failed; switching to local download | %s", url[:80])
        local_path, dur = await loop.run_in_executor(
            _exec, _download_audio_sync, url, not is_video
        )
        if local_path:
            return local_path, None, dur, {}
        raise Exception(f"❌ Local download failed: {url[:60]}")

    # ── Normal path: CDN not known-blocked (VPS / home server) ───
    # Race yt-dlp signed URL against Invidious. Falls back to local download
    # only if both fail to give a streamable URL.
    ytdlp_task = loop.run_in_executor(_exec, _extract_sync, url, not is_video)
    inv_task = asyncio.create_task(
        _try_invidious(vid_id, not is_video)
        if vid_id
        else asyncio.sleep(0, result=None)
    )
    info_result = inv_result = None
    done, pending = await asyncio.wait(
        {ytdlp_task, inv_task},
        return_when=asyncio.FIRST_COMPLETED,
    )

    if inv_task in done:
        try:
            inv_result = inv_task.result()
        except Exception as exc:
            inv_result = exc
        if isinstance(inv_result, tuple) and inv_result:
            for task in pending:
                task.cancel()
            await asyncio.gather(*pending, return_exceptions=True)
        else:
            try:
                info_result = await ytdlp_task
            except Exception as exc:
                info_result = exc
    elif ytdlp_task in done:
        try:
            info_result = ytdlp_task.result()
        except Exception as exc:
            info_result = exc
        if info_result:
            inv_task.cancel()
            await asyncio.gather(inv_task, return_exceptions=True)
        else:
            try:
                inv_result = await inv_task
            except Exception as exc:
                inv_result = exc

    if isinstance(inv_result, tuple) and inv_result:
        su, dur = inv_result
        log.info("✅ Invidious proxy URL use ho rahi hai | vid=%s", vid_id)
        _cache_stream(url, is_video, su, None, dur, {})
        return su, None, dur, {}

    if isinstance(inv_result, Exception):
        log.warning("Invidious failed for %s: %s", vid_id, inv_result)

    info = None if isinstance(info_result, Exception) else info_result
    if not info:
        exc_msg = str(info_result) if isinstance(info_result, Exception) else ""
        raise Exception(
            f"❌ Stream resolve nahi hua: {url[:60]}"
            + (f" — {exc_msg}" if exc_msg else "")
        )

    su, audio_url = _pick_urls(info, not is_video)
    dur = info.get("duration", 0) or 0
    headers = info.get("http_headers") or {}

    # BUG FIX: For video (is_video=True), YouTube always serves DASH streams —
    # a video-only CDN URL (su) and a separate audio-only CDN URL (audio_url).
    # pytgcalls MediaStream accepts only ONE path; passing the video CDN URL
    # plays the video track with NO audio.  The audio_url is discarded.
    #
    # Fix: for video, skip the CDN URL path entirely and use FIFO pipe
    # streaming.  yt-dlp muxes the video+audio DASH tracks and writes the
    # result progressively to a named pipe.  Playback starts within ~2-3 s
    # with no full-file download (a 2-hour 1080p movie was downloading 1.7 GB
    # before the first frame; now it starts streaming in seconds).
    if is_video and _pipe_failures.get(url, 0) == 0:
        log.info(
            "⚡ Video: pipe streaming (DASH mux) → instant start, no 1.7 GB download | %s",
            url[:60],
        )
        # BUG FIX: wrap in try/except so FIFO-unsupported platforms fall back
        # to CDN URL or local download rather than crashing the handler.
        try:
            pipe_path = _start_pipe_download(url, audio_only=False)
            return pipe_path, None, 0, {}
        except OSError as _vpe:
            log.warning(
                "⚠️ Video FIFO pipe unavailable (%s) — using CDN/download fallback | %s",
                _vpe, url[:60]
            )

    if su and _is_streamable_url(su):
        log.info("✅ Direct signed stream ready | %s", url[:60])
        _cache_stream(url, is_video, su, audio_url, dur, headers)
        return su, audio_url, dur, headers

    log.info(
        "📥 %s — downloading locally via yt-dlp | %s",
        "Direct stream unavailable" if not su else "CDN retry fallback",
        url[:60],
    )
    local_path, dur = await loop.run_in_executor(
        _exec, _download_audio_sync, url, not is_video
    )
    if local_path:
        return local_path, None, dur, {}

    if not su:
        raise Exception(f"❌ Stream URL empty hai: {url[:60]}")
    log.warning("⚠️ Falling back to CDN URL after local download failure: %s", su[:80])
    _cache_stream(url, is_video, su, audio_url, dur, headers)
    return su, audio_url, dur, headers


async def get_stream(
    url: str,
    is_video: bool = False,
    force_refresh: bool = False,
) -> tuple[str, str | None, int, dict]:
    """Return a shared stream-resolution task for normal playback."""
    key = _stream_key(url, is_video)
    if not force_refresh:
        cached = _cached_stream(url, is_video)
        if cached:
            # ⚡ CLOUD BUG FIX: On Heroku/Railway/Render/cloud IPs, YouTube CDN
            # URLs (googlevideo.com) are IP-blocked. search_song() can cache a
            # CDN URL from _extract_sync (for direct YouTube URLs). Returning
            # that cached URL causes ntgcalls to get an immediate EOF → the
            # premature-end retry kicks in → 10-15s of silence before music
            # actually plays. Skip the CDN-URL cache on cloud and use pipe
            # streaming instead.
            if _cdn_blocked and _is_streamable_url(cached[0]):
                log.debug(
                    "Cloud host: bypassing cached CDN URL — using pipe | %s",
                    (cached[0] or "")[:70],
                )
                _stream_cache.pop(key, None)
            else:
                return cached
        pending = _stream_tasks.get(key)
        if pending:
            try:
                return await pending
            finally:
                if pending.done() and _stream_tasks.get(key) is pending:
                    _stream_tasks.pop(key, None)

    task = asyncio.create_task(_resolve_stream(url, is_video, force_refresh))
    if not force_refresh:
        _stream_tasks[key] = task
    try:
        return await task
    finally:
        if not force_refresh and _stream_tasks.get(key) is task:
            _stream_tasks.pop(key, None)


def prefetch_stream(url: str, is_video: bool = False) -> None:
    """Resolve a stream as soon as search returns, without blocking the UI.

    Search deliberately uses yt-dlp's flat mode for fast metadata. Starting the
    real stream resolution here overlaps that work with Telegram message
    handling and VC setup, so _do_play() can consume the same task instead of
    performing a second sequential extraction.
    """
    if not url:
        return
    # Cloud playback returns a one-shot FIFO immediately. Prefetching it from
    # /play and resolving it again in _do_play can create two yt-dlp children
    # for the same track; skip that duplicate path on Heroku/cloud hosts.
    if _cdn_blocked:
        log.debug("Skipping cloud pipe prefetch; playback owns one FIFO | %s", url[:60])
        return
    key = _stream_key(url, is_video)
    if key in _stream_tasks or _cached_stream(url, is_video):
        return

    task = asyncio.create_task(_resolve_stream(url, is_video=is_video))
    _stream_tasks[key] = task

    def _remove_completed(completed: asyncio.Task) -> None:
        if _stream_tasks.get(key) is completed:
            _stream_tasks.pop(key, None)

    def _consume_error(completed: asyncio.Task) -> None:
        if completed.cancelled():
            return
        try:
            completed.exception()
        except Exception:
            pass

    task.add_done_callback(_remove_completed)
    task.add_done_callback(_consume_error)


async def download_audio(url: str, is_video: bool = False) -> tuple[str, int]:
    """Download media locally for queue prefetch/archive upload."""
    loop = asyncio.get_running_loop()
    return await loop.run_in_executor(_exec, _download_audio_sync, url, not is_video)


def start_early_pipe(query_or_url: str, audio_only: bool = True) -> str:
    """Start a yt-dlp pipe IMMEDIATELY for cloud/CDN-blocked hosts.

    ⚡ WHY THIS SAVES ~1.5s:
    On cloud (Heroku/Railway/Render), the playback path is:
      flat-search (~1.5s) → pipe starts → yt-dlp metadata fetch (~2s) → audio

    Total latency without this: 1.5s + 2s = 3.5s before first audio frame.

    With start_early_pipe() called at t=0 (before flat search):
      yt-dlp metadata fetch (~2s) runs IN PARALLEL with flat search (~1.5s).
    So by the time search is done and call_py.play() is called, yt-dlp is
    already ~1.5s into its metadata fetch → audio starts flowing at t≈2s.

    For plain search queries, passes them as 'ytsearch1:<query>' to yt-dlp so
    it uses the same first YouTube search result as our flat-metadata search.

    Returns the FIFO path immediately (yt-dlp runs in a background thread).
    Use cleanup_temp_file(path) to abort if the pipe is no longer needed.
    """
    if not (query_or_url.startswith("http://") or query_or_url.startswith("https://")):
        query_or_url = f"ytsearch1:{query_or_url}"
    # BUG FIX: catch OSError from mkfifo so callers that don't wrap this
    # (e.g. dm_music.py) don't crash on FIFO-unsupported platforms.
    try:
        pipe_path = _start_pipe_download(query_or_url, audio_only)
    except OSError as _ep:
        log.warning("⚡ start_early_pipe: FIFO unavailable (%s) — returning empty path", _ep)
        return ""   # callers check for empty path / wrap in try/except
    log.info("⚡ Early pipe started | target=%r | fifo=%s", query_or_url[:60], pipe_path)
    return pipe_path


# ── Invidious fallback ────────────────────────────────────────────
# Public Invidious instances — used when yt-dlp fails on Heroku/cloud IPs.
# IMPORTANT: We do NOT use the direct CDN URLs from adaptiveFormats — those are
# googlevideo.com links that are still blocked from Heroku.
# Instead we use /latest_version?local=true which makes Invidious proxy the
# stream through its own server, bypassing the IP block entirely.
_INVIDIOUS_INSTANCES = [
    # ⚡ SPEED: More instances = better parallel race coverage.
    # All probed simultaneously; first valid response wins (~200-400 ms typical).
    # companion-proxy instances (stream via their own servers, not bare CDN).
    "https://invidious.f5.si",
    "https://invidious.privacydev.net",
    "https://invidious.nerdvpn.de",
    "https://invidious.fdn.fr",
    "https://invidious.io.lol",
    "https://yt.drgnz.club",
    "https://invidious.einfachzocken.eu",
    "https://inv.us.projectsegfau.lt",
    "https://inv.in.projectsegfau.lt",
    "https://invidious.protokolla.fi",
    "https://invidious.privacyredirect.com",
    "https://invidious.jing.rocks",
    # Additional fast instances for better race coverage
    "https://iv.melmac.space",
    "https://invidious.asir.dev",
    "https://invidious.drgns.space",
    "https://invidious.slipfox.xyz",
    "https://invidious.perennialte.ch",
    "https://invidious.reallyaweso.me",
    "https://invidious.darkness.services",
    "https://iv.datura.network",
]

# Common YouTube audio itags. These let the fallback work even when an
# Invidious instance disables /api/v1/videos but still serves /latest_version.
_INVIDIOUS_AUDIO_ITAGS = (251, 140, 250, 139)


def _extract_video_id(url: str) -> str | None:
    """Extract YouTube video ID from any YouTube URL format."""
    patterns = [
        r"[?&]v=([a-zA-Z0-9_-]{11})",
        r"youtu\.be/([a-zA-Z0-9_-]{11})",
        r"embed/([a-zA-Z0-9_-]{11})",
        r"shorts/([a-zA-Z0-9_-]{11})",
        r"^([a-zA-Z0-9_-]{11})$",
    ]
    for p in patterns:
        m = re.search(p, url)
        if m:
            return m.group(1)
    return None


async def _try_invidious(video_id: str, audio_only: bool) -> tuple[str, int] | None:
    """
    Try public Invidious instances as a last-resort fallback.
    Returns (stream_url, duration_secs) or None.

    KEY: We use /latest_version?local=true — NOT the direct CDN URLs from
    adaptiveFormats. The adaptiveFormats URLs are googlevideo.com CDN links
    that are STILL blocked from Heroku IPs. With local=true, Invidious
    fetches and re-serves the stream through its own server, so ntgcalls
    pulls audio from Invidious (not blocked) instead of YouTube CDN (blocked).
    """
    # Metadata endpoints are frequently disabled, while /latest_version still
    # works. Probe known itags directly and probe all instances concurrently;
    # serially waiting on dead public instances was adding 40–60 seconds.
    itags = _INVIDIOUS_AUDIO_ITAGS if audio_only else (18, 22)
    # connect=5: companion servers (jp1-cmp.invidious.f5.si etc.) need a fresh
    # TLS handshake that takes >2s from Heroku — old connect=2 always timed out.
    # total=20: allow up to 6 redirect hops, each needing its own round-trip.
    timeout = aiohttp.ClientTimeout(total=20, connect=5)
    headers = {"User-Agent": "Mozilla/5.0"}

    async def _probe(instance: str) -> tuple[str, int] | None:
        for itag in itags:
            current = (
                f"{instance}/latest_version?"
                f"id={video_id}&itag={itag}&local=true"
            )
            # Up to 6 hops: instance → companion/latest_version →
            # companion/videoplayback (3 hops for f5.si-style setups).
            for _ in range(6):
                try:
                    async with session.get(
                        current,
                        headers=headers,
                        allow_redirects=False,
                    ) as response:
                        if response.status in (301, 302, 303, 307, 308):
                            location = response.headers.get("Location", "")
                            if not location:
                                break
                            current = urljoin(current, location)
                            # If the redirect lands on a bare googlevideo CDN
                            # URL (no /companion/ prefix) it will be blocked from
                            # Heroku — skip this itag immediately.
                            if (
                                'googlevideo.com/videoplayback' in current
                                and '/companion/' not in current
                            ):
                                log.debug(
                                    'Invidious → bare CDN redirect (blocked) | %s itag=%s',
                                    instance, itag,
                                )
                                break
                            continue

                        if response.status not in (200, 206):
                            break

                        content_type = response.headers.get(
                            "Content-Type", ""
                        ).lower()
                        # Whitelist: only audio/* or video/* is a real stream.
                        # text/plain (error messages), text/html (login pages),
                        # application/json, and empty types are all rejected.
                        # inv.in.projectsegfau.lt returns 'text/plain; charset=utf-8'
                        # with a 25-byte error body — the old blacklist accepted it.
                        if not (
                            content_type.startswith("audio/")
                            or content_type.startswith("video/")
                        ):
                            break

                        log.info(
                            f"✅ Invidious proxy OK | {instance} | "
                            f"{video_id} | itag={itag} | hops<=4"
                        )
                        return current, 0
                except Exception as e:
                    log.debug(
                        f"Invidious probe failed "
                        f"({instance}, itag={itag}): {e}"
                    )
                    break
        return None

    async with aiohttp.ClientSession(timeout=timeout) as session:
        tasks = {
            asyncio.create_task(_probe(instance))
            for instance in _INVIDIOUS_INSTANCES
        }
        try:
            pending = tasks
            while pending:
                done, pending = await asyncio.wait(
                    pending,
                    return_when=asyncio.FIRST_COMPLETED,
                )
                for task in done:
                    try:
                        result = task.result()
                    except Exception as e:
                        log.debug(f"Invidious worker failed: {e}")
                        continue
                    if result:
                        for other in pending:
                            other.cancel()
                        await asyncio.gather(*pending, return_exceptions=True)
                        return result
        finally:
            for task in tasks:
                if not task.done():
                    task.cancel()
            await asyncio.gather(*tasks, return_exceptions=True)

    log.error(f"❌ All Invidious instances failed for {video_id}")
    return None


def extract_video_id(url: str) -> str | None:
    """Public wrapper — extract YouTube video ID from any URL format."""
    return _extract_video_id(url)


def fmt_duration(secs: int) -> str:
    """Format seconds → M:SS or H:MM:SS or LIVE."""
    if not secs or secs <= 0:
        return "🔴 LIVE"
    h, r = divmod(int(secs), 3600)
    m, s = divmod(r, 60)
    return f"{h}:{m:02d}:{s:02d}" if h else f"{m}:{s:02d}"



async def get_youtube_suggestions(video_id: str, max_results: int = 10) -> list[dict]:
    """
    Fetch YouTube suggested songs via Radio/Mix playlist (RD{video_id}).
    Falls back to ytsearch if the mix playlist is unavailable.
    Returns list of dicts: title, webpage_url, duration, thumbnail.
    """
    if not video_id:
        return []
    mix_url = f"https://www.youtube.com/watch?v={video_id}&list=RD{video_id}"
    watch_url = f"https://www.youtube.com/watch?v={video_id}"
    cookie = _resolve_cookie_file()

    def _fetch_sync() -> list[dict]:
        base_opts = {"quiet": True, "no_warnings": True, "skip_download": True}
        if cookie:
            base_opts["cookiefile"] = cookie
        base = _opts(True)
        for k in ("extractor_args", "http_headers", "proxy"):
            if k in base:
                base_opts[k] = base[k]

        results: list[dict] = []

        # ── Method 1: YouTube Radio/Mix playlist (RD{video_id}) ──
        try:
            opts = {**base_opts, "extract_flat": "in_playlist", "playlistend": max_results + 5}
            with yt_dlp.YoutubeDL(opts) as ydl:
                info = ydl.extract_info(mix_url, download=False)
                if info:
                    for entry in (info.get("entries") or []):
                        if not entry:
                            continue
                        vid = entry.get("id") or ""
                        if not vid or vid == video_id:
                            continue
                        title = (entry.get("title") or "")[:100]
                        if not title or title in ("[Private video]", "[Deleted video]"):
                            continue
                        results.append({
                            "id":          vid,
                            "title":       title,
                            "webpage_url": f"https://www.youtube.com/watch?v={vid}",
                            "duration":    int(entry.get("duration") or 0),
                            "thumbnail":   entry.get("thumbnail") or "",
                            "uploader":    entry.get("uploader") or entry.get("channel") or "",
                        })
                        if len(results) >= max_results:
                            break
        except Exception as exc:
            log.debug("YouTube Radio mix failed for %s: %s", video_id, exc)

        if results:
            log.info("🎲 YouTube Radio suggestions: %d tracks for %s", len(results), video_id)
            return results

        # ── Method 2: Fallback — search YouTube for related songs ──
        # If mix playlist is unavailable (IP blocked, private video, etc.),
        # do a related-query search using yt-dlp flat search.
        try:
            # Fetch video title for a better search query
            title_opts = {**base_opts, "extract_flat": True}
            with yt_dlp.YoutubeDL(title_opts) as ydl:
                vid_info = ydl.extract_info(watch_url, download=False)
            video_title = (vid_info or {}).get("title") or ""
            uploader = (vid_info or {}).get("uploader") or (vid_info or {}).get("channel") or ""
            # Build search query: artist mix / related songs
            search_query = f"{uploader} {video_title}" if uploader else video_title
            if not search_query.strip():
                search_query = f"related:{video_id}"
            search_query = search_query[:80]

            search_opts = {
                **base_opts,
                "extract_flat": "in_playlist",
                "playlistend": max_results + 3,
                "default_search": f"ytsearch{max_results + 3}",
            }
            with yt_dlp.YoutubeDL(search_opts) as ydl:
                info = ydl.extract_info(f"ytsearch{max_results + 3}:{search_query}", download=False)
                if info:
                    for entry in (info.get("entries") or []):
                        if not entry:
                            continue
                        vid = entry.get("id") or ""
                        if not vid or vid == video_id:
                            continue
                        title = (entry.get("title") or "")[:100]
                        if not title or title in ("[Private video]", "[Deleted video]"):
                            continue
                        results.append({
                            "id":          vid,
                            "title":       title,
                            "webpage_url": f"https://www.youtube.com/watch?v={vid}",
                            "duration":    int(entry.get("duration") or 0),
                            "thumbnail":   (entry.get("thumbnails") or [{}])[0].get("url", "") if entry.get("thumbnails") else "",
                            "uploader":    entry.get("uploader") or entry.get("channel") or "",
                        })
                        if len(results) >= max_results:
                            break
            if results:
                log.info("🎲 YouTube search fallback suggestions: %d tracks | query=%r", len(results), search_query[:50])
        except Exception as exc:
            log.debug("YouTube suggestions search fallback failed: %s", exc)

        return results

    loop = asyncio.get_running_loop()
    return await loop.run_in_executor(_exec, _fetch_sync)

def clear_cache():
    """Clear all caches (call after yt-dlp update)."""
    _stream_cache.clear()
    _search_cache.clear()
    log.info("🗑️ YouTube cache cleared")


# ══ MULTI-RESULT SEARCH ══════════════════════════════════════════

def _search_multiple_sync(query: str, max_results: int = 5) -> list[dict]:
    """Search YouTube and return top N results (yt-dlp flat search)."""
    cookie = _resolve_cookie_file()
    opts = {
        "quiet": True,
        "no_warnings": True,
        "extract_flat": "in_playlist",
        "playlistend": max_results + 2,
        "skip_download": True,
        "default_search": f"ytsearch{max_results}",
    }
    if cookie:
        opts["cookiefile"] = cookie

    base = _opts(True)
    for k in ("extractor_args", "http_headers", "proxy"):
        if k in base:
            opts[k] = base[k]

    results = []
    try:
        with yt_dlp.YoutubeDL(opts) as ydl:
            info = ydl.extract_info(f"ytsearch{max_results}:{query}", download=False)
            if not info:
                return []
            for entry in (info.get("entries") or []):
                if not entry:
                    continue
                vid_id = entry.get("id") or ""
                title  = (entry.get("title") or "")[:100]
                if not title or not vid_id:
                    continue
                thumbnails = entry.get("thumbnails") or []
                thumb = (
                    next(
                        (t.get("url", "") for t in thumbnails if t.get("quality") in {"medium", "high"}),
                        thumbnails[0].get("url", "") if thumbnails else ""
                    )
                )
                results.append({
                    "id":          vid_id,
                    "title":       title,
                    "webpage_url": f"https://www.youtube.com/watch?v={vid_id}",
                    "duration":    int(entry.get("duration") or 0),
                    "thumbnail":   thumb,
                    "uploader":    entry.get("uploader") or entry.get("channel") or "",
                    "view_count":  int(entry.get("view_count") or 0),
                })
                if len(results) >= max_results:
                    break
    except Exception as e:
        log.debug("search_multiple error: %s", e)
    return results


async def search_multiple(query: str, max_results: int = 5) -> list[dict]:
    """Async: search YouTube and return top N results."""
    loop = asyncio.get_running_loop()
    return await loop.run_in_executor(_exec, _search_multiple_sync, query, max_results)


# ══════════════════════════════════════════════════════════════════
# HD THUMBNAIL HELPER
# ══════════════════════════════════════════════════════════════════

async def get_hd_thumbnail(video_url_or_id: str) -> str:
    """
    Fetch the highest-resolution YouTube thumbnail available.
    
    Priority order:
      1. maxresdefault (1280×720)
      2. sddefault     (640×480)  
      3. hqdefault     (480×360)
      4. mqdefault     (320×180)
      5. Original thumbnail URL (passed through unchanged)
    
    Returns a working URL string, or the original URL as fallback.
    """
    if not video_url_or_id:
        return ""

    # Extract video ID from URL or use as-is if already an ID
    vid_id = _extract_video_id(video_url_or_id) if "/" in video_url_or_id or "." in video_url_or_id else video_url_or_id
    if not vid_id:
        return video_url_or_id  # return unchanged if not a YouTube URL

    qualities = ["maxresdefault", "sddefault", "hqdefault", "mqdefault"]
    
    try:
        async with aiohttp.ClientSession(
            timeout=aiohttp.ClientTimeout(total=5),
            headers={"User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36"},
        ) as session:
            for quality in qualities:
                thumb_url = f"https://i.ytimg.com/vi/{vid_id}/{quality}.jpg"
                try:
                    async with session.head(thumb_url, allow_redirects=True) as resp:
                        if resp.status == 200:
                            # Check it's a real image and not the gray placeholder (< 2KB)
                            content_length = int(resp.headers.get("Content-Length", "9999"))
                            if content_length > 2000:
                                return thumb_url
                except Exception:
                    continue
    except Exception:
        pass
    
    # Final fallback: use hqdefault without checking (always exists for valid videos)
    if vid_id:
        return f"https://i.ytimg.com/vi/{vid_id}/hqdefault.jpg"
    return video_url_or_id


# search_and_resolve — high-level helper used by plugins/play.py
# ══════════════════════════════════════════════════════════════════

async def search_and_resolve(query: str, video: bool = False) -> "Song | None":
    """
    Search YouTube for *query* and resolve a playable stream URL.

    Returns a populated ``Song`` dataclass (from helpers/queue.py) or None.

    Steps:
      1. search_song() — dual-engine parallel search (yt-dlp + Invidious)
      2. get_stream()  — resolve stream URL with caching / FIFO pipe
      3. Wrap result in Song and return.
    """
    from helpers.queue import Song

    try:
        info = await search_song(query, is_video=video)
    except Exception as exc:
        log.warning("search_and_resolve: search_song failed: %s", exc)
        return None

    if not info:
        return None

    webpage_url = info.get("webpage_url") or ""
    title       = info.get("title") or query[:100]
    thumbnail   = info.get("thumbnail") or ""
    duration    = info.get("duration") or 0
    uploader    = info.get("uploader") or ""

    if not webpage_url:
        log.warning("search_and_resolve: no webpage_url in search result for %r", query[:50])
        return None

    # Resolve stream URL
    try:
        stream_url, audio_url, dur, http_headers = await get_stream(webpage_url, is_video=video)
    except Exception as exc:
        log.error("search_and_resolve: get_stream failed for %r: %s", webpage_url[:80], exc)
        return None

    if not stream_url:
        log.warning("search_and_resolve: get_stream returned empty URL for %r", webpage_url[:80])
        return None

    duration = dur or duration  # prefer resolved duration

    return Song(
        title        = title,
        url          = stream_url,
        duration     = duration,
        webpage_url  = webpage_url,
        thumbnail    = thumbnail,
        source       = "youtube",
        is_video     = video,
        http_headers = http_headers or {},
        artist       = uploader,
    )
