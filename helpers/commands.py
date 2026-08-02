"""Telegram command menu definitions and registration.

The handlers live in the plugin modules, but Telegram's slash-command menu is
managed centrally so it cannot silently drift away from the commands that are
actually available.
"""

from collections.abc import Iterable

from pyrogram import Client
from pyrogram.types import (
    BotCommand,
    BotCommandScopeAllChatAdministrators,
    BotCommandScopeAllGroupChats,
    BotCommandScopeAllPrivateChats,
)


def _commands(items: Iterable[tuple[str, str]]) -> list[BotCommand]:
    return [BotCommand(command, description) for command, description in items]


# Commands available to users in private chats. Group-only features are kept
# out of this scope because Telegram displays the scope-specific list.
PRIVATE_COMMANDS = _commands(
    (
        ("start", "Start the bot and claim your welcome bonus"),
        ("help", "Open the complete command guide"),
        ("ping", "Check bot response time and uptime"),
        ("about", "See bot information and features"),
        ("id", "Show your Telegram user ID"),
        ("info", "Show a user's profile information"),
        ("whois", "Show a user's profile information"),
        # DM Music
        ("play", "Download and receive a song as audio file"),
        ("download", "Download a song from YouTube"),
        # Economy
        ("balance", "Check your wallet balance"),
        ("daily", "Claim your daily wallet reward"),
        ("transfer", "Transfer money to another user"),
        # Owner
        ("broadcast", "Broadcast a message to all bot chats"),
        ("gban", "Globally ban a user"),
        ("ungban", "Remove a global ban"),
        ("gbans", "Show the global ban count"),
        ("givemoney", "Give money to a user (owner only)"),
        ("takemoney", "Take money from a user (owner only)"),
        ("setmoney", "Set a user's balance (owner only)"),
        ("genname", "Generate a fancy name"),
        ("gendp", "Generate a profile picture"),
        # Tools
        ("weather", "Get current weather for any city"),
        ("translate", "Translate text to any language"),
        ("qr", "Generate a QR code"),
        ("tts", "Text to speech"),
        ("calc", "Calculator"),
        ("lyrics", "Get song lyrics"),
        ("paste", "Paste text to hastebin"),
        # Fun
        ("joke", "Get a random joke"),
        ("shayari", "Get a shayari"),
        ("quote", "Get a motivational quote"),
        ("flip", "Flip a coin"),
        ("dice", "Roll a dice"),
        ("8ball", "Ask the magic 8-ball"),
        # Owner controls (private)
        ("reload", "Reload all plugins (owner only)"),
        ("reboot", "Restart the bot process (owner only)"),
        ("update", "Git pull + restart (owner only)"),
        ("maintenance", "Toggle maintenance mode (owner only)"),
        ("logs", "View recent log output (owner only)"),
        ("clearall", "Stop music in all active VCs (owner only)"),
        ("activevc", "List all active voice chats (owner only)"),
        ("shutdown", "Gracefully shut down the bot (owner only)"),
    )
)


