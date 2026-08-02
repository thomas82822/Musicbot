"""
custom_emoji_sender.py — 4ST Music Bot v7.0
=============================================
Non-premium accounts ke liye Telegram Custom Emoji support.

PROBLEM:
  <tg-emoji> HTML tags sirf Telegram Premium accounts se kaam karte hain.
  Non-premium bots (jaise CoOwner_Assistant_Bot) agar <tg-emoji> tags bhejne
  ki koshish karein to Telegram 400 BAD_REQUEST deta hai — aur message silently
  fail ho jaata hai.

SOLUTION:
  Bot API ka MessageEntity(type=CUSTOM_EMOJI) approach use karo.
  Ye kisi bhi bot se kaam karta hai — premium account ki zaroorat NAHI.
  Custom emoji IDs EMOJI_POOLS se auto-select hote hain, ya explicitly de sakte ho.

PUBLIC API:
  • send_custom_emoji_message(client, chat_id, text, ...)
      → custom emoji entities ke saath message bhejo
  • edit_custom_emoji_message(client, chat_id, message_id, text, ...)
      → custom emoji entities ke saath message edit karo
  • reply_custom_emoji_message(message, text, ...)
      → custom emoji entities ke saath reply karo
  • build_custom_emoji_entities(text, emoji_map=None)
      → (text, [MessageEntity]) — sirf entities chahiye to use karo

SIMPLE USAGE (single emoji):
  await send_custom_emoji_message(
      client=bot,
      chat_id=msg.chat.id,
      text="🎵 Ab music enjoy karo!",
      reply_to_message_id=msg.id,
  )

EXPLICIT EMOJI ID usage:
  await send_custom_emoji_message(
      client=bot,
      chat_id=msg.chat.id,
      text="🎵 Playing your track...",
      custom_emoji_id="6127406790666623284",   # specific ID
      emoji_char="🎵",                          # which char to replace
  )

MULTIPLE EMOJIS via map:
  await send_custom_emoji_message(
      client=bot,
      chat_id=msg.chat.id,
      text="✅ Done! 🎵 Enjoy!",
      emoji_map={
          "✅": "6199693070238227698",
          "🎵": "6127406790666623284",
      },
  )

HOW IT WORKS:
  1. Text scan karo: har known emoji ka UTF-16 offset nikalo
  2. MessageEntity(CUSTOM_EMOJI, offset, length, custom_emoji_id=...) banao
  3. client.send_message(..., entities=[...]) se bhejo — parse_mode NAHI
  4. Telegram clients pe animated premium emoji render hoga
"""

from __future__ import annotations

import logging
import random
import re
from typing import Any

log = logging.getLogger("ApexBot.custom_emoji_sender")

# ── UTF-16 helpers ────────────────────────────────────────────────────────────

def _utf16_len(s: str) -> int:
    """String ka UTF-16 code-unit length — Telegram entity offsets ke liye zaroori."""
    return len(s.encode("utf-16-le")) // 2


# ── Core entity builder ───────────────────────────────────────────────────────

def build_custom_emoji_entities(
    text: str,
    emoji_map: dict[str, str] | None = None,
) -> tuple[str, list]:
    """
    Text mein har known emoji ki jagah MessageEntity(CUSTOM_EMOJI) banao.

    Args:
        text:      Source message text (normal emojis ke saath)
        emoji_map: { emoji_char: custom_emoji_id_str } override.
                   None dene par EMOJI_POOLS se random ID pick hoga.

    Returns:
        (text, [MessageEntity])  — text unchanged; entities inject karo send_message mein.

    Note:
        Ye function SIRF custom_emoji entities banata hai.
        Text formatting (bold/italic/code) ke liye alag se parse_mode use karo —
        lekin dhyan raho: Telegram Bot API mein parse_mode aur entities ek saath
        use nahi ho sakte (entities pass karte waqt parse_mode ignore hoti hai).
        Agar formatting bhi chahiye, use _md_to_plain_and_entities() (internal).
    """
    try:
        from pyrogram.types import MessageEntity
        from pyrogram.enums import MessageEntityType
        from helpers.premium_emojis import EMOJI_POOLS, _SORTED_EMOJIS
    except ImportError as exc:
        log.warning("build_custom_emoji_entities: import error — %s", exc)
        return text, []

    # Effective mapping: override > EMOJI_POOLS random pick
    def _get_id(emoji_char: str) -> str | None:
        if emoji_map and emoji_char in emoji_map:
            return emoji_map[emoji_char]
        if emoji_char in EMOJI_POOLS:
            return random.choice(EMOJI_POOLS[emoji_char])
        return None

    entities: list = []
    offset = 0   # running UTF-16 offset
    i = 0

    while i < len(text):
        # Longest-first scan so multi-codepoint emojis (⚠️ > ⚠) match first
        matched_emoji: str | None = None
        for e in _SORTED_EMOJIS:
            if text[i:].startswith(e):
                matched_emoji = e
                break

        if matched_emoji:
            eid = _get_id(matched_emoji)
            u16_len = _utf16_len(matched_emoji)
            if eid:
                try:
                    entities.append(
                        MessageEntity(
                            type=MessageEntityType.CUSTOM_EMOJI,
                            offset=offset,
                            length=u16_len,
                            custom_emoji_id=int(eid),
                        )
                    )
                except Exception as exc:
                    log.debug("Entity build failed for %s: %s", matched_emoji, exc)
            offset += u16_len
            i += len(matched_emoji)
        else:
            offset += _utf16_len(text[i])
            i += 1

    return text, entities


