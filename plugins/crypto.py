"""
crypto.py — Cryptocurrency price lookup
Commands: /crypto [symbol], /price [symbol]
"""
import aiohttp
import logging
from pyrogram import Client, filters
from pyrogram.types import Message
from clients import bot

log = logging.getLogger("ApexBot.crypto")

COINGECKO_API = "https://api.coingecko.com/api/v3/simple/price"

POPULAR = {
    "btc": "bitcoin", "eth": "ethereum", "bnb": "binancecoin",
    "sol": "solana", "ada": "cardano", "doge": "dogecoin",
    "xrp": "ripple", "dot": "polkadot", "matic": "matic-network",
    "avax": "avalanche-2", "ltc": "litecoin", "shib": "shiba-inu",
}


@bot.on_message(filters.command(["crypto", "price"]))
async def crypto_cmd(_, message: Message):
    if len(message.command) < 2:
        coins = ", ".join(f"`{k.upper()}`" for k in list(POPULAR.keys())[:6])
        return await message.reply(
            f"**Usage:** `/crypto [symbol]`\n\nPopular: {coins}"
        )

    symbol = message.command[1].lower()
    coin_id = POPULAR.get(symbol, symbol)

    msg = await message.reply(f"📊 Fetching price for `{symbol.upper()}`...")
    try:
        async with aiohttp.ClientSession() as session:
            params = {
                "ids": coin_id,
                "vs_currencies": "usd,eur",
                "include_24hr_change": "true",
                "include_market_cap": "true",
            }
            async with session.get(COINGECKO_API, params=params, timeout=aiohttp.ClientTimeout(total=8)) as resp:
                if resp.status != 200:
                    return await msg.edit("❌ API error. Try again later.")
                data = await resp.json()

        if coin_id not in data:
            return await msg.edit(f"❌ Coin `{symbol.upper()}` not found. Try full name like `bitcoin`.")

        info = data[coin_id]
        price_usd = info.get("usd", "N/A")
        price_eur = info.get("eur", "N/A")
        change_24h = info.get("usd_24h_change", 0)
        mcap = info.get("usd_market_cap", "N/A")

        change_icon = "📈" if change_24h >= 0 else "📉"
        change_str = f"{'+' if change_24h >= 0 else ''}{change_24h:.2f}%"

        if isinstance(mcap, (int, float)):
            mcap_str = f"${mcap:,.0f}"
        else:
            mcap_str = str(mcap)

        await msg.edit(
            f"💰 **{symbol.upper()} Price**\n"
            f"━━━━━━━━━━━━━━━━━\n"
            f"💵 **USD:** `${price_usd:,.4f}`\n"
            f"💶 **EUR:** `€{price_eur:,.4f}`\n"
            f"{change_icon} **24h Change:** `{change_str}`\n"
            f"📊 **Market Cap:** `{mcap_str}`\n"
            f"━━━━━━━━━━━━━━━━━\n"
            f"_Powered by CoinGecko_"
        )
    except Exception as e:
        await msg.edit(f"❌ Error: `{e}`")
