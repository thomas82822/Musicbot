"""
fun.py — Fun commands
Commands: /roll, /flip, /8ball, /quote, /joke, /meme, /ship
"""
import random
from pyrogram import Client, filters
from pyrogram.types import Message
from clients import bot

EIGHT_BALL = [
    "✅ It is certain.", "✅ Definitely yes.", "✅ Without a doubt.",
    "✅ Yes, absolutely!", "✅ Most likely.", "🤔 Reply hazy, try again.",
    "🤔 Ask again later.", "🤔 Cannot predict now.", "❌ Don't count on it.",
    "❌ My reply is no.", "❌ Very doubtful.", "❌ Most likely not.",
]

JOKES = [
    "Why don't scientists trust atoms? Because they make up everything! 😂",
    "I told my wife she was drawing her eyebrows too high. She looked surprised. 😅",
    "What do you call a fake noodle? An impasta! 🍝",
    "Why did the scarecrow win an award? He was outstanding in his field! 🌾",
    "I'm reading a book about anti-gravity. It's impossible to put down! 📚",
    "Did you hear about the mathematician who's afraid of negative numbers? He'll stop at nothing to avoid them!",
    "What do you call cheese that isn't yours? Nacho cheese! 🧀",
]

QUOTES = [
    "🎵 *Music is the shorthand of emotion.* — Leo Tolstoy",
    "🎶 *Without music, life would be a mistake.* — Friedrich Nietzsche",
    "🎵 *Music gives a soul to the universe, wings to the mind.* — Plato",
    "🎶 *One good thing about music, when it hits you, you feel no pain.* — Bob Marley",
    "🎵 *Music is the universal language of mankind.* — Henry Wadsworth Longfellow",
    "🎶 *Where words fail, music speaks.* — Hans Christian Andersen",
]


@bot.on_message(filters.command("roll"))
async def roll(_, message: Message):
    sides = 6
    if len(message.command) > 1:
        try:
            sides = max(2, min(1000, int(message.command[1])))
        except ValueError:
            pass
    result = random.randint(1, sides)
    await message.reply(f"🎲 Rolled a **{sides}-sided dice:** `{result}`")


@bot.on_message(filters.command("flip"))
async def flip(_, message: Message):
    result = random.choice(["🪙 Heads!", "🪙 Tails!"])
    await message.reply(result)


@bot.on_message(filters.command("8ball"))
async def eight_ball(_, message: Message):
    if len(message.command) < 2:
        return await message.reply("**Usage:** `/8ball [question]`")
    answer = random.choice(EIGHT_BALL)
    question = " ".join(message.command[1:])
    await message.reply(f"🎱 **Q:** {question}\n\n**A:** {answer}")


@bot.on_message(filters.command("quote"))
async def quote(_, message: Message):
    await message.reply(random.choice(QUOTES))


@bot.on_message(filters.command("joke"))
async def joke(_, message: Message):
    await message.reply(random.choice(JOKES))


@bot.on_message(filters.command("ship"))
async def ship(_, message: Message):
    if not message.reply_to_message:
        return await message.reply("**Usage:** Reply to a message with `/ship`")
    u1 = message.from_user.first_name if message.from_user else "User1"
    u2 = message.reply_to_message.from_user.first_name if message.reply_to_message.from_user else "User2"
    percent = random.randint(0, 100)
    bar = "💕" * (percent // 10) + "🖤" * (10 - percent // 10)
    await message.reply(
        f"💘 **Ship Meter**\n\n"
        f"👤 {u1} × {u2}\n\n"
        f"{bar}\n"
        f"**{percent}% Compatible!**"
    )
