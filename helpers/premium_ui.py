"""
premium_ui.py — 4ST Music Bot v7.1
=====================================
Bot ke saare outgoing messages mein Telegram custom emojis inject karta hai —
bina kisi plugin ko touch kiye.

DO MODES:
  1. Premium clients  (<tg-emoji> HTML tags):
       Telegram Premium accounts ke liye animated emoji via HTML parse mode.
       register_premium_client(client) se mark karo.

  2. Non-premium clients  (MessageEntity approach) ← NEW:
       CoOwner_Assistant_Bot jaise non-premium bots ke liye
       MessageEntity(type=CUSTOM_EMOJI) use karo — koi premium zaroorat nahi.
       <tg-emoji> tags non-premium se bhejne par Telegram 400 BAD_REQUEST
       deta hai aur message silently fail ho jaata hai.

Per-client premium tracking:
  register_premium_client(client)   → mark a client as premium-capable
  unregister_premium_client(client) → remove premium mark (e.g. on session change)
  is_client_premium(client)         → check if a client is premium-capable

IMPORTANT — signature safety:
  Pyrogram internally calls Client methods with POSITIONAL arguments
  (e.g. edit_message_text(chat_id, msg_id, text, parse_mode, entities, ...)).
  `**kwargs` ONLY captures keyword arguments, NOT extra positional ones.
  Isliye sabhi patches *args style use karte hain taaki koi bhi positional
  arg silently drop na ho aur TypeError na aaye.

v7.1 FIX — emoji send/edit graceful degradation:
  Pehle sirf send_message aur Message.reply ke paas ek chhota fallback tha
  (sirf "DOCUMENT_INVALID" string match par, aur sirf non-premium entities
  ke liye). edit_message_text, edit_message_caption, Message.edit,
  Message.edit_caption, aur saare media senders (send_photo/audio/video/
  document/animation) ke paas KOI fallback nahi tha — aur premium <tg-emoji>
  path (koi bhi method) ke paas bhi kabhi koi fallback nahi tha. Iska matlab
  jab Telegram ek invalid custom-emoji ID ya kisi wajah se entity/tag reject
  karta tha (jo edits ke through zyada hota hai — skip/restart/resume jaisi
  now-playing card updates), poora message/edit fail ho jaata tha ya crash.

  Ab HAR patched method (send + edit, premium + non-premium) same generic
  fallback use karta hai: agar Telegram entity/emoji-related error de,
  to custom-emoji entities ya <tg-emoji> tags dono hata ke plain text ke
  saath ek retry hota hai, taaki user ko kam se kam plain message/edit
  dikhe instead of silent failure.
"""

from __future__ import annotations

import logging
import re
from typing import Any

log = logging.getLogger("ApexBot.premium_ui")

# ── Per-client premium status registry ──────────────────────────────────────
# Stores the `name` attribute of Pyrogram Client instances that are confirmed
# Telegram Premium accounts.  Only these clients inject <tg-emoji> tags.
_PREMIUM_CLIENTS: set[str] = set()


def register_premium_client(client: Any) -> None:
    """Mark a Pyrogram Client instance as Telegram-Premium capable.

    Call this after ``client.start()`` + ``client.get_me()`` confirms
    ``me.is_premium is True``.
    """
    name = getattr(client, "name", None) or str(id(client))
    _PREMIUM_CLIENTS.add(name)
    log.info("✅ Premium emojis ENABLED for client '%s'", name)


def unregister_premium_client(client: Any) -> None:
    """Remove premium-capable flag from a client (e.g. session change)."""
    name = getattr(client, "name", None) or str(id(client))
    _PREMIUM_CLIENTS.discard(name)
    log.info("ℹ️  Premium emojis DISABLED for client '%s'", name)


def is_client_premium(client: Any) -> bool:
    """Return True if this client instance is registered as Telegram Premium."""
    name = getattr(client, "name", None) or str(id(client))
    return name in _PREMIUM_CLIENTS


