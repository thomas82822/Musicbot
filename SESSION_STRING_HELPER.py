"""
SESSION_STRING_HELPER.py
━━━━━━━━━━━━━━━━━━━━━━━━
Yeh script assistant account ka session string generate karti hai.
Apne PC par run karo (server par nahi).

Steps:
1. Install: pip install pyrofork
2. Run: python3 SESSION_STRING_HELPER.py
3. Generated string ko HEROKU CONFIG VAR mein daalo as SESSION_STRING
"""

import asyncio


async def main():
    from pyrogram import Client

    print("=" * 50)
    print("  Apex Bot — Session String Generator")
    print("=" * 50)
    print()
    print("⚠️  IMPORTANT: Use a SECOND Telegram account")
    print("    (Not your main account — this is the 'assistant')")
    print()

    api_id   = int(input("Enter API_ID   : ").strip())
    api_hash = input("Enter API_HASH  : ").strip()

    print()
    print("📱 Telegram se OTP aayega — woh daalna hai...")
    print()

    async with Client(
        ":memory:",
        api_id=api_id,
        api_hash=api_hash,
    ) as app:
        session_string = await app.export_session_string()

    print()
    print("=" * 50)
    print("✅ SESSION_STRING:")
    print()
    print(session_string)
    print()
    print("=" * 50)
    print("Isko copy karo aur Heroku Config Vars mein daalo as:")
    print("  SESSION_STRING = <above string>")


if __name__ == "__main__":
    asyncio.run(main())