# ── HTML-aware entity builder (formatting + custom emoji) ──────────────────────
# Many plugins build "ready" Telegram HTML strings (e.g. "<blockquote>🎵 <b>NOW
# PLAYING</b></blockquote>") and send them with parse_mode=ParseMode.HTML. The
# Bot API does not allow `entities=` and `parse_mode=` together, so the old
# non-premium entity injector simply skipped any text that already looked like
# HTML — meaning premium custom-emoji entities were NEVER attached to those
# messages (this is most of the bot's DM/UI output: now-playing cards, /start,
# /help, error replies, etc.). This converts the HTML itself into plain text +
# a merged entity list (formatting entities + CUSTOM_EMOJI entities) so both
# can be sent together via `entities=`.

# Flexible regex: matches ANY HTML tag with ANY attributes.
# The old regex only handled href= and failed silently on tags like
# <blockquote expandable>, causing literal tag text to appear in messages.
_HTML_TAG_RE = re.compile(
    r'<(/?)([a-zA-Z][a-zA-Z0-9-]*)([^>]*)>',
    re.IGNORECASE,
)

# Separate helper to extract href value from a raw attributes string.
_HREF_ATTR_RE = re.compile(r'''href=(?:"([^"]*)"|'([^']*)')''', re.IGNORECASE)

_HTML_TAG_TO_ENTITY_TYPE = {
    "b": "BOLD", "strong": "BOLD",
    "i": "ITALIC", "em": "ITALIC",
    "u": "UNDERLINE", "ins": "UNDERLINE",
    "s": "STRIKETHROUGH", "strike": "STRIKETHROUGH", "del": "STRIKETHROUGH",
    "code": "CODE",
    "pre": "PRE",
    "blockquote": "BLOCKQUOTE",
    "tg-spoiler": "SPOILER", "spoiler": "SPOILER",
}

_HTML_UNESCAPE_MAP = (
    ("&lt;", "<"), ("&gt;", ">"), ("&quot;", '"'), ("&#39;", "'"), ("&apos;", "'"),
    ("&amp;", "&"),  # must run last so "&amp;lt;" etc. don't double-unescape
)


def _unescape_html(s: str) -> str:
    for needle, repl in _HTML_UNESCAPE_MAP:
        s = s.replace(needle, repl)
    return s