# ── Public helpers (backward-compat) ────────────────────────────────────────

def is_premium_text(text: Any) -> bool:
    if not isinstance(text, str):
        return False
    return "<tg-emoji" in text


def premium_text(text: Any, *, limit: int | None = None) -> Any:
    """Convert text to premium emoji HTML. No-op if already converted."""
    if not isinstance(text, str) or not text:
        return text
    from helpers.premium_emojis import md_to_html, is_html_text
    if is_html_text(text) and "<tg-emoji" in text:
        return text
    return md_to_html(text)


# ── Entity/emoji error detection + graceful degradation ─────────────────────
# Telegram can reject a message/edit for several different reasons when the
# custom-emoji document id is invalid/inaccessible, or when a <tg-emoji> tag
# is sent from a non-premium account. Match broadly instead of a single
# substring so all of these degrade gracefully instead of failing silently.
_ENTITY_ERROR_HINTS = (
    "DOCUMENT_INVALID",
    "STICKER_INVALID",
    "CUSTOM_EMOJI_INVALID",
    "ENTITY_MENTION_USER_INVALID",
    "ENTITY_BOUNDS_INVALID",
    "MEDIA_EMPTY",
    "CAN'T PARSE ENTITIES",
    "MESSAGE_EMPTY",
)

_TG_EMOJI_TAG_RE = re.compile(r"<tg-emoji[^>]*>(.*?)</tg-emoji>", re.DOTALL | re.IGNORECASE)


def _looks_like_entity_error(exc: Exception) -> bool:
    msg = str(exc).upper()
    if any(hint in msg for hint in _ENTITY_ERROR_HINTS):
        return True
    # Catch-all: Telegram entity/emoji rejections almost always mention
    # both "EMOJI" and "INVALID" (or "ENTIT" for "entities"/"entity").
    return ("EMOJI" in msg and "INVALID" in msg) or ("ENTIT" in msg and "INVALID" in msg)


def _strip_tg_emoji_tags(text: Any) -> Any:
    """Remove <tg-emoji ...>X</tg-emoji> wrappers, keeping the inner emoji char."""
    if not isinstance(text, str) or "<tg-emoji" not in text:
        return text
    return _TG_EMOJI_TAG_RE.sub(r"\1", text)


def _degrade_payload(text: Any, kwargs: dict) -> Any:
    """
    On an entity/emoji-related send or edit failure, strip whichever emoji
    mechanism was used — non-premium MessageEntity(CUSTOM_EMOJI) entities,
    or premium <tg-emoji> HTML tags — and return safe text so the retry has
    a real chance of succeeding instead of hitting the exact same error.

    FIX: When we converted HTML → plain_text + entities (non-premium path),
    the original HTML is saved in kwargs["_apex_orig_html"].  On failure,
    restore the HTML text with parse_mode=HTML so that bold/blockquote/italic
    still render — only the custom-emoji entities are lost, not the formatting.
    Without this fix the fallback sent raw plain text with no formatting at all.
    """
    kwargs.pop("entities", None)
    orig_html = kwargs.pop("_apex_orig_html", None)
    if orig_html:
        # Restore HTML mode — formatting (bold/blockquote/italic) is kept;
        # only the custom-emoji document IDs are stripped to avoid another rejection.
        try:
            from pyrogram.enums import ParseMode
            kwargs["parse_mode"] = ParseMode.HTML
        except Exception:
            kwargs["parse_mode"] = "html"
        return _strip_tg_emoji_tags(orig_html)
    return _strip_tg_emoji_tags(text)


# ── Monkey-patch installer ───────────────────────────────────────────────────

_INSTALLED = False


