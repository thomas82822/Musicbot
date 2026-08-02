"""
reload.py — Bot management commands
Commands: /reload, /reboot, /update, /logs, /cleardb
Only for Owner/Sudo users.
Proper Telegram HTML: <blockquote>, <b>, <code>
"""
import os
import sys
import asyncio
import importlib
import logging
import html as _html
from pyrogram import Client, filters, enums
from pyrogram.types import Message
from clients import bot
from helpers.decorators import owner_only

log = logging.getLogger("ApexBot.reload")

LOG_FILE = "bot.log"


@bot.on_message(filters.command("reload") & filters.private)
@owner_only
async def reload_plugins(_, message: Message):
    """Hot-reload all plugins without restarting."""
    msg = await message.reply(
        "<blockquote>♻️ <b>Reloading Plugins...</b></blockquote>",
        parse_mode=enums.ParseMode.HTML,
    )
    reloaded = []
    failed   = []

    plugins_dir = os.path.join(os.path.dirname(os.path.dirname(__file__)), "plugins")
    for fname in os.listdir(plugins_dir):
        if fname.endswith(".py") and not fname.startswith("_"):
            mod_name = f"plugins.{fname[:-3]}"
            try:
                if mod_name in sys.modules:
                    importlib.reload(sys.modules[mod_name])
                    reloaded.append(fname[:-3])
            except Exception as e:
                failed.append(f"{fname[:-3]}: {_html.escape(str(e))}")

    ok_list   = ", ".join(f"<code>{r}</code>" for r in reloaded) if reloaded else "none"
    fail_text = ""
    if failed:
        fail_text = "\n\n❌ <b>Failed:</b>\n" + "\n".join(
            f"  • {f}" for f in failed
        )

    await msg.edit(
        f"<blockquote>♻️ <b>Reload Complete</b></blockquote>\n\n"
        f"✅ <b>Reloaded:</b> {ok_list}"
        f"{fail_text}",
        parse_mode=enums.ParseMode.HTML,
    )


@bot.on_message(filters.command("reboot") & filters.private)
@owner_only
async def reboot(_, message: Message):
    """Restart the bot process."""
    await message.reply(
        "<blockquote>🔄 <b>Rebooting Bot...</b></blockquote>\n\n"
        "<i>Bot will be back in a few seconds!</i>",
        parse_mode=enums.ParseMode.HTML,
    )
    await asyncio.sleep(1)
    os.execv(sys.executable, [sys.executable] + sys.argv)


@bot.on_message(filters.command("update") & filters.private)
@owner_only
async def update(_, message: Message):
    """Pull latest code from GitHub and restart."""
    msg = await message.reply(
        "<blockquote>⬇️ <b>Pulling Latest Code from GitHub...</b></blockquote>",
        parse_mode=enums.ParseMode.HTML,
    )
    try:
        proc = await asyncio.create_subprocess_shell(
            "git pull",
            stdout=asyncio.subprocess.PIPE,
            stderr=asyncio.subprocess.PIPE,
        )
        stdout, stderr = await proc.communicate()
        output = _html.escape((stdout + stderr).decode().strip()[-1500:])
        await msg.edit(
            f"<blockquote>📦 <b>Git Pull Output</b></blockquote>\n\n"
            f"<code>{output}</code>\n\n"
            f"🔄 <i>Rebooting now...</i>",
            parse_mode=enums.ParseMode.HTML,
        )
        await asyncio.sleep(2)

        req_proc = await asyncio.create_subprocess_shell(
            f"{sys.executable} -m pip install -r requirements.txt -q",
        )
        await req_proc.wait()

        os.execv(sys.executable, [sys.executable] + sys.argv)
    except Exception as e:
        await msg.edit(
            f"<blockquote>❌ <b>Update Failed</b></blockquote>\n\n"
            f"<code>{_html.escape(str(e))}</code>",
            parse_mode=enums.ParseMode.HTML,
        )


@bot.on_message(filters.command("logs") & filters.private)
@owner_only
async def send_logs(_, message: Message):
    """Send last 50 log lines."""
    try:
        if os.path.exists(LOG_FILE):
            with open(LOG_FILE, "r") as f:
                lines = f.readlines()
            last_50 = "".join(lines[-50:])
            if len(last_50) > 3500:
                last_50 = last_50[-3500:]
            await message.reply(
                f"<blockquote>📄 <b>Last Logs</b></blockquote>\n\n"
                f"<code>{_html.escape(last_50)}</code>",
                parse_mode=enums.ParseMode.HTML,
            )
        else:
            proc = await asyncio.create_subprocess_shell(
                "journalctl -u bot -n 50 --no-pager 2>/dev/null || echo 'Log file not found'",
                stdout=asyncio.subprocess.PIPE,
            )
            stdout, _ = await proc.communicate()
            output = _html.escape(stdout.decode()[-3000:])
            await message.reply(
                f"<blockquote>📄 <b>Logs</b></blockquote>\n\n"
                f"<code>{output}</code>",
                parse_mode=enums.ParseMode.HTML,
            )
    except Exception as e:
        await message.reply(
            f"❌ Error: <code>{_html.escape(str(e))}</code>",
            parse_mode=enums.ParseMode.HTML,
        )


@bot.on_message(filters.command("cleardb") & filters.private)
@owner_only
async def clear_db_cache(_, message: Message):
    """Clear database cache."""
    msg = await message.reply(
        "<blockquote>🗑 <b>Clearing DB Cache...</b></blockquote>",
        parse_mode=enums.ParseMode.HTML,
    )
    try:
        from helpers.settings_cache import _settings_cache, _store
        _settings_cache.clear()
        _store.clear()
        await msg.edit(
            "<blockquote>✅ <b>DB Cache Cleared!</b></blockquote>",
            parse_mode=enums.ParseMode.HTML,
        )
    except Exception as e:
        await msg.edit(
            f"❌ Error: <code>{_html.escape(str(e))}</code>",
            parse_mode=enums.ParseMode.HTML,
        )
