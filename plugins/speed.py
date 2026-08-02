"""
speed.py — Playback speed and audio effects
Commands: /speed, /bass, /treble, /nightcore, /slowreverb, /reset_audio
"""
import logging
from pyrogram import Client, filters
from pyrogram.types import Message
from clients import bot, call_py
from helpers.queue import get_current

log = logging.getLogger("ApexBot.speed")

VALID_SPEEDS = [0.5, 0.75, 1.0, 1.25, 1.5, 2.0]


async def _change_stream_with_ffmpeg(chat_id: int, url: str, headers: dict,
                                      ffmpeg_params: str, is_video: bool = False):
    """Restart stream with new FFmpeg parameters."""
    if not call_py:
        return
    try:
        from pytgcalls.types import MediaStream, AudioQuality, VideoQuality
        if is_video:
            stream = MediaStream(
                url,
                audio_parameters=AudioQuality.STUDIO,
                video_parameters=VideoQuality.HD_720p,
                ffmpeg_parameters=ffmpeg_params,
                headers=headers,
            )
        else:
            stream = MediaStream(
                url,
                audio_parameters=AudioQuality.STUDIO,
                ffmpeg_parameters=ffmpeg_params,
                headers=headers,
            )
        await call_py.change_stream(chat_id, stream)
    except Exception as e:
        log.error("change_stream_with_ffmpeg: %s", e)
        raise


@bot.on_message(filters.command("speed") & filters.group)
async def speed_cmd(_, message: Message):
    if not call_py:
        return await message.reply("❌ Voice chat unavailable.")
    song = get_current(message.chat.id)
    if not song:
        return await message.reply("❌ Nothing is playing.")

    if len(message.command) < 2:
        valid = " / ".join(str(s) for s in VALID_SPEEDS)
        return await message.reply(f"**Usage:** `/speed [speed]`\nValid: `{valid}`")

    try:
        spd = float(message.command[1])
    except ValueError:
        return await message.reply("❌ Speed must be a number.")

    if spd not in VALID_SPEEDS:
        return await message.reply(f"❌ Valid speeds: {' / '.join(str(s) for s in VALID_SPEEDS)}")

    reconnect = "-reconnect 1 -reconnect_streamed 1 -reconnect_delay_max 5"
    ffmpeg = f"{reconnect} -af atempo={spd}"
    try:
        await _change_stream_with_ffmpeg(
            message.chat.id, song.url, song.http_headers, ffmpeg, song.is_video
        )
        await message.reply(f"⚡ Speed set to **{spd}x**")
    except Exception as e:
        await message.reply(f"❌ Error: `{e}`")


@bot.on_message(filters.command("bass") & filters.group)
async def bass_cmd(_, message: Message):
    if not call_py:
        return await message.reply("❌ Voice chat unavailable.")
    song = get_current(message.chat.id)
    if not song:
        return await message.reply("❌ Nothing is playing.")

    level = 10
    if len(message.command) > 1:
        try:
            level = max(0, min(20, int(message.command[1])))
        except ValueError:
            pass

    reconnect = "-reconnect 1 -reconnect_streamed 1 -reconnect_delay_max 5"
    ffmpeg = f"{reconnect} -af bass=g={level}"
    try:
        await _change_stream_with_ffmpeg(message.chat.id, song.url, song.http_headers, ffmpeg)
        await message.reply(f"🎸 Bass boost set to **{level}**")
    except Exception as e:
        await message.reply(f"❌ Error: `{e}`")


@bot.on_message(filters.command("treble") & filters.group)
async def treble_cmd(_, message: Message):
    if not call_py:
        return await message.reply("❌ Voice chat unavailable.")
    song = get_current(message.chat.id)
    if not song:
        return await message.reply("❌ Nothing is playing.")

    level = 5
    if len(message.command) > 1:
        try:
            level = max(0, min(20, int(message.command[1])))
        except ValueError:
            pass

    reconnect = "-reconnect 1 -reconnect_streamed 1 -reconnect_delay_max 5"
    ffmpeg = f"{reconnect} -af treble=g={level}"
    try:
        await _change_stream_with_ffmpeg(message.chat.id, song.url, song.http_headers, ffmpeg)
        await message.reply(f"🎵 Treble boost set to **{level}**")
    except Exception as e:
        await message.reply(f"❌ Error: `{e}`")


@bot.on_message(filters.command("nightcore") & filters.group)
async def nightcore_cmd(_, message: Message):
    if not call_py:
        return await message.reply("❌ Voice chat unavailable.")
    song = get_current(message.chat.id)
    if not song:
        return await message.reply("❌ Nothing is playing.")

    reconnect = "-reconnect 1 -reconnect_streamed 1 -reconnect_delay_max 5"
    ffmpeg = f"{reconnect} -af atempo=1.25,asetrate=44100*1.25"
    try:
        await _change_stream_with_ffmpeg(message.chat.id, song.url, song.http_headers, ffmpeg)
        await message.reply("🌸 **Nightcore** effect applied! (fast + pitch up)")
    except Exception as e:
        await message.reply(f"❌ Error: `{e}`")


@bot.on_message(filters.command("slowreverb") & filters.group)
async def slowreverb_cmd(_, message: Message):
    if not call_py:
        return await message.reply("❌ Voice chat unavailable.")
    song = get_current(message.chat.id)
    if not song:
        return await message.reply("❌ Nothing is playing.")

    reconnect = "-reconnect 1 -reconnect_streamed 1 -reconnect_delay_max 5"
    ffmpeg = f"{reconnect} -af atempo=0.8,aecho=0.8:0.8:500:0.5"
    try:
        await _change_stream_with_ffmpeg(message.chat.id, song.url, song.http_headers, ffmpeg)
        await message.reply("🌊 **Slow + Reverb** effect applied! (lofi vibes)")
    except Exception as e:
        await message.reply(f"❌ Error: `{e}`")


@bot.on_message(filters.command("reset_audio") & filters.group)
async def reset_audio_cmd(_, message: Message):
    if not call_py:
        return await message.reply("❌ Voice chat unavailable.")
    song = get_current(message.chat.id)
    if not song:
        return await message.reply("❌ Nothing is playing.")

    reconnect = "-reconnect 1 -reconnect_streamed 1 -reconnect_delay_max 5"
    try:
        await _change_stream_with_ffmpeg(message.chat.id, song.url, song.http_headers, reconnect)
        await message.reply("🔄 Audio reset to **normal**.")
    except Exception as e:
        await message.reply(f"❌ Error: `{e}`")