def install_premium_ui() -> None:
    """
    Pyrogram Client ke key methods ko wrap karo taaki premium-registered clients
    ke saare outgoing messages mein premium animated emojis auto-inject hon.

    Non-premium clients ke messages unchanged pass through hote hain.

    Patched methods (all use *args style for positional-arg safety), every
    one of which now shares the same entity/emoji-error fallback:
      - Client.send_message           → args: chat_id, text, [parse_mode, ...]
      - Client.edit_message_text      → args: chat_id, msg_id, text, [parse_mode, ...]
      - Client.send_photo             → args: chat_id, photo, [caption/parse_mode via kw]
      - Client.send_audio             → args: chat_id, audio, [caption/parse_mode via kw]
      - Client.send_video             → args: chat_id, video, [caption/parse_mode via kw]
      - Client.send_document          → args: chat_id, doc,   [caption/parse_mode via kw]
      - Client.send_animation         → args: chat_id, anim,  [caption/parse_mode via kw]
      - Client.edit_message_caption   → args: chat_id, msg_id, caption, [parse_mode, ...]
      - Message.reply / Message.edit / Message.edit_caption
    """
    global _INSTALLED
    if _INSTALLED:
        return

    try:
        from pyrogram import Client
        from pyrogram.enums import ParseMode
        from helpers.premium_emojis import md_to_html, _apply_premium_emojis, is_html_text

        # ── Core upgrade logic ─────────────────────────────────────────────────
        def _upgrade_text(text: Any, parse_mode: Any) -> tuple[Any, Any]:
            """
            Convert text + parse_mode  →  (html_text, ParseMode.HTML).
            Rules:
              • Not a string / empty      → return as-is
              • parse_mode = DISABLED     → return as-is
              • already has <tg-emoji>    → ensure HTML mode, no double-process
              • already HTML (no emoji)   → inject emojis only
              • markdown / None / DEFAULT → full MD→HTML + inject emojis
            """
            if not isinstance(text, str) or not text:
                return text, parse_mode

            # If caller explicitly disabled parsing, skip
            try:
                if parse_mode == ParseMode.DISABLED:
                    return text, parse_mode
            except Exception:
                pass
            if isinstance(parse_mode, str) and parse_mode.lower() in ("disabled", "none"):
                return text, parse_mode

            # Already premium — just fix parse_mode
            if "<tg-emoji" in text:
                return text, ParseMode.HTML

            try:
                already_html = is_html_text(text)
                if already_html:
                    # Only inject emojis — text is already proper HTML
                    return _apply_premium_emojis(text), ParseMode.HTML
                else:
                    # Markdown (or unknown) → convert to HTML, then inject
                    return md_to_html(text), ParseMode.HTML
            except Exception as exc:
                log.debug("premium_ui._upgrade_text error (falling back): %s", exc)
                return text, parse_mode

        # ── Non-premium entity injector ───────────────────────────────────────
        # Non-premium clients ke liye MessageEntity(CUSTOM_EMOJI) approach.
        # <tg-emoji> HTML tags mat use karo — Telegram 400 deta hai non-premium se.
        def _inject_emoji_entities(text: Any, kwargs: dict) -> Any:
            """
            Non-premium clients ke liye text mein custom emoji entities inject karo.
            - 'entities' key kwargs mein set ho jaata hai
            - parse_mode removed hoti hai (entities aur parse_mode saath nahi chalte)
            - Agar koi exception aaye, silently pass-through karo

            v7.2 FIX — HTML-formatted text (parse_mode=HTML or literal <b>/
            <blockquote>/... tags) used to be skipped ENTIRELY here, because
            entities + parse_mode can't be sent together and naively stripping
            parse_mode would have shown raw HTML tags as plain text. That meant
            every HTML-built caption — now-playing cards, /start, /help, error
            replies, i.e. most of what a non-premium bot actually sends in a
            user's DM — never got premium custom-emoji entities at all.

            Fix: convert the HTML itself into (plain_text, entities) via
            html_to_plain_and_entities(), which turns both the formatting tags
            AND the emoji characters into MessageEntity objects. Only switch
            the message over to entities-mode if that conversion actually
            found premium emoji to inject — otherwise leave the original
            HTML/parse_mode path untouched.
            """
            if not isinstance(text, str) or not text:
                return text
            # Caller ne already entities provide kiye hain — don't overwrite
            if kwargs.get("entities"):
                return text

            pm_is_html = False
            try:
                from pyrogram.enums import ParseMode
                pm = kwargs.get("parse_mode")
                pm_is_html = pm == ParseMode.HTML or (isinstance(pm, str) and pm.lower() == "html")
            except Exception:
                pass

            looks_like_html = bool(re.search(r"<[a-zA-Z/][^>]*>", text))

            try:
                if pm_is_html or looks_like_html:
                    from helpers.custom_emoji_sender import html_to_plain_and_entities
                    final_text, entities, had_emoji = html_to_plain_and_entities(text)
                    if had_emoji:
                        kwargs["entities"] = entities
                        kwargs.pop("parse_mode", None)
                        # FIX: Save original HTML so _degrade_payload can fall back
                        # to HTML mode on error — preserving bold/blockquote/italic.
                        # Without this, any entity rejection strips ALL formatting.
                        kwargs["_apex_orig_html"] = text
                        return final_text
                    # No premium emoji found in this HTML — leave it exactly
                    # as the caller built it (parse_mode=HTML keeps rendering).
                    return text
                else:
                    from helpers.custom_emoji_sender import build_custom_emoji_entities
                    _, entities = build_custom_emoji_entities(text)
                    if entities:
                        kwargs["entities"] = entities
                        kwargs.pop("parse_mode", None)
            except Exception as _exc:
                log.debug("_inject_emoji_entities skipped: %s", _exc)
            return text

        # ── send_message ──────────────────────────────────────────────────────
        # Pyrogram signature (pyrofork 2.x):
        #   send_message(self, chat_id, text, parse_mode=DEFAULT, entities=None, ...)
        _orig_send_message = Client.send_message

        async def _send_message(self, chat_id, text, *args, **kwargs):
            if is_client_premium(self):
                # Premium path: HTML <tg-emoji> tags (existing behaviour)
                parse_mode = args[0] if args else kwargs.pop("parse_mode", None)
                text, parse_mode = _upgrade_text(text, parse_mode)
                kwargs["parse_mode"] = parse_mode
                remaining_args = args[1:] if args else ()
            else:
                # Non-premium path: try MessageEntity(CUSTOM_EMOJI); fallback to plain
                text = _inject_emoji_entities(text, kwargs)
                remaining_args = args
            # Pop internal tracking key — Pyrogram doesn't accept it
            _saved_orig_html = kwargs.pop("_apex_orig_html", None)
            try:
                return await _orig_send_message(self, chat_id, text, *remaining_args, **kwargs)
            except Exception as _e:
                if _looks_like_entity_error(_e):
                    if _saved_orig_html is not None:
                        kwargs["_apex_orig_html"] = _saved_orig_html
                    text = _degrade_payload(text, kwargs)
                    log.debug("send_message: emoji rejected, retrying plain: %s", _e)
                    return await _orig_send_message(self, chat_id, text, *remaining_args, **kwargs)
                raise

        Client.send_message = _send_message

        # ── edit_message_text ──────────────────────────────────────────────────
        _orig_edit_text = Client.edit_message_text

        async def _edit_message_text(self, chat_id, message_id, text, *args, **kwargs):
            if is_client_premium(self):
                parse_mode = args[0] if args else kwargs.pop("parse_mode", None)
                text, parse_mode = _upgrade_text(text, parse_mode)
                kwargs["parse_mode"] = parse_mode
                remaining_args = args[1:] if args else ()
            else:
                text = _inject_emoji_entities(text, kwargs)
                remaining_args = args
            _saved_orig_html = kwargs.pop("_apex_orig_html", None)
            try:
                return await _orig_edit_text(self, chat_id, message_id, text, *remaining_args, **kwargs)
            except Exception as _e:
                if _looks_like_entity_error(_e):
                    if _saved_orig_html is not None:
                        kwargs["_apex_orig_html"] = _saved_orig_html
                    text = _degrade_payload(text, kwargs)
                    log.debug("edit_message_text: emoji rejected, retrying plain: %s", _e)
                    return await _orig_edit_text(self, chat_id, message_id, text, *remaining_args, **kwargs)
                raise

        Client.edit_message_text = _edit_message_text

        # ── edit_message_caption ──────────────────────────────────────────────
        _orig_edit_caption = Client.edit_message_caption

        async def _edit_message_caption(self, chat_id, message_id, caption, *args, **kwargs):
            if is_client_premium(self):
                parse_mode = args[0] if args else kwargs.pop("parse_mode", None)
                caption, parse_mode = _upgrade_text(caption, parse_mode)
                kwargs["parse_mode"] = parse_mode
                remaining_args = args[1:] if args else ()
            else:
                caption = _inject_emoji_entities(caption, kwargs)
                remaining_args = args
            _saved_orig_html = kwargs.pop("_apex_orig_html", None)
            try:
                return await _orig_edit_caption(self, chat_id, message_id, caption, *remaining_args, **kwargs)
            except Exception as _e:
                if _looks_like_entity_error(_e):
                    if _saved_orig_html is not None:
                        kwargs["_apex_orig_html"] = _saved_orig_html
                    caption = _degrade_payload(caption, kwargs)
                    log.debug("edit_message_caption: emoji rejected, retrying plain: %s", _e)
                    return await _orig_edit_caption(self, chat_id, message_id, caption, *remaining_args, **kwargs)
                raise

        Client.edit_message_caption = _edit_message_caption

        # ── Media senders (caption via keyword, safe) ──────────────────────────
        # These methods pass caption as a keyword argument, so **kwargs is safe here.
        # All five now share the same try/retry-plain fallback on entity errors
        # (previously they had NONE at all).

        def _make_media_sender(orig_func):
            async def _sender(self, chat_id, media, *args, **kwargs):
                if "caption" in kwargs and kwargs["caption"]:
                    if is_client_premium(self):
                        pm = kwargs.pop("parse_mode", None)
                        kwargs["caption"], pm = _upgrade_text(kwargs["caption"], pm)
                        kwargs["parse_mode"] = pm
                    else:
                        kwargs["caption"] = _inject_emoji_entities(kwargs["caption"], kwargs)
                _saved_orig_html = kwargs.pop("_apex_orig_html", None)
                try:
                    return await orig_func(self, chat_id, media, *args, **kwargs)
                except Exception as _e:
                    if _looks_like_entity_error(_e) and kwargs.get("caption"):
                        if _saved_orig_html is not None:
                            kwargs["_apex_orig_html"] = _saved_orig_html
                        kwargs["caption"] = _degrade_payload(kwargs["caption"], kwargs)
                        log.debug("media sender: emoji rejected, retrying plain caption: %s", _e)
                        return await orig_func(self, chat_id, media, *args, **kwargs)
                    raise
            return _sender

        Client.send_photo     = _make_media_sender(Client.send_photo)
        Client.send_audio     = _make_media_sender(Client.send_audio)
        Client.send_video     = _make_media_sender(Client.send_video)
        Client.send_document  = _make_media_sender(Client.send_document)
        Client.send_animation = _make_media_sender(Client.send_animation)

        # ── Message.reply / Message.edit / Message.edit_caption ───────────────
        # These are instance methods on Message objects.  Some pyrofork versions
        # call internal RPCs directly instead of going through Client.send_message
        # / Client.edit_message_text, so patching Client alone is not enough.
        #
        # CRITICAL BUG FIX (original):
        #   Non-premium clients must NOT get <tg-emoji> HTML tags.
        #   Telegram rejects them with [400 BAD_REQUEST: can't parse entities].
        #   Pyrogram's dispatcher catches this silently → silent failure.
        #
        # NEW BEHAVIOUR for non-premium:
        #   MessageEntity(CUSTOM_EMOJI) entities inject hote hain instead.
        #
        # v7.1: ALL three (reply/edit/edit_caption) now retry-plain on any
        # entity/emoji error, premium or non-premium — previously only
        # `reply` had this, and only for the non-premium substring case.
        from pyrogram.types import Message as _Message

        _orig_msg_reply = _Message.reply
        async def _msg_reply(self, text, *args, **kwargs):
            _c = getattr(self, '_client', None)
            if _c and isinstance(text, str):
                if is_client_premium(_c):
                    pm = kwargs.pop('parse_mode', None)
                    text, pm = _upgrade_text(text, pm)
                    kwargs['parse_mode'] = pm
                else:
                    text = _inject_emoji_entities(text, kwargs)
            # Pop internal tracking key — Pyrogram doesn't accept it
            _saved_orig_html = kwargs.pop('_apex_orig_html', None)
            try:
                return await _orig_msg_reply(self, text, *args, **kwargs)
            except Exception as _e:
                if _looks_like_entity_error(_e):
                    if _saved_orig_html is not None:
                        kwargs['_apex_orig_html'] = _saved_orig_html
                    text = _degrade_payload(text, kwargs)
                    log.debug("msg_reply: emoji rejected, retrying plain: %s", _e)
                    return await _orig_msg_reply(self, text, *args, **kwargs)
                raise
        _Message.reply = _msg_reply

        _orig_msg_edit = _Message.edit
        async def _msg_edit(self, text, *args, **kwargs):
            _c = getattr(self, '_client', None)
            if _c and isinstance(text, str):
                if is_client_premium(_c):
                    pm = kwargs.pop('parse_mode', None)
                    text, pm = _upgrade_text(text, pm)
                    kwargs['parse_mode'] = pm
                else:
                    text = _inject_emoji_entities(text, kwargs)
            _saved_orig_html = kwargs.pop('_apex_orig_html', None)
            try:
                return await _orig_msg_edit(self, text, *args, **kwargs)
            except Exception as _e:
                if _looks_like_entity_error(_e):
                    if _saved_orig_html is not None:
                        kwargs['_apex_orig_html'] = _saved_orig_html
                    text = _degrade_payload(text, kwargs)
                    log.debug("msg_edit: emoji rejected, retrying plain: %s", _e)
                    return await _orig_msg_edit(self, text, *args, **kwargs)
                raise
        _Message.edit = _msg_edit

        _orig_msg_edit_caption = _Message.edit_caption
        async def _msg_edit_caption(self, caption=None, *args, **kwargs):
            _c = getattr(self, '_client', None)
            if caption and _c:
                if is_client_premium(_c):
                    pm = kwargs.pop('parse_mode', None)
                    caption, pm = _upgrade_text(caption, pm)
                    kwargs['parse_mode'] = pm
                else:
                    caption = _inject_emoji_entities(caption, kwargs)
            _saved_orig_html = kwargs.pop('_apex_orig_html', None)
            try:
                return await _orig_msg_edit_caption(self, caption, *args, **kwargs)
            except Exception as _e:
                if _looks_like_entity_error(_e) and caption:
                    if _saved_orig_html is not None:
                        kwargs['_apex_orig_html'] = _saved_orig_html
                    caption = _degrade_payload(caption, kwargs)
                    log.debug("msg_edit_caption: emoji rejected, retrying plain: %s", _e)
                    return await _orig_msg_edit_caption(self, caption, *args, **kwargs)
                raise
        _Message.edit_caption = _msg_edit_caption

        _INSTALLED = True
        log.info(
            "✅ Premium UI monkey-patch installed — "
            "Premium clients: <tg-emoji> HTML tags | "
            "Non-premium clients: MessageEntity(CUSTOM_EMOJI) entities | "
            "All send/edit paths now self-heal on entity/emoji rejection"
        )

    except Exception as e:
        log.warning("⚠️ Premium UI install failed (non-fatal): %s", e)
