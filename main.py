import asyncio
import os

import discord
from discord.ext import commands

bot = commands.Bot(command_prefix="!", intents=discord.Intents.default())


@bot.event
async def on_ready():
    await bot.tree.sync()
    print("Coach bot ready as", bot.user)


async def main():
    async with bot:
        await bot.load_extension("coach_bot.cog")
        await bot.start(os.environ["DISCORD_TOKEN"])


asyncio.run(main())