GROUP_COMMANDS = _commands(
    (
        ("start", "Show group bot information"),
        ("help", "Open the complete command guide"),
        ("ping", "Check bot response time and uptime"),
        ("about", "See bot information and features"),
        ("id", "Show a user's or chat's ID"),
        ("info", "Show a user's profile information"),
        ("whois", "Show a user's profile information"),
        # Music
        ("play", "Play audio from YouTube"),
        ("vplay", "Play video from YouTube"),
        ("playforce", "Play immediately and clear the current queue"),
        ("pause", "Pause the current stream"),
        ("resume", "Resume the current stream"),
        ("skip", "Skip to the next song"),
        ("stop", "Stop playback and clear the queue"),
        ("vol", "Set playback volume"),
        ("queue", "Show the music queue"),
        ("np", "Show the currently playing song"),
        ("shuffle", "Shuffle the music queue"),
        ("loop", "Toggle song loop"),
        ("autoplay", "Toggle YouTube suggestions autoplay (non-stop)"),
        ("seek", "Seek to a position in the current song"),
        ("remove", "Remove a song from the queue"),
        ("move", "Move a song to a different queue position"),
        ("playlist", "Add an entire YouTube playlist to the queue"),
        ("search", "Search YouTube and choose a song to play"),
        ("scplay", "Play a SoundCloud track in voice chat"),
        ("lyrics", "Get lyrics for the current or any song"),
        ("download", "Download current song to your DM"),
        ("speed", "Change playback speed"),
        ("history", "Show recently played songs"),
        ("trending", "Show the most played songs in this group"),
        ("mystats", "Show your music listening stats"),
        ("musicquiz", "Guess the song from a lyric clue"),
        ("schedule", "Schedule a song for later"),
        ("schedules", "List scheduled songs"),
        ("cancelschedule", "Cancel a scheduled song"),
        ("timer", "Auto-stop music after a delay"),
        ("stoptimer", "Cancel the auto-stop timer"),
        ("balance", "Check your wallet balance"),
        ("daily", "Claim your daily wallet reward"),
        ("transfer", "Transfer money to another user"),
        ("richlist", "Show the richest group users"),
        ("truth", "Get a truth question"),
        ("dare", "Get a dare challenge"),
        ("wyr", "Play would-you-rather"),
        ("trivia", "Get a timed trivia question"),
        ("kill", "Attack another user"),
        ("rob", "Try to rob another user"),
        ("revive", "Revive another user"),
        ("protect", "Buy temporary protection"),
        ("slap", "Slap another user"),
        ("fight", "Fight another user"),
        ("marry", "Propose marriage"),
        ("divorce", "End your current marriage"),
        ("couples", "Show group couples"),
        ("gban", "Globally ban a user"),
        ("ungban", "Remove a global ban"),
        # NOTE: "gbans" removed from group scope — brings ADMIN_COMMANDS under
        # Telegram's 100-command limit. Available via /gbans in private chat.
        ("antiporn", "Toggle NSFW sticker protection"),
        ("addfilter", "Add a word filter"),
        ("rmfilter", "Remove a word filter"),
        ("filters", "List word filters"),
        ("savenote", "Save a group note"),
        ("get", "Read a saved group note"),
        ("delnote", "Delete a saved group note"),
        ("notes", "List saved group notes"),
        ("setwelcome", "Set the welcome message"),
        ("setgoodbye", "Set the goodbye message"),
        ("welcome", "Toggle or view welcome settings"),
        ("goodbye", "Toggle or view goodbye settings"),
        ("resetwelcome", "Reset the welcome message"),
        ("resetgoodbye", "Reset the goodbye message"),
        ("stats", "Show group activity statistics"),
        ("rankings", "Show the most active users"),
        # NOTE: "topgroups" removed from group scope — keeps ADMIN_COMMANDS ≤ 100.
        ("chatbot", "Toggle the group chatbot"),
        ("tagall", "Mention group members"),
        ("ontag", "Toggle automatic tag replies"),
        ("reaction", "Toggle automatic reactions"),
        # Tools
        ("weather", "Get current weather for any city"),
        ("translate", "Translate text to any language"),
        ("qr", "Generate a QR code"),
        ("tts", "Convert text to speech"),
        ("calc", "Calculator"),
        ("paste", "Paste text to hastebin"),
        # Fun
        ("joke", "Get a random joke"),
        ("shayari", "Get a shayari"),
        ("quote", "Get a motivational quote"),
        ("flip", "Flip a coin"),
        ("dice", "Roll a dice"),
        ("8ball", "Ask the magic 8-ball"),
        # Radio
        ("radio", "Stream a live radio station"),
        ("radiolist", "Show all preset radio stations"),
        ("stopradio", "Stop radio playback"),
        # New music shortcuts
        ("end", "Stop music and leave VC (alias for /stop)"),
        ("joinvc", "Make bot join voice chat"),
        ("leavevc", "Make bot leave voice chat"),
        ("speedup", "Increase playback speed by 0.25x"),
        ("slowdown", "Decrease playback speed by 0.25x"),
        ("stream", "Stream from a direct audio/video URL"),
        # Settings
        ("settings", "Open group settings panel"),
    )
)


# Admins get a more useful list than regular group members. Telegram applies
# this scope only to administrators, while GROUP_COMMANDS remains the fallback
# for everyone else.
_ADMIN_ONLY_COMMANDS = (
    ("ban", "Ban a user from the group"),
    ("unban", "Unban a user"),
    ("kick", "Remove a user from the group"),
    ("mute", "Mute a user"),
    ("unmute", "Unmute a user"),
    ("warn", "Warn a user"),
    ("warns", "View a user's warnings"),
    ("clearwarn", "Clear a user's warnings"),
    ("promote", "Promote a user to admin"),
    ("fpromote", "Promote a user with full rights"),
    ("demote", "Remove admin rights"),
    ("pin", "Pin a replied message"),
    ("unpin", "Unpin the latest message"),
    ("purge", "Delete messages from a reply"),
    ("admins", "List group administrators"),
    ("report", "Report a user to group admins"),
    ("banall", "Ban all listed target users"),
    ("unbanall", "Unban all listed target users"),
    ("antiporn", "Toggle NSFW sticker protection"),
    ("addfilter", "Add a word filter"),
    ("rmfilter", "Remove a word filter"),
    ("filters", "List word filters"),
    ("setwelcome", "Set the welcome message"),
    ("setgoodbye", "Set the goodbye message"),
    ("welcome", "Toggle or view welcome settings"),
    ("goodbye", "Toggle or view goodbye settings"),
    ("resetwelcome", "Reset the welcome message"),
    ("resetgoodbye", "Reset the goodbye message"),
    ("chatbot", "Toggle the group chatbot"),
    ("tagall", "Mention group members"),
    ("ontag", "Toggle automatic tag replies"),
    ("reaction", "Toggle automatic reactions"),
)