def html_to_plain_and_entities(html: str) -> tuple[str, list, bool]:
    """
    Convert already-built Telegram HTML (with literal emoji chars in its text
    nodes) into (plain_text, [MessageEntity], had_custom_emoji).

    Supports the tags this bot actually emits: b/strong, i/em, u/ins,
    s/strike/del, code, pre, blockquote, blockquote[expandable],
    tg-spoiler/spoiler, tg-emoji (stripped to inner char), a[href], and any
    other tag (unknown tags are skipped — inner text is still included).
    Assumes well-formed, properly-nested markup (true for all programmatically
    built captions in this codebase).
    """
    try:
        from pyrogram.types import MessageEntity
        from pyrogram.enums import MessageEntityType
        from helpers.premium_emojis import EMOJI_POOLS, _SORTED_EMOJIS
    except ImportError as exc:
        log.warning("html_to_plain_and_entities: import error — %s", exc)
        return html, [], False

    if not isinstance(html, str) or not html:
        return html, [], False

    entities: list = []
    stack: list[tuple[str, int, str | None]] = []  # (kind, start_offset, url)
    output: list[str] = []
    offset = 0
    had_emoji = False

    def _append_text(s: str) -> None:
        nonlocal offset, had_emoji
        s = _unescape_html(s)
        i = 0
        while i < len(s):
            matched_emoji: str | None = None
            for e in _SORTED_EMOJIS:
                if s[i:].startswith(e):
                    matched_emoji = e
                    break
            if matched_emoji:
                eid = random.choice(EMOJI_POOLS[matched_emoji])
                u16_len = _utf16_len(matched_emoji)
                start = offset
                output.append(matched_emoji)
                offset += u16_len
                try:
                    entities.append(
                        MessageEntity(
                            type=MessageEntityType.CUSTOM_EMOJI,
                            offset=start, length=u16_len,
                            custom_emoji_id=int(eid),
                        )
                    )
                    had_emoji = True
                except Exception as exc:
                    log.debug("html_to_plain_and_entities: entity build failed for %s: %s", matched_emoji, exc)
                i += len(matched_emoji)
            else:
                ch = s[i]
                output.append(ch)
                offset += _utf16_len(ch)
                i += 1

    pos = 0
    for m in _HTML_TAG_RE.finditer(html):
        _append_text(html[pos:m.start()])
        closing  = m.group(1)                  # "/" for closing tags, "" for opening
        tag      = m.group(2).lower()           # tag name (e.g. "blockquote", "b")
        attrs_str = m.group(3) or ""            # raw attribute string (e.g. " expandable")

        # Extract href from attributes (for <a href="...">)
        _hm = _HREF_ATTR_RE.search(attrs_str)
        href = (_hm.group(1) if _hm.group(1) is not None else _hm.group(2)) if _hm else None

        if tag == "a":
            if closing:
                for j in range(len(stack) - 1, -1, -1):
                    if stack[j][0] == "a":
                        _, start, url = stack.pop(j)
                        length = offset - start
                        if length > 0:
                            try:
                                entities.append(MessageEntity(
                                    type=MessageEntityType.TEXT_LINK,
                                    offset=start, length=length, url=url or "",
                                ))
                            except Exception as _exc:
                                log.debug("html_to_plain_and_entities: TEXT_LINK failed: %s", _exc)
                        break
            else:
                stack.append(("a", offset, href))
        else:
            # Resolve entity kind.  For <blockquote expandable>, try
            # EXPANDABLE_BLOCKQUOTE first (newer pyrofork); fall back to BLOCKQUOTE.
            if tag == "blockquote" and not closing and "expandable" in attrs_str.lower():
                kind = (
                    "EXPANDABLE_BLOCKQUOTE"
                    if hasattr(MessageEntityType, "EXPANDABLE_BLOCKQUOTE")
                    else "BLOCKQUOTE"
                )
            else:
                kind = _HTML_TAG_TO_ENTITY_TYPE.get(tag)

            if kind:
                if closing:
                    # Match the most-recent open tag of this kind OR blockquote
                    # family (BLOCKQUOTE / EXPANDABLE_BLOCKQUOTE both close on </blockquote>)
                    blockquote_family = {"BLOCKQUOTE", "EXPANDABLE_BLOCKQUOTE"}
                    for j in range(len(stack) - 1, -1, -1):
                        stack_kind = stack[j][0]
                        if stack_kind == kind or (
                            kind in blockquote_family and stack_kind in blockquote_family
                        ):
                            _, start, _url = stack.pop(j)
                            length = offset - start
                            if length > 0:
                                try:
                                    entities.append(MessageEntity(
                                        type=getattr(MessageEntityType, stack_kind),
                                        offset=start, length=length,
                                    ))
                                except (AttributeError, ValueError, TypeError) as _exc:
                                    log.debug(
                                        "html_to_plain_and_entities: unsupported entity "
                                        "type '%s': %s", stack_kind, _exc,
                                    )
                            break
                else:
                    stack.append((kind, offset, None))
        pos = m.end()

    _append_text(html[pos:])

    return "".join(output), entities, had_emoji


