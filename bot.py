import asyncio
import logging

import discord
from discord.ext import commands, tasks

import config
from db import DB
from digilab import DigiLabClient
from points_sync import sync_points

logging.basicConfig(level=logging.INFO, format="%(asctime)s [%(name)s] %(levelname)s: %(message)s")
log = logging.getLogger("bot")

INTENTS = discord.Intents.default()
INTENTS.members = True  # needed to resolve users for mentions/leaderboards reliably


class FactionBot(commands.Bot):
    def __init__(self):
        super().__init__(command_prefix="!", intents=INTENTS)
        self.db = DB()
        self.digilab: DigiLabClient | None = None

    async def setup_hook(self):
        await self.db.connect()
        await self.db.ensure_factions(config.DEFAULT_FACTIONS)
        self.digilab = DigiLabClient()
        await self.digilab.__aenter__()

        for ext in ("cogs.factions", "cogs.registration", "cogs.admin", "cogs.reactions"):
            await self.load_extension(ext)

        if config.GUILD_ID:
            guild = discord.Object(id=config.GUILD_ID)
            self.tree.copy_global_to(guild=guild)
            await self.tree.sync(guild=guild)
            log.info("Synced commands to guild %s", config.GUILD_ID)
        else:
            await self.tree.sync()
            log.info("Synced global commands (can take up to an hour to propagate)")

        self.points_poll_loop.start()

    async def close(self):
        self.points_poll_loop.cancel()
        if self.digilab:
            await self.digilab.__aexit__(None, None, None)
        await self.db.close()
        await super().close()

    @tasks.loop(minutes=config.POLL_INTERVAL_MINUTES)
    async def points_poll_loop(self):
        try:
            awards = await sync_points(self.db, self.digilab, self)
            if awards:
                log.info("Awarded points for %d new result(s)", len(awards))
        except Exception:
            log.exception("Points poll failed")

    @points_poll_loop.before_loop
    async def before_poll(self):
        await self.wait_until_ready()


bot = FactionBot()


@bot.event
async def on_ready():
    log.info("Logged in as %s (%s)", bot.user, bot.user.id)


if __name__ == "__main__":
    if not config.DISCORD_TOKEN:
        raise SystemExit("DISCORD_TOKEN is not set (check your .env)")
    if not config.DIGILAB_API_KEY:
        log.warning("DIGILAB_API_KEY is not set — DigiLab requests will fail")
    asyncio.run(bot.start(config.DISCORD_TOKEN))
