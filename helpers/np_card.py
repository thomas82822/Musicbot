"""
np_card.py — Now Playing Card v2.0  (Screenshot-matched style)
================================================================
Screenshot style ka HD playing card banata hai:
  • Dark gradient background
  • Circular album art thumbnail (centered top)
  • "PLAYING NOW" gold text + song info on right side
  • Views / Duration / Channel metadata
  • Bot name watermark at top-left
"""

from __future__ import annotations

import asyncio
import io
import logging
import os
import tempfile

log = logging.getLogger("ApexBot.np_card")


# ─────────────────────────────────────────────────────────────────
#  Asset helpers
# ─────────────────────────────────────────────────────────────────

async def _fetch_bytes(url: str) -> bytes | None:
    try:
        import aiohttp
        async with aiohttp.ClientSession() as s:
            async with s.get(url, timeout=aiohttp.ClientTimeout(total=9)) as r:
                if r.status == 200:
                    return await r.read()
    except Exception as e:
        log.debug("_fetch_bytes %s: %s", url, e)
    return None


async def _get_user_photo_bytes(user_id: int) -> bytes | None:
    if not user_id:
        return None
    try:
        from clients import bot
        photos = await bot.get_profile_photos(user_id, limit=1)
        if not photos or not photos.total_count:
            return None
        data = await bot.download_media(photos.photos[0], in_memory=True)
        return data.getvalue() if hasattr(data, "getvalue") else bytes(data)
    except Exception as e:
        log.debug("get_user_photo user=%s: %s", user_id, e)
    return None


def _circle_crop(img, size: int):
    from PIL import Image, ImageDraw
    img = img.convert("RGBA").resize((size, size), Image.LANCZOS)
    mask = Image.new("L", (size, size), 0)
    ImageDraw.Draw(mask).ellipse((0, 0, size, size), fill=255)
    result = Image.new("RGBA", (size, size), (0, 0, 0, 0))
    result.paste(img, (0, 0), mask)
    return result


def _ring(inner_img, ring_px: int = 5, ring_color=(255, 215, 0, 255)):
    from PIL import Image, ImageDraw
    s = inner_img.size[0]
    total = s + ring_px * 2
    canvas = Image.new("RGBA", (total, total), (0, 0, 0, 0))
    ImageDraw.Draw(canvas).ellipse((0, 0, total - 1, total - 1), fill=ring_color)
    canvas.paste(inner_img, (ring_px, ring_px), inner_img)
    return canvas