# ── Markdown-aware entity builder (formatting + custom emoji) ─────────────────

def _md_to_plain_and_entities(text: str) -> tuple[str, list]:
    """
    Markdown text → (plain_text, merged_entity_list).

    Handles:
      **bold**         → MessageEntityType.BOLD
      __italic__       → MessageEntityType.ITALIC
      `code`           → MessageEntityType.CODE
      [label](url)     → MessageEntityType.TEXT_LINK
      ||spoiler||      → MessageEntityType.SPOILER
      emoji chars      → MessageEntityType.CUSTOM_EMOJI  (EMOJI_POOLS)

    NOTE: Ye internal helper hai. Sirf tab use karo jab formatting + custom
          emojis dono ek saath chahiye hon bina parse_mode ke.
    """
    import re as _re

    try:
        from pyrogram.types import MessageEntity
        from pyrogram.enums import MessageEntityType
        from helpers.premium_emojis import EMOJI_POOLS, _SORTED_EMOJIS
    except ImportError as exc:
        log.warning("_md_to_plain_and_entities: import error — %s", exc)
        return text, []

    entities: list = []
    output_chars: list[str] = []     # plain text chars
    offset = 0                        # running UTF-16 offset of output

    def _append(s: str) -> int:
        """Append string to output, return its UTF-16 length."""
        nonlocal offset
        l = _utf16_len(s)
        output_chars.append(s)
        offset += l
        return l

    # ── Pre-pass: extract links [label](url) since they involve removing markup ──
    # Replace with placeholder tokens, process rest linearly, then restore.
    LINK_RE = _re.compile(r'\[([^\]]+)\]\((https?://[^\s)]+)\)')
    link_slots: list[tuple[str, str]] = []   # (label, url) in order

    def _stash_link(m: _re.Match) -> str:
        link_slots.append((m.group(1), m.group(2)))
        return f"\x00LINK{len(link_slots)-1}\x00"

    text = LINK_RE.sub(_stash_link, text)

    # ── Linear scan ──────────────────────────────────────────────────────────
    i = 0
    length = len(text)

    while i < length:
        ch = text[i]

        # ── Link placeholder ────────────────────────────────────────────────
        if ch == '\x00' and text[i:i+5].startswith('\x00LINK'):
            end = text.index('\x00', i + 1)
            slot_idx = int(text[i+5:end])
            label, url = link_slots[slot_idx]
            start = offset
            l = _append(label)
            entities.append(MessageEntity(
                type=MessageEntityType.TEXT_LINK,
                offset=start, length=l, url=url,
            ))
            i = end + 1
            continue

        # ── Spoiler ||text|| ────────────────────────────────────────────────
        if text[i:i+2] == '||':
            end = text.find('||', i + 2)
            if end != -1:
                inner = text[i+2:end]
                start = offset
                l = _append(inner)
                entities.append(MessageEntity(
                    type=MessageEntityType.SPOILER, offset=start, length=l,
                ))
                i = end + 2
                continue

        # ── Bold+italic ***text*** ──────────────────────────────────────────
        if text[i:i+3] == '***':
            end = text.find('***', i + 3)
            if end != -1:
                inner = text[i+3:end]
                start = offset
                l = _append(inner)
                entities.append(MessageEntity(type=MessageEntityType.BOLD,   offset=start, length=l))
                entities.append(MessageEntity(type=MessageEntityType.ITALIC, offset=start, length=l))
                i = end + 3
                continue

        # ── Bold **text** ──────────────────────────────────────────────────
        if text[i:i+2] == '**':
            end = text.find('**', i + 2)
            if end != -1:
                inner = text[i+2:end]
                start = offset
                l = _append(inner)
                entities.append(MessageEntity(type=MessageEntityType.BOLD, offset=start, length=l))
                i = end + 2
                continue

        # ── Italic __text__ ────────────────────────────────────────────────
        if text[i:i+2] == '__':
            end = text.find('__', i + 2)
            if end != -1:
                inner = text[i+2:end]
                start = offset
                l = _append(inner)
                entities.append(MessageEntity(type=MessageEntityType.ITALIC, offset=start, length=l))
                i = end + 2
                continue

        # ── Italic _text_ (not inside words) ───────────────────────────────
        if ch == '_' and (i == 0 or not text[i-1].isalnum()):
            end = text.find('_', i + 1)
            if end != -1 and not text[end+1:end+2].isalnum():
                inner = text[i+1:end]
                start = offset
                l = _append(inner)
                entities.append(MessageEntity(type=MessageEntityType.ITALIC, offset=start, length=l))
                i = end + 1
                continue

        # ── Code block ```text``` ──────────────────────────────────────────
        if text[i:i+3] == '```':
            end = text.find('```', i + 3)
            if end != -1:
                inner = text[i+3:end]
                start = offset
                l = _append(inner)
                entities.append(MessageEntity(type=MessageEntityType.PRE, offset=start, length=l))
                i = end + 3
                continue

        # ── Inline code `text` ─────────────────────────────────────────────
        if ch == '`':
            end = text.find('`', i + 1)
            if end != -1 and '\n' not in text[i+1:end]:
                inner = text[i+1:end]
                start = offset
                l = _append(inner)
                entities.append(MessageEntity(type=MessageEntityType.CODE, offset=start, length=l))
                i = end + 1
                continue

        # ── Emoji (longest-first from EMOJI_POOLS) ─────────────────────────
        matched_emoji: str | None = None
        for e in _SORTED_EMOJIS:
            if text[i:].startswith(e):
                matched_emoji = e
                break

        if matched_emoji:
            eid = random.choice(EMOJI_POOLS[matched_emoji])
            u16_len = _utf16_len(matched_emoji)
            start = offset
            output_chars.append(matched_emoji)
            offset += u16_len
            try:
                entities.append(
                    MessageEntity(
                        type=MessageEntityType.CUSTOM_EMOJI,
                        offset=start, length=u16_len,
                        custom_emoji_id=int(eid),
                    )
                )
            except Exception as exc:
                log.debug("Emoji entity failed for %s: %s", matched_emoji, exc)
            i += len(matched_emoji)
            continue

        # ── Plain character ────────────────────────────────────────────────
        _append(ch)
        i += 1

    return "".join(output_chars), entities


