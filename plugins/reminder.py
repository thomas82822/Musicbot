"""
reminder.py — Placeholder for future reminder functionality.
startup_restore_reminders is called by main.py on bot startup.
"""
import logging
from pyrogram import Client

log = logging.getLogger("ApexBot.reminder")


async def startup_restore_reminders(bot: Client) -> None:
    """Restore any pending reminders from DB on bot startup."""
    # Reminder plugin not yet implemented — no-op stub
    log.debug("Reminder restore: no reminders to restore (plugin not active)")
