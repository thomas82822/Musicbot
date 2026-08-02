"""
schedule.py — Placeholder for future scheduled message functionality.
startup_restore_schedules is called by main.py on bot startup.
"""
import logging
from pyrogram import Client

log = logging.getLogger("ApexBot.schedule")


async def startup_restore_schedules(bot: Client) -> None:
    """Restore any pending scheduled messages from DB on bot startup."""
    # Schedule plugin not yet implemented — no-op stub
    log.debug("Schedule restore: no schedules to restore (plugin not active)")
