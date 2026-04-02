"""
main.py — Entrypoint for deployment platforms (Railway, Render, Fly.io, etc.)
"""
from bot import bot
import os
from dotenv import load_dotenv

load_dotenv()
TOKEN = os.getenv("DISCORD_TOKEN")

if not TOKEN:
    raise ValueError("No DISCORD_TOKEN found. Set it in your environment variables or .env file.")

bot.run(TOKEN)
