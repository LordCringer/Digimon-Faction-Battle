import discord
from discord import app_commands
from discord.ext import commands

from digilab import DigiLabError


class PlayerPicker(discord.ui.View):
    def __init__(self, players: list[dict], requester_id: int):
        super().__init__(timeout=60)
        self.chosen = None
        self.requester_id = requester_id

        options = [
            discord.SelectOption(
                label=p["name"][:100],
                description=f"Rating {p.get('rating', '?')} · {p.get('events_played', '?')} events"
                            f"{' · ' + p['scene_name'] if p.get('scene_name') else ''}"[:100],
                value=str(i),
            )
            for i, p in enumerate(players[:25])
        ]
        select = discord.ui.Select(placeholder="Which player is you?", options=options)
        select.callback = self._on_select
        self.players = players
        self.add_item(select)

    async def _on_select(self, interaction: discord.Interaction):
        if interaction.user.id != self.requester_id:
            await interaction.response.send_message("This isn't your registration prompt.", ephemeral=True)
            return
        idx = int(interaction.data["values"][0])
        self.chosen = self.players[idx]
        self.stop()
        await interaction.response.edit_message(
            content=f"Linked to **{self.chosen['name']}**.", view=None
        )


class Registration(commands.Cog):
    def __init__(self, bot: commands.Bot):
        self.bot = bot

    @property
    def db(self):
        return self.bot.db

    @app_commands.command(name="register", description="Link your Discord account to your DigiLab player profile")
    @app_commands.describe(name="Your player name as it appears on DigiLab / at tournaments")
    async def register(self, interaction: discord.Interaction, name: str):
        await interaction.response.defer(ephemeral=True)
        try:
            results = await self.bot.digilab.search(name)
        except DigiLabError as e:
            await interaction.followup.send(f"Couldn't reach DigiLab right now: {e}", ephemeral=True)
            return

        players = (results or {}).get("players", [])
        if not players:
            await interaction.followup.send(
                f"No DigiLab players found matching **{name}**. Check the spelling of your tournament name.",
                ephemeral=True,
            )
            return

        if len(players) == 1:
            p = players[0]
            await self.db.register_player(interaction.user.id, p["id"], p["name"], p["slug"])
            await interaction.followup.send(f"Linked to **{p['name']}**.", ephemeral=True)
            return

        view = PlayerPicker(players, interaction.user.id)
        await interaction.followup.send(
            f"Found {len(players)} players matching **{name}** — pick yours:", view=view, ephemeral=True
        )
        await view.wait()
        if view.chosen:
            p = view.chosen
            await self.db.register_player(interaction.user.id, p["id"], p["name"], p["slug"])

    @app_commands.command(name="unregister", description="Unlink your DigiLab player profile")
    async def unregister(self, interaction: discord.Interaction):
        await self.bot.db._conn.execute(
            "DELETE FROM registrations WHERE discord_id = ?", (interaction.user.id,)
        )
        await self.bot.db._conn.commit()
        await interaction.response.send_message("Unlinked your DigiLab profile.", ephemeral=True)


async def setup(bot: commands.Bot):
    await bot.add_cog(Registration(bot))