# ── Public sender functions ───────────────────────────────────────────────────

async def send_custom_emoji_message(
    client: Any,
    chat_id: int | str,
    text: str,
    *,
    custom_emoji_id: str | int | None = None,
    emoji_char: str | None = None,
    emoji_map: dict[str, str] | None = None,
    parse_markdown: bool = False,
    reply_to_message_id: int | None = None,
    reply_markup: Any = None,
    disable_web_page_preview: bool = True,
    **kwargs: Any,
) -> Any:
    """
    Custom emoji entities ke saath message bhejo — bina premium account ke.

    Non-premium bots ke liye MessageEntity(CUSTOM_EMOJI) approach use karta hai.
    Telegram Premium <tg-emoji> HTML tags ki zaroorat nahi.

    Args:
        client:               Pyrogram Client instance (bot ya assistant)
        chat_id:              Target chat ID ya username
        text:                 Message text (normal emojis allowed)
        custom_emoji_id:      Specific custom emoji ID (emoji_char ke saath use karo)
        emoji_char:           Konsa emoji char replace hoga (custom_emoji_id ke saath)
        emoji_map:            { emoji_char: custom_emoji_id } — multiple emojis
        parse_markdown:       True karo agar text mein **bold**/__italic__ etc. ho
                              aur formatting bhi chahiye (entities-only mode)
        reply_to_message_id:  (optional) Message ID to reply to
        reply_markup:         (optional) InlineKeyboardMarkup etc.
        disable_web_page_preview: Web preview disable kare (default True)
        **kwargs:             Extra args pass-through to send_message

    Returns:
        Sent pyrogram.types.Message object

    Examples:
        # Auto-map all emojis from EMOJI_POOLS:
        await send_custom_emoji_message(bot, chat.id, "🎵 Playing track!")

        # Specific emoji ID:
        await send_custom_emoji_message(
            bot, chat.id, "🎵 Playing track!",
            custom_emoji_id="6127406790666623284",
            emoji_char="🎵",
        )

        # Multiple emojis + keep markdown formatting:
        await send_custom_emoji_message(
            bot, chat.id, "✅ **Done!** 🎵 Enjoy!",
            parse_markdown=True,
        )
    """
    try:
        # Build effective emoji_map
        effective_map: dict[str, str] | None = emoji_map
        if custom_emoji_id is not None and emoji_char is not None:
            effective_map = dict(emoji_map or {})
            effective_map[emoji_char] = str(custom_emoji_id)

        # Build entities
        if parse_markdown:
            # Full markdown parse + custom emoji entities (no parse_mode)
            final_text, entities = _md_to_plain_and_entities(text)
        else:
            # Emoji entities only; text unchanged (use with parse_mode if needed)
            final_text, entities = build_custom_emoji_entities(text, effective_map)

        send_kwargs: dict[str, Any] = {
            "chat_id": chat_id,
            "text": final_text,
            "disable_web_page_preview": disable_web_page_preview,
            **kwargs,
        }
        if entities:
            send_kwargs["entities"] = entities
            # When entities are provided, parse_mode should not be set
            send_kwargs.pop("parse_mode", None)
        if reply_to_message_id is not None:
            send_kwargs["reply_to_message_id"] = reply_to_message_id
        if reply_markup is not None:
            send_kwargs["reply_markup"] = reply_markup

        return await client.send_message(**send_kwargs)

    except Exception as exc:
        log.error("send_custom_emoji_message failed: %s — falling back to plain send", exc)
        # Graceful fallback: plain message, no entities
        return await client.send_message(
            chat_id=chat_id,
            text=text,
            reply_to_message_id=reply_to_message_id,
            reply_markup=reply_markup,
            disable_web_page_preview=disable_web_page_preview,
        )