# Telegram uses the most specific matching scope. Include the regular group
# menu here as well, otherwise administrators would lose music and fun
# commands when the administrator-specific menu is applied.
#
# NOTE: Telegram enforces a hard 100-command limit per scope.
# GROUP_COMMANDS (91) + 18 unique admin-only = 109 → exceeds limit.
# We cap at 100 by prioritising admin-specific commands first, then filling
# remaining slots with the general group commands.
_ADMIN_COMMANDS_BY_NAME: dict[str, str] = {}

# 1. Admin-only commands get priority slots (they don't appear in GROUP menu)
for _command, _description in _ADMIN_ONLY_COMMANDS:
    _ADMIN_COMMANDS_BY_NAME[_command] = _description

# 2. Fill remaining slots with group commands (skip already-added)
for _cmd_obj in GROUP_COMMANDS:
    if len(_ADMIN_COMMANDS_BY_NAME) >= 100:
        break
    _ADMIN_COMMANDS_BY_NAME.setdefault(_cmd_obj.command, _cmd_obj.description)

ADMIN_COMMANDS = _commands(_ADMIN_COMMANDS_BY_NAME.items())

# Useful for static checks and documentation tooling. Aliases remain accepted
# by the handlers but are intentionally not all shown in Telegram's menu.
COMMAND_ALIASES = {
    # BUG FIX: "play" previously listed "fplay" and "pf" as its aliases, but
    # those are registered exclusively by the "playforce" handler.
    "play": ("p",),
    "vplay": ("vp",),
    "playforce": ("pf", "fplay"),
    "skip": ("next", "s"),
    "stop": ("end",),
    "vol": ("volume", "v"),
    "queue": ("q",),
    "np": ("now", "song"),
    "balance": ("bal", "wallet"),
    "transfer": ("give",),
    "richlist": ("toprich", "richboard"),
    "trivia": ("quiz",),
    "couples": ("couple", "ship"),
    "gban": ("gbanlist",),
    "ungban": (),
    "addfilter": ("filter",),
    "rmfilter": ("unfilter", "delfilter"),
    "filters": ("listfilters",),
    "savenote": ("note",),
    "delnote": ("deletenote",),
    "notes": ("listnotes",),
    "rankings": ("topusers", "top", "leaderboard"),
    "admins": ("adminlist", "staff"),
    "tagall": ("tg", "mentionall"),
    "genname": ("fname", "fancyname"),
    "gendp": ("dp", "genpic"),
    "broadcast": ("bc",),
    # New aliases
    "seek": ("sk",),
    "remove": ("rm", "dequeue"),
    "move": ("mv",),
    "playlist": ("plist", "addplaylist"),
    "lyrics": ("lyric", "ly"),
    "download": ("dl", "dlsong", "save"),
    "speed": ("spd",),
    "weather": ("wt",),
    "translate": ("tr",),
    "qr": ("qrcode",),
    "tts": ("speak",),
    "calc": ("calculate", "math"),
    "paste": ("hastebin",),
    "joke": ("lol", "mazak"),
    "shayari": ("poetry", "love"),
    # BUG FIX: "q" was shared by "queue" and "quote" — both handlers fired on
    # /q. Removed "q" from "quote"; /q now correctly maps only to queue.
    "quote": ("motivate", "inspire"),
    "flip": ("coin",),
    "dice": ("roll",),
    "8ball": ("eightball", "magic"),
    "givemoney": ("addmoney",),
    "takemoney": ("removemoney",),
    "musicquiz": ("songquiz",),
    "scplay": ("sc", "soundcloud"),
    "trending": ("topsongs",),
    "globaltop": ("globaltrending", "worldtop"),
    "schedule": ("sched",),
    "schedules": ("listschedules",),
    "cancelschedule": ("unschedule",),
    "mystats": ("mymusic",),
    "quizstop": (),
    # New commands
    "radio": ("liveradio",),
    "radiolist": ("stations", "radios"),
    "stopradio": ("radiooff",),
    "end": ("endplay",),
    "joinvc": ("joincall",),
    "leavevc": ("leavecall", "endvc"),
    "speedup": ("faster",),
    "slowdown": ("slower",),
    "stream": ("streamurl",),
    "settings": ("gsettings", "config", "groupsettings"),
    "reload": (),
    "reboot": ("restart",),
    "update": ("gitpull",),
    "maintenance": ("maint",),
    "logs": ("log",),
    "clearall": ("stopall",),
    "activevc": ("vclist", "allvc"),
    "shutdown": ("poweroff",),
}


async def register_bot_commands(client: Client) -> None:
    """Publish all slash menus after the bot client has started.

    Registration is deliberately best-effort: a Telegram API issue should be
    logged by the caller without taking down music playback and other handlers.
    """

    await client.set_bot_commands(
        PRIVATE_COMMANDS,
        scope=BotCommandScopeAllPrivateChats(),
    )
    await client.set_bot_commands(
        GROUP_COMMANDS,
        scope=BotCommandScopeAllGroupChats(),
    )
    await client.set_bot_commands(
        ADMIN_COMMANDS,
        scope=BotCommandScopeAllChatAdministrators(),
    )