def _dominant_color(img) -> tuple[int, int, int]:
    try:
        small = img.convert("RGB").resize((10, 10), Image.BILINEAR)
        pixels = list(small.getdata())
        avg = tuple(sum(c[i] for c in pixels) // len(pixels) for i in range(3))
        return avg  # type: ignore
    except Exception:
        return (80, 80, 200)


def _load_font(path: str, size: int):
    from PIL import ImageFont
    try:
        return ImageFont.truetype(path, size)
    except Exception:
        return ImageFont.load_default()


def _find_font(bold: bool = False) -> str:
    """Best available font path."""
    candidates = []
    if bold:
        candidates = [
            "/usr/share/fonts/truetype/dejavu/DejaVuSans-Bold.ttf",
            "/usr/share/fonts/truetype/liberation/LiberationSans-Bold.ttf",
            "/usr/share/fonts/truetype/noto/NotoSans-Bold.ttf",
        ]
    else:
        candidates = [
            "/usr/share/fonts/truetype/dejavu/DejaVuSans.ttf",
            "/usr/share/fonts/truetype/liberation/LiberationSans-Regular.ttf",
            "/usr/share/fonts/truetype/noto/NotoSans-Regular.ttf",
        ]
    for p in candidates:
        if os.path.exists(p):
            return p
    return ""


# ─────────────────────────────────────────────────────────────────
#  Main card generator  (screenshot style)
# ─────────────────────────────────────────────────────────────────

async def create_np_thumbnail(
    thumb_url: str,
    user_id: int = 0,
    song_title: str = "",
    artist: str = "",
    views: str = "",
    duration_str: str = "",
    channel: str = "",
    bot_name: str = "",
) -> str | None:
    """
    Screenshot-style Now Playing card banao.

    Layout (800×420):
      Left half  → circular album art with gold ring
      Right half → PLAYING NOW (gold) + title (white bold) + metadata rows
      Top-left   → bot name watermark
      Background → dark gradient (near-black) + subtle blurred thumb overlay
    """
    try:
        from PIL import Image, ImageDraw, ImageFilter, ImageFont
    except ImportError:
        log.debug("Pillow not available — skipping NP card")
        return None

    thumb_bytes = await _fetch_bytes(thumb_url) if thumb_url else None
    if not thumb_bytes:
        return None

    try:
        W, H = 800, 420

        thumb_src = Image.open(io.BytesIO(thumb_bytes)).convert("RGB")

        # ── Background: very dark blurred thumbnail ───────────────
        bg_blur = thumb_src.resize((W, H), Image.LANCZOS)
        bg_blur = bg_blur.filter(ImageFilter.GaussianBlur(radius=30))

        # Dark overlay: make it near-black
        dark_overlay = Image.new("RGBA", (W, H), (10, 10, 18, 210))
        canvas = bg_blur.convert("RGBA")
        canvas = Image.alpha_composite(canvas, dark_overlay)

        draw = ImageDraw.Draw(canvas)

        # ── Load fonts ────────────────────────────────────────────
        fp_bold   = _find_font(bold=True)
        fp_reg    = _find_font(bold=False)
        f_bot     = _load_font(fp_reg,  20)   # bot name watermark
        f_playing = _load_font(fp_bold, 26)   # "PLAYING NOW"
        f_title   = _load_font(fp_bold, 30)   # song title
        f_meta    = _load_font(fp_reg,  20)   # views/dur/channel
        f_label   = _load_font(fp_bold, 20)   # metadata labels

        # ── Bot name watermark (top-left) ─────────────────────────
        if bot_name:
            draw.text((18, 14), bot_name, font=f_bot, fill=(180, 180, 200, 180))

        # ── Left side: circular album art ─────────────────────────
        circ_size = 230
        circ_img  = _circle_crop(thumb_src, circ_size)
        accent    = _dominant_color(thumb_src)

        # Gold ring
        ringed = _ring(circ_img, ring_px=6, ring_color=(255, 215, 0, 255))

        # Center vertically in left half, some top padding for watermark
        circ_x = 40
        circ_y = (H - ringed.size[0]) // 2 + 10
        canvas.paste(ringed, (circ_x, circ_y), ringed)

        # ── Right side: song info ─────────────────────────────────
        rx = circ_x + ringed.size[0] + 30   # start x for right section
        ry = circ_y + 15                     # start y

        # "PLAYING NOW" — gold / yellow
        draw.text((rx, ry), "PLAYING NOW", font=f_playing, fill=(255, 215, 0, 255))
        ry += 38

        # Song title — white bold, truncated
        title_disp = (song_title[:32] + "…") if len(song_title) > 32 else song_title
        title_disp = title_disp.upper()
        draw.text((rx, ry), title_disp, font=f_title, fill=(255, 255, 255, 255))
        ry += 44

        # Separator line
        line_end_x = W - 30
        draw.line([(rx, ry), (line_end_x, ry)], fill=(255, 215, 0, 120), width=1)
        ry += 14

        # Metadata rows: icon + label + value
        def _meta_row(icon: str, label: str, value: str, y: int) -> int:
            row_text = f"{icon}  {label}: {value}"
            draw.text((rx, y), row_text, font=f_meta, fill=(200, 200, 220, 220))
            return y + 30

        if views:
            ry = _meta_row("◈", "Views", views, ry)
        if duration_str:
            ry = _meta_row("◈", "Duration", duration_str, ry)
        if channel:
            ry = _meta_row("◈", "Channel", channel[:28], ry)
        if artist:
            ry = _meta_row("◈", "Artist", artist[:28], ry)

        # ── Gold accent bar at bottom ─────────────────────────────
        bar_h = 5
        draw.rectangle([(0, H - bar_h), (W, H)], fill=(255, 215, 0, 255))

        # Subtle left glow from accent color
        for i in range(8):
            alpha = int(60 * (1 - i / 8))
            draw.rectangle(
                [(circ_x - i, circ_y - i),
                 (circ_x + ringed.size[0] + i, circ_y + ringed.size[1] + i)],
                outline=(*accent, alpha),
            )

        # ── Save ──────────────────────────────────────────────────
        out = canvas.convert("RGB")
        tmp = tempfile.NamedTemporaryFile(
            suffix=".jpg", delete=False, dir="/tmp", prefix="np_card_")
        out.save(tmp.name, "JPEG", quality=92, optimize=True)
        tmp.close()
        return tmp.name

    except Exception as e:
        log.warning("create_np_thumbnail failed: %s", e)
        return None


# ─────────────────────────────────────────────────────────────────
#  generate_np_card — convenience wrapper used by plugins/thumbnail.py
#  Returns raw JPEG bytes (or raises on failure).
# ─────────────────────────────────────────────────────────────────

def _fmt_dur(secs: int) -> str:
    if not secs or secs <= 0:
        return ""
    m, s = divmod(int(secs), 60)
    h, m2 = divmod(m, 60)
    return f"{h}:{m2:02d}:{s:02d}" if h else f"{m}:{s:02d}"


async def generate_np_card(
    title: str = "",
    artist: str = "",
    duration: int = 0,
    thumbnail_url: str = "",
    requested_by: str = "",
    user_id: int = 0,
) -> bytes:
    """
    Generate a Now Playing card and return its raw JPEG bytes.

    Wrapper around create_np_thumbnail() for plugins/thumbnail.py.
    Raises RuntimeError if card generation fails.
    """
    path = await create_np_thumbnail(
        thumb_url=thumbnail_url,
        user_id=user_id,
        song_title=title,
        artist=artist,
        duration_str=_fmt_dur(duration),
        channel=requested_by,
    )
    if not path:
        raise RuntimeError("Card generation failed (Pillow unavailable or no thumbnail)")
    try:
        with open(path, "rb") as fh:
            return fh.read()
    finally:
        try:
            import os as _os
            _os.remove(path)
        except Exception:
            pass
