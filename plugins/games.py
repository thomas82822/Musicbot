"""
games.py — Group games
Commands: /ttt, /wordchain, /trivia, /guess
"""
import random
import asyncio
import logging
from pyrogram import Client, filters
from pyrogram.types import Message, InlineKeyboardMarkup, InlineKeyboardButton, CallbackQuery
from clients import bot

log = logging.getLogger("ApexBot.games")

TRIVIA_QS = [
    ("Which band recorded 'Bohemian Rhapsody'?", "queen"),
    ("Who sang 'Thriller'?", "michael jackson"),
    ("Which artist released 'Shape of You'?", "ed sheeran"),
    ("Who sang 'Hello' (2015)?", "adele"),
    ("Which band sang 'Hotel California'?", "eagles"),
    ("Who sang 'Blinding Lights'?", "the weeknd"),
    ("Which artist released 'Bad Guy'?", "billie eilish"),
    ("Who sang 'Rolling in the Deep'?", "adele"),
    ("Which group sang 'Uptown Funk'?", "mark ronson"),
    ("Who released 'Despacito'?", "luis fonsi"),
]

_guess_games: dict[int, dict] = {}  # chat_id → game state
_trivia_games: dict[int, dict] = {}


@bot.on_message(filters.command("trivia") & filters.group)
async def trivia(_, message: Message):
    chat_id = message.chat.id
    if chat_id in _trivia_games:
        return await message.reply("🎮 A trivia game is already running! Answer it first.")

    q, answer = random.choice(TRIVIA_QS)
    _trivia_games[chat_id] = {"answer": answer, "asker": message.from_user.id if message.from_user else 0}

    await message.reply(
        f"🎵 **Music Trivia!**\n\n"
        f"❓ {q}\n\n"
        f"⏱ You have **30 seconds!** Reply with your answer.",
    )
    await asyncio.sleep(30)
    if chat_id in _trivia_games:
        del _trivia_games[chat_id]
        await message.reply(f"⏰ Time's up! The answer was: **{answer.title()}**")


@bot.on_message(filters.group & filters.text & ~filters.bot, group=6)
async def trivia_answer(_, message: Message):
    chat_id = message.chat.id
    game = _trivia_games.get(chat_id)
    if not game:
        return
    if message.text and message.text.lower().strip() == game["answer"]:
        del _trivia_games[chat_id]
        user = message.from_user.first_name if message.from_user else "Someone"
        await message.reply(f"🏆 **{user}** got it right!\n\nAnswer: **{game['answer'].title()}** ✅")


@bot.on_message(filters.command("guess") & filters.group)
async def guess_game(_, message: Message):
    chat_id = message.chat.id
    if chat_id in _guess_games:
        return await message.reply("🎮 A guess game is already running!")

    number = random.randint(1, 100)
    _guess_games[chat_id] = {"number": number, "tries": 0, "max_tries": 7}
    await message.reply(
        "🔢 **Guess the Number!**\n\n"
        "I'm thinking of a number between **1-100**.\n"
        f"You have **7** tries! Type a number to guess.",
    )

    await asyncio.sleep(120)
    if chat_id in _guess_games:
        game = _guess_games.pop(chat_id)
        await message.reply(f"⏰ Game over! The number was **{game['number']}**.")


@bot.on_message(filters.group & filters.text & ~filters.bot, group=7)
async def guess_answer(_, message: Message):
    chat_id = message.chat.id
    game = _guess_games.get(chat_id)
    if not game:
        return

    try:
        guess = int(message.text.strip())
    except (ValueError, AttributeError):
        return

    game["tries"] += 1
    number = game["number"]
    tries_left = game["max_tries"] - game["tries"]
    user = message.from_user.first_name if message.from_user else "Player"

    if guess == number:
        del _guess_games[chat_id]
        await message.reply(
            f"🎉 **{user}** guessed it!\n\n"
            f"Number was **{number}** — found in {game['tries']} tries!"
        )
    elif tries_left <= 0:
        del _guess_games[chat_id]
        await message.reply(f"💀 Out of tries! Number was **{number}**.")
    elif guess < number:
        await message.reply(f"📈 Too low! {tries_left} tries left.")
    else:
        await message.reply(f"📉 Too high! {tries_left} tries left.")