async def edit_custom_emoji_message(
    client: Any,
    chat_id: int | str,
    message_id: int,
    text: str,
    *,
    custom_emoji_id: str | int | None = None,
    emoji_char: str | None = None,
    emoji_map: dict[str, str] | None = None,
    parse_markdown: bool = False,
    reply_markup: Any = None,
    **kwargs: Any,
) -> Any:
    """
    Custom emoji entities ke saath existing message edit karo.

    Args: (same as send_custom_emoji_message, minus reply_to_message_id)

    Returns:
        Edited pyrogram.types.Message object
    """
    try:
        effective_map = dict(emoji_map or {})
        if custom_emoji_id is not None and emoji_char is not None:
            effective_map[emoji_char] = str(custom_emoji_id)

        if parse_markdown:
            final_text, entities = _md_to_plain_and_entities(text)
        else:
            final_text, entities = build_custom_emoji_entities(
                text, effective_map or None
            )

        edit_kwargs: dict[str, Any] = {
            "chat_id": chat_id,
            "message_id": message_id,
            "text": final_text,
            **kwargs,
        }
        if entities:
            edit_kwargs["entities"] = entities
            edit_kwargs.pop("parse_mode", None)
        if reply_markup is not None:
            edit_kwargs["reply_markup"] = reply_markup

        return await client.edit_message_text(**edit_kwargs)

    except Exception as exc:
        log.error("edit_custom_emoji_message failed: %s — falling back to plain edit", exc)
        return await client.edit_message_text(
            chat_id=chat_id,
            message_id=message_id,
            text=text,
            reply_markup=reply_markup,
        )


async def reply_custom_emoji_message(
    message: Any,
    text: str,
    *,
    custom_emoji_id: str | int | None = None,
    emoji_char: str | None = None,
    emoji_map: dict[str, str] | None = None,
    parse_markdown: bool = False,
    reply_markup: Any = None,
    **kwargs: Any,
) -> Any:
    """
    Ek existing Message ko custom emoji entities ke saath reply karo.

    Args:
        message:  pyrogram.types.Message object (jo aaya hai user se)
        text:     Reply text
        ...       (baki args same as send_custom_emoji_message)

    Returns:
        Sent reply Message object
    """
    client = getattr(message, "_client", None)
    if client is None:
        log.warning("reply_custom_emoji_message: message._client not found")
        return await message.reply(text, reply_markup=reply_markup, **kwargs)

    return await send_custom_emoji_message(
        client=client,
        chat_id=message.chat.id,
        text=text,
        custom_emoji_id=custom_emoji_id,
        emoji_char=emoji_char,
        emoji_map=emoji_map,
        parse_markdown=parse_markdown,
        reply_to_message_id=message.id,
        reply_markup=reply_markup,
        **kwargs,
    )